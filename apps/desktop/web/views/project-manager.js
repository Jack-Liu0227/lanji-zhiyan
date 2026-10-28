/* Compact creation and metadata editing beside the project switcher. */
const ProjectManager=(()=>{
  const h=value=>esc(String(value??''));
  async function open({edit=false}={}){
    if(window.ProjectWorkspace?.active()&&!await ProjectWorkspace.flush())return;
    const existing=document.querySelector('dialog.project-manager');if(existing){existing.focus();return;}
    const project=edit?S.tasks.find(row=>row.id===curTask):null;
    const dialog=document.createElement('dialog');dialog.className='project-manager pm-compact';
    const title=project?'编辑项目信息':'新建项目';let directory='';
    dialog.setAttribute('aria-label',title);
    dialog.innerHTML=`<header class="pm-head"><h2>${title}</h2><button type="button" class="pw-text-button" data-pm-close aria-label="关闭">×</button></header><form class="pm-form"><label>项目名称<input class="control" id="t-title" maxlength="200" required value="${h(project?.title||'')}" placeholder="给研究课题起个名字"></label><label>研究目标<textarea class="control" id="t-q" rows="3" placeholder="研究什么问题，交付什么成果？">${h(project?.question||'')}</textarea></label><details><summary>关键词与保存位置</summary><label>关键词<input class="control" id="t-kw" value="${h(project?.material_system||'')}" placeholder="用逗号分隔"></label><label>项目目录<div class="pm-directory"><input class="control" id="t-dir" value="${h(project?.base_dir||'')}" readonly placeholder="自动保存在本地项目目录"><button type="button" class="btn ghost" id="t-dir-pick" ${project?'disabled':''}>选择文件夹</button></div></label></details><p class="hint">资料、任务、工作流与自动化归属本项目。也可以让智研管家根据研究目标自动立项。</p><p class="pw-error" data-pm-error role="alert"></p><footer><button type="button" class="pw-text-button" data-pm-cancel>取消</button><button class="btn btn-primary" id="t-go" type="submit">${project?'保存':'创建并进入'}</button></footer></form>`;
    dialog.querySelector('[data-pm-close]').onclick=dialog.querySelector('[data-pm-cancel]').onclick=()=>dialog.close();
    if(project){
      const remove=document.createElement('button');remove.type='button';remove.className='pw-text-button';remove.dataset.pmDelete='';remove.textContent='删除项目记录';
      dialog.querySelector('form footer').prepend(remove);
      remove.onclick=async()=>{
        if(!await uiConfirm('删除项目「'+project.title+'」及其关联记录？此操作不可撤销。'))return;
        try{await api('/api/task/delete',{id:project.id});await refreshState(false);needTask();dialog.close();await render();}
        catch(error){dialog.querySelector('[data-pm-error]').textContent=error.message;}
      };
    }
    dialog.querySelector('#t-dir-pick').onclick=async()=>{
      try{const result=await api('/api/folder/pick?title='+encodeURIComponent('选择项目目录')+'&initial='+encodeURIComponent(directory));if(result.dir){directory=result.dir;dialog.querySelector('#t-dir').value=directory;}}
      catch(error){dialog.querySelector('[data-pm-error]').textContent=error.message;}
    };
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault();const button=dialog.querySelector('#t-go');button.disabled=true;
      try{
        const body={title:dialog.querySelector('#t-title').value.trim(),question:dialog.querySelector('#t-q').value.trim(),material_system:dialog.querySelector('#t-kw').value.trim()};
        const result=await api(project?'/api/task/update':'/api/task/create',project?{...body,id:project.id,expected_updated_at:project.updated_at}:{...body,base_dir:directory});
        const id=project?.id||result.task_id;await refreshState(false);dialog.close();await ProjectWorkspace.openProject(id);
      }catch(error){dialog.querySelector('[data-pm-error]').textContent=error.message;button.disabled=false;}
    };
    document.body.appendChild(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();dialog.querySelector('#t-title').focus();return dialog;
  }
  window.ProjectManager={open};return window.ProjectManager;
})();
