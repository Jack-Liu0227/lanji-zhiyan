/* Project-bound workflow diagrams and the existing research step panels. */
(() => {
  "use strict";
  const root = document.createElement("style");
  root.textContent = `
    .wfl-choice{display:flex;flex-direction:column;gap:5px;width:100%;text-align:left;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:11px;margin:0 0 8px;cursor:pointer}
    .wfl-choice.on{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,var(--panel))}
    .wfl-choice b{font-size:13px}.wfl-choice small{color:var(--muted);font-size:11px;line-height:1.5}
    .wfl-graph{border:1px solid var(--line);border-radius:12px;background:var(--bg);overflow:hidden}
    .wfl-graph-scroll{overflow:auto;background:var(--bg);background-image:radial-gradient(color-mix(in srgb,var(--line) 85%,transparent) 1px,transparent 1px);background-size:18px 18px}
    .wfl-graph-svg{display:block;margin:0 auto}
    .wfl-edge{fill:none;stroke:color-mix(in srgb,var(--muted) 45%,var(--line));stroke-width:1.6}
    .wfl-edge.is-done{stroke:color-mix(in srgb,#10b981 55%,var(--line))}
    .wfl-node{cursor:pointer;outline:none;font-family:inherit}
    .wfl-row{display:flex;flex-direction:column;justify-content:space-between;gap:6px;height:100%;padding:9px 11px;position:relative;overflow:hidden;box-sizing:border-box;border-radius:11px;background:var(--panel);border:1.5px solid var(--line);color:var(--fg);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.04);transition:border-color .15s,box-shadow .15s,transform .12s}
    .wfl-row:hover{border-color:color-mix(in srgb,var(--accent) 55%,var(--line));box-shadow:0 4px 14px rgba(0,0,0,.08)}
    .wfl-card-top{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:6px;min-width:0}
    .wfl-card-title-wrap{display:flex;align-items:flex-start;gap:6px;min-width:0;flex:1}
    .wfl-card-dot{width:7px;height:7px;border-radius:50%;margin-top:4px;flex-shrink:0;background:var(--muted)}
    .wfl-row-title{font-size:11.5px;font-weight:650;line-height:1.32;color:var(--fg);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
    .wfl-card-badges{display:inline-flex;align-items:center;justify-content:flex-end;gap:4px;width:100%;font-size:10px;font-variant-numeric:tabular-nums}
    .wfl-bot-badge{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:5px;font-size:9px;color:#fff;background:#7c3aed;flex-shrink:0}
    .wfl-bot-badge.is-join{background:#10b981}
    .wfl-bot-badge.is-check{background:#f59e0b}
    .wfl-row-no{font-size:10px;font-weight:600;color:var(--muted)}
    .wfl-row-pct{font-size:10px;font-weight:700;color:var(--fg)}
    .wfl-card-summary{display:flex;align-items:center;justify-content:space-between;gap:5px;padding:4px 7px;border-radius:6px;background:color-mix(in srgb,var(--panel2) 85%,var(--bg));color:var(--muted);font-size:10px;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .wfl-card-summary-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
    .wfl-row-pill{flex:none;font-size:9.5px;padding:1px 5px;border-radius:4px;white-space:nowrap;color:var(--muted);background:color-mix(in srgb,var(--muted) 10%,transparent)}
    .wfl-node-chips{display:inline-flex;align-items:center;gap:3px;flex-shrink:0}
    .wfl-chip{display:inline-flex;align-items:center;gap:2px;font-size:9px;padding:1px 5px;border-radius:4px;background:color-mix(in srgb,var(--accent) 12%,var(--panel));color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 26%,transparent);white-space:nowrap;max-width:96px;overflow:hidden;text-overflow:ellipsis}
    .wfl-node.done .wfl-card-dot,.wfl-node.delivered .wfl-card-dot,.wfl-node.succeeded .wfl-card-dot{background:#10b981}
    .wfl-node.running .wfl-card-dot,.wfl-node.ready .wfl-card-dot{background:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.2)}
    .wfl-node.awaiting_review .wfl-card-dot{background:#f59e0b;box-shadow:0 0 0 3px rgba(245,158,11,.22)}
    .wfl-node.failed .wfl-card-dot{background:var(--err)}
    .wfl-node.done .wfl-row,.wfl-node.delivered .wfl-row,.wfl-node.succeeded .wfl-row{border:2px solid #10b981}
    .wfl-node.delivered .wfl-row-pct,.wfl-node.succeeded .wfl-row-pct,.wfl-node.done .wfl-row-pct{color:var(--success,#059669)}
    .wfl-node.failed .wfl-row{border:2px solid var(--err);background:color-mix(in srgb,var(--err) 6%,var(--panel))}
    .wfl-node.failed .wfl-row-pct,.wfl-node.failed .wfl-card-summary-text{color:var(--err)}
    .wfl-node.interrupted .wfl-row{border-color:var(--warn,#d97706)}
    .wfl-node.awaiting_review .wfl-row{border-color:#f59e0b;background:color-mix(in srgb,#f59e0b 5%,var(--panel));box-shadow:0 3px 12px rgba(245,158,11,.14)}
    .wfl-node.awaiting_review .wfl-row-pct{color:#d97706}
    .wfl-node.running .wfl-row{border-color:#3b82f6;background:color-mix(in srgb,#3b82f6 6%,var(--panel));box-shadow:0 3px 14px rgba(59,130,246,.16)}
    .wfl-node.running .wfl-row-pct{color:#3b82f6}
    .wfl-edge-artifact{font-size:9.5px;fill:var(--muted)}
    .wfl-node.selected .wfl-row,.wfl-node:focus-visible .wfl-row{box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 38%,transparent)}
    .wfl-note{padding:7px 13px;border-top:1px solid var(--line);font-size:11px;color:var(--muted);line-height:1.5}
    .wfl-error{color:var(--err);font-size:12px;white-space:pre-wrap}.wfl-editor-grid{display:grid;grid-template-columns:var(--wfl-editor-list-w,185px) minmax(0,1fr);gap:14px;margin-top:12px;position:relative}
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
  const labels = {config:"已配置",awaiting_review:"待人工审查",queued:"已准备",pending:"待开始",ready:"可开始",waiting:"等待依赖",running:"运行中",done:"执行完成",succeeded:"待验收",delivered:"已交付",stale:"需重新确认",failed:"失败",cancelled:"已停止",blocked:"被阻塞"};
  let settingsData = null, draft = null, projectNotice = "", settingsRequest = 0;
  const byId = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const stageLabel = step => settingsData?.stages.find(stage=>stage.id===step)?.name || step;

  function computeHierarchicalStepNumbers(nodes = [], edges = []){
    if(!nodes || !nodes.length) return new Map();
    const inDeg = new Map(), adj = new Map();
    nodes.forEach(n => { inDeg.set(n.id, 0); adj.set(n.id, []); });
    (edges || []).forEach(e => {
      if(inDeg.has(e.target) && adj.has(e.source)){
        inDeg.set(e.target, inDeg.get(e.target) + 1);
        adj.get(e.source).push(e.target);
      }
    });

    const depth = new Map();
    const hasExplicitLevel = nodes.some(n => Number.isFinite(n.level));
    if(hasExplicitLevel){
      nodes.forEach(n => {
        if(Number.isFinite(n.level)) depth.set(n.id, n.level);
      });
    }

    if(depth.size < nodes.length){
      const q = [];
      nodes.forEach(n => {
        if(!depth.has(n.id) && inDeg.get(n.id) === 0){
          depth.set(n.id, 0);
          q.push(n.id);
        }
      });
      const remInDeg = new Map(inDeg);
      while(q.length > 0){
        const u = q.shift();
        const d = depth.get(u) || 0;
        for(const v of adj.get(u) || []){
          depth.set(v, Math.max(depth.get(v) || 0, d + 1));
          remInDeg.set(v, (remInDeg.get(v) || 1) - 1);
          if(remInDeg.get(v) === 0) q.push(v);
        }
      }
      nodes.forEach((n, idx) => {
        if(!depth.has(n.id)) depth.set(n.id, idx);
      });
    }

    const levelMap = new Map();
    nodes.forEach(n => {
      const lvl = depth.get(n.id) ?? 0;
      if(!levelMap.has(lvl)) levelMap.set(lvl, []);
      levelMap.get(lvl).push(n);
    });

    const sortedLevels = [...levelMap.keys()].sort((a, b) => a - b);
    const result = new Map();

    sortedLevels.forEach((lvl, majorIdx) => {
      const group = levelMap.get(lvl) || [];
      const majorNo = majorIdx + 1;
      const isParallel = group.length > 1;

      group.forEach((node, subIdx) => {
        const minorNo = subIdx + 1;
        const stepNum = isParallel ? `${majorNo}.${minorNo}` : `${majorNo}`;
        const stepLabel = `Step ${stepNum}`;
        const badgeLabel = `#${stepNum}`;

        const raw = String(node.label || node.title || node.id || "").trim();
        const cleanTitle = raw.replace(/^Step\s*\d+(\.\d+)?\s*[:：]\s*/i, "");
        const formattedTitle = `${stepLabel}: ${cleanTitle}`;

        result.set(node.id, {
          id: node.id,
          majorNo,
          minorNo,
          isParallel,
          stepNum,
          stepLabel,
          badgeLabel,
          cleanTitle,
          formattedTitle,
          waveIndex: majorIdx,
          waveTotal: group.length,
        });
      });
    });

    return result;
  }

  function graph(data, interactive = true, rowWidth = 680){
    const GAP_X = 12, PAD_X = 12, H = 100, LEVEL_STEP = 136, CARD_W = 236, MIN_CARD_W = 176;
    const stepMap = computeHierarchicalStepNumbers(data.nodes || [], data.edges || []);
    const lanes = new Map();
    for(const node of data.nodes || []){
      const level = Number.isFinite(node.level) ? node.level : (stepMap.get(node.id)?.waveIndex ?? 0);
      if(!lanes.has(level)) lanes.set(level, []);
      lanes.get(level).push(node);
    }
    const maxCols = Math.max(1, ...[...lanes.values()].map(lane => lane.length));
    const minWidth = maxCols * MIN_CARD_W + (maxCols - 1) * GAP_X + PAD_X * 2;
    const width = Math.max(Math.round(rowWidth) || 520, minWidth);
    const positions = new Map();
    for(const [level, lane] of lanes){
      const availW = width - PAD_X * 2;
      const w = Math.min(CARD_W, Math.floor((availW - (lane.length - 1) * GAP_X) / lane.length));
      const rowTotalW = lane.length * w + (lane.length - 1) * GAP_X;
      const startX = Math.round((width - rowTotalW) / 2);
      lane.forEach((node, index) => positions.set(node.id, {x: startX + index * (w + GAP_X), y: 18 + level * LEVEL_STEP, w}));
    }
    const height = Math.max(160, ...[...positions.values()].map(p => p.y + H + 22));
    const statusById = new Map((data.nodes || []).map(n => [n.id, n.status || "pending"]));
    const paths = (data.edges || []).map(edge => {
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if(!a || !b) return "";
      const x = Math.round(a.x + a.w / 2), y = a.y + H, end = Math.round(b.x + b.w / 2), ey = b.y - 3;
      const midY = Math.round((y + ey) / 2);
      const isDone = ["done", "delivered", "succeeded"].includes(statusById.get(edge.source));
      const line = `<path class="wfl-edge ${isDone ? "is-done" : ""}" d="M ${x} ${y} C ${x} ${midY}, ${end} ${midY}, ${end} ${ey}" marker-end="url(#wfl-arrow)"/>`;
      const tag = edge.artifact ? `<text class="wfl-edge-artifact" x="${Math.round((x + end) / 2)}" y="${midY - 3}" text-anchor="middle">📎 ${esc(edge.artifact)}</text>` : "";
      return line + tag;
    }).join("");
    const pctOf = {done: "✓ 100% 执行完成", delivered: "✓ 100% 已交付", succeeded: "✓ 100% 执行完成", failed: "✗ 失败", awaiting_review: "待核查", running: "执行中", ready: "可开始", queued: "排队中", pending: "未开始", cancelled: "已停止", interrupted: "已中断", blocked: "等待处理", waiting: "等待前置交付"};
    const inDeg = new Map(), outDeg = new Map();
    for(const e of data.edges || []){
      outDeg.set(e.source, (outDeg.get(e.source) || 0) + 1);
      inDeg.set(e.target, (inDeg.get(e.target) || 0) + 1);
    }
    const nodes = (data.nodes || []).map((node, index) => {
      const p = positions.get(node.id);
      const stepInfo = stepMap.get(node.id);
      const key = node.status || "pending";
      const status = labels[key] || labels.pending;
      const sub = node.owner_name || (node.node_kind === "tool" ? "🔧 " + (node.tool_name || "固定工具") : node.node_kind === "workflow" ? "🔀 子工作流" : "智能匹配专家");
      const pct = key==='running'&&Number.isFinite(node.progress_percent)?Math.max(0,Math.min(100,node.progress_percent))+'% 执行中':pctOf[key]||labels[key]||'未开始';
      const rawSummary = node.summary_text ? String(node.summary_text).replace(/\s+/g, " ").trim() : "";
      const summary = rawSummary ? rawSummary.slice(0, 34) + (rawSummary.length > 34 ? "…" : "") : (["done", "delivered", "succeeded"].includes(key) ? `已完成 ${node.label}` : `${sub} · ${status}`);
      const chips = Array.isArray(node.artifact_chips) && node.artifact_chips.length
        ? `<span class="wfl-node-chips">${node.artifact_chips.slice(0, 1).map(chip => `<span class="wfl-chip" data-wfl-chip="${esc(chip.task_id || "")}" title="${esc(chip.name || "")}">📄 ${esc(chip.name || "产物")}</span>`).join("")}</span>`
        : "";
      const isJoin = (inDeg.get(node.id) || 0) > 1;
      const isLeaf = !(outDeg.get(node.id) || 0) && index > 0;
      const botClass = isJoin ? "is-join" : isLeaf ? "is-check" : "";
      const displayTitle = (stepInfo && /^Step\s*\d+/i.test(node.label)) ? stepInfo.formattedTitle : node.label;
      const label = displayTitle.length > 26 ? displayTitle.slice(0, 25) + "…" : displayTitle;
      const badgeNo = stepInfo ? stepInfo.badgeLabel : `#${index + 1}`;
      return `<g class="wfl-node ${esc(key)} ${node.id === data.selected_node ? "selected" : ""}" transform="translate(${p.x},${p.y})" ${interactive ? `data-wfl-node="${esc(node.id)}" role="button" tabindex="0" aria-label="${esc(displayTitle + "：" + status)}"` : ""}>
        <title>${esc([displayTitle, status, sub, rawSummary].filter(Boolean).join(" · "))}</title>
        <foreignObject width="${p.w}" height="${H}">
          <div xmlns="http://www.w3.org/1999/xhtml" class="wfl-row">
            <div class="wfl-card-top">
              <div class="wfl-card-title-wrap">
                <i class="wfl-card-dot"></i>
                <span class="wfl-row-title">${esc(label)}</span>
              </div>
              <div class="wfl-card-badges">
                <span class="wfl-bot-badge ${botClass}" title="${esc(sub)}">✦</span>
                <span class="wfl-row-no">${esc(badgeNo)}</span>
                <span class="wfl-row-pct">${esc(pct)}</span>
              </div>
            </div>
            <div class="wfl-card-summary">
              <span class="wfl-card-summary-text">${esc(summary)}</span>
              ${chips || `<span class="wfl-row-pill">${esc(status)}</span>`}
            </div>
          </div>
        </foreignObject>
      </g>`;
    }).join("");
    return `<div class="wfl-graph-scroll"><svg class="wfl-graph-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="工作流节点及依赖状态图"><defs><marker id="wfl-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M 0 0 L 6 3 L 0 6 z" fill="var(--muted)"/></marker></defs>${paths}${nodes}</svg></div>`;
  }
  window.WorkflowLayout={computeHierarchicalStepNumbers};
  window.WorkflowGraph={render:graph,computeHierarchicalStepNumbers};
  if(!SET_SECS.some(section=>section[0]==="workflows")) SET_SECS.push(["workflows","🔀","工作流"]);
  const originalSettings = renderSettings;
  renderSettings = function(m){
    originalSettings(m);
    if(setSec !== "workflows" || !S.loaded) return;
    const body = byId("set-body"); if(!body) return;
    if(settingsData) drawEditor(body);
    else body.innerHTML = '<div class="card"><h3>工作流设置</h3><p class="hint">正在读取工作流定义…</p></div>';
    const requestId=++settingsRequest;
    api("/api/workflow/layouts").then(result=>{
      if(requestId!==settingsRequest) return;
      const active=route()==="settings" && setSec==="workflows" && body.isConnected;
      if(active && body.querySelector("[data-wfl-name]")) captureEditor(body);
      const previous=settingsData?.layouts.find(layout=>layout.id===draft?.id);
      const dirty=draft && (!previous || ["name","description","nodes","edges","order"].some(key=>JSON.stringify(draft[key])!==JSON.stringify(previous[key])));
      settingsData=result;
      if(!active) return;
      if(!dirty) draft=clone(result.layouts.find(layout=>layout.id===draft?.id) || result.layouts[0] || freshDraft());
      else if(draft.id && !result.layouts.some(layout=>layout.id===draft.id)) draft.id="";
      drawEditor(body);
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
      const mode=row.querySelector("[data-wfl-mode]").value;
      nodes.push({...draft.nodes.find(node=>node.id===id),id,step:row.querySelector("[data-wfl-step]").value,label:row.querySelector("[data-wfl-label]").value.trim(),
        review_required:row.querySelector("[data-wfl-review]").checked,
        executor_role_id:row.querySelector("[data-wfl-role]").value.trim() || id,
        execution_mode:mode,
        assistant_id:mode==="team"?"":row.querySelector("[data-wfl-assistant]").value,team_id:mode==="assistant"?"":row.querySelector("[data-wfl-team]").value,
        member_id:mode==="assistant"?"":row.querySelector("[data-wfl-member]").value,prompt:row.querySelector("[data-wfl-prompt]").value,
        tools:row.querySelector("[data-wfl-limit-tools]").checked ? [...row.querySelector("[data-wfl-tools]").selectedOptions].map(o=>o.value) : null,
        skills:[...row.querySelector("[data-wfl-skills]").selectedOptions].map(o=>o.value)});
      const sources=row.querySelector("[data-wfl-parents]").value.split(/[,，\s]+/).filter(Boolean);
      sources.forEach(source=>edges.push(draft.edges.find(edge=>edge.source===source&&edge.target===id)||{source,target:id}));
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
    const stages=[...(settingsData.stages || [])];
    if(!stages.some(stage=>stage.id==="task")) stages.push({id:"task",name:"自定义项目任务"});
    const execution=settingsData.execution_options || {};
    const choices=(rows,current,empty)=>`<option value="">${empty}</option>${(rows || []).map(item=>`<option value="${esc(item.id)}" ${item.id===current?"selected":""}>${esc(item.name || item.label || item.id)}</option>`).join("")}`;
    const multi=(rows,selected)=> (rows || []).map(item=>`<option value="${esc(item.id)}" ${(selected || []).includes(item.id)?"selected":""}>${esc(item.name || item.label || item.id)}</option>`).join("");
    const nodeConfig=node=>`<details><summary>执行与审查${node.review_required===false?" · 自动交付":" · 需人工审查"}</summary>
      <label class="field"><input type="checkbox" style="width:auto" data-wfl-review ${node.review_required===false?"":"checked"}> 产物生成后需要人工审查</label>
      <label class="field"><span>项目执行者角色 ID</span><input data-wfl-role maxlength="120" value="${esc(node.executor_role_id || node.id)}" placeholder="如 literature_reviewer"><small class="hint">在项目任务的“工作流角色绑定”中，为此角色选择本项目专家或团队。</small></label>
      <label class="field"><span>执行方式</span><select data-wfl-mode aria-label="节点执行方式">${[["","使用本项目的角色绑定"],["assistant","专家执行"],["team","团队执行"]].map(([id,label])=>`<option value="${id}" ${id===(node.execution_mode || (node.team_id?"team":node.assistant_id?"assistant":""))?"selected":""}>${label}</option>`).join("")}</select></label>
      <label class="field"><span>执行专家（专家模式）</span><select data-wfl-assistant aria-label="节点专家">${choices(execution.assistants,node.assistant_id,"请选择专家；团队模式使用成员绑定")}</select></label>
      <label class="field"><span>执行团队（团队模式）</span><select data-wfl-team aria-label="节点团队">${choices(execution.teams,node.team_id,"请选择团队")}</select></label>
      <label class="field"><span>团队成员</span><select data-wfl-member aria-label="节点团队成员">${choices(execution.teams?.find(t=>t.id===node.team_id)?.members,node.member_id,"按环节匹配，或使用 Leader")}</select></label>
      <label class="field"><span>节点任务说明</span><textarea data-wfl-prompt rows="3" placeholder="留空按当前环节执行；自动承接上游成果和人工反馈">${esc(node.prompt || "")}</textarea></label>
      <label class="field"><input type="checkbox" style="width:auto" data-wfl-limit-tools ${node.tools!=null?"checked":""}> 仅允许使用选中的工具</label>
      <select data-wfl-tools multiple size="5" aria-label="节点工具">${multi(execution.tools,node.tools)}</select>
      <label class="field"><span>指定应用的技能，可多选</span><select data-wfl-skills multiple size="5" aria-label="节点技能">${multi(execution.skills,node.skills)}</select></label>
      <p class="hint">按住 Ctrl 或 ⌘ 多选。节点将继承项目记忆、前序交付和人工反馈。工具仍受专家权限控制。</p></details>`;
    body.innerHTML=`<div class="card wfl-editor"><h3>工作流定义</h3><p class="hint">编辑节点、前置依赖和顺序，保存后可在工作流页选择。可添加自定义项目任务，或重复使用同类步骤；项目角色绑定决定实际执行者。</p>
      <div class="wfl-editor-actions"><button class="btn" data-wfl-new>新建工作流</button><button class="btn ghost" data-wfl-copy>复制当前</button><span class="hint" role="status" data-wfl-message>${esc(notice)}</span></div>
      <div class="wfl-editor-grid"><aside class="wfl-editor-list">${settingsData.layouts.map(layout=>`<button class="wfl-choice ${layout.id===draft.id?"on":""}" data-wfl-edit="${esc(layout.id)}"><b>${esc(layout.name)}</b><small>${layout.nodes.length} 个节点</small></button>`).join("")}</aside>
      <section><label class="field"><span>工作流名称</span><input data-wfl-name maxlength="120" value="${esc(draft.name)}"></label><label class="field"><span>说明</span><textarea data-wfl-description rows="2">${esc(draft.description || "")}</textarea></label>
      <div class="wfl-node-table"><table><thead><tr><th>功能步骤 / 节点 ID</th><th>节点名称</th><th>前置节点 ID（逗号分隔）</th><th>顺序</th></tr></thead><tbody>${draftNodes().map((node,index)=>`<tr data-wfl-row="${esc(node.id)}"><td><select data-wfl-step aria-label="功能步骤">${stages.map(stage=>`<option value="${esc(stage.id)}" ${stage.id===node.step?"selected":""}>${esc(stage.name)}</option>`).join("")}</select><code>${esc(node.id)}</code></td><td><input data-wfl-label aria-label="节点名称" value="${esc(node.label)}" maxlength="120"></td><td><input data-wfl-parents aria-label="前置节点" value="${esc(draft.edges.filter(edge=>edge.target===node.id).map(edge=>edge.source).join(", "))}" placeholder="无前置依赖"></td><td><button class="btn ghost" data-wfl-up="${index}" ${index===0?"disabled":""} aria-label="上移节点">↑</button><button class="btn ghost" data-wfl-down="${index}" ${index===draft.order.length-1?"disabled":""} aria-label="下移节点">↓</button><button class="btn ghost" data-wfl-remove="${esc(node.id)}" aria-label="移除节点">移除</button></td></tr>`).join("")}</tbody></table></div>
      <div class="wfl-editor-actions"><select data-wfl-add-step aria-label="添加步骤">${stages.map(stage=>`<option value="${esc(stage.id)}">${esc(stage.name)}</option>`).join("")}</select><button class="btn ghost" data-wfl-add>添加节点</button><button class="btn ghost" data-wfl-chain>按当前顺序串联</button></div>
      <details><summary>高级 JSON（nodes / edges / order）</summary><textarea data-wfl-json class="wfl-json" spellcheck="false" aria-label="工作流 JSON"></textarea><button class="btn ghost" data-wfl-apply-json>应用 JSON 到编辑器</button></details>
      <div class="wfl-graph"><div class="wfl-note">组织预览（不是运行状态）</div><div data-wfl-preview></div></div>
      <div class="wfl-editor-actions"><button class="btn" data-wfl-save>保存工作流</button>${draft.id?'<button class="btn ghost" data-wfl-delete>删除工作流</button>':""}</div></section></div></div>`;
    body.querySelectorAll("[data-wfl-row]").forEach(row=>{
      const node=draft.nodes.find(n=>n.id===row.dataset.wflRow);
      row.children[1].insertAdjacentHTML("beforeend",nodeConfig(node));
    });
    const message=text=>{ const el=body.querySelector("[data-wfl-message]"); if(el) el.textContent=text; };
    body.querySelectorAll("[data-wfl-name],[data-wfl-description]").forEach(input=>input.oninput=()=>captureEditor(body));
    body.querySelector("[data-wfl-new]").onclick=()=>{ draft=freshDraft(); drawEditor(body); };
    body.querySelector("[data-wfl-copy]").onclick=()=>{ captureEditor(body); draft.id=""; draft.name+="（副本）"; drawEditor(body); };
    body.querySelectorAll("[data-wfl-edit]").forEach(button=>button.onclick=()=>{ draft=clone(settingsData.layouts.find(layout=>layout.id===button.dataset.wflEdit)); drawEditor(body); });
    body.querySelectorAll("[data-wfl-row] input,[data-wfl-row] select").forEach(input=>input.onchange=()=>{
      const configRow=input.closest("[data-wfl-row]");
      if(input.hasAttribute("data-wfl-assistant") && input.value) configRow.querySelector("[data-wfl-mode]").value="assistant";
      if(input.hasAttribute("data-wfl-team")){
        if(input.value) configRow.querySelector("[data-wfl-mode]").value="team";
        input.closest("[data-wfl-row]").querySelector("[data-wfl-member]").innerHTML=choices(execution.teams?.find(t=>t.id===input.value)?.members,"","按环节匹配，或使用 Leader");
      }
      if(input.hasAttribute("data-wfl-step")){
        const row=input.closest("[data-wfl-row]"), previous=draft.nodes.find(node=>node.id===row.dataset.wflRow);
        const label=row.querySelector("[data-wfl-label]");
        if(previous && label.value===stageLabel(previous.step)) label.value=stageLabel(input.value);
      }
      captureEditor(body); previewDraft(body);
    });
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
      draft.nodes.push({id,step,label:step==="task"?"自定义项目任务":stageLabel(step),executor_role_id:id,review_required:true}); draft.order.push(id); drawEditor(body);
    };
    body.querySelector("[data-wfl-chain]").onclick=()=>{ captureEditor(body); draft.edges=draft.order.slice(1).map((target,index)=>{const source=draft.order[index];return draft.edges.find(edge=>edge.source===source&&edge.target===target)||{source,target};}); drawEditor(body); };
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
      captureEditor(body); event.target.disabled=true; settingsRequest++;
      try{
        const saved=await api("/api/workflow/layouts/save",draft); draft=clone(saved.layout);
        settingsData=await api("/api/workflow/layouts"); if(body.isConnected) drawEditor(body,"已保存");
      }catch(error){ message("保存失败："+error.message); event.target.disabled=false; }
    };
    const del=body.querySelector("[data-wfl-delete]");
    if(del) del.onclick=async()=>{
      const impact=await api('/api/workflow/layouts/delete-impact?id='+encodeURIComponent(draft.id));
      if(!await uiConfirm("删除工作流「"+draft.name+"」？涉及 "+impact.projects+" 个项目选择、"+impact.schedules+" 个定时任务和 "+impact.subworkflows+" 处子工作流引用。项目选择将清空，相关引用需重新选择；历史运行与产物保留。")) return;
      del.disabled=true; settingsRequest++;
      try{ await api("/api/workflow/layouts/delete",{id:draft.id}); settingsData=await api("/api/workflow/layouts"); draft=null; if(body.isConnected) drawEditor(body,"工作流已删除"); }
      catch(error){ message("删除失败："+error.message); del.disabled=false; }
    };
    previewDraft(body);
  }

})();
