/* 1.0 的项目文献页：只读列出 search_cache 里该项目的题录。
   2.0 版还挂着 Zotero 网页暂存导入（/api/zotero/web/staged/*），1.0 没有那条链路，故不移植。 */
const ProjectLiterature=(()=>{
  const h=value=>esc(String(value??''));
  async function open(projectId){
    if(!projectId)return;
    const origin=document.activeElement,dialog=document.createElement('dialog');
    dialog.className='pt-dialog pl-dialog';dialog.setAttribute('aria-label','项目文献');
    dialog.innerHTML=`<form method="dialog" class="li-form"><header><h3>项目文献 <span class="pill">…</span></h3><button type="button" class="pw-text-button" data-pl-close aria-label="关闭">×</button></header><div class="pl-body" data-pl-list><p class="hint">正在读取…</p></div><p class="pw-error" data-pl-error role="alert"></p><footer><span class="pw-spacer"></span><button type="button" class="btn ghost" data-pl-close>关闭</button></footer></form>`;
    dialog.querySelectorAll('[data-pl-close]').forEach(b=>b.onclick=()=>dialog.close());
    dialog.addEventListener('close',()=>{dialog.remove();if(origin?.isConnected)origin.focus();},{once:true});
    document.body.append(dialog);dialog.showModal();
    try{
      const data=await api('/api/library/project?task_id='+encodeURIComponent(projectId));
      const items=data.items||[];
      if(!dialog.isConnected)return;
      dialog.querySelector('.pill').textContent=items.length+' 篇';
      dialog.querySelector('[data-pl-list]').innerHTML=items.map(row=>{
        const url=/^https?:/i.test(row.pdf_url||'')?row.pdf_url:(/^https?:/i.test(row.url||'')?row.url:'');
        return `<article class="pl-row"><strong>${h(row.title)}</strong><p class="hint">${h(row.authors||'作者未知')} · ${h(row.year||'年份未知')} · ${h(row.source||'')}</p>${url?`<a class="btn ghost" href="${h(url)}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>`:'<p class="hint">没有可打开的链接</p>'}</article>`;
      }).join('')||'<p class="hint">这个项目还没有文献。去情报日报点“导入项目”，或用“链接 / DOI 导入”。</p>';
    }catch(error){
      if(dialog.isConnected)dialog.querySelector('[data-pl-error]').textContent=error.message;
    }
  }
  return {open};
})();
