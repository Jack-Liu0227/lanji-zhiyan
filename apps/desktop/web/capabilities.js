/* Local capability editors. No MCP process is started by rendering this page. */
(() => {
  "use strict";
  const sections = new Set(["skills", "tools", "mcp"]);
  const cache = new Map();
  let projectId = "", view = "list", search = "", request = 0, activeDialogClose = null;
  const e = value => esc(String(value ?? ""));
  const context = () => projectId ? {project_id: projectId} : {};
  const query = () => projectId ? "?project_id=" + encodeURIComponent(projectId) : "";
  const labels = {skills: "Skills", tools: "Tools", mcp: "MCP"};
  const kinds = {skills: "skill", tools: "tool", mcp: "mcp"};
  const styles = document.createElement("style");
  styles.textContent = `
    .cap-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
    .cap-toolbar .control{min-width:150px;max-width:270px}.cap-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
    .cap-grid .card{margin:0;min-width:0}.cap-grid h4{margin:0 0 8px;overflow-wrap:anywhere}.cap-grid p{white-space:pre-wrap;overflow-wrap:anywhere}
    .cap-card-actions{display:flex;gap:7px;margin-top:14px;flex-wrap:wrap}
    .cap-matrix-header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px}
    .cap-cats{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
    .cap-cat-pill{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:3px 10px;font-size:12px;font-weight:600;cursor:pointer;color:var(--muted);transition:all .15s}
    .cap-cat-pill:hover{border-color:var(--accent);color:var(--fg)}
    .cap-cat-pill.on{background:color-mix(in srgb,var(--accent) 15%,transparent);border-color:var(--accent);color:var(--accent)}
    .cap-th-corner{display:flex;align-items:center;justify-content:space-between;gap:8px}
    .cap-th-col{display:flex;flex-direction:column;align-items:center;gap:4px;white-space:nowrap;padding:4px 0}
    .cap-row-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
    .cap-row-title{min-width:0;line-height:1.3}
    .cap-bulk-btns{display:flex;align-items:center;gap:4px;flex-shrink:0}
    .cap-btn-mini{font-size:10.5px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);background:var(--panel);cursor:pointer;line-height:1.4;transition:all .15s}
    .cap-btn-mini.on:hover{background:color-mix(in srgb,var(--accent) 20%,transparent);border-color:var(--accent);color:var(--accent)}
    .cap-btn-mini.off:hover{background:color-mix(in srgb,var(--err,#bd3f34) 20%,transparent);border-color:var(--err,#bd3f34);color:var(--err,#bd3f34)}
    .cap-table{overflow:auto;max-height:calc(100vh - 280px);min-height:360px;border:1px solid var(--line);border-radius:10px;position:relative}
    .cap-table table{margin:0;min-width:max-content;border-collapse:separate;border-spacing:0}
    .cap-table th{position:sticky;top:0;background:var(--panel2,var(--bg));z-index:2;border-bottom:1px solid var(--line);padding:8px 10px}
    .cap-table th:first-child,.cap-table td:first-child{position:sticky;left:0;background:var(--panel,var(--bg));z-index:1;min-width:260px;max-width:320px;white-space:normal;overflow-wrap:anywhere;box-shadow:1px 0 0 var(--line)}
    .cap-table th:first-child{z-index:3;background:var(--panel2,var(--bg))}
    .cap-table input{width:18px;height:18px;cursor:pointer}
    .cap-overlay{position:fixed;inset:0;z-index:9000;background:#0008;display:flex;align-items:center;justify-content:center;padding:18px}
    .cap-dialog{background:var(--panel,var(--bg));border:1px solid var(--line);border-radius:16px;padding:22px;width:min(1050px,95vw);max-height:90vh;overflow:auto;box-shadow:0 24px 80px #0005}
    .cap-dialog-head{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:14px}.cap-dialog h3{margin:0;overflow-wrap:anywhere}
    .cap-dialog textarea{width:100%;min-height:390px;resize:vertical;font-family:Consolas,monospace;line-height:1.6}.cap-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:58vh;overflow:auto}
    .cap-field{display:grid;gap:6px;margin:12px 0}.cap-error{color:var(--danger,#bd3f34);white-space:pre-wrap}.cap-hint{margin:12px 0;overflow-wrap:anywhere}
    @media(max-width:650px){.cap-overlay{padding:6px}.cap-dialog{padding:14px;max-height:96vh}.cap-dialog textarea{min-height:300px}.cap-grid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(styles);

  function notify(message, type) {
    if (typeof toast === "function") toast(message, type);
  }
  function dialog(title, html, bind) {
    activeDialogClose?.();
    const overlay = document.createElement("div");
    overlay.className = "cap-overlay";
    overlay.innerHTML = `<section class="cap-dialog" role="dialog" aria-modal="true" aria-label="${e(title)}"><div class="cap-dialog-head"><h3>${e(title)}</h3><button class="btn ghost" data-close aria-label="关闭">关闭</button></div>${html}<p class="cap-error" data-error role="alert"></p></section>`;
    const previousFocus = document.activeElement;
    function close() {
      overlay.querySelectorAll("textarea,input").forEach(field => { field.value = ""; });
      overlay.remove();
      document.removeEventListener("keydown", keydown);
      if (activeDialogClose === close) activeDialogClose = null;
      previousFocus?.focus();
    }
    function keydown(event) {
      if (event.key === "Escape") close();
      if (event.key !== "Tab") return;
      const fields = [...overlay.querySelectorAll("button,input,textarea,select,[tabindex]")].filter(node => !node.hidden && !node.disabled && node.offsetParent);
      if (!fields.length) return;
      const first = fields[0], last = fields[fields.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", keydown);
    activeDialogClose = close;
    overlay.querySelector("[data-close]").onclick = close;
    overlay.onclick = event => { if (event.target === overlay) close(); };
    document.body.appendChild(overlay);
    overlay.querySelector("button").focus();
    bind?.(overlay, close);
    return overlay;
  }
  function fail(host, error) {
    const target = host.querySelector("[data-error]");
    if (target) target.textContent = error.message || String(error);
    else notify(error.message || String(error), "error");
  }
  async function update() {
    cache.delete(projectId);
    await load();
  }
  function checked(meta, aid, kind, itemId) {
    const binding = meta.bindings.find(item => item.assistant_id === aid && item.kind === kind && item.item_id === itemId);
    if (binding) return Boolean(binding.enabled);
    const global = meta.bindings.find(item => item.assistant_id === "*" && item.kind === kind && item.item_id === itemId);
    return global ? Boolean(global.enabled) : meta.default_enabled !== false;
  }
  let currentCategory = "all";
  function itemCategory(item) {
    const text = ((item.label || "") + " " + (item.description || item.desc || "") + " " + (item.id || item.name || "")).toLowerCase();
    if (/arxiv|pubmed|scholar|search|cite|lit|bib|paper|检索|文献|引文|阅读|深度阅读/.test(text)) return "literature";
    if (/write|doc|outline|draft|review|compile|edit|写作|大纲|起草|评审|排版|正文|修改/.test(text)) return "writing";
    if (/file|folder|vault|note|library|obsidian|read|attachment|skill_read|知识|工作区|笔记|附件/.test(text)) return "workspace";
    if (/feishu|zotero|mail|hook|webhook|飞书|邮件|集成|通讯/.test(text)) return "integration";
    return "general";
  }
  const CATEGORIES = [
    {id: "all", label: "全部"},
    {id: "literature", label: "🔎 文献检索"},
    {id: "writing", label: "✍️ 论文写作"},
    {id: "workspace", label: "📚 知识与工作区"},
    {id: "integration", label: "🔌 协同集成"},
    {id: "general", label: "⚙️ 基础通用"}
  ];

  function filtered(meta) {
    const needle = search.trim().toLocaleLowerCase();
    let list = meta[setSec] || [];
    if (currentCategory !== "all") {
      list = list.filter(item => itemCategory(item) === currentCategory);
    }
    if (needle) {
      list = list.filter(item => [item.name, item.label, item.description, item.desc].some(value => String(value || "").toLocaleLowerCase().includes(needle)));
    }
    return list;
  }
  function drawMatrix(meta, items) {
    const kind = kinds[setSec];
    const assistants = [{id: "*", name: "默认 / 未绑定专家"}, ...meta.assistants];
    return `
      <div class="cap-matrix-header">
        <p class="hint" style="margin:0">勾选立即保存；支持横向/纵向/全局一键全开全关。滚轮可横纵平滑滑动，表头与能力列常驻固定。</p>
        <div class="cap-cats">
          ${CATEGORIES.map(c => `<button class="cap-cat-pill ${currentCategory === c.id ? "on" : ""}" data-cap-cat="${c.id}">${c.label}</button>`).join("")}
        </div>
      </div>
      <div class="cap-table" id="capTableWrap">
        <table>
          <thead>
            <tr>
              <th>
                <div class="cap-th-corner">
                  <span>${labels[setSec]} (${items.length})</span>
                  <div class="cap-bulk-btns">
                    <button class="cap-btn-mini on" id="cap-mat-all-on" title="将当前分类下所有项对所有专家全部启用">全开</button>
                    <button class="cap-btn-mini off" id="cap-mat-all-off" title="将当前分类下所有项对所有专家全部停用">全关</button>
                  </div>
                </div>
              </th>
              ${assistants.map(a => `<th title="${e(a.id)}">
                <div class="cap-th-col">
                  <b>${e(a.name)}${a.enabled === 0 ? "（未启用）" : ""}</b>
                  <div class="cap-bulk-btns">
                    <button class="cap-btn-mini on" data-col-toggle="${e(a.id)}" data-action="1" title="为该专家全部启用当前列">全开</button>
                    <button class="cap-btn-mini off" data-col-toggle="${e(a.id)}" data-action="0" title="为该专家全部停用当前列">全关</button>
                  </div>
                </div>
              </th>`).join("")}
            </tr>
          </thead>
          <tbody>
            ${items.length ? items.map(item => `<tr>
              <td>
                <div class="cap-row-head">
                  <div class="cap-row-title">
                    <strong>${e(item.label || item.name)}</strong>
                    ${item.scope ? `<br><span class="hint">${e(item.scope)}</span>` : ""}
                  </div>
                  <div class="cap-bulk-btns">
                    <button class="cap-btn-mini on" data-row-toggle="${e(item.id)}" data-action="1" title="为此项启用所有专家（横向全部启用）">全开</button>
                    <button class="cap-btn-mini off" data-row-toggle="${e(item.id)}" data-action="0" title="为此项停用所有专家（横向全部停用）">全关</button>
                  </div>
                </div>
              </td>
              ${assistants.map(a => `<td style="text-align:center">
                <input type="checkbox" data-cap-binding data-kind="${kind}" data-item="${e(item.id)}" data-assistant="${e(a.id)}" aria-label="${e(a.name + ' / ' + item.name)}" ${checked(meta, a.id, kind, item.id) ? "checked" : ""}>
              </td>`).join("")}
            </tr>`).join("") : `<tr><td colspan="${assistants.length + 1}" style="text-align:center;padding:24px;color:var(--muted)">当前分类下暂无项目</td></tr>`}
          </tbody>
        </table>
      </div>`;
  }
  function drawCards(meta, items) {
    if (!items.length) return `<div class="card"><h4>没有找到${labels[setSec]}</h4><p class="hint">${setSec === "skills" ? "将技能放入下方 .agents/skills 目录中的 SKILL.md；然后刷新。" : "请调整搜索条件或添加配置。"}</p></div>`;
    return `<div class="cap-grid">${items.map(item => `<div class="card"><h4>${e(item.label || item.name)}</h4><span class="pill">${e(item.scope || item.transport || "内置工具")}</span>${setSec === "mcp" ? `<span class="pill">${item.enabled ? "启用" : "停用"}</span>` : ""}<p class="hint">${e(item.description || item.desc || "暂无说明")}</p>${item.path ? `<p class="hint">${e(item.path)}</p>` : ""}<div class="cap-card-actions">${setSec === "mcp" ? `<button class="btn" data-cap-connect="${e(item.id)}">连接 / 拉取工具</button><button class="btn ghost" data-cap-mcp-preview="${e(item.id)}">工具预览 (${item.tools.length})</button>` : `<button class="btn ghost" data-cap-preview="${e(item.id)}">预览 / 编辑</button>`}</div></div>`).join("")}</div>`;
  }
  function paint(host, meta) {
    const section = setSec;
    const selected = document.activeElement?.id === "cap-search";
    const position = selected ? document.activeElement.selectionStart : 0;
    const items = filtered(meta);
    host.innerHTML = `<div class="card"><h3>${labels[section]} <span class="pill">${items.length} / ${(meta[section] || []).length}</span></h3><div class="cap-toolbar"><select class="control" id="cap-project" aria-label="项目技能作用域"><option value="">用户技能（全局）</option>${(S.tasks || []).map(task => `<option value="${e(task.id)}" ${task.id === projectId ? "selected" : ""}>${e(task.title)}</option>`).join("")}</select><input class="control" id="cap-search" value="${e(search)}" placeholder="搜索名称、说明" aria-label="搜索能力"><button class="btn ${view === "list" ? "" : "ghost"}" data-cap-view="list">列表</button><button class="btn ${view === "matrix" ? "" : "ghost"}" data-cap-view="matrix">专家启用矩阵</button><button class="btn ghost" id="cap-refresh">刷新</button>${section === "mcp" ? `<button class="btn" id="cap-mcp-json">JSON 编辑 / 密钥</button>` : ""}</div><p class="hint">${section === "skills" ? "默认读取用户 ~/.agents/skills，选择项目后叠加项目 .agents/skills；同名项目技能优先。预览编辑会直接写入显示的 SKILL.md。" : section === "tools" ? "Tools 与 MCP 独立管理。可以编辑内置工具的显示名称及给模型的说明；工具实现与参数结构只读。" : "保存 JSON 不启动进程。仅点击连接或专家实际调用已发现工具时执行本地命令/联网；请只使用可信 MCP。"}</p>${section === "mcp" ? `<p class="hint">凭据保护：${e(meta.storage_security)}。环境变量、请求头、命令参数和密钥默认隐藏。</p>` : ""}</div>${view === "matrix" ? drawMatrix(meta, items) : drawCards(meta, items)}${section === "skills" ? `<div class="card cap-hint"><h4>技能搜索目录（后者优先）</h4>${meta.skill_roots.map(root => `<p class="hint"><code>${e(root.path)}</code> · ${e(root.scope)} · ${root.exists ? "存在" : "尚未创建"}</p>`).join("")}</div>` : ""}<p class="cap-error" data-error role="alert"></p>`;
    host.querySelector("#cap-project").onchange = event => { projectId = event.target.value; load(); };
    host.querySelector("#cap-search").oninput = event => { search = event.target.value; paint(host, meta); };
    host.querySelectorAll("[data-cap-view]").forEach(button => button.onclick = () => { view = button.dataset.capView; paint(host, meta); });
    host.querySelectorAll("[data-cap-cat]").forEach(b => b.onclick = () => { currentCategory = b.dataset.capCat; paint(host, meta); });
    const matAllOn = host.querySelector("#cap-mat-all-on");
    if (matAllOn) matAllOn.onclick = async () => {
      const list = filtered(meta);
      const kind = kinds[setSec];
      const assistants = [{id: "*"}, ...meta.assistants];
      const updates = [];
      list.forEach(item => { assistants.forEach(a => { updates.push({assistant_id: a.id, kind, item_id: item.id, enabled: true}); }); });
      try {
        await api("/api/capabilities/bindings", {...context(), bindings: updates});
        updates.forEach(u => {
          meta.bindings = meta.bindings.filter(x => !(x.assistant_id === u.assistant_id && x.kind === u.kind && x.item_id === u.item_id));
          meta.bindings.push(u);
        });
        paint(host, meta);
        notify("已全部启用当前项");
      } catch (err) { fail(host, err); }
    };
    const matAllOff = host.querySelector("#cap-mat-all-off");
    if (matAllOff) matAllOff.onclick = async () => {
      const list = filtered(meta);
      const kind = kinds[setSec];
      const assistants = [{id: "*"}, ...meta.assistants];
      const updates = [];
      list.forEach(item => { assistants.forEach(a => { updates.push({assistant_id: a.id, kind, item_id: item.id, enabled: false}); }); });
      try {
        await api("/api/capabilities/bindings", {...context(), bindings: updates});
        updates.forEach(u => {
          meta.bindings = meta.bindings.filter(x => !(x.assistant_id === u.assistant_id && x.kind === u.kind && x.item_id === u.item_id));
          meta.bindings.push(u);
        });
        paint(host, meta);
        notify("已全部停用当前项");
      } catch (err) { fail(host, err); }
    };
    host.querySelectorAll("[data-row-toggle]").forEach(b => b.onclick = async () => {
      const itemId = b.dataset.rowToggle;
      const enable = b.dataset.action === "1";
      const kind = kinds[setSec];
      const assistants = [{id: "*"}, ...meta.assistants];
      const updates = assistants.map(a => ({assistant_id: a.id, kind, item_id: itemId, enabled: enable}));
      try {
        await api("/api/capabilities/bindings", {...context(), bindings: updates});
        updates.forEach(u => {
          meta.bindings = meta.bindings.filter(x => !(x.assistant_id === u.assistant_id && x.kind === u.kind && x.item_id === u.item_id));
          meta.bindings.push(u);
        });
        paint(host, meta);
        notify(enable ? "已启用该项所有专家" : "已停用该项所有专家");
      } catch (err) { fail(host, err); }
    });
    host.querySelectorAll("[data-col-toggle]").forEach(b => b.onclick = async () => {
      const aid = b.dataset.colToggle;
      const enable = b.dataset.action === "1";
      const kind = kinds[setSec];
      const list = filtered(meta);
      const updates = list.map(item => ({assistant_id: aid, kind, item_id: item.id, enabled: enable}));
      try {
        await api("/api/capabilities/bindings", {...context(), bindings: updates});
        updates.forEach(u => {
          meta.bindings = meta.bindings.filter(x => !(x.assistant_id === u.assistant_id && x.kind === u.kind && x.item_id === u.item_id));
          meta.bindings.push(u);
        });
        paint(host, meta);
        notify(enable ? "已全部启用该专家" : "已全部停用该专家");
      } catch (err) { fail(host, err); }
    });
    host.querySelector("#cap-refresh").onclick = () => update().catch(error => fail(host, error));
    host.querySelectorAll("[data-cap-binding]").forEach(input => input.onchange = async () => {
      const next = input.checked;
      input.disabled = true;
      try {
        const binding = {assistant_id: input.dataset.assistant, kind: input.dataset.kind, item_id: input.dataset.item, enabled: next};
        await api("/api/capabilities/binding", {...context(), ...binding});
        meta.bindings = meta.bindings.filter(item => !(item.assistant_id === binding.assistant_id && item.kind === binding.kind && item.item_id === binding.item_id));
        meta.bindings.push(binding);
        paint(host, meta);
      } catch (error) { input.checked = !next; input.disabled = false; fail(host, error); }
    });
    host.querySelectorAll("[data-cap-preview]").forEach(button => button.onclick = () => {
      const item = items.find(row => row.id === button.dataset.capPreview);
      if (section === "skills") editSkill(item).catch(error => fail(host, error));
      else editTool(item);
    });
    host.querySelector("#cap-mcp-json")?.addEventListener("click", () => editMcp(meta));
    host.querySelectorAll("[data-cap-connect]").forEach(button => button.onclick = async () => {
      if (!window.confirm("连接将运行配置中的本地命令（拥有当前用户权限）或访问远程 MCP，并可能读取敏感数据。确认信任该服务器并继续？")) return;
      button.disabled = true;
      button.textContent = "正在连接…";
      try {
        const result = await api("/api/capabilities/mcp/discover", {server: button.dataset.capConnect, confirm: true});
        notify(`已发现 ${result.tools.length} 个工具`);
        await update();
      } catch (error) { fail(host, error); button.disabled = false; button.textContent = "重试连接"; }
    });
    host.querySelectorAll("[data-cap-mcp-preview]").forEach(button => button.onclick = () => {
      const item = items.find(row => row.id === button.dataset.capMcpPreview);
      dialog(item.name + " · MCP 工具", `<p class="hint">${item.tools.length ? "已发现工具；助手实际调用时会重新连接服务器。" : "尚未发现工具，请先明确点击“连接 / 拉取工具”。"}</p><pre>${e(JSON.stringify(item.tools, null, 2))}</pre>`);
    });
    if (selected) {
      const field = host.querySelector("#cap-search");
      field.focus(); field.setSelectionRange(position, position);
    }
  }
  async function load() {
    const host = document.getElementById("cap-settings");
    if (!host || !sections.has(setSec) || !S.loaded) return;
    const serial = ++request, scope = projectId;
    if (cache.has(scope)) { paint(host, cache.get(scope)); return; }
    host.innerHTML = `<div class="card"><h3>${labels[setSec]}</h3><p class="hint">正在读取本机能力配置…</p></div>`;
    try {
      const meta = await api("/api/capabilities" + query());
      cache.set(scope, meta);
      if (serial === request && scope === projectId && host.isConnected && sections.has(setSec)) paint(host, meta);
    } catch (error) {
      if (serial !== request || !host.isConnected) return;
      host.innerHTML = `<div class="card"><h3>读取失败</h3><p class="cap-error">${e(error.message)}</p><button class="btn" id="cap-retry">重试</button></div>`;
      host.querySelector("#cap-retry").onclick = load;
    }
  }
  async function editSkill(item) {
    const params = new URLSearchParams({...context(), id: item.id});
    const result = await api("/api/capabilities/skill?" + params);
    dialog(item.name, `<p class="hint">${e(result.path)}</p><div class="cap-toolbar"><button class="btn ghost" data-preview>预览</button><button class="btn ghost" data-edit>编辑 SKILL.md</button></div><div data-preview-body><pre>${e(result.content)}</pre></div><textarea class="control" data-content hidden aria-label="技能文件内容">${e(result.content)}</textarea><div class="cap-card-actions"><button class="btn" data-save hidden>保存到技能文件</button></div>`, (host, close) => {
      const field = host.querySelector("[data-content]"), preview = host.querySelector("[data-preview-body]"), save = host.querySelector("[data-save]");
      host.querySelector("[data-edit]").onclick = () => { field.hidden = false; preview.hidden = true; save.hidden = false; };
      host.querySelector("[data-preview]").onclick = () => { preview.innerHTML = `<pre>${e(field.value)}</pre>`; field.hidden = true; preview.hidden = false; save.hidden = true; };
      save.onclick = async () => {
        save.disabled = true;
        try { await api("/api/capabilities/skill", {...context(), id: item.id, content: field.value}); close(); await update(); notify("技能文件已保存"); }
        catch (error) { save.disabled = false; fail(host, error); }
      };
    });
  }
  function editTool(item) {
    dialog(item.name + " · 工具元数据", `<p class="hint">工具 ID 不变，说明会实际发送给模型。修改参数或实现需要更新应用代码。</p><label class="cap-field">显示名称<input class="control" data-label value="${e(item.label)}"></label><label class="cap-field">工具说明<textarea class="control" data-description style="min-height:150px">${e(item.description)}</textarea></label><details><summary>查看参数 JSON Schema（只读）</summary><pre>${e(JSON.stringify(item.parameters, null, 2))}</pre></details><div class="cap-card-actions"><button class="btn" data-save>保存说明</button></div>`, (host, close) => {
      host.querySelector("[data-save]").onclick = async () => {
        try {
          await api("/api/capabilities/tool", {name: item.id, label: host.querySelector("[data-label]").value, description: host.querySelector("[data-description]").value});
          close(); await update(); notify("工具说明已保存");
        } catch (error) { fail(host, error); }
      };
    });
  }
  function editMcp(meta) {
    const example = {mcpServers: {"trusted-server": {command: "python", args: ["-m", "your_mcp_server"], env: {API_KEY: "replace-me"}, enabled: true}}};
    dialog("MCP · JSON 配置", `<p class="hint">支持标准 mcpServers JSON：stdio（command/args/env/cwd）或 Streamable HTTP（url/headers）。默认隐藏已有密钥；保留 ******** 不会覆盖原值，清空字段则会删除。保存不会执行命令。Windows 下使用当前用户 DPAPI 加密。</p><div class="cap-toolbar"><button class="btn ghost" data-reveal>显示已保存密钥</button><button class="btn ghost" data-hide>隐藏密钥 / 撤销草稿</button><button class="btn ghost" data-format>校验 / 格式化</button><button class="btn ghost" data-example>查看格式示例</button></div><textarea class="control" data-json spellcheck="false" aria-label="MCP JSON 配置">${e(JSON.stringify(meta.mcp_config, null, 2))}</textarea><div class="cap-card-actions"><button class="btn" data-save>保存 JSON</button></div><details data-example-body hidden><summary>格式示例（不会自动写入）</summary><pre>${e(JSON.stringify(example, null, 2))}</pre></details>`, (host, close) => {
      const field = host.querySelector("[data-json]");
      host.querySelector("[data-reveal]").onclick = async () => {
        if (!window.confirm("即将在当前屏幕显示所有 MCP 密钥和命令参数，并替换未保存草稿。请确认周围环境安全。")) return;
        try { const result = await api("/api/capabilities/mcp/reveal", {confirm: true}); field.value = JSON.stringify(result.config, null, 2); }
        catch (error) { fail(host, error); }
      };
      host.querySelector("[data-hide]").onclick = () => { field.value = JSON.stringify(meta.mcp_config, null, 2); };
      host.querySelector("[data-format]").onclick = () => {
        try { field.value = JSON.stringify(JSON.parse(field.value), null, 2); host.querySelector("[data-error]").textContent = "JSON 语法正确；完整字段校验将在保存时执行。"; }
        catch (error) { fail(host, error); }
      };
      host.querySelector("[data-example]").onclick = () => { const block = host.querySelector("[data-example-body]"); block.hidden = !block.hidden; block.open = true; };
      host.querySelector("[data-save]").onclick = async () => {
        try { await api("/api/capabilities/mcp", {config: JSON.parse(field.value)}); close(); await update(); notify("MCP 配置已保存，尚未启动服务器"); }
        catch (error) { fail(host, error); }
      };
    });
  }

  document.addEventListener("rw:assistant-detail", event => {
    cache.clear();
    const assistant = event.detail?.assistant, container = event.detail?.container;
    if (!assistant || !container) return;
    container.querySelector("[data-cap-assistant]")?.remove();
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.capAssistant = assistant.id;
    card.innerHTML = `<h3>专家能力</h3><p class="hint">查看并配置 ${e(assistant.name)} 的 Skills、Tools、MCP 启用状态。服务端会校验每次工具执行权限。</p><div class="cap-card-actions"><button class="btn ghost" data-open-cap>配置能力</button></div>`;
    container.appendChild(card);
    card.querySelector("[data-open-cap]").onclick = async () => {
      try {
        const meta = await api("/api/capabilities");
        let active = "skill";
        const host = dialog(assistant.name + " · 能力启用", `<div class="cap-toolbar"><button class="btn ghost" data-kind="skill">Skills</button><button class="btn ghost" data-kind="tool">Tools</button><button class="btn ghost" data-kind="mcp">MCP</button></div><p class="hint">当前显示用户技能；项目特有技能请在设置中选择项目后配置矩阵。勾选立即保存。</p><div data-assistant-caps></div>`, overlay => {
          function redraw() {
            const section = {skill: "skills", tool: "tools", mcp: "mcp"}[active];
            const target = overlay.querySelector("[data-assistant-caps]");
            target.innerHTML = `<div class="cap-table"><table><thead><tr><th>名称</th><th>启用</th></tr></thead><tbody>${meta[section].map(item => `<tr><td>${e(item.label || item.name)}<br><span class="hint">${e(item.description || item.desc || "")}</span></td><td><input type="checkbox" data-item="${e(item.id)}" aria-label="${e(item.name)}" ${checked(meta, assistant.id, active, item.id) ? "checked" : ""}></td></tr>`).join("")}</tbody></table></div>`;
            target.querySelectorAll("input").forEach(input => input.onchange = async () => {
              const next = input.checked, kind = active;
              input.disabled = true;
              try {
                const binding = {assistant_id: assistant.id, kind, item_id: input.dataset.item, enabled: next};
                await api("/api/capabilities/binding", binding);
                meta.bindings = meta.bindings.filter(item => !(item.assistant_id === binding.assistant_id && item.kind === binding.kind && item.item_id === binding.item_id));
                meta.bindings.push(binding);
                cache.clear();
                input.disabled = false;
              } catch (error) { input.checked = !next; input.disabled = false; fail(overlay, error); }
            });
          }
          overlay.querySelectorAll("[data-kind]").forEach(button => button.onclick = () => { active = button.dataset.kind; redraw(); });
          redraw();
        });
        host.querySelector("[data-error]").textContent = "";
      } catch (error) { notify(error.message || String(error), "error"); }
    };
  });

  const originalPanel = setPanel;
  setPanel = function () {
    return sections.has(setSec) ? `<div id="cap-settings"></div>` : originalPanel();
  };
  const originalSettings = renderSettings;
  renderSettings = function (host) {
    if (sections.has(setSec) && !setMeta) setMeta = {};
    originalSettings(host);
    if (sections.has(setSec)) load();
  };
  // Explicit refresh hook for assistant CRUD and project context changes.
  window.refreshCapabilities = () => { cache.clear(); return load(); };
})();
