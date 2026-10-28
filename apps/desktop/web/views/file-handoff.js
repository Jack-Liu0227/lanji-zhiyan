/* File work can be a project task or belong to a specific workflow node. */
const FileHandoff = (() => {
  "use strict";
  const h = value => esc(String(value ?? ""));
  const statuses = {draft:"已准备，尚未执行",queued:"等待执行",running:"执行中",awaiting_review:"等待节点验收",succeeded:"已交付",failed:"执行失败",blocked:"待修改",interrupted:"中断待恢复",cancelled:"已停止",delivered:"已交付",rejected:"已退回"};
  const css = document.createElement("style");
  css.textContent = `.handoff-dialog{width:min(820px,calc(100vw - 32px));max-height:88vh;overflow:auto;padding:22px;border:1px solid var(--line);border-radius:14px;background:var(--bg);color:var(--fg)}.handoff-dialog::backdrop{background:#0008}.handoff-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.handoff-row h3{flex:1;margin:0}.handoff-context{padding:12px;background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:14px 0;overflow-wrap:anywhere}.handoff-muted{font-size:12px;color:var(--muted);line-height:1.7}.handoff-dialog input,.handoff-dialog select,.handoff-dialog textarea{width:100%;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:9px;font:inherit}.handoff-dialog textarea{min-height:90px;resize:vertical}.handoff-dialog label{display:block;margin:12px 0}.handoff-targets{display:grid;gap:8px;max-height:230px;overflow:auto;margin:12px 0}.handoff-target{display:block;width:100%;text-align:left;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--fg);cursor:pointer}.handoff-target[aria-pressed=true],.handoff-row [aria-pressed=true]{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,var(--panel))}.handoff-target:disabled{opacity:.65;cursor:default}.handoff-target small{display:block;margin-top:4px}.handoff-error{color:var(--err);white-space:pre-wrap;margin:10px 0}.handoff-actions{margin-top:16px;justify-content:flex-end}.handoff-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}.handoff-history-item{padding:12px 0;border-bottom:1px solid var(--line)}.handoff-dialog :focus-visible{outline:2px solid var(--accent);outline-offset:2px}`;
  document.head.append(css);
  css.textContent += `.handoff-dialog{font-size:13px}.handoff-dialog h3{font-size:17px}.handoff-context{padding:10px;margin:10px 0}.handoff-context p{margin:7px 0}.handoff-dialog label{margin:8px 0}.handoff-dialog textarea{min-height:70px}.handoff-targets{max-height:180px;margin:10px 0}.handoff-dialog [data-view]{background:var(--panel);color:var(--fg)}.handoff-dialog [data-view][aria-pressed=true]{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,var(--panel))}.handoff-actions{position:sticky;bottom:-22px;padding:12px 0;background:var(--bg);border-top:1px solid var(--line);z-index:1}`;
  let active = null;
  let previewRequest=0;
  const keyOf = (node,actor) => [node.id,actor.team_id,actor.member_id,actor.assistant_id].join("|");
  const read = key => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } };
  const write = (key,value) => { try { localStorage.setItem(key,JSON.stringify(value)); } catch {} };
  function dialog(title){
    active?.close();
    const origin=document.activeElement, el=document.createElement("dialog");
    el.className="handoff-dialog"; el.setAttribute("aria-label",title);
    el.innerHTML=`<div class="handoff-row"><h3>${h(title)}</h3><button class="btn ghost" data-close>关闭</button></div><div data-body></div><p class="handoff-error" role="status" data-error></p>`;
    el.querySelector("[data-close]").onclick=()=>el.close();
    el.addEventListener("close",()=>{ el.remove(); if(active===el) active=null; if(origin?.isConnected) origin.focus(); },{once:true});
    document.body.append(el); el.showModal(); active=el;
    return el;
  }
  function focused(){
    const element=document.activeElement;
    const row=element?.closest('.fl-tree-row, [data-tree-path], .nb-note-row');
    if(row && row.dataset.dir!=="1" && row.dataset.treeType!=="dir"){
      const flow=row.matches('.fl-tree-row');
      const relative=flow?row.dataset.rel:row.dataset.treePath || row.querySelector('[data-pn-file]')?.dataset.pnFile;
      if(relative) return {project_id:flow?(row.dataset.projectId || curTask):mdDashProject,relative};
    }
    if(element?.closest('.fl-col') && flSel && uiRouteIs("workflow") && document.getElementById("flHand")?.disabled===false) return {project_id:curTask,relative:flSel};
    return null;
  }
  function dirty(ref){
    return flCurrentFile?.projectId===ref.project_id && flCurrentFile.row.rel===ref.relative && flCurrentFile.text!==flCurrentFile.originalText;
  }
  async function saveInput(ref){
    if(dirty(ref) && !await flSaveCurrent()) throw new Error("文件尚未保存，交接已停止。请先处理编辑器的保存错误。");
    if(mdDashProject===ref.project_id && mdDashPath===ref.relative && !await pnSaveCurrent()) throw new Error("笔记保存失败，交接已停止。");
  }
  async function open(ref, preferred=""){
    if(!ref?.project_id || !ref.relative){ uiNotify("请先选择项目中的文件"); return; }
    if(uiRouteIs("workflow") && ref.project_id!==curTask){uiNotify("项目已切换，请重新选择文件。");return;}
    ref={project_id:ref.project_id,relative:ref.relative};
    const activeProject=curTask;
    const el=dialog("交给当前工作流"), body=el.querySelector("[data-body]"), error=el.querySelector("[data-error]");
    const draftKey="rw-handoff-draft:"+ref.project_id+":"+ref.relative;
    let data=null, view="node", selection=null, group=[], related=[], requestKey=crypto.randomUUID(), prepared=null;
    const saved=read(draftKey) || {};
    body.innerHTML=`<div class="handoff-row" role="group" aria-label="任务范围"><button class="btn" data-handoff-project aria-pressed="false">项目任务 · 无需工作流</button><button class="btn" data-handoff-workflow aria-pressed="true">当前工作流节点</button></div>
      <div class="handoff-context"><b>${h((S.tasks || []).find(t=>t.id===ref.project_id)?.title || "当前项目")}</b><div>${h(ref.relative)}</div><div class="handoff-muted" data-workflow>读取当前工作流…</div><div class="handoff-muted">${dirty(ref)?"有未保存修改，准备或发送前会先保存。":"使用已保存的文件；执行时固定输入版本。"}</div></div>
      <div class="handoff-context" data-configuration hidden role="status"></div>
      <div class="handoff-row" role="group" aria-label="目标查找方式">${[["node","按节点"],["team","按团队"],["assistant","按专家"]].map(([id,label])=>`<button class="btn" data-view="${id}" aria-pressed="${id==="node"}">${label}</button>`).join("")}<button class="btn ghost" data-refresh>刷新目标</button><button class="btn ghost" data-configure>配置工作流</button></div>
      <label>查找目标<input type="search" data-search placeholder="搜索当前工作流内的名称"></label>
      <label><span data-target-label>选择节点</span><select data-target-select aria-label="选择节点" disabled><option value="">正在读取…</option></select></label>
      <div class="handoff-targets" data-targets>正在读取…</div>
      <div class="handoff-context" data-selection>选择一个目标；团队或专家都需要归属到具体节点。</div>
      <label>接收方式<select data-parent><option value="">新建节点任务</option></select></label><div class="handoff-muted">补充已有任务会另建关联的处理记录，保留原任务与运行输入。</div>
      <label>任务说明<textarea data-instruction maxlength="20000" placeholder="希望用这个文件完成什么？">${h(saved.instruction || "")}</textarea></label>
      <p class="handoff-muted">只执行选中的节点。前置交付尚未验收、或项目已有运行时，材料会保留为待处理任务。</p>
      <div class="handoff-row handoff-actions"><button class="btn" data-prepare disabled>准备任务</button><button class="btn btn-primary" data-start disabled>发送并开始</button></div>`;
    const instruction=body.querySelector("[data-instruction]"), search=body.querySelector("[data-search]");
    const remember=()=>write(draftKey,{instruction:instruction.value,layout_id:data?.layout_id,selection:selection?keyOf(selection.node,selection.actor):""});
    body.querySelector("[data-handoff-project]").onclick=async()=>{
      remember();
      const goal=instruction.value;
      el.close();
      await ProjectTasks.compose(ref.project_id,{title:ref.relative.split(/[\\/]/).at(-1),goal,inputs:[{relative:ref.relative}],metadata:{source:"file_handoff"},beforeSubmit:async()=>{
        if(curTask!==activeProject) throw new Error("项目已切换，已保留任务说明。请从当前项目重新打开文件任务。");
        await saveInput(ref);
      }});
    };
    body.querySelector("[data-handoff-workflow]").onclick=()=>instruction.focus();
    const invalidate=()=>{prepared=null;requestKey=crypto.randomUUID();remember();};
    const preferences=()=>read("rw-handoff-targets:"+ref.project_id+":"+data.layout_id) || {pins:[],recent:[]};
    function choose(node,actor,focus=true){ selection={node,actor}; invalidate(); drawSelection(); drawTargets(); if(focus) body.querySelector('[data-target][aria-pressed="true"]')?.focus(); }
    function drawSelection(){
      const host=body.querySelector("[data-selection]");
      const parent=body.querySelector("[data-parent]"), previous=parent.value;
      const eligible=selection?related.filter(item=>item.layout_id===data.layout_id && item.revision===data.revision && item.node_id===selection.node.id &&
        ["assistant_id","team_id","member_id"].every(k=>item.payload[k]===selection.actor[k])):[];
      parent.innerHTML='<option value="">新建节点任务</option>'+eligible.map(item=>`<option value="${h(item.id)}">补充：${h(item.payload.relative)} · ${h(statuses[item.status] || item.status)} · ${h(item.created_at)}</option>`).join("");
      if(eligible.some(item=>item.id===previous)) parent.value=previous;
      body.querySelectorAll("[data-prepare],[data-start]").forEach(b=>b.disabled=!selection);
      if(!selection){
        if(group.length>1){
          host.innerHTML=`<label>此执行者参与多个节点，请选择归属节点<select data-handoff-node><option value="">请选择节点…</option>${group.map((r,i)=>`<option value="${i}">${h(r.node.label+(r.actor.team_name?" · "+r.actor.team_name+" / "+r.actor.member_name:""))}</option>`).join("")}</select></label>`;
          host.querySelector("select").onchange=e=>{if(e.target.value!==""){const row=group[Number(e.target.value)];choose(row.node,row.actor);}};
        }else host.textContent="选择一个目标；团队或专家都需要归属到具体节点。";
        return;
      }
      const {node,actor}=selection;
      host.innerHTML=`<b>${h(node.label)}</b><p>${h(actor.team_name?actor.team_name+" → "+actor.member_name+"（"+actor.assistant_name+"）":actor.assistant_name)}</p><div class="handoff-muted">${node.dependencies.length?"前置节点："+h(node.dependencies.map(id=>data.nodes.find(n=>n.id===id)?.label || id).join("、")):"无前置依赖"} · ${node.review_required===false?"沿用节点自动交付配置":"生成产物后等待节点验收"}</div>
        ${actor.binding_source==="project_team"?'<div class="handoff-muted">沿用当前内置工作流的项目常驻团队配置</div>':""}
        ${view!=="assistant" && node.actors.length>1?`<label>执行专家<select data-actor-select aria-label="执行专家">${node.actors.map(a=>`<option value="${h(keyOf(node,a))}" ${keyOf(node,a)===keyOf(node,actor)?"selected":""}>${h((a.member_name?a.member_name+" · ":"")+a.assistant_name)}</option>`).join("")}</select></label>`:""}
        <button class="btn ghost" data-pin>${preferences().pins.includes(keyOf(node,actor))?"取消常用":"设为常用"}</button>`;
      const executor=host.querySelector("[data-actor-select]");
      if(executor) executor.onchange=e=>{
        const chosen=node.actors.find(a=>keyOf(node,a)===e.target.value);
        if(chosen){choose(node,chosen,false);host.querySelector("[data-actor-select]")?.focus();}
      };
      host.querySelector("[data-pin]").onclick=()=>{
        const prefs=preferences(), key=keyOf(node,actor);
        if(prefs.pins.includes(key)) prefs.pins=prefs.pins.filter(k=>k!==key);
        else if(prefs.pins.length<2) prefs.pins.push(key);
        else {error.textContent="最多固定两个常用目标，请先取消一个。";return;}
        write("rw-handoff-targets:"+ref.project_id+":"+data.layout_id,prefs);drawSelection();drawTargets();
      };
    }
    function drawTargets(){
      if(!data) return;
      const host=body.querySelector("[data-targets]"), q=search.value.toLowerCase(), prefs=preferences();
      const rows=[];
      for(const node of data.nodes){
        const list=view==="assistant"?node.actors:view==="team"?node.actors.filter(a=>a.team_id).filter((a,i,all)=>all.findIndex(b=>b.team_id===a.team_id)===i):node.actors.slice(0,1);
        if(view==="team" && !node.team_id && !list.length) continue;
        if(!list.length && view!=="assistant") rows.push({node,actor:null});
        for(const actor of list) rows.push({node,actor});
      }
      const valid=new Set(data.nodes.flatMap(n=>n.actors.map(a=>keyOf(n,a))));
      prefs.pins=prefs.pins.filter(k=>valid.has(k));prefs.recent=prefs.recent.filter(k=>valid.has(k));
      write("rw-handoff-targets:"+ref.project_id+":"+data.layout_id,prefs);
      const rank=r=>{
        if(!r.actor) return 3;
        const key=keyOf(r.node,r.actor), pin=prefs.pins.indexOf(key), recent=prefs.recent.indexOf(key);
        return pin>=0?pin/10:recent>=0?1+recent/10:2;
      };
      const matches=rows.filter(r=>JSON.stringify([r.node.label,r.actor]).toLowerCase().includes(q)).sort((a,b)=>rank(a)-rank(b));
      const groups=new Map();
      for(const row of matches){
        const key=view==="node" || !row.actor?row.node.id:view==="team"?row.actor.team_id:row.actor.assistant_id;
        if(!groups.has(key)) groups.set(key,[]);groups.get(key).push(row);
      }
      const filtered=[...groups.values()];
      const targetSelect=body.querySelector("[data-target-select]"), label={node:"选择节点",team:"选择团队",assistant:"选择专家"}[view];
      body.querySelector("[data-target-label]").textContent=label;
      targetSelect.setAttribute("aria-label",label);
      const groupId=row=>view==="node" || !row.actor?row.node.id:view==="team"?row.actor.team_id:row.actor.assistant_id;
      const groupTitle=row=>view==="node"?row.node.label:view==="team"?row.actor?.team_name || "团队配置待修复":row.actor.assistant_name;
      targetSelect.innerHTML=`<option value="">${label}…</option>`+[...groups].map(([key,options])=>`<option value="${h(key)}" ${options[0].actor?"":"disabled"}>${h(groupTitle(options[0]))}${options.length>1?" · "+options.length+" 个关联节点":""}${options[0].actor?"":" · "+h(options[0].node.disabled_reason)}</option>`).join("");
      targetSelect.disabled=!filtered.some(options=>options[0].actor);
      const selected=selection?groupId(selection):group[0]?groupId(group[0]):"";
      if(groups.has(selected)) targetSelect.value=selected;
      function selectGroup(options,fromSelect=false){
        group=options || [];
        if(group.length===1) choose(group[0].node,group[0].actor,!fromSelect);
        else {selection=null;invalidate();drawSelection();drawTargets();if(group.length>1) body.querySelector("[data-handoff-node]").focus();}
      }
      targetSelect.onchange=e=>selectGroup(groups.get(e.target.value),true);
      host.innerHTML=filtered.map((options,i)=>{
        const {node,actor}=options[0];
        const title=view==="team"?actor?.team_name || "团队配置待修复":view==="assistant"?actor.assistant_name:node.label;
        const key=actor?keyOf(node,actor):"";
        return `<button class="handoff-target" data-target="${i}" ${actor?"":"disabled"} aria-pressed="${!!selection && options.some(r=>r.actor && keyOf(r.node,r.actor)===keyOf(selection.node,selection.actor))}"><b>${h(title)}</b>${prefs.pins.includes(key)?" · 常用":prefs.recent.includes(key)?" · 最近使用":""}<small class="handoff-muted">${options.length>1?h(options.map(r=>r.node.label).join("、"))+" · 请选择归属节点":actor?h((view==="node"?"":node.label+" · ")+(actor.team_name?actor.team_name+" / "+actor.member_name+" / ":"")+actor.assistant_name):h(node.disabled_reason)}</small></button>`;
      }).join("") || (q?'<p class="handoff-muted">没有匹配的目标，请修改搜索词。</p>':`<div class="handoff-muted">当前没有可用的${view==="team"?"团队":view==="assistant"?"专家":"节点"}。${data.nodes.some(n=>n.disabled_reason)?`<details open><summary>查看节点配置原因</summary>${data.nodes.filter(n=>n.disabled_reason).map(n=>`<div>${h(n.label)}：${h(n.disabled_reason)}</div>`).join("")}</details>`:"该工作流只配置了直接执行的专家，未绑定团队。"}</div>`);
      host.querySelectorAll("[data-target]").forEach(button=>button.onclick=()=>{
        selectGroup(filtered[Number(button.dataset.target)]);
      });
      host.onkeydown=event=>{
        if(!["ArrowDown","ArrowUp","Home","End"].includes(event.key)) return;
        const buttons=[...host.querySelectorAll("button:not(:disabled)")], index=buttons.indexOf(document.activeElement);
        if(index<0) return;event.preventDefault();
        const next=event.key==="Home"?0:event.key==="End"?buttons.length-1:(index+(event.key==="ArrowDown"?1:-1)+buttons.length)%buttons.length;
        buttons[next].focus();
      };
    }
    async function refresh(){
      error.textContent=""; selection=null;group=[]; drawSelection();
      try{
        const [next,history]=await Promise.all([api("/api/handoff/targets?project_id="+encodeURIComponent(ref.project_id)),api("/api/handoff/history?project_id="+encodeURIComponent(ref.project_id)+"&summary=1")]);
        if(!el.open) return;
        const first=!data;data=next;related=history.items;invalidate();
        body.querySelector("[data-workflow]").textContent="工作流："+data.layout_name+" · 仅显示此工作流内的目标";
        const notice=body.querySelector("[data-configuration]"), config=data.configuration || {};
        notice.hidden=true;notice.innerHTML="";
        if(config.legacy_leaders>0){
          notice.hidden=false;
          notice.innerHTML=`<p>本项目的团队仍有 ${config.legacy_leaders} 个旧版负责人缺少专家绑定。修复后即可从当前工作流选择团队和专家。</p><button class="btn" data-repair>修复本项目团队绑定</button>`;
          notice.querySelector("[data-repair]").onclick=async event=>{
            event.target.disabled=true;error.textContent="";
            try{
              if(curTask!==activeProject) throw new Error("项目已切换，请从当前项目重新打开交接面板。");
              await api("/api/handoff/repair-configuration",{project_id:ref.project_id,layout_id:data.layout_id});
              await refresh();
            }catch(e){error.textContent=e.message;event.target.disabled=false;}
          };
        }else if(config.team_count===0 && !data.nodes.some(node=>node.actors.length)){
          notice.hidden=false;
          notice.innerHTML='<p>当前项目尚未创建工作流团队。打开本项目团队配置，初始化团队后返回刷新目标。</p><button class="btn" data-project-teams>打开本项目团队配置</button>';
          notice.querySelector("[data-project-teams]").onclick=()=>navigateConfiguration("team");
        }
        if(first && (preferred || saved.layout_id===data.layout_id)){
          for(const node of data.nodes) for(const actor of node.actors) if(keyOf(node,actor)===(preferred || saved.selection)) selection={node,actor};
        }
        drawTargets();drawSelection();
      }catch(e){error.textContent=e.message;body.querySelector("[data-targets]").textContent="目标暂不可用。任务说明已保留。";}
    }
    body.querySelectorAll("[data-view]").forEach(button=>button.onclick=()=>{view=button.dataset.view;group=[];body.querySelectorAll("[data-view]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));drawTargets();drawSelection();});
    body.querySelector("[data-refresh]").onclick=refresh;
    async function navigateConfiguration(tab){
      if(curTask!==activeProject){error.textContent="项目已切换，请从当前项目重新打开交接面板。";return;}
      if(flCurrentFile && flCurrentFile.text!==flCurrentFile.originalText && !await flSaveCurrent()){error.textContent="文件保存失败，已保留当前编辑。";return;}
      uiNavigate(()=>{el.close();if(tab==="settings") setSec="workflows";openTab(tab);});
    }
    body.querySelector("[data-configure]").onclick=()=>navigateConfiguration("settings");
    search.oninput=drawTargets;instruction.oninput=invalidate;
    body.querySelector("[data-parent]").onchange=invalidate;
    el.addEventListener("close",()=>{remember();preview(ref);},{once:true});
    async function submit(start){
      if(!selection) return;
      error.textContent="";body.querySelectorAll("button,input,textarea,select").forEach(b=>b.disabled=true);
      try{
        if(curTask!==activeProject) throw new Error("当前项目已切换。任务说明已保留，请从当前项目重新打开交接面板。");
        await saveInput(ref);
        if(!prepared){
          const {node,actor}=selection;
          prepared=(await api("/api/handoff/prepare",{...ref,layout_id:data.layout_id,revision:data.revision,node_id:node.id,
            assistant_id:actor.assistant_id,team_id:actor.team_id,member_id:actor.member_id,instruction:instruction.value,parent_id:body.querySelector("[data-parent]").value,request_key:requestKey})).handoff;
        }
        if(start) prepared=(await api("/api/handoff/start",{id:prepared.id})).handoff;
        const prefs=preferences(), key=keyOf(selection.node,selection.actor);
        prefs.recent=[key,...prefs.recent.filter(k=>k!==key)].slice(0,3);
        write("rw-handoff-targets:"+ref.project_id+":"+data.layout_id,prefs);
        const result=prepared;el.close();showRecord(result);
      }catch(e){error.textContent=e.message;body.querySelectorAll("button,input,textarea,select").forEach(b=>b.disabled=false);drawTargets();drawSelection();}
    }
    body.querySelector("[data-prepare]").onclick=()=>submit(false);
    body.querySelector("[data-start]").onclick=()=>submit(true);
    await refresh();
  }
  function showRecord(initial){
    if(initial.canonical_task_id && window.ProjectTasks) return ProjectTasks.openTask(initial.canonical_task_id,initial.project_id);
    const el=dialog("节点文件任务与回执"), body=el.querySelector("[data-body]"), error=el.querySelector("[data-error]");
    let record=initial, signature="", busy=false, feedback="";
    function draw(){
      const next=JSON.stringify(record); if(signature===next) return;signature=next;
      const p=record.payload, run=record.run, node=run?.nodes.at(-1), output=node?.checkpoint?.output;
      body.innerHTML=`<div class="handoff-context"><b>${h(p.node_label)}</b><div>${h(p.relative)}</div><div class="handoff-muted">${h(p.actor.team_name?p.actor.team_name+" / "+p.actor.member_name+" / ":"")}${h(p.actor.assistant_name)}</div></div><div class="handoff-row"><b>${h(statuses[record.status] || record.status)}</b><small>${h(record.id)}</small></div>
        ${record.waiting_reason?`<p role="status">${h(record.waiting_reason)}</p>`:""}<p>${h(p.instruction || "按节点任务说明处理此文件")}</p>
        ${p.parent_id?`<p class="handoff-muted">补充任务：${h(p.parent_id)} · 包含 ${(p.related_ids || []).length} 份已关联材料</p>`:""}
        ${run?.error?`<p class="handoff-error">${h(run.error)}</p>`:""}
        ${node?`<details open><summary>执行结果</summary><pre>${h(node.checkpoint.reply || node.error || "等待执行结果")}</pre></details>`:""}
        ${(output?.files || []).map((path,i)=>`<div class="handoff-row handoff-muted"><span>${h(path)}</span><button class="btn ghost" data-output="${i}">打开产物</button></div>`).join("")}
        ${run?`<details><summary>执行追踪</summary><pre>${h(JSON.stringify({workflow:run.layout.name,node_id:record.node_id,run_id:run.id,team_task_id:node?.checkpoint.team_task_id,agent_run_id:node?.checkpoint.agent_run_id,delivery_id:node?.checkpoint.delivery_id,input:run.settings.file_input},null,2))}</pre></details>`:""}
        ${record.status==="awaiting_review"?`<label>节点验收意见<textarea data-feedback placeholder="退回时请填写修改意见">${h(feedback)}</textarea></label>`:""}
        <div class="handoff-row handoff-actions">${!run?'<button class="btn btn-primary" data-action="start">开始此节点任务</button><button class="btn" data-action="reprepare">重新选择目标与文件版本</button>':""}
        ${record.status==="awaiting_review"?'<button class="btn btn-primary" data-action="approve">验收并交付节点</button><button class="btn" data-action="reject">退回修改</button>':""}
        ${["failed","blocked","interrupted"].includes(record.status)?'<button class="btn" data-action="resume">核对产物后重试</button>':""}
        ${run && !["succeeded","cancelled"].includes(record.status)?'<button class="btn ghost" data-action="cancel">停止本次任务</button>':""}</div>`;
      body.querySelector("[data-feedback]")?.addEventListener("input",e=>feedback=e.target.value);
      body.querySelectorAll("[data-output]").forEach(b=>b.onclick=()=>api("/api/conv/open",{path:output.files[Number(b.dataset.output)]}).catch(e=>error.textContent=e.message));
      body.querySelectorAll("[data-action]").forEach(b=>b.onclick=async()=>{
        if(busy) return;
        const action=b.dataset.action;
        if(action==="reprepare"){el.close();open({project_id:record.project_id,relative:p.relative});return;}
        busy=true;b.disabled=true;error.textContent="";
        try{
          if(action==="start") record=(await api("/api/handoff/start",{id:record.id})).handoff;
          else {
            await api("/api/workflow/execution/"+(["approve","reject"].includes(action)?"review":action),{id:run.id,node_id:node?.id,approved:action==="approve",feedback});
            record=(await api("/api/handoff/detail?id="+encodeURIComponent(record.id))).handoff;
          }
          signature="";draw();
        }catch(e){error.textContent=e.message;b.disabled=false;}finally{busy=false;}
      });
    }
    draw();
    el.addEventListener("close",()=>preview({project_id:record.project_id,relative:record.payload.relative}),{once:true});
    const timer=setInterval(async()=>{
      if(busy || !el.open || !record.run_id || ["succeeded","cancelled"].includes(record.status)) return;
      busy=true;
      try{const result=await api("/api/handoff/detail?id="+encodeURIComponent(record.id));if(el.open){record=result.handoff;draw();}}catch(e){error.textContent=e.message;}finally{busy=false;}
    },2000);
    el.addEventListener("close",()=>clearInterval(timer),{once:true});
  }
  async function history(ref){
    if(!ref?.project_id) return;
    const el=dialog("文件交接记录"), body=el.querySelector("[data-body]");
    body.textContent="读取交接记录…";
    try{
      const result=await api("/api/handoff/history?project_id="+encodeURIComponent(ref.project_id)+"&relative="+encodeURIComponent(ref.relative || "")+"&summary=1");
      if(!el.open) return;
      body.innerHTML=result.items.map((item,i)=>`<div class="handoff-history-item"><b>${h(item.payload.node_label)}</b> · ${h(statuses[item.status] || item.status)}<div class="handoff-muted">${h(item.payload.actor.assistant_name)} · ${h(fmtDate(item.created_at))}</div><button class="btn" data-record="${i}">查看任务与回执</button></div>`).join("") || '<p class="handoff-muted">这个文件还没有交接记录。</p>';
      body.querySelectorAll("[data-record]").forEach(b=>b.onclick=async()=>{
        try{const detail=await api("/api/handoff/detail?id="+encodeURIComponent(result.items[Number(b.dataset.record)].id));if(el.open){el.close();showRecord(detail.handoff);}}
        catch(e){el.querySelector("[data-error]").textContent=e.message;}
      });
    }catch(e){el.querySelector("[data-error]").textContent=e.message;}
  }
  async function preview(ref){
    const button=document.getElementById("flHandoffHistory"), pins=document.getElementById("flHandoffPins");
    if(!button || curTask!==ref.project_id || flSel!==ref.relative) return;
    button.textContent="交接记录";
    const request=++previewRequest;
    const stillCurrent=()=>request===previewRequest && button.isConnected && curTask===ref.project_id && flSel===ref.relative;
    const results=await Promise.allSettled([
      api("/api/handoff/history?project_id="+encodeURIComponent(ref.project_id)+"&relative="+encodeURIComponent(ref.relative)+"&limit=1&summary=1"),
      api("/api/handoff/targets?project_id="+encodeURIComponent(ref.project_id))
    ]);
    if(!stillCurrent()) return;
    if(results[0].status==="fulfilled"){
      const item=results[0].value.items[0];
      if(item){button.textContent=item.payload.node_label+" · "+(statuses[item.status] || item.status);button.title="最近交接："+item.payload.actor.assistant_name+"；点击查看记录与回执";}
    }
    if(pins && results[1].status==="fulfilled"){
      const data=results[1].value, prefs=read("rw-handoff-targets:"+ref.project_id+":"+data.layout_id) || {pins:[]};
      const choices=data.nodes.flatMap(node=>node.actors.map(actor=>({node,actor,key:keyOf(node,actor)}))).filter(x=>prefs.pins.includes(x.key)).slice(0,2);
      const signature=JSON.stringify([ref.project_id,ref.relative,choices]);
      if(pins.dataset.signature===signature) return;
      const restore=pins.contains(document.activeElement);
      pins.innerHTML="";pins.dataset.signature=signature;
      choices.forEach(choice=>{
        const b=document.createElement("button");b.className="btn btn-sm";b.textContent=choice.node.label+" · "+choice.actor.assistant_name;
        b.style.cssText="max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        b.title="交给此常用目标（先查看任务）";b.onclick=()=>open(ref,choice.key);pins.append(b);
      });
      if(restore) (pins.querySelector("button") || document.getElementById("flHand"))?.focus();
    }else if(pins){
      pins.innerHTML="";delete pins.dataset.signature;
    }
  }
  setInterval(()=>{
    const button=document.getElementById("flHand");
    if(!active && document.visibilityState!=="hidden" && flSel && button?.disabled===false && button.getClientRects().length) preview({project_id:curTask,relative:flSel});
  },6000);
  return {open,history,focused,preview};
})();
