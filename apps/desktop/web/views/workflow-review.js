/* Conversation handoffs resolve live tasks; human edits stay attached to one attempt. */
const WorkflowReview=(()=>{
  const h=value=>esc(String(value??''));
  const statusLabels={ready:'待执行',draft:'已准备',queued:'排队中',running:'执行中',awaiting_review:'等待人工核查',delivered:'已交付',succeeded:'已完成',blocked:'等待处理',failed:'执行失败',interrupted:'中断待恢复',cancelled:'已停止'};
  function mount(card,target){
    if(!target?.project_id||!target.workflow_run_id||card.querySelector('[data-wr-card]'))return;
    const host=document.createElement('div');host.dataset.wrCard='';host.className='pt-box';card.append(host);
    let busy=false,timer,lastSignature='',pendingSignature='';
    async function refresh(){
      if(!card.isConnected){clearInterval(timer);return;}if(busy||document.hidden)return;busy=true;
      try{
        const [taskResponse,runResponse]=await Promise.allSettled([
          api('/api/project-tasks?project_id='+encodeURIComponent(target.project_id)+'&workflow_run_id='+encodeURIComponent(target.workflow_run_id)),
          api('/api/workflow/execution?id='+encodeURIComponent(target.workflow_run_id))
        ]);
        const data=taskResponse.status==='fulfilled'?taskResponse.value:{tasks:[]};
        const run=(runResponse.status==='fulfilled'&&runResponse.value?.run)||null;
        if(!card.isConnected)return;
        let tasks=data.tasks||[];
        if(!tasks.length&&target.workflow_run_id){
          try{
            const graph=await api('/api/langgraph/state?project_id='+encodeURIComponent(target.project_id)+'&run_id='+encodeURIComponent(target.workflow_run_id));
            const ids=(graph.checkpoints||[]).map(item=>item.task_id||item.id).filter(Boolean);
            const details=await Promise.all(ids.map(id=>api('/api/project-tasks/detail?project_id='+encodeURIComponent(target.project_id)+'&id='+encodeURIComponent(id)).then(value=>value.task).catch(()=>null)));
            tasks=details.filter(Boolean);
          }catch{}
        }
        const orderList=run?.layout?.order||(run?.layout?.nodes||[]).map(n=>n.id);
        const orderMap=new Map(orderList.map((nid,idx)=>[nid,idx]));
        tasks=[...tasks].sort((a,b)=>{
          const ia=orderMap.has(a.workflow_node_id)?orderMap.get(a.workflow_node_id):999;
          const ib=orderMap.has(b.workflow_node_id)?orderMap.get(b.workflow_node_id):999;
          if(ia!==ib)return ia-ib;
          return String(a.created_at||'').localeCompare(String(b.created_at||''));
        });
        const pending=tasks.filter(task=>task.status==='awaiting_review');
        const failed=tasks.find(task=>['failed','blocked','interrupted'].includes(task.status));
        const runStatus=String(run?.status||'');
        const taskStatuses=tasks.map(task=>task.status);
        const isWaitingReview=pending.length>0&&!failed;
        const status=isWaitingReview?'工作流等待你的审查':
          runStatus==='failed'||runStatus==='blocked'||runStatus==='interrupted'||failed?'工作流需要处理':
          ['succeeded','completed'].includes(runStatus)||(tasks.length&&tasks.every(task=>task.status==='delivered'))?'工作流已完成':
          runStatus==='cancelled'||(tasks.length&&tasks.every(task=>task.status==='cancelled'))?'工作流已停止':
          runStatus==='awaiting_review'?'工作流等待你的审查':
          runStatus==='running'||taskStatuses.some(value=>value==='running')?'工作流执行中':
          runStatus==='queued'||taskStatuses.some(value=>['queued','ready','draft'].includes(value))?'工作流排队中':'工作流任务已保存';
        const error=isWaitingReview?'':(failed?.error||run?.error||'');

        const layoutNodes = run?.layout?.nodes?.length ? run.layout.nodes : tasks.map(t=>({id:t.workflow_node_id||t.id,label:t.title}));
        const layoutEdges = run?.layout?.edges || [];
        const stepMap = (window.WorkflowLayout?.computeHierarchicalStepNumbers || window.WorkflowGraph?.computeHierarchicalStepNumbers)?.(layoutNodes, layoutEdges) || new Map();
        const getTaskStep = (task, idx) => {
          const nid = task.workflow_node_id || task.id;
          const info = stepMap.get(nid);
          if (info) return info;
          const raw = String(task.title||'').trim().replace(/^Step\s*\d+(\.\d+)?\s*[:：]\s*/i,'');
          return {
            id: nid,
            majorNo: idx + 1,
            minorNo: 1,
            isParallel: false,
            stepNum: String(idx + 1),
            stepLabel: `Step ${idx + 1}`,
            badgeLabel: `#${idx + 1}`,
            cleanTitle: raw,
            formattedTitle: `Step ${idx + 1}: ${raw}`,
            waveIndex: idx,
            waveTotal: 1,
          };
        };

        const signature=JSON.stringify({status,error,tasks:tasks.map(task=>({id:task.id,title:task.title,status:task.status,version:task.version,run_id:task.latest_run_id,result:task.node_result||null}))});
        if(signature===lastSignature){if(tasks.length&&tasks.every(task=>['delivered','cancelled'].includes(task.status)))clearInterval(timer);return;}
        if(lastSignature&&(host.matches(':hover')||host.contains(document.activeElement))){pendingSignature=signature;return;}
        pendingSignature='';
        const doneCount=tasks.filter(t=>['delivered','succeeded'].includes(t.status)).length;
        const runningCount=tasks.filter(t=>['running','queued'].includes(t.status)).length;
        const pct=tasks.length?Math.round((doneCount/tasks.length)*100):0;
        const wfName=run?.layout?.name?`${run.layout.name} · `:'';
        const stateSummary=pending.length?`${pending.length} 个待审查`:runningCount?`${runningCount} 个执行中`:doneCount===tasks.length&&tasks.length?'全部已完成':`${tasks.length-doneCount} 个待执行`;
        const noteText=tasks.length?`${wfName}${tasks.length} 个阶段 · 已完成 ${doneCount}/${tasks.length} (${pct}%) · ${stateSummary}`:'正在同步工作流节点…';

        // 按波次聚合串行与并行节点
        const waveGroups = [];
        const waveMap = new Map();
        tasks.forEach((task, idx) => {
          const info = getTaskStep(task, idx);
          const waveKey = info.waveIndex ?? info.majorNo;
          if(!waveMap.has(waveKey)){
            const group = { waveKey, majorNo: info.majorNo, items: [] };
            waveMap.set(waveKey, group);
            waveGroups.push(group);
          }
          waveMap.get(waveKey).items.push({ task, info });
        });

        const renderChip = ({ task, info }) => {
          const st = task.status || 'ready';
          const dot = st==='delivered'||st==='succeeded'?'●':st==='awaiting_review'?'🟠':st==='running'?'◐':st==='failed'||st==='blocked'?'✖':'○';
          const label = info.formattedTitle;
          const available = WorkflowNodeWorkbench.eligible(task);
          const badgeText = st==='delivered'||st==='succeeded'?'1/1':st==='awaiting_review'?'待审':st==='running'?'运行中':'0/1';
          return `<span class="wr-stage-group is-${h(st)}">`+
            `<button type="button" class="wr-stage-chip is-${h(st)}" data-wr-task="${h(task.id)}" title="${h(label)} · ${h(statusLabels[st]||st)}"><span class="wr-dot">${dot}</span><span>${h(label)}</span></button>`+
            (available?`<button type="button" class="wr-stage-badge is-${h(st)}" data-wr-result="${h(task.id)}" title="${h(label)} · 查看并编辑产物">${badgeText}</button>`:`<small class="wr-stage-badge is-${h(st)}">${badgeText}</small>`)+
            `</span>`;
        };

        const pipelineHtml = waveGroups.map((group, gIdx) => {
          const isParallel = group.items.length > 1;
          const prevGroup = gIdx > 0 ? waveGroups[gIdx - 1] : null;
          const prevParallel = prevGroup && prevGroup.items.length > 1;
          const conn = gIdx === 0 ? '' : (!prevParallel && isParallel)
            ? '<span class="wr-pipe-line is-fork" title="分叉为并行执行">⑂──</span>'
            : (prevParallel && !isParallel)
            ? '<span class="wr-pipe-line is-join" title="并行分支汇合">──⤙</span>'
            : '<span class="wr-pipe-line">──</span>';

          if(!isParallel){
            return conn + renderChip(group.items[0]);
          }

          const chipsHtml = group.items.map(renderChip).join('<span class="wr-pipe-line">·</span>');
          return conn + `<div class="wr-parallel-wave">`+
            `<div class="wr-parallel-wave-head"><span class="wr-parallel-badge">⑂ Step ${group.majorNo} 并行分支 (${group.items.length}路)</span></div>`+
            `<div class="wr-parallel-wave-body">${chipsHtml}</div>`+
            `</div>`;
        }).join('');
        const bindActions=()=>{
          host.querySelectorAll('[data-wr-task]').forEach(button=>{
            button.onclick=()=>{
              const task=tasks.find(item=>item.id===button.dataset.wrTask);
              if(!task)return;
              ProjectWorkspace.openTask(task.id,target.project_id,{chatId:target.chat_id||'',peek:true});
            };
          });
          host.querySelectorAll('[data-wr-result]').forEach(button=>{
            button.onclick=()=>{
              const task=tasks.find(item=>item.id===button.dataset.wrResult);
              if(!task)return;
              ProjectWorkspace.openNodeResult(WorkflowNodeWorkbench.availableSelection(task),target.project_id,{chatId:target.chat_id||'',peek:true});
            };
          });
          const dockBtn=host.querySelector('[data-wr-dock]');
          if(dockBtn)dockBtn.onclick=()=>ProjectWorkspace.openWorkflowDock?ProjectWorkspace.openWorkflowDock(target.project_id,target.workflow_run_id):ProjectWorkspace.openWorkflowRun(target.project_id,target.workflow_run_id);
          host.querySelector('[data-wr-graph]').onclick=()=>ProjectWorkspace.openWorkflowRun(target.project_id,target.workflow_run_id);
          host.querySelector('[data-wr-run]').onclick=()=>ProjectWorkspace.openWorkflowRun(target.project_id,target.workflow_run_id);
          host.querySelector('[data-wr-refresh]').onclick=refresh;
        };
        lastSignature=signature;
        host.innerHTML=`<div class="wr-head-row"><div class="wr-head-main"><strong data-wr-status>${status}</strong><span class="pt-muted" data-wr-note>${h(noteText)}</span></div><div class="wr-head-actions"><button type="button" class="btn btn-primary sm wr-dock-btn" data-wr-dock>右侧工作流 ↗</button><button type="button" class="btn ghost sm" data-wr-graph hidden>打开 DAG 工作流</button><button type="button" class="btn ghost sm" data-wr-run hidden>查看运行状态</button><button type="button" class="pw-text-button" data-wr-refresh title="刷新工作流进度">↻</button></div></div>${error?`<p class="pw-error" data-wr-error>${h(error)}</p>`:'<p class="pw-error" data-wr-error hidden></p>'}<div data-wr-pipeline-wrap class="wr-pipeline" data-wr-pipeline>${pipelineHtml}</div><div data-wr-node-actions hidden></div>`;
        bindActions();
        if(tasks.length&&tasks.every(task=>['delivered','cancelled'].includes(task.status)))clearInterval(timer);
        }catch(error){if(card.isConnected)host.textContent='运行状态暂时无法读取：'+error.message;}finally{busy=false;}
    }
    host.addEventListener('mouseleave',()=>{if(pendingSignature)refresh();});
    timer=setInterval(refresh,2500);refresh();
  }
  async function editGraph(projectId,taskId,onSaved){
    const origin=document.activeElement,dialog=document.createElement('dialog');dialog.className='output-revision-dialog';dialog.setAttribute('aria-label','修订节点证据链');
    dialog.innerHTML='<header><strong>修订节点证据链</strong><button type="button" class="btn ghost" data-wr-close>关闭</button></header><p class="hint">选择图中节点编辑主张、证据说明与来源；调整连线后保存为新的待审查版本。</p><div data-wr-graph style="overflow:auto;max-height:320px"></div><div class="pt-row"><label>选择节点<select class="control" data-wr-select></select></label><button type="button" class="btn ghost" data-wr-add>添加节点</button><button type="button" class="btn ghost" data-wr-remove>删除节点</button></div><div class="pt-row"><label>类型<select class="control" data-wr-type><option value="section">章节 / 主张</option><option value="paper">文献</option><option value="note">笔记</option><option value="data">实验数据</option></select></label><label>标题<input class="control" data-wr-label maxlength="120"></label><label>来源引用<input class="control" data-wr-ref maxlength="300"></label></div><label>证据说明<textarea class="control" data-wr-detail maxlength="600" style="min-height:70px"></textarea></label><h4>证据连线</h4><div data-wr-edges></div><div class="pt-row"><select class="control" aria-label="连线起点" data-wr-from></select><select class="control" aria-label="连线终点" data-wr-to></select><input class="control" aria-label="连线说明" data-wr-edge-label maxlength="40" placeholder="支持 / 引用 / 反驳"><button type="button" class="btn ghost" data-wr-link>添加连线</button></div><footer><label>修订说明<input class="control" data-wr-reason></label><p class="pw-error" role="alert" data-wr-error></p><button type="button" class="btn btn-primary" data-wr-save disabled>保存图谱修订并返回核查</button></footer>';
    const el=name=>dialog.querySelector('[data-wr-'+name+']');let snapshot=null,graph=null,selected='',busy=false,baseline='';
    document.body.append(dialog);dialog.showModal();
    async function close(){if(busy)return;if(!graph||JSON.stringify(graph)===baseline||await uiConfirm('图谱修订尚未保存，确定关闭？'))dialog.close();}
    el('close').onclick=close;dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    dialog.addEventListener('close',()=>{dialog.remove();if(origin?.isConnected)origin.focus();},{once:true});
    function drawGraph(){
      const positions=new Map(graph.nodes.map((node,index)=>[node.id,{x:20+(index%3)*230,y:20+Math.floor(index/3)*90}]));
      const lines=graph.edges.map(edge=>{const a=positions.get(edge.from),b=positions.get(edge.to);return a&&b?`<g><line x1="${a.x+95}" y1="${a.y+30}" x2="${b.x+95}" y2="${b.y+30}" stroke="var(--muted)" marker-end="url(#wr-arrow)"/><text x="${(a.x+b.x)/2+95}" y="${(a.y+b.y)/2+24}" fill="var(--fg)" font-size="11">${h(edge.label)}</text></g>`:'';}).join('');
      el('graph').innerHTML=`<svg viewBox="0 0 710 ${Math.max(100,Math.ceil(graph.nodes.length/3)*90+20)}" style="min-width:600px;width:100%" role="group" aria-label="节点证据链"><defs><marker id="wr-arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="var(--muted)"/></marker></defs>${lines}${graph.nodes.map(node=>{const p=positions.get(node.id);return `<g transform="translate(${p.x} ${p.y})" tabindex="0" role="button" aria-label="${h(node.label)}" data-wr-node="${h(node.id)}"><rect width="190" height="60" rx="8" fill="var(--panel)" stroke="${node.id===selected?'var(--accent)':'var(--line)'}" stroke-width="2"/><text x="10" y="34" font-size="13" fill="var(--fg)">${h(node.label.slice(0,18))}</text></g>`;}).join('')}</svg>`;
      el('graph').querySelectorAll('[data-wr-node]').forEach(node=>{node.onclick=()=>choose(node.dataset.wrNode);node.onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();choose(node.dataset.wrNode);}};});
    }
    function choose(id){selected=id;const node=graph.nodes.find(node=>node.id===id);if(!node)return;el('select').value=id;for(const field of ['type','label','ref','detail'])el(field).value=node[field]||'';drawGraph();}
    function draw(){
      const options=graph.nodes.map(node=>`<option value="${h(node.id)}">${h(node.label)}</option>`).join('');
      for(const field of ['select','from','to'])el(field).innerHTML=options;
      el('edges').innerHTML=graph.edges.map((edge,index)=>`<div class="pt-row"><span>${h(graph.nodes.find(node=>node.id===edge.from)?.label)} → ${h(graph.nodes.find(node=>node.id===edge.to)?.label)} · ${h(edge.label)}</span><button type="button" class="btn ghost" data-wr-unlink="${index}">删除连线</button></div>`).join('');
      el('edges').querySelectorAll('[data-wr-unlink]').forEach(button=>button.onclick=()=>{graph.edges.splice(Number(button.dataset.wrUnlink),1);draw();});
      choose(graph.nodes.some(node=>node.id===selected)?selected:graph.nodes[0]?.id);
    }
    el('select').onchange=event=>choose(event.target.value);
    for(const field of ['type','label','ref','detail'])el(field).oninput=()=>{const node=graph?.nodes.find(node=>node.id===selected);if(node){node[field]=el(field).value;drawGraph();}};
    el('add').onclick=()=>{if(!graph)return;selected='human-'+crypto.randomUUID().slice(0,8);graph.nodes.push({id:selected,type:'note',label:'新证据',detail:'',ref:'',mine:true});draw();};
    el('remove').onclick=()=>{if(!graph||graph.nodes.length<=1)return;graph.nodes=graph.nodes.filter(node=>node.id!==selected);graph.edges=graph.edges.filter(edge=>edge.from!==selected&&edge.to!==selected);draw();};
    el('link').onclick=()=>{if(!graph)return;graph.edges.push({from:el('from').value,to:el('to').value,label:el('edge-label').value});draw();};
    el('save').onclick=async()=>{
      if(busy||!snapshot?.editable)return;busy=true;dialog.querySelectorAll('button,input,select,textarea').forEach(input=>input.disabled=true);el('error').textContent='';
      try{const result=await api('/api/project-tasks/revise-graph',{id:taskId,project_id:projectId,reviewed_run_id:snapshot.run_id,expected_version:snapshot.task_version,graph,reason:el('reason').value});baseline=JSON.stringify(graph);dialog.close();await onSaved?.(result.task);}
      catch(error){if(dialog.isConnected)el('error').textContent=error.message;}
      finally{busy=false;if(dialog.isConnected)dialog.querySelectorAll('button,input,select,textarea').forEach(input=>input.disabled=false);}
    };
    try{snapshot=await api('/api/project-tasks/graph-output?id='+encodeURIComponent(taskId)+'&project_id='+encodeURIComponent(projectId));if(!dialog.isConnected)return;graph=structuredClone(snapshot.graph);baseline=JSON.stringify(graph);draw();el('save').disabled=!snapshot.editable;if(!snapshot.editable){dialog.querySelectorAll('input,select,textarea,button:not([data-wr-close])').forEach(input=>input.disabled=true);el('error').textContent='此节点已不在待审查状态。';}}
    catch(error){el('error').textContent=error.message;}
    return dialog;
  }
  return {mount,editGraph};
})();
