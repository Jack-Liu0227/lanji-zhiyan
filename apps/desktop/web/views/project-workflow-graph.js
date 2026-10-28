/* Project-owned graph editing reuses canonical tasks for execution and human review. */
const ProjectWorkflowGraph=(()=>{
  const h=value=>esc(String(value??'')),copy=value=>JSON.parse(JSON.stringify(value));
  let current=null;
  function dirty(){return !!current?.host.isConnected&&current.changed;}
  async function flush(){return !dirty()||await current.save();}
  async function mount(host,options){
    host.__graphDispose?.();
    const state={host,changed:false,selected:'',mode:'config',layout:null,data:null,bindings:[],run:null,tasks:[],save:null};current=state;
    const alive=()=>host.isConnected&&options.live();
    host.innerHTML='<p class="pw-loading">正在读取项目工作流…</p>';
    try{
      const [data,executors,runs,tasks]=await Promise.all([
        api('/api/workflow/layouts/state?task_id='+encodeURIComponent(options.projectId)),
        api('/api/project-tasks/executors?project_id='+encodeURIComponent(options.projectId)),
        api('/api/workflow/execution?'+(options.focus?.runId?'id='+encodeURIComponent(options.focus.runId):'task_id='+encodeURIComponent(options.projectId))),
        api('/api/project-tasks?project_id='+encodeURIComponent(options.projectId))]);
      if(!alive())return;Object.assign(state,{data,layout:copy(data.layout),selected:data.selected_node,bindings:executors.bindings||[],teams:executors.teams||[],run:runs.run,tasks:tasks.tasks||[]});
      if(options.focus?.nodeId&&state.run?.layout.nodes.some(node=>node.id===options.focus.nodeId)){state.mode='run';state.selected=options.focus.nodeId;}
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
        node.label=el('label').value.trim();node.step=el('step').value;node.prompt=el('prompt').value;node.review_required=el('review').checked;
        node.executor_binding_id=el('binding').value;node.member_id=el('member').value;
        node.node_kind=el('kind').value;
        if(node.node_kind==='tool'){node.tool_name=el('tool-name').value.trim();try{node.tool_args=JSON.parse(el('tool-args').value||'{}');}catch{throw new Error('固定工具参数须为有效 JSON 对象');}}
        if(node.node_kind==='workflow')node.workflow_template_id=el('template').value;
        for(const key of ['tools','skills']){const value=el(key).value.trim();node[key]=value?value.split(/[,，\n]/).map(value=>value.trim()).filter(Boolean):null;}
        const parents=[...host.querySelectorAll('[data-pwg-parent]:checked')].map(input=>input.dataset.pwgParent);
        state.layout.edges=state.layout.edges.filter(edge=>edge.target!==node.id).concat(parents.map(source=>({source,target:node.id})));
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
        return {...source,selected_node:state.selected,nodes:source.nodes.map(node=>{const taskId=state.run?.settings?.task_ids?.[node.id],task=state.tasks.find(task=>task.id===taskId);return {...node,level:level.get(node.id)||0,status:state.mode==='run'?task?.status||'pending':'pending'};})};
      }
      function drawMember(node){
        const binding=state.bindings.find(item=>item.id===el('binding').value),team=state.teams.find(team=>team.id===binding?.target_id);
        const members=ExecutorChoices.members(team?.members,node.member_id);
        el('member').innerHTML='<option value="">跟随团队负责人</option>'+members.map(member=>`<option value="${h(member.id)}">${h(member.name)}</option>`).join('');
        if(node.member_id&&!members.some(member=>member.id===node.member_id))el('member').insertAdjacentHTML('beforeend',`<option value="${h(node.member_id)}" disabled>原成员已失效，请重新选择</option>`);
        el('member').value=node.member_id||'';el('member').disabled=!team;
      }
      function inspector(){
        const graph=graphData(),node=graph.nodes.find(node=>node.id===state.selected)||graph.nodes[0];if(!node)return;state.selected=node.id;
        const taskId=state.run?.settings?.task_ids?.[node.id],task=state.tasks.find(task=>task.id===taskId);
        const parents=state.layout.edges.filter(edge=>edge.target===node.id).map(edge=>edge.source);
        el('inspector').innerHTML=`<div class="pwg-node-heading"><span class="pw-eyebrow">${state.mode==='run'?'运行快照':'节点配置'} · ${h(node.id)}</span><h3>${h(node.label)}</h3></div>${state.mode==='config'?`
          <label>节点名称<input class="control" data-pwg-label value="${h(node.label)}"></label><label>执行步骤<select class="control" data-pwg-step>${[{id:'task',name:'通用项目任务'},...state.data.stages].map(step=>`<option value="${h(step.id)}" ${step.id===node.step?'selected':''}>${h(step.name)}</option>`).join('')}</select></label>
          <label>执行者<select class="control" data-pwg-binding><option value="">跟随项目角色绑定</option>${ExecutorChoices.unique(state.bindings,node.executor_binding_id).filter(ExecutorChoices.ready).map(binding=>`<option value="${h(binding.id)}" ${binding.id===node.executor_binding_id?'selected':''}>${h(binding.kind==='team'?'团队 · ':'专家 · ')}${h(binding.display_name)}</option>`).join('')}</select></label>
          <label>团队成员<select class="control" data-pwg-member></select></label><label>任务要求<textarea class="control" data-pwg-prompt rows="5" placeholder="此节点需要完成什么，产物需要满足哪些条件？">${h(node.prompt||'')}</textarea></label>
          <label class="pwg-check"><input type="checkbox" data-pwg-review ${node.review_required!==false?'checked':''}>生成产物后需要人工审查</label>
          <fieldset class="pwg-dependencies"><legend>前置节点</legend>${state.layout.nodes.filter(item=>item.id!==node.id).map(item=>`<label><input type="checkbox" data-pwg-parent="${h(item.id)}" ${parents.includes(item.id)?'checked':''}>${h(item.label)}</label>`).join('')||'<span class="hint">暂无其他节点</span>'}</fieldset>
          <details><summary>工具与技能范围</summary><label>允许工具 ID<textarea class="control" data-pwg-tools rows="2" placeholder="留空跟随执行者配置">${h((node.tools||[]).join(', '))}</textarea></label><label>允许技能 ID<textarea class="control" data-pwg-skills rows="2" placeholder="留空跟随执行者配置">${h((node.skills||[]).join(', '))}</textarea></label></details>
          <div class="pwg-actions"><button class="btn btn-primary" data-pwg-save>保存节点</button><button class="btn ghost" data-pwg-remove ${state.layout.nodes.length<=1?'disabled':''}>删除节点</button></div>`:`<p class="hint">本次运行使用已固定的节点配置。编辑下一次运行请切换到“节点配置”。</p><h4>任务要求</h4><pre>${h(node.prompt||'跟随研究阶段要求')}</pre><p class="hint">${node.review_required!==false?'人工审查节点':'按运行策略交付'}</p>`}
          <div class="pwg-node-execution"><h4>执行与核查</h4>${task?`<p class="hint">${h(task.title)} · ${h(task.status)}</p><button type="button" class="btn" data-pwg-task="${h(task.id)}">查看产物与人工核查</button>`:'<p class="hint">此节点尚未准备任务。</p>'}<button type="button" class="btn ghost" data-pw-node="${h(node.id)}">交给此节点</button></div>`;
        if(state.mode==='config'){
          el('label').closest('label').insertAdjacentHTML('beforebegin',`<label>节点类型<select class="control" data-pwg-kind><option value="executor">专家 / 团队</option><option value="tool">固定工具</option><option value="workflow">子工作流</option></select></label><div data-pwg-tool-fields><label>工具 ID<input class="control" data-pwg-tool-name value="${h(node.tool_name||'')}"></label><label>固定参数（JSON）<textarea class="control" data-pwg-tool-args rows="4">${h(JSON.stringify(node.tool_args||{},null,2))}</textarea></label><p class="hint">使用下方执行者的工具权限，直接调用工具并保存结果。</p></div><label data-pwg-template-fields>子工作流模板<select class="control" data-pwg-template><option value="">请选择模板</option>${state.data.layouts.filter(layout=>layout.id!==state.layout.id).map(layout=>`<option value="${h(layout.id)}">${h(layout.name)}</option>`).join('')}</select><span class="hint">准备运行时固定子节点及其执行者；人工审查保留在子节点。</span></label>`);
          el('kind').value=node.node_kind||'executor';el('template').value=node.workflow_template_id||'';
          const showKind=()=>{const nested=el('kind').value==='workflow';el('tool-fields').hidden=el('kind').value!=='tool';el('template-fields').hidden=!nested;for(const key of ['binding','member','prompt'])el(key).closest('label').hidden=nested;el('tools').closest('details').hidden=nested;};showKind();el('kind').onchange=()=>{state.changed=true;showKind();if(el('kind').value==='workflow')el('step').value='task';};
          drawMember(node);el('binding').onchange=()=>{state.changed=true;node.member_id='';drawMember(node);};
          el('inspector').querySelectorAll('input,select,textarea').forEach(input=>input.addEventListener('input',()=>{state.changed=true;}));
          el('save').onclick=state.save;
          el('remove').onclick=async()=>{if(!await uiConfirm('删除节点「'+node.label+'」以及它的依赖连线？'))return;state.layout.nodes=state.layout.nodes.filter(item=>item.id!==node.id);state.layout.edges=state.layout.edges.filter(edge=>edge.source!==node.id&&edge.target!==node.id);state.selected=state.layout.nodes[0].id;state.changed=true;draw();};
        }
        host.querySelector('[data-pw-node]').onclick=async()=>{if(await state.save())options.handoff(node.id);};
        if(node.node_kind==='workflow'){
          host.querySelector('[data-pw-node]').textContent='准备 / 查看组合流程';
          host.querySelector('[data-pw-node]').onclick=async()=>{if(await state.save())await ConnectedResearch.openRun(options.projectId);};
        }else if(!state.layout.nodes.some(item=>item.id===node.id))host.querySelector('[data-pw-node]').hidden=true;
        host.querySelector('[data-pwg-task]')?.addEventListener('click',()=>options.openTask(task.id));
      }
      function drawGraph(){
        el('graph').innerHTML=WorkflowGraph.render(graphData()).replace('role="img"','role="group"');
        const svg=el('graph').querySelector('svg');if(svg)svg.style.zoom=String(state.zoom||1);
        el('graph').querySelectorAll('[data-wfl-node]').forEach(node=>{const choose=()=>{try{capture();}catch(error){message(error.message);return;}const nodeId=node.dataset.wflNode;state.selected=nodeId;drawGraph();inspector();if(state.mode==='config')state.selectionSave=(state.selectionSave||Promise.resolve()).catch(()=>{}).then(()=>api('/api/workflow/layouts/select',{task_id:options.projectId,layout_id:state.layout.id,node_id:nodeId})).catch(error=>message(error.message));};node.onclick=choose;node.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}};});
      }
      function draw(){
        host.innerHTML=`<section class="project-workflow-graph"><div class="pwg-toolbar"><label>LangGraph 工作流<select class="control" id="pwWorkflowChoice">${state.data.layouts.map(layout=>`<option value="${h(layout.id)}" ${layout.id===state.layout.id?'selected':''}>${h(layout.name)}</option>`).join('')}</select></label><span class="pw-spacer"></span><button class="btn ghost" id="pwRoles">角色绑定</button><button class="btn ghost" data-pwg-add>添加节点</button><button class="btn ghost" data-pwg-publish>发布为全局模板</button><button class="btn btn-primary" id="pwWorkflowRun">准备 / 查看运行</button></div><div class="pwg-mode"><button type="button" class="pw-text-button" data-pwg-mode="config" aria-pressed="${state.mode==='config'}">节点配置 · v${state.layout.revision||0}</button><button type="button" class="pw-text-button" data-pwg-mode="run" aria-pressed="${state.mode==='run'}" ${state.run?'':'disabled'}>本次运行</button><span class="hint">配置修改用于新运行，当前运行保留原版本。</span></div><p class="pwg-notice" data-pwg-notice role="status"></p><p class="pw-error" data-pwg-error role="alert"></p><div class="pwg-layout"><div class="pwg-canvas" data-pwg-graph></div><aside class="pwg-inspector" data-pwg-inspector aria-label="所选工作流节点"></aside></div></section>`;
        drawGraph();inspector();
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
        host.querySelector('#pwWorkflowChoice').onchange=async event=>{if(!await state.save()){event.target.value=state.layout.id;return;}await api('/api/workflow/layouts/select',{task_id:options.projectId,layout_id:event.target.value});await options.reload();};
        host.querySelector('#pwRoles').onclick=async()=>{if(!await state.save())return;const dialog=await ProjectTasks.openRoles(options.projectId,state.layout.id);dialog?.addEventListener('close',options.reload,{once:true});};
        host.querySelector('#pwWorkflowRun').onclick=async()=>{if(await state.save()){await ConnectedResearch.openRun(options.projectId);document.querySelector('dialog.connected-dialog')?.addEventListener('close',()=>{if(alive())options.reload();},{once:true});}};
        el('publish').onclick=async()=>{
          if(!await state.save())return;
          if(!await uiConfirm('将当前配置发布为所有项目可调用的模板？当前项目将跟随模板，已有运行保持原版本。'))return;
          try{await api('/api/workflow/layouts/publish',{project_id:options.projectId,layout_id:state.layout.id,expected_fingerprint:state.layout.fingerprint,expected_template_updated_at:state.layout.updated_at});await options.reload();}catch(error){message(error.message);}
        };
        el('add').onclick=()=>{capture();const id='node-'+crypto.randomUUID().slice(0,8);state.layout.nodes.push({id,step:'task',label:'新节点',prompt:'',review_required:true,executor_role_id:id});state.selected=id;state.mode='config';state.changed=true;draw();el('label')?.focus();};
        host.querySelectorAll('[data-pwg-mode]').forEach(button=>button.onclick=async()=>{if(!await state.save())return;state.mode=button.dataset.pwgMode;state.selected=(state.mode==='run'?state.run.layout:state.layout).nodes[0]?.id;draw();});
      }
      draw();
      let refreshing=false;
      const timer=setInterval(async()=>{
        if(!alive()){clearInterval(timer);return;}if(refreshing||document.hidden)return;refreshing=true;
          try{const [runs,tasks]=await Promise.all([api('/api/workflow/execution?'+(options.focus?.runId?'id='+encodeURIComponent(options.focus.runId):'task_id='+encodeURIComponent(options.projectId))),api('/api/project-tasks?project_id='+encodeURIComponent(options.projectId))]);
          if(!alive())return;const changed=JSON.stringify([state.run,state.tasks])!==JSON.stringify([runs.run,tasks.tasks||[]]);state.run=runs.run;state.tasks=tasks.tasks||[];
          if(changed&&state.mode==='run'){drawGraph();inspector();}
        }catch(error){if(alive())message('运行状态读取失败：'+error.message);}finally{refreshing=false;}
      },2500);
      host.__graphDispose=()=>clearInterval(timer);
    }catch(error){if(alive())host.innerHTML=`<p class="pw-error">工作流读取失败：${h(error.message)}</p>`;}
  }
  const graphEditor={mount,dirty,flush};window.ProjectWorkflowGraph=graphEditor;return graphEditor;
})();
