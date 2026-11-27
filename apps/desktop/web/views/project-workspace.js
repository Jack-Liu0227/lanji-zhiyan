/* Project-owned discussions, task execution and document review share one surface. */
const ProjectWorkspace = (() => {
  "use strict";
  const labels={ready:"可执行",draft:"已准备",pending:"未开始",queued:"排队中",running:"执行中",awaiting_review:"等待人工核查",succeeded:"已完成",delivered:"已交付",blocked:"等待处理",failed:"执行失败",interrupted:"已中断",cancelled:"已停止",waiting:"等待前置交付"};
  const resources={'file-preview':'文件预览',catalog:'统一资料库',manuscripts:'论文稿件',experiments:'实验记录',outputs:'交付产物',files:"文件",papers:"文献",notes:"笔记与成果",tasks:"任务与自动化",automation:"自动化",workflow:"工作流",plan:"进度与计划",stars:"论文星图",overview:"项目概况",search:"搜索项目资料",'node-result':'节点产物','expert-chat':'节点专家对话'};
  const surfaces={chat:'目标对话',library:'统一资料库',workbench:'产物工作台',tasks:'任务与自动化',overview:'概览'};
  const resourceSurface={'file-preview':'chat',catalog:'library',manuscripts:'library',experiments:'library',outputs:'library',files:'library',papers:'library',notes:'library',stars:'library',search:'library',tasks:'tasks',automation:'tasks',plan:'tasks',workflow:'chat',overview:'overview','task-detail':'tasks','node-result':'workbench','run-artifacts':'workbench','expert-chat':'workbench'};
  const DEFAULT_CHAT_TITLES=new Set(['','新对话','新的课题讨论','课题讨论','新会话']);
  const states=new Map(), chats=new Map();
  resources['run-artifacts']='本次运行产物';
  let mounted=null, intent=null, generation=0, navigation=0, timer=null, restored=false;
  const h=value=>esc(String(value??""));
  const messageKey=value=>String(value??'').replace(/\s+/g,' ').trim();
  const userInputKey=value=>{
    const normalized=messageKey(value);
    for(const marker of ['【当前项目明确引用的材料】','【@上下文】','【用户明确使用的提示词模板：','【用户选择的材料】','【@提及的上下文】']){
      const index=normalized.indexOf(marker);
      if(index>0)return normalized.slice(0,index).trim();
    }
    return normalized;
  };
  const deriveChatTitle=(raw,fallback='新的课题讨论')=>{
    const cleaned=userInputKey(raw).replace(/^[#>*\-\s"'“”‘’`]+/,'').trim();
    if(!cleaned)return fallback;
    const first=(cleaned.split(/[。！？!?\r\n；;]/)[0]||cleaned).trim();
    if(!first)return fallback;
    return first.length>30?first.slice(0,30)+'…':first;
  };
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
  const write=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));}catch{}};
  const root=()=>document.getElementById("projectWorkspace");
  const active=()=>route()==="workspace" && !!root();
  const live=state=>active() && mounted===state && curTask===state.id && root()?.dataset.projectId===state.id;
  const dockSections=new Set(['workflow','node-result','expert-chat','file-preview']);
  function disposeResource(){
    const host=document.getElementById('pwResource')?.firstElementChild;
    for(const key of ['__nodeDispose','__graphDispose','__expertDispose','__previewDispose'])host?.[key]?.();
  }
  function saveDock(state){
    if(state.surface!=='chat')return;
    state.dockStates||={};
    state.dockStates[state.chatId||'new']={section:dockSections.has(state.section)||state.section==='files'?state.section:'workflow',
      open:!!state.peek,runId:state.dockRunId||'',runSelected:!!state.liveRunSelected,nodeResultSelection:state.nodeResultSelection,
      expertNodeContext:state.expertNodeContext,expertDockMode:state.expertDockMode||'chat',expertAttempts:state.expertAttempts||{},previewFile:state.previewFile||null,fileRow:state.fileRow};
  }
  function restoreDock(state){
    const dock=state.dockStates?.[state.chatId||'new'];
    state.peek=!!dock?.open;state.section=dock?.section||'workflow';
    state.dockRunId=dock?.runId||'';state.liveRunSelected=!!dock?.runSelected;state.nodeResultSelection=dock?.nodeResultSelection||null;
    state.expertNodeContext=dock?.expertNodeContext||null;state.expertDockMode=dock?.expertDockMode||'chat';
    state.expertAttempts={...dock?.expertAttempts};
    state.previewFile=dock?.previewFile||null;state.focusWorkflow=state.dockRunId?{runId:state.dockRunId}:null;
    state.allowWorkflowTemplate=false;
    state.fileRow=dock?.fileRow||null;
    state.review=null;state.inspection=null;state.conversationContext=null;state.contextError='';state.contextRequest=(state.contextRequest||0)+1;
  }
  async function conversationContext(state,{force=false}={}){
    const chatId=state.chatId;
    if(!chatId)return {chat_id:'',run_ids:[],task_ids:[],runs:[]};
    if(!force&&state.conversationContext?.chat_id===chatId)return state.conversationContext;
    if(state.contextLoading?.chatId===chatId&&state.contextLoading.request===state.contextRequest)return state.contextLoading.promise;
    const request=state.contextRequest=(state.contextRequest||0)+1;
    const loading={chatId,request};
    loading.promise=(async()=>{
      try{
        const result=await api('/api/agent-settings/workflow-context?project_id='+encodeURIComponent(state.id)+'&chat_id='+encodeURIComponent(chatId));
        if(!live(state)||state.chatId!==chatId||request!==state.contextRequest)return null;
        state.conversationContext=result;state.contextError='';paintWorkflowLive(state);return result;
      }catch(err){
        if(live(state)&&state.chatId===chatId&&request===state.contextRequest){state.contextError=err.message;paintWorkflowLive(state);}
        throw err;
      }finally{if(state.contextLoading===loading)state.contextLoading=null;}
    })();
    state.contextLoading=loading;return loading.promise;
  }
  function conversationTasks(state){
    const context=state.conversationContext;
    if(context?.chat_id!==state.chatId)return [];
    const ids=new Set(context.task_ids||[]),runs=new Set(context.run_ids||[]);
    return (state.tasks||[]).filter(task=>ids.has(task.id)||runs.has(task.workflow_run_id));
  }
  function paintWorkflowLive(state){
    const host=document.getElementById('pwWorkflowLive');if(!host||!live(state))return;
    const context=state.conversationContext?.chat_id===state.chatId?state.conversationContext:null;
    const runs=context?.runs||[];
    host.hidden=!runs.length&&!state.contextError;
    if(!runs.length){host.innerHTML=state.contextError?'<p>工作流状态读取失败，尚未取得当前对话数据。<button type="button" class="pw-text-button" data-live-refresh>重新读取</button></p>':'';host.querySelector('[data-live-refresh]')?.addEventListener('click',()=>conversationContext(state,{force:true}).catch(()=>{}));return;}
    const attentionRuns=runs.filter(run=>['running','queued','awaiting_review','blocked','interrupted'].includes(run.status));
    const previous=runs.find(run=>run.id===state.dockRunId);
    const selected=(state.liveRunSelected&&previous)||attentionRuns[0]||previous||runs[0];
    state.dockRunId=selected.id;state.focusWorkflow={...state.focusWorkflow,runId:selected.id};
    const run=selected.live;if(!run){host.hidden=true;return;}
    const chatId=state.chatId,runId=run.id,current=()=>live(state)&&state.chatId===chatId&&state.dockRunId===runId;
    const nodes=run.nodes||[],attention=nodes.filter(node=>['running','queued','failed','interrupted','blocked','awaiting_review'].includes(node.status));
    const visibleNodes=nodes.filter(node=>attention.includes(node)||(['ready','pending'].includes(node.status)&&(node.waiting_for||[]).length));
    const phase={model:'等待模型响应',reasoning:'模型正在分析',responding:'正在生成节点响应',tool_result:'工具结果已返回，等待后续处理',preparing:'准备节点执行',finalizing:'正在校验并保存产物',tools_ready:'继续待执行工具',tool:'等待工具返回',starting:'准备执行',thinking:'等待模型响应',executing:'执行工具',waiting_children:'等待子任务交付'};
    const timeText=value=>value?new Date(value).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'未记录';
    const open=host.querySelector('details')?.open??state.liveExpanded??false;
    const terminal=['succeeded','failed','cancelled'].includes(run.status);
    if(state.chatId?.startsWith('execution-')||(terminal&&state.dismissedLiveRun===runId)){host.hidden=true;return;}
    const final=nodes.filter(node=>node.outputs?.length).at(-1);
    const reviewNode=nodes.find(node=>node.status==='awaiting_review');
    const cleanErr=msg=>String(msg||'').includes('Service restarted; resume the saved attempt')?'服务曾重启中断，可从检查点继续':msg;
    const compactNodeText=node=>{
      const watch=node.monitoring||{};
      if((node.waiting_for||[]).length)return '等待前置节点：'+node.waiting_for.map(id=>nodes.find(item=>item.node_id===id)?.label||id).join('、');
      if(node.status==='running')return watch.worker_alive?(phase[watch.phase]||'正在执行'):'状态待确认，尚未收到有效心跳';
      if(node.status==='awaiting_review')return '产物已保存，等待人工核查';
      return cleanErr(node.error)||labels[node.status]||node.status;
    };
    host.dataset.status=run.status;
    const others=attentionRuns.filter(item=>item.id!==runId);
    const executionNodes=attention.filter(node=>node.status==='running');
    const primaryNodes=[...executionNodes,...attention.filter(node=>node.status==='queued')];
    const glanceNodes=primaryNodes.slice(0,1).length?primaryNodes.slice(0,1):attention.slice(0,1);
    const otherNodes=attention.filter(node=>!glanceNodes.includes(node));
    const moreLabels=[
      [otherNodes.filter(node=>node.status==='running').length,'节点执行中'],
      [otherNodes.filter(node=>node.status==='queued').length,'节点排队中'],
      [otherNodes.filter(node=>['failed','interrupted','blocked'].includes(node.status)).length,'节点待恢复'],
      [otherNodes.filter(node=>node.status==='awaiting_review').length,'节点待核查'],
    ].filter(([count])=>count).map(([count,label])=>`另有 ${count} 个${label}`).join(' · ');
    host.innerHTML=`<details ${open?'open':''}><summary><span class="pw-live-indicator" aria-hidden="true"></span><strong>${h(run.name)}</strong><span class="pw-live-status-label">${h(run.prepared?'方案已准备，尚未启动':labels[run.status]||run.status)}</span><span class="pw-live-count">${run.delivered_count}/${run.total_count} 已交付${run.review_count?' · '+run.review_count+' 待核查':''}</span><span data-live-clock></span><span class="pw-live-actions">${others.length?`<button type="button" class="pw-text-button" data-live-active>另有 ${others.length} 条运行需关注</button>`:''}${reviewNode?`<button type="button" class="btn sm" data-live-quick-review="${h(reviewNode.node_id)}">核查产物</button>`:'<button type="button" class="pw-text-button" data-live-workflow>工作流</button>'}<span class="pw-live-expand">${open?'收起':'详情'}</span>${terminal?'<button type="button" class="pw-text-button" data-live-dismiss title="隐藏已完成工作流提示" aria-label="关闭工作流提示">×</button>':''}</span></summary><div class="pw-live-body">${runs.length>1?`<label>本对话运行<select class="control" data-live-select aria-label="当前对话的工作流运行">${runs.map(item=>`<option value="${h(item.id)}" ${item.id===runId?'selected':''}>${h(item.layout_name)} · ${h(labels[item.status]||item.status)} · ${h(item.id.slice(0,8))}</option>`).join('')}</select></label>`:''}<div class="pw-live-nodes">${visibleNodes.map(node=>`<div class="pw-live-node" data-status="${h(node.status)}"><button type="button" class="pw-text-button" data-live-node="${h(node.node_id)}">${h(node.label)}</button><span>${h(compactNodeText(node))}</span><small>${node.attempt?'第 '+node.attempt+' 次尝试 · ':''}<span data-live-node-clock="${h(node.node_id)}"></span>${node.status==='running'?`当前阶段 <span data-live-phase-clock="${h(node.node_id)}"></span> · `:''}业务进展 ${h(timeText(node.monitoring?.last_progress_at))}${node.monitoring?.heartbeat_at?' · 心跳 '+h(timeText(node.monitoring.heartbeat_at)):''}</small>${node.recovery?.can_continue||node.recovery?.can_restart?`<p>${h(node.recovery.description)}</p><button type="button" class="btn ghost" data-live-resume="${h(node.node_id)}" ${state.liveSubmitting||!node.recovery.can_continue?'disabled':''}>${node.recovery.can_continue?(node.recovery.mode==='checkpoint'?'从检查点继续':'重试本节点'):'需在工作流中核对工具后重启'}</button>`:''}${node.status==='awaiting_review'?`<button type="button" class="btn ghost" data-live-review="${h(node.node_id)}">打开产物并核查</button>`:''}</div>`).join('')||`<p>${run.status==='succeeded'?'工作流已完成，交付文件与核查记录已保存。':run.prepared?'向管家确认开始后启动执行。':'暂无活动节点。可查看工作流中的前置依赖和已保存成果。'}</p>`}</div><div class="pw-live-footer"><span>${state.contextError?'刷新失败，保留上次状态 · ':''}更新于 ${h(timeText(run.observed_at))}${state.liveSubmitting?' · 正在提交，等待状态确认':''}</span><button type="button" class="pw-text-button" data-live-workflow-foot>查看工作流</button>${final?'<button type="button" class="pw-text-button" data-live-artifacts>查看已保存产物</button>':''}${state.contextError?'<button type="button" class="pw-text-button" data-live-refresh>重新读取</button>':''}</div></div></details>${glanceNodes.length?`<div class="pw-live-glance">${glanceNodes.map(node=>`<p data-live-glance-node="${h(node.node_id)}"><strong>${h(node.label)}</strong><span>${h(compactNodeText(node))}</span>${node.status==='running'?`<span data-live-glance-clock="${h(node.node_id)}"></span><small>${node.monitoring?.worker_alive?'后台心跳正常':'后台状态待确认'}</small>`:''}${moreLabels?`<small class="pw-live-more">${h(moreLabels)}</small>`:''}</p>`).join('')}</div>`:''}`;
    host.classList.toggle('is-stale',!!state.contextError);
    host.querySelector('details').ontoggle=event=>{state.liveExpanded=event.target.open;const ex=host.querySelector('.pw-live-expand');if(ex)ex.textContent=event.target.open?'收起':'详情';};
    host.querySelector('[data-live-quick-review]')?.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();const node=nodes.find(n=>n.node_id===event.currentTarget.dataset.liveQuickReview);if(current()&&node)openTask(node.task_id,state.id,{peek:true});});
    host.querySelector('[data-live-dismiss]')?.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();state.dismissedLiveRun=runId;host.hidden=true;});
    host.querySelector('[data-live-active]')?.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(!current())return;state.dockRunId=others[0].id;state.liveRunSelected=true;state.focusWorkflow={runId:state.dockRunId};persist(state);paintWorkflowLive(state);if(state.peek)showResource('workflow',state,{peek:true});});
    const start=Date.parse(run.started_at),end=Date.parse(run.ended_at);
    const clock=host.querySelector('[data-live-clock]');
    if(start&&!run.prepared&&(!terminal||end))ChatPresentation.clock(clock,start,'总耗时 ',end||0);
    else clock.textContent=run.prepared?'尚未开始':'';
    for(const node of attention){
      const target=[...host.querySelectorAll('[data-live-node-clock]')].find(el=>el.dataset.liveNodeClock===node.node_id);
      const a=Date.parse(node.started_at),b=Date.parse(node.ended_at);
      if(a&&(b||['running','queued'].includes(node.status)))ChatPresentation.clock(target,a,'节点 ',b||0);
      if(node.status==='running'){
        const phaseStart=Date.parse(node.monitoring?.phase_started_at);
        ChatPresentation.clock([...host.querySelectorAll('[data-live-phase-clock]')].find(el=>el.dataset.livePhaseClock===node.node_id),phaseStart,'',0);
        ChatPresentation.clock([...host.querySelectorAll('[data-live-glance-clock]')].find(el=>el.dataset.liveGlanceClock===node.node_id),phaseStart,'本阶段已用 ',node.monitoring?.worker_alive?0:Date.parse(run.observed_at));
      }
    }
    host.querySelector('[data-live-select]')?.addEventListener('change',async event=>{
      if(!current()||!await flush())return;
      state.dockRunId=event.target.value;state.liveRunSelected=true;state.focusWorkflow={runId:state.dockRunId};persist(state);paintWorkflowLive(state);
      if(state.peek)await showResource('workflow',state,{peek:true});
    });
    host.querySelectorAll('[data-live-workflow],[data-live-workflow-foot]').forEach(btn=>btn.onclick=event=>{event.preventDefault();event.stopPropagation();if(current())openWorkflowDock(state.id,runId);});
    host.querySelector('[data-live-artifacts]')?.addEventListener('click',()=>current()&&showResource('run-artifacts',state,{runId}));
    host.querySelector('[data-live-refresh]')?.addEventListener('click',()=>conversationContext(state,{force:true}).catch(()=>{}));
    host.querySelectorAll('[data-live-node]').forEach(button=>button.onclick=()=>{if(current()){state.focusWorkflow={runId,nodeId:button.dataset.liveNode};showResource('workflow',state,{peek:true});}});
    host.querySelectorAll('[data-live-review]').forEach(button=>button.onclick=()=>{const node=nodes.find(n=>n.node_id===button.dataset.liveReview);if(current())openTask(node.task_id,state.id,{peek:true});});
    host.querySelectorAll('[data-live-resume]').forEach(button=>button.onclick=async()=>{
      if(!current()||state.liveSubmitting)return;
      const node=nodes.find(n=>n.node_id===button.dataset.liveResume);
      if(!node.recovery.can_continue){state.focusWorkflow={runId,nodeId:node.node_id};return showResource('workflow',state,{peek:true});}
      state.liveSubmitting=true;state.liveRetryKeys||={};const key=runId+':'+node.node_id+':'+node.task_run_id;
      state.liveRetryKeys[key]||=crypto.randomUUID();paintWorkflowLive(state);
      try{await api('/api/workflow/execution/resume',{id:runId,project_id:state.id,node_id:node.node_id,expected_task_run_id:node.task_run_id,request_key:state.liveRetryKeys[key]});if(current())await refresh(state);}
      catch(err){if(current())error(err.message,state);}
      finally{state.liveSubmitting=false;if(current())paintWorkflowLive(state);}
    });
  }
  async function changeConversation(state,chatId){
    saveDock(state);disposeResource();state.resourceEpoch++;state.fileRequest=(state.fileRequest||0)+1;
    state.resourceRequest=(state.resourceRequest||0)+1;
    if(typeof flCurrentFile!=='undefined')flCurrentFile=null;
    state.chatId=chatId;state.surface='chat';restoreDock(state);
    paintWorkflowLive(state);
    document.getElementById('pwResource')?.replaceChildren();
    persist(state);layoutPanels(state);
    if(state.peek)await showResource(state.section,state,{initial:true,peek:true});
  }
  const get=id=>{
    if(!states.has(id)){
      const saved=read("rw-workspace:"+id,{});if(saved.section==='tools')saved.section='workflow';if(saved.sections?.flow==='tools')saved.sections.flow='workflow';if(saved.surface==='workbench'&&saved.section==='files'){saved.surface='library';saved.section='files';saved.sections={...saved.sections,library:'files',workbench:'run-artifacts'};}
      const nodePeek=saved.surface==='chat'&&((saved.section==='node-result'&&!!saved.nodeResultSelection&&saved.nodePeek===true)||(saved.dockPeek===true&&['workflow','node-result','expert-chat'].includes(saved.section)));
      states.set(id,{id,chatId:saved.chatId||"",section:saved.section||"overview",surface:surfaces[saved.surface]?saved.surface:'chat',sections:{library:'catalog',tasks:'tasks',flow:'workflow',workbench:'run-artifacts',...saved.sections},peek:nodePeek,navOpen:false,inspection:null,mode:saved.mode||"supervised",modeTouched:!!saved.modeTouched,scope:saved.scope||"project",target:saved.target||"",reading:false,
        searchQuery:saved.searchQuery||"",navHidden:!!saved.navHidden,treeHidden:!!saved.treeHidden,drafts:read("rw-workspace-drafts:"+id,{}),fileDirectory:saved.fileDirectory||'',fileRow:saved.fileRow||null,libraryFile:saved.libraryFile||saved.fileRow||null,nodeResultSelection:saved.nodeResultSelection||null,expertNodeContext:saved.expertNodeContext||null,artifactRunId:saved.artifactRunId||'',tasks:[],bindings:[],teams:[],targets:null,layout:null,project:null,review:null,filter:"",epoch:0,resourceEpoch:0,refreshing:false});
    }
    const state=states.get(id);
    if(!state.dockStates){const saved=read('rw-workspace:'+id,{});state.dockStates=saved.dockStates||{};if(state.surface==='chat')restoreDock(state);}
    return state;
  };
  function persist(state){saveDock(state);write("rw-workspace:"+state.id,{dockStates:state.dockStates,chatId:state.chatId,section:state.section,surface:state.surface,sections:state.sections,searchQuery:state.searchQuery,mode:state.mode,modeTouched:state.modeTouched,scope:state.scope,target:state.target,navHidden:state.navHidden,treeHidden:state.treeHidden,fileDirectory:state.fileDirectory,fileRow:state.fileRow,libraryFile:state.libraryFile,nodeResultSelection:state.nodeResultSelection,expertNodeContext:state.expertNodeContext,artifactRunId:state.artifactRunId,nodePeek:state.surface==='chat'&&state.section==='node-result'&&state.peek,dockPeek:state.surface==='chat'&&state.peek&&dockSections.has(state.section)});write("rw-workspace-drafts:"+state.id,state.drafts);}
  function draft(state){const key=state.chatId||"new";return state.drafts[key]||(state.drafts[key]={text:"",refs:[]});}
  function currentChat(state){return (S.chats||[]).find(c=>c.id===state.chatId && c.task_id===state.id);}
  function chatRecord(id){if(!chats.has(id))chats.set(id,{messages:[],events:[],run:null,loaded:false,request:0});return chats.get(id);}
  function error(message,state=mounted){if(live(state)){const box=document.getElementById("pwError");if(box)box.textContent=message||"";}}
  function notice(message,state=mounted){if(live(state)){const box=document.getElementById("pwNotice");if(box)box.textContent=message||"";}}
  function hasSurface(kind){return active() && (mounted.surface!=='chat'||mounted.peek) && ((kind==="space"&&["papers","notes","plan","stars","overview"].includes(mounted.section)) || (kind==="workflow"&&["files","tools"].includes(mounted.section)));}
  function hasDirty(){
    if(!active())return false;
    if(typeof WorkflowNodeWorkbench!=='undefined'&&WorkflowNodeWorkbench.dirty())return true;
    if(window.ProjectWorkflowGraph?.dirty())return true;
    const file=typeof flCurrentFile!=="undefined"?flCurrentFile:null;
    if(file?.projectId===mounted.id && file.text!==file.originalText)return true;
    if(typeof mdProj!=="undefined"&&mdProj&&document.getElementById("mdSource")?.getClientRects().length){
      return !mdProj.read_only && (!!mdProj.savePromise || (mdProj.editVersion||0)!==(mdProj.savedVersion||0) || (!mdProj.path&&!!(document.getElementById('mdSource').value.trim()||document.getElementById('mdNoteTitle')?.value.trim())));
    }
    return false;
  }
  async function flush(){
    const state=mounted;if(!state || !root())return true;
    if(typeof WorkflowNodeWorkbench!=='undefined'&&!await WorkflowNodeWorkbench.flush()){error('节点产物草稿尚未保存，当前编辑已保留。',state);return false;}
    if(window.ProjectWorkflowGraph&&!await ProjectWorkflowGraph.flush()){error('节点配置尚未保存，请处理提示后再切换。',state);return false;}
    const ask=document.getElementById("pwAsk");if(ask){draft(state).text=ask.value;persist(state);}
    if(typeof flCurrentFile!=="undefined"&&flCurrentFile?.projectId===state.id){
      const editor=document.getElementById("flEditText");if(editor)flCurrentFile.text=editor.value;
      if(flCurrentFile.text!==flCurrentFile.originalText && !await flSaveCurrent()){error("文件保存失败，仍保留当前内容。请保存成功后再切换。",state);return false;}
    }
    if(typeof mdProj!=="undefined"&&mdProj&&document.getElementById("mdSource")?.getClientRects().length){
      if(!await pnSaveCurrent()){error("笔记保存失败，仍保留当前笔记。请重试保存后再切换。",state);return false;}
    }
    return true;
  }
  async function leave(action){const token=++navigation;if(await flush()&&token===navigation)return action();}
  function requestRoute(value){
    if(value==="workflow")intent={section:"workflow"};
    else if(value==='workbench')intent={section:'run-artifacts'};
    else if(value==='tasks'||value==='automation')intent={section:value};
    else if(value==="notes")intent={section:"notes"};
    else if(value==="space")intent={section:typeof spTab!=="undefined"?spTab:"overview"};
  }
  function projectOptions(id){return (S.tasks||[]).map(p=>`<option value="${h(p.id)}" ${p.id===id?"selected":""}>${h(p.title)}</option>`).join("");}
  function icon(name){
    const paths={chat:'M4 4h16v11H9l-5 5V4Z',library:'M3 7h7l2-3h9v16H3V7Z',workbench:'M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7Zm0 4h20M8 11v8',tasks:'m4 6 2 2 4-4M13 6h7M4 13l2 2 4-4M13 13h7M4 20h16',flow:'M8 3h8v5H8V3ZM3 16h6v5H3v-5Zm12 0h6v5h-6v-5ZM12 8v4M6 16v-4h12v4',overview:'M4 4h16v6H4V4Zm0 8h7v8H4v-8Zm9 0h7v8h-7v-8Z',menu:'M4 6h16M4 12h16M4 18h16',back:'m10 5-7 7 7 7M3 12h18'};
    return `<svg class="pw-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.library}"/></svg>`;
  }
  function shell(state){
    return `<section class="project-workspace" id="projectWorkspace" data-project-id="${h(state.id)}" data-surface="${state.surface}" aria-label="项目工作区">
      <header class="pw-top">
        <button type="button" class="pw-nav-toggle" id="pwToggleNav" aria-label="项目导航" title="展开或收起当前视图的导航" aria-controls="pwProjectNav" aria-expanded="true">${icon('menu')}</button>
        <div class="pw-project-heading"><span>项目工作区</span><select class="control" id="pwProject" aria-label="当前项目">${projectOptions(state.id)}</select></div>
        <span class="pw-spacer"></span><div class="pw-counts" aria-label="项目任务状态"><button type="button" data-pw-filter="running" id="pwRunning">运行中 0</button><button type="button" data-pw-filter="review" id="pwPending">待确认 0</button><button type="button" data-pw-filter="attention" id="pwFailed">需处理 0</button></div>
        <button type="button" class="pw-project-manage pw-text-button" id="pwProjectCreate" title="新建项目" aria-label="新建项目">＋<span>新建项目</span></button><details class="pw-settings" id="pwProjectSettings"><summary>项目设置</summary><div class="pw-settings-menu"><button type="button" id="pwProjectEdit">编辑项目信息</button><button type="button" data-pw-view="overview">项目概况</button><button type="button" id="pwRebuildAssets">整理资料索引</button><button type="button" id="pwBindings">项目执行者与模式</button><button type="button" id="pwMembers">团队与成员</button><button type="button" id="pwExperts">管理专家</button><label>新任务模式<select class="control" id="pwMode" aria-label="新任务执行模式"><option value="supervised">人工核查</option><option value="full_access">Full access</option></select></label><p>仅影响新任务，已有运行保持原模式。</p></div></details>
      </header>
      <nav class="pw-primary" role="tablist" aria-label="项目工作视图">${Object.entries(surfaces).map(([id,label])=>`<button type="button" role="tab" id="pwTab-${id}" data-pw-surface="${id}" aria-selected="${state.surface===id}" aria-controls="pwWorkArea" tabindex="${state.surface===id?'0':'-1'}">${icon(id)}<span>${label}</span></button>`).join('')}</nav>
      <div class="pw-notice" id="pwNotice" role="status" aria-live="polite"></div><div class="pw-error" id="pwError" role="alert"></div>
      <div class="pw-layout">
        <button type="button" id="pwNavShade" class="pw-nav-shade" aria-label="关闭项目导航" hidden></button>
        <nav id="pwProjectNav" class="pw-sidebar" aria-label="项目视图导航">
          <div class="pw-nav-heading"><strong id="pwNavTitle">项目对话</strong><button type="button" id="pwNavClose" class="pw-text-button" aria-label="收起项目导航">×</button></div>
          <section data-pw-nav-group="chat" class="pw-nav-section"><button type="button" class="pw-new-chat" id="pwNewChat">＋ 新对话</button><div class="pw-sessions" id="pwSessions"></div><div class="pw-quick-links" aria-label="快捷入口"><div class="pw-quick-links-title">快捷入口</div><button type="button" data-pw-quick="library">📁 统一资料库</button><button type="button" data-pw-quick="workflow">🔀 右侧 DAG 工作流</button><button type="button" data-pw-quick="bindings">👨‍🔬 专家与团队</button><button type="button" data-pw-quick="tasks">📋 任务与资产中心</button></div></section>
          <section data-pw-nav-group="library" class="pw-nav-section"><form id="pwSearchForm" class="pw-search-form" role="search"><input type="search" class="control" id="pwSearchInput" aria-label="搜索当前项目资料" placeholder="搜索文献、笔记和进度…"><button type="submit" class="pw-text-button">搜索</button></form><button type="button" class="btn ghost" id="pwUploadFile">上传文件（也可拖入）</button><div class="pw-resource-links"><button type="button" data-pw-view="catalog">全部资料</button><button type="button" data-pw-view="files">目录与文件</button><button type="button" data-pw-view="papers" title="文献论文 (PDF)">文献论文 (PDF)</button><button type="button" data-pw-view="notes" title="研读笔记 (Markdown)">研究笔记 (Markdown)</button><button type="button" data-pw-view="stars" title="文献关联知识星图">论文星图</button></div></section>
          <div id="pwSidebarTreeSlot"><div class="pw-file-tree" id="pwFileTree"></div></div>
        </nav>
        <section class="pw-main" id="pwWorkArea" role="tabpanel" aria-labelledby="pwTab-${state.surface}">
          <button type="button" class="pw-dock-launcher" id="pwDockLauncher" title="展开右侧 EvoFlow 工作流与节点审查侧边栏" hidden>🔀 工作流 ▣</button>
          <section class="pw-chat" aria-label="项目 AI 对话"><header class="pw-chat-head"><div class="pw-chat-title-wrap"><span class="pw-eyebrow">项目对话</span><strong id="pwChatTitle">${h(currentChat(state)?.title||'新的课题讨论')}</strong><button type="button" class="pw-text-button" id="pwRenameChat">改名</button></div><nav class="pw-conversation-tabs" role="tablist" aria-label="会话查看方式"><button type="button" role="tab" id="pwChatView" data-pw-chat-view="chat" aria-controls="pwMessages">对话</button><button type="button" role="tab" id="pwTraceView" data-pw-chat-view="trajectory" aria-controls="pwTrajectory">执行轨迹</button></nav><span class="pw-spacer"></span><button type="button" class="btn ghost" id="pwTopLanggraph" title="在右侧边栏打开或收起 DAG 科研工作流与节点审查">🔀 DAG 工作流</button><button type="button" class="btn ghost" id="pwCreateTask">新建任务</button><button type="button" class="btn ghost" id="pwShowDoc">浏览资料</button></header><section id="pwWorkflowLive" class="pw-workflow-live" aria-label="当前对话工作流状态" hidden></section><div class="chat-reading-area"><nav class="chat-turn-nav" aria-label="本次对话的用户发言" hidden></nav><div id="pwMessages" class="pw-discussion" role="log" aria-label="当前项目对话"></div><div class="chat-turn-preview" hidden></div><button type="button" data-chat-latest hidden>回到最新 ↓</button></div><section class="workspace-trajectory" id="pwTrajectory" aria-label="当前对话执行轨迹" hidden></section>
            <div class="pw-compose composer"><div class="pw-context-bar" id="pwRefs"></div><textarea id="pwAsk" rows="2" aria-label="向项目专家提出目标" placeholder="描述目标，或 @ 项目专家与团队…"></textarea><div class="pw-mention-menu" id="pwMentionMenu" role="listbox" aria-label="@引用建议" hidden></div><div class="pw-compose-options">${AgentRuntime.markup('pw',currentChat(state)||{})}<label class="pw-target-detail" id="pwRecipientDetailControl" hidden><span id="pwRecipientDetailLabel">团队成员</span><select class="control" id="pwRecipientDetail" aria-label="团队成员"></select></label></div><div class="pw-send-row"><button type="button" class="pw-text-button" id="pwAddReference" title="添加项目资料">＋ 资料</button><button type="button" class="pw-text-button" id="pwAddFile">＋ 上传</button><input type="file" id="pwFileInput" multiple hidden><button type="button" class="pw-text-button" id="pwAddImage">＋ 图片</button><input type="file" id="pwImageInput" multiple hidden accept=".png,.jpg,.jpeg,.webp,.pdf"><select class="control pw-compose-project" id="pwComposeProject" aria-label="对话所属项目" title="切换项目，分别保留各项目的草稿与运行">${projectOptions(state.id)}</select><select class="control pw-scope" id="pwScope" aria-label="发送范围"><option value="project">当前项目</option><option value="workflow">当前工作流</option></select><select class="control" id="pwRecipient" aria-label="发送目标"><option value="">项目专家</option></select><span class="pw-spacer"></span><button type="button" class="pw-text-button" id="pwPrepareTask" hidden>准备任务</button><button type="button" class="pw-send-button" id="pwSend" aria-label="发送" title="Enter 发送">↑</button></div><span id="pwScopeLabel" class="pw-compose-caption">当前项目 · 已授权执行者</span></div>

          </section>
          <aside class="pw-doc" id="pwDocumentPane" aria-label="项目资料与任务内容"><nav class="pw-dock-bar" id="pwDockBar" aria-label="右侧多标签工作台" hidden><button type="button" class="pw-dock-tab" data-pw-dock="workflow" aria-selected="false">🔀 工作流</button><button type="button" class="pw-dock-tab" data-pw-dock="node-result" aria-selected="false">📝 节点审查</button><button type="button" class="pw-dock-tab" data-pw-dock="expert-chat" aria-selected="false">👨‍🔬 节点专家</button><button type="button" class="pw-dock-tab" data-pw-dock="file-preview" aria-selected="false">文件预览</button><div class="pw-dock-actions"><button type="button" class="pw-text-button" id="pwDockNewFlow" title="打开或编排 DAG 工作流">＋</button><button type="button" class="pw-text-button" id="pwDockStageToggle" title="切换至主工作区大屏">⤢ 主区</button><button type="button" class="pw-text-button" id="pwDockCollapse" title="收起右侧边栏">▣ 收起</button></div></nav><header class="pw-doc-head"><div class="pw-doc-heading"><div class="pw-trail"><button type="button" id="pwBack" class="pw-text-button" hidden>${icon('back')}<span>返回</span></button><span id="pwTrail">资料</span></div><strong id="pwResourceTitle">项目文件</strong></div><div class="pw-doc-actions"><button type="button" class="btn ghost" id="pwToggleTree" aria-controls="pwFileTree" aria-expanded="true">收起目录</button><button type="button" class="btn ghost" id="pwQuote">引用选区</button><button type="button" class="btn" id="pwUseMaterial">引用到对话</button><button type="button" class="btn ghost" id="pwMaterialTask">创建任务</button><button type="button" class="pw-text-button" id="pwFocus">专注阅读</button><button type="button" class="pw-text-button" id="pwCloseDoc" aria-label="返回对话">返回对话</button></div><div class="pw-file-tools" id="pwFileToolbar" hidden></div></header><div class="pw-resource" id="pwResource"></div><section id="pwReview" class="pw-review" aria-label="人工核查" hidden></section></aside>
        </section>
      </div>
      <footer class="pw-footer"><span id="pwFooter">当前项目内协作</span><span class="pw-spacer"></span><span id="pwBackground"></span></footer>
    </section>`;
  }
  function narrow(){return root()?.clientWidth<900;}
  function layoutPanels(state){
    if(!live(state))return;
    const node=root(),small=narrow(),chat=document.querySelector('.pw-chat'),doc=document.getElementById('pwDocumentPane');
    node.dataset.surface=state.surface;node.dataset.section=state.section;node.dataset.fileMode=typeof flCurrentMode!=='undefined'?flCurrentMode:'preview';
    node.classList.toggle('pw-reading',state.reading);
    const hasSidebar=state.surface==='library'||state.surface==='chat';
    node.dataset.pwSidebar=hasSidebar?'on':'off';
    if(!hasSidebar)state.navOpen=false;
    node.classList.toggle('pw-nav-closed',state.navHidden);node.classList.toggle('pw-nav-open',state.navOpen);node.classList.toggle('pw-peek',state.surface==='chat'&&state.peek);node.classList.toggle('pw-narrow',small);
    if(typeof SplitResizer!=='undefined'){
      const layoutEl=node.querySelector('.pw-layout');
      if(layoutEl&&!layoutEl.dataset.resizerBound){
        layoutEl.dataset.resizerBound='true';
        SplitResizer.bind(layoutEl,{mode:'fixed-left',storageKey:'pw-sidebar',defaultSize:224,min:180,max:420,cssVar:'--pw-sidebar-w'});
      }
      const mainEl=node.querySelector('.pw-main');
      if(mainEl&&!mainEl.dataset.resizerBound){
        mainEl.dataset.resizerBound='true';
        SplitResizer.bind(mainEl,{mode:'fixed-right',storageKey:'pw-dock',defaultSize:420,min:300,max:800,cssVar:'--pw-dock-w'});
      }
    }
    chat.hidden=state.surface!=='chat';chat.inert=small&&state.surface==='chat'&&state.peek;
    document.getElementById('pwWorkArea').inert=small&&state.navOpen;
    doc.hidden=state.surface==='chat'&&!state.peek;
    const dockBar=document.getElementById('pwDockBar'),dockLauncher=document.getElementById('pwDockLauncher'),docHead=doc.querySelector('.pw-doc-head');
    if(dockBar){
      dockBar.hidden=!(state.surface==='chat'&&state.peek);
      dockBar.querySelectorAll('[data-pw-dock]').forEach(tab=>tab.setAttribute('aria-selected',String(tab.dataset.pwDock===state.section)));
    }
    if(docHead)docHead.hidden=state.surface==='chat'&&state.peek&&['workflow','file-preview'].includes(state.section);
    if(dockLauncher)dockLauncher.hidden=!(state.surface==='chat'&&!state.peek);
    paintWorkflowLive(state);
    if(small&&state.surface==='chat'&&state.peek){doc.setAttribute('role','dialog');doc.setAttribute('aria-modal','true');}else{doc.removeAttribute('role');doc.removeAttribute('aria-modal');}
    document.getElementById('pwWorkArea').setAttribute('aria-labelledby','pwTab-'+state.surface);
    node.querySelectorAll('[data-pw-surface]').forEach(b=>{const on=b.dataset.pwSurface===state.surface;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
    node.querySelectorAll('[data-pw-nav-group]').forEach(group=>group.hidden=group.dataset.pwNavGroup!==state.surface);
    node.querySelectorAll('[data-pw-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.pwView===state.section&&state.surface===resourceSurface[state.section]?'page':'false'));
    document.getElementById('pwNavTitle').textContent={chat:'项目对话',overview:'项目概况',library:'项目资料',workbench:'产物工作台',tasks:'执行与交付',flow:'DAG 工作流'}[state.surface]||'项目资料';
    document.getElementById('pwNavShade').hidden=!small||!state.navOpen;
    document.getElementById('pwToggleNav').setAttribute('aria-expanded',String(small?state.navOpen:!state.navHidden&&!state.reading));
    const nav=document.getElementById('pwProjectNav');nav.inert=small?!state.navOpen:state.navHidden||state.reading;
    if(small&&state.navOpen){nav.setAttribute('role','dialog');nav.setAttribute('aria-modal','true');doc.removeAttribute('aria-modal');}else{nav.removeAttribute('role');nav.removeAttribute('aria-modal');}
    const tree=document.getElementById('pwFileTree'),inline=document.getElementById('pwInlineTree'),slot=document.getElementById('pwSidebarTreeSlot');
    const inPeek=state.surface==='chat'&&state.peek;
    const fileBrowser=state.section==='files'&&state.surface==='library';
    if(tree&&slot){
      const target=state.section==='files'&&(fileBrowser||inPeek)&&inline?inline:slot;
      if(tree.parentElement!==target)target.append(tree);
      slot.hidden=true;
      const showTree=fileBrowser?!state.treeHidden&&!state.reading:inPeek&&!!state.peekTree;
      tree.hidden=!showTree;
      if(inline)inline.hidden=!showTree;
    }
    const material=['files','notes'].includes(state.section)&&!!currentFile(),output=state.inspection?.origin==='task';
    const treeVisible=inPeek?!!state.peekTree:!state.treeHidden&&!state.reading;
    const toggle=document.getElementById('pwToggleTree');toggle.hidden=state.section!=='files'||output;toggle.textContent=treeVisible?'收起目录':'显示目录';toggle.setAttribute('aria-expanded',String(treeVisible));
    document.getElementById('pwFileToolbar').hidden=state.section!=='files'||!state.fileRow;
    document.querySelector('[data-pw-root-folder]')?.setAttribute('aria-current',String(!state.fileRow&&!state.fileDirectory));
    document.getElementById('pwUseMaterial').hidden=!material;
    document.getElementById('pwQuote').hidden=!material;
    document.getElementById('pwMaterialTask').hidden=!material||state.section==='files'||output;
    document.getElementById('pwFocus').hidden=state.section!=='notes'&&!material;document.getElementById('pwFocus').textContent=state.reading?'退出专注':'专注阅读';
    const back=document.getElementById('pwBack');
    const returnToRun=state.section==='node-result';
    back.hidden=!output&&!returnToRun&&state.section!=='task-detail'&&!inPeek&&!(fileBrowser&&state.fileRow)&&state.inspection?.origin!=='chat';
    back.querySelector('span').textContent=inPeek?'返回对话':returnToRun?'返回运行产物':output?'返回任务':fileBrowser?'返回文件列表':state.section==='task-detail'?'任务列表':'返回对话';
    document.getElementById('pwCloseDoc').hidden=state.surface!=='chat'||state.section==='node-result';
    document.getElementById('pwTrail').textContent=state.section==='node-result'?'产物工作台 · 节点产物':output?'任务产物 · 固定版本':state.section==='task-detail'?'任务与核查':(surfaces[resourceSurface[state.section]]||'资料')+(state.fileRow&&state.section==='files'?' / '+state.fileRow.rel.split('/').slice(0,-1).join('/'):'');
    if(state.review)paintReview(state,state.review);else document.getElementById('pwReview').hidden=true;
  }
  async function switchSurface(surface,state=mounted){
    if(!live(state)||!surfaces[surface]||!await flush())return;
    saveDock(state);
    if(state.section==='node-result'){
      state.navHidden=false;
      state.treeHidden=false;
    }
    if(state.inspection?.origin==='task')state.fileRow=state.libraryFile;
    state.navOpen=false;state.reading=false;state.peek=false;state.inspection=null;
    if(surface==='chat'){state.resourceEpoch++;disposeResource();state.surface='chat';state.chatView='chat';persist(state);layoutPanels(state);paintChatView(state);document.getElementById('pwAsk').focus();return;}
    await showResource((surface==='workbench'?'run-artifacts':state.sections[surface])||{overview:'overview',library:'files',workbench:'run-artifacts',tasks:'tasks',flow:'workflow'}[surface],state);
  }
  function closeNavigator(state){state.navOpen=false;persist(state);layoutPanels(state);document.getElementById('pwToggleNav').focus();}

  async function render(container){
    if(!curTask||!(S.tasks||[]).some(t=>t.id===curTask))needTask();
    if(!curTask){container.innerHTML=`<section class="card"><h2>项目工作区</h2><p class="hint">先建立一个项目，再添加资料、专家和研究任务。</p><button class="btn btn-primary" id="pwFirstProject">新建项目</button></section>`;document.getElementById("pwFirstProject").onclick=()=>ProjectManager.open();return;}
    if(mounted&&root()&&mounted.id!==curTask){const previous=mounted;if(!await flush()){curTask=previous.id;localStorage.setItem("rw-task",curTask);document.getElementById("pwProject").value=curTask;renderStatus();return;}}
    const state=get(curTask);const requested=intent;intent=null;
    if(root()&&mounted===state){
      document.getElementById("pwProject").innerHTML=projectOptions(state.id);document.getElementById("pwComposeProject").innerHTML=projectOptions(state.id);paintSessions(state);refresh(state);
      if(requested)await showResource(requested.section,state);else if(state.surface==='flow'&&state.section==='workflow')await showResource('workflow',state);return;
    }
    generation++;mounted=state;state.epoch=++generation;
    const pool=(S.chats||[]).filter(c=>c.task_id===state.id&&!String(c.id||'').startsWith('execution-'));if(!state.chatId||!(S.chats||[]).some(c=>c.id===state.chatId&&c.task_id===state.id))state.chatId=pool[0]?.id||"";
    container.innerHTML=shell(state);bind(state);layoutPanels(state);paintSessions(state);paintDraft(state);paintChat(state);
    if(requested)await showResource(requested.section,state,{initial:true});
    else if(state.surface!=='chat'||state.peek)await showResource(resources[state.section]?state.section:state.sections[state.surface],state,{initial:true,peek:state.peek,surface:state.surface});
    if(live(state))refresh(state);
    if(!restored){restored=true;restoreRuns();}
    clearInterval(timer);timer=setInterval(()=>{if(live(state)&&!document.hidden)refresh(state);else if(!active())clearInterval(timer);},4000);
  }
  async function uploadFiles(state,files,{imagesOnly=false,libraryOnly=false}={}){
    if(state.sending)return error('当前正在上传或提交，请稍候',state);
    if(files.length>20)return error('每批最多 20 个文件',state);
    const targetDraft=draft(state),imageCount=libraryOnly?0:files.filter(file=>imagesOnly||FileDrop.image(file)).length;
    if((targetDraft.images||[]).length+imageCount>5)return error('每条对话最多 5 个图片文件；批量图片可使用论文工具箱',state);
    state.sending=true;error('',state);const errors=[];let done=0;
    try{for(const file of files){try{
      notice(`正在上传 ${done+1}/${files.length}：${file.name}`,state);
      if(!libraryOnly&&(imagesOnly||FileDrop.image(file))){
        if(file.size>20*1024*1024)throw new Error('图片最多 20 MB');
        const asset=await api('/api/paper-toolbox/upload',{project_id:state.id,name:file.name,data:await FileDrop.dataURL(file)});
        (targetDraft.images||=[]).push(asset);
      }else{
        const asset=await FileDrop.project(file,state.id,percent=>notice(`正在上传：${file.name} · ${percent}%`,state));
        if(!libraryOnly)(targetDraft.refs||=[]).push({name:asset.name,relative:asset.relative,asset_id:asset.asset_id});
      }
      done++;persist(state);paintRefs(state);
    }catch(err){errors.push(file.name+'：'+err.message);}}
    if(live(state)&&state.section==='files'&&state.surface!=='chat')await refreshFiles(state.id,'');
    notice(`已上传 ${done}/${files.length} 个文件`+(libraryOnly?'，保存在「其余 / 上传文件」':'，可继续发送消息'),state);
    if(errors.length)error(errors.join('；'),state);
    }finally{state.sending=false;paintSend(state);}
  }
  function bind(state){
    document.getElementById('pwAddImage').onclick=()=>document.getElementById('pwImageInput').click();
    document.getElementById('pwImageInput').onchange=event=>{const files=[...event.target.files];event.target.value='';uploadFiles(state,files,{imagesOnly:true});};
    let uploadToLibrary=false;
    document.getElementById('pwAddFile').onclick=()=>{uploadToLibrary=false;document.getElementById('pwFileInput').click();};
    document.getElementById('pwUploadFile').onclick=()=>{uploadToLibrary=true;document.getElementById('pwFileInput').click();};
    document.getElementById('pwFileInput').onchange=event=>{const files=[...event.target.files];event.target.value='';uploadFiles(state,files,{libraryOnly:uploadToLibrary});};
    FileDrop.bind(root(),{label:'松开鼠标上传到当前项目',busy:()=>state.sending,onError:message=>error(message,state),onFiles:(files,event)=>uploadFiles(state,files,{libraryOnly:!event.target.closest('.pw-chat')})});
    document.getElementById("pwPrepareTask").onclick=()=>send(state,true);
    root().querySelectorAll('[data-pw-chat-view]').forEach(button=>button.onclick=()=>{state.chatView=button.dataset.pwChatView;paintChatView(state);});
    for(const [id,key] of [['pwModel','model_id'],['pwPermission','permission'],['pwReasoning','reasoning']])document.getElementById(id).onchange=e=>saveChatOption(state,key,e.target.value);
    document.getElementById('pwSearchInput').value=state.searchQuery||'';
    document.getElementById('pwSearchForm').onsubmit=e=>{e.preventDefault();state.searchQuery=document.getElementById('pwSearchInput').value.trim();showResource('search',state);};
    document.getElementById('pwRebuildAssets').onclick=async()=>{try{await api('/api/project/resources/index',{project_id:state.id});const result=await api('/api/assets/diagnostics?project_id='+encodeURIComponent(state.id));notice(`资料索引已整理：${result.assets||0} 项资料，${result.conflicts||0} 项冲突。`,state);}catch(err){error('资料索引整理失败：'+err.message,state);}};
    for(const selectorId of ['pwProject','pwComposeProject'])document.getElementById(selectorId).onchange=async event=>{
      const id=event.target.value;event.target.disabled=true;
      try{await switchProject(id);}finally{for(const key of ['pwProject','pwComposeProject']){const select=document.getElementById(key);if(select){select.value=curTask;select.disabled=false;}}}
    };
    document.getElementById("pwProjectCreate").onclick=()=>ProjectManager.open();
    document.getElementById("pwProjectEdit").onclick=()=>ProjectManager.open({edit:true});
    root().querySelectorAll("[data-pw-view]").forEach(b=>b.onclick=()=>showResource(b.dataset.pwView,state));
    root().querySelectorAll('[data-pw-surface]').forEach(b=>b.onclick=()=>switchSurface(b.dataset.pwSurface,state));
    root().querySelector('.pw-primary').onkeydown=e=>{const buttons=[...root().querySelectorAll('[data-pw-surface]')],i=buttons.indexOf(document.activeElement);if(i<0)return;let next;if(e.key==='ArrowRight')next=(i+1)%buttons.length;else if(e.key==='ArrowLeft')next=(i+buttons.length-1)%buttons.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=buttons.length-1;else return;e.preventDefault();buttons[next].focus();};
    root().querySelectorAll("[data-pw-filter]").forEach(b=>b.onclick=()=>{state.filter=b.dataset.pwFilter;showResource("tasks",state);});
    document.getElementById("pwMode").value=state.mode;
    document.getElementById("pwMode").onchange=e=>{state.mode=e.target.value;state.modeTouched=true;persist(state);notice("已选择新任务模式。正在执行的任务仍按各自的运行模式处理。",state);};
    document.getElementById("pwScope").value=state.scope;
    document.getElementById("pwScope").onchange=async e=>{state.scope=e.target.value;state.target="";persist(state);paintTargets(state);if(state.scope==='workflow'){try{state.targets=await api('/api/handoff/targets?project_id='+encodeURIComponent(state.id));if(live(state))paintTargets(state);}catch(err){notice(err.message,state);}}};
    document.getElementById("pwRecipient").onchange=e=>{state.target=e.target.value;persist(state);paintTargets(state);paintChatOptions(state);};
    document.getElementById('pwRecipientDetail').onchange=e=>{state.target=e.target.value;persist(state);paintTargets(state);paintChatOptions(state);};
    document.getElementById("pwAsk").oninput=()=>handleMentionInput(state);
    document.getElementById("pwAsk").onkeydown=e=>{if(e.isComposing||e.keyCode===229)return;const menu=document.getElementById("pwMentionMenu");if(!menu?.hidden){if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();state.mentionIndex=(state.mentionIndex+(e.key==="ArrowDown"?1:-1)+state.mentionItems.length)%state.mentionItems.length;menu.querySelectorAll("[data-pw-mention]").forEach((b,i)=>b.setAttribute("aria-selected",String(i===state.mentionIndex)));return;}if(e.key==="Enter"){e.preventDefault();insertMention(state,state.mentionIndex);return;}if(e.key==="Escape"){e.preventDefault();closeMentionMenu(state);return;}}if(e.key==="Enter"&&!e.shiftKey&&!e.ctrlKey&&!e.altKey&&!e.metaKey){e.preventDefault();send(state);}};
    document.getElementById("pwSend").onclick=()=>send(state);
    document.getElementById("pwNewChat").onclick=()=>newChat(state);
    document.getElementById("pwRenameChat").onclick=async()=>{const row=currentChat(state);if(!row)return notice("发送第一条消息后可以给对话改名。",state);const title=await uiPrompt("对话名称",row.title);if(title===null)return;await api("/api/chat/rename",{id:row.id,title});await refreshState(false);if(live(state)){paintSessions(state);document.getElementById("pwChatTitle").textContent=currentChat(state)?.title||"课题讨论";}};
    document.getElementById('pwToggleNav').onclick=()=>{if(narrow())state.navOpen=!state.navOpen;else{state.navHidden=state.reading?false:!state.navHidden;state.reading=false;}persist(state);layoutPanels(state);if(state.navOpen)document.getElementById('pwNavClose').focus();};
    document.getElementById('pwNavShade').onclick=()=>closeNavigator(state);document.getElementById('pwNavClose').onclick=()=>{if(narrow())closeNavigator(state);else{state.navHidden=true;persist(state);layoutPanels(state);}};
    document.getElementById('pwToggleTree').onclick=()=>{if(state.surface==='chat')state.peekTree=!state.peekTree;else{state.treeHidden=state.reading?false:!state.treeHidden;state.reading=false;}persist(state);layoutPanels(state);};
    document.getElementById('pwShowDoc').onclick=()=>switchSurface('library',state);
    document.getElementById('pwCloseDoc').onclick=()=>switchSurface('chat',state);
    document.getElementById('pwBack').onclick=()=>{
      if(state.peek)return switchSurface('chat',state);
      if(state.section==='node-result')return showResource('run-artifacts',state,{runId:state.nodeResultSelection?.workflow_run_id});
      if(state.inspection?.origin==='task')return openTask(state.inspection.taskId,state.id);
      if(state.surface==='library'&&state.section==='files')return browseDirectory(state,state.fileRow?.rel.split('/').slice(0,-1).join('/')||'');
      if(state.section==='task-detail')return showResource('tasks',state);
      return switchSurface('chat',state);
    };
    document.getElementById('pwFocus').onclick=async()=>{if(!await flush()||!live(state))return;if(state.surface==='chat'){state.surface='library';state.peek=false;state.inspection={origin:'chat'};state.sections.library=state.section;}state.reading=!state.reading;state.navOpen=false;persist(state);layoutPanels(state);};
    document.getElementById("pwBindings").onclick=()=>ProjectTasks.openBindings(state.id).then(d=>d?.addEventListener("close",()=>refresh(state),{once:true}));
    document.getElementById("pwMembers").onclick=()=>uiNavigate(()=>openTab("team"));
    document.getElementById("pwExperts").onclick=()=>uiNavigate(()=>openTab("assistants"));
    const compose=async data=>{if(!await flush())return;const dialog=await ProjectTasks.compose(state.id,data);dialog?.addEventListener('close',()=>refresh(state),{once:true});};
    document.getElementById('pwTopLanggraph').onclick=()=>{
      if(state.surface==='chat'&&state.peek&&state.section==='workflow')return switchSurface('chat',state);
      const activeRun = state.dockRunId || '';
      return openWorkflowDock(state.id, activeRun);
    };
    const dockLauncher=document.getElementById('pwDockLauncher');
    if(dockLauncher)dockLauncher.onclick=()=>showResource(dockSections.has(state.section)?state.section:'workflow',state,{peek:true});
    root().querySelectorAll('[data-pw-dock]').forEach(tab=>tab.onclick=()=>showResource(tab.dataset.pwDock,state,{peek:true}));
    const dockCollapse=document.getElementById('pwDockCollapse');
    if(dockCollapse)dockCollapse.onclick=()=>switchSurface('chat',state);
    const dockStage=document.getElementById('pwDockStageToggle');
    if(dockStage)dockStage.onclick=()=>showResource(state.section||'workflow',state,{peek:false});
    const dockNewFlow=document.getElementById('pwDockNewFlow');
    if(dockNewFlow)dockNewFlow.onclick=()=>showResource('workflow',state,{peek:true});
    root().querySelectorAll('[data-pw-quick]').forEach(btn=>btn.onclick=()=>{
      const action=btn.dataset.pwQuick;
      if(action==='library')return switchSurface('library',state);
      if(action==='workflow')return showResource('workflow',state,{peek:true});
      if(action==='bindings')return document.getElementById('pwBindings').click();
      if(action==='tasks')return switchSurface('tasks',state);
    });
    document.getElementById('pwCreateTask').onclick=()=>compose({goal:draft(state).text,inputs:(draft(state).refs||[]).map(r=>({relative:r.relative}))});
    document.getElementById('pwMaterialTask').onclick=()=>{const file=currentFile();if(file)compose({inputs:[{relative:file.relative}]});};
    document.getElementById('pwAddReference').onclick=()=>state.peek&&currentFile()?addReference(state):switchSurface('library',state);
    document.getElementById('pwUseMaterial').onclick=()=>addReference(state);
    document.getElementById("pwQuote").onclick=()=>quote(state);
    root().addEventListener("click",e=>{
      const fileLink=e.target.closest('.md a[href]');
      if(fileLink&&typeof WorkflowFilePreview!=='undefined'){
        const file=WorkflowFilePreview.reference(fileLink.getAttribute('href'));
        if(file){e.preventDefault();openFilePreview(file,state.id).catch(err=>error(err.message,state));return;}
      }
      const mode=e.target.closest('[data-fl-mode]');if(mode)root().dataset.fileMode=mode.dataset.flMode;
      const ref=e.target.closest('[data-pw-ref]');if(ref)openReference(state,Number(ref.dataset.pwRef));
      if(e.target.closest('#pwProjectSettings button'))document.getElementById('pwProjectSettings').open=false;
      const b=e.target.closest("[data-pw-task],[data-pw-start],[data-pw-cancel],[data-pw-remove-ref]");if(!b)return;
      if(b.dataset.pwTask)openTask(b.dataset.pwTask,state.id);
      if(b.dataset.pwStart)taskCommand(state,b.dataset.pwStart,"start");
      if(b.dataset.pwCancel)taskCommand(state,b.dataset.pwCancel,"cancel");
      if(b.hasAttribute("data-pw-remove-ref")){draft(state).refs.splice(Number(b.dataset.pwRemoveRef),1);persist(state);paintRefs(state);}
    });
    root().addEventListener('keydown',e=>{
      if(e.key==='Escape'&&document.getElementById('pwProjectSettings').open){e.preventDefault();e.stopPropagation();document.getElementById('pwProjectSettings').open=false;document.querySelector('#pwProjectSettings summary').focus();return;}
      const panel=narrow()&&state.navOpen?document.getElementById('pwProjectNav'):narrow()&&state.peek?document.getElementById('pwDocumentPane'):null;
      if(!panel)return;
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();if(state.navOpen)closeNavigator(state);else switchSurface('chat',state);return;}
      if(e.key==='Tab'){const items=[...panel.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')].filter(el=>!el.disabled&&el.getClientRects().length&&!el.closest('[hidden],details:not([open])'));const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
    });
  }
  async function switchProject(id){
    if(!id||id===mounted?.id)return;
    if(!S.tasks.some(project=>project.id===id))throw new Error('项目不存在');
    const old=mounted;if(!await flush()){if(document.getElementById("pwProject"))document.getElementById("pwProject").value=old.id;return;}
    persist(old);curTask=id;localStorage.setItem("rw-task",id);spId=null;flSel="";flCurrentFile=null;await window.render();
  }
  function paintSessions(state){
    if(!live(state))return;
    const history=ConversationHistory.mount(document.getElementById('pwSessions'),{projectId:state.id,selected:()=>state.chatId,onSelect:id=>selectChat(state,id),onChanged:async()=>{
      if(!live(state))return;
      if(state.chatId&&!(S.chats||[]).some(chat=>chat.id===state.chatId)){await changeConversation(state,'');paintDraft(state);await paintChat(state);}
      document.getElementById('pwChatTitle').textContent=currentChat(state)?.title||'新的课题讨论';
    }});
    document.getElementById("pwChatTitle").textContent=currentChat(state)?.title||"新的课题讨论";
    return history.ready;
  }
  async function selectChat(state,id){
    if(state.sending||state.creating)return notice('正在保存当前请求，请稍后切换对话。',state);
    if(!live(state)||!await flush())return;
    const belongsToProject=()=>S.chats?.some(chat=>chat.id===id&&chat.task_id===state.id);
    if(id&&!belongsToProject()){
      try{await refreshState(false);}catch(err){return error(err.message,state);}
      if(!live(state))return;
      if(!belongsToProject())return error('对话已删除、归档或不属于当前项目。',state);
    }
    await changeConversation(state,id);paintSessions(state);paintDraft(state);paintChat(state);error("",state);
  }
  async function newChat(state){
    if(!live(state)||state.sending||state.creating)return;
    state.creating=true;document.getElementById('pwNewChat').disabled=true;
    try{
      if(!await flush())return;
      if(!state.chatId&&(draft(state).text.trim()||draft(state).refs.length))await ensureChat(state);
      const result=await api('/api/chat/create',{title:'新的课题讨论',task_id:state.id});
      await changeConversation(state,result.chat.id);await refreshState(false);
      if(live(state)){await paintSessions(state);if(!live(state))return;paintDraft(state);await paintChat(state);document.getElementById('pwAsk').focus();}
    }catch(err){error(err.message,state);}
    finally{state.creating=false;if(live(state))document.getElementById('pwNewChat').disabled=false;}
  }
  function promptKey(state){return `workspace:${state.id}:${state.chatId||'new'}`;}
  function paintDraft(state){if(!live(state))return;document.getElementById("pwAsk").value=draft(state).text||"";PromptUI.select(promptKey(state),draft(state).template);PromptUI.mount(document.getElementById('pwAsk'),promptKey(state),state.id,value=>{draft(state).template=value;persist(state);});paintRefs(state);paintSend(state);paintChatOptions(state);}
  function paintChatOptions(state){
    if(!live(state))return;const chat=currentChat(state)||{},model=document.getElementById('pwModel');
    model.innerHTML='<option value="">跟随专家 / 当前模型</option>'+(S.models||[]).map(item=>`<option value="${h(item.id)}">${h(item.name||item.model)}</option>`).join('');model.value=chat.model_id||'';
    document.getElementById('pwPermission').value=chat.permission||'auto';document.getElementById('pwReasoning').value=chat.reasoning||'auto';
    document.querySelectorAll('#pwRuntime select').forEach(item=>item.disabled=state.scope==='workflow'||!!state.optionSaving);
    const hint=document.getElementById('pwRuntimeHint');
    if(state.scope==='workflow'||state.optionSaving){hint.dataset.effective='';hint.textContent=state.optionSaving?'正在保存…':'节点任务使用执行者配置';}
    else{
      const target=targetRows(state).find(item=>item.value===state.target);
      AgentRuntime.effective('pw',state.chatId,target?.binding?{executor_binding_id:target.binding.id,member_id:target.member?.id||''}:{});
    }
  }
  async function saveChatOption(state,key,value){
    if(!live(state)||state.optionSaving)return;state.optionSaving=true;paintChatOptions(state);
    try{const id=await ensureChat(state);if(!live(state))return;paintChatOptions(state);const result=await api('/api/agent-settings/runtime-save',{id,[key]:value});const item=(S.chats||[]).find(chat=>chat.id===id);if(item)Object.assign(item,result.chat);if(live(state)&&state.chatId===id)notice('已保存本会话配置，下次发送时生效。',state);}
    catch(err){error('会话配置保存失败：'+err.message,state);}finally{state.optionSaving=false;if(live(state))paintChatOptions(state);}
  }
  function mentionDraft(state){const value=draft(state);value.mentions=Array.isArray(value.mentions)?value.mentions:[];return value.mentions;}
  function closeMentionMenu(state){const menu=document.getElementById('pwMentionMenu');if(menu){menu.hidden=true;menu.replaceChildren();}state.mentionQuery='';state.mentionItems=[];state.mentionIndex=0;}
  async function mentionCatalog(state){
    if(state.mentionCatalog)return state.mentionCatalog;const items=[];
    if(!state.targets){try{state.targets=await api('/api/handoff/targets?project_id='+encodeURIComponent(state.id));}catch{}}
    if(state.targets?.layout_id){items.push({kind:'workflow',id:state.targets.layout_id,label:state.targets.layout_name||'当前工作流'});for(const node of state.targets.nodes||[])items.push({kind:'node',id:node.id,label:'节点 · '+(node.label||node.id),node});}
    try{const tree=await api('/api/tree?id='+encodeURIComponent(state.id)+'&rel=');for(const row of tree.rows||[]){if(!row.dir)items.push({kind:'file',id:row.rel,label:'文件 · '+row.rel,relative:row.rel,name:row.name||row.rel});}}catch{}
    try{const capabilities=await api('/api/capabilities?project_id='+encodeURIComponent(state.id));for(const tool of capabilities.tools||[])items.push({kind:'tool',id:tool.id||tool.name,label:'工具 · '+(tool.label||tool.name)});for(const skill of capabilities.skills||[])items.push({kind:'skill',id:skill.id||skill.name,label:'Skill · '+(skill.name||skill.id)});}catch{}
    state.mentionCatalog=items;return items;
  }
  async function openMentionMenu(state){const ask=document.getElementById('pwAsk'),menu=document.getElementById('pwMentionMenu');if(!ask||!menu)return;const match=ask.value.slice(0,ask.selectionStart).match(/(?:^|\s)@([^\s@]*)$/);if(!match){closeMentionMenu(state);return;}state.mentionQuery=match[1].toLowerCase();state.mentionStart=ask.selectionStart-match[1].length-1;const all=await mentionCatalog(state),items=all.filter(item=>!state.mentionQuery||item.label.toLowerCase().includes(state.mentionQuery)||item.id.toLowerCase().includes(state.mentionQuery)).slice(0,12);state.mentionItems=items;state.mentionIndex=0;menu.innerHTML=items.length?items.map((item,index)=>`<button type="button" role="option" data-pw-mention="${h(item.id)}" data-pw-mention-index="${index}"><span>${h(item.label)}</span><small>${h(item.kind)}</small></button>`).join(''):'<p class="hint">没有匹配的工作流、文件、工具或 Skill</p>';menu.hidden=false;menu.querySelectorAll('[data-pw-mention]').forEach(button=>button.onclick=()=>insertMention(state,Number(button.dataset.pwMentionIndex)));}
  function insertMention(state,index){const ask=document.getElementById('pwAsk'),item=state.mentionItems?.[index];if(!ask||!item)return;const start=state.mentionStart,end=ask.selectionStart,label=item.label.replace(/^(文件|工具|Skill|节点) · /,'');ask.value=ask.value.slice(0,start)+'@'+label+' '+ask.value.slice(end);ask.selectionStart=ask.selectionEnd=start+label.length+2;const value=mentionDraft(state);if(item.kind==='file'&&!value.refs.some(ref=>ref.relative===item.relative))value.refs.push({relative:item.relative,name:item.name||item.relative,mention:true});else if(item.kind==='workflow'||item.kind==='node'){state.scope='workflow';state.target='';document.getElementById('pwScope').value='workflow';if(!value.some(mention=>mention.kind===item.kind&&mention.id===item.id))value.push({kind:item.kind,id:item.id,label:item.label});}else if(!value.some(mention=>mention.kind===item.kind&&mention.id===item.id))value.push({kind:item.kind,id:item.id,label:item.label});persist(state);paintRefs(state);paintTargets(state);closeMentionMenu(state);ask.focus();}
  function handleMentionInput(state){draft(state).text=document.getElementById('pwAsk').value;persist(state);openMentionMenu(state);}
  function paintRefs(state){if(!live(state))return;const value=draft(state),refs=value.refs||[],mentions=mentionDraft(state);document.getElementById('pwRefs').innerHTML=refs.map((r,i)=>`<span class="pw-ref" title="${h(r.relative)}"><button type="button" data-pw-ref="${i}" aria-label="查看引用 ${h(r.name||r.relative)}">${h(r.name||r.relative)}${r.quote?' · 选区':''}</button><button type="button" data-pw-remove-ref="${i}" aria-label="移除引用 ${h(r.name||r.relative)}">×</button></span>`).join('')+mentions.map((m,i)=>`<span class="pw-ref pw-mention" title="${h(m.id)}"><span>@${h(m.label||m.id)}</span><button type="button" data-pw-remove-mention="${i}" aria-label="移除 @${h(m.label||m.id)}">×</button></span>`).join('');const host=document.getElementById('pwRefs');host.insertAdjacentHTML('beforeend',(value.images||[]).map((item,index)=>`<span class="pw-ref">${h(item.name)}<button type="button" data-remove-image="${index}" aria-label="移除图片">×</button></span>`).join(''));host.querySelectorAll('[data-remove-image]').forEach(button=>button.onclick=()=>{value.images.splice(Number(button.dataset.removeImage),1);persist(state);paintRefs(state);});host.querySelectorAll('[data-pw-remove-mention]').forEach(button=>button.onclick=()=>{mentions.splice(Number(button.dataset.pwRemoveMention),1);persist(state);paintRefs(state);});}
  function paintSend(state){
    if(!live(state))return;
    const running=state.chatId&&chatRecord(state.chatId).run?.status==="running",button=document.getElementById("pwSend");
    button.textContent=running?"■":state.scope==="workflow"?"↗":"↑";button.title=running?"停止当前对话":state.scope==="workflow"?"准备任务":"发送";button.setAttribute("aria-label",button.title);button.classList.toggle("stop",!!running);document.getElementById("pwPrepareTask").hidden=!state.target||state.scope==="workflow";document.getElementById("pwPrepareTask").disabled=!!running||!!state.sending;
    document.getElementById("pwScopeLabel").textContent=state.scope==="workflow"?"当前工作流 · 目标归属具体节点":"当前项目 · 已授权执行者";
  }
  function paintChatView(state,renderExecution=true){
    if(!live(state))return;const trace=state.chatView==='trajectory';document.querySelector('.pw-chat').classList.toggle('pw-viewing-trajectory',trace);
    document.getElementById('pwMessages').hidden=trace;document.getElementById('pwTrajectory').hidden=!trace;
    root().querySelectorAll('[data-pw-chat-view]').forEach(button=>button.setAttribute('aria-selected',String((trace?'trajectory':'chat')===button.dataset.pwChatView)));
    if(renderExecution&&state.chatId?.startsWith('execution-')){const record=chatRecord(state.chatId);if(record.execution)ExecutionConversation.render(trace?document.getElementById('pwTrajectory'):document.getElementById('pwMessages'),{execution:record.execution,messages:record.messages},()=>{record.revision='';return paintChat(state);});}
    const indicator=root().querySelector('[data-execution-indicator]');if(indicator&&!state.chatId?.startsWith('execution-'))indicator.remove();
    if(trace&&!state.chatId?.startsWith('execution-'))WorkspaceTrajectory.mount(document.getElementById('pwTrajectory'),{chatId:state.chatId,projectId:state.id,live:()=>live(state)&&state.surface==='chat'&&state.chatView==='trajectory'});
  }
  function append(box,event){appendOne(box,event);}
  async function paintChat(state){
    if(!live(state))return;
    paintChatView(state,false);
    const box=document.getElementById("pwMessages"),id=state.chatId;
    ChatPresentation.mount(box,id||"new");
    if(!id)box.dataset.chatId='';
    if(!id){ChatPresentation.replace(box,"new",()=>{box.innerHTML="";});box.innerHTML=`<div class="pw-empty"><h3>从一个研究问题开始</h3><p>讨论目标、引用资料，或把具体工作交给本项目的专家与团队。</p><div class="pw-empty-actions"><button type="button" class="btn" data-pw-empty="files">查看项目文件</button><button type="button" class="btn" data-pw-empty="papers">导入项目文献</button><button type="button" class="btn" data-pw-empty="bindings">加入项目专家</button></div></div>`;box.querySelectorAll("[data-pw-empty]").forEach(b=>b.onclick=()=>b.dataset.pwEmpty==="bindings"?document.getElementById("pwBindings").click():showResource(b.dataset.pwEmpty,state));return;}
    const record=chatRecord(id),seq=++record.request;
    if(record.loaded&&box.dataset.chatId!==id)drawMessages(state,id);else if(!record.loaded&&box.dataset.chatId!==id){box.dataset.chatId=id;box.textContent="正在读取对话…";}
    try{const result=await api("/api/chat?id="+encodeURIComponent(id));if(seq!==record.request)return;const changed=!result.revision||record.revision!==result.revision;record.messages=result.messages||[];record.execution=result.execution;record.revision=result.revision;record.loaded=true;if(live(state)&&state.chatId===id){if(state.executionTargetChat!==id){state.executionTargetChat=id;if(record.execution){const execution=record.execution;state.scope='project';state.target=execution.kind==='task'?(execution.member_id?'member:'+execution.executor_binding_id+':'+execution.member_id:'binding:'+execution.executor_binding_id):'';document.getElementById('pwScope').value='project';paintTargets(state);paintChatOptions(state);persist(state);}}if(changed||box.dataset.chatId!==id)drawMessages(state,id);clearTimeout(state.executionRefresh);if(record.execution)state.executionRefresh=setTimeout(()=>{if(live(state)&&state.chatId===id&&state.surface==='chat'&&!record.run)paintChat(state);},2000);}}catch(err){if(live(state)&&state.chatId===id)error(err.message,state);}
  }
  function drawMessages(state,id){
    if(!live(state)||state.chatId!==id)return;
    const record=chatRecord(id),box=document.getElementById("pwMessages");ChatPresentation.replace(box,id,()=>{box.dataset.chatId=id;if(record.execution){const refresh=()=>{record.revision="";return paintChat(state);};ExecutionConversation.render(state.chatView==="trajectory"?document.getElementById("pwTrajectory"):box,{execution:record.execution,messages:record.messages},refresh);if(record.run?.status==="running")record.events.forEach(ev=>{if(ev.type!=='user')append(box,ev);});return;}box.innerHTML="";
    const history=record.run?.status==="running"?record.base||record.messages:record.messages;
    const seenUserInputs=new Set();
    history.forEach(m=>{
      const events=Array.isArray(m.events)?m.events.filter(event=>{
        if(event.type!=="user")return true;
        const key=userInputKey(event.content);if(!key)return true;
        if(seenUserInputs.has(key))return false;
        seenUserInputs.add(key);return true;
      }):m.events;
      if(m.role==="user"){const key=userInputKey(m.content);if(key)seenUserInputs.add(key);}
      append(box,{...m,events,type:m.role,replay:true,at:m.created_at});
    });
    if(record.run?.status==="running"){record.events.forEach(ev=>{if(ev.type==='user'&&ev.local&&history.some(item=>item.role==='user'&&userInputKey(item.content)===userInputKey(ev.content)))return;append(box,ev);});}
    if(!box.children.length)box.innerHTML='<div class="pw-empty">这个对话已绑定当前项目，可以引用资料并提出目标。</div>';
    });
  }
  async function ensureChat(state){
    if(currentChat(state))return state.chatId;
    const saved=draft(state),made=await api("/api/chat/create",{title:deriveChatTitle(saved.text,"新的课题讨论"),task_id:state.id});
    state.chatId=made.chat.id;state.drafts[state.chatId]={...saved,refs:[...(saved.refs||[])]};delete state.drafts.new;persist(state);
    await refreshState(false);if(live(state))paintSessions(state);return state.chatId;
  }
  async function send(state,asTask=false){
    if(!live(state)||state.sending||state.creating||state.optionSaving)return;
    const running=state.chatId&&chatRecord(state.chatId).run;
    if(running?.status==="running"){
      running.status="cancelled";
      paintSend(state);
      await api("/api/agent/cancel",{idempotency_key:running.key}).catch(()=>{});
      return;
    }
    draft(state).text=document.getElementById("pwAsk").value;
    const submission={text:draft(state).text,refs:[...(draft(state).refs||[])],mentions:[...mentionDraft(state)],images:[...(draft(state).images||[])],template:PromptUI.ref(promptKey(state),state.id)},goal=submission.text.trim();if(!goal)return;
    state.sending=true;document.getElementById("pwSend").disabled=true;error("",state);
    try{
      if(!await flush())return;
      if(state.target&&(asTask||state.scope==='workflow')){await prepareTask(state,goal,submission);return;}
      const selectedModel=currentChat(state)?.model_id,model=(S.models||[]).find(item=>item.id===selectedModel)||S.active;
      const localModel=model && (()=>{try{return ['127.0.0.1','localhost','[::1]'].includes(new URL(model.base_url).hostname);}catch{return false;}})();
      if((!localModel&&!selectedModel&&!await requireModelKey())||!live(state))return;
      const id=await ensureChat(state),record=chatRecord(id),refs=submission.refs;
      const chatRow=currentChat(state);
      if(chatRow&&DEFAULT_CHAT_TITLES.has(String(chatRow.title||'').trim())){
        const autoTitle=deriveChatTitle(goal,'');
        if(autoTitle&&!DEFAULT_CHAT_TITLES.has(autoTitle)){
          chatRow.title=autoTitle;
          if(live(state)){const titleEl=document.getElementById("pwChatTitle");if(titleEl)titleEl.textContent=autoTitle;paintSessions(state);}
          api("/api/chat/rename",{id,title:autoTitle}).catch(()=>{});
        }
      }
      const mentions=submission.mentions||[],context=refs.length?"\n\n【当前项目明确引用的材料】\n"+refs.map(r=>r.quote?`文件：${r.relative}\n原文选区：\n${r.quote}`:`文件：${r.relative}`).join("\n\n"):"";
      const mentionContext=mentions.length?"\n\n【@上下文】\n"+mentions.map(item=>"@"+(item.label||item.id)+"（"+item.kind+"："+item.id+"）").join("、"):"";
      const selected=state.target?targetRows(state).find(row=>row.value===state.target):null;if(state.target&&!selected)throw new Error('执行者已移除，请重新选择');
      const run={key:crypto.randomUUID(),api:"/api/agent",runId:"",cursor:0,status:"running",startedAt:new Date().toISOString(),projectId:state.id,chatId:id,draftText:submission.text,draftImages:submission.images,request:{task_id:id,executor_binding_id:selected?.binding?.id||'',member_id:selected?.member?.id||'',image_ids:submission.images.map(item=>item.asset_id),message:goal+context+mentionContext,mentions,template_ref:submission.template,idempotency_key:""}};run.request.idempotency_key=run.key;
      record.lastError="";record.run=run;run.base=[...record.messages];record.base=run.base;
      const binding=selected?.binding;
      if(binding?.kind==='team'){
        const team=state.teams.find(item=>item.id===binding.target_id);
        run.assistantId=(selected.member || team?.members?.find(member=>member.role==='leader'))?.assistant_id;
      }else if(binding?.kind==='assistant'){
        run.assistantId=binding.target_id;
      }
      record.events=[{type:"user",content:goal+context+mentionContext,at:run.startedAt},{type:"chat_waiting",assistant_id:run.assistantId,started_at:run.startedAt}];
      if(draft(state).text===submission.text){draft(state).text="";draft(state).images=[];}persist(state);paintDraft(state);drawMessages(state,id);persistRuns();follow(run,record);
    }catch(err){error(err.message,state);}finally{state.sending=false;if(live(state)){document.getElementById("pwSend").disabled=false;paintSend(state);}}
  }
  function persistRuns(){write("rw-workspace-runs",[...chats.values()].filter(c=>c.run?.status==="running").map(c=>c.run));}
  async function follow(run,record){
    await observeAgentRun(run,event=>{
      if(event.type==='user')return;
      record.events.push(event);const state=get(run.projectId);
      if(live(state)&&state.chatId===run.chatId){
        const box=document.getElementById("pwMessages");
        if(["done","cancelled"].includes(event.type)){
          updateChatActivity(box,event.type,null,event);
          if(event.type==="done"&&!record.events.some(e=>e.type==="assistant"))append(box,{...event,type:"assistant",assistant_id:event.assistant_id||run.assistantId,content:event.content||"执行结束"});
        }else append(box,event);
        if(event.type==='tool_result'&&event.ok&&event.navigation?.open_right_stage){
          const targetTab=event.navigation.dock_tab||'workflow';
          if(event.navigation.workflow_run_id)state.focusWorkflow={runId:String(event.navigation.workflow_run_id),nodeId:''};
          showResource(targetTab,state,{peek:true});
        }
      }
      if(event.type==="error")record.lastError=event.message;paintBackground();
    },persistRuns,()=>{const state=get(run.projectId);if(live(state)&&state.chatId===run.chatId){
      updateChatActivity(document.getElementById("pwMessages"),"chat_reconnecting");
    }});
    const owner=get(run.projectId);
    if(live(owner)&&owner.chatId===run.chatId)updateChatActivity(document.getElementById("pwMessages"),run.status);
    if(!run.runId&&run.status==='failed'){
      const failedDraft=owner.drafts[run.chatId];
      if(failedDraft&&failedDraft.text.length===0&&!(failedDraft.images||[]).length){
        failedDraft.text=run.draftText||'';
        failedDraft.images=run.draftImages||[];
        persist(owner);
        if(live(owner)&&owner.chatId===run.chatId)paintDraft(owner);
      }
    }
    record.run=null;record.events=[];record.loaded=false;persistRuns();
    try{await refreshState(false);const state=get(run.projectId);if(live(state)){paintSessions(state);paintSend(state);if(state.chatId===run.chatId){await paintChat(state);if(record.lastError)error(record.lastError,state);}refresh(state);}}catch{}
    paintBackground();
  }
  function restoreRuns(){
    for(const saved of read("rw-workspace-runs",[])){
      if(!saved.projectId||!(S.tasks||[]).some(p=>p.id===saved.projectId)||!(S.chats||[]).some(c=>c.id===saved.chatId&&c.task_id===saved.projectId))continue;
      const record=chatRecord(saved.chatId);if(record.run)continue;saved.status="running";saved.cursor=0;record.run=saved;record.base=saved.base||[];record.events=[{type:'user',content:saved.request.message,at:saved.startedAt,local:true},{type:"chat_waiting",assistant_id:saved.assistantId,started_at:saved.startedAt}];follow(saved,record);
    }
    paintBackground();
  }
  function paintBackground(){
    const host=document.getElementById("pwBackground");if(!host)return;
    const runs=[...chats.values()].map(c=>c.run).filter(r=>r?.status==="running"&&r.projectId!==mounted?.id);
    host.innerHTML=runs.map(r=>`<button type="button" class="btn ghost" data-pw-background="${h(r.projectId)}">${h((S.tasks||[]).find(p=>p.id===r.projectId)?.title||"其他项目")} · 对话执行中</button>`).join("");
    host.querySelectorAll('[data-pw-background]').forEach(b=>b.onclick=()=>switchProject(b.dataset.pwBackground));
  }
  function selectedBinding(state){
    if(state.scope!=='project')return null;
    const member=state.target.startsWith('member:'),identifier=member?state.target.slice(7,state.target.lastIndexOf(':')):state.target.startsWith('binding:')?state.target.slice(8):'';
    const binding=ExecutorChoices.find(state.bindings,identifier);
    if(binding&&binding.id!==identifier){state.target=(member?'member:':'binding:')+binding.id+(member?state.target.slice(state.target.lastIndexOf(':')):'');persist(state);}
    return binding;
  }
  function targetRows(state){
    if(state.scope==="workflow")return (state.targets?.nodes||[]).flatMap(node=>{
      const actors=new Map();for(const actor of node.actors||[]){const value=`node:${node.id}:${actor.team_id}:${actor.member_id}:${actor.assistant_id}`,key=(actor.team_id?'team:':'assistant:')+actor.assistant_id,old=actors.get(key);if(!old||value===state.target)actors.set(key,{value,label:actor.member_name||actor.assistant_name,node,actor});}
      return [...actors.values()];
    });
    const preferred=selectedBinding(state),memberId=state.target.startsWith('member:')?state.target.slice(state.target.lastIndexOf(':')+1):'';
    return ExecutorChoices.unique(state.bindings,preferred?.id).filter(ExecutorChoices.ready).flatMap(binding=>[
      {value:'binding:'+binding.id,label:(binding.kind==='team'?'团队 · ':'专家 · ')+binding.display_name,binding},
      ...(binding.kind==='team'?ExecutorChoices.members(state.teams.find(team=>team.id===binding.target_id)?.members,memberId).filter(member=>state.bindings.some(item=>item.kind==='assistant'&&item.target_id===member.assistant_id&&ExecutorChoices.ready(item))).map(member=>({value:'member:'+binding.id+':'+member.id,label:member.name,binding,member})):[])
    ]);
  }
  function paintTargets(state){
    if(!live(state))return;
    const select=document.getElementById('pwRecipient'),rows=targetRows(state),selected=rows.find(row=>row.value===state.target);
    let primary;
    if(state.scope==='workflow'){
      const nodes=new Map();for(const row of rows){if(!nodes.has(row.node.id)||row.value===state.target)nodes.set(row.node.id,row);}
      primary=[...nodes.values()].map(row=>({...row,label:row.node.label+' · '+(row.actor.team_name||row.actor.assistant_name)}));
    }else primary=rows.filter(row=>!row.member);
    const option=row=>`<option value="${h(row.value)}">${h(row.label)}</option>`;
    const groups=state.scope==='project'?['team','assistant'].map(kind=>{const items=primary.filter(row=>row.binding.kind===kind);return items.length?`<optgroup label="${kind==='team'?'团队':'专家'}">${items.map(option).join('')}</optgroup>`:'';}).join(''):primary.map(option).join('');
    select.innerHTML='<option value="">项目专家 · 讨论与协调</option>'+groups;
    const binding=selectedBinding(state),parent=state.scope==='project'?primary.find(row=>row.binding.id===(selected?.binding.id||binding?.id)):primary.find(row=>row.node.id===selected?.node.id);
    if(state.target&&!parent&&!selected)select.insertAdjacentHTML('beforeend',`<option value="${h(state.target)}" disabled>原执行目标已失效，请重新选择</option>`);
    select.value=parent?.value||state.target;
    const control=document.getElementById('pwRecipientDetailControl'),detail=document.getElementById('pwRecipientDetail');
    const team=state.scope==='project'&&parent?.binding.kind==='team';
    const details=team?[{value:parent.value,label:'由团队负责人安排'},...rows.filter(row=>row.member&&row.binding.id===parent.binding.id)]:state.scope==='workflow'&&parent?rows.filter(row=>row.node.id===parent.node.id):[];
    control.hidden=!team&&details.length<2;
    document.getElementById('pwRecipientDetailLabel').textContent=team?'团队成员':'节点执行者';detail.setAttribute('aria-label',team?'团队成员':'节点执行者');
    detail.innerHTML=details.map(option).join('');
    if(!control.hidden){if(state.target&&!details.some(row=>row.value===state.target))detail.insertAdjacentHTML('beforeend',`<option value="${h(state.target)}" disabled>原成员已失效，请重新选择</option>`);detail.value=state.target;}
    paintSend(state);
  }
  async function refresh(state){
    if(state.refreshing)return;state.refreshing=true;const epoch=state.epoch,chatId=state.chatId;
    try{
      const result=await Promise.allSettled([api('/api/project-tasks?project_id='+encodeURIComponent(state.id)),api('/api/project-tasks/executors?project_id='+encodeURIComponent(state.id)),state.targets?.layout_id?api('/api/handoff/targets?project_id='+encodeURIComponent(state.id)):Promise.resolve(state.targets),api('/api/project-tasks/policy?project_id='+encodeURIComponent(state.id)),api('/api/execution-board?project_id='+encodeURIComponent(state.id))]);
      if(epoch!==state.epoch)return;
      if(result[0].status==='fulfilled')state.tasks=result[0].value.tasks||[];
      if(result[4].status==='fulfilled')state.executionRows=result[4].value.items||[];
      if(result[1].status==='fulfilled'){state.bindings=result[1].value.bindings||[];state.teams=result[1].value.teams||[];}
      if(result[2].status==='fulfilled')state.targets=result[2].value;
      if(result[3].status==='fulfilled'&&!state.modeTouched){state.mode=result[3].value.policy.autonomy_mode||'supervised';if(live(state))document.getElementById('pwMode').value=state.mode;}
      if(live(state)){
        if(state.chatId&&state.chatId.startsWith('execution-')&&!(state.tasks||[]).some(t=>t.chat_id===state.chatId)&&!(state.executionRows||[]).some(r=>!r.deleted_at&&r.chat_id===state.chatId)){
          await refreshState(false);
          if(live(state)&&state.chatId&&!(S.chats||[]).some(chat=>chat.id===state.chatId&&chat.task_id===state.id)){
            await changeConversation(state,'');paintDraft(state);await paintChat(state);
          }
        }
        if(state.chatId===chatId)await conversationContext(state,{force:true}).catch(()=>null);
        if(state.surface==='chat'&&!state.peek)paintSessions(state);paintTargets(state);paintTasks(state);paintBackground();if(!state.optionSaving&&!document.activeElement?.closest('#pwRuntime'))paintChatOptions(state);if(result[0].status==='rejected')error('任务读取失败：'+result[0].reason.message,state);
        const updated=state.tasks.find(t=>t.id===state.review?.id);
        if(updated&&(updated.version!==state.review.version||updated.status!==state.review.status)){
          const oldRun=state.review.latest_run,newRun=updated.latest_run,changedOutput=!!oldRun&&(oldRun.id!==newRun?.id||(oldRun.checkpoint?.output_revision||0)!==(newRun?.checkpoint?.output_revision||0));
          if(changedOutput&&showsReview(state,state.review)){state.reviewStale=true;notice('产物已有新版本。当前预览和核查仍保留原版本，请查看最新产物后重新核查。',state);paintReview(state,state.review);}
          else if(state.surface==='tasks'&&state.section==='task-detail')await openTask(updated.id,state.id);
          else{state.review=updated;paintReview(state,updated);}
        }
      }
    }finally{state.refreshing=false;}
  }
  function stateBindingName(task){return get(task.project_id).bindings.find(b=>b.id===task.executor_binding_id)?.name||'项目执行者';}
  function paintTasks(state){
    if(!live(state))return;paintExecutionCounts(state);
    if(state.section==='tasks'){if(state.taskCenterHost?.isConnected)state.taskCenterHost.__taskCenterRefresh?.();}
  }
  function paintExecutionCounts(state){
    if(!live(state))return;const rows=(state.executionRows||[]).filter(row=>!row.deleted_at);
    document.getElementById('pwRunning').textContent='运行中 '+rows.filter(t=>t.status==='running').length;
    document.getElementById('pwPending').textContent='待确认 '+rows.filter(t=>['awaiting_review','waiting_input'].includes(t.status)).length;
    document.getElementById('pwFailed').textContent='需处理 '+rows.filter(t=>['failed','blocked','interrupted'].includes(t.status)).length;
  }
  async function prepareTask(state,goal,submission){
    const selected=targetRows(state).find(t=>t.value===state.target);if(!selected)throw new Error('执行者配置已变化，请重新选择。');
    const refs=submission.refs,inputs=[...refs.map(r=>({relative:r.relative})),...(submission.images||[]).map(image=>({asset_id:image.asset_id}))];const requestKey=crypto.randomUUID();
    const taskGoal=goal+refs.filter(r=>r.quote).map(r=>`\n\n【材料选区：${r.relative}】\n${r.quote}`).join('');
    const mentionContext=(submission.mentions||[]).length?"\n\n【@上下文】\n"+(submission.mentions||[]).map(item=>"@"+(item.label||item.id)+"（"+item.kind+"："+item.id+"）").join("、"):"";
    let task;
    if(selected.node){
      const result=await api('/api/workflow/execution/prepare-node',{project_id:state.id,task_id:state.id,node_id:selected.node.id,layout_id:state.targets.layout_id,revision:state.targets.revision,
        ...selected.actor,goal:taskGoal+mentionContext,inputs,template_ref:submission.template,request_key:requestKey});task=result.task;
    }else task=(await api('/api/project-tasks/create',{project_id:state.id,title:goal.slice(0,90),goal:taskGoal+mentionContext,inputs,template_ref:submission.template,executor_binding_id:selected.binding.id,member_id:selected.member?.id||'',review_required:true,request_key:requestKey})).task;
    task=(await api('/api/project-tasks/mode',{id:task.id,project_id:state.id,autonomy_mode:state.mode})).task;
    if(draft(state).text===submission.text){draft(state).text='';draft(state).images=[];}persist(state);if(live(state)){paintDraft(state);state.tasks=[task,...state.tasks.filter(t=>t.id!==task.id)];paintTasks(state);notice('任务已准备，尚未调用模型。请在任务对话中核对目标、材料和专家后开始。',state);if(state.surface==='chat'&&!draft(state).text.trim()){state.sending=false;await openConversation(task.chat_id,state.id);}}
  }
  async function taskCommand(state,id,action){
    const task=state.tasks.find(t=>t.id===id);if(!task)return;
    if(action==='start'&&task.latest_run?.checkpoint?.phase==='tool'){ProjectTasks.openTask(id,state.id);return;}
    try{const result=await api('/api/project-tasks/'+action,{id,project_id:state.id,...(action==='start'?{autonomy_mode:state.mode,request_key:crypto.randomUUID()}:{})});state.tasks=state.tasks.map(t=>t.id===id?result.task:t);if(live(state)){error('',state);paintTasks(state);if(state.surface==='tasks'&&state.section==='task-detail'&&state.review?.id===id)await openTask(id,state.id);}}catch(err){error(err.message,state);}
  }
  async function showResource(section,state=mounted,options={}){
    if(options.surface==='workbench'&&section==='files')options={...options,surface:'library'};
    if(section==='tools')section='workflow';
    if(section==='workflow')options={...options,peek:true,surface:'chat'};
    if(!live(state))return;if(section==='discussion')return switchSurface('chat',state);
    if(section==='workflow'&&!state.chatId)await ensureChat(state);
    const chatId=state.chatId;
    const request=state.resourceRequest=(state.resourceRequest||0)+1;
    const current=()=>live(state)&&state.chatId===chatId&&state.resourceRequest===request;
    if(!options.initial&&!await flush())return;if(!current())return;notice('',state);error('',state);
    if(options.peek&&dockSections.has(section)){
      let context;
      try{context=await conversationContext(state,{force:true});}catch(err){return error(err.message,state);}
      if(!context||!current())return;
      if((context.task_ids||[]).some(id=>!state.tasks.some(task=>task.id===id))){
        try{
          const result=await api('/api/project-tasks?project_id='+encodeURIComponent(state.id));
          if(!current())return;
          state.tasks=result.tasks||[];
        }catch(err){if(live(state)&&state.chatId===chatId)error(err.message,state);return;}
      }
      const runs=context.run_ids||[];
      const requested=state.focusWorkflow?.runId||state.dockRunId;
      state.dockRunId=runs.includes(requested)?requested:(context.runs||[]).find(run=>['running','queued','awaiting_review'].includes(run.status))?.id||runs[0]||'';
      state.focusWorkflow=state.dockRunId?{...state.focusWorkflow,runId:state.dockRunId}:null;
      if(state.nodeResultSelection&&!context.task_ids.includes(state.nodeResultSelection.task_id))state.nodeResultSelection=null;
      if(state.expertNodeContext&&!context.task_ids.includes(state.expertNodeContext.taskId))state.expertNodeContext=null;
    }
    state.section=resources[section]?section:'overview';state.surface=options.peek?'chat':options.surface||resourceSurface[state.section];state.peek=!!options.peek;state.peekTree=false;state.navOpen=false;state.reading=state.section==='node-result'&&!state.peek;state.inspection=options.inspection||null;
    if(state.section==='node-result'&&!state.peek)state.navHidden=true;
    if(state.surface===resourceSurface[state.section])state.sections[state.surface]=state.section;
    if(state.surface==='library'&&state.section==='files')state.fileRow=state.libraryFile;
    state.resourceEpoch++;persist(state);document.getElementById('pwProjectSettings').open=false;
    document.getElementById('pwResourceTitle').textContent=state.section==='catalog'?'全部资料':resources[state.section];
    const tree=document.getElementById('pwFileTree');if(tree?.parentElement!==document.getElementById('pwSidebarTreeSlot'))document.getElementById('pwSidebarTreeSlot').append(tree);
    document.getElementById('pwFileToolbar').replaceChildren();
    const area=document.getElementById('pwResource');disposeResource();area.innerHTML='<div class="pw-resource-content"></div>';const host=area.firstElementChild;
    if(state.section!=='files')tree.innerHTML='';layoutPanels(state);
    if(state.section==='run-artifacts'){
      await showRunArtifacts(state,host,options.runId);
      return;
    }
    if(state.section==='overview'){
      host.innerHTML='<div class="hint">正在读取项目概况…</div>';
      try{const view=await window.SpaceOverview(state.id);if(!live(state)||state.section!=='overview')return;host.innerHTML=view.html;view.bind();}
      catch(err){host.innerHTML='<div class="msg error">'+h(err.message||'读取项目概况失败')+'</div>';}
      return;
    }
    if(state.section==='expert-chat'){
      await mountExpertChat(state,host);
      return;
    }
    if(state.section==='file-preview'){
      if(!state.previewFile){host.innerHTML='<p class="pw-dock-empty">点击对话中的 Markdown 或 CSV 文件，在这里预览。</p>';return;}
      const epoch=state.resourceEpoch;
      await WorkflowFilePreview.mount(host,{projectId:state.id,chatId:state.chatId,file:state.previewFile,
        live:()=>live(state)&&state.chatId===chatId&&state.resourceEpoch===epoch,
        openFile:file=>openFilePreview(file,state.id)});
      return;
    }
    if(state.section==='node-result'){
      if(!state.nodeResultSelection){
        const eligibleTask=(state.peek?conversationTasks(state):state.tasks||[]).find(t=>WorkflowNodeWorkbench.eligible(t));
        if(eligibleTask){
          state.nodeResultSelection=WorkflowNodeWorkbench.availableSelection(eligibleTask);
        }else{
          host.innerHTML='<div class="wnw-empty" style="text-align:center;padding:60px 20px"><span class="pw-eyebrow" style="display:inline-block;margin-bottom:8px">WORKFLOW BENCH</span><h3 style="margin-bottom:10px">工作流产物工作台</h3><p class="hint" style="max-width:540px;margin:0 auto 16px">当前项目还没有执行节点。先在 DAG 工作流中准备并启动一次运行，节点运行后可在此查看状态；节点保存产物后，可继续预览、编辑版本并交付给下游节点。</p><div style="display:flex;justify-content:center;gap:10px"><button type="button" class="btn btn-primary" id="pwEmptyToFlow">查看 DAG 工作流</button><button type="button" class="btn ghost" id="pwEmptyToChat">返回目标对话</button></div></div>';
          document.getElementById('pwEmptyToFlow')?.addEventListener('click',()=>showResource('workflow',state,{peek:state.peek}));
          document.getElementById('pwEmptyToChat')?.addEventListener('click',()=>switchSurface('chat',state));
          return;
        }
      }
      const epoch=state.resourceEpoch;
      await WorkflowNodeWorkbench.mount(host,{selection:state.nodeResultSelection,isPeek:state.peek,live:()=>live(state)&&state.section==='node-result'&&state.resourceEpoch===epoch,
        selected:selection=>{state.nodeResultSelection={...state.nodeResultSelection,...selection};persist(state);},
        select:selection=>openNodeResult(selection,state.id,{chatId:state.nodeResultSelection?.chat_id,peek:state.peek}),
        toggleDock:selection=>openNodeResult(selection||state.nodeResultSelection,state.id,{chatId:state.nodeResultSelection?.chat_id,peek:!state.peek}),
        quoteToChat:({artifact,selection,quote,title})=>{
          const rel=artifact?.relative||artifact?.path||artifact?.name||title||'节点产物';
          const ref={relative:rel,name:artifact?.name||title||'节点产物',quote:quote||'',read_only:true};
          if(!draft(state).refs.some(r=>r.relative===ref.relative&&r.quote===ref.quote))draft(state).refs.push(ref);
          persist(state);paintRefs(state);
          if(!state.peek)switchSurface('chat',state);
          document.getElementById('pwAsk')?.focus();
          notice('已将节点产物选区引用到主对话输入框。',state);
        },
        openConversation:chatId=>chatId?openConversation(chatId,state.id):switchSurface('chat',state),
        openTask:id=>openTask(id,state.id,{chatId:state.nodeResultSelection?.chat_id,peek:state.peek}),changed:()=>refresh(state),
        locate:identity=>{state.focusWorkflow={runId:identity.workflow_run_id,nodeId:identity.workflow_node_id};return showResource('workflow',state,{peek:state.peek});}});return;
    }
    if(state.section==='files'){await mountFiles(state,host);return;}
    if(state.section==='papers'){
      const epoch=state.resourceEpoch;
      host.innerHTML=ProjectLiterature.html();
      await ProjectLiterature.mount(host.firstElementChild,state.id,[],()=>live(state)&&state.resourceEpoch===epoch&&host.isConnected);
      return;
    }
    if(['catalog','manuscripts','experiments','outputs'].includes(state.section)){const epoch=state.resourceEpoch;await ProjectResources.mount(host,state.id,{kind:{manuscripts:'manuscript',experiments:'experiment',outputs:'output'}[state.section]||'',live:()=>live(state)&&state.resourceEpoch===epoch&&host.isConnected});return;}
    if(['tasks','automation','plan'].includes(state.section)){state.taskCenterHost=host;await TaskCenter.render(host,{projectId:state.id,view:state.section==='automation'?'automation':state.section==='plan'?'plans':'board',filter:state.filter,live:()=>live(state)&&state.surface==='tasks',onSnapshot:rows=>{state.executionRows=rows;paintExecutionCounts(state);}});return;}
    if(state.section==='workflow'){await mountWorkflow(state,host);return;}
    if(state.section==='search'){await mountSearch(state,host);return;}
    const seq=state.resourceEpoch;host.innerHTML='<p class="pw-loading">正在读取项目资料…</p>';
    try{const data=await api('/api/task?id='+encodeURIComponent(state.id));if(!live(state)||seq!==state.resourceEpoch||!host.isConnected)return;
      state.project=data;spId=state.id;spTab=state.section;host.innerHTML=`<div id="spBody">${spBodyHTML(data)}</div>`;await spBind(data);
      if(!live(state)||seq!==state.resourceEpoch||!host.isConnected)return;
      const graph=document.getElementById('spStars');if(graph&&window.StarMap){graph.__api=api;graph.__toast=toast;StarMap.mount(graph,data.paper_graph||{nodes:[],edges:[],stats:{}});}
      const generate=document.getElementById('spStarGen');if(generate)generate.onclick=async()=>{generate.disabled=true;try{await api('/api/task/papergraph/build',{id:state.id});if(live(state)&&state.section==='stars')await showResource('stars',state);}catch(err){error(err.message,state);if(generate.isConnected)generate.disabled=false;}};
    }catch(err){if(live(state)&&host.isConnected)host.innerHTML=`<p class="pw-error">${h(err.message)}</p>`;}
  }
  async function mountSearch(state,host){
    const query=state.searchQuery||'',seq=state.resourceEpoch;
    if(!query){host.innerHTML='<p class="hint">在左侧输入关键词，搜索本项目的文献、笔记、进度和证据。</p>';return;}
    host.innerHTML='<p class="pw-loading">正在搜索当前项目…</p>';
    try{
      const data=await api('/api/space/search?task_id='+encodeURIComponent(state.id)+'&q='+encodeURIComponent(query));
      if(!live(state)||seq!==state.resourceEpoch||!host.isConnected)return;
      const groups={papers:'文献',notes:'笔记',todos:'进度任务',evidence:'证据'};
      host.innerHTML=`<div class="pw-view-head"><h3>“${h(query)}”的搜索结果</h3><span class="hint">${h(data.total||0)} 条</span></div>`+Object.entries(groups).map(([key,label])=>{
        const rows=data.results?.[key]||[];if(!rows.length)return '';
        return `<section class="pw-search-results"><h4>${label}</h4>${rows.map(row=>`<button type="button" data-pw-search-group="${key}"><strong>${h(row.title||row.filename||row.claim||'未命名')}</strong><span>${h(row.snippet||row.quote||row.authors||row.status||'')}</span></button>`).join('')}</section>`;
      }).join('')+(!data.total?'<p class="hint">当前项目中没有匹配的资料。</p>':'');
      host.querySelectorAll('[data-pw-search-group]').forEach(button=>button.onclick=()=>showResource({papers:'papers',notes:'notes',todos:'plan',evidence:'overview'}[button.dataset.pwSearchGroup],state));
    }catch(err){if(live(state)&&seq===state.resourceEpoch&&host.isConnected)host.innerHTML=`<p class="pw-error">搜索失败：${h(err.message)}</p>`;}
  }
  async function createEntry(projectId,parent,kind){
    const state=get(projectId);if(!live(state)||!await flush())return;
    const name=await uiPrompt(kind==='folder'?'新建文件夹名称':'新建文件名称（包含扩展名）',kind==='file'?'未命名.md':'');if(!name?.trim())return;
    try{
      const row=await api('/api/project/files/create',{project_id:projectId,parent,name:name.trim(),kind});
      if(!live(state))return;
      if(parent){flExpanded.add(parent);flSaveExpanded();}
      await flInitTree();if(!live(state))return;
      if(kind==='file'){await openFile(row,projectId,{peek:false});if(live(state)&&flCurrentFile?.row.rel===row.rel){flCurrentMode='edit';flRenderContent();layoutPanels(state);document.getElementById('flEditText')?.focus();}}
      notice(kind==='folder'?'已在当前目录创建文件夹。':'文件已创建，可以开始编辑。',state);
    }catch(err){error('创建失败：'+err.message,state);}
  }
  async function refreshFiles(projectId,oldRelative,newRelative=''){
    const state=get(projectId),matches=rel=>rel===oldRelative||rel?.startsWith(oldRelative+'/');
    const change=row=>{if(!row||!matches(row.rel))return row;if(!newRelative)return null;const rel=newRelative+row.rel.slice(oldRelative.length);return {...row,rel,path:row.path.slice(0,-row.rel.length)+rel.replaceAll('/',row.path.includes('\\')?'\\':'/'),name:rel.split('/').pop(),ext:rel.split('.').pop()};};
    const changed=matches(state.fileRow?.rel);state.fileRow=change(state.fileRow);state.libraryFile=change(state.libraryFile);
    if(matches(state.fileDirectory))state.fileDirectory=newRelative?newRelative+state.fileDirectory.slice(oldRelative.length):oldRelative.split('/').slice(0,-1).join('/');
    if(live(state)){for(const rel of [...flExpanded])if(matches(rel)){flExpanded.delete(rel);if(newRelative)flExpanded.add(newRelative+rel.slice(oldRelative.length));}flSaveExpanded();}
    if(newRelative)Object.values(state.drafts).forEach(value=>(value.refs||[]).forEach(ref=>{if(matches(ref.relative)){ref.relative=newRelative+ref.relative.slice(oldRelative.length);ref.name=ref.relative.split('/').pop();}}));
    persist(state);if(!live(state))return;
    if(changed){flCurrentFile=null;flSel=state.fileRow?.rel||'';}
    if(state.section==='files'&&(state.surface!=='chat'||state.peek))await showResource('files',state,{peek:state.peek,surface:state.surface,inspection:state.inspection});
    paintRefs(state);
  }
  async function mountFiles(state,host){
    const previous=typeof flCurrentFile!=='undefined'&&flCurrentFile?.projectId===state.id&&state.fileRow?.path===flCurrentFile.row.path&&!state.fileRow.read_only?flCurrentFile:null;
    host.innerHTML='<div id="pwInlineTree" class="pw-inline-tree"></div>'+filesPanelHTML(true);
    const tree=host.querySelector('.fl-grid>.fl-col'), fileBrowser=state.surface==='library';
    document.getElementById('pwFileTree').replaceChildren(tree);
    if(fileBrowser)tree.querySelector('.fl-h').insertAdjacentHTML('afterend','<button type="button" class="pw-file-root pw-text-button" data-pw-root-folder>项目根目录</button>');
    document.getElementById('pwFileToolbar').replaceChildren(host.querySelector('.fl-grid>.fl-col:last-child>.fl-h'));
    document.getElementById('flHand').textContent='交由专家/团队处理';document.getElementById('flHand').title='指派给当前项目专家、专家或多智能体团队执行';document.getElementById('flHandoffHistory').textContent='任务记录';
    const treeReady=flBind();
    // Open the target picker first.  The previous direct ``project`` shortcut
    // attempted to save and create a task before the user could enter a goal,
    // which also discarded the handoff dialog when a file-save barrier fired.
    document.getElementById('flHand').onclick=()=>FileHandoff.open({project_id:state.id,relative:flSel});
    layoutPanels(state);
    const list=document.getElementById('flList');
    list.onclick=async event=>{
      const el=event.target.closest('[data-rel]');if(!el||!live(state))return;
      const row=flFindRow(el.dataset.rel);if(!row)return;
      if(!row.dir)return openFile(row,state.id);
      if(fileBrowser&&(state.fileRow||row.rel!==state.fileDirectory))return browseDirectory(state,row.rel);
      if(flExpanded.has(row.rel))flExpanded.delete(row.rel);else flExpanded.add(row.rel);
      flSaveExpanded();flRenderTree();
      if(flExpanded.has(row.rel)&&!flTreeCache[row.rel]){await flLoadNode(row.rel);if(list.isConnected)flRenderTree();}
    };
    tree.querySelector('[data-pw-root-folder]')?.addEventListener('click',()=>browseDirectory(state,''));
    if(previous){flCurrentFile=previous;flSel=previous.row.rel;document.getElementById('flName').textContent=previous.row.name;document.getElementById('pwResourceTitle').textContent=previous.row.name;document.getElementById('flModeTabs').style.display='inline-flex';document.getElementById('flSaveBtn').style.display='';document.getElementById('flHand').disabled=false;document.getElementById('flHandoffHistory').disabled=false;flUpdateModeTabs();flRenderContent();layoutPanels(state);}
    else if(state.fileRow)await displayFile(state,state.fileRow);
    else if(fileBrowser){await treeReady;if(host.isConnected&&!state.fileRow)await browseDirectory(state,state.fileDirectory||'');}
    if(!host.isConnected)return;
    document.getElementById('flReload').onclick=async()=>{if(await flush()&&live(state)){await flInitTree();if(host.isConnected&&fileBrowser&&!state.fileRow)await browseDirectory(state,state.fileDirectory||'');}};
    document.getElementById('flOpen').onclick=async()=>{if(!state.fileRow)return;try{if(await flush())await api('/api/conv/open',{path:state.fileRow.path});}catch(err){error(err.message,state);}};
  }
  async function browseDirectory(state,relative){
    const stage=document.getElementById('flStage');
    if(!stage||!live(state)||!await flush()||!stage.isConnected)return;
    const ticket=state.fileRequest=(state.fileRequest||0)+1;
    state.fileDirectory=relative;state.fileRow=null;state.libraryFile=null;state.reading=false;
    const parts=relative?relative.split('/'):[];
    for(let i=1;i<=parts.length;i++)flExpanded.add(parts.slice(0,i).join('/'));
    flSaveExpanded();
    flCurrentFile=null;flSel=relative;
    document.getElementById('pwResourceTitle').textContent='目录与文件';
    persist(state);layoutPanels(state);flRenderTree();
    stage.innerHTML='<p class="hint">正在读取目录…</p>';
    if(!flTreeCache[relative])await flLoadNode(relative);
    if(!live(state)||!stage.isConnected||ticket!==state.fileRequest)return;
    const rows=flTreeCache[relative];
    const breadcrumbs=['项目根目录',...parts].map((name,index)=>`<button type="button" class="pw-text-button" data-pw-folder-path="${index}">${h(name)}</button>`).join('<span aria-hidden="true">/</span>');
    stage.innerHTML=`<section class="pw-directory"><nav class="pw-directory-path" aria-label="当前目录">${breadcrumbs}</nav><div class="pw-directory-heading"><h3>${h(parts.at(-1)||'项目根目录')} <small>${rows?.length??0} 项</small></h3><input type="search" class="control" aria-label="筛选当前目录" placeholder="筛选当前目录"></div><div data-pw-folder-rows></div></section>`;
    stage.querySelectorAll('[data-pw-folder-path]').forEach(button=>button.onclick=()=>browseDirectory(state,parts.slice(0,Number(button.dataset.pwFolderPath)).join('/')));
    const search=stage.querySelector('input'), results=stage.querySelector('[data-pw-folder-rows]');
    function paint(){
      const query=search.value.trim().toLocaleLowerCase();
      const visible=(rows||[]).filter(row=>row.name.toLocaleLowerCase().includes(query));
      results.innerHTML=visible.map(row=>`<button type="button" class="pw-directory-row" data-pw-file-entry="${h(row.rel)}"><span aria-hidden="true">${row.dir?'📁':flGetIcon(row.ext)}</span><strong>${h(row.name)}</strong><small>${row.dir?'文件夹':h(flFmtSize(row.size)||row.ext.toUpperCase())}</small><time>${h(row.at)}</time></button>`).join('')||`<p class="hint pw-directory-empty">${!rows?'无法读取此目录，请刷新后重试。':query?'当前目录没有匹配的项目。':'当前目录为空。'}</p>`;
      results.onclick=event=>{
        const button=event.target.closest('[data-pw-file-entry]');
        const row=visible.find(item=>item.rel===button?.dataset.pwFileEntry);
        if(!row)return;
        if(!row.dir)return openFile(row,state.id,{peek:false});
        return browseDirectory(state,row.rel);
      };
    }
    search.oninput=paint;
    paint();flRenderTree();
  }
  function relative(path,base){const normal=v=>String(v||'').replaceAll('\\','/').replace(/\/+$/,'');const p=normal(path),b=normal(base);return p.toLowerCase().startsWith(b.toLowerCase()+'/')?p.slice(b.length+1):'';}
  async function openFilePreview(file,projectId=curTask){
    if(!active()||mounted?.id!==projectId)await openProject(projectId);
    const state=get(projectId),chatId=state.chatId;
    if(!live(state)||!await flush()||!live(state)||state.chatId!==chatId)return;
    if(!state.chatId)await ensureChat(state);
    state.previewFile={...file};
    await showResource('file-preview',state,{peek:true});
  }
  async function openFile(row,projectId=curTask,options={}){
    if(projectId!==mounted?.id){await openProject(projectId);}
    const state=get(projectId);if(!live(state)||!await flush())return;
    const project=(S.tasks||[]).find(p=>p.id===projectId)||state.project?.task||{};
    const rel=row.paper_id?`paper:${row.paper_id}`:(row.read_only?relative(row.path,project.base_dir):'')||row.rel||row.relative||relative(row.path,project.base_dir);
    if(!rel)return error('该文件尚未作为当前项目副本保存，请先导入项目后再操作。',state);
    const task=state.review,output=options.inspection?.origin==='task'||row.read_only&&(task?.outputs||[]).some(o=>o.path===row.path),peek=!output&&(options.peek===true||options.peek!==false&&state.surface==='chat');
    state.fileRequest=(state.fileRequest||0)+1;state.fileRow={...row,rel,name:row.name||rel.split('/').pop(),ext:row.ext||rel.split('.').pop(),project_id:projectId};
    if(!output&&!peek)state.libraryFile=state.fileRow;
    const inspection=output?options.inspection||{origin:'task',taskId:task.id}:peek?{origin:'chat'}:null;
    if(state.section!=='files'||!document.getElementById('flStage')||state.peek!==peek||output&&state.surface!=='tasks')await showResource('files',state,{peek,surface:output?'tasks':'library',inspection});
    else if(live(state)){state.inspection=inspection;await displayFile(state,state.fileRow);}
  }
  async function displayFile(state,row){
    if(!live(state))return;const ticket=state.fileRequest;if(flCurrentFile?.row?.path!==row.path)flCurrentMode='preview';await flShow(row);if(!live(state)||ticket!==state.fileRequest||state.fileRow?.rel!==row.rel)return;
    state.fileRow=row;persist(state);document.getElementById('pwResourceTitle').textContent=row.name;layoutPanels(state);
    if(row.read_only){flCurrentMode='preview';flUpdateModeTabs();flRenderContent();document.getElementById('flModeTabs').style.display='none';document.getElementById('flSaveBtn').style.display='none';}
  }
  function currentFile(){if(!active()||mounted.surface==='chat'&&!mounted.peek)return null;if(mounted.section==='files'&&mounted.fileRow&&!mounted.fileRow.paper_id)return {project_id:mounted.id,relative:mounted.fileRow.rel};if(mounted.section==='notes'&&typeof mdProj!=='undefined'&&mdProj?.path)return {project_id:mounted.id,relative:mdProj.path};return null;}
  async function addReference(state,quotation=''){
    if(!live(state)||!await flush())return;const file=currentFile();if(!file)return notice('先打开项目文件、PDF 或笔记，再引用到当前对话。',state);
    const ref={relative:file.relative,name:file.relative.split('/').pop(),quote:quotation,read_only:state.section==='files'&&!!state.fileRow?.read_only};if(!draft(state).refs.some(r=>r.relative===ref.relative&&r.quote===ref.quote))draft(state).refs.push(ref);persist(state);paintRefs(state);await switchSurface('chat',state);document.getElementById('pwAsk').focus();
  }
  async function referenceResource(row,projectId=curTask){
    if(!row?.relative)return notice("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u53ef\u7528\u7684\u9879\u76ee\u6587\u4ef6",mounted);
    if(!active()||mounted?.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    const ref={relative:row.relative,name:row.title||row.name||row.relative.split('/').pop(),quote:'',read_only:!!row.read_only};
    if(!draft(state).refs.some(item=>item.relative===ref.relative&&!item.quote))draft(state).refs.push(ref);
    persist(state);paintRefs(state);await switchSurface('chat',state);document.getElementById('pwAsk')?.focus();
  }
  async function handoffResource(row,projectId=curTask){
    if(!row?.relative)return notice("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u53ef\u7528\u7684\u9879\u76ee\u6587\u4ef6",mounted);
    if(!active()||mounted?.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    const child=await ProjectTasks.compose(projectId,{title:row.title||row.name||row.relative.split('/').pop(),goal:'',inputs:[{relative:row.relative}],metadata:{source:'file_handoff'},quick:true});
    child?.addEventListener('close',()=>refresh(state),{once:true});
  }
  async function openReference(state,index){
    const ref=draft(state).refs[index];if(!ref||!live(state))return;
    try{const data=await api('/api/file/location',{project_id:state.id,relative:ref.relative});if(live(state))await openFile({rel:ref.relative,path:data.path,name:ref.name||ref.relative.split('/').pop(),ext:ref.relative.split('.').pop(),read_only:!!ref.read_only},state.id,{peek:true});}
    catch(err){error('引用材料无法打开：'+err.message,state);}
  }
  async function quote(state){
    const selected=window.getSelection()?.toString()||'';let text=selected;
    if(!text){const editor=document.getElementById('flEditText')||document.getElementById('mdSource');if(editor&&editor.selectionStart!==editor.selectionEnd)text=editor.value.slice(editor.selectionStart,editor.selectionEnd);}
    if(!text){text=await uiPrompt('粘贴要引用的原文，将关联当前打开的项目资料','');if(text===null)return;}
    if(text.trim())await addReference(state,text.trim());
  }
  async function openPaper(row){const state=mounted;if(!live(state))return;const data=await api('/api/task?id='+encodeURIComponent(state.id));if(!live(state))return;state.project=data;const paper=(data.paper_list||[]).find(p=>p.id===row.paper_id||p.id===row.id);if(!paper)return error('没有找到这篇项目 PDF。',state);await openFile({path:paper.source_path||paper.path,paper_id:paper.source_path?'':paper.id,name:paper.filename||paper.title||row.title,ext:'pdf',read_only:true},state.id);}
  async function openProject(id){if(!S.tasks.some(p=>p.id===id))await refreshState(false);if(!S.tasks.some(p=>p.id===id))throw new Error('项目不存在');if(active())await switchProject(id);else{curTask=id;localStorage.setItem('rw-task',id);await openTab('workspace');}return get(id);}
  async function openNodeResult(selection,projectId=curTask,options={}){
    if(typeof selection==='string')selection={task_id:selection};
    projectId=selection.project_id||projectId;
    if(!active()||mounted.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    const peek=options.peek??state.surface==='chat';
    if(peek){
      const chatId=state.chatId,context=await conversationContext(state,{force:true});
      if(!live(state)||state.chatId!==chatId)return;
      if(!context?.task_ids.includes(selection.task_id)){
        const data=await api('/api/execution-conversations?kind=task&entity_id='+encodeURIComponent(selection.task_id)+'&project_id='+encodeURIComponent(projectId));
        if(!live(state)||state.chatId!==chatId)return;
        await changeConversation(state,data.execution.chat_id);paintSessions(state);paintDraft(state);paintChat(state);
      }
    }
    state.nodeResultSelection={...selection,project_id:projectId,chat_id:options.chatId||selection.chat_id||state.reviewOrigins?.[selection.task_id]||state.chatId};
    state.review=null;await showResource('node-result',state,{initial:true,peek:options.peek??state.surface==='chat'});
  }
  async function showRunArtifacts(state,host,runId){
    host.innerHTML=`<div class="pw-view-head"><div data-pw-run-picker></div><span class="pw-workbench-scope">工作流运行与节点产物</span></div><div data-pw-run-list><p class="hint">正在读取运行记录…</p></div>`;
    const isCurrent=()=>host.isConnected&&live(state)&&state.surface==='workbench'&&state.section==='run-artifacts';
    const list=host.querySelector('[data-pw-run-list]');
    const project=encodeURIComponent(state.id);
    try{
      const {runs=[]}=await api('/api/workflow/execution?project_id='+project);
      if(!isCurrent())return;
      const selected=runs.find(run=>run.id===(runId||state.artifactRunId))||runs[0];
      if(!selected){list.innerHTML='<p class="hint">运行工作流后，这里会按阶段展示本次交付。</p>';return;}
      host.querySelector('[data-pw-run-picker]').innerHTML='<label>工作流运行 <select class="control" data-pw-artifact-run>'+runs.map(run=>'<option value="'+h(run.id)+'" '+(run===selected?'selected':'')+'>'+h(run.layout_name||run.name||'工作流')+' · '+h(labels[run.status]||run.status)+' · '+h(run.started_at||run.id)+'</option>').join('')+'</select></label>';
      let request=0;
      const loadNodes=async run=>{
        const token=++request;
        state.artifactRunId=run.id;
        persist(state);
        list.innerHTML='<p class="hint">正在读取节点状态…</p>';
        try{
          const {tasks=[]}=await api('/api/project-tasks?project_id='+project+'&workflow_run_id='+encodeURIComponent(run.id));
          if(!isCurrent()||token!==request)return;
          const stages=tasks.filter(task=>task.workflow_node_id&&!task.parent_task_id);
          const nodes=new Map(stages.map(task=>[task.workflow_node_id,task]));
          const order=run.node_order||[];
          const ordered=[...order.map(id=>nodes.get(id)).filter(Boolean),...stages.filter(task=>!order.includes(task.workflow_node_id))];
          list.innerHTML=ordered.map((task,index)=>`<div class="pw-output"><b>${h(task.title)}</b><p class="hint">${h(labels[task.status]||task.status)}</p>${task.latest_run?.error?`<p class="pw-error">${h(task.latest_run.error)}</p>`:''}<button type="button" class="btn" data-pw-artifact="${index}">${WorkflowNodeWorkbench.eligible(task)?'查看产物':'查看节点状态'}</button></div>`).join('')||'<p class="hint">本次运行尚未生成节点任务。</p>';
          list.querySelectorAll('[data-pw-artifact]').forEach(button=>button.onclick=()=>{
            const task=ordered[Number(button.dataset.pwArtifact)];
            return openNodeResult({...WorkflowNodeWorkbench.availableSelection(task),workflow_run_id:run.id},state.id,{peek:false});
          });
        }catch(err){if(isCurrent()&&token===request)list.textContent='读取节点状态失败：'+err.message;}
      };
      host.querySelector('[data-pw-artifact-run]').onchange=event=>{
        const run=runs.find(candidate=>candidate.id===event.target.value);
        if(run&&isCurrent())loadNodes(run);
      };
      await loadNodes(selected);
    }catch(err){if(isCurrent())list.textContent='读取运行产物失败：'+err.message;}
  }
  async function openWorkflowRun(projectId=curTask,runId='',nodeId=''){
    if(!projectId)return;
    if(!active()||mounted?.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    if(runId&&nodeId){
      try{
        const data=await api('/api/project-tasks?project_id='+encodeURIComponent(projectId)+'&workflow_run_id='+encodeURIComponent(runId));
        const task=(data.tasks||[]).find(item=>item.workflow_node_id===nodeId);
        if(task)return openNodeResult(WorkflowNodeWorkbench.availableSelection(task),projectId,{chatId:state.chatId,peek:false});
      }catch{}
    }
    state.nodeResultSelection=null;state.navHidden=false;
    await openWorkflowDock(projectId,runId,nodeId);
  }
  async function openWorkflowNode(projectId,nodeId,layoutId=''){
    if(!projectId||!nodeId)return false;
    const {runs=[]}=await api('/api/workflow/execution?project_id='+encodeURIComponent(projectId));
    const run=layoutId?runs.find(item=>item.layout_id===layoutId):runs[0];if(!run)return false;
    const {tasks=[]}=await api('/api/project-tasks?project_id='+encodeURIComponent(projectId)+'&workflow_run_id='+encodeURIComponent(run.id));
    const task=tasks.find(item=>item.workflow_node_id===nodeId);
    if(!task)return false;
    await openNodeResult(WorkflowNodeWorkbench.availableSelection(task),projectId,{peek:false});
    return true;
  }
  async function openStage(projectId, step){
    if(!projectId)throw new Error('请先选择项目');
    return uiNavigate(async()=>{
      await openProject(projectId);
      const state=get(projectId);
      if(!live(state))return;
      const layout=await api('/api/workflow/layouts/state?task_id='+encodeURIComponent(projectId));
      if(!live(state))return;
      const node=layout.nodes.find(item=>item.step===step);
      if(node&&await openWorkflowNode(projectId,node.id,layout.layout.id))return;
      if(!live(state))return;
      if(node)await api('/api/workflow/layouts/select',{task_id:projectId,layout_id:layout.layout.id,node_id:node.id});
      if(live(state))await showResource('workflow',state);
    });
  }
  async function openTask(id,projectId=curTask,options={}){
    if(!active()||mounted.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    state.reviewOrigins||={};if(options.chatId)state.reviewOrigins[id]=options.chatId;state.reviewChatId=state.reviewOrigins[id]||'';
    const seq=++state.resourceEpoch;let task;
    try{task=(await api('/api/project-tasks/detail?id='+encodeURIComponent(id)+'&project_id='+encodeURIComponent(projectId))).task;}catch(err){return error(err.message,state);}
    if(!live(state)||seq!==state.resourceEpoch)return;
    const peek=options.peek??(state.surface==='chat');
    if(task.workflow_run_id&&task.workflow_node_id&&options.preferWorkbench!==false){
      return openNodeResult(WorkflowNodeWorkbench.availableSelection(task),projectId,{chatId:options.chatId||state.reviewChatId,peek});
    }
    state.tasks=[task,...state.tasks.filter(t=>t.id!==task.id)];state.review=task;state.reviewStale=false;state.section='task-detail';state.surface=peek?'chat':'tasks';state.peek=peek;state.navOpen=false;state.reading=false;state.inspection=null;persist(state);document.getElementById('pwFileToolbar').replaceChildren();document.getElementById('pwResourceTitle').textContent=task.title||'任务审查';
    const run=task.latest_run||{},decision=(task.deliveries||[]).at(-1),outputs=!task.workflow_run_id||WorkflowNodeWorkbench.eligible(task)?task.outputs||[]:[];
    const tree=document.getElementById('pwFileTree');if(tree)document.getElementById('pwSidebarTreeSlot').append(tree);layoutPanels(state);
    const tipHtml=['ready','draft'].includes(task.status)?`<div class="pt-box" style="margin:10px 0;background:var(--panel2);border-left:3px solid var(--accent);padding:10px 14px"><strong style="color:var(--accent)">💡 该节点就绪待运行</strong><p class="hint" style="margin:4px 0 0">点击下方「${run.id?'重新运行':'开始任务'}」启动执行，生成产物后将直接在此提供预览与编辑工作台。</p></div>`:['running','queued'].includes(task.status)?`<div class="pt-box" style="margin:10px 0;background:var(--panel2);border-left:3px solid var(--accent);padding:10px 14px"><strong style="color:var(--accent)">⏳ 专家正在执行该节点任务</strong><p class="hint" style="margin:4px 0 0">生成产物后将自动切换为产物预览、版本对比与编辑工作台…</p></div>`:'';
    document.getElementById('pwResource').innerHTML=`<article class="pw-task-detail"><div class="pw-view-head"><span class="pw-state">${h(labels[task.status]||task.status)}</span><span class="hint">${run.id?'第 '+run.attempt+' 次运行':'尚未执行'}</span></div><h3>${h(task.title)}</h3><p>${h(task.goal)}</p>${tipHtml}<p class="hint">执行者：${h(run.executor_snapshot?.member?.name||run.executor_snapshot?.assistant?.name||stateBindingName(task))}${task.workflow_run_id?' · 当前工作流节点':''}</p>${run.error?`<p class="pw-error">${h(run.error)}</p>`:''}<div class="pw-view-head">${['ready','failed','blocked','cancelled'].includes(task.status)?`<button class="btn btn-primary" data-pw-start="${h(task.id)}">${run.checkpoint?.phase==='tool'?'确认中断继续':run.id?'重新运行':'开始任务'}</button>`:''}${['queued','running','blocked'].includes(task.status)?`<button class="btn ghost" data-pw-cancel="${h(task.id)}">停止任务</button>`:''}<button class="btn ghost" id="pwTaskAdvanced">完整运行记录</button></div><h4>执行结果</h4><div class="md">${md(run.checkpoint?.reply||'等待执行结果。')}</div><h4>产物版本</h4>${run.checkpoint?.output_revision?`<p class="hint">人工修订 v${run.checkpoint.output_revision} · 核查以当前产物为准</p>`:""}${outputs.map((o,i)=>`<div class="pw-output"><b>${h(o.name||o.relative||'任务产物')}</b><p class="hint">${h((o.content_hash||o.sha256||'').slice(0,16))} · 本次运行固定版本</p><button class="btn btn-primary" data-pw-output-fullscreen="${i}">全屏预览</button><button class="btn ghost" data-pw-output="${i}">打开文件</button>${ProjectOutputReview.canEdit(task,o)?`<button class="btn ghost" data-pw-output-edit="${i}">人工修订</button>`:""}</div>`).join('')||'<p class="hint">尚未生成产物。</p>'}${decision?`<p class="hint">${h(decision.source==='human'?'人工核查':decision.source==='policy'?'策略验收':'历史交付')} · ${h(decision.feedback||'')} · ${h(decision.created_at||'')}</p>`:''}<details><summary>任务输入与执行范围</summary><pre>${h(JSON.stringify({inputs:run.input_snapshot||task.inputs,scope:task.scope},null,2))}</pre></details></article>`;
    document.getElementById('pwTaskAdvanced').onclick=()=>ProjectTasks.openTask(id,state.id);
    if(task.chat_id){const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='打开任务对话';button.onclick=()=>openConversation(task.chat_id,state.id);document.getElementById('pwTaskAdvanced').parentElement.prepend(button);}
    const actions=document.getElementById('pwTaskAdvanced').parentElement;
    if(task.workflow_run_id&&task.workflow_node_id){
      const button=document.createElement('button');button.type='button';button.className='btn btn-primary';button.dataset.pwNodeResult=task.id;button.textContent=WorkflowNodeWorkbench.eligible(task)?'查看当前节点产物':'打开当前节点工作台';button.onclick=()=>openNodeResult(WorkflowNodeWorkbench.availableSelection(task),state.id,{chatId:state.reviewChatId||state.chatId});actions.prepend(button);
    }
    if(task.workflow_run_id){
      const button=document.createElement('button');button.className='btn ghost';button.textContent='定位工作流节点';button.onclick=async()=>{state.focusWorkflow={runId:task.workflow_run_id,nodeId:task.workflow_node_id};await showResource('workflow',state);};actions.append(button);
    }
    if(task.metadata?.stage==='map'&&task.metadata?.node_config?.node_kind!=='tool'&&task.status==='awaiting_review'&&(!task.workflow_run_id||WorkflowNodeWorkbench.eligible(task))){
      const button=document.createElement('button');button.className='btn btn-primary';button.dataset.pwGraphEdit='';button.textContent='编辑节点证据链';button.onclick=()=>WorkflowReview.editGraph(state.id,task.id,()=>openTask(task.id,state.id));actions.append(button);
    }
    if(state.reviewChatId){
      const button=document.createElement('button');button.className='btn ghost';button.textContent='返回发起对话';button.onclick=()=>openConversation(state.reviewChatId,state.id);actions.append(button);
    }
    document.querySelectorAll('[data-pw-output]').forEach(b=>b.onclick=()=>openFile({...outputs[Number(b.dataset.pwOutput)],path:outputs[Number(b.dataset.pwOutput)].path,read_only:true},state.id,{surface:'tasks',inspection:{origin:'task',taskId:task.id}}));
    document.querySelectorAll('[data-pw-output-fullscreen]').forEach(button=>button.onclick=()=>ProjectOutputReview.open(state.id,task.id,Number(button.dataset.pwOutputFullscreen),()=>openTask(task.id,state.id)));
    document.querySelectorAll('[data-pw-output-edit]').forEach(button=>button.onclick=()=>ProjectOutputReview.open(state.id,task.id,Number(button.dataset.pwOutputEdit),()=>openTask(task.id,state.id)));
    paintReview(state,task);
  }
  function showsReview(state,task){
    return (state.surface==='tasks'||state.peek)&&(state.section==='task-detail'||(state.inspection?.origin==='task'&&state.inspection.taskId===task.id&&state.section==='files'&&(task.outputs||[]).some(o=>o.path===state.fileRow?.path)));
  }
  function paintReview(state,task){
    const host=document.getElementById('pwReview');if(!live(state)||!host)return;
    host.hidden=task.status!=='awaiting_review'||!showsReview(state,task);if(host.hidden)return;
    const feedback=state.feedback?.[task.id]||'';
    host.innerHTML=`<div class="pw-context-bar">核查对象：${h(task.title)} · 第 ${task.latest_run.attempt} 次产物</div><label>核查意见<textarea class="control" id="pwFeedback" placeholder="记录依据；退回时请填写修改要求">${h(feedback)}</textarea></label><div class="pw-view-head"><button type="button" class="btn btn-primary" id="pwApprove">通过交付</button><button type="button" class="btn ghost" id="pwReject">退回修改</button></div>`;
    document.getElementById('pwFeedback').oninput=e=>{state.feedback||={};state.feedback[task.id]=e.target.value;};
    const review=async approved=>{const text=document.getElementById('pwFeedback').value;if(!approved&&!text.trim())return error('退回任务前请填写修改要求。',state);host.querySelectorAll('button').forEach(b=>b.disabled=true);try{const result=await api('/api/project-tasks/review',{id:task.id,project_id:state.id,reviewed_run_id:task.latest_run.id,reviewed_output_version:task.latest_run.checkpoint?.output_revision||0,approved,feedback:text,request_key:crypto.randomUUID()});state.review=result.task;host.hidden=true;await refresh(state);if(live(state))notice(approved?'已记录人工核查并交付当前版本。':'已退回，重试任务将带入修改意见。',state);}catch(err){error(err.message,state);host.querySelectorAll('button').forEach(b=>b.disabled=false);}};
    document.getElementById('pwApprove').onclick=()=>review(true);document.getElementById('pwReject').onclick=()=>review(false);
    const retry=document.createElement('button');retry.type='button';retry.className='btn ghost';retry.id='pwRejectRetry';retry.textContent='退回并重做';
    retry.onclick=async()=>{const feedback=document.getElementById('pwFeedback').value;if(!feedback.trim())return error('请填写重做要求。',state);host.querySelectorAll('button').forEach(button=>button.disabled=true);try{await api('/api/project-tasks/review',{id:task.id,project_id:state.id,reviewed_run_id:task.latest_run.id,reviewed_output_version:task.latest_run.checkpoint?.output_revision||0,approved:false,retry:true,feedback,request_key:crypto.randomUUID()});await openTask(task.id,state.id);}catch(err){error(err.message,state);host.querySelectorAll('button').forEach(button=>button.disabled=false);}};
    document.getElementById('pwReject').after(retry);
    if(state.reviewStale){host.querySelectorAll('button').forEach(button=>button.disabled=true);const latest=document.createElement('button');latest.type='button';latest.className='btn';latest.dataset.pwReviewReload='';latest.textContent='查看最新产物后重新核查';latest.onclick=()=>{const updated=state.tasks.find(item=>item.id===task.id);if(WorkflowNodeWorkbench.eligible(updated))return openNodeResult(WorkflowNodeWorkbench.availableSelection(updated),state.id,{chatId:state.reviewChatId});return openTask(task.id,state.id);};host.append(latest);}
  }
  async function openWorkflowDock(projectId=curTask,runId='',nodeId=''){
    if(!projectId)return;
    if(!active()||mounted?.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    if(runId){
      const chatId=state.chatId;
      const context=await conversationContext(state,{force:true});
      if(!live(state)||state.chatId!==chatId)return;
      if(!context?.run_ids.includes(runId)){
        const data=await api('/api/workflow/execution?id='+encodeURIComponent(runId));
        if(!live(state)||state.chatId!==chatId||data.run?.project_id!==projectId)return;
        await changeConversation(state,data.run.chat_id);paintSessions(state);paintDraft(state);paintChat(state);
      }
      state.dockRunId=runId;state.liveRunSelected=true;
    }
    if(runId||nodeId)state.focusWorkflow={runId:String(runId||''),nodeId:String(nodeId||'')};
    await showResource('workflow',state,{initial:true,peek:true});
  }
  async function openDockExpertChat(state,task,node){
    if(task)state.tasks=[task,...state.tasks.filter(item=>item.id!==task.id)];
    state.expertNodeContext={taskId:task?.id||'',nodeId:node?.id||task?.workflow_node_id||'',nodeLabel:node?.label||task?.title||'工作流节点',runId:task?.workflow_run_id||''};
    persist(state);
    await showResource('expert-chat',state,{peek:true});
  }
  async function mountExpertChat(state,host){
    host.__expertDispose?.();
    const chatId=state.chatId,epoch=state.resourceEpoch;
    let disposed=false,bodySignature='';
    const alive=()=>!disposed&&host.isConnected&&live(state)&&state.chatId===chatId&&state.resourceEpoch===epoch&&state.section==='expert-chat';
    let pollTimer;
    host.__expertDispose=()=>{disposed=true;clearTimeout(pollTimer);host.querySelector('[data-pw-expert-body]')?.__trajectoryDispose?.();};
    const ctx=state.expertNodeContext||{};
    const tasks=conversationTasks(state);
    const curRunId=state.dockRunId||ctx.runId||'';
    let flowTasks=tasks.filter(t=>t.workflow_node_id);
    if(curRunId){
      const runTasks=flowTasks.filter(t=>t.workflow_run_id===curRunId);
      flowTasks=runTasks;
    }
    const taskMap=new Map();
    for(const t of flowTasks){
      const key=t.workflow_node_id||t.id;
      if(!taskMap.has(key))taskMap.set(key,t);
      else{
        const prev=taskMap.get(key);
        if((t.updated_at||'')>(prev.updated_at||''))taskMap.set(key,t);
      }
    }
    flowTasks=Array.from(taskMap.values());
    function parseStep(task){
      const nodeMeta=task?.metadata?.workflow_node||{};
      const stepNo=String(nodeMeta.step_no||'');
      if(stepNo){
        const parts=stepNo.split('.').map(n=>parseInt(n,10)||0);
        return [parts[0]||0,parts[1]||0];
      }
      const m=String(task?.title||'').match(/Step\s+(\d+)(?:\.(\d+))?/i);
      if(m)return [parseInt(m[1],10),parseInt(m[2]||0,10)];
      return [999,999];
    }
    flowTasks.sort((a,b)=>{
      const [ma1,mi1]=parseStep(a);
      const [ma2,mi2]=parseStep(b);
      return ma1!==ma2?ma1-ma2:mi1-mi2;
    });
    const activeTask=(ctx.taskId&&flowTasks.find(t=>t.id===ctx.taskId))||(ctx.nodeId&&flowTasks.find(t=>t.workflow_node_id===ctx.nodeId))||flowTasks[0]||null;
    if(!activeTask){host.innerHTML='<p class="pw-dock-empty">当前对话尚未关联工作流节点。</p>';return;}
    const executorName=activeTask?(activeTask.latest_run?.executor_snapshot?.member?.name||activeTask.latest_run?.executor_snapshot?.assistant?.name||stateBindingName(activeTask)):'项目负责专家';
    const isReview=activeTask?.status==='awaiting_review';
    const isDone=activeTask?.status==='delivered'||activeTask?.status==='succeeded';
    const isRunning=activeTask?.status==='running'||activeTask?.status==='queued';
    const hasWorkbench=activeTask&&WorkflowNodeWorkbench.eligible(activeTask);
    const dockMode=state.expertDockMode||'chat';

    const stepperHtml=flowTasks.length?`<div class="pw-dock-stepper" role="tablist" aria-label="工作流阶段导航">
      ${flowTasks.map(t=>{
        const [maj,min]=parseStep(t);
        const isCur=t.id===activeTask?.id;
        const stepNum=min?`${maj}.${min}`:`${maj}`;
        const statusLabel=labels[t.status]||t.status;
        const statusCls=t.status==='awaiting_review'?'status-review':((t.status==='delivered'||t.status==='succeeded')?'status-delivered':(t.status==='running'?'status-running':(['failed','interrupted'].includes(t.status)?'status-failed':'status-pending')));
        return `<button type="button" class="pw-stepper-pill ${isCur?'is-active':''} ${statusCls}" data-pw-step-id="${h(t.id)}" title="${h(t.title)} (${h(statusLabel)})">
          <span class="pw-stepper-dot"></span>
          <span class="pw-stepper-label">Step ${h(stepNum)}</span>
        </button>`;
      }).join('')}
    </div>`:'';

    const modeBarHtml=`<div class="pw-expert-mode-bar" role="tablist">
      <button type="button" class="pw-expert-mode-tab ${dockMode==='chat'?'is-active':''}" data-pw-expert-tab="chat">💬 专家对话</button>
      <button type="button" class="pw-expert-mode-tab ${dockMode==='trajectory'?'is-active':''}" data-pw-expert-tab="trajectory">⚡ 执行轨迹</button>
      <button type="button" class="pw-expert-mode-tab ${dockMode==='artifact'?'is-active':''}" data-pw-expert-tab="artifact">📄 本步产物</button>
    </div>`;

    const chipsHtml=`<div class="pw-dock-chips">
      <button type="button" class="pw-dock-chip" data-pw-chip-text="请核实本节点产物引用的实验数据与证据链是否完整。">🔍 核实证据数据</button>
      <button type="button" class="pw-dock-chip" data-pw-chip-text="请在产物中补充横向对比分析表格，对齐各项关键指标。">📊 补充对比表格</button>
      <button type="button" class="pw-dock-chip" data-pw-chip-text="请精简本节点正文，保留核心论据与交付结论。">✍️ 精简语言</button>
      <button type="button" class="pw-dock-chip" data-pw-chip-text="请阐明推导关键逻辑与可复现执行步骤。">🛠️ 阐明推导逻辑</button>
    </div>`;

    let actionsHtml='';
    if(isReview){
      actionsHtml=`
        ${hasWorkbench?`<button type="button" class="btn ghost sm" data-pw-expert-workbench>产物工作台</button>`:''}
        <button type="button" class="btn ghost sm" data-pw-expert-discuss>💬 发送讨论</button>
        <button type="button" class="btn ghost sm" data-pw-dock-reject style="color:#d97706;border-color:#d97706">⚡ 退回重做</button>
        <button type="button" class="btn btn-primary sm" data-pw-dock-approve style="background:#059669;border-color:#059669;color:#fff">✓ 通过并交付</button>
      `;
    }else if(isDone){
      actionsHtml=`
        ${hasWorkbench?`<button type="button" class="btn btn-primary sm" data-pw-expert-workbench>打开产物工作台</button>`:''}
        <button type="button" class="btn ghost sm" data-pw-expert-discuss>💬 补充提问</button>
        <button type="button" class="btn ghost sm" data-pw-expert-rework>⚡ 重新生成本节点</button>
      `;
    }else if(isRunning){
      actionsHtml=`
        <span class="hint" style="align-self:center;font-size:12px">${h(activeTask.title)} · 第 ${h(activeTask.latest_run?.attempt||1)} 次尝试 · ${h(labels[activeTask.status])} · ${h(({model:'等待模型响应',tool:'等待工具返回',tools_ready:'继续剩余工具'})[activeTask.latest_run?.checkpoint?.phase]||'准备执行')}</span>
        <button type="button" class="btn ghost sm" data-pw-expert-tab-switch="trajectory">查看实时轨迹</button>
        ${hasWorkbench?`<button type="button" class="btn ghost sm" data-pw-expert-workbench>产物工作台</button>`:''}
      `;
    }else{
      actionsHtml=`
        ${hasWorkbench?`<button type="button" class="btn ghost sm" data-pw-expert-workbench>打开产物工作台</button>`:''}
        <button type="button" class="btn ghost sm" data-pw-expert-discuss ${activeTask?'':'disabled'}>💬 发送讨论</button>
        <button type="button" class="btn btn-primary sm" data-pw-expert-rework ${activeTask?'':'disabled'}>⚡ 交专家执行节点</button>
      `;
    }

    host.innerHTML=`<section class="pw-expert-dock" aria-label="节点专家侧栏对话">
      <header class="pw-expert-dock-head">
        <div><span class="pw-eyebrow">节点值班专家 · 实时对话与微调</span><strong style="display:block;font-size:14px">${h(executorName)} · ${h(activeTask?.title||ctx.nodeLabel||'当前工作流节点')}</strong></div>
        <div class="pt-row">
          <select class="control" data-pw-expert-task aria-label="切换工作流节点" style="max-width:210px;font-size:12px">
            ${flowTasks.map(t=>`<option value="${h(t.id)}" ${t.id===activeTask?.id?'selected':''}>${h(t.title)} (${h(labels[t.status]||t.status)})</option>`).join('')||`<option value="">${h(ctx.nodeLabel||'当前节点')}</option>`}
          </select>
          <button type="button" class="btn ghost" data-pw-expert-to-flow>返回 DAG 图</button>
        </div>
      </header>
      ${stepperHtml}
      ${modeBarHtml}
      <p class="pw-monitor-error" data-pw-monitor-error role="status"></p>
      <div class="pw-expert-dock-body" data-pw-expert-body>
        <p class="hint">正在加载节点内容…</p>
      </div>
      <div class="pw-expert-dock-compose">
        ${chipsHtml}
        <label style="display:grid;gap:4px">
          <textarea class="control" data-pw-expert-input rows="3" placeholder="向节点专家提问、下达核查指示或微调指令…"></textarea>
        </label>
        <div class="pw-decision-row">
          ${actionsHtml}
        </div>
      </div>
    </section>`;

    host.querySelector('[data-pw-expert-to-flow]').onclick=()=>showResource('workflow',state,{peek:true});
    const picker=host.querySelector('[data-pw-expert-task]');
    if(picker)picker.onchange=()=>{const chosen=flowTasks.find(t=>t.id===picker.value);if(chosen)openDockExpertChat(state,chosen,null);};

    host.querySelectorAll('[data-pw-step-id]').forEach(pill=>{
      pill.onclick=()=>{
        const chosen=flowTasks.find(t=>t.id===pill.dataset.pwStepId);
        if(chosen)openDockExpertChat(state,chosen,null);
      };
    });

    host.querySelectorAll('[data-pw-expert-tab]').forEach(tabBtn=>{
      tabBtn.onclick=async()=>{
        state.expertDockMode=tabBtn.dataset.pwExpertTab;persist(state);
        await mountExpertChat(state,host);
      };
    });

    const switchTrajBtn=host.querySelector('[data-pw-expert-tab-switch="trajectory"]');
    if(switchTrajBtn)switchTrajBtn.onclick=async()=>{
      state.expertDockMode='trajectory';
      await mountExpertChat(state,host);
    };

    host.querySelectorAll('[data-pw-chip-text]').forEach(chip=>{
      chip.onclick=()=>{
        const input=host.querySelector('[data-pw-expert-input]');
        if(input){
          const prefix=input.value.trim()?input.value.trim()+'\n':'';
          input.value=prefix+chip.dataset.pwChipText;
          input.focus();
        }
      };
    });

    async function loadExpertBody(){
    const bodyEl=host.querySelector('[data-pw-expert-body]');
    if(activeTask&&bodyEl&&alive()){
      if(dockMode==='chat'){
        try{
          const convRes=await api('/api/execution-conversations?kind=task&entity_id='+encodeURIComponent(activeTask.id)+'&project_id='+encodeURIComponent(state.id));
          if(!alive())return;
          const msgs=convRes.messages||[];
          const signature=JSON.stringify(msgs);if(signature===bodySignature)return;bodySignature=signature;
          const oldScroll=bodyEl.scrollTop,atBottom=bodyEl.scrollHeight-bodyEl.clientHeight-oldScroll<60;
          if(!msgs.length){
            const defaultReply=activeTask.latest_run?.checkpoint?.reply||activeTask.goal||'我是负责本节点的科研专家。产物已准备完毕，请在下方下达核查指示或讨论指令。';
            bodyEl.innerHTML=`<div class="pw-msg is-assistant"><div class="pw-msg-head"><strong>👨‍🔬 ${h(executorName)}</strong><span class="badge ${isReview?'status-review':(isDone?'status-delivered':'')}">${h(labels[activeTask.status]||activeTask.status)}</span></div><div class="md">${md(defaultReply)}</div></div>`;
          }else{
            bodyEl.innerHTML=msgs.map(m=>{
              const isGoal=m.id&&m.id.endsWith(':goal');
              if(isGoal){
                return `<div class="pw-msg is-user is-goal">
                  <div class="pw-msg-head"><strong>🎯 节点任务目标</strong><time style="font-size:11px;color:var(--muted)">${h(fmtDate(m.created_at))}</time></div>
                  <div class="md">${md(m.content||'')}</div>
                </div>`;
              }
              const isUser=m.role==='user';
              if(isUser){
                const userContent=m.display_content||(m.content&&m.content.includes('\n用户问题：')?m.content.split('\n用户问题：').pop():m.content);
                return `<div class="pw-msg is-user">
                  <div class="pw-msg-head"><strong>👤 研究员 · 提问与指示</strong><time style="font-size:11px;color:var(--muted)">${h(fmtDate(m.created_at))}</time></div>
                  <div class="md">${md(userContent||'')}</div>
                </div>`;
              }
              const senderName=m.sender||executorName;
              const isInitial=m.status==='succeeded'&&(!m.tool_calls||!m.tool_calls.length)&&(!m.reasoning);
              const reasoningHtml=m.reasoning?`<details class="pw-msg-reasoning"><summary>过程摘要</summary><div class="pw-reasoning-body md">${md(m.reasoning)}</div></details>`:'';
              const toolsHtml=(m.tool_calls&&m.tool_calls.length)?`<div class="pw-msg-tools">${m.tool_calls.map(tc=>`
                <details class="pw-tool-call-item"><summary>
                  <span class="pw-tool-badge">${h(ChatPresentation.toolLabel(tc.name||'tool'))}</span>
                  <span class="pw-tool-detail">${h(ChatPresentation.toolSummary(tc)||'展开查看调用详情')}</span></summary>
                  <pre>${h(JSON.stringify(tc.args||{},null,2))}</pre>
                  ${tc.result_summary?`<pre>${h(tc.result_summary)}</pre>`:''}
                </details>`).join('')}</div>`:'';
              const artifactsHtml=(m.outputs&&m.outputs.length)?`<div class="pw-msg-artifacts">${m.outputs.map(f=>`
                <button type="button" class="pw-art-pill" data-pw-open-art="${h(f.path)}" data-pw-art-run="${h(m.id)}" data-pw-art-revision="${Number(m.output_revision||0)}" title="在大屏工作台查看此产物：${h(f.path)}">📄 ${h(f.name||(f.path||'').split(/[\\/]/).pop())}</button>`).join('')}</div>`:'';
              return `<div class="pw-msg is-assistant">
                <div class="pw-msg-head"><strong>👨‍🔬 ${h(senderName)}</strong>${isInitial?'<span class="badge status-delivered">初始交付产物</span>':''}<time style="font-size:11px;color:var(--muted)">${h(fmtDate(m.created_at))}</time></div>
                ${reasoningHtml}
                ${toolsHtml}
                <div class="md pw-msg-content">${md(m.content||'')}</div>
                ${artifactsHtml}
              </div>`;
            }).join('');
            bodyEl.querySelectorAll('[data-pw-open-art]').forEach(btn=>{
              btn.onclick=()=>openFilePreview({task_id:activeTask.id,task_run_id:btn.dataset.pwArtRun,output_revision:Number(btn.dataset.pwArtRevision||0),path:btn.dataset.pwOpenArt,name:btn.textContent.trim()},state.id);
            });
            bodyEl.scrollTop=atBottom?bodyEl.scrollHeight:oldScroll;
          }
        }catch(e){
          if(!alive())return;
          const defaultReply=activeTask.latest_run?.checkpoint?.reply||'我是负责本节点的科研专家。你可以在下方输入问题与我核对当前节点的产物与证据，或直接下达修改要求让我基于当前版本重做本节点。';
          bodyEl.innerHTML=`<div class="pw-msg is-assistant"><div class="pw-msg-head"><strong>👨‍🔬 ${h(executorName)}</strong><span>${h(labels[activeTask.status]||activeTask.status)}</span></div><div class="md">${md(defaultReply)}</div></div>`;
        }
      }else if(dockMode==='trajectory'){
        WorkspaceTrajectory.mount(bodyEl,{projectId:state.id,chatId:activeTask.chat_id||('execution-'+activeTask.id),
          runId:state.expertAttempts?.[activeTask.id]||state.expertJobs?.[activeTask.id]||activeTask.latest_run_id,live:alive,
          onSelectRun:runId=>{if(alive()){state.expertAttempts||={};state.expertAttempts[activeTask.id]=runId;persist(state);}}});
      }else if(dockMode==='artifact'){
        const files=activeTask.latest_run?.output_snapshot?.files||activeTask.outputs||[];
        bodyEl.innerHTML=files.map((file,index)=>`<button type="button" class="btn ghost" data-pw-preview-art="${index}">${h(file.name||file.relative||file.path)}</button>`).join('')||'<p class="pw-dock-empty">本节点尚未保存文件产物。</p>';
        bodyEl.querySelectorAll('[data-pw-preview-art]').forEach(button=>button.onclick=()=>openFilePreview({
          ...files[Number(button.dataset.pwPreviewArt)],task_id:activeTask.id,task_run_id:activeTask.latest_run_id,
          output_revision:activeTask.latest_run?.checkpoint?.output_revision||0},state.id));
      }
    }
    }
    await loadExpertBody();
    if(!alive())return;
    async function pollExpert(){
      if(!alive())return;
      try{
        const data=await api('/api/project-tasks/detail?project_id='+encodeURIComponent(state.id)+'&id='+encodeURIComponent(activeTask.id));
        if(!alive())return;
        const updated=data.task;
        state.tasks=state.tasks.map(task=>task.id===updated.id?updated:task);
        if(updated.status!==activeTask.status||updated.latest_run_id!==activeTask.latest_run_id){
          const text=host.querySelector('[data-pw-expert-input]')?.value||'';
          await mountExpertChat(state,host);
          if(host.isConnected&&live(state)&&state.chatId===chatId&&state.resourceEpoch===epoch&&host.querySelector('[data-pw-expert-input]'))host.querySelector('[data-pw-expert-input]').value=text;
          return;
        }
        const job=state.expertJobs?.[activeTask.id];
        if(job){
          const info=await api('/api/agent-settings/trajectory/events?project_id='+encodeURIComponent(state.id)+'&chat_id='+encodeURIComponent(activeTask.chat_id)+'&run_id='+encodeURIComponent(job)+'&limit=1');
          if(!alive())return;
          if(!['running','queued','pending'].includes(info.status))delete state.expertJobs[activeTask.id];
        }
        if(dockMode==='chat')await loadExpertBody();
        if(!['running','queued'].includes(updated.status)&&!state.expertJobs?.[activeTask.id])return;
      }catch(err){if(alive())host.querySelector('[data-pw-monitor-error]').textContent='更新失败，将自动重试：'+err.message;}
      if(alive())pollTimer=setTimeout(pollExpert,1500);
    }
    if(isRunning||state.expertJobs?.[activeTask.id])pollTimer=setTimeout(pollExpert,1500);

    const wbBtn=host.querySelector('[data-pw-expert-workbench]');
    if(wbBtn&&activeTask)wbBtn.onclick=()=>openNodeResult(WorkflowNodeWorkbench.availableSelection(activeTask),state.id,{chatId:state.chatId,peek:state.peek});

    const approveBtn=host.querySelector('[data-pw-dock-approve]');
    if(approveBtn)approveBtn.onclick=async()=>{
      if(!activeTask)return;
      approveBtn.disabled=true;
      try{
        const fb=(host.querySelector('[data-pw-expert-input]')?.value||'').trim();
        await api('/api/project-tasks/review',{
          id:activeTask.id,
          project_id:state.id,
          reviewed_run_id:activeTask.latest_run_id||'',
          reviewed_output_version:Number(activeTask.latest_run?.checkpoint?.output_revision||0),
          approved:true,
          feedback:fb,
          request_key:crypto.randomUUID()
        });
        if(curRunId){
          await api('/api/workflow/execution/resume',{id:curRunId,task_id:state.id}).catch(()=>{});
        }
        notice('节点已通过人工审查并交付下游！',state);
        await refresh(state);
        await mountExpertChat(state,host);
      }catch(err){
        error(err.message,state);
        approveBtn.disabled=false;
      }
    };

    const rejectBtn=host.querySelector('[data-pw-dock-reject]');
    if(rejectBtn)rejectBtn.onclick=async()=>{
      if(!activeTask)return;
      const text=(host.querySelector('[data-pw-expert-input]')?.value||'').trim();
      if(!text)return error('退回重做时请在输入框填写修改要求。',state);
      rejectBtn.disabled=true;
      try{
        await api('/api/project-tasks/review',{
          id:activeTask.id,
          project_id:state.id,
          reviewed_run_id:activeTask.latest_run_id||'',
          reviewed_output_version:Number(activeTask.latest_run?.checkpoint?.output_revision||0),
          approved:false,
          retry:true,
          feedback:text,
          request_key:crypto.randomUUID()
        });
        notice('已退回节点专家重做。',state);
        await refresh(state);
        await mountExpertChat(state,host);
      }catch(err){
        error(err.message,state);
        rejectBtn.disabled=false;
      }
    };

    const sendExpertAction=async mode=>{
      if(!activeTask)return;
      const input=host.querySelector('[data-pw-expert-input]');
      const text=(input?.value||'').trim();
      if(!text)return error('请输入要与节点专家讨论或修改的内容。',state);

      const bodyEl=host.querySelector('[data-pw-expert-body]');
      if(bodyEl){
        const userTurnHtml=`<div class="pw-msg is-user">
          <div class="pw-msg-head"><strong>👤 研究员 · 提问与指示</strong><time style="font-size:11px;color:var(--muted)">刚刚</time></div>
          <div class="md">${md(text)}</div>
        </div>`;
        const workingMsgHtml=`<div class="pw-msg is-assistant is-running" data-pw-live-agent>
          <div class="pw-msg-head"><strong>👨‍🔬 ${h(executorName)}</strong><span class="pw-live-pulse">● 正在思考与调用工具执行中…</span></div>
          <div class="pw-live-status"><span class="pw-spinner"></span> ${mode==='discuss'?'节点专家正在研读上下文并执行长时检索与推理…':'节点专家正在重新执行长时任务并更新产物…'}</div>
        </div>`;
        bodyEl.insertAdjacentHTML('beforeend',userTurnHtml+workingMsgHtml);
        bodyEl.scrollTop=bodyEl.scrollHeight;
      }

      host.querySelectorAll('button').forEach(b=>b.disabled=true);
      if(input)input.value='';

      try{
        const ws=await api('/api/project-tasks/node-workspace?project_id='+encodeURIComponent(state.id)+'&id='+encodeURIComponent(activeTask.id));
        const identity=ws.result?.identity;
        if(!identity)throw new Error('当前节点尚未生成可操作的运行快照');
        const cmdRes=await api('/api/project-tasks/node-command',{
          project_id:state.id,id:activeTask.id,action:mode,identity,
          ...(mode==='discuss'?{message:text}:{instruction:text}),
          request_key:crypto.randomUUID()
        });
        notice(mode==='discuss'?'讨论已下达给节点专家，正在执行长时任务…':'已指派节点专家重新执行本节点。',state);

        if(!alive())return;
        if(mode==='discuss'&&cmdRes.run_id){state.expertJobs||={};state.expertJobs[activeTask.id]=cmdRes.run_id;}
        const selectedRun=cmdRes.run_id||cmdRes.run?.id;
        if(selectedRun){state.expertAttempts||={};state.expertAttempts[activeTask.id]=selectedRun;persist(state);}
        await refresh(state);
        if(alive())await mountExpertChat(state,host);
      }catch(err){
        error(err.message,state);
        host.querySelectorAll('button').forEach(b=>b.disabled=false);
        const liveEl=host.querySelector('[data-pw-live-agent]');
        if(liveEl)liveEl.remove();
      }
    };

    const discussBtn=host.querySelector('[data-pw-expert-discuss]');
    if(discussBtn)discussBtn.onclick=()=>sendExpertAction('discuss');
    const reworkBtn=host.querySelector('[data-pw-expert-rework]');
    if(reworkBtn)reworkBtn.onclick=()=>sendExpertAction('rework');
  }
  async function mountWorkflow(state,host){
    const epoch=state.resourceEpoch,chatId=state.chatId;
    host.__graphDispose?.();
    const current=()=>live(state)&&state.chatId===chatId&&state.resourceEpoch===epoch&&state.section==='workflow';
    host.innerHTML='<p class="pw-loading" role="status">正在读取当前对话的工作流…</p>';
    let ctx;
    try{ctx=await conversationContext(state);}catch(err){
      if(current()){
        host.innerHTML=`<section class="pwg-empty"><p class="pw-error" role="alert">工作流上下文读取失败：${h(err.message)}</p><button type="button" class="btn" data-pw-context-retry>重试</button></section>`;
        host.querySelector('[data-pw-context-retry]').onclick=()=>mountWorkflow(state,host);
      }
      return;
    }
    if(!current()||!ctx)return;
    const scopedRuns=ctx?.runs||[];
    const activeRunId=state.focusWorkflow?.runId||(scopedRuns.some(run=>run.id===state.focusWorkflow?.runId)?state.focusWorkflow.runId:scopedRuns[0]?.id||'');
    await ProjectWorkflowGraph.mount(host,{
       projectId:state.id,chatId,scopedRuns,contextual:true,allowTemplate:!!state.allowWorkflowTemplate,enableTemplate:()=>{state.allowWorkflowTemplate=true;},live:()=>live(state)&&state.chatId===chatId&&state.resourceEpoch===epoch&&state.section==='workflow',
       focus:activeRunId?{runId:activeRunId,nodeId:state.focusWorkflow?.nodeId}:null,
       peek:state.peek,
       askSteward:async()=>{
         if(!current())return;
         state.scope='project';state.target='';persist(state);
         await switchSurface('chat',state);
         if(state.chatId!==chatId||!live(state))return;
         if(!draft(state).text?.trim())draft(state).text='请根据我的研究目标编排工作流：';
         paintDraft(state);persist(state);document.getElementById('pwAsk')?.focus();
       },
       saved:()=>refresh(state),reload:runId=>{if(runId&&typeof runId==='string'){state.dockRunId=runId;state.liveRunSelected=true;state.focusWorkflow={...state.focusWorkflow,runId};}return showResource('workflow',state,{peek:state.peek});},openTask:id=>openTask(id,state.id,{peek:state.peek}),openArtifacts:runId=>showResource('run-artifacts',state,{runId}),
       openResult:selection=>openNodeResult(selection,state.id,{chatId:state.chatId,peek:state.peek}),
       openStageResult:selection=>openNodeResult(selection,state.id,{chatId:state.chatId,peek:false}),
       openExpertChat:(task,node)=>openDockExpertChat(state,task,node),
       onDeleteRun:async runId=>{
         try{
           await api('/api/workflow/execution/delete',{id:runId,project_id:state.id});
           state.focusWorkflow=null;
           state.conversationContext=null;
           await refresh(state);
           await showResource('workflow',state,{peek:state.peek});
           notice('已删除工作流运行记录。',state);
         }catch(err){error('删除工作流失败：'+err.message,state);}
       },
      handoff:async nodeId=>{
        state.scope='workflow';state.targets=await api('/api/handoff/targets?project_id='+encodeURIComponent(state.id));
        await refresh(state);if(!live(state))return;
        document.getElementById('pwScope').value='workflow';paintTargets(state);
        const choice=targetRows(state).find(target=>target.node.id===nodeId);
        if(!choice)return error('该节点尚未绑定可用执行者，请先配置节点执行者。',state);
        state.target=choice.value;paintTargets(state);document.getElementById('pwRecipient').value=choice.value;
        paintSend(state);paintChatOptions(state);persist(state);await switchSurface('chat',state);document.getElementById('pwAsk').focus();
      }
    });
    persist(state);
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!e.isComposing&&active()&&!document.querySelector('dialog[open],.modal:not(.hidden)')){const run=mounted.chatId&&chatRecord(mounted.chatId).run;if(run?.status==='running'){e.preventDefault();api('/api/agent/cancel',{idempotency_key:run.key}).catch(err=>error(err.message));}}});
  window.addEventListener('beforeunload',e=>{if(active()&&hasDirty()){e.preventDefault();e.returnValue='';}});
  window.addEventListener('resize',()=>{if(active())layoutPanels(mounted);});
  const oldTabs=tabs.filter(t=>['space','workflow','notes','workspace'].includes(t.route));
  if(oldTabs.length){const keep=oldTabs.find(t=>t.id===activeTab)||oldTabs[0];if(keep.route!=='workspace')requestRoute(keep.route);keep.route='workspace';keep.title='项目工作区';tabs=tabs.filter(t=>!oldTabs.includes(t)||t===keep);}
  async function applyTemplate(project,value){await openProject(project);if(!live(get(project))||!await flush())return;const state=get(project);draft(state).template=value;draft(state).text=`请按照「${value.title}」完成任务。`;persist(state);await switchSurface('chat',state);paintDraft(state);document.getElementById('pwAsk').focus();}
  async function openConversation(chatId,projectId){if(!(S.chats||[]).some(chat=>chat.id===chatId))await refreshState(false);if(!active()||mounted?.id!==projectId)await openProject(projectId);const state=get(projectId);await selectChat(state,chatId);}
  function navigationContext(){
    if(!active())return null;
    const {id,chatId,chatView,surface,section,sections,peek,inspection,fileDirectory,fileRow,libraryFile,
      nodeResultSelection,artifactRunId,searchQuery,navHidden,treeHidden,review}=mounted;
    const savedSections={...sections},chat=surface==='chat'&&!peek;
    const note=!chat&&section==='notes'&&mdProj?.pid===id?mdProj:null;
    const label=(S.tasks.find(project=>project.id===id)?.title||TITLES.workspace)+' · '+
      (chat?surfaces.chat:section==='task-detail'?'任务详情':resources[section]||surfaces[surface]);
    const key=[id,surface,chat?'chat':section,chatId,chatView,peek,review?.id,fileRow?.rel,
      nodeResultSelection?.task_id,artifactRunId];
    return {label,key,async restore(){
      await openProject(id);
      const state=get(id);
      if(!live(state))return;
      if(state.chatId!==chatId)await selectChat(state,chatId);
      Object.assign(state,{sections:{...savedSections},fileDirectory,fileRow,libraryFile,nodeResultSelection,artifactRunId,
        searchQuery,navHidden,treeHidden});
      if(chat){
        await switchSurface('chat',state);
        state.chatView=chatView||'chat';
        await paintChat(state);
      }else if(section==='task-detail'&&review){
        await openTask(review.id,id,{peek,preferWorkbench:false});
      }else{
        if(inspection?.origin==='task')state.review=review;
        await showResource(section,state,{surface,peek,inspection,initial:true});
        if(note?.path&&live(state)&&state.section==='notes'&&mdDashPath!==note.path)await Notebook.open(note.path);
      }
    }};
  }
  window.ProjectWorkspace={openConversation,openWorkflowRun,openWorkflowDock,openWorkflowNode,openStage,active,hasSurface,hasDirty,flush,leave,requestRoute,render,openProject,openTask,openNodeResult,openFile,openFilePreview,openPaper,currentFile,referenceResource,handoffResource,showResource,createEntry,refreshFiles,applyTemplate,navigationContext,refresh:()=>mounted&&refresh(mounted)};
  return window.ProjectWorkspace;
})();
