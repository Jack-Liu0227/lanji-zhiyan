/* RSS configuration remains global and feeds the existing intelligence reader. */
window.RSSSettings=(()=>{
  const h=value=>esc(String(value??''));
  if(!SET_SECS.some(section=>section[0]==='rss'))SET_SECS.splice(3,0,['rss','📡','RSS 订阅源']);
  const style=document.createElement('style');style.textContent=`
    .rss-settings .rss-settings-head,.rss-settings .rss-source-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
    .rss-settings .rss-settings-head>div,.rss-settings .rss-source-head>div{flex:1;min-width:160px}.rss-settings h3,.rss-settings h4{margin:0 0 6px;overflow-wrap:anywhere}
    .rss-settings .rss-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.rss-settings .rss-source{margin-top:12px;padding:16px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}
    .rss-settings .rss-source-url{font-size:12px;color:var(--muted);overflow-wrap:anywhere;margin:9px 0}.rss-settings .rss-source-meta{font-size:11px;line-height:1.8;color:var(--muted)}
    .rss-settings .rss-source-error,.rss-settings [data-rss-error]{color:var(--err);white-space:pre-wrap;overflow-wrap:anywhere}.rss-settings .btn{font-size:12px}.rss-settings [data-rss-status]{margin:12px 0}
    .intel .rss-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .rss-settings .rss-empty{padding:24px 0;line-height:1.8}.rss-settings-dialog label{display:grid;gap:7px}.rss-settings-dialog input[type=checkbox]{width:auto}.rss-settings-dialog .rss-enabled{display:flex;gap:8px;align-items:center}
  `;document.head.append(style);
  function markup(){return `<section class="rss-settings card" data-rss-settings><div class="rss-settings-head"><div><h3>情报日报 · RSS 订阅源</h3><p class="hint">配置全局资讯来源，启用后参与情报日报刷新。保存配置不会立即联网抓取。</p></div><div class="rss-actions"><button type="button" class="btn" data-rss-add disabled>新增订阅源</button><button type="button" class="btn ghost" data-rss-defaults disabled>补充内置源</button></div></div><p class="hint" data-rss-status role="status">正在读取订阅源…</p><p data-rss-error role="alert"></p><div data-rss-list></div></section>`;}
  async function mount(host){
    if(!host)return;let sources=[],busy=false;
    const el=name=>host.querySelector('[data-rss-'+name+']');
    const error=err=>{if(host.isConnected)el('error').textContent=err?.message||String(err||'');};
    function lock(value){busy=value;host.querySelectorAll('button').forEach(button=>button.disabled=value);}
    function paint(){
      el('status').textContent=sources.length+' 个订阅源 · '+sources.filter(source=>source.enabled).length+' 个已启用';
      el('list').innerHTML=sources.map(source=>`<article class="rss-source" data-rss-source="${h(source.id)}"><div class="rss-source-head"><div><h4>${h(source.title)}</h4><span class="pill">${source.enabled?'已启用':'已停用'}</span> <span class="hint">${h(source.category)}</span></div><div class="rss-actions"><button type="button" class="btn ghost" data-rss-edit="${h(source.id)}">编辑</button><button type="button" class="btn ghost" data-rss-toggle="${h(source.id)}">${source.enabled?'停用':'启用'}</button><button type="button" class="btn ghost" data-rss-delete="${h(source.id)}">删除</button></div></div><p class="rss-source-url">${h(source.url)}</p><div class="rss-source-meta">已入库 ${h(source.count||0)} 条 · 上次抓取：${h(source.last_fetch||'尚未抓取')}</div>${source.last_error?`<div class="rss-source-error">抓取失败：${h(source.last_error)}</div>`:''}</article>`).join('')||'<p class="hint rss-empty">还没有订阅源。可新增 RSS 地址，或点击“补充内置源”。</p>';
      host.querySelectorAll('[data-rss-edit]').forEach(button=>button.onclick=()=>edit(sources.find(source=>source.id===button.dataset.rssEdit)));
      host.querySelectorAll('[data-rss-toggle]').forEach(button=>button.onclick=()=>action(async()=>{const source=sources.find(item=>item.id===button.dataset.rssToggle);await api('/api/rss/sources/save',{id:source.id,enabled:!source.enabled});await load();}));
      host.querySelectorAll('[data-rss-delete]').forEach(button=>button.onclick=async()=>{if(busy)return;const source=sources.find(item=>item.id===button.dataset.rssDelete);if(!await uiConfirm('删除订阅源「'+source.title+'」？已抓取的资讯会保留。',{ok:'删除订阅源',cancel:'取消',danger:true}))return;await action(async()=>{await api('/api/rss/sources/delete',{id:source.id});await load();});});
    }
    async function load(){const data=await api('/api/rss/sources');if(!host.isConnected)return;sources=data.sources||[];paint();}
    async function action(work){if(busy)return;lock(true);error('');try{await work();}catch(err){error(err);}finally{if(host.isConnected)lock(false);}}
    function edit(source=null){
      if(busy)return;
      const dialog=Notebook.dialog(source?'编辑 RSS 订阅源':'新增 RSS 订阅源',`<form class="nb-form"><label>订阅源名称<input class="control" name="title" aria-label="订阅源名称" maxlength="200" required value="${h(source?.title||'')}" placeholder="例如：材料科学研究动态"></label><label>RSS 地址<input class="control" name="url" aria-label="RSS 地址" type="url" required maxlength="8192" value="${h(source?.url||'')}" placeholder="https://example.org/feed.xml"></label><label>分类<input class="control" name="category" aria-label="订阅源分类" maxlength="60" value="${h(source?.category||'学术论文')}"></label><label class="rss-enabled"><input type="checkbox" name="enabled" ${source?.enabled===0?'':'checked'}>启用抓取</label><p class="hint">仅保存订阅配置；返回情报日报点击“刷新”后抓取内容。</p><button type="submit" class="btn">保存订阅源</button></form>`);
      dialog.classList.add('rss-settings-dialog');
      dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]');button.disabled=true;
        try{await api('/api/rss/sources/save',{id:source?.id||'',title:form.elements.title.value,url:form.elements.url.value,category:form.elements.category.value,enabled:form.elements.enabled.checked});dialog.close();await load();}
        catch(err){if(dialog.isConnected)Notebook.error(dialog,err);else error(err);}finally{button.disabled=false;}
      };
    }
    el('add').onclick=()=>edit();el('defaults').onclick=()=>action(async()=>{const result=await api('/api/rss/sources/defaults',{});await load();el('status').textContent+=' · 新增 '+result.added+' 个内置源，已有配置保留';});
    await action(load);
  }
  const previousPanel=setPanel;setPanel=function(){return setSec==='rss'?markup():previousPanel();};
  const previousSettings=renderSettings;renderSettings=function(container){previousSettings(container);if(setSec==='rss')mount(container.querySelector('[data-rss-settings]'));};
  return {open(){setSec='rss';openTab('settings');}};
})();
