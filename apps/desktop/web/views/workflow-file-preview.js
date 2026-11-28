/* Read-only file previews. Registration and navigation belong to the workspace. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);

  function reference(href) {
    if (typeof href !== 'string') return null;
    let value = href.trim();
    if (value.startsWith('/__workflow-file?')) {
      const query = new URLSearchParams(value.slice(value.indexOf('?') + 1).split('#')[0]);
      if (query.getAll('path').length !== 1) return null;
      value = query.get('path').trim();
    }
    // Check both before and after decoding: encoded protocols must stay external.
    const external = path => /^(?:[a-z][a-z0-9+.-]*:|[\\/]{2})/i.test(path) && !/^[a-z]:[\\/]/i.test(path);
    if (!value || external(value) || value.startsWith('#')) return null;
    value = value.split(/[?#]/)[0];
    try { value = decodeURIComponent(value).trim(); } catch { return null; }
    if (external(value) || /[\u0000-\u001f\u007f]/.test(value) || !/\.(md|markdown|csv)$/i.test(value)) return null;
    return /^[a-z]:[\\/]/i.test(value) ? {path: value} : {relative: value.replace(/\\/g, '/')};
  }

  function relativeLink(file, source, candidate) {
    if (candidate.path || candidate.relative.startsWith('/')) return candidate;
    const base = String(source.base_relative || source.relative || file.relative || file.path || '').replace(/\\/g, '/');
    if (!base.includes('/')) return candidate;
    const joined = base.slice(0, base.lastIndexOf('/') + 1) + candidate.relative;
    const parts = [];
    for (const part of joined.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..' && parts.length && parts.at(-1) !== '..' && !/^[a-z]:$/i.test(parts.at(-1))) parts.pop();
      else parts.push(part);
    }
    const resolved = parts.join('/');
    return /^[a-z]:\//i.test(resolved) ? {path: resolved} : {relative: resolved};
  }

  async function mount(host, {projectId, chatId, file = {}, live = () => true, openFile} = {}) {
    host.__previewDispose?.();
    const selected = {...file};
    let disposed = false;
    let sequence = 0;
    let snapshot = null;
    let controller = null;
    const root = document.createElement('section');
    root.className = 'wfp';
    root.setAttribute('aria-label', '文件只读预览');
    root.innerHTML = `<header class="wfp-heading"><strong data-wfp-name></strong><span class="wfp-badge">只读</span></header>
      <div class="wfp-meta" data-wfp-source></div><p class="wfp-status" data-wfp-status role="status" aria-live="polite"></p>
      <div class="wfp-body" data-wfp-body></div><footer class="wfp-pager" data-wfp-pager hidden>
      <button type="button" data-wfp-prev>上一页</button><span data-wfp-page aria-live="polite"></span>
      <button type="button" data-wfp-next>下一页</button></footer>`;
    host.replaceChildren(root);
    const element = name => root.querySelector(`[data-wfp-${name}]`);
    const current = () => !disposed && host.isConnected && root.parentNode === host && live();
    const name = selected.name || selected.relative || selected.path || '文件预览';
    element('name').textContent = name;
    element('name').title = name;
    const request = new URLSearchParams({project_id: projectId || '', chat_id: chatId || ''});
    for (const key of ['relative', 'path', 'task_id', 'task_run_id', 'output_revision', 'artifact_id']) {
      if (selected[key] !== undefined && selected[key] !== null && selected[key] !== '') request.set(key, String(selected[key]));
    }
    if (!request.has('relative') && !request.has('path') && selected.rel) request.set('relative', selected.rel);

    function fail(error) {
      element('status').textContent = '预览失败：' + (error?.message || String(error));
      element('status').classList.add('wfp-error');
      element('status').setAttribute('role', 'alert');
    }

    function render(data) {
      snapshot = data;
      const source = data.source || {};
      element('name').textContent = data.name || name;
      element('name').title = data.name || name;
      root.setAttribute('data-wfp-meta', JSON.stringify({
        project_id: source.project_id || projectId || '',
        relative: source.relative || selected.relative || selected.rel || '',
        path: source.path || selected.path || '',
        name: data.name || name,
        task_id: source.task_id || selected.task_id || '',
        read_only: source.kind === 'node'
      }));
      element('source').textContent = source.kind === 'node'
        ? `节点产物 · ${source.task_id} · 运行 ${source.task_run_id} · 修订 v${source.output_revision} · ${source.artifact_id}`
        : `项目文件 · ${source.relative || selected.relative || selected.path || ''} · 当前文件`;
      element('source').title = element('source').textContent;
      element('status').textContent = '';
      element('pager').hidden = data.kind !== 'table';
      if (data.kind === 'markdown') {
        const renderer = typeof renderMarkdown === 'function' ? renderMarkdown : typeof md === 'function' ? md : null;
        const content = renderer ? renderer(data.text || '') : `<pre>${escape(data.text)}</pre>`;
        element('body').innerHTML = `<div class="wfp-markdown md md-render">${content}</div>`;
      } else if (data.kind === 'table') {
        const columns = data.columns || [];
        const rows = data.rows || [];
        element('body').innerHTML = `<div class="wfp-table-scroll" tabindex="0" role="region" aria-label="CSV 表格，可横向滚动">
          <table><caption class="wfp-sr-only">${escape(data.name)}，共 ${escape(data.total_rows)} 行</caption><thead><tr>${columns.map(column => `<th scope="col">${escape(column)}</th>`).join('')}</tr></thead>
          <tbody>${rows.map(row => `<tr>${columns.map((_, index) => {
            const cellVal = escape(row[index]);
            return `<td title="${cellVal}"><div class="wfp-cell-truncate">${cellVal}</div></td>`;
          }).join('')}</tr>`).join('')}</tbody></table>
          ${rows.length ? '' : '<p class="wfp-empty">暂无数据行</p>'}</div>`;
        element('page').textContent = data.total_rows
          ? `${data.offset + 1}–${data.offset + rows.length} / ${data.total_rows} 行`
          : '0 / 0 行';
        element('prev').disabled = data.offset === 0;
        element('next').disabled = data.offset + rows.length >= data.total_rows;
      } else if (data.kind === 'text') {
        element('body').innerHTML = `<pre class="wfp-text">${escape(data.text || '')}</pre>`;
      } else if (['pdf', 'image', 'html'].includes(data.kind) || ['pdf', 'image', 'html'].includes(data.preview?.kind)) {
        const media = data.preview || data;
        const kind = media.kind || data.kind;
        const raw = media.url || media.preview_url || data.url;
        if (raw) {
          element('body').innerHTML = kind === 'image'
            ? `<img class="wfp-image" src="${escape(raw)}" alt="${escape(data.name || name)}">`
            : `<iframe class="wfp-frame" title="${escape(data.name || name)}" ${kind === 'html' ? 'sandbox="allow-same-origin"' : ''} src="${escape(raw)}"></iframe>`;
        } else if (data.download_url) {
          element('body').innerHTML = `<div class="wfp-empty"><a class="btn" href="${escape(data.download_url)}" download>下载 ${escape(data.name || name)}</a></div>`;
        } else {
          throw new Error('缺少预览地址');
        }
      } else if (data.download_url) {
        element('body').innerHTML = `<div class="wfp-empty"><p>${escape(data.preview_note || '此格式暂不支持直接预览，可下载后查看。')}</p><a class="btn" href="${escape(data.download_url)}" download>下载 ${escape(data.name || name)}</a></div>`;
      } else {
        throw new Error('服务器返回了不支持的预览类型');
      }
    }

    async function load(offset = 0) {
      if (!current()) return;
      const ticket = ++sequence;
      controller?.abort();
      controller = new AbortController();
      element('status').classList.remove('wfp-error');
      element('status').setAttribute('role', 'status');
      element('status').textContent = '正在读取文件…';
      root.setAttribute('aria-busy', 'true');
      element('prev').disabled = element('next').disabled = true;
      request.set('offset', String(offset));
      request.set('limit', '100');
      try {
        const response = await api('/api/project-tasks/file-preview?' + request.toString(), {method: 'GET', signal: controller.signal});
        if (!current() || ticket !== sequence) return;
        if (!response?.preview) throw new Error('预览响应缺少文件内容');
        render(response.preview);
      } catch (error) {
        if (!current() || ticket !== sequence || error.name === 'AbortError') return;
        fail(error);
      } finally {
        if (current() && ticket === sequence) root.setAttribute('aria-busy', 'false');
      }
    }

    async function click(event) {
      const anchor = event.target.closest?.('a[href]');
      if (!anchor || !root.contains(anchor) || !current()) return;
      const candidate = reference(anchor.getAttribute('href'));
      if (!candidate) return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof openFile !== 'function') return fail(new Error('未配置项目文件打开入口'));
      try {
        await openFile(relativeLink(selected, snapshot?.source || {}, candidate));
      } catch (error) {
        if (current()) fail(error);
      }
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      ++sequence;
      controller?.abort();
      root.removeEventListener('click', click);
      element('prev').onclick = element('next').onclick = null;
      if (host.__previewDispose === dispose) delete host.__previewDispose;
    }

    host.__previewDispose = dispose;
    root.addEventListener('click', click);
    element('prev').onclick = () => snapshot && load(Math.max(0, snapshot.offset - snapshot.limit));
    element('next').onclick = () => snapshot && load(snapshot.offset + snapshot.limit);
    await load();
    return {destroy: dispose, reload: () => load(snapshot?.offset || 0)};
  }

  window.WorkflowFilePreview = {mount, reference};
})();
