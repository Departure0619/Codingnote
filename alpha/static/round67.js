'use strict';

// Product operations reuse the existing forms, saved versions and local API.
function preferenceSnapshot(turn) {
  const prefs=turn.context?.learning_preferences;if(!prefs)return '';
  return '<details class="record-details"><summary>查看本轮实际设置</summary>'+preferenceFields.map(([key,label,values])=>
    '<p class="help">'+esc(label)+'：'+esc(values[prefs[key]]||'历史记录未提供')+'</p>').join('')+'</details>';
}
function teachingRequest(data) {
  return {purpose:data.get('purpose')||'chat',explicit_direct:data.has('explicit_direct'),
    evidence_ids:data.getAll('share_evidence'),expected_source_sha256:currentVersion().source_sha256};
}
function renderSharedEvidence() {
  const box=document.getElementById('shared-evidence');if(!box)return;
  const selected=new Set([...box.querySelectorAll('input:checked')].map(e=>e.value));
  const items=state.task.evidence.filter(e=>e.version_id===state.task.current_version_id&&e.result.status!=='running').slice(-6);
  box.innerHTML=items.length?items.map(e=>`<label class="check"><input type="checkbox" name="share_evidence" value="${esc(e.id)}" ${selected.has(e.id)?'checked':''}><span>${esc(executionLabel(e))} · ${when(e.created_at)}${e.result.hidden?' · 仅共享隐藏测试状态':''}</span></label>`).join(''):empty('当前版本尚无已完成的执行证据。不会用旧版本结果证明当前代码。');
}
async function mountTeachingTools(task,epoch) {
  const form=document.getElementById('chat-form');
  form.insertAdjacentHTML('afterbegin',select('本次辅导目标','purpose',option('chat','连续提问','chat')+option('analyze','生成错误原因候选','chat')+option('summary','生成错题总结草稿','chat'))+
    check('本次直接讲解，允许给出完整解释','explicit_direct')+
    '<details><summary>选择本次共享的执行证据</summary><p class="help">默认不共享。只可选择当前版本；隐藏测试仅提供状态，不发送输入输出。</p><div id="shared-evidence"></div></details>');
  form.querySelector('.actions').insertAdjacentHTML('beforeend','<button type="button" id="cancel-chat" hidden>取消回复</button>');
  document.getElementById('cancel-chat').addEventListener('click',async()=>{
    const request=state.chatRequest;if(!request)return;
    try {const result=await api(`${request.path}/${encodeURIComponent(request.request_id)}/cancel`,'POST');notify(result.message);}catch(error){notify(error.message,true);}
  });
  form.elements.purpose.addEventListener('change',()=>{
    if(form.elements.purpose.value!=='chat'&&!form.elements.message.value.trim())form.elements.message.value=form.elements.purpose.value==='analyze'?'请根据当前代码和我选择的证据提出待确认错误原因。':'请生成可编辑的错题总结草稿；没有执行的修复请保留不确定。';
    document.getElementById('generation-notice').hidden=form.elements.purpose.value==='chat';updateDirty();
  });
  form.insertAdjacentHTML('beforeend','<p class="help" id="generation-notice" hidden>每次生成或重新生成都需要一次模型调用，可能额外计费。保存与处理已有草稿不调用模型。</p>');
  const notes=document.getElementById('mistake-panel');
  notes.insertAdjacentHTML('beforeend','<section class="space-top"><h3>AI 原因与总结草稿</h3><p class="help">推断由你确认；程序执行结果始终以运行记录为准。</p><div id="teaching-drafts"></div><details><summary>已保存的总结版本</summary><div id="summary-versions"></div></details></section>');
  const side=document.getElementById('associations-panel');
  side.insertAdjacentHTML('beforeend','<section class="space-top"><h3>相关练习与错题候选</h3><div id="learning-suggestions"></div></section>');
  renderSharedEvidence();renderTeachingDrafts();await renderLearningSuggestions(epoch,task.id);
}
function renderTeachingDrafts() {
  const box=document.getElementById('teaching-drafts');if(!box)return;
  if([...box.querySelectorAll('form[data-draft]')].some(dirtyForm))return;
  const labels={draft:'待处理草稿',accept:'用户确认',modified:'用户修改后保存',rejected:'已拒绝',ignored:'已忽略',uncertain:'尚未确定'};
  box.innerHTML=(state.task.teaching_outputs||[]).slice().reverse().map(d=>{
    const turn=state.task.turns.find(t=>t.id===d.turn_id);const p=d.payload;
    const text=d.kind==='analyze'?p.mechanism:p.sections.map(s=>s.heading+'\n'+s.text).join('\n\n');
    const savedRecord=d.kind==='analyze'?(state.task.errors||[]).find(e=>e.id===d.saved_id):(state.task.summary_versions||[]).find(s=>s.id===d.saved_id);
    const savedText=savedRecord?(d.kind==='analyze'?savedRecord.reason:savedRecord.text):text;
    return `<article class="error-record"><div class="row wrap-mobile"><h4>${d.kind==='analyze'?'错误原因候选':'错题总结草稿'}</h4><span class="badge">${labels[d.status]||esc(d.status)}</span></div><p class="help">${esc(vlabel(turn?.version_id))} · ${when(turn?.created_at)}</p>
      ${d.kind==='analyze'?`<p class="text-block"><strong>相关知识点</strong> ${esc(p.knowledge.join('、'))}</p><p class="text-block"><strong>修改方向</strong> ${esc(p.repair)}</p><p class="help">认知假设：${esc(p.cognitive_hypothesis)}</p>`:''}
      <p class="help">不确定性：${esc(p.uncertainties.join('；'))}</p><p class="help">证据引用：${esc(p.evidence_refs.join('、')||'未引用执行证据')}</p>
      ${d.checks.conflicts.length?'<p class="status-note error">结构化事实与执行记录冲突，已阻止确认。请核对原始运行记录。</p>':''}
      <p class="help">${esc(d.checks.scope)}</p>
      ${d.status==='draft'?`<form id="teaching-${esc(d.turn_id)}" data-draft>${d.kind==='analyze'?area('实际表现（AI描述，需核对）','observed',p.observed,'maxlength="5000" required'):''}${area(d.kind==='analyze'?'原因草稿':'总结草稿','text',text,'maxlength="10000" required')}<div class="actions wrap-mobile"><button type="submit" name="decision" value="accept" class="primary" ${d.checks.conflicts.length?'disabled':''}>确认保存</button><button type="submit" name="decision" value="modified" ${d.checks.conflicts.length?'disabled':''}>按修改保存</button><button type="submit" name="decision" value="uncertain" ${d.kind!=='analyze'?'hidden':''}>尚未确定</button><button type="submit" name="decision" value="rejected">拒绝</button><button type="submit" name="decision" value="ignored">忽略</button></div></form>`:`<pre class="text-block">${esc(savedText)}</pre>`}</article>`;
  }).join('')||empty('尚无AI草稿。在连续辅导中选择生成目标；REAL_AI_PARTIAL：真实AI尚未完成完整教学验收。');
  for(const draft of state.task.teaching_outputs||[]) {
    const form=document.getElementById('teaching-'+draft.turn_id);if(!form)continue;
    saved(form);
    form.addEventListener('submit',event=>{event.preventDefault();const button=event.submitter;if(!button)return;
      const actionName=button.value;action(form,async()=>{
        const data=new FormData(form);const changed=dirtyForm(form);
        const decision=actionName==='accept'&&changed?'modified':actionName;
        await api('/api/teaching/'+encodeURIComponent(draft.turn_id)+'/decision','POST',{
          action:decision,text:data.get('text'),observed:data.has('observed')?data.get('observed'):null});
        saved(form);const task=await api('/api/tasks/'+state.task.id);state.task=task;
        const noteForm=document.getElementById('mistake-form');
        if(task.mistake&&noteForm&&!dirtyForm(noteForm)){
          noteForm.elements.summary.value=task.mistake.summary;
          noteForm.elements.original_version_id.value=task.mistake.original_version_id;
          noteForm.elements.repair_version_id.value=task.mistake.repair_version_id||'';
          saved(noteForm);
        }
        renderTeachingDrafts();renderErrors();
        if(!dirtyForm(document.getElementById('review-form'))){renderReview();const reviewForm=document.getElementById('review-form');if(reviewForm)saved(reviewForm);}
        notify('草稿处理结果已保存；历史版本保留。');
      });
    });
  }
  const history=document.getElementById('summary-versions');
  history.innerHTML=(state.task.summary_versions||[]).map(s=>`<article><p class="help">${esc(vlabel(s.version_id))} · ${when(s.created_at)}</p><pre class="text-block">${esc(s.text)}</pre></article>`).join('')||empty('尚无已确认的AI总结。');
}
async function refreshTeachingHistory() {
  const task=await api('/api/tasks/'+state.task.id);
  state.task.teaching_outputs=task.teaching_outputs;state.task.summary_versions=task.summary_versions;
  renderTeachingDrafts();
}
async function renderLearningSuggestions(epoch,taskId) {
  const box=document.getElementById('learning-suggestions');if(!box)return;
  try {
    const result=await api('/api/learning/recommendations'+(taskId?'?task_id='+encodeURIComponent(taskId):''));
    if(epoch!==state.epoch)return;
    box.innerHTML=(!result.multi_difficulty_supported?'<p class="help">现有20道题均为基础难度；按共同知识点安排巩固与迁移顺序，不代表更高难度匹配。</p>':'')+(result.items.length?result.items.map(item=>{
      const basis=item.basis;
      return `<article class="error-record" data-learning-rec="${esc(item.id)}"><h4>${esc(item.title)}</h4><p class="text-block">${esc(item.reason)}</p><details><summary>查看推荐依据</summary><p class="help">${esc(basis.evidence_requirement)}</p><p>共同知识点：${esc(basis.shared_knowledge.join('、')||'见双方原因记录')}</p>${basis.difficulty_note?`<p class="help">${esc(basis.difficulty_note)}</p>`:''}<p class="help">推荐类型：${basis.relationship==='knowledge_related'?'知识点相关':'相似机制的待核对候选'}</p><a href="${taskUrl(basis.task_id)}">查看来源练习</a></details><p class="help" data-learning-status>${({pending:'待处理',accepted:'已接受',ignored:'已忽略',completed:'已完成'})[item.status]||esc(item.status)}</p>${item.question_id?`<a href="#/bank/${encodeURIComponent(item.question_id)}">打开推荐练习 →</a>`:`<a href="${taskUrl(basis.other_task_id)}">查看另一道错题 →</a>`}<div class="actions space-top">${['accepted','ignored','completed'].map(s=>`<button type="button" data-learning-action="${s}">${({accepted:'接受',ignored:'忽略',completed:'完成'})[s]}</button>`).join('')}</div></article>`;
    }).join(''):empty('当前没有充分依据提出相关练习。补充知识分类和错误记录后可再查看。'));
    box.querySelectorAll('[data-learning-action]').forEach(button=>button.addEventListener('click',()=>{
      const card=button.closest('[data-learning-rec]');action(card,async()=>{
        const item=await api('/api/recommendations/'+card.dataset.learningRec,'PATCH',{status:button.dataset.learningAction});
        card.querySelector('[data-learning-status]').textContent={accepted:'已接受',ignored:'已忽略',completed:'已完成'}[item.status];
        notify('推荐决策已保存，不会自动确认错误关联。');
      });
    }));
  } catch(error) {box.textContent='推荐暂不可用：'+error.message;}
}
async function mountAISettings(status) {
  const form=document.getElementById('ai-form');const fields=document.getElementById('deepseek-fields');
  fields.insertAdjacentHTML('afterbegin',select('模型（官方文档核对于 '+esc(status.ai.catalog_verified_on)+'）','model',
    (status.ai.available_models||['deepseek-flash']).map(m=>option(m,m,status.ai.model)).join(''))+
    field('本次费用上限（美元，按保守峰值费率预留）','budget_usd',String(status.ai.budget_usd||0.5),'type="number" min="0.000001" max="100" step="0.000001" required')+
    field('请求超时（秒）','timeout',String(status.ai.timeout||45),'type="number" min="1" max="120" required'));
  form.insertAdjacentHTML('afterend','<section id="ai-retest-approval" class="space-top" aria-labelledby="retest-heading"></section><div id="ai-usage" class="space-top"></div>');
  await refreshAIUsage();
  await refreshRetestApproval();
}
function roundAIUsage() {
  const round=state.status?.ai?.round_authorization;if(!round)return '';
  const stage=round.current_stage;
  const used=stage?.attempts??stage?.used_requests??round.stage_attempts??0;
  const remaining=stage?.remaining_requests??stage?.phase_remaining??round.phase_remaining??Math.max(0,(stage?.max_new_requests??4)-used);
  const effective=Object.hasOwn(round,'effective_stop_reason')?round.effective_stop_reason:round.stop_reason;
  return `<h3>跨学习库的第8轮累计账本</h3><p>累计发送尝试 ${esc(round.attempts??0)} / ${esc(round.max_requests??8)} 次 · 已知费用估算 $${Number(round.known_cost_usd||0).toFixed(6)} / $${Number(round.budget_usd??0.5).toFixed(2)}</p><p class="help">未知费用保守预留 $${Number(round.reserved_unknown_cost_usd||0).toFixed(6)}。保存设置、重启或换库均不恢复累计额度。</p><p class="help text-block">历史停因：${esc(round.historical_stop_reason??round.stop_reason??'无')}；当前停止值：${esc(effective||'无')}。</p>${stage?`<p class="help text-block">当前复验阶段 ${esc(stage.id||stage.phase||'已批准')} · 阶段已用 ${esc(used)} / ${esc(stage.max_new_requests??4)} 次 · 剩余 ${esc(remaining)} 次。</p>`:''}`;
}
async function refreshRetestApproval(errorMessage='') {
  const box=document.getElementById('ai-retest-approval');if(!box)return;
  let review;
  try {review=await api('/api/ai/retest');}
  catch(error){if(document.getElementById('ai-retest-approval')===box)box.innerHTML='<h3 id="retest-heading">受控复验授权</h3><p id="retest-status" class="status-note error" role="alert">'+esc(errorMessage?errorMessage+'；'+error.message:error.message)+'</p><p class="help">复验范围暂不可核对，未提供新的批准表单。普通 AI 设置仍可使用。</p>';return;}
  if(document.getElementById('ai-retest-approval')!==box)return;
  const round=state.status?.ai?.round_authorization||{};
  const stage=review.current_stage||round.current_stage;
  const challenge=review.challenge;
  const reviewText=value=>esc(value&&typeof value==='object'?JSON.stringify(value,null,2):value);
  const expiresAt=typeof challenge?.expires_at==='number'&&challenge.expires_at<1e12?challenge.expires_at*1000:challenge?.expires_at;
  const canApprove=review.eligible&&!stage&&challenge?.id&&challenge?.token&&challenge?.review_fingerprint;
  const used=stage?.attempts??stage?.used_requests??round.stage_attempts??0;
  const remaining=stage?.remaining_requests??stage?.phase_remaining??round.phase_remaining??Math.max(0,(stage?.max_new_requests??review.max_new_requests??4)-used);
  const effective=Object.hasOwn(round,'effective_stop_reason')?round.effective_stop_reason:round.stop_reason;
  box.innerHTML=`<h3 id="retest-heading">受控复验授权</h3>${errorMessage?`<p class="status-note error" role="alert">${esc(errorMessage)}</p>`:''}<div id="retest-review"><p class="help text-block">历史已尝试 ${esc(review.historical_attempts)} 次；历史停因 ${esc(review.historical_stop_reason)}。历史请求、费用与停因保留。</p><p class="help text-block">已采取的修补：${esc(review.repair_summary)}</p><p>本阶段新增最多 ${esc(review.max_new_requests)} 次；第8轮累计最多 ${esc(review.cumulative_max_requests)} 次，总预算上限 US$${Number(review.cumulative_budget_usd).toFixed(2)}，包含历史已知费用 $${Number(review.known_cost_usd||0).toFixed(6)}。仅使用 ${esc(review.model)}。</p><p class="help">批准后，主动辅导操作可向第三方 DeepSeek 发送题目、代码、所选执行证据及相关历史，调用可能产生费用。批准本身不发送模型请求。</p><details><summary>核对修订与复验依据</summary><p class="help text-block">代码修订：${reviewText(review.code_revision)}</p><p class="help text-block">复验依据：${reviewText(review.review_basis)}</p><p class="help text-block">冻结计划 SHA-256：${esc(review.frozen_plan_sha256)}</p></details></div><p id="retest-status" class="status-note text-block" role="status">${stage?`复验阶段 ${esc(stage.id||stage.phase||'已批准')} 已存在；阶段已用 ${esc(used)} / ${esc(stage.max_new_requests??review.max_new_requests)} 次，剩余 ${esc(remaining)} 次。当前停止值：${esc(effective||'无')}。${esc(review.reason||'')}`:esc(review.reason||'未批准，真实请求仍锁定。')}</p>${canApprove?`<form id="retest-approval-form" autocomplete="off">${field('本次复验重新填写 API Key','api_key','','type="password" autocomplete="new-password" maxlength="4096" required placeholder="仅存于当前服务内存"')}<label class="check"><input type="checkbox" name="confirm_scope" required><span>我已核对历史、修补、代码修订和复验依据，仅批准上方次数、预算及 Flash 模型范围。</span></label><label class="check"><input type="checkbox" name="approve_paid_calls" required><span>我明确同意向第三方 DeepSeek 发送上述学习材料，并承担本范围内可能产生的调用费用。</span></label><p class="help">本次确认有效至 ${esc(when(expiresAt))}。取消填写不会授权；提交失败后须重新核对并手动批准。</p><div class="actions"><button type="submit" class="primary">批准受控复验并启用</button><button type="reset">取消本次填写</button></div></form>`:''}`;
  const form=document.getElementById('retest-approval-form');if(!form)return;
  form.addEventListener('submit',event=>{
    event.preventDefault();if(!form.reportValidity())return;
    action(form,async()=>{
      const key=form.elements.api_key.value.trim();
      if(!key||!form.elements.confirm_scope.checked||!form.elements.approve_paid_calls.checked)throw new Error('请重新填写本次复验密钥，并勾选两项明确确认。');
      try {
        const result=await api('/api/ai/retest/approve','POST',{api_key:key,challenge_id:challenge.id,confirmation_token:challenge.token,review_fingerprint:challenge.review_fingerprint,confirm_scope:true,approve_paid_calls:true});
        form.reset();state.status.ai=result.ai||result;
        const status=document.getElementById('settings-ai-status');if(status)status.textContent=aiLabel(state.status.ai);
        await refreshAIUsage();await refreshRetestApproval();notify('受控复验已批准；历史账本保留。尚未发送模型请求。');
      } catch(error) {
        form.reset();await refreshRetestApproval(error.message);notify(error.message,true);
      }
    });
  });
}
async function refreshAIUsage() {
  const box=document.getElementById('ai-usage');if(!box)return;
  try {const u=await api('/api/ai/usage');
    const connection={not_connected:'未连接',not_tested:'尚未发起请求',connected:'上次请求成功',auth_failed:'凭据无效',timeout:'请求超时',rate_limited:'服务商限流',cancelled:'已取消',provider_error:'服务暂不可用',model_unavailable:'模型不可用',incompatible_response:'回复格式无效'}[state.status.ai.connection]||'需核对服务状态';
    box.innerHTML=roundAIUsage()+`<h3>当前学习库调用镜像</h3><p class="help">以下仅为当前学习库记录，不代表跨库累计额度；换库显示 0 也不会重置上方账本。</p><p>真实请求 ${u.real_requests} 次 · 已知Token ${u.real_known_tokens} · 已知费用估算 $${Number(u.real_known_cost_usd).toFixed(6)}</p><p>离线替身 ${u.fixture_requests} 次 · 夹具Token ${u.fixture_known_tokens} · 夹具费用估算 $${Number(u.fixture_known_cost_usd).toFixed(6)}（不计费）</p><p>用量未知 ${u.unknown_usage_requests} 次 · 费用保守预留 $${Number(u.reserved_unknown_cost_usd||0).toFixed(6)}</p><p class="help">${esc(u.cost_notice)}</p><p class="help">连接状态：${connection}。保存设置不会探测连接。</p>`;
  }catch(error){box.textContent=error.message;}
}
async function mountImportTools(form,epoch) {
  const upload=document.getElementById('image-upload');
  upload.querySelector('p.help').textContent='图片仅发送到本机服务临时识别，不保存原图，不使用云OCR。识别文字需编辑确认。';
  upload.insertAdjacentHTML('beforeend','<button type="button" id="recognize-image">本地识别截图</button><p id="ocr-status" class="help" role="status"></p><details id="ocr-draft" hidden><summary>识别草稿 · 核对后应用到表单</summary><div id="ocr-draft-fields"></div></details>');
  const sourceInput=form.elements.source_url;
  sourceInput.closest('label').insertAdjacentHTML('afterend','<button type="button" id="recognize-source">识别链接来源候选</button><p id="source-candidate" class="help" role="status">来源和题号可以手动填写；候选必须由你核对。</p>'+check('我已核对来源平台、原始题号与题目内容','source_confirmed'));
  const info=document.querySelector('.import-aside .import-notes > div:last-child dd');
  if(info)info.textContent='本地OCR提取文字后，先编辑草稿再应用到表单；保存题目由你确认。';
  form.elements.import_status.addEventListener('change',()=>{
    if(form.elements.import_status.value==='pending_ocr')document.getElementById('import-mode-note').textContent='截图导入：本机OCR → 核对草稿 → 保存。无法识别时仍可手工填写。';
  });
  function sourceCandidate(candidate) {
    form.sourceCandidate=candidate;form.elements.source_confirmed.checked=false;
    document.getElementById('source-candidate').textContent=`${candidate.platform||'平台未识别'} · ${candidate.original_id||'题号未识别'}。依据：${candidate.basis.join('；')}。`+
      (candidate.duplicates?.length?'发现可能重复：'+candidate.duplicates.map(t=>t.title).join('、')+'；保存会创建新记录，不会覆盖。':'');
  }
  document.getElementById('recognize-source').addEventListener('click',()=>action(form,async()=>{
    const candidate=await api('/api/import/source','POST',{url:form.elements.source_url.value});
    sourceCandidate(candidate);
    if(candidate.platform)form.elements.source_platform.value=candidate.platform;
    if(candidate.original_id)form.elements.original_id.value=candidate.original_id;
    updateDirty();
  }));
  for(const key of ['source_url','source_platform','original_id'])form.elements[key].addEventListener('input',()=>{form.elements.source_confirmed.checked=false;});
  const status=await api('/api/import/status');if(epoch!==state.epoch)return;
  document.getElementById('ocr-status').textContent=status.ocr_available?'本地CPU引擎可用。':'本地OCR未安装；可以手动导入。';
  document.getElementById('recognize-image').addEventListener('click',()=>action(form,async()=>{
    const file=form.elements.image.files[0];if(!file)throw new Error('请先选择PNG、JPEG或WebP题目截图。');
    if(file.size>5*1024*1024)throw new Error('图片不能超过5MB。');
    document.getElementById('ocr-status').textContent='正在本机识别，最多等待45秒…';
    const buffer=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<buffer.length;i+=8192)binary+=String.fromCharCode(...buffer.subarray(i,i+8192));
    let draft;
    try {draft=await api('/api/import/ocr','POST',{image_base64:btoa(binary)});}catch(error){document.getElementById('ocr-status').textContent='识别未完成：'+error.message;throw error;}
    const box=document.getElementById('ocr-draft');box.hidden=false;box.open=true;
    document.getElementById('ocr-draft-fields').innerHTML=field('识别标题草稿','ocr_title',draft.title,'maxlength="160"')+area('识别正文草稿（含可见样例）','ocr_statement',draft.statement,'maxlength="30000"')+'<p class="help">'+esc(draft.notice)+'</p><button type="button" id="apply-ocr-draft">应用已核对的识别草稿</button>';
    document.getElementById('ocr-status').textContent=draft.engine+' · 已识别 '+draft.lines.length+' 行；尚未创建题目。';
    document.getElementById('apply-ocr-draft').addEventListener('click',()=>{
      form.elements.title.value=form.elements.ocr_title.value;form.elements.statement.value=form.elements.ocr_statement.value;
      sourceCandidate(draft.source);
      if(draft.source.platform)form.elements.source_platform.value=draft.source.platform;
      if(draft.source.original_id)form.elements.original_id.value=draft.source.original_id;
      updateDirty();notify('识别草稿已应用。请继续核对来源和题号，再保存。');
    });updateDirty();
  }));
}
