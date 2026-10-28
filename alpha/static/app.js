'use strict';

const app = document.querySelector('#app');
const notice = document.querySelector('#notice');
const state = {task:null, status:null, route:'', epoch:0, busy:0, baselines:new Map(), noticeTimer:null, indentWithTab:false};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lang = value => (state.languages?.[value]?.display_name || state.status?.execution?.languages?.[value]?.display_name || value);
const when = value => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '';
const outcome = value => ({understood:'已理解',unsure:'仍不确定',retry:'需要重做'}[value] || value);
const statusName = value => ({completed:'运行完成',compile_error:'编译失败',exception:'运行异常',timeout:'执行超时',resource_limit:'资源限制终止',cancelled:'已停止',unavailable:'环境未就绪',unsupported:'暂不支持',running:'结果待确认',interrupted:'执行中断',service_error:'执行服务异常',invalid_input:'输入无效',output_limit:'输出超限',passed:'通过',wrong_answer:'输出不一致',failed:'失败',not_executed:'未执行',not_completed:'未完成'}[value] || value || '未提供');
const languageOptions = value => Object.keys(state.languages || state.status?.execution?.languages || {}).map(x => `<option value="${esc(x)}" ${x===value?'selected':''}>${esc(lang(x))}</option>`).join('');
const field = (label,name,value='',extra='') => `<label class="field"><span>${label}</span><input name="${name}" value="${esc(value)}" ${extra}></label>`;
const area = (label,name,value='',extra='') => `<label class="field"><span>${label}</span><textarea name="${name}" ${extra}>${esc(value)}</textarea></label>`;
const check = (label,name,checked=false) => `<label class="check"><input type="checkbox" name="${name}" ${checked?'checked':''}><span>${label}</span></label>`;
const select = (label,name,options,extra='') => `<label class="field"><span>${label}</span><select name="${name}" ${extra}>${options}</select></label>`;
const option = (value,label,current) => `<option value="${esc(value)}" ${value===current?'selected':''}>${esc(label)}</option>`;
const empty = message => `<p class="empty">${message}</p>`;
const taskUrl = id => `#/task/${encodeURIComponent(id)}`;
const executionLabel = record => record.kind==='external'?record.result.status:record.result.status==='completed'&&record.result.matches_expected!==undefined?(record.result.matches_expected?'期望输出一致':'输出不一致'):statusName(record.result.status);

async function api(path, method='GET', body) {
  let response;
  try { response = await fetch(path, {method, headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body):undefined}); }
  catch (_) { throw new Error('无法连接本机服务。编辑内容仍在，请确认服务已启动后重试。'); }
  let data;
  try { data = await response.json(); } catch (_) { throw new Error('服务返回了无法读取的结果。请保留编辑内容，稍后重试。'); }
  if (!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : '操作未成功，请检查填写内容后重试。');
  return data;
}
function notify(message,error=false) {
  clearTimeout(state.noticeTimer); notice.textContent=message; notice.className=error?'error':''; notice.hidden=false;
  state.noticeTimer=setTimeout(()=>notice.hidden=true,error?12000:5000);
}
function serialize(form) { return JSON.stringify(Array.from(new FormData(form).entries())); }
function watchForms() { app.querySelectorAll('form[data-draft]').forEach(form=>state.baselines.set(form.id,serialize(form))); }
function dirtyForm(form) { return !!form && state.baselines.has(form.id) && state.baselines.get(form.id)!==serialize(form); }
function dirty() { return Array.from(app.querySelectorAll('form[data-draft]')).some(dirtyForm); }
function saved(form, snapshot=serialize(form)) { state.baselines.set(form.id,snapshot); updateDirty(); }
function updateDirty() {
  const indicator=document.querySelector('#code-dirty');
  if(indicator) indicator.textContent=dirtyForm(document.querySelector('#task-form'))?'有未保存修改':'已保存';
  app.querySelectorAll('[data-form-state]').forEach(node=>{const form=document.getElementById(node.dataset.formState);node.textContent=dirtyForm(form)?'有未保存修改':'已保存至本机';node.dataset.dirty=String(dirtyForm(form));});
}
async function action(form, handler) {
  if(form.dataset.busy) return;
  if(state.busy) { notify('上一项操作仍在处理。你可以继续编辑，完成后再保存或提交。'); return; }
  form.dataset.busy='1'; state.busy++;
  const buttons=Array.from(form.querySelectorAll('button')); const prior=buttons.map(b=>b.disabled);
  buttons.forEach(b=>b.disabled=true); form.setAttribute('aria-busy','true');
  form.querySelector(':scope > .form-error')?.remove();
  try { await handler(); } catch(error) {
    const feedback=document.createElement('p');feedback.className='status-note error form-error';feedback.setAttribute('role','alert');feedback.textContent=error.message;
    form.append(feedback);notify(error.message,true);
  }
  finally { state.busy--; delete form.dataset.busy; form.removeAttribute('aria-busy'); buttons.forEach((b,i)=>b.disabled=prior[i]); }
}
async function submitOperation(path, body, collection) {
  // Keep only a payload hash and request ID across reloads, never API credentials.
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(body)));
  const signature=Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');
  const key='alpha-request:'+path;
  let previous;
  try {previous=JSON.parse(sessionStorage.getItem(key)||'null');}catch(_){}
  const pending=previous?.signature===signature?previous:{signature,request_id:crypto.randomUUID()};
  sessionStorage.setItem(key,JSON.stringify(pending));
  const cancelButton=document.getElementById(collection==='evidence'?'cancel-run':'cancel-chat');
  if(cancelButton){state[collection==='evidence'?'runRequest':'chatRequest']={path,request_id:pending.request_id};cancelButton.hidden=false;cancelButton.disabled=false;}
  try {
    const item=await api(path,'POST',{...body,request_id:pending.request_id});
    sessionStorage.removeItem(key);return item;
  } catch(error) {
    // A lost response may already be saved. Reconcile before offering a retry;
    // unresolved retries retain the reservation ID and cannot repeat paid work.
    let record;
    try {const task=await api(path.replace(/\/(run|chat)$/,''));record=task[collection].find(x=>x.request_id===pending.request_id);}catch(_){}
    if(record && (collection==='turns'?record.status!=='running':record.result.status!=='running')) {
      sessionStorage.removeItem(key);
      if(collection==='evidence'||record.status==='completed')return record;
    }
    throw error;
  } finally {
    if(cancelButton){cancelButton.hidden=true;state[collection==='evidence'?'runRequest':'chatRequest']=null;}
  }
}
function mergeRecord(collection,item) {
  const index=state.task[collection].findIndex(x=>x.id===item.id);
  if(index<0)state.task[collection].push(item);else state.task[collection][index]=item;
}
function bindForm(id, handler) {
  const form=document.getElementById(id); if(!form)return;
  form.addEventListener('submit', event=>{event.preventDefault(); if(form.reportValidity()) action(form,()=>handler(form,new FormData(form)));});
}
function requireSaved() { if(dirtyForm(document.querySelector('#task-form'))) throw new Error('题目或代码有未保存修改。请先保存版本，再运行或发送问题。'); }
function currentVersion(task=state.task) { return task.versions.find(v=>v.id===task.current_version_id); }
function vlabel(id) { const v=state.task?.versions.find(v=>v.id===id); return v ? `v${v.number} · ${lang(v.language)}`:'版本不可用'; }
function versionOptions(selected,blank=false) { return (blank?'<option value="">暂不关联</option>':'')+state.task.versions.map(v=>option(v.id,`v${v.number} · ${lang(v.language)}${v.id===state.task.current_version_id?' · 当前':''}`,selected)).join(''); }
function aiLabel(ai) {
  if(!ai)return 'AI 状态暂不可用';
  if(ai.mode==='demo')return '模拟辅导 · 非模型回答';
  if(ai.mode==='disabled')return 'AI 已关闭';
  if(ai.transport==='fixture')return `离线协议替身 · 非真实模型 · 剩余 ${ai.remaining_approved_requests} 次测试请求`;
  return ai.real_enabled?`DeepSeek 已授权 · 剩余 ${ai.remaining_approved_requests} 次`:'DeepSeek 未启用真实请求';
}
function taskFields(task,version) {
  return field('题目名称','title',task?.title||'','required maxlength="160" autocomplete="off"')+
    area('题目描述','statement',task?.statement||'','maxlength="30000" placeholder="粘贴题干、输入输出要求或你要解决的问题"')+
    field('来源链接（可选）','source_url',task?.source_url||'','type="url" maxlength="2000" placeholder="https://…"')+
    select('编程语言','language',languageOptions(version?.language||'python'))+
    area('代码','code',version?.source||'',`class="code ${task?'':'new-code'}" spellcheck="false" autocapitalize="off" autocomplete="off" maxlength="80000" aria-label="代码"`);
}
function taskBody(data) { return {title:data.get('title'),statement:data.get('statement'),source_url:data.get('source_url'),language:data.get('language'),code:data.get('code'),...sourceBody(data)}; }
async function home(epoch) { return finalHome(epoch); }
async function taskPage(id,epoch) {
  const [task,status]=await Promise.all([api(`/api/tasks/${encodeURIComponent(id)}`),api('/api/status'),loadLanguages()]); if(epoch!==state.epoch)return;
  state.task=task;state.status=status;
  app.innerHTML=`<div class="page-heading row"><div><p class="eyebrow">练习工作区</p><h1 id="task-title" class="task-title">${esc(task.title)}</h1><p class="muted">保存代码版本后可运行或自由追问，每条反馈都保留对应版本。</p></div><a href="#/">返回学习首页</a></div><div class="workspace"><div class="work-main"><section class="panel"><form id="task-form" data-draft><details><summary>题目与来源</summary>${field('题目名称','title',task.title,'required maxlength="160"')}${area('题目描述','statement',task.statement,'maxlength="30000"')}${field('来源链接（可选）','source_url',task.source_url,'type="url" maxlength="2000"')}${task.source_url?`<a class="source-link" href="${esc(safeLink(task.source_url))}" target="_blank" rel="noopener noreferrer">打开原题来源</a>`:''}</details><div class="row space-top wrap-mobile"><h2 class="inline-title">代码编辑</h2><span id="version-label" class="badge"></span></div><div class="space-top">${select('编程语言','language',languageOptions(currentVersion().language))}${area('代码','code',currentVersion().source,'class="code" spellcheck="false" autocapitalize="off" autocomplete="off" maxlength="80000"')}</div><div class="actions"><button class="primary" type="submit">保存版本</button><span id="code-dirty" class="dirty-indicator"></span><span class="key-help">Ctrl / ⌘ + S 保存</span></div></form></section><section class="panel"><h2>运行与反馈</h2><p id="execution-status" class="status-note"></p><form id="run-form" data-draft>${area('标准输入','stdin','','maxlength="16000" placeholder="不需要输入时可以留空"')}${check('比较期望输出（勾选后，留空表示期望为空）','compare')}${area('期望输出','expected_output','','maxlength="16000" disabled')}<div class="actions"><button type="submit" class="primary">运行已保存版本</button><span class="help">单次运行不代表隐藏用例通过。</span></div></form><details><summary>录入外部判题</summary><p class="help">如牛客、PTA 的反馈。仅记录你的输入，不作本地核验。</p><form id="external-form" data-draft>${select('判题对应版本','version_id',versionOptions(task.current_version_id))}<div class="two-col">${field('判题平台','source','','required maxlength="120" placeholder="例如 PTA"')}${field('判题结果','verdict','','required maxlength="120" placeholder="例如 答案错误"')}</div>${area('反馈说明','notes','','maxlength="10000"')}<button type="submit">保存外部反馈</button></form></details><div id="evidence-list" class="space-top"></div></section><details class="panel"><summary>历史代码版本</summary><p class="help">历史版本只读；版本上的反馈与辅导不会迁移到新代码。</p>${select('查看历史版本','history-version',versionOptions(task.current_version_id),'id="history-version"')}<div id="history-code"></div></details><details class="panel scroll-target" id="mistake-panel"><summary>错题笔记与复习</summary><p class="help">按自己的理解整理。AI 标签只是草稿，由你决定是否确认。</p><form id="mistake-form" data-draft><div class="two-col">${select('原错代码版本','original_version_id',versionOptions(task.mistake?.original_version_id||task.current_version_id))}${select('修复代码版本（可选）','repair_version_id',versionOptions(task.mistake?.repair_version_id||'',true))}</div>${area('我的总结','summary',task.mistake?.summary||'','maxlength="10000" placeholder="哪里出了问题？下次如何发现？"')}${field('标签（用逗号分隔）','tags',(task.mistake?.tags||[]).join('，'),'placeholder="由你概括错误原因，每个标签最多 40 字"')}${check('我确认这些标签适用于这道题','confirmed',task.mistake?.confirmed)}${check('需要后续复习','needs_review',task.mistake?.needs_review??true)}<div class="actions"><button class="primary" type="submit">保存错题笔记</button><button type="button" id="suggest-tags">引用 AI 候选标签</button></div><p id="tag-help" class="help space-top"></p></form><div id="review-container"></div></details></div><aside class="work-chat panel" aria-label="连续辅导"><div class="row"><h2 class="inline-title">连续辅导</h2><a href="#/settings" class="tiny">配置 AI</a></div><p id="ai-status" class="status-note space-top"></p><p class="help">可以直接提问，不必先运行。历史消息标明当时的代码版本。</p><div id="chat-log" class="chat-log" aria-live="polite"></div><form id="chat-form" class="chat-form" data-draft>${area('你的问题','message','','required maxlength="8000" placeholder="写下卡住的地方，或继续追问上一轮…"')}<div class="actions"><button type="submit" class="primary">发送问题</button><span class="key-help">Ctrl / ⌘ + Enter</span></div></form></aside></div>`;
  bindTaskForms(); updateTaskParts(); renderReview(); await mountIntegratedTask(task,epoch); if(epoch!==state.epoch)return; await mountTeachingTools(task,epoch); if(epoch!==state.epoch)return; finalArrangeWorkspace(); watchForms();
}
function arrangeWorkspace() { return finalArrangeWorkspace(); }
function updateTaskParts() {
  document.getElementById('task-title').textContent=state.task.title;
  const description=document.querySelector('.task-context .problem-text');if(description)description.textContent=state.task.statement;
  const source=document.querySelector('.task-context details:not(.problem-body) .source-grid');if(source)source.outerHTML=sourceSummary(state.task);
  document.getElementById('version-label').textContent=`当前 ${vlabel(state.task.current_version_id)}`;
  document.getElementById('ai-status').textContent=aiLabel(state.status.ai)+(state.status.ai?.mode==='deepseek'?'。真实 API 效果尚未独立验收；配置不会自动发送请求。':'');
  const language=document.querySelector('#task-form [name="language"]').value;
  const execution=state.status.execution.languages?.[language];
  document.getElementById('execution-status').textContent=execution?.ready?`${lang(language)} · 本机 Docker 隔离环境就绪 · ${execution?.entry_note||''}`:`${lang(language)} · ${execution?.reason||'运行环境状态暂不可用'}`;
  for(const name of ['original_version_id','repair_version_id']) {
    const node=document.querySelector(`#mistake-form [name="${name}"]`);const value=node.value; node.innerHTML=versionOptions(value,name==='repair_version_id');
  }
  for(const node of [document.querySelector('#external-form [name="version_id"]'),document.getElementById('history-version')]) { const value=node.value;node.innerHTML=versionOptions(value); }
  renderEvidence();renderChat();renderHistory();updateTagHelp();updateDirty();
}
function renderEvidence() {
  renderSharedEvidence();
  const records=[...state.task.evidence].reverse();
  document.getElementById('evidence-list').innerHTML=`<h3>反馈记录</h3>${records.length?records.map(e=>{
    const r=e.result;const old=e.version_id!==state.task.current_version_id;
    return `<article class="evidence"><div class="row wrap-mobile"><h3>${esc(e.kind==='external'?r.status:statusName(r.status))}</h3><span class="badge ${old?'warn':''}">${esc(vlabel(e.version_id))}${old?' · 历史版本':' · 当前版本'}</span></div><div class="meta"><span>${e.kind==='external'?'用户录入 · 未经本地核验':'本地隔离运行'}</span><span>${esc(e.source)}</span><span>${when(e.created_at)}</span></div>${old?'<p class="help history-help">此记录属于旧版代码，不能代表当前版本。</p>':''}${r.error?`<p class="status-note error">${esc(r.error)}</p>`:''}${e.kind==='external'?`<div class="text-block">${esc(r.notes||'未补充说明')}</div>`:`<p class="help">程序执行：${r.executed?'已执行':'未确认执行'} · 编译：${esc(statusName(r.compile_status))}${r.elapsed_seconds!==undefined?` · 总耗时 ${esc(r.elapsed_seconds)} 秒`:''}${r.resource_reason?` · 受限资源：${esc(({memory:'内存',pids:'进程数量',cpu:'CPU 时间'})[r.resource_reason]||r.resource_reason)}`:''}${r.matches_expected!==undefined?` · 期望比较：${r.matches_expected?'一致':'不一致'}`:' · 未比较期望输出'}</p><details><summary>输入、输出与编译信息</summary><div class="evidence-label">标准输入</div><pre>${esc(e.stdin||'（空）')}</pre>${e.expected_output!==null?`<div class="evidence-label">期望输出</div><pre>${esc(e.expected_output||'（空）')}</pre>`:''}<div class="evidence-label">标准输出 stdout</div><pre>${esc(r.stdout||'（空）')}</pre><div class="evidence-label">标准错误 stderr</div><pre>${esc(r.stderr||'（空）')}</pre>${r.compile_result?`<div class="evidence-label">编译输出</div><pre>${esc([r.compile_result.stdout,r.compile_result.stderr,r.compile_result.error].filter(Boolean).join('\n')||'（空）')}</pre>`:''}</details>`}</article>`;
  }).join(''):empty('还没有反馈。你可以运行已保存的代码，或记录外部判题结果。')}`;
  document.querySelectorAll('#evidence-list .evidence').forEach((node,index)=>{
    const evidence=records[index], result=evidence.result;
    const tone=evidence.kind==='external'?'neutral':result.matches_expected===true?'success':result.matches_expected===false||['compile_error','exception','timeout','resource_limit','service_error','output_limit'].includes(result.status)?'error':'neutral';
    const title=node.querySelector('h3');title.className='result-heading '+tone;
    title.textContent=executionLabel(evidence);
    const id=document.createElement('p');id.className='help evidence-id';id.textContent='执行证据：'+evidence.id;node.querySelector('.meta').after(id);
  });
  let latest=document.getElementById('latest-feedback');
  if(!latest&&document.getElementById('execution-history')){latest=document.createElement('div');latest.id='latest-feedback';document.getElementById('execution-history').before(latest);}
  if(latest){
    const record=records[0];latest.innerHTML=record?`<div class="section-heading"><h3>最近一次运行反馈</h3><span class="badge neutral">${esc(vlabel(record.version_id))}${record.version_id!==state.task.current_version_id?' · 历史版本':''}</span></div><p class="result-heading ${record.result.matches_expected===false||record.result.error?'error':record.result.matches_expected===true?'success':'neutral'}">${esc(executionLabel(record))}</p><p class="help">${when(record.created_at)} · ${record.kind==='external'?'用户录入，未经本地核验':'绑定已保存代码版本'}</p>${record.result.error?`<p class="status-note error">${esc(record.result.error)}</p>`:''}${record.kind==='external'?`<p class="text-block">${esc(record.result.notes||'未补充说明')}</p>`:record.result.stdout?`<pre>${esc(record.result.stdout)}</pre>`:''}`:'';
  }
}
function renderChat() {
  const log=document.getElementById('chat-log');
  const readings=new Map((state.task.teaching_readings||[]).filter(r=>r.state==='unverified').map(r=>[r.turn_id,r]));
  log.innerHTML=state.task.turns.length?state.task.turns.map(t=>`<article class="turn"><div class="meta"><span>${esc(vlabel(t.version_id))}${t.version_id!==state.task.current_version_id?' · 历史版本':''}</span><span>${t.provider==='demo'?'模拟 · 非模型回答':t.provider==='deepseek'?'DeepSeek':'AI 未连接'}</span><span>${when(t.created_at)}</span></div><p class="help">辅导偏好：${esc(presetLabels[t.context?.learning_preferences?.preset]||'历史设置')} · ${esc(t.context?.purpose==='analyze'?'错误原因候选':t.context?.purpose==='summary'?'总结草稿':'连续提问')}${t.context?.explicit_direct?' · 本次直接讲解':''}</p>${preferenceSnapshot(t)}<div class="question text-block"><strong>你</strong>\n${esc(t.message)}</div>${t.status==='completed'?`<div class="answer text-block">${esc(t.answer)}</div>`:`<div class="status-note ${t.status==='failed'?'error':''}">${esc(t.error||'这次回复尚未确认，请稍后查看记录。')}<br>问题已保留；不会自动重新请求。</div>`}${t.status==='failed'&&readings.has(t.id)?`<div class="space-top"><p class="status-note warn">结构化校验失败 · 未验证解释，仅供阅读；执行事实以运行记录为准</p><div class="text-block" data-teaching-reading="${esc(t.id)}">${esc(readings.get(t.id).text)}</div></div>`:''}</article>`).join(''):empty('还没有对话。写下问题后，辅导记录会连续保存在这里。');
  if(!state.task.turns.length)log.innerHTML=empty(state.status.ai?.mode==='disabled'?'AI 当前关闭。这里会保留未来的辅导与版本记录；你可以先独立练习并记录疑问。':'还没有辅导记录。模拟模式的回复会明确标识，不代表真实代码分析。');
  log.scrollTop=log.scrollHeight;
}
function renderHistory() { const selected=document.getElementById('history-version').value;const v=state.task.versions.find(x=>x.id===selected);document.getElementById('history-code').innerHTML=v?`<p class="meta">${esc(vlabel(v.id))} · ${when(v.created_at)}</p><pre class="readonly-code">${esc(v.source||'（空代码）')}</pre>`:''; }
function tagCandidate() { return [...state.task.turns].reverse().find(t=>t.status==='completed'&&t.suggested_tags?.length); }
function updateTagHelp() { const candidate=tagCandidate();document.getElementById('suggest-tags').disabled=!candidate;document.getElementById('tag-help').textContent=candidate?`可引用 ${vlabel(candidate.version_id)} 的 AI 候选标签。引用将取消确认，请核对后再保存。`:'尚无 AI 候选标签。你可以直接填写自己的标签。'; }
function renderReview() {
  const m=state.task.mistake;const target=document.getElementById('review-container');
  if(!m){target.innerHTML='<p class="help space-top">保存错题笔记后，可以记录复习情况。</p>';return;}
  target.innerHTML=`<details open><summary>记录本次复习</summary><form id="review-form" data-draft>${select('复习情况','outcome',option('understood','已理解','unsure')+option('unsure','仍不确定','unsure')+option('retry','需要重做','unsure'))}${area('复习备注','notes','','maxlength="5000"')}<button type="submit">记录复习</button></form><div class="review-history"><h3>复习历史</h3>${state.task.reviews.length?[...state.task.reviews].reverse().map(r=>`<article class="review-entry"><div class="meta"><span>${outcome(r.outcome)}</span><span>${when(r.created_at)}</span></div><p class="text-block review-note">${esc(r.notes||'未填写备注')}</p></article>`).join(''):empty('还没有复习记录。')}</div></details>`;
  bindForm('review-form',async(form,data)=>{
    const snapshot=serialize(form);const review=await api(`/api/mistakes/${m.id}/reviews`,'POST',{outcome:data.get('outcome'),notes:data.get('notes')});state.task.reviews.push(review);state.task.mistake.needs_review=review.outcome!=='understood';
    const checkbox=document.querySelector('#mistake-form [name="needs_review"]');const mistakeForm=document.getElementById('mistake-form');const wasDirty=dirtyForm(mistakeForm);if(!wasDirty){checkbox.checked=state.task.mistake.needs_review;saved(mistakeForm);}
    saved(form,snapshot); if(serialize(form)===snapshot){renderReview();saved(document.getElementById('review-form'));}else{document.querySelector('.review-history').insertAdjacentHTML('afterbegin',`<p class="help">${when(review.created_at)} · ${outcome(review.outcome)}，已保存。</p>`);} notify('复习记录已保存。');
  });
}
function bindTaskForms() {
  bindForm('task-form',async(form,data)=>{
    const snapshot=serialize(form);const previous=state.task.current_version_id;
    const task=await api(`/api/tasks/${state.task.id}`,'PUT',{...taskBody(data),revision:state.task.revision});state.task=task;saved(form,snapshot);updateTaskParts();
    if(task.current_version_id!==previous) { const external=document.querySelector('#external-form [name="version_id"]');if(!dirtyForm(external.form)){external.value=task.current_version_id;saved(external.form);} }
    await refreshIntegratedTask();
    notify(`已保存 ${vlabel(task.current_version_id)}${dirtyForm(form)?'；保存期间的新修改仍留在编辑区。':'。'}`);
  });
  const runForm=document.getElementById('run-form');runForm.elements.compare.addEventListener('change',()=>runForm.elements.expected_output.disabled=!runForm.elements.compare.checked);
  const cancelButton=document.createElement('button');cancelButton.type='button';cancelButton.id='cancel-run';cancelButton.textContent='停止运行';cancelButton.hidden=true;runForm.after(cancelButton);
  cancelButton.addEventListener('click',async()=>{const pending=state.runRequest;if(!pending)return;cancelButton.disabled=true;try{const result=await api(`${pending.path}/${encodeURIComponent(pending.request_id)}/cancel`,'POST');notify(result.message);}catch(error){notify(error.message,true);cancelButton.disabled=false;}});
  bindForm('run-form',async(form,data)=>{requireSaved();const snapshot=serialize(form);const item=await submitOperation(`/api/tasks/${state.task.id}/run`,{version_id:state.task.current_version_id,stdin:data.get('stdin'),expected_output:data.has('compare')?data.get('expected_output'):null},'evidence');mergeRecord('evidence',item);saved(form,snapshot);renderEvidence();refreshErrorSelectors();notify(item.result.error||'运行反馈已保存。',!!item.result.error);});
  bindForm('external-form',async(form,data)=>{const snapshot=serialize(form);const e=await api(`/api/tasks/${state.task.id}/external`,'POST',Object.fromEntries(data));state.task.evidence.push(e);saved(form,snapshot);renderEvidence();refreshErrorSelectors();notify('外部反馈已保存，并标明用户录入。');});
  bindForm('chat-form',async(form,data)=>{
    requireSaved();const message=data.get('message');const snapshot=serialize(form);const id=state.task.id;
    try { const turn=await submitOperation(`/api/tasks/${id}/chat`,{version_id:state.task.current_version_id,message,...teachingRequest(data)},'turns');mergeRecord('turns',turn); if(serialize(form)===snapshot)form.elements.message.value='';saved(form,JSON.stringify(JSON.parse(snapshot).map(([k,v])=>[k,k==='message'?'':v])));renderChat();updateTagHelp();await refreshTeachingHistory();notify('回复与草稿已保存。'); }
    catch(error) { try { const task=await api(`/api/tasks/${id}`);state.task.turns=task.turns;state.task.teaching_outputs=task.teaching_outputs;state.task.teaching_readings=task.teaching_readings;renderChat();renderTeachingDrafts();updateTagHelp(); }catch(_){} throw error; }
    finally { try {state.status=await api('/api/status');document.getElementById('ai-status').textContent=aiLabel(state.status.ai);}catch(_){} }
  });
  bindForm('mistake-form',async(form,data)=>{const snapshot=serialize(form);const m=await api(`/api/tasks/${state.task.id}/mistake`,'PUT',{original_version_id:data.get('original_version_id'),repair_version_id:data.get('repair_version_id')||null,summary:data.get('summary'),tags:data.get('tags').split(/[,，\n]/).map(x=>x.trim()).filter(Boolean),confirmed:data.has('confirmed'),needs_review:data.has('needs_review')});const first=!state.task.mistake;state.task.mistake=m;saved(form,snapshot);if(first){renderReview();saved(document.getElementById('review-form'));}notify('错题笔记已保存。重复保存会更新同一条笔记。');});
  document.getElementById('suggest-tags').addEventListener('click',()=>{const candidate=tagCandidate();if(!candidate)return;const form=document.getElementById('mistake-form');form.elements.tags.value=candidate.suggested_tags.join('，');form.elements.confirmed.checked=false;notify('候选标签已填入草稿。请核对、编辑后再保存。');});
  document.getElementById('history-version').addEventListener('change',renderHistory);
  document.querySelector('#task-form [name="language"]').addEventListener('change',()=>{const language=document.querySelector('#task-form [name="language"]').value;const e=state.status.execution.languages?.[language];document.getElementById('execution-status').textContent=e?.ready?`${lang(language)} · 本机 Docker 隔离环境就绪 · ${e?.entry_note||''}`:`${lang(language)} · ${e?.reason||'状态暂不可用'}`;});
}
async function library(epoch) {
  const status=await api('/api/status');if(epoch!==state.epoch)return;state.status=status;
  app.innerHTML='<div class="page-heading"><h1>错题库</h1><p class="muted">回看自己的总结，以及当时的代码、反馈和对话。</p></div><section class="panel"><form id="search-form" class="filters">'+field('搜索','q','','placeholder="题目、总结或标签"')+select('语言筛选','language','<option value="">全部语言</option>'+languageOptions(''))+field('标签筛选','tag','','placeholder="精确标签"')+'<button type="submit" class="primary">搜索错题</button></form><div id="library-results" class="space-top"></div></section>';
  async function search(data) {const results=await api('/api/mistakes?'+new URLSearchParams(data));if(epoch!==state.epoch)return;document.getElementById('library-results').innerHTML=results.length?results.map(m=>`<article class="library-item"><h2><a href="${taskUrl(m.task_id)}">${esc(m.title)}</a></h2><div class="text-block">${esc(m.summary||'尚未写总结')}</div><div class="meta"><span>${lang(m.language)}</span><span>${m.confirmed?'标签已确认':'标签待确认'}</span><span>${m.needs_review?'需要复习':'暂不需要复习'}</span>${m.tags.map(t=>`<span class="badge">${esc(t)}</span>`).join('')}</div><p class="help space-top">点击题目查看完整代码版本、反馈、辅导与笔记。</p></article>`).join(''):empty('没有符合条件的错题。可以调整筛选，或到学习任务里保存一条笔记。');}
  bindForm('search-form',(_,data)=>search(data));await search(new FormData(document.getElementById('search-form')));
}
async function recommendations(epoch) {
  const [items,mistakes,settings]=await Promise.all([api('/api/recommendations'),api('/api/mistakes'),api('/api/settings')]);if(epoch!==state.epoch)return;
  const mapping=new Map(mistakes.map(m=>[m.id,m]));
  app.innerHTML=`<div class="page-heading"><h1>复习建议</h1><p class="muted">根据你确认的标签和复习记录提出建议，由你决定下一步。</p></div><section class="panel narrow"><p class="help">${settings.proactive?'你可以接受、忽略或完成建议；相同标签只表示待核对的关联，不是认知诊断。':'你已关闭主动建议。已有学习记录仍保留。'}<a href="#/settings">前往设置${settings.proactive?'关闭':'开启'}建议</a></p>${items.length?items.map(r=>`<article class="recommendation" data-recommendation="${r.id}"><h2>${esc(r.title)}</h2><p>${esc(r.reason)}</p><div class="actions">${r.mistake_ids.map(id=>{const m=mapping.get(id);return m?`<a href="${taskUrl(m.task_id)}">${esc(m.title)}</a>`:'<span class="help">关联错题暂不可用</span>';}).join('')}</div><div class="actions"><span class="badge" data-rec-status>${{pending:'待处理',accepted:'已接受',ignored:'已忽略',completed:'已完成'}[r.status]}</span>${r.status==='pending'?'<button type="button" data-rec-action="accepted">接受建议</button><button type="button" data-rec-action="ignored">忽略建议</button><button type="button" data-rec-action="completed">标记完成</button>':r.status==='accepted'?'<button type="button" data-rec-action="completed">标记完成</button><button type="button" data-rec-action="ignored">忽略建议</button>':''}</div></article>`).join(''):empty(settings.proactive?'目前没有需要处理的建议。继续练习、整理笔记，之后可再来看看。':'主动建议已关闭。')}</section>`;
  const suggested=app.querySelector('section');suggested.classList.remove('narrow');suggested.insertAdjacentHTML('afterbegin','<h2>复习建议与处理记录</h2>');
  const layout=document.createElement('div');layout.className='review-layout';suggested.before(layout);
  const due=mistakes.filter(m=>m.needs_review),queue=document.createElement('section');queue.className='panel';
  queue.innerHTML=`<div class="section-heading"><h2>当前待回顾</h2><span class="badge ${due.length?'warn':'neutral'}">${due.length} 道</span></div><p class="help">这里列出你标记为需要复习的错题。独立重做后，再记录自己的理解。</p>${due.length?due.map(m=>`<article class="review-task"><div><h3><a href="${taskUrl(m.task_id)}?panel=mistake-panel">${esc(m.title)}</a></h3><p class="preview">${esc(m.primary_reason||m.reason||m.summary||'回看当时的代码和错误记录。')}</p><div class="review-knowledge">${m.source?.knowledge_primary?knowledgeBadge(m.source.knowledge_primary):''}</div><p class="help">${m.last_review_at?'上次复习 '+when(m.last_review_at):'尚未记录复习'}</p></div><a class="button" href="${taskUrl(m.task_id)}?panel=mistake-panel">进入复习 →</a></article>`).join(''):empty('当前没有标记为待复习的错题。你可以先练习，再整理需要回顾的内容。')}<a class="text-link" href="#/library">打开全部错题 →</a>`;
  layout.append(queue,suggested);
  const reviewed=mistakes.filter(m=>m.last_review_at).sort((a,b)=>new Date(b.last_review_at)-new Date(a.last_review_at));
  layout.insertAdjacentHTML('afterend','<section class="panel"><h2>相关练习与错题候选</h2><div id="learning-suggestions"></div></section>');await renderLearningSuggestions(epoch);if(epoch!==state.epoch)return;
  const history=document.createElement('section');history.className='panel review-history-list';history.innerHTML=`<h2>最近复习记录</h2>${reviewed.length?reviewed.map(m=>`<article class="review-task"><div><h3>${esc(m.title)}</h3><p class="meta">${when(m.last_review_at)}</p></div><a href="${taskUrl(m.task_id)}?panel=mistake-panel">查看复习历史 →</a></article>`).join(''):empty('还没有复习记录。完成回顾后，在题目中保存本次复习。')}`;layout.after(history);
  app.querySelectorAll('[data-rec-action]').forEach(button=>button.addEventListener('click',()=>{const card=button.closest('[data-recommendation]');action(card,async()=>{const item=await api(`/api/recommendations/${card.dataset.recommendation}`,'PATCH',{status:button.dataset.recAction});card.querySelector('[data-rec-status]').textContent={accepted:'已接受',ignored:'已忽略',completed:'已完成'}[item.status];card.querySelectorAll('button').forEach(b=>{if(item.status!=='accepted'||b.dataset.recAction==='accepted')b.remove();});if(item.status==='accepted'&&!card.querySelector('[data-rec-action="completed"]')){await recommendations(epoch);}notify('建议状态已更新。');});}));
}
async function settingsPage(epoch) {
  const [settings,status]=await Promise.all([api('/api/settings'),api('/api/status')]);if(epoch!==state.epoch)return;state.status=status;
  app.innerHTML=`<div class="page-heading"><h1>设置</h1><p class="muted">调整辅导方式与复习节奏。</p></div><div class="settings-grid"><section class="panel"><h2>学习偏好</h2><form id="settings-form" data-draft>${select('帮助程度','help_level',option('hint','提示优先',settings.help_level)+option('step','逐步辅导',settings.help_level)+option('direct','直接解释',settings.help_level))}${select('复习频率','review_intensity',option('light','轻量 · 7 天',settings.review_intensity)+option('standard','标准 · 3 天',settings.review_intensity)+option('intensive','密集 · 1 天',settings.review_intensity))}${check('启用主动复习建议','proactive',settings.proactive)}<p class="help">关闭建议不会删除错题或复习记录。</p><button type="submit" class="primary">保存学习偏好</button></form></section><section class="panel"><h2>AI 辅导</h2><p id="settings-ai-status" class="status-note">${esc(aiLabel(status.ai))}</p><form id="ai-form" data-draft>${select('AI 模式','mode',option('disabled','关闭 AI',status.ai.mode)+option('demo','模拟辅导（非模型回答）',status.ai.mode)+option('deepseek','DeepSeek（需单独授权真实调用）',status.ai.mode))}<div id="deepseek-fields" ${status.ai.mode!=='deepseek'?'hidden':''}>${field('API Key','api_key','','type="password" autocomplete="new-password" maxlength="4096" placeholder="仅存于当前服务内存"')}<p class="help">密钥不写入学习数据库或备份。重新配置需再次填写；服务重启后清空。</p>${field('本次批准的真实请求次数','approved_requests','0','type="number" min="0" max="100" step="1" required')}${check('我明确同意向 DeepSeek 发送题目、代码及相关历史，调用可能产生费用，并仅批准上方次数。','approve_paid_calls')}<p class="help">默认 0 次。真实 API 效果尚未独立验收；保存配置不会测试连接或发送问题。</p></div><div class="actions"><button class="primary" type="submit">保存 AI 配置</button></div><p class="help space-top">模拟模式仅验证流程，不分析代码正确性。真实调用须有密钥和明确的次数授权。</p></form></section><section class="panel"><h2>本机数据</h2><p class="help">脱敏导出包含题目、代码版本、公开反馈、辅导与复习记录；隐藏测试内容和 API Key 不在导出中。此文件不能用于完整恢复。</p><a href="/api/backup" download="alpha-learning-export.sqlite3">导出学习记录（脱敏）</a><p class="help space-top">完整恢复备份需先停止对应本机服务，再运行 <code>python -m alpha.migrations backup</code>，按命令帮助指定数据目录与输出路径。</p><p class="help space-top">数据位置</p><p class="text-block tiny">${esc(status.data_dir)}</p></section></div>`;
  document.querySelector('.settings-grid').insertAdjacentHTML('afterbegin','<section id="global-preferences" class="panel"></section>');
  await mountPreferences(epoch);if(epoch!==state.epoch)return;
  const titles=app.querySelectorAll('.settings-grid>.panel>h2');if(titles[1])titles[1].textContent='复习节奏与基础辅导';
  for(const id of ['settings-form','ai-form']){
    const form=document.getElementById(id);form.insertAdjacentHTML('beforeend',`<p class="help space-top" role="status" data-form-state="${id}">已保存至本机</p>`);
  }
  bindForm('settings-form',async(form,data)=>{const snapshot=serialize(form);await api('/api/settings','PUT',{help_level:data.get('help_level'),review_intensity:data.get('review_intensity'),proactive:data.has('proactive')});saved(form,snapshot);notify('学习偏好已保存。');});
  document.getElementById('settings-form').insertAdjacentHTML('afterbegin','<p class="help">帮助程度用于模拟辅导；真实辅导与复习节奏优先采用八项学习偏好。关闭主动建议对所有模式生效。</p>');
  const aiForm=document.getElementById('ai-form');aiForm.elements.mode.addEventListener('change',()=>document.getElementById('deepseek-fields').hidden=aiForm.elements.mode.value!=='deepseek');
  bindForm('ai-form',async(form,data)=>{
    const snapshot=serialize(form);
    const real=data.get('mode')==='deepseek';const count=real?Number(data.get('approved_requests')):0;
    if(count>0&&(!data.has('approve_paid_calls')||!data.get('api_key').trim()))throw new Error('启用真实请求需要填写密钥，并明确勾选费用与数据发送授权。');
    const status=await api('/api/ai','PUT',{mode:data.get('mode'),api_key:real?data.get('api_key'):'',approved_requests:count,approve_paid_calls:real&&data.has('approve_paid_calls'),model:data.get('model'),budget_usd:Number(data.get('budget_usd')),timeout:Number(data.get('timeout'))});state.status.ai=status;
    if(serialize(form)===snapshot){form.elements.api_key.value='';form.elements.approved_requests.value='0';form.elements.approve_paid_calls.checked=false;saved(form);}else saved(form,snapshot);
    await refreshAIUsage();document.getElementById('settings-ai-status').textContent=aiLabel(status);notify('AI 配置已保存。本次配置没有发送任何 AI 请求。'+(dirtyForm(form)?'保存期间的新修改尚未保存。':''));
  });
  await mountAISettings(status);if(epoch!==state.epoch)return;watchForms();
}
function go(route) { location.hash=route; }
function confirmLeave() {
  const dialog=document.getElementById('confirm-dialog'),previous=document.activeElement;
  if(dialog.open)return Promise.resolve(false);
  dialog.returnValue='cancel';dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>{previous?.focus({preventScroll:true});resolve(dialog.returnValue==='confirm');},{once:true}));
}
async function route() {
  let next=location.hash||'#/';
  if(state.navigationPrompt){state.navigationPrompt.next=next;return;}
  if(next===state.route)return;
  if(state.route&&state.busy){history.replaceState(null,'',state.route);notify('当前操作仍在处理，请完成后再切换页面。');return;}
  if(state.route&&dirty()){
    const pending=state.navigationPrompt={next};const confirmed=await confirmLeave();state.navigationPrompt=null;
    if(!confirmed){history.replaceState(null,'',state.route);return;}
    next=pending.next;history.replaceState(null,'',next);
  }
  state.route=next;state.task=null;state.indentWithTab=false;state.baselines.clear();document.body.dataset.page=next.split('/')[1]||'home';const epoch=++state.epoch;
  // Register all form baselines before the page becomes editable, including
  // preferences loaded asynchronously. A draft must never become a baseline.
  app.inert=true;app.setAttribute('aria-busy','true');
  app.innerHTML='<div class="page-loading" role="status"><span class="loading-line"></span><p class="empty">正在加载学习记录…</p></div>';notice.hidden=true;
  const path=next.split('?')[0].replace(/^#\/?/,'').split('/');const active=path[0]||'home';
  document.querySelectorAll('[data-nav]').forEach(a=>{if(a.dataset.nav===active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  try {if(active==='home')await finalHome(epoch);else if(active==='workspace')await finalHistoryPage(epoch,true);else if(active==='history')await finalHistoryPage(epoch);else if(active==='task'&&path[1])await taskPage(decodeURIComponent(path[1]),epoch);else if(active==='library')await productLibrary(epoch);else if(active==='bank'&&path[1])await bankDetail(decodeURIComponent(path[1]),epoch);else if(active==='bank')await bankPage(epoch);else if(active==='import')await importPage(epoch);else if(active==='association'&&path[1])await associationPage(decodeURIComponent(path[1]),epoch);else if(active==='reviews')await recommendations(epoch);else if(active==='settings'){await settingsPage(epoch);if(epoch===state.epoch)finalSettingsLayout();}else app.innerHTML='<h1>页面不存在</h1><a href="#/">返回学习首页</a>';if(epoch===state.epoch){finalSyncShell(active);polishImportActions();polishKnowledgeTags();}}
  catch(error){if(epoch===state.epoch)app.innerHTML=`<section class="panel narrow"><h1>暂时无法打开</h1><p class="status-note error">${esc(error.message)}</p><button id="retry-page" type="button">重新加载</button> <a href="#/">返回学习首页</a></section>`;document.getElementById('retry-page')?.addEventListener('click',()=>{state.route='';route();});}
  if(epoch===state.epoch){app.inert=false;app.removeAttribute('aria-busy');window.scrollTo(0,0);app.focus({preventScroll:true});const panel=new URLSearchParams(next.split('?')[1]||'').get('panel');if(panel&&['mistake-panel','errors-panel','code-history','tutor-panel'].includes(panel)){const target=document.getElementById(panel);if(target){if(target.tagName==='DETAILS')target.open=true;target.scrollIntoView({block:'start'});}}}
}
app.addEventListener('input',updateDirty);
window.addEventListener('hashchange',route);
window.addEventListener('beforeunload',event=>{if(dirty()||state.busy){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){const form=document.getElementById('task-form')||document.getElementById('new-task-form');if(form){event.preventDefault();form.requestSubmit();}}
  if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&event.target.closest('#chat-form')){event.preventDefault();document.getElementById('chat-form').requestSubmit();}
  if(event.key==='Escape'&&event.target.matches('#task-form textarea.code')&&state.indentWithTab){state.indentWithTab=false;const toggle=document.getElementById('toggle-tab-indent');if(toggle){toggle.setAttribute('aria-pressed','false');toggle.textContent='Tab 缩进：关闭';}}
  if(event.key==='Tab'&&!event.shiftKey&&state.indentWithTab&&event.target.matches('#task-form textarea.code')){event.preventDefault();const node=event.target;node.setRangeText('    ',node.selectionStart,node.selectionEnd,'end');node.dispatchEvent(new Event('input',{bubbles:true}));}
});
document.querySelector('.skip-link').addEventListener('click',event=>{event.preventDefault();app.scrollIntoView();app.focus();});
route();
