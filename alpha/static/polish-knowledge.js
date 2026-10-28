'use strict';
// Exact, stable visual aliases only. These never change stored classification.
(() => {
  const categories=Object.freeze(['输入输出','条件','循环','数组','字符串','函数','基础结构','简单算法']);
  const families=Object.freeze({
    '输入输出':'blue','输入与输出':'blue','输入/输出':'blue','基础输入输出':'blue',
    '条件':'violet','条件判断':'violet','分支结构':'violet','条件与分支':'violet',
    '循环':'teal','循环结构':'teal','循环与迭代':'teal','循环迭代':'teal',
    '数组':'green','数组与字符串':'green','数组和字符串':'green',
    '字符串':'rose','字符串处理':'rose','查找与边界处理':'rose','查找算法':'rose','二分查找':'rose',
    '函数':'orange','函数与递归':'orange','函数和递归':'orange','递归':'orange',
    '基础结构':'indigo','数据结构':'indigo','基础数据结构':'indigo',
    '简单算法':'gold','数学与基础算法':'gold','基础算法':'gold','数学':'gold'
  });
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const family=primary=>{
    const value=String(primary??'').replace(/[\s\u3000]+/g,'');
    return Object.hasOwn(families,value)?families[value]:'neutral';
  };
  function badge(primary,label=primary,secondary=false){
    const text=String(label??'').trim()||'未分类';
    return `<span class="badge knowledge-badge knowledge-${family(primary)}${secondary?' knowledge-secondary':''}" data-knowledge-primary="${escape(primary||'')}"${secondary?' data-knowledge-secondary="true"':''} title="${escape(text)}">${escape(text)}</span>`;
  }
  function update(root=document.querySelector('#app')){
    if(!root)return;
    const nodes=[...(root.matches?.('[data-knowledge-primary]')?[root]:[]),...root.querySelectorAll('[data-knowledge-primary]')];
    for(const node of nodes){
      for(const token of [...node.classList])if(/^knowledge-(?:blue|violet|teal|green|rose|orange|indigo|gold|neutral)$/.test(token))node.classList.remove(token);
      node.classList.add('knowledge-badge','knowledge-'+family(node.dataset.knowledgePrimary));
      node.classList.toggle('knowledge-secondary',node.dataset.knowledgeSecondary==='true');
    }
  }
  window.knowledgeBadge=badge;
  window.polishKnowledgeTags=update;
  window.AlphaKnowledge=Object.freeze({family,categories});
})();
