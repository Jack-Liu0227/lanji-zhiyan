/* Keep a return path for links between application views. */
const AppNavigation = (() => {
  const trail = [];
  const bar = $('appReturn'), button = $('appReturnButton');
  const scrollTargets = ['#main', '#pwResource', '#pwMessages', '#pwSessions', '#flStage', '.fl-editor-preview',
    '#mdSource', '#mdPreview', '.at-msgs'];
  let busy = false;

  function context() {
    const page = route();
    const workspace = page === 'workspace' ? window.ProjectWorkspace?.navigationContext() : null;
    const settings = page === 'settings' ? setSec : null;
    return {tab:activeTab, page, workspace, settings, project:curTask,
      key:JSON.stringify([activeTab, workspace?.key, settings]),
      label:workspace?.label || (settings === 'rss' ? 'RSS 订阅源设置' : TITLES[page])};
  }

  function capture(trigger) {
    const source = context(), focus = trigger || document.activeElement;
    return {...source, focusSelector:selectorFor(focus), board:TaskCenter.navigationContext(),
      scroll:scrollTargets.flatMap(selector => {
        const element = document.querySelector(selector);
        return element?.getClientRects().length ? [{selector, top:element.scrollTop, left:element.scrollLeft}] : [];
      }),
      expanded:source.page === 'intel' ? new Set([...document.querySelectorAll('#main [data-intel-id] details[open]')]
        .map(details => details.closest('[data-intel-id]').dataset.intelId)) : null};
  }

  function selectorFor(element) {
    if (element?.id) return `#${CSS.escape(element.id)}`;
    if (element?.matches('[data-it-import]')) {
      const item = element.closest('[data-intel-id]');
      if (item) return `[data-intel-id="${CSS.escape(item.dataset.intelId)}"] [data-it-import]`;
    }
    if (element?.matches('[data-cal-open]')) return `[data-cal-open="${CSS.escape(element.dataset.calOpen)}"]`;
    if (element?.matches('a[href]')) return `#main a[href="${CSS.escape(element.getAttribute('href'))}"]`;
    return '';
  }

  function sync() {
    const entry = trail.at(-1);
    bar.hidden = entry?.destination !== activeTab;
    if (!bar.hidden) $('appReturnLabel').textContent = `返回${entry.source.label}`;
  }

  async function follow(open, {trigger, previousHash, destinationHash} = {}) {
    if (busy) return false;
    busy = true;
    try {
      const source = capture(trigger);
      return await uiNavigate(async () => {
        if (context().key !== source.key) return false;
        await open();
        const target = context();
        if (target.key !== source.key) {
          while (trail.length && trail.at(-1).destination !== source.tab) trail.pop();
          if (trail.length === 12) trail.shift();
          trail.push({source, destination:target.tab, previousHash, destinationHash});
          sync();
          if (!document.activeElement?.closest('#main')) button.focus({preventScroll:true});
        }
        return true;
      });
    } catch (error) { uiNotify(error.message); return false; }
    finally { busy = false; }
  }

  async function restore(source) {
    if (source.workspace) await source.workspace.restore();
    else {
      if (source.project && S.tasks.some(project => project.id === source.project)) {
        curTask = source.project;
        localStorage.setItem('rw-task', curTask);
      }
      if (source.settings) setSec = source.settings;
      if (source.page === 'task-center') source.board?.restoreScope();
      await openTab(source.page);
    }
    if (route() !== source.page) return;
    await source.board?.restoreFilters();
    if (source.expanded) {
      $('main').querySelectorAll('[data-intel-id]').forEach(item => {
        if (source.expanded.has(item.dataset.intelId)) {
          const details = item.querySelector('details');
          if (details) details.open = true;
        }
      });
    }
    for (const {selector, top, left} of source.scroll) {
      document.querySelector(selector)?.scrollTo({top, left, behavior:'instant'});
    }
    const focus = source.focusSelector && document.querySelector(source.focusSelector);
    (focus?.getClientRects().length ? focus : $('main')).focus({preventScroll:true});
  }

  function replaceHash(hash) {
    history.replaceState(history.state, '', location.pathname + location.search + hash);
  }

  async function back() {
    const entry = trail.at(-1);
    if (busy || entry?.destination !== activeTab) return;
    busy = true;
    try {
      await uiNavigate(async () => {
        if (activeTab !== entry.destination) return;
        await restore(entry.source);
        trail.pop();
        if (trail.at(-1)?.destination === entry.source.tab) trail.at(-1).destination = activeTab;
        if (entry.destinationHash && location.hash === entry.destinationHash) replaceHash(entry.previousHash);
        sync();
      });
    } catch (error) { uiNotify(error.message); }
    finally { busy = false; }
  }

  function targetFor(url) {
    if (url.origin !== location.origin || !url.hash) return null;
    if (url.pathname !== location.pathname && ![url.pathname, location.pathname].every(path => path === '/' || path === '/index.html')) return null;
    const fragment = url.hash.slice(1);
    const params = new URLSearchParams(fragment), project = params.get('project'), task = params.get('task');
    if (project && task) return () => ProjectWorkspace.openTask(task, project, {peek:false});
    const page = fragment.replace(/^\//, '');
    if (page === 'workbench' || Object.hasOwn(TITLES, page)) return () => openTab(page);
    return null;
  }

  button.onclick = back;
  document.addEventListener('click', event => {
    if (!S.loaded || event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const anchor = event.target.closest?.('a[href]');
    const target = anchor?.getAttribute('target');
    if (!anchor || anchor.hasAttribute('download') || (target && target.toLowerCase() !== '_self')) return;
    const href = anchor.getAttribute('href');
    if (!URL.canParse(href, location.href)) return;
    const open = targetFor(new URL(href, location.href));
    if (!open) return;
    event.preventDefault();
    follow(open, {trigger:anchor});
  });
  window.addEventListener('hashchange', async event => {
    const url = new URL(event.newURL), open = targetFor(url);
    if (!open || !S.loaded) return;
    const previousHash = new URL(event.oldURL).hash;
    if (!await follow(open, {previousHash, destinationHash:url.hash}) && location.hash === url.hash) replaceHash(previousHash);
  });

  async function start() {
    try { await targetFor(new URL(location.href))?.(); }
    catch (error) { uiNotify(error.message); }
  }

  return {follow, sync, start};
})();
