/* Explicit connector configuration and previews; external writes are never implicit. */
(() => {
  "use strict";
  const e = value => esc(String(value ?? ""));
  let assistantId = "", projectId = "", generation = 0;
  // Device authorization sessions are keyed by assistant so switching assistants can
  // never display or poll another assistant's QR/session.
  const registrationSessions = new Map();
  const style = document.createElement("style");
  style.textContent = `
    .integration-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    .integration-grid .field{display:grid;gap:6px}
    .integration-actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:14px 0}
    .integration-result{white-space:pre-wrap;overflow-wrap:anywhere;max-height:380px;overflow:auto}
    .integration-docs{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}
    .integration-json{min-height:190px;width:100%;font-family:Consolas,monospace}
    .integration-error{color:var(--err);white-space:pre-wrap}
    .integration-status{padding:12px;border:1px solid var(--line);border-radius:9px;margin:12px 0}
    .feishu-registration{margin:14px 0;padding:14px;border:1px solid var(--line);border-radius:10px;background:color-mix(in srgb,var(--panel) 88%,var(--accent) 12%)}
    .feishu-registration-grid{display:grid;grid-template-columns:minmax(150px,220px) 1fr;gap:14px;align-items:start}
    .feishu-qr{min-height:170px;display:grid;place-items:center;padding:10px;border:1px dashed var(--line);border-radius:8px;background:var(--panel);text-align:center}
    .feishu-qr img,.feishu-qr canvas{display:block;max-width:100%;width:220px;height:220px;object-fit:contain;background:#fff}
    .feishu-qr a{overflow-wrap:anywhere}
    .feishu-countdown{font-variant-numeric:tabular-nums;font-weight:600}
    .sc-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}
    .sc-title-group{display:flex;gap:12px;align-items:center}
    .sc-icon{font-size:24px;display:grid;place-items:center;width:40px;height:40px;border-radius:10px;background:var(--panel2);border:1px solid var(--line);flex:none}
    .es-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;font-size:11.5px;font-weight:600;line-height:1.4;margin:2px 4px 2px 0}
    .es-badge.sci{background:color-mix(in srgb,var(--accent) 18%,transparent);color:var(--accent);border:1px solid var(--accent)}
    .es-badge.top{background:#f59e0b26;color:#d97706;border:1px solid #d97706}
    .es-badge.warn{background:#ef444422;color:#ef4444;border:1px solid #ef4444;font-weight:700}
    .es-badge.jcr{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok);border:1px solid var(--ok)}
    .es-badge.if{background:var(--panel2);color:var(--fg);border:1px solid var(--line)}
    .es-badge.ccf{background:#8b5cf622;color:#8b5cf6;border:1px solid #8b5cf6}
    .es-badge.domestic{background:var(--panel2);color:var(--muted);border:1px solid var(--line)}
    @media(max-width:760px){
      .integration-grid{grid-template-columns:1fr}
      .feishu-registration-grid{grid-template-columns:1fr}
      .feishu-qr img,.feishu-qr canvas{width:190px;height:190px}
      .sc-head{flex-direction:column;align-items:stretch}
    }
  `;
  document.head.appendChild(style);

  for (const item of [["easyscholar", "🎓", "easyScholar 分区"], ["zotero", "Z", "Zotero 写入与同步"], ["channels", "↗", "消息渠道"]]) {
    if (!SET_SECS.some(section => section[0] === item[0])) SET_SECS.push(item);
  }

  function host() { return document.getElementById("integration-settings"); }
  function field(id) { return document.getElementById(id); }
  function error(message) { const box = field("integration-error"); if (box) box.textContent = String(message?.message || message || ""); }
  async function action(button, work) {
    button.disabled = true; error("");
    try { await work(); } catch (err) { if (button.isConnected) error(err); }
    finally { button.disabled = false; }
  }
  function docs(links) {
    return `<div class="integration-docs">${Object.entries(links).map(([label, url]) => `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${e(label)} ↗</a>`).join("")}</div>`;
  }
  function optionRows(rows, selected, label) {
    return rows.map(row => `<option value="${e(row.id)}" ${row.id === selected ? "selected" : ""}>${e(row[label])}</option>`).join("");
  }

  // ==================== easyScholar 设置 ====================
  async function loadEasyScholar() {
    const ticket = ++generation;
    const cfg = await api("/api/easyscholar/config");
    if (ticket !== generation || setSec !== "easyscholar" || !host()) return;

    host().innerHTML = `
      <div class="card">
        <div class="sc-head">
          <div class="sc-title-group">
            <span class="sc-icon">🎓</span>
            <div>
              <p class="inst-label" style="margin:0 0 2px">JOURNAL TIERS & PARTITIONS</p>
              <h3 style="margin:0">easyScholar 期刊等级与 SCI 分区</h3>
              <p class="hint" style="margin:4px 0 0">免密查询期刊分区（中科院升级版大类/小类/Top/预警、JCR 分区与影响因子、CCF 等级、国内核心）。</p>
            </div>
          </div>
          <button class="as-toggle ${cfg.enabled ? "on" : "off"}" id="es-toggle" type="button">
            <span class="as-knob"></span><span class="as-toggle-txt">${cfg.enabled ? "已启用" : "已停用"}</span>
          </button>
        </div>

        <div class="integration-status">
          接口地址：<code>https://www.easyscholar.cc/open/getPublicationRank</code> · 状态：<b id="es-st-txt">${cfg.enabled ? "已启用" : "已停用"}</b> · 密钥：<b>${cfg.key_present ? "已配置" : "未填写"}</b> · 本地缓存：<b>SQLite (30天)</b> · 频率保护：<b>最高2次/秒</b>
        </div>

        <div class="integration-grid">
          <label class="field" style="grid-column: 1 / -1">
            <span>easyScholar 密钥 (SecretKey)${cfg.key_present ? "（已保存，留空保持原值）" : ""}</span>
            <div style="display:flex;gap:8px">
              <input class="control" type="password" id="es-key" placeholder="在 easyScholar 控制台复制 SecretKey" style="flex:1" autocomplete="new-password">
              <button class="btn ghost" type="button" id="es-key-eye" title="切换明文显示">👁</button>
            </div>
            <span class="hint">密钥使用 Windows DPAPI (securestore) 本机加密落盘，前端不返回明文。</span>
          </label>
        </div>

        <div class="integration-actions">
          <button class="btn" id="es-save">保存配置</button>
          <button class="btn ghost" id="es-test">测试连接与鉴权</button>
          <a class="btn ghost" href="https://www.easyscholar.cc/console/user/open" target="_blank" rel="noopener noreferrer">获取 SecretKey ↗</a>
        </div>

        <hr style="margin:16px 0;border:none;border-top:1px solid var(--line)">

        <h4 style="margin:0 0 8px">🧪 期刊等级速查验证</h4>
        <div class="integration-actions" style="margin:0 0 10px">
          <input class="control" id="es-probe-input" value="Nature" placeholder="输入期刊全称或标准缩写" style="max-width:320px">
          <button class="btn ghost" id="es-probe-btn">查询分区等级</button>
        </div>
        <div id="es-probe-badges" style="display:flex;gap:6px;flex-wrap:wrap;min-height:30px;align-items:center">
          <span class="hint">点击「查询分区等级」查看该期刊的分区标签。</span>
        </div>

        <div id="integration-error" class="integration-error"></div>
        <div id="integration-result" class="integration-result"></div>
      </div>
    `;

    let isEnabled = !!cfg.enabled;
    field("es-toggle").onclick = () => {
      isEnabled = !isEnabled;
      field("es-toggle").className = `as-toggle ${isEnabled ? "on" : "off"}`;
      field("es-toggle").querySelector(".as-toggle-txt").textContent = isEnabled ? "已启用" : "已停用";
      field("es-st-txt").textContent = isEnabled ? "已启用" : "已停用";
    };

    field("es-key-eye").onclick = () => {
      const inp = field("es-key");
      inp.type = inp.type === "password" ? "text" : "password";
    };

    field("es-save").onclick = event => action(event.target, async () => {
      const keyVal = field("es-key").value.trim();
      const res = await api("/api/easyscholar/config", {enabled: isEnabled, secret_key: keyVal});
      if (res.ok) {
        field("integration-result").textContent = "配置已成功保存！密钥已加密存储。";
        await loadEasyScholar();
      }
    });

    field("es-test").onclick = event => action(event.target, async () => {
      const keyVal = field("es-key").value.trim();
      field("integration-result").textContent = "正在发起 easyScholar 鉴权探针…";
      const res = await api("/api/easyscholar/test", {secret_key: keyVal});
      if (res.ok) {
        field("integration-result").textContent = `${res.msg}（耗时 ${res.cost_ms}ms）\n示例期刊 Nature 命中标签：${(res.sample_badges || []).join(" · ")}`;
      } else {
        throw new Error(res.msg || "鉴权失败");
      }
    });

    field("es-probe-btn").onclick = event => action(event.target, async () => {
      const jname = field("es-probe-input").value.trim();
      if (!jname) return;
      field("es-probe-badges").innerHTML = `<span class="hint">正在查询…</span>`;
      const res = await api("/api/easyscholar/rank?journal=" + encodeURIComponent(jname));
      if (!res.ok) {
        field("es-probe-badges").innerHTML = `<span class="integration-error">${e(res.msg || "查询失败")}</span>`;
        return;
      }
      const badges = res.badges || [];
      if (!badges.length) {
        field("es-probe-badges").innerHTML = `<span class="hint">未找到该期刊的分区信息。</span>`;
        return;
      }
      field("es-probe-badges").innerHTML = badges.map(b => `<span class="es-badge ${b.type}">${e(b.label)}</span>`).join("");
    });
  }

  // ==================== 飞书设置 ====================
  function registrationMarkup(cfg) {
    const session = registrationSessions.get(assistantId);
    const region = session?.region || cfg.registration_region || cfg.region || "feishu";
    const active = session && session.status === "pending";
    const qr = active ? (session.qrUrl || session.verificationUriComplete || "") : "";
    const qrImage = active && session.qrDataUri && /^data:image\/(?:svg\+xml|png);/i.test(session.qrDataUri) ? `<img src="${e(session.qrDataUri)}" alt="飞书扫码二维码">` : "";
    const statusText = active ? "等待手机确认授权…" : session?.status === "success" ? "绑定成功" : session ? e(session.message || "尚未开始扫码绑定") : "尚未开始扫码绑定";
    return `<div class="feishu-registration" data-feishu-registration>
      <h4 style="margin:0 0 6px">手机扫码绑定专家</h4>
      <p class="hint" style="margin-top:0">使用飞书/Lark 官方 Device Authorization：选择区域后生成一次性二维码，用手机飞书扫描并确认，即可把该账号绑定到当前专家。授权会话仅保存于本机服务端。</p>
      <div class="integration-grid">
        <label class="field"><span>区域</span><select class="control" id="fs-region"><option value="feishu" ${region === "feishu" ? "selected" : ""}>飞书（中国大陆）</option><option value="lark" ${region === "lark" ? "selected" : ""}>Lark（国际版）</option></select></label>
        <div class="integration-actions" style="margin:0;align-items:end"><button class="btn" id="fs-device-begin">${active ? "重新生成二维码" : "手机扫码绑定"}</button><button class="btn ghost" id="fs-device-cancel" ${active ? "" : "hidden"}>取消会话</button></div>
      </div>
      <div class="feishu-registration-grid" style="margin-top:12px">
        <div class="feishu-qr" id="fs-device-qr">${qrImage || (qr ? `<div><div class="hint">请用手机打开或扫描官方授权链接</div><a href="${e(qr)}" target="_blank" rel="noopener noreferrer">打开飞书授权链接</a></div>` : `<span class="hint">点击“手机扫码绑定”生成二维码</span>`)}</div>
        <div><div class="integration-status" id="fs-device-status" style="margin:0">${statusText}</div>
          <div class="hint" id="fs-device-countdown" style="margin-top:8px">${active && session.expiresAt ? "二维码有效期计算中…" : ""}</div>
          <div class="hint" style="margin-top:8px">如果当前客户端不支持显示二维码，可点击上方官方链接，在手机浏览器或飞书中完成确认。</div></div>
      </div>
    </div>`;
  }
  function registrationStatus(text, kind) {
    const box = field("fs-device-status");
    if (box) { box.textContent = text; box.dataset.state = kind || ""; }
  }
  function renderRegistrationSession(session) {
    if (!session || assistantId !== session.assistantId || setSec !== "feishu") return;
    const qr = session.status === "pending" ? (session.qrUrl || session.verificationUriComplete || "") : "";
    const qrBox = field("fs-device-qr");
    if (qrBox) {
      const qrImage = session.status === "pending" && session.qrDataUri && /^data:image\/(?:svg\+xml|png);/i.test(session.qrDataUri) ? `<img src="${e(session.qrDataUri)}" alt="飞书扫码二维码">` : "";
      qrBox.innerHTML = qrImage || (qr ? `<div><div class="hint">用手机飞书扫描二维码，或打开官方授权链接</div><a href="${e(qr)}" target="_blank" rel="noopener noreferrer">打开飞书授权链接</a></div>` : `<span class="hint">二维码地址为空，请重试</span>`);
    }
    const cancel = field("fs-device-cancel"); if (cancel) cancel.hidden = session.status !== "pending";
    const begin = field("fs-device-begin"); if (begin) begin.textContent = session.status === "pending" ? "重新生成二维码" : "手机扫码绑定";
    registrationStatus(session.status === "pending" ? "等待手机确认授权…" : session.status === "success" ? "绑定成功" : session.message || "授权失败", session.status);
    if (session.status === "success" && session.openId) registrationStatus(`绑定成功（open_id: ${session.openId}）`, "success");
  }
  function updateRegistrationCountdown(session) {
    if (!session || assistantId !== session.assistantId || setSec !== "feishu") return;
    const box = field("fs-device-countdown");
    if (!box || !session.expiresAt) return;
    const seconds = Math.max(0, Math.ceil((session.expiresAt - Date.now()) / 1000));
    box.textContent = seconds ? `二维码剩余有效期：${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2, "0")}秒` : "二维码已过期，请重新生成";
    if (!seconds && session.status === "pending") {
      session.status = "error"; session.message = "二维码已过期";
      if (session.timer) { clearTimeout(session.timer); session.timer = null; }
      renderRegistrationSession(session);
    }
  }
  function startRegistrationCountdown(session) {
    if (session.countdownTimer) clearInterval(session.countdownTimer);
    if (!session.expiresAt) return;
    updateRegistrationCountdown(session);
    session.countdownTimer = setInterval(() => {
      if (!registrationSessions.has(session.assistantId) || registrationSessions.get(session.assistantId) !== session || session.status !== "pending") {
        clearInterval(session.countdownTimer); session.countdownTimer = null; return;
      }
      if (assistantId !== session.assistantId || setSec !== "feishu") {
        clearInterval(session.countdownTimer); session.countdownTimer = null; return;
      }
      updateRegistrationCountdown(session);
    }, 1000);
  }
  function scheduleRegistrationPoll(session, delay) {
    if (!session || session.status !== "pending") return;
    if (session.timer) clearTimeout(session.timer);
    session.timer = setTimeout(() => { session.timer = null; pollRegistration(session); }, Math.max(1000, Number(delay || session.intervalMs || 5000)));
  }
  function registrationIntervalMs(result, fallback = 5000) {
    const intervalMs = Number(result?.interval_ms);
    if (Number.isFinite(intervalMs) && intervalMs > 0) return intervalMs;
    const intervalSeconds = Number(result?.interval);
    if (Number.isFinite(intervalSeconds) && intervalSeconds > 0) return intervalSeconds * 1000;
    return fallback;
  }
  function registrationPollDelay(result, session) {
    return registrationIntervalMs(result, session.intervalMs || 5000);
  }
  async function pollRegistration(session) {
    if (!session || registrationSessions.get(session.assistantId) !== session || session.status !== "pending") return;
    try {
      // Poll is state-changing; send the assistant/session in the JSON body so
      // the server enforces assistant-scoped sessions.
      const result = await api("/api/feishu/registration/poll", {
        assistant_id: session.assistantId,
        session_id: session.sessionId
      });
      const status = String(result.status || "pending").toLowerCase();
      if (status === "pending" || status === "slow_down") {
        registrationStatus(result.message || "等待手机确认授权…", "pending");
        scheduleRegistrationPoll(session, registrationPollDelay(result, session));
      } else if (status === "success") {
        session.status = "success"; session.openId = result.open_id || "";
        session.message = "绑定成功"; if (session.timer) clearTimeout(session.timer);
        if (session.countdownTimer) clearInterval(session.countdownTimer);
        renderRegistrationSession(session);
        const resultBox = field("integration-result"); if (resultBox) resultBox.textContent = `手机扫码绑定成功${session.openId ? `（open_id: ${session.openId}）` : ""}。`;
        await loadFeishu();
      } else {
        session.status = "error"; session.message = result.message || result.error || `授权失败（${status}）`;
        if (session.countdownTimer) clearInterval(session.countdownTimer);
        renderRegistrationSession(session);
      }
    } catch (err) {
      if (registrationSessions.get(session.assistantId) !== session) return;
      session.status = "error"; session.message = err?.message || String(err);
      if (session.countdownTimer) clearInterval(session.countdownTimer);
      renderRegistrationSession(session);
    }
  }

  async function loadFeishu() {
    const ticket = ++generation;
    if (!assistantId && S.assistants.length) assistantId = S.assistants[0].id;
    const aid = assistantId;
    const current = () => ticket === generation && setSec === "feishu" && !!host();
    const result = await api("/api/feishu/config?assistant_id=" + encodeURIComponent(aid));
    if (ticket !== generation || setSec !== "feishu" || !host()) return;
    const cfg = result.config || {};
    host().innerHTML = `<div class="card"><p class="inst-label">ASSISTANT CONNECTION</p><h3>飞书机器人</h3>
      <p class="hint">每个专家独立配置。可使用企业自建应用机器人，也可用群聊自定义机器人 Webhook。扫码绑定由飞书官方授权页完成，不会自动创建、发布或授予应用权限。</p>
      <label class="field"><span>配置哪个专家</span><select class="control" id="int-assistant">${optionRows(S.assistants || [], assistantId, "name")}</select></label>
      ${docs({"创建 / 管理飞书应用": result.docs.console, "官方 OAuth 授权说明": result.docs.oauth, "机器人消息权限": result.docs.bot, "自定义机器人说明": result.docs.webhook})}
       <div class="integration-status">扫码授权：<b>${cfg.authorized ? "已完成" : "未完成"}</b>${cfg.expires_at ? ` · 用户令牌${cfg.expires_at * 1000 > Date.now() ? "有效" : "已过期，请重新扫码"}` : ""} · Webhook：${cfg.webhook_present ? "已配置" : "未配置"} · 接收连接：${e(result.bridge?.status||"stopped")}<br><span class="hint">应用机器人需启用、发布，并授予发送消息与接收消息权限；接收事件使用飞书长连接。</span></div>
      ${registrationMarkup(cfg)}
      <div class="integration-grid">
        <label class="field"><span>App ID</span><input class="control" id="fs-app" value="${e(cfg.app_id)}" placeholder="cli_..."></label>
        <label class="field"><span>App Secret${cfg.app_secret_present ? "（已保存，留空保留）" : ""}</span><input class="control" type="password" id="fs-secret" autocomplete="new-password"></label>
        <label class="field"><span>OAuth 回调地址（必须登记到飞书安全设置）</span><input class="control" id="fs-redirect" value="${e(cfg.redirect_uri || location.origin + "/api/feishu/callback")}"></label>
        <label class="field"><span>OAuth Scopes（空格分隔，需应用获批）</span><input class="control" id="fs-scopes" value="${e(cfg.scopes || "contact:user.base:readonly offline_access")}"></label>
        <label class="field"><span>默认收件人 / 群聊 ID（扫码后可自动绑定 open_id）</span><input class="control" id="fs-recipient" value="${e(cfg.receive_id)}"></label>
        <label class="field"><span>收件人类型</span><select class="control" id="fs-type">${["open_id", "chat_id", "user_id", "union_id", "email"].map(kind => `<option ${kind === (cfg.receive_id_type || "open_id") ? "selected" : ""}>${kind}</option>`).join("")}</select></label>
         <label class="field"><span>接收任务的项目</span><select class="control" id="fs-project"><option value="">请选择项目</option>${optionRows(S.tasks||[],cfg.project_id||"","title")}</select></label>
         <label class="field"><span>允许发送任务的用户 open_id</span><input class="control" id="fs-sender" value="${e(cfg.allowed_sender||"")}" placeholder="留空时使用扫码绑定的 open_id"></label>
      </div>
       <label class="field"><input type="checkbox" id="fs-inbound" ${cfg.inbound_enabled?"checked":""}> 接收飞书消息并交给此专家执行项目任务</label>
      <details style="margin-top:15px"><summary>可选：群自定义机器人（不需要扫码；配置后优先使用）</summary><div class="integration-grid" style="margin-top:12px">
        <label class="field"><span>Webhook${cfg.webhook_present ? "（已保存，留空保留）" : ""}</span><input class="control" id="fs-webhook" type="password" autocomplete="off" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."></label>
        <label class="field"><span>签名 Secret（如已开启签名校验）</span><input class="control" id="fs-sign" type="password" autocomplete="new-password"></label>
        <label><input type="checkbox" id="fs-clear-webhook"> 清除已保存 Webhook，改用应用机器人</label></div></details>
      <div class="integration-actions"><label><input type="checkbox" id="fs-enabled" ${cfg.enabled ? "checked" : ""}> 允许此专家使用机器人</label><button class="btn" id="fs-save">保存配置</button><button class="btn ghost" id="fs-auth">打开官方扫码授权</button><button class="btn ghost" id="fs-refresh">刷新授权状态</button></div>
      <div class="integration-actions"><input class="control" id="fs-message" value="览籍智研飞书机器人连接测试" style="flex:1"><button class="btn ghost" id="fs-test">发送测试消息</button></div>
      <div id="integration-error" class="integration-error"></div><div id="integration-result" class="integration-result"></div></div>`;
    field("int-assistant").onchange = async event => { assistantId = event.target.value; await loadFeishu(); };
    field("fs-device-begin").onclick = event => action(event.target, async () => {
      const previous = registrationSessions.get(aid);
      if (previous?.timer) clearTimeout(previous.timer);
      if (previous?.countdownTimer) clearInterval(previous.countdownTimer);
      const region = field("fs-region").value;
      const started = await api("/api/feishu/registration/begin", {assistant_id: aid, region});
      const session = {assistantId: aid, sessionId: started.session_id || started.sessionId, region: started.region || region,
        qrUrl: started.qr_url || started.qrUrl || started.verification_uri_complete || started.verificationUriComplete || started.verification_uri || "",
        qrDataUri: started.qr_svg || started.qr_data_uri || started.qr_image || "",
        verificationUriComplete: started.verification_uri_complete || started.verificationUriComplete || "", intervalMs: registrationIntervalMs(started),
        expiresAt: Date.now() + Math.max(1, Number(started.expires_in || 600)) * 1000, status: String(started.status || "pending").toLowerCase()};
      registrationSessions.set(aid, session);
      renderRegistrationSession(session);
      startRegistrationCountdown(session);
      scheduleRegistrationPoll(session, registrationPollDelay(started, session));
    });
    field("fs-device-cancel").onclick = event => action(event.target, async () => {
      const session = registrationSessions.get(aid);
      if (!session) return;
      if (session.timer) clearTimeout(session.timer);
      if (session.countdownTimer) clearInterval(session.countdownTimer);
      try {
        await api("/api/feishu/registration/cancel", {assistant_id: aid, session_id: session.sessionId});
      } finally {
        session.status = "cancelled";
        session.message = "已取消扫码会话";
        if (current()) registrationStatus("已取消扫码会话", "");
        renderRegistrationSession(session);
      }
    });
    field("fs-save").onclick = event => action(event.target, async () => {
      await api("/api/feishu/config", {assistant_id: aid, app_id: field("fs-app").value.trim(), app_secret: field("fs-secret").value.trim(),
        redirect_uri: field("fs-redirect").value.trim(), scopes: field("fs-scopes").value.trim(), receive_id: field("fs-recipient").value.trim(),
         receive_id_type: field("fs-type").value, project_id: field("fs-project").value,
         allowed_sender: field("fs-sender").value.trim(), inbound_enabled: field("fs-inbound").checked,
         webhook: field("fs-clear-webhook").checked ? "" : field("fs-webhook").value.trim(),
         signing_secret: field("fs-sign").value.trim(), enabled: field("fs-enabled").checked, clear_webhook: field("fs-clear-webhook").checked});
      if (!current()) return;
      await loadFeishu();
      if (generation === ticket + 1 && setSec === "feishu" && field("integration-result")) field("integration-result").textContent = "配置已保存。";
    });
    field("fs-auth").onclick = event => action(event.target, async () => {
      const popup = window.open("about:blank", "_blank");
      if (popup) popup.opener = null;
      try {
        const authorization = await api("/api/feishu/authorize", {assistant_id: aid});
        if (!current()) { if (popup) popup.close(); return; }
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
      const text = field("fs-message").value;
      if (!await uiConfirm("将向已配置的飞书收件人 / 群聊发送一条真实测试消息，是否继续？")) return;
      if (!current()) return;
      const sent = await api("/api/feishu/test", {assistant_id: aid, text});
      if (current()) field("integration-result").textContent = "发送成功：" + (sent.message_id || sent.mode || "已确认");
    });
  }

  // ==================== Zotero 设置 (含 MCP) ====================
  async function loadZotero() {
    const ticket = ++generation;
    if (!projectId || !S.tasks.some(t => t.id === projectId)) projectId = curTask || S.tasks[0]?.id || "";
    const [result, mcpRes] = await Promise.all([
      api("/api/zotero/web/config"),
      api("/api/zotero/mcp/status").catch(() => ({registered: false, enabled: false}))
    ]);
    if (ticket !== generation || setSec !== "zotero" || !host()) return;
    const cfg = result.config || {}, bound = {};
    host().innerHTML = `
      <div class="card switch-card" style="margin-bottom:14px">
        <div class="sc-head">
          <div class="sc-title-group">
            <span class="sc-icon">⚡</span>
            <div>
              <p class="inst-label" style="margin:0 0 2px">MCP PROTOCOL / AGENT CAPABILITY</p>
              <h3 style="margin:0">Zotero stdio MCP 服务</h3>
              <p class="hint" style="margin:4px 0 0">让专家检索、读取和修改本机 Zotero 文献，并挂载笔记。同步与 MCP 共用本机写入授权。</p>
            </div>
          </div>
          <span class="pill ${mcpRes.enabled ? "ok" : ""}" id="zm-pill">${mcpRes.enabled ? "MCP 已启用" : "未启用"}</span>
        </div>
        <div class="integration-actions" style="margin-top:12px">
          <button class="btn" id="zm-enable">${mcpRes.registered ? "重新激活 / 注册 MCP" : "一键注册并启用 Zotero MCP"}</button>
          <button class="btn ghost" id="zm-open-mcp">去 MCP 矩阵查看权限 →</button>
        </div>
        <div class="hint" style="margin-top:8px">包含工具：<code>zotero_status</code> · <code>zotero_search</code> · <code>zotero_item</code> · <code>zotero_pdf</code> · <code>zotero_create_item</code> · <code>zotero_update_item</code> · <code>zotero_create_note</code></div>
      </div>

      <div class="card"><p class="inst-label">LIBRARY CONSISTENCY</p><h3>Zotero 连接与授权</h3><p class="hint">配置本机连接和写入权限。项目文献的来源、题录和同步在项目空间统一管理。</p>
        <p class="hint">连接方式：${cfg.transport==='local'?'本机 Zotero，无需云端 Key':'Web API（兼容模式）'} · ${result.local?.readable?'本机可读':'本机尚未连接'} · ${result.local?.authorized?'已有本机写入授权':'首次写入时由 Zotero 授权'}</p>
        <div class="integration-actions"><button class="btn" id="zs-local">使用本机 Zotero</button><button class="btn ghost" id="zs-authorize">检查 / 授予本机写入权限</button></div>
        <details><summary>可选：连接云端或群组 Library</summary>
        ${docs({"创建 API Key / 获取 User ID": result.docs.key, "官方写入 API": result.docs.api})}
        <div class="integration-grid"><label class="field"><span>Library 类型</span><select class="control" id="zs-type"><option value="users" ${cfg.library_type !== "groups" ? "selected" : ""}>个人用户</option><option value="groups" ${cfg.library_type === "groups" ? "selected" : ""}>群组</option></select></label>
        <label class="field"><span>User ID / Group ID（数字）</span><input class="control" id="zs-id" value="${e(cfg.library_id)}"></label>
        <label class="field"><span>API Key${cfg.api_key_present ? "（已保存，留空保留）" : ""}</span><input class="control" id="zs-key" type="password" autocomplete="new-password"></label>
        <div class="hint">请只授予需要的 Library 读写权限。群组写入还受成员角色控制；桌面 Zotero 开启自动同步后会取得云端变更。</div></div>
        <div class="integration-actions"><button class="btn" id="zs-save">切换并保存 Web API 配置</button></div></details><hr>
        <label class="field"><span>项目</span><select class="control" id="zs-project">${optionRows(S.tasks || [], projectId, "title")}</select></label>
        <p class="hint">在项目空间的“文献”页选择多个 Collection、逐篇加入文献、编辑题录、同步和移除或恢复关联。</p>
        <button class="btn" id="zs-manage" ${projectId?'':'disabled'}>打开项目文献管理</button>
        <details><summary>高级：预览 / 写入单个 Zotero 条目 JSON</summary><p class="hint">现有条目修改必须提供 key 和 version；字段依照 Zotero 官方条目格式。点击写入将再次确认。</p><textarea class="control integration-json" id="zs-item">${e(JSON.stringify({itemType: "journalArticle", title: "", creators: [], tags: [], collections: bound.collection ? [bound.collection] : []}, null, 2))}</textarea><div class="integration-actions"><button class="btn ghost" id="zs-item-preview">格式校验 / 预览</button><button class="btn ghost" id="zs-item-write">确认写入条目</button></div></details>
        <div id="integration-error" class="integration-error"></div><pre id="integration-result" class="integration-result"></pre></div>`;

    field("zm-enable").onclick = event => action(event.target, async () => {
      const res = await api("/api/zotero/mcp/enable", {});
      if (res.ok) {
        field("integration-result").textContent = "Zotero MCP 服务已成功注册并启用！\nAgent 现可在对话中调用 Zotero 读写工具。";
        await loadZotero();
      }
    });

    field("zm-open-mcp").onclick = () => { setSec = "mcp"; render(); };
    field('zs-local').onclick = event => action(event.target, async()=>{ await api('/api/zotero/web/config',{transport:'local'}); await loadZotero(); });
    field('zs-authorize').onclick = event => action(event.target, async()=>{ const data=await api('/api/zotero/web/authorize',{}); field('integration-result').textContent=data.message; });

    field("zs-project").onchange = async event => { projectId = event.target.value; await loadZotero(); };
    field('zs-manage').onclick = () => ProjectLiterature.open(projectId, true);
    field("zs-save").onclick = event => action(event.target, async () => {
      await api("/api/zotero/web/config", {library_type: field("zs-type").value, library_id: field("zs-id").value.trim(), api_key: field("zs-key").value.trim()});
      await loadZotero(); field("integration-result").textContent = "配置已保存；可到项目空间的文献页读取 Collections。";
    });
    field("zs-item-preview").onclick = event => action(event.target, async () => {
      const item = JSON.parse(field("zs-item").value);
      if (!item.itemType) throw new Error("条目必须包含 itemType");
      field("zs-item").value = JSON.stringify(item, null, 2);
      field("integration-result").textContent = "JSON 语法正确，尚未发送。字段有效性由 Zotero API 校验。\n" + field("zs-item").value;
    });
    field("zs-item-write").onclick = event => action(event.target, async () => {
      const item = JSON.parse(field("zs-item").value);
      if (!await uiConfirm("将把此条目 JSON 写入当前连接的 Zotero，确认已核对字段及目标 Library？")) return;
      const result = await api("/api/zotero/web/write", {item, confirm: true});
      field("integration-result").textContent = JSON.stringify(result, null, 2);
    });
  }

  const originalPanel = setPanel;
  setPanel = function () {
    if (["easyscholar", "feishu", "zotero", "channels"].includes(setSec)) return `<div id="integration-settings"><div class="card">正在读取本机配置…</div></div>`;
    return originalPanel();
  };
  const originalSettings = renderSettings;
  renderSettings = function (container) {
    originalSettings(container);
    if (setSec === "easyscholar") loadEasyScholar().catch(error);
    if (setSec === "feishu") loadFeishu().then(()=>{if(host()){const back=document.createElement("button");back.className="btn ghost";back.textContent="← 消息渠道";back.onclick=()=>{setSec="channels";render();};host().prepend(back);}}).catch(error);
    if (setSec === "channels") MessageChannels.mount(host());
    if (setSec === "zotero") loadZotero().catch(error);
  };
  document.addEventListener("rw:assistant-detail", event => {
    const {assistant, container} = event.detail || {};
    if (!assistant || !container || container.querySelector("[data-feishu-assistant]")) return;
    const box = document.createElement("div"); box.className = "card"; box.style.marginTop = "14px";
    box.innerHTML = '<h3>飞书机器人</h3><p class="hint">为此专家配置独立机器人，打开飞书官方扫码授权页绑定收件人。</p><button class="btn ghost" data-feishu-assistant>配置 / 查看飞书机器人</button>';
    box.querySelector("button").onclick = () => { assistantId = assistant.id; setSec = "feishu"; openTab("settings"); render(); };
    container.appendChild(box);
  });
  window.IntegrationSettings = {
    openFeishu(id) { assistantId = id || ""; setSec = "feishu"; openTab("settings"); render(); },
    openZotero(id) { projectId = id || curTask || ""; setSec = "zotero"; openTab("settings"); render(); }
  };
})();
