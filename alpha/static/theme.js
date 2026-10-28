'use strict';
// Runs before styles are painted. Theme changes never remount learning forms.
(() => {
  const root=document.documentElement;
  let selected='light';
  try {if(localStorage.getItem('alpha:theme')==='dark')selected='dark';}catch(_){}
  function controls(){document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
    const dark=root.dataset.theme==='dark';
    button.textContent=dark?'浅色外观':'深色外观';
    button.setAttribute('aria-label',dark?'切换为浅色主题':'切换为深色主题');
    button.setAttribute('aria-pressed',String(dark));
  });}
  function set(value,persist=true){
    selected=value==='dark'?'dark':'light';root.dataset.theme=selected;root.style.colorScheme=selected;
    if(persist)try{localStorage.setItem('alpha:theme',selected);}catch(_){}
    controls();document.dispatchEvent(new CustomEvent('alpha:themechange'));
  }
  window.AlphaTheme={set,toggle:()=>set(selected==='dark'?'light':'dark')};
  set(selected,false);
  document.addEventListener('DOMContentLoaded',()=>{
    controls();document.addEventListener('click',event=>{if(event.target.closest('[data-theme-toggle]'))window.AlphaTheme.toggle();});
  });
})();
