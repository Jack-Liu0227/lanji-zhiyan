/* A conversation is the stable entry point for every execution and retry. */
const ExecutionConversation=(()=>{
  const labels={ready:'待执行',queued:'排队中',running:'进行中',awaiting_review:'等待审查',succeeded:'已完成',completed:'已完成',delivered:'已交付',failed:'执行失败',blocked:'等待处理',interrupted:'中断待恢复',cancelled:'已停止'};
  const h=value=>esc(String(value??''));
  async function open(kind,id,project){
    const data=await api('/api/execution-conversations?kind='+encodeURIComponent(kind)+'&entity_id='+encodeURIComponent(id)+'&project_id='+encodeURIComponent(project));
    await ProjectWorkspace.openConversation(data.execution.chat_id,project);
  }
  function render(box,data,refresh){
    const execution=data.execution,project=execution.project_id,flow=execution.kind==='workflow';
    const header=box.closest('.pw-chat')?.querySelector('.pw-conversation-tabs');
    if(header){let indicator=header.querySelector('[data-execution-indicator]');if(!indicator){indicator=document.createElement('button');indicator.type='button';indicator.className='execution-chat-indicator';indicator.dataset.executionIndicator='';header.append(indicator);}indicator.textContent=(flow?'工作流':'任务')+' · '+(labels[execution.status]||execution.status);indicator.title='查看执行进度和操作';indicator.onclick=()=>box.querySelector('.execution-chat-summary')?.scrollIntoView({block:'start'});}
    const opened=new Set([...box.querySelectorAll('details[open]')].map(node=>node.dataset.executionDetail).filter(Boolean));
    const oldScroll=box.scrollTop,atBottom=box.scrollHeight-box.clientHeight-oldScroll<70;
    box.innerHTML=`<section class="execution-chat-summary" data-execution-id="${h(execution.entity_id)}"><div><span class="execution-chat-kind">${flow?'工作流对话':'任务对话'}</span><strong>${h(execution.title)}</strong><span class="pw-state">${h(labels[execution.status]||execution.status)}</span></div><div class="execution-chat-actions"><button type="button" class="btn ghost" data-execution-detail>${flow?'工作流节点与审查':'任务与产物'}</button>${(execution.prepared||['ready','failed','blocked','interrupted',...(flow?[]:['cancelled'])].includes(execution.status))?'<button type="button" class="btn" data-pw-start>继续执行</button>':''}${['queued','running'].includes(execution.status)?'<button type="button" class="btn ghost" data-execution-stop>停止执行</button>':''}<button type="button" class="pw-text-button" data-execution-refresh>刷新进度</button></div><p class="pw-error" role="alert" data-execution-error></p></section>`;
    box.querySelector('[data-execution-detail]').onclick=()=>flow?ConnectedResearch.openRun(project,execution.workflow_run_id):ProjectWorkspace.openTask(execution.entity_id,project);
    box.querySelector('[data-execution-refresh]').onclick=refresh;
    box.querySelector('[data-execution-error]').textContent=execution.error||'';
    if(execution.executor){const who=execution.executor,label=document.createElement('p');label.className='execution-chat-executor';label.textContent='执行专家 · '+who.sender+(who.team_name?' · '+who.team_name:'')+(who.member_name?' / '+who.member_name:'');box.querySelector('.execution-chat-summary').append(label);}
    const start=box.querySelector('[data-pw-start]');if(start)start.textContent=execution.prepared?'开始工作流':execution.status==='ready'?'开始任务':'继续执行';
    for(const [index,output] of (execution.outputs||[]).entries()){
      const button=document.createElement('button');button.type='button';button.className='btn ghost';button.dataset.pwOutput=String(index);button.textContent='查看产物 · '+(output.name||output.relative||'执行结果');
      button.onclick=async event=>{event.stopPropagation();await ProjectWorkspace.openTask(execution.entity_id,project,{chatId:execution.chat_id});document.querySelector('#pwResource [data-pw-output="'+index+'"]')?.click();};
      box.querySelector('.execution-chat-actions').append(button);
    }
    for(const child of execution.children||[]){
      if(child.status!=='awaiting_review')continue;
      const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='审查 · '+child.title;button.dataset.executionReview=child.id;
      button.onclick=()=>ProjectWorkspace.openTask(child.id,project,{chatId:execution.chat_id});box.querySelector('.execution-chat-actions').append(button);
    }
    for(const [selector,action] of [['[data-pw-start]','start'],['[data-execution-stop]','cancel']]){
      const button=box.querySelector(selector);if(!button)continue;
      button.onclick=async event=>{event.stopPropagation();button.disabled=true;try{
        if(action==='cancel'&&execution.active_run_kind==='conversation')await api('/api/execution-board/conversation-action',{project_id:project,run_id:execution.active_run_id,action:'stop'});
        else await api(flow?'/api/workflow/execution/'+(action==='start'?(execution.prepared?'start':'resume'):'cancel'):'/api/project-tasks/'+action,{id:execution.entity_id,project_id:project,request_key:crypto.randomUUID()});await refresh();
      }catch(error){box.querySelector('[data-execution-error]').textContent=error.message;button.disabled=false;}};
    }
    if(execution.children?.length){
      const nodes=document.createElement('details');nodes.className='execution-chat-nodes';nodes.dataset.executionDetail='nodes';nodes.open=opened.has('nodes');
      nodes.innerHTML=`<summary>${flow?'工作流节点':'派生任务'} · ${execution.children.length}</summary><div>${execution.children.map(child=>`<article><span><b>${h(child.title)}</b><small>${h(labels[child.status]||child.status)}</small></span><button type="button" class="pw-text-button" data-child-chat="${h(child.chat_id)}">查看对话</button><button type="button" class="btn ghost" data-child-task="${h(child.id)}">${child.status==='awaiting_review'?'审查产物':'任务详情'}</button></article>`).join('')}</div>`;
      nodes.querySelectorAll('[data-child-chat]').forEach(button=>button.onclick=()=>ProjectWorkspace.openConversation(button.dataset.childChat,project));
      nodes.querySelectorAll('[data-child-task]').forEach(button=>button.onclick=()=>ProjectWorkspace.openTask(button.dataset.childTask,project,{chatId:execution.chat_id}));box.append(nodes);
    }
    for(const message of data.messages||[]){
      const section=document.createElement('section');section.className='execution-chat-turn';section.dataset.executionMessage=message.id;
      if(message.attempt)section.innerHTML=`<div class="execution-chat-turn-meta">${h(message.task_title)} · 第 ${message.attempt} 次执行 · ${h(labels[message.status]||message.status)}${message.team_name?' · '+h(message.team_name):''}${message.member_name?' · '+h(message.member_name):''}</div>`;
      appendOne(section,{...message,type:message.role,content:message.content||labels[message.status]||'',replay:true,at:message.created_at},message.sender?{name:message.sender}:undefined);
      section.querySelectorAll('details').forEach((node,index)=>{node.dataset.executionDetail=message.id+':'+index;node.open=opened.has(node.dataset.executionDetail);});box.append(section);
    }
    box.scrollTop=atBottom?box.scrollHeight:oldScroll;
  }
  return {open,render};
})();
