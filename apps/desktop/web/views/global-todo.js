/* One persistent capture field, backed by the same tasks as the progress board. */
const GlobalTodos = (() => {
  const host = document.getElementById('globalTodo');
  let todos = [], busy = false, request = 0, loaded = false, projectSignature = '';
  const labels = {TODO:'待办', IN_PROGRESS:'进行中', REVIEW:'待审核', DONE:'已完成'};
  host.className = 'global-todo';
  host.innerHTML = `<div class="todo-capture">
    <button type="button" id="todoToggle" class="todo-toggle" aria-expanded="false" aria-controls="todoPanel">☑ To do <span id="todoCount">…</span></button>
    <form id="todoCaptureForm"><input id="todoCaptureInput" aria-label="随手记一条待办" placeholder="有什么想做的？记下来，Enter 保存" maxlength="500" autocomplete="off">
      <select id="todoCaptureProject" aria-label="待办归属"><option value="">个人待办</option></select>
      <button type="submit" id="todoCaptureAdd" class="btn">添加</button></form>
    <span id="todoCaptureStatus" role="status" aria-live="polite"></span>
  </div>
  <section id="todoPanel" class="todo-panel-popover" aria-label="待办清单" hidden>
    <div class="todo-panel-head"><strong>待办清单</strong><button type="button" id="todoRefresh" class="btn ghost">刷新</button><button type="button" id="todoClose" class="btn ghost" aria-label="收起待办清单">✕</button></div>
    <div class="todo-list-filters"><input id="todoListSearch" type="search" aria-label="搜索待办" placeholder="搜索待办…"><select id="todoListScope" aria-label="筛选待办归属"><option value="all">全部待办</option><option value="personal">个人待办</option></select><label><input id="todoShowDone" type="checkbox">已完成</label></div>
    <div id="todoList" class="global-todo-list"></div>
  </section>`;
  const input = $('todoCaptureInput'), scope = $('todoCaptureProject'), panel = $('todoPanel');
  input.value = localStorage.getItem('rw-todo-draft') || '';
  input.oninput = () => localStorage.setItem('rw-todo-draft', input.value);
  scope.onchange = () => localStorage.setItem('rw-todo-project', scope.value);
  function syncProjects(){
    const projects = S.tasks || [], signature = JSON.stringify(projects.map(p=>[p.id,p.title]));
    if(signature === projectSignature) return;
    projectSignature = signature;
    const selected = scope.value || localStorage.getItem('rw-todo-project') || '';
    const listScope = $('todoListScope').value;
    const options = projects.map(p=>`<option value="${esc(p.id)}">${esc(p.title)}</option>`).join('');
    scope.innerHTML = '<option value="">个人待办</option>' + options;
    scope.value = projects.some(p=>p.id===selected) ? selected : '';
    $('todoListScope').innerHTML = '<option value="all">全部待办</option><option value="personal">个人待办</option>' + options;
    $('todoListScope').value = ['all','personal',...projects.map(p=>p.id)].includes(listScope) ? listScope : 'all';
    paint();
  }
  function status(message){ $('todoCaptureStatus').textContent = message; }
  async function refresh(){
    const version = ++request;
    try {
      const result = await api('/api/todos');
      if(version !== request) return;
      todos = result.todos || []; loaded = true; paint();
    } catch(error) {
      if(version !== request) return;
      status('待办读取失败：' + error.message);
      if(!loaded) $('todoList').textContent = '暂时无法读取待办，点击刷新重试。';
    }
  }
  function paint(){
    $('todoCount').textContent = loaded ? todos.filter(t=>t.status!=='DONE').length : '…';
    if(panel.hidden) return;
    const query = $('todoListSearch').value.trim().toLowerCase(), filter = $('todoListScope').value;
    const done = $('todoShowDone').checked;
    const rows = todos.filter(t=>(done ? t.status==='DONE' : t.status!=='DONE') &&
      (filter==='all' || (filter==='personal' ? !t.task_id : t.task_id===filter)) &&
      (!query || (t.title+' '+t.description).toLowerCase().includes(query)));
    const active = document.activeElement, previousRow = active?.closest('[data-global-todo-id]');
    const focused = previousRow ? {id:previousRow.dataset.globalTodoId, action:active.dataset.todoAction} : null;
    $('todoList').innerHTML = rows.map(t=>`<div class="global-todo-row ${t.status==='DONE'?'done':''}" data-global-todo-id="${esc(t.id)}">
      <input type="checkbox" data-todo-action="toggle" aria-label="${t.status==='DONE'?'重新打开':'完成'}：${esc(t.title)}" ${t.status==='DONE'?'checked':''} ${t.stage?'disabled title="状态由工作流交付更新"':''}>
      <div class="global-todo-copy"><strong>${esc(t.title)}</strong><span>${esc(S.tasks.find(p=>p.id===t.task_id)?.title || (t.task_id?'项目待办':'个人待办'))} · ${labels[t.status] || t.status}${t.due_date?' · '+esc(t.due_date):''}</span></div>
      <button type="button" class="context-trigger" data-todo-action="edit" title="编辑待办" aria-label="编辑待办">✎</button>
      ${t.task_id?'<button type="button" class="context-trigger" data-todo-action="project" title="查看项目进度" aria-label="查看项目进度">↗</button>':''}
      <button type="button" class="context-trigger" data-todo-action="delete" title="删除待办" aria-label="删除待办">×</button>
    </div>`).join('') || `<div class="todo-list-empty">${loaded?(query?'没有匹配的待办':done?'还没有已完成的待办':'暂时没有待办，随时在上方记一条。'):'正在读取待办…'}</div>`;
    if(focused){
      const row = [...$('todoList').children].find(el=>el.dataset.globalTodoId===focused.id);
      (row?.querySelector(`[data-todo-action="${focused.action}"]`) || $('todoListSearch')).focus();
    }
  }
  function close(restore=true){
    panel.hidden = true; $('todoToggle').setAttribute('aria-expanded','false');
    if(restore) $('todoToggle').focus();
  }
  function open(){
    syncProjects(); panel.hidden = false; $('todoToggle').setAttribute('aria-expanded','true');
    paint(); refresh(); $('todoListSearch').focus();
  }
  function focus(options={}){
    syncProjects(); close(false);
    if(options.projectId !== undefined){ scope.value=options.projectId; scope.onchange(); }
    if(options.title && !input.value.trim()){ input.value=options.title; input.oninput(); }
    input.focus();
  }
  async function submit(event){
    event.preventDefault();
    if(busy || !input.value.trim()) { if(!busy) input.focus(); return; }
    const title=input.value.trim(), draft=input.value, project=scope.value;
    busy=true; $('todoCaptureAdd').disabled=true; status('正在保存…');
    try {
      await api('/api/todos/add',{task_id:project,title,priority:'P2'});
      if(input.value===draft){ input.value=''; input.oninput(); }
      status('已记下'); await refresh();
    } catch(error) { status('未保存：'+error.message); }
    finally { busy=false; $('todoCaptureAdd').disabled=false; }
  }
  function openProject(todo){
    return uiNavigate(()=>{
      curTask=todo.task_id; spId=todo.task_id; spTab='plan';
      localStorage.setItem('rw-task',curTask); openTab('space');
    });
  }
  async function act(id, action){
    const todo=todos.find(t=>t.id===id); if(!todo) return;
    try {
      if(action==='toggle' && !todo.stage) await api('/api/todos/toggle',{id});
      else if(action==='edit'){
        const title=await uiPrompt('编辑待办',todo.title);
        if(title===null) return;
        if(!title.trim()) throw new Error('待办标题不能为空');
        await api('/api/todos/update',{id,title:title.trim()});
      } else if(action==='delete'){
        if(!await uiConfirm(`删除待办「${todo.title}」？`,{okText:'删除'})) return;
        await api('/api/todos/delete',{id});
      } else if(action==='project') { close(false); return openProject(todo); }
    } catch(error) { uiNotify(error.message); }
    await refresh();
  }
  function contextActions(row){
    const todo=todos.find(t=>t.id===row.dataset.globalTodoId); if(!todo) return [];
    return [{text:'编辑待办…',run:()=>act(todo.id,'edit')},
      {text:todo.status==='DONE'?'重新打开待办':'标记为已完成',disabled:!!todo.stage,run:()=>act(todo.id,'toggle')},
      ...(todo.task_id?[{text:'查看项目进度',run:()=>act(todo.id,'project')}]:[]),
      {text:'新增待办',run:()=>focus({projectId:todo.task_id})},
      {text:'删除待办…',danger:true,run:()=>act(todo.id,'delete')}];
  }
  $('todoCaptureForm').onsubmit=submit;
  input.onkeydown=event=>{ if(event.key==='Enter' && (event.isComposing || event.repeat)) event.preventDefault(); };
  $('todoToggle').onclick=()=>panel.hidden?open():close();
  $('todoClose').onclick=()=>close();
  $('todoRefresh').onclick=refresh;
  $('todoListSearch').oninput=paint; $('todoListScope').onchange=paint; $('todoShowDone').onchange=paint;
  $('todoList').onclick=event=>{
    const button=event.target.closest('[data-todo-action]'); if(!button) return;
    const row=button.closest('[data-global-todo-id]');
    if(button.dataset.todoAction==='toggle') button.disabled=true;
    act(row.dataset.globalTodoId,button.dataset.todoAction).finally(()=>{if(button.isConnected)button.disabled=false;});
  };
  document.addEventListener('pointerdown',event=>{
    if(!panel.hidden && !host.contains(event.target) && !event.target.closest('.context-menu, dialog, .modal')) close(false);
  });
  window.addEventListener('todos-changed',refresh);
  window.addEventListener('focus',refresh);
  syncProjects(); refresh();
  return {focus,open,close,refresh,syncProjects,contextActions,isOpen:()=>!panel.hidden};
})();
