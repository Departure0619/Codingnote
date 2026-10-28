'use strict';
// Presentation only. Keep the fingerprinted authorization implementation intact.
(() => {
  const number=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0?v:null;
  const count=v=>number(v)!==null&&Number.isInteger(v)?v:null;
  const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=v=>number(v)===null?'暂不可核对':'US$ '+v.toFixed(6);
  const quantity=v=>count(v)===null?'暂不可核对':String(v)+' 次';
  function derive(ai={},usage={}) {
    const r=ai.round_authorization&&typeof ai.round_authorization==='object'?ai.round_authorization:null;
    const stage=r?.current_stage&&typeof r.current_stage==='object'?r.current_stage:null;
    const rawCap=number(r?.budget_usd),stageCap=number(stage?.cumulative_budget_usd);
    const cap=r?.authorized===true&&rawCap>0&&(!stage||stageCap>0)?Math.min(rawCap,stage?stageCap:rawCap):null;
    const known=number(r?.known_cost_usd),reserved=number(r?.reserved_unknown_cost_usd);
    const occupied=known!==null&&reserved!==null?Number((known+reserved).toFixed(12)):null;
    const ratio=cap!==null&&occupied!==null?occupied/cap:null;
    const remainingBudget=cap!==null&&occupied!==null?Math.max(0,cap-occupied):null;
    const a=count(ai.remaining_approved_requests),b=count(r?.remaining_requests);
    const remainingCalls=a!==null?(!r?a:b!==null?Math.min(a,b):null):null;
    const effective=r&&Object.hasOwn(r,'effective_stop_reason')?r.effective_stop_reason:r?.stop_reason;
    const warning=ratio===null?'unknown':ratio>=1-1e-9?'limit':ratio>=.9-1e-9?'warning':ratio>=.7-1e-9?'notice':'normal';
    return {cap,known,reserved,occupied,ratio,remainingBudget,remainingCalls,warning,
      configurationBudget:number(ai.budget_usd),calls:count(r?.attempts),callLimit:count(r?.max_requests),
      authorized:r?.authorized===true,stageExists:!!stage,stageLimit:count(stage?.max_new_requests),stageUsed:count(r?.stage_attempts),stageRemaining:count(r?.phase_remaining),newAllowance:count(stage?.max_new_requests),
      billingVerified:false,blocked:ai.mode!=='deepseek'||ai.real_enabled!==true||remainingCalls===0||!!effective||ai.upstream_pending===true,
      cumulativeAvailable:!!r,usage};
  }
  window.AlphaBudgetView=Object.freeze({derive,money});
  let cache=null,refreshGeneration=0,diagnosticQueue=Promise.resolve();
  function unavailable(overview){if(!overview)return;overview.replaceChildren();const p=document.createElement('p');p.className='status-note warn';p.textContent='费用与额度暂不可核对：读取失败，请稍后刷新。原有设置仍可使用。';overview.append(p);}
  function statusText(ai,d){
    if(ai.mode==='disabled')return 'AI 已关闭，不会发起新的模型请求。';
    if(ai.mode==='demo')return '当前是离线模拟，不是真实模型回答。';
    if(ai.mode!=='deepseek')return 'AI 状态暂不可核对。';
    if(!ai.key_configured)return '尚未配置密钥；开启选项不会自动获得调用授权。';
    if(ai.upstream_pending)return '上一条请求尚未结束，当前暂停新的调用。';
    if(d.remainingCalls===0)return '当前可调用次数为 0，仍受已有授权、预算和停止限制。';
    return d.blocked?'当前调用受限，请核对授权记录。':'AI 已配置，仅在你主动发送问题时调用。';
  }
  function diagnostics(box,ai,u){
    if(!box)return;
    const r=ai.round_authorization||{};
    const groups=[['累计调用记录',r.calls,['created_at','id','state','dispatched','cost_usd','reserved_usd','error_code']],['阶段授权记录',r.stages,['approved_at','id','model','max_new_requests','cumulative_max_requests','cumulative_budget_usd']],['历史停止记录',r.stop_events,['created_at','id','reason','event','stage_id']],['当前学习库授权记录',u.authorizations,['created_at','id','model','max_requests','budget_usd']],['当前学习库调用记录',u.calls,['created_at','id','transport','model','state','cost_usd','reserved_usd','error_code']]];
    const labels={created_at:'时间',approved_at:'批准时间',id:'记录标识',state:'状态',dispatched:'是否发送',cost_usd:'已知费用估算',reserved_usd:'未知费用预留',error_code:'诊断代码',model:'模型',max_new_requests:'新增调用上限',cumulative_max_requests:'累计调用上限',cumulative_budget_usd:'累计预算上限',reason:'原因',event:'事件',stage_id:'阶段标识',max_requests:'调用上限',budget_usd:'预算上限',transport:'传输来源'};
    box.innerHTML=groups.map(([title,rows,keys])=>`<details class="copy-record-group"><summary>${title} · ${Array.isArray(rows)?rows.length:0} 条</summary>${Array.isArray(rows)&&rows.length?rows.map(row=>`<dl class="copy-record">${keys.filter(key=>Object.hasOwn(row,key)).map(key=>`<div><dt>${labels[key]}</dt><dd>${escape(key.endsWith('_usd')?money(row[key]):row[key]===null?'未提供':typeof row[key]==='boolean'?(row[key]?'是':'否'):row[key])}</dd></div>`).join('')}</dl>`).join(''):'<p class="help">当前接口没有相应记录。</p>'}</details>`).join('');
  }
  function render(ai,u){
    const box=document.getElementById('copy-budget-overview');if(!box)return;
    const d=derive(ai,u),stateNode=document.getElementById('settings-ai-status');
    if(stateNode)stateNode.textContent=statusText(ai,d);
    const warning={unknown:'预算占用暂不可核对。',normal:'预算使用正常。',notice:'预算占用达到70%，使用较多。',warning:'预算占用达到90%，接近上限。',limit:'预算占用已达上限，实际能否调用仍以后端门禁为准。'}[d.warning];
    const callText=d.calls===null?'暂不可核对':d.callLimit===null?`${d.calls} 次 / 上限暂不可核对`:d.callLimit>0?`${d.calls} / ${d.callLimit} 次`:`${d.calls} 次 · 尚未授权`;
    box.innerHTML=`<div class="copy-budget-heading"><h3>费用与额度</h3><span class="badge ${d.blocked?'warn':'neutral'}">当前可调用 ${d.remainingCalls===null?'暂不可核对':d.remainingCalls+' 次'}</span></div><p class="help">${d.cumulativeAvailable?'以下来自跨学习库累计授权账本，按发送尝试计数。':'累计授权账本暂不可核对；当前学习库记录单独保留在高级诊断中。'}</p>${ai.transport==='fixture'?'<p class="copy-fixture-label">隔离测试传输 · 合成预算仅用于界面验收，不是实际扣款证明</p>':''}<dl class="copy-budget-metrics"><div><dt>累计调用</dt><dd>${escape(callText)}</dd></div><div><dt>已知费用估算</dt><dd>${money(d.known)}</dd></div><div><dt>有效累计授权预算</dt><dd>${money(d.cap)}</dd></div><div><dt>预估剩余预算</dt><dd>${money(d.remainingBudget)}</dd></div></dl><div class="copy-budget-progress"><div><span>预算占用（含未核对预留）</span><strong>${d.ratio===null?'暂不可核对':(d.ratio*100).toFixed(1)+'%'}</strong></div>${d.ratio===null?'<p class="help">没有可靠的已授权预算、费用或预留数据，暂不计算进度。</p>':`<progress max="100" value="${Math.min(100,d.ratio*100)}" aria-label="预算占用比例">${(d.ratio*100).toFixed(1)}%</progress>`}<p class="copy-budget-warning ${d.warning==='limit'||d.warning==='warning'?'is-warning':''}" role="status">${warning}${d.remainingCalls!==null&&d.remainingCalls<=2?' 当前仅可调用 '+d.remainingCalls+' 次。':''}</p></div><p class="copy-budget-footnote">未知费用预留：${money(d.reserved)}。预估剩余＝有效累计预算－已知估算－未知预留，最低为0；它不是服务商账户余额，也不等于获准调用。</p><p class="copy-stage-summary">${d.stageExists?`当前阶段新增上限 ${quantity(d.stageLimit)} · 已使用 ${quantity(d.stageUsed)} · 阶段次数额度 ${quantity(d.stageRemaining)}（还受费用与停止限制）`:'当前没有已批准的阶段范围，本次允许新增次数暂不可核对。'}</p><p class="copy-budget-billing"><strong>实际扣款尚未核对</strong><span>费用为本机估算，与服务商实际账单可能不同。</span></p><p class="help">当前已保存的配置预算：${money(d.configurationBudget)}；这是服务配置，不会提高累计授权上限。下方输入须保存后才生效。</p>`;
    diagnostics(document.getElementById('copy-budget-records'),ai,u);
  }
  const oldRefresh=refreshAIUsage;
  refreshAIUsage=async function(){
    const box=document.getElementById('ai-usage');if(!box)return;
    const epoch=state.epoch,generation=++refreshGeneration;
    const current=()=>generation===refreshGeneration&&epoch===state.epoch&&document.getElementById('ai-usage')===box;
    try{
      const [snapshot,u]=await Promise.all([api('/api/status'),api('/api/ai/usage')]);
      if(!current())return;
      state.status=snapshot;cache={ai:snapshot.ai||{},usage:u,owner:box};
      diagnosticQueue=diagnosticQueue.catch(()=>{}).then(()=>current()?oldRefresh():undefined);
      await diagnosticQueue;
      if(!current())return;
      render(cache.ai,cache.usage);
    }catch(error){if(current()){cache={owner:box,error:true};unavailable(document.getElementById('copy-budget-overview'));}}
  };
  const oldMount=mountAISettings;
  mountAISettings=async function(status){
    const form=document.getElementById('ai-form'),epoch=state.epoch;
    await oldMount(status);
    if(epoch!==state.epoch||!form?.isConnected||form.dataset.copyBudgetMounted)return;
    form.dataset.copyBudgetMounted='true';
    const panel=form.closest('.panel');panel.classList.add('copy-ai-panel');
    panel.querySelector('h2').textContent='AI 辅助设置';
    const intro=document.createElement('p');intro.className='copy-ai-intro';intro.textContent='选择模型并设置调用预算。关闭AI时不会发起新请求；开启后仍需明确授权，并受预算与次数限制。';panel.querySelector('h2').after(intro);
    const overview=document.createElement('section');overview.id='copy-budget-overview';overview.setAttribute('aria-label','费用与额度');form.before(overview);
    const advanced=document.createElement('details');advanced.id='copy-budget-diagnostics';advanced.innerHTML='<summary>高级诊断与授权记录</summary><p class="help">历史记录与授权依据保持原样。展开查看累计账本、学习库镜像、停止原因与复验范围。</p>';panel.append(advanced);
    for(const [id,title]of [['ai-usage','累计额度与学习库用量'],['ai-retest-approval','复验授权与诊断依据']]){const node=document.getElementById(id);if(node){const section=document.createElement('section');section.className='copy-advanced-section';const h=document.createElement('h3');h.textContent=title;section.append(h,node);advanced.append(section);}}
    const records=document.createElement('section');records.id='copy-budget-records';advanced.append(records);
    const fields=document.getElementById('deepseek-fields');
    function group(title,nodes,parent=form){const fieldset=document.createElement('fieldset');fieldset.className='copy-ai-group';const legend=document.createElement('legend');legend.textContent=title;fieldset.append(legend);nodes.filter(Boolean).forEach(node=>fieldset.append(node));parent.append(fieldset);return fieldset;}
    function label(name,text){const control=form.elements.namedItem(name),node=control?.closest('label');const span=node?.querySelector(':scope > span');if(span)span.textContent=text;return node;}
    const mode=label('mode','AI 辅助方式'),modeGroup=group('AI辅助开关与状态',[mode]);form.prepend(modeGroup);modeGroup.after(overview);
    const model=label('model','模型'),budget=label('budget_usd','本次配置费用上限（美元）'),timeout=label('timeout','请求等待时间（秒）'),key=label('api_key','API Key'),limit=label('approved_requests','本次配置请求次数上限（受累计授权限制）'),consent=form.elements.approve_paid_calls.closest('label');
    const notes=document.createElement('details');notes.className='copy-record-group';notes.innerHTML='<summary>原配置说明</summary>';
    const controls=[model,budget,timeout,key,limit,consent];controls.forEach(n=>n?.remove());while(fields.firstChild)notes.append(fields.firstChild);advanced.append(notes);
    group('模型与服务商',[model],fields).insertAdjacentHTML('beforeend','<p class="help">服务商：DeepSeek。模型名单由当前本机服务提供；模型选择与学习辅导程度分别设置。</p>');
    group('费用与预算保护',[budget,timeout],fields).insertAdjacentHTML('beforeend','<p class="help">费用是估算。此配置不会重置历史支出或提高已批准的累计预算，达到门禁限制后仍无法调用。</p>');
    group('API Key配置',[key],fields).insertAdjacentHTML('beforeend','<p class="help">密钥默认隐藏，仅存于当前服务内存，不写入学习库、URL或备份。服务重启后需要重新填写。</p>');
    group('本次授权与调用限制',[limit,consent],fields).insertAdjacentHTML('beforeend','<p class="help">此输入不会追加累计额度；阶段新增范围仅来自单独的受控复验批准。未勾选不会获得付费调用授权。主动发送问题后，题目、代码、所选证据与相关历史可能发送给第三方模型；其中可能包含你的私人学习内容。</p>');
    const actions=form.querySelector(':scope > .actions');if(actions)form.append(actions);
    const saveState=form.querySelector('[data-form-state]');if(saveState)form.append(saveState);
    if(cache?.owner===document.getElementById('ai-usage')){if(cache.error)unavailable(overview);else render(cache.ai,cache.usage);}
  };
})();
