WorkflowNodeRenderers.register('map', {
  accepts({artifact, data}) {
    return artifact.kind === 'graph' && Array.isArray(data?.nodes) && Array.isArray(data?.edges);
  },

  render(target, {data: graph, editable, view, onChange}) {
    const escape = value => esc(String(value ?? ''));
    const fields = [['label', '标题'], ['ref', '来源 ID'], ['detail', '证据说明']];
    const types = [['section', '章节'], ['paper', '文献'], ['note', '笔记'], ['data', '数据'], ['evidence', '页级证据']];

    function draw() {
      const nodes = new Map(graph.nodes.map(item => [item.id, item]));
      const node = nodes.get(view.selected) || graph.nodes[0];
      view.selected = node?.id;
      const positions = new Map(graph.nodes.map((item, index) => [item.id, {
        left: 20 + index % 3 * 220, top: 20 + Math.floor(index / 3) * 95,
      }]));
      const options = graph.nodes.map(item => `<option value="${escape(item.id)}">${escape(item.label)}</option>`).join('');
      const source = node?.type === 'evidence' && graph.sources?.[node.ref];
      const links = graph.edges.map(edge => {
        const start = positions.get(edge.from);
        const end = positions.get(edge.to);
        if (!start || !end) return '';
        return `<g><line x1="${start.left + 90}" y1="${start.top + 30}" x2="${end.left + 90}" y2="${end.top + 30}" stroke="var(--muted)"/>
          <text x="${(start.left + end.left) / 2 + 90}" y="${(start.top + end.top) / 2 + 20}" fill="var(--fg)" font-size="10">${escape(edge.label)}</text></g>`;
      }).join('');
      const blocks = graph.nodes.map(item => {
        const position = positions.get(item.id);
        const selected = view.selected === item.id;
        return `<g transform="translate(${position.left} ${position.top})" tabindex="0" role="button" aria-pressed="${selected}"
          data-wnw-graph-node="${escape(item.id)}" aria-label="${escape(item.label)}">
          <rect width="185" height="64" rx="8" fill="var(--panel)" stroke="${selected ? 'var(--accent)' : 'var(--line)'}" stroke-width="2"/>
          <text x="10" y="34" fill="var(--fg)" font-size="12">${escape(item.label?.slice(0, 18) || item.id)}</text></g>`;
      }).join('');
      target.innerHTML = `<div class="wnw-graph"><svg viewBox="0 0 680 ${Math.max(110, Math.ceil(graph.nodes.length / 3) * 95 + 20)}" role="group" aria-label="证据图谱">${links}${blocks}</svg></div>
        ${source ? `<section class="wnw-graph-source"><h4>${escape(source.filename)} · 第 ${escape(source.page_no)} 页</h4><blockquote>${escape(source.excerpt)}</blockquote></section>` : ''}
        ${node ? `<div class="wnw-graph-fields"><label>类型<select class="control" data-wnw-graph-field="type" ${editable ? '' : 'disabled'}>
          ${types.map(([value, label]) => `<option value="${value}" ${node.type === value ? 'selected' : ''}>${label}</option>`).join('')}
          </select></label>${fields.map(([field, label]) => `<label>${label}<textarea class="control" data-wnw-graph-field="${field}" ${editable ? '' : 'readonly'}>${escape(node[field])}</textarea></label>`).join('')}</div>` : ''}
        ${editable ? `<div class="wnw-agent-buttons"><button class="btn ghost" type="button" data-wnw-add-node>添加证据</button><button class="btn ghost" type="button" data-wnw-remove-node ${graph.nodes.length < 2 ? 'disabled' : ''}>删除所选证据</button></div>` : ''}
        <h4>证据连线</h4><div data-wnw-edges>${graph.edges.map((edge, index) => `<div class="wnw-edge"><span>${escape(nodes.get(edge.from)?.label)} → ${escape(nodes.get(edge.to)?.label)} · ${escape(edge.label)}</span>
          ${editable ? `<button class="pw-text-button" type="button" data-wnw-remove-edge="${index}">删除</button>` : ''}</div>`).join('')}</div>
        ${editable ? `<div class="wnw-edge-new"><select class="control" data-wnw-from aria-label="证据连线起点">${options}</select>
          <select class="control" data-wnw-to aria-label="证据连线终点">${options}</select>
          <input class="control" data-wnw-edge-label aria-label="证据连线说明" placeholder="支持 / 引用 / 反驳">
          <button class="btn ghost" type="button" data-wnw-add-edge>添加连线</button></div>` : ''}`;

      for (const button of target.querySelectorAll('[data-wnw-graph-node]')) {
        const choose = () => {
          view.selected = button.dataset.wnwGraphNode;
          draw();
          target.querySelector('[data-wnw-graph-node][aria-pressed="true"]')?.focus();
        };
        button.onclick = choose;
        button.onkeydown = event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            choose();
          }
        };
      }
      if (!editable) return;
      for (const input of target.querySelectorAll('[data-wnw-graph-field]')) {
        input.oninput = () => {
          node[input.dataset.wnwGraphField] = input.value;
          onChange(graph);
        };
      }
      target.querySelector('[data-wnw-add-node]').onclick = () => {
        const item = {id: 'human-' + crypto.randomUUID(), type: 'note', label: '新证据', ref: '', detail: ''};
        graph.nodes.push(item);
        view.selected = item.id;
        onChange(graph);
        draw();
      };
      target.querySelector('[data-wnw-remove-node]').onclick = () => {
        graph.nodes = graph.nodes.filter(item => item.id !== view.selected);
        graph.edges = graph.edges.filter(edge => edge.from !== view.selected && edge.to !== view.selected);
        onChange(graph);
        draw();
      };
      for (const button of target.querySelectorAll('[data-wnw-remove-edge]')) {
        button.onclick = () => {
          graph.edges.splice(Number(button.dataset.wnwRemoveEdge), 1);
          onChange(graph);
          draw();
        };
      }
      target.querySelector('[data-wnw-add-edge]').onclick = () => {
        graph.edges.push({from: target.querySelector('[data-wnw-from]').value,
          to: target.querySelector('[data-wnw-to]').value, label: target.querySelector('[data-wnw-edge-label]').value});
        onChange(graph);
        draw();
      };
    }

    draw();
  },
});
