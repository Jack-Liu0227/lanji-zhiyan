/* easyScholar settings module: owns its own settings panel and enable switch. */
(() => {
  "use strict";
  const e = value => esc(String(value ?? ""));
  let generation = 0;
  const style = document.createElement("style");
  style.textContent = `
    .es-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;font-size:11.5px;font-weight:600;line-height:1.4;margin:2px 4px 2px 0}
    .es-badge.sci{background:color-mix(in srgb,var(--accent) 18%,transparent);color:var(--accent);border:1px solid var(--accent)}
    .es-badge.top{background:#f59e0b26;color:#d97706;border:1px solid #d97706}
    .es-badge.warn{background:#ef444422;color:#ef4444;border:1px solid #ef4444;font-weight:700}
    .es-badge.jcr{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok);border:1px solid var(--ok)}
    .es-badge.if{background:var(--panel2);color:var(--fg);border:1px solid var(--line)}
    .es-badge.ccf{background:#8b5cf622;color:#8b5cf6;border:1px solid #8b5cf6}
    .es-badge.domestic{background:var(--panel2);color:var(--muted);border:1px solid var(--line)}
  `;
  document.head.appendChild(style);

  if (!SET_SECS.some(section => section[0] === "easyscholar")) {
    SET_SECS.push(["easyscholar", "🎓", "easyScholar 分区"]);
  }

  function host() { return document.getElementById("integration-settings"); }
  function field(id) { return document.getElementById(id); }
  function error(message) {
    const box = field("integration-error");
    if (box) box.textContent = String(message?.message || message || "");
  }
  async function action(button, work) {
    button.disabled = true; error("");
    try { await work(); } catch (err) { error(err); }
    finally { button.disabled = false; }
  }

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
          <label class="field" style="grid-column:1/-1">
            <span>easyScholar 密钥 (SecretKey)${cfg.key_present ? "（已保存，留空保持原值）" : ""}</span>
            <div style="display:flex;gap:8px">
              <input class="control" type="password" id="es-key" placeholder="在 easyScholar 控制台复制 SecretKey" style="flex:1" autocomplete="new-password">
              <button class="btn ghost" type="button" id="es-key-eye" title="切换明文显示">👁</button>
            </div>
            <span class="hint">密钥使用 Windows DPAPI (securestore) 本机加密落盘；读取配置时不会返回明文。</span>
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
      </div>`;

    let isEnabled = !!cfg.enabled;
    field("es-toggle").onclick = () => {
      isEnabled = !isEnabled;
      field("es-toggle").className = `as-toggle ${isEnabled ? "on" : "off"}`;
      field("es-toggle").querySelector(".as-toggle-txt").textContent = isEnabled ? "已启用" : "已停用";
      field("es-st-txt").textContent = isEnabled ? "已启用" : "已停用";
    };
    field("es-key-eye").onclick = () => {
      const input = field("es-key");
      input.type = input.type === "password" ? "text" : "password";
    };
    field("es-save").onclick = event => action(event.target, async () => {
      const result = await api("/api/easyscholar/config", {
        enabled: isEnabled, secret_key: field("es-key").value.trim()
      });
      if (result.ok) {
        field("integration-result").textContent = "配置已成功保存！密钥已加密存储。";
        await loadEasyScholar();
      }
    });
    field("es-test").onclick = event => action(event.target, async () => {
      field("integration-result").textContent = "正在发起 easyScholar 鉴权探针…";
      const result = await api("/api/easyscholar/test", {secret_key: field("es-key").value.trim()});
      if (!result.ok) throw new Error(result.msg || "鉴权失败");
      field("integration-result").textContent = `${result.msg}（耗时 ${result.cost_ms}ms）\n示例期刊 Nature 命中标签：${(result.sample_badges || []).join(" · ")}`;
    });
    field("es-probe-btn").onclick = event => action(event.target, async () => {
      const journal = field("es-probe-input").value.trim();
      if (!journal) return;
      field("es-probe-badges").innerHTML = `<span class="hint">正在查询…</span>`;
      const result = await api("/api/easyscholar/rank?journal=" + encodeURIComponent(journal));
      if (!result.ok) {
        field("es-probe-badges").innerHTML = `<span class="integration-error">${e(result.msg || "查询失败")}</span>`;
        return;
      }
      const badges = result.badges || [];
      field("es-probe-badges").innerHTML = badges.length
        ? badges.map(item => `<span class="es-badge ${item.type}">${e(item.label)}</span>`).join("")
        : `<span class="hint">未找到该期刊的分区信息。</span>`;
    });
  }

  const originalPanel = setPanel;
  setPanel = function () {
    if (setSec === "easyscholar") return `<div id="integration-settings"><div class="card">正在读取本机配置…</div></div>`;
    return originalPanel();
  };
  const originalSettings = renderSettings;
  renderSettings = function (container) {
    originalSettings(container);
    if (setSec === "easyscholar") loadEasyScholar().catch(error);
  };
})();
