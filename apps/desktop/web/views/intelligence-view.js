/* RSS discovery, source reading and project bibliography share one list. */
const IntelligenceView=(()=>{
  const h=value=>esc(String(value??''));
  const filters={source:'',q:'',year:'',project_id:''};
  function renderRow(row,selected,expanded,recovering){
    const url=LiteratureImport.safeLink(row.link);
    const paragraphs=(row.summary||'').trim().split(/\n\s*\n/).filter(Boolean);
    return `<article class="it-row${selected?' sel':''}" data-intel-id="${h(row.id)}">
      <input type="checkbox" data-it-select aria-label="选择 ${h(row.title)}" ${selected?'checked':''}>
      <div class="it-content">
        ${url?`<a class="it-t" href="${h(url)}" target="_blank" rel="noopener noreferrer">${h(row.title)}</a>`:`<span class="it-t">${h(row.title)}</span>`}
        <div class="it-m">
          <div class="it-authors"><span class="it-meta-label">作者</span> ${h(row.authors||'未提供')}</div>
          <div class="it-meta-line">
            <span><span class="it-meta-label">发布日期</span> ${h(row.published_at||'未提供')}</span>
            <span><span class="it-meta-label">来源</span> ${h(row.source_title||'未提供')}${row.source_deleted?'（已移除）':''}</span>
          </div>
        </div>
        <details class="it-summary" ${expanded?'open':''}>
          <summary>摘要与详情</summary>
          <div class="it-summary-body">
            ${paragraphs.length?`<div class="it-abstract">${paragraphs.map(text=>`<p>${h(text)}</p>`).join('')}</div>`:'<p class="hint">订阅源未提供摘要</p>'}
            ${url?`<a class="it-url" href="${h(url)}" target="_blank" rel="noopener noreferrer">${h(url)}</a>`:'<p class="hint">未提供可打开的网页链接</p>'}
            ${row.can_recover_arxiv?`<button type="button" class="btn ghost it-recover" data-it-recover ${recovering?'disabled':''}>${recovering?'正在读取 arXiv…':'从 arXiv 补全摘要'}</button>`:''}
            <p class="hint it-recovery-status" data-it-recovery-status role="status">${h(row.recoveryMessage)}</p>
          </div>
        </details>
        <div class="it-actions">
          ${url?`<a class="btn ghost" data-it-original href="${h(url)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a><button class="btn ghost" data-it-copy>复制链接</button>`:''}
          <button class="btn ghost" data-it-import>${row.imported_result_id?'已在项目中 · 查看':'导入项目'}</button>
          ${row.imported_result_id?'<span class="pill it-imported">题录已入库</span>':''}
        </div>
      </div>
    </article>`;
  }
  async function render(container){
    if(!S.tasks?.some(p=>p.id===filters.project_id))filters.project_id=S.tasks?.some(p=>p.id===curTask)?curTask:'';
    const state={rows:[],selected:new Set(),recovering:new Set(),version:0,timer:0};
    container.innerHTML=`<section class="card intel intel-library"><div class="it-head"><div><h3>情报日报 <span class="pill" id="itCount">…</span></h3><p class="hint">发现文献，查看原文，再把需要的题录导入研究项目。</p></div><div class="rss-actions"><button class="btn ghost" id="itManualImport">链接 / DOI 导入</button><button class="btn ghost" id="itSourceSettings">订阅源设置</button><button class="btn" id="itGo">刷新</button></div></div><div class="it-bar"><select id="itSrc" class="control" aria-label="筛选情报来源"><option value="">全部来源</option></select><input id="itQ" type="search" aria-label="搜索情报" placeholder="关键词（标题 / 摘要 / 作者）" value="${h(filters.q)}"><input id="itYear" aria-label="筛选年份" placeholder="年份" inputmode="numeric" value="${h(filters.year)}"></div><div class="it-import-bar"><label>导入项目<select class="control" id="itProject" aria-label="情报导入项目"><option value="">选择目标项目</option>${(S.tasks||[]).map(p=>`<option value="${h(p.id)}" ${p.id===filters.project_id?'selected':''}>${h(p.title)}</option>`).join('')}</select></label><button class="btn ghost" id="itOpenLibrary">打开项目文献</button><span class="pw-spacer"></span><label><input type="checkbox" id="itSelectAll"> 选择当前结果</label><button class="btn" id="itImportSelected" disabled>导入所选（0）</button></div><p class="hint" id="itMsg" role="status" aria-live="polite"></p><div class="it-list" id="itList"></div></section>`;
    const el=id=>container.querySelector('#'+id),live=()=>container.isConnected&&!!el('itList');
    function selection(){const ids=state.rows.map(row=>row.id);el('itSelectAll').checked=!!ids.length&&ids.every(id=>state.selected.has(id));el('itSelectAll').indeterminate=ids.some(id=>state.selected.has(id))&&!el('itSelectAll').checked;el('itImportSelected').disabled=!state.selected.size;el('itImportSelected').textContent=`导入所选（${state.selected.size}）`;el('itOpenLibrary').disabled=!filters.project_id;}
    function importItems(items){LiteratureImport.open({projectId:filters.project_id,items,onImported:async result=>{filters.project_id=result.project_id;el('itProject').value=result.project_id;await load();}});}
    async function recover(row,card){
      if(state.recovering.has(row.id))return;
      const button=card.querySelector('[data-it-recover]');
      const status=card.querySelector('[data-it-recovery-status]');
      state.recovering.add(row.id);
      button.disabled=true;
      button.textContent='正在读取 arXiv…';
      status.textContent='';
      try{
        const {item}=await api('/api/rss/recover-arxiv',{item_id:row.id});
        state.recovering.delete(row.id);
        if(!live())return;
        const ownsFocus=document.activeElement===button||document.activeElement===document.body;
        state.rows=state.rows.map(current=>current.id===item.id?{...current,...item,recoveryMessage:'已补全 arXiv 信息'}:current);
        paint();
        if(ownsFocus)el('itList').querySelector(`[data-intel-id="${CSS.escape(row.id)}"] summary`)?.focus({preventScroll:true});
      }catch(error){
        state.recovering.delete(row.id);
        if(!live())return;
        const current=el('itList').querySelector(`[data-intel-id="${CSS.escape(row.id)}"]`);
        if(!current)return;
        current.querySelector('[data-it-recovery-status]').textContent=error.message;
        const retry=current.querySelector('[data-it-recover]');
        if(retry){
          retry.disabled=false;
          retry.textContent='从 arXiv 补全摘要';
        }
      }
    }
    function paint(){
      const expanded=new Set([...el('itList').querySelectorAll('.it-summary[open]')].map(details=>details.closest('[data-intel-id]').dataset.intelId));
      el('itList').innerHTML=state.rows.map(row=>renderRow(row,state.selected.has(row.id),expanded.has(row.id),state.recovering.has(row.id))).join('')||'<p class="hint">暂无情报。可刷新已启用的订阅源，或通过链接 / DOI 手动导入文献。</p>';
      el('itList').querySelectorAll('[data-intel-id]').forEach(card=>{const row=state.rows.find(row=>row.id===card.dataset.intelId);card.querySelector('[data-it-select]').onchange=event=>{event.target.checked?state.selected.add(row.id):state.selected.delete(row.id);card.classList.toggle('sel',event.target.checked);selection();};card.querySelector('[data-it-import]').onclick=()=>row.imported_result_id?ProjectLiterature.open(filters.project_id):importItems([row]);
        const recoverButton=card.querySelector('[data-it-recover]');
        if(recoverButton)recoverButton.onclick=()=>recover(row,card);
        // 勾选框视觉隐藏（intelligence-view.css 的 -9999px），所以整行可点：点空白处即切换勾选，行内链接/按钮/摘要照旧
        card.addEventListener('click',event=>{if(event.target.closest('a,button,input,details,summary'))return;const box=card.querySelector('[data-it-select]');box.checked=!box.checked;box.dispatchEvent(new Event('change',{bubbles:true}));card.classList.toggle('sel',box.checked);});const copy=card.querySelector('[data-it-copy]');if(copy)copy.onclick=async()=>{try{await navigator.clipboard.writeText(row.link);el('itMsg').textContent='原文链接已复制';}catch{el('itMsg').textContent='复制未成功，请在“摘要与详情”中复制链接。';}};});selection();
    }
    async function load(){const version=++state.version;const data=await api('/api/rss/items?'+new URLSearchParams(filters));if(!live()||version!==state.version)return;state.rows=data.items||[];state.selected=new Set([...state.selected].filter(id=>state.rows.some(row=>row.id===id)));el('itCount').textContent=`${data.total||0} 条`;paint();}
    async function sources(){const data=await api('/api/rss/sources');if(!live())return;if(filters.source&&!data.sources.some(source=>source.id===filters.source))filters.source='';el('itSrc').innerHTML='<option value="">全部来源</option>'+data.sources.map(source=>`<option value="${h(source.id)}" ${source.id===filters.source?'selected':''}>${h(source.title)}（${source.count}）</option>`).join('');}
    async function reload(){try{await load();}catch(error){if(live())el('itMsg').textContent=error.message;}}
    el('itGo').onclick=async()=>{const button=el('itGo');if(button.disabled)return;button.disabled=true;button.textContent='抓取中…';el('itMsg').textContent='正在抓取已启用的 RSS 来源…';try{const result=await api('/api/rss/refresh',{});if(!live())return;el('itMsg').textContent=`已抓取 ${result.fetched}/${result.sources} 个来源，新增 ${result.added} 条${result.failures?.length?'；失败 '+result.failures.length+' 个（'+result.failures.map(row=>row.title).join('、')+'）':''}`;await sources();await load();}catch(error){if(live())el('itMsg').textContent='刷新失败：'+error.message;}finally{if(live()){button.disabled=false;button.textContent='刷新';}}};
    el('itSourceSettings').onclick=()=>RSSSettings.open();el('itManualImport').onclick=()=>LiteratureImport.open({projectId:filters.project_id,onImported:reload});
    el('itOpenLibrary').onclick=()=>ProjectLiterature.open(filters.project_id);
    el('itImportSelected').onclick=()=>importItems(state.rows.filter(row=>state.selected.has(row.id)));
    el('itSelectAll').onchange=event=>{state.selected=event.target.checked?new Set(state.rows.map(row=>row.id)):new Set();paint();};
    el('itProject').onchange=event=>{filters.project_id=event.target.value;reload();};el('itSrc').onchange=event=>{filters.source=event.target.value;reload();};
    for(const [id,key] of [['itQ','q'],['itYear','year']])el(id).oninput=event=>{filters[key]=event.target.value.trim();clearTimeout(state.timer);state.timer=setTimeout(reload,300);};
    try{await sources();await load();}catch(error){if(live())el('itMsg').textContent='读取失败：'+error.message;}
  }
  return {render};
})();
