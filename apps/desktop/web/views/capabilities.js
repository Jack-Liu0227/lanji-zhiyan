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
    .cap-toolbar .control{min-width:150px;max-width:270px}
    .cap-matrix-header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px}
    .cap-cats{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
    .cap-cat-pill{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;color:var(--muted);transition:all .15s;display:inline-flex;align-items:center;gap:5px}
    .cap-cat-pill:hover{border-color:var(--accent);color:var(--fg)}
    .cap-cat-pill.on{background:color-mix(in srgb,var(--accent) 15%,transparent);border-color:var(--accent);color:var(--accent)}
    .cap-cat-count{font-size:10.5px;padding:1px 6px;border-radius:999px;background:var(--panel2,rgba(255,255,255,0.06));opacity:.85}

    /* List View Styles */
    .cap-list{display:flex;flex-direction:column;gap:7px;margin-top:12px}
    .cap-list-row{display:flex;align-items:center;gap:14px;padding:11px 16px;background:var(--panel,var(--bg));border:1px solid var(--line);border-radius:10px;cursor:pointer;transition:all .15s ease}
    .cap-list-row:hover{background:color-mix(in srgb,var(--accent) 7%,var(--panel,var(--bg)));border-color:color-mix(in srgb,var(--accent) 45%,var(--line));box-shadow:0 3px 10px rgba(0,0,0,0.05);transform:translateY(-1px)}
    .cap-row-icon{font-size:18px;width:32px;height:32px;display:flex;align-items:center;justify-content:center;flex-shrink:0;border-radius:8px;background:var(--panel2,rgba(255,255,255,0.04));border:1px solid var(--line)}
    .cap-row-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
    .cap-row-top{display:flex;align-items:center;gap:8px}
    .cap-item-name{font-size:14px;font-weight:600;color:var(--fg);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .cap-scope-pill{font-size:11px;padding:1px 7px;border-radius:999px}
    .cap-scope-pill.user{color:#38bdf8;border-color:color-mix(in srgb,#38bdf8 35%,transparent);background:color-mix(in srgb,#38bdf8 10%,transparent)}
    .cap-scope-pill.proj{color:#a855f7;border-color:color-mix(in srgb,#a855f7 35%,transparent);background:color-mix(in srgb,#a855f7 10%,transparent)}
    .cap-row-desc{font-size:12.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.4}
    .cap-row-meta{flex-shrink:0;display:flex;align-items:center;gap:8px}
    .cap-binding-badge{font-size:12px;padding:2px 8px;border-radius:6px;background:var(--panel2,rgba(255,255,255,0.04));border:1px solid var(--line);color:var(--muted);white-space:nowrap}
    .cap-row-actions{flex-shrink:0;display:flex;align-items:center;gap:6px}
    .cap-btn-sm{padding:4px 10px;font-size:12px;border-radius:6px;line-height:1.4;white-space:nowrap}

    /* Detail View Styles */
    .cap-detail-nav-card{margin-bottom:12px;padding:12px 18px}
    .cap-detail-nav{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
    .cap-detail-nav-left{display:flex;align-items:center;gap:12px}
    .cap-btn-back{font-weight:600;display:inline-flex;align-items:center;gap:6px}
    .cap-breadcrumbs{display:flex;align-items:center;gap:8px;font-size:14px}
    .cap-breadcrumbs .sep{color:var(--muted);opacity:.6}
    .cap-detail-nav-right{display:flex;align-items:center;gap:8px}

    .cap-detail-card{padding:22px;border-radius:12px;background:var(--panel,var(--bg));border:1px solid var(--line)}
    .cap-detail-header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding-bottom:16px;border-bottom:1px solid var(--line)}
    .cap-detail-title-group{display:flex;align-items:center;gap:14px}
    .cap-detail-icon{font-size:30px;width:50px;height:50px;display:flex;align-items:center;justify-content:center;border-radius:12px;background:var(--panel2,rgba(255,255,255,0.05));border:1px solid var(--line)}
    .cap-detail-title{margin:0;font-size:20px;font-weight:700;letter-spacing:-0.01em;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
    .cap-detail-tags{display:flex;gap:8px;align-items:center;margin-top:6px}

    .cap-detail-path-box{display:flex;align-items:center;gap:10px;background:var(--panel2,rgba(255,255,255,0.03));padding:10px 14px;border-radius:8px;border:1px solid var(--line);margin:16px 0}
    .cap-detail-path-box code{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted)}

    .cap-detail-section{margin-top:20px}
    .cap-section-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
    .cap-section-title{font-size:14px;font-weight:700;margin:0;color:var(--fg)}
    .cap-detail-desc{font-size:13.5px;line-height:1.65;color:var(--fg);background:var(--panel2,rgba(255,255,255,0.03));padding:14px 16px;border-radius:8px;border:1px solid var(--line);white-space:pre-wrap;overflow-wrap:anywhere}

    .cap-detail-assistants-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;margin-top:8px}
    .cap-detail-as-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel2,var(--bg));cursor:pointer;transition:all .15s}
    .cap-detail-as-item:hover{border-color:var(--accent)}
    .cap-detail-as-item.checked{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,var(--panel2,var(--bg)))}
    .cap-detail-as-item input{width:16px;height:16px;cursor:pointer}
    .cap-as-name{font-size:13px;font-weight:500;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

    .cap-file-tabs{display:flex;gap:4px}
    .cap-file-tabs .btn.active{background:color-mix(in srgb,var(--accent) 18%,transparent);border-color:var(--accent);color:var(--accent);font-weight:600}
    .cap-code-view{background:var(--panel2,#121316);border:1px solid var(--line);border-radius:8px;padding:14px 16px;max-height:480px;overflow:auto}
    .cap-code-view pre{margin:0;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--fg)}
    .cap-detail-editor{width:100%;min-height:360px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.6;resize:vertical;border-radius:8px;padding:12px;box-sizing:border-box}

    /* Existing Matrix & Dialog Styles */
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
    @media(max-width:650px){.cap-overlay{padding:6px}.cap-dialog{padding:14px;max-height:96vh}.cap-dialog textarea{min-height:300px}.cap-list-row{flex-direction:column;align-items:flex-start}.cap-row-meta,.cap-row-actions{width:100%;justify-content:space-between}}
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
    skillFileCache.clear();
    await load();
  }
  function checked(meta, aid, kind, itemId) {
    const binding = meta.bindings.find(item => item.assistant_id === aid && item.kind === kind && item.item_id === itemId);
    if (binding) return Boolean(binding.enabled);
    const global = meta.bindings.find(item => item.assistant_id === "*" && item.kind === kind && item.item_id === itemId);
    return global ? Boolean(global.enabled) : meta.default_enabled !== false;
  }
  let currentCategory = "all";
  let selectedId = null;
  let detailTab = "preview";
  let currentSection = "";
  const skillFileCache = new Map();

  function itemCategory(item) {
    const text = ((item.label || "") + " " + (item.description || item.desc || "") + " " + (item.id || item.name || "")).toLowerCase();
    if (/arxiv|pubmed|scholar|search|cite|lit|bib|paper|检索|文献|引文|阅读|深度阅读/.test(text)) return "literature";
    if (/write|doc|outline|draft|review|compile|edit|写作|大纲|起草|评审|排版|正文|修改/.test(text)) return "writing";
    if (/file|folder|vault|note|library|obsidian|read|attachment|skill_read|知识|工作区|笔记|附件/.test(text)) return "workspace";
    if (/feishu|zotero|mail|hook|webhook|飞书|邮件|集成|通讯/.test(text)) return "integration";
    return "general";
  }
  const CATEGORIES = [
    {id: "all", label: "全部", icon: "📋"},
    {id: "literature", label: "文献检索", icon: "🔎"},
    {id: "writing", label: "论文写作", icon: "✍️"},
    {id: "workspace", label: "知识与工作区", icon: "📚"},
    {id: "integration", label: "协同集成", icon: "🔌"},
    {id: "general", label: "基础通用", icon: "⚙️"}
  ];

  function categoryIcon(item) {
    const cat = itemCategory(item);
    const map = {literature: "🔎", writing: "✍️", workspace: "📚", integration: "🔌", general: "⚙️"};
    return map[cat] || "⚡";
  }
  function categoryLabel(item) {
    const cat = typeof item === "string" ? item : itemCategory(item);
    const found = CATEGORIES.find(c => c.id === cat);
    return found ? found.label : "基础通用";
  }
  function categoryCount(meta, catId) {
    const list = meta[setSec] || [];
    if (catId === "all") return list.length;
    return list.filter(item => itemCategory(item) === catId).length;
  }
  function bindingBadgeText(meta, item) {
    const kind = kinds[setSec];
    const assistants = meta.assistants || [];
    let count = 0;
    assistants.forEach(a => {
      if (checked(meta, a.id, kind, item.id)) count++;
    });
    if (count === 0) return `<span class="cap-binding-badge" style="opacity:.6">未授权专家</span>`;
    if (count === assistants.length) return `<span class="cap-binding-badge" style="color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,transparent)">全部专家 (${count})</span>`;
    return `<span class="cap-binding-badge">${count}/${assistants.length} 位专家</span>`;
  }

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
          ${CATEGORIES.map(c => `<button class="cap-cat-pill ${currentCategory === c.id ? "on" : ""}" data-cap-cat="${c.id}">${c.icon} ${c.label}</button>`).join("")}
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
  function drawListView(meta, items) {
    const section = setSec;
    if (!items.length) {
      return `<div class="card" style="text-align:center;padding:36px;color:var(--muted)"><h4>没有找到匹配的${labels[section]}</h4><p class="hint">可调整搜索词或切换上方的分类筛选。</p></div>`;
    }
    return `<div class="cap-list">
      ${items.map(item => `
        <div class="cap-list-row" data-cap-row="${e(item.id)}" title="点击查看详情">
          <div class="cap-row-icon" title="${e(categoryLabel(item))}">${categoryIcon(item)}</div>
          <div class="cap-row-main">
            <div class="cap-row-top">
              <span class="cap-item-name" title="${e(item.label || item.name)}">${e(item.label || item.name)}</span>
              <span class="pill cap-scope-pill ${item.scope === 'project' ? 'proj' : 'user'}">${item.scope === 'project' ? '项目' : '全局'}</span>
              ${section === 'mcp' ? `<span class="pill ${item.enabled ? 'on' : 'off'}">${item.enabled ? '启用' : '停用'}</span>` : ''}
              ${item.installation ? `<span class="pill" title="${e(item.installation.source)}">已安装</span>` : ''}
            </div>
            <div class="cap-row-desc" title="${e(item.description || item.desc || '')}">${e(item.description || item.desc || '暂无说明')}</div>
          </div>
          <div class="cap-row-meta">
            ${bindingBadgeText(meta, item)}
          </div>
          <div class="cap-row-actions">
            <button class="btn ghost cap-btn-sm" data-cap-detail="${e(item.id)}" title="进入详情页">详情 →</button>
            <button class="btn ghost cap-btn-sm" data-cap-preview="${e(item.id)}" title="快速弹窗预览/编辑">预览/编辑</button>
          </div>
        </div>
      `).join("")}
    </div>`;
  }

  function drawDetailView(meta, item, items) {
    const section = setSec;
    const kind = kinds[section];
    const assistants = [{id: "*", name: "默认 / 未绑定专家"}, ...meta.assistants];
    const currentIndex = items.findIndex(it => it.id === item.id);
    const hasPrev = currentIndex > 0;
    const hasNext = currentIndex >= 0 && currentIndex < items.length - 1;
    const prevId = hasPrev ? items[currentIndex - 1].id : "";
    const nextId = hasNext ? items[currentIndex + 1].id : "";

    return `
      <div class="card cap-detail-nav-card">
        <div class="cap-detail-nav">
          <div class="cap-detail-nav-left">
            <button class="btn ghost cap-btn-back" id="cap-back-list">← 返回技能列表</button>
            <div class="cap-breadcrumbs">
              <span class="muted">${labels[section]}</span>
              <span class="sep">/</span>
              <strong>${e(item.label || item.name)}</strong>
            </div>
          </div>
          <div class="cap-detail-nav-right">
            <button class="btn ghost cap-btn-sm" id="cap-prev-item" data-target="${e(prevId)}" ${hasPrev ? "" : "disabled"} title="查看上一个技能">← 上一项</button>
            <span class="hint" style="font-size:12px">${currentIndex >= 0 ? currentIndex + 1 : 1} / ${items.length}</span>
            <button class="btn ghost cap-btn-sm" id="cap-next-item" data-target="${e(nextId)}" ${hasNext ? "" : "disabled"} title="查看下一个技能">下一项 →</button>
          </div>
        </div>
      </div>

      <div class="card cap-detail-card">
        <div class="cap-detail-header">
          <div class="cap-detail-title-group">
            <div class="cap-detail-icon">${categoryIcon(item)}</div>
            <div>
              <h3 class="cap-detail-title">${e(item.label || item.name)}</h3>
              <div class="cap-detail-tags">
                <span class="pill cap-scope-pill ${item.scope === 'project' ? 'proj' : 'user'}">
                  ${item.scope === 'project' ? '项目技能 (.agents/skills)' : item.scope === 'builtin' ? '应用内置技能 (.agents/skills)' : '全局用户技能 (~/.agents/skills)'}
                </span>
                <span class="pill">${categoryLabel(item)}</span>
                ${section === 'mcp' ? `<span class="pill ${item.enabled ? 'on' : 'off'}">${item.enabled ? '已启用' : '已停用'}</span>` : ''}
              </div>
            </div>
          </div>
          <div class="cap-detail-head-actions">
            <button class="btn ghost cap-btn-sm" data-cap-preview="${e(item.id)}" title="弹窗形式预览与编辑">弹窗编辑</button>
          </div>
        </div>

        ${item.path ? `
        <div class="cap-detail-path-box">
          <span class="hint" style="font-weight:600;white-space:nowrap">文件路径：</span>
          <code id="cap-path-text" title="${e(item.path)}">${e(item.path)}</code>
          <button class="btn ghost cap-btn-sm" id="cap-copy-path">复制路径</button>
        </div>` : ''}
        ${item.installation ? `<p class="hint" style="overflow-wrap:anywhere">安装来源：${e(item.installation.source)} · ${item.installation.files} 个文件 · ${e(item.installation.installed_at)}</p>` : ''}

        <div class="cap-detail-section">
          <h4 class="cap-section-title">功能说明</h4>
          <div class="cap-detail-desc">${e(item.description || item.desc || "暂无说明")}</div>
        </div>

        <div class="cap-detail-section">
          <div class="cap-section-head">
            <div>
              <h4 class="cap-section-title">专家启用授权</h4>
              <p class="hint" style="margin:2px 0 0">指定哪些专家在工作流与对话中可以使用此能力。勾选立即保存。</p>
            </div>
            <div class="cap-bulk-btns">
              <button class="cap-btn-mini on" id="cap-detail-all-on">全部启用</button>
              <button class="cap-btn-mini off" id="cap-detail-all-off">全部停用</button>
            </div>
          </div>
          <div class="cap-detail-assistants-grid">
            ${assistants.map(a => `
              <label class="cap-detail-as-item ${checked(meta, a.id, kind, item.id) ? 'checked' : ''}">
                <input type="checkbox" data-detail-binding data-assistant="${e(a.id)}" data-item="${e(item.id)}" data-kind="${kind}" ${checked(meta, a.id, kind, item.id) ? 'checked' : ''}>
                <span class="cap-as-name">${e(a.name)}${a.id === '*' ? '（默认）' : ''}</span>
              </label>
            `).join("")}
          </div>
        </div>

        ${section === 'skills' ? `
        <div class="cap-detail-section">
          <div class="cap-section-head">
            <h4 class="cap-section-title">SKILL.md 规则定义</h4>
            <div class="cap-file-tabs">
              <button class="btn ghost cap-btn-sm ${detailTab === 'preview' ? 'active' : ''}" id="cap-tab-preview">文档预览</button>
              <button class="btn ghost cap-btn-sm ${detailTab === 'edit' ? 'active' : ''}" id="cap-tab-edit">编辑源码</button>
            </div>
          </div>
          <div id="cap-file-container">
            <p class="hint">正在加载 SKILL.md 内容…</p>
          </div>
        </div>` : ''}

        ${section === 'tools' ? `
        <div class="cap-detail-section">
          <div class="cap-section-head">
            <h4 class="cap-section-title">参数定义 (JSON Schema)</h4>
          </div>
          <pre class="cap-code-view"><code>${e(JSON.stringify(item.parameters, null, 2))}</code></pre>
        </div>` : ''}

        ${section === 'mcp' ? `
        <div class="cap-detail-section">
          <div class="cap-section-head">
            <h4 class="cap-section-title">已发现工具 (${item.tools.length})</h4>
            <button class="btn cap-btn-sm" data-cap-connect="${e(item.id)}">连接 / 拉取工具</button>
          </div>
          <div class="cap-list" style="margin-top:8px">
            ${item.tools.length ? item.tools.map(t => `
              <div class="cap-list-row" style="cursor:default">
                <div class="cap-row-main">
                  <div class="cap-row-top"><strong>${e(t.name)}</strong></div>
                  <div class="cap-row-desc">${e(t.description || "暂无说明")}</div>
                </div>
              </div>
            `).join("") : `<p class="hint">尚未发现工具，请点击“连接 / 拉取工具”拉取可用工具。</p>`}
          </div>
        </div>` : ''}
      </div>
    `;
  }

  function paint(host, meta) {
    const section = setSec;
    if (section !== currentSection) {
      currentSection = section;
      selectedId = null;
    }
    const selected = document.activeElement?.id === "cap-search";
    const position = selected ? document.activeElement.selectionStart : 0;
    const items = filtered(meta);

    // If an item is selected and we're in list view, render the detail view
    if (view === "list" && selectedId) {
      const selectedItem = (meta[section] || []).find(it => it.id === selectedId);
      if (selectedItem) {
        host.innerHTML = drawDetailView(meta, selectedItem, items) + `<p class="cap-error" data-error role="alert"></p>`;

        // Wire detail view events
        host.querySelector("#cap-back-list").onclick = () => {
          selectedId = null;
          paint(host, meta);
        };
        const prevBtn = host.querySelector("#cap-prev-item");
        if (prevBtn && prevBtn.dataset.target) {
          prevBtn.onclick = () => {
            selectedId = prevBtn.dataset.target;
            paint(host, meta);
          };
        }
        const nextBtn = host.querySelector("#cap-next-item");
        if (nextBtn && nextBtn.dataset.target) {
          nextBtn.onclick = () => {
            selectedId = nextBtn.dataset.target;
            paint(host, meta);
          };
        }
        const copyBtn = host.querySelector("#cap-copy-path");
        if (copyBtn && selectedItem.path) {
          copyBtn.onclick = () => {
            navigator.clipboard.writeText(selectedItem.path).then(() => {
              notify("路径已复制到剪贴板");
            }).catch(() => {
              notify("复制失败，请手动复制", "error");
            });
          };
        }
        host.querySelectorAll("[data-cap-preview]").forEach(button => button.onclick = () => {
          if (section === "skills") editSkill(selectedItem).catch(error => fail(host, error));
          else editTool(selectedItem);
        });

        // Assistant toggles
        host.querySelectorAll("[data-detail-binding]").forEach(input => input.onchange = async () => {
          const next = input.checked;
          const aid = input.dataset.assistant;
          const kind = input.dataset.kind;
          const itemId = input.dataset.item;
          input.disabled = true;
          try {
            const binding = {assistant_id: aid, kind, item_id: itemId, enabled: next};
            await api("/api/capabilities/binding", {...context(), ...binding});
            meta.bindings = meta.bindings.filter(x => !(x.assistant_id === aid && x.kind === kind && x.item_id === itemId));
            meta.bindings.push(binding);
            input.disabled = false;
            input.closest(".cap-detail-as-item")?.classList.toggle("checked", next);
            notify(next ? "已开启该专家授权" : "已停用该专家授权");
          } catch (err) {
            input.checked = !next;
            input.disabled = false;
            fail(host, err);
          }
        });

        const detailAllOn = host.querySelector("#cap-detail-all-on");
        if (detailAllOn) detailAllOn.onclick = async () => {
          const kind = kinds[section];
          const assistants = [{id: "*"}, ...meta.assistants];
          const updates = assistants.map(a => ({assistant_id: a.id, kind, item_id: selectedItem.id, enabled: true}));
          try {
            await api("/api/capabilities/bindings", {...context(), bindings: updates});
            updates.forEach(u => {
              meta.bindings = meta.bindings.filter(x => !(x.assistant_id === u.assistant_id && x.kind === u.kind && x.item_id === u.item_id));
              meta.bindings.push(u);
            });
            paint(host, meta);
            notify("已对所有专家开启授权");
          } catch (err) { fail(host, err); }
        };

        const detailAllOff = host.querySelector("#cap-detail-all-off");
        if (detailAllOff) detailAllOff.onclick = async () => {
          const kind = kinds[section];
          const assistants = [{id: "*"}, ...meta.assistants];
          const updates = assistants.map(a => ({assistant_id: a.id, kind, item_id: selectedItem.id, enabled: false}));
          try {
            await api("/api/capabilities/bindings", {...context(), bindings: updates});
            updates.forEach(u => {
              meta.bindings = meta.bindings.filter(x => !(x.assistant_id === u.assistant_id && x.kind === u.kind && x.item_id === u.item_id));
              meta.bindings.push(u);
            });
            paint(host, meta);
            notify("已对所有专家停用授权");
          } catch (err) { fail(host, err); }
        };

        // File loader for SKILL.md
        if (section === "skills") {
          const container = host.querySelector("#cap-file-container");
          const tabPreview = host.querySelector("#cap-tab-preview");
          const tabEdit = host.querySelector("#cap-tab-edit");

          function renderFileBody(content) {
            if (!container) return;
            if (detailTab === "preview") {
              container.innerHTML = `<div class="cap-code-view"><pre><code>${e(content)}</code></pre></div>`;
            } else {
              container.innerHTML = `
                <textarea class="control cap-detail-editor" id="cap-edit-area" spellcheck="false" aria-label="技能文件内容">${e(content)}</textarea>
                <div class="cap-card-actions" style="margin-top:10px">
                  <button class="btn btn-primary" id="cap-file-save">保存到技能文件</button>
                  <button class="btn ghost" id="cap-file-cancel">放弃修改</button>
                </div>
              `;
              const saveBtn = container.querySelector("#cap-file-save");
              const cancelBtn = container.querySelector("#cap-file-cancel");
              const editArea = container.querySelector("#cap-edit-area");
              if (saveBtn) {
                saveBtn.onclick = async () => {
                  saveBtn.disabled = true;
                  saveBtn.textContent = "保存中…";
                  try {
                    await api("/api/capabilities/skill", {...context(), id: selectedItem.id, content: editArea.value});
                    skillFileCache.set(projectId + ":" + selectedItem.id, editArea.value);
                    notify("技能文件已保存");
                    detailTab = "preview";
                    tabPreview?.classList.add("active");
                    tabEdit?.classList.remove("active");
                    renderFileBody(editArea.value);
                    await update();
                  } catch (err) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = "保存到技能文件";
                    fail(host, err);
                  }
                };
              }
              if (cancelBtn) {
                cancelBtn.onclick = () => {
                  detailTab = "preview";
                  tabPreview?.classList.add("active");
                  tabEdit?.classList.remove("active");
                  renderFileBody(content);
                };
              }
            }
          }

          if (tabPreview) tabPreview.onclick = () => {
            detailTab = "preview";
            tabPreview.classList.add("active");
            tabEdit?.classList.remove("active");
            const cacheKey = projectId + ":" + selectedItem.id;
            renderFileBody(skillFileCache.get(cacheKey) || "");
          };
          if (tabEdit) tabEdit.onclick = () => {
            detailTab = "edit";
            tabEdit.classList.add("active");
            tabPreview?.classList.remove("active");
            const cacheKey = projectId + ":" + selectedItem.id;
            renderFileBody(skillFileCache.get(cacheKey) || "");
          };

          const cacheKey = projectId + ":" + selectedItem.id;
          if (skillFileCache.has(cacheKey)) {
            renderFileBody(skillFileCache.get(cacheKey));
          } else {
            api("/api/capabilities/skill?" + new URLSearchParams({...context(), id: selectedItem.id})).then(result => {
              skillFileCache.set(cacheKey, result.content || "");
              renderFileBody(result.content || "");
            }).catch(err => {
              if (container) container.innerHTML = `<p class="cap-error">${e(err.message || String(err))}</p>`;
            });
          }
        }

        return;
      }
      // If item not found, reset selectedId
      selectedId = null;
    }

    // Default: List or Matrix view
    host.innerHTML = `
      <div class="card">
        <h3>${labels[section]} <span class="pill">${items.length} / ${(meta[section] || []).length}</span></h3>
        <div class="cap-toolbar">
          <select class="control" id="cap-project" aria-label="项目技能作用域">
            <option value="">用户技能（全局）</option>
            ${(S.tasks || []).map(task => `<option value="${e(task.id)}" ${task.id === projectId ? "selected" : ""}>${e(task.title)}</option>`).join("")}
          </select>
          <input class="control" id="cap-search" value="${e(search)}" placeholder="搜索名称、说明" aria-label="搜索能力">
          <button class="btn ${view === "list" ? "" : "ghost"}" data-cap-view="list">列表</button>
          <button class="btn ${view === "matrix" ? "" : "ghost"}" data-cap-view="matrix">专家启用矩阵</button>
          <button class="btn ghost" id="cap-refresh">刷新</button>
          ${section === "skills" ? `<button class="btn" id="cap-install">安装技能</button>` : ""}
          ${section === "skills" ? `<button class="btn ghost" id="cap-skill-sources">来源管理</button>` : ""}
          ${section === "mcp" ? `<button class="btn" id="cap-mcp-json">JSON 编辑 / 密钥</button>` : ""}
        </div>
        <p class="hint">
          ${section === "skills" ? "默认读取用户 ~/.agents/skills，选择项目后叠加项目 .agents/skills；同名项目技能优先。点击技能列表项进入详情并可编辑 SKILL.md。" : section === "tools" ? "Tools 与 MCP 独立管理。可以编辑内置工具的显示名称及给模型的说明；工具实现与参数结构只读。" : "保存 JSON 不启动进程。仅点击连接或专家实际调用已发现工具时执行本地命令/联网；请只使用可信 MCP。"}
        </p>
        ${section === "skills" ? `
        <div class="cap-cats" style="margin-top:12px">
          ${CATEGORIES.map(c => `
            <button class="cap-cat-pill ${currentCategory === c.id ? "on" : ""}" data-cap-cat="${c.id}">
              ${c.icon} ${c.label} <span class="cap-cat-count">${categoryCount(meta, c.id)}</span>
            </button>
          `).join("")}
        </div>` : ""}
        ${section === "mcp" ? `<p class="hint">凭据保护：${e(meta.storage_security)}。环境变量、请求头、命令参数和密钥默认隐藏。</p>` : ""}
      </div>
      ${view === "matrix" ? drawMatrix(meta, items) : drawListView(meta, items)}
      ${section === "skills" ? `
      <div class="card cap-hint" style="margin-top:14px">
        <h4 style="margin:0 0 8px">技能搜索目录（后者优先）</h4>
        ${meta.skill_roots.map(root => `<p class="hint" style="margin:4px 0"><code>${e(root.path)}</code> · ${e(root.scope)} · ${root.exists ? "存在" : "尚未创建"}</p>`).join("")}
      </div>` : ""}
      <p class="cap-error" data-error role="alert"></p>
    `;

    host.querySelector("#cap-project").onchange = event => { projectId = event.target.value; selectedId = null; load(); };
    host.querySelector("#cap-search").oninput = event => { search = event.target.value; paint(host, meta); };
    const installButton = host.querySelector("#cap-install");
    if (installButton) installButton.onclick = () => window.SkillInstaller.open({
      dialog, projectId, projects: S.tasks || [], onInstalled: update
    });
    const sourcesButton = host.querySelector("#cap-skill-sources");
    if (sourcesButton) sourcesButton.onclick = () => window.SkillInstaller.open({
      dialog, projectId, projects: S.tasks || [], onInstalled: update, initialTab: "sources"
    });
    host.querySelectorAll("[data-cap-view]").forEach(button => button.onclick = () => { view = button.dataset.capView; selectedId = null; paint(host, meta); });
    host.querySelectorAll("[data-cap-cat]").forEach(b => b.onclick = () => { currentCategory = b.dataset.capCat; paint(host, meta); });

    // List row click to enter detail view
    host.querySelectorAll("[data-cap-row]").forEach(row => {
      row.onclick = event => {
        if (event.target.closest("button") || event.target.closest("input")) return;
        selectedId = row.dataset.capRow;
        paint(host, meta);
      };
    });
    host.querySelectorAll("[data-cap-detail]").forEach(button => {
      button.onclick = event => {
        event.stopPropagation();
        selectedId = button.dataset.capDetail;
        paint(host, meta);
      };
    });

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
      if (!await uiConfirm("连接将运行配置中的本地命令（拥有当前用户权限）或访问远程 MCP，并可能读取敏感数据。确认信任该服务器并继续？")) return;
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
      dialog(item.name + " · MCP 工具", `<p class="hint">${item.tools.length ? "已发现工具；专家实际调用时会重新连接服务器。" : "尚未发现工具，请先明确点击“连接 / 拉取工具”。"}</p><pre>${e(JSON.stringify(item.tools, null, 2))}</pre>`);
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
        if (!await uiConfirm("即将在当前屏幕显示所有 MCP 密钥和命令参数，并替换未保存草稿。请确认周围环境安全。")) return;
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
