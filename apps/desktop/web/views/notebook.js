/* Logical categories and hierarchical project file tree navigation. */
window.Notebook = (() => {
  let project = "", selected = "all", board = null, filters = [], selection = new Set();
  const loadTreeExpanded = () => {
    try {
      const raw = localStorage.getItem("rw-nb-tree-expanded");
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) return new Set(arr);
      }
    } catch (_) {}
    return new Set(); // 默认所有文件层级全部折叠
  };

  const saveTreeExpanded = () => {
    try {
      localStorage.setItem("rw-nb-tree-expanded", JSON.stringify([...treeExpanded]));
    } catch (_) {}
  };

  let treeExpanded = loadTreeExpanded();
  let treeCollapsed = localStorage.getItem("rw-nb-tree-collapsed") === "true";
  let viewMode = localStorage.getItem("rw-nb-view") || "tree"; // "tree" | "category"
  let editorMode = localStorage.getItem("rw-md-mode") || "split"; // "split" | "write" | "preview"
  let propCollapsed = localStorage.getItem("rw-md-prop-collapsed") === "true";
  let treeQuery = "";

  const node = id => board?.catalog?.categories?.find(c => c.id === id);
  const ordered = () => [...(board?.catalog?.categories || [])].sort((a,b) => a.sort_order-b.sort_order || a.name.localeCompare(b.name));
  const label = c => c ? (c.parent_id ? `${node(c.parent_id)?.name || ""} / ` : "") + c.name : "待整理";
  const children = id => new Set([id, ...(board?.catalog?.categories || []).filter(c=>c.parent_id===id).map(c=>c.id)]);
  const current = () => node(selected);
  const options = (value="", exclude=new Set()) => `<option value="">待整理</option>` + ordered().filter(c=>!c.parent_id).flatMap(c=>[c,...ordered().filter(x=>x.parent_id===c.id)])
    .filter(c=>!exclude.has(c.id)).map(c=>`<option value="${esc(c.id)}" ${c.id===value?"selected":""}>${esc(label(c))}</option>`).join("");
  const suggestionTags = () => [...new Set([...(current()?.suggested_tags || []), ...(board?.tags || [])])];

  function tagPicker(host, initial, available, change, {allowCreate=true, disabled=false, name="标签"}={}){
    let values = [...initial], choices = [...new Set([...available,...values])];
    host.classList.add("nb-tag-picker");
    host.innerHTML = `<button type="button" class="btn ghost nb-tag-toggle" aria-expanded="false" ${disabled?"disabled":""}></button>
      <div class="nb-tag-popup" hidden><input type="search" class="control" aria-label="搜索${esc(name)}" placeholder="搜索${esc(name)}…"><div class="nb-tag-choices"></div>
      <div class="nb-actions"><button type="button" class="btn ghost nb-tag-clear">清空选择</button><button type="button" class="btn ghost nb-tag-done">完成</button></div></div><div class="nb-tag-chips"></div>`;
    const toggle=host.querySelector('.nb-tag-toggle'), popup=host.querySelector('.nb-tag-popup'), search=popup.querySelector('input'), list=host.querySelector('.nb-tag-choices');
    const close=()=>{popup.hidden=true;toggle.setAttribute('aria-expanded','false');};
    function paint(){
      toggle.textContent = `${name}${values.length ? ` · ${values.length}` : "（可选）"} ▾`;
      host.querySelector('.nb-tag-chips').innerHTML=values.map((t,i)=>`<button type="button" class="nb-tag" data-remove="${i}" aria-label="移除标签 ${esc(t)}" ${disabled?"disabled":""}>${esc(t)} ×</button>`).join('');
      host.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{values.splice(Number(b.dataset.remove),1);notify();});
      const q=search.value.trim().replace(/^#+/,''), shown=choices.filter(t=>t.toLocaleLowerCase().includes(q.toLocaleLowerCase()));
      list.innerHTML=shown.map(t=>`<label class="nb-tag-option"><input type="checkbox" value="${esc(t)}" ${values.includes(t)?"checked":""}><span>${esc(t)}</span></label>`).join('') || '<p class="hint">没有匹配的标签</p>';
      list.querySelectorAll('input').forEach(el=>el.onchange=()=>{values=el.checked?[...values,el.value]:values.filter(t=>t!==el.value);notify();});
      if(allowCreate && q && q.length<=60 && !choices.some(t=>t.toLocaleLowerCase()===q.toLocaleLowerCase())){
        const create=document.createElement('button');create.type='button';create.className='btn ghost';create.textContent=`新建标签：${q}`;
        create.onclick=()=>{choices.push(q);values.push(q);search.value='';notify();search.focus();};list.appendChild(create);
      }
    }
    function notify(){const active=document.activeElement, checkbox=active?.type==='checkbox'?active.value:null;paint();if(checkbox!==null)[...list.querySelectorAll('input')].find(el=>el.value===checkbox)?.focus();change([...values]);}
    toggle.onclick=()=>{popup.hidden=!popup.hidden;toggle.setAttribute('aria-expanded',String(!popup.hidden));if(!popup.hidden)search.focus();};
    search.oninput=paint;
    host.querySelector('.nb-tag-clear').onclick=()=>{values=[];notify();};
    host.querySelector('.nb-tag-done').onclick=()=>{close();toggle.focus();};
    host.addEventListener('keydown',e=>{
      if(e.isComposing)return;
      if(e.key==='Escape'&&!popup.hidden){e.preventDefault();e.stopPropagation();close();toggle.focus();}
      if(['ArrowDown','ArrowUp'].includes(e.key)&&!popup.hidden){
        const focusable=[search,...list.querySelectorAll('input,button')], index=focusable.indexOf(document.activeElement);
        e.preventDefault();e.stopPropagation();focusable[(index+(e.key==='ArrowDown'?1:-1)+focusable.length)%focusable.length]?.focus();
      }
      if(e.key==='Enter'&&e.target===search){e.preventDefault();e.stopPropagation();}
    });
    host.addEventListener('focusout',()=>setTimeout(()=>{if(!host.contains(document.activeElement))close();},0));
    paint();
    return {value:()=>[...values]};
  }

  function dialog(title, body){
    const d=document.createElement('dialog');d.className='nb-dialog';d.setAttribute('aria-label',title);
    d.innerHTML=`<div class="nb-dialog-head"><h3>${esc(title)}</h3><button type="button" class="btn ghost" data-close aria-label="关闭">×</button></div>${body}<p class="nb-error" role="alert"></p>`;
    document.body.appendChild(d);d.querySelector('[data-close]').onclick=()=>d.close();d.addEventListener('close',()=>d.remove());d.showModal();return d;
  }
  function error(d,e){d.querySelector('.nb-error').textContent=e.message || String(e);}
  async function catalogChange(body){
    const result=await api('/api/project/note-categories',{project_id:project,expected_hash:board.catalog.content_hash,...body});
    board.catalog=result;return result;
  }
  async function manage(){
    if(mdDashReadOnly || !await pnSaveCurrent())return;
    const d=dialog('管理分类',`<p class="hint">可修改默认分类，也可创建一级、二级分类。修改分类不会搬动笔记文件。</p>
      <div class="nb-actions"><button type="button" class="btn" data-add>＋ 新建分类</button><button type="button" class="btn ghost" data-default>设为新项目默认</button><button type="button" class="btn ghost" data-export>导出分类方案</button></div><div class="nb-category-list"></div>`);
    function paint(){
      d.querySelector('.nb-category-list').innerHTML=ordered().map(c=>`<div class="nb-category-row"><span>${esc(label(c))}</span><button type="button" class="btn ghost" data-up="${esc(c.id)}" aria-label="上移 ${esc(c.name)}">↑</button><button type="button" class="btn ghost" data-edit="${esc(c.id)}">编辑</button><button type="button" class="btn ghost" data-delete="${esc(c.id)}">删除</button></div>`).join('');
      d.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>editCategory(node(b.dataset.edit),paint));
      d.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>deleteCategory(node(b.dataset.delete),paint));
      d.querySelectorAll('[data-up]').forEach(b=>b.onclick=async()=>{try{await catalogChange({action:'reorder',id:b.dataset.up,direction:'up'});paint();await mdDashLoad();}catch(e){error(d,e);}});
    }
    d.querySelector('[data-add]').onclick=()=>editCategory(null,paint);
    d.querySelector('[data-export]').onclick=()=>{const {content_hash,...catalog}=board.catalog,url=URL.createObjectURL(new Blob([JSON.stringify(catalog,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='分类配置.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    d.querySelector('[data-default]').onclick=async()=>{try{await catalogChange({action:'set_default'});d.querySelector('.nb-error').textContent='已保存。仅用于以后创建的项目。';}catch(e){error(d,e);}};
    paint();
  }
  function editCategory(c, done){
    const d=dialog(c?'编辑分类':'新建分类',`<form class="nb-form"><label>分类名称<input class="control" name="name" required maxlength="80" value="${esc(c?.name||'')}"></label>
      <label>上级分类<select class="control" name="parent"><option value="">无（一级分类）</option>${ordered().filter(x=>!x.parent_id&&x.id!==c?.id).map(x=>`<option value="${esc(x.id)}" ${c?.parent_id===x.id?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label>
      <label>说明<textarea class="control" name="description" rows="2">${esc(c?.description||'')}</textarea></label><button type="submit" class="btn">保存分类</button></form>`);
    d.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget;
      try{await catalogChange({id:c?.id||'',name:form.elements.name.value,parent_id:form.elements.parent.value,description:form.elements.description.value});d.close();done();await mdDashLoad();}catch(err){error(d,err);}};
  }
  function deleteCategory(c, done){
    const excluded=children(c.id), count=board.items.filter(n=>excluded.has(n.category_id)).length;
    const d=dialog('删除分类',`<p>删除「${esc(c.name)}」及其子分类，共涉及 ${count} 篇笔记。笔记文件会保留。</p>
      <form class="nb-form"><label>将笔记移到<select class="control" name="destination">${options('',excluded)}</select></label><button type="submit" class="btn">迁移并删除分类</button></form>`);
    d.querySelector('form').onsubmit=async e=>{e.preventDefault();try{
      await catalogChange({action:'delete',id:c.id,destination_id:e.currentTarget.elements.destination.value});if(excluded.has(selected))selected='all';d.close();done();await mdDashLoad();
    }catch(err){error(d,err);}};
  }

  function renderTreeNode(n, depth = 0){
    if(!n) return "";
    const isDir = n.type === "dir";
    const isRoot = !n.path || n.path === "" || n.path === "." || n.path === "笔记";
    const q = treeQuery.trim().toLowerCase();

    // 过滤判断
    if(q){
      const matchSelf = (n.name||"").toLowerCase().includes(q) || (n.title||"").toLowerCase().includes(q);
      const matchDesc = isDir && (n.children||[]).some(c => hasMatch(c, q));
      if(!matchSelf && !matchDesc && !isRoot) return "";
    }

    let html = "";
    if(!isRoot){
      const isOpen = q ? true : treeExpanded.has(n.path);
      const isSel = isDir ? (mdDashSelectedFolder === n.path) : ((mdProj && mdProj.path === n.path) || mdDashPath === n.path);
      const padLeft = 8 + depth * 14;

      if(isDir){
        html += `
          <div class="fl-tree-row dir ${isOpen ? 'open' : ''} ${isSel ? 'on' : ''}" data-tree-dir="${esc(n.path)}" data-tree-path="${esc(n.path)}" data-tree-type="dir" style="padding-left:${padLeft}px" title="${esc(n.name)}">
            <span class="fl-chevron">${isOpen ? '▾' : '▸'}</span>
            <span class="fl-folder-ico">${isOpen ? '📂' : '📁'}</span>
            <span class="fl-name">${esc(n.name)}</span>
            <span class="fl-count">${n.count || (n.children ? n.children.length : 0)}</span>
          </div>`;
      } else {
        html += `
          <div class="fl-tree-row file ${isSel ? 'on' : ''}" data-tree-file="${esc(n.path)}" data-tree-path="${esc(n.path)}" data-tree-type="file" style="padding-left:${padLeft}px" title="${esc(n.title || n.name)}">
            <span class="fl-indent-spacer"></span>
            <span class="fl-file-ico">📄</span>
            <span class="fl-name">${esc(n.title || n.name)}</span>
            <span class="fl-ext-badge">${esc((n.ext || 'md').toUpperCase())}</span>
          </div>`;
      }

      if(isDir && isOpen && n.children && n.children.length){
        html += `<div class="fl-tree-children">`;
        for(const child of n.children){
          html += renderTreeNode(child, depth + 1);
        }
        html += `</div>`;
      }
    } else {
      if(n.children && n.children.length){
        for(const child of n.children){
          html += renderTreeNode(child, 0);
        }
      }
    }
    return html;
  }

  function hasMatch(n, q){
    if((n.name||"").toLowerCase().includes(q) || (n.title||"").toLowerCase().includes(q)) return true;
    if(n.children) return n.children.some(c => hasMatch(c, q));
    return false;
  }

  function sidebar(){
    if(treeCollapsed){
      return `<aside class="nb-sidebar collapsed">
        <button type="button" class="nb-side-expand-btn" id="nbTreeExpand" title="展开项目笔记与文件目录 (留出更多工作空间)">
          <span class="nb-expand-arrow">▶</span>
          <span class="nb-expand-ico">📁</span>
          <span class="nb-expand-txt">目录</span>
        </button>
      </aside>`;
    }

    const counts = id => board.items.filter(n => children(id).has(n.category_id)).length;
    const button = (id, name, count, sub=false) => `<button type="button" class="nb-category ${selected===id?'active':''} ${sub?'nb-sub':''}" data-category="${esc(id)}" aria-current="${selected===id?'page':'false'}"><span>${esc(name)}</span><small>${count}</small></button>`;

    const rootTree = board?.tree || board?.project_tree || { type: "dir", name: "项目", path: "", children: [] };
    const projectPathDisplay = board?.project_rel || (board?.project_id ? `projects/${board.project_id.slice(0, 12)}…` : "projects/…");

    return `<aside class="nb-sidebar">
      <div class="fl-h nb-tree-h">
        <b title="项目笔记目录">笔记目录</b>
        <span class="fl-tag" title="笔记根目录">笔记/</span>
        <span class="fl-sp"></span>
        <button type="button" class="fl-ib" id="nbTreeReload" title="刷新笔记目录">↻</button>
        <button type="button" class="fl-ib" id="nbTreeNewFile" title="新建笔记 / 文件" ${mdDashReadOnly?'disabled':''}>📄+</button>
        <button type="button" class="fl-ib" id="nbTreeNewFolder" title="新建文件夹" ${mdDashReadOnly?'disabled':''}>📁+</button>
        <button type="button" class="fl-ib" id="nbTreeCollapse" title="折叠左侧栏，让工作区占据最大面积">◀</button>
      </div>

      <div class="nb-nav-tabs">
        <button type="button" class="nb-nav-tab ${viewMode==='tree'?'active':''}" data-nb-view="tree">📁 目录层级</button>
        <button type="button" class="nb-nav-tab ${viewMode==='category'?'active':''}" data-nb-view="category">🏷 分类标签</button>
      </div>

      ${viewMode === 'tree' ? `
        <div class="nb-tree-filter-wrap">
          <input type="search" class="control nb-tree-filter" id="nbTreeSearch" placeholder="🔍 筛选笔记…" value="${esc(treeQuery)}">
        </div>
        <div class="fl-list nb-tree-scroll" id="nbTreeContainer">
          <div class="fl-tree-box">
            ${renderTreeNode(rootTree, 0) || '<div class="fl-empty">暂无笔记</div>'}
          </div>
        </div>
      ` : `
        <nav class="nb-category-tree" aria-label="笔记分类">
          ${button('all','全部笔记',board.items.length)}
          ${ordered().filter(c=>!c.parent_id).map(c=>button(c.id,c.name,counts(c.id))+ordered().filter(x=>x.parent_id===c.id).map(x=>button(x.id,x.name,counts(x.id),true)).join('')).join('')}
          ${button('unfiled','待整理',board.items.filter(n=>!n.category_id).length)}
        </nav>
      `}

      <div class="nb-side-bottom">
        <div style="display:flex;gap:6px;width:100%">
          <button type="button" class="btn ghost btn-sm" data-manage style="flex:1" ${mdDashReadOnly?'disabled':''}>🏷 管理分类</button>
          <button type="button" class="btn ghost btn-sm" data-import style="flex:1" ${mdDashReadOnly?'disabled':''}>📥 导入.md</button>
        </div>
      </div>
    </aside>`;
  }

  async function selectCategory(id){
    if(!await pnSaveCurrent())return;
    selected=id;selection.clear();mdProj=null;mdDraft=false;mdDashPath='';mdDashSelectedFolder=current()?.folder||'笔记';await mdDashLoad();
  }

  async function open(path){
    if(!await pnSaveCurrent())return;
    if(path){
      const parts = path.split("/");
      let cur = "";
      for(let i = 0; i < parts.length - 1; i++){
        cur = cur ? (cur + "/" + parts[i]) : parts[i];
        treeExpanded.add(cur);
      }
      saveTreeExpanded();
    }
    mdProj=null;mdDraft=false;mdDashPath=path;await mdDashLoad();
  }

  async function render(){
    const host=$('mdDashboardBody');if(!host||!mdDashProject)return;
    const generation=++mdDashLoadId, pid=mdDashProject;
    if(project!==pid){project=pid;selected='all';filters=[];selection.clear();mdProj=null;mdDraft=false;mdDashPath='';}
    try{
      const data=await api(`/api/project/notes?project_id=${encodeURIComponent(pid)}&query=${encodeURIComponent(mdDashQuery)}`);
      if(generation!==mdDashLoadId||pid!==mdDashProject||!host.isConnected)return;
      board=data;mdDashReadOnly=!!data.read_only;mdDashTree=data.tree;
      if(!['all','unfiled'].includes(selected)&&!node(selected))selected='all';
      if(!mdProj)mdDashSelectedFolder=current()?.folder||'笔记';
      let returnButton=document.querySelector('[data-notebook-return]');if(returnButton)returnButton.remove();
      mdMountEditor(document.querySelector('#mdView .md-note-main'));

      if(mdDashPath||mdDraft){
        if(!mdProj||mdProj.pid!==pid||(!mdDraft&&mdProj.path!==mdDashPath)){
          const file=await api(`/api/project/note?project_id=${encodeURIComponent(pid)}&path=${encodeURIComponent(mdDashPath)}`);
          if(generation!==mdDashLoadId||pid!==mdDashProject)return;
          mdProj={pid,path:mdDashPath,folder:mdDashPath.split('/').slice(0,-1).join('/'),title:file.title,content:file.body,
            saved_content:file.content,content_hash:file.content_hash,read_only:file.read_only,metadata:{category_id:file.category_id,tags:file.tags,note_type:file.note_type,
              ...Object.fromEntries(Object.entries(file.metadata||{}).filter(([k])=>['variables','template_id','template_status','description'].includes(k)))}};
        }

        host.innerHTML=`<div class="nb-layout ${treeCollapsed?'side-collapsed':''}">${sidebar()}<section class="nb-main nb-editor-main">
          <div class="nb-toolbar">
            <button class="btn ghost btn-sm" id="mdDashBackFiles" title="返回笔记列表">← 返回列表</button>
            <span class="hint nb-path" title="${esc(mdProj.path||'新笔记')}">${esc(mdProj.path||'新笔记')}</span>

            <div class="seg-tabs md-mode-tabs" role="group" aria-label="编辑器视图切换">
              <button type="button" class="seg-tab ${editorMode==='split'?'active':''}" data-emode="split" title="分栏对照模式">◫ 分栏</button>
              <button type="button" class="seg-tab ${editorMode==='write'?'active':''}" data-emode="write" title="纯编辑模式（折叠右栏预览，最大化书写区）">📝 纯编辑</button>
              <button type="button" class="seg-tab ${editorMode==='preview'?'active':''}" data-emode="preview" title="纯预览模式（折叠左栏编辑）">👁 纯预览</button>
            </div>

            <button type="button" class="btn ghost btn-sm" id="btnToggleProp" title="折叠/展开笔记分类与标签属性">${propCollapsed?'属性 ▸':'属性 ▾'}</button>

            <span class="nb-spacer"></span>
            <button class="btn btn-sm" id="mdDashSave" ${mdDashReadOnly?'disabled':''}>保存</button>
            <button class="btn ghost btn-sm" id="btnMdExportTop">导出 MD</button>
            <button class="btn ghost btn-sm" id="btnMdPdfTop">导出 PDF</button>
          </div>
          <div id="pnEditorSlot"></div>
        </section></div>`;

        mdMountEditor($('pnEditorSlot'));
        mdPaintProj();
        mountMetadata();
        applyEditorMode(editorMode);
        applyPropCollapse(propCollapsed);

        $('mdDashBackFiles').onclick=pnBackToDash;
        $('mdDashSave').onclick=()=>mdSaveProj();
        $('btnMdExportTop').onclick=()=>mdExport('md');
        $('btnMdPdfTop').onclick=()=>mdExport('pdf');
        $('btnToggleProp').onclick=()=>{
          propCollapsed=!propCollapsed;
          localStorage.setItem('rw-md-prop-collapsed', propCollapsed);
          applyPropCollapse(propCollapsed);
          $('btnToggleProp').textContent = propCollapsed ? '属性 ▸' : '属性 ▾';
        };

        host.querySelectorAll('[data-emode]').forEach(b=>{
          b.onclick=()=>{
            editorMode = b.dataset.emode;
            localStorage.setItem('rw-md-mode', editorMode);
            host.querySelectorAll('[data-emode]').forEach(x=>x.classList.toggle('active', x===b));
            applyEditorMode(editorMode);
          };
        });

      } else {
        const visible=board.items.filter(n=>(selected==='all'||(selected==='unfiled'?!n.category_id:children(selected).has(n.category_id)))&&n.matches_query!==false);
        host.innerHTML=`<div class="nb-layout ${treeCollapsed?'side-collapsed':''}">${sidebar()}<section class="nb-main">
          <div class="nb-toolbar">
            <h3>${esc(current()?label(current()):selected==='unfiled'?'待整理':'全部笔记')}</h3>
            <span class="hint">${visible.length} 篇</span>
            <span class="nb-spacer"></span>
            <button class="btn ghost btn-sm" data-import ${mdDashReadOnly?'disabled':''}>导入 Markdown</button>
            <button class="btn ghost btn-sm" data-new-template ${mdDashReadOnly?'disabled':''}>新建模板</button>
          </div>
          <div class="nb-filter-row">
            <div data-filter-tags></div>
            <button class="btn ghost btn-sm" data-batch ${mdDashReadOnly?'disabled':''}>批量整理（${selection.size}）</button>
            <button class="btn ghost btn-sm" data-manage-tags ${mdDashReadOnly?'disabled':''}>管理标签</button>
          </div>
          <div class="nb-notes">
            ${visible.map(n=>`<div class="nb-note-row"><input type="checkbox" data-select-note="${esc(n.path)}" aria-label="选择 ${esc(n.title)}" ${selection.has(n.path)?'checked':''} ${mdDashReadOnly?'disabled':''}>
              <button class="nb-note-open" data-pn-file="${esc(n.path)}">
                <strong>${esc(n.title)}</strong>
                <span class="nb-note-meta">${esc(label(node(n.category_id)))}${n.note_type==='prompt_template'?' · 提示词模板':''}</span>
                <span class="nb-note-excerpt">${esc(n.excerpt||n.metadata_error||'')}</span>
              </button>
              <div class="nb-row-tags">${n.tags.slice(0,4).map(t=>`<span class="nb-tag">${esc(t)}</span>`).join('')}${n.tags.length>4?`<span class="hint">+${n.tags.length-4}</span>`:''}</div>
            </div>`).join('')||'<div class="nb-empty">这里还没有符合条件的笔记。可新建笔记或调整筛选条件。</div>'}
          </div>
        </section></div>`;

        function applyFilters(){
          let count=0;
          host.querySelectorAll('.nb-note-row').forEach(row=>{
            const path=row.querySelector('[data-pn-file]').dataset.pnFile,item=visible.find(n=>n.path===path),shown=filters.every(t=>item.tags.includes(t));
            row.style.display=shown?'':'none';if(shown)count++;
          });
          host.querySelector('.nb-toolbar .hint').textContent=`${count} 篇`;
        }
        tagPicker(host.querySelector('[data-filter-tags]'),filters,suggestionTags(),v=>{filters=v;applyFilters();},{allowCreate:false,name:'筛选标签'});
        applyFilters();

        host.querySelectorAll('[data-pn-file]').forEach(b=>b.onclick=()=>open(b.dataset.pnFile));
        host.querySelectorAll('[data-select-note]').forEach(el=>el.onchange=()=>{
          el.checked?selection.add(el.dataset.selectNote):selection.delete(el.dataset.selectNote);
          host.querySelector('[data-batch]').textContent=`批量整理（${selection.size}）`;
        });
        host.querySelector('[data-import]').onclick=()=>{$('mdFileInput').click();};
        host.querySelector('[data-new-template]').onclick=()=>newTemplate();
        const bindingButton=document.createElement('button');
        bindingButton.type='button';
        bindingButton.className='btn ghost btn-sm';
        bindingButton.textContent='模板绑定';
        bindingButton.disabled=mdDashReadOnly;
        bindingButton.onclick=()=>PromptUI.manageBindings(project);
        host.querySelector('.nb-toolbar').appendChild(bindingButton);
        host.querySelector('[data-batch]').onclick=batchDialog;
        host.querySelector('[data-manage-tags]').onclick=manageTags;
      }

      bindSidebarEvents(host);

      if(mdDashReadOnly){
        const notice=document.createElement('p');notice.className='hint';notice.textContent='只读项目：可阅读和导出笔记。';host.prepend(notice);
      }
    }catch(e){
      if(generation===mdDashLoadId)host.innerHTML=`<div class="msg error">${esc(e.message)}</div>`;
    }
  }

  function applyEditorMode(mode){
    const ws = $('mdWorkspace');
    if(!ws) return;
    ws.className = 'md-workspace ' + mode;
  }

  function applyPropCollapse(collapsed){
    const bar = document.querySelector('.nb-note-properties');
    if(!bar) return;
    bar.style.display = collapsed ? 'none' : '';
  }

  function bindSidebarEvents(host){
    // 侧栏折叠/展开
    const btnCollapse = host.querySelector('#nbTreeCollapse');
    const btnExpand = host.querySelector('#nbTreeExpand');
    if(btnCollapse) btnCollapse.onclick = () => {
      treeCollapsed = true;
      localStorage.setItem('rw-nb-tree-collapsed', 'true');
      render();
    };
    if(btnExpand) btnExpand.onclick = () => {
      treeCollapsed = false;
      localStorage.setItem('rw-nb-tree-collapsed', 'false');
      render();
    };

    // 目录树 / 分类 切换
    host.querySelectorAll('[data-nb-view]').forEach(btn => {
      btn.onclick = () => {
        viewMode = btn.dataset.nbView;
        localStorage.setItem('rw-nb-view', viewMode);
        render();
      };
    });

    // 目录树搜索
    const search = host.querySelector('#nbTreeSearch');
    if(search){
      search.oninput = (e) => {
        treeQuery = e.target.value;
        const container = host.querySelector('#nbTreeContainer');
        if(container){
          const rootTree = board?.tree || board?.project_tree || { type: "dir", name: "项目", path: "", children: [] };
          container.innerHTML = `<div class="fl-tree-box">${renderTreeNode(rootTree, 0) || '<div class="fl-empty">暂无匹配文件</div>'}</div>`;
          bindTreeItemClicks(container);
        }
      };
    }

    // 目录刷新
    const reload = host.querySelector('#nbTreeReload');
    if(reload) reload.onclick = () => mdDashLoad();

    // 目录新建笔记 / 文件 (统一头部按钮)
    const btnNew = host.querySelector('#nbTreeNewFile') || host.querySelector('#pnBtnSideNewFile');
    if(btnNew) btnNew.onclick = () => pnNewNote(mdDashSelectedFolder || '笔记');

    // 新建文件夹 (统一头部按钮)
    const btnFolder = host.querySelector('#nbTreeNewFolder');
    if(btnFolder) btnFolder.onclick = () => promptNewFolder(mdDashSelectedFolder || '笔记');

    // 管理分类
    const btnManage = host.querySelector('[data-manage]');
    if(btnManage) btnManage.onclick = manage;

    // 导入
    const btnImport = host.querySelector('[data-import]');
    if(btnImport) btnImport.onclick = () => $('mdFileInput')?.click();

    // 分类按钮点击
    host.querySelectorAll('[data-category]').forEach(b => {
      b.onclick = () => selectCategory(b.dataset.category);
    });

    // 目录树行点击及空白区右键
    const treeBox = host.querySelector('#nbTreeContainer');
    if(treeBox){
      bindTreeItemClicks(treeBox);
      treeBox.oncontextmenu = (e) => {
        if(e.target.closest('.fl-tree-row')) return;
        e.preventDefault();
        e.stopPropagation();
        if(window.AppInteractions?.openBlankTreeMenu){
          window.AppInteractions.openBlankTreeMenu(e.clientX, e.clientY, mdDashSelectedFolder || '笔记');
        }
      };
    }
  }

  function bindTreeItemClicks(container){
    // 目录展开/收起
    container.querySelectorAll('[data-tree-dir]').forEach(row => {
      row.onclick = (e) => {
        if(e.target.closest('.context-trigger, [data-context-trigger]')) return;
        const path = row.dataset.treeDir;
        if(treeExpanded.has(path)){
          treeExpanded.delete(path);
        } else {
          treeExpanded.add(path);
        }
        saveTreeExpanded();
        mdDashSelectedFolder = path;
        const rootTree = board?.tree || board?.project_tree || { type: "dir", name: "项目", path: "", children: [] };
        container.innerHTML = `<div class="fl-tree-box">${renderTreeNode(rootTree, 0) || '<div class="fl-empty">暂无文件</div>'}</div>`;
        bindTreeItemClicks(container);
      };
    });

    // 文件点击打开
    container.querySelectorAll('[data-tree-file]').forEach(row => {
      row.onclick = (e) => {
        if(e.target.closest('.context-trigger, [data-context-trigger]')) return;
        const path = row.dataset.treeFile;
        open(path);
      };
    });
  }

  function promptNewFolder(parentPath = ""){
    let base = parentPath || mdDashSelectedFolder || "笔记";
    if(!base.startsWith("笔记")) base = "笔记";
    const d = dialog("新建文件夹", `<form class="nb-form">
      <label>文件夹名称<input class="control" name="folderName" required placeholder="如：实验数据记录"></label>
      <label>父级目录<input class="control" name="parentPath" value="${esc(base)}" readonly style="opacity:0.75"></label>
      <button type="submit" class="btn">创建文件夹</button>
    </form>`);
    d.querySelector('form').onsubmit = async (e) => {
      e.preventDefault();
      const name = e.currentTarget.elements.folderName.value.trim();
      if(!name) return;
      const targetRel = base ? `${base}/${name}` : name;
      try {
        await api('/api/project/folder/create', { project_id: project, relative: targetRel });
        d.close();
        treeExpanded.add(targetRel);
        saveTreeExpanded();
        await mdDashLoad();
      } catch(err) {
        // 如果后端没有此单独接口，可走新建笔记触发生效
        try {
          await api('/api/project/note/save', { project_id: project, path: `${targetRel}/README.md`, title: name, content: `# ${name}\n\n目录说明。`, create: true });
          d.close();
          treeExpanded.add(targetRel);
          saveTreeExpanded();
          await mdDashLoad();
        } catch(e2) {
          error(d, err);
        }
      }
    };
  }

  function showTreeMenu(anchor, type, path){
    const existing = document.getElementById('nbContextMenu');
    if(existing) existing.remove();

    const menu = document.createElement('div');
    menu.id = 'nbContextMenu';
    menu.className = 'nb-context-popover';

    if(type === 'dir'){
      menu.innerHTML = `
        <button type="button" class="nb-menu-item" data-act="new-note">＋ 在此新建笔记</button>
        <button type="button" class="nb-menu-item" data-act="new-sub">📁+ 新建子文件夹</button>
        <button type="button" class="nb-menu-item del" data-act="del-dir">🗑 删除此文件夹</button>
      `;
    } else {
      menu.innerHTML = `
        <button type="button" class="nb-menu-item" data-act="open-note">打开查看/编辑</button>
        <button type="button" class="nb-menu-item del" data-act="del-note">🗑 删除笔记</button>
      `;
    }

    const rect = anchor.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.left = `${Math.min(rect.left, window.innerWidth - 180)}px`;
    document.body.appendChild(menu);
    if(mdDashReadOnly) menu.querySelectorAll('[data-act]').forEach(button=>{
      if(button.dataset.act!=="open-note") button.disabled=true;
    });

    const closeMenu = (e) => {
      if(!menu.contains(e.target)) {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      }
    };
    setTimeout(() => document.addEventListener('click', closeMenu), 10);

    menu.querySelectorAll('[data-act]').forEach(btn => {
      btn.onclick = async () => {
        const act = btn.dataset.act;
        if(mdDashReadOnly && act!=="open-note") return;
        menu.remove();
        if(act === 'new-note') pnNewNote(path);
        else if(act === 'new-sub') promptNewFolder(path);
        else if(act === 'open-note') open(path);
        else if(act === 'del-dir'){
          if(!await uiConfirm(`确认删除文件夹「${path}」及其中所有内容？`)) return;
          try {
            await api('/api/project/note/delete', { project_id: project, path });
            await mdDashLoad();
          } catch(err){ uiNotify(err.message); }
        } else if(act === 'del-note'){
          if(!await uiConfirm(`确认删除笔记「${path}」？`)) return;
          try {
            await api('/api/project/note/delete', { project_id: project, path });
            if(mdDashPath === path) mdDashPath = '';
            await mdDashLoad();
          } catch(err){ uiNotify(err.message); }
        }
      };
    });
  }

  function metadataForNew(folder){
    const c=current() || board?.catalog?.categories?.find(c=>c.folder===folder);
    let parent=c;
    if(c?.parent_id)parent=node(c.parent_id);
    return {category_id:c?.id||'',tags:[],note_type:parent?.preset_key==='prompt_templates'?'prompt_template':'note'};
  }

  async function newTemplate(){
    const c=board?.catalog?.categories?.find(c=>c.preset_key==='prompt_templates');selected=c?.id||'all';mdDashSelectedFolder=c?.folder||'笔记';
    await pnNewNote(mdDashSelectedFolder);if(mdProj){mdProj.metadata.note_type='prompt_template';mountMetadata();}
  }

  function mountMetadata(){
    if(!mdProj)return;
    const shell=$('mdEditorShell');let bar=shell?.querySelector('.nb-note-properties');
    if(!bar && shell){bar=document.createElement('div');bar.className='nb-note-properties';shell.querySelector('.md-title-row')?.after(bar);}
    if(!bar) return;
    const note=mdProj, meta=note.metadata ||= metadataForNew(note.folder);
    bar.innerHTML=`<label class="nb-category-field">归属分类<select class="control" aria-label="归属分类" ${note.read_only?'disabled':''}>${options(meta.category_id)}</select></label><div data-note-tags></div><div class="nb-template-actions"></div>`;
    bar.querySelector('select').onchange=e=>{meta.category_id=e.target.value;if(!note.path)note.folder=node(meta.category_id)?.folder||'笔记';mdScheduleSave();};
    tagPicker(bar.querySelector('[data-note-tags]'),meta.tags||[],suggestionTags(),v=>{meta.tags=v;mdScheduleSave();},{disabled:note.read_only});
    if(window.PromptUI)PromptUI.noteActions(bar.querySelector('.nb-template-actions'),note);
  }

  function batchDialog(){
    if(!selection.size)return uiNotify('请先勾选需要整理的笔记。');
    const d=dialog('批量整理',`<p>已选择 ${selection.size} 篇笔记</p><form class="nb-form"><label>归属分类<select name="category"><option value="__keep">保持原分类</option>${options()}</select></label><div data-add-tags></div><div data-remove-tags></div><button type="submit" class="btn">应用到所选笔记</button></form>`);
    const add=tagPicker(d.querySelector('[data-add-tags]'),[],suggestionTags(),()=>{},{name:'添加标签'});
    const remove=tagPicker(d.querySelector('[data-remove-tags]'),[],board.tags,()=>{},{name:'移除标签',allowCreate:false});
    d.querySelector('form').onsubmit=async e=>{e.preventDefault();try{const category=e.currentTarget.elements.category.value;
      await api('/api/project/note/batch',{project_id:project,paths:[...selection],add_tags:add.value(),remove_tags:remove.value(),...(category==='__keep'?{}:{category_id:category})});selection.clear();d.close();await mdDashLoad();
    }catch(err){error(d,err);}};
  }

  function manageTags(){
    const d=dialog('管理标签',`<form class="nb-form"><label>现有标签<select name="old">${board.tags.map(t=>`<option>${esc(t)}</option>`).join('')}</select></label><label>新名称<input class="control" name="name" maxlength="60" placeholder="填写新名称；删除请点击移除标签"></label><div class="nb-actions"><button type="submit" class="btn">重命名标签</button><button type="button" class="btn ghost" data-remove>移除标签</button></div></form>`);
    async function apply(remove){
      const form=d.querySelector('form'), old=form.elements.old.value, name=form.elements.name.value.trim();
      if(!old||(!remove&&!name))return error(d,new Error('请选择标签并填写名称'));
      const paths=board.items.filter(n=>n.tags.includes(old)).map(n=>n.path);
      try{await api('/api/project/note/batch',{project_id:project,paths,...(remove?{remove_tags:[old]}:{rename_tags:{[old]:name}})});d.close();await mdDashLoad();}catch(e){error(d,e);}
    }
    d.querySelector('form').onsubmit=e=>{e.preventDefault();apply(false);};d.querySelector('[data-remove]').onclick=()=>apply(true);
  }

  window.promptNewFolder = promptNewFolder;
  const apiObject={render,tagPicker,dialog,error,manage,mountMetadata,metadataForNew,newTemplate,current,options,open,promptNewFolder,get board(){return board;},fileMode:false};
  return apiObject;
})();
