const WorkflowDocument = {
  accepts({artifact, text}) {
    return typeof text === 'string' && (['markdown', 'checklist'].includes(artifact.kind) ||
      /\.tex$/i.test(artifact.name || '') || (artifact.kind === 'text' && artifact.id === 'structured'));
  },

  render(target, {artifact, text, editable, onChange}, label) {
    const latex = /\.tex$/i.test(artifact.name || '');
    if (editable && label === '稿件' && text.startsWith('<!-- lineage:')) {
      const separator = '\n\n<!-- lineage-author-revision -->\n\n';
      const boundary = text.indexOf(separator);
      const source = (boundary < 0 ? text : text.slice(0, boundary)).trim();
      target.innerHTML = `<details><summary>查看谱系正文与证据</summary><div class="md-render wnw-prose">${renderMarkdown(source)}</div></details>
        <label>作者修订<textarea class="wnw-editor" data-wnw-editor data-wnw-author-revision spellcheck="false"></textarea></label>`;
      const editor = target.querySelector('textarea');
      editor.value = boundary < 0 ? '' : text.slice(boundary + separator.length);
      editor.oninput = () => onChange(editor.value.trim() ? source + separator + editor.value : source + '\n');
      return;
    }
    if (editable) {
      target.innerHTML = `<label>${label}${latex ? ' LaTeX' : ' Markdown'}<textarea class="wnw-editor" data-wnw-editor spellcheck="false"></textarea></label>`;
      const editor = target.querySelector('textarea');
      editor.value = text;
      editor.oninput = () => onChange(editor.value);
      return;
    }

    if (latex) {
      const source = document.createElement('pre');
      source.className = 'wnw-text';
      source.textContent = text;
      target.replaceChildren(source);
      return;
    }

    target.innerHTML = `<div class="md-render wnw-prose">${renderMarkdown(text)}</div>`;
    const headings = [...target.querySelectorAll('h1,h2,h3,h4,h5,h6')];
    if (!headings.length) return;

    const index = document.createElement('nav');
    index.className = 'wnw-outline-index';
    index.setAttribute('aria-label', label + '章节');
    const list = document.createElement('ol');
    const level = Math.min(...headings.map(heading => Number(heading.tagName[1])));
    for (const heading of headings) {
      heading.tabIndex = -1;
      const item = document.createElement('li');
      item.style.paddingInlineStart = `${Number(heading.tagName[1]) - level}em`;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'pw-text-button';
      button.textContent = heading.textContent;
      button.onclick = () => {
        heading.scrollIntoView({block: 'start'});
        heading.focus({preventScroll: true});
      };
      item.append(button);
      list.append(item);
    }
    index.append(list);
    target.prepend(index);
  },
};

for (const [step, label] of [['outline', '大纲'], ['write', '稿件']]) {
  WorkflowNodeRenderers.register(step, {
    accepts: WorkflowDocument.accepts,
    render: (target, context) => WorkflowDocument.render(target, context, label),
  });
}
