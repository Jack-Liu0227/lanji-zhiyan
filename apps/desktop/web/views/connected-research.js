/* Sequential workflow controls and shared project bibliography refresh. */
(() => {
  "use strict";
  const h = value => esc(String(value ?? ""));
  const statuses = {draft:"已准备",prepared:"已准备",queued:"等待执行",running:"执行中",awaiting_review:"等待人工审查",delivered:"已交付",succeeded:"全部完成",failed:"失败",cancelled:"已停止",blocked:"等待修改",interrupted:"中断待恢复",rejected:"已退回"};
  let dialog = null, currentRun = null, currentProject = "", focusedRun = "", rendered = "", pollBusy = false, calendarRendered = "";
  const feedback = new Map(), versions = new Map(), goalDrafts = new Map(), modeDrafts = new Map();
  const modeOptions=mode=>`<option value="supervised" ${mode!=="full_access"?"selected":""}>人工核查模式</option><option value="full_access" ${mode==="full_access"?"selected":""}>Full access · 自动推进</option>`;
  const style = document.createElement("style");
  style.textContent = `.connected-dialog{width:min(900px,calc(100vw - 28px));max-height:88vh;overflow:auto;border:1px solid var(--line);border-radius:14px;padding:22px;background:var(--bg);color:var(--fg)}.connected-dialog::backdrop{background:#0007}.connected-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.connected-dialog textarea{width:100%;min-height:80px}.connected-node{padding:12px 0;border-bottom:1px solid var(--line)}.connected-node pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;font-size:12px}.connected-node details{margin:8px 0}.connected-status{font-size:12px;color:var(--muted);margin:10px 0}.connected-error{color:var(--err);white-space:pre-wrap}.connected-library{font-size:12px;margin:10px 0;color:var(--muted)}`;
  document.head.append(style);

  function syncSettings() { ProjectLiterature.open(curTask,true); }
  function activeProject() { return (S.tasks || []).find(t=>t.id===currentProject); }
  function signature(run) { return JSON.stringify(run && [run.id,run.status,run.error,run.current_node,run.settings,run.nodes.map(n=>[n.id,n.status,n.ended_at,n.error,n.checkpoint])]); }
  async function fetchRun() {
    const project=currentProject;
    const result=await api("/api/workflow/execution?"+(dialog?.open && focusedRun?"id="+encodeURIComponent(focusedRun):"task_id="+encodeURIComponent(project)));
    if(project!==currentProject) return;
    currentRun=result.run;
    const button=document.getElementById("wfSequenceOpen");
    if(button){
      const txt = button.querySelector(".wf-seq-text") || button;
      txt.textContent=currentRun ? "顺序执行 · "+(statuses[currentRun.status] || currentRun.status) : "顺序执行整个工作流";
    }
    if(dialog?.open && rendered!==signature(currentRun)) drawRun();
  }
  function drawRun() {
    if(!dialog) return;
    const host=dialog.querySelector("[data-sequence-body]"), run=currentRun;
    rendered=signature(run);
    const active=run && !["succeeded","cancelled","failed"].includes(run.status);
    const prepared=run && (["draft","prepared"].includes(run.status) || run.status==="queued" && run.settings?.prepared && !run.nodes.length);
    const mode=modeDrafts.get(run?.id || currentProject) || run?.settings?.policy?.autonomy_mode || run?.settings?.autonomy_mode || "supervised";
    host.innerHTML=`<p class="hint">每个节点以项目任务承接上游交付、项目记忆及人工反馈。人工核查模式保留关键节点判断；Full access 在产物、依赖和版本校验通过后自动推进，并记录自动验收来源。</p>
      ${!active?`<label class="field"><span>本次研究目标，可选</span><textarea class="control" data-sequence-goal placeholder="例如：围绕当前项目的研究问题完成文献到成稿流程">${h(goalDrafts.get(currentProject) || "")}</textarea></label><label class="field"><span>本次执行模式</span><select class="control" data-sequence-mode>${modeOptions(modeDrafts.get(currentProject) || "supervised")}</select></label><div class="connected-row"><button class="btn" data-sequence-prepare>准备工作流任务</button><button class="btn btn-primary" data-sequence-start>开始顺序执行</button></div>`:""}
      ${run?`<div class="connected-row" style="margin-top:12px"><b>${h(run.layout.name)}</b><span class="pill">${h(statuses[run.status] || run.status)}</span>
        ${["failed","blocked","interrupted"].includes(run.status)?'<button class="btn" data-sequence-resume>从已保存进度继续</button><button class="btn ghost" data-sequence-restart>核对后重新执行当前节点</button>':""}
        ${prepared?'<button class="btn btn-primary" data-sequence-start-prepared>开始已准备的工作流</button>':""}
        <button class="btn" data-sequence-chat>打开工作流对话</button><button class="btn ghost" data-sequence-project-tasks>查看本项目任务</button>
        ${!["succeeded","cancelled"].includes(run.status)?'<button class="btn ghost" data-sequence-cancel>停止本次运行</button>':""}</div>
        ${active?`<div class="connected-row" style="margin-top:12px"><label class="field"><span>本次运行模式</span><select class="control" data-sequence-run-mode>${modeOptions(mode)}</select></label><button class="btn" data-sequence-mode-apply>应用运行模式</button></div><p class="hint">模式调整作用于本次运行的项目任务；项目范围与专家工具权限保持各自的授权。</p>`:""}
        ${run.error?`<p class="connected-error">${h(run.error)}</p>`:""}
        <p class="connected-status">${h(run.settings.goal || "按项目研究问题执行")} · 运行 ${h(run.id)}</p>
        ${prepared?`<details open><summary>准备的节点任务</summary>${(run.layout.order || []).map(id=>{const node=run.layout.nodes.find(n=>n.id===id),taskId=run.settings.task_ids?.[id];return `<div class="connected-row" style="margin:8px 0"><span>${h(node?.label || id)}</span>${taskId?`<button class="btn ghost" data-sequence-task="${h(taskId)}">查看任务与执行者</button>`:""}</div>`;}).join("")}</details>`:""}
        ${run.nodes.map(node=>{
          const config=node.acceptance?.config || {}, checkpoint=node.checkpoint || {}, input=checkpoint.input || {}, output=checkpoint.output || {};
          const delivery=checkpoint.delivery || node.delivery || {}, source=delivery.source || checkpoint.delivery_source;
          const deliveryMode=delivery.policy_snapshot?.autonomy_mode || checkpoint.policy_snapshot?.autonomy_mode || run.settings?.policy?.autonomy_mode || run.settings?.autonomy_mode;
          return `<article class="connected-node"><div class="connected-row"><b>${h(config.label || node.stage)}</b><span class="pill">${h(statuses[node.status] || node.status)}</span><small>${h(input.member?.name || (S.assistants || []).find(a=>a.id===input.assistant_id)?.name || input.assistant_id || "")}</small></div>
            <details><summary>输入与记忆 · ${(input.history || []).length} 个前序结果</summary><pre>${h(JSON.stringify({goal:input.goal,upstream:input.handoff,memory:input.memory,history:(input.history || []).map(n=>({step:n.step,delivery_id:n.delivery_id,reply:n.reply,feedback:n.feedback}))},null,2))}</pre></details>
            <details ${node.status==="awaiting_review"?"open":""}><summary>产物与执行结果</summary><pre>${h(checkpoint.reply || node.error || "正在执行")}</pre><pre>${h(typeof output.content==="string"?output.content:JSON.stringify(output.content || output.items || {},null,2))}</pre>${(output.files || []).map(file=>`<div class="hint">${h(typeof file==="string"?file:file.relative || file.name || file.path || file.asset_id)}</div>`).join("")}</details>
            ${checkpoint.feedback?`<p>人工反馈：${h(checkpoint.feedback)}</p>`:""}
            ${source?`<p class="connected-status" data-sequence-decision-source>${source==="human"?(delivery.decision==="rejected"?"已由人工核查退回":"已由人工核查通过"):source==="policy"?(deliveryMode==="full_access"?"已按 Full access 自动验收":"已按节点配置自动验收"):h(source)}</p>`:""}
            ${checkpoint.canonical_task_id || node.canonical_task_id?`<button class="btn ghost" data-sequence-task="${h(checkpoint.canonical_task_id || node.canonical_task_id)}">打开节点项目任务与交付</button>`:""}
            ${node.status==="awaiting_review" && run.status==="awaiting_review" && run.current_node===node.id?`<label class="field"><span>审查意见，通过时可留空，退回时必填</span><textarea class="control" data-sequence-feedback="${h(node.id)}">${h(feedback.get(node.id) || "")}</textarea></label>
              <div class="connected-row"><button class="btn btn-primary" data-sequence-approve="${h(node.id)}">通过并继续</button><button class="btn ghost" data-sequence-reject="${h(node.id)}">退回修改</button></div>`:""}</article>`;
        }).join("")}`:"<p class='hint'>当前项目还没有顺序执行记录。先在工作流设置中选择节点的专家、团队、工具和技能。</p>"}`;
    host.querySelectorAll("[data-sequence-feedback]").forEach(input=>input.oninput=()=>feedback.set(input.dataset.sequenceFeedback,input.value));
    host.querySelector("[data-sequence-goal]")?.addEventListener("input",e=>goalDrafts.set(currentProject,e.target.value));
    host.querySelector("[data-sequence-mode]")?.addEventListener("change",e=>modeDrafts.set(currentProject,e.target.value));
    host.querySelector("[data-sequence-run-mode]")?.addEventListener("change",e=>modeDrafts.set(run.id,e.target.value));
    host.querySelectorAll("[data-sequence-task]").forEach(button=>button.onclick=()=>ProjectTasks.openTask(button.dataset.sequenceTask,currentProject));
    host.querySelector("[data-sequence-project-tasks]")?.addEventListener("click",()=>ProjectTasks.open(currentProject));
    host.querySelector('[data-sequence-chat]')?.addEventListener('click',async()=>{const project=currentProject,id=run.chat_id;dialog.close();await ProjectWorkspace.openConversation(id,project);});
    const bind=(selector,action,extra=()=>({}))=>host.querySelectorAll(selector).forEach(button=>button.onclick=async()=>{
      button.disabled=true;
      try {
        if(button.hasAttribute('data-sequence-restart') && !await uiConfirm('已核对中断节点的产物和外部操作？重新执行可能再次调用写入工具。')) {button.disabled=false;return;}
        const result=await api("/api/workflow/execution/"+action,{id:currentRun?.id,task_id:currentProject,...extra(button)});
        currentRun=result.run || currentRun; if(currentRun) focusedRun=currentRun.id; rendered=""; drawRun();
      } catch(error) { dialog.querySelector("[data-sequence-error]").textContent=error.message; button.disabled=false; }
    });
    const newRun=()=>({id:"",goal:host.querySelector("[data-sequence-goal]").value,autonomy_mode:host.querySelector("[data-sequence-mode]").value});
    bind("[data-sequence-start]","start",newRun);
    bind("[data-sequence-prepare]","prepare",newRun);
    bind("[data-sequence-start-prepared]","start",()=>({autonomy_mode:host.querySelector("[data-sequence-run-mode]").value}));
    bind("[data-sequence-mode-apply]","mode",()=>({autonomy_mode:host.querySelector("[data-sequence-run-mode]").value}));
    bind("[data-sequence-resume]","resume");
    bind('[data-sequence-restart]','resume',()=>({restart_node:true}));
    bind("[data-sequence-cancel]","cancel");
    bind("[data-sequence-approve]","review",button=>({node_id:button.dataset.sequenceApprove,approved:true,reviewed_output_version:Number(button.dataset.sequenceOutputVersion||0),feedback:feedback.get(button.dataset.sequenceApprove) || ""}));
    bind("[data-sequence-reject]","review",button=>({node_id:button.dataset.sequenceReject,approved:false,reviewed_output_version:Number(button.dataset.sequenceOutputVersion||0),feedback:feedback.get(button.dataset.sequenceReject) || ""}));
  }
  async function openRun(project,runId="") {
    currentProject=project;
    if(dialog) dialog.close();
    focusedRun=runId;
    dialog=document.createElement("dialog"); dialog.className="connected-dialog"; dialog.setAttribute("aria-label","工作流顺序执行与人工审查");
    dialog.innerHTML=`<div class="connected-row"><h3 style="flex:1">${h(activeProject()?.title || "项目")} · 顺序执行</h3><button class="btn ghost" data-sequence-close>关闭</button></div><div class="connected-error" role="status" data-sequence-error></div><div data-sequence-body>正在读取运行记录…</div>`;
    document.body.append(dialog);
    const instance=dialog;
    instance.querySelector("[data-sequence-close]").onclick=()=>instance.close();
    instance.addEventListener("close",()=>{ instance.remove(); if(dialog===instance){dialog=null;focusedRun="";} },{once:true});
    instance.showModal(); rendered="";
    try {
      if(!modeDrafts.has(project)){
        const result=await api("/api/project-tasks/policy?project_id="+encodeURIComponent(project));
        if(!modeDrafts.has(project)) modeDrafts.set(project,(result.policy || result).autonomy_mode || "supervised");
      }
      await fetchRun();
    } catch(error) { instance.querySelector("[data-sequence-error]").textContent=error.message; }
  }

  const originalWorkflow=renderWorkflow;
  renderWorkflow=async function(container) {
    await originalWorkflow(container);
    if(!uiRouteIs("workflow") || !curTask || !container.isConnected) return;
    if(dialog?.open && currentProject!==curTask) dialog.close();
    currentProject=curTask;
    const seqBtn=container.querySelector("#wfSequenceOpen");
    if(seqBtn) seqBtn.onclick=()=>openRun(curTask);
    const syncBtn=container.querySelector("#wfSyncSettings");
    if(syncBtn) syncBtn.onclick=syncSettings;
    fetchRun().catch(()=>{});
  };

  async function refreshLibrary() {
    const project=curTask, tab=route();
    const showSpace=uiRouteIs("space") && spTab==="papers", showFlow=uiRouteIs("workflow") && wfStep==="search";
    if(!project || (!showSpace && !showFlow)) return;
    const data=await api("/api/library/project?task_id="+encodeURIComponent(project));
    if(curTask!==project || route()!==tab) return;
    const key=project+":"+tab, previous=versions.get(key); versions.set(key,data.version);
    if(showFlow && wfStep==="search") {
      wsRes=data.items.filter(item=>item.result_id);
      if(document.getElementById("wsList")) wsPaint();
    }
    if(showSpace && spTab==="papers") {
      if(previous!==data.version) ProjectLiterature.update(project,data);
      ProjectLiterature.refreshStatus(project).catch(()=>{});
    }
  }
  const originalCalendar=renderCalendar;
  renderCalendar=function(container){ originalCalendar(container); calendarRendered=JSON.stringify(S.events); };
  async function refreshCalendar(){
    if(route()!=="calendar") return;
    const data=await api('/api/calendar/linked');
    if(route()!=="calendar") return;
    S.events=data.events;
    if(calendarRendered===JSON.stringify(S.events) || document.querySelector('#todayInput')?.value || document.querySelector('#todayTime')?.value) return;
    if(document.activeElement?.closest('.quick-add, .cal-pop')) return;
    renderCalendar(document.getElementById('main'));
  }
  async function openNote(project,path){
    await ProjectWorkspace.openProject(project);
    if(curTask!==project) return;
    await ProjectWorkspace.showResource('notes');
    await Notebook.open(path);
  }
  async function openCalendarSource(id){
    const event=S.events.find(e=>e.id===id);
    if(!event) throw new Error('关联日程已更新，请刷新后重试');
    if(!await pnSaveCurrent()) return;
    const project=event.task_id;
    if(event.target_type==='workflow') return openRun(project,event.run_id || event.target_id);
    if(event.target_type==='project_task') return ProjectWorkspace.openTask(event.target_id,project);
    if(event.target_type==='note') return openNote(project,event.note_path);
    curTask=project; localStorage.setItem('rw-task',project);
    if(event.target_type==='schedule'){
      await ProjectWorkspace.openProject(project);await ProjectWorkspace.showResource('automation');
      document.querySelector(`[data-sch-edit="${CSS.escape(event.schedule_id || event.target_id)}"]`)?.closest('.sch-card')?.scrollIntoView({block:'center'});
    }else if(event.target_type==='todo'){
      await ProjectWorkspace.openProject(project);
      await ProjectWorkspace.showResource('plan');
      await TaskCenter.openPlan(event.source_id,project);
    }
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-cal-open]');
    if(button) openCalendarSource(button.dataset.calOpen).catch(error=>uiNotify(error.message));
  });
  let inbox=null;
  async function reviewInbox(){
    if(inbox?.open) {inbox.focus();return;}
    inbox=document.createElement('dialog'); inbox.className='connected-dialog'; inbox.setAttribute('aria-label','待核查任务');
    inbox.innerHTML='<div class="connected-row"><h3 style="flex:1">待核查任务</h3><button class="btn ghost" data-review-close>关闭</button></div><p class="hint">稍后提醒只调整提醒时间；请打开项目任务核对产物，再决定通过或退回。</p><div role="status" data-review-error></div><div data-review-items>正在读取…</div>';
    document.body.append(inbox); const instance=inbox;
    instance.querySelector('[data-review-close]').onclick=()=>instance.close();
    instance.onclose=()=>{instance.remove(); if(inbox===instance) inbox=null;};instance.showModal();
    async function draw(){
      const data=await api('/api/workflow/reviews');
      instance.querySelector('[data-review-items]').innerHTML=data.items.map(r=>`<article class="connected-node"><b>${h(r.project_title)} · ${h(r.label)}</b><p>${r.overdue?'已逾期 · ':''}收到于 ${h(new Date(r.received_at).toLocaleString())}${r.due_at?' · 截止 '+h(new Date(r.due_at).toLocaleString()):''}</p><p>${h(r.summary)}</p><div class="connected-row"><button class="btn" data-review-open="${h(r.node_id)}">核对产物并审查</button><button class="btn ghost" data-review-snooze="${h(r.node_id)}">一小时后提醒</button><label>截止时间 <input type="datetime-local" data-review-due="${h(r.node_id)}"></label><button class="btn ghost" data-review-save="${h(r.node_id)}">保存期限</button></div></article>`).join('') || '<p>暂无待审查节点。</p>';
      for(const r of data.items){
        const select=attr=>instance.querySelector(`[${attr}="${CSS.escape(r.node_id)}"]`);
        if(r.due_at){const d=new Date(r.due_at);select('data-review-due').value=new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
        select('data-review-open').onclick=()=>{instance.close();if(r.canonical_task_id) ProjectTasks.openTask(r.canonical_task_id,r.project_id);else openRun(r.project_id,r.run_id);};
        const update=async body=>{try{await api('/api/workflow/reviews',{node_id:r.node_id,...body});await draw();await refreshReviews();}catch(error){instance.querySelector('[data-review-error]').textContent=error.message;}};
        select('data-review-snooze').onclick=()=>update({snooze_minutes:60});
        select('data-review-save').onclick=()=>{const value=select('data-review-due').value;update({due_at:value?new Date(value).toISOString():''});};
      }
    }
    try{await draw();}catch(error){instance.querySelector('[data-review-error]').textContent=error.message;}
  }
  async function refreshReviews(){
    const data=await api('/api/workflow/reviews');
    const host=document.querySelector('.topbar-actions') || document.getElementById('topbar');
    if(!host) return;
    let button=document.getElementById('workflowReviewInbox');
    if(!button){button=document.createElement('button');button.id='workflowReviewInbox';button.className='btn ghost';button.style.webkitAppRegion='no-drag';button.onclick=reviewInbox;host.prepend(button);}
    button.textContent=`待核查 ${data.count}`;button.classList.toggle('warn',data.notify_count>0);button.title=data.notify_count?`${data.notify_count} 项需要核查`:'查看待核查项目任务';
  }
  setInterval(async()=>{
    if(pollBusy || document.hidden) return;
    pollBusy=true;
    try {
      if(dialog?.open || uiRouteIs("workflow")) await fetchRun();
      await refreshLibrary();
      await refreshCalendar();
      await refreshReviews();
    } catch(error) {
      if(dialog?.open) dialog.querySelector("[data-sequence-error]").textContent=error.message;
    } finally { pollBusy=false; }
  },3000);
  window.ConnectedResearch={openRun,openNote,refreshLibrary,refreshCalendar,openCalendarSource,reviewInbox,refreshReviews};
})();
