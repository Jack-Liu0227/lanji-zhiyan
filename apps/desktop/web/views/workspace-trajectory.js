/* Durable tool activity is a read-only view of a conversation's actual runs. */
const WorkspaceTrajectory=(()=>{
  const h=value=>esc(String(value??''));
  const statusLabels={running:'运行中',pending:'等待结果',done:'完成',failed:'失败',cancelled:'已停止',interrupted:'已中断',error:'失败',success:'成功'};
  function records(events,status){
    const rows=[],pending=[];
    for(const event of events){const data=event.payload||{},kind=data.type||event.kind;
      if(kind==='model_request'){rows.push({key:'model:'+data.id,kind:'model',name:data.model||'模型请求',status:'pending',input:{permission:data.permission},round:data.round,at:event.created_at});}
      else if(kind==='tool_call'){const row={key:'tool:'+event.event_id,callId:data.id,kind:'tool',name:data.name,status:'pending',input:data.args,round:data.round,at:event.created_at};rows.push(row);pending.push(row);}
      else if(kind==='model_response'){const row=rows.find(row=>row.key==='model:'+data.id);if(row)Object.assign(row,{status:'success',duration:data.duration_ms,output:data.usage});}
      else if(kind==='tool_result'){const row=pending.find(row=>row.status==='pending'&&(data.id?row.callId===data.id:row.name===data.name));if(row)Object.assign(row,{status:data.ok?'success':'failed',duration:data.duration_ms,output:data.result??data.summary,summary:data.summary,path:data.path});else rows.push({key:'result:'+event.event_id,kind:'tool',name:data.name,status:data.ok?'success':'failed',output:data.result??data.summary,duration:data.duration_ms,at:event.created_at});}
      else if(kind==='reasoning_done'&&data.content){rows.push({key:'reasoning:'+event.event_id,kind:'reasoning',name:'思考摘要',status:'done',output:data.content,duration:data.duration_ms,at:event.created_at});}
      else if(kind==='assistant'){rows.push({key:'assistant:'+event.event_id,kind:'assistant',name:'专家回复',status:'done',output:data.content,at:event.created_at});}
      else if(['error','cancelled','sub','team','template_used','done'].includes(kind)){rows.push({key:kind+':'+event.event_id,kind,name:{error:'运行错误',cancelled:'停止运行',done:'本轮结束',sub:'子调用',team:'团队协作',template_used:'应用模板'}[kind],status:kind==='error'?'failed':kind==='cancelled'?'cancelled':'done',output:data,at:event.created_at});}
    }
    if(['done','failed','cancelled','interrupted'].includes(status))rows.filter(row=>row.status==='pending').forEach(row=>row.status='interrupted');
    return rows;
  }
  function mount(host,options){
    if(host.__trajectoryKey===options.projectId+':'+options.chatId)return;
    host.__trajectoryDispose?.();host.__trajectoryKey=options.projectId+':'+options.chatId;
    let runs=[],runId='',events=[],after=0,hasMore=false,busy=false,selected='',request=0,signature='',olderCursor=null;
    host.innerHTML=`<header class="wt-heading"><div><span class="pw-eyebrow">AGENT ACTIVITY</span><h3>执行时间线</h3></div><span class="wt-live" data-wt-live></span></header><div class="wt-toolbar"><label>运行<select class="control" data-wt-runs aria-label="选择执行轮次"></select></label><input class="control" type="search" data-wt-search aria-label="筛选执行轨迹" placeholder="搜索工具、参数或结果"><label class="wt-errors"><input type="checkbox" data-wt-errors>只看异常</label><button class="pw-text-button" data-wt-refresh>刷新</button></div><p class="wt-summary" data-wt-summary></p><div class="wt-error" data-wt-error role="status"></div><div class="wt-body"><div class="wt-timeline" data-wt-list role="list" aria-label="执行事件时间线"></div></div><button type="button" class="pw-text-button" data-wt-more hidden>加载后续记录</button>`;
    host.querySelector('.wt-toolbar').insertAdjacentHTML('beforeend','<button type="button" class="pw-text-button" data-wt-older hidden>更早轮次</button>');
    const el=name=>host.querySelector('[data-wt-'+name+']');
    const current=()=>host.isConnected&&options.live();
    const serialize=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
    const atTime=value=>{const date=new Date(value);return Number.isNaN(date.getTime())?'时间未记录':date.toLocaleTimeString('zh-CN',{hour12:false});};
    const kinds={model:['◈','模型请求'],tool:['⌘','工具调用'],reasoning:['◌','思考摘要'],assistant:['↗','专家回复'],error:['!','运行错误'],cancelled:['■','已停止'],done:['✓','本轮完成'],team:['⑂','团队协作'],sub:['⑂','子调用'],template_used:['▤','模板']};
    function render(){
      const run=runs.find(row=>row.id===runId),all=records(events,run?.status),query=el('search').value.toLowerCase();
      const shown=all.filter(row=>(!el('errors').checked||['failed','cancelled','interrupted'].includes(row.status))&&(!query||JSON.stringify(row).toLowerCase().includes(query)));
      const scroll=el('list').scrollTop;
      el('live').textContent=run?statusLabels[run.status]||run.status:'等待运行';el('live').classList.toggle('is-running',run?.status==='running');
      el('summary').textContent=run?`${run.model_name||'模型未记录'} · ${all.filter(row=>row.kind==='tool').length} 次工具调用 · ${all.filter(row=>row.status==='failed').length} 项异常`:'发送一条消息后，模型请求、工具调用与产物会按时间出现在这里。';
      el('list').innerHTML=shown.map((row,index)=>{
        const [glyph,label]=kinds[row.kind]||['·','事件'],expanded=row.key===selected;
        const preview=serialize(row.summary||row.output||row.input||'等待结果')||'';
        return `<article class="wt-event ${h(row.status)} ${expanded?'is-expanded':''}" role="listitem"><time class="wt-time" datetime="${h(row.at||'')}">${h(atTime(row.at))}</time><div class="wt-track"><span class="wt-dot" aria-hidden="true">${glyph}</span></div><div class="wt-entry"><button type="button" class="wt-row" data-wt-record="${h(row.key)}" aria-expanded="${expanded}"><span class="wt-row-top"><span class="wt-kind">${label}${row.round===undefined?'':' · 第 '+(row.round+1)+' 轮'}</span><span class="wt-state">${h(statusLabels[row.status]||row.status)}</span><span class="wt-duration">${row.duration===undefined?'耗时未记录':(row.duration/1000).toFixed(2)+' s'}</span><span class="wt-chevron">${expanded?'−':'＋'}</span></span><strong class="wt-name">${h(row.name)}</strong><span class="wt-preview">${h(preview.replace(/\s+/g,' ').slice(0,180))}</span></button>${expanded?`<section class="wt-inspector" data-wt-inspector aria-label="调用详情"><div class="wt-inspector-head"><span>调用详情</span><button type="button" class="pw-text-button" data-wt-copy>复制详情</button></div><div class="wt-detail-grid"><div><h4>输入参数</h4><pre>${h(serialize(row.input??'此事件没有输入参数'))}</pre></div><div><h4>输出结果</h4><pre>${h(serialize(row.output??'等待结果'))}</pre></div></div>${row.output?.truncated?'<p class="hint">当前为结果预览，完整内容请查看任务产物。</p>':''}<details><summary>执行信息</summary><pre>${h(JSON.stringify({run_id:runId,call_id:row.callId,round:row.round,started_at:row.at,duration_ms:row.duration,path:row.path},null,2))}</pre></details></section>`:''}</div></article>`;
      }).join('')||'<div class="wt-empty"><span aria-hidden="true">◌</span><h4>这里记录每一步执行</h4><p>完成一次对话运行后，可展开节点查看输入、结果与耗时。</p></div>';
      el('list').scrollTop=scroll;el('more').hidden=!hasMore;
      el('list').querySelectorAll('[data-wt-record]').forEach(button=>button.onclick=()=>{selected=selected===button.dataset.wtRecord?'':button.dataset.wtRecord;render();el('list').querySelectorAll('[data-wt-record]').forEach(item=>{if(item.dataset.wtRecord===button.dataset.wtRecord)item.focus({preventScroll:true});});});
      const row=all.find(row=>row.key===selected);if(row&&el('copy'))el('copy').onclick=()=>navigator.clipboard.writeText(JSON.stringify(row,null,2)).then(()=>uiNotify('已复制调用详情'),()=>uiNotify('复制失败'));
    }
    async function loadEvents(reset=false){
      if(!runId||!current())return;const ticket=++request,id=runId;
      const data=await api('/api/agent-settings/trajectory/events?chat_id='+encodeURIComponent(options.chatId)+'&project_id='+encodeURIComponent(options.projectId)+'&run_id='+encodeURIComponent(id)+'&after='+(reset?0:after));
      if(!current()||ticket!==request||id!==runId)return;
      events=reset?data.events:[...events,...data.events.filter(row=>!events.some(old=>old.event_id===row.event_id))];after=data.next_after;hasMore=data.has_more;
      const mark=JSON.stringify([id,data.status,events]);if(mark!==signature){signature=mark;render();}
    }
    async function refresh(){
      if(busy||!current()||!options.chatId)return;busy=true;
      try{const data=await api('/api/agent-settings/trajectory?chat_id='+encodeURIComponent(options.chatId)+'&project_id='+encodeURIComponent(options.projectId));if(!current())return;if(!runs.length)olderCursor=data.next_before;runs=[...data.runs,...runs.filter(old=>!data.runs.some(row=>row.id===old.id))];el('older').hidden=!olderCursor;
        if(!runs.some(row=>row.id===runId)){runId=runs[0]?.id||'';events=[];after=0;selected='';}
        if(document.activeElement!==el('runs')){el('runs').innerHTML=runs.map(row=>`<option value="${h(row.id)}">${h(fmtDate(row.created_at))} · ${h(statusLabels[row.status]||row.status)} · ${h(row.message.slice(0,32))}</option>`).join('');el('runs').value=runId;}
        if(runId)await loadEvents(!events.length);else render();el('error').textContent='';
      }catch(error){if(current())el('error').textContent='轨迹读取失败：'+error.message;}finally{busy=false;}
    }
    el('runs').onchange=()=>{runId=el('runs').value;events=[];after=0;selected='';loadEvents(true).catch(error=>el('error').textContent=error.message);};
    el('older').onclick=async()=>{if(!olderCursor)return;try{const data=await api('/api/agent-settings/trajectory?chat_id='+encodeURIComponent(options.chatId)+'&project_id='+encodeURIComponent(options.projectId)+'&before='+olderCursor);if(!current())return;runs.push(...data.runs.filter(row=>!runs.some(old=>old.id===row.id)));olderCursor=data.next_before;await refresh();}catch(error){if(current())el('error').textContent=error.message;}};
    el('search').oninput=render;el('errors').onchange=render;el('refresh').onclick=refresh;el('more').onclick=()=>loadEvents().catch(error=>el('error').textContent=error.message);
    const timer=setInterval(()=>{if(!host.isConnected){clearInterval(timer);return;}if(!document.hidden&&!host.hidden)refresh();},1500);
    host.__trajectoryDispose=()=>{clearInterval(timer);request++;};refresh();render();
  }
  return {mount};
})();
