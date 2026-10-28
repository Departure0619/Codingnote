'use strict';

// Deterministic suggestions and write protection. No model or remote page fetch.
globalThis.AlphaImportRules={
  canPrefill(current,atStart,edited,provenance){return !edited&&current===atStart&&!['manual','ocr_edited'].includes(provenance);},
  titleFromText(text){
    const line=String(text||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean).find(s=>!/^\s*(?:洛谷|PTA|牛客|LeetCode|Codeforces)(?:\s|$|[：:]|P\d|\d)/i.test(s));
    if(!line||line.length<2||line.length>80||/[。！？.!?]$/.test(line)||/[{};=]/.test(line)||/^\s*(?:请|给定|输入|输出|计算|求出|读取|编写|要求|对于|如果|已知|有一个|现有|需要|约束|数据范围|说明|样例|示例|Input|Output|Sample|Example|Constraints|Given|Read|Write|Compute|Print|def\s|class\s|function\s)/i.test(line)||/^\s*[\d\s+\-.,]+$/.test(line))return '';
    return line;
  },
  suggest(text,allowed){
    const rules=[['数组',/(?:遍历数组|数组遍历|数组元素|一维数组|二维数组)/],['字符串',/(?:字符串比较|字符串长度|字符串反转|字符串中的字符)/],['循环',/(?:for\s*循环|while\s*循环|循环遍历|循环求和|循环直到)/i],['条件',/(?:条件判断|分支判断|if\s*语句|if\s*条件)/i],['函数',/(?:递归函数|递归计算|函数调用|递归求解)/],['基础结构',/(?:栈的|栈中|队列|链表)/],['简单算法',/(?:二分查找|二分搜索|排序算法|最大公约数)/]];
    return rules.filter(([primary,pattern])=>allowed.includes(primary)&&pattern.test(text)).slice(0,2).map(([primary,pattern])=>({primary,basis:String(text).match(pattern)[0]}));
  },
  confidence(lines){return (lines||[]).some(line=>Number(line.confidence)<.65)?'部分文字置信度较低，请逐字核对。':'OCR只提取可见文字，题意、符号和样例仍需核对。';}
};

async function mountPolishImportTools(form,epoch){
  if(!form||epoch!==state.epoch)return;
  const layout=form.closest('.import-layout');layout.classList.add('polish-import-layout');
  const main=form.closest('.panel');main.classList.add('polish-import-main');
  const fields=['title','statement','source_url','source_platform','original_id','knowledge_primary','knowledge_tags'];
  const touched={},provenance={};fields.forEach(name=>{touched[name]=0;provenance[name]=form.elements[name].value?'manual':'';});
  let serial=0,sourceSerial=0,ocrAvailable=false,ocrStatusKnown=false,ocrBusy=false,sourceBusy=false,previewUrl='',draftSnapshot=null;
  let recognitionStatus='选择输入方式，填写或识别题目后再保存。',recognitionTone='neutral',sourceDescription='尚未识别来源。可以留空，或手动填写。',suggestions=[],categories=[];
  const isCurrent=()=>form.isConnected&&epoch===state.epoch;
  const snapshot=()=>Object.fromEntries(fields.map(name=>[name,{value:form.elements[name].value,edits:touched[name],source:provenance[name]}]));
  const applyField=(name,value,before,source)=>{
    value=String(value??'');const input=form.elements[name],old=before[name];
    if(!AlphaImportRules.canPrefill(input.value,old.value,touched[name]!==old.edits||['manual','ocr_edited'].includes(old.source),old.source))return false;
    input.value=value;provenance[name]=value?source:'';return true;
  };
  const labelFor=name=>({title:'标题',statement:'题干',source_url:'来源链接',source_platform:'平台',original_id:'原题号',knowledge_primary:'一级分类',knowledge_tags:'二级标签'}[name]);
  const sourceLabel=value=>({ocr:'OCR预填 · 待核对',url:'URL规则候选 · 待核对',manual:'手动填写',ocr_edited:'OCR草稿 · 用户修改',rule:'规则建议 · 已采用'}[value]||'尚未填写');

  const selector=form.elements.import_status,selectorLabel=selector.closest('label');selectorLabel.hidden=true;
  const chooser=document.createElement('section');chooser.className='polish-import-intake';
  chooser.innerHTML='<div class="polish-import-section-heading"><span class="eyebrow">带入正在学习的题目</span><span class="help">识别只生成可编辑草稿</span></div><div class="polish-import-methods" role="group" aria-label="导入方式"><button type="button" data-import-mode="pending_ocr"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4zM8 9h1m-3 8 5-5 4 4 2-2 3 3"/></svg><strong>截图识别</strong><small>上传图片，本地提取文字</small></button><button type="button" data-import-mode="pending_link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 15 6-6m-7 3-2 2a4 4 0 0 0 6 6l2-2m-4-12 2-2a4 4 0 0 1 6 6l-2 2"/></svg><strong>粘贴链接</strong><small>识别平台与原始题号</small></button><button type="button" data-import-mode="manual"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16-1 5 5-1L21 7l-4-4L4 16zm11-11 4 4"/></svg><strong>手动录入</strong><small>直接填写，自由补充</small></button></div><div id="polish-input-stage"></div>';
  form.prepend(chooser);
  const stage=chooser.querySelector('#polish-input-stage');
  const upload=document.getElementById('image-upload');upload.classList.add('polish-image-upload');
  upload.querySelector('label>span').textContent='选择题目截图';
  upload.querySelector('p.help').textContent='PNG、JPEG、WebP，最大5MB。仅在本机临时识别，不保存原图，不使用云OCR。';
  upload.insertAdjacentHTML('beforeend','<div class="polish-recognition-actions"><button type="button" id="recognize-image">重新识别截图</button><span id="ocr-status" class="help" role="status"></span></div><p class="help polish-overwrite-note">识别只补入尚未手动编辑的字段。重新识别会更新OCR草稿，保留你已修改的题目内容。</p><details id="ocr-draft" hidden><summary>查看原始OCR草稿 · 可继续编辑</summary><div id="ocr-draft-fields"></div></details>');
  stage.append(upload);
  const fileInput=form.elements.image.cloneNode(true);form.elements.image.replaceWith(fileInput);
  const urlPanel=document.createElement('div');urlPanel.id='polish-url-input';urlPanel.className='polish-url-input';
  urlPanel.append(form.elements.source_url.closest('label'));
  urlPanel.insertAdjacentHTML('beforeend','<div class="polish-recognition-actions"><button type="button" id="recognize-source">识别来源</button><p class="help">按域名和路径识别，不访问第三方网页。</p></div><p class="help polish-overwrite-note">只预填可确定的平台和原题号；标题与正文请自行补充。已手动编辑的字段保持原样。</p>');stage.append(urlPanel);
  const manualPanel=document.createElement('div');manualPanel.id='polish-manual-input';manualPanel.className='polish-manual-input';manualPanel.innerHTML='<p>填写标题与题干即可整理这道题。来源、分类、标签和代码都可以稍后补充。</p>';stage.append(manualPanel);
  const modeNote=document.getElementById('import-mode-note');modeNote.className='polish-mode-status';chooser.append(modeNote);
  const hidden=document.createElement('input');hidden.type='hidden';hidden.name='polish_image_selection';form.append(hidden);

  const sourceSection=form.elements.source_platform.closest('fieldset');sourceSection.querySelector('legend').textContent='来源与知识分类';
  sourceSection.querySelector('legend').insertAdjacentHTML('afterend','<p id="source-candidate" class="help polish-source-feedback" role="status">来源候选待核对；不能识别时可以留空或手动填写。</p>');
  sourceSection.insertAdjacentHTML('beforeend','<div id="polish-import-tags" class="polish-import-tags"></div><div id="polish-knowledge-suggestions" class="polish-knowledge-suggestions"></div>');
  const languageSection=form.elements.language.closest('fieldset'),languageDetails=document.createElement('details');languageDetails.className='polish-import-code';languageDetails.innerHTML='<summary>编程语言与初始代码 <span class="help">可选，默认Python；进入工作区后仍可修改</span></summary>';languageSection.before(languageDetails);languageDetails.append(languageSection);
  form.elements.knowledge_primary.setAttribute('list','polish-primary-options');form.elements.knowledge_primary.placeholder='不知道时可留空，之后再分类';
  form.elements.knowledge_tags.setAttribute('list','polish-tag-options');form.elements.knowledge_tags.placeholder='可留空，例如 循环边界、数组遍历';
  sourceSection.insertAdjacentHTML('beforeend','<datalist id="polish-primary-options"></datalist><datalist id="polish-tag-options"></datalist>');
  const footer=form.querySelector('.form-footer');footer.insertAdjacentHTML('afterbegin',check('我已核对题目内容与来源；未识别的字段可暂留空','source_confirmed'));
  form.elements.source_confirmed.required=true;
  footer.querySelector('p.help').textContent='识别不会自动创建题目。由你确认并提交，保存后进入正式编程工作区。';
  footer.insertAdjacentHTML('beforeend','<button type="button" class="text-button" id="polish-cancel-import">取消并返回首页</button>');
  document.getElementById('polish-cancel-import').addEventListener('click',()=>go('#/'));
  const aside=layout.querySelector('.import-aside');aside.classList.add('polish-import-context');aside.setAttribute('aria-label','当前导入状态与核对清单');

  fields.forEach(name=>{
    const input=form.elements[name],label=input.closest('label');
    label.querySelector('span').insertAdjacentHTML('beforeend',` <small class="polish-field-origin" data-origin-for="${name}"></small>`);
    input.addEventListener('input',()=>{touched[name]++;provenance[name]='manual';form.elements.source_confirmed.checked=false;if(name==='source_url'){sourceSerial++;sourceBusy=false;sourceDescription='链接已修改，之前的识别结果已失效。';form.sourceCandidate={};}render();});
  });
  form.elements.source_confirmed.addEventListener('change',render);
  form.elements.language.addEventListener('change',render);
  function status(message,tone='neutral'){recognitionStatus=message;recognitionTone=tone;render();}
  function render(){
    if(!isCurrent())return;
    modeNote.textContent=recognitionStatus;modeNote.dataset.tone=recognitionTone;
    for(const name of fields){const node=form.querySelector(`[data-origin-for="${name}"]`);if(node)node.textContent=form.elements[name].value?sourceLabel(provenance[name]):'';}
    const primary=form.elements.knowledge_primary.value.trim(),tags=splitTags(form.elements.knowledge_tags.value);
    document.getElementById('polish-import-tags').innerHTML=(primary?(typeof knowledgeBadge==='function'?knowledgeBadge(primary):`<span class="badge">${esc(primary)}</span>`):'<span class="help">未分类也可以保存。</span>')+tags.map(tag=>typeof knowledgeBadge==='function'?knowledgeBadge(primary,tag,true):`<span class="badge">${esc(tag)}</span>`).join('');
    suggestions=AlphaImportRules.suggest(form.elements.statement.value,categories);
    document.getElementById('polish-knowledge-suggestions').innerHTML=suggestions.length?'<p class="help">分类规则建议 · 根据明确关键词，仍需你判断</p>'+suggestions.map((item,i)=>`<button type="button" data-use-suggestion="${i}" ${primary?'disabled':''}>${esc(item.primary)} <span>依据“${esc(item.basis)}”</span></button>`).join('')+(primary?'<p class="help">已保留你填写的分类，不自动覆盖。</p>':''):'';
    document.getElementById('source-candidate').textContent=sourceDescription;
    const content=!!form.elements.statement.value.trim(),title=!!form.elements.title.value.trim(),source=!!form.elements.source_platform.value.trim(),original=!!form.elements.original_id.value.trim(),confirmed=form.elements.source_confirmed.checked;
    const rows=[['题目标题',title,title?'待你核对':'需要填写'],['题干与可见样例',content,content?'检查符号、格式与缺失内容':'可以先保存标题，稍后补充'],['来源平台 / 原题号',source||original,source||original?'候选或手动填写，需核对':'尚未识别，可留空'],['知识分类',!!primary,primary?'可修改或清空':'未分类，可稍后补充'],['人工确认',confirmed,confirmed?'已勾选，仍需手动点击保存':'完成检查后勾选确认']];
    aside.innerHTML=`<p class="eyebrow">IMPORT CONTEXT</p><h2>${ocrBusy?'正在本机识别':sourceBusy?'正在识别来源':confirmed?'准备保存题目':'核对后再加入学习库'}</h2><p class="polish-context-status" data-tone="${recognitionTone}" role="status">${esc(recognitionStatus)}</p><section><h3>当前来源</h3><strong>${esc(form.elements.source_platform.value||'尚未确定平台')}</strong><p class="help">${esc(form.elements.original_id.value?'原始题号 '+form.elements.original_id.value:'题号尚未识别，可手动补充或留空')}</p><p class="help">${esc(sourceDescription)}</p></section><section><h3>保存前核对</h3><ul class="polish-checklist">${rows.map(([label,ready,detail])=>`<li><span class="polish-check-indicator ${ready?'is-present':''}" aria-hidden="true">${ready?'✓':'○'}</span><div><strong>${label}</strong><small>${detail}</small></div></li>`).join('')}</ul></section><section class="polish-import-steps"><h3>接下来</h3><p><b>1</b> ${selector.value==='pending_ocr'?'查看OCR文本并修正不清楚的地方':selector.value==='pending_link'?'核对平台与题号，补充题目内容':'填写标题，按需补充题目内容'}</p><p><b>2</b> 确认内容与来源，手动保存</p><p><b>3</b> 进入工作区，开始编程与回顾</p></section><p class="help">图片临时本地识别；链接只做本地规则匹配。这里没有外部AI分析，也不会自动保存。</p>`;
    const imageButton=document.getElementById('recognize-image');imageButton.disabled=ocrBusy||(!ocrAvailable&&ocrStatusKnown);imageButton.textContent=ocrBusy?'正在识别…':'重新识别截图';
    document.getElementById('recognize-source').disabled=sourceBusy;
    document.getElementById('recognize-source').textContent=sourceBusy?'正在识别…':'识别来源';
  }
  document.getElementById('polish-knowledge-suggestions').addEventListener('click',event=>{
    const button=event.target.closest('[data-use-suggestion]');if(!button||form.elements.knowledge_primary.value.trim())return;
    const item=suggestions[Number(button.dataset.useSuggestion)];if(!item)return;form.elements.knowledge_primary.value=item.primary;provenance.knowledge_primary='rule';touched.knowledge_primary++;form.elements.source_confirmed.checked=false;render();updateDirty();
  });
  function setMode(mode){
    serial++;sourceSerial++;ocrBusy=false;sourceBusy=false;selector.value=mode;selector.dispatchEvent(new Event('change',{bubbles:true}));
    const urlLabel=form.elements.source_url.closest('label');
    if(mode==='pending_link')urlPanel.prepend(urlLabel);else sourceSection.querySelector('legend').after(urlLabel);
    upload.hidden=mode!=='pending_ocr';urlPanel.hidden=mode!=='pending_link';manualPanel.hidden=mode!=='manual';
    chooser.querySelectorAll('[data-import-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.importMode===mode)));
    form.elements.source_confirmed.checked=false;
    status(mode==='pending_ocr'?(ocrStatusKnown&&!ocrAvailable?'本地OCR未安装，可直接手动补充下面的字段。':'上传截图后自动本地识别；已有手动内容会保留。'):mode==='pending_link'?'粘贴链接后点击识别来源；仅有规则证据的字段会预填。':'手动填写内容；来源与分类可暂留空。');updateDirty();
  }
  chooser.querySelectorAll('[data-import-mode]').forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.importMode)));
  function candidate(value,origin,before){
    form.sourceCandidate=value||{};form.elements.source_confirmed.checked=false;
    const preserved=[];
    for(const [name,key] of [['source_platform','platform'],['original_id','original_id']])if(!applyField(name,value?.[key]||'',before,origin)&&form.elements[name].value)preserved.push(labelFor(name));
    sourceDescription=`${value?.platform||'平台未识别'} · ${value?.original_id||'题号未识别'}。${(value?.basis||[]).join('；')||'无可靠匹配，可手动补充。'}${preserved.length?' 已保留你填写的'+preserved.join('、')+'。':''}${value?.duplicates?.length?' 发现已有相同来源练习，保存会创建新记录，不会覆盖。':''}`;
  }
  async function identifySource(){
    if(sourceBusy)return;const token=++sourceSerial,before=snapshot(),url=form.elements.source_url.value.trim();
    if(!url){status('请先粘贴来源链接。','warning');return;}
    sourceBusy=true;status('正在使用本地URL规则识别平台与题号…');
    try{const value=await api('/api/import/source','POST',{url});if(!isCurrent()||token!==sourceSerial||form.elements.source_url.value.trim()!==url)return;candidate(value,'url',before);status(value.platform?'已识别来源候选；请核对平台和题号，再补充题目内容。':'没有可靠来源匹配，可以继续手动填写。',value.platform?'neutral':'warning');updateDirty();}
    catch(error){if(isCurrent()&&token===sourceSerial){sourceDescription='识别失败；已填写的字段保留。';status('来源识别未完成：'+error.message+' 可继续手动填写。','warning');}}
    finally{if(isCurrent()&&token===sourceSerial){sourceBusy=false;render();}}
  }
  document.getElementById('recognize-source').addEventListener('click',identifySource);
  async function identifyImage(){
    if(ocrBusy)return;const file=fileInput.files[0];if(!file){status('请先选择PNG、JPEG或WebP题目截图。','warning');return;}
    if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1024*1024){status('请选择不超过5MB的PNG、JPEG或WebP图片。','warning');return;}
    if(ocrStatusKnown&&!ocrAvailable){status('本地OCR未安装。图片可以预览，题目仍可手动录入。','warning');return;}
    const token=++serial,before=snapshot();ocrBusy=true;form.elements.source_confirmed.checked=false;status('正在本机识别截图，最多等待45秒；可以继续编辑其他字段。');document.getElementById('ocr-status').textContent='本地识别中…';
    try{
      const buffer=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<buffer.length;i+=8192)binary+=String.fromCharCode(...buffer.subarray(i,i+8192));
      const draft=await api('/api/import/ocr','POST',{image_base64:btoa(binary)});
      if(!isCurrent()||token!==serial||fileInput.files[0]!==file||selector.value!=='pending_ocr')return;
      const rawTitle=AlphaImportRules.titleFromText(draft.text||draft.statement||'');
      const titleApplied=applyField('title',rawTitle,before,'ocr'),statementApplied=applyField('statement',draft.statement||draft.text,before,'ocr');candidate(draft.source,'ocr',before);
      const box=document.getElementById('ocr-draft');box.hidden=false;
      document.getElementById('ocr-draft-fields').innerHTML=field('识别标题草稿','ocr_title',rawTitle,'maxlength="160"')+area('完整OCR文字草稿（含可见样例）','ocr_statement',draft.statement||draft.text,'maxlength="30000"')+`<p class="help">${esc(AlphaImportRules.confidence(draft.lines))}</p><button type="button" id="apply-ocr-draft">将草稿补入未编辑字段</button>`;
      draftSnapshot=snapshot();
      document.getElementById('apply-ocr-draft').addEventListener('click',()=>{
        if(!draftSnapshot)return;const kept=[];
        for(const [name,draftName] of [['title','ocr_title'],['statement','ocr_statement']]){
          const value=form.elements[draftName].value,edited=value!==(name==='title'?rawTitle:(draft.statement||draft.text));
          if(applyField(name,value,draftSnapshot,edited?'ocr_edited':'ocr')){if(edited)touched[name]++;}
          else if(form.elements[name].value)kept.push(labelFor(name));
        }
        draftSnapshot=snapshot();form.elements.source_confirmed.checked=false;status(kept.length?'草稿已补入未编辑字段；保留了你填写的'+kept.join('、')+'。':'草稿已补入表单，请核对后手动保存。');updateDirty();
      });
      const kept=[!titleApplied&&form.elements.title.value?'标题':'',!statementApplied&&form.elements.statement.value?'题干':''].filter(Boolean);
      document.getElementById('ocr-status').textContent=`${draft.engine} · ${draft.lines?.length||0} 行 · 原图未保存`;
      status('OCR文字已进入可编辑草稿。'+(kept.length?'保留了你手动编辑的'+kept.join('、')+'。':'已预填可识别内容。')+(!rawTitle?'标题无法可靠判断，请手动填写。':'')+'请核对符号、格式与缺失内容。');updateDirty();
    }catch(error){if(isCurrent()&&token===serial){document.getElementById('ocr-status').textContent='识别未完成';status('截图识别未完成：'+error.message+' 已填写的内容保留，可以继续手动录入。','warning');}}
    finally{if(isCurrent()&&token===serial){ocrBusy=false;render();}}
  }
  fileInput.addEventListener('change',()=>{
    serial++;ocrBusy=false;const file=fileInput.files[0];form.elements.source_confirmed.checked=false;
    if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl='';}document.getElementById('image-preview').replaceChildren();
    if(!file){hidden.value='';render();return;}
    if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1024*1024){fileInput.value='';hidden.value='';status('请选择不超过5MB的PNG、JPEG或WebP图片。','warning');notify('图片格式或大小不符合要求。',true);return;}
    hidden.value=file.name+':'+file.size+':'+file.lastModified;
    previewUrl=URL.createObjectURL(file);const image=new Image();image.alt='当前题目截图预览';image.src=previewUrl;document.getElementById('image-preview').append(image);
    updateDirty();identifyImage();
  });
  document.getElementById('recognize-image').addEventListener('click',identifyImage);
  form.addEventListener('submit',event=>{
    if(ocrBusy||sourceBusy){event.preventDefault();event.stopImmediatePropagation();notify('识别仍在进行。可以等待完成，或切换到手动录入后保存。');}
  },true);
  // Current drafts remain in this live form when switching methods or themes.
  // The original navigation guard protects edits; no learner content is written to browser storage.
  setMode('pending_ocr');
  const [ocrStatus,bank]=await Promise.allSettled([api('/api/import/status'),api('/api/bank')]);
  if(!isCurrent())return;
  ocrStatusKnown=true;ocrAvailable=ocrStatus.status==='fulfilled'&&ocrStatus.value.ocr_available;
  document.getElementById('ocr-status').textContent=ocrAvailable?'本地CPU引擎可用 · 上传后自动识别':'本地OCR暂不可用 · 手动导入仍可用';
  if(!ocrAvailable&&selector.value==='pending_ocr')status('本地OCR未安装或状态无法读取。可以继续手动填写题目内容。','warning');
  if(bank.status==='fulfilled'){
    const questions=Array.isArray(bank.value.items)?bank.value.items:Array.isArray(bank.value.questions)?bank.value.questions:[];categories=[...new Set(questions.map(q=>q.knowledge?.primary).filter(Boolean))];const tags=[...new Set(questions.flatMap(q=>q.knowledge?.tags||[]))];
    document.getElementById('polish-primary-options').innerHTML=categories.map(value=>`<option value="${esc(value)}"></option>`).join('');document.getElementById('polish-tag-options').innerHTML=tags.map(value=>`<option value="${esc(value)}"></option>`).join('');
  }
  render();
}
