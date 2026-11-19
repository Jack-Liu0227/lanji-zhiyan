WorkflowNodeRenderers.register('download', {
  accepts({artifact, data}) {
    return artifact.id === 'structured' && artifact.kind === 'papers' && Array.isArray(data) && data.length > 0;
  },

  render(target, {data, editable, files, openArtifact, onChange}) {
    const escape = value => esc(String(value ?? ''));
    const pdfs = files.filter(file => file.kind === 'pdf');
    const paperFile = row => pdfs.find(file => {
      if (row.sha256) return file.content_hash === row.sha256;
      return file.id === 'asset:' + row.id || file.name === row.filename;
    });
    const annotations = (row, index) => editable ? `
      <label>核查理由<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="reason">${escape(row.reason)}</textarea></label>
      <label>核查备注<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="annotation">${escape(row.annotation)}</textarea></label>` :
      [row.reason, row.annotation].filter(Boolean).map(text => `<p>${escape(text)}</p>`).join('');

    target.innerHTML = `<div class="wnw-papers">${data.map((row, index) => {
      const pdf = paperFile(row);
      const abstract = row.abstract?.abstract;
      const page = row.abstract?.page;
      const notes = Array.isArray(row.reading_notes) ? row.reading_notes : [];
      return `<article class="wnw-download-paper"><h4>${escape(row.title || row.filename || '文献')}</h4>
        ${pdf ? `<button class="btn" type="button" data-wnw-paper-open="${escape(pdf.id)}">预览原文 PDF</button>` : '<p>此版本没有对应的 PDF 快照。</p>'}
        ${abstract ? `<details><summary>摘要${page ? ` · 第 ${escape(page)} 页` : ''}</summary><p>${escape(abstract)}</p></details>` : ''}
        ${notes.map(note => `<details><summary>${escape(note.title || '逐页精读')}</summary><div class="md-render wnw-prose">${renderMarkdown(note.markdown || '')}</div></details>`).join('')}
        ${annotations(row, index)}
      </article>`;
    }).join('')}</div>`;

    target.querySelectorAll('[data-wnw-paper-open]').forEach(button => {
      button.onclick = () => openArtifact(pdfs.find(file => file.id === button.dataset.wnwPaperOpen));
    });
    target.querySelectorAll('[data-wnw-cell]').forEach(input => {
      input.oninput = () => {
        const row = data[Number(input.dataset.wnwCell)];
        row[input.dataset.field] = input.value;
        onChange(data);
      };
    });
  },
});
