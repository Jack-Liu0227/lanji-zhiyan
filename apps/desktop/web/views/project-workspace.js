/* Project-owned discussions, task execution and document review share one surface. */
const ProjectWorkspace = (() => {
  "use strict";
  const labels={ready:"可执行",draft:"已准备",queued:"排队中",running:"执行中",awaiting_review:"等待人工核查",delivered:"已交付",blocked:"等待处理",failed:"执行失败",cancelled:"已停止",waiting:"等待前置交付"};
  const resources={catalog:'统一资料库',manuscripts:'论文稿件',experiments:'实验记录',outputs:'交付产物',files:"文件",papers:"文献",notes:"笔记与成果",tasks:"任务与自动化",automation:"自动化",workflow:"LangGraph 工作流",plan:"进度与计划",stars:"论文星图",overview:"项目概况",search:"搜索项目资料"};
  const surfaces={chat:'目标对话',library:'统一资料库',tasks:'任务与自动化',flow:'LangGraph 图流'};
  const resourceSurface={catalog:'library',manuscripts:'library',experiments:'library',outputs:'library',files:'library',papers:'library',notes:'library',stars:'library',search:'library',tasks:'tasks',automation:'tasks',plan:'tasks',workflow:'flow',overview:'library','task-detail':'tasks'};
  const states=new Map(), chats=new Map();
  let mounted=null, intent=null, generation=0, navigation=0, timer=null, restored=false;
  const h=value=>esc(String(value??""));
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
  const write=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));}catch{}};
  const root=()=>document.getElementById("projectWorkspace");
  const active=()=>route()==="workspace" && !!root();
  const live=state=>active() && mounted===state && curTask===state.id && root()?.dataset.projectId===state.id;
  const get=id=>{
    if(!states.has(id)){
      const saved=read("rw-workspace:"+id,{});if(saved.section==='tools')saved.section='workflow';if(saved.sections?.flow==='tools')saved.sections.flow='workflow';
      states.set(id,{id,chatId:saved.chatId||"",section:saved.section||"catalog",surface:surfaces[saved.surface]?saved.surface:'chat',sections:{library:'catalog',tasks:'tasks',flow:'workflow',...saved.sections},peek:false,navOpen:false,inspection:null,mode:saved.mode||"supervised",modeTouched:!!saved.modeTouched,scope:saved.scope||"project",target:saved.target||"",reading:false,
        searchQuery:saved.searchQuery||"",navHidden:!!saved.navHidden,treeHidden:!!saved.treeHidden,drafts:read("rw-workspace-drafts:"+id,{}),fileRow:saved.fileRow||null,libraryFile:saved.libraryFile||saved.fileRow||null,tasks:[],bindings:[],teams:[],targets:null,layout:null,project:null,review:null,filter:"",epoch:0,resourceEpoch:0,refreshing:false});
    }
    return states.get(id);
  };
  function persist(state){write("rw-workspace:"+state.id,{chatId:state.chatId,section:state.section,surface:state.surface,sections:state.sections,searchQuery:state.searchQuery,mode:state.mode,modeTouched:state.modeTouched,scope:state.scope,target:state.target,navHidden:state.navHidden,treeHidden:state.treeHidden,fileRow:state.fileRow,libraryFile:state.libraryFile});write("rw-workspace-drafts:"+state.id,state.drafts);}
  function draft(state){const key=state.chatId||"new";return state.drafts[key]||(state.drafts[key]={text:"",refs:[]});}
  function currentChat(state){return (S.chats||[]).find(c=>c.id===state.chatId && c.task_id===state.id);}
  function chatRecord(id){if(!chats.has(id))chats.set(id,{messages:[],events:[],run:null,loaded:false,request:0});return chats.get(id);}
  function error(message,state=mounted){if(live(state)){const box=document.getElementById("pwError");if(box)box.textContent=message||"";}}
  function notice(message,state=mounted){if(live(state)){const box=document.getElementById("pwNotice");if(box)box.textContent=message||"";}}
  function hasSurface(kind){return active() && (mounted.surface!=='chat'||mounted.peek) && ((kind==="space"&&["papers","notes","plan","stars","overview"].includes(mounted.section)) || (kind==="workflow"&&["files","tools"].includes(mounted.section)));}
  function hasDirty(){
    if(!active())return false;
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
    else if(value==='tasks'||value==='automation')intent={section:value};
    else if(value==="notes")intent={section:"notes"};
    else if(value==="space")intent={section:typeof spTab!=="undefined"?spTab:"overview"};
  }
  function projectOptions(id){return (S.tasks||[]).map(p=>`<option value="${h(p.id)}" ${p.id===id?"selected":""}>${h(p.title)}</option>`).join("");}
  function icon(name){
    const paths={chat:'M4 4h16v11H9l-5 5V4Z',library:'M3 7h7l2-3h9v16H3V7Z',tasks:'m4 6 2 2 4-4M13 6h7M4 13l2 2 4-4M13 13h7M4 20h16',flow:'M8 3h8v5H8V3ZM3 16h6v5H3v-5Zm12 0h6v5h-6v-5ZM12 8v4M6 16v-4h12v4',menu:'M4 6h16M4 12h16M4 18h16',back:'m10 5-7 7 7 7M3 12h18'};
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
          <section data-pw-nav-group="chat" class="pw-nav-section"><button type="button" class="pw-new-chat" id="pwNewChat">＋ 新对话</button><div class="pw-sessions" id="pwSessions"></div></section>
          <section data-pw-nav-group="library" class="pw-nav-section"><form id="pwSearchForm" class="pw-search-form" role="search"><input type="search" class="control" id="pwSearchInput" aria-label="搜索当前项目资料" placeholder="搜索文献、笔记和进度…"><button type="submit" class="pw-text-button">搜索</button></form><button type="button" class="btn ghost" id="pwUploadFile">上传文件（也可拖入）</button><div class="pw-resource-links"><button type="button" data-pw-view="catalog">全部资料</button><button type="button" data-pw-view="files">目录与文件</button><button type="button" data-pw-view="papers" title="文献论文 (PDF)">文献论文 (PDF)</button><button type="button" data-pw-view="notes" title="研读笔记 (Markdown)">研究笔记 (Markdown)</button><button type="button" data-pw-view="stars" title="文献关联知识星图">论文星图</button></div><div id="pwSidebarTreeSlot"><div class="pw-file-tree" id="pwFileTree"></div></div></section>

          <section data-pw-nav-group="flow" class="pw-nav-section"><div class="pw-resource-links"><button type="button" data-pw-view="workflow">LangGraph 工作流</button></div><p class="pw-nav-help">在同一张图中配置节点、绑定执行者并跟踪交付。</p></section>
          <div class="pw-nav-project" id="pwNavProject">当前项目内协作</div>
        </nav>
        <section class="pw-main" id="pwWorkArea" role="tabpanel" aria-labelledby="pwTab-${state.surface}">
          <section class="pw-chat" aria-label="项目 AI 对话"><header class="pw-chat-head"><div><span class="pw-eyebrow">项目对话</span><strong id="pwChatTitle">${h(currentChat(state)?.title||'新的课题讨论')}</strong></div><span class="pw-spacer"></span><button type="button" class="pw-text-button" id="pwRenameChat">改名</button><button type="button" class="btn ghost" id="pwTopLanggraph" title="查看或启动 LangGraph 科研图流与人工审查">⚡ LangGraph 图流</button><button type="button" class="btn ghost" id="pwCreateTask">新建任务</button><button type="button" class="btn ghost" id="pwShowDoc">浏览资料</button></header><nav class="pw-conversation-tabs" role="tablist" aria-label="会话查看方式"><button type="button" role="tab" id="pwChatView" data-pw-chat-view="chat" aria-controls="pwMessages">对话</button><button type="button" role="tab" id="pwTraceView" data-pw-chat-view="trajectory" aria-controls="pwTrajectory">执行轨迹</button></nav><div id="pwMessages" class="pw-discussion" role="log" aria-label="当前项目对话"></div><section class="workspace-trajectory" id="pwTrajectory" aria-label="当前对话执行轨迹" hidden></section>
            <div class="pw-compose composer"><div class="pw-context-bar" id="pwRefs"></div><textarea id="pwAsk" rows="3" aria-label="向项目专家提出目标" placeholder="描述目标，或 @ 项目专家与团队…"></textarea><div class="pw-compose-options">${AgentRuntime.markup('pw',currentChat(state)||{})}<label class="pw-target-detail" id="pwRecipientDetailControl" hidden><span id="pwRecipientDetailLabel">团队成员</span><select class="control" id="pwRecipientDetail" aria-label="团队成员"></select></label></div><div class="pw-send-row"><button type="button" class="pw-text-button" id="pwAddReference" title="添加项目资料">＋ 资料</button><button type="button" class="pw-text-button" id="pwAddFile">＋ 上传</button><input type="file" id="pwFileInput" multiple hidden><button type="button" class="pw-text-button" id="pwAddImage">＋ 图片</button><input type="file" id="pwImageInput" multiple hidden accept=".png,.jpg,.jpeg,.webp,.pdf"><select class="control pw-compose-project" id="pwComposeProject" aria-label="对话所属项目" title="切换项目，分别保留各项目的草稿与运行">${projectOptions(state.id)}</select><select class="control pw-scope" id="pwScope" aria-label="发送范围"><option value="project">当前项目</option><option value="workflow">当前工作流</option></select><select class="control" id="pwRecipient" aria-label="发送目标"><option value="">项目专家</option></select><span class="pw-spacer"></span><button type="button" class="pw-text-button" id="pwPrepareTask" hidden>准备任务</button><button type="button" class="pw-send-button" id="pwSend" aria-label="发送" title="Enter 发送">↑</button></div><span id="pwScopeLabel" class="pw-compose-caption">当前项目 · 已授权执行者</span></div>

          </section>
          <aside class="pw-doc" id="pwDocumentPane" aria-label="项目资料与任务内容"><header class="pw-doc-head"><div class="pw-doc-heading"><div class="pw-trail"><button type="button" id="pwBack" class="pw-text-button" hidden>${icon('back')}<span>返回</span></button><span id="pwTrail">资料</span></div><strong id="pwResourceTitle">项目文件</strong></div><div class="pw-doc-actions"><button type="button" class="btn ghost" id="pwToggleTree" aria-controls="pwFileTree" aria-expanded="true">收起目录</button><button type="button" class="btn ghost" id="pwQuote">引用选区</button><button type="button" class="btn" id="pwUseMaterial">引用到对话</button><button type="button" class="btn ghost" id="pwMaterialTask">创建任务</button><button type="button" class="pw-text-button" id="pwFocus">专注阅读</button><button type="button" class="pw-text-button" id="pwCloseDoc" aria-label="返回对话">返回对话</button></div><div class="pw-file-tools" id="pwFileToolbar" hidden></div></header><div class="pw-resource" id="pwResource"></div><section id="pwReview" class="pw-review" aria-label="人工核查" hidden></section></aside>
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
    node.classList.toggle('pw-reading',state.reading);node.classList.toggle('pw-nav-closed',state.navHidden);node.classList.toggle('pw-nav-open',state.navOpen);node.classList.toggle('pw-peek',state.surface==='chat'&&state.peek);node.classList.toggle('pw-narrow',small);
    chat.hidden=state.surface!=='chat';chat.inert=small&&state.surface==='chat'&&state.peek;
    document.getElementById('pwWorkArea').inert=small&&state.navOpen;
    doc.hidden=state.surface==='chat'&&!state.peek;
    if(small&&state.surface==='chat'&&state.peek){doc.setAttribute('role','dialog');doc.setAttribute('aria-modal','true');}else{doc.removeAttribute('role');doc.removeAttribute('aria-modal');}
    document.getElementById('pwWorkArea').setAttribute('aria-labelledby','pwTab-'+state.surface);
    node.querySelectorAll('[data-pw-surface]').forEach(b=>{const on=b.dataset.pwSurface===state.surface;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
    node.querySelectorAll('[data-pw-nav-group]').forEach(group=>group.hidden=group.dataset.pwNavGroup!==state.surface);
    node.querySelectorAll('[data-pw-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.pwView===state.section&&state.surface===resourceSurface[state.section]?'page':'false'));
    document.getElementById('pwNavTitle').textContent={chat:'项目对话',library:'项目资料',tasks:'执行与交付',flow:'LangGraph 工作流'}[state.surface];
    document.getElementById('pwNavShade').hidden=!small||!state.navOpen;
    document.getElementById('pwToggleNav').setAttribute('aria-expanded',String(small?state.navOpen:!state.navHidden&&!state.reading));
    const nav=document.getElementById('pwProjectNav');nav.inert=small?!state.navOpen:state.navHidden||state.reading;
    if(small&&state.navOpen){nav.setAttribute('role','dialog');nav.setAttribute('aria-modal','true');doc.removeAttribute('aria-modal');}else{nav.removeAttribute('role');nav.removeAttribute('aria-modal');}
    const tree=document.getElementById('pwFileTree'),inline=document.getElementById('pwInlineTree'),slot=document.getElementById('pwSidebarTreeSlot');
    const inPeek=state.surface==='chat'&&state.peek;
    if(tree&&slot){const target=state.section==='files'&&inPeek&&inline?inline:slot;if(tree.parentElement!==target)target.append(tree);tree.hidden=state.treeHidden||(inPeek&&!state.peekTree);if(inline)inline.hidden=!inPeek||!state.peekTree;}
    const material=['files','notes'].includes(state.section)&&!!currentFile(),output=state.inspection?.origin==='task';
    const treeVisible=inPeek?!!state.peekTree:small?state.navOpen:!state.treeHidden&&!state.navHidden&&!state.reading;
    const toggle=document.getElementById('pwToggleTree');toggle.hidden=state.section!=='files'||output;toggle.textContent=small&&!inPeek?'项目目录':treeVisible?'收起目录':'显示目录';toggle.setAttribute('aria-expanded',String(treeVisible));
    document.getElementById('pwFileToolbar').hidden=state.section!=='files';
    document.getElementById('pwUseMaterial').hidden=!material;
    document.getElementById('pwQuote').hidden=!material;
    document.getElementById('pwMaterialTask').hidden=!material||state.section==='files'||output;
    document.getElementById('pwFocus').hidden=!['files','notes'].includes(state.section);document.getElementById('pwFocus').textContent=state.reading?'退出专注':'专注阅读';
    const back=document.getElementById('pwBack');back.hidden=!output&&state.section!=='task-detail'&&!inPeek&&state.inspection?.origin!=='chat';back.querySelector('span').textContent=output?'返回任务':state.section==='task-detail'?'任务列表':'返回对话';
    document.getElementById('pwCloseDoc').hidden=state.surface!=='chat';
    document.getElementById('pwTrail').textContent=output?'任务产物 · 固定版本':state.section==='task-detail'?'任务与核查':(surfaces[resourceSurface[state.section]]||'资料')+(state.fileRow&&state.section==='files'?' / '+state.fileRow.rel.split('/').slice(0,-1).join('/'):'');
    if(state.review)paintReview(state,state.review);else document.getElementById('pwReview').hidden=true;
  }
  async function switchSurface(surface,state=mounted){
    if(!live(state)||!surfaces[surface]||!await flush())return;
    if(state.inspection?.origin==='task')state.fileRow=state.libraryFile;
    state.navOpen=false;state.reading=false;state.peek=false;state.inspection=null;
    if(surface==='chat'){state.resourceEpoch++;state.surface='chat';state.chatView='chat';persist(state);layoutPanels(state);paintChatView(state);document.getElementById('pwAsk').focus();return;}
    await showResource(state.sections[surface]||{library:'files',tasks:'tasks',flow:'workflow'}[surface],state);
  }
  function closeNavigator(state){state.navOpen=false;persist(state);layoutPanels(state);document.getElementById('pwToggleNav').focus();}

  async function render(container){
    if(!curTask)needTask();
    if(!curTask){container.innerHTML=`<section class="card"><h2>项目工作区</h2><p class="hint">先建立一个项目，再添加资料、专家和研究任务。</p><button class="btn btn-primary" id="pwFirstProject">新建项目</button></section>`;document.getElementById("pwFirstProject").onclick=()=>ProjectManager.open();return;}
    if(mounted&&root()&&mounted.id!==curTask){const previous=mounted;if(!await flush()){curTask=previous.id;localStorage.setItem("rw-task",curTask);document.getElementById("pwProject").value=curTask;renderStatus();return;}}
    const state=get(curTask);const requested=intent;intent=null;
    if(root()&&mounted===state){
      document.getElementById("pwProject").innerHTML=projectOptions(state.id);document.getElementById("pwComposeProject").innerHTML=projectOptions(state.id);paintSessions(state);refresh(state);
      if(requested)await showResource(requested.section,state);else if(state.surface==='flow'&&state.section==='workflow')await showResource('workflow',state);return;
    }
    generation++;mounted=state;state.epoch=++generation;
    const pool=(S.chats||[]).filter(c=>c.task_id===state.id);if(!pool.some(c=>c.id===state.chatId))state.chatId=pool[0]?.id||"";
    container.innerHTML=shell(state);bind(state);layoutPanels(state);paintSessions(state);paintDraft(state);paintChat(state);
    if(requested)await showResource(requested.section,state,{initial:true});
    else if(state.surface!=='chat')await showResource(resources[state.section]?state.section:state.sections[state.surface],state,{initial:true});
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
    document.getElementById("pwAsk").oninput=e=>{draft(state).text=e.target.value;persist(state);if(e.target.value.endsWith("@")){document.getElementById("pwRecipient").focus();notice("选择项目或当前工作流配置的专家、团队或节点。",state);}};
    document.getElementById("pwAsk").onkeydown=e=>{if(e.isComposing||e.keyCode===229)return;if(e.key==="Enter"&&!e.shiftKey&&!e.ctrlKey&&!e.altKey&&!e.metaKey){e.preventDefault();send(state);}};
    document.getElementById("pwSend").onclick=()=>send(state);
    document.getElementById("pwNewChat").onclick=()=>newChat(state);
    document.getElementById("pwRenameChat").onclick=async()=>{const row=currentChat(state);if(!row)return notice("发送第一条消息后可以给对话改名。",state);const title=await uiPrompt("对话名称",row.title);if(title===null)return;await api("/api/chat/rename",{id:row.id,title});await refreshState(false);if(live(state)){paintSessions(state);document.getElementById("pwChatTitle").textContent=currentChat(state)?.title||"课题讨论";}};
    document.getElementById('pwToggleNav').onclick=()=>{if(narrow())state.navOpen=!state.navOpen;else{state.navHidden=state.reading?false:!state.navHidden;state.reading=false;}persist(state);layoutPanels(state);if(state.navOpen)document.getElementById('pwNavClose').focus();};
    document.getElementById('pwNavShade').onclick=()=>closeNavigator(state);document.getElementById('pwNavClose').onclick=()=>{if(narrow())closeNavigator(state);else{state.navHidden=true;persist(state);layoutPanels(state);}};
    document.getElementById('pwToggleTree').onclick=()=>{if(state.surface==='chat'){state.peekTree=!state.peekTree;}else if(narrow()){state.navOpen=!state.navOpen;state.treeHidden=false;}else{state.treeHidden=state.navHidden||state.reading?false:!state.treeHidden;if(!state.treeHidden){state.navHidden=false;state.reading=false;}}persist(state);layoutPanels(state);if(state.navOpen)document.getElementById('pwNavClose').focus();};
    document.getElementById('pwShowDoc').onclick=()=>switchSurface('library',state);
    document.getElementById('pwCloseDoc').onclick=()=>switchSurface('chat',state);
    document.getElementById('pwBack').onclick=()=>state.inspection?.origin==='task'?openTask(state.inspection.taskId,state.id):state.section==='task-detail'?showResource('tasks',state):switchSurface('chat',state);
    document.getElementById('pwFocus').onclick=async()=>{if(!await flush()||!live(state))return;if(state.surface==='chat'){state.surface='library';state.peek=false;state.inspection={origin:'chat'};state.sections.library=state.section;}state.reading=!state.reading;state.navOpen=false;persist(state);layoutPanels(state);};
    document.getElementById("pwBindings").onclick=()=>ProjectTasks.openBindings(state.id).then(d=>d?.addEventListener("close",()=>refresh(state),{once:true}));
    document.getElementById("pwMembers").onclick=()=>uiNavigate(()=>openTab("team"));
    document.getElementById("pwExperts").onclick=()=>uiNavigate(()=>openTab("assistants"));
    const compose=async data=>{if(!await flush())return;const dialog=await ProjectTasks.compose(state.id,data);dialog?.addEventListener('close',()=>refresh(state),{once:true});};
    document.getElementById('pwTopLanggraph').onclick=()=>showResource('workflow',state);
    document.getElementById('pwCreateTask').onclick=()=>compose({goal:draft(state).text,inputs:(draft(state).refs||[]).map(r=>({relative:r.relative}))});
    document.getElementById('pwMaterialTask').onclick=()=>{const file=currentFile();if(file)compose({inputs:[{relative:file.relative}]});};
    document.getElementById('pwAddReference').onclick=()=>state.peek&&currentFile()?addReference(state):switchSurface('library',state);
    document.getElementById('pwUseMaterial').onclick=()=>addReference(state);
    document.getElementById("pwQuote").onclick=()=>quote(state);
    root().addEventListener("click",e=>{
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
      if(state.chatId&&!(S.chats||[]).some(chat=>chat.id===state.chatId)){state.chatId='';persist(state);paintDraft(state);await paintChat(state);}
      document.getElementById('pwChatTitle').textContent=currentChat(state)?.title||'新的课题讨论';
    }});
    document.getElementById("pwChatTitle").textContent=currentChat(state)?.title||"新的课题讨论";
    return history.ready;
  }
  async function selectChat(state,id){
    if(state.sending||state.creating)return notice('正在保存当前请求，请稍后切换对话。',state);
    if(!live(state)||!await flush())return;
    if(id && !(S.chats||[]).some(c=>c.id===id&&c.task_id===state.id))return error("对话不属于当前项目。",state);
    state.chatId=id;persist(state);paintSessions(state);paintDraft(state);paintChat(state);error("",state);
  }
  async function newChat(state){
    if(!live(state)||state.sending||state.creating)return;
    state.creating=true;document.getElementById('pwNewChat').disabled=true;
    try{
      if(!await flush())return;
      if(!state.chatId&&(draft(state).text.trim()||draft(state).refs.length))await ensureChat(state);
      const result=await api('/api/chat/create',{title:'新的课题讨论',task_id:state.id});
      state.chatId=result.chat.id;persist(state);await refreshState(false);
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
    document.getElementById('pwRuntimeHint').textContent=state.target?'节点任务使用执行者配置':state.optionSaving?'正在保存…':'';
  }
  async function saveChatOption(state,key,value){
    if(!live(state)||state.optionSaving)return;state.optionSaving=true;paintChatOptions(state);
    try{const id=await ensureChat(state);if(!live(state))return;paintChatOptions(state);const result=await api('/api/agent-settings/runtime-save',{id,[key]:value});const item=(S.chats||[]).find(chat=>chat.id===id);if(item)Object.assign(item,result.chat);if(live(state)&&state.chatId===id)notice('已保存本会话配置，下次发送时生效。',state);}
    catch(err){error('会话配置保存失败：'+err.message,state);}finally{state.optionSaving=false;if(live(state))paintChatOptions(state);}
  }
  function paintRefs(state){if(!live(state))return;document.getElementById('pwRefs').innerHTML=(draft(state).refs||[]).map((r,i)=>`<span class="pw-ref" title="${h(r.relative)}"><button type="button" data-pw-ref="${i}" aria-label="查看引用 ${h(r.name||r.relative)}">${h(r.name||r.relative)}${r.quote?' · 选区':''}</button><button type="button" data-pw-remove-ref="${i}" aria-label="移除引用 ${h(r.name||r.relative)}">×</button></span>`).join('');const host=document.getElementById('pwRefs');host.insertAdjacentHTML('beforeend',(draft(state).images||[]).map((item,index)=>`<span class="pw-ref">${h(item.name)}<button type="button" data-remove-image="${index}" aria-label="移除图片">×</button></span>`).join(''));host.querySelectorAll('[data-remove-image]').forEach(button=>button.onclick=()=>{draft(state).images.splice(Number(button.dataset.removeImage),1);persist(state);paintRefs(state);});}
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
  function append(box,event){appendOne(box,event,event.sender?{name:event.sender}:undefined);}
  async function paintChat(state){
    if(!live(state))return;
    paintChatView(state,false);
    const box=document.getElementById("pwMessages"),id=state.chatId;
    if(!id){box.innerHTML=`<div class="pw-empty"><h3>从一个研究问题开始</h3><p>讨论目标、引用资料，或把具体工作交给本项目的专家与团队。</p><div class="pw-empty-actions"><button type="button" class="btn" data-pw-empty="files">查看项目文件</button><button type="button" class="btn" data-pw-empty="papers">导入项目文献</button><button type="button" class="btn" data-pw-empty="bindings">加入项目专家</button></div></div>`;box.querySelectorAll("[data-pw-empty]").forEach(b=>b.onclick=()=>b.dataset.pwEmpty==="bindings"?document.getElementById("pwBindings").click():showResource(b.dataset.pwEmpty,state));return;}
    const record=chatRecord(id),seq=++record.request;
    if(record.loaded&&box.dataset.chatId!==id)drawMessages(state,id);else if(!record.loaded&&box.dataset.chatId!==id){box.dataset.chatId=id;box.textContent="正在读取对话…";}
    try{const result=await api("/api/chat?id="+encodeURIComponent(id));if(seq!==record.request)return;const changed=!result.revision||record.revision!==result.revision;record.messages=result.messages||[];record.execution=result.execution;record.revision=result.revision;record.loaded=true;if(live(state)&&state.chatId===id){if(state.executionTargetChat!==id){state.executionTargetChat=id;if(record.execution){const execution=record.execution;state.scope='project';state.target=execution.kind==='task'?(execution.member_id?'member:'+execution.executor_binding_id+':'+execution.member_id:'binding:'+execution.executor_binding_id):'';document.getElementById('pwScope').value='project';paintTargets(state);paintChatOptions(state);persist(state);}}if(changed||box.dataset.chatId!==id)drawMessages(state,id);clearTimeout(state.executionRefresh);if(record.execution)state.executionRefresh=setTimeout(()=>{if(live(state)&&state.chatId===id&&state.surface==='chat'&&!record.run)paintChat(state);},2000);}}catch(err){if(live(state)&&state.chatId===id)error(err.message,state);}
  }
  function drawMessages(state,id){
    if(!live(state)||state.chatId!==id)return;
    const record=chatRecord(id),box=document.getElementById("pwMessages");box.dataset.chatId=id;if(record.execution){const refresh=()=>{record.revision="";return paintChat(state);};ExecutionConversation.render(state.chatView==="trajectory"?document.getElementById("pwTrajectory"):box,{execution:record.execution,messages:record.messages},refresh);if(record.run?.status==="running")record.events.forEach(ev=>append(box,ev));return;}box.innerHTML="";
    (record.run?.status==="running"?record.base||record.messages:record.messages).forEach(m=>append(box,{...m,type:m.role,replay:true,at:m.created_at}));
    if(record.run?.status==="running")record.events.forEach(ev=>append(box,ev));
    if(!box.children.length)box.innerHTML='<div class="pw-empty">这个对话已绑定当前项目，可以引用资料并提出目标。</div>';
    box.scrollTop=box.scrollHeight;
  }
  async function ensureChat(state){
    if(currentChat(state))return state.chatId;
    const saved=draft(state),made=await api("/api/chat/create",{title:(saved.text||"课题讨论").slice(0,45),task_id:state.id});
    state.chatId=made.chat.id;state.drafts[state.chatId]={...saved,refs:[...(saved.refs||[])]};delete state.drafts.new;persist(state);
    await refreshState(false);if(live(state))paintSessions(state);return state.chatId;
  }
  async function send(state,asTask=false){
    if(!live(state)||state.sending||state.creating||state.optionSaving)return;
    const running=state.chatId&&chatRecord(state.chatId).run;
    if(running?.status==="running"){await api("/api/agent/cancel",{idempotency_key:running.key});return;}
    draft(state).text=document.getElementById("pwAsk").value;
    const submission={text:draft(state).text,refs:[...(draft(state).refs||[])],images:[...(draft(state).images||[])],template:PromptUI.ref(promptKey(state),state.id)},goal=submission.text.trim();if(!goal)return;
    state.sending=true;document.getElementById("pwSend").disabled=true;error("",state);
    try{
      if(!await flush())return;
      if(state.target&&(asTask||state.scope==='workflow')){await prepareTask(state,goal,submission);return;}
      const selectedModel=currentChat(state)?.model_id,model=(S.models||[]).find(item=>item.id===selectedModel)||S.active;
      const localModel=model && (()=>{try{return ['127.0.0.1','localhost','[::1]'].includes(new URL(model.base_url).hostname);}catch{return false;}})();
      if((!localModel&&!selectedModel&&!await requireModelKey())||!live(state))return;
      const id=await ensureChat(state),record=chatRecord(id),refs=submission.refs;
      const context=refs.length?"\n\n【当前项目明确引用的材料】\n"+refs.map(r=>r.quote?`文件：${r.relative}\n原文选区：\n${r.quote}`:`文件：${r.relative}`).join("\n\n"):"";
      const selected=state.target?targetRows(state).find(row=>row.value===state.target):null;if(state.target&&!selected)throw new Error('执行者已移除，请重新选择');
      const run={key:crypto.randomUUID(),api:"/api/agent",runId:"",cursor:0,status:"running",projectId:state.id,chatId:id,draftText:submission.text,draftImages:submission.images,request:{task_id:id,executor_binding_id:selected?.binding?.id||'',member_id:selected?.member?.id||'',image_ids:submission.images.map(item=>item.asset_id),message:goal+context,template_ref:submission.template,idempotency_key:""}};run.request.idempotency_key=run.key;
      record.lastError="";record.run=run;run.base=[...record.messages];record.base=run.base;record.events=[{type:"user",content:goal+context}];
      if(draft(state).text===submission.text){draft(state).text="";draft(state).images=[];}persist(state);paintDraft(state);drawMessages(state,id);persistRuns();follow(run,record);
    }catch(err){error(err.message,state);}finally{state.sending=false;if(live(state)){document.getElementById("pwSend").disabled=false;paintSend(state);}}
  }
  function persistRuns(){write("rw-workspace-runs",[...chats.values()].filter(c=>c.run?.status==="running").map(c=>c.run));}
  async function follow(run,record){
    await observeAgentRun(run,event=>{
      if(event.type==='user')return;
      record.events.push(event);const state=get(run.projectId);
      if(live(state)&&state.chatId===run.chatId){const box=document.getElementById("pwMessages");if(["done","cancelled"].includes(event.type)){if(event.type==="done"&&!record.events.some(e=>e.type==="assistant"))append(box,{type:"assistant",content:event.content||"执行结束"});}else append(box,event);}
      if(event.type==="error")record.lastError=event.message;paintBackground();
    },persistRuns,()=>{const state=get(run.projectId);if(live(state)&&state.chatId===run.chatId)notice("对话连接暂时中断，正在恢复；后台执行仍归属于当前对话。",state);});
    const owner=get(run.projectId),failed=['error','failed','interrupted'].includes(run.status);
    if(failed){const saved=owner.drafts[run.chatId]||(owner.drafts[run.chatId]={text:'',refs:[]});if(!saved.text)saved.text=run.draftText||run.request.message;if(!saved.images?.length)saved.images=run.draftImages||[];persist(owner);}
    record.run=null;record.events=[];record.loaded=false;persistRuns();
    try{await refreshState(false);const state=get(run.projectId);if(live(state)){paintSessions(state);paintSend(state);if(state.chatId===run.chatId){if(failed)paintDraft(state);await paintChat(state);if(record.lastError)error(record.lastError,state);}refresh(state);}}catch{}
    paintBackground();
  }
  function restoreRuns(){
    for(const saved of read("rw-workspace-runs",[])){
      if(!saved.projectId||!(S.tasks||[]).some(p=>p.id===saved.projectId)||!(S.chats||[]).some(c=>c.id===saved.chatId&&c.task_id===saved.projectId))continue;
      const record=chatRecord(saved.chatId);if(record.run)continue;saved.status="running";saved.cursor=0;record.run=saved;record.base=saved.base||[];record.events=[{type:'user',content:saved.request.message}];follow(saved,record);
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
    if(state.refreshing)return;state.refreshing=true;const epoch=state.epoch;
    try{
      const result=await Promise.allSettled([api('/api/project-tasks?project_id='+encodeURIComponent(state.id)),api('/api/project-tasks/executors?project_id='+encodeURIComponent(state.id)),state.scope==='workflow'&&state.targets?.layout_id?api('/api/handoff/targets?project_id='+encodeURIComponent(state.id)):Promise.resolve(state.targets),api('/api/project-tasks/policy?project_id='+encodeURIComponent(state.id)),api('/api/execution-board?project_id='+encodeURIComponent(state.id))]);
      if(epoch!==state.epoch)return;
      if(result[0].status==='fulfilled')state.tasks=result[0].value.tasks||[];
      if(result[4].status==='fulfilled')state.executionRows=result[4].value.items||[];
      if(result[1].status==='fulfilled'){state.bindings=result[1].value.bindings||[];state.teams=result[1].value.teams||[];}
      if(result[2].status==='fulfilled')state.targets=result[2].value;
      if(result[3].status==='fulfilled'&&!state.modeTouched){state.mode=result[3].value.policy.autonomy_mode||'supervised';if(live(state))document.getElementById('pwMode').value=state.mode;}
      if(live(state)){paintTargets(state);paintTasks(state);paintBackground();if(!state.optionSaving&&!document.activeElement?.closest('#pwRuntime'))paintChatOptions(state);if(result[0].status==='rejected')error('任务读取失败：'+result[0].reason.message,state);
        const updated=state.tasks.find(t=>t.id===state.review?.id);
        if(updated&&(updated.version!==state.review.version||updated.status!==state.review.status)){
          if(state.surface==='tasks'&&state.section==='task-detail')await openTask(updated.id,state.id);
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
    let task;
    if(selected.node){
      const result=await api('/api/workflow/execution/prepare-node',{project_id:state.id,task_id:state.id,node_id:selected.node.id,layout_id:state.targets.layout_id,revision:state.targets.revision,
        ...selected.actor,goal:taskGoal,inputs,template_ref:submission.template,request_key:requestKey});task=result.task;
    }else task=(await api('/api/project-tasks/create',{project_id:state.id,title:goal.slice(0,90),goal:taskGoal,inputs,template_ref:submission.template,executor_binding_id:selected.binding.id,member_id:selected.member?.id||'',review_required:true,request_key:requestKey})).task;
    task=(await api('/api/project-tasks/mode',{id:task.id,project_id:state.id,autonomy_mode:state.mode})).task;
    if(draft(state).text===submission.text){draft(state).text='';draft(state).images=[];}persist(state);if(live(state)){paintDraft(state);state.tasks=[task,...state.tasks.filter(t=>t.id!==task.id)];paintTasks(state);notice('任务已准备，尚未调用模型。请在任务对话中核对目标、材料和专家后开始。',state);if(state.surface==='chat'&&!draft(state).text.trim()){state.sending=false;await openConversation(task.chat_id,state.id);}}
  }
  async function taskCommand(state,id,action){
    const task=state.tasks.find(t=>t.id===id);if(!task)return;
    if(action==='start'&&task.latest_run?.checkpoint?.phase==='tool'){ProjectTasks.openTask(id,state.id);return;}
    try{const result=await api('/api/project-tasks/'+action,{id,project_id:state.id,...(action==='start'?{autonomy_mode:state.mode,request_key:crypto.randomUUID()}:{})});state.tasks=state.tasks.map(t=>t.id===id?result.task:t);if(live(state)){error('',state);paintTasks(state);if(state.surface==='tasks'&&state.section==='task-detail'&&state.review?.id===id)await openTask(id,state.id);}}catch(err){error(err.message,state);}
  }
  async function showResource(section,state=mounted,options={}){
    if(section==='tools')section='workflow';
    if(!live(state))return;if(section==='discussion')return switchSurface('chat',state);
    if(!options.initial&&!await flush())return;if(!live(state))return;notice('',state);error('',state);
    state.section=resources[section]?section:'overview';state.surface=options.peek?'chat':options.surface||resourceSurface[state.section];state.peek=!!options.peek;state.peekTree=false;state.navOpen=false;state.reading=false;state.inspection=options.inspection||null;
    if(state.surface===resourceSurface[state.section])state.sections[state.surface]=state.section;
    if(state.surface==='library'&&state.section==='files')state.fileRow=state.libraryFile;
    state.resourceEpoch++;persist(state);document.getElementById('pwProjectSettings').open=false;
    document.getElementById('pwResourceTitle').textContent=resources[state.section];
    const tree=document.getElementById('pwFileTree');if(tree?.parentElement!==document.getElementById('pwSidebarTreeSlot'))document.getElementById('pwSidebarTreeSlot').append(tree);
    document.getElementById('pwFileToolbar').replaceChildren();
    const area=document.getElementById('pwResource');area.innerHTML='<div class="pw-resource-content"></div>';const host=area.firstElementChild;
    if(state.section!=='files')tree.innerHTML='';layoutPanels(state);
    if(state.section==='files'){await mountFiles(state,host);return;}
    if(['catalog','manuscripts','experiments','outputs'].includes(state.section)){const epoch=state.resourceEpoch;await ProjectResources.mount(host,state.id,{kind:{manuscripts:'manuscript',experiments:'experiment',outputs:'output'}[state.section]||'',live:()=>live(state)&&state.resourceEpoch===epoch&&host.isConnected});return;}
    if(['tasks','automation','plan'].includes(state.section)){state.taskCenterHost=host;await TaskCenter.render(host,{projectId:state.id,view:state.section==='automation'?'automation':state.section==='plan'?'plans':'board',filter:state.filter,live:()=>live(state)&&state.surface==='tasks',onSnapshot:rows=>{state.executionRows=rows;paintExecutionCounts(state);}});return;}
    if(state.section==='workflow'){await mountWorkflow(state,host);return;}
    if(state.section==='search'){await mountSearch(state,host);return;}
    const seq=state.resourceEpoch;host.innerHTML='<p class="pw-loading">正在读取项目资料…</p>';
    try{const data=await api('/api/task?id='+encodeURIComponent(state.id));if(!live(state)||seq!==state.resourceEpoch||!host.isConnected)return;
      state.project=data;spId=state.id;spTab=state.section;host.innerHTML=`<div id="spBody">${spBodyHTML(data)}</div>`;spBind(data);
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
    if(live(state)){for(const rel of [...flExpanded])if(matches(rel)){flExpanded.delete(rel);if(newRelative)flExpanded.add(newRelative+rel.slice(oldRelative.length));}flSaveExpanded();}
    if(newRelative)Object.values(state.drafts).forEach(value=>(value.refs||[]).forEach(ref=>{if(matches(ref.relative)){ref.relative=newRelative+ref.relative.slice(oldRelative.length);ref.name=ref.relative.split('/').pop();}}));
    persist(state);if(!live(state))return;
    if(changed){flCurrentFile=null;flSel=state.fileRow?.rel||'';}
    if(state.section==='files'&&(state.surface!=='chat'||state.peek))await showResource('files',state,{peek:state.peek,surface:state.surface,inspection:state.inspection});
    paintRefs(state);
  }
  async function mountFiles(state,host){
    const previous=typeof flCurrentFile!=='undefined'&&flCurrentFile?.projectId===state.id&&state.fileRow?.path===flCurrentFile.row.path&&!state.fileRow.read_only?flCurrentFile:null;
    host.innerHTML='<div id="pwInlineTree" class="pw-inline-tree"></div>'+filesPanelHTML(true);const tree=host.querySelector('.fl-grid>.fl-col');document.getElementById('pwFileTree').replaceChildren(tree);
    document.getElementById('pwFileToolbar').replaceChildren(host.querySelector('.fl-grid>.fl-col:last-child>.fl-h'));
    document.getElementById('flHand').textContent='交由专家/团队处理';document.getElementById('flHand').title='指派给当前项目专家、专家或多智能体团队执行';document.getElementById('flHandoffHistory').textContent='任务记录';
    flBind();layoutPanels(state);
    const list=document.getElementById('flList');
    list.onclick=async e=>{const el=e.target.closest('.fl-tree-row');if(!el||!live(state))return;const rel=el.dataset.rel,row=flFindRow(rel);if(el.dataset.dir==='1'){if(flExpanded.has(rel))flExpanded.delete(rel);else flExpanded.add(rel);flSaveExpanded();flRenderTree();if(flExpanded.has(rel)&&!flTreeCache[rel]){const pending=flLoadNode(rel);flRenderTree();await pending;if(live(state)&&list.isConnected)flRenderTree();}}else if(row)await openFile(row,state.id);};
    if(previous){flCurrentFile=previous;flSel=previous.row.rel;document.getElementById('flName').textContent=previous.row.name;document.getElementById('pwResourceTitle').textContent=previous.row.name;document.getElementById('flModeTabs').style.display='inline-flex';document.getElementById('flSaveBtn').style.display='';document.getElementById('flHand').disabled=false;document.getElementById('flHandoffHistory').disabled=false;flUpdateModeTabs();flRenderContent();layoutPanels(state);}
    else if(state.fileRow)await displayFile(state,state.fileRow);
    document.getElementById('flReload').onclick=async()=>{if(await flush()&&live(state))await flInitTree();};
    document.getElementById('flOpen').onclick=async()=>{if(!state.fileRow)return;try{if(await flush())await api('/api/conv/open',{path:state.fileRow.path});}catch(err){error(err.message,state);}};
  }
  function relative(path,base){const normal=v=>String(v||'').replaceAll('\\','/').replace(/\/+$/,'');const p=normal(path),b=normal(base);return p.toLowerCase().startsWith(b.toLowerCase()+'/')?p.slice(b.length+1):'';}
  async function openFile(row,projectId=curTask,options={}){
    if(projectId!==mounted?.id){await openProject(projectId);}
    const state=get(projectId);if(!live(state)||!await flush())return;
    const project=(S.tasks||[]).find(p=>p.id===projectId)||state.project?.task||{};
    const rel=(row.read_only?relative(row.path,project.base_dir):'')||row.rel||row.relative||relative(row.path,project.base_dir);
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
  function currentFile(){if(!active()||mounted.surface==='chat'&&!mounted.peek)return null;if(mounted.section==='files'&&mounted.fileRow)return {project_id:mounted.id,relative:mounted.fileRow.rel};if(mounted.section==='notes'&&typeof mdProj!=='undefined'&&mdProj?.path)return {project_id:mounted.id,relative:mdProj.path};return null;}
  async function addReference(state,quotation=''){
    if(!live(state)||!await flush())return;const file=currentFile();if(!file)return notice('先打开项目文件、PDF 或笔记，再引用到当前对话。',state);
    const ref={relative:file.relative,name:file.relative.split('/').pop(),quote:quotation,read_only:state.section==='files'&&!!state.fileRow?.read_only};if(!draft(state).refs.some(r=>r.relative===ref.relative&&r.quote===ref.quote))draft(state).refs.push(ref);persist(state);paintRefs(state);await switchSurface('chat',state);document.getElementById('pwAsk').focus();
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
  async function openPaper(row){const state=mounted;if(!live(state))return;const data=await api('/api/task?id='+encodeURIComponent(state.id));if(!live(state))return;state.project=data;const paper=(data.paper_list||[]).find(p=>p.id===row.paper_id||p.id===row.id);if(!paper)return error('没有找到项目 PDF 副本，请先导入或下载到当前项目。',state);await openFile({path:paper.source_path||paper.path,name:paper.filename||paper.title||row.title,ext:'pdf',read_only:true},state.id);}
  async function openProject(id){if(!S.tasks.some(p=>p.id===id))await refreshState(false);if(!S.tasks.some(p=>p.id===id))throw new Error('项目不存在');if(active())await switchProject(id);else{curTask=id;localStorage.setItem('rw-task',id);await openTab('workspace');}return get(id);}
  async function openTask(id,projectId=curTask,options={}){
    if(!active()||mounted.id!==projectId)await openProject(projectId);
    const state=get(projectId);if(!live(state)||!await flush())return;
    state.reviewOrigins||={};if(options.chatId)state.reviewOrigins[id]=options.chatId;state.reviewChatId=state.reviewOrigins[id]||'';
    const seq=++state.resourceEpoch;let task;
    try{task=(await api('/api/project-tasks/detail?id='+encodeURIComponent(id)+'&project_id='+encodeURIComponent(projectId))).task;}catch(err){return error(err.message,state);}
    if(!live(state)||seq!==state.resourceEpoch)return;
    state.tasks=[task,...state.tasks.filter(t=>t.id!==task.id)];state.review=task;state.section='task-detail';state.surface='tasks';state.peek=false;state.navOpen=false;state.reading=false;state.inspection=null;persist(state);document.getElementById('pwFileToolbar').replaceChildren();document.getElementById('pwResourceTitle').textContent=task.title||'任务与产物';
    const run=task.latest_run||{},decision=(task.deliveries||[]).at(-1),outputs=task.outputs||[];
    const tree=document.getElementById('pwFileTree');if(tree)document.getElementById('pwSidebarTreeSlot').append(tree);layoutPanels(state);
    document.getElementById('pwResource').innerHTML=`<article class="pw-task-detail"><div class="pw-view-head"><span class="pw-state">${h(labels[task.status]||task.status)}</span><span class="hint">${run.id?'第 '+run.attempt+' 次运行':'尚未执行'}</span></div><h3>${h(task.title)}</h3><p>${h(task.goal)}</p><p class="hint">执行者：${h(run.executor_snapshot?.member?.name||run.executor_snapshot?.assistant?.name||stateBindingName(task))}${task.workflow_run_id?' · 当前工作流节点':''}</p>${run.error?`<p class="pw-error">${h(run.error)}</p>`:''}<div class="pw-view-head">${['ready','failed','blocked','cancelled'].includes(task.status)?`<button class="btn btn-primary" data-pw-start="${h(task.id)}">${run.checkpoint?.phase==='tool'?'检查中断结果':run.id?'重试任务':'开始任务'}</button>`:''}${['queued','running','blocked'].includes(task.status)?`<button class="btn ghost" data-pw-cancel="${h(task.id)}">停止任务</button>`:''}<button class="btn ghost" id="pwTaskAdvanced">完整运行记录</button></div><h4>执行结果</h4><div class="md">${md(run.checkpoint?.reply||'等待执行结果。')}</div><h4>产物版本</h4>${run.checkpoint?.output_revision?`<p class="hint">人工修订 v${run.checkpoint.output_revision} · 核查以当前产物为准</p>`:""}${outputs.map((o,i)=>`<div class="pw-output"><b>${h(o.name||o.relative||'任务产物')}</b><p class="hint">${h((o.content_hash||o.sha256||'').slice(0,16))} · 本次运行固定版本</p><button class="btn" data-pw-output="${i}">打开这份产物</button>${ProjectOutputReview.canEdit(task,o)?`<button class="btn ghost" data-pw-output-edit="${i}">人工修订</button>`:""}</div>`).join('')||'<p class="hint">尚未生成产物。</p>'}${decision?`<p class="hint">${h(decision.source==='human'?'人工核查':decision.source==='policy'?'策略验收':'历史交付')} · ${h(decision.feedback||'')} · ${h(decision.created_at||'')}</p>`:''}<details><summary>任务输入与执行范围</summary><pre>${h(JSON.stringify({inputs:run.input_snapshot||task.inputs,scope:task.scope},null,2))}</pre></details></article>`;
    document.getElementById('pwTaskAdvanced').onclick=()=>ProjectTasks.openTask(id,state.id);
    if(task.chat_id){const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='打开任务对话';button.onclick=()=>openConversation(task.chat_id,state.id);document.getElementById('pwTaskAdvanced').parentElement.prepend(button);}
    const actions=document.getElementById('pwTaskAdvanced').parentElement;
    if(task.workflow_run_id){
      const button=document.createElement('button');button.className='btn ghost';button.textContent='定位工作流节点';button.onclick=async()=>{state.focusWorkflow={runId:task.workflow_run_id,nodeId:task.workflow_node_id};await showResource('workflow',state);};actions.append(button);
    }
    if(task.metadata?.stage==='map'&&task.status==='awaiting_review'){
      const button=document.createElement('button');button.className='btn btn-primary';button.dataset.pwGraphEdit='';button.textContent='编辑节点证据链';button.onclick=()=>WorkflowReview.editGraph(state.id,task.id,()=>openTask(task.id,state.id));actions.append(button);
    }
    if(state.reviewChatId){
      const button=document.createElement('button');button.className='btn ghost';button.textContent='返回发起对话';button.onclick=()=>openConversation(state.reviewChatId,state.id);actions.append(button);
    }
    document.querySelectorAll('[data-pw-output]').forEach(b=>b.onclick=()=>openFile({...outputs[Number(b.dataset.pwOutput)],path:outputs[Number(b.dataset.pwOutput)].path,read_only:true},state.id,{surface:'tasks',inspection:{origin:'task',taskId:task.id}}));
    document.querySelectorAll('[data-pw-output-edit]').forEach(button=>button.onclick=()=>ProjectOutputReview.open(state.id,task.id,Number(button.dataset.pwOutputEdit),()=>openTask(task.id,state.id)));
    paintReview(state,task);
  }
  function paintReview(state,task){
    const host=document.getElementById('pwReview');if(!live(state)||!host)return;
    const matches=state.surface==='tasks'&&(state.section==='task-detail'||(state.inspection?.origin==='task'&&state.inspection.taskId===task.id&&state.section==='files'&&(task.outputs||[]).some(o=>o.path===state.fileRow?.path)));
    host.hidden=task.status!=='awaiting_review'||!matches;if(host.hidden)return;
    const feedback=state.feedback?.[task.id]||'';
    host.innerHTML=`<div class="pw-context-bar">核查对象：${h(task.title)} · 第 ${task.latest_run.attempt} 次产物</div><label>核查意见<textarea class="control" id="pwFeedback" placeholder="记录依据；退回时请填写修改要求">${h(feedback)}</textarea></label><div class="pw-view-head"><button type="button" class="btn btn-primary" id="pwApprove">通过交付</button><button type="button" class="btn ghost" id="pwReject">退回修改</button></div>`;
    document.getElementById('pwFeedback').oninput=e=>{state.feedback||={};state.feedback[task.id]=e.target.value;};
    const review=async approved=>{const text=document.getElementById('pwFeedback').value;if(!approved&&!text.trim())return error('退回任务前请填写修改要求。',state);host.querySelectorAll('button').forEach(b=>b.disabled=true);try{const result=await api('/api/project-tasks/review',{id:task.id,project_id:state.id,reviewed_run_id:task.latest_run.id,reviewed_output_version:task.latest_run.checkpoint?.output_revision||0,approved,feedback:text,request_key:crypto.randomUUID()});state.review=result.task;host.hidden=true;await refresh(state);if(live(state))notice(approved?'已记录人工核查并交付当前版本。':'已退回，重试任务将带入修改意见。',state);}catch(err){error(err.message,state);host.querySelectorAll('button').forEach(b=>b.disabled=false);}};
    document.getElementById('pwApprove').onclick=()=>review(true);document.getElementById('pwReject').onclick=()=>review(false);
    const retry=document.createElement('button');retry.type='button';retry.className='btn ghost';retry.id='pwRejectRetry';retry.textContent='退回并重做';
    retry.onclick=async()=>{const feedback=document.getElementById('pwFeedback').value;if(!feedback.trim())return error('请填写重做要求。',state);host.querySelectorAll('button').forEach(button=>button.disabled=true);try{await api('/api/project-tasks/review',{id:task.id,project_id:state.id,reviewed_run_id:task.latest_run.id,reviewed_output_version:task.latest_run.checkpoint?.output_revision||0,approved:false,retry:true,feedback,request_key:crypto.randomUUID()});await openTask(task.id,state.id);}catch(err){error(err.message,state);host.querySelectorAll('button').forEach(button=>button.disabled=false);}};
    document.getElementById('pwReject').after(retry);
  }
  async function mountWorkflow(state,host){
    await ProjectWorkflowGraph.mount(host,{
      projectId:state.id,live:()=>live(state)&&state.section==='workflow',
      focus:state.focusWorkflow,
      saved:()=>refresh(state),reload:()=>showResource('workflow',state),openTask:id=>openTask(id,state.id),
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
    state.focusWorkflow=null;
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!e.isComposing&&active()&&!document.querySelector('dialog[open],.modal:not(.hidden)')){const run=mounted.chatId&&chatRecord(mounted.chatId).run;if(run?.status==='running'){e.preventDefault();api('/api/agent/cancel',{idempotency_key:run.key}).catch(err=>error(err.message));}}});
  window.addEventListener('beforeunload',e=>{if(active()&&hasDirty()){e.preventDefault();e.returnValue='';}});
  window.addEventListener('resize',()=>{if(active())layoutPanels(mounted);});
  const oldTabs=tabs.filter(t=>['space','workflow','notes','workspace'].includes(t.route));
  if(oldTabs.length){const keep=oldTabs.find(t=>t.id===activeTab)||oldTabs[0];if(keep.route!=='workspace')requestRoute(keep.route);keep.route='workspace';keep.title='项目工作区';tabs=tabs.filter(t=>!oldTabs.includes(t)||t===keep);}
  async function applyTemplate(project,value){await openProject(project);if(!live(get(project))||!await flush())return;const state=get(project);draft(state).template=value;draft(state).text=`请按照「${value.title}」完成任务。`;persist(state);await switchSurface('chat',state);paintDraft(state);document.getElementById('pwAsk').focus();}
  async function openConversation(chatId,projectId){if(!(S.chats||[]).some(chat=>chat.id===chatId))await refreshState(false);if(!active()||mounted?.id!==projectId)await openProject(projectId);const state=get(projectId);await selectChat(state,chatId);await switchSurface('chat',state);}
  window.ProjectWorkspace={openConversation,active,hasSurface,hasDirty,flush,leave,requestRoute,render,openProject,openTask,openFile,openPaper,currentFile,showResource,createEntry,refreshFiles,applyTemplate,refresh:()=>mounted&&refresh(mounted)};
  return window.ProjectWorkspace;
})();
