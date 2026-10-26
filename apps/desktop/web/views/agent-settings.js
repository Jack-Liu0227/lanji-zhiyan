/* Editable providers, assistants, team rosters and durable session selection. */
(() => {
  "use strict";
  const endpoint = "/api/agent-settings/";
  const request = (name, body) => api(endpoint + name, body);
  const eligible = a => a.source === "user" || !!a.enabled;
  const el = id => document.getElementById(id);
  const h = value => esc(String(value ?? ""));
  const options = (values, current, labels = {}) => values.map(v => `<option value="${h(v)}" ${v === current ? "selected" : ""}>${h(labels[v] || v)}</option>`).join("");
  const effortLabels = {auto:"Agent 决定",none:"不推理",minimal:"最小",low:"低",medium:"中",high:"高",xhigh:"极高"};
  const permissionLabels = {auto:"Agent 决定",read_only:"只读",workspace_write:"工作区写入",full_access:"Full Access（危险）"};
  const efforts = Object.keys(effortLabels), permissions = Object.keys(permissionLabels);
  let registry = null, assistantFilter = "all", assistantId = null, teamSession = "";
  let viewEpoch = 0;
  let dialog = null;
  const css = document.createElement("style");
  css.textContent = `.ag-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}.ag-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.ag-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px;margin-top:14px}.ag-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.ag-form label,.ag-stack{display:flex;flex-direction:column;gap:6px}.ag-wide{grid-column:1/-1}.ag-modal{width:min(760px,calc(100vw - 32px));max-height:88vh;overflow:auto;background:var(--surface,var(--bg));color:var(--text);border:1px solid var(--line,var(--border));border-radius:16px;padding:24px;box-shadow:0 24px 90px #0006}.ag-modal::backdrop{background:#0008;backdrop-filter:blur(3px)}.ag-list{display:flex;flex-direction:column;gap:8px;max-height:270px;overflow:auto}.ag-model{display:flex;align-items:center;gap:10px;padding:8px;border-bottom:1px solid var(--line,var(--border));overflow-wrap:anywhere}.ag-danger{padding:10px 12px;border-left:3px solid var(--err,#b54b45);background:color-mix(in srgb,var(--err,#b54b45) 9%,transparent);font-size:12px}.ag-meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.ag-team{display:grid;grid-template-columns:220px minmax(0,1fr);gap:14px}.ag-team-nav{display:flex;flex-direction:column;gap:8px}.ag-team-nav button{text-align:left}.ag-transcript{max-height:52vh;min-height:180px;overflow:auto;display:flex;flex-direction:column;gap:10px;margin:14px 0}.ag-message{padding:12px;border:1px solid var(--line,var(--border));border-radius:10px;white-space:pre-wrap;overflow-wrap:anywhere}.ag-message strong{font-size:12px;color:var(--brand)}.ag-member{cursor:pointer}.ag-status{min-height:20px;margin:10px 0}.ag-model-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:8px;max-height:320px;overflow:auto}@media(max-width:720px){.ag-form,.ag-team{grid-template-columns:1fr}.ag-team-nav{flex-direction:row;overflow:auto}.ag-team-nav button{min-width:160px}.ag-modal{padding:16px}.ag-head{align-items:flex-start}}`;
  document.head.append(css);

  async function load() {
    registry = await request("state");
    S.models = registry.models;
    S.assistants = registry.assistants;
    return registry;
  }
  function error(container, err) {
    container.textContent = err.message || String(err);
    container.className = "msg error ag-status";
  }
  function modal(title, body) {
    if (dialog) dialog.close();
    const d = document.createElement("dialog");
    d.className = "ag-modal";
    d.innerHTML = `<div class="ag-head"><h3>${h(title)}</h3><button class="btn ghost" data-close aria-label="关闭">关闭</button></div>${body}<div class="ag-status" role="status"></div>`;
    document.body.append(d);
    dialog = d;
    d.querySelector("[data-close]").onclick = () => d.close();
    d.addEventListener("close", () => { d.remove(); if (dialog === d) dialog = null; });
    d.showModal();
    return d;
  }
  async function saved() { await refreshState(false); await load(); window.refreshCapabilities?.(); }
  const modelOptions = current => `<option value="">跟随当前模型</option>${(S.models || []).map(m => `<option value="${h(m.id)}" ${m.id === current ? "selected" : ""}>${h(m.name)}${m.name.includes(m.model) ? "" : " · " + h(m.model)}</option>`).join("")}`;
  function policyFields(a, prefix) {
    return `<label><span>模型</span><select class="control" id="${prefix}-model">${modelOptions(a.model_id)}</select></label>
      <label><span>推理强度</span><select class="control" id="${prefix}-reasoning">${options(efforts,a.reasoning || "auto",effortLabels)}</select></label>
      <label><span>权限</span><select class="control" id="${prefix}-permission">${options(permissions,a.permission || "auto",permissionLabels)}</select></label>
      <div class="ag-danger ag-wide">Full Access 将允许所有已启用工具执行写入和外发操作，不再逐项确认，可能修改数据或泄露资料。只对可信助手启用。技能与工具矩阵的禁用规则仍然生效。</div>`;
  }
  function policies(prefix) {
    return {model_id:el(prefix + "-model").value,reasoning:el(prefix + "-reasoning").value,permission:el(prefix + "-permission").value};
  }
  async function risk(oldValue, nextValue) {
    return nextValue !== "full_access" || oldValue === "full_access" || await uiConfirm("危险：Full Access 允许文件/数据库修改、邮件外发和第三方写入，无需逐次确认。确认仅为可信助手开启？");
  }

  async function providerEditor(provider = null) {
    const p = provider || {id:"",name:"",base_url:"",api:"openai-completions",models:[]};
    let selected = new Set(p.models || []), candidates = [...selected].map(id => ({id,name:id})), pid = p.id;
    const d = modal(pid ? "配置 Provider" : "添加 Provider", `<div class="ag-form">
      <label><span>名称</span><input class="control" id="ag-p-name" value="${h(p.name)}"></label>
      <label><span>协议</span><select class="control" id="ag-p-api">${(registry.wires || []).map(w => `<option value="${h(w.id)}" ${w.id === p.api ? "selected" : ""}>${h(w.label)}</option>`).join("")}</select></label>
      <label class="ag-wide"><span>接口 URL（包含 /v1 等 API 前缀）</span><input class="control" id="ag-p-url" value="${h(p.base_url)}" placeholder="https://api.example.com/v1"></label>
      <label class="ag-wide"><span>API Key${p.key_present ? "（已保存，留空不修改）" : "（本机接口可留空）"}</span><div class="ag-row"><input class="control" id="ag-p-key" type="password" autocomplete="off" style="flex:1" placeholder="${p.key_present ? "已保存，留空不修改" : "API Key"}"><button class="btn" id="ag-p-show">显示</button><button class="btn ghost" id="ag-p-clear">清空 Key</button></div></label>
      <div class="ag-wide ag-row"><button class="btn btn-primary" id="ag-p-save">保存配置</button><button class="btn" id="ag-p-discover">保存并拉取模型</button></div>
      <div class="ag-wide ag-stack"><span>模型（勾选后可供所有助手、Leader 和团队成员使用）</span><input class="control" id="ag-p-filter" placeholder="搜索全部模型，不截断列表"><div class="ag-list" id="ag-p-candidates"></div>
      <div class="ag-row"><input class="control" id="ag-p-manual" style="flex:1" placeholder="手动输入模型 ID"><button class="btn" id="ag-p-add">添加模型</button></div>
      <div class="ag-row"><button class="btn" id="ag-p-apply">保存所选模型</button><button class="btn" id="ag-p-auto">自动选择全部并设置默认</button></div></div></div>`);
    let keyDirty = false;
    const status = d.querySelector("[role=status]");
    el("ag-p-key").oninput = () => { keyDirty = true; };
    el("ag-p-clear").onclick = () => { el("ag-p-key").value = ""; keyDirty = true; status.textContent = "保存配置后将清空 Key。"; };
    el("ag-p-show").onclick = async () => {
      try {
        const input = el("ag-p-key");
        if (input.type === "password") {
          if (pid && !keyDirty && !input.value) input.value = (await api(endpoint + "provider-secret?id=" + encodeURIComponent(pid))).api_key;
          input.type = "text";
        } else input.type = "password";
      } catch (e) { error(status,e); }
    };
    const save = async () => {
      const data = {id:pid,name:el("ag-p-name").value,base_url:el("ag-p-url").value,api:el("ag-p-api").value,api_key: keyDirty || !pid ? el("ag-p-key").value : null};
      pid = (await request("provider-save",data)).provider.id;
      keyDirty = false;
      await saved();
      status.className = "ag-status"; status.textContent = "配置已保存。";
    };
    const draw = () => {
      const search = el("ag-p-filter").value.toLowerCase();
      el("ag-p-candidates").innerHTML = candidates.filter(c => (c.id + " " + c.name).toLowerCase().includes(search)).map(c => `<label class="ag-model"><input type="checkbox" value="${h(c.id)}" ${selected.has(c.id) ? "checked" : ""}><span>${h(c.name || c.id)}${c.name !== c.id ? `<br><small>${h(c.id)}</small>` : ""}</span></label>`).join("") || `<p class="hint">尚无模型；先拉取或手动添加。</p>`;
      el("ag-p-candidates").querySelectorAll("input").forEach(i => i.onchange = () => i.checked ? selected.add(i.value) : selected.delete(i.value));
    };
    el("ag-p-filter").oninput = draw;
    draw();
    el("ag-p-save").onclick = async () => { try { await save(); renderModels(el("set-models") || el("main")); } catch (e) { error(status,e); } };
    el("ag-p-discover").onclick = async () => {
      try {
        await save(); status.textContent = "正在拉取模型…";
        const r = await request("provider-discover",{id:pid});
        candidates = [...new Map([...candidates,...r.candidates].map(c => [c.id,c])).values()];
        draw(); status.textContent = `已拉取 ${r.candidates.length} 个模型，可手动勾选或自动选择。`;
      } catch (e) { error(status,e); }
    };
    el("ag-p-add").onclick = () => {
      const id = el("ag-p-manual").value.trim(); if (!id) return;
      selected.add(id); if (!candidates.some(c => c.id === id)) candidates.push({id,name:id});
      el("ag-p-manual").value = ""; draw();
    };
    const apply = async auto => {
      try {
        await save();
        if (auto) {
          if (!candidates.length) candidates = (await request("provider-discover",{id:pid})).candidates;
          selected = new Set(candidates.map(c => c.id));
        }
        await request("provider-models",{id:pid,models:[...selected],auto});
        await saved(); d.close(); render();
      } catch (e) { error(status,e); }
    };
    el("ag-p-apply").onclick = () => apply(false);
    el("ag-p-auto").onclick = () => apply(true);
  }

  renderModels = async function(container) {
    if (!S.loaded || !container) return;
    const expectedRoute = route(), epoch = ++viewEpoch;
    try {
      if (!registry) await load();
      if (!container.isConnected || route() !== expectedRoute || epoch !== viewEpoch) return;
      container.innerHTML = `<div class="ag-head"><div><h3>模型与 Provider</h3><p class="hint">先选择 Provider 配置，再拉取或手动添加模型。已配置的模型可在助手和团队中选择。</p></div></div>
        <div class="card ag-row"><label style="flex:1">支持的 Provider <select class="control" id="ag-provider-select"><option value="">选择 Provider…</option>${registry.catalog.map(p => `<option value="${h(p.id)}">${h(p.name)}${p.supported ? "" : "（请改选兼容协议）"}</option>`).join("")}<option value="custom">自定义兼容接口</option></select></label><button class="btn btn-primary" id="ag-provider-add">配置</button></div>
        <h4 style="margin-top:20px">可选模型 · ${S.models.length}</h4><div class="ag-model-grid">${S.models.map(mm => `<div class="card"><b>${h(mm.name)}</b><p class="ag-meta">${h(mm.api || "openai-completions")}</p><button class="btn ghost" data-ag-active="${h(mm.id)}">${S.active?.id === mm.id ? "当前默认" : "设为默认"}</button></div>`).join("") || `<p class="hint">还没有配置模型。请先添加 Provider。</p>`}</div>
        <h4 style="margin-top:24px">已配置的 Provider · ${registry.providers.length}</h4><div class="ag-grid">${registry.providers.map(p => `<article class="card"><div class="ag-head"><b>${h(p.name)}</b><span class="pill">${p.models.length} 模型</span></div><p class="ag-meta">${h(p.base_url)}<br>${h(p.api)} · Key ${p.key_invalid ? `<span style="color:var(--err,#bd3f34);font-weight:600">⚠️ 解密失败（请重填）</span>` : (p.key_present ? "已保存" : "未设置")}</p><div class="ag-row"><button class="btn" data-ag-provider="${h(p.id)}">查看 / 编辑</button><button class="btn ghost" data-ag-provider-del="${h(p.id)}">移除</button></div></article>`).join("")}</div>`;
      el("ag-provider-add").onclick = () => {
        const id = el("ag-provider-select").value;
        if (!id) return uiNotify("请先选择 Provider。");
        const configured = registry.providers.find(p => p.id === id);
        const c = registry.catalog.find(p => p.id === id);
        providerEditor(configured || (c ? {...c,api:c.supported ? c.api : "openai-completions",models:[]} : null));
      };
      container.querySelectorAll("[data-ag-provider]").forEach(b => b.onclick = () => providerEditor(registry.providers.find(p => p.id === b.dataset.agProvider)));
      container.querySelectorAll("[data-ag-provider-del]").forEach(b => b.onclick = async () => { if (!await uiConfirm("移除此 Provider 和已采用模型？助手中原来的模型绑定将需要重新选择。")) return; try { await request("provider-delete",{id:b.dataset.agProviderDel}); await saved(); render(); } catch(e) { uiNotify(e.message); } });
      container.querySelectorAll("[data-ag-active]").forEach(b => b.onclick = async () => { try { await api("/api/models/active",{id:b.dataset.agActive}); await saved(); render(); } catch(e) { uiNotify(e.message); } });
    } catch(e) { error(container,e); }
  };

  function assistantEditor(a) {
    let prompts = [];
    try { prompts = Array.isArray(a.prompts) ? a.prompts : JSON.parse(a.prompts || "[]"); } catch (_) { /* Legacy malformed prompts remain editable. */ }
    const d = modal(a.id ? "编辑助手" : "新建助手", `<div class="ag-form"><label><span>名称</span><input class="control" id="ag-a-name" value="${h(a.name)}"></label><label><span>头像</span><input class="control" id="ag-a-avatar" value="${h(a.avatar)}"></label><label class="ag-wide"><span>描述</span><input class="control" id="ag-a-desc" value="${h(a.description)}"></label>${policyFields(a,"ag-a")}<label class="ag-wide"><span>规则（进入系统提示）</span><textarea class="control" rows="7" id="ag-a-rules">${h(a.rules)}</textarea></label><label class="ag-wide"><span>推荐问题（一行一条）</span><textarea class="control" rows="3" id="ag-a-prompts">${h(prompts.join("\n"))}</textarea></label><label class="ag-wide ag-row"><span><input type="checkbox" id="ag-a-enabled" ${a.enabled !== 0 ? "checked" : ""}>启用公开助手（我的助手始终可选）</span></label><button class="btn btn-primary" id="ag-a-save">保存助手</button></div>`);
    el("ag-a-save").onclick = async () => {
      try {
        const policy = policies("ag-a"); if (!(await risk(a.permission,policy.permission))) return;
        const r = await request("assistant-save",{id:a.id,name:el("ag-a-name").value.trim() || "新助手",avatar:el("ag-a-avatar").value,description:el("ag-a-desc").value,rules:el("ag-a-rules").value,prompts:el("ag-a-prompts").value.split("\n").filter(Boolean),enabled:el("ag-a-enabled").checked,...policy});
        assistantId = r.assistant.id; await saved(); d.close(); render();
      } catch(e) { error(d.querySelector("[role=status]"),e); }
    };
  }
  renderAssistants = async function(container) {
    if (!S.loaded || !container) return;
    const expectedRoute = route(), epoch = ++viewEpoch;
    try {
      await load(); if (!container.isConnected || route() !== expectedRoute || epoch !== viewEpoch) return;
      const a = S.assistants.find(x => x.id === assistantId);
      if (a) {
        const hist = await api(endpoint + "assistant-history?id=" + encodeURIComponent(a.id));
        if (!container.isConnected || route() !== expectedRoute || epoch !== viewEpoch) return;
        container.innerHTML = `<div class="ag-head"><div><p class="inst-label">ASSISTANT</p><h3>${h(a.avatar)} ${h(a.name)}</h3><p class="hint">${h(a.description)}</p></div><div class="ag-row"><button class="btn" id="ag-a-back">返回</button><button class="btn" id="ag-a-edit">编辑</button><button class="btn btn-primary" id="ag-a-chat" ${eligible(a) ? "" : "disabled"}>新建会话</button></div></div><div class="ag-grid"><div class="card"><h4>运行配置</h4><p>模型：${h(S.models.find(m => m.id === a.model_id)?.name || "自动选择 / 当前模型")}</p><p>推理：${h(effortLabels[a.reasoning] || "Agent 决定")} · 权限：${h(permissionLabels[a.permission] || "Agent 决定")}</p><p>${a.source === "user" ? "我的助手 · 始终可选" : "公开助手 · " + (a.enabled ? "已启用" : "未启用")}</p>${a.source !== "user" ? `<button class="btn" id="ag-a-toggle">${a.enabled ? "停用" : "启用"}</button>` : ""}</div><div class="card"><h4>规则预览</h4><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${h(a.rules || "未设置规则")}</pre></div></div><div class="card" style="margin-top:14px"><h4>历史会话 · ${hist.sessions.length}</h4><p class="hint">会话及消息保存在本地数据库，重启后仍可继续。</p><div class="ag-list">${hist.sessions.map(c => `<button class="btn" data-ag-history="${h(c.id)}">${h(c.title)} · ${h(fmtDate(c.updated_at))}</button>`).join("") || `<p class="hint">还没有会话。点击“新建会话”开始。</p>`}</div></div><div class="ag-row" id="ag-assistant-integrations" style="margin-top:14px"></div>`;
        el("ag-a-back").onclick = () => { assistantId = null; render(); };
        el("ag-a-edit").onclick = () => assistantEditor(a);
        const actions = container.querySelector(".ag-head .ag-row");
        const duplicate = document.createElement("button"); duplicate.className = "btn"; duplicate.textContent = "复制为我的";
        duplicate.onclick = () => assistantEditor({...a,id:"",source:"user",name:a.name + "（我的）",enabled:1}); actions.append(duplicate);
        if (a.source === "user") {
          const remove = document.createElement("button"); remove.className = "btn ghost"; remove.textContent = "删除助手";
          remove.onclick = async () => { if (!await uiConfirm("删除此助手？已有会话和历史消息仍保留。")) return; try { await api("/api/assistant/delete",{id:a.id}); assistantId = null; await saved(); render(); } catch(e) { uiNotify(e.message); } }; actions.append(remove);
        }
        el("ag-a-chat").onclick = async () => { try { await useAssistant(a.id === "builtin-steward" ? "" : a.id, a.name); } catch(e) { uiNotify(e.message); } };
        if (el("ag-a-toggle")) el("ag-a-toggle").onclick = async () => { try { await request("assistant-save",{id:a.id,enabled:!a.enabled}); await saved(); render(); } catch(e) { uiNotify(e.message); } };
        container.querySelectorAll("[data-ag-history]").forEach(b => b.onclick = async () => { curChat = b.dataset.agHistory; localStorage.setItem("rw-chat",curChat); await refreshState(false); openTab("agent"); });
        document.dispatchEvent(new CustomEvent("rw:assistant-detail",{detail:{assistant:a,container}}));
        return;
        }
      const list = S.assistants
        .filter(a => assistantFilter === "all" || (assistantFilter === "user" ? a.source === "user" : (assistantFilter === "public" ? a.source !== "user" : true)))
        .sort((x, y) => {
          if (x.id === "builtin-steward") return -1;
          if (y.id === "builtin-steward") return 1;
          return (x.sort_order ?? 0) - (y.sort_order ?? 0);
        });
      container.innerHTML = `<div class="ag-head"><div><p class="inst-label">EXPERTS</p><h3>专家</h3><p class="hint">所有我的专家和已启用系统专家均可被选择；没有数量截断。点击卡片右上角可立即启用/停用。</p></div><div class="ag-row"><button class="btn btn-sm" id="ag-as-all-on">全部启用</button><button class="btn btn-sm ghost" id="ag-as-all-off">全部停用</button><button class="btn btn-primary" id="ag-assistant-new">＋ 新建专家</button></div></div><div class="ag-row">${[["all","全部专家"],["public","系统专家"],["user","我的专家"]].map(([id,label]) => `<button class="btn ${assistantFilter === id ? "btn-primary" : ""}" data-ag-as-filter="${id}">${label}</button>`).join("")}</div><div class="ag-grid">${list.map(a => `<article class="card"><div style="display:flex;align-items:center;justify-content:space-between;gap:8px"><h4>${a.id === "builtin-steward" ? `<img src="${Q_AV}" alt="智研管家" style="width:22px;height:22px;object-fit:contain;vertical-align:-4px;margin-right:2px">` : h(a.avatar || "👨‍🔬")} ${h(a.name)}</h4><button class="as-toggle ${a.enabled !== 0 ? "on" : "off"}" data-ag-as-toggle="${h(a.id)}" title="${a.enabled !== 0 ? "已启用（点击停用）" : "未启用（点击启用）"}"><span class="as-knob"></span><span class="as-toggle-txt">${a.enabled !== 0 ? "已启用" : "未启用"}</span></button></div><p class="ag-meta">${h(a.description || "（暂无专长描述）")}</p><p class="ag-meta">${a.source === "user" ? "自建 · 始终可选" : (a.enabled ? "系统 · 已启用" : "系统 · 未启用")} · ${h(S.models.find(m => m.id === a.model_id)?.name || "自动模型")}</p><div class="ag-row" style="margin-top:8px;gap:8px"><button class="btn btn-primary as-go" data-ag-chat-as="${h(a.id)}">💬 去对话</button><button class="btn" data-ag-assistant="${h(a.id)}">查看 / 编辑</button></div></article>`).join("") || `<p class="hint">尚无专家，点击“新建专家”。</p>`}</div>`;
      el("ag-assistant-new").onclick = () => assistantEditor({id:"",name:"",rules:"",enabled:1});
      const bOn = el("ag-as-all-on"), bOff = el("ag-as-all-off");
      if (bOn) bOn.onclick = async () => {
        const ids = list.map(x => x.id);
        if (!ids.length) return;
        try {
          await api("/api/assistant/batch-toggle", {ids, enabled: 1});
          await saved();
          render();
        } catch(err) { uiNotify("批量启用失败：" + err.message); }
      };
      if (bOff) bOff.onclick = async () => {
        const ids = list.map(x => x.id);
        if (!ids.length) return;
        try {
          await api("/api/assistant/batch-toggle", {ids, enabled: 0});
          await saved();
          render();
        } catch(err) { uiNotify("批量停用失败：" + err.message); }
      };
      container.querySelectorAll("[data-ag-as-filter]").forEach(b => b.onclick = () => { assistantFilter = b.dataset.agAsFilter; render(); });
      container.querySelectorAll("[data-ag-assistant]").forEach(b => b.onclick = () => { assistantId = b.dataset.agAssistant; render(); });
      container.querySelectorAll("[data-ag-as-toggle]").forEach(b => b.onclick = async (e) => {
        e.stopPropagation();
        const aid = b.dataset.agAsToggle;
        const a = S.assistants.find(x => x.id === aid);
        if (!a) return;
        const next = a.enabled === 0 ? 1 : 0;
        try {
          await api("/api/assistant/toggle", {id: aid, enabled: next});
          a.enabled = next;
          await saved();
          render();
        } catch(err) { uiNotify("切换状态失败：" + err.message); }
      });
      container.querySelectorAll("[data-ag-chat-as]").forEach(b => b.onclick = async (e) => {
        e.stopPropagation();
        const aid = b.dataset.agChatAs;
        const targetAid = aid === "builtin-steward" ? "" : aid;
        if (typeof useAssistant === "function") {
          await useAssistant(targetAid);
        } else {
          try {
            const r = await api("/api/chat/create", {});
            const cid = r.id || r.chat?.id;
            await api("/api/chat/assistant", {id: cid, assistant_id: targetAid});
            curChat = cid; localStorage.setItem("rw-chat", cid);
            await refreshState(false);
            openTab("agent");
          } catch(err) { uiNotify("开启对话失败：" + err.message); }
        }
      });
    } catch(e) { error(container,e); }
  };

  async function memberEditor(tid, member = {}) {
    const pool = registry.selectable || [];
    const d = modal(member.id ? "编辑团队角色" : "添加团队成员", `<div class="ag-form"><label><span>专家</span><select class="control" id="ag-m-assistant"><option value="">使用自定义角色</option>${pool.map(a => `<option value="${h(a.id)}" ${a.id === member.assistant_id ? "selected" : ""}>${h(a.name)}</option>`).join("")}</select></label><label><span>角色名称</span><input class="control" id="ag-m-name" value="${h(member.name)}"></label>${policyFields(member,"ag-m")}<label class="ag-wide"><span>团队内职责（叠加专家规则）</span><textarea class="control" id="ag-m-prompt" rows="5">${h(member.prompt)}</textarea></label><button class="btn btn-primary" id="ag-m-save">保存角色</button>${member.id && member.role !== "leader" ? `<button class="btn" id="ag-m-leader">设为 Leader</button><button class="btn ghost" id="ag-m-remove">移出团队</button>` : ""}</div>`);
    el("ag-m-assistant").onchange = () => { const a = pool.find(x => x.id === el("ag-m-assistant").value); if (a) { el("ag-m-name").value = a.name; el("ag-m-prompt").value = a.rules || ""; } };
    el("ag-m-save").onclick = async () => {
      try { const policy = policies("ag-m"); if (!(await risk(member.permission,policy.permission))) return; await request("member-save",{team_id:tid,member_id:member.id,assistant_id:el("ag-m-assistant").value,name:el("ag-m-name").value,prompt:el("ag-m-prompt").value,...policy}); d.close(); teamList = null; render(); } catch(e) { error(d.querySelector("[role=status]"),e); }
    };
    if (el("ag-m-leader")) el("ag-m-leader").onclick = async () => { try { await request("leader",{team_id:tid,member_id:member.id}); d.close(); render(); } catch(e) { error(d.querySelector("[role=status]"),e); } };
    if (el("ag-m-remove")) el("ag-m-remove").onclick = async () => { try { await api("/api/team/chat/remove",{member_id:member.id}); d.close(); render(); } catch(e) { error(d.querySelector("[role=status]"),e); } };
  }
  function teamEditor(t = {}) {
    const d = modal(t.id ? "编辑团队" : "新建持久团队", `<div class="ag-stack"><label>团队名称<input class="control" id="ag-t-name" value="${h(t.name)}"></label><label>团队描述<textarea class="control" id="ag-t-desc" rows="4">${h(t.description)}</textarea></label><button class="btn btn-primary" id="ag-t-save">保存团队</button></div>`);
    el("ag-t-save").onclick = async () => { try { const r = await request("team-save",{id:t.id,name:el("ag-t-name").value,description:el("ag-t-desc").value,project:curTask||""}); teamScope = r.team.id; localStorage.setItem("rw-team-chat",teamScope); teamSession = ""; teamList = null; d.close(); render(); } catch(e) { error(d.querySelector("[role=status]"),e); } };
  }
  const renderTeamSettings = async function(container) {
    if (!S.loaded || !container) return;
    const expectedRoute = route(), epoch = ++viewEpoch;
    clearTimeout(teamTick);
    try {
      await load();
      const all = (await api("/api/teams?project=" + encodeURIComponent(curTask || ""))).teams || [];
      if (!all.some(t => t.id === teamScope)) teamScope = all[0]?.id || "";
      if (!teamScope) { container.innerHTML = `<div class="card"><h3>团队配置</h3><button class="btn" id="ag-team-new">新建团队</button></div>`; el("ag-team-new").onclick = () => teamEditor(); return; }
      const data = await api(endpoint + "team-detail?id=" + encodeURIComponent(teamScope) + (teamSession ? "&session_id=" + encodeURIComponent(teamSession) : ""));
      if (!container.isConnected || route() !== expectedRoute || epoch !== viewEpoch) return;
      const t = data.team || all.find(x => x.id === teamScope) || {}, running = data.running;
      const selected = data.session || {};
      container.innerHTML = `<div class="ag-head"><div><p class="inst-label">TEAMS · ${all.length}</p><h3>团队管理与角色配置</h3><p class="hint">在此创建与编排团队。设置 Leader、成员角色、指定模型、推理强度及 Full Access 权限。日常团队协作对话请直接前往主界面「团队」研讨室。</p></div><div class="ag-row"><button class="btn ghost" id="ag-team-back">← 返回平面</button><button class="btn btn-primary" id="ag-team-new">新建团队</button><button class="btn" id="ag-team-go-chat">进入团队对话 💬</button></div></div><div class="ag-team"><nav class="ag-team-nav">${all.map(t => `<button class="btn ${t.id === teamScope ? "btn-primary" : ""}" data-ag-team="${h(t.id)}">${h(t.name)}<br><small>${t.kind === "custom" ? "持久团队" : t.kind === "fixed" ? "预设团队" : "机动团队"}</small></button>`).join("")}</nav><section><div class="card"><div class="ag-head"><div><h3>${h(t.name)}</h3><p class="hint">${h(t.description)}</p></div><div class="ag-row"><button class="btn" id="ag-team-edit">编辑团队</button>${t.kind === "temp" ? `<button class="btn ghost" id="ag-team-dissolve" style="color:var(--err)">解散</button>` : ""}</div></div><div class="ag-grid">${data.members.map(m => `<button class="card ag-member" data-ag-member="${h(m.id)}" style="text-align:left"><b>${m.role === "leader" ? "Leader · " : "成员 · "}${h(m.name)}</b><p class="ag-meta">${h(m.model_id ? S.models.find(x => x.id === m.model_id)?.name || "模型已移除" : "跟随助手 / 当前模型")}</p><span class="ag-meta">${h(effortLabels[m.reasoning] || "Agent 决定")} · ${h(permissionLabels[m.permission] || "Agent 决定")}</span></button>`).join("")}</div><button class="btn" id="ag-member-new" style="margin-top:12px">添加成员（全部可用助手）</button></div><div class="card" style="margin-top:12px"><div class="ag-row"><label style="flex:1">会话历史记录<select class="control" id="ag-team-session">${(data.sessions || []).map(s => `<option value="${h(s.id)}" ${s.id === selected.id ? "selected" : ""}>${h(s.title)} · ${h(fmtDate(s.updated_at))}${s.active ? " · 当前" : ""}</option>`).join("")}</select></label><button class="btn" id="ag-session-new">新建会话</button></div><p class="hint">团队对话请在主界面的「团队」研讨室进行全屏沉浸交流。</p></div></section></div>`;
      el("ag-team-back").onclick = () => { window.__teamMode = ""; render(); };
      el("ag-team-new").onclick = () => teamEditor();
      el("ag-team-edit").onclick = () => teamEditor(t);
      if (el("ag-team-dissolve")) el("ag-team-dissolve").onclick = async () => { if (!await uiConfirm("解散这个机动团队？")) return; try { await api("/api/teams/dissolve",{id:teamScope}); teamScope = ""; render(); } catch(e) { uiNotify(e.message); } };
      el("ag-team-go-chat").onclick = () => { window.__teamMode = "chat"; render(); };
      el("ag-member-new").onclick = () => memberEditor(teamScope);
      container.querySelectorAll("[data-ag-team]").forEach(b => b.onclick = () => { teamScope = b.dataset.agTeam; teamSession = ""; localStorage.setItem("rw-team-chat",teamScope); render(); });
      container.querySelectorAll("[data-ag-member]").forEach(b => b.onclick = () => memberEditor(teamScope,data.members.find(m => m.id === b.dataset.agMember)));
      el("ag-team-session").onchange = async e => { try { teamSession = e.target.value; await request("team-session",{team_id:teamScope,session_id:teamSession}); render(); } catch(e) { uiNotify(e.message); } };
      el("ag-session-new").onclick = async () => { try { const r = await request("team-session",{team_id:teamScope,create:true}); teamSession = r.session.id; render(); } catch(e) { uiNotify(e.message); } };
    } catch(e) { error(container,e); }
  };

  const originalRenderTeam = typeof renderTeam === "function" ? renderTeam : null;
  renderTeam = function(container) {
    if (window.__teamMode === "manage") return renderTeamSettings(container);     // 修改编制
    if (window.__teamMode === "chat" && originalRenderTeam) return originalRenderTeam(container);
    return renderTeamOffice(container);                                          // 一层平面
  };

  const originalRenderAgent = renderAgent;
  renderAgent = function(container) {
    originalRenderAgent(container);
    const pick = el("assist"), chat = (S.chats || []).find(c => c.id === curChat);
    if (pick) pick.innerHTML = `<option value="">智研管家（全局）</option>${(S.assistants || []).filter(a => eligible(a) && a.id !== "builtin-steward").map(a => `<option value="${h(a.id)}" ${chat?.assistant_id === a.id ? "selected" : ""}>${h(a.name)}</option>`).join("")}`;
    const model = el("modpick");
    if (model && chat) {
      const bound = S.assistants.find(a => a.id === chat.assistant_id);
      model.innerHTML = modelOptions(chat.model_id || bound?.model_id || "");
      model.onchange = async () => { try { await request("runtime-save",{id:curChat,model_id:model.value}); await refreshState(false); } catch(e) { uiNotify(e.message); } };
    }
    const row = container.querySelector(".at-tools-l");
    if (row && chat) {
      const bound = S.assistants.find(a => a.id === chat.assistant_id) || {};
      const controls = document.createElement("span"); controls.className = "ag-row";
      controls.innerHTML = `<label class="at-sel" title="当前会话推理强度">⚡<span class="k">推理</span><select id="ag-chat-reasoning">${options(efforts,chat.reasoning !== "auto" ? chat.reasoning : bound.reasoning || "auto",effortLabels)}</select></label><label class="at-sel" title="当前会话权限（Full Access 有风险）">🛡<span class="k">权限</span><select id="ag-chat-permission">${options(permissions,chat.permission !== "auto" ? chat.permission : bound.permission || "auto",permissionLabels)}</select></label>`;
      row.append(controls);
      for (const key of ["reasoning","permission"]) el("ag-chat-" + key).onchange = async e => {
        if (key === "permission" && !(await risk(chat.permission,e.target.value))) { e.target.value = chat.permission || "auto"; return; }
        try { await request("runtime-save",{id:curChat,[key]:e.target.value}); await refreshState(false); } catch(err) { uiNotify(err.message); }
      };
      const hint = container.querySelector(".at-hint:not(.at-project-scope)"); if (hint) hint.textContent = "Enter 发送 · Shift + Enter 换行 · 推理和权限默认由 Agent 决定；Full Access 会放开写入与外发确认，请谨慎启用。";
    }
  };
  const originalSettings = renderSettings;
  renderSettings = function(container) {
    if (setSec === "teams") {
      container.innerHTML = `<div class="page-head"><h3>设置 / 团队</h3><button class="btn" id="ag-settings-back">返回设置</button></div><div id="ag-settings-teams"></div>`;
      el("ag-settings-back").onclick = () => { setSec = "theme"; render(); };
      renderTeamSettings(el("ag-settings-teams"));
      return;
    }
    originalSettings(container);
    const nav = container.querySelector(".set-nav");
    if (nav && !nav.querySelector("[data-ag-set-team]")) {
      const b = document.createElement("button"); b.className = "set-row"; b.dataset.agSetTeam = "1"; b.textContent = "团队配置";
      b.onclick = () => { setSec = "teams"; render(); }; nav.append(b);
    }
  };
  window.renderTeamSettings = renderTeamSettings;
  window.AgentSettings = {eligible,openAssistant(id) { assistantId = id; openTab("assistants"); },openProvider:providerEditor};
})();
