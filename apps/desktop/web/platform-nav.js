// -*- coding: utf-8 -*-
/**
 * 科研平台快速导航模块
 * 支持 Google Scholar / arXiv / PubMed / CNKI / IEEE / ScienceDirect / DBLP 等平台检索一键直达
 */
(function () {
  const PLATFORMS = [
    { id: "scholar", name: "Google Scholar", icon: "🎓", url: "https://scholar.google.com/scholar?q={q}" },
    { id: "arxiv", name: "arXiv", icon: "📄", url: "https://arxiv.org/search/?query={q}&searchtype=all" },
    { id: "pubmed", name: "PubMed", icon: "🧬", url: "https://pubmed.ncbi.nlm.nih.gov/?term={q}" },
    { id: "wos", name: "Web of Science", icon: "🌐", url: "https://www.webofscience.com/wos/woscc/basic-search" },
    { id: "cnki", name: "中国知网 (CNKI)", icon: "📚", url: "https://kns.cnki.net/kns8s/defaultresult/index?korder=SU&kw={q}" },
    { id: "ieee", name: "IEEE Xplore", icon: "⚡", url: "https://ieeexplore.ieee.org/search/searchresult.jsp?newsearch=true&queryText={q}" },
    { id: "sciencedirect", name: "ScienceDirect", icon: "🔬", url: "https://www.sciencedirect.com/search?qs={q}" },
    { id: "dblp", name: "DBLP", icon: "💻", url: "https://dblp.org/search?q={q}" }
  ];

  function openPlatform(platformId, query) {
    const p = PLATFORMS.find(item => item.id === platformId) || PLATFORMS[0];
    const encoded = encodeURIComponent((query || "").trim());
    const targetUrl = p.url.replace("{q}", encoded);
    window.open(targetUrl, "_blank");
  }

  function renderPlatformNav(container, initialQuery) {
    if (!container) return;
    const bar = document.createElement("div");
    bar.className = "platform-nav-bar card";
    bar.style.padding = "10px 14px";
    bar.style.marginBottom = "14px";
    bar.style.display = "flex";
    bar.style.alignItems = "center";
    bar.style.gap = "8px";
    bar.style.flexWrap = "wrap";

    const label = document.createElement("span");
    label.style.fontWeight = "600";
    label.style.fontSize = "13px";
    label.style.marginRight = "4px";
    label.textContent = "科研平台直达：";
    bar.appendChild(label);

    PLATFORMS.forEach(p => {
      const btn = document.createElement("button");
      btn.className = "btn ghost sm";
      btn.style.padding = "4px 8px";
      btn.style.fontSize = "12px";
      btn.title = `在 ${p.name} 中检索`;
      btn.innerHTML = `${p.icon} ${p.name}`;
      btn.onclick = () => {
        const q = initialQuery || (window.curTask && window.S?.taskMap?.[window.curTask]?.title) || "";
        openPlatform(p.id, q);
      };
      bar.appendChild(btn);
    });

    container.prepend(bar);
  }

  window.ResearchPlatforms = {
    list: PLATFORMS,
    open: openPlatform,
    render: renderPlatformNav
  };
})();
