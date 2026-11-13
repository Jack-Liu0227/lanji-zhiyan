/* One catalog; categories refer to the original files and canonical assets. */
const ProjectResources=(()=>{
  const labels={all:'全部资料',literature:'文献',note:'研究笔记',manuscript:'论文稿件',experiment:'实验记录',output:'交付产物',file:'其他文件'};
  const reviewLabels={delivered:'已交付',awaiting_review:'待核查',rejected:'已退回',superseded:'历史版本',modified:'已修改待确认',blocked:'等待处理',source:'原始资料',unverified:'来源待确认'};
  const h=value=>esc(String(value??''));
  async function mount(host,projectId,{kind='',live=()=>host.isConnected}={}){
    let filter=kind,query='',request=0,reviewStatus=kind==='output'?'delivered':'';
    host.innerHTML=`<section class="pr-catalog"><header class="pr-heading"><div><span class="pw-eyebrow">PROJECT LIBRARY</span><h3>统一资料库</h3><p class="hint">按类型查看同一份资料，文件、版本与来源保持关联。</p></div><button class="btn ghost" type="button" data-pr-index>整理索引</button></header><nav class="pr-filters" aria-label="资料类型"></nav><form class="pr-search" role="search"><input class="control" type="search" aria-label="搜索统一资料库" placeholder="搜索标题、路径或标签"><button class="btn" type="submit">搜索</button></form><p class="pw-error" data-pr-error role="alert"></p><div class="pr-items" aria-live="polite"></div></section>`;
    const list=host.querySelector('.pr-items'),nav=host.querySelector('.pr-filters'),err=host.querySelector('[data-pr-error]');
    const statusLabel=document.createElement('label');statusLabel.className='pr-review-filter';statusLabel.textContent='产物状态 ';
    const statusSelect=document.createElement('select');statusSelect.className='control';statusSelect.setAttribute('aria-label','产物核查状态');statusSelect.innerHTML='<option value="all">全部状态</option>'+Object.entries(reviewLabels).filter(([key])=>key!=='source').map(([key,label])=>`<option value="${key}">${label}</option>`).join('');statusSelect.value=reviewStatus||'all';statusSelect.onchange=()=>{reviewStatus=statusSelect.value;load();};statusLabel.append(statusSelect);host.querySelector('form').after(statusLabel);
    async function load(){
      const version=++request;list.innerHTML='<p class="hint">正在读取项目资料…</p>';
      try{
        const data=await api('/api/project/resources?project_id='+encodeURIComponent(projectId)+'&kind='+encodeURIComponent(filter)+'&q='+encodeURIComponent(query)+'&review_status='+encodeURIComponent(reviewStatus||'all'));
        if(!live()||version!==request)return;
        nav.innerHTML=Object.entries(labels).map(([key,label])=>`<button type="button" data-pr-kind="${key==='all'?'':key}" aria-pressed="${filter===(key==='all'?'':key)}">${label}<span>${key==='all'?Object.values(data.counts).reduce((a,b)=>a+b,0):data.counts[key]||0}</span></button>`).join('');
        nav.querySelectorAll('button').forEach(button=>button.onclick=()=>{filter=button.dataset.prKind;if(filter==='output'&&!reviewStatus){reviewStatus='delivered';statusSelect.value=reviewStatus;}load();});
        const renderItem=(row,index)=>{
          const canAct=Boolean(row.exists&&row.relative&&!row.controlled&&!row.node_result);
          return `<div class="pr-item-row"><button type="button" class="pr-item" data-pr-item="${index}"><span class="pr-kind">${labels[row.kind]||'\u6587\u4ef6'}</span><span class="pr-description"><strong>${h(row.title||'\u672a\u547d\u540d')}</strong><small>${h(row.relative||row.metadata?.doi||row.path||'\u6587\u732e\u5143\u6570\u636e \u00b7 \u5c1a\u672a\u5bfc\u5165 PDF')}</small></span><span class="pr-state">${row.exists?'\u6253\u5f00':'\u67e5\u770b\u8bb0\u5f55'} \u2197</span></button>${canAct?`<div class="pr-item-actions"><button type="button" class="btn ghost" data-pr-action="reference" data-pr-row="${index}" title="\u5f15\u7528\u5230\u5bf9\u8bdd">\u5f15\u7528</button><button type="button" class="btn ghost" data-pr-action="handoff" data-pr-row="${index}" title="\u4ea4\u7ed9\u9ed8\u8ba4\u6267\u884c\u8005">\u4ea4\u7ed9\u5904\u7406</button></div>`:''}</div>`;
        };
        list.innerHTML=data.items.length?data.items.map(renderItem).join(''):'<div class="pw-empty"><h3>\u8fd9\u91cc\u8fd8\u6ca1\u6709\u8d44\u6599</h3><p>\u5bfc\u5165\u6587\u732e\u3001\u4fdd\u5b58\u7b14\u8bb0\u6216\u8ba9\u7ba1\u5bb6\u751f\u6210\u7814\u7a76\u6210\u679c\u540e\uff0c\u5728\u8fd9\u91cc\u7edf\u4e00\u67e5\u770b\u3002\u5df2\u6709\u6587\u4ef6\u53ef\u901a\u8fc7\u201c\u6574\u7406\u7d22\u5f15\u201d\u7eb3\u5165\u3002</p></div>';
        list.querySelectorAll('[data-pr-item]').forEach(button=>button.onclick=async()=>{
          const row=data.items[Number(button.dataset.prItem)];
          if(row.node_result&&row.node_result.available!==false){await ProjectWorkspace.openNodeResult({...row.node_result,project_id:projectId,task_id:row.node_result.task_id||row.node_result.id||row.task_id||row.metadata?.project_task_id},projectId);return;}
          if(row.controlled||row.metadata?.project_task_id){const taskId=row.task_id||row.metadata?.project_task_id;if(taskId)await ProjectWorkspace.openTask(taskId,projectId);else err.textContent='这份产物的执行来源尚未确认，请在任务运行记录中核对后查看。';return;}
          if(row.paper_id)await ProjectWorkspace.openPaper(row);
          else if(row.exists&&row.relative)await ProjectWorkspace.openFile({path:row.path,rel:row.relative,name:row.title,read_only:!!row.read_only},projectId,{peek:false});
          else await ProjectWorkspace.showResource('papers');
        });
        list.querySelectorAll('[data-pr-action]').forEach(button=>button.onclick=async event=>{
          event.stopPropagation();
          const row=data.items[Number(button.dataset.prRow)];
          button.disabled=true;err.textContent='';
          try{
            if(button.dataset.prAction==='reference')await ProjectWorkspace.referenceResource(row,projectId);
            else await ProjectWorkspace.handoffResource(row,projectId);
          }catch(error){if(live())err.textContent=error.message;}
          finally{if(button.isConnected)button.disabled=false;}
        });
        list.querySelectorAll('[data-pr-item]').forEach(button=>{const row=data.items[Number(button.dataset.prItem)],status=row.review_status;if(status&&reviewLabels[status]){const badge=document.createElement('small');badge.className='pr-review-state';badge.textContent=reviewLabels[status];button.querySelector('.pr-description').append(badge);}});
      }catch(error){if(live()&&version===request){err.textContent=error.message;list.replaceChildren();}}
    }
    host.querySelector('form').onsubmit=event=>{event.preventDefault();query=host.querySelector('input').value;load();};
    host.querySelector('[data-pr-index]').onclick=async event=>{
      const button=event.currentTarget;button.disabled=true;err.textContent='';
      try{const result=await api('/api/project/resources/index',{project_id:projectId});if(live()){if(result.truncated)err.textContent='本次整理达到 10000 个文件，请按项目拆分资料。';await load();}}
      catch(error){if(live())err.textContent=error.message;}
      finally{if(button.isConnected)button.disabled=false;}
    };
    await load();
  }
  return {mount};
})();
