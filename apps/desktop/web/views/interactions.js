/* Shared commands keep keyboard, command search and visible controls in sync. */
const AppInteractions = (() => {
  const isMac = /mac/i.test(navigator.userAgentData?.platform || navigator.userAgent);
  const labelKey = key => key.replaceAll("Mod", isMac ? "⌘" : "Ctrl");
  const visible = el => !!el?.isConnected && !!el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
  const editable = el => !!el?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]');
  const usable = selector => [...document.querySelectorAll(selector)].find(el => visible(el) && !el.disabled);
  const enabled = () => localStorage.getItem("rw-shortcuts") !== "off";
  const singleKeys = () => localStorage.getItem("rw-single-key-shortcuts") !== "off";
  const searchField = () => usable('#main #mdDashSearch, #main #spSearchInput, #main #mailSearch, #main #itQ, #main #wsInputQuery, #main #kbSearchInput, #main input[type="search"]');
  const sendButton = () => {
    const field = document.activeElement;
    if(!editable(field)) return null;
    const button = field.id==='tcSay' ? document.getElementById('tcSend') : field.closest('.composer, .at-dock, .ol-grid, .wv-composer')?.querySelector('.send-btn, #send, #pwSend, [id$="-send"]');
    if(button?.id==='wv-send' && wfActiveRun()?.status==='running') return null;
    return visible(button) && !button.disabled && !button.classList.contains("stop") ? button : null;
  };
  const commands = [
    {id:"palette", text:"搜索命令与页面", keys:["Mod+k"], scope:"全局", inInput:true, run:openCommandPalette},
    {id:"help", text:"快捷键与操作帮助", keys:["F1", "?"], scope:"全局", run:openHelp},
    {id:"quick-todo", text:"随手记录 To do", keys:["Mod+Shift+Enter"], scope:"全局", inInput:true, run:()=>GlobalTodos.focus()},
    {id:"progress-new", text:"创建研究计划", keys:["Alt+Shift+n"], scope:"当前项目（输入框外）", available:()=>!!curTask, run:()=>TaskCenter.newPlan()},
    {id:"file-location", text:"打开文件所在位置", keys:["Alt+Shift+o"], scope:"焦点所在文件或项目", available:()=>!!fileReference(document.activeElement?.closest(rowSelector)), run:()=>locationAction(document.activeElement.closest(rowSelector),true)},
    {id:"file-handoff", text:"将文件交给当前工作流…", keys:["Alt+Shift+h"], scope:"文件列表或文件编辑器", inInput:true, available:()=>!!FileHandoff.focused(), run:()=>FileHandoff.open(FileHandoff.focused())},
    {id:"search", text:"搜索当前页面", keys:["/"], scope:"有搜索框的页面", available:()=>!!searchField(), run:()=>{ searchField()?.focus(); searchField()?.select(); }},
    {id:"save-note", text:"保存当前编辑", keys:["Mod+s"], scope:"编辑器", inInput:true, available:()=>!!usable('#mdDashSave, #flSaveBtn, [id$="-edit-save"]:not(.hidden)'), run:async()=>{
      const flSave = usable('#flSaveBtn');
      if(flSave){
        flSave.click();
        return;
      }
      const docSave = usable('[id$="-edit-save"]:not(.hidden)');
      if(docSave){
        docSave.click();
        return;
      }
      if(await pnSaveCurrent()) uiNotify("笔记已保存"); else uiNotify("保存失败，请检查笔记上方的提示后重试。");
    }},
    {id:"new-note", text:"新建笔记", keys:["Mod+Alt+n"], scope:"项目笔记", available:()=>!!usable('#nbTreeNewFile, #pnBtnSideNewFile'), run:()=>pnNewNote()},
    {id:"bold", text:"加粗", keys:["Mod+b"], scope:"Markdown 编辑器", inInput:true, available:()=>document.activeElement?.id === "mdSource" && !document.activeElement.readOnly, run:()=>mdCmd("bold")},
    {id:"italic", text:"斜体", keys:["Mod+i"], scope:"Markdown 编辑器", inInput:true, available:()=>document.activeElement?.id === "mdSource" && !document.activeElement.readOnly, run:()=>mdCmd("italic")},
    {id:"send", text:"发送当前消息", keys:["Mod+Enter"], scope:"对话输入框", inInput:true, available:()=>!!sendButton(), run:()=>sendButton()?.click()},
  ];
  function matches(event, key){
    if(key === "?" || key === "/") return event.key === key && !event.ctrlKey && !event.metaKey && !event.altKey;
    const parts=key.toLowerCase().split("+"), main=parts.pop();
    return event.key.toLowerCase()===main && event.ctrlKey===(!isMac && parts.includes("mod")) &&
      event.metaKey===(isMac && parts.includes("mod")) && event.altKey===parts.includes("alt") && event.shiftKey===parts.includes("shift");
  }
  function consume(event){ event.preventDefault(); event.stopImmediatePropagation(); }
  function run(command){
    if(!command || (command.available && !command.available())) return;
    Promise.resolve().then(command.run).catch(error=>uiNotify("操作失败："+error.message));
  }
  function commandItems(){
    return commands.filter(c=>c.id!=="palette" && (!c.available || c.available())).map(c=>({
      text:c.text, sub:c.scope, cat:"常用操作", ico:"⌨", shortcut:enabled()?labelKey(c.keys[0]):"", run:()=>run(c)
    }));
  }

  function openHelp(){
    let modal=$("shortcutHelp");
    if(!modal){
      modal=document.createElement("div"); modal.id="shortcutHelp"; modal.className="modal hidden";
      modal.setAttribute("aria-label","快捷键与操作帮助");
      document.body.appendChild(modal);
    }
    modal.innerHTML=`<div class="modal-mask"></div><section class="modal-dialog shortcut-dialog">
      <div class="modal-head"><h3>快捷键与操作帮助</h3><button type="button" class="modal-close" aria-label="关闭快捷键帮助">✕</button></div>
      <div class="modal-body"><p class="hint">在按钮提示、右键菜单和命令搜索中发现操作。右键菜单也可以通过「⋯」或 Shift+F10 打开。</p>
      <div class="shortcut-preferences"><label><input type="checkbox" data-shortcuts ${enabled()?"checked":""}>启用应用快捷键</label>
      <label><input type="checkbox" data-single-keys ${singleKeys()?"checked":""}>启用单键 / 搜索、? 帮助（输入时不触发）</label></div>
      <table class="shortcut-table"><thead><tr><th>操作</th><th>快捷键</th><th>生效位置</th></tr></thead><tbody>
      ${commands.map(c=>`<tr><td>${esc(c.text)}</td><td>${c.keys.map(k=>`<kbd>${esc(labelKey(k))}</kbd>`).join(" ")}</td><td>${esc(c.scope)}</td></tr>`).join("")}
      <tr><td>切换科研步骤（前 10 步）</td><td><kbd>Alt+1 … Alt+0</kbd></td><td>科研平台（输入框外）</td></tr>
      <tr><td>打开操作菜单</td><td><kbd>Shift+F10</kbd></td><td>笔记、项目、文献、标签页</td></tr>
      <tr><td>菜单导航 / 执行 / 关闭</td><td><kbd>↑ ↓</kbd> <kbd>Enter</kbd> <kbd>Esc</kbd></td><td>当前菜单</td></tr>
      <tr><td>切换 / 关闭标签页</td><td><kbd>← →</kbd> <kbd>Enter</kbd> / <kbd>Delete</kbd></td><td>焦点在标签页上</td></tr>
      </tbody></table><p class="hint">Esc 优先关闭最上层弹窗并返回原来的位置。输入法选词时不会发送消息或触发应用快捷键。</p></div></section>`;
    modal.querySelector(".modal-close").onclick=()=>uiCloseModal(modal);
    modal.querySelector(".modal-mask").onclick=()=>uiCloseModal(modal);
    modal.querySelector("[data-shortcuts]").onchange=e=>{ localStorage.setItem("rw-shortcuts",e.target.checked?"on":"off"); decorateCommands(); };
    modal.querySelector("[data-single-keys]").onchange=e=>localStorage.setItem("rw-single-key-shortcuts",e.target.checked?"on":"off");
    uiOpenModal(modal);
  }

  let contextMenu=null, menuOwner=null, menuOrigin=null, menuAnchor=null;
  const rowSelector='[data-schedule-id], [data-ec-task], [data-history-id], [data-plan-id], [data-ec-column], [data-tree-path], [data-pn-file]:not(button), .nb-note-row, [data-card-folder], .proj-row, .zot-row-card, [data-tab-id], .fl-tree-row[data-rel], .cv-f[data-p], [data-global-todo-id]';
  function fileReference(row){
    if(!row) return null;
    if(row.matches('.proj-row')) return {project_id:row.querySelector('[data-go]')?.dataset.go,relative:''};
    if(row.matches('.zot-row-card')) return {zotero_key:row.dataset.zotKey};
    if(row.matches('.fl-tree-row')) return {project_id:row.dataset.projectId || curTask,relative:row.dataset.rel};
    if(row.matches('.cv-f')) return {path:row.dataset.p};
    const relative=row.dataset.treePath || row.dataset.pnFile || row.dataset.cardFolder || row.querySelector('[data-pn-file]')?.dataset.pnFile;
    return relative ? {project_id:mdDashProject,relative} : null;
  }
  async function copyText(value){
    try { await navigator.clipboard.writeText(value); }
    catch {
      const origin=document.activeElement, field=document.createElement('textarea');
      field.value=value; field.style.position='fixed'; field.style.opacity='0'; document.body.appendChild(field); field.select();
      const ok=document.execCommand('copy'); field.remove(); origin?.focus();
      if(!ok) throw new Error('无法写入剪贴板');
    }
    uiNotify('路径已复制');
  }
  async function locationAction(row,open){
    const reference=fileReference(row); if(!reference) return;
    const result=await api('/api/file/location',{...reference,open});
    if(open) uiNotify('已打开所在文件夹'); else await copyText(result.path);
  }
  function fileActions(row){
    return [
      {text:'打开文件所在位置',run:()=>locationAction(row,true)},
      {text:'复制完整路径',run:()=>locationAction(row,false)}
    ];
  }
  const ownTrigger=row=>[...row.querySelectorAll('[data-context-trigger]')].find(button=>button.closest(rowSelector)===row);
  function buttonAction(button, text){
    return {text:text || button.title || button.textContent.trim(), disabled:button.disabled,
      danger:button.matches('.del, [data-del]') || /删除/.test(button.title || button.textContent),
      run:()=>{ if(button.isConnected && !button.disabled) button.click(); }};
  }
  function contextActions(row){
    if(row.matches('[data-schedule-id]'))return [...row.querySelectorAll('[data-sch-run],[data-sch-toggle],[data-sch-edit],[data-sch-del]')].map(button=>({...buttonAction(button),danger:button.hasAttribute('data-sch-del')}));
    if(row.matches('[data-history-id]'))return ConversationHistory.actions(row);
    if(row.matches('[data-global-todo-id]')) return GlobalTodos.contextActions(row);
    if(row.matches('[data-ec-task], [data-plan-id], [data-ec-column]')) return TaskCenter.contextActions(row);
    if(row.matches('.cv-f')) return [
      {text:'预览文件',run:()=>row.click()},
      ...fileActions(row),
      {text:'记录相关待办',run:()=>GlobalTodos.focus({projectId:curTask,title:'处理：'+(row.querySelector('.fl-name')?.textContent || row.dataset.p?.split(/[\\/]/).pop() || row.dataset.rel)})}
    ];
    if(row.matches('[data-tab-id]')){
      const id=row.dataset.tabId, index=tabs.findIndex(t=>t.id===id);
      const closeSet=ids=>uiNavigate(()=>{
        tabs=tabs.filter(t=>!ids.includes(t.id));
        if(!tabs.some(t=>t.id===activeTab)) activeTab=id;
        render(); document.querySelector(`[data-t="${activeTab}"]`)?.focus();
      });
      return [
      {text:"关闭标签页", disabled:tabs.length<2, run:()=>uiNavigate(()=>{ closeTab(id); document.querySelector('#tabsbar [aria-selected="true"]')?.focus(); })},
        {text:"关闭其他标签页", disabled:tabs.length<2, run:()=>closeSet(tabs.filter(t=>t.id!==id).map(t=>t.id))},
        {text:"关闭右侧标签页", disabled:index===tabs.length-1, run:()=>closeSet(tabs.slice(index+1).map(t=>t.id))}
      ];
    }
    if(row.matches('.proj-row')) return [
      {text:"设为当前项目", run:()=>uiNavigate(()=>row.click())},
      ...fileActions(row),
      {text:'新增项目待办',run:()=>GlobalTodos.focus({projectId:row.querySelector('[data-go]').dataset.go})},
      ...[...row.querySelectorAll('[data-go], [data-del]')].map(b=>buttonAction(b,b.hasAttribute('data-go')?"进入科研平台":"删除项目…"))
    ];
    if(row.matches('.zot-row-card')) return [
      ...[...row.querySelectorAll('.zot-open-pdf, .zot-single-note')].map(b=>buttonAction(b)),
      ...fileActions(row)
    ];

    if(row.matches('[data-tree-path], .fl-tree-row, .nb-note-row')){
      const isFileTree=row.matches('.fl-tree-row')&&!!row.closest('#flList');
      const isDir = row.dataset.treeType === "dir" || row.dataset.dir === "1" || row.hasAttribute('data-card-folder');
      const treePath = row.dataset.treePath || row.dataset.rel || row.dataset.pnFile || row.dataset.p || "";
      const parentDir = isDir ? treePath : (treePath.split('/').slice(0, -1).join('/') || '笔记');
      const targetName = (treePath.split('/').pop() || treePath).split(/[\\/]/).pop() || "";
      const isProjectNote = row.matches('[data-tree-path], .nb-note-row') || treePath.startsWith("笔记");
      const projectId = isFileTree ? row.dataset.projectId || curTask : mdDashProject || curTask;
      const readOnly = isFileTree ? false : mdDashReadOnly;

      return [
        ...(!isDir ? [{text:"打开文件",run:()=>row.click()}, {text:"交给当前工作流…",shortcut:"Alt+Shift+H",run:()=>FileHandoff.open(fileReference(row))},
          {text:"交接记录与回执",run:()=>FileHandoff.history(fileReference(row))}, {separator:true}] : []),
        {
          icon: "📄",
          text: "新文件",
          disabled: readOnly,
          run: () => {
            if(isFileTree)return ProjectWorkspace.createEntry(projectId,isDir?treePath:treePath.split('/').slice(0,-1).join('/'),'file');
            if(window.pnNewNote) window.pnNewNote(parentDir);
          }
        },
        {
          icon: "📁",
          text: "新建文件夹",
          disabled: readOnly,
          run: () => {
            if(isFileTree)return ProjectWorkspace.createEntry(projectId,isDir?treePath:treePath.split('/').slice(0,-1).join('/'),'folder');
            if(window.promptNewFolder) window.promptNewFolder(parentDir);
            else if(window.Notebook?.promptNewFolder) window.Notebook.promptNewFolder(parentDir);
          }
        },
        { separator: true },
        {
          icon: "📋",
          text: "复制",
          run: async () => {
            if(isDir){
              await copyText(treePath);
              uiNotify("文件夹路径已复制");
            } else {
              try {
                const loc = await api('/api/file/location', { ...fileReference(row), open: false });
                const readRes = await api(`/api/conv/read?path=${encodeURIComponent(loc?.path || treePath)}`);
                if(readRes && readRes.text){
                  await copyText(readRes.text);
                  uiNotify("文件内容已复制到剪贴板");
                } else {
                  await copyText(treePath);
                  uiNotify("已复制");
                }
              } catch {
                await copyText(treePath);
                uiNotify("已复制");
              }
            }
          }
        },
        {
          icon: "📋",
          text: "复制路径",
          shortcut: "Alt+Shift+C",
          run: async () => {
            const loc = await api('/api/file/location', { ...fileReference(row), open: false });
            if(loc?.path){
              await copyText(loc.path);
              uiNotify("完整路径已复制");
            } else {
              await copyText(treePath);
              uiNotify("路径已复制");
            }
          }
        },
        {
          icon: "📋",
          text: "复制相对路径",
          shortcut: "Ctrl+Alt+Shift+C",
          run: async () => {
            await copyText(treePath);
            uiNotify("相对路径已复制");
          }
        },
        {
          icon: "💻",
          text: "复制终端切换目录命令",
          run: async () => {
            const loc = await api('/api/file/location', { ...fileReference(row), open: false });
            const absPath = loc?.path || treePath;
            const dir = isDir ? absPath : absPath.replace(/[\\/][^\\/]+$/, '');
            await copyText(`cd "${dir}"`);
            uiNotify(`已复制终端命令: cd "${dir}"`);
          }
        },
        {
          icon: "🔍",
          text: isFileTree ? "搜索同名项目资料" : "在文件夹中查找",
          shortcut: "Ctrl+Shift+F",
          run: () => {
            const s = document.getElementById('nbTreeSearch') || searchField();
            if(s){
              s.value = targetName;
              s.focus();
              s.dispatchEvent(new Event('input', { bubbles: true }));
              if(isFileTree)s.closest('form')?.requestSubmit();
            }
          }
        },
        {
          icon: "↗",
          text: "在文件资源管理器中显示",
          run: () => locationAction(row, true)
        },
        { separator: true },
        {
          icon: "✏",
          text: "重命名",
          shortcut: "Enter",
          disabled: readOnly,
          run: async () => {
            if(isFileTree&&!await ProjectWorkspace.flush())return;
            const curName = isDir || isFileTree&&!isProjectNote ? targetName : (row.dataset.title || targetName.replace(/\.md$/i, ''));
            const newName = window.prompt("输入新名称：", curName);
            if(!newName || newName.trim() === curName) return;
            const cleanNewName = newName.trim();
            if(isProjectNote){
              try {
                const renamed=await api('/api/project/note/rename', {
                  project_id: projectId,
                  path: treePath,
                  new_name: cleanNewName
                });
                uiNotify("重命名成功");
                if(isFileTree)await ProjectWorkspace.refreshFiles(projectId,treePath,renamed.new_path);else await mdDashLoad();
              } catch(e) {
                uiNotify("重命名失败：" + e.message);
              }
            } else {
              try {
                const loc = await api('/api/file/location', { ...fileReference(row), open: false });
                await api('/api/conv/rename', {
                  path: loc?.path || treePath,
                  new_name: cleanNewName
                });
                uiNotify("重命名成功");
                if(isFileTree)await ProjectWorkspace.refreshFiles(projectId,treePath,treePath.split('/').slice(0,-1).concat(cleanNewName).join('/'));else if(window.flInitTree) window.flInitTree();
              } catch(e) {
                uiNotify("重命名失败：" + e.message);
              }
            }
          }
        },
        {
          icon: "🗑",
          text: "删除",
          shortcut: "Delete",
          danger: true,
          disabled: readOnly,
          run: async () => {
            if(isFileTree&&!await ProjectWorkspace.flush())return;
            const label = isDir ? `文件夹「${targetName}」及其全部内容` : `文件「${targetName}」`;
            if(!await uiConfirm(`确定要永久删除 ${label} 吗？`, { okText: "删除", danger: true })) return;
            if(isProjectNote){
              try {
                await api('/api/project/note/delete', {
                  project_id: projectId,
                  path: treePath
                });
                uiNotify("已删除");
                if(isFileTree)await ProjectWorkspace.refreshFiles(projectId,treePath);else await mdDashLoad();
              } catch(e) {
                uiNotify("删除失败：" + e.message);
              }
            } else {
              try {
                const loc = await api('/api/file/location', { ...fileReference(row), open: false });
                await api('/api/conv/delete', {
                  path: loc?.path || treePath
                });
                uiNotify("已删除");
                if(isFileTree)await ProjectWorkspace.refreshFiles(projectId,treePath);else if(window.flInitTree) window.flInitTree();
              } catch(e) {
                uiNotify("删除失败：" + e.message);
              }
            }
          }
        }
      ];
    }

    const path=row.dataset.treePath || row.dataset.pnFile || row.dataset.cardFolder || row.querySelector('[data-pn-file]')?.dataset.pnFile;
    const actionRow=path ? [...document.querySelectorAll('[data-tree-path]')].find(item=>item.dataset.treePath===path) || row : row;
    const existing=[...actionRow.querySelectorAll('.pn-tree-act')].map(b=>buttonAction(b));
    const projectId=mdDashProject;
    return [
      {text:row.dataset.treeType==="dir" || row.hasAttribute('data-card-folder')?"打开文件夹":"打开笔记", run:()=>(row.querySelector('button[data-pn-file]') || row).click()},
      ...fileActions(row),
      {text:'记录相关待办',run:()=>GlobalTodos.focus({projectId,title:'处理：'+(path || row.dataset.treePath).split('/').pop()})},
      ...existing
    ];
  }
  function closeMenu(restore=true){
    if(!contextMenu) return;
    contextMenu.remove(); contextMenu=null;
    menuOwner?.classList.remove("context-active");
    if(menuOwner) ownTrigger(menuOwner)?.setAttribute('aria-expanded','false');
    if(restore){
      if(menuOrigin?.isConnected) menuOrigin.focus({preventScroll:true});
      else document.querySelector('#tabsbar [aria-selected="true"]')?.focus();
    }
    menuOwner=null; menuOrigin=null; menuAnchor=null;
    window.dispatchEvent(new Event('rw:context-menu-closed'));
  }
  function openPopupItems(items, x, y, origin, owner){
    closeMenu(false);
    if(!items || !items.length) return;
    menuOwner = owner || null;
    menuOrigin = origin || null;
    menuAnchor = owner ? owner.getBoundingClientRect() : { top: y, left: x, width: 0, height: 0 };
    if(owner){
      owner.classList.add("context-active");
      ownTrigger(owner)?.setAttribute('aria-expanded','true');
    }
    contextMenu=document.createElement("div"); contextMenu.className="context-menu";
    contextMenu.setAttribute("role","menu"); contextMenu.setAttribute("aria-label","操作菜单");

    const actions = [];
    contextMenu.innerHTML = items.map(item => {
      if(item.separator) return '<div role="separator" class="context-separator"></div>';
      const idx = actions.length;
      actions.push(item);
      return `<button type="button" role="menuitem" tabindex="-1" data-menu-index="${idx}" ${item.disabled ? 'disabled aria-disabled="true"' : ''} class="${item.danger ? 'danger' : ''}">
        <span class="context-item-left">
          ${item.icon ? `<span class="context-item-ico" aria-hidden="true">${item.icon}</span>` : ''}
          <span class="context-item-text">${esc(item.text)}</span>
        </span>
        ${item.shortcut ? `<kbd class="context-shortcut" aria-hidden="true">${esc(item.shortcut)}</kbd>` : ''}
      </button>`;
    }).join("");

    document.body.appendChild(contextMenu);
    contextMenu.style.left=Math.max(8,Math.min(x,innerWidth-contextMenu.offsetWidth-8))+"px";
    contextMenu.style.top=Math.max(8,Math.min(y,innerHeight-contextMenu.offsetHeight-8))+"px";
    contextMenu.querySelectorAll('button').forEach(button=>{
      button.onclick=()=>{
        const act=actions[Number(button.dataset.menuIndex)];
        closeMenu();
        if(act) run({run:act.run, available:()=>(!owner || owner.isConnected) && !act.disabled});
      };
    });
    contextMenu.querySelector('button:not(:disabled)')?.focus({preventScroll:true});
  }

  function openMenu(row, x, y, origin){
    const items=contextActions(row);
    if(!items.length) return;
    openPopupItems(items, x, y, origin, row);
  }

  function openBlankTreeMenu(x, y, folder){
    const items = [
      {
        icon: "📄",
        text: "新文件",
        run: () => { if(window.pnNewNote) window.pnNewNote(folder || "笔记"); }
      },
      {
        icon: "📁",
        text: "新建文件夹",
        run: () => { if(window.promptNewFolder) window.promptNewFolder(folder || "笔记"); }
      },
      { separator: true },
      {
        icon: "↗",
        text: "在文件资源管理器中显示",
        run: async () => {
          await api('/api/file/location', { project_id: mdDashProject, relative: folder || "笔记", open: true });
        }
      },
      {
        icon: "↻",
        text: "刷新笔记目录",
        run: () => mdDashLoad()
      }
    ];
    openPopupItems(items, x, y, null, null);
  }
  function menuKey(event){
    const buttons=[...contextMenu.querySelectorAll('button:not(:disabled)')];
    const i=buttons.indexOf(document.activeElement);
    if(event.key==='Escape'){ consume(event); closeMenu(); }
    else if(event.key==='Tab'){ closeMenu(); event.stopImmediatePropagation(); }
    else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
      consume(event);
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
      buttons[next]?.focus();
    } else if(event.key==='Enter' || event.key===' '){ consume(event); buttons[i]?.click(); }
  }
  function topModal(){
    const native=[...document.querySelectorAll('dialog[open]')].filter(visible);
    if(native.length) return native.at(-1);
    return [...document.querySelectorAll('.modal:not(.hidden), .cap-overlay')].filter(visible)
      .sort((a,b)=>Number(getComputedStyle(a).zIndex || 0)-Number(getComputedStyle(b).zIndex || 0)).at(-1);
  }
  function trapTab(event, modal){
    const fields=[...modal.querySelectorAll('button, input, textarea, select, a[href], [tabindex]')].filter(el=>visible(el) && !el.disabled && el.tabIndex>=0);
    const first=fields[0], last=fields.at(-1), current=document.activeElement;
    if(!first){ consume(event); modal.tabIndex=-1; modal.focus(); }
    else if(!modal.contains(current) || (!event.shiftKey && current===last) || (event.shiftKey && current===first)){
      consume(event); (event.shiftKey?last:first).focus();
    }
  }
  window.addEventListener('keydown', event=>{
    // Preserve the IME's native editing while keeping application handlers idle.
    if(event.isComposing || event.keyCode===229){ event.stopImmediatePropagation(); return; }
    if(event.defaultPrevented) return;
    if(contextMenu){ menuKey(event); return; }
    const modal=topModal();
    if(modal){
      if(modal.id==='cmdPaletteModal' && handleCommandPaletteKey(event)){ consume(event); return; }
      if(event.key==='Tab'){ trapTab(event,modal); return; }
      if(event.key==='Escape'){
        const close=modal.querySelector('[data-no], .modal-close, [data-close]');
        if(close){ consume(event); close.click(); }
        else if(modal.id==='cmdPaletteModal'){ consume(event); closeCommandPalette(); }
      }
      return;
    }
    if(event.key==='Escape' && GlobalTodos.isOpen()){ consume(event); GlobalTodos.close(); return; }
    const attachment=usable('#attmenu');
    if(event.key==='Escape' && attachment){ consume(event); attachment.style.display='none'; document.getElementById('plus')?.focus(); return; }
    const target=event.target, row=target.closest?.(rowSelector);
    if((event.key==='ContextMenu' || (event.shiftKey && event.key==='F10')) && row && !editable(target)){
      consume(event); const r=row.getBoundingClientRect(); openMenu(row,r.left+24,r.top+24,target); return;
    }
    if(target.matches?.('[role="tab"][data-t]')){
      const tabButtons=[...document.querySelectorAll('#tabsbar [data-t]')], index=tabButtons.indexOf(target);
      if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
        consume(event);
        const next=event.key==='Home'?0:event.key==='End'?tabButtons.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabButtons.length)%tabButtons.length;
        tabButtons.forEach((button,i)=>button.tabIndex=i===next?0:-1); tabButtons[next]?.focus(); return;
      }
      if(event.key==='Delete' && tabs.length>1){ consume(event); uiNavigate(()=>{ closeTab(target.dataset.t); document.querySelector('#tabsbar [aria-selected="true"]')?.focus(); }); return; }
    }
    if(row===target && !row.matches('[data-tab-id]') && ['Enter',' '].includes(event.key)){
      consume(event); const action=contextActions(row).find(a=>!a.separator && !a.disabled); if(action) run(action); return;
    }
    if(row && (row===target || row.contains(target)) && !editable(target)){
      if(event.key==='F2'){
        consume(event); const renameAct=contextActions(row).find(a=>a.text==='重命名' && !a.disabled); if(renameAct) run(renameAct); return;
      }
      if(event.key==='Delete'){
        consume(event); const delAct=contextActions(row).find(a=>a.danger && !a.disabled); if(delAct) run(delAct); return;
      }
      if(event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey && (event.key==='C' || event.key==='c')){
        consume(event); const cPathAct=contextActions(row).find(a=>a.text==='复制路径'); if(cPathAct) run(cPathAct); return;
      }
      if((event.ctrlKey || event.metaKey) && event.altKey && event.shiftKey && (event.key==='C' || event.key==='c')){
        consume(event); const cRelAct=contextActions(row).find(a=>a.text==='复制相对路径'); if(cRelAct) run(cRelAct); return;
      }
    }
    if(!enabled() || event.repeat) return;
    for(const command of commands){
      if(editable(target) && !command.inInput) continue;
      if(command.available && !command.available()) continue;
      if(command.keys.some(key=>(key.length!==1 || singleKeys()) && matches(event,key))){ consume(event); run(command); return; }
    }
  },true);
  document.addEventListener('contextmenu', event=>{
    if(editable(event.target)) return;
    const row=event.target.closest(rowSelector); if(!row) return;
    event.preventDefault(); openMenu(row,event.clientX,event.clientY,row.querySelector('[data-t]') || row);
  });
  document.addEventListener('pointerdown',event=>{ if(contextMenu && !contextMenu.contains(event.target)) closeMenu(false); },true);
  document.addEventListener('scroll',event=>{
    if(!contextMenu || contextMenu.contains(event.target)) return;
    const current=menuOwner?.getBoundingClientRect();
    // A pointer click may queue a focus scroll before the menu opens. Only
    // dismiss when the anchor actually moves after the menu was positioned.
    if(!current || Math.abs(current.top-menuAnchor.top)>1 || Math.abs(current.left-menuAnchor.left)>1) closeMenu(false);
  },true);
  window.addEventListener('resize',()=>closeMenu(false));
  window.addEventListener('blur',()=>closeMenu(false));
  document.addEventListener('click',event=>{
    const trigger=event.target.closest('[data-command], [data-context-trigger], #tabActions');
    if(!trigger) return;
    if(trigger.dataset.command){ run(commands.find(c=>c.id===trigger.dataset.command)); return; }
    const row=trigger.id==='tabActions'?document.querySelector(`[data-tab-id="${activeTab}"]`):trigger.closest(rowSelector);
    if(row){ event.stopPropagation(); const r=trigger.getBoundingClientRect(); openMenu(row,r.left,r.bottom+4,trigger); }
  });
  function decorateCommands(){
    document.querySelectorAll('[data-command]').forEach(button=>{
      const command=commands.find(c=>c.id===button.dataset.command); if(!command) return;
      button.title=command.text+(enabled()?`（${labelKey(command.keys[0])}）`:"");
      const badge=button.querySelector('kbd'); if(badge) badge.textContent=enabled()?labelKey(command.keys[0]):"";
    });
    const save=document.getElementById('mdDashSave');
    if(save){ save.title=enabled()?`保存笔记（${labelKey('Mod+S')}）`:"保存笔记"; save.setAttribute('aria-keyshortcuts',isMac?'Meta+s':'Control+s'); }
    const capture=document.getElementById('todoCaptureInput');
    if(capture) capture.title=enabled()?`随手记录 To do（${labelKey('Mod+Shift+Enter')}）`:'随手记录 To do';
    const progress=document.querySelector('[data-ec-new]');
    if(progress) progress.title=enabled()?'创建研究计划（Alt+Shift+N）':'创建研究计划';
  }
  function enhance(){
    if(contextMenu && !menuOwner?.isConnected) closeMenu();
    document.querySelectorAll(rowSelector).forEach(row=>{
      if(row.dataset.contextReady || row.matches('[data-tab-id]')) return;
      row.dataset.contextReady='true';
      row.tabIndex=0;
      row.setAttribute('role','group');
      row.setAttribute('aria-label',row.dataset.treePath || row.dataset.pnFile || row.dataset.cardFolder || row.dataset.rel || row.querySelector('.nm, strong, b, h3')?.textContent || '操作条目');
      row.setAttribute('aria-description','按 Enter 执行默认操作，按 Shift+F10 查看更多操作。');
      const more=document.createElement('button'); more.type='button'; more.className='context-trigger'; more.dataset.contextTrigger='true';
      more.setAttribute('aria-label','更多操作'); more.setAttribute('aria-haspopup','menu'); more.setAttribute('aria-expanded','false'); more.title='更多操作（Shift+F10）'; more.textContent='⋯';
      more.onclick=event=>{ event.stopPropagation(); const r=more.getBoundingClientRect(); openMenu(row,r.left,r.bottom+4,more); };
      row.appendChild(more);
    });
    decorateCommands();
  }
  let pending=false;
  new MutationObserver(records=>{
    if(pending || !records.some(r=>[...r.addedNodes,...r.removedNodes].some(n=>n.nodeType===1))) return;
    pending=true; queueMicrotask(()=>{pending=false; enhance();});
  }).observe(document.body,{childList:true,subtree:true});
  enhance();
  return {commandItems, openHelp, labelKey, openBlankTreeMenu};
})();
