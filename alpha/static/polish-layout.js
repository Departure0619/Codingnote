'use strict';
// Targeted Round 11 polish. Original forms and their save handlers stay intact.
function polishImportActions(){
  app.querySelectorAll('.page-heading a[href="#/import"]').forEach(link=>{
    if(link.classList.contains('polish-import-action'))return;
    link.classList.add('primary','polish-import-action');
    link.innerHTML=finalIcon('import')+'<span>导入题目</span>';
  });
}
function mountPolishSettings(){
  const grid=app.querySelector('.settings-grid.final-settings');
  if(!grid||grid.parentElement.classList.contains('polish-settings-layout'))return;
  const layout=document.createElement('div');layout.className='polish-settings-layout';grid.before(layout);layout.append(grid);
  const aside=document.createElement('aside');aside.className='polish-settings-context';aside.setAttribute('aria-label','当前设置说明');layout.append(aside);
  const modes={light:'轻量辅助',standard:'标准学习',deep:'深度学习',custom:'自定义'};
  const descriptions=[
    ['当前学习模式','八项偏好分别控制辅导、错题整理和复习。模式是起点，每一项都可以继续调整。','保存到本机，对未设置单题覆盖的练习生效。单题覆盖可在工作区的学习记录中调整。'],
    ['复习与基础辅导','这里控制原有模拟辅导和主动复习建议；真实辅导优先使用八项学习偏好。','关闭主动建议不会删除错题或已有复习记录。'],
    ['模型与学习方式分开设置','选择模型不会改变辅导深度；保存配置也不会自动测试连接或发送问题。','真实调用仍需密钥和明确授权。真实教学验收保持部分完成，历史停止与费用记录保留。'],
    ['备份与导出','脱敏导出用于查看和转移公开学习记录，包含版本与公开反馈。','完整恢复请按页面说明正常停服、另建备份和新恢复目录，保留原学习库。'],
    ['选择适合你的外观','浅色便于阅读题目，深色便于长时间编程。主题选择会在本浏览器中保留。','切换主题保留代码草稿、筛选和表单，不触发执行或模型调用。']
  ];
  const render=()=>{
    if(!aside.isConnected)return;
    const buttons=[...app.querySelectorAll('.settings-tabs [role=tab]')],index=Math.max(0,buttons.findIndex(b=>b.getAttribute('aria-selected')==='true'));
    const [title,explanation,scope]=descriptions[index]||descriptions[0];
    const form=grid.querySelector(index===0?'#preferences-form':index===1?'#settings-form':'#ai-form');
    const mode=index===0?(modes[grid.querySelector('[name=preference_preset]')?.value]||'自定义'):index===4?(document.documentElement.dataset.theme==='dark'?'深色主题':'浅色主题'):index===2?(state.status?.ai?.mode==='disabled'?'AI 当前关闭':'以已保存配置为准'):'';
    const pending=index<3&&!!form&&dirtyForm(form);
    aside.replaceChildren();
    const kicker=document.createElement('p');kicker.className='eyebrow';kicker.textContent='设置说明';
    const h=document.createElement('h2');h.textContent=title;aside.append(kicker,h);
    if(mode){const strong=document.createElement('p');strong.className='polish-current-mode';strong.textContent=mode;aside.append(strong);}
    for(const text of [explanation,scope]){const p=document.createElement('p');p.textContent=text;aside.append(p);}
    if(index<3){const status=document.createElement('p');status.className='polish-settings-save'+(pending?' is-dirty':'');status.setAttribute('role','status');status.textContent=pending?'有未保存修改 · 点击保存后生效':'当前显示已保存的设置';aside.append(status);}
  };
  grid.addEventListener('input',render);grid.addEventListener('change',render);
  const observer=new MutationObserver(()=>{if(!grid.isConnected){observer.disconnect();return;}render();});
  observer.observe(grid,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden']});
  const nav=app.querySelector('.settings-tabs');if(nav)new MutationObserver(render).observe(nav,{subtree:true,attributes:true,attributeFilter:['aria-selected']});
  aside.refreshSettingsContext=render;render();
}
document.addEventListener('alpha:themechange',()=>document.querySelector('.polish-settings-context')?.refreshSettingsContext?.());
