'use strict';
// Current Bank.practice inherits the formal language registry; explicit future
// per-question metadata takes precedence, including an empty supported set.
(() => {
  const own=(value,key)=>value!=null&&Object.prototype.hasOwnProperty.call(value,key);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const shortNames={c:'C',cpp:'C++',python:'Python',java:'Java',javascript:'JavaScript'};
  function ordered(ids,registry){
    const unique=[...new Set(ids)],known=Object.keys(registry||{});
    return known.filter(id=>unique.includes(id)).concat(unique.filter(id=>!known.includes(id)).sort());
  }
  function support(question,registry){
    if(own(question,'languages')){
      const ids=question.languages;
      if(!Array.isArray(ids)||ids.some(id=>typeof id!=='string'||!id.trim()))return {languages:null,inherited:false};
      return {languages:ordered(ids.map(id=>id.trim()),registry),inherited:false};
    }
    const languages=Object.keys(registry||{});
    return {languages:languages.length?languages:null,inherited:true};
  }
  function analyze(questions,registry){
    const sets=questions.map(q=>support(q,registry).languages),known=sets.filter(Array.isArray);
    const anyUnknown=sets.some(ids=>ids===null),languages=known[0]||[];
    const uniform=sets.length>0&&!anyUnknown&&languages.length>0&&known.every(ids=>ids.length===languages.length&&ids.every(id=>languages.includes(id)));
    return {uniform,languages:uniform?languages:[],anyUnknown,common:known.length?known[0].filter(id=>known.every(ids=>ids.includes(id))):[],union:ordered(known.flat(),registry)};
  }
  function label(id,registry,versioned=true){return versioned?(registry?.[id]?.display_name||id):(shortNames[id]||registry?.[id]?.display_name||id);}
  function describe(question,registry){
    const {languages}=support(question,registry);
    return languages===null?'暂不可核对':languages.length?languages.map(id=>label(id,registry)).join(' / '):'当前题目未声明可用语言';
  }
  function summary(info,registry){
    if(info.uniform)return '全部题目支持 '+info.languages.map(id=>label(id,registry,false)).join(' / ');
    if(info.anyUnknown)return '部分题目的语言支持暂不可核对，完整信息见题目详情';
    return '语言支持因题目而异'+(info.common.length?' · 共同支持 '+info.common.map(id=>label(id,registry,false)).join(' / '):'')+'；完整语言与版本见题目详情';
  }
  function row(question,info,registry){
    if(info.uniform)return '';
    const {languages}=support(question,registry),text=languages===null?'语言支持暂不可核对':languages.length?'支持 '+languages.length+' 种语言':'未声明可用语言';
    return `<small class="final-language-note" title="${escape(describe(question,registry))}">${text}</small>`;
  }
  function executable(question,registry){return (support(question,registry).languages||[]).filter(id=>own(registry,id));}
  window.AlphaBankLanguages=Object.freeze({support,analyze,label,describe,summary,row,executable});
})();
