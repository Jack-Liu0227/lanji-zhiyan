/* ==================== v1.2.0 全局命令面板 & 差异对比 & 快捷键流 ==================== */

// ---------- Diff 对比与双向合并 ----------
let diffAcceptCallback = null;
let diffRejectCallback = null;

function computeDiff(oldStr, newStr) {
  const oldLines = String(oldStr || "").split("\n");
  const newLines = String(newStr || "").split("\n");
  const diffs = [];
  let i = 0, j = 0;
  let additions = 0, deletions = 0;

  while (i < oldLines.length || j < newLines.length) {
    if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
      diffs.push({ type: "same", text: oldLines[i] });
      i++; j++;
    } else if (j < newLines.length && (i >= oldLines.length || !oldLines.slice(i, i + 6).includes(newLines[j]))) {
      diffs.push({ type: "add", text: newLines[j] });
      additions++;
      j++;
    } else if (i < oldLines.length) {
      diffs.push({ type: "del", text: oldLines[i] });
      deletions++;
      i++;
    } else {
      diffs.push({ type: "add", text: newLines[j] });
      additions++;
      j++;
    }
  }
  return { lines: diffs, additions, deletions, total: diffs.length };
}

function showDiffModal(oldText, newText, onAccept, onReject) {
  const modal = $("diffModal");
  if (!modal) return;
  diffAcceptCallback = onAccept || null;
  diffRejectCallback = onReject || null;

  const result = computeDiff(oldText, newText);
  const statsEl = $("diffStats");
  if (statsEl) {
    statsEl.innerHTML = `<span>总比对行数: ${result.total}</span> <span class="add">+${result.additions} 新增</span> <span class="del">-${result.deletions} 删除</span>`;
  }
  const cont = $("diffContainer");
  if (cont) {
    cont.innerHTML = result.lines.map((l, idx) => {
      const sym = l.type === "add" ? "+ " : (l.type === "del" ? "- " : "  ");
      return `<div class="diff-line ${l.type}"><span style="user-select:none;color:var(--muted);width:32px;text-align:right">${idx + 1}</span><span>${sym}${esc(l.text)}</span></div>`;
    }).join("");
  }
  modal.classList.remove("hidden");
}

function closeDiffModal() {
  const modal = $("diffModal");
  if (modal) modal.classList.add("hidden");
  diffAcceptCallback = null;
  diffRejectCallback = null;
}

$("btnDiffClose")?.addEventListener("click", closeDiffModal);
$("btnDiffCancel")?.addEventListener("click", closeDiffModal);
$("diffModalMask")?.addEventListener("click", closeDiffModal);
$("btnDiffAccept")?.addEventListener("click", () => {
  if (diffAcceptCallback) diffAcceptCallback();
  closeDiffModal();
});
$("btnDiffReject")?.addEventListener("click", () => {
  if (diffRejectCallback) diffRejectCallback();
  closeDiffModal();
});


// ---------- 全局命令面板 (Command Palette - Ctrl+K) ----------
let cmdActiveIndex = 0;
let cmdItems = [];

function buildCommandList() {
  const items = [];

  const NAV_ITEMS = [
    { id: "agent", title: "Agent 全局对话", ico: "💬", cat: "导航" },
    { id: "assistants", title: "专家管理", ico: "👨‍🔬", cat: "导航" },
    { id: "team", title: "团队协作", ico: "👥", cat: "导航" },
    { id: "calendar", title: "日历与日程", ico: "📅", cat: "导航" },
    { id: "tasks", title: "项目管理", ico: "📁", cat: "导航" },
    { id: "space", title: "项目空间", ico: "🌌", cat: "导航" },
    { id: "workflow", title: "工作流总览", ico: "🔀", cat: "导航" },
    { id: "schedules", title: "定时任务", ico: "⏰", cat: "导航" },
    { id: "library", title: "知识星图", ico: "📚", cat: "导航" },
    { id: "notes", title: "笔记中心", ico: "📝", cat: "导航" },
    { id: "intel", title: "情报日报 (RSS)", ico: "📡", cat: "导航" },
    { id: "mail", title: "邮箱通讯", ico: "✉️", cat: "导航" },
    { id: "settings", title: "系统设置", ico: "⚙️", cat: "导航" },
  ];
  NAV_ITEMS.forEach(n => {
    items.push({
      text: n.title,
      sub: `跳转至 ${n.title}`,
      ico: n.ico,
      cat: "主模块",
      run: () => openTab(n.id)
    });
  });

  WF_ORDER.forEach((s, idx) => {
    const rawName = STAGE[s] || s;
    const ico = rawName.split(" ")[0] || "✦";
    const name = rawName.slice(rawName.indexOf(" ") + 1) || rawName;
    items.push({
      text: `Step ${idx + 1}: ${name}`,
      sub: `直达工作流第 ${idx + 1} 步`,
      ico: ico,
      cat: "工作流步骤",
      run: () => {
        openTab("workflow");
        wfStep = s;
        localStorage.setItem("rw-wf-step", wfStep);
        renderWorkflow($("main"));
      }
    });
  });

  (S.tasks || []).forEach(t => {
    items.push({
      text: `项目: ${t.title}`,
      sub: t.question || "切换当前科研课题",
      ico: "📂",
      cat: "项目",
      run: () => {
        curTask = t.id;
        localStorage.setItem("rw-task", curTask);
        render();
      }
    });
  });

  items.push({
    text: "切换深色主题 (Dark Theme)",
    sub: "Dark Obsidian 纯黑极简学术模式",
    ico: "🌙",
    cat: "主题",
    run: () => {
      document.documentElement.dataset.theme = "dark";
      localStorage.setItem("rw-theme", "dark");
      applyTheme(currentPrimary());
    }
  });
  items.push({
    text: "切换浅色主题 (Light Theme)",
    sub: "Paper White 高清高对比度纸质模式",
    ico: "☀️",
    cat: "主题",
    run: () => {
      document.documentElement.dataset.theme = "light";
      localStorage.setItem("rw-theme", "light");
      applyTheme(currentPrimary());
    }
  });
  items.push({
    text: "从 Zotero 导入文献",
    sub: "检索本地 Zotero 条目并导入",
    ico: "📖",
    cat: "工具",
    run: () => openZotero()
  });

  return items;
}

function openCommandPalette() {
  const modal = $("cmdPaletteModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  const input = $("cmdPaletteInput");
  if (input) {
    input.value = "";
    input.focus();
  }
  cmdActiveIndex = 0;
  renderCommandResults("");
}

function closeCommandPalette() {
  const modal = $("cmdPaletteModal");
  if (modal) modal.classList.add("hidden");
}

function renderCommandResults(query) {
  const listEl = $("cmdPaletteResults");
  if (!listEl) return;
  const q = String(query || "").trim().toLowerCase();
  const all = buildCommandList();
  cmdItems = q ? all.filter(item =>
    item.text.toLowerCase().includes(q) ||
    item.sub.toLowerCase().includes(q) ||
    item.cat.toLowerCase().includes(q)
  ) : all;

  if (cmdActiveIndex >= cmdItems.length) cmdActiveIndex = Math.max(0, cmdItems.length - 1);

  if (!cmdItems.length) {
    listEl.innerHTML = `<div style="text-align:center;padding:24px 0;color:var(--muted);font-size:13px">未找到匹配的命令或模块</div>`;
    return;
  }

  listEl.innerHTML = cmdItems.map((item, idx) => `
    <div class="cmd-item ${idx === cmdActiveIndex ? "active" : ""}" data-cmd-idx="${idx}">
      <span class="cmd-ico">${item.ico}</span>
      <span class="cmd-text">${esc(item.text)}<span class="cmd-sub">${esc(item.sub)}</span></span>
      <span class="cmd-tag">${esc(item.cat)}</span>
    </div>
  `).join("");

  listEl.querySelectorAll(".cmd-item").forEach(el => {
    el.onmouseenter = () => {
      cmdActiveIndex = Number(el.dataset.cmdIdx);
      listEl.querySelectorAll(".cmd-item").forEach((x, i) => x.classList.toggle("active", i === cmdActiveIndex));
    };
    el.onclick = () => {
      executeActiveCommand();
    };
  });

  const activeEl = listEl.querySelector(".cmd-item.active");
  if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
}

function executeActiveCommand() {
  if (cmdItems[cmdActiveIndex] && typeof cmdItems[cmdActiveIndex].run === "function") {
    const act = cmdItems[cmdActiveIndex].run;
    closeCommandPalette();
    try { act(); } catch(e) { console.error(e); }
  }
}

window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    const modal = $("cmdPaletteModal");
    if (modal && !modal.classList.contains("hidden")) {
      closeCommandPalette();
    } else {
      openCommandPalette();
    }
    return;
  }

  const pModal = $("cmdPaletteModal");
  if (pModal && !pModal.classList.contains("hidden")) {
    if (e.key === "Escape") {
      e.preventDefault();
      closeCommandPalette();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (cmdItems.length) {
        cmdActiveIndex = (cmdActiveIndex + 1) % cmdItems.length;
        renderCommandResults($("cmdPaletteInput")?.value || "");
      }
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdItems.length) {
        cmdActiveIndex = (cmdActiveIndex - 1 + cmdItems.length) % cmdItems.length;
        renderCommandResults($("cmdPaletteInput")?.value || "");
      }
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      executeActiveCommand();
      return;
    }
  }

  if (e.key === "Escape") {
    closeDiffModal();
    const prof = $("profileModal");
    if (prof && !prof.classList.contains("hidden")) prof.classList.add("hidden");
    const zot = $("zoteroModal");
    if (zot && !zot.classList.contains("hidden")) zot.classList.add("hidden");
  }

  if (e.altKey && !e.ctrlKey && !e.metaKey) {
    const code = e.key;
    let stepIndex = -1;
    if (code >= "1" && code <= "9") stepIndex = parseInt(code, 10) - 1;
    else if (code === "0") stepIndex = 9;

    if (stepIndex >= 0 && stepIndex < WF_ORDER.length) {
      e.preventDefault();
      openTab("workflow");
      wfStep = WF_ORDER[stepIndex];
      localStorage.setItem("rw-wf-step", wfStep);
      renderWorkflow($("main"));
      return;
    }
  }

  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    const act = document.activeElement;
    if (act && (act.tagName === "TEXTAREA" || act.tagName === "INPUT")) {
      const parentAt = act.closest(".composer, .at-dock, .ol-grid");
      if (parentAt) {
        const sendBtn = parentAt.querySelector(".send-btn, #send, button[id$='-send']");
        if (sendBtn && !sendBtn.disabled) {
          e.preventDefault();
          sendBtn.click();
        }
      }
    }
  }
});

$("cmdPaletteMask")?.addEventListener("click", closeCommandPalette);
$("cmdPaletteInput")?.addEventListener("input", (e) => {
  cmdActiveIndex = 0;
  renderCommandResults(e.target.value);
});
