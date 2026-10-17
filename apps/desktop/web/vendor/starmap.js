/* 论文星图（SVG 版）：绘制、布局、配色、星云贴图着色与交互模型照搬参考实现，
   只把 React 外壳换成本项目的 DOM 挂载：StarMap.mount(el, graph)。
   性能：整张图只构建一次；平移/缩放/拖节点只改变换属性，不重建 DOM。
   graph = { nodes:[{id,title,authors,year,degree,doi}], edges:[{source,target,kind,weight}] } */
(function () {
  "use strict";

  var SPRITE_CACHE = new Map();
  var FRINGE_CACHE = new Map();
  var textureStats = { generations: 0, durationMs: 0 };
  var TEXTURE_URL = "/vendor/media/paper-nodes/user-round-nebula-v4-20261006.png";
  var ne = null, oe = null;

  function loadTexture() {
    if (oe) return oe;
    oe = new Promise(function (res) {
      var img = new Image();
      img.onload = function () { ne = img; res(true); };
      img.onerror = function () { res(false); };
      img.src = TEXTURE_URL;
    });
    return oe;
  }
  function hasTexture() { return !!ne; }

  var smoothstep = function (s) {
    var i = Math.max(0, Math.min(1, s));
    return i * i * (3 - 2 * i);
  };
  var rand01 = function (s) { return (Math.imul(s + 7919, 2246822507) >>> 0) % 1e5 / 1e5; };

  /* 星云贴图着色：按年份色染色 + 压暗边缘。按「颜色 + 旋转档」缓存，最多 4×8 张。 */
  function lightMode() { return document.documentElement.dataset.theme === "light"; }
  function makeSprite(color, rot, unknown) {
    var light = lightMode();
    var key = color + ":" + rot + ":" + unknown + ":" + !!ne + ":" + light;
    if (SPRITE_CACHE.has(key)) return SPRITE_CACHE.get(key);
    var t0 = performance.now();
    var cv = document.createElement("canvas");
    var y = 256;
    cv.width = cv.height = y;
    var D = cv.getContext("2d");
    if (ne) {
      D.translate(y / 2, y / 2);
      D.rotate((rot - 1.5) * 0.12);
      D.drawImage(ne, -y * 0.4, -y * 0.4, y * 0.8, y * 0.8);
      D.resetTransform();
      var px = D.getImageData(0, 0, y, y);
      var h = color.charAt(0) === "#"
        ? [1, 3, 5].map(function (r) { return parseInt(color.slice(r, r + 2), 16); })
        : (color.match(/\d+/g) || []).map(Number);
      for (var r = 0; r < px.data.length; r += 4) {
        var l = (r / 4) % y, m = Math.floor(r / 4 / y);
        var S = Math.hypot(l - y / 2, m - y / 2) / (y * 0.343);
        var C = (px.data[r] * 0.2126 + px.data[r + 1] * 0.7152 + px.data[r + 2] * 0.0722) / 255;
        var X = 1 - smoothstep((S - 0.58) / 0.42);
        var core = Math.exp(-Math.pow(S / 0.11, 2));
        var P = Math.min(0.94, Math.pow(C, 3) * 0.7 + core * 0.65);
        var M = 0.56 + 0.44 * Math.sqrt(C);
        px.data[r + 3] = Math.round(255 * Math.pow(px.data[r + 3] / 255, 0.68) * Math.pow(X, 1.3) * (0.82 + 0.18 * Math.sqrt(C)));
        for (var O = 0; O < 3; O++) {
          px.data[r + O] = Math.round((h[O] === undefined ? 180 : h[O]) * M * (1 - P) + 255 * P);
        }
      }
      D.putImageData(px, 0, 0);
      if (!FRINGE_CACHE.has(rot)) {
        FRINGE_CACHE.set(rot, Array.from({ length: 48 }, function (_, k) {
          var a = rand01(k * 17 + rot * 31) * Math.PI * 2;
          var rad = 0.25 + 0.115 * rand01(k * 53 + rot * 7);
          return {
            x: Math.cos(a) * rad, y: Math.sin(a) * rad,
            r: 0.0015 + 0.002 * rand01(k * 71),
            opacity: 0.06 + 0.15 * rand01(k * 11),
            phase: rand01(k * 19) * 8,
          };
        }));
      }
    }
    var url = cv.toDataURL("image/png");
    SPRITE_CACHE.set(key, url);
    if (ne) { textureStats.generations++; textureStats.durationMs += performance.now() - t0; }
    return url;
  }
  function fringeOf(rot) { return FRINGE_CACHE.get(rot) || []; }

  function radiusOf(citations, max) {
    if (citations === null) return 4.5;
    if (max <= 0) return 5.5;
    var t = Math.log1p(Math.max(0, citations)) / Math.log1p(max);
    return 5.5 + 10.5 * t + 6 * Math.pow(Math.max(0, (t - 0.45) / 0.55), 2);
  }
  /* 边的起止点让到星云可见光晕之外，否则箭头被星云盖住（只在拖动时才露出来） */
  function spriteRadius(d, max) {
    return Math.max(18, radiusOf((d || {}).citation_count, max) * 1.75);
  }
  function norm01(v, min, max) {
    if (typeof v !== "number" || !isFinite(v) || v <= 0) return null;
    return min === max ? 0.5 : Math.max(0, Math.min(1, (v - min) / (max - min)));
  }
  function yearColor(t) {
    if (t === null) return "#9ba6b5";
    var stops = [
      { at: 0, rgb: [255, 142, 92] },
      { at: 0.32, rgb: [255, 212, 119] },
      { at: 0.68, rgb: [120, 194, 255] },
      { at: 1, rgb: [244, 248, 255] },
    ];
    var idx = stops.findIndex(function (n) { return n.at >= t; });
    var b = stops[Math.max(1, idx)], a = stops[Math.max(0, idx - 1)];
    var g = (t - a.at) / (b.at - a.at);
    return "rgb(" + a.rgb.map(function (n, k) { return Math.round(n + (b.rgb[k] - n) * g); }).join(" ") + ")";
  }

  function authorLabel(node) {
    /* 标签只写首作者的前三个字符（小写），例如 kim / ljr */
    var a = node.authors && node.authors[0];
    var name = (typeof a === "string" ? a.trim() : (a && a.family) || "") || "";
    name = String(name).replace(/\s+/g, "");
    return name ? name.slice(0, 3).toLowerCase() : "未知";
  }

  /* 布局：社区（贪心模块度）+ 按关系数的同心环 + 黄金角排布 */
  function layout(graph) {
    var idx = new Map(graph.nodes.map(function (d, r) { return [d.id, r]; }));
    var w = graph.nodes.map(function () { return graph.nodes.map(function () { return 0; }); });
    var degW = graph.nodes.map(function () { return 0; });
    var total = 0;
    graph.edges.forEach(function (d) {
      var r = idx.get(d.source), l = idx.get(d.target);
      if (r === undefined || l === undefined || r === l) return;
      var m = (isFinite(d.weight) ? d.weight : 1) * (d.kind === "semantic_similarity" ? 0.65 : 1);
      w[r][l] += m; w[l][r] += m; degW[r] += m; degW[l] += m; total += m;
    });
    var groups = graph.nodes.map(function (_, r) { return [r]; });
    if (total > 0) {
      while (groups.length > 1) {
        var best = 0, pick = [];
        for (var l = 0; l < groups.length; l++) {
          for (var m = l + 1; m < groups.length; m++) {
            var S = groups[l].reduce(function (P, M) { return P + groups[m].reduce(function (O, j) { return O + w[M][j]; }, 0); }, 0);
            var C = groups[l].reduce(function (P, M) { return P + degW[M]; }, 0);
            var X = groups[m].reduce(function (P, M) { return P + degW[M]; }, 0);
            var gain = S - C * X / (2 * total);
            if (gain > best + 1e-8) { best = gain; pick = [l, m]; }
          }
        }
        if (!pick.length) break;
        groups[pick[0]].push.apply(groups[pick[0]], groups[pick[1]]);
        groups.splice(pick[1], 1);
      }
    }
    var originIdx = idx.get(graph.origin_id);
    groups.sort(function (d, r) {
      return d.includes(originIdx == null ? -1 : originIdx) ? -1
        : r.includes(originIdx == null ? -1 : originIdx) ? 1
        : Math.min.apply(null, d) - Math.min.apply(null, r);
    });
    var peers = graph.nodes.map(function () { return new Set(); });
    graph.edges.forEach(function (d) {
      var r = idx.get(d.source), l = idx.get(d.target);
      if (r === undefined || l === undefined || r === l || r === originIdx || l === originIdx) return;
      peers[r].add(l); peers[l].add(r);
    });
    var degrees = Array.from(new Set(graph.nodes
      .filter(function (d) { return d.id !== graph.origin_id; })
      .map(function (d) { return peers[idx.get(d.id)].size; }))).sort(function (d, r) { return r - d; });
    var ringCount = Math.min(4, degrees.length);
    var radii = ringCount === 1 ? [150] : Array.from({ length: ringCount }, function (_, r) { return 65 + r * 135 / (ringCount - 1); });
    var nodes = graph.nodes.map(function (d, r) {
      var community = groups.findIndex(function (C) { return C.includes(r); });
      var peerDegree = peers[r].size;
      var ring = d.id === graph.origin_id ? -1
        : Math.min(ringCount - 1, Math.floor(degrees.indexOf(peerDegree) * ringCount / degrees.length));
      return Object.assign({}, d, {
        community: community, peerDegree: peerDegree, ring: ring,
        angle: community * 2.39996 + r * 0.3, x: 400, y: 265,
      });
    });
    var rings = radii.map(function (d, r) {
      return nodes.filter(function (l) { return l.ring === r; })
        .sort(function (l, m) { return l.community - m.community || l.id.localeCompare(m.id); });
    });
    for (var pass = 0; pass < 6; pass++) {
      for (var ri = 0; ri < rings.length; ri++) {
        var ringNodes = rings[ri];
        if (pass) {
          ringNodes.sort(function (m, S) {
            var angleFor = function (X) {
              var $ = idx.get(X.id);
              var others = nodes.filter(function (j) { return j.id !== graph.origin_id && j.id !== X.id; })
                .map(function (j) { return { angle: j.angle, w: w[$][idx.get(j.id)] }; });
              var vx = others.reduce(function (j, U) { return j + Math.cos(U.angle) * U.w; }, 0);
              var vy = others.reduce(function (j, U) { return j + Math.sin(U.angle) * U.w; }, 0);
              return vx || vy ? Math.atan2(vy, vx) : X.angle;
            };
            return angleFor(m) - angleFor(S) || m.id.localeCompare(S.id);
          });
        }
        ringNodes.forEach(function (m, S) {
          m.angle = -Math.PI / 2 + 2 * Math.PI * (S + 0.23 * ri) / ringNodes.length;
          m.x = 400 + Math.cos(m.angle) * radii[ri];
          m.y = 265 + Math.sin(m.angle) * radii[ri];
        });
      }
    }
    return nodes;
  }

  function hash01(s) {
    var i = Math.imul(s + 101, 2246822507);
    i = Math.imul(i ^ i >>> 13, 3266489909);
    return ((i ^ i >>> 16) >>> 0) / 4294967296;
  }

  var REL_STYLES = {
    citation: { color: "#8eefff", dash: undefined, arrow: true, label: "来源支持的直接引用（箭头指向被引用论文）" },
    bibliographic_coupling: { color: "#c4adff", dash: "7 4", arrow: false, label: "共享参考文献" },
    co_citation: { color: "#6ee7b7", dash: "2 4", arrow: false, label: "共同被其他论文引用" },
    semantic_similarity: { color: "#f69fcc", dash: "9 3 2 3", arrow: false, label: "语义相似（推断，非引用）" },
  };
  /* 边的颜色走 CSS 变量：深色主题用亮色，浅色主题压深（见 starmap.css） */
  var EDGE_VAR = {
    citation: "--rel-citation", bibliographic_coupling: "--rel-biblio",
    co_citation: "--rel-co", semantic_similarity: "--rel-semantic",
  };
  function relStyle(kind) {
    return REL_STYLES[kind] || { color: "#cbd5e1", dash: "3 3", arrow: false, label: String(kind || "relationship") };
  }
  function relGeometry(edge, edges, a, b, ra, rb) {
    var peers = edges.filter(function (other) {
      return (other.source === edge.source && other.target === edge.target)
        || (other.source === edge.target && other.target === edge.source);
    });
    var lane = (peers.indexOf(edge) - (peers.length - 1) / 2) * 5;
    var dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
    var ux = dx / length, uy = dy / length;
    var sign = String(edge.source) < String(edge.target) ? 1 : -1;
    var ox = -uy * sign * lane, oy = ux * sign * lane;
    var start = Math.min(Math.max(12, ra + 4), length / 3);
    var end = Math.min(Math.max(12, rb + 4), length / 3);
    return { x1: a.x + ox + ux * start, y1: a.y + oy + uy * start, x2: b.x + ox - ux * end, y2: b.y + oy - uy * end, ux: ux, uy: uy };
  }
  function arrowPoints(geo) {
    var s = 6.5, w = 3.0;
    var tx = geo.x2, ty = geo.y2;
    var bx = tx - geo.ux * s, by = ty - geo.uy * s;
    return [tx.toFixed(1) + "," + ty.toFixed(1),
      (bx - geo.uy * w).toFixed(1) + "," + (by + geo.ux * w).toFixed(1),
      (bx + geo.uy * w).toFixed(1) + "," + (by - geo.ux * w).toFixed(1)].join(" ");
  }
  function strokeVar(item) {
    var v = EDGE_VAR[item.kind];
    return v ? "var(" + v + ", " + item.color + ")" : item.color;
  }
  function relLegend(edges) {
    var kinds = new Set(edges.filter(function (e) { return e.kind !== "bibliographic_coupling"; }).map(function (e) { return e.kind; }));
    var items = [];
    if (kinds.has("citation")) {
      items.push({ color: "#8eefff", kind: "citation", arrow: true, label: "直接引用（箭头指向被引用论文）" });
    }
    kinds.forEach(function (kind) { if (kind !== "citation") items.push(Object.assign({ kind: kind }, relStyle(kind))); });
    if (edges.some(function (e) { return e.kind === "bibliographic_coupling"; })) {
      items.push(Object.assign({ kind: "bibliographic_coupling" }, relStyle("bibliographic_coupling")));
    }
    return '<div class="paper-relation-legend" aria-label="关系类型与线型说明">' + items.map(function (item) {
      return '<span><svg width="28" height="10" viewBox="0 0 28 10" aria-hidden="true">'
        + '<line x1="1" y1="5" x2="24" y2="5" style="stroke:' + strokeVar(item) + '" stroke-width="1.3"'
        + (item.dash ? ' stroke-dasharray="' + item.dash + '"' : "") + "/>"
        + (item.arrow ? '<path d="M20 2 L25 5 L20 8" fill="none" style="stroke:' + strokeVar(item) + '" stroke-width="1.2"/>' : "")
        + "</svg>" + item.label + "</span>";
    }).join("") + "</div>";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function mount(el, raw) {
    el.innerHTML = "";
    var nodes0 = (raw.nodes || []).map(function (n) {
      return Object.assign({}, n, {
        year: n.year === "" || n.year == null ? null : Number(n.year),
        citation_count: Number(n.degree || 0),
        authors: n.authors ? String(n.authors).split("、") : [],
      });
    });
    var edges = (raw.edges || []).map(function (e) {
      var KIND_MAP = { cite: "citation", shared: "bibliographic_coupling" };
      return { source: e.source, target: e.target, kind: KIND_MAP[e.kind] || e.kind, weight: e.weight || 1 };
    });
    var graph = { nodes: nodes0, edges: edges, origin_id: null };

    var state = {
      zoom: 1, pan: { x: 0, y: 0 }, spinning: false, dragging: false,
      selected: "", offsets: {}, reduced: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      dim: document.hidden,
    };
    var drag = { x: 0, y: 0, startX: 0, startY: 0, moved: false, pointer: -1, nodeId: "" };

    var wrap = document.createElement("div");
    wrap.className = "paper-network";
    wrap.innerHTML = '<div class="paper-network-heading" aria-hidden="true"><span class="paper-network-beacon"></span>'
      + "<div><strong>论文星图</strong><small>" + nodes0.length + " 篇论文 · " + edges.length + " 条关系线</small></div></div>"
      + '<span class="paper-network-hint">拖动空白处平移 · 拖动节点调整 · 点击节点高亮 · 双击打开 PDF</span>'
      + relLegend(edges);
    el.appendChild(wrap);

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 800 520");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "二维论文关系网络");
    svg.style.width = "100%";
    svg.style.height = "100%";
    wrap.appendChild(svg);

    var base = layout(graph);
    var maxCitations = Math.max(0, nodes0.reduce(function (m, d) { return Math.max(m, d.citation_count || 0); }, 0));
    var years = nodes0.map(function (d) { return d.year; }).filter(function (y) { return typeof y === "number" && isFinite(y) && y > 0; });
    var yearMin = years.length ? Math.min.apply(null, years) : 0;
    var yearMax = years.length ? Math.max.apply(null, years) : 0;

    function placeLabels(nodes) {
      var placed = [], map = new Map();
      var order = nodes.slice().sort(function (a, b) {
        return a.id === graph.origin_id ? -1 : b.id === graph.origin_id ? 1 : a.y - b.y;
      });
      order.forEach(function (t) {
        var text = authorLabel(t) + ", " + (t.year == null ? "年份未知" : t.year);
        var shown = text.length > 19 ? text.slice(0, 18) + "…" : text;
        var wpx = Math.min(132, Math.max(54, shown.length * 5.8 + 12));
        var hgt = 18;
        var r = Math.max(18, radiusOf(t.citation_count, maxCitations) * 2.1);
        var cands = [
          { x: t.x - wpx / 2, y: t.y - r - 23 },
          { x: t.x - wpx / 2, y: t.y + r + 8 },
          { x: t.x + r + 11, y: t.y - hgt / 2 },
          { x: t.x - r - wpx - 11, y: t.y - hgt / 2 },
        ];
        var clamp = function (v, lo, hi) { return Math.max(lo, Math.min(hi, v)); };
        var overlap = function (a, b) {
          return Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x))
            * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
        };
        var nodeBoxes = [];
        var best = cands.map(function (c, i) {
          var box = { x: clamp(c.x, 5, 795 - wpx), y: clamp(c.y, 5, 495 - hgt), w: wpx, h: hgt };
          var score = i * 3 + placed.reduce(function (acc, other) { return acc + overlap(box, other) * 2; }, 0);
          if (!nodeBoxes.length) {
            nodes.forEach(function (o) {
              if (o.id === t.id) return;
              var rr = radiusOf(o.citation_count, maxCitations);
              nodeBoxes.push({ x: o.x - rr - 3, y: o.y - rr - 3, w: 2 * rr + 6, h: 2 * rr + 6 });
            });
          }
          score += nodeBoxes.reduce(function (acc, ob) { return acc + overlap(box, ob) * 0.7; }, 0);
          return { box: box, score: score };
        }).sort(function (a, b) { return a.score - b.score; })[0].box;
        map.set(t.id, Object.assign({}, best, { text: shown }));
        placed.push(best);
      });
      return map;
    }

    var labelBoxes = placeLabels(base);
    var topG = null;
    var nodeEls = new Map();
    var edgeEls = [];
    var posOf = function (id) {
      var d = base.find(function (x) { return x.id === id; });
      var off = state.offsets[id] || {};
      return { x: (d ? d.x : 0) + (off.x || 0), y: (d ? d.y : 0) + (off.y || 0) };
    };

    function build() {
      var sprites = base.map(function (d, i) {
        return makeSprite(yearColor(norm01(d.year, yearMin, yearMax)), i % 4, d.citation_count === null);
      });
      var defs = base.map(function (d, i) {
        var c = yearColor(norm01(d.year, yearMin, yearMax));
        return '<radialGradient id="paper-star-' + i + '">'
          + '<stop offset="0%" stop-color="' + c + '" stop-opacity=".82"/>'
          + '<stop offset="24%" stop-color="' + c + '" stop-opacity=".55"/>'
          + '<stop offset="58%" stop-color="' + c + '" stop-opacity=".18"/>'
          + '<stop offset="100%" stop-color="' + c + '" stop-opacity="0"/>'
          + "</radialGradient>";
      }).join("");

      var ringRadii = Array.from(new Set(base.filter(function (d) { return d.ring >= 0; })
        .map(function (d) { return Math.round(Math.hypot(d.x - 400, d.y - 265)); })));
      var ringsMarkup = ringRadii.map(function (r) {
        return '<circle class="paper-topology-ring" cx="400" cy="265" r="' + r + '"/>';
      }).join("");

      var arrowMarkup = "";
      var edgeMarkup = edges.map(function (e, i) {
        var a = base.find(function (x) { return x.id === e.source });
        var b = base.find(function (x) { return x.id === e.target });
        if (!a || !b) return "";
        var style = relStyle(e.kind);
        var width = Math.min(1.35, 0.75 + (isFinite(e.weight) ? e.weight : 1) * 0.2);
        var geo = relGeometry(e, edges, a, b, spriteRadius(a, maxCitations) - 4, spriteRadius(b, maxCitations) - 4);
        if (style.arrow) {
          arrowMarkup += '<polygon class="paper-edge-arrow" data-e="' + i + '" data-kind="' + esc(e.kind)
            + '" opacity=".9" points="' + arrowPoints(geo) + '"/>';
        }
        return '<g class="paper-edge" data-e="' + i + '" data-source="' + esc(e.source)
          + '" data-target="' + esc(e.target)
          + '" data-kind="' + esc(e.kind) + '" pointer-events="none">'
          + '<line class="paper-edge-glow" stroke-width="' + (width + 1) + '"'
          + (style.dash ? ' stroke-dasharray="' + style.dash + '"' : "") + ' opacity=".03"/>'
          + '<line class="paper-edge-line" stroke-width="' + width + '"'
          + (style.dash ? ' stroke-dasharray="' + style.dash + '"' : "") + ' opacity=".7"/>'
          + "<title>" + esc(style.label) + "</title></g>";
      }).join("");

      var nodeMarkup = base.map(function (d, i) {
        var r = radiusOf(d.citation_count, maxCitations);
        var color = yearColor(norm01(d.year, yearMin, yearMax));
        var known = d.citation_count !== null;
        var label = labelBoxes.get(d.id);
        var size = Math.max(36, r * 3.5);
        var rot = i % 4;
        var dust = fringeOf(rot).map(function (p, k) {
          return '<circle class="paper-fringe-particle" style="animation-delay:-' + p.phase + "s;animation-duration:"
            + (4 + hash01(i * 91 + k * 7) * 4) + 's" cx="' + (p.x * size) + '" cy="' + (p.y * size)
            + '" r="' + (p.r * size) + '" fill="' + color + '" opacity="' + p.opacity + '"/>';
        }).join("");
        return '<g class="paper-graph-node' + (known ? " is-known" : " is-unknown")
          + '" role="button" tabindex="0" data-node-id="' + esc(d.id) + '" aria-label="' + esc(d.title) + '">'
          + '<g class="paper-node-body">'
          + '<circle class="paper-node-hit" cx="' + d.x + '" cy="' + d.y + '" r="' + Math.max(22, r * 1.9) + '" fill="transparent"/>'
          + '<circle class="paper-node-core" cx="' + d.x + '" cy="' + d.y + '" r="' + r + '" fill="none"'
          + (known ? ' stroke="none" stroke-width="0"' : ' stroke="' + color + '" stroke-width=".8" stroke-dasharray="1.5 2.5"') + "/>"
          + '<image class="paper-node-nebula" style="animation-duration:' + (90 + i % 5 * 6) + "s;animation-delay:-" + (i * 8.7)
          + 's" href="' + sprites[i] + '" x="' + (d.x - Math.max(18, r * 1.75)) + '" y="' + (d.y - Math.max(18, r * 1.75))
          + '" width="' + Math.max(36, r * 3.5) + '" height="' + Math.max(36, r * 3.5) + '" opacity="1" pointer-events="none"/>'
          + '<g class="paper-nebula-fringe" aria-hidden="true" style="transform-origin:' + d.x + "px " + d.y
          + "px;animation-duration:" + (108 + i % 4 * 4) + "s;animation-delay:-" + (i * 7.3) + 's" pointer-events="none">' + dust + "</g>"
          + "</g>"
          + '<g class="paper-node-label">'
          + '<line class="paper-label-leader" x1="' + d.x + '" y1="' + d.y + '" x2="' + Math.max(label.x, Math.min(label.x + label.w, d.x))
          + '" y2="' + Math.max(label.y, Math.min(label.y + label.h, d.y)) + '"/>'
          + '<text data-label-state="ordinary" x="' + (label.x + label.w / 2) + '" y="' + (label.y + 13)
          + '" text-anchor="middle">' + esc(label.text) + "</text></g>"
          + "<title>" + esc((d.title || "") + " · " + d.year) + "</title></g>";
      }).join("");

      svg.innerHTML = "<defs>" + defs + "</defs>"
        + '<rect class="paper-backdrop" data-paper-background="pure-black" width="800" height="520" pointer-events="none"/>'
        + '<g class="paper-root" transform="translate(' + (state.pan.x + 400) + " " + (state.pan.y + 240) + ") scale(" + state.zoom + ') translate(-400 -240)">'
        + '<g class="paper-topology-rings" pointer-events="none" aria-hidden="true">' + ringsMarkup + "</g>"
        + edgeMarkup + nodeMarkup
        + '<g class="paper-focus-layer" pointer-events="none"></g>'
        + '<g class="paper-arrow-layer" pointer-events="none">' + arrowMarkup + "</g></g>";

      topG = svg.querySelector(".paper-root");
      nodeEls = new Map();
      svg.querySelectorAll("[data-node-id]").forEach(function (g) { nodeEls.set(g.getAttribute("data-node-id"), g); });
      edgeEls = [];
      svg.querySelectorAll(".paper-edge").forEach(function (g) {
        edgeEls.push({
          el: g, source: g.getAttribute("data-source"), target: g.getAttribute("data-target"),
          glow: g.querySelector(".paper-edge-glow"), line: g.querySelector(".paper-edge-line"),
          arrow: svg.querySelector('.paper-edge-arrow[data-e="' + g.getAttribute("data-e") + '"]'),
          kind: g.getAttribute("data-kind"),
          weight: (edges.find(function (x) { return x.source === g.getAttribute("data-source") && x.target === g.getAttribute("data-target"); }) || {}).weight || 1,
          edge: edges.find(function (x) { return x.source === g.getAttribute("data-source") && x.target === g.getAttribute("data-target"); }),
        });
      });
      edgeEls.forEach(drawEdge);            // 线段的 x1/y1/x2/y2 只在 drawEdge 里算，建图后必须先跑一遍
      wrap.dataset.nodeTextureSource = hasTexture() ? "user-round-nebula-v4-20261006" : "loading";
      wrap.dataset.nodeTextureGenerations = String(textureStats.generations);
      wrap.dataset.nodeTextureMs = String(Math.round(textureStats.durationMs));
      applyMotion();
    }

    function applyMotion() {
      wrap.dataset.nodeMotion = state.spinning || state.reduced || state.dim || state.dragging ? "paused" : "running";
    }
    function applyTransform() {
      if (!topG) return;
      topG.setAttribute("transform", "translate(" + (state.pan.x + 400) + " " + (state.pan.y + 240)
        + ") scale(" + state.zoom + ") translate(-400 -240)");
    }
    function edgeGeometry(rec) {
      var a = posOf(rec.source), b = posOf(rec.target);
      var na = base.find(function (x) { return x.id === rec.source; }) || {};
      var nb = base.find(function (x) { return x.id === rec.target; }) || {};
      return relGeometry(rec.edge, edges, a, b, spriteRadius(na, maxCitations) - 4, spriteRadius(nb, maxCitations) - 4);
    }
    function drawEdge(rec) {
      var geo = edgeGeometry(rec);
      rec.glow.setAttribute("x1", geo.x1); rec.glow.setAttribute("y1", geo.y1);
      rec.glow.setAttribute("x2", geo.x2); rec.glow.setAttribute("y2", geo.y2);
      rec.line.setAttribute("x1", geo.x1); rec.line.setAttribute("y1", geo.y1);
      rec.line.setAttribute("x2", geo.x2); rec.line.setAttribute("y2", geo.y2);
      if (rec.arrow) {
        rec.arrow.setAttribute("points", arrowPoints(geo));
      }
    }
    function setNodeOffset(id, dx, dy) {
      state.offsets[id] = { x: dx, y: dy };
      var g = nodeEls.get(id);
      if (g) g.setAttribute("transform", "translate(" + dx + " " + dy + ")");
      edgeEls.forEach(function (rec) { if (rec.source === id || rec.target === id) drawEdge(rec); });
      if (state.selected) paintFocus(state.selected);
    }
    function select(id) {
      state.selected = id || "";
      var near = {};
      edgeEls.forEach(function (rec) {
        if (rec.source === id) near[rec.target] = 1;
        if (rec.target === id) near[rec.source] = 1;
      });
      nodeEls.forEach(function (g, key) {
        var on = key === state.selected, nb = !!near[key];
        g.classList.toggle("is-selected", on);
        g.classList.toggle("is-neighbor", !on && nb);
        g.classList.toggle("is-dimmed", !!id && !on && !nb);
        var txt = g.querySelector(".paper-node-label text");
        if (txt) txt.setAttribute("data-label-state", on ? "selected" : (nb ? "neighbor" : "ordinary"));
      });
      edgeEls.forEach(function (rec) {
        var hot = !!id && (rec.source === id || rec.target === id);
        rec.el.classList.toggle("is-hot", hot);
        rec.el.classList.toggle("is-muted", !!id && !hot);
        if (rec.arrow) {
          rec.arrow.classList.toggle("is-hot", hot);
          rec.arrow.setAttribute("opacity", hot ? "1" : ".95");
        }
      });
      var layer = svg.querySelector(".paper-arrow-layer");
      if (layer) layer.classList.toggle("is-focus", !!id);
      paintFocus(id);
    }

    var NS = "http://www.w3.org/2000/svg";
    function paintFocus(id) {
      var box = svg.querySelector(".paper-focus-layer");
      if (!box) return;
      box.innerHTML = "";
      if (!id) return;
      edgeEls.forEach(function (rec) {
        if (rec.source !== id && rec.target !== id) return;
        var geo = edgeGeometry(rec);
        var ln = document.createElementNS(NS, "line");
        ln.setAttribute("x1", geo.x1); ln.setAttribute("y1", geo.y1);
        ln.setAttribute("x2", geo.x2); ln.setAttribute("y2", geo.y2);
        ln.setAttribute("class", "paper-focus-line k-" + rec.kind);
        box.appendChild(ln);
      });
    }

    /* 平移 / 拖节点：只写属性，不重建 DOM */
    function svgPoint(ev) {
      var ctm = svg.getScreenCTM();
      if (!ctm) return { x: 0, y: 0 };
      var p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse());
      return { x: p.x, y: p.y };
    }

    var raf = 0, pending = null;
    function schedule(fn) {
      pending = fn;
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = 0; if (pending) pending(); pending = null; });
    }

    build();
    loadTexture().then(function (ok) { if (ok) build(); });   // 贴图是异步的，到了再画一遍星星

    svg.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      var p = svgPoint(ev);
      var nodeEl = ev.target.closest("[data-node-id]");
      var nodeId = nodeEl ? nodeEl.getAttribute("data-node-id") : "";
      var off = state.offsets[nodeId] || { x: 0, y: 0 };
      drag = {
        x: p.x, y: p.y,
        startX: nodeId ? off.x : state.pan.x,
        startY: nodeId ? off.y : state.pan.y,
        moved: false, pointer: ev.pointerId, nodeId: nodeId,
      };
      svg.setPointerCapture(ev.pointerId);
      state.dragging = true;
      applyMotion();
    });
    svg.addEventListener("pointermove", function (ev) {
      if (drag.pointer !== ev.pointerId) return;
      var p = svgPoint(ev);
      var dx = p.x - drag.x, dy = p.y - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) <= 3) return;
      drag.moved = true;
      if (drag.nodeId) {
        schedule(function () { setNodeOffset(drag.nodeId, drag.startX + dx / state.zoom, drag.startY + dy / state.zoom); });
      } else {
        schedule(function () { state.pan = { x: drag.startX + dx, y: drag.startY + dy }; applyTransform(); });
      }
    });
    function openPaper(id) {
      if (!id || typeof el.__api !== "function") return;
      el.__api("/api/paper/open?id=" + encodeURIComponent(id))
        .then(function () { el.__toast && el.__toast("已用系统默认程序打开这篇 PDF", "success"); })
        .catch(function (err) { el.__toast && el.__toast("打开失败：" + err.message, "error"); });
    }
    var lastClick = { id: "", at: 0 };
    function endDrag(ev) {
      if (drag.pointer !== ev.pointerId) return;
      var id = drag.nodeId;
      if (!drag.moved) {
        if (id) {
          var now = Date.now();
          if (lastClick.id === id && now - lastClick.at < 450) {   // 同一颗星 450ms 内点第二下 = 双击开 PDF
            lastClick = { id: "", at: 0 };
            openPaper(id);
          } else {
            lastClick = { id: id, at: now };
            select(id);
          }
        } else select("");                                        // 点空白 = 取消高亮
      }
      drag.pointer = -1;
      drag.nodeId = "";
      state.dragging = false;
      applyMotion();
      if (svg.hasPointerCapture && svg.hasPointerCapture(ev.pointerId)) svg.releasePointerCapture(ev.pointerId);
    }
    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", function () {
      drag.pointer = -1; drag.moved = true; state.dragging = false; applyMotion();
    });
    svg.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      var nodeEl = ev.target.closest("[data-node-id]");
      if (!nodeEl) return;
      ev.preventDefault();
      select(nodeEl.getAttribute("data-node-id"));
    });
    document.addEventListener("visibilitychange", function () { state.dim = document.hidden; applyMotion(); });

    /* 年份图例 */
    var legend = document.createElement("div");
    legend.className = "paper-year-legend";
    var grad = yearMax === yearMin ? yearColor(years.length ? 0.5 : null)
      : "linear-gradient(to right, " + yearColor(0) + " 0%, " + yearColor(0.32) + " 32%, "
      + yearColor(0.68) + " 68%, " + yearColor(1) + " 100%)";
    legend.innerHTML = "<span>发表时间 · 越新越亮</span>"
      + '<div class="paper-year-gradient" style="background:' + grad + '"></div>'
      + '<div class="paper-year-ticks"><span>' + (years.length ? yearMin + " · 较早" : "年份未知") + "</span><span>"
      + (years.length ? yearMax + " · 较新" : "") + "</span></div>"
      + (years.length < nodes0.length ? "<small>部分论文年份未识别，按中性灰显示</small>" : "");
    wrap.appendChild(legend);

    var tools = document.createElement("div");
    tools.className = "paper-graph-zoom";
    tools.innerHTML = '<button type="button" data-zoom="spin" aria-label="暂停/播放节点自转">❚❚</button>'
      + '<button type="button" data-zoom="out" aria-label="缩小关系图">−</button>'
      + '<button type="button" data-zoom="in" aria-label="放大关系图">+</button>'
      + '<button type="button" data-zoom="reset" aria-label="重置关系图视角">⟲</button>';
    wrap.appendChild(tools);
    tools.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-zoom]");
      if (!b) return;
      var act = b.dataset.zoom;
      if (act === "spin") { state.spinning = !state.spinning; b.textContent = state.spinning ? "▶" : "❚❚"; applyMotion(); return; }
      if (act === "out") state.zoom = Math.max(0.55, state.zoom - 0.15);
      if (act === "in") state.zoom = Math.min(1.7, state.zoom + 0.15);
      if (act === "reset") {
        state.zoom = 1; state.pan = { x: 0, y: 0 }; state.offsets = {};
        nodeEls.forEach(function (g) { g.removeAttribute("transform"); });
        edgeEls.forEach(drawEdge);
      }
      applyTransform();
    });

    wrap.dataset.buildMs = String(Math.round(performance.now() - (mount._t0 || performance.now())));
    return { data: graph, select: select, state: state };
  }

  window.StarMap = {
    mount: function (el, graph) {
      mount._t0 = performance.now();
      return mount(el, graph);
    },
  };
})();
