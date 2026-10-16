/* Explicit connector configuration and previews; external writes are never implicit. */
(() => {
  "use strict";
  const e = value => esc(String(value ?? ""));
  let assistantId = "", projectId = "", preview = null, generation = 0;
  const style = document.createElement("style");
  style.textContent = `.integration-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.integration-grid .field{display:grid;gap:6px}.integration-actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:14px 0}.integration-result{white-space:pre-wrap;overflow-wrap:anywhere;max-height:380px;overflow:auto}.integration-docs{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}.integration-json{min-height:190px;width:100%;font-family:Consolas,monospace}.integration-error{color:var(--err);white-space:pre-wrap}.integration-status{padding:12px;border:1px solid var(--line);border-radius:9px;margin:12px 0}@media(max-width:760px){.integration-grid{grid-template-columns:1fr}}`;
  document.head.appendChild(style);
  for (const item of [["feishu", "↗", "飞书机器人"], ["zotero", "Z", "Zotero 写入与同步"]]) {
    if (!SET_SECS.some(section => section[0] === item[0])) SET_SECS.push(item);
  }
  function host() { return document.getElementById("integration-settings"); }
  function field(id) { return document.getElementById(id); }
  function error(message) { const box = field("integration-error"); if (box) box.textContent = String(message?.message || message || ""); }
  async function action(button, work) {
    button.disabled = true; error("");
    try { await work(); } catch (err) { error(err); }
    finally { button.disabled = false; }
  }
  function docs(links) {
    return `<div class="integration-docs">${Object.entries(links).map(([label, url]) => `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${e(label)} ↗</a>`).join("")}</div>`;
  }
  function optionRows(rows, selected, label) {
    return rows.map(row => `<option value="${e(row.id)}" ${row.id === selected ? "selected" : ""}>${e(row[label])}</option>`).join("");
  }
  async function loadFeishu() {
    const ticket = ++generation;
    if (!assistantId && S.assistants.length) assistantId = S.assistants[0].id;
    const result = await api("/api/feishu/config?assistant_id=" + encodeURIComponent(assistantId));
    if (ticket !== generation || setSec !== "feishu" || !host()) return;
    const cfg = result.config || {};
    host().innerHTML = `<div class="card"><p class="inst-label">ASSISTANT CONNECTION</p><h3>飞书机器人</h3>
      <p class="hint">每个助手独立配置。可使用企业自建应用机器人，也可用群聊自定义机器人 Webhook。扫码绑定由飞书官方授权页完成，不会自动创建、发布或授予应用权限。</p>
      <label class="field"><span>配置哪个助手</span><select class="control" id="int-assistant">${optionRows(S.assistants || [], assistantId, "name")}</select></label>
      ${docs({"创建 / 管理飞书应用": result.docs.console, "官方 OAuth 授权说明": result.docs.oauth, "机器人消息权限": result.docs.bot, "自定义机器人说明": result.docs.webhook})}
      <div class="integration-status">扫码授权：<b>${cfg.authorized ? "已完成" : "未完成"}</b>${cfg.expires_at ? ` · 用户令牌${cfg.expires_at * 1000 > Date.now() ? "有效" : "已过期，请重新扫码"}` : ""} · Webhook：${cfg.webhook_present ? "已配置" : "未配置"}<br><span class="hint">机器人发送使用应用租户令牌；用户扫码用于授权和绑定默认收件人。应用需启用机器人、发布、开放 im:message:send_as_bot，并确保机器人可以联系收件人。</span></div>
      <div class="integration-grid">
        <label class="field"><span>App ID</span><input class="control" id="fs-app" value="${e(cfg.app_id)}" placeholder="cli_..."></label>
        <label class="field"><span>App Secret${cfg.app_secret_present ? "（已保存，留空保留）" : ""}</span><input class="control" type="password" id="fs-secret" autocomplete="new-password"></label>
        <label class="field"><span>OAuth 回调地址（必须登记到飞书安全设置）</span><input class="control" id="fs-redirect" value="${e(cfg.redirect_uri || location.origin + "/api/feishu/callback")}"></label>
        <label class="field"><span>OAuth Scopes（空格分隔，需应用获批）</span><input class="control" id="fs-scopes" value="${e(cfg.scopes || "contact:user.base:readonly offline_access")}"></label>
        <label class="field"><span>默认收件人 / 群聊 ID（扫码后可自动绑定 open_id）</span><input class="control" id="fs-recipient" value="${e(cfg.receive_id)}"></label>
        <label class="field"><span>收件人类型</span><select class="control" id="fs-type">${["open_id", "chat_id", "user_id", "union_id", "email"].map(kind => `<option ${kind === (cfg.receive_id_type || "open_id") ? "selected" : ""}>${kind}</option>`).join("")}</select></label>
      </div>
      <details style="margin-top:15px"><summary>可选：群自定义机器人（不需要扫码；配置后优先使用）</summary><div class="integration-grid" style="margin-top:12px">
        <label class="field"><span>Webhook${cfg.webhook_present ? "（已保存，留空保留）" : ""}</span><input class="control" id="fs-webhook" type="password" autocomplete="off" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."></label>
        <label class="field"><span>签名 Secret（如已开启签名校验）</span><input class="control" id="fs-sign" type="password" autocomplete="new-password"></label>
        <label><input type="checkbox" id="fs-clear-webhook"> 清除已保存 Webhook，改用应用机器人</label></div></details>
      <div class="integration-actions"><label><input type="checkbox" id="fs-enabled" ${cfg.enabled ? "checked" : ""}> 允许此助手使用机器人</label><button class="btn" id="fs-save">保存配置</button><button class="btn ghost" id="fs-auth">打开官方扫码授权</button><button class="btn ghost" id="fs-refresh">刷新授权状态</button></div>
      <div class="integration-actions"><input class="control" id="fs-message" value="览籍智研飞书机器人连接测试" style="flex:1"><button class="btn ghost" id="fs-test">发送测试消息</button></div>
      <div id="integration-error" class="integration-error"></div><div id="integration-result" class="integration-result"></div></div>`;
    field("int-assistant").onchange = async event => { assistantId = event.target.value; await loadFeishu(); };
    field("fs-save").onclick = event => action(event.target, async () => {
      await api("/api/feishu/config", {assistant_id: assistantId, app_id: field("fs-app").value.trim(), app_secret: field("fs-secret").value,
        redirect_uri: field("fs-redirect").value.trim(), scopes: field("fs-scopes").value.trim(), receive_id: field("fs-recipient").value.trim(),
        receive_id_type: field("fs-type").value, webhook: field("fs-webhook").value.trim(), signing_secret: field("fs-sign").value,
        clear_webhook: field("fs-clear-webhook").checked, enabled: field("fs-enabled").checked});
      await loadFeishu(); field("integration-result").textContent = "配置已保存；实际连接及发信请使用测试按钮验证。";
    });
    field("fs-auth").onclick = event => action(event.target, async () => {
      // Open synchronously so browsers do not block a window after awaiting the API.
      const popup = window.open("about:blank", "_blank");
      if (popup) popup.opener = null;
      try {
        const authorization = await api("/api/feishu/authorize", {assistant_id: assistantId});
        if (popup) popup.location = authorization.url;
        field("integration-result").textContent = authorization.hint;
        if (!popup) {
          const link = document.createElement("a"); link.href = authorization.url; link.target = "_blank"; link.rel = "noopener noreferrer";
          link.textContent = "浏览器阻止了弹窗，点击打开飞书官方扫码授权页"; field("integration-result").append(document.createElement("br"), link);
        }
      } catch (err) { if (popup) popup.close(); throw err; }
    });
    field("fs-refresh").onclick = event => action(event.target, loadFeishu);
    field("fs-test").onclick = event => action(event.target, async () => {
      if (!confirm("将向已配置的飞书收件人 / 群聊发送一条真实测试消息，是否继续？")) return;
      const sent = await api("/api/feishu/test", {assistant_id: assistantId, text: field("fs-message").value});
      field("integration-result").textContent = "发送成功：" + (sent.message_id || sent.mode || "已确认");
    });
  }
  async function loadZotero() {
    const ticket = ++generation;
    if (!projectId || !S.tasks.some(t => t.id === projectId)) projectId = curTask || S.tasks[0]?.id || "";
    const result = await api("/api/zotero/web/config?task_id=" + encodeURIComponent(projectId));
    if (ticket !== generation || setSec !== "zotero" || !host()) return;
    const cfg = result.config || {}, bound = result.binding || {};
    preview = null;
    host().innerHTML = `<div class="card"><p class="inst-label">LIBRARY CONSISTENCY</p><h3>Zotero 写入与项目同步</h3><p class="hint">本机 Local API 保持只读；写入使用 Zotero 官方 Web API，不碰 zotero.sqlite。每个项目绑定一个 Collection，以条目 Key、版本和本地内容摘要跟踪变化。同步需要先预览，再明确确认。</p>
      ${docs({"创建 API Key / 获取 User ID": result.docs.key, "官方写入 API": result.docs.api, "同步与版本冲突说明": result.docs.sync})}
      <div class="integration-grid"><label class="field"><span>Library 类型</span><select class="control" id="zs-type"><option value="users" ${cfg.library_type !== "groups" ? "selected" : ""}>个人用户</option><option value="groups" ${cfg.library_type === "groups" ? "selected" : ""}>群组</option></select></label>
      <label class="field"><span>User ID / Group ID（数字）</span><input class="control" id="zs-id" value="${e(cfg.library_id)}"></label>
      <label class="field"><span>API Key${cfg.api_key_present ? "（已保存，留空保留）" : ""}</span><input class="control" id="zs-key" type="password" autocomplete="new-password"></label>
      <div class="hint">请只授予需要的 Library 读写权限。群组写入还受成员角色控制；桌面 Zotero 开启自动同步后会取得云端变更。</div></div>
      <div class="integration-actions"><button class="btn" id="zs-save">保存 Web API 配置</button></div><hr>
      <label class="field"><span>项目</span><select class="control" id="zs-project">${optionRows(S.tasks || [], projectId, "title")}</select></label>
      <div class="integration-status">当前绑定：${bound.collection ? `<code>${e(bound.library)} / ${e(bound.collection)}</code>` : "未绑定"}${bound.last_sync ? ` · 最近同步 ${e(new Date(Number(bound.last_sync) * 1000).toLocaleString())}` : ""}</div>
      <div class="integration-actions"><button class="btn ghost" id="zs-load">拉取全部 Collections</button><select class="control" id="zs-collection"><option value="${e(bound.collection)}">${e(bound.collection || "先拉取集合或创建项目同名集合")}</option></select><button class="btn ghost" id="zs-bind">绑定已有集合</button><button class="btn ghost" id="zs-create">创建项目同名集合并绑定</button></div>
      <div class="integration-actions"><select class="control" id="zs-mode"><option value="merge">双向合并元数据</option><option value="pull">拉取 Zotero 到项目</option><option value="push">推送项目到 Zotero</option></select><button class="btn" id="zs-preview">预览同步</button><button class="btn ghost" id="zs-apply" disabled>确认执行预览</button></div>
      <p class="hint">同步范围：项目文献检索元数据与集合成员。不删除任一端条目，不上传 PDF，不自动重命名集合。两端同时修改、远端删除或移出集合会阻止执行并显示冲突；不会静默覆盖。</p>
      <details><summary>高级：预览 / 写入单个 Zotero 条目 JSON</summary><p class="hint">现有条目修改必须提供 key 和 version；字段依照 Zotero 官方条目格式。点击写入将再次确认。</p><textarea class="control integration-json" id="zs-item">${e(JSON.stringify({itemType: "journalArticle", title: "", creators: [], tags: [], collections: bound.collection ? [bound.collection] : []}, null, 2))}</textarea><div class="integration-actions"><button class="btn ghost" id="zs-item-preview">格式校验 / 预览</button><button class="btn ghost" id="zs-item-write">确认写入条目</button></div></details>
      <div id="integration-error" class="integration-error"></div><pre id="integration-result" class="integration-result"></pre></div>`;
    field("zs-project").onchange = async event => { projectId = event.target.value; await loadZotero(); };
    field("zs-save").onclick = event => action(event.target, async () => {
      await api("/api/zotero/web/config", {library_type: field("zs-type").value, library_id: field("zs-id").value.trim(), api_key: field("zs-key").value.trim()});
      await loadZotero(); field("integration-result").textContent = "配置已保存；请拉取 Collections 验证连接和权限。";
    });
    field("zs-load").onclick = event => action(event.target, async () => {
      const data = await api("/api/zotero/web/collections");
      field("zs-collection").innerHTML = data.collections.map(item => `<option value="${e(item.key)}">${e(item.data?.name || item.key)} · ${e(item.key)}</option>`).join("");
      field("integration-result").textContent = `已拉取 ${data.collections.length} 个集合。`;
    });
    for (const [id, create] of [["zs-bind", false], ["zs-create", true]]) field(id).onclick = event => action(event.target, async () => {
      if (!confirm(create ? "在 Zotero 云端创建一个项目同名集合并绑定，是否继续？" : "更新项目的集合绑定？旧条目不会被删除；更换集合后将重建映射。")) return;
      await api("/api/zotero/web/bind", {task_id: projectId, collection: field("zs-collection").value, create, confirm: true});
      await loadZotero();
    });
    field("zs-mode").onchange = () => { preview = null; field("zs-apply").disabled = true; };
    field("zs-preview").onclick = event => action(event.target, async () => {
      preview = await api("/api/zotero/web/sync", {task_id: projectId, mode: field("zs-mode").value, dry_run: true});
      field("integration-result").textContent = JSON.stringify(preview, null, 2);
      field("zs-apply").disabled = !!preview.conflicts.length || !preview.actions.length;
    });
    field("zs-apply").onclick = event => action(event.target, async () => {
      if (!preview || !confirm(`将执行预览中的 ${preview.actions.length} 项同步（可能修改本地或 Zotero 云端），是否继续？`)) return;
      const result = await api("/api/zotero/web/sync", {task_id: projectId, mode: preview.mode, dry_run: false, plan_token: preview.plan_token, confirm: true});
      field("integration-result").textContent = JSON.stringify(result, null, 2); preview = null;
      field("zs-apply").disabled = true;
    });
    field("zs-item-preview").onclick = event => action(event.target, async () => {
      const item = JSON.parse(field("zs-item").value);
      if (!item.itemType) throw new Error("条目必须包含 itemType");
      field("zs-item").value = JSON.stringify(item, null, 2);
      field("integration-result").textContent = "JSON 语法正确，尚未发送。字段有效性由 Zotero API 校验。\n" + field("zs-item").value;
    });
    field("zs-item-write").onclick = event => action(event.target, async () => {
      const item = JSON.parse(field("zs-item").value);
      if (!confirm("将把此条目 JSON 写入 Zotero 云端，确认已核对字段及目标 Library？")) return;
      const result = await api("/api/zotero/web/write", {item, confirm: true});
      field("integration-result").textContent = JSON.stringify(result, null, 2);
    });
  }
  const originalPanel = setPanel;
  setPanel = function () {
    if (["feishu", "zotero"].includes(setSec)) return `<div id="integration-settings"><div class="card">正在读取本机配置…</div></div>`;
    return originalPanel();
  };
  const originalSettings = renderSettings;
  renderSettings = function (container) {
    originalSettings(container);
    if (setSec === "feishu") loadFeishu().catch(error);
    if (setSec === "zotero") loadZotero().catch(error);
  };
  document.addEventListener("rw:assistant-detail", event => {
    const {assistant, container} = event.detail || {};
    if (!assistant || !container || container.querySelector("[data-feishu-assistant]")) return;
    const box = document.createElement("div"); box.className = "card"; box.style.marginTop = "14px";
    box.innerHTML = '<h3>飞书机器人</h3><p class="hint">为此助手配置独立机器人，打开飞书官方扫码授权页绑定收件人。</p><button class="btn ghost" data-feishu-assistant>配置 / 查看飞书机器人</button>';
    box.querySelector("button").onclick = () => { assistantId = assistant.id; setSec = "feishu"; openTab("settings"); render(); };
    container.appendChild(box);
  });
  window.IntegrationSettings = {openFeishu(id) { assistantId = id || ""; setSec = "feishu"; openTab("settings"); render(); }};
})();
