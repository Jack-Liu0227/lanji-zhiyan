/* Plans describe goals; this board projects actual runtime state. */
const TaskCenter=(()=>{
  const h=value=>esc(String(value??''));
  const labels={draft:'已准备',ready:'待执行',queued:'排队中',running:'进行中',waiting_input:'等待输入',awaiting_review:'待核查',delivered:'已交付',completed:'已完成',blocked:'等待处理',failed:'失败',cancelled:'已停止',interrupted:'中断待恢复'};
  const activities={not_started:'尚未启动',conversation_running:'对话中的专家正在执行',organize:'任务信息已更新',delete:'已移入最近删除',restore:'任务已恢复',created:'任务已准备',queued:'任务已排队',claimed:'开始执行',running:'进行中',output_ready:'产物已生成，等待核查',delivered:'已交付',reviewed:'核查已记录',approved:'核查通过',rejected:'已退回修改',failed:'执行失败',cancelled:'已停止',interrupted:'运行中断',blocked:'等待前置条件',model_request:'请求模型',model_response:'模型已返回',tool_call:'调用工具',tool_result:'工具已返回',error:'执行失败',board_delete:'已从看板删除',board_restore:'已恢复到看板',done:'运行结束',run_started:'开始运行',plan_linked:'已关联研究计划',mode_changed:'执行模式已更新'};
  const activity=value=>value==='start_blocked'?'启动未成功':activities[value]||value;
  const planLabels={TODO:'待安排',IN_PROGRESS:'进行中',REVIEW:'待验收',DONE:'已完成'};
  const planTones={TODO:'sky',IN_PROGRESS:'violet',REVIEW:'amber',DONE:'green'};
  const lanes=[
    {id:'pending',label:'待执行',tone:'sky',statuses:['draft','ready','queued']},
    {id:'running',label:'进行中',tone:'violet',statuses:['running']},
    {id:'review',label:'待确认',tone:'amber',statuses:['awaiting_review','waiting_input']},
    {id:'done',label:'已完成',tone:'green',statuses:['delivered','completed']},
    {id:'attention',label:'需处理',tone:'rose',statuses:['blocked','failed','interrupted'],optional:true},
    {id:'stopped',label:'已停止',tone:'slate',statuses:['cancelled'],optional:true}
  ];
  const tone=status=>planTones[status]||lanes.find(lane=>lane.statuses.includes(status))?.tone||'slate';
  const views=new Map();let mounted=null,globalProject='',globalView='board';
  const owner=row=>row.project_id||row.task_id;
  function requestView(view){globalView=view;}
  function renderGlobal(container){if(globalProject&&!S.tasks.some(row=>row.id===globalProject))globalProject='';return render(container,{global:true,projectId:globalProject,view:globalView,live:()=>route()==='task-center'});}
  function dispose(){mounted?.source?.close();clearInterval(mounted?.fallback);clearInterval(mounted?.guard);if(mounted?.onVisible)document.removeEventListener('visibilitychange',mounted.onVisible);if(mounted?.todoChanged)window.removeEventListener('todos-changed',mounted.todoChanged);if(mounted?.menuClosed)window.removeEventListener('rw:context-menu-closed',mounted.menuClosed);mounted=null;}
  async function render(container,options={}){
    dispose();const project=options.projectId||'';if(!project&&!options.global)throw new Error('请先选择项目');
    const scope=project?'project_id='+encodeURIComponent(project):'all_projects=1';
    const live=()=>mounted===state&&container.isConnected&&(options.live?options.live():route()==='workspace'&&curTask===project);
    const state={view:options.view==='reviews'?'board':options.view==='tasks'?'board':options.view||views.get(project)||'plans',filter:options.view==='reviews'?'awaiting_review':options.filter||'',query:'',projects:S.tasks||[],items:[],plans:[],selected:'',cursor:0,source:null,guard:null,fallback:null,loading:false,version:0,layout:'list',planHistory:false,boardLayout:'status',laneFilter:'',trash:false};mounted=state;
    if(lanes.some(lane=>lane.id===state.filter)){state.laneFilter=state.filter;state.filter='';}
    container.innerHTML=`<section class="execution-center ec-workspace"><header class="ec-heading"><div><span class="pw-eyebrow">${options.global?'全局研究与执行':'项目研究与执行'}</span><h2>任务与自动化</h2><p class="hint">安排研究目标，跟踪实际执行，管理持续触发。</p></div><div class="ec-head-actions">${options.global?'<label class="ec-project-scope">项目范围<select class="control" data-ec-project aria-label="筛选项目"></select></label>':''}<span class="ec-connection" data-ec-connection role="status">正在连接运行时</span><button type="button" class="pw-text-button" data-ec-refresh>刷新</button></div></header><nav class="ec-tabs" aria-label="任务查看方式">${[['plans','To-do 计划'],['board','实时任务看板'],['automation','自动化规则']].map(([id,label])=>`<button type="button" data-ec-view="${id}" aria-pressed="${state.view===id}">${label}</button>`).join('')}</nav><div class="ec-filters" data-ec-filters><input type="search" class="control" data-ec-search placeholder="搜索研究目标或执行任务" aria-label="搜索研究任务"><select class="control" data-ec-status aria-label="任务状态"></select><select class="control" data-ec-layout aria-label="计划查看方式"><option value="list">列表</option><option value="kanban">看板</option><option value="timeline">时间线</option></select><button type="button" class="pw-text-button" data-ec-trash>最近删除</button><button type="button" class="btn" data-ec-new></button></div><div class="ec-kpi-grid" data-ec-summary role="group" aria-label="任务状态概览"></div><p class="hint" data-ec-total></p><p class="pw-error" data-ec-error role="alert"></p><div data-ec-content></div></section>`;
    const el=name=>container.querySelector('[data-ec-'+name+']');
    function projectOptions(){return state.projects.map(row=>`<option value="${h(row.id)}">${h(row.title)}</option>`).join('');}
    function paintProjects(){if(!options.global)return;const html='<option value="">所有项目</option>'+projectOptions();if(state.projectOptions===html)return;state.projectOptions=html;el('project').innerHTML=html;el('project').value=project;}
    paintProjects();
    if(options.global)el('project').onchange=()=>{globalProject=el('project').value;globalView=state.view;renderGlobal(container);};
    async function chooseProject(){
      if(project)return project;
      if(!state.projects.length){uiNotify('请先在项目工作区新建项目');return '';}
      return new Promise(resolve=>{
        const dialog=document.createElement('dialog');dialog.className='pt-dialog ec-project-dialog';dialog.setAttribute('aria-label','选择任务所属项目');
        dialog.innerHTML=`<form><h3>选择所属项目</h3><p class="hint">任务、对话和产物将保存到所选项目。</p><label>项目<select class="control" name="project_id" required><option value="">请选择项目</option>${projectOptions()}</select></label><footer><button class="btn ghost" type="button">取消</button><button class="btn" type="submit">继续</button></footer></form>`;
        document.body.append(dialog);dialog.querySelector('[type=button]').onclick=()=>dialog.close();
        dialog.querySelector('form').onsubmit=event=>{event.preventDefault();dialog.close(event.currentTarget.elements.project_id.value);};
        dialog.addEventListener('close',()=>{resolve(dialog.returnValue||'');dialog.remove();el('new')?.focus();},{once:true});dialog.showModal();
      });
    }
    async function createTask(){const target=await chooseProject();if(target)await ProjectTasks.compose(target,{});}
    const projectLabel=row=>options.global?`<span class="ec-project-label" title="所属项目">${h(row.project_name||state.projects.find(p=>p.id===owner(row))?.title||'未命名项目')}</span>`:'';
    el('trash').onclick=()=>{state.trash=!state.trash;state.filter='';state.laneFilter='';paint();};
    state.live=live;state.newPlan=async defaults=>{if(state.view!=='plans')await switchView('plans');editPlan(null,defaults);};state.openPlan=id=>{const plan=state.plans.find(row=>row.id===id);if(!plan)throw new Error('关联计划已删除');editPlan(plan);};
    function error(message){if(live())el('error').textContent=message||'';}
    const pendingPlan=plan=>plan.status==='TODO'&&!(plan.tasks||[]).length;
    function paint(){
      if(!live()||state.view==='automation')return;
      if(container.querySelector('.context-active')){state.pendingPaint=true;return;}
      state.pendingPaint=false;
      const isPlan=state.view==='plans',dict=isPlan?planLabels:labels;el('trash').hidden=isPlan;el('trash').textContent=state.trash?'返回任务':'最近删除';
      el('status').innerHTML=isPlan?'<option value="pending">待安排</option><option value="history">已派发与历史</option>':'<option value="">所有状态</option>'+Object.entries(dict).map(([id,label])=>`<option value="${id}">${label}</option>`).join('');el('status').value=isPlan?(state.planHistory?'history':'pending'):state.filter;
      el('status').setAttribute('aria-label',isPlan?'计划范围':'任务状态');
      el('layout').innerHTML=isPlan?'<option value=list>列表</option><option value=timeline>按截止日期</option>':'<option value=status>按状态分列</option><option value=workflow>按协作分组</option>';el('layout').value=isPlan?state.layout:state.boardLayout;el('layout').setAttribute('aria-label',isPlan?'计划查看方式':'执行看板分组');el('new').textContent=isPlan?'＋ 新建计划':'＋ 执行任务';
      const rows=(isPlan?state.plans:state.items).filter(row=>(isPlan||!!row.deleted_at===state.trash)&&(isPlan?(state.planHistory?!pendingPlan(row):pendingPlan(row)):!state.filter||row.status===state.filter)&&(!state.laneFilter||lanes.find(lane=>lane.id===state.laneFilter)?.statuses.includes(row.status))&&(!state.query||(row.title+' '+(row.description||'')+' '+(row.acceptance||'')+' '+(row.activity||'')+' '+(row.assistant_name||'')+' '+(row.project_name||state.projects.find(p=>p.id===owner(row))?.title||'')).toLowerCase().includes(state.query)));
      el('total').textContent=isPlan?`${rows.length} 条${state.planHistory?'已派发或历史计划':'待安排计划'} · 执行与验收状态请在实时任务看板查看`:`${rows.length} 项执行 · ${state.items.filter(row=>!row.deleted_at&&row.status==='running').length} 项运行中 · 状态由运行时更新`;
      const summary=isPlan?[{id:'pending',label:'待安排',tone:'sky',count:state.plans.filter(pendingPlan).length},{id:'history',label:'已派发与历史',tone:'slate',count:state.plans.filter(plan=>!pendingPlan(plan)).length}]:lanes.map(lane=>({...lane,count:state.items.filter(item=>!!item.deleted_at===state.trash&&lane.statuses.includes(item.status)).length})).filter(lane=>!lane.optional||lane.count);
      el('summary').hidden=isPlan;el('summary').style.setProperty('--ec-kpi-count',summary.length);el('summary').innerHTML=summary.map(item=>`<div class="ec-kpi-wrap"><button type="button" class="ec-kpi" data-ec-summary-filter="${item.id}" data-tone="${item.tone}" aria-pressed="${isPlan?(state.planHistory?'history':'pending')===item.id:state.laneFilter===item.id}"><span class="ec-kpi-label"><i aria-hidden="true"></i>${h(item.label)}</span><strong>${item.count}</strong><span class="ec-kpi-caption">${isPlan?'研究计划':'实际任务'}</span></button>${!isPlan&&!state.trash?`<button type="button" class="ec-kpi-clear pw-text-button" data-ec-clear="${item.id}" aria-label="清理${h(item.label)}记录" title="清理本列记录" ${item.count?'':'disabled'}>清理</button>`:''}</div>`).join('');
      el('summary').querySelectorAll('[data-ec-summary-filter]').forEach(button=>button.onclick=()=>{const id=button.dataset.ecSummaryFilter;if(isPlan)state.planHistory=id==='history';else{state.filter='';state.laneFilter=state.laneFilter===id?'':id;}paint();});
      if(isPlan){
        if(state.layout==='timeline')rows.sort((a,b)=>(a.due_date||'9999').localeCompare(b.due_date||'9999')||a.priority.localeCompare(b.priority));
        el('content').innerHTML=`<div class="ec-plans ec-plan-${state.layout}">${rows.map(planCard).join('')}</div>`;
        el('content').querySelectorAll('[data-plan-id]').forEach(card=>{const plan=state.plans.find(row=>row.id===card.dataset.planId);card.__ecActions=[{text:'编辑研究计划…',run:()=>editPlan(plan)},{text:'优先级…',run:()=>editPlan(plan,{},'priority')},{text:'截止日期…',run:()=>editPlan(plan,{},'due_date')},{separator:true},{text:'交给 Agent / 团队',run:()=>ProjectTasks.compose(owner(plan),{plan_id:plan.id,title:plan.title,goal:plan.description||plan.title})},{text:plan.status==='DONE'?'恢复为待安排':'归档计划',disabled:!!plan.stage||!!(plan.tasks||[]).length,run:async()=>{await api('/api/plans/save',{project_id:owner(plan),id:plan.id,status:plan.status==='DONE'?'TODO':'DONE'});window.dispatchEvent(new Event('todos-changed'));await load();}},{text:'复制计划内容',run:()=>TaskActions.copy(plan.title+'\n'+(plan.description||''))},{separator:true},{text:'删除计划…',danger:true,run:()=>removePlan(plan)}];});
        if(!rows.length)el('content').innerHTML='<div class="pw-empty"><h3>没有待安排的计划</h3><p>可新建研究目标。已派发的任务请到实时任务看板跟踪，原计划保留在“已派发与历史”中。</p></div>';
        el('content').querySelectorAll('[data-plan-edit]').forEach(button=>button.onclick=()=>editPlan(state.plans.find(row=>row.id===button.dataset.planEdit)));
        el('content').querySelectorAll('[data-plan-dispatch]').forEach(button=>button.onclick=()=>{const plan=state.plans.find(row=>row.id===button.dataset.planDispatch);ProjectTasks.compose(owner(plan),{plan_id:plan.id,title:plan.title,goal:plan.description||plan.title});});
        el('content').querySelectorAll('[data-plan-task]').forEach(button=>button.onclick=()=>ProjectWorkspace.openTask(button.dataset.planTask,owner(state.plans.find(row=>row.id===button.closest('[data-plan-id]').dataset.planId))));
        return;
      }
      if(!rows.some(row=>row.id===state.selected))state.selected='';
      const shownLanes=lanes.filter(lane=>state.laneFilter?lane.id===state.laneFilter:state.filter?lane.statuses.includes(state.filter):!lane.optional||rows.some(row=>lane.statuses.includes(row.status)));
      let board;
      if(state.boardLayout==='workflow'){
        const grouped=new Map();for(const row of rows){const key=row.workflow_run_id||row.parent_task_id||'standalone';if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(row);}
        board=[...grouped].map(([key,items])=>`<section class="ec-run-group"><h3>${key==='standalone'?'独立任务与对话':key.startsWith('wrun')?'LangGraph 工作流':'团队协作'}<span class="ec-count">${items.length}</span></h3><div class="ec-run-grid">${items.map(runCard).join('')}</div></section>`).join('')||'<div class="pw-empty"><h3>还没有执行记录</h3><p>从计划派发、对话执行或自动化触发后，在这里跟踪进度。</p></div>';
      }else{
        board=`<div class="ec-lanes" data-lane-count="${shownLanes.length}">${shownLanes.map(lane=>{const tasks=rows.filter(row=>lane.statuses.includes(row.status));return `<section class="ec-status-lane" data-ec-lane="${lane.id}" data-tone="${lane.tone}"><header class="ec-lane-head"><h3><span class="ec-lane-dot" aria-hidden="true"></span>${lane.label}</h3><div class="ec-lane-actions"><span class="ec-count">${tasks.length}</span>${!state.trash?`<button type="button" class="pw-text-button" data-ec-clear="${lane.id}" aria-label="清理${h(lane.label)}本列记录" ${tasks.length?'':'disabled'}>清理本列</button>`:''}</div></header><div class="ec-lane-body">${tasks.map(runCard).join('')||'<p class="ec-lane-empty">暂无'+lane.label+'的任务</p>'}</div></section>`;}).join('')}</div>`;
      }
      el('content').innerHTML=`<div class="ec-live-layout"><div class="ec-live-groups">${board}</div><aside class="ec-run-inspector" data-ec-inspector ${state.selected&&state.inspectorOpen?'':'hidden'} aria-label="执行详情"></aside></div>`;
      el('content').querySelectorAll('[data-ec-task]').forEach(card=>{
        const row=state.items.find(item=>item.id===card.dataset.ecTask);
        const open=async()=>{if(state.opening)return;state.opening=true;try{if(row.deleted_at){state.selected=row.id;await inspect();}else if(row.channel==='toolbox')await PaperToolbox.openResult(row.run_id,row.project_id);else await ProjectWorkspace.openConversation(row.chat_id,row.project_id);}catch(err){error(err.message);}finally{state.opening=false;}};
        card.onclick=event=>{if(event.target.closest('button,a,input,select'))return;state.selected=row.id;container.querySelectorAll('[data-ec-task]').forEach(item=>{item.classList.toggle('is-selected',item===card);item.setAttribute('aria-pressed',String(item===card));});if(row.deleted_at)inspect();};
        card.ondblclick=event=>{if(!event.target.closest('button,a,input,select'))open();};
        card.onkeydown=event=>{if(event.target===card&&['Enter',' '].includes(event.key)){event.preventDefault();open();}};
        card.querySelector('[data-ec-open-chat]')?.addEventListener('click',event=>{event.stopPropagation();open();});
      });
      el('content').querySelectorAll('[data-ec-task]').forEach(card=>{const row=state.items.find(item=>item.id===card.dataset.ecTask),actions=TaskActions.actions(row,load),detail={text:'查看执行详情',run:()=>{state.selected=card.dataset.ecTask;state.inspectorOpen=true;inspect();}};card.__ecActions=row.deleted_at?[detail,...actions]:[actions[0],detail,...actions.slice(1)];});
      container.querySelectorAll('[data-ec-clear]').forEach(button=>button.onclick=async()=>{
        const lane=lanes.find(item=>item.id===button.dataset.ecClear),items=state.items.filter(row=>!row.deleted_at&&lane.statuses.includes(row.status));
        button.disabled=true;try{await TaskActions.visibility(items,load,'delete',lane.label);}catch(err){error(err.message);}finally{if(button.isConnected)button.disabled=!items.length;}
      });
      if(state.selected&&state.inspectorOpen)inspect();
    }
    function planCard(plan){return `<article class="ec-plan-card" data-plan-id="${h(plan.id)}" data-status="${h(plan.status)}" data-tone="${tone(plan.status)}"><div class="ec-plan-title"><span class="ec-priority">${h(plan.priority)}</span><strong>${h(plan.title)}</strong><span class="ec-status" data-tone="${tone(plan.status)}">${pendingPlan(plan)?'待安排':plan.tasks?.length?'已派发':'历史计划'}</span></div><p>${h(plan.description||'尚未补充研究要求')}</p>${plan.acceptance?`<small>验收：${h(plan.acceptance)}</small>`:''}<div class="ec-plan-meta">${projectLabel(plan)}<time>${h(plan.due_date||'未设置截止日期')}</time><span>${plan.tasks?.length||0} 项关联执行</span></div><div class="ec-plan-actions"><button type="button" class="pw-text-button" data-plan-edit="${h(plan.id)}">编辑计划</button><button type="button" class="btn ghost" data-plan-dispatch="${h(plan.id)}">交给 Agent / 团队</button></div>${(plan.tasks||[]).map(task=>`<button class="ec-plan-linked" type="button" data-plan-task="${h(task.id)}">${h(task.title)} <span>${labels[task.status]}</span></button>`).join('')}</article>`;}
    function runCard(row){
      const kind=row.execution_kind==='workflow'?'工作流':row.workflow_node_id?'流程节点':row.channel==='toolbox'?'论文工具':row.kind==='conversation'?'对话运行':row.executor_kind==='team'?'团队协作':'专家任务';
      return `<article role="button" tabindex="0" class="ec-run-card ${state.selected===row.id?'is-selected':''}" data-ec-task="${h(row.id)}" data-ec-owner="${h(row.project_id)}" data-status="${h(row.status)}" data-tone="${tone(row.status)}" aria-pressed="${state.selected===row.id}" title="双击查看执行对话；Enter 键也可打开"><div class="ec-run-title"><strong class="ec-card-title">${row.pinned?'<span class="ec-pin" title="已置顶">置顶</span>':''}${h(row.title)}</strong><span class="ec-status" data-tone="${tone(row.status)}"><i aria-hidden="true"></i>${labels[row.status]||h(row.status)}</span></div>${projectLabel(row)}<p class="ec-card-activity">${h(activity(row.activity)||'等待运行事件')}</p><div class="ec-run-actor"><span class="ec-actor-mark" aria-hidden="true">${h((row.assistant_name||'助').slice(0,1))}</span><span>${h(row.assistant_name||'等待执行者')}</span>${row.attempt?'<span class="ec-attempt">第 '+row.attempt+' 次</span>':''}</div><footer><span class="ec-card-type">${kind}${row.priority?' · '+h(row.priority):''}</span>${row.due_date?`<span>截止 ${h(row.due_date)}</span>`:''}<time>${h(fmtDate(row.updated_at))}</time>${!row.deleted_at?`<button type="button" class="pw-text-button ec-open-chat" data-ec-open-chat aria-label="打开 ${h(row.title)} 的执行对话">${row.channel==='toolbox'?'查看结果':'打开对话'} ↗</button>`:''}</footer>${row.error?`<small class="ec-card-error">${h(row.error)}</small>`:''}</article>`;
    }
    async function inspect(){
      const row=state.items.find(item=>item.id===state.selected),box=el('inspector');if(!row||!box)return;
      state.inspectorOpen=true;box.hidden=false;box.dataset.tone=tone(row.status);container.querySelectorAll('[data-ec-task]').forEach(card=>{const selected=card.dataset.ecTask===row.id;card.classList.toggle('is-selected',selected);card.setAttribute('aria-pressed',String(selected));});box.innerHTML=`<header><strong>${h(row.title)}</strong><button type="button" class="pw-text-button" data-ec-inspector-close aria-label="关闭执行详情">×</button></header><p class="ec-status" data-tone="${tone(row.status)}">${labels[row.status]||h(row.status)}</p><p>${h(row.assistant_name)}${row.model_id?' · '+h(S.models?.find(model=>model.id===row.model_id)?.model||row.model_id):''}</p><div class="ec-inspector-actions"><button type="button" class="btn" data-ec-open>${row.channel==='toolbox'?'查看工具结果':'打开关联对话'}</button>${['running','queued','waiting_input'].includes(row.status)?'<button type="button" class="btn ghost" data-ec-stop>停止</button>':''}${row.kind==='conversation'&&['failed','interrupted'].includes(row.status)?'<button type="button" class="btn ghost" data-ec-resume>继续执行</button>':''}</div><ol class="ec-event-rail" data-ec-events></ol>`;
      const visibility=document.createElement('button');visibility.type='button';visibility.className='btn ghost';visibility.dataset.ecVisibility='';visibility.textContent=row.deleted_at?'恢复到看板':'从看板删除';
      box.querySelector('.ec-inspector-actions').append(visibility);visibility.onclick=async()=>{visibility.disabled=true;try{await TaskActions.visibility([row],load,row.deleted_at?'restore':'delete');}catch(err){error(err.message);}finally{if(visibility.isConnected)visibility.disabled=false;}};
      box.querySelector('[data-ec-inspector-close]').onclick=()=>{state.selected='';state.inspectorOpen=false;box.hidden=true;container.querySelectorAll('[data-ec-task]').forEach(card=>{card.classList.remove('is-selected');card.setAttribute('aria-pressed','false');});};
      box.querySelector('[data-ec-open]').onclick=()=>row.channel==='toolbox'?PaperToolbox.openResult(row.run_id,row.project_id):ProjectWorkspace.openConversation(row.chat_id,row.project_id);
      for(const action of ['stop','resume']){const button=box.querySelector('[data-ec-'+action+']');if(button)button.onclick=async()=>{button.disabled=true;try{if(action==='stop')await TaskActions.stop(row);else await api(row.execution_kind==='workflow'?'/api/workflow/execution/resume':'/api/execution-board/conversation-action',{project_id:row.project_id,id:row.run_id,run_id:row.run_id,action});await load();}catch(err){error(err.message);if(button.isConnected)button.disabled=false;}};}
      try{const data=await api('/api/execution-board/events?project_id='+encodeURIComponent(row.project_id)+'&task_id='+encodeURIComponent(row.active_run_kind==='conversation'||row.kind==='conversation'?row.chat_id:row.id)+'&run_id='+encodeURIComponent(row.active_run_id||row.run_id||'')+'&after=0&limit=200&latest=1');if(!live()||!box.isConnected||state.selected!==row.id)return;box.querySelector('[data-ec-events]').innerHTML=data.events.slice(-40).map(event=>`<li><time>${h(fmtDate(event.created_at))}</time><strong>${h(activity(event.activity||event.kind))}</strong></li>`).join('')||'<li>尚未记录运行事件</li>';}catch(err){error(err.message);}
    }
    async function removePlan(plan){
      if(!await uiConfirm('删除这条研究计划？关联运行和产物仍可在看板中查看。'))return false;
      await api('/api/plans/delete',{project_id:owner(plan),id:plan.id});window.dispatchEvent(new Event('todos-changed'));await load();return true;
    }
    async function editPlan(plan,defaults={},focus='title'){
      const target=plan?owner(plan):await chooseProject();if(!target)return;
      const origin=document.activeElement;
      const dialog=document.createElement('dialog');dialog.className='pt-dialog';dialog.setAttribute('aria-label',plan?'编辑研究计划':'新建研究计划');
      dialog.innerHTML=`<form class="ec-plan-form"><header><h3>${plan?'编辑研究计划':'新建研究计划'}</h3><button type="button" class="pw-text-button" data-plan-close aria-label="关闭">×</button></header><label>研究目标<input class="control" name="title" required maxlength="500" value="${h(plan?.title||'')}"></label><label>研究要求<textarea class="control" name="description" rows="4">${h(plan?.description||'')}</textarea></label><label>验收标准<textarea class="control" name="acceptance" rows="3">${h(plan?.acceptance||'')}</textarea></label><div class="ec-plan-fields"><label>优先级<select class="control" name="priority">${['P0','P1','P2'].map(id=>`<option ${(plan?.priority||'P1')===id?'selected':''}>${id}</option>`).join('')}</select></label><label>截止日期<input type="date" class="control" name="due_date" value="${h(plan?.due_date||'')}"></label></div><label>前置计划<select class="control" name="dependencies" multiple>${state.plans.filter(row=>owner(row)===target&&row.id!==plan?.id).map(row=>`<option value="${h(row.id)}" ${plan?.dependencies?.includes(row.id)?'selected':''}>${h(row.title)}</option>`).join('')}</select></label><p class="pw-error" data-plan-error role="alert"></p><footer>${plan?'<button type="button" class="pw-text-button" data-plan-delete>删除计划</button>':''}<span class="pw-spacer"></span><button class="btn" type="submit">保存计划</button></footer></form>`;
      document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();if(origin?.isConnected)origin.focus();else el('new')?.focus();},{once:true});dialog.querySelector('[data-plan-close]').onclick=()=>dialog.close();
      const remove=dialog.querySelector('[data-plan-delete]');if(remove)remove.onclick=async()=>{if(!await uiConfirm('删除这条研究计划？关联运行和产物仍可在看板中查看。'))return;try{await api('/api/plans/delete',{project_id:owner(plan),id:plan.id});dialog.close();window.dispatchEvent(new Event('todos-changed'));await load();}catch(err){dialog.querySelector('[data-plan-error]').textContent=err.message;}};
      dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]');button.disabled=true;try{const data=Object.fromEntries(new FormData(form));data.dependencies=[...form.elements.dependencies.selectedOptions].map(option=>option.value);await api('/api/plans/save',{...data,project_id:target,id:plan?.id||''});if(!plan)state.planHistory=false;dialog.close();window.dispatchEvent(new Event('todos-changed'));await load();}catch(err){dialog.querySelector('[data-plan-error]').textContent=err.message;button.disabled=false;}};
      dialog.showModal();dialog.querySelector('[name='+focus+']').focus();
    }
    async function load(){
      if(!live()||state.view==='automation')return;
      if(state.loading){state.reload=true;return;}
      state.loading=true;const view=state.view;
      try{
        const data=await api((view==='plans'?'/api/plans?':'/api/execution-board?')+scope);
        if(!live())return;if(state.view!==view){state.reload=true;return;}
        if(data.projects){state.projects=data.projects;paintProjects();}
        const rows=view==='plans'?data.plans:data.items,signature=JSON.stringify(rows),changed=state.loadedView!==view||state.signature!==signature;
        if(view==='plans')state.plans=rows;else{state.items=rows;state.cursor=Math.max(state.cursor,data.cursor);options.onSnapshot?.(rows);}
        state.loadedView=view;state.signature=signature;if(changed)paint();error('');
      }catch(err){error(err.message);}finally{state.loading=false;if(state.reload){state.reload=false;queueMicrotask(load);}}
    }
    async function switchView(view,initial=false){
      container.scrollTop=0;state.loadedView='';state.view=view;if(!initial){state.filter='';state.laneFilter='';}state.selected='';views.set(project,view);if(options.global)globalView=view;
      container.querySelectorAll('[data-ec-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.ecView===view)));
      el('filters').hidden=el('summary').hidden=el('total').hidden=view==='automation';el('content').replaceChildren();
      if(view==='automation'){await renderSchedules(el('content'),{projectId:project,live:()=>live()&&state.view==='automation'});return;}
      await load();
    }
    let refreshTimer;
    function connect(){if(!live())return;state.source?.close();const source=new EventSource('/api/execution-board/stream?'+scope+'&after='+state.cursor);state.source=source;source.onopen=()=>{if(live()){el('connection').textContent='● 实时连接';load();}};source.onmessage=event=>{if(!live())return;state.cursor=Math.max(state.cursor,Number(event.lastEventId)||0);if(!refreshTimer)refreshTimer=setTimeout(()=>{refreshTimer=null;load();},100);};source.onerror=()=>{if(live())el('connection').textContent='正在重连 · 记录已保存';};}
    container.querySelectorAll('[data-ec-view]').forEach(button=>button.onclick=()=>switchView(button.dataset.ecView));
    el('search').oninput=()=>{state.query=el('search').value.toLowerCase();paint();};el('status').onchange=()=>{if(state.view==='plans')state.planHistory=el('status').value==='history';else state.filter=el('status').value;state.laneFilter='';paint();};el('layout').onchange=()=>{if(state.view==='plans')state.layout=el('layout').value;else state.boardLayout=el('layout').value;paint();};el('new').onclick=()=>state.view==='plans'?editPlan():createTask();el('refresh').onclick=()=>state.view==='automation'?renderSchedules(el('content'),{projectId:project,live:()=>live()&&state.view==='automation'}):load();
    state.guard=setInterval(()=>{if(!live()&&mounted===state)dispose();},1000);state.fallback=setInterval(()=>{if(live()&&!document.hidden)load();},3000);
    state.menuClosed=()=>setTimeout(()=>{if(live()&&state.pendingPaint)paint();},0);window.addEventListener('rw:context-menu-closed',state.menuClosed);
    state.todoChanged=()=>{if(live()&&state.view==='plans')load();};window.addEventListener('todos-changed',state.todoChanged);container.__taskCenterRefresh=load;state.onVisible=()=>{if(!document.hidden)load();};document.addEventListener('visibilitychange',state.onVisible);await switchView(state.view,true);connect();
  }
  async function newPlan(){if(mounted?.live?.())return mounted.newPlan();await ProjectWorkspace.openProject(curTask);await ProjectWorkspace.showResource('plan');return mounted.newPlan();}
  async function openPlan(id,project){await ProjectWorkspace.openProject(project);await ProjectWorkspace.showResource('plan');return mounted.openPlan(id);}
  return {render,renderGlobal,requestView,dispose,newPlan,openPlan,contextActions:row=>row.__ecActions||[]};
})();
