/* Project work, executor grants and acceptance use the same task records. */
const ProjectTasks = (() => {
  "use strict";
  const h=value=>esc(String(value ?? ""));
  const names={draft:"已准备",ready:"可执行",queued:"排队中",running:"执行中",awaiting_review:"等待人工核查",delivered:"已交付",blocked:"等待依赖或配置",failed:"执行失败",cancelled:"已停止",interrupted:"中断待恢复",succeeded:"执行完成"};
  const modeName=mode=>mode==="full_access"?"Full access · 自动推进":"人工核查模式";
  const modes=(mode)=>`<option value="supervised" ${mode!=="full_access"?"selected":""}>人工核查模式</option><option value="full_access" ${mode==="full_access"?"selected":""}>Full access · 自动推进</option>`;
  const json=value=>{if(value && typeof value==="object") return value;try{return JSON.parse(value || "{}");}catch{return {};}};
  const label=b=>b.display_name || b.name || b.assistant_name || b.team_name || b.target_id || b.id;
  const savedModes=new Map();
  const style=document.createElement("style");
  style.textContent=`.pt-dialog{width:min(860px,calc(100vw - 28px));max-height:88vh;overflow:auto;border:1px solid var(--line);border-radius:14px;padding:20px;background:var(--bg);color:var(--fg);font-size:13px}.pt-dialog::backdrop{background:#0008}.pt-row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.pt-row h3{flex:1;margin:0}.pt-dialog label{display:block;margin:12px 0}.pt-dialog label>span{display:block;margin-bottom:5px}.pt-dialog input:not([type=checkbox]),.pt-dialog select,.pt-dialog textarea{width:100%;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:9px;font:inherit}.pt-dialog textarea{min-height:90px;resize:vertical}.pt-muted{color:var(--muted);font-size:12px;line-height:1.65}.pt-error{color:var(--err);white-space:pre-wrap;margin:10px 0}.pt-box{padding:12px;margin:12px 0;background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow-wrap:anywhere}.pt-list{display:grid;gap:8px;margin-top:12px}.pt-item{padding:11px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}.pt-item b{overflow-wrap:anywhere}.pt-actions{margin-top:16px;justify-content:flex-end}.pt-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:270px;overflow:auto;font-size:12px}.pt-dialog .btn{color:var(--fg)}.pt-dialog .btn-primary{color:var(--accent-ink)}.pt-dialog :focus-visible{outline:2px solid var(--accent);outline-offset:2px}.pt-split{display:grid;grid-template-columns:1fr 1fr;gap:12px}.pt-dialog details{margin:10px 0}.pt-role{display:grid;grid-template-columns:minmax(120px,1fr) minmax(170px,2fr) auto;gap:10px;align-items:center;margin:10px 0}@media(max-width:650px){.pt-split,.pt-role{grid-template-columns:1fr}.pt-dialog{padding:14px}}`;
  document.head.append(style);

  function modal(title){
    const el=document.createElement("dialog"), origin=document.activeElement;
    el.className="pt-dialog";el.setAttribute("aria-label",title);
    el.innerHTML=`<div class="pt-row"><h3>${h(title)}</h3><button class="btn ghost" data-close>关闭</button></div><div data-pt-body></div><div class="pt-error" role="status" data-pt-error></div>`;
    el.querySelector("[data-close]").onclick=()=>el.close();
    el.addEventListener("close",()=>{el.remove();if(origin?.isConnected) origin.focus();},{once:true});
    document.body.append(el);el.showModal();return el;
  }
  const problem=(el,error)=>{if(el.isConnected) el.querySelector("[data-pt-error]").textContent=error?.message || String(error);};
  const url=(suffix,project)=>"/api/project-tasks"+suffix+(suffix.includes("?")?"&":"?")+"project_id="+encodeURIComponent(project);
  const projectName=pid=>(S.tasks || []).find(t=>t.id===pid)?.title || "项目";
  function outputPath(output){return typeof output==="string"?output:output?.path || output?.canonical_uri || output?.file || "";}
  async function openOutput(output,el){
    const path=outputPath(output);if(!path) return;
    try{await api("/api/conv/open",{path});}catch(e){problem(el,e);}
  }
  function tasksOf(result){return result.tasks || result.items || [];}
  function bindingsOf(result){return result.bindings || result.executors || [];}
  function usableBinding(binding){return binding.enabled!==false && binding.enabled!==0 && !binding.disabled_reason;}
  function choiceOptions(bindings,current=""){
    current=ExecutorChoices.find(bindings,current)?.id||current;
    return '<option value="">请选择执行者…</option>'+ExecutorChoices.unique(bindings,current).map(b=>`<option value="${h(b.id)}" ${b.id===current?"selected":""} ${usableBinding(b)?"":"disabled"}>${h((b.kind==="team"?"团队 · ":"专家 · ")+label(b))}${b.disabled_reason?" · "+h(b.disabled_reason):""}</option>`).join("");
  }

  async function open(projectId=curTask){
    if(!projectId){uiNotify("请先选择项目");return;}
    const el=modal(projectName(projectId)+" · 项目任务"), body=el.querySelector("[data-pt-body]");
    let tasks=[],request=0;
    body.innerHTML=`<p class="pt-muted">单项工作可以直接交给项目内的专家或团队；需要多步骤协作时再采用工作流。</p><div class="pt-row"><button class="btn btn-primary" data-pt-new>新建项目任务</button><button class="btn" data-pt-executors>执行者与默认模式</button><button class="btn" data-pt-roles>工作流角色绑定</button><button class="btn ghost" data-pt-refresh>刷新</button></div><div class="pt-split"><label><span>查找任务</span><input type="search" data-pt-search placeholder="按标题或任务说明查找"></label><label><span>任务状态</span><select data-pt-filter><option value="">全部状态</option>${Object.entries(names).filter(([k])=>k!=="succeeded").map(([k,v])=>`<option value="${k}">${h(v)}</option>`).join("")}</select></label></div><div class="pt-list" data-pt-tasks>正在读取任务…</div>`;
    function draw(){
      const q=body.querySelector("[data-pt-search]").value.toLowerCase(),status=body.querySelector("[data-pt-filter]").value;
      const shown=tasks.filter(t=>(!status || t.status===status) && [t.title,t.goal,t.id].join(" ").toLowerCase().includes(q));
      body.querySelector("[data-pt-tasks]").innerHTML=shown.map(t=>`<article class="pt-item"><div class="pt-row"><b style="flex:1">${h(t.title || t.goal || "项目任务")}</b><span>${h(names[t.status] || t.status)}</span><button class="btn" data-pt-open="${h(t.id)}">打开任务</button></div><div class="pt-muted">${h(t.workflow_run_id?"工作流节点任务":"项目任务")}${t.parent_task_id?" · 子任务":""} · ${h(t.updated_at?fmtDate(t.updated_at):"")}</div>${t.goal && t.goal!==t.title?`<div class="pt-muted">${h(t.goal.slice(0,180))}</div>`:""}</article>`).join("") || '<div class="pt-box pt-muted">当前没有符合条件的任务。点击“新建项目任务”开始；执行者可在“执行者与默认模式”中加入本项目。</div>';
      body.querySelectorAll("[data-pt-open]").forEach(button=>button.onclick=()=>openTask(button.dataset.ptOpen,projectId));
    }
    async function refresh(){
      const seq=++request;
      try{const result=await api(url("",projectId));if(el.open && seq===request){tasks=tasksOf(result);draw();}}
      catch(error){problem(el,error);}
    }
    body.querySelector("[data-pt-new]").onclick=()=>compose(projectId).then(child=>child?.addEventListener("close",refresh,{once:true}));
    body.querySelector("[data-pt-executors]").onclick=()=>openBindings(projectId);
    body.querySelector("[data-pt-roles]").onclick=()=>openRoles(projectId);
    body.querySelector("[data-pt-refresh]").onclick=refresh;
    body.querySelector("[data-pt-search]").oninput=draw;body.querySelector("[data-pt-filter]").onchange=draw;
    await refresh();
    const timer=setInterval(()=>{if(el.open && document.querySelector("dialog:last-of-type")===el) refresh();},6000);
    el.addEventListener("close",()=>clearInterval(timer),{once:true});return el;
  }

  async function openBindings(projectId){
    const el=modal(projectName(projectId)+" · 执行者与默认模式"),body=el.querySelector("[data-pt-body]");
    let data={bindings:[],available_assistants:[],teams:[]},policyTouched=false;
    body.innerHTML=`<p class="pt-muted">所有已启用的专家和团队均可在本项目调用。这里设置项目专属的停用或工具限制，不影响其他项目。</p><div class="pt-box"><div data-pt-bindings>正在读取…</div></div><div class="pt-split"><label><span>加入类型</span><select data-pt-grant-kind><option value="assistant">专家</option><option value="team">全局团队</option></select></label><label><span>选择执行者</span><select data-pt-grant-target></select></label></div><button class="btn" data-pt-grant>保存项目设置</button><hr><label><span>本项目新任务的默认模式</span><select data-pt-policy>${modes("supervised")}</select></label><p class="pt-muted">关键节点默认等待人工判断。Full access 按产物和依赖校验自动推进，并记录自动验收来源。工具访问权限另行控制。</p><button class="btn" data-pt-policy-save>保存默认模式</button>`;
    body.querySelector("[data-pt-policy]").disabled=true;
    body.querySelector("[data-pt-policy-save]").disabled=true;
    body.querySelector("[data-pt-policy]").onchange=()=>policyTouched=true;
    function choices(){
      const kind=body.querySelector("[data-pt-grant-kind]").value,rows=kind==="team"?data.teams || []:data.available_assistants || data.assistants || [];
      body.querySelector("[data-pt-grant-target]").innerHTML='<option value="">请选择…</option>'+rows.map(row=>`<option value="${h(row.id)}" ${row.enabled===0 || row.enabled===false?"disabled":""}>${h(row.name || row.title || row.id)}${row.enabled===0 || row.enabled===false?" · 已停用":""}</option>`).join("");
    }
    async function refresh(){
      try{
        const [executors,policy]=await Promise.all([api(url("/executors",projectId)),api(url("/policy",projectId))]);
        if(!el.open) return;data=executors;
        body.querySelector("[data-pt-bindings]").innerHTML=bindingsOf(data).map(b=>`<div class="pt-row" style="margin:8px 0"><span style="flex:1">${h((b.kind==="team"?"团队 · ":"专家 · ")+label(b))}</span><span class="pt-muted">${h(b.disabled_reason || (usableBinding(b)?"可用于项目任务":"已停用"))}</span><button class="btn ghost" data-pt-binding-toggle="${h(b.id)}">${b.enabled?"停用":"重新启用"}</button></div>`).join("") || '<p class="pt-muted">项目尚未授权执行者。</p>';
        body.querySelectorAll("[data-pt-binding-toggle]").forEach(button=>button.onclick=async()=>{
          const binding=bindingsOf(data).find(b=>b.id===button.dataset.ptBindingToggle);button.disabled=true;
          try{await api("/api/project-tasks/bindings",{project_id:projectId,kind:binding.kind,target_id:binding.target_id,tools:binding.tools,enabled:!binding.enabled});await refresh();}
          catch(error){problem(el,error);button.disabled=false;}
        });
        if(!policyTouched) body.querySelector("[data-pt-policy]").value=(policy.policy || policy.settings || policy).autonomy_mode || "supervised";
        body.querySelector("[data-pt-policy]").disabled=false;body.querySelector("[data-pt-policy-save]").disabled=false;choices();
      }catch(error){problem(el,error);}
    }
    body.querySelector("[data-pt-grant-kind]").onchange=choices;
    body.querySelector("[data-pt-grant]").onclick=async event=>{
      const target=body.querySelector("[data-pt-grant-target]").value;if(!target){problem(el,"请先选择执行者");return;}
      event.target.disabled=true;
      try{await api("/api/project-tasks/bindings",{project_id:projectId,kind:body.querySelector("[data-pt-grant-kind]").value,target_id:target});await refresh();}
      catch(error){problem(el,error);}finally{event.target.disabled=false;}
    };
    body.querySelector("[data-pt-policy-save]").onclick=async event=>{
      event.target.disabled=true;
      try{await api("/api/project-tasks/policy",{project_id:projectId,autonomy_mode:body.querySelector("[data-pt-policy]").value});uiNotify("项目默认模式已保存，适用于后续新运行");}
      catch(error){problem(el,error);}finally{event.target.disabled=false;}
    };
    await refresh();return el;
  }

  async function openRoles(projectId,templateId=''){
    const el=modal(projectName(projectId)+" · 工作流角色绑定"),body=el.querySelector("[data-pt-body]");
    body.innerHTML='<p class="pt-muted">为本项目的节点角色选择执行者。节点中单独选择的执行者优先于角色绑定；其他项目使用各自的配置。</p><label><span>工作流模板</span><select data-pt-role-template></select></label><div data-pt-role-list>正在读取…</div>';
    let layouts=[],request=0;
    async function showRoles(){
      const templateId=body.querySelector("[data-pt-role-template]").value, seq=++request;
      if(!templateId) return;
      try{
        const [result,executors]=await Promise.all([api(url("/workflow-roles?template_id="+encodeURIComponent(templateId),projectId)),api(url("/executors",projectId))]);
        if(!el.open || seq!==request) return;
        const roles=result.roles || [],layout=result.template||layouts.find(item=>item.id===templateId),bindings=result.bindings || bindingsOf(executors);
        const needed=new Map((layout?.nodes || []).map(node=>[node.executor_role_id || node.id,node.label || node.id]));
        for(const role of roles) if(!needed.has(role.role_id)) needed.set(role.role_id,role.role_id);
        const host=body.querySelector("[data-pt-role-list]");
        host.innerHTML=[...needed].map(([id,name])=>`<div class="pt-role" data-pt-role="${h(id)}"><div><b>${h(name)}</b><div class="pt-muted">${h(id)}</div></div><select aria-label="${h(name)}的项目执行者">${choiceOptions(bindings,roles.find(r=>r.role_id===id)?.executor_binding_id || roles.find(r=>r.role_id===id)?.binding_id)}</select><button class="btn">保存绑定</button></div>`).join("") || '<p class="pt-muted">此模板还没有节点。</p>';
        host.querySelectorAll("[data-pt-role]").forEach(row=>row.querySelector("button").onclick=async event=>{
          const binding=row.querySelector("select").value;if(!binding){problem(el,"请选择本项目执行者");return;}
          event.target.disabled=true;
          try{await api("/api/project-tasks/workflow-roles",{project_id:projectId,template_id:templateId,role_id:row.dataset.ptRole,executor_binding_id:binding});uiNotify("本项目角色绑定已保存");}
          catch(error){problem(el,error);}finally{event.target.disabled=false;}
        });
      }catch(error){problem(el,error);}
    }
    try{
      const data=await api("/api/workflow/layouts");layouts=data.layouts || [];
      if(!el.open) return el;
      body.querySelector("[data-pt-role-template]").innerHTML=layouts.map(layout=>`<option value="${h(layout.id)}">${h(layout.name)}</option>`).join("");
      if(templateId&&layouts.some(layout=>layout.id===templateId))body.querySelector('[data-pt-role-template]').value=templateId;
      body.querySelector("[data-pt-role-template]").onchange=showRoles;await showRoles();
    }catch(error){problem(el,error);}
    return el;
  }

  async function compose(projectId,options={}){
    if(!projectId){uiNotify("请先选择项目");return;}
    const el=modal("新建项目任务"),body=el.querySelector("[data-pt-body]"), inputs=options.inputs || [];
    const draftKey="rw-project-task-draft:"+projectId+":"+JSON.stringify(inputs)+':'+(options.plan_id || ''), saved=json(localStorage.getItem(draftKey));
    let requestKey=crypto.randomUUID(),prepared=null,bindings=[],teams=[],modeTouched=Boolean(saved.autonomy_mode);
    body.innerHTML=`<div class="pt-box"><b>${h(projectName(projectId))}</b><div class="pt-muted">独立项目任务，无需选择工作流节点。</div>${inputs.map(input=>`<div>${h(input.relative || input.name || input.asset_id || "项目材料")}</div>`).join("")}</div><label><span>任务标题</span><input data-pt-title maxlength="200" value="${h(saved.title || options.title || "")}" placeholder="例如：提取这篇论文的实验条件"></label><label><span>任务说明</span><textarea data-pt-goal maxlength="20000" placeholder="说明要解决的问题、需要核对的证据及预期产物">${h(saved.goal || options.goal || "")}</textarea></label><label><span>项目执行者</span><select data-pt-executor disabled><option value="">正在读取…</option></select></label><button class="btn ghost" data-pt-compose-bindings>执行者与项目权限</button><label><input type="checkbox" data-pt-review ${saved.review_required===false?"":"checked"}> 本任务需要人工核查</label><label><span>本次执行模式</span><select data-pt-mode>${modes(saved.autonomy_mode)}</select></label><div class="pt-muted">Full access 会在实际产物和依赖校验通过后自动交付，记录为自动验收。它不扩大项目或工具权限。</div><details><summary>交付要求</summary><label><span>至少输出字符数</span><input type="number" min="1" max="1000000" data-pt-min-chars value="${h(saved.min_chars || 1)}"></label><label><span>必须生成的项目文件，每行一个相对路径</span><textarea data-pt-required placeholder="笔记/分析结果.md">${h(saved.required_files || "")}</textarea></label></details><div class="pt-row pt-actions"><button class="btn" data-pt-prepare>准备任务</button><button class="btn btn-primary" data-pt-create-start>创建并开始</button></div>`;
    body.querySelector("[data-pt-executor]").closest("label").insertAdjacentHTML("afterend",'<label data-pt-member-label hidden><span>团队执行成员</span><select data-pt-member><option value="">由团队负责人承接</option></select></label><label><span>任务关系</span><select data-pt-parent><option value="">独立项目任务</option></select></label>');
    const value=selector=>body.querySelector(selector).value;
    const remember=()=>{
      const draft={title:value("[data-pt-title]"),goal:value("[data-pt-goal]"),binding_id:value("[data-pt-executor]"),member_id:value("[data-pt-member]"),parent_task_id:value("[data-pt-parent]"),review_required:body.querySelector("[data-pt-review]").checked,autonomy_mode:value("[data-pt-mode]"),min_chars:value("[data-pt-min-chars]"),required_files:value("[data-pt-required]")};
      try{localStorage.setItem(draftKey,JSON.stringify(draft));}catch{}
    };
    const invalidate=()=>{prepared=null;requestKey=crypto.randomUUID();remember();};
    body.querySelectorAll("input,textarea,select").forEach(field=>{field.addEventListener("input",invalidate);field.addEventListener("change",invalidate);});
    body.querySelector("[data-pt-mode]").addEventListener("change",()=>modeTouched=true);
    el.addEventListener("close",remember,{once:true});
    function members(current=""){
      const binding=bindings.find(item=>item.id===value("[data-pt-executor]")),team=teams.find(item=>item.id===binding?.target_id);
      const select=body.querySelector("[data-pt-member]");
      body.querySelector("[data-pt-member-label]").hidden=binding?.kind!=="team";
      const members=ExecutorChoices.members(team?.members,current);
      select.innerHTML='<option value="">由团队负责人承接</option>'+members.map(member=>`<option value="${h(member.id)}" ${member.id===current?"selected":""}>${h(member.name || member.id)}${member.role==="leader"?" · 负责人":""}</option>`).join("");
      if(current&&!members.some(member=>member.id===current))select.insertAdjacentHTML('beforeend',`<option value="${h(current)}" selected disabled>原成员已失效，请重新选择</option>`);
    }
    body.querySelector("[data-pt-executor]").addEventListener("change",()=>members());
    async function loadExecutors(){
      try{
        const result=await api(url("/executors",projectId));if(!el.open) return;
        const current=value("[data-pt-executor]") || saved.binding_id || options.executor_binding_id || "";
        const currentMember=value("[data-pt-member]") || saved.member_id || options.member_id;
        bindings=bindingsOf(result);teams=result.teams || [];const select=body.querySelector("[data-pt-executor]");select.innerHTML=choiceOptions(bindings,current);select.disabled=false;members(currentMember);
        if(!bindings.some(usableBinding)) problem(el,"当前项目尚无可用执行者，请先加入专家或团队。");
      }catch(error){problem(el,error);}
    }
    body.querySelector("[data-pt-compose-bindings]").onclick=async()=>{const child=await openBindings(projectId);child.addEventListener("close",loadExecutors,{once:true});};
    async function submit(start){
      const goal=value("[data-pt-goal]").trim() || value("[data-pt-title]").trim(),binding=value("[data-pt-executor]");
      if(!goal || !binding){problem(el,"请填写任务说明并选择项目执行者。");return;}
      el.querySelector("[data-pt-error]").textContent="";
      body.querySelectorAll("button,input,textarea,select").forEach(field=>field.disabled=true);
      try{
        await options.beforeSubmit?.();
        const mode=value("[data-pt-mode]");
        if(!prepared){
          prepared=(await api("/api/project-tasks/create",{project_id:projectId,plan_id:options.plan_id||'',title:value("[data-pt-title]").trim() || goal.slice(0,80),goal,inputs,executor_binding_id:binding,
            member_id:value("[data-pt-member]"),review_required:body.querySelector("[data-pt-review]").checked,parent_task_id:value("[data-pt-parent]"),metadata:options.metadata || {},
            output_contract:{min_chars:Math.max(1,Number(value("[data-pt-min-chars]")) || 1),required_files:value("[data-pt-required]").split(/\r?\n/).map(s=>s.trim()).filter(Boolean)},request_key:requestKey})).task;
        }
        savedModes.set(prepared.id,mode);
        if(start) prepared=(await api("/api/project-tasks/start",{id:prepared.id,project_id:projectId,request_key:requestKey+":start",autonomy_mode:mode})).task;
        const task=prepared;el.close();await ProjectWorkspace.openConversation(task.chat_id,projectId);
      }catch(error){problem(el,error);body.querySelectorAll("button,input,textarea,select").forEach(field=>field.disabled=false);}
    }
    body.querySelector("[data-pt-prepare]").onclick=()=>submit(false);body.querySelector("[data-pt-create-start]").onclick=()=>submit(true);
    try{
      const result=await api(url("",projectId)),parents=tasksOf(result).filter(task=>!task.workflow_run_id);
      const selected=options.parent_task_id || saved.parent_task_id || "",select=body.querySelector("[data-pt-parent]");
      select.innerHTML='<option value="">独立项目任务</option>'+parents.map(task=>`<option value="${h(task.id)}" ${task.id===selected?"selected":""}>补充任务：${h(task.title || task.goal || task.id)}</option>`).join("");
      if(options.parent_task_id){select.value=options.parent_task_id;select.disabled=true;}
    }catch(error){problem(el,error);}
    await loadExecutors();
    if(!modeTouched){try{const result=await api(url("/policy",projectId));if(el.open && !modeTouched) body.querySelector("[data-pt-mode]").value=(result.policy || result.settings || result).autonomy_mode || "supervised";}catch(error){problem(el,error);}}
    return el;
  }

  async function openTask(taskId,projectId=curTask){
    const el=modal("项目任务与交付"),body=el.querySelector("[data-pt-body]");
    let task=null,signature="",feedback="",busy=false,mode=savedModes.get(taskId) || "",request=0;
    body.textContent="正在读取任务…";
    async function fetchTask(){
      const seq=++request;
      try{const result=await api(url("/detail?id="+encodeURIComponent(taskId),projectId));if(el.open && seq===request){task=result.task || result;draw();}}
      catch(error){problem(el,error);}
    }
    function draw(){
      if(!task) return;const next=JSON.stringify(task);if(signature===next) return;signature=next;
      const run=task.latest_run || {},policy={...json(run.policy_snapshot),...task.metadata?._autonomy_override},output=json(run.output_snapshot),outputs=task.outputs || output.files || output.assets || [];
      const uncertainTool=["failed","blocked"].includes(task.status) && json(run.checkpoint).phase==="tool";
      if(!mode) mode=policy.autonomy_mode || task.autonomy_mode || "supervised";
      body.innerHTML=`<div class="pt-box"><b>${h(task.title || task.goal)}</b><div class="pt-muted">${h(projectName(task.project_id || projectId))} · ${h(task.workflow_run_id?"工作流节点任务":"项目任务")} · ${h(task.id)}</div><p>${h(task.goal || "")}</p></div><div class="pt-row"><b data-pt-status>${h(names[task.status] || task.status)}</b>${run.id?`<span class="pt-muted">${h(modeName(policy.autonomy_mode))}</span>`:""}</div>
        ${task.blocked_reason || task.error || run.error?`<p class="pt-error">${h(task.blocked_reason || task.error || run.error)}</p>`:""}
        ${uncertainTool?`<div class="pt-box"><b>上次工具调用的执行结果尚未确认</b><p class="pt-muted">请核对已生成的文件和外部操作。普通重试会保留这个检查点；确认可以重做后，再选择“核对后重新执行”。</p><details><summary>查看上次工具调用</summary><pre>${h(JSON.stringify(json(run.checkpoint).inflight || {},null,2))}</pre></details><button class="btn" data-pt-action="restart">核对后重新执行</button></div>`:""}
        ${run.id?`<details open><summary>执行结果</summary><pre>${h(output.reply || output.text || json(run.checkpoint).reply || "等待执行结果")}</pre></details>`:""}
        ${outputs.length?`<div class="pt-list">${outputs.map((item,i)=>`<div class="pt-item pt-row"><span style="flex:1">${h(typeof item==="string"?item:item.title || item.name || outputPath(item) || item.asset_id || "产物")}</span>${outputPath(item)?`<button class="btn ghost" data-pt-output="${i}">打开产物</button>${ProjectOutputReview.canEdit(task,item)?`<button class="btn ghost" data-pt-output-edit="${i}">人工修订</button>`:""}`:""}</div>`).join("")}</div>`:""}
        ${(task.deliveries || []).map(delivery=>{const p=json(delivery.policy_snapshot),source=delivery.source==="human"?"人工核查":delivery.source==="policy"?(p.autonomy_mode==="full_access"?"按 Full access 自动验收":"按节点配置自动验收"):"历史交付记录";return `<div class="pt-box" data-pt-decision-source><b>${h(source)} · ${delivery.decision==="rejected"?"退回":"通过"}</b>${delivery.feedback?`<p>${h(delivery.feedback)}</p>`:""}<div class="pt-muted">${h(delivery.created_at?fmtDate(delivery.created_at):"")}</div></div>`;}).join("")}
        ${(task.children || []).length?`<details open><summary>子任务</summary>${task.children.map(child=>{const item=typeof child==="string"?{id:child}:child;return `<div class="pt-row"><span>${h(item.title || item.id)} · ${h(names[item.status] || item.status || "")}</span><button class="btn ghost" data-pt-child="${h(item.id)}">打开</button></div>`;}).join("")}</details>`:""}
        ${task.status==="awaiting_review"?`<label><span>人工核查意见</span><textarea data-pt-feedback placeholder="通过时可留空，退回时请说明需要修改的内容">${h(feedback)}</textarea></label><div class="pt-row"><button class="btn btn-primary" data-pt-action="approve">人工核查通过</button><button class="btn" data-pt-action="reject">退回修改</button></div>`:""}
        ${task.status!=="delivered"?`<label><span>当前任务执行模式</span><select data-pt-task-mode>${modes(mode)}</select></label><p class="pt-muted">自动验收仍检查实际产物、依赖和版本；工具访问权限单独控制。</p><div class="pt-row pt-actions">${["draft","ready","failed","blocked","cancelled","interrupted"].includes(task.status)?`<button class="btn btn-primary" data-pt-action="start">${["failed","cancelled","interrupted"].includes(task.status)?"重试任务":"开始执行"}</button>`:""}${["queued","running","awaiting_review"].includes(task.status)?'<button class="btn" data-pt-action="mode">应用模式并继续</button>':""}${task.status!=="cancelled"?'<button class="btn ghost" data-pt-action="cancel">停止任务</button>':""}</div>`:""}
        <details><summary>运行追踪</summary><pre>${h(JSON.stringify({task_id:task.id,run_id:run.id,parent_task_id:task.parent_task_id,workflow_run_id:task.workflow_run_id,node_id:task.workflow_node_id,policy:run.policy_snapshot,executor:run.executor_snapshot},null,2))}</pre></details>`;
      body.querySelector("[data-pt-feedback]")?.addEventListener("input",e=>feedback=e.target.value);
      body.querySelector("[data-pt-task-mode]")?.addEventListener("change",e=>{mode=e.target.value;savedModes.set(task.id,mode);});
      body.querySelectorAll("[data-pt-output]").forEach(button=>button.onclick=()=>openOutput(outputs[Number(button.dataset.ptOutput)],el));
      body.querySelectorAll('[data-pt-output-edit]').forEach(button=>button.onclick=()=>ProjectOutputReview.open(projectId,task.id,Number(button.dataset.ptOutputEdit),async()=>{signature='';await fetchTask();}));
      body.querySelectorAll("[data-pt-child]").forEach(button=>button.onclick=()=>openTask(button.dataset.ptChild,projectId));
      body.querySelectorAll("[data-pt-action]").forEach(button=>button.onclick=async()=>{
        if(busy) return;const action=button.dataset.ptAction;
        if(action==="reject" && !feedback.trim()){problem(el,"退回时请填写核查意见。");return;}
        if(action==="restart" && !await uiConfirm("已核对上次工具调用的产物和外部操作？重新执行可能再次调用写入工具。")) return;
        el.querySelector("[data-pt-error]").textContent="";
        busy=true;++request;body.querySelectorAll("[data-pt-action]").forEach(b=>b.disabled=true);
        try{
          const route=action==="approve" || action==="reject"?"review":action==="restart"?"start":action;
          const result=await api("/api/project-tasks/"+route,{id:task.id,project_id:projectId,request_key:crypto.randomUUID(),version:task.version,reviewed_run_id:task.latest_run?.id||'',reviewed_output_version:task.latest_run?.checkpoint?.output_revision||0,approved:action==="approve",feedback,autonomy_mode:mode,restart:action==="restart"});
          if(result.task) task=result.task;signature="";draw();
        }catch(error){problem(el,error);body.querySelectorAll("[data-pt-action]").forEach(b=>b.disabled=false);}finally{busy=false;}
      });
    }
    await fetchTask();
    const timer=setInterval(()=>{if(el.open && !busy && task && !["delivered","cancelled"].includes(task.status)) fetchTask();},1800);
    el.addEventListener("close",()=>clearInterval(timer),{once:true});return el;
  }
  const originalSpace=renderSpace;
  renderSpace=async function(container){
    await originalSpace(container);
    const tabs=container.querySelector("#spTabs"),projectId=curTask;
    if(!tabs || !projectId || !container.isConnected || tabs.querySelector("[data-pt-project-open]")) return;
    tabs.insertAdjacentHTML("beforeend",'<span style="flex:1"></span><button type="button" class="btn btn-sm" data-pt-project-open title="创建、执行与核查当前项目的任务">项目任务</button><button type="button" class="btn ghost btn-sm" data-pt-project-config title="管理当前项目的专家、团队授权和默认执行模式">执行者与模式</button>');
    tabs.querySelector("[data-pt-project-open]").onclick=()=>open(projectId);
    tabs.querySelector("[data-pt-project-config]").onclick=()=>openBindings(projectId);
  };
  return {open,openTask,compose,openBindings,openRoles};
})();
window.ProjectTasks=ProjectTasks;
