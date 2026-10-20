/* Project-bound workflow diagrams and the existing research step panels. */
(() => {
  "use strict";
  const root = document.createElement("style");
  root.textContent = `
    .wfl-choice{display:flex;flex-direction:column;gap:5px;width:100%;text-align:left;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:11px;margin:0 0 8px;cursor:pointer}
    .wfl-choice.on{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,var(--panel))}
    .wfl-choice b{font-size:13px}.wfl-choice small{color:var(--muted);font-size:11px;line-height:1.5}
    .wfl-graph{border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow:hidden}
    .wfl-graph-scroll{overflow:auto;background-image:radial-gradient(var(--line) .8px,transparent .8px);background-size:16px 16px}
    .wfl-graph-svg{display:block}.wfl-edge{fill:none;stroke:var(--muted);stroke-width:1.4;opacity:.65}
    .wfl-node{cursor:pointer;outline:none}.wfl-node rect{fill:var(--panel);stroke:var(--line);stroke-width:1.4}
    .wfl-node text{fill:var(--fg);font-size:12px;font-family:inherit}.wfl-node .wfl-node-status{font-size:10px;fill:var(--muted)}
    .wfl-node circle{fill:var(--muted)}.wfl-node.running circle,.wfl-node.ready circle{fill:var(--accent)}.wfl-node.done circle{fill:#388c68}.wfl-node.failed circle{fill:var(--err)}
    .wfl-node.selected rect,.wfl-node:focus rect{stroke:var(--accent);stroke-width:2.4}.wfl-node.running rect{stroke:var(--accent)}
    .wfl-note{padding:7px 13px;border-top:1px solid var(--line);font-size:11px;color:var(--muted);line-height:1.5}
    .wfl-error{color:var(--err);font-size:12px;white-space:pre-wrap}.wfl-editor-grid{display:grid;grid-template-columns:185px minmax(0,1fr);gap:14px;margin-top:12px}
    .wfl-editor .field{display:block;margin:10px 0}.wfl-editor input,.wfl-editor textarea,.wfl-editor select{max-width:100%;width:100%;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:7px;font:inherit}
    .wfl-editor .field>span{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}.wfl-editor-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}
    .wfl-editor-actions select{width:auto;min-width:140px}.wfl-node-table{overflow:auto}.wfl-node-table table{width:100%;font-size:12px}.wfl-node-table td{padding:6px;vertical-align:top}.wfl-node-table td:first-child{min-width:130px}.wfl-node-table td:nth-child(2){min-width:145px}.wfl-node-table td:nth-child(3){min-width:130px}.wfl-node-table code{display:block;font-size:10px;color:var(--muted);margin:4px 0}.wfl-node-table .btn{padding:4px 7px;font-size:11px}
    .wfl-json{min-height:180px;font-family:Consolas,monospace!important;font-size:12px!important;margin:8px 0}.wfl-editor details{margin:10px 0}
    .wfl-project-dialog{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:16px;padding:18px;width:min(700px,calc(100vw - 32px));max-height:90vh;overflow:auto}
    .wfl-project-dialog::backdrop{background:#0009}.wfl-project-dialog .proj-new{position:static;margin:10px 0 0}.wfl-dialog-head{display:flex;justify-content:space-between;align-items:center}.wfl-project-dialog .proj-new h3{display:none}.wfl-project-notice{color:var(--accent);font-size:13px;margin:8px 0}
    @media(max-width:1000px){.wfl-editor-grid{grid-template-columns:1fr}.wfl-editor-list{display:flex;gap:8px;overflow:auto}.wfl-editor-list .wfl-choice{min-width:160px}}
    @media(max-width:700px){.wfl-project-dialog .row{grid-template-columns:1fr}}
  `;
  document.head.appendChild(root);
  const labels = {pending:"未开始",ready:"可开始",waiting:"等待依赖",running:"运行中",done:"已完成",failed:"失败",cancelled:"已停止",blocked:"被阻塞"};
  let settingsData = null, draft = null, projectNotice = "";
  const byId = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const stageLabel = step => String(STAGE[step] || step).replace(/^\S+\s+/, "");

  function graph(data, interactive = true){
    const groups = new Map(), positions = new Map();
    for(const node of data.nodes || []){
      const level = Number.isFinite(node.level) ? node.level : 0;
      const row = groups.get(level) || 0;
      positions.set(node.id, {x:18 + level * 190, y:16 + row * 86});
      groups.set(level, row + 1);
    }
    const width = Math.max(220, ...[...positions.values()].map(p=>p.x + 182));
    const height = Math.max(100, ...[...positions.values()].map(p=>p.y + 80));
    const paths = (data.edges || []).map(edge=>{
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if(!a || !b) return "";
      const x = a.x + 164, y = a.y + 31, end = b.x - 4, ey = b.y + 31;
      return `<path class="wfl-edge" d="M ${x} ${y} C ${x+14} ${y}, ${end-14} ${ey}, ${end} ${ey}" marker-end="url(#wfl-arrow)"/>`;
    }).join("");
    const nodes = (data.nodes || []).map(node=>{
      const p = positions.get(node.id), status = labels[node.status] || labels.pending;
      const label = node.label.length > 12 ? node.label.slice(0,11) + "…" : node.label;
      return `<g class="wfl-node ${esc(node.status || "pending")} ${node.id===data.selected_node?"selected":""}" transform="translate(${p.x},${p.y})" ${interactive?`data-wfl-node="${esc(node.id)}" role="button" tabindex="0" aria-label="${esc(node.label + "：" + status)}"`:""}>
        <title>${esc(node.label + " · " + status + "\n" + (node.summary || "") + (node.source ? "\n来源：" + node.source + " / " + node.record_id : "") + (node.updated_at ? "\n" + fmtDate(node.updated_at) : ""))}</title>
        <rect width="164" height="62" rx="9"/><circle cx="13" cy="18" r="3.5"/>
        <text x="24" y="22">${esc(label)}</text><text class="wfl-node-status" x="13" y="45">${esc(status)}</text></g>`;
    }).join("");
    return `<div class="wfl-graph-scroll"><svg class="wfl-graph-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="工作流节点及依赖状态图"><defs><marker id="wfl-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 z" fill="var(--muted)"/></marker></defs>${paths}${nodes}</svg></div>`;
  }

  if(!SET_SECS.some(section=>section[0]==="workflows")) SET_SECS.push(["workflows","🔀","工作流"]);
  const originalSettings = renderSettings;
  renderSettings = function(m){
    originalSettings(m);
    if(setSec !== "workflows" || !S.loaded) return;
    const body = byId("set-body"); if(!body) return;
    if(settingsData){ drawEditor(body); return; }
    body.innerHTML = '<div class="card"><h3>工作流设置</h3><p class="hint">正在读取工作流定义…</p></div>';
    api("/api/workflow/layouts").then(result=>{
      settingsData=result;
      if(route()==="settings" && setSec==="workflows" && body.isConnected) drawEditor(body);
    }).catch(error=>{ if(body.isConnected) body.innerHTML=`<p class="wfl-error">${esc(error.message)}</p>`; });
  };

  function freshDraft(){
    return {id:"",name:"新工作流",description:"",nodes:[{id:"idea",step:"idea",label:stageLabel("idea")}],edges:[],order:["idea"]};
  }
  function draftNodes(){
    const nodes = new Map(draft.nodes.map(node=>[node.id,node]));
    return draft.order.map(id=>nodes.get(id)).filter(Boolean);
  }
  function captureEditor(body){
    draft.name = body.querySelector("[data-wfl-name]").value.trim();
    draft.description = body.querySelector("[data-wfl-description]").value.trim();
    const nodes=[],edges=[];
    body.querySelectorAll("[data-wfl-row]").forEach(row=>{
      const id=row.dataset.wflRow;
      nodes.push({id,step:row.querySelector("[data-wfl-step]").value,label:row.querySelector("[data-wfl-label]").value.trim()});
      const sources=row.querySelector("[data-wfl-parents]").value.split(/[,，\s]+/).filter(Boolean);
      sources.forEach(source=>edges.push({source,target:id}));
    });
    draft.nodes=nodes; draft.edges=edges; draft.order=nodes.map(node=>node.id);
  }
  function previewDraft(body){
    const target=body.querySelector("[data-wfl-preview]"); if(!target) return;
    try{
      const nodes=clone(draftNodes()), levels=new Map();
      if(!nodes.length) throw new Error("请添加至少一个步骤");
      for(const node of nodes){
        const parents=draft.edges.filter(edge=>edge.target===node.id).map(edge=>edge.source);
        if(parents.some(id=>!levels.has(id))) throw new Error("依赖须指向排在当前节点之前的有效节点；请调整顺序或前置节点");
        node.level=Math.max(0,...parents.map(id=>levels.get(id)+1)); node.status="pending"; levels.set(node.id,node.level);
      }
      target.innerHTML=graph({nodes,edges:draft.edges},false);
    }catch(error){ target.innerHTML=`<p class="wfl-error">${esc(error.message)}</p>`; }
    const json=body.querySelector("[data-wfl-json]");
    if(json && document.activeElement !== json) json.value=JSON.stringify({nodes:draft.nodes,edges:draft.edges,order:draft.order},null,2);
  }
  function drawEditor(body, notice=""){
    if(!draft) draft=clone(settingsData.layouts[0] || freshDraft());
    const stages=settingsData.stages || [];
    body.innerHTML=`<div class="card wfl-editor"><h3>工作流定义</h3><p class="hint">编辑节点、前置依赖和顺序，保存后可在工作流页选择。仅支持已有功能步骤；保存时校验循环依赖和非法步骤。</p>
      <div class="wfl-editor-actions"><button class="btn" data-wfl-new>新建工作流</button><button class="btn ghost" data-wfl-copy>复制当前</button><span class="hint" role="status" data-wfl-message>${esc(notice)}</span></div>
      <div class="wfl-editor-grid"><aside class="wfl-editor-list">${settingsData.layouts.map(layout=>`<button class="wfl-choice ${layout.id===draft.id?"on":""}" data-wfl-edit="${esc(layout.id)}"><b>${esc(layout.name)}</b><small>${layout.nodes.length} 个节点</small></button>`).join("")}</aside>
      <section><label class="field"><span>工作流名称</span><input data-wfl-name maxlength="120" value="${esc(draft.name)}"></label><label class="field"><span>说明</span><textarea data-wfl-description rows="2">${esc(draft.description || "")}</textarea></label>
      <div class="wfl-node-table"><table><thead><tr><th>功能步骤 / 节点 ID</th><th>节点名称</th><th>前置节点 ID（逗号分隔）</th><th>顺序</th></tr></thead><tbody>${draftNodes().map((node,index)=>`<tr data-wfl-row="${esc(node.id)}"><td><select data-wfl-step aria-label="功能步骤">${stages.map(stage=>`<option value="${esc(stage.id)}" ${stage.id===node.step?"selected":""}>${esc(stage.name)}</option>`).join("")}</select><code>${esc(node.id)}</code></td><td><input data-wfl-label aria-label="节点名称" value="${esc(node.label)}" maxlength="120"></td><td><input data-wfl-parents aria-label="前置节点" value="${esc(draft.edges.filter(edge=>edge.target===node.id).map(edge=>edge.source).join(", "))}" placeholder="无前置依赖"></td><td><button class="btn ghost" data-wfl-up="${index}" ${index===0?"disabled":""} aria-label="上移节点">↑</button><button class="btn ghost" data-wfl-down="${index}" ${index===draft.order.length-1?"disabled":""} aria-label="下移节点">↓</button><button class="btn ghost" data-wfl-remove="${esc(node.id)}" aria-label="移除节点">移除</button></td></tr>`).join("")}</tbody></table></div>
      <div class="wfl-editor-actions"><select data-wfl-add-step aria-label="添加步骤">${stages.filter(stage=>!draft.nodes.some(node=>node.step===stage.id)).map(stage=>`<option value="${esc(stage.id)}">${esc(stage.name)}</option>`).join("")}</select><button class="btn ghost" data-wfl-add ${draft.nodes.length>=stages.length?"disabled":""}>添加步骤</button><button class="btn ghost" data-wfl-chain>按当前顺序串联</button></div>
      <details><summary>高级 JSON（nodes / edges / order）</summary><textarea data-wfl-json class="wfl-json" spellcheck="false" aria-label="工作流 JSON"></textarea><button class="btn ghost" data-wfl-apply-json>应用 JSON 到编辑器</button></details>
      <div class="wfl-graph"><div class="wfl-note">组织预览（不是运行状态）</div><div data-wfl-preview></div></div>
      <div class="wfl-editor-actions"><button class="btn" data-wfl-save>保存工作流</button>${draft.id?'<button class="btn ghost" data-wfl-delete>删除工作流</button>':""}</div></section></div></div>`;
    const message=text=>{ const el=body.querySelector("[data-wfl-message]"); if(el) el.textContent=text; };
    body.querySelector("[data-wfl-new]").onclick=()=>{ draft=freshDraft(); drawEditor(body); };
    body.querySelector("[data-wfl-copy]").onclick=()=>{ captureEditor(body); draft.id=""; draft.name+="（副本）"; drawEditor(body); };
    body.querySelectorAll("[data-wfl-edit]").forEach(button=>button.onclick=()=>{ draft=clone(settingsData.layouts.find(layout=>layout.id===button.dataset.wflEdit)); drawEditor(body); });
    body.querySelectorAll("[data-wfl-row] input,[data-wfl-row] select").forEach(input=>input.onchange=()=>{ captureEditor(body); previewDraft(body); });
    body.querySelectorAll("[data-wfl-up],[data-wfl-down]").forEach(button=>button.onclick=()=>{
      captureEditor(body);
      const index=Number(button.dataset.wflUp ?? button.dataset.wflDown), next=index+(button.hasAttribute("data-wfl-up")?-1:1);
      [draft.order[index],draft.order[next]]=[draft.order[next],draft.order[index]]; drawEditor(body);
    });
    body.querySelectorAll("[data-wfl-remove]").forEach(button=>button.onclick=()=>{
      captureEditor(body); const id=button.dataset.wflRemove;
      draft.nodes=draft.nodes.filter(node=>node.id!==id); draft.order=draft.order.filter(nid=>nid!==id); draft.edges=draft.edges.filter(edge=>edge.source!==id && edge.target!==id); drawEditor(body);
    });
    body.querySelector("[data-wfl-add]").onclick=()=>{
      const step=body.querySelector("[data-wfl-add-step]").value; if(!step) return;
      captureEditor(body); let id=step;
      while(draft.nodes.some(node=>node.id===id)) id+="_";
      draft.nodes.push({id,step,label:stageLabel(step)}); draft.order.push(id); drawEditor(body);
    };
    body.querySelector("[data-wfl-chain]").onclick=()=>{ captureEditor(body); draft.edges=draft.order.slice(1).map((id,index)=>({source:draft.order[index],target:id})); drawEditor(body); };
    body.querySelector("[data-wfl-apply-json]").onclick=()=>{
      try{
        const value=JSON.parse(body.querySelector("[data-wfl-json]").value);
        if(!Array.isArray(value.nodes) || !Array.isArray(value.edges) || !Array.isArray(value.order)) throw new Error("必须包含 nodes、edges、order 数组");
        if(value.nodes.some(node=>!node || typeof node.id!=="string" || typeof node.step!=="string" || typeof node.label!=="string")) throw new Error("节点需包含 id、step 和 label 字符串");
        if(value.edges.some(edge=>!edge || typeof edge.source!=="string" || typeof edge.target!=="string")) throw new Error("连线需包含 source 和 target 字符串");
        if(value.order.length!==value.nodes.length || new Set(value.order).size!==value.nodes.length || value.order.some(id=>!value.nodes.some(node=>node.id===id))) throw new Error("order 必须包含每个节点 ID 一次");
        draft.name=body.querySelector("[data-wfl-name]").value.trim(); draft.description=body.querySelector("[data-wfl-description]").value.trim();
        Object.assign(draft,{nodes:value.nodes,edges:value.edges,order:value.order}); drawEditor(body,"JSON 已应用；保存后生效");
      }catch(error){ message("JSON 无效："+error.message); }
    };
    body.querySelector("[data-wfl-save]").onclick=async event=>{
      captureEditor(body); event.target.disabled=true;
      try{
        const saved=await api("/api/workflow/layouts/save",draft); draft=clone(saved.layout);
        settingsData=await api("/api/workflow/layouts"); if(body.isConnected) drawEditor(body,"已保存");
      }catch(error){ message("保存失败："+error.message); event.target.disabled=false; }
    };
    const del=body.querySelector("[data-wfl-delete]");
    if(del) del.onclick=async()=>{
      if(!await uiConfirm("删除工作流「"+draft.name+"」？绑定它的项目会退回剩余工作流，项目数据不删除。")) return;
      del.disabled=true;
      try{ await api("/api/workflow/layouts/delete",{id:draft.id}); settingsData=await api("/api/workflow/layouts"); draft=null; if(body.isConnected) drawEditor(body,"工作流已删除"); }
      catch(error){ message("删除失败："+error.message); del.disabled=false; }
    };
    previewDraft(body);
  }

  const originalProjects = renderTasks;
  renderTasks = function(m){
    originalProjects(m);
    if(!S.loaded) return;
    const form=m.querySelector(".proj-new"), title=byId("t-title"), create=byId("t-go");
    if(!form || !title || !create) return;
    const message=document.createElement("div"); message.className="wfl-project-notice"; message.setAttribute("role","status"); form.appendChild(message);
    if(projectNotice){ message.textContent=projectNotice; projectNotice=""; }
    title.required=true; title.maxLength=300;
    if(tDir){ byId("t-dir").textContent=tDir; byId("t-dir-box").classList.add("set"); }
    let add=m.querySelector(".proj-add");
    const button=document.createElement("button"); button.type="button"; button.className="proj-add"; button.textContent="＋ 新建项目";
    if(add) add.replaceWith(button); else (m.querySelector(".pj-sec") || m).appendChild(button);
    let dialog=null;
    button.onclick=()=>{
      const placeholder=document.createComment("project form position"); form.before(placeholder);
      dialog=document.createElement("dialog"); dialog.className="wfl-project-dialog"; dialog.setAttribute("aria-label","新建项目");
      dialog.innerHTML='<div class="wfl-dialog-head"><strong>新建项目</strong><button class="btn ghost" type="button" data-wfl-close>关闭</button></div>';
      document.body.appendChild(dialog); dialog.appendChild(form);
      const activeDialog=dialog;
      activeDialog.addEventListener("close",()=>{ placeholder.replaceWith(form); activeDialog.remove(); if(dialog===activeDialog) dialog=null; if(button.isConnected) button.focus(); },{once:true});
      activeDialog.querySelector("[data-wfl-close]").onclick=()=>activeDialog.close();
      activeDialog.showModal(); title.focus();
    };
    create.onclick=async()=>{
      if(!title.value.trim()){ title.setCustomValidity("请填写项目标题"); title.reportValidity(); return; }
      title.setCustomValidity(""); create.disabled=true; message.textContent="正在创建项目…";
      try{
        const result=await api("/api/task/create",{title:title.value.trim(),question:byId("t-q").value.trim(),material_system:byId("t-kw").value.trim(),base_dir:tDir});
        const tid=result.task_id || result.id; if(!tid) throw new Error("服务端未返回项目 ID");
        curTask=tid; localStorage.setItem("rw-task",tid); tDir="";
        projectNotice="项目已创建"+(result.dirs?.length?"，已建立 "+result.dirs.length+" 个项目目录":"");
        message.textContent=projectNotice;
        if(dialog) dialog.close(); await refreshState(false); render();
      }catch(error){ message.textContent="创建失败："+error.message; create.disabled=false; }
    };
    title.oninput=()=>title.setCustomValidity("");
    title.onkeydown=event=>{ if(event.key==="Enter"){ event.preventDefault(); create.click(); } };
  };
})();
