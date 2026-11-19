(() => {
  const pageSize = 20;
  const searchable = ['title', 'authors', 'author', 'abstract', 'doi', 'source', 'journal', 'year'];
  const text = value => esc(String(value ?? ''));

  function paperMarkup(row, index, total, editable) {
    const excluded = row.included === false;
    const stateClass = excluded ? 'is-excluded' : '';
    const title = text(row.title || '未命名文献');
    const meta = [row.authors || row.author, row.year, row.source || row.journal, row.doi ? 'DOI ' + row.doi : '']
      .filter(Boolean).map(value => `<span>${text(value)}</span>`).join('');
    const abstract = row.abstract || row.summary;
    const details = abstract ? `<details class="wnw-paper-abstract"><summary>摘要</summary><p>${text(abstract)}</p></details>` : '';
    const controls = editable ? `<div class="wnw-paper-edit">
      <label><input type="checkbox" data-wnw-cell="${index}" data-field="included" ${excluded ? '' : 'checked'}> 纳入本阶段交付</label>
      <label>筛选理由<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="reason">${text(row.reason)}</textarea></label>
      <label>人工批注<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="annotation">${text(row.annotation)}</textarea></label>
      </div>` : '';
    const order = editable ? `<div class="wnw-paper-order">
      <button type="button" class="pw-text-button" data-wnw-row-move="${index}:-1" aria-label="上移第 ${index + 1} 条文献" ${index === 0 ? 'disabled' : ''}>上移</button>
      <button type="button" class="pw-text-button" data-wnw-row-move="${index}:1" aria-label="下移第 ${index + 1} 条文献" ${index === total - 1 ? 'disabled' : ''}>下移</button>
      </div>` : '';
    return `<article class="wnw-search-paper ${stateClass}">
      <header><span class="wnw-paper-number">${index + 1}</span><h3>${title}</h3>${order}</header>
      <div class="wnw-paper-meta">${meta}<span class="wnw-paper-state ${stateClass}">${excluded ? '已排除' : '已纳入'}</span></div>${details}
      ${row.reason && !editable ? `<p class="wnw-paper-reason">${text(row.reason)}</p>` : ''}
      ${row.annotation && !editable ? `<p class="wnw-paper-annotation">${text(row.annotation)}</p>` : ''}${controls}</article>`;
  }

  function render(target, {data: rows, editable, view, onChange}) {
    view.query ??= '';
    view.filter ??= 'all';
    view.page ??= 0;
    target.innerHTML = `<div class="wnw-search-tools">
      <label>筛选本次文献<input class="control" type="search" data-wnw-paper-search placeholder="搜索标题、作者、摘要、DOI" value="${text(view.query)}"></label>
      <label>纳入状态<select class="control" data-wnw-paper-filter>
        <option value="all">全部记录</option><option value="included">仅纳入</option><option value="excluded">仅排除</option>
      </select></label>
      <span class="wnw-search-count" data-wnw-search-count role="status"></span>
      </div><div class="wnw-search-results" data-wnw-search-results></div>
      <nav class="wnw-pagination" data-wnw-pagination aria-label="文献结果分页"></nav>`;
    const list = target.querySelector('[data-wnw-search-results]');
    const count = target.querySelector('[data-wnw-search-count]');
    const pagination = target.querySelector('[data-wnw-pagination]');
    const search = target.querySelector('[data-wnw-paper-search]');
    const filter = target.querySelector('[data-wnw-paper-filter]');
    filter.value = view.filter;

    function updateCount(matches) {
      const included = rows.filter(row => row.included !== false).length;
      count.textContent = `${matches} 条匹配 · 共 ${rows.length} 条 · 纳入 ${included} 条`;
    }

    function paint() {
      const query = view.query.trim().toLocaleLowerCase();
      const matches = rows.map((row, index) => ({row, index})).filter(({row}) => {
        const status = row.included === false ? 'excluded' : 'included';
        return (view.filter === 'all' || view.filter === status) &&
          (!query || searchable.some(key => String(row[key] ?? '').toLocaleLowerCase().includes(query)));
      });
      const pages = Math.max(1, Math.ceil(matches.length / pageSize));
      view.page = Math.min(view.page, pages - 1);
      const visible = matches.slice(view.page * pageSize, (view.page + 1) * pageSize);
      updateCount(matches.length);
      list.innerHTML = visible.map(({row, index}) => paperMarkup(row, index, rows.length, editable)).join('') ||
        '<p class="wnw-empty">没有匹配的文献记录</p>';
      pagination.innerHTML = `<button type="button" class="btn ghost" data-wnw-page="-1" ${view.page ? '' : 'disabled'}>上一页</button>
        <span>第 ${view.page + 1} / ${pages} 页</span>
        <button type="button" class="btn ghost" data-wnw-page="1" ${view.page + 1 < pages ? '' : 'disabled'}>下一页</button>`;
      pagination.querySelectorAll('[data-wnw-page]').forEach(button => button.onclick = () => {
        view.page += Number(button.dataset.wnwPage);
        paint();
      });
      list.querySelectorAll('[data-wnw-cell]').forEach(input => input.oninput = () => {
        const row = rows[Number(input.dataset.wnwCell)];
        const field = input.dataset.field;
        row[field] = field === 'included' ? input.checked : input.value;
        onChange(rows);
        if (field !== 'included') return;
        if (view.filter !== 'all') {
          paint();
          (list.querySelector('[data-field="included"]') || filter).focus();
          return;
        }
        const card = input.closest('article');
        const badge = card.querySelector('.wnw-paper-state');
        card.classList.toggle('is-excluded', !row.included);
        badge.classList.toggle('is-excluded', !row.included);
        badge.textContent = row.included ? '已纳入' : '已排除';
        updateCount(matches.length);
      });
      list.querySelectorAll('[data-wnw-row-move]').forEach(button => button.onclick = () => {
        const [index, direction] = button.dataset.wnwRowMove.split(':').map(Number);
        const next = index + direction;
        if (next < 0 || next >= rows.length) return;
        [rows[index], rows[next]] = [rows[next], rows[index]];
        onChange(rows);
        paint();
        (list.querySelector(`[data-wnw-row-move="${next}:${direction}"]:enabled`) ||
          list.querySelector(`[data-field="included"][data-wnw-cell="${next}"]`) || search).focus();
      });
    }

    search.oninput = () => { view.query = search.value; view.page = 0; paint(); };
    filter.onchange = () => { view.filter = filter.value; view.page = 0; paint(); };
    paint();
  }

  WorkflowNodeRenderers.register('search', {
    accepts: ({artifact, data}) => artifact.id === 'structured' && ['table', 'tool'].includes(artifact.kind) &&
      Array.isArray(data) && data.every(row => row && typeof row.title === 'string'),
    render,
  });
})();
