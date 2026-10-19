function renderStar(m){
  m.innerHTML = `<div class="sm-wrap">
      <div class="sm-bar">
      <b>知识星图</b>
      <span class="hint">点项目进分支 → 点星团看条目 → 点条目看分析，Esc 逐层退回</span>
      <button class="btn ghost" id="smRebuild" type="button" style="margin-left:auto" title="重新扫库编译一次（平时只读上次编译结果，不实时重算）">重算星图</button>
    </div>
    <iframe id="kbFrame" class="sm-frame" src="/kb.html?theme=${document.documentElement.dataset.theme||"dark"}" title="知识星图"></iframe>
  </div>`;
  const rb = m.querySelector("#smRebuild"), fr = m.querySelector("#kbFrame");
  if(rb && fr) rb.onclick = ()=>{                    // 星图是编译产物：只有这里（或第一次进知识库）才重算
    rb.disabled = true; rb.textContent = "重算中…";
    fr.onload = ()=>{ rb.disabled = false; rb.textContent = "重算星图"; fr.onload = null; };
    fr.src = "/kb.html?theme=" + (document.documentElement.dataset.theme||"dark") + "&rebuild=1";
  };
}

// 星图里点「在应用里打开」：论文开 PDF，笔记跳到笔记页（其他类型不猜，留在星图里看分析）
window.addEventListener("message", e => {
  const d = e.data || {};
  if(d.rw === "theme"){ setTheme(d.theme || "dark"); return; }   // 星图只在深色下画：点提示里的按钮直接切过来
  if(d.rw !== "open") return;
  if(d.kind === "post") api("/api/paper/open?id=" + encodeURIComponent(d.ref)).catch(err => alert("打开失败：" + err.message));
  else if(d.kind === "note") openTab("notes");
});

/* ---------------- 笔记（大笔记：Markdown 编辑器，不绑项目） ---------------- */
let mdNotes=[], mdActiveId=null, mdSaveTimer=null, mdLoaded=false, mdFocusOn=false, mdNarrowOn=false, mdFocusPrev="split";
