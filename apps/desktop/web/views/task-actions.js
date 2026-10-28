const TaskActions=(()=>{
  const link=row=>location.origin+location.pathname+'#project='+encodeURIComponent(row.project_id)+'&task='+encodeURIComponent(row.id);
  async function copy(text){await navigator.clipboard.writeText(text);uiNotify('已复制');}
  async function visibility(rows,refresh,action='delete',section=''){
    if(!rows.length)return;
    if(action==='delete'){
      const running=rows.some(row=>['running','queued','waiting_input'].includes(row.status));
      if(!await uiConfirm(`将${section?'“'+section+'”中的 ':''}${rows.length} 条记录从看板移入最近删除？对应会话、任务和产物会保留。${running?' 其中尚未结束的执行会继续；如需结束执行，请先停止。':''}`,{ok:'从看板删除'}))return;
    }
    const groups=new Map();
    for(const row of rows){if(!groups.has(row.project_id))groups.set(row.project_id,[]);groups.get(row.project_id).push({kind:row.kind,id:row.id});}
    try{
      for(const [project_id,items] of groups)for(let offset=0;offset<items.length;offset+=500)await api('/api/execution-board/visibility',{project_id,action,items:items.slice(offset,offset+500)});
    }finally{await refresh();}
  }
  const removal=(row,refresh)=>({text:row.deleted_at?'恢复到看板':'从看板删除',danger:!row.deleted_at,run:()=>visibility([row],refresh,row.deleted_at?'restore':'delete')});
  async function stop(row){
    if(row.active_run_kind==='conversation'||(row.kind==='conversation'&&row.execution_kind!=='workflow'))return api('/api/execution-board/conversation-action',{project_id:row.project_id,run_id:row.active_run_id||row.run_id,action:'stop'});
    return api(row.execution_kind==='workflow'?'/api/workflow/execution/cancel':'/api/project-tasks/cancel',{project_id:row.project_id,id:row.execution_kind==='workflow'?row.run_id:row.id});
  }
  function actions(row,refresh){
    if(row.deleted_at)return [removal(row,refresh)];
    if(row.kind!=='task')return [
      {text:row.channel==='toolbox'?'查看工具处理结果':'打开对话',run:()=>row.channel==='toolbox'?PaperToolbox.openResult(row.run_id,row.project_id):ProjectWorkspace.openConversation(row.chat_id,row.project_id)},
      ...(['running','queued','waiting_input'].includes(row.status)?[{text:'停止执行',run:async()=>{await stop(row);await refresh();}}]:[]),
      ...(['failed','interrupted'].includes(row.status)?[{text:'继续执行',run:async()=>{await api(row.execution_kind==='workflow'?'/api/workflow/execution/resume':'/api/execution-board/conversation-action',{id:row.run_id,project_id:row.project_id,run_id:row.run_id,action:'resume'});await refresh();}}]:[]),
      {separator:true},removal(row,refresh)
    ];
    const command=async(action,body={})=>{const result=await api('/api/project-tasks/'+action,{project_id:row.project_id,id:row.id,...body});await refresh();return result;};
    const edit=async(key,title,value)=>{const answer=await uiPrompt(title,value||'');if(answer!==null)await command('organize',{[key]:answer});};
    return [
      {text:'打开任务对话',run:()=>ProjectWorkspace.openConversation(row.chat_id,row.project_id)},
      {text:'打开任务与审查',run:()=>ProjectWorkspace.openTask(row.id,row.project_id)},
      {text:'重命名任务…',run:()=>edit('title','任务名称',row.title)},
      {text:'优先级…',run:()=>edit('priority','优先级（P0 / P1 / P2）',row.priority)},
      {text:'开始日期…',run:()=>edit('start_date','开始日期（YYYY-MM-DD，留空清除）',row.start_date)},
      {text:'截止日期…',run:()=>edit('due_date','截止日期（YYYY-MM-DD，留空清除）',row.due_date)},
      {text:row.pinned?'取消置顶':'置顶任务',run:()=>command('organize',{pinned:!row.pinned})},
      {separator:true},
      {text:'在新标签页打开',run:()=>window.open(link(row),'_blank','noopener')},
      {text:'复制任务链接',run:()=>copy(link(row))},
      {text:'复制本地工作目录',run:async()=>{const data=await command('location');await copy(data.path);}},
      {text:'复制为新任务',run:async()=>{const data=await command('duplicate');await ProjectWorkspace.openTask(data.task.id,row.project_id);}},
      {separator:true},
      ...(['running','queued'].includes(row.status)?[{text:'停止执行',run:async()=>{await stop(row);await refresh();}}]:[]),
      removal(row,refresh)
    ];
  }
  async function navigate(){
    const params=new URLSearchParams(location.hash.slice(1)),project=params.get('project'),task=params.get('task');
    if(project&&task){try{await ProjectWorkspace.openTask(task,project);}catch(error){uiNotify(error.message);}}
  }
  window.addEventListener('hashchange',navigate);
  window.addEventListener('load',()=>{let tries=0;const timer=setInterval(()=>{if(S.loaded){clearInterval(timer);navigate();}else if(++tries>60)clearInterval(timer);},250);});
  return {actions,copy,visibility,stop};
})();
