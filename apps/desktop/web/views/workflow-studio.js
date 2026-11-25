/* Dedicated Workflow Studio for managing templates and deep DAG node editing */
const WorkflowStudio = (() => {
  "use strict";

  const h = value => esc(String(value ?? ""));
  const copy = value => JSON.parse(JSON.stringify(value));

  let container = null;
  let layoutsData = null;
  let currentLayout = null;
  let selectedNodeId = "";
  let zoom = 1;
  let dirty = false;

  function freshLayout() {
    return {
      id: "",
      name: "新工作流模板",
      description: "",
      nodes: [
        { id: "node-init", step: "idea", label: "初始课题定义", prompt: "", review_required: true, executor_role_id: "node-init" }
      ],
      edges: [],
      order: ["node-init"]
    };
  }

  async function loadData() {
    layoutsData = await api("/api/workflow/layouts");
    if (!currentLayout || !layoutsData.layouts.some(l => l.id === currentLayout.id)) {
      currentLayout = copy(layoutsData.layouts[0] || freshLayout());
    } else {
      const match = layoutsData.layouts.find(l => l.id === currentLayout.id);
      if (match && !dirty) currentLayout = copy(match);
    }
    if (!currentLayout.nodes.some(n => n.id === selectedNodeId)) {
      selectedNodeId = currentLayout.nodes[0]?.id || "";
    }
  }

  function topoSort(layout) {
    const order = [], pending = new Set(layout.nodes.map(n => n.id));
    for (let pass = 0; pending.size && pass < layout.nodes.length; pass++) {
      for (const id of [...pending]) {
        if (layout.edges.filter(e => e.target === id).every(e => order.includes(e.source))) {
          order.push(id);
          pending.delete(id);
        }
      }
    }
    layout.order = [...order, ...pending];
  }

  function addNode(step, label, topology = "serial") {
    const id = "node-" + crypto.randomUUID().slice(0, 8);
    const node = {
      id,
      step: step || "task",
      label: label || "新节点",
      prompt: "",
      review_required: true,
      executor_role_id: id
    };
    currentLayout.nodes.push(node);

    if (topology === "parallel" && selectedNodeId) {
      const parents = currentLayout.edges.filter(e => e.target === selectedNodeId).map(e => e.source);
      const children = currentLayout.edges.filter(e => e.source === selectedNodeId).map(e => e.target);
      parents.forEach(p => currentLayout.edges.push({ source: p, target: id }));
      children.forEach(c => currentLayout.edges.push({ source: id, target: c }));
    } else if (topology === "join" && selectedNodeId) {
      const parents = currentLayout.nodes.filter(n => n.id !== id && !currentLayout.edges.some(e => e.source === n.id)).map(n => n.id);
      parents.forEach(p => currentLayout.edges.push({ source: p, target: id }));
    } else {
      const tail = selectedNodeId || currentLayout.order?.filter(i => i !== id).at(-1);
      if (tail) {
        const outs = currentLayout.edges.filter(e => e.source === tail).map(e => e.target);
        currentLayout.edges = currentLayout.edges.filter(e => e.source !== tail);
        currentLayout.edges.push({ source: tail, target: id });
        outs.forEach(t => currentLayout.edges.push({ source: id, target: t }));
      }
    }

    topoSort(currentLayout);
    selectedNodeId = id;
    dirty = true;
    renderStudio();
  }

  async function deleteCurrentNode() {
    if (currentLayout.nodes.length <= 1) {
      uiNotify("工作流至少需要保留一个节点，无法删除唯一节点");
      return;
    }
    const node = currentLayout.nodes.find(n => n.id === selectedNodeId);
    if (!node) return;
    if (!await uiConfirm(`确定删除节点「${node.label}」？系统将自动缝合前后依赖连接。`)) return;

    try {
      const res = await api("/api/workflow/layouts/delete-node", {
        layout: currentLayout,
        node_id: node.id,
        restitch: true
      });
      currentLayout = res.layout;
      selectedNodeId = currentLayout.nodes[0]?.id || "";
      dirty = true;
      renderStudio();
      uiNotify("已删除节点并自动缝合前后依赖");
    } catch (err) {
      uiNotify("删除节点失败：" + err.message);
    }
  }

  async function saveTemplate() {
    if (!currentLayout.name.trim()) {
      uiNotify("请输入工作流模板名称");
      return;
    }
    try {
      const res = await api("/api/workflow/layouts/save", currentLayout);
      currentLayout = copy(res.layout);
      dirty = false;
      await loadData();
      renderStudio();
      uiNotify(res.reused?'已复用相同模板':res.name_conflict?'已保存：同名模板业务定义不同，分别保留':'工作流模板已保存');
    } catch (err) {
      uiNotify("保存失败：" + err.message);
    }
  }

  async function deleteCurrentTemplate() {
    try {
      const impact=await api('/api/workflow/layouts/delete-impact?id='+encodeURIComponent(currentLayout.id));
      if (!await uiConfirm(`删除工作流模板「${currentLayout.name}」？涉及 ${impact.projects} 个项目选择、${impact.schedules} 个定时任务和 ${impact.subworkflows} 处子工作流引用。项目选择将清空，相关引用需重新选择；历史运行与产物保留。`)) return;
      await api("/api/workflow/layouts/delete", { id: currentLayout.id });
      currentLayout = null;
      dirty = false;
      await loadData();
      renderStudio();
      uiNotify("工作流模板已删除");
    } catch (err) {
      uiNotify("删除模板失败：" + err.message);
    }
  }

  function renderSidebar() {
    const list = container.querySelector("[data-wfs-list]");
    if (!list || !layoutsData) return;

    list.innerHTML = layoutsData.layouts.map(layout => {
      const isBuiltin = layout.id.startsWith("builtin.");
      const isActive = currentLayout && currentLayout.id === layout.id;
      return `<button type="button" class="wfs-template-card ${isActive ? 'active' : ''}" data-wfs-template="${h(layout.id)}">
        <div class="wfs-card-head">
          <b>${h(layout.name)}</b>
          <span class="wfs-card-badge ${isBuiltin ? 'builtin' : ''}">${isBuiltin ? '内置' : '自定义'}</span>
        </div>
        <small>${h(layout.description || `${layout.nodes?.length || 0} 个节点 DAG 拓扑`)}</small>
      </button>`;
    }).join("");

    list.querySelectorAll("[data-wfs-template]").forEach(btn => {
      btn.onclick = () => {
        const tid = btn.dataset.wfsTemplate;
        const match = layoutsData.layouts.find(l => l.id === tid);
        if (match) {
          currentLayout = copy(match);
          selectedNodeId = currentLayout.nodes[0]?.id || "";
          dirty = false;
          renderStudio();
        }
      };
    });
  }

  function renderCanvas() {
    const area = container.querySelector("[data-wfs-canvas]");
    if (!area || !currentLayout) return;

    const canvasWidth = Math.max(600, area.clientWidth - 48);
    const renderedNodes = currentLayout.nodes.map(n => ({
      ...n,
      status: "pending"
    }));

    area.innerHTML = WorkflowGraph.render({
      ...currentLayout,
      selected_node: selectedNodeId,
      nodes: renderedNodes
    }, true, canvasWidth);

    const svg = area.querySelector("svg");
    if (svg) svg.style.zoom = String(zoom);

    area.querySelectorAll("[data-wfl-node]").forEach(nodeEl => {
      const nid = nodeEl.dataset.wflNode;
      nodeEl.onclick = () => {
        selectedNodeId = nid;
        renderCanvas();
        renderInspector();
      };
    });
  }

  function renderInspector() {
    const inspector = container.querySelector("[data-wfs-inspector]");
    if (!inspector || !currentLayout) return;

    const node = currentLayout.nodes.find(n => n.id === selectedNodeId);
    if (!node) {
      inspector.innerHTML = `<div class="wfs-inspector-head"><h4>节点配置</h4></div><div class="wfs-inspector-body"><p class="hint">请选择画布中的节点进行属性编辑。</p></div>`;
      return;
    }

    const stages = layoutsData?.stages || [];
    const execution = layoutsData?.execution_options || {};
    const models = execution.models || (typeof S !== 'undefined' ? S?.models : []) || [];
    const nodeFallbacks = new Set(Array.isArray(node.fallback_model_ids) ? node.fallback_model_ids : []);
    const parents = new Map(currentLayout.edges.filter(e => e.target === node.id).map(e => [e.source, e]));

    inspector.innerHTML = `
      <div class="wfs-inspector-head">
        <h4>节点配置 · ${h(node.id)}</h4>
        <button type="button" class="btn ghost sm" style="color:var(--err)" data-wfs-del-node ${currentLayout.nodes.length <= 1 ? 'disabled' : ''}>删除节点</button>
      </div>
      <div class="wfs-inspector-body">
        <label class="wfs-field">
          <span>节点名称</span>
          <input class="control" data-wfs-node-label value="${h(node.label)}">
        </label>
        <label class="wfs-field">
          <span>所属研究阶段</span>
          <select class="control" data-wfs-node-step>
            <option value="task">通用项目任务</option>
            ${stages.map(s => `<option value="${h(s.id)}" ${s.id === node.step ? 'selected' : ''}>${h(s.name)}</option>`).join('')}
          </select>
        </label>
        <label class="wfs-field">
          <span>执行方式</span>
          <select class="control" data-wfs-node-mode>
            <option value="" ${!node.execution_mode ? 'selected' : ''}>跟随项目角色绑定</option>
            <option value="assistant" ${node.execution_mode === 'assistant' ? 'selected' : ''}>专家执行</option>
            <option value="team" ${node.execution_mode === 'team' ? 'selected' : ''}>团队执行</option>
          </select>
        </label>
        <label class="wfs-field" data-wfs-assistant-wrap ${node.execution_mode === 'assistant' ? '' : 'hidden'}>
          <span>指派专家</span>
          <select class="control" data-wfs-node-assistant>
            <option value="">请选择专家</option>
            ${(execution.assistants || []).map(a => `<option value="${h(a.id)}" ${a.id === node.assistant_id ? 'selected' : ''}>${h(a.name)}</option>`).join('')}
          </select>
        </label>
        <label class="wfs-field">
          <span>节点主模型（可选覆盖）</span>
          <select class="control" data-wfs-node-model>
            <option value="">跟随执行者 / 默认模型</option>
            ${models.map(m => `<option value="${h(m.id)}" ${m.id === node.model_id ? 'selected' : ''}>${h(m.name || m.model)}</option>`).join('')}
          </select>
        </label>
        <label class="wfs-field">
          <span>模型容错策略</span>
          <select class="control" data-wfs-node-model-policy>
            <option value="fallback" ${node.model_policy !== 'strict' ? 'selected' : ''}>主模型不可用时自动切换备用模型</option>
            <option value="strict" ${node.model_policy === 'strict' ? 'selected' : ''}>严格锁定主模型（禁止自动降级）</option>
          </select>
        </label>
        <label class="wfs-field">
          <span>节点备用模型链（按 Ctrl/⌘ 多选）</span>
          <select class="control" data-wfs-node-fallbacks multiple size="3">
            ${models.map(m => `<option value="${h(m.id)}" ${nodeFallbacks.has(m.id) ? 'selected' : ''}>${h(m.name || m.model)}</option>`).join('')}
          </select>
        </label>
        <label class="wfs-field">
          <span>任务要求与目标 (Prompt)</span>
          <textarea class="control" data-wfs-node-prompt rows="4" placeholder="详细定义本节点的执行目标，可引用 {{topic}} 等变量">${h(node.prompt || "")}</textarea>
        </label>
        <label class="wfs-check">
          <input type="checkbox" data-wfs-node-review ${node.review_required !== false ? 'checked' : ''}>
          <span>产物生成后需要人工审查</span>
        </label>
        <label class="wfs-check">
          <input type="checkbox" data-wfs-node-audit ${node.claim_audit ? 'checked' : ''}>
          <span>启用文献结论自证审计 (Claim Audit)</span>
        </label>
        <div class="wfs-field">
          <span>前置依赖节点与产物交接</span>
          <div class="wfs-dep-box">
            ${currentLayout.nodes.filter(n => n.id !== node.id).map(parent => {
              const edge = parents.get(parent.id);
              return `<div class="wfs-dep-item" data-wfs-edge-box="${h(parent.id)}">
                <label class="wfs-check">
                  <input type="checkbox" data-wfs-dep-check ${edge ? 'checked' : ''}>
                  <span>${h(parent.label)}</span>
                </label>
                <input class="control" data-wfs-dep-artifact placeholder="传递的产物文件名（可选）" value="${h(edge?.artifact || '')}" style="font-size:11px" ${edge ? '' : 'hidden'}>
              </div>`;
            }).join('') || '<span class="hint">暂无其他节点</span>'}
          </div>
        </div>
      </div>
    `;

    // Event Bindings
    inspector.querySelector("[data-wfs-del-node]").onclick = deleteCurrentNode;

    inspector.querySelector("[data-wfs-node-label]").oninput = e => {
      node.label = e.target.value.trim();
      dirty = true;
      renderCanvas();
    };

    inspector.querySelector("[data-wfs-node-step]").onchange = e => {
      node.step = e.target.value;
      dirty = true;
      renderCanvas();
    };

    const modeSelect = inspector.querySelector("[data-wfs-node-mode]");
    const assistantWrap = inspector.querySelector("[data-wfs-assistant-wrap]");
    modeSelect.onchange = e => {
      node.execution_mode = e.target.value;
      assistantWrap.hidden = node.execution_mode !== "assistant";
      dirty = true;
    };

    inspector.querySelector("[data-wfs-node-assistant]")?.addEventListener("change", e => {
      node.assistant_id = e.target.value;
      dirty = true;
    });

    inspector.querySelector("[data-wfs-node-model]")?.addEventListener("change", e => {
      node.model_id = e.target.value;
      dirty = true;
    });

    inspector.querySelector("[data-wfs-node-model-policy]")?.addEventListener("change", e => {
      node.model_policy = e.target.value;
      dirty = true;
    });

    inspector.querySelector("[data-wfs-node-fallbacks]")?.addEventListener("change", e => {
      node.fallback_model_ids = [...e.target.selectedOptions].map(o => o.value).filter(Boolean);
      dirty = true;
    });

    inspector.querySelector("[data-wfs-node-prompt]").oninput = e => {
      node.prompt = e.target.value;
      dirty = true;
    };

    inspector.querySelector("[data-wfs-node-review]").onchange = e => {
      node.review_required = e.target.checked;
      dirty = true;
    };

    inspector.querySelector("[data-wfs-node-audit]").onchange = e => {
      node.claim_audit = e.target.checked;
      dirty = true;
    };

    inspector.querySelectorAll("[data-wfs-edge-box]").forEach(box => {
      const pid = box.dataset.wfsEdgeBox;
      const chk = box.querySelector("[data-wfs-dep-check]");
      const art = box.querySelector("[data-wfs-dep-artifact]");
      const updateEdge = () => {
        currentLayout.edges = currentLayout.edges.filter(e => !(e.source === pid && e.target === node.id));
        if (chk.checked) {
          currentLayout.edges.push({
            source: pid,
            target: node.id,
            ...(art.value.trim() ? { artifact: art.value.trim() } : {})
          });
        }
        art.hidden = !chk.checked;
        topoSort(currentLayout);
        dirty = true;
        renderCanvas();
      };
      chk.onchange = updateEdge;
      art.oninput = updateEdge;
    });
  }

  function renderStudio() {
    if (!container) return;

    const isBuiltin = currentLayout?.id?.startsWith("builtin.");

    container.innerHTML = `
      <section class="wfs-container" aria-label="工作流工作台">
        <aside class="wfs-sidebar">
          <div class="wfs-sidebar-head">
            <h3>🔀 工作流模板</h3>
            <button class="btn btn-primary sm" data-wfs-new title="新建空白工作流">＋ 新建</button>
          </div>
          <div class="wfs-template-list" data-wfs-list></div>
        </aside>

        <main class="wfs-main">
          <header class="wfs-toolbar">
            <input class="wfs-title-input" data-wfs-name value="${h(currentLayout?.name || '')}" placeholder="工作流名称">
            <span class="wfs-card-badge ${isBuiltin ? 'builtin' : ''}">${isBuiltin ? '内置模板' : '自定义模板'}</span>
            <span class="pw-spacer"></span>
            <button class="btn ghost sm" data-wfs-add-serial title="顺序追加串行步骤">＋ 串行步骤</button>
            <button class="btn ghost sm" data-wfs-add-parallel title="为选中节点添加同层并行分支">＋ 并行步骤</button>
            <button class="btn ghost sm" data-wfs-add-join title="添加汇聚步骤合并分支">＋ 汇聚步骤</button>
            <button class="btn ghost sm" data-wfs-zoom-out>− 缩小</button>
            <button class="btn ghost sm" data-wfs-zoom-in>＋ 放大</button>
            <button class="btn ghost sm" data-wfs-copy>复制为自定义</button>
            <button class="btn btn-primary sm" data-wfs-save ${isBuiltin ? 'disabled' : ''}>保存模板</button>
            ${currentLayout?.id ? '<button class="btn ghost sm" style="color:var(--err)" data-wfs-delete-tpl>删除模板</button>' : ''}
          </header>

          <div class="wfs-canvas-area" data-wfs-canvas></div>
        </main>

        <aside class="wfs-inspector" data-wfs-inspector></aside>
      </section>
    `;

    // Bind toolbar actions
    container.querySelector("[data-wfs-name]").oninput = e => {
      if (isBuiltin) {
        uiNotify("内置模板不可直接修改名称，请点击“复制为自定义”后再编辑");
        e.target.value = currentLayout.name;
        return;
      }
      currentLayout.name = e.target.value.trim();
      dirty = true;
      renderSidebar();
    };

    container.querySelector("[data-wfs-new]").onclick = () => {
      currentLayout = freshLayout();
      selectedNodeId = currentLayout.nodes[0].id;
      dirty = true;
      renderStudio();
    };

    container.querySelector("[data-wfs-copy]").onclick = () => {
      currentLayout = copy(currentLayout);
      currentLayout.id = "";
      currentLayout.name = currentLayout.name + " (自定义副本)";
      dirty = true;
      renderStudio();
      uiNotify("已复制为自定义模板草稿，可自由编辑并保存");
    };

    container.querySelector("[data-wfs-add-serial]").onclick = () => addNode("task", "串行分析步骤", "serial");
    container.querySelector("[data-wfs-add-parallel]").onclick = () => addNode("task", "并行分析任务", "parallel");
    container.querySelector("[data-wfs-add-join]").onclick = () => addNode("task", "综合汇聚节点", "join");

    container.querySelector("[data-wfs-zoom-out]").onclick = () => {
      zoom = Math.max(0.4, zoom - 0.15);
      renderCanvas();
    };
    container.querySelector("[data-wfs-zoom-in]").onclick = () => {
      zoom = Math.min(2.0, zoom + 0.15);
      renderCanvas();
    };

    container.querySelector("[data-wfs-save]").onclick = saveTemplate;
    const delTplBtn = container.querySelector("[data-wfs-delete-tpl]");
    if (delTplBtn) delTplBtn.onclick = deleteCurrentTemplate;

    renderSidebar();
    renderCanvas();
    renderInspector();
  }

  async function render(host) {
    container = host;
    container.innerHTML = '<div class="muted" style="padding:24px">正在加载工作流管理中心…</div>';
    await loadData();
    renderStudio();
  }

  return { render };
})();
