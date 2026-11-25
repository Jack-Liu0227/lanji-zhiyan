/**
 * Universal Draggable Split Resizer Engine (Lanji Zhiyan)
 * Enables smooth, persistent, accessible width adjustments for all split / multi-column layouts across the workbench.
 */
const SplitResizer = (() => {
  const STORAGE_PREFIX = 'rw-split:';
  // Map<HTMLElement, Map<string, ResizerInstance>>
  const containerMap = new WeakMap();

  function getSaved(key, fallback, isRatio) {
    if (!key) return fallback;
    try {
      const val = localStorage.getItem(STORAGE_PREFIX + key);
      if (val !== null) {
        const num = isRatio ? parseFloat(val) : parseInt(val, 10);
        if (!Number.isNaN(num)) return num;
      }
    } catch (_) {}
    return fallback;
  }

  function saveValue(key, val) {
    if (!key) return;
    try {
      localStorage.setItem(STORAGE_PREFIX + key, String(val));
    } catch (_) {}
  }

  function clamp(val, min, max) {
    return Math.min(Math.max(val, min), max);
  }

  /**
   * Bind a resizer to a container
   * @param {HTMLElement} container - The layout container element
   * @param {Object} options
   */
  function bind(container, options = {}) {
    if (!container || !(container instanceof HTMLElement)) return null;

    const {
      direction = 'horizontal', // horizontal = drag width, vertical = drag height
      mode = 'fixed-left',      // 'fixed-left' | 'fixed-right' | 'ratio'
      storageKey = '',
      defaultSize = mode === 'ratio' ? 0.5 : 240,
      min = mode === 'ratio' ? 0.2 : 160,
      max = mode === 'ratio' ? 0.8 : 500,
      cssVar = '--split-w',
      gutter: explicitGutter = null,
      gutterSelector = '',
      gutterClass = 'split-gutter',
      gutterSide = mode === 'fixed-right' ? 'right' : 'left',
      positioning = 'absolute', // 'absolute' | 'flow'
      onResize = null,
      onReset = null
    } = options;

    const isRatio = mode === 'ratio';
    let currentSize = clamp(getSaved(storageKey, defaultSize, isRatio), min, max);

    // Look for existing gutter
    let gutter = explicitGutter || (gutterSelector ? container.querySelector(gutterSelector) : null);
    let autoCreated = false;

    if (!gutter) {
      const existing = container.querySelector(`:scope > .${gutterClass}[data-split-var="${cssVar}"]`);
      if (existing) {
        gutter = existing;
      } else {
        gutter = document.createElement('div');
        gutter.className = gutterClass;
        gutter.dataset.splitVar = cssVar;
        gutter.dataset.splitMode = mode;
        gutter.dataset.splitSide = gutterSide;
        if (positioning === 'absolute') {
          gutter.classList.add('split-gutter-absolute');
        }
        gutter.setAttribute('role', 'separator');
        gutter.setAttribute('aria-orientation', direction === 'horizontal' ? 'vertical' : 'horizontal');
        gutter.setAttribute('tabindex', '0');
        gutter.setAttribute('title', '拖拽调整宽度，双击恢复默认');
        container.appendChild(gutter);
        autoCreated = true;
      }
    } else {
      gutter.classList.add(gutterClass);
      gutter.dataset.splitVar = cssVar;
      gutter.dataset.splitMode = mode;
      gutter.dataset.splitSide = gutterSide;
      gutter.setAttribute('role', 'separator');
      gutter.setAttribute('aria-orientation', direction === 'horizontal' ? 'vertical' : 'horizontal');
      gutter.setAttribute('tabindex', '0');
      gutter.setAttribute('title', '拖拽调整宽度，双击恢复默认');
    }

    // Apply initial CSS property to container and gutter
    applyStyle(currentSize);
    updateAria(currentSize);

    function applyStyle(val) {
      const formatVal = isRatio ? `${(val * 100).toFixed(2)}%` : `${Math.round(val)}px`;
      container.style.setProperty(cssVar, formatVal);
      if (gutter && positioning === 'absolute') {
        if (gutterSide === 'right') {
          gutter.style.right = formatVal;
          gutter.style.left = '';
        } else {
          gutter.style.left = formatVal;
          gutter.style.right = '';
        }
      }
    }

    function updateAria(val) {
      if (!gutter) return;
      gutter.setAttribute('aria-valuenow', String(isRatio ? Math.round(val * 100) : Math.round(val)));
      gutter.setAttribute('aria-valuemin', String(isRatio ? Math.round(min * 100) : Math.round(min)));
      gutter.setAttribute('aria-valuemax', String(isRatio ? Math.round(max * 100) : Math.round(max)));
    }

    let isDragging = false;
    let startCoord = 0;
    let startVal = currentSize;
    let rafId = null;

    function onPointerDown(e) {
      if (e.button !== 0) return;
      isDragging = true;
      startCoord = direction === 'horizontal' ? e.clientX : e.clientY;
      startVal = currentSize;

      try {
        if (gutter.setPointerCapture) gutter.setPointerCapture(e.pointerId);
      } catch (_) {}

      document.body.classList.add('is-resizing-split');
      gutter.classList.add('active');
      e.preventDefault();
      e.stopPropagation();

      // Window-level listeners ensure drag is NEVER lost regardless of cursor velocity or iframes
      window.addEventListener('pointermove', onPointerMove, { passive: false });
      window.addEventListener('pointerup', onPointerUp);
      window.addEventListener('pointercancel', onPointerUp);
    }

    function onPointerMove(e) {
      if (!isDragging) return;
      e.preventDefault();

      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const clientCoord = direction === 'horizontal' ? e.clientX : e.clientY;
        const delta = clientCoord - startCoord;
        let nextVal = startVal;

        if (mode === 'fixed-left') {
          nextVal = startVal + delta;
        } else if (mode === 'fixed-right') {
          nextVal = startVal - delta;
        } else if (mode === 'ratio') {
          const rect = container.getBoundingClientRect ? container.getBoundingClientRect() : { left: 0, top: 0, width: 800, height: 600 };
          const total = direction === 'horizontal' ? rect.width : rect.height;
          if (total > 0) {
            const pos = direction === 'horizontal' ? (e.clientX - rect.left) : (e.clientY - rect.top);
            nextVal = pos / total;
          }
        }

        nextVal = clamp(nextVal, min, max);
        currentSize = nextVal;
        applyStyle(currentSize);
        updateAria(currentSize);

        if (typeof onResize === 'function') {
          onResize(currentSize);
        }
      });
    }

    function onPointerUp(e) {
      if (!isDragging) return;
      isDragging = false;
      if (rafId) cancelAnimationFrame(rafId);

      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerUp);

      document.body.classList.remove('is-resizing-split');
      gutter.classList.remove('active');
      saveValue(storageKey, currentSize);

      try {
        if (gutter.hasPointerCapture && gutter.hasPointerCapture(e.pointerId)) {
          gutter.releasePointerCapture(e.pointerId);
        }
      } catch (_) {}
    }

    function onDblClick(e) {
      e.preventDefault();
      e.stopPropagation();
      currentSize = defaultSize;
      applyStyle(currentSize);
      updateAria(currentSize);
      saveValue(storageKey, currentSize);
      if (typeof onReset === 'function') onReset(currentSize);
      if (typeof onResize === 'function') onResize(currentSize);
    }

    function onKeyDown(e) {
      const step = isRatio ? 0.02 : 12;
      let nextVal = currentSize;

      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        nextVal = mode === 'fixed-right' ? currentSize + step : currentSize - step;
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        nextVal = mode === 'fixed-right' ? currentSize - step : currentSize + step;
      } else if (e.key === 'Home') {
        nextVal = min;
      } else if (e.key === 'End') {
        nextVal = max;
      } else if (e.key === 'Enter') {
        nextVal = defaultSize;
      } else {
        return;
      }

      e.preventDefault();
      currentSize = clamp(nextVal, min, max);
      applyStyle(currentSize);
      updateAria(currentSize);
      saveValue(storageKey, currentSize);
      if (typeof onResize === 'function') onResize(currentSize);
    }

    gutter.addEventListener('pointerdown', onPointerDown);
    gutter.addEventListener('dblclick', onDblClick);
    gutter.addEventListener('keydown', onKeyDown);

    const instance = {
      container,
      gutter,
      cssVar,
      mode,
      getSize: () => currentSize,
      setSize: (val) => {
        currentSize = clamp(val, min, max);
        applyStyle(currentSize);
        updateAria(currentSize);
        saveValue(storageKey, currentSize);
      },
      reset: () => {
        currentSize = defaultSize;
        applyStyle(currentSize);
        updateAria(currentSize);
        saveValue(storageKey, currentSize);
      },
      destroy: () => {
        gutter.removeEventListener('pointerdown', onPointerDown);
        gutter.removeEventListener('dblclick', onDblClick);
        gutter.removeEventListener('keydown', onKeyDown);
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', onPointerUp);
        window.removeEventListener('pointercancel', onPointerUp);
        if (autoCreated && gutter.parentElement) gutter.remove();

        const map = containerMap.get(container);
        if (map) {
          map.delete(cssVar);
          if (map.size === 0) containerMap.delete(container);
        }
      }
    };

    let map = containerMap.get(container);
    if (!map) {
      map = new Map();
      containerMap.set(container, map);
    }
    map.set(cssVar, instance);

    return instance;
  }

  function get(container, cssVar = null) {
    const map = containerMap.get(container);
    if (!map) return null;
    if (cssVar) return map.get(cssVar) || null;
    // Return first registered instance if cssVar not specified
    return map.values().next().value || null;
  }

  const AUTO_RULES = [
    // 1. Global Shell Sidebar
    { selector: '.app-shell', mode: 'fixed-left', storageKey: 'shell-sidebar', defaultSize: 232, min: 160, max: 420, cssVar: '--shell-sidebar-w', positioning: 'absolute' },

    // 2. Project Workspace
    { selector: '.pw-layout', mode: 'fixed-left', storageKey: 'pw-sidebar', defaultSize: 224, min: 180, max: 420, cssVar: '--pw-sidebar-w', positioning: 'absolute' },
    { selector: '.pw-main', mode: 'fixed-right', storageKey: 'pw-dock', defaultSize: 420, min: 300, max: 800, cssVar: '--pw-dock-w', positioning: 'absolute' },
    { selector: '.pw-library-grid', mode: 'fixed-left', storageKey: 'pw-library-tree', defaultSize: 280, min: 200, max: 480, cssVar: '--pw-library-tree-w', positioning: 'absolute' },

    // 3. Notebook and Hierarchy
    { selector: '.nb-layout', mode: 'fixed-left', storageKey: 'nb-sidebar', defaultSize: 260, min: 180, max: 480, cssVar: '--nb-sidebar-w', positioning: 'absolute' },

    // 4. Project Files
    { selector: '.fl-grid', mode: 'fixed-left', storageKey: 'fl-grid', defaultSize: 220, min: 160, max: 400, cssVar: '--fl-sidebar-w', positioning: 'absolute' },
    { selector: '.fl-editor-split', mode: 'ratio', storageKey: 'fl-editor', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--fl-split-ratio', gutterSelector: '.fl-split-divider', positioning: 'flow' },

    // 5. Notes and Settings
    { selector: '.md-view', mode: 'fixed-left', storageKey: 'md-sidebar', defaultSize: 238, min: 160, max: 420, cssVar: '--md-sidebar-w', positioning: 'absolute' },
    { selector: '.md-workspace:not(.write):not(.preview)', mode: 'ratio', storageKey: 'md-workspace', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--md-split-ratio', positioning: 'absolute' },
    { selector: '.pn-layout', mode: 'fixed-left', storageKey: 'pn-layout', defaultSize: 240, min: 180, max: 420, cssVar: '--pn-sidebar-w', positioning: 'absolute' },
    { selector: '.set-wrap', mode: 'fixed-left', storageKey: 'set-tabs', defaultSize: 200, min: 160, max: 360, cssVar: '--set-tabs-w', positioning: 'absolute' },
    { selector: '.sp-notes-layout', mode: 'fixed-left', storageKey: 'sp-notes', defaultSize: 350, min: 240, max: 500, cssVar: '--sp-notes-w', positioning: 'absolute' },

    // 6. Agent and Chat
    { selector: '.agent', mode: 'fixed-left', storageKey: 'agent-history', defaultSize: 236, min: 160, max: 400, cssVar: '--agent-hist-w', positioning: 'absolute' },

    // 7. Workflow Node Workbench
    { selector: '.wnw-grid', mode: 'fixed-left', storageKey: 'wnw-context', defaultSize: 220, min: 170, max: 360, cssVar: '--wnw-context-w', positioning: 'absolute' },
    { selector: '.wnw-grid', mode: 'fixed-right', storageKey: 'wnw-drawer', defaultSize: 300, min: 240, max: 500, cssVar: '--wnw-drawer-w', positioning: 'absolute' },
    { selector: '.wnw-diff', mode: 'ratio', storageKey: 'wnw-diff', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--wnw-diff-ratio', positioning: 'absolute' },
    { selector: '.wnw-bilingual', mode: 'ratio', storageKey: 'wnw-bilingual', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--wnw-bilingual-ratio', positioning: 'absolute' },

    // 8. Paper Toolbox
    { selector: '.paper-toolbox.ptb-page', mode: 'fixed-left', storageKey: 'ptb-page', defaultSize: 184, min: 140, max: 280, cssVar: '--ptb-nav-w', positioning: 'absolute' },
    { selector: '.ptb-editors', mode: 'ratio', storageKey: 'ptb-editors', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--ptb-editor-ratio', positioning: 'absolute' },

    // 9. Prompt Library
    { selector: '.prompt-library .pl-layout', mode: 'fixed-left', storageKey: 'pl-layout', defaultSize: 270, min: 180, max: 420, cssVar: '--pl-list-w', positioning: 'absolute' },

    // 10. Task Center
    { selector: '.ec-live-layout', mode: 'fixed-right', storageKey: 'ec-inspector', defaultSize: 320, min: 260, max: 560, cssVar: '--ec-inspector-w', positioning: 'absolute' },

    // 11. Workflow Panels & Outlines
    { selector: '.ol-grid', mode: 'fixed-right', storageKey: 'ol-grid', defaultSize: 340, min: 260, max: 550, cssVar: '--ol-right-w', positioning: 'absolute' },
    { selector: '.ol-body-wrap.doc-split-wrap', mode: 'ratio', storageKey: 'ol-doc-split', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--ol-doc-split-ratio', positioning: 'flow' },

    // 12. Model Playground
    { selector: '.mp-grid', mode: 'fixed-right', storageKey: 'mp-grid', defaultSize: 320, min: 240, max: 500, cssVar: '--mp-right-w', positioning: 'absolute' },

    // 13. Context Vault
    { selector: '.cv-grid', mode: 'fixed-left', storageKey: 'cv-grid', defaultSize: 214, min: 160, max: 360, cssVar: '--cv-list-w', positioning: 'absolute' },

    // 14. Evidence Navigator
    { selector: '.en-grid', mode: 'ratio', storageKey: 'en-grid', defaultSize: 0.5, min: 0.2, max: 0.8, cssVar: '--en-split-ratio', positioning: 'absolute' },

    // 15. Mermaid Tool
    { selector: '.mermaid-tool .mmt-workspace', mode: 'ratio', storageKey: 'mmt-workspace', defaultSize: 0.45, min: 0.2, max: 0.8, cssVar: '--mmt-code-ratio', positioning: 'absolute' },

    // 16. Team Office & Chat
    { selector: '.tof-body', mode: 'fixed-right', storageKey: 'tof-panel', defaultSize: 356, min: 260, max: 500, cssVar: '--tof-panel-w', positioning: 'absolute' },
    { selector: '.tc-page', mode: 'fixed-left', storageKey: 'tc-side', defaultSize: 230, min: 160, max: 360, cssVar: '--tc-side-w', positioning: 'absolute' },

    // 17. Mail Client
    { selector: '.mail-wrap', mode: 'fixed-left', storageKey: 'mail-side', defaultSize: 218, min: 160, max: 340, cssVar: '--mail-side-w', positioning: 'absolute' },

    // 18. Dialogs
    { selector: '.ag-team', mode: 'fixed-left', storageKey: 'ag-team-nav', defaultSize: 220, min: 160, max: 340, cssVar: '--ag-team-nav-w', positioning: 'absolute' },
    { selector: '.wfl-editor-grid', mode: 'fixed-left', storageKey: 'wfl-editor-list', defaultSize: 185, min: 140, max: 300, cssVar: '--wfl-editor-list-w', positioning: 'absolute' }
  ];

  function scan(root = document) {
    if (!root || !root.querySelectorAll) return;
    for (const rule of AUTO_RULES) {
      let elements = [];
      try {
        if (root.matches && root.matches(rule.selector)) {
          elements.push(root);
        }
      } catch (_) {}
      try {
        const found = root.querySelectorAll(rule.selector);
        for (let i = 0; i < found.length; i++) {
          if (!elements.includes(found[i])) elements.push(found[i]);
        }
      } catch (_) {}

      elements.forEach(el => {
        if (rule.condition && !rule.condition(el)) return;
        // Check if gutter already exists and is attached
        const existingGutter = el.querySelector(`:scope > .split-gutter[data-split-var="${rule.cssVar}"]`);
        if (!existingGutter || !existingGutter.parentElement) {
          bind(el, rule);
        }
      });
    }
  }

  let observer = null;
  function initObserver() {
    if (observer || typeof MutationObserver === 'undefined') return;
    let timer = null;
    observer = new MutationObserver((mutations) => {
      if (timer) return;
      timer = requestAnimationFrame(() => {
        timer = null;
        for (const m of mutations) {
          if (m.addedNodes) {
            for (const node of m.addedNodes) {
              if (node.nodeType === 1) scan(node);
            }
          }
        }
      });
    });

    const attach = () => {
      if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
        scan(document.body);
      }
    };

    if (typeof document !== 'undefined') {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attach);
      } else {
        attach();
      }
    }
  }

  // Auto initialize observer
  initObserver();

  return {
    bind,
    get,
    scan,
    clamp
  };
})();

if (typeof window !== 'undefined') {
  window.SplitResizer = SplitResizer;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = SplitResizer;
}
