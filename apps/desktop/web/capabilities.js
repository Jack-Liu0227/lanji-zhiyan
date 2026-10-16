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
    .cap-card-actions{display:flex;gap:7px;margin-top:14px;flex-wrap:wrap}.cap-table{overflow:auto;max-height:65vh;border:1px solid var(--line);border-radius:10px}
    .cap-table table{margin:0;min-width:max-content}.cap-table th{position:sticky;top:0;background:var(--bg);z-index:2}
    .cap-table th:first-child,.cap-table td:first-child{position:sticky;left:0;background:var(--bg);z-index:1;max-width:280px;white-space:normal;overflow-wrap:anywhere}
    .cap-table th:first-child{z-index:3}.cap-table input{width:18px;height:18px;cursor:pointer}
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
  function filtered(meta) {
    const needle = search.trim().toLocaleLowerCase();
    return (meta[setSec] || []).filter(item => !needle || [item.name, item.label, item.description, item.desc].some(value => String(value || "").toLocaleLowerCase().includes(needle)));
  }
  function drawMatrix(meta, items) {
    const kind = kinds[setSec];
    const assistants = [{id: "*", name: "默认 / 未绑定助手"}, ...meta.assistants];
    return `<p class="hint">勾选立即保存并在服务端执行时校验；默认启用，可为每个助手独立关闭。横向滚动查看全部 ${meta.assistants.length} 个助手，没有截断。</p><div class="cap-table"><table><thead><tr><th>${labels[setSec]}</th>${assistants.map(a => `<th title="${e(a.id)}">${e(a.name)}${a.enabled === 0 ? "（未启用）" : ""}</th>`).join("")}</tr></thead><tbody>${items.map(item => `<tr><td><strong>${e(item.label || item.name)}</strong>${item.scope ? `<br><span class="hint">${e(item.scope)}</span>` : ""}</td>${assistants.map(a => `<td style="text-align:center"><input type="checkbox" data-cap-binding data-kind="${kind}" data-item="${e(item.id)}" data-assistant="${e(a.id)}" aria-label="${e(a.name + ' / ' + item.name)}" ${checked(meta, a.id, kind, item.id) ? "checked" : ""}></td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
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
    host.innerHTML = `<div class="card"><h3>${labels[section]} <span class="pill">${items.length} / ${(meta[section] || []).length}</span></h3><div class="cap-toolbar"><select class="control" id="cap-project" aria-label="项目技能作用域"><option value="">用户技能（全局）</option>${(S.tasks || []).map(task => `<option value="${e(task.id)}" ${task.id === projectId ? "selected" : ""}>${e(task.title)}</option>`).join("")}</select><input class="control" id="cap-search" value="${e(search)}" placeholder="搜索名称、说明" aria-label="搜索能力"><button class="btn ${view === "list" ? "" : "ghost"}" data-cap-view="list">列表</button><button class="btn ${view === "matrix" ? "" : "ghost"}" data-cap-view="matrix">助手启用矩阵</button><button class="btn ghost" id="cap-refresh">刷新</button>${section === "mcp" ? `<button class="btn" id="cap-mcp-json">JSON 编辑 / 密钥</button>` : ""}</div><p class="hint">${section === "skills" ? "默认读取用户 ~/.agents/skills，选择项目后叠加项目 .agents/skills；同名项目技能优先。预览编辑会直接写入显示的 SKILL.md。" : section === "tools" ? "Tools 与 MCP 独立管理。可以编辑内置工具的显示名称及给模型的说明；工具实现与参数结构只读。" : "保存 JSON 不启动进程。仅点击连接或助手实际调用已发现工具时执行本地命令/联网；请只使用可信 MCP。"}</p>${section === "mcp" ? `<p class="hint">凭据保护：${e(meta.storage_security)}。环境变量、请求头、命令参数和密钥默认隐藏。</p>` : ""}</div>${view === "matrix" ? drawMatrix(meta, items) : drawCards(meta, items)}${section === "skills" ? `<div class="card cap-hint"><h4>技能搜索目录（后者优先）</h4>${meta.skill_roots.map(root => `<p class="hint"><code>${e(root.path)}</code> · ${e(root.scope)} · ${root.exists ? "存在" : "尚未创建"}</p>`).join("")}</div>` : ""}<p class="cap-error" data-error role="alert"></p>`;
    host.querySelector("#cap-project").onchange = event => { projectId = event.target.value; load(); };
    host.querySelector("#cap-search").oninput = event => { search = event.target.value; paint(host, meta); };
    host.querySelectorAll("[data-cap-view]").forEach(button => button.onclick = () => { view = button.dataset.capView; paint(host, meta); });
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
    card.innerHTML = `<h3>助手能力</h3><p class="hint">查看并配置 ${e(assistant.name)} 的 Skills、Tools、MCP 启用状态。服务端会校验每次工具执行权限。</p><div class="cap-card-actions"><button class="btn ghost" data-open-cap>配置能力</button></div>`;
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
