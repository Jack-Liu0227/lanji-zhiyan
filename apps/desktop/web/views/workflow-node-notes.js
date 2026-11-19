WorkflowNodeRenderers.register('notes', {
  accepts({artifact, data}) {
    return artifact.id === 'structured' && artifact.kind === 'bilingual' && Array.isArray(data) && data.length > 0;
  },

  render(target, {data, editable, files, openArtifact, loadPdf, onChange}) {
    const escape = value => esc(String(value ?? ''));
    const pdfs = new Map(files.filter(file => file.kind === 'pdf' && file.source_path).map(file => [file.source_path, file]));
    const openButton = (file, label) => file ? `<button class="btn" type="button" data-wnw-notes-pdf="${escape(file.id)}">${label}</button>` : '';

    target.innerHTML = data.map((paper, index) => {
      const source = pdfs.get(paper.source_path);
      const translation = pdfs.get(paper.translation_path || paper.files?.[0]);
      const dual = pdfs.get(paper.dual_path || paper.files?.[1]);
      const notes = paper.reading_notes || [];
      return `<article class="wnw-reading-paper" data-wnw-reading-paper="${index}">
        <h4>${escape(paper.title || translation?.name || '文献 ' + (index + 1))}</h4>
        <div class="wnw-reading-actions">
          ${openButton(source, '原文 PDF')}${openButton(translation, '译文 PDF')}${openButton(dual, '双语 PDF')}
          ${source && translation ? '<button class="btn" type="button" data-wnw-compare aria-expanded="false">原文与译文对照</button>' : ''}
        </div>
        ${source && translation ? '<div data-wnw-comparison hidden></div>' : '<p class="hint">此版本缺少可配对的 PDF。</p>'}
        ${notes.map((note, noteIndex) => editable ? `
          <label>${escape(note.title || '精读笔记')}<textarea class="control" rows="6" data-wnw-reading-note="${noteIndex}">${escape(note.markdown)}</textarea></label>` : `
          <section><h5>${escape(note.title || '精读笔记')}</h5><div class="md-render wnw-prose">${note.markdown ? renderMarkdown(note.markdown) : '<p class="hint">尚无笔记</p>'}</div></section>`).join('')}
        ${!notes.length && typeof paper.translation === 'string' ? (editable ? `
          <label>精读笔记<textarea class="control" rows="6" data-wnw-reading-legacy>${escape(paper.translation)}</textarea></label>` :
          `<div class="md-render wnw-prose">${renderMarkdown(paper.translation)}</div>`) : ''}
      </article>`;
    }).join('');

    for (const article of target.querySelectorAll('[data-wnw-reading-paper]')) {
      const paper = data[Number(article.dataset.wnwReadingPaper)];
      for (const button of article.querySelectorAll('[data-wnw-notes-pdf]')) {
        button.onclick = () => openArtifact(files.find(file => file.id === button.dataset.wnwNotesPdf));
      }
      for (const input of article.querySelectorAll('[data-wnw-reading-note]')) {
        input.oninput = () => {
          paper.reading_notes[Number(input.dataset.wnwReadingNote)].markdown = input.value;
          onChange(data);
        };
      }
      const legacy = article.querySelector('[data-wnw-reading-legacy]');
      if (legacy) legacy.oninput = () => {
        paper.translation = legacy.value;
        onChange(data);
      };
      const compare = article.querySelector('[data-wnw-compare]');
      const panel = article.querySelector('[data-wnw-comparison]');
      if (compare) compare.onclick = async () => {
        panel.hidden = !panel.hidden;
        compare.setAttribute('aria-expanded', String(!panel.hidden));
        if (panel.hidden) {
          panel.replaceChildren();
          return;
        }
        const loading = document.createElement('p');
        loading.setAttribute('role', 'status');
        loading.textContent = '正在读取 PDF…';
        panel.replaceChildren(loading);
        try {
          const urls = await Promise.all([loadPdf(pdfs.get(paper.source_path)), loadPdf(pdfs.get(paper.translation_path))]);
          if (!loading.isConnected) return;
          const pages = paper.page_map || [];
          panel.innerHTML = `${pages.length ? `<label>对照页码 <select class="control" data-wnw-reading-page>
            ${pages.map((page, index) => `<option value="${index}">原文 ${escape(page.source)} / 译文 ${escape(page.translation)}</option>`).join('')}
            </select></label>` : '<p class="hint">此版本没有已核对的页码映射，可分别翻页。</p>'}
            <div class="wnw-reading-pdfs"><iframe title="原文 PDF"></iframe><iframe title="译文 PDF"></iframe></div>`;
          const frames = panel.querySelectorAll('iframe');
          const show = page => {
            frames[0].src = urls[0] + (page ? '#page=' + page.source : '');
            frames[1].src = urls[1] + (page ? '#page=' + page.translation : '');
          };
          const select = panel.querySelector('select');
          if (select) select.onchange = () => show(pages[Number(select.value)]);
          show(pages[0]);
        } catch (error) {
          if (loading.isConnected) loading.textContent = '无法预览 PDF：' + error.message;
        }
      };
    }
  },
});
