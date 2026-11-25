/* Shared presentation for streamed messages and saved execution conversations. */
const ChatPresentation = (() => {
  const states = new WeakMap();
  const processEvents = new Set(['reasoning_start', 'reasoning_delta', 'reasoning_done', 'tool_call', 'tool_result', 'sub']);
  const terminalLabels = {done:'已完成',completed:'已完成',succeeded:'已完成',delivered:'已交付',error:'执行失败',failed:'执行失败',cancelled:'已停止',interrupted:'已中断',awaiting_review:'等待人工核查',blocked:'等待处理',waiting_input:'等待你的操作'};
  const toolLabels = {skill_read:'读取技能说明',workflow_context_read:'读取工作流上下文',library_overview:'查看项目资料',file_list:'浏览项目文件',read_attachment:'阅读附件',paper_abstracts:'检索文献摘要',paper_download:'下载论文',paper_search:'检索文献',file_read:'读取文件',file_write:'保存文件'};
  const activityLabels = {chat_waiting:'正在准备上下文',chat_reconnecting:'连接中断，正在重连',reasoning_start:'正在等待模型响应',reasoning_delta:'正在整理过程摘要',tool_call:'正在等待工具返回',tool_result:'正在处理工具结果',assistant_delta:'正在生成回复',assistant:'正在继续执行',team:'专家正在协作'};
  const time = value => {
    if(value == null || value === '')return 0;
    const parsed = typeof value === 'number' ? value : Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  function duration(milliseconds) {
    const seconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
    if(milliseconds > 0 && seconds === 0)return '不足 1 秒';
    if(seconds < 60)return seconds + ' 秒';
    if(seconds < 3600)return Math.floor(seconds / 60) + ' 分' + (seconds % 60 ? ' ' + seconds % 60 + ' 秒' : '');
    return Math.floor(seconds / 3600) + ' 小时' + (Math.floor(seconds % 3600 / 60) ? ' ' + Math.floor(seconds % 3600 / 60) + ' 分' : '');
  }
  function toolSummary(event){
    let summary=String(event.summary||event.result_summary||'').trim();
    if(/^[\[{]/.test(summary)){
      try{const value=JSON.parse(summary);summary=value.message||value.summary||(value.status?'状态：'+value.status:'结构化结果已保存，展开查看详情');}
      catch{summary='结果已保存，展开查看详情';}
    }
    summary=summary.replace(/[A-Za-z]:[\\/][^\s，；"<>]+/g,path=>path.split(/[\\/]/).at(-1));
    return /^(完成|已完成|成功|调用成功)[。.!！]?$/.test(summary)?'':summary.length>160?summary.slice(0,160)+'…':summary;
  }
  function state(box) {
    if(!states.has(box))states.set(box, {follow:true,depth:0,sequence:0,process:null,replay:false,context:null});
    return states.get(box);
  }
  function clock(node, start, prefix = '', end = 0) {
    if(!node || !start)return;
    node.textContent = prefix + duration((end || Date.now()) - start);
    node.dataset.chatClockStart = String(start);
    node.dataset.chatClockPrefix = prefix;
    if(end)delete node.dataset.chatClockStart;
  }
  function scrollRoot(box) { return box.closest('.pw-discussion, .at-msgs') || box; }
  function schedule(box, added = false) {
    const root = scrollRoot(box), s = state(root);
    if(s.depth)return;
    s.unread ||= added && !s.follow;
    if(s.frame)return;
    s.frame = requestAnimationFrame(() => {
      s.frame = 0;
      if(!root.isConnected)return;
      if(s.follow)root.scrollTop = root.scrollHeight;
      navigation(root);
    });
  }
  function mount(box, chatId) {
    const s = state(box), shell = box.closest('.chat-reading-area');
    if(s.chatId !== chatId){s.chatId=chatId;s.follow=true;s.unread=false;s.process=null;s.turnStart=0;}
    if(!shell || s.mounted)return s;
    s.mounted = true;s.shell = shell;
    s.nav = shell.querySelector('.chat-turn-nav');
    s.preview = shell.querySelector('.chat-turn-preview');
    s.latest = shell.querySelector('[data-chat-latest]');
    box.addEventListener('scroll', () => {
      if(!s.depth && !s.frame && !s.jumping)s.follow = box.scrollHeight - box.clientHeight - box.scrollTop < 48;
      if(s.follow)s.unread=false;
      navigation(box);
    }, {passive:true});
    box.addEventListener('wheel', event => {if(event.deltaY < 0){s.follow=false;s.jumping=false;}}, {passive:true});
    box.addEventListener('pointerdown', () => {s.jumping=false;});
    box.addEventListener('scrollend', () => {s.jumping=false;});
    s.latest.onclick = () => {s.follow=true;s.unread=false;s.jumping=false;box.scrollTo({top:box.scrollHeight,behavior:'instant'});navigation(box);};
    s.nav.addEventListener('keydown', event => {
      const buttons = [...s.nav.querySelectorAll('button')], index = buttons.indexOf(document.activeElement);
      const next = event.key === 'ArrowDown' ? Math.min(index+1,buttons.length-1) : event.key === 'ArrowUp' ? Math.max(0,index-1) : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : -1;
      if(next >= 0){event.preventDefault();buttons[next]?.focus();}
    });
    return s;
  }
  function detailKey(node, index) {
    return node.dataset.executionDetail || node.dataset.processKey || (node.closest('.tool-card')?.dataset.toolKey ? 'tool:'+node.closest('.tool-card').dataset.toolKey : '') || node.dataset.reasoningId || 'detail:'+index;
  }
  function replace(box, chatId, render) {
    const s = mount(box, chatId), same = box.dataset.presentationChat === chatId;
    const oldTop = box.scrollTop, top = box.getBoundingClientRect().top;
    const anchor = same && !s.follow ? [...box.querySelectorAll('[data-chat-anchor]')].find(node => node.getBoundingClientRect().bottom > top + 8) : null;
    const anchorKey = anchor?.dataset.chatAnchor, offset = anchor ? anchor.getBoundingClientRect().top-top : 0;
    const opened = new Map([...box.querySelectorAll('details')].map((node,index) => [detailKey(node,index),node.open]));
    const previousActivity=box.querySelector('.chat-activity')?.dataset;
    const activityState=previousActivity?{...previousActivity}:null;
    s.depth++;s.process=null;s.sequence=0;s.turnStart=0;
    try { render(); } finally { s.depth--; }
    box.dataset.presentationChat=chatId;
    if(same)box.querySelectorAll('details').forEach((node,index) => {const key=detailKey(node,index);if(opened.has(key))node.open=opened.get(key);});
    const currentActivity=box.querySelector('.chat-activity');
    if(same && activityState && currentActivity?.dataset.phase===activityState.phase && currentActivity.dataset.startedAt===activityState.startedAt){
      currentActivity.dataset.phaseAt=activityState.phaseAt;
      if(activityState.eventKey===currentActivity.dataset.eventKey)currentActivity.dataset.lastEvent=activityState.lastEvent;
    }
    if(same && !s.follow){
      const target = [...box.querySelectorAll('[data-chat-anchor]')].find(node => node.dataset.chatAnchor === anchorKey);
      box.scrollTop = target ? oldTop + target.getBoundingClientRect().top-top-offset : oldTop;
    }
    schedule(box);
  }
  function navigation(box) {
    const s = state(box);
    if(!s.nav)return;
    const users = [...box.querySelectorAll('.msg-user')], keys = users.map(node => node.dataset.chatAnchor);
    if(s.navKey !== JSON.stringify(keys)){
      const focused = s.nav.contains(document.activeElement) ? document.activeElement?.dataset.chatTurn : '';
      s.nav.replaceChildren();
      users.forEach((row,index) => {
        const button = document.createElement('button');button.type='button';button.className='chat-turn-node';button.dataset.chatTurn=row.dataset.chatAnchor;
        const text = row.querySelector('p')?.textContent || '', date = row.querySelector('time')?.textContent || '';
        const summary = text.split(/【当前项目明确引用的材料】|【@上下文】|【用户明确使用的提示词模板：/)[0].replace(/\s+/g,' ').trim();
        const preview = '第 '+(index+1)+' 次提问'+(date?' · '+date:'')+'\n'+summary.slice(0,220);
        button.setAttribute('aria-label','跳转到第 '+(index+1)+' 次提问：'+summary.slice(0,80));
        button.title=preview;
        const number=document.createElement('span');number.className='chat-turn-number';number.textContent=String(index+1);number.setAttribute('aria-hidden','true');
        const caption=document.createElement('span');caption.className='chat-turn-caption';caption.textContent=summary||'附件提问';
        button.append(number,caption);
        const show = () => {s.preview.textContent=preview;s.preview.hidden=false;s.preview.style.top=Math.max(8,Math.min(button.getBoundingClientRect().top-s.shell.getBoundingClientRect().top,Math.max(8,s.shell.clientHeight-s.preview.offsetHeight-8)))+'px';};
        const hide = () => {s.preview.hidden=true;};
        button.onmouseenter=show;button.onmouseleave=hide;button.onfocus=show;button.onblur=hide;
        button.onclick = () => {
          const target = [...box.querySelectorAll('.msg-user')].find(node => node.dataset.chatAnchor===button.dataset.chatTurn);
          if(!target)return;
          s.follow=false;s.unread=false;s.jumping=true;
          const current = box.querySelector('.chat-jump-highlight');current?.classList.remove('chat-jump-highlight');
          target.classList.add('chat-jump-highlight');setTimeout(()=>target.classList.remove('chat-jump-highlight'),1800);
          box.scrollTo({top:Math.max(0,box.scrollTop+target.getBoundingClientRect().top-box.getBoundingClientRect().top-20),behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
          setTimeout(()=>{s.jumping=false;},650);
          navigation(box);
        };
        s.nav.append(button);
      });
      s.navKey=JSON.stringify(keys);
      if(focused)[...s.nav.children].find(button=>button.dataset.chatTurn===focused)?.focus({preventScroll:true});
    }
    s.nav.hidden=users.length===0 || box.hidden;
    const cutoff=box.getBoundingClientRect().top+Math.min(100,box.clientHeight*.25);
    let index=0;
    users.forEach((node,i)=>{if(node.getBoundingClientRect().top<=cutoff)index=i;});
    if(s.follow && box.scrollHeight-box.clientHeight-box.scrollTop<48)index=users.length-1;
    [...s.nav.children].forEach((button,i)=>{if(i===index)button.setAttribute('aria-current','location');else button.removeAttribute('aria-current');});
    if(s.activeIndex!==index){
      const button=s.nav.children[index];
      if(button && (button.offsetTop<s.nav.scrollTop || button.offsetTop+button.offsetHeight>s.nav.scrollTop+s.nav.clientHeight))s.nav.scrollTop=button.offsetTop-s.nav.clientHeight/2;
      s.activeIndex=index;
    }
    s.latest.hidden=s.follow || box.hidden || box.scrollHeight<=box.clientHeight+2;
    s.latest.textContent=s.unread?'有新回复 · 回到最新 ↓':'回到最新 ↓';
  }
  function process(box, event) {
    if(!processEvents.has(event.type))return box;
    if(event.type==='tool_result' || event.type==='sub' && event.phase!=='start')return box;
    const s = state(box);
    if(!s.process || !box.contains(s.process)){
      const group=document.createElement('details');group.className='chat-process';group.dataset.processKey='process:'+(s.sequence++);
      group.innerHTML='<summary><span class="disclosure-arrow" aria-hidden="true">›</span><span class="chat-process-label">执行过程</span><span class="chat-process-meta"></span></summary><div class="chat-process-body"></div>';
      box.append(group);s.process=group;
      const start=time(s.context?.started_at)||s.turnStart||time(event.at)||(!s.replay?Date.now():0);
      if(start)group.dataset.startedAt=String(start);
      group.dataset.status=s.replay?(s.context?.status||'completed'):'running';
      const end=time(s.context?.ended_at)||time(s.context?.at);
      if(s.replay && group.dataset.status!=='running' && end)group.dataset.endedAt=String(end);
    }
    return s.process.querySelector('.chat-process-body');
  }
  function summarize(box) {
    box.querySelectorAll('.chat-process').forEach(group => {
      if(!group.querySelector('.chat-process-body').childElementCount){group.remove();return;}
      const tools=[...group.querySelectorAll('.tool-card')], failed=tools.filter(node=>node.classList.contains('bad')&&!node.dataset.recovered).length;
      const recovered=tools.filter(node=>node.dataset.recovered).length, running=tools.filter(node=>!node.dataset.complete).length;
      const meta=group.querySelector('.chat-process-meta');
      const pieces=[];
      if(tools.length)pieces.push(tools.length+' 次工具调用');
      if(running && group.dataset.status==='running')pieces.push(running+' 项执行中');
      if(failed)pieces.push(failed+' 项工具失败');
      if(recovered)pieces.push(recovered+' 项重试成功');
      meta.textContent=pieces.join(' · ');
      group.classList.toggle('has-error',failed>0);
      group.querySelector('.chat-process-label').textContent='执行过程'+(terminalLabels[group.dataset.status]?' · '+terminalLabels[group.dataset.status]:'');
      let elapsed=group.querySelector('.chat-process-elapsed');
      if(!elapsed){elapsed=document.createElement('span');elapsed.className='chat-process-elapsed';group.querySelector('summary').append(elapsed);}
      const start=Number(group.dataset.startedAt||0), end=Number(group.dataset.endedAt||0);
      if(start && (end || group.dataset.status==='running'))clock(elapsed,start,'用时 ',end);
      else {elapsed.textContent='';delete elapsed.dataset.chatClockStart;}
    });
  }
  function settle(box, status, end = Date.now()) {
    box.querySelectorAll('.chat-process').forEach(group => {
      if(group.dataset.status==='running'){group.dataset.status=status;group.dataset.endedAt=String(end);}
    });
    box.querySelectorAll('.tool-card:not([data-complete])').forEach(card => {
      card.dataset.complete='true';card.dataset.status=status;
      const text=card.querySelector('.tool-dur');if(text){text.textContent=terminalLabels[status]||'执行结束';delete text.dataset.chatClockStart;}
      const detail=card.querySelector('.tool-detail');if(detail)detail.textContent='未收到工具结果';
    });
    box.querySelectorAll('.reasoning-card.running').forEach(node=>{node.classList.remove('running');node.querySelector('.reasoning-label').textContent='过程摘要 · '+(terminalLabels[status]||'执行结束');});
    summarize(box);
  }
  function activity(box, type, who, event = {}) {
    if(!box)return;
    const s=state(box);
    if(s.replay || event.replay)return;
    let status=box.querySelector('.chat-activity');
    if(terminalLabels[type] && !['awaiting_review','blocked','waiting_input'].includes(type)){
      status?.remove();settle(box,type,time(event.at)||Date.now());schedule(box);return;
    }
    if(!activityLabels[type])return;
    if(!status){
      status=document.createElement('div');status.className='chat-activity';status.setAttribute('role','status');
      status.innerHTML='<span class="chat-activity-speaker"></span><span class="chat-activity-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="chat-activity-label"></span><span class="chat-activity-time" aria-hidden="true"></span><span class="chat-activity-stale"></span>';
      status.dataset.startedAt=String(time(event.started_at)||s.turnStart||Date.now());
      status.dataset.phaseAt=String(time(event.at)||Date.now());
    }
    if(who){const avatar=status.querySelector('.chat-activity-speaker'),html=speakerAvatar(who);if(avatar.__avatar!==html){avatar.innerHTML=html;avatar.__avatar=html;}avatar.title=who.name;}
    const label=type==='tool_call' && event.name ? '正在'+(toolLabels[event.name]||'运行工具 '+event.name) : activityLabels[type];
    if(status.dataset.phase!==type){status.dataset.phaseAt=String(time(event.at)||Date.now());status.dataset.phase=type;}
    status.dataset.lastEvent=String(Date.now());
    status.dataset.eventKey=String(event.id||event.at||event.event_key||'')+':'+String(event.content||'').length;
    status.querySelector('.chat-activity-label').textContent=label+'…';
    clock(status.querySelector('.chat-activity-time'),Number(status.dataset.startedAt),'本轮 ');
    box.append(status);schedule(box);
  }
  function append(box, event, render) {
    const s=state(box), previous={replay:s.replay,context:s.context};
    if(event.replay){s.replay=true;if(event.type==='assistant'||event.type==='user')s.context=event;}
    if(event.type==='user'){s.turnStart=time(event.at)||time(event.created_at)||(!s.replay?Date.now():0);s.process=null;}
    s.depth++;
    try {render();} finally {s.depth--;s.replay=previous.replay;s.context=previous.context;}
    if(event.replay && event.type==='assistant' && terminalLabels[event.status])settle(box,event.status,time(event.ended_at)||time(event.at)||Date.now());
    if(event.type==='user'){
      const row=[...box.querySelectorAll('.msg-user')].at(-1);
      if(row){row.dataset.chatAnchor='user:'+String(event.message_id||event.id||box.querySelectorAll('.msg-user').length);row.tabIndex=-1;}
    }
    if(event.type==='assistant' && !event.events?.length){
      const group=s.process;
      if(group && terminalLabels[event.status]){group.dataset.status=event.status;group.dataset.endedAt=String(time(event.ended_at)||time(event.at)||Date.now());}
      s.process=null;
    }
    if(event.type==='assistant' && !['running','queued'].includes(event.status)){
      const start=time(event.started_at)||time(previous.context?.started_at)||s.turnStart;
      const end=time(event.ended_at)||time(previous.context?.ended_at)||time(event.at)||time(previous.context?.at)||(!event.replay?Date.now():0);
      const row=[...box.querySelectorAll('.msg-assistant')].at(-1);
      if(row && start && end>start){
        let elapsed=row.querySelector('.msg-elapsed');
        if(!elapsed){elapsed=document.createElement('span');elapsed.className='msg-elapsed';row.querySelector('.msg-meta')?.insertBefore(elapsed,row.querySelector('.msg-copy'));}
        elapsed.textContent='用时 '+duration(end-start);
      }
    }
    const liveActivity=box.querySelector('.chat-activity');
    if(liveActivity && liveActivity!==box.lastElementChild)box.append(liveActivity);
    box.querySelectorAll('.tool-card').forEach(card=>{card.dataset.chatAnchor='tool:'+card.dataset.toolKey;});
    summarize(box);schedule(box,!event.replay && !s.replay && ['user','assistant','assistant_delta','team','tool_result','error'].includes(event.type));
  }
  function execution(box, data) {
    if(!box || box.id!=='pwMessages')return;
    const status=data.execution.status, messages=data.messages||[];
    const running=messages.filter(message=>['queued','running'].includes(message.status));
    if(['queued','running'].includes(status)){
      const current=running.at(-1), start=time(current?.started_at)||time(current?.created_at)||time(data.execution.started_at);
      const s=state(box);s.turnStart=start;
      const tool=[...box.querySelectorAll('.tool-card:not([data-complete])')].at(-1);
      const last=current?.events?.at(-1);
      const phase=tool?'tool_call':last?.type==='reasoning_done'?'reasoning_start':'chat_waiting';
      activity(box,phase,current?assistantSpeaker(box,current):null,{started_at:start,name:tool?.dataset.toolName,at:last?.at,id:last?.id,content:last?.content});
      const active=box.querySelector('.chat-activity');
      if(active && !current && status==='queued')active.querySelector('.chat-activity-label').textContent='正在排队等待执行…';
    }else{
      box.querySelector('.chat-activity')?.remove();
      const end=time(messages.at(-1)?.ended_at)||time(data.execution.ended_at);
      if(end)settle(box,status,end);
      if(['awaiting_review','blocked','waiting_input'].includes(status)){
        const hint=document.createElement('div');hint.className='chat-action-state';hint.setAttribute('role','status');hint.textContent=terminalLabels[status];box.append(hint);
      }
    }
    schedule(box);
  }
  setInterval(() => {
    document.querySelectorAll('[data-chat-clock-start]').forEach(node=>{
      node.textContent=(node.dataset.chatClockPrefix||'')+duration(Date.now()-Number(node.dataset.chatClockStart));
    });
    document.querySelectorAll('.chat-activity').forEach(node=>{
      const phase=Number(node.dataset.phaseAt), elapsed=phase?duration(Date.now()-phase):'';
      const total=duration(Date.now()-Number(node.dataset.startedAt));
      node.querySelector('.chat-activity-time').textContent='本轮 '+total+(elapsed?' · 当前阶段 '+elapsed:'');
      node.querySelector('.chat-activity-stale').textContent=Date.now()-Number(node.dataset.lastEvent)>30000?'仍在等待新结果':'';
    });
  },1000);
  return {append,process,activity,replace,mount,execution,duration,clock,toolSummary,toolLabel:name=>toolLabels[name]||name};
})();
