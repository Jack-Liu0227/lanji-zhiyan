/* Human revision is a new reviewed artifact version, separate from model execution. */
const ProjectOutputReview=(()=>{
  function canEdit(task,output){return task.status==='awaiting_review'&&['','task','outline','write','review'].includes(task.metadata?.stage||'')&&/\.(md|markdown|txt|json|yaml|yml|csv|tex|bib|py|js|css|html)$/i.test(output.name||output.path||'');}
  async function open(projectId,taskId,index,onSaved){
    const origin=document.activeElement,dialog=document.createElement('dialog');dialog.className='output-revision-dialog';dialog.setAttribute('aria-label','人工修订产物');
    dialog.innerHTML='<header><div><strong>人工修订产物</strong><p class="hint" data-por-meta>正在读取当前版本…</p></div><button type="button" class="pw-text-button" data-por-close>关闭</button></header><div class="por-modes"><button type="button" class="pw-text-button" data-por-mode="edit">编辑</button><button type="button" class="pw-text-button" data-por-mode="preview">预览</button></div><textarea data-por-text aria-label="产物修订正文" spellcheck="false" disabled></textarea><div class="por-preview md-render" data-por-preview hidden></div><footer><label>修订说明<input class="control" data-por-reason placeholder="说明本次修改的依据或目的"></label><p class="pw-error" data-por-error role="alert"></p><button type="button" class="btn btn-primary" data-por-save disabled>保存修订，继续核查</button></footer>';
    document.body.append(dialog);dialog.showModal();let snapshot=null,busy=false;
    const el=name=>dialog.querySelector('[data-por-'+name+']');
    const changed=()=>snapshot&&el('text').value!==snapshot.text;
    const close=async()=>{if(busy)return;if(!changed()||await uiConfirm('修订尚未保存，关闭会丢失本次修改。仍要关闭？'))dialog.close();};
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});el('close').onclick=close;
    dialog.addEventListener('close',()=>{dialog.remove();if(origin?.isConnected)origin.focus();},{once:true});
    dialog.querySelectorAll('[data-por-mode]').forEach(button=>button.onclick=()=>{const preview=button.dataset.porMode==='preview';el('text').hidden=preview;el('preview').hidden=!preview;if(preview)el('preview').innerHTML=renderMarkdown(el('text').value);});
    const save=async()=>{if(!snapshot||busy)return;busy=true;el('save').disabled=true;el('error').textContent='';try{const result=await api('/api/project-tasks/revise-output',{id:taskId,project_id:projectId,reviewed_run_id:snapshot.run_id,expected_version:snapshot.task_version,expected_hash:snapshot.content_hash,output_index:index,text:el('text').value,reason:el('reason').value});dialog.close();await onSaved?.(result.task);}catch(error){if(dialog.isConnected)el('error').textContent=error.message;}finally{busy=false;if(dialog.isConnected)el('save').disabled=!snapshot?.editable;}};
    el('save').onclick=save;dialog.onkeydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();event.stopPropagation();save();}};
    try{snapshot=await api('/api/project-tasks/output?id='+encodeURIComponent(taskId)+'&project_id='+encodeURIComponent(projectId)+'&output_index='+index);if(!dialog.isConnected)return dialog;el('text').value=snapshot.text;el('text').disabled=!snapshot.editable;el('save').disabled=!snapshot.editable;el('meta').textContent=snapshot.name+' · 修订 v'+snapshot.output_revision+' · 原文件与历次修订均保留';if(!snapshot.editable)el('error').textContent='当前产物已不在待人工核查状态。';else el('text').focus();}
    catch(error){if(dialog.isConnected)el('error').textContent=error.message;}
    return dialog;
  }
  return {open,canEdit};
})();
