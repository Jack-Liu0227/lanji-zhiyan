/* A single import preview for intelligence and manually entered references. */
const LiteratureImport = (() => {
  const h = value => esc(String(value ?? ''));
  const safeLink = value => {try {const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}};
  function open({projectId=curTask,items=null,onImported=()=>{}}={}) {
    const origin=document.activeElement,dialog=document.createElement('dialog');
    dialog.className='pt-dialog li-dialog';dialog.setAttribute('aria-label',items?'导入情报到项目':'通过链接或 DOI 导入文献');
    dialog.innerHTML=`<form class="li-form"><header><h3>${items?'导入情报到项目':'通过链接或 DOI 导入文献'}</h3><button type="button" class="pw-text-button" data-li-close aria-label="关闭">×</button></header><label>目标项目<select class="control" name="project_id" required><option value="">选择目标项目</option>${(S.tasks||[]).map(p=>`<option value="${h(p.id)}" ${p.id===projectId?'selected':''}>${h(p.title)}</option>`).join('')}</select></label>${items?`<p class="hint">确认 ${items.length} 条情报。保存标题、作者、日期、摘要和原文链接；相同 DOI 或链接的文献会跳过。</p><div class="li-preview">${items.map(item=>`<article><strong>${h(item.title)}</strong><p class="hint">${h(item.authors||'作者未知')} · ${h(item.source_title||'情报日报')}</p><details><summary>预览摘要与链接</summary><p>${h(item.summary||'订阅源未提供摘要')}</p>${safeLink(item.link)?`<a href="${h(safeLink(item.link))}" target="_blank" rel="noopener noreferrer">${h(item.link)}</a>`:'<p class="pw-error">此条目没有可打开的网页链接，导入时会提示原因。</p>'}</details></article>`).join('')}</div>`:`<label>文献标题<input class="control" name="title" required maxlength="2000" placeholder="填写准确标题，方便检索与引用"></label><label>原文链接<input class="control" name="url" type="url" placeholder="https://…"></label><label>DOI<input class="control" name="doi" placeholder="10.xxxx/xxxxx 或 https://doi.org/…"></label><div class="li-fields"><label>作者<input class="control" name="authors" placeholder="用分号分隔"></label><label>年份<input class="control" name="year" maxlength="4" inputmode="numeric"></label></div><label>摘要<textarea class="control" name="abstract" rows="3"></textarea></label><p class="hint">原文链接与 DOI 至少填写一项。题录入库后，可在项目文献中补充或修改。</p>`}<p class="hint">本次导入保存题录，不代表已下载 PDF 全文。</p><p data-li-status role="status" aria-live="polite"></p><p class="pw-error" data-li-error role="alert"></p><footer><button type="button" class="btn ghost" data-li-library hidden>打开项目文献</button><span class="pw-spacer"></span><button type="button" class="btn ghost" data-li-cancel>关闭</button><button type="submit" class="btn" ${S.tasks?.length?'':'disabled'}>确认导入题录</button></footer></form>`;
    let saving=false,importedProject='';
    dialog.addEventListener('cancel',event=>{if(saving)event.preventDefault();});
    dialog.addEventListener('close',()=>{dialog.remove();if(origin?.isConnected)origin.focus();},{once:true});
    dialog.querySelectorAll('[data-li-close],[data-li-cancel]').forEach(button=>button.onclick=()=>{if(!saving)dialog.close();});
    dialog.querySelector('[data-li-library]').onclick=async()=>{dialog.close();await ProjectLiterature.open(importedProject);};
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault();if(saving)return;
      const form=event.currentTarget,button=form.querySelector('[type=submit]'),values=Object.fromEntries(new FormData(form));
      saving=true;button.disabled=true;form.querySelector('[data-li-error]').textContent='';
      try {
        const result=await api(items?'/api/rss/import':'/api/library/import',items?{project_id:values.project_id,ids:items.map(item=>item.id)}:values);
        importedProject=result.project_id;
        form.querySelector('[data-li-status]').textContent=items?`已导入 ${result.imported} 条题录，已存在 ${result.existing} 条。`:(result.existing?'这篇文献已在项目文献库中。':'文献题录已导入项目。');
        form.querySelector('[data-li-error]').textContent=(result.failures||[]).map(row=>row.title+'：'+row.error).join('\n');
        form.querySelector('[data-li-library]').hidden=!(result.result_id||result.items?.length);
        await onImported(result);button.textContent='再次检查导入';
      } catch(error) {form.querySelector('[data-li-error]').textContent=error.message;}
      finally {saving=false;if(dialog.isConnected)button.disabled=false;}
    };
    document.body.append(dialog);dialog.showModal();dialog.querySelector('[name=project_id]').focus();
  }
  return {open,safeLink};
})();
