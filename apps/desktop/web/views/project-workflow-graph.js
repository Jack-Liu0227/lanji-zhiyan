/* Project-owned graph editing reuses canonical tasks for execution and human review. */
const ProjectWorkflowGraph=(()=>{
  const h=value=>esc(String(value??'')),copy=value=>JSON.parse(JSON.stringify(value));
  let current=null;
  function dirty(){return !!current?.host.isConnected&&current.changed;}
  async function flush(){return !dirty()||await current.save();}
  // ponytail: 拖拽重排只支持线性链（edges 恰好构成一条链）。分支流程用右侧“前置节点”改连线。
  function chainOrder(layout){
    const nodes=layout.nodes||[],edges=layout.edges||[];
    if(!nodes.length||edges.length!==nodes.length-1)return null;
    const ids=new Set(nodes.map(node=>node.id)),out=new Map(),inn=new Map();
    for(const edge of edges){
      if(!ids.has(edge.source)||!ids.has(edge.target))return null;
      out.set(edge.source,(out.get(edge.source)||0)+1);inn.set(edge.target,(inn.get(edge.target)||0)+1);
    }
    if(nodes.some(node=>(out.get(node.id)||0)>1||(inn.get(node.id)||0)>1))return null;
    const head=nodes.filter(node=>!inn.get(node.id));
    if(head.length!==1)return null;
    const order=[];let cursor=head[0];
    while(cursor&&order.length<=nodes.length){
      order.push(cursor.id);
      const next=edges.find(edge=>edge.source===cursor.id);
      cursor=next&&nodes.find(node=>node.id===next.target);
    }
    return order.length===nodes.length?order:null;
  }
  async function mount(host,options){
    host.__graphDispose?.();
    const state={host,changed:false,selected:'',mode:'config',layout:null,data:null,bindings:[],run:null,tasks:[],save:null};current=state;
    host.__graphState=state;
    host.__graphDispose=()=>{if(host.__graphState===state)host.__graphState=null;};
    const alive=()=>host.__graphState===state&&host.isConnected&&options.live();
    function empty(message='当前对话暂无工作流运行'){
      host.innerHTML=`<section class="pwg-empty" role="status"><h3>${h(message)}</h3><p class="hint">向管家描述研究目标，或前往工作流工作台创建、选择模板。</p><div class="pwg-actions"><button class="btn btn-primary" type="button" data-pwg-ask-steward>向管家描述目标</button><button class="btn ghost" type="button" data-pwg-open-studio>打开工作流工作台</button>${options.projectId?'<button class="btn ghost" type="button" data-pwg-template>查看 / 编辑本项目工作流</button>':''}</div></section>`;
      host.querySelector('[data-pwg-ask-steward]').onclick=()=>options.askSteward?.();
      host.querySelector('[data-pwg-open-studio]').onclick=()=>openTab('workflow-studio');
      const tplBtn=host.querySelector('[data-pwg-template]');
      if(tplBtn)tplBtn.onclick=()=>{options.enableTemplate?.();mount(host,{...options,allowTemplate:true});};
    }
    if(options.contextual&&!options.focus?.runId&&!options.allowTemplate&&Array.isArray(options.scopedRuns)&&options.scopedRuns.length===0){empty();return;}
    const fetchRun=()=>options.contextual&&!options.focus?.runId?Promise.resolve({run:null}):api('/api/workflow/execution?'+(options.focus?.runId?'id='+encodeURIComponent(options.focus.runId):'task_id='+encodeURIComponent(options.projectId)));
    const fetchTasks=()=>options.contextual&&!options.focus?.runId?Promise.resolve({tasks:[]}):api('/api/project-tasks?project_id='+encodeURIComponent(options.projectId)+(options.focus?.runId?'&workflow_run_id='+encodeURIComponent(options.focus.runId):''));
    host.innerHTML='<p class="pw-loading">正在读取项目工作流…</p>';
    try{
      const [data,executors,runs,tasks]=await Promise.all([
        api('/api/workflow/layouts/state?task_id='+encodeURIComponent(options.projectId)),
        api('/api/project-tasks/executors?project_id='+encodeURIComponent(options.projectId)),
        fetchRun(),
        fetchTasks()]);
      if(!alive())return;
      if(!data.layout&&!runs.run){empty('当前项目未选择工作流模板');return;}
      Object.assign(state,{data,layout:copy(data.layout||runs.run.layout),selected:data.selected_node,bindings:executors.bindings||[],teams:executors.teams||[],run:runs.run,tasks:tasks.tasks||[]});
      if(options.focus?.runId&&state.run){
        const requested=options.focus.nodeId;
        const fallback=state.run.settings?.current_node_id||state.run.layout.order?.[0]||state.run.layout.nodes[0]?.id;
        state.mode='run';state.selected=requested&&state.run.layout.nodes.some(node=>node.id===requested)?requested:fallback||state.selected;
      }
      for(const node of state.layout.nodes){
        if(node.executor_binding_id)node.executor_binding_id=ExecutorChoices.find(state.bindings,node.executor_binding_id)?.id||node.executor_binding_id;
        if(!node.executor_binding_id&&(node.team_id||node.assistant_id)){
          const match=state.bindings.find(binding=>binding.kind===(node.team_id?'team':'assistant')&&binding.target_id===(node.team_id||node.assistant_id));
          if(match)node.executor_binding_id=match.id;
        }
      }
      const el=name=>host.querySelector('[data-pwg-'+name+']');
      function message(value){if(alive())el('error').textContent=value||'';}
      function capture(){
        if(state.mode!=='config')return;const node=state.layout.nodes.find(node=>node.id===state.selected);if(!node||!el('label'))return;
        node.label=el('label').value.trim();node.step=el('step').value;node.prompt=el('prompt').value;node.review_required=el('review').checked;node.manual_review_required=el('manual-review').checked;
        node.manual_execution=el('manual-execution').checked;
        node.claim_audit=node.step==='review'&&!node.manual_execution&&el('claim-audit').checked;
        node.executor_binding_id=el('binding').value;node.member_id=el('member').value;
        node.model_id=el('model')?.value||'';
        node.reasoning=el('reasoning')?.value||'auto';
        node.model_policy=el('model-policy')?.value||'fallback';
        node.fallback_model_ids=el('fallbacks')?[...el('fallbacks').selectedOptions].map(o=>o.value).filter(Boolean):[];
        node.node_kind=el('kind').value;
        if(node.node_kind==='tool'){node.tool_name=el('tool-name').value.trim();try{node.tool_args=JSON.parse(el('tool-args').value||'{}');}catch{throw new Error('固定工具参数须为有效 JSON 对象');}}
        if(node.node_kind==='workflow')node.workflow_template_id=el('template').value;
        for(const key of ['tools','skills']){const value=el(key).value.trim();node[key]=value?value.split(/[,，\n]/).map(value=>value.trim()).filter(Boolean):null;}
        const incoming=[...host.querySelectorAll('[data-pwg-parent]:checked')].map(input=>{
          const field=input.closest('[data-pwg-edge]');
          const artifact=field.querySelector('[data-pwg-artifact]').value.trim();
          const argument=node.node_kind==='tool'?field.querySelector('[data-pwg-argument]').value.trim():'';
          return {source:input.dataset.pwgParent,target:node.id,...(artifact?{artifact}:{}),...(argument?{argument}:{})};
        });
        state.layout.edges=state.layout.edges.filter(edge=>edge.target!==node.id).concat(incoming);
        const order=[],pending=new Set(state.layout.nodes.map(node=>node.id));
        for(let pass=0;pending.size&&pass<state.layout.nodes.length;pass++)for(const id of [...pending]){
          if(state.layout.edges.filter(edge=>edge.target===id).every(edge=>order.includes(edge.source))){order.push(id);pending.delete(id);}
        }
        state.layout.order=[...order,...pending];
      }
      state.save=async()=>{
        if(state.saving)return state.saving;
        if(!alive()||state.mode!=='config')return true;await state.selectionSave;try{capture();}catch(error){message(error.message);return false;}if(!state.changed)return true;
        host.inert=true;el('notice').textContent='正在保存节点…';
        state.saving=(async()=>{try{const response=await api('/api/workflow/layouts/project',{project_id:options.projectId,layout_id:state.layout.id,expected_revision:state.layout.revision,expected_fingerprint:state.layout.fingerprint,...Object.fromEntries(['name','description','nodes','edges','order'].map(key=>[key,state.layout[key]]))});
          if(!alive())return true;state.layout=response.layout;state.changed=false;options.saved?.();draw();el('notice').textContent='已保存到当前项目，后续新运行使用此版本。';return true;
        }catch(error){message(error.message);if(alive())el('notice').textContent='';return false;}finally{host.inert=false;state.saving=null;}})();
        return state.saving;
      };
      function graphData(){
        const source=state.mode==='run'&&state.run?state.run.layout:state.layout;
        const ordered=new Map(source.nodes.map(node=>[node.id,node])),level=new Map(),pending=new Set(ordered.keys());
        for(let loop=0;pending.size&&loop<source.nodes.length;loop++)for(const id of [...pending]){const parents=source.edges.filter(edge=>edge.target===id).map(edge=>edge.source);if(parents.every(id=>level.has(id))){level.set(id,Math.max(-1,...parents.map(id=>level.get(id)))+1);pending.delete(id);}}
        return {...source,selected_node:state.selected,nodes:source.nodes.map(node=>{
          const taskId=state.run?.settings?.task_ids?.[node.id];
          const task=state.tasks.find(t=>t.id===taskId)||state.tasks.find(t=>state.run&&t.workflow_run_id===state.run.id&&t.workflow_node_id===node.id);
          const owner=state.bindings.find(item=>item.id===node.executor_binding_id);
          const hasResult=state.mode==='run'&&WorkflowNodeWorkbench.eligible(task);
          const reply=(task?.latest_run?.checkpoint?.reply||'').replace(/^[#>*\-\s]+/,'').trim();
          const chips=(task?.outputs||[]).map((f,i)=>({id:'file:'+i,task_id:task.id,name:f.name||(f.path||'').split(/[\\/]/).pop()||'产物'}));
          const mirrored=state.run?.nodes?.find(item=>item.acceptance?.node_id===node.id||item.checkpoint?.canonical_task_id===taskId);
          const scheduledFailure=Object.hasOwn(state.run?.settings?.dag_errors||{},node.id)&&!['delivered','awaiting_review','running'].includes(task?.status);
          const liveStatus=scheduledFailure?'failed':task?.status||mirrored?.status||'pending';
          const failure=task?.latest_run?.error||task?.metadata?._start_error||state.run?.settings?.dag_errors?.[node.id]||mirrored?.error;
          const reason=['failed','blocked','interrupted'].includes(liveStatus)?failure:'';
          return {...node,level:level.get(node.id)||0,status:liveStatus,owner_name:owner?.display_name||'',output_label:hasResult?'可查看产物':'',result_available:hasResult,summary_text:reason||reply||(node.prompt||'').slice(0,36),artifact_chips:chips||[]};
        })};
      }
      function drawMember(node){
        const binding=state.bindings.find(item=>item.id===el('binding').value),team=state.teams.find(team=>team.id===binding?.target_id);
        const members=ExecutorChoices.members(team?.members,node.member_id);
        el('member').innerHTML='<option value="">跟随团队负责人</option>'+members.map(member=>`<option value="${h(member.id)}">${h(member.name)}</option>`).join('');
        if(node.member_id&&!members.some(member=>member.id===node.member_id))el('member').insertAdjacentHTML('beforeend',`<option value="${h(node.member_id)}" disabled>原成员已失效，请重新选择</option>`);
        el('member').value=node.member_id||'';el('member').disabled=!team;
      }
      function recoverable(task){
        const checkpoint=task.latest_run?.checkpoint||{};
        return !task.metadata?.node_config?.manual_execution&&!checkpoint.waiting_children&&
          !['delivered','awaiting_review','cancelled'].includes(task.status)&&
          (['failed','interrupted'].includes(task.status)||Object.hasOwn(state.run?.settings?.dag_errors||{},task.workflow_node_id)||
            (task.status==='blocked'&&['failed','interrupted'].includes(task.latest_run?.status)));
      }
      function canResume(){return state.run&&['failed','blocked','interrupted','awaiting_review'].includes(state.run.status)&&state.tasks.some(recoverable);}
      function refreshHeader(){
        const ids=Object.values(state.run?.settings?.task_ids||{}),total=ids.length;
        const delivered=ids.filter(id=>state.tasks.find(task=>task.id===id)?.status==='delivered').length;
        const reviews=ids.filter(id=>state.tasks.find(task=>task.id===id)?.status==='awaiting_review').length;
        const metric=host.querySelector('.pwg-run-progress');
        if(metric)metric.textContent=state.run?`${total?Math.round(delivered/total*100):0}% · ${delivered}/${total} 已交付${reviews?' · '+reviews+' 待核查':''}`:'尚未运行';
        const existing=el('resume');
        if(existing&&!canResume())existing.remove();
        if(!existing&&canResume()){
          metric?.insertAdjacentHTML('afterend','<button type="button" class="btn btn-primary sm" data-pwg-resume>从检查点继续</button>');
          el('resume')?.addEventListener('click',()=>resumeRun());
        }
      }
      async function resumeRun(task=null,restart=false,modelId=''){
        if(state.resuming)return;
        if(restart&&!await uiConfirm('重新执行「'+task.title+'」？本节点将从起点执行，旧尝试和其他节点交付保留。请先核对上次工具是否已写入文件或产生外部结果。'))return;
        state.resuming=true;
        const key=(restart?'restart:':'continue:')+(modelId?modelId+':':'')+(task?task.workflow_node_id+':'+(task.latest_run?.id||''):JSON.stringify(state.tasks.filter(recoverable).map(item=>[item.id,item.latest_run?.id])));
        state.retryKeys||=new Map();
        if(!state.retryKeys.has(key))state.retryKeys.set(key,crypto.randomUUID());
        host.querySelectorAll('[data-pwg-resume],[data-pwg-retry-node]').forEach(button=>button.disabled=true);
        try{
          await api('/api/workflow/execution/resume',{id:state.run.id,project_id:options.projectId,
            request_key:state.retryKeys.get(key),restart_node:restart,
            ...(modelId?{model_id:modelId,allow_model_switch:true}:{}),
            ...(task?{node_id:task.workflow_node_id,expected_task_run_id:task.latest_run?.id||''}:{})});
          if(alive())await options.reload(state.run.id);
        }catch(error){message(error.message);}finally{
          state.resuming=false;
          if(alive())host.querySelectorAll('[data-pwg-resume],[data-pwg-retry-node]').forEach(button=>button.disabled=false);
        }
      }
      function inspector(){
        const graph=graphData(),node=graph.nodes.find(node=>node.id===state.selected)||graph.nodes[0];if(!node)return;state.selected=node.id;
        const models=(typeof S!=='undefined'?S?.models:window?.S?.models)||[];
        const nodeFallbacks=new Set(Array.isArray(node.fallback_model_ids)?node.fallback_model_ids:[]);
        const taskId=state.run?.settings?.task_ids?.[node.id],task=state.tasks.find(task=>task.id===taskId);
        const parents=new Map(state.layout.edges.filter(edge=>edge.target===node.id).map(edge=>[edge.source,edge]));
        const hasResult=!!(task&&WorkflowNodeWorkbench.eligible(task));
        const quickResult=el('quick-result');
        if(quickResult){quickResult.hidden=state.mode!=='run'||!task;quickResult.textContent=hasResult?'打开节点产物工作台':'打开节点工作台';quickResult.onclick=()=>task&&options.openResult(WorkflowNodeWorkbench.availableSelection(task));}
        el('inspector').innerHTML=`<div class="pwg-node-heading"><span class="pw-eyebrow">${state.mode==='run'?'运行快照':'节点配置'} · ${h(node.id)}</span><h3>${h(node.label)}</h3></div>${state.mode==='config'?`
          <label>节点名称<input class="control" data-pwg-label value="${h(node.label)}"></label><label>执行步骤<select class="control" data-pwg-step>${[{id:'task',name:'通用项目任务'},...state.data.stages].map(step=>`<option value="${h(step.id)}" ${step.id===node.step?'selected':''}>${h(step.name)}</option>`).join('')}</select></label>
          <label>执行者<select class="control" data-pwg-binding><option value="">跟随项目角色绑定</option>${ExecutorChoices.unique(state.bindings,node.executor_binding_id).filter(ExecutorChoices.ready).map(binding=>`<option value="${h(binding.id)}" ${binding.id===node.executor_binding_id?'selected':''}>${h(binding.kind==='team'?'团队 · ':'专家 · ')}${h(binding.display_name)}</option>`).join('')}</select></label>
          <label>团队成员<select class="control" data-pwg-member></select></label>
          <details ${node.model_id||nodeFallbacks.size||node.model_policy==='strict'?'open':''}><summary>模型与容错配置${node.model_id?' · '+h(models.find(m=>m.id===node.model_id)?.model||node.model_id):' · 跟随执行者'}</summary>
            <label>节点主模型<select class="control" data-pwg-model><option value="">跟随执行者 / 默认模型</option>${models.map(m=>`<option value="${h(m.id)}" ${m.id===node.model_id?'selected':''}>${h(m.name||m.model)}${m.health?.status&&m.health.status!=='unknown'?' ('+h(m.health.status)+')':''}</option>`).join('')}</select></label>
            <label>容错策略<select class="control" data-pwg-model-policy><option value="fallback" ${node.model_policy!=='strict'?'selected':''}>主模型不可用时自动切换备用模型</option><option value="strict" ${node.model_policy==='strict'?'selected':''}>严格锁定主模型（禁止自动降级）</option></select></label>
            <label>推理强度<select class="control" data-pwg-reasoning>${[['auto','跟随执行者'],['none','关闭'],['minimal','极低'],['low','低'],['medium','中'],['high','高'],['xhigh','极高']].map(([id,txt])=>`<option value="${id}" ${(node.reasoning||'auto')===id?'selected':''}>${txt}</option>`).join('')}</select></label>
            <label>节点备用模型链（按 Ctrl/⌘ 多选）<select class="control" data-pwg-fallbacks multiple size="3">${models.map(m=>`<option value="${h(m.id)}" ${nodeFallbacks.has(m.id)?'selected':''}>${h(m.name||m.model)}</option>`).join('')}</select></label>
          </details>
          <label>任务要求<textarea class="control" data-pwg-prompt rows="5" placeholder="此节点需要完成什么，产物需要满足哪些条件？">${h(node.prompt||'')}</textarea></label>
          <label class="pwg-check"><input type="checkbox" data-pwg-review ${node.review_required!==false?'checked':''}>生成产物后需要人工审查</label>
          <label class="pwg-check"><input type="checkbox" data-pwg-manual-review ${node.manual_review_required?'checked':''}>强制人工核查，自动推进模式也需等待确认</label>
          <label class="pwg-check"><input type="checkbox" data-pwg-manual-execution ${node.manual_execution?'checked':''}>本节点由我完成并提交产物</label>
          <label class="pwg-check"><input type="checkbox" data-pwg-claim-audit ${node.claim_audit?'checked':''}>核查稿件结论与 PDF 原文；证据不足时暂停交付</label>
          <fieldset class="pwg-dependencies"><legend>前置节点</legend>${state.layout.nodes.filter(item=>item.id!==node.id).map(item=>{const edge=parents.get(item.id);return `<div data-pwg-edge><label><input type="checkbox" data-pwg-parent="${h(item.id)}" ${edge?'checked':''}>${h(item.label)}</label><div data-pwg-handoff><input class="control" data-pwg-artifact aria-label="上游产物文件名 · ${h(item.label)}" placeholder="产物文件名（可选）" value="${h(edge?.artifact||'')}"><input class="control" data-pwg-argument aria-label="固定工具参数名 · ${h(item.label)}" placeholder="固定工具参数名（可选）" value="${h(edge?.argument||'')}"></div></div>`;}).join('')||'<span class="hint">暂无其他节点</span>'}</fieldset>
          <details><summary>工具与技能范围</summary><label>允许工具 ID<textarea class="control" data-pwg-tools rows="2" placeholder="留空跟随执行者配置">${h((node.tools||[]).join(', '))}</textarea></label><label>允许技能 ID<textarea class="control" data-pwg-skills rows="2" placeholder="留空跟随执行者配置">${h((node.skills||[]).join(', '))}</textarea></label></details>
          <div class="pwg-actions"><button class="btn btn-primary" data-pwg-save>保存节点</button><button class="btn ghost" data-pwg-remove ${state.layout.nodes.length<=1?'disabled':''}>删除节点</button></div>`:`<p class="hint">本次运行使用已固定的节点配置。编辑下一次运行请切换到“节点配置”。</p><h4>任务要求</h4><pre>${h(node.prompt||'跟随研究阶段要求')}</pre><p class="hint">${node.review_required!==false?'人工审查节点':'按运行策略交付'}</p>`}
          <div class="pwg-node-execution"><h4>执行与人工审查</h4>${task?`<p class="hint">${h(task.title)} · ${h(task.status)}${task.latest_run?.executor_snapshot?.model_info?.model?' · 模型 '+h(task.latest_run.executor_snapshot.model_info.model):''}${task.latest_run?.checkpoint?.output_revision!==undefined?' · 修订 v'+h(task.latest_run.checkpoint.output_revision):''}</p>${task.latest_run?.error?`<p class="pw-error">${h(task.latest_run.error)}</p>`:''}${
            (task.outputs||[]).length?`<div class="pwg-artifact-chips">${task.outputs.map((f,idx)=>`<button type="button" class="btn ghost sm" data-pwg-chip-open="${idx}" title="点击在工作台预览或编辑该节点文件">📄 ${h(f.name||(f.path||'').split(/[\\/]/).pop()||'产物 '+(idx+1))}</button>`).join('')}</div>`:''
          }<div class="pwg-inline-doc" data-pwg-inline-doc hidden></div>${task.status==='awaiting_review'?`<div class="pwg-inline-review"><p class="pwg-review-hint">🟠 当前节点等待人工审查：可在下方直接预览编辑产物、通过放行或退回重做。</p><label>人工审查意见<textarea class="control" data-pwg-inline-feedback rows="2" placeholder="通过时可留空；退回重做时请填写修改要求…"></textarea></label><div class="pwg-inline-review-acts"><button type="button" class="btn btn-primary" data-pwg-inline-approve>✓ 通过并交付</button><button type="button" class="btn ghost" data-pwg-inline-reject>↩ 退回重做本节点</button>${options.openExpertChat?`<button type="button" class="btn ghost" data-pwg-open-expert>💬 节点专家对话</button>`:''}</div></div>`:''}${
            state.run?.settings?.manual_pending?.node_id===node.id?`<div class="pwg-inline-review"><h5>✋ 提交人工节点产物</h5><label>正文或结果说明<textarea class="control" data-pwg-manual-text rows="3" placeholder="输入本节点的人工分析结论或文本内容"></textarea></label><label>关联项目文件相对路径（每行一个，可选）<input class="control" data-pwg-manual-paths placeholder="例如：笔记/综述初稿.md"></label><button type="button" class="btn btn-primary" data-pwg-manual-submit>提交人工产物并交付</button></div>`:''
          }<div class="pwg-exec-btns"><button type="button" class="btn" data-pwg-task="${h(task.id)}">查看节点状态</button>${state.mode==='run'&&hasResult?`<button type="button" class="btn btn-primary" data-pwg-result="${h(task.id)}">打开节点产物工作台</button>`:''}${options.openStageResult&&hasResult?`<button type="button" class="btn ghost" data-pwg-stage-result="${h(task.id)}" title="在中间主工作区大屏展开该节点产物精修">⤢ 主区大屏精修</button>`:''}${options.openExpertChat&&task.status!=='awaiting_review'?`<button type="button" class="btn ghost" data-pwg-open-expert>💬 节点专家对话</button>`:''}</div>`:'<p class="hint">此节点尚未准备任务。</p>'}${state.mode==='run'&&!hasResult?'<p class="hint">节点工作台会显示当前状态、前置依赖和可执行操作；产物保存后可在同一处预览、编辑并交付。</p>':''}<button type="button" class="btn ghost" data-pw-node="${h(node.id)}" ${state.mode==='run'?'hidden':''}>按此配置准备任务</button></div>`;
        if(state.mode==='run'&&task&&recoverable(task)){
          const capability=task.recovery;
          el('inspector').querySelector('.pwg-node-execution')?.insertAdjacentHTML('beforeend',`<p class="hint">${h(capability?.description||'保留其他节点成果，只恢复本节点。')}</p>${models.length?`<label>切换其他可用模型并继续（可选）<select class="control" data-pwg-switch-model><option value="">保持原模型重试</option>${models.map(m=>`<option value="${h(m.id)}">${h(m.name||m.model)}${m.health?.status&&m.health.status!=='unknown'?' · '+h(m.health.status):''}</option>`).join('')}</select></label>`:''}<div class="pwg-exec-btns"><button type="button" class="btn btn-primary" data-pwg-retry-node ${capability?.can_continue===false?'disabled':''}>${capability?.mode==='checkpoint'?'从检查点继续':'重试此节点'}</button><button type="button" class="btn ghost" data-pwg-restart-node>重新执行本节点</button></div>`);
          el('retry-node').onclick=()=>resumeRun(task,false,el('switch-model')?.value||'');
          el('restart-node').onclick=()=>resumeRun(task,true,el('switch-model')?.value||'');
        }
        if(state.mode==='config'){
          el('label').closest('label').insertAdjacentHTML('beforebegin',`<label>节点类型<select class="control" data-pwg-kind><option value="executor">专家 / 团队</option><option value="tool">固定工具</option><option value="workflow">子工作流</option></select></label><div data-pwg-tool-fields><label>工具 ID<input class="control" data-pwg-tool-name value="${h(node.tool_name||'')}"></label><label>固定参数（JSON）<textarea class="control" data-pwg-tool-args rows="4">${h(JSON.stringify(node.tool_args||{},null,2))}</textarea></label><p class="hint">使用下方执行者的工具权限，直接调用工具并保存结果。</p></div><label data-pwg-template-fields>子工作流模板<select class="control" data-pwg-template><option value="">请选择模板</option>${state.data.layouts.filter(layout=>layout.id!==state.layout.id).map(layout=>`<option value="${h(layout.id)}">${h(layout.name)}</option>`).join('')}</select><span class="hint">准备运行时固定子节点及其执行者；人工审查保留在子节点。</span></label>`);
          el('kind').value=node.node_kind||'executor';
          if(node.workflow_template_id&&!state.data.layouts.some(layout=>layout.id===node.workflow_template_id))el('template').insertAdjacentHTML('beforeend',`<option value="${h(node.workflow_template_id)}" disabled>原模板已删除，请重新选择</option>`);
          el('template').value=node.workflow_template_id||'';
          const showKind=()=>{const nested=el('kind').value==='workflow';el('tool-fields').hidden=el('kind').value!=='tool';el('template-fields').hidden=!nested;for(const key of ['binding','member','prompt'])el(key).closest('label').hidden=nested;el('tools').closest('details').hidden=nested;};showKind();el('kind').onchange=()=>{state.changed=true;showKind();if(el('kind').value==='workflow')el('step').value='task';};
          const updateHandoffs=()=>host.querySelectorAll('[data-pwg-edge]').forEach(field=>{
            field.querySelector('[data-pwg-handoff]').hidden=!field.querySelector('[data-pwg-parent]').checked;
            field.querySelector('[data-pwg-argument]').hidden=el('kind').value!=='tool';
          });
          const updateEvidenceCheck=()=>{
            const audit=el('claim-audit');
            audit.closest('label').hidden=el('step').value!=='review'||el('manual-execution').checked;
          };
          el('inspector').onchange=()=>{updateHandoffs();updateEvidenceCheck();};
          updateHandoffs();updateEvidenceCheck();
          drawMember(node);el('binding').onchange=()=>{state.changed=true;node.member_id='';drawMember(node);};
          el('inspector').querySelectorAll('input,select,textarea').forEach(input=>input.addEventListener('input',()=>{state.changed=true;refreshSaveTop();}));
          el('save').onclick=state.save;
          el('remove').onclick=async()=>{
            if(state.layout.nodes.length<=1){
              message('工作流至少需要保留一个节点，无法删除唯一节点');
              return;
            }
            const prompt='确定删除节点「'+node.label+'」？系统将自动缝合其前后依赖连接以保持流程连贯。';
            if(!await uiConfirm(prompt)||!alive())return;
            try{
              const res=await api('/api/workflow/layouts/delete-node',{layout:state.layout,node_id:node.id,restitch:true});
              state.layout=res.layout;
              state.selected=state.layout.nodes[0]?.id||'';
              state.changed=true;
              draw();
              message('已删除节点并自动重连前后依赖。');
            }catch(err){
              message('删除节点失败：'+err.message);
            }
          };
        }
        host.querySelector('[data-pw-node]').onclick=async()=>{if(await state.save())options.handoff(node.id);};
        if(node.node_kind==='workflow'){
          host.querySelector('[data-pw-node]').textContent='准备 / 查看组合流程';
          host.querySelector('[data-pw-node]').onclick=async()=>{if(await state.save())await ConnectedResearch.openRun(options.projectId,'',options.chatId);};
        }else if(!state.layout.nodes.some(item=>item.id===node.id))host.querySelector('[data-pw-node]').hidden=true;
        host.querySelector('[data-pwg-task]')?.addEventListener('click',()=>options.openTask(task.id));
        host.querySelector('[data-pwg-result]')?.addEventListener('click',()=>options.openResult(WorkflowNodeWorkbench.availableSelection(task)));
        host.querySelector('[data-pwg-stage-result]')?.addEventListener('click',()=>options.openStageResult(WorkflowNodeWorkbench.availableSelection(task)));
        host.querySelectorAll('[data-pwg-chip-open]').forEach(btn=>btn.onclick=()=>options.openResult({...WorkflowNodeWorkbench.availableSelection(task),artifact_id:'file:'+btn.dataset.pwgChipOpen}));
        host.querySelector('[data-pwg-open-expert]')?.addEventListener('click',()=>options.openExpertChat?.(task,node));
        const inlineDecision=async approved=>{
          if(!task||!state.run)return;
          const fb=(host.querySelector('[data-pwg-inline-feedback]')?.value||'').trim();
          if(!approved&&!fb){message('退回重做前请填写人工审查意见。');return;}
          try{
            await api('/api/project-tasks/review',{id:task.id,project_id:options.projectId,reviewed_run_id:task.latest_run?.id||'',reviewed_output_version:Number(task.latest_run?.checkpoint?.output_revision||0),approved,retry:!approved,feedback:fb,request_key:crypto.randomUUID()});
            if(approved&&state.run.status==='awaiting_review'){
              await api('/api/workflow/execution/resume',{id:state.run.id,task_id:options.projectId}).catch(()=>{});
            }
            const [runs,tasks]=await Promise.all([fetchRun(),fetchTasks()]);
            if(!alive())return;state.run=runs.run;state.tasks=tasks.tasks||[];
            drawGraph();inspector();options.saved?.();
            el('notice').textContent=approved?'已通过人工审查并交付下游。':'已退回节点专家重做。';
          }catch(err){message(err.message);}
        };
        host.querySelector('[data-pwg-inline-approve]')?.addEventListener('click',()=>inlineDecision(true));
        host.querySelector('[data-pwg-inline-reject]')?.addEventListener('click',()=>inlineDecision(false));
        const inlineDoc=host.querySelector('[data-pwg-inline-doc]');
        if(inlineDoc&&task&&hasResult){
          const loadInlineDoc=async()=>{
            try{
              const ws=await api('/api/project-tasks/node-workspace?project_id='+encodeURIComponent(options.projectId)+'&id='+encodeURIComponent(task.id));
              if(!alive()||!inlineDoc.isConnected)return;
              const res=ws.result,art=(res?.artifacts||[]).find(a=>a.id.startsWith('file:'))||(res?.artifacts||[])[0];
              if(!art)return;
              const prevRes=await api('/api/project-tasks/node-workspace-preview?project_id='+encodeURIComponent(options.projectId)+'&id='+encodeURIComponent(task.id)+'&artifact_id='+encodeURIComponent(art.id));
              if(!alive()||!inlineDoc.isConnected)return;
              const textVal=prevRes.preview?.text??(prevRes.preview?.data?JSON.stringify(prevRes.preview.data,null,2):'');
              if(textVal===undefined||textVal===null)return;
              inlineDoc.hidden=false;
              inlineDoc.innerHTML=`<div class="pwg-inline-doc-head"><strong>📄 ${h(art.name||art.id)} · v${h(res.identity.output_revision||0)}</strong><div class="pt-row"><button type="button" class="pw-text-button" data-pwg-doc-tab="preview" aria-pressed="true">预览</button><button type="button" class="pw-text-button" data-pwg-doc-tab="edit" aria-pressed="false" ${res.editable?'':'disabled'}>在线编辑</button></div></div><div class="pwg-inline-doc-preview md" data-pwg-doc-view>${typeof renderMarkdown==='function'?renderMarkdown(textVal):h(textVal)}</div><div data-pwg-doc-edit-wrap hidden><textarea class="control pwg-inline-doc-editor" data-pwg-doc-editor rows="6">${h(textVal)}</textarea><div class="pt-row" style="margin-top:6px;justify-content:flex-end"><input class="control" data-pwg-doc-reason placeholder="修订说明（可选）" style="flex:1;font-size:11px"><button type="button" class="btn btn-primary sm" data-pwg-doc-save>💾 保存产物修订</button></div></div>`;
              const viewBox=inlineDoc.querySelector('[data-pwg-doc-view]'),editWrap=inlineDoc.querySelector('[data-pwg-doc-edit-wrap]');
              inlineDoc.querySelectorAll('[data-pwg-doc-tab]').forEach(tab=>tab.onclick=()=>{
                const isEdit=tab.dataset.pwgDocTab==='edit';
                viewBox.hidden=isEdit;editWrap.hidden=!isEdit;
                inlineDoc.querySelectorAll('[data-pwg-doc-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b===tab)));
              });
              const saveBtn=inlineDoc.querySelector('[data-pwg-doc-save]');
              if(saveBtn)saveBtn.onclick=async()=>{
                const newText=inlineDoc.querySelector('[data-pwg-doc-editor]').value;
                const reason=inlineDoc.querySelector('[data-pwg-doc-reason]').value.trim()||'侧边栏抽屉在线修订';
                saveBtn.disabled=true;
                try{
                  await api('/api/project-tasks/node-command',{project_id:options.projectId,id:task.id,action:art.id==='structured'?'revise-structured':'revise',identity:res.identity,artifact_id:art.id,text:newText,reason,request_key:crypto.randomUUID()});
                  const [runs,tasks]=await Promise.all([fetchRun(),fetchTasks()]);
                  if(!alive())return;state.run=runs.run;state.tasks=tasks.tasks||[];
                  drawGraph();inspector();options.saved?.();
                  el('notice').textContent='已保存为新的节点产物修订版本。';
                }catch(err){message(err.message);saveBtn.disabled=false;}
              };
            }catch{}
          };
          loadInlineDoc();
        }
        host.querySelector('[data-pwg-manual-submit]')?.addEventListener('click',async()=>{
          if(!state.run)return;
          const text=(host.querySelector('[data-pwg-manual-text]')?.value||'').trim();
          const paths=(host.querySelector('[data-pwg-manual-paths]')?.value||'').split(/\r?\n|,/).map(v=>v.trim()).filter(Boolean);
          try{
            const res=await api('/api/workflow/execution/submit-manual',{id:state.run.id,task_id:options.projectId,node_id:node.id,text,paths});
            state.run=res.run||state.run;
            const tData=await fetchTasks();
            if(!alive())return;state.tasks=tData.tasks||[];
            drawGraph();inspector();options.saved?.();
            el('notice').textContent='已提交人工产物，请核查确认。';
          }catch(err){message(err.message);}
        });
      }
      function rebuildChain(order){
        // nodes 数组也要按新顺序排：targets/产物流转都按 nodes 顺序读，只改 order 会让两边打架
        const byId=new Map(state.layout.nodes.map(node=>[node.id,node]));
        state.layout.nodes=order.map(id=>byId.get(id)).filter(Boolean);
        state.layout.order=[...order];
        state.layout.edges=order.slice(0,-1).map((id,index)=>({source:id,target:order[index+1]}));
      }
      function refreshSaveTop(){
        const button=host.querySelector('[data-pwg-savetop]');if(!button)return;
        const active=state.mode==='config'&&state.changed;
        button.disabled=!active;button.classList.toggle('btn-primary',active);
      }
      function dropLine(){
        const wrap=el('graph');let line=wrap.querySelector('.pwg-drop');
        if(!line){line=document.createElement('div');line.className='pwg-drop';line.hidden=true;wrap.appendChild(line);}
        return line;
      }
      function dropTarget(clientY){
        const wrap=el('graph'),box=wrap.getBoundingClientRect();
        const nodes=[...wrap.querySelectorAll('[data-wfl-node]')].map(node=>({id:node.dataset.wflNode,rect:node.getBoundingClientRect()}));
        if(!nodes.length)return null;
        const sorted=nodes.sort((a,b)=>a.rect.top-b.rect.top);
        let index=0;
        for(const node of sorted)if(clientY>node.rect.top+node.rect.height/2)index++;
        const top=index<sorted.length?sorted[index].rect.top:sorted[sorted.length-1].rect.bottom+8;
        return {index,top:Math.round(top-box.top+wrap.scrollTop-1)};
      }
      function onDragMove(event){
        const drag=state.drag;if(!drag)return;
        if(!drag.active){
          if(Math.abs(event.clientY-drag.y)<6&&Math.abs(event.clientX-drag.x)<6)return;
          if(state.mode!=='config'){state.drag=null;return;}
          if(!chainOrder(state.layout)){state.drag=null;message('当前工作流含并行分支，拖拽只支持线性流程；请用右侧“前置节点”调整连线。');return;}
          try{capture();}catch(error){state.drag=null;message(error.message);return;}
          drag.active=true;drag.node?.classList.add('dragging');document.body.style.cursor='grabbing';
        }
        const at=dropTarget(event.clientY),line=dropLine();
        line.hidden=!at;
        if(at){line.style.top=at.top+'px';line.style.left='80px';line.style.right='20px';}
      }
      function onDragUp(event){
        const drag=state.drag;state.drag=null;
        drag?.node?.classList.remove('dragging');document.body.style.cursor='';
        if(!drag)return;
        dropLine().hidden=true;
        if(!drag.active||event.type==='pointercancel')return;
        state.suppressClick=true;setTimeout(()=>{state.suppressClick=false;},0);
        const at=dropTarget(event.clientY),order=chainOrder(state.layout);
        if(!at||!order)return;
        const from=order.indexOf(drag.id),rest=order.filter(id=>id!==drag.id);
        const to=Math.max(0,Math.min(rest.length,at.index>from?at.index-1:at.index));
        rest.splice(to,0,drag.id);
        if(rest.join()===order.join())return;
        rebuildChain(rest);state.changed=true;
        drawGraph();inspector();refreshSaveTop();
        el('notice').textContent='已调整顺序，点“保存到本项目”生效。';
      }
      function reorder(){
        if(!el('graph')?.querySelector('svg'))return;
        if(!state.dragBound){
          state.dragBound=true;
          const move=event=>onDragMove(event),up=event=>onDragUp(event);
          window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);
          state.dragDispose=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);};
        }
        el('graph').querySelectorAll('[data-wfl-node]').forEach(node=>{
          node.addEventListener('pointerdown',event=>{
            if(event.button!==0||state.mode!=='config')return;
            state.drag={id:node.dataset.wflNode,x:event.clientX,y:event.clientY,active:false,node};
          });
        });
      }
      function topoSort(){
        const order=[],pending=new Set(state.layout.nodes.map(n=>n.id));
        for(let pass=0;pending.size&&pass<state.layout.nodes.length;pass++)for(const id of [...pending]){
          if(state.layout.edges.filter(e=>e.target===id).every(e=>order.includes(e.source))){order.push(id);pending.delete(id);}
        }
        state.layout.order=[...order,...pending];
      }
      function defaultBindingId(){
        return ExecutorChoices.unique(state.bindings).find(ExecutorChoices.ready)?.id||'';
      }
      function addNode(step,label,topology='serial'){
        const order=chainOrder(state.layout);
        const id='node-'+crypto.randomUUID().slice(0,8);
        const bindingId=defaultBindingId();
        state.layout.nodes.push({id,step,label:label||'新节点',prompt:'',review_required:true,executor_role_id:id,...(bindingId?{executor_binding_id:bindingId}:{})});
        if(topology==='parallel'&&state.selected){
          const parents=state.layout.edges.filter(e=>e.target===state.selected).map(e=>e.source);
          const children=state.layout.edges.filter(e=>e.source===state.selected).map(e=>e.target);
          parents.forEach(p=>state.layout.edges.push({source:p,target:id}));
          children.forEach(c=>state.layout.edges.push({source:id,target:c}));
          topoSort();
        }else if(topology==='join'){
          const graph=graphData();
          const curNode=graph.nodes.find(n=>n.id===state.selected);
          const siblings=curNode?graph.nodes.filter(n=>n.level===curNode.level).map(n=>n.id):[];
          const sources=siblings.length>1?siblings:state.layout.nodes.filter(n=>n.id!==id&&!state.layout.edges.some(e=>e.source===n.id)).map(n=>n.id);
          const commonChildren=new Set();
          sources.forEach(s=>{
            state.layout.edges.filter(e=>e.source===s).forEach(e=>commonChildren.add(e.target));
          });
          if(commonChildren.size){
            state.layout.edges=state.layout.edges.filter(e=>!(sources.includes(e.source)&&commonChildren.has(e.target)));
            commonChildren.forEach(c=>state.layout.edges.push({source:id,target:c}));
          }
          sources.forEach(s=>state.layout.edges.push({source:s,target:id}));
          topoSort();
        }else if(order&&state.selected&&order.includes(state.selected)){
          const next=[...order];next.splice(next.indexOf(state.selected)+1,0,id);rebuildChain(next);
        }else{
          const tail=state.selected||(state.layout.order||[]).filter(item=>item!==id).at(-1);
          if(tail){
            const outs=state.layout.edges.filter(e=>e.source===tail).map(e=>e.target);
            state.layout.edges=state.layout.edges.filter(e=>e.source!==tail);
            state.layout.edges.push({source:tail,target:id});
            outs.forEach(t=>state.layout.edges.push({source:id,target:t}));
          }
          topoSort();
        }
        state.selected=id;state.changed=true;draw();refreshSaveTop();el('label')?.focus();
      }
      function stepLibrary(){
        const button=el('add');
        if(button)button.onclick=()=>{
          if(state.mode!=='config'){message('请先切到“节点配置”再添加节点。');return;}
          try{capture();}catch(error){message(error.message);return;}
          addNode('task','新节点','serial');
        };
        const menuButton=el('add-menu');if(!menuButton)return;
        menuButton.onclick=()=>{
          if(state.mode!=='config'){message('请先切到“节点配置”再添加节点。');return;}
          try{capture();}catch(error){message(error.message);return;}
          host.querySelector('.pwg-steplib')?.remove();
          const current=state.layout.nodes.find(node=>node.id===state.selected);
          const box=document.createElement('div');box.className='pwg-steplib';
          box.innerHTML=`<p class="pwg-steplib-head">插入到「${h(current?.label||'末尾')}」之后</p>`+
            [{id:'task',name:'通用项目任务'},...state.data.stages].map(step=>`<button type="button" data-pwg-step-pick="${h(step.id)}">${h(step.name)}</button>`).join('');
          menuButton.closest('.pwg-toolbar').appendChild(box);
          box.querySelectorAll('[data-pwg-step-pick]').forEach(item=>item.onclick=()=>{
            const label=item.textContent.trim();box.remove();addNode(item.dataset.pwgStepPick,label);
          });
          const close=event=>{if(!box.contains(event.target)&&!menuButton.contains(event.target)){box.remove();document.removeEventListener('pointerdown',close,true);}};
          document.addEventListener('pointerdown',close,true);
        };
      }
      function drawGraph(){
        const renderedGraph=graphData();
        el('graph').innerHTML=WorkflowGraph.render(renderedGraph,true,Math.max(320,(el('graph')?.clientWidth||880)-32)).replace('role="img"','role="group"');
        const svg=el('graph').querySelector('svg');if(svg)svg.style.zoom=String(state.zoom||1);
        el('graph').querySelectorAll('[data-wfl-chip]').forEach(chip=>{
          let chipTimer=null;
          chip.onclick=event=>{
            event.stopPropagation();
            if(chipTimer)clearTimeout(chipTimer);
            chipTimer=setTimeout(()=>{
              chipTimer=null;
              const task=state.tasks.find(item=>item.id===chip.dataset.wflChip);
              if(task)options.openResult(WorkflowNodeWorkbench.availableSelection(task));
            },200);
          };
          chip.ondblclick=event=>{
            event.stopPropagation();
            if(chipTimer){clearTimeout(chipTimer);chipTimer=null;}
            const task=state.tasks.find(item=>item.id===chip.dataset.wflChip);
            if(task)options.openStageResult(WorkflowNodeWorkbench.availableSelection(task));
          };
        });
        el('graph').querySelectorAll('[data-wfl-node]').forEach(node=>{
          const graphNode=renderedGraph.nodes.find(item=>item.id===node.dataset.wflNode);
          if(graphNode?.result_available){node.classList.add('result-available');node.setAttribute('title','单击查看节点审查与对话，双击进入主区大屏精修');}
          const taskForNode=()=>{
            const nodeId=node.dataset.wflNode;
            const taskId=state.run?.settings?.task_ids?.[nodeId];
            if(taskId){
              const t=state.tasks.find(item=>item.id===taskId);
              if(t)return t;
            }
            return state.tasks.find(item=>item.workflow_node_id===nodeId);
          };
          const selectHighlight=()=>{
            if(state.mode==='config'){
              try{capture();}catch(error){message(error.message);return;}
            }
            const nodeId=node.dataset.wflNode;
            state.selected=nodeId;
            state.inspectorOpen=true;
            el('graph').closest('.pwg-layout')?.classList.add('pwg-inspector-open');
            el('graph').querySelectorAll('[data-wfl-node]').forEach(n=>{
              if(n.dataset.wflNode===nodeId)n.classList.add('selected');
              else n.classList.remove('selected');
            });
            inspector();
            node.scrollIntoView({block:'nearest',inline:'nearest'});
            if(state.mode==='config'){
              state.selectionSave=(state.selectionSave||Promise.resolve()).catch(()=>{}).then(()=>api('/api/workflow/layouts/select',{task_id:options.projectId,layout_id:state.layout.id,node_id:nodeId})).catch(error=>message(error.message));
            }
          };
          let clickTimer=null;
          node.onclick=(event)=>{
            if(state.suppressClick)return;
            selectHighlight();
            if(state.mode!=='run')return;
            if(taskForNode()&&recoverable(taskForNode()))return;
            if(clickTimer)clearTimeout(clickTimer);
            clickTimer=setTimeout(()=>{
              clickTimer=null;
              const task=taskForNode();
              if(task&&WorkflowNodeWorkbench.eligible(task)){
                options.openResult(WorkflowNodeWorkbench.availableSelection(task));
              }else if(task&&options.openExpertChat){
                options.openExpertChat(task,graphNode);
              }
            },220);
          };
          node.ondblclick=(event)=>{
            event.stopPropagation();
            if(clickTimer){clearTimeout(clickTimer);clickTimer=null;}
            const task=taskForNode();
            if(task){
              if(WorkflowNodeWorkbench.eligible(task)){
                options.openStageResult(WorkflowNodeWorkbench.availableSelection(task));
              }else{
                options.openTask(task.id);
              }
            }
          };
          node.onkeydown=event=>{
            if(['Enter',' '].includes(event.key)){
              event.preventDefault();
              selectHighlight();
              const task=taskForNode();
              if(task&&WorkflowNodeWorkbench.eligible(task))options.openResult(WorkflowNodeWorkbench.availableSelection(task));
            }
          };
        });
        reorder();refreshSaveTop();
      }
      function draw(){
         const totalNodes=(state.mode==='run'&&state.run?state.run.layout.nodes:state.layout.nodes).length||1;
          const doneNodes=state.run?Object.values(state.run.settings?.task_ids||{}).filter(tid=>state.tasks.find(t=>t.id===tid)?.status==='delivered').length:0;
         const reviewNodes=state.run?Object.values(state.run.settings?.task_ids||{}).filter(tid=>state.tasks.find(t=>t.id===tid)?.status==='awaiting_review').length:0;
         const pct=state.run?Math.min(100,Math.round((doneNodes/totalNodes)*100)):0;
         const shortId=state.run?String(state.run.id).slice(0,12):String(state.layout.id||'').slice(0,12);

         const scopedRuns=options.scopedRuns;
         let titleHtml='';
         if(Array.isArray(scopedRuns)&&options.contextual&&!options.allowTemplate&&(!options.focus?.runId||scopedRuns.some(r=>r.id===options.focus.runId))){
           if(scopedRuns.length===0){
             titleHtml=`<span class="pwg-scoped-title hint" style="font-size:12px;color:var(--muted)">当前对话暂无工作流运行</span>`;
           }else if(scopedRuns.length===1){
             titleHtml=`<span class="pwg-scoped-title" title="${h(scopedRuns[0].id)}" style="font-size:12.5px;font-weight:600"><b>${h(scopedRuns[0].layout_name||state.run?.layout?.name||'当前工作流')}</b></span>`;
           }else{
             titleHtml=`<select class="control pwg-flow-select" id="pwWorkflowChoice" aria-label="当前对话的工作流">${scopedRuns.map(r=>`<option value="${h(r.id)}" ${r.id===(state.run?.id||scopedRuns[0].id)?'selected':''}>${h(r.layout_name||r.id)}</option>`).join('')}</select>`;
           }
         }else{
           titleHtml=`<select class="control pwg-flow-select" id="pwWorkflowChoice" aria-label="当前工作流">${state.data.layouts.map(layout=>`<option value="${h(layout.id)}" ${layout.id===state.layout.id?'selected':''}>${h(layout.name)}</option>`).join('')}</select>`;
         }

         host.innerHTML=`<section class="project-workflow-graph">
           <header class="pwg-evoflow-head">
             <div class="pwg-evoflow-title">
               <i class="pwg-evoflow-dot ${state.run?.status||'ready'}"></i>
               ${titleHtml}
             </div>
             <div class="pwg-evoflow-metrics">
               <span class="pwg-run-progress" title="工作流运行进度">↻ ${pct}% &nbsp; <b>${doneNodes}/${totalNodes}</b>${reviewNodes?` &nbsp; <b style="color:#d97706">${reviewNodes}待审</b>`:''}</span>
              ${canResume()?'<button type="button" class="btn btn-primary sm" data-pwg-resume>从检查点继续</button>':''}
               <button class="btn ghost sm" type="button" id="pwOpenWorkflowStudio" title="打开独立工作流工作台，管理模板与全屏编排">⤢ 工作台</button>
               <button class="btn ghost sm" type="button" data-pwg-toggle-toolbar title="展开或收起高级编排工具栏">⚙ 编排</button>
               <button class="btn ghost sm" type="button" data-pwg-toggle-inspector title="展开或收起底部抽屉面板">📋 详情</button>
               <button class="btn btn-primary sm" id="pwWorkflowRun" ${state.mode==='run'&&state.run?'hidden':''}>准备运行</button>
               <button class="btn btn-primary sm" data-pwg-quick-result hidden>打开产物工作台</button>
             </div>
           </header>
            <div class="pwg-toolbar ${(state.toolbarOpen??true)?'pwg-toolbar-open':''}">
             <div class="pwg-mode">
               <button type="button" class="pw-text-button" data-pwg-mode="config" aria-pressed="${state.mode==='config'}">编排组合 · v${state.layout.revision||0}</button>
               <button type="button" class="pw-text-button" data-pwg-mode="run" aria-pressed="${state.mode==='run'}" ${state.run?'':'disabled'}>运行视图</button>
             </div>
             <span class="pw-spacer"></span>
             <button class="btn ghost sm" data-pwg-add>添加节点</button>
             <button class="btn ghost sm" type="button" data-pwg-add-menu title="按科研阶段模板插入节点">▾ 阶段</button>
             <button class="btn ghost sm" type="button" data-pwg-add-parallel title="同层并排添加并行分支">＋并行</button>
             <button class="btn ghost sm" type="button" data-pwg-add-join title="添加汇合节点汇聚并行分支">＋汇合</button>
             <button class="btn ghost sm" id="pwRoles">角色绑定</button>
             <button class="btn sm" data-pwg-savetop disabled>保存到本项目</button>
             <button class="btn ghost sm" data-pwg-publish>发布模板</button>
             ${state.run&&options.onDeleteRun?`<button class="btn ghost sm" type="button" id="pwDeleteRun" style="color:var(--err)" title="删除当前工作流运行记录">删除运行</button>`:''}
           </div>
           <p class="pwg-notice" data-pwg-notice role="status"></p><p class="pw-error" data-pwg-error role="alert"></p>
            <div class="pwg-layout ${(state.inspectorOpen??true)?'pwg-inspector-open':''}"><div class="pwg-canvas" data-pwg-graph></div><aside class="pwg-inspector" data-pwg-inspector aria-label="所选工作流节点"></aside></div>
         </section>`;
        drawGraph();inspector();refreshHeader();
        el('resume')?.addEventListener('click',()=>resumeRun());
        host.querySelector('[data-pwg-toggle-inspector]')?.addEventListener('click',()=>{
          state.inspectorOpen=host.querySelector('.pwg-layout')?.classList.toggle('pwg-inspector-open');
        });
        host.querySelector('[data-pwg-add-parallel]')?.addEventListener('click',()=>{
          if(state.mode!=='config'){message('请先切到“DAG 编排组合”再添加并行分支。');return;}
          try{capture();}catch(error){message(error.message);return;}
          const cur=state.layout.nodes.find(n=>n.id===state.selected);
          addNode(cur?.step||'task',(cur?.label||'并行任务')+' (并行)','parallel');
        });
        host.querySelector('[data-pwg-add-join]')?.addEventListener('click',()=>{
          if(state.mode!=='config'){message('请先切到“DAG 编排组合”再添加汇合节点。');return;}
          try{capture();}catch(error){message(error.message);return;}
          addNode('task','汇合汇编节点','join');
        });
        el('graph').closest('.pwg-layout').insertAdjacentHTML('beforebegin','<div class="pwg-zoom"><button type="button" class="pw-text-button" data-pwg-zoom="out">− 缩小</button><button type="button" class="pw-text-button" data-pwg-zoom="fit">适应画布</button><button type="button" class="pw-text-button" data-pwg-zoom="in">＋ 放大</button></div>');
        host.querySelector('.pwg-zoom').insertAdjacentHTML('beforeend','<div class="pwg-search"><input class="control" type="search" data-pwg-search aria-label="搜索工作流节点" placeholder="搜索节点名称、执行者或 ID"><div data-pwg-search-results hidden></div></div>');
        el('search').oninput=()=>{
          const query=el('search').value.trim().toLocaleLowerCase(),results=el('search-results');results.hidden=!query;
          const matches=graphData().nodes.filter(node=>[node.label,node.id,state.bindings.find(binding=>binding.id===node.executor_binding_id)?.display_name].join(' ').toLocaleLowerCase().includes(query));
          results.innerHTML=matches.map(node=>`<button type="button" data-pwg-match="${h(node.id)}">${h(node.label)} <small>${h(node.id)}</small></button>`).join('')||'<p role="status">没有匹配的节点</p>';
          results.querySelectorAll('[data-pwg-match]').forEach(button=>button.onclick=()=>{
            const id=button.dataset.pwgMatch;el('graph').querySelector(`[data-wfl-node="${CSS.escape(id)}"]`)?.onclick();
            const node=el('graph').querySelector(`[data-wfl-node="${CSS.escape(id)}"]`);node?.scrollIntoView({block:'nearest',inline:'center'});node?.focus({preventScroll:true});
            results.hidden=true;el('search').value='';
          });
        };
        el('search').onkeydown=event=>{if(event.key==='Escape'){event.stopPropagation();el('search').value='';el('search-results').hidden=true;}else if(event.key==='ArrowDown'){event.preventDefault();el('search-results').querySelector('button')?.focus();}else if(event.key==='Enter'){event.preventDefault();el('search-results').querySelector('button')?.click();}};
        host.querySelectorAll('[data-pwg-zoom]').forEach(button=>button.onclick=()=>{const svg=el('graph').querySelector('svg'),mode=button.dataset.pwgZoom;state.zoom=mode==='fit'?Math.min(1,Math.max(.25,(el('graph').clientWidth-40)/(svg?.width.baseVal.value||500))):Math.max(.25,Math.min(2,(state.zoom||1)+(mode==='in'?.15:-.15)));drawGraph();});
        const flowChoice = host.querySelector('#pwWorkflowChoice');
        if (flowChoice) {
          flowChoice.onchange = async event => {
            const val = event.target.value;
            if (options.contextual && !options.allowTemplate && (!options.focus?.runId || options.scopedRuns?.some?.(r => r.id === options.focus.runId)) && Array.isArray(options.scopedRuns)) {
              if (options.openArtifacts) options.openArtifacts(val);
              return;
            }
            if (!await state.save()) { flowChoice.value = state.layout.id; return; }
            if (!alive()) return;
            try {
              await api('/api/workflow/layouts/select', { task_id: options.projectId, layout_id: val });
              await options.reload();
            } catch (error) {
              flowChoice.value = state.layout.id;
              message(error.message);
            }
          };
        }
        host.querySelector('#pwOpenWorkflowStudio')?.addEventListener('click', () => {
          if (typeof openTab === 'function') openTab('workflow-studio');
        });
        host.querySelector('#pwDeleteRun')?.addEventListener('click', async () => {
          if (!state.run) return;
          if (!await uiConfirm('确定删除当前工作流运行记录？该运行下的所有任务和产物状态将一并清理。')) return;
          await options.onDeleteRun?.(state.run.id);
        });
        host.querySelector('#pwRoles').onclick=async()=>{if(!await state.save())return;const dialog=await ProjectTasks.openRoles(options.projectId,state.layout.id);dialog?.addEventListener('close',options.reload,{once:true});};
        host.querySelector('#pwWorkflowRun').onclick=async()=>{if(await state.save()){await ConnectedResearch.openRun(options.projectId,'',options.chatId);document.querySelector('dialog.connected-dialog')?.addEventListener('close',()=>{if(alive())options.reload();},{once:true});}};
        el('publish').onclick=async()=>{
          if(!await state.save())return;
          if(!await uiConfirm('将当前配置发布为所有项目可调用的模板？当前项目将跟随模板，已有运行保持原版本。'))return;
          try{await api('/api/workflow/layouts/publish',{project_id:options.projectId,layout_id:state.layout.id,expected_fingerprint:state.layout.fingerprint,expected_template_updated_at:state.layout.updated_at});await options.reload();}catch(error){message(error.message);}
        };
        stepLibrary();
        host.querySelector('[data-pwg-savetop]').onclick=()=>state.save();
        if(state.run&&options.openArtifacts){
          const toolbar=host.querySelector('.pwg-toolbar');
          toolbar.insertAdjacentHTML('beforeend','<button type="button" class="btn ghost" data-pwg-artifacts>浏览本次运行产物</button>');
          el('artifacts').onclick=()=>options.openArtifacts(state.run.id);
        }
        host.querySelectorAll('[data-pwg-mode]').forEach(button=>button.onclick=async()=>{if(!await state.save())return;state.mode=button.dataset.pwgMode;state.selected=(state.mode==='run'?state.run.layout:state.layout).nodes[0]?.id;draw();});
        const toggleTb = host.querySelector('[data-pwg-toggle-toolbar]');
        if(toggleTb){
          toggleTb.onclick=()=>{
            const tb = host.querySelector('.pwg-toolbar');
            if(tb)state.toolbarOpen=tb.classList.toggle('pwg-toolbar-open');
          };
        }
      }
      draw();
      let refreshing=false;
      const timer=setInterval(async()=>{
        if(!alive()){clearInterval(timer);return;}if(refreshing||document.hidden)return;refreshing=true;
          try{const [runs,tasks]=await Promise.all([fetchRun(),fetchTasks()]);
          if(!alive())return;const changed=JSON.stringify([state.run,state.tasks])!==JSON.stringify([runs.run,tasks.tasks||[]]);state.run=runs.run;state.tasks=tasks.tasks||[];
          if(changed&&state.mode==='run'){drawGraph();inspector();refreshHeader();}
        }catch(error){if(alive())message('运行状态读取失败：'+error.message);}finally{refreshing=false;}
      },2500);
      // 整行卡宽度跟着画布走：窗口缩放要重绘，否则卡片还停在旧宽度上
      let resizeTimer=null;
      const onResize=()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{const top=el('graph')?.scrollTop||0;drawGraph();const box=el('graph');if(box)box.scrollTop=top;},180);};
      window.addEventListener('resize',onResize);
      host.__graphDispose=()=>{if(host.__graphState===state)host.__graphState=null;clearInterval(timer);clearTimeout(resizeTimer);window.removeEventListener('resize',onResize);state.dragDispose?.();};
    }catch(error){if(alive())host.innerHTML=`<p class="pw-error">工作流读取失败：${h(error.message)}</p>`;}
  }
  const graphEditor={mount,dirty,flush,chainOrder};window.ProjectWorkflowGraph=graphEditor;return graphEditor;
})();
