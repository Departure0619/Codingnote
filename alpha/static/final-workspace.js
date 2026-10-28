'use strict';

// Layout only: every original form, listener and evidence contract stays intact.
function finalArrangeWorkspace() {
  const workspace=document.querySelector('.workspace');
  if(!workspace||workspace.classList.contains('final-ide'))return;
  const original=workspace.querySelector('.work-main'),taskForm=document.getElementById('task-form');
  const editorPanel=taskForm.closest('.panel'),runForm=document.getElementById('run-form');
  const runPanel=runForm.closest('.panel'),chat=workspace.querySelector('.work-chat');
  state.indentWithTab=false;
  workspace.className='workspace final-ide';workspace.dataset.activeView='editor';

  const navigation=document.createElement('nav');navigation.className='final-workspace-navigation';navigation.setAttribute('aria-label','编程工作区面板');
  navigation.innerHTML='<div class="final-view-switch" aria-label="工作区主要面板">'+[['problem','题目说明'],['editor','代码与结果'],['ai','AI 辅导']].map(([view,label])=>`<button type="button" data-workspace-view="${view}" aria-pressed="${view==='editor'}">${label}</button>`).join('')+'</div><div class="final-record-switch">'+[['code-history','版本历史'],['mistake-panel','笔记与复习'],['errors-panel','错误原因'],['associations-panel','跨题关联']].map(([id,label])=>`<button type="button" data-workspace-record="${id}">${label}</button>`).join('')+'</div>';
  workspace.before(navigation);

  const problem=document.createElement('aside');problem.className='problem-column final-ide-problem';problem.id='workspace-problem';problem.setAttribute('aria-label','题目说明与来源');
  const context=original.querySelector('.task-context');problem.append(context);
  context.querySelector('h2').textContent='题目说明';
  context.querySelector('.row').classList.add('final-pane-heading');
  const problemText=context.querySelector('.problem-text');
  const source=document.createElement('p');source.className='final-problem-source';source.textContent=[state.task.source?.source_platform||state.task.source?.source_description||'自主练习',state.task.source?.original_id||state.task.source?.question_id,lang(currentVersion().language)].filter(Boolean).join(' / ');
  problemText.before(source);
  const problemTitle=document.createElement('h3');problemTitle.className='final-problem-title';problemTitle.textContent=state.task.title;problemText.before(problemTitle);
  const topic=document.createElement('div');topic.className='final-problem-topics';
  topic.innerHTML=finalWorkspaceKnowledgeTopics(state.task.source);problemText.before(topic);
  const foldProblem=document.createElement('button');foldProblem.type='button';foldProblem.className='final-pane-fold';foldProblem.textContent='收起';foldProblem.setAttribute('aria-label','收起题目说明，扩大编辑器');foldProblem.addEventListener('click',()=>{workspace.classList.add('problem-folded');navigation.querySelector('[data-workspace-view="problem"]').setAttribute('aria-pressed','false');});context.querySelector('.row').append(foldProblem);

  const center=document.createElement('section');center.className='editor-column final-ide-center';center.setAttribute('aria-label','代码编辑与执行结果');
  editorPanel.id='code-editor-panel';editorPanel.classList.add('editor-panel');
  const heading=taskForm.querySelector('.row');heading.className='row final-editor-heading';heading.querySelector('h2').textContent='代码编辑器';
  const toolbar=document.createElement('div');toolbar.className='editor-toolbar final-editor-toolbar';
  const language=taskForm.elements.language;toolbar.append(language.closest('label'));
  const save=taskForm.querySelector('button[type=submit]');save.classList.add('save-code');toolbar.append(save);
  const runButton=document.createElement('button');runButton.type='button';runButton.id='workspace-run';runButton.className='primary';runButton.innerHTML='<span aria-hidden="true">▷</span> 运行';runButton.title='Ctrl / ⌘ + Enter · 运行已保存版本';runButton.addEventListener('click',()=>{const sample=document.getElementById('sample-tests');if(sample)sample.click();else runForm.requestSubmit();});toolbar.append(runButton);
  if(document.getElementById('submit-tests')){const submit=document.createElement('button');submit.type='button';submit.id='workspace-submit';submit.textContent='提交测试';submit.addEventListener('click',()=>document.getElementById('submit-tests').click());toolbar.append(submit);}
  heading.after(toolbar);

  const code=taskForm.elements.code;code.setAttribute('aria-label','代码编辑器');code.setAttribute('wrap','off');
  const codeLabel=code.closest('label');codeLabel.classList.add('code-field');codeLabel.querySelector('span').classList.add('sr-only');
  const codeWindow=document.createElement('div');codeWindow.className='final-code-window';
  const gutter=document.createElement('pre');gutter.className='final-line-numbers';gutter.setAttribute('aria-hidden','true');
  const highlighted=document.createElement('pre');highlighted.className='final-code-highlight';highlighted.setAttribute('aria-hidden','true');
  code.before(codeWindow);codeWindow.append(gutter,highlighted,code);
  const keybar=document.createElement('div');keybar.className='editor-keybar final-code-keybar';keybar.innerHTML='<span id="workspace-cursor">Ln 1, Col 1</span><span>Ctrl / ⌘ + S 保存 · Ctrl / ⌘ + Enter 运行</span><button type="button" id="toggle-tab-indent" aria-pressed="false">Tab 缩进：关闭</button>';codeLabel.after(keybar);
  document.getElementById('toggle-tab-indent').addEventListener('click',event=>{state.indentWithTab=!state.indentWithTab;event.currentTarget.setAttribute('aria-pressed',String(state.indentWithTab));event.currentTarget.textContent='Tab 缩进：'+(state.indentWithTab?'开启（Esc 退出）':'关闭');});
  const syncCode=()=>{
    const text=code.value;gutter.textContent=Array.from({length:text.split('\n').length},(_,i)=>i+1).join('\n');
    highlighted.innerHTML=finalWorkspaceHighlight(text,language.value)+'\n';highlighted.scrollTop=code.scrollTop;highlighted.scrollLeft=code.scrollLeft;gutter.scrollTop=code.scrollTop;
    const before=text.slice(0,code.selectionStart),line=before.split('\n');document.getElementById('workspace-cursor').textContent=`Ln ${line.length}, Col ${line.at(-1).length+1}`;
    finalWorkspaceRenderResult();
  };
  code.addEventListener('input',syncCode);code.addEventListener('click',syncCode);code.addEventListener('keyup',syncCode);code.addEventListener('scroll',()=>{highlighted.scrollTop=code.scrollTop;highlighted.scrollLeft=code.scrollLeft;gutter.scrollTop=code.scrollTop;});
  language.addEventListener('change',syncCode);
  code.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();runButton.click();}});
  const metadata=taskForm.querySelector('details');metadata.classList.add('task-metadata');
  const template=document.getElementById('language-template-panel');
  for(const [popup,label] of [[metadata,'题目设置'],[template,'语言模板']]){
    if(!popup)continue;popup.classList.add('final-editor-popup');popup.querySelector('summary').textContent=label;
    const body=document.createElement('div');body.className='final-editor-popover';
    while(popup.children.length>1)body.append(popup.children[1]);popup.append(body);toolbar.append(popup);
    popup.addEventListener('toggle',()=>{if(popup.open)for(const sibling of toolbar.querySelectorAll('details'))if(sibling!==popup)sibling.open=false;});
    popup.addEventListener('keydown',event=>{if(event.key==='Escape'&&popup.open){event.preventDefault();popup.open=false;popup.querySelector('summary').focus();}});
  }
  workspace.addEventListener('pointerdown',event=>{for(const popup of toolbar.querySelectorAll('.final-editor-popup[open]'))if(!popup.contains(event.target))popup.open=false;});
  const editorStatus=document.createElement('div');editorStatus.className='final-editor-status';
  const oldActions=taskForm.querySelector(':scope > .actions');
  if(oldActions){const dirtyIndicator=oldActions.querySelector('#code-dirty');if(dirtyIndicator)editorStatus.append(dirtyIndicator);while(oldActions.firstChild)oldActions.firstChild.remove();oldActions.remove();}
  const contextVersion=document.createElement('span');contextVersion.id='workspace-version-context';editorStatus.append(contextVersion);keybar.after(editorStatus);

  runPanel.id='execution-panel';runPanel.classList.add('execution-panel');
  runPanel.querySelector('h2').textContent='运行结果';runPanel.querySelector('h2').classList.add('final-results-title');
  const custom=document.createElement('details');custom.className='custom-run';custom.innerHTML='<summary>自定义输入与运行</summary>';runForm.before(custom);custom.append(runForm);const cancel=document.getElementById('cancel-run');if(cancel)custom.append(cancel);
  const evidence=document.getElementById('evidence-list'),evidenceHistory=document.createElement('details');evidenceHistory.id='execution-history';evidenceHistory.innerHTML='<summary>所有运行记录与执行证据</summary>';evidence.before(evidenceHistory);evidenceHistory.append(evidence);
  const result=document.createElement('div');result.id='workspace-latest-result';runPanel.querySelector('#execution-status').after(result);
  center.append(editorPanel,runPanel);

  const ai=document.createElement('aside');ai.className='final-ide-ai';ai.id='tutor-panel';ai.setAttribute('aria-label','AI 辅导与历史');
  const aiBar=document.createElement('div');aiBar.className='final-ai-heading';aiBar.innerHTML='<strong>AI 学习助手</strong><button type="button" class="final-pane-fold" aria-label="收起 AI 辅导，扩大编辑器">收起</button>';aiBar.querySelector('button').addEventListener('click',()=>workspace.classList.add('ai-folded'));
  const aiContext=document.createElement('div');aiContext.className='final-ai-context';aiContext.id='workspace-ai-context';
  aiContext.innerHTML=`<span class="badge warn">真实 AI 验收 · 部分完成</span><p>${state.status.ai?.transport==='fixture'?'当前为隔离测试传输；真实模型请求关闭。':'历史内容按来源与版本保留。'} 回答、原因候选与个人笔记分别保存。</p>`;
  const chatHeading=chat.querySelector(':scope > .row');chatHeading.querySelector('h2').textContent='连续辅导';
  const aiSettings=chatHeading.querySelector('a');if(aiSettings)aiBar.insertBefore(aiSettings,aiBar.querySelector('button'));
  ai.append(aiBar,aiContext,chat);

  const records=document.createElement('details');records.className='final-workspace-records';records.id='workspace-records';records.innerHTML='<summary>学习记录与历史 <span class="help">版本 / 笔记 / 原因 / 关联 / 偏好</span></summary>';
  const recordNav=document.createElement('nav');recordNav.className='final-record-tabs';recordNav.setAttribute('aria-label','学习记录分类');
  const history=document.getElementById('history-version').closest('details.panel');history.id='code-history';
  const preferences=document.getElementById('task-preferences')?.closest('details.panel');if(preferences)preferences.id='workspace-preferences';
  const recordNodes=[history,document.getElementById('mistake-panel'),document.getElementById('errors-panel'),document.getElementById('associations-panel'),preferences].filter(Boolean);
  recordNav.innerHTML=recordNodes.map(node=>`<button type="button" data-workspace-record="${esc(node.id)}" aria-pressed="false">${esc({'code-history':'代码版本','mistake-panel':'笔记与复习','errors-panel':'错误原因','associations-panel':'跨题关联','workspace-preferences':'本题学习偏好'}[node.id])}</button>`).join('');
  records.append(recordNav);recordNodes.forEach(node=>{node.classList.add('final-record-pane');node.hidden=true;records.append(node);});
  // Keep any future business panels reachable instead of dropping them in a layout upgrade.
  while(original.firstElementChild){const remaining=original.firstElementChild;if(remaining===editorPanel||remaining===runPanel)break;records.append(remaining);}
  workspace.replaceChildren(problem,center,ai);workspace.after(records);
  const showRecord=(id,focus=true)=>{
    const target=document.getElementById(id);if(!target)return;records.open=true;
    recordNodes.forEach(node=>{node.hidden=node!==target;if(node===target&&node.tagName==='DETAILS')node.open=true;});
    recordNav.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.workspaceRecord===id)));
    if(focus){target.setAttribute('tabindex','-1');target.scrollIntoView({block:'start'});target.focus({preventScroll:true});}
  };
  [navigation,recordNav].forEach(nav=>nav.addEventListener('click',event=>{const button=event.target.closest('[data-workspace-record]');if(button)showRecord(button.dataset.workspaceRecord);}));
  navigation.querySelectorAll('[data-workspace-view]').forEach(button=>button.addEventListener('click',()=>{
    const view=button.dataset.workspaceView;workspace.dataset.activeView=view;
    if(view==='problem')workspace.classList.remove('problem-folded');if(view==='ai')workspace.classList.remove('ai-folded');
    navigation.querySelectorAll('[data-workspace-view]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));
    if(matchMedia('(max-width:1100px)').matches){const target=view==='problem'?problem:view==='ai'?ai:center;target.setAttribute('tabindex','-1');target.focus({preventScroll:true});}
  }));
  const initialPanel=new URLSearchParams(location.hash.split('?')[1]||'').get('panel');
  if(recordNodes.some(node=>node.id===initialPanel))showRecord(initialPanel,false);
  if(initialPanel==='tutor-panel'){workspace.dataset.activeView='ai';navigation.querySelectorAll('[data-workspace-view]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab.dataset.workspaceView==='ai')));}
  const title=document.querySelector('.page-heading');if(title?.querySelector('.muted'))title.querySelector('.muted').textContent='先保存当前代码，再运行、核对结果或向 AI 提问。';
  const mountedTask=state.task.id;
  const updateContext=()=>{
    if(!workspace.isConnected||state.task?.id!==mountedTask){observer.disconnect();return;}
    contextVersion.textContent='运行与 AI 绑定 '+vlabel(state.task.current_version_id);
    problemTitle.textContent=state.task.title;
    source.textContent=[state.task.source?.source_platform||state.task.source?.source_description||'自主练习',state.task.source?.original_id||state.task.source?.question_id,lang(currentVersion().language)].filter(Boolean).join(' / ');
    topic.innerHTML=finalWorkspaceKnowledgeTopics(state.task.source);
    syncCode();
  };
  const observer=new MutationObserver(updateContext);observer.observe(document.getElementById('version-label'),{childList:true,subtree:true});observer.observe(evidence,{childList:true});
  updateContext();renderEvidence();
  const log=document.getElementById('chat-log');if(log)requestAnimationFrame(()=>{log.scrollTop=log.scrollHeight;});
}

function finalWorkspaceHighlight(source,language='python') {
  const comments=language==='python'?String.raw`#[^\n]*`:String.raw`\/\*[\s\S]*?\*\/|\/\/[^\n]*`;
  const common=/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b(?:def|class|return|if|else|elif|for|while|break|continue|import|from|as|in|not|and|or|try|except|raise|with|pass|True|False|None|int|float|void|char|double|long|public|private|static|final|new|const|let|var|function|async|await|true|false|null|undefined|include|using|namespace|switch|case|default|do|throw|throws|package|boolean|string)\b|\b\d+(?:\.\d+)?\b/;
  const tokens=new RegExp(comments+'|'+common.source,'g');
  let result='',offset=0;
  for(const match of source.matchAll(tokens)){
    result+=esc(source.slice(offset,match.index));const token=match[0];
    const kind=/^(\/\*|\/\/|#)/.test(token)?'comment':/^["'`]/.test(token)?'string':/^\d/.test(token)?'number':'keyword';
    result+=`<span class="final-syntax-${kind}">${esc(token)}</span>`;offset=match.index+token.length;
  }
  return result+esc(source.slice(offset));
}

function finalWorkspaceRenderResult() {
  const target=document.getElementById('workspace-latest-result');if(!target||!state.task)return;
  const records=state.task.evidence||[],record=records.at(-1);
  const draft=typeof dirtyForm==='function'&&dirtyForm(document.getElementById('task-form'));
  const warning=draft?'<p class="status-note warn final-result-stale">编辑器有未保存修改。以下记录仍属于已保存的代码，尚未执行当前草稿。</p>':'';
  if(!record){target.innerHTML=warning+'<div class="final-result-empty"><strong>还没有运行结果</strong><p>保存代码后运行公开样例，或展开自定义输入。不会为未执行的代码显示结果。</p></div>';return;}
  const r=record.result||{},old=record.version_id!==state.task.current_version_id;
  let content=`${warning}<div class="final-result-overview"><strong class="${r.matches_expected===false||r.error?'error':r.matches_expected===true?'success':'neutral'}">${esc(executionLabel(record))}</strong><span class="badge ${old?'warn':'neutral'}">${esc(vlabel(record.version_id))}${old?' · 历史版本':' · 当前保存版本'}</span></div><p class="help">${esc(when(record.created_at))}${r.elapsed_seconds!==undefined?' · '+esc(r.elapsed_seconds)+' s':' · 耗时未记录'}${r.memory_bytes!==undefined?' · '+esc((r.memory_bytes/1048576).toFixed(1))+' MiB':r.memory_kb!==undefined?' · '+esc(r.memory_kb)+' KiB':' · 内存未记录'} · ${record.kind==='external'?'用户录入，未经本地核验':'绑定执行证据'}</p>`;
  if(old)content+='<p class="status-note warn">这是历史版本的运行结果，不能证明当前代码已运行。</p>';
  if(record.kind==='external')content+=`<p class="text-block">${esc(r.notes||'未补充外部判题说明')}</p>`;
  else if(r.hidden)content+='<p class="help">隐藏测试仅展示状态；输入、输出和期望内容不向页面提供。</p>';
  else content+=`<div class="final-result-io"><div><span>标准输入</span><pre>${esc(record.stdin||'（空）')}</pre></div><div><span>实际输出</span><pre>${esc(r.stdout||'（空）')}</pre></div>${record.expected_output!==null&&record.expected_output!==undefined?`<div><span>期望输出</span><pre>${esc(record.expected_output||'（空）')}</pre></div>`:''}</div>${r.stderr?`<details class="final-stderr" open><summary>标准错误 stderr</summary><pre>${esc(r.stderr)}</pre></details>`:''}`;
  if(r.error)content+=`<p class="status-note error">${esc(r.error)}</p>`;
  content+=`<p class="help mono final-result-evidence">执行证据 ${esc(record.id)}</p>`;target.innerHTML=content;
}

function finalWorkspaceKnowledgeTopics(source={}) {
  const badge=(label,secondary=false)=>typeof knowledgeBadge==='function'?knowledgeBadge(source?.knowledge_primary,label,secondary):`<span class="badge neutral">${esc(label)}</span>`;
  return (source?.knowledge_primary?badge(source.knowledge_primary):'')+(source?.knowledge_tags||[]).map(tag=>badge(tag,true)).join('');
}
