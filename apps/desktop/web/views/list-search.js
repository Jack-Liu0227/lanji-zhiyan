/* Shared filtering for local catalogs. Existing server-backed searches keep their own scope. */
const ListSearch=(()=>{
  const queries=new Map();
  function mount(list,{key,label,selector,place=list,inside=false,text=row=>row.textContent}={}){
    if(!list||!place)return;
    let bar=inside?place.firstElementChild:place.previousElementSibling;
    if(bar?.dataset.listSearch!==key){
      bar=document.createElement('div');bar.className='list-search';bar.dataset.listSearch=key;
      bar.innerHTML='<input type="search" class="control" autocomplete="off"><button type="button" class="pw-text-button">清空</button><span role="status" aria-live="polite"></span>';
      bar.querySelector('input').setAttribute('aria-label',label);bar.querySelector('input').placeholder=label+'…';
      bar.querySelector('input').value=queries.get(key)||'';
      if(inside)place.prepend(bar);else place.before(bar);
    }
    const field=bar.querySelector('input'),counter=bar.querySelector('[role=status]'),clear=bar.querySelector('button');
    const apply=()=>{
      const query=field.value.trim().toLocaleLowerCase(),tokens=query.split(/\s+/).filter(Boolean),rows=[...list.querySelectorAll(selector)];
      let count=0;for(const row of rows){const match=tokens.every(word=>String(text(row)).toLocaleLowerCase().includes(word));row.hidden=!match;row.toggleAttribute('data-list-search-hidden',!match);if(match)count++;}
      queries.set(key,field.value);clear.hidden=!field.value;
      const message=query?(count?`${count} / ${rows.length} 项`:'没有匹配结果，请更换关键词'):`${rows.length} 项`;
      if(counter.textContent!==message)counter.textContent=message;
    };
    field.oninput=apply;field.onkeydown=event=>{if(event.key==='Escape'&&field.value){event.preventDefault();event.stopPropagation();field.value='';apply();}};
    clear.onclick=()=>{field.value='';apply();field.focus();};apply();
  }
  function enhance(){
    const main=document.getElementById('main');if(!main)return;
    const view=route();
    if(view==='assistants')mount(main.querySelector('.ag-grid'),{key:'experts',label:'搜索专家名称、专长或模型',selector:':scope > .card'});
    if(main.querySelector('#ag-provider-select')){
      mount(main.querySelector('.ag-grid'),{key:'providers',label:'搜索已配置的模型提供商',selector:':scope > .card'});
      mount(main.querySelector('.ag-model-grid'),{key:'models',label:'搜索已采用模型',selector:':scope > *'});
    }
    if(view==='team')mount(main.querySelector('#tofSheet'),{key:'teams:'+curTask,label:'搜索团队或成员',selector:'.tof-room[data-office]',place:main.querySelector('.tof-body')});
    mount(main.querySelector('.ag-team-nav'),{key:'team-settings',label:'搜索团队',selector:'[data-ag-team]',inside:true});
    mount(main.querySelector('.set-nav'),{key:'settings',label:'搜索设置',selector:'[data-set]',inside:true});
    mount(main.querySelector('.channel-list'),{key:'channels',label:'搜索消息渠道',selector:'[data-channel]'});
    mount(main.querySelector('.ptb-tools'),{key:'toolbox',label:'搜索论文工具',selector:'[data-tb-tool]'});
    mount(main.querySelector('[data-rss-list]'),{key:'rss-sources',label:'搜索订阅源名称、分类或地址',selector:'[data-rss-source]'});
    mount(main.querySelector('.wfl-editor-list'),{key:'workflow-templates',label:'搜索工作流模板',selector:'[data-wfl-edit]',inside:true});
    const schedules=main.querySelector('[data-schedule-list]');
    mount(schedules,{key:'schedules:'+curTask,label:'搜索自动化规则与运行记录',selector:'.sch-card,.sch-run',place:main.querySelector('[data-schedule-search-anchor]')});
  }
  let pending=false;
  const observer=new MutationObserver(()=>{if(pending)return;pending=true;queueMicrotask(()=>{pending=false;enhance();});});
  observer.observe(document.getElementById('main'),{childList:true,subtree:true});
  return {mount};
})();
