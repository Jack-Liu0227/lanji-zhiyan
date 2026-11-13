/* A completed node result keeps one identity through preview, revision and review. */
const WorkflowNodeWorkbench=(()=>{
  'use strict';
  const h=value=>esc(String(value??''));
  const copy=value=>{
    if(value===null||typeof value!=='object')return value;
    if(Array.isArray(value))return value.map(item=>typeof item==='object'&&item!==null?{...item}:item);
    return JSON.parse(JSON.stringify(value));
  };
  const textOf=value=>typeof value==='string'?value:JSON.stringify(value??{},null,2);
  const statuses={awaiting_review:'等待人工核查',delivered:'已交付',blocked:'已退回',running:'执行中',queued:'排队中',succeeded:'已完成'};
  let current=null;
  let windowId;
  try{windowId=sessionStorage.getItem('rw-node-window');if(!windowId){windowId=crypto.randomUUID();sessionStorage.setItem('rw-node-window',windowId);}}catch{windowId=crypto.randomUUID();}

  function query(selection,artifactId){
    const params=new URLSearchParams({project_id:selection.project_id,id:selection.task_id||selection.id,window_id:windowId});
    if(selection.task_run_id)params.set('task_run_id',selection.task_run_id);
    if(selection.output_revision!==undefined&&selection.output_revision!==null)params.set('output_revision',selection.output_revision);
    if(artifactId)params.set('artifact_id',artifactId);
    return params.toString();
  }
  const eligible=task=>!!task?.node_result?.available;
  function availableSelection(task){return {task_id:task.id,project_id:task.project_id,task_run_id:task.node_result?.task_run_id,output_revision:task.node_result?.output_revision};}
  function dirty(){return !!current?.alive()&&current.draftDirty;}
  async function flush(){return !current?.alive()||await current.saveDraft();}
  function dispose(){current?.dispose();current=null;}

  async function mount(host,options){
    dispose();
    const state={host,options,result:null,artifact:null,preview:null,value:'',baseline:'',reason:'',feedback:'',draftVersion:0,draftDirty:false,busy:false,mode:'preview',paperView:{query:'',filter:'all',page:0},request:0,autosave:null,savePromise:null,disposed:false};
    const el=name=>host.querySelector('[data-wnw-'+name+']');
    state.alive=()=>!state.disposed&&host.isConnected&&options.live();
    state.dispose=()=>{state.disposed=true;clearTimeout(state.autosave);clearInterval(state.poll);if(state.onFsChange)document.removeEventListener('fullscreenchange',state.onFsChange);document.body.classList.remove('has-wnw-fullscreen');};
    host.__nodeDispose=state.dispose;current=state;
    const changed=()=>state.value!==state.baseline;
    const editable=()=>!!state.result?.editable&&state.artifact?.editable!==false&&state.preview?.editable!==false;
    const message=(text,error=false)=>{if(state.alive()){el(error?'error':'notice').textContent=text||'';}};
    const command=(action,payload={})=>api('/api/project-tasks/node-command',{project_id:state.result.identity.project_id,id:state.result.identity.task_id,action,identity:copy(state.result.identity),request_key:crypto.randomUUID(),...payload});

    function skeleton(){
      host.innerHTML=`<article class="wnw" aria-label="节点产物工作台">
        <header class="wnw-heading"><div><span class="pw-eyebrow">工作流产物 · 预览与编辑</span><h3 data-wnw-title>正在读取节点产物…</h3><p class="hint" data-wnw-meta></p></div><div class="wnw-heading-actions"><button class="btn ghost" type="button" data-wnw-fullscreen title="全屏查看并编辑产物">全屏模式</button><button class="btn ghost" type="button" data-wnw-origin>返回发起对话</button><button class="btn ghost" type="button" data-wnw-locate>定位流程节点</button></div></header>
        <p class="pw-error" data-wnw-error role="alert"></p><p class="wnw-notice" data-wnw-notice role="status"></p>
        <div class="wnw-versionbar"><button class="btn btn-primary" type="button" data-wnw-collect hidden>固定已保存产物，进入编辑核查</button><button class="btn ghost" type="button" data-wnw-stop hidden>停止当前节点</button><button class="btn" type="button" data-wnw-start hidden>继续执行本节点</button><label>查看版本<select class="control" data-wnw-version aria-label="节点产物版本"></select></label><span class="wnw-status" data-wnw-status></span><button class="pw-text-button" type="button" data-wnw-latest hidden>有更新，查看最新版本</button></div>
        <div class="wnw-grid">
          <aside class="wnw-context" aria-label="工作流与阶段产物">
            <section><h4>工作流阶段</h4><nav class="wnw-steps" data-wnw-steps aria-label="工作流阶段"></nav></section>
            <section><h4>本阶段产物</h4><nav class="wnw-artifacts" data-wnw-files aria-label="节点产物列表"></nav></section>
            <section class="wnw-requirements"><h4>节点要求</h4><p data-wnw-goal></p><details><summary>输入与前置交付</summary><div data-wnw-inputs></div></details><details><summary>核查清单</summary><div data-wnw-checks></div></details></section>
          </aside>
          <section class="wnw-document" aria-label="产物预览与编辑"><header class="wnw-document-heading"><div><strong data-wnw-file-title></strong><small data-wnw-file-meta></small></div><button type="button" class="btn" data-wnw-export-edit hidden>编辑源稿并重新生成</button><div class="wnw-modes" role="group" aria-label="产物查看方式"><button type="button" data-wnw-mode="preview" aria-pressed="true">预览</button><button type="button" data-wnw-mode="edit" aria-pressed="false">编辑</button><button type="button" data-wnw-mode="diff" aria-pressed="false">版本差异</button><button type="button" class="btn ghost" data-pw-output-edit data-wnw-file-edit hidden>人工修订</button></div></header><div data-wnw-content class="wnw-content"><p class="hint">正在读取产物…</p></div><label class="wnw-reason">修订说明<input class="control" data-wnw-reason placeholder="记录这次修改的依据"></label></section>
          <aside class="wnw-agent"><h4>节点专家</h4><p data-wnw-executor></p><p class="hint">讨论保留当前核查对象；交专家修改会基于眼前版本重做本节点。</p><button class="pw-text-button" type="button" data-wnw-quote>引用当前选区</button><blockquote data-wnw-selection hidden></blockquote><label>讨论或修改要求<textarea class="control" data-wnw-instruction rows="6" placeholder="指出需要核对或修改的内容"></textarea></label><div class="wnw-agent-buttons"><button type="button" class="btn ghost" data-wnw-discuss>讨论当前内容</button><button type="button" class="btn" data-wnw-rework>交专家修改</button></div><details data-wnw-tool-fields hidden><summary>固定工具参数</summary><p class="hint">修改参数后重新执行，原工具输出保留。</p><textarea class="control wnw-code" data-wnw-tool-args rows="7" aria-label="固定工具参数"></textarea></details><details><summary>另建关联任务</summary><p class="hint">单独处理新要求，完成后再决定是否采用。</p><button type="button" class="btn ghost" data-wnw-duplicate>按要求另建任务</button></details><details><summary>原执行回复</summary><pre data-wnw-original></pre></details></aside>
        </div>
        <footer class="wnw-footer"><label>人工核查意见<textarea class="control" data-wnw-feedback rows="2" placeholder="退回时填写修改要求"></textarea></label><div class="wnw-footer-actions"><span class="hint" data-wnw-draft-state></span><button type="button" class="btn ghost" data-wnw-draft>保存草稿</button><button type="button" class="btn" data-wnw-save>保存修订</button><button type="button" class="btn ghost" data-wnw-reject>退回修改</button><button type="button" class="btn btn-primary" data-wnw-approve>通过并交付</button></div></footer>
      </article>`;
      const toggleFullscreen=()=>{
        const article=host.querySelector('.wnw')||host;
        const isFs=article.classList.contains('wnw-fullscreen')||!!document.fullscreenElement;
        if(isFs){
          article.classList.remove('wnw-fullscreen');
          document.body.classList.remove('has-wnw-fullscreen');
          if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});
          const btn=el('fullscreen');if(btn)btn.textContent='全屏模式';
        }else{
          article.classList.add('wnw-fullscreen');
          document.body.classList.add('has-wnw-fullscreen');
          if(article.requestFullscreen)article.requestFullscreen().catch(()=>{});
          const btn=el('fullscreen');if(btn)btn.textContent='退出全屏';
        }
      };
      const fsBtn=el('fullscreen');if(fsBtn)fsBtn.onclick=toggleFullscreen;
      state.onFsChange=()=>{
        const article=host.querySelector('.wnw')||host;
        const btn=el('fullscreen');
        if(!document.fullscreenElement&&article&&!article.classList.contains('wnw-fullscreen')){
          document.body.classList.remove('has-wnw-fullscreen');
          if(btn)btn.textContent='全屏模式';
        }
      };
      document.addEventListener('fullscreenchange',state.onFsChange);
      el('origin').onclick=()=>options.openConversation(state.result?.origin_chat_id||options.selection.chat_id||state.result?.chat_id);
      el('locate').onclick=()=>options.locate(state.result.identity);
      el('draft').onclick=()=>state.saveDraft();
      el('save').onclick=saveRevision;
      el('approve').onclick=()=>review(true);
      el('reject').onclick=()=>review(false);
      el('rework').onclick=rework;
      el('discuss').onclick=discuss;
      el('duplicate').onclick=duplicate;
      el('latest').onclick=()=>options.select({task_id:state.result.identity.task_id,project_id:state.result.identity.project_id});
      el('reason').oninput=()=>{state.reason=el('reason').value;scheduleDraft();};
      el('feedback').oninput=()=>{state.feedback=el('feedback').value;scheduleDraft();};
      el('instruction').oninput=scheduleDraft;
      el('quote').onclick=captureSelection;
      el('export-edit').onclick=async()=>{if(!await state.saveDraft())return;state.mode='edit';await selectArtifact(state.result.artifacts.find(item=>item.id==='export-source'));};
      el('file-edit').onclick=()=>{const artifact=state.artifact,index=artifact?.id?.startsWith('file:')?Number(artifact.id.slice(5)):-1;if(index>=0)ProjectOutputReview.open(state.result.identity.project_id,state.result.identity.task_id,index,async()=>{options.changed?.();await options.openTask(state.result.identity.task_id);});};
      host.querySelectorAll('[data-wnw-mode]').forEach(button=>button.onclick=async()=>{state.mode=button.dataset.wnwMode;await drawContent();updateButtons();});
      host.addEventListener('keydown',event=>{
        if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();event.stopPropagation();saveRevision();}
        if(event.key==='Escape'){const article=host.querySelector('.wnw')||host;if(article.classList.contains('wnw-fullscreen')||document.fullscreenElement){event.preventDefault();event.stopPropagation();toggleFullscreen();}}
        if(event.key==='F11'){event.preventDefault();event.stopPropagation();toggleFullscreen();}
      });
    }
    function updateButtons(){
      if(!state.result||!state.alive())return;
      const conflict=!!state.result.fileop_conflicts?.length,canEdit=editable()&&!conflict,canReview=state.result.editable&&state.result.task.status==='awaiting_review'&&!conflict;
      el('save').disabled=state.busy||!canEdit||!changed();
      el('approve').disabled=state.busy||!canReview||changed();
      el('reject').disabled=state.busy||!canReview;
      el('rework').disabled=state.busy||!(state.result.can_rework??state.result.editable)||changed()||conflict;
      el('draft').disabled=state.busy||!(state.result.editable||state.result.can_rework);
      el('duplicate').disabled=state.busy||state.result.provisional;
      el('discuss').disabled=state.busy||state.result.provisional;
      el('draft-state').textContent=state.result.provisional?'已保存的中间产物 · 固定后可编辑交付':state.draftDirty?'草稿尚未保存':changed()?'草稿已保存，尚未成为产物修订':'当前产物已固定';
      host.querySelectorAll('[data-wnw-mode]').forEach(button=>{button.setAttribute('aria-pressed',String(state.mode===button.dataset.wnwMode));button.disabled=state.busy||(button.dataset.wnwMode==='edit'&&!canEdit);});
    }
    function scheduleDraft(){
      if(!(state.result?.editable||state.result?.can_rework))return;
      state.draftDirty=true;clearTimeout(state.autosave);state.autosave=setTimeout(()=>state.saveDraft(),650);updateButtons();
    }
    state.saveDraft=async()=>{
      clearTimeout(state.autosave);
      if(!state.alive()||!(state.result?.editable||state.result?.can_rework)||!state.draftDirty)return true;
      if(state.savePromise){await state.savePromise;if(!state.draftDirty)return true;}
      const saved={text:state.value,reason:state.reason,feedback:state.feedback,instruction:el('instruction').value};
      const artifactId=state.artifact?.id||'structured';
      state.savePromise=(async()=>{
        try{
          const response=await command('draft',{artifact_id:artifactId,window_id:windowId,expected_draft_version:state.draftVersion,...saved});
          if(!state.alive())return true;
          state.draftVersion=response.draft?.version??response.draft?.draft_version??state.draftVersion+1;
          if(response.draft){state.result.drafts=[{...response.draft,artifact_id:artifactId,window_id:windowId,base_revision:state.result.identity.output_revision},...(state.result.drafts||[]).filter(item=>!(item.artifact_id===artifactId&&item.window_id===windowId&&(item.base_revision??item.revision??item.output_revision)===state.result.identity.output_revision))];}
          state.draftDirty=JSON.stringify(saved)!==JSON.stringify({text:state.value,reason:state.reason,feedback:state.feedback,instruction:el('instruction').value});
          updateButtons();if(state.draftDirty)scheduleDraft();return true;
        }catch(error){message('草稿保存失败，当前编辑仍保留。'+error.message,true);return false;}
      })();
      try{return await state.savePromise;}finally{state.savePromise=null;}
    };
    async function load(selection,keepArtifact=true){
      const seq=++state.request;
      const response=await api('/api/project-tasks/node-workspace?'+query(selection));
      if(!state.alive()||seq!==state.request)return;
      if(keepArtifact&&state.result?.provisional&&response.result.provisional&&state.result.identity.output_digest===response.result.identity.output_digest&&state.result.task.status===response.result.task.status&&state.result.identity.task_run_id===response.result.identity.task_run_id)return;
      const previousArtifact=state.artifact,previousRun=state.result?.identity?.task_run_id;
      state.result=response.result;
      state.selection={...selection,...state.result.identity,task_id:state.result.identity.task_id};
      options.selected(state.selection);
      const result=state.result,task=result.task,node=result.node||{},identity=result.identity;
      el('title').textContent=node.label||task.title||'节点产物';
      el('meta').textContent='第 '+(result.run.attempt||1)+' 次执行 · 修订 v'+identity.output_revision+' · '+(task.workflow_node_id||identity.workflow_node_id)+(result.scheduling?' · '+result.scheduling:'');
      el('status').textContent=statuses[task.status]||task.status;
      el('steps').innerHTML=(result.steps||[]).map((step,index)=>`<button type="button" data-wnw-step="${h(step.task_id)}" aria-current="${step.task_id===task.id?'step':'false'}"><small>${index+1}</small><span>${h(step.title)}<em>${h(statuses[step.status]||step.status)}</em></span></button>`).join('');
      el('steps').querySelectorAll('[data-wnw-step]').forEach(button=>button.onclick=async()=>{if(await state.saveDraft())options.select({project_id:task.project_id,task_id:button.dataset.wnwStep});});
      el('collect').hidden=!result.can_collect;
      el('stop').hidden=!['running','queued'].includes(task.status);
      el('start').hidden=!result.provisional||!['ready','draft','failed','interrupted','cancelled','blocked'].includes(task.status);
      const lifecycle=async(action,payload={})=>{if(state.busy)return;state.busy=true;updateButtons();try{await api('/api/project-tasks/'+action,{project_id:task.project_id,id:task.id,...payload});await load({project_id:task.project_id,task_id:task.id},false);options.changed?.();}catch(error){message(error.message,true);}finally{state.busy=false;updateButtons();}};
      el('collect').onclick=()=>lifecycle('collect-output',{task_run_id:identity.task_run_id,expected_version:identity.task_version,request_key:crypto.randomUUID()});
      el('stop').onclick=()=>lifecycle('cancel');
      el('start').onclick=()=>lifecycle('start',{request_key:crypto.randomUUID()});
      if(result.provisional)message(result.artifacts.length?'这里展示本节点已实际保存的产物。执行结束后可以固定版本、编辑内容并交付下一阶段。':'本节点尚未保存产物。开始执行后，已保存的内容会显示在这里。');
      else if(el('notice').textContent.startsWith('这里展示')||el('notice').textContent.startsWith('本节点尚未'))message('');
      el('version').disabled=!!result.provisional;
      host.querySelector('.wnw-reason').hidden=!!result.provisional;
      host.querySelector('.wnw-footer').hidden=!!result.provisional;
      host.querySelector('.wnw-agent').hidden=!!result.provisional;
      host.querySelector('.wnw').classList.toggle('wnw-provisional',!!result.provisional);
      let sync=host.querySelector('[data-wnw-sync]');if(!sync){sync=document.createElement('section');sync.dataset.wnwSync='';sync.className='wnw-notice';el('notice').after(sync);}
      const syncErrors=result.sync_errors||[],fileConflicts=result.fileop_conflicts||[];sync.hidden=!syncErrors.length&&!fileConflicts.length;
      if(!sync.hidden){sync.innerHTML=`<p>${fileConflicts.length?'文件写入恢复存在冲突，请核对工作文件；产物和草稿仍保留。':'交付决定已保存，部分页面的状态同步尚未完成。'}</p>${syncErrors.map(error=>`<p>${h(error.adapter||'状态同步')}：${h(error.error||error.message||'同步失败')}</p>`).join('')}<button class="btn ghost" type="button" data-wnw-reconcile>重新同步状态</button>`;sync.querySelector('button').onclick=async()=>{if(!await state.saveDraft())return;const response=await mutate('reconcile');if(response)await load(state.selection);};}
      el('goal').textContent=node.prompt||task.goal||'';
      el('inputs').innerHTML=inputMarkup(result);
      el('original').textContent=result.run.checkpoint?.reply||result.original_reply||'本次执行未单独记录文字回复。';
      const executor=result.run.executor_snapshot||result.executor||{};
      el('executor').textContent=executor.member?.name||executor.assistant?.name||executor.assistant_name||'本节点原执行者';
      el('tool-fields').hidden=node.node_kind!=='tool';
      el('tool-args').value=JSON.stringify(node.tool_args||{},null,2);
      const history=result.history||[];
      const entries=history.some(item=>sameVersion(item,identity))?history:[{...identity,label:'当前查看版本'},...history];
      state.history=entries;
      el('version').innerHTML=entries.map((item,index)=>`<option value="${index}" ${sameVersion(item,identity)?'selected':''}>${h(item.label||'运行 '+(item.attempt||'')+' · 修订 v'+item.output_revision)}</option>`).join('');
      el('version').onchange=()=>{const selected=entries[Number(el('version').value)];options.select({...state.selection,task_run_id:selected.task_run_id||selected.identity?.task_run_id,output_revision:selected.output_revision??selected.identity?.output_revision});};
      el('files').innerHTML=(result.artifacts||[]).map((artifact,index)=>`<button type="button" class="wnw-file" data-wnw-file="${index}"><span>${h(artifact.name||artifact.id)}</span><small>${h(artifact.kind||'文件')}</small></button>`).join('');
      el('files').querySelectorAll('[data-wnw-file]').forEach(button=>button.onclick=async()=>{if(await state.saveDraft())await selectArtifact(result.artifacts[Number(button.dataset.wnwFile)]);});
      let checks=Array.isArray(result.checks)?result.checks:Object.entries(result.checks||{}).map(([name,value])=>({label:(({format:'输出格式',min_chars:'最少字数',sections:'必要章节',required_files:'必要文件'})[name]||name)+' '+textOf(value)}));
      if(!checks.length)checks=[{label:'核对实际产物及证据来源'},{label:'核对输入、版本与修改要求'}];
      el('checks').innerHTML=checks.map(item=>`<label class="wnw-check"><input type="checkbox" ${item.passed||item.checked?'checked':''}>${h(typeof item==='string'?item:item.label||item.name||item.message)}</label>`).join('');
      const next=(result.artifacts||[]).find(item=>item.id===(keepArtifact?state.artifact?.id:selection.artifact_id))||(result.artifacts||[])[0];
      if(!next){state.artifact=null;state.preview=null;state.value='';state.baseline='';el('file-title').textContent='本阶段产物';el('file-meta').textContent='';el('content').innerHTML='<div class="wnw-empty"><h4>本节点尚未保存产物</h4><p>生成的文献表、证据图谱、正文或文件会在此预览；核查版本可直接编辑并交付。</p></div>';updateButtons();return;}
      if(!(keepArtifact&&result.provisional&&state.preview&&previousRun===identity.task_run_id&&previousArtifact?.id===next.id&&previousArtifact?.content_hash===next.content_hash))await selectArtifact(next);
      updateButtons();
    }
    function sameVersion(item,identity){return (item.task_run_id||item.identity?.task_run_id)===identity.task_run_id&&(item.output_revision??item.identity?.output_revision)===identity.output_revision;}
    function inputMarkup(result){
      const inputs=result.run.input_snapshot||result.inputs||{};
      const dependencies=inputs.dependencies||[];
      return `<ul class="wnw-input-list">${dependencies.map((item,index)=>`<li><b>${h(item.title||item.label||'前置交付 '+(index+1))}</b><small>已验收的固定版本${item.outputs?.length?' · '+item.outputs.length+' 个文件':''}</small></li>`).join('')}${(inputs.assets||[]).map((item,index)=>`<li>${h(item.name||item.relative||'输入资料 '+(index+1))}<small>${h((item.content_hash||'').slice(0,12))}</small></li>`).join('')}</ul>${!dependencies.length&&!inputs.assets?.length?'<p class="hint">本节点未单独附加资料，依据已固定的任务要求执行。</p>':''}`;
    }
    async function selectArtifact(artifact){
      const seq=++state.previewRequest||1;state.previewRequest=seq;
      if(state.artifact?.id!==artifact.id)state.paperView={query:'',filter:'all',page:0};
      state.artifact=artifact;
      const response=await api('/api/project-tasks/node-workspace-preview?'+query(state.selection,artifact.id));
      if(!state.alive()||state.previewRequest!==seq)return;
      state.preview=response.preview;
      state.rawDraft=false;
      state.structuredData=copy(state.preview.data??null);
      state.baseline=state.preview.text!==undefined?state.preview.text:(typeof state.preview.data==='string'?state.preview.data:'');
      state.value=state.baseline;state.reason='';state.draftVersion=0;state.draftDirty=false;
      const saved=(state.result.drafts||[]).find(item=>item.artifact_id===artifact.id&&item.window_id===windowId&&(item.base_revision??item.output_revision??state.result.identity.output_revision)===state.result.identity.output_revision);
      if(saved&&(state.result.editable||state.result.can_rework)){state.value=saved.text??state.value;state.reason=saved.reason||'';state.feedback=saved.feedback||state.feedback;state.draftVersion=saved.version??saved.draft_version??0;if(saved.instruction)el('instruction').value=saved.instruction;message('已恢复此窗口在当前版本上的编辑草稿。');}
      el('reason').value=state.reason;el('feedback').value=state.feedback;
      el('file-title').textContent=artifact.name||artifact.id;
      el('export-edit').hidden=artifact.id==='export-source'||!state.result.artifacts.some(item=>item.id==='export-source'&&item.editable);
      const legacyEdit=el('file-edit');
      if(legacyEdit){legacyEdit.hidden=!String(artifact.id||'').startsWith('file:');legacyEdit.disabled=!editable();}
      el('file-meta').textContent=(artifact.kind||state.preview.kind||'文件')+' · '+((artifact.content_hash||artifact.hash||'').slice(0,16));
      let previewNote=host.querySelector('[data-wnw-preview-note]');if(!previewNote){previewNote=document.createElement('p');previewNote.dataset.wnwPreviewNote='';previewNote.className='hint wnw-preview-note';el('content').before(previewNote);}previewNote.hidden=!state.preview.preview_note;previewNote.textContent=state.preview.preview_note||'';
      el('files').querySelectorAll('[data-wnw-file]').forEach(button=>button.setAttribute('aria-current',String(state.result.artifacts[Number(button.dataset.wnwFile)].id===artifact.id)));
      if(state.mode==='edit'&&!editable())state.mode='preview';
      await drawContent();updateButtons();
      drawStaleDrafts();
      options.selected({...state.selection,artifact_id:artifact.id});
    }
    function drawStaleDrafts(){
      let panel=host.querySelector('[data-wnw-stale-drafts]');
      if(!panel){panel=document.createElement('section');panel.dataset.wnwStaleDrafts='';panel.className='wnw-stale-drafts';el('content').before(panel);}
      const identity=state.result.identity;
      const drafts=(state.result.drafts||[]).filter(item=>item.artifact_id===state.artifact.id&&item.window_id===windowId&&item.text!==state.baseline&&(item.stale||(item.base_revision??item.revision??item.output_revision)!==identity.output_revision));
      panel.hidden=!drafts.length;if(!drafts.length)return;
      panel.innerHTML=`<p>保留了 ${drafts.length} 份基于旧版本的编辑草稿。查看或复制草稿后，可与当前版本对照；当前产物不会自动改变。</p>${drafts.map((draft,index)=>`<details data-wnw-stale-draft="${index}"><summary>查看修订 v${Number(draft.base_revision??draft.revision??draft.output_revision)} 上的草稿${draft.updated_at?' · '+h(fmtDate(draft.updated_at)):''}</summary><pre>${h(draft.text||'空文本草稿')}</pre>${draft.reason?`<p>${h(draft.reason)}</p>`:''}<button class="btn ghost" type="button" data-wnw-restore-draft="${index}" ${editable()?'':'disabled'}>复制到当前编辑区</button></details>`).join('')}`;
      panel.querySelectorAll('[data-wnw-restore-draft]').forEach(button=>button.onclick=async()=>{
        if(!editable()||!await state.saveDraft())return;
        const draft=drafts[Number(button.dataset.wnwRestoreDraft)];
        state.value=draft.text??'';state.reason=draft.reason||'';state.feedback=draft.feedback||state.feedback;
        if(draft.instruction)el('instruction').value=draft.instruction;
        if(state.artifact.kind==='table'&&/\.csv$/i.test(state.artifact.name||''))state.rawDraft=true;
        el('reason').value=state.reason;el('feedback').value=state.feedback;state.mode='edit';
        scheduleDraft();await drawContent();updateButtons();message('旧草稿已复制到当前编辑区。请对照检查并保存修订，当前产物版本保持不变。');
      });
    }
    function change(value){state.value=value;scheduleDraft();}
    function parsed(){
      if(state.structuredData!==null&&typeof state.structuredData==='object')return state.structuredData;
      try{return JSON.parse(state.value);}catch{return state.structuredData??state.preview.data??state.preview.content;}
    }
    function changeData(data){
      state.structuredData=data;
      if(state.artifact.id.startsWith('file:')&&/\.csv$/i.test(state.artifact.name||'')&&Array.isArray(data)){
        const keys=[...new Set(data.flatMap(item=>Object.keys(item)))];
        const cell=value=>{const text=String(value??'');return /[",\r\n]/.test(text)?'"'+text.replaceAll('"','""')+'"':text;};
        change([keys,...data.map(item=>keys.map(key=>item[key]))].map(row=>row.map(cell).join(',')).join('\n'));
      }else change(textOf(data));
    }
    function kind(){return state.artifact.kind||state.preview.kind||'text';}
    async function drawContent(){
      const target=el('content');if(!state.preview)return;
      host.querySelectorAll('[data-wnw-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.wnwMode===state.mode)));
      if(state.mode==='diff'){await drawDiff(target);return;}
      const type=kind(),edit=state.mode==='edit'&&editable();
      if(edit&&state.rawDraft){target.innerHTML=`<textarea class="wnw-editor" data-wnw-editor aria-label="产物修订正文" spellcheck="false">${h(state.value)}</textarea>`;target.querySelector('textarea').oninput=event=>change(event.target.value);return;}
      if(type==='export'){drawExport(target,parsed(),edit);return;}
      if(type==='papers'&&Array.isArray(parsed())&&!edit){drawPapers(target,parsed());return;}
      if(type==='table'&&Array.isArray(parsed())&&isLiteratureRows(parsed())){drawSearchResults(target,parsed(),edit);return;}
      if(type==='graph'){drawGraph(target,parsed(),edit);return;}
      if(type==='idea'&&typeof parsed()==='object'){drawIdea(target,parsed(),edit);return;}
      if(type==='bilingual'&&!Array.isArray(parsed())&&parsed()&&('source' in parsed()||'source_text' in parsed())&&!parsed().segments){drawBilingual(target,parsed(),edit);return;}
      if(['table','papers','bilingual','checklist'].includes(type)&&typeof parsed()==='object'){drawStructured(target,parsed(),edit,type==='papers'?'table':type);return;}
      if(edit){
        target.innerHTML=`<textarea class="wnw-editor" data-wnw-editor aria-label="产物修订正文" spellcheck="false">${h(state.value)}</textarea>`;
        target.querySelector('textarea').oninput=event=>change(event.target.value);return;
      }
      const media=state.preview.preview||state.preview;
      if(['pdf','image','html'].includes(type)||['pdf','image','html'].includes(media.kind)){
        const raw=media.url||media.preview_url||state.preview.url;
        if(raw){const url=new URL(raw,location.href);if(url.origin!==location.origin)throw new Error('产物预览地址不属于本机服务。');target.innerHTML=(media.kind||type)==='image'?`<img class="wnw-image" src="${h(url.href)}" alt="${h(state.artifact.name)}">`:`<iframe class="wnw-frame" title="${h(state.artifact.name)}" ${(media.kind||type)==='html'?'sandbox="allow-same-origin"':''} src="${h(url.href)}"></iframe>`;return;}
      }
      if(['markdown','text','checklist'].includes(type)&&typeof parsed()!=='object'||state.preview.text!==undefined){target.innerHTML=/\.(md|markdown)$/i.test(state.artifact.name||'')||type==='markdown'||type==='checklist'?`<div class="md-render wnw-prose">${renderMarkdown(state.value)}</div>`:`<pre class="wnw-text">${h(state.value)}</pre>`;return;}
      target.innerHTML=`<pre class="wnw-text">${h(state.value||'此产物需使用对应文件查看器打开。')}</pre>`;
    }
    function drawExport(target,data,edit){
      if(!edit){target.innerHTML=`<div class="md-render wnw-prose">${renderMarkdown(data.markdown||'')}</div>`;return;}
      target.innerHTML=`<label>生成格式<select class="control" data-wnw-export-format>${[['docx','Word'],['pdf','PDF'],['html','HTML'],['md','Markdown']].map(([value,label])=>`<option value="${value}" ${data.format===value?'selected':''}>${label}</option>`).join('')}</select></label><p class="hint">保存修订会根据源稿生成新的成品文件，旧版保留在版本记录中。</p><textarea class="wnw-editor" data-wnw-export-source aria-label="排版源稿">${h(data.markdown||'')}</textarea>`;
      target.querySelector('[data-wnw-export-source]').oninput=event=>{data.markdown=event.target.value;changeData(data);};
      target.querySelector('[data-wnw-export-format]').onchange=event=>{data.format=event.target.value;changeData(data);};
    }
    function drawPapers(target,rows){
      const files=state.result.artifacts.filter(item=>item.kind==='pdf');
      target.innerHTML=`<div class="wnw-papers">${rows.map((row,index)=>{const file=files.find(item=>item.name===row.filename||item.name===row.title||item.name.includes(row.id));return `<article><span class="wnw-paper-number">${index+1}</span><div><h4>${h(row.title||row.filename||'论文')}</h4><p>${h(row.annotation||row.reason||'核查论文内容及来源证据')}</p>${file?`<button class="btn" type="button" data-wnw-paper-open="${h(file.id)}">打开论文预览</button>`:''}</div></article>`;}).join('')}</div>`;
      target.querySelectorAll('[data-wnw-paper-open]').forEach(button=>button.onclick=()=>selectArtifact(files.find(item=>item.id===button.dataset.wnwPaperOpen)));
    }
    function isLiteratureRows(rows){return rows.some(row=>row&&typeof row==='object'&&('title'in row||'filename'in row)&&('abstract'in row||'doi'in row||'authors'in row||'year'in row||'source'in row));}
    function drawSearchResults(target,rows,edit){
      const pageSize=20,view=state.paperView;
      target.innerHTML=`<div class="wnw-search-tools"><label>检索文献<input class="control" type="search" data-wnw-paper-search placeholder="搜索标题、作者、摘要、DOI" value="${h(view.query)}"></label><label>纳入状态<select class="control" data-wnw-paper-filter><option value="all" ${view.filter==='all'?'selected':''}>全部记录</option><option value="included" ${view.filter==='included'?'selected':''}>仅纳入</option><option value="excluded" ${view.filter==='excluded'?'selected':''}>仅排除</option></select></label><span class="wnw-search-count" data-wnw-search-count></span></div><div class="wnw-search-results" data-wnw-search-results></div><nav class="wnw-pagination" data-wnw-pagination aria-label="文献结果分页"></nav>`;
      const list=target.querySelector('[data-wnw-search-results]'),count=target.querySelector('[data-wnw-search-count]'),pagination=target.querySelector('[data-wnw-pagination]');
      const paint=()=>{
        const query=view.query.trim().toLocaleLowerCase();
        const matches=rows.map((row,index)=>({row,index})).filter(({row})=>{
          if(view.filter==='included'&&row.included===false)return false;
          if(view.filter==='excluded'&&row.included!==false)return false;
          if(!query)return true;
          return (row.title&&String(row.title).toLocaleLowerCase().includes(query))||
                 (row.authors&&String(row.authors).toLocaleLowerCase().includes(query))||
                 (row.abstract&&String(row.abstract).toLocaleLowerCase().includes(query))||
                 (row.doi&&String(row.doi).toLocaleLowerCase().includes(query))||
                 (row.source&&String(row.source).toLocaleLowerCase().includes(query))||
                 (row.filename&&String(row.filename).toLocaleLowerCase().includes(query));
        });
        const pages=Math.max(1,Math.ceil(matches.length/pageSize));view.page=Math.min(view.page,pages-1);
        const visible=matches.slice(view.page*pageSize,(view.page+1)*pageSize),included=rows.filter(row=>row.included!==false).length;
        count.textContent=`${matches.length} 条匹配 · 共 ${rows.length} 条 · 纳入 ${included} 条`;
        list.innerHTML=visible.length?visible.map(({row,index})=>{
          const meta=[row.authors||row.author,row.year,row.source||row.journal,row.doi?'DOI '+row.doi:''].filter(Boolean).map(value=>`<span>${h(value)}</span>`).join('');
          const abstract=row.abstract||row.summary||'',details=abstract?`<details class="wnw-paper-abstract"><summary>摘要</summary><p>${h(abstract)}</p></details>`:'';
          const controls=edit?`<div class="wnw-paper-edit"><label><input type="checkbox" data-wnw-cell="${index}" data-field="included" ${row.included!==false?'checked':''}> 纳入本阶段交付</label><label>文献标题<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="title">${h(row.title||row.filename||'')}</textarea></label><label>筛选理由<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="reason">${h(row.reason||'')}</textarea></label><label>人工批注<textarea class="control" rows="2" data-wnw-cell="${index}" data-field="annotation">${h(row.annotation||'')}</textarea></label></div>`:'';
          const order=edit?`<div class="wnw-paper-order"><button type="button" class="pw-text-button" data-wnw-row-move="${index}:-1" ${index===0?'disabled':''}>上移</button><button type="button" class="pw-text-button" data-wnw-row-move="${index}:1" ${index===rows.length-1?'disabled':''}>下移</button></div>`:'';
          return `<article class="wnw-search-paper ${row.included===false?'is-excluded':''}"><header><span class="wnw-paper-number">${index+1}</span><h3>${h(row.title||row.filename||'未命名文献')}</h3>${order}</header><div class="wnw-paper-meta">${meta}<span class="wnw-paper-state ${row.included===false?'is-excluded':''}">${row.included===false?'已排除':'已纳入'}</span></div>${details}${row.reason&&!edit?`<p class="wnw-paper-reason">${h(row.reason)}</p>`:''}${row.annotation&&!edit?`<p class="wnw-paper-annotation">${h(row.annotation)}</p>`:''}${controls}</article>`;
        }).join(''):'<p class="wnw-empty">没有匹配的文献记录</p>';
        pagination.innerHTML=`<button type="button" class="btn ghost" data-wnw-page="-1" ${view.page?'':'disabled'}>上一页</button><span>第 ${view.page+1} / ${pages} 页</span><button type="button" class="btn ghost" data-wnw-page="1" ${view.page+1<pages?'':'disabled'}>下一页</button>`;
        pagination.querySelectorAll('[data-wnw-page]').forEach(button=>button.onclick=()=>{view.page+=Number(button.dataset.wnwPage);paint();});
        list.querySelectorAll('[data-wnw-cell]').forEach(input=>{const update=()=>{const row=rows[Number(input.dataset.wnwCell)];row[input.dataset.field]=input.type==='checkbox'?input.checked:input.value;changeData(rows);updateButtons();};input.oninput=update;input.onchange=update;});
        list.querySelectorAll('[data-wnw-row-move]').forEach(button=>button.onclick=()=>{const [index,direction]=button.dataset.wnwRowMove.split(':').map(Number),next=index+direction;if(next<0||next>=rows.length)return;[rows[index],rows[next]]=[rows[next],rows[index]];changeData(rows);view.page=0;paint();updateButtons();});
      };
      let searchTimer=null;
      target.querySelector('[data-wnw-paper-search]').oninput=event=>{
        clearTimeout(searchTimer);
        searchTimer=setTimeout(()=>{
          view.query=event.target.value;view.page=0;paint();
        },100);
      };
      target.querySelector('[data-wnw-paper-filter]').onchange=event=>{view.filter=event.target.value;view.page=0;paint();};
      paint();
    }
    function drawIdea(target,data,edit){
      const report=typeof data.report==='object'&&data.report?data.report:{markdown:typeof data.report==='string'?data.report:''};
      target.innerHTML=`<h4>研究问题</h4><div class="wnw-prose">${h(data.input||'')}</div><h4>创新点审查结论</h4>${edit?'<textarea class="wnw-editor" data-wnw-idea-report aria-label="创新点审查结论"></textarea>':`<div class="md-render wnw-prose">${renderMarkdown(report.markdown||'')}</div>`}`;
      const editor=target.querySelector('textarea');if(editor){editor.value=report.markdown||'';editor.oninput=()=>{report.markdown=editor.value;data.report=report;change(textOf(data));};}
    }
    function drawBilingual(target,data,edit){
      const sourceKey='source' in data?'source':'source_text',targetKey='result' in data?'result':'target_text' in data?'target_text':'translation';
      target.innerHTML=`<div class="wnw-bilingual"><section><h4>原文</h4><div class="md-render wnw-prose">${renderMarkdown(data[sourceKey]||'')}</div></section><section><h4>译文</h4>${edit?'<textarea class="wnw-editor" data-wnw-translation aria-label="译文修订"></textarea>':`<div class="md-render wnw-prose">${renderMarkdown(data[targetKey]||'')}</div>`}</section></div>`;
      const editor=target.querySelector('textarea');if(editor){editor.value=data[targetKey]||'';editor.oninput=()=>{data[targetKey]=editor.value;change(textOf(data));};}
    }
    function drawStructured(target,data,edit,type){
      const key=Array.isArray(data)?null:['rows','items','results','segments','paragraphs','issues'].find(name=>Array.isArray(data?.[name]));
      const rows=key?data[key]:Array.isArray(data)?data:[];
      if(!rows.length){target.innerHTML=`<pre class="wnw-text">${h(textOf(data))}</pre>${edit?'<p class="hint">使用结构化内容编辑器修改字段。</p><textarea class="wnw-editor" data-wnw-editor aria-label="结构化产物内容"></textarea>':''}`;if(edit){target.querySelector('textarea').value=state.value;target.querySelector('textarea').oninput=event=>change(event.target.value);}return;}
      if(type==='bilingual'&&rows.some(row=>Array.isArray(row.segments))){drawSegments(target,data,rows,edit);return;}
      const present=[...new Set(rows.flatMap(row=>typeof row==='object'&&row?Object.keys(row).filter(field=>!['embedding','raw','html'].includes(field)):['content']))];
      const preferred=type==='table'?['title','authors','doi','year','abstract','included','reason','annotation']:type==='checklist'?['title','detail','text','severity','resolved','comment']:['source','translation','target','annotation'];
      const columns=[...new Set([...preferred.filter(field=>present.includes(field)||['included','reason','annotation','resolved','comment','translation'].includes(field)),...present])].slice(0,10);
      const labels={title:'标题',authors:'作者',doi:'DOI',year:'年份',abstract:'摘要',annotation:'核查批注',source:type==='table'?'来源':'原文',source_text:'原文',target:'译文',translation:'译文',target_text:'译文',text:'内容',content:'内容',included:'纳入',reason:'理由',severity:'严重程度',resolved:'已处理',page:'页码',comment:'核查意见'};
      function cellMarkup(row,index,field){
        const value=typeof row==='object'&&row?row[field]:row;
        const boolean=typeof value==='boolean'||['included','resolved'].includes(field),canEdit=edit&&!['id','source','created_at','task_id','sha256','files','source_path'].includes(field);
        if(!canEdit)return `<td>${h(boolean?(value===false?'否':'是'):typeof value==='object'?textOf(value):value??'')}</td>`;
        return `<td>${boolean?`<input type="checkbox" data-wnw-cell="${index}" data-field="${h(field)}" aria-label="第 ${index+1} 行 ${h(labels[field]||field)}" ${value!==false&&field==='included'||value===true?'checked':''}>`:`<textarea class="control" rows="${type==='bilingual'?5:2}" data-wnw-cell="${index}" data-field="${h(field)}" aria-label="第 ${index+1} 行 ${h(labels[field]||field)}">${h(typeof value==='object'?textOf(value):value??'')}</textarea>`}</td>`;
      }
      function orderMarkup(index){return `<td class="wnw-order"><span>${index+1}</span>${edit?`<button type="button" class="pw-text-button" data-wnw-row-move="${index}:-1" aria-label="第 ${index+1} 行上移" ${index===0?'disabled':''}>上移</button><button type="button" class="pw-text-button" data-wnw-row-move="${index}:1" aria-label="第 ${index+1} 行下移" ${index===rows.length-1?'disabled':''}>下移</button>`:''}</td>`;}
      target.innerHTML=`<div class="wnw-table-wrap"><table class="wnw-table"><thead><tr><th scope="col">顺序</th>${columns.map(field=>`<th scope="col">${h(labels[field]||field)}</th>`).join('')}</tr></thead><tbody>${rows.map((row,index)=>`<tr>${orderMarkup(index)}${columns.map(field=>cellMarkup(row,index,field)).join('')}</tr>`).join('')}</tbody></table></div>`;
      target.querySelectorAll('[data-wnw-row-move]').forEach(button=>button.onclick=()=>{
        const [index,direction]=button.dataset.wnwRowMove.split(':').map(Number),next=index+direction;if(next<0||next>=rows.length)return;
        [rows[index],rows[next]]=[rows[next],rows[index]];changeData(data);drawStructured(target,data,edit,type);
        target.querySelector('[data-wnw-row-move="'+next+':'+direction+'"]')?.focus();
      });
      target.querySelectorAll('[data-wnw-cell]').forEach(input=>input.oninput=()=>{
        const index=Number(input.dataset.wnwCell),field=input.dataset.field;
        if(typeof rows[index]!=='object'||rows[index]===null)rows[index]={content:rows[index]};
        const prior=rows[index][field];let value=input.type==='checkbox'?input.checked:typeof prior==='number'&&input.value.trim()!==''?Number(input.value):input.value;
        if(prior&&typeof prior==='object'){try{value=JSON.parse(input.value);}catch{}}
        rows[index][field]=value;
        changeData(data);
      });
    }
    function drawSegments(target,data,rows,edit){
      target.innerHTML=rows.map((row,rowIndex)=>`<section class="wnw-segment-group"><h4>${h(row.title||row.id||'双语对照')}</h4>${(row.segments||[]).map((segment,index)=>`<div class="wnw-bilingual"><section><small>原文 · ${index+1}</small><p>${h(segment.source||'')}</p></section><section><label>译文 · ${index+1}${edit?`<textarea class="control" rows="5" data-wnw-segment="${rowIndex}:${index}" aria-label="第 ${rowIndex+1} 篇第 ${index+1} 段译文">${h(segment.target??segment.translation??'')}</textarea>`:`<p>${h(segment.target??segment.translation??'')}</p>`}</label></section></div>`).join('')}</section>`).join('');
      target.querySelectorAll('[data-wnw-segment]').forEach(input=>input.oninput=()=>{const [row,index]=input.dataset.wnwSegment.split(':').map(Number),segment=rows[row].segments[index];segment['target' in segment?'target':'translation']=input.value;changeData(data);});
    }
    function drawGraph(target,graph,edit){
      if(!graph?.nodes||!graph.edges){target.innerHTML=`<pre class="wnw-text">${h(state.value)}</pre>`;return;}
      let selected=graph.nodes[0]?.id;
      const positions=new Map(graph.nodes.map((node,index)=>[node.id,{x:20+index%3*220,y:20+Math.floor(index/3)*95}]));
      const draw=()=>{
        const node=graph.nodes.find(item=>item.id===selected);
        target.innerHTML=`<div class="wnw-graph"><svg viewBox="0 0 680 ${Math.max(110,Math.ceil(graph.nodes.length/3)*95+20)}" role="group" aria-label="当前版本证据链">${graph.edges.map(edge=>{const a=positions.get(edge.from),b=positions.get(edge.to);return a&&b?`<g><line x1="${a.x+90}" y1="${a.y+30}" x2="${b.x+90}" y2="${b.y+30}" stroke="var(--muted)"/><text x="${(a.x+b.x)/2+90}" y="${(a.y+b.y)/2+20}" fill="var(--fg)" font-size="10">${h(edge.label)}</text></g>`:'';}).join('')}${graph.nodes.map(item=>{const p=positions.get(item.id);return `<g transform="translate(${p.x} ${p.y})" tabindex="0" role="button" data-wnw-graph-node="${h(item.id)}" aria-label="${h(item.label)}"><rect width="185" height="64" rx="8" fill="var(--panel)" stroke="${item.id===selected?'var(--accent)':'var(--line)'}" stroke-width="2"/><text x="10" y="34" fill="var(--fg)" font-size="12">${h(item.label?.slice(0,18)||item.id)}</text></g>`;}).join('')}</svg></div>${node?`<div class="wnw-graph-fields"><label>类型<select class="control" data-wnw-graph-field="type" ${edit?'':'disabled'}>${['section','paper','note','data'].map(type=>`<option ${node.type===type?'selected':''}>${type}</option>`).join('')}</select></label>${[['label','标题'],['ref','来源'],['detail','证据说明']].map(([field,label])=>`<label>${label}<textarea class="control" data-wnw-graph-field="${field}" ${edit?'':'readonly'}>${h(node[field]||'')}</textarea></label>`).join('')}</div>`:''}${edit?'<div class="wnw-agent-buttons"><button class="btn ghost" type="button" data-wnw-add-node>添加证据</button><button class="btn ghost" type="button" data-wnw-remove-node>删除所选证据</button></div>':''}<h4>证据连线</h4><div data-wnw-edges>${graph.edges.map((edge,index)=>`<div class="wnw-edge"><span>${h(graph.nodes.find(item=>item.id===edge.from)?.label)} → ${h(graph.nodes.find(item=>item.id===edge.to)?.label)} · ${h(edge.label)}</span>${edit?`<button class="pw-text-button" type="button" data-wnw-remove-edge="${index}">删除</button>`:''}</div>`).join('')}</div>${edit?`<div class="wnw-edge-new"><select class="control" data-wnw-from aria-label="证据连线起点">${graph.nodes.map(item=>`<option value="${h(item.id)}">${h(item.label)}</option>`).join('')}</select><select class="control" data-wnw-to aria-label="证据连线终点">${graph.nodes.map(item=>`<option value="${h(item.id)}">${h(item.label)}</option>`).join('')}</select><input class="control" data-wnw-edge-label aria-label="证据连线说明" placeholder="支持 / 引用 / 反驳"><button class="btn ghost" type="button" data-wnw-add-edge>添加连线</button></div>`:''}`;
        target.querySelectorAll('[data-wnw-graph-node]').forEach(button=>{const choose=()=>{selected=button.dataset.wnwGraphNode;draw();};button.onclick=choose;button.onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();choose();}};});
        target.querySelectorAll('[data-wnw-graph-field]').forEach(input=>input.oninput=()=>{node[input.dataset.wnwGraphField]=input.value;change(textOf(graph));});
        target.querySelector('[data-wnw-add-node]')?.addEventListener('click',()=>{const item={id:'human-'+crypto.randomUUID().slice(0,8),type:'note',label:'新证据',ref:'',detail:''};positions.set(item.id,{x:20+graph.nodes.length%3*220,y:20+Math.floor(graph.nodes.length/3)*95});graph.nodes.push(item);selected=item.id;change(textOf(graph));draw();});
        target.querySelector('[data-wnw-remove-node]')?.addEventListener('click',()=>{if(graph.nodes.length<2)return;graph.nodes=graph.nodes.filter(item=>item.id!==selected);graph.edges=graph.edges.filter(edge=>edge.from!==selected&&edge.to!==selected);selected=graph.nodes[0].id;change(textOf(graph));draw();});
        target.querySelectorAll('[data-wnw-remove-edge]').forEach(button=>button.onclick=()=>{graph.edges.splice(Number(button.dataset.wnwRemoveEdge),1);change(textOf(graph));draw();});
        target.querySelector('[data-wnw-add-edge]')?.addEventListener('click',()=>{graph.edges.push({from:target.querySelector('[data-wnw-from]').value,to:target.querySelector('[data-wnw-to]').value,label:target.querySelector('[data-wnw-edge-label]').value});change(textOf(graph));draw();});
      };
      draw();
    }
    async function drawDiff(target){
      const history=state.history.filter(item=>!sameVersion(item,state.result.identity));
      target.innerHTML=`<label class="wnw-compare">对照版本<select class="control" data-wnw-compare>${history.map((item,index)=>`<option value="${index}">${h(item.label||'修订 v'+item.output_revision)}</option>`).join('')}</select></label><div data-wnw-diff-body></div>`;
      const draw=async()=>{
        let previous=state.preview.previous_content??state.artifact.previous_content??state.result.previous_content;
        if(history.length){const item=history[Number(target.querySelector('select').value)];try{const response=await api('/api/project-tasks/node-workspace-preview?'+query({...state.selection,task_run_id:item.task_run_id||item.identity?.task_run_id,output_revision:item.output_revision??item.identity?.output_revision},state.artifact.id));previous=response.preview.text??response.preview.data??response.preview.content;}catch(error){message(error.message,true);}}
        if(!state.alive()||state.mode!=='diff')return;
        const before=previous===undefined?state.baseline:textOf(previous);
        target.querySelector('[data-wnw-diff-body]').innerHTML=diffMarkup(before,state.value);
      };
      target.querySelector('select').disabled=!history.length;target.querySelector('select').onchange=draw;await draw();
    }
    function diffMarkup(before,after){
      const a=before.split('\n'),b=after.split('\n');let prefix=0,suffix=0;
      while(prefix<a.length&&prefix<b.length&&a[prefix]===b[prefix])prefix++;
      while(suffix<a.length-prefix&&suffix<b.length-prefix&&a[a.length-1-suffix]===b[b.length-1-suffix])suffix++;
      const line=(value,index,changed,before)=>`<div class="wnw-diff-line ${changed?before?'wnw-removed':'wnw-added':''}"><span>${index+1}</span><code>${h(value)||' '}</code></div>`;
      return `<div class="wnw-diff"><section><h4>对照版本</h4>${a.map((value,index)=>line(value,index,index>=prefix&&index<a.length-suffix,true)).join('')}</section><section><h4>当前内容${changed()?' · 含草稿':''}</h4>${b.map((value,index)=>line(value,index,index>=prefix&&index<b.length-suffix,false)).join('')}</section></div>`;
    }
    function captureSelection(){
      const editor=el('content').querySelector('textarea[data-wnw-editor]');
      let text='',start,end;
      if(editor){start=editor.selectionStart;end=editor.selectionEnd;text=editor.value.slice(start,end);}else{const selection=window.getSelection();if(selection&&el('content').contains(selection.anchorNode))text=selection.toString();}
      if(!text.trim()){message('先在当前产物中选择需要引用的文字。',true);return;}
      state.quote={artifact_id:state.artifact.id,text,start,end};el('selection').textContent=text;el('selection').hidden=false;message('选区已绑定当前产物版本。');
    }
    async function mutate(action,payload){
      if(state.busy)return;
      state.busy=true;message('',true);updateButtons();
      const editableFields=[...host.querySelectorAll('input,textarea,select')];editableFields.forEach(input=>{input.dataset.wnwWasDisabled=String(input.disabled);input.disabled=true;});
      try{return await command(action,payload);}
      catch(error){message(error.message+' 当前内容已保留。',true);return null;}
      finally{state.busy=false;if(state.alive()){editableFields.forEach(input=>input.disabled=input.dataset.wnwWasDisabled==='true');updateButtons();}}
    }
    async function saveRevision(){
      if(!editable()||!changed()||state.busy)return;
      if(!await state.saveDraft())return;
      const type=kind();let action='revise',payload={artifact_id:state.artifact.id,text:state.value,reason:state.reason};
      if(state.artifact.id==='export-source'){action='revise-export';payload={...payload,content:parsed()};}
      if(state.artifact.id==='structured'){
        let content=state.value;
        if(['graph','table','papers','idea','json'].includes(type)||state.preview.data!==null&&typeof state.preview.data==='object'){
          try{content=JSON.parse(state.value);}catch{message('结构化内容须为有效 JSON，请保留草稿并修正后保存。',true);return;}
        }
        action=type==='graph'?'revise-graph':'revise-structured';payload={...payload,...(type==='graph'?{graph:content}:{content})};
      }
      const response=await mutate(action,payload);
      if(response){state.draftDirty=false;await load({task_id:state.result.identity.task_id,project_id:state.result.identity.project_id},true);message('已保存为新的产物修订版本，请核查后交付。');}
    }
    async function review(approved){
      if(approved&&changed()){message('还有未保存的产物修改，请先保存修订。',true);return;}
      if(!approved&&!state.feedback.trim()){message('退回前请填写人工核查意见。',true);return;}
      if(!await state.saveDraft())return;
      const response=await mutate('review',{approved,feedback:state.feedback});
      if(response){await load(state.selection);message(approved?'已人工验收并交付此版本，工作流继续执行。':'已退回当前结果，修改要求已记录。');options.changed?.();}
    }
    async function rework(){
      const instruction=el('instruction').value.trim();if(!instruction){message('请填写需要专家修改的内容。',true);return;}
      if(changed()){message('请先保存产物修订，再基于该版本交专家修改。',true);return;}
      if(!await state.saveDraft())return;
      const payload={instruction,selection:state.quote||null};
      if(state.result.node?.node_kind==='tool'){try{payload.tool_args=JSON.parse(el('tool-args').value||'{}');}catch{message('固定工具参数须为有效 JSON 对象。',true);return;}}
      const response=await mutate('rework',payload);
      if(response){state.draftDirty=false;options.changed?.();await options.openTask(state.result.identity.task_id);}
    }
    async function discuss(){
      const text=el('instruction').value.trim();if(!text){message('请填写需要讨论的问题。',true);return;}
      if(!await state.saveDraft())return;
      const response=await mutate('discuss',{message:text,selection:state.quote||null});
      if(response){message('问题及当前版本已发送到节点专家对话。');if(response.chat_id)await options.openConversation(response.chat_id);}
    }
    async function duplicate(){
      const goal=el('instruction').value.trim();if(!goal){message('请填写新任务要求。',true);return;}
      if(!await state.saveDraft())return;
      const response=await mutate('duplicate',{goal});if(response?.task)await options.openTask(response.task.id);
    }
    skeleton();
    const grid=host.querySelector('.wnw-grid');
    const contextPane=grid?.querySelector('.wnw-context');
    const documentPane=grid?.querySelector('.wnw-document');
    const agentPane=grid?.querySelector('.wnw-agent');
    if(grid&&documentPane&&contextPane&&agentPane){
      grid.replaceChildren(contextPane,documentPane,agentPane);
    }
    try{await load(options.selection,false);}catch(error){if(state.alive()){el('content').innerHTML='<p class="hint">无法读取本节点产物，请刷新后重试。</p>';message(error.message,true);host.querySelectorAll('button,input,select,textarea').forEach(input=>input.disabled=true);el('origin').disabled=false;}}
    state.poll=setInterval(async()=>{
      if(!state.alive()||!state.result||document.hidden||state.pollBusy||state.busy)return;
      if(state.result.provisional){state.pollBusy=true;try{await load({project_id:state.result.identity.project_id,task_id:state.result.identity.task_id});}catch(error){message(error.message,true);}finally{state.pollBusy=false;}return;}
      try{const data=await api('/api/project-tasks/detail?project_id='+encodeURIComponent(state.result.identity.project_id)+'&id='+encodeURIComponent(state.result.identity.task_id));if(state.alive())el('latest').hidden=data.task.version===state.result.identity.task_version&&data.task.latest_run_id===state.result.identity.task_run_id&&(data.task.latest_run?.checkpoint?.output_revision||0)===state.result.identity.output_revision;}catch{}
    },4000);
  }
  return {mount,dirty,flush,dispose,eligible,availableSelection};
})();
