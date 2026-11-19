WorkflowNodeRenderers.register('entrans', {
  accepts({artifact, data}) {
    return artifact.id === 'structured' && data && typeof data.result === 'string';
  },

  render(target, {data, editable, onChange}) {
    const segments = Array.isArray(data.segments) && data.segments.length ? data.segments : null;
    const rows = segments || [{source: data.source, target: data.result}];
    target.innerHTML = rows.map((segment, index) => {
      const suffix = segments ? `第 ${index + 1} 段` : '';
      const translation = segment.target ?? segment.translation ?? '';
      return `<div class="wnw-bilingual wnw-segment-group">
        <section><h4>原文 ${suffix}</h4><div class="md-render wnw-prose">${renderMarkdown(segment.source || '')}</div></section>
        <section>${editable ? `<label>译文 ${suffix}<textarea class="wnw-editor" spellcheck="false" data-wnw-translation="${index}">${esc(translation)}</textarea></label>` :
          `<h4>译文 ${suffix}</h4><div class="md-render wnw-prose">${renderMarkdown(translation)}</div>`}</section></div>`;
    }).join('');
    for (const editor of target.querySelectorAll('[data-wnw-translation]')) {
      editor.oninput = () => {
        if (segments) {
          const segment = segments[Number(editor.dataset.wnwTranslation)];
          segment['target' in segment ? 'target' : 'translation'] = editor.value;
          data.result = segments.map(row => row.target ?? row.translation ?? '').join('\n\n');
        } else {
          data.result = editor.value;
        }
        onChange(data);
      };
    }
  },
});
