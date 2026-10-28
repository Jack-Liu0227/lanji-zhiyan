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
  uiOpenModal(modal, $("btnDiffCancel"));
}

function closeDiffModal() {
  const modal = $("diffModal");
  if (modal) uiCloseModal(modal);
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
  const items = typeof AppInteractions === "undefined" ? [] : AppInteractions.commandItems();

  const NAV_ITEMS=NAV.flatMap(group=>group.items).map(item=>({id:item.id,title:item.label,ico:item.ico}));
  items.push({text:'新建项目',sub:'在项目工作区创建项目',ico:'＋',cat:'项目',run:()=>ProjectManager.open()});
  NAV_ITEMS.forEach(n => {
    items.push({
      text: n.title,
      sub: `跳转至 ${n.title}`,
      ico: n.ico,
      cat: "主模块",
      run: () => uiNavigate(()=>openTab(n.id))
    });
  });

  (S.tasks || []).forEach(t => {
    items.push({
      text: `项目: ${t.title}`,
      sub: t.question || "切换当前科研课题",
      ico: "📂",
      cat: "项目",
      run: () => uiNavigate(()=>{
        curTask = t.id;
        spId = t.id;
        localStorage.setItem("rw-task", curTask);
        openTab("space");
      })
    });
  });

  items.push({
    text: "切换深色主题 (Dark Theme)",
    sub: "Dark Obsidian 纯黑极简学术模式",
    ico: "🌙",
    cat: "主题",
    run: () => uiNavigate(()=>setTheme("dark"))
  });
  items.push({
    text: "切换浅色主题 (Light Theme)",
    sub: "Paper White 高清高对比度纸质模式",
    ico: "☀️",
    cat: "主题",
    run: () => uiNavigate(()=>setTheme("light"))
  });
  items.push({
    text: "从 Zotero 导入文献",
    sub: "检索本地 Zotero 条目并导入",
    ico: "📖",
    cat: "工具",
    run: () => zotOpen()
  });

  return items;
}

function openCommandPalette() {
  const modal = $("cmdPaletteModal");
  if (!modal) return;
  const input = $("cmdPaletteInput");
  if (input) {
    input.value = "";
  }
  cmdActiveIndex = 0;
  renderCommandResults("");
  uiOpenModal(modal, input);
  input?.setAttribute("aria-expanded", "true");
}

function closeCommandPalette() {
  const modal = $("cmdPaletteModal");
  if (modal) uiCloseModal(modal);
  $("cmdPaletteInput")?.setAttribute("aria-expanded", "false");
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
    $("cmdPaletteInput")?.removeAttribute("aria-activedescendant");
    listEl.innerHTML = `<div style="text-align:center;padding:24px 0;color:var(--muted);font-size:13px">未找到匹配的命令或模块</div>`;
    return;
  }

  listEl.innerHTML = cmdItems.map((item, idx) => `
    <div class="cmd-item ${idx === cmdActiveIndex ? "active" : ""}" id="cmd-option-${idx}" role="option" aria-selected="${idx === cmdActiveIndex}" data-cmd-idx="${idx}">
      <span class="cmd-ico">${item.ico}</span>
      <span class="cmd-text">${esc(item.text)}<span class="cmd-sub">${esc(item.sub)}</span></span>
      ${item.shortcut?`<kbd>${esc(item.shortcut)}</kbd>`:""}<span class="cmd-tag">${esc(item.cat)}</span>
    </div>
  `).join("");

  listEl.querySelectorAll(".cmd-item").forEach(el => {
    el.onmouseenter = () => {
      cmdActiveIndex = Number(el.dataset.cmdIdx);
      listEl.querySelectorAll(".cmd-item").forEach((x, i) => { x.classList.toggle("active", i === cmdActiveIndex); x.setAttribute("aria-selected",i===cmdActiveIndex); });
      $("cmdPaletteInput").setAttribute("aria-activedescendant",el.id);
    };
    el.onclick = () => {
      cmdActiveIndex = Number(el.dataset.cmdIdx);
      executeActiveCommand();
    };
  });

  const activeEl = listEl.querySelector(".cmd-item.active");
  if(activeEl) $("cmdPaletteInput").setAttribute("aria-activedescendant",activeEl.id);
  if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
}

function executeActiveCommand() {
  if (cmdItems[cmdActiveIndex] && typeof cmdItems[cmdActiveIndex].run === "function") {
    const act = cmdItems[cmdActiveIndex].run;
    closeCommandPalette();
    Promise.resolve().then(act).catch(e=>uiNotify("操作失败："+e.message));
  }
}

function handleCommandPaletteKey(e) {
  if(e.isComposing || e.keyCode === 229) return false;
  if(e.key === "Escape" || ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k")) {
    closeCommandPalette(); return true;
  }
  if(e.target !== $("cmdPaletteInput")) return false;
  if(["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
    if(cmdItems.length){
      cmdActiveIndex = e.key === "Home" ? 0 : e.key === "End" ? cmdItems.length - 1
        : (cmdActiveIndex + (e.key === "ArrowDown" ? 1 : -1) + cmdItems.length) % cmdItems.length;
      renderCommandResults($("cmdPaletteInput").value);
    }
    return true;
  }
  if(e.key === "Enter") { if(!e.repeat) executeActiveCommand(); return true; }
  return false;
}

$("cmdPaletteClose")?.addEventListener("click", closeCommandPalette);
$("cmdPaletteMask")?.addEventListener("click", closeCommandPalette);
$("cmdPaletteInput")?.addEventListener("input", (e) => {
  cmdActiveIndex = 0;
  renderCommandResults(e.target.value);
});
