/* One catalog; categories refer to the original files and canonical assets. */
const ProjectResources=(()=>{
  const labels={all:'全部',literature:'文献',note:'研究笔记',manuscript:'论文稿件',experiment:'实验记录',output:'交付产物',file:'其他文件'};
  const kindIcons={literature:'📚',note:'📝',manuscript:'📄',experiment:'🧪',output:'📦',file:'📁'};
  const reviewLabels={source:'原始资料',delivered:'已交付',awaiting_review:'待核查',rejected:'已退回',superseded:'历史版本',modified:'修改待确认',blocked:'等待依赖',unverified:'来源待确认',queued:'排队中',running:'运行中',failed:'执行失败',interrupted:'已中断',cancelled:'已停止'};
  const PAGE_SIZE=80;
  const h=value=>esc(String(value??''));

  function itemSearchBlob(row){
    if(row._blob===undefined){
      row._blob=(String(row.title||'')+' '+String(row.relative||'')+' '+String(row.path||'')+' '+JSON.stringify(row.metadata||{})).toLowerCase();
    }
    return row._blob;
  }

  function renderItem(row,index,projectId=''){
    const title=row.title||'未命名资料';
    const rel=row.relative||'';
    const fullPath=row.path||'';
    const location=rel||fullPath||row.metadata?.doi||(row.kind==='literature'?'文献记录 · 尚无 PDF':'项目资料记录');
    const status=reviewLabels[row.review_status];
    const canUse=row.exists&&rel&&!row.controlled&&!row.node_result&&!row.metadata?.project_task_id;
    const isLocalFile=row.exists&&rel;
    const producerText=row.produced_by?`${row.produced_by.node_label}${row.produced_by.attempt?' · 第 '+row.produced_by.attempt+' 次执行':''} · ${row.produced_by.is_snapshot?'固定核查版本':'当前工作文件'}${row.produced_by.working_relative?' · 工作文件 '+row.produced_by.working_relative:''}`:'';
    return `<li class="pr-item-row" data-pr-row-idx="${index}" data-project-id="${h(row.project_id||projectId||'')}" data-rel="${h(rel)}" data-path="${h(fullPath)}">
      <span class="pr-kind" data-kind="${h(row.kind)}">${kindIcons[row.kind]||'📁'} ${labels[row.kind]||'文件'}</span>
      <button type="button" class="pr-item" data-pr-item="${index}" title="${h(fullPath||location)}">
        <strong class="pr-title">${h(title)}</strong>
        <span class="pr-path-row">
          <small class="pr-path" title="${h(fullPath||location)}">${h(location)}</small>
          ${row.read_only?'<span class="pr-tag-ro">只读快照</span>':''}
        </span>
        ${producerText?`<small class="pr-producer">${h(producerText)}</small>`:''}
      </button>
      ${status?`<span class="pr-review-state" data-status="${h(row.review_status)}">${status}</span>`:''}
      <div class="pr-item-actions">
        ${row.produced_by?.workflow_run_id?`<button type="button" class="pw-text-button" data-pr-action="workflow" data-pr-row="${index}">来源流程</button>`:''}
        ${row.node_result&&row.node_result.available!==false?`<button type="button" class="pw-text-button" data-pr-action="workbench" data-pr-row="${index}">工作台</button>`:''}
        ${row.exists&&fullPath?`<button type="button" class="pw-text-button" data-pr-action="reveal" data-pr-row="${index}" title="在文件资源管理器中定位：${h(fullPath)}">本地目录</button>`:''}
        ${canUse?`<button type="button" class="pw-text-button" data-pr-action="reference" data-pr-row="${index}" title="引用到对话">引用</button><button type="button" class="pw-text-button" data-pr-action="handoff" data-pr-row="${index}" title="交给专家或工作流处理">交给处理</button>`:''}
        <button type="button" class="btn btn-sm ghost pr-open-btn" data-pr-item="${index}" aria-label="${isLocalFile?'在目录与文件中打开':row.exists?'打开':'查看记录'}：${h(title)}">${isLocalFile?'目录定位':row.exists?'打开':'查看记录'}</button>
      </div>
    </li>`;
  }

  async function openItem(row,projectId){
    if(row.exists&&row.relative){
      const ext=(row.relative.split('.').pop()||'').toLowerCase();
      return ProjectWorkspace.openFile({path:row.path,rel:row.relative,name:row.title||row.relative.split('/').pop(),ext,read_only:!!row.read_only},projectId,{peek:false,forceLibrary:true});
    }
    if(row.paper_id&&row.exists)return ProjectWorkspace.openPaper(row);
    if(row.exists&&row.path){
      return api('/api/file/location',{project_id:projectId,path:row.path,open:true}).catch(()=>api('/api/conv/open',{path:row.path}));
    }
    if(row.node_result&&row.node_result.available!==false){
      const taskId=row.node_result.task_id||row.node_result.id||row.task_id||row.metadata?.project_task_id;
      return ProjectWorkspace.openNodeResult({...row.node_result,project_id:projectId,task_id:taskId},projectId,{peek:false});
    }
    if(row.controlled||row.metadata?.project_task_id){
      const taskId=row.task_id||row.metadata?.project_task_id;
      if(!taskId)throw new Error('这份产物的来源待确认，请到任务运行记录中核对。');
      return ProjectWorkspace.openTask(taskId,projectId,{peek:false});
    }
    if(row.paper_id)return ProjectWorkspace.openPaper(row);
    if(row.kind==='literature')return ProjectWorkspace.showResource('papers');
    throw new Error('找不到项目文件，请在“目录与文件”中核对原路径。');
  }

  async function mount(host,projectId,{kind='',live=()=>host.isConnected}={}){
    let filter=kind,query='',request=0,defaultOutputStatus=kind==='output',visibleLimit=PAGE_SIZE;
    const cache=new Map();
    host.innerHTML=`<section class="pr-catalog" aria-label="项目资料目录">
      <header class="pr-heading">
        <div class="pr-summary-wrap">
          <p class="pr-summary" role="status"></p>
        </div>
        <button class="btn ghost" type="button" data-pr-index>整理索引</button>
      </header>
      <div class="pr-toolbar">
        <div class="pr-query">
          <form class="pr-search" role="search">
            <input class="control" type="search" aria-label="搜索统一资料库" placeholder="搜索标题、路径、DOI 或标签…">
            <button class="btn" type="submit">搜索</button>
            <button class="pw-text-button" type="button" data-pr-clear hidden>清除搜索</button>
          </form>
          <label class="pr-review-filter">核查状态<select class="control" aria-label="筛选核查状态">
            <option value="all">全部状态</option>${Object.entries(reviewLabels).map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}
          </select></label>
        </div>
        <nav class="pr-filters" aria-label="按资料类型筛选">${Object.entries(labels).map(([key,label])=>`<button type="button" data-pr-kind="${key==='all'?'':key}" aria-pressed="false">${label}<span aria-hidden="true">0</span></button>`).join('')}</nav>
      </div>
      <p class="pw-error" data-pr-error role="alert"></p>
      <div class="pr-items" aria-busy="true"></div>
    </section>`;
    const list=host.querySelector('.pr-items');
    const summary=host.querySelector('.pr-summary');
    const err=host.querySelector('[data-pr-error]');
    const search=host.querySelector('input[type=search]');
    const status=host.querySelector('select');
    const clear=host.querySelector('[data-pr-clear]');
    const categories=[...host.querySelectorAll('[data-pr-kind]')];
    status.value=kind==='output'?'delivered':'all';

    function filterItems(raw){
      const needle=query.toLowerCase();
      return (raw.items||[]).filter(item=>{
        if(filter&&item.kind!==filter)return false;
        if(filter==='output'&&status.value==='all'&&defaultOutputStatus&&!(item.review_status==='delivered'&&item.consumable))return false;
        if(needle&&!itemSearchBlob(item).includes(needle))return false;
        return true;
      });
    }

    function paint(raw){
      const matched=filterItems(raw);
      const shown=matched.slice(0,visibleLimit);
      summary.innerHTML=`<span>全部资料 <strong>${raw.catalog_total}</strong></span><span>当前筛选 <strong>${matched.length}</strong></span>`;
      const counts=raw.counts||{};
      const allCount=Object.values(counts).reduce((sum,count)=>sum+count,0);
      categories.forEach(button=>{
        const k=button.dataset.prKind;
        button.setAttribute('aria-pressed',String(k===filter));
        button.querySelector('span').textContent=k?(counts[k]||0):allCount;
      });
      const filtered=query||filter||status.value!=='all';
      if(shown.length){
        const more=matched.length>shown.length?`<div class="pr-load-more"><button type="button" class="btn ghost" data-pr-more>显示更多（已展示 ${shown.length} / 共 ${matched.length} 项）</button></div>`:'';
        list.innerHTML=`<ul class="pr-result-list" aria-label="资料列表">${shown.map((r,i)=>renderItem(r,i,projectId)).join('')}</ul>${more}`;
      }else if(filtered){
        list.innerHTML='<div class="pw-empty"><h3>没有符合条件的资料</h3><p>调整关键词、资料类型或核查状态。</p><button class="btn ghost" type="button" data-pr-reset>重置筛选</button></div>';
      }else{
        list.innerHTML='<div class="pw-empty"><h3>还没有可浏览的项目资料</h3><p>上传文件或导入文献。已有项目文件可用“整理索引”登记。</p></div>';
      }
      list.querySelector('[data-pr-reset]')?.addEventListener('click',()=>{
        filter='';status.value='all';defaultOutputStatus=false;search.value='';applySearch();search.focus();
      });
      list.querySelector('[data-pr-more]')?.addEventListener('click',()=>{
        visibleLimit+=PAGE_SIZE;
        paint(raw);
      });
      list.querySelectorAll('[data-pr-item], [data-pr-action]').forEach(button=>button.onclick=async()=>{
        const row=shown[Number(button.dataset.prItem??button.dataset.prRow)];
        if(!row)return;
        button.disabled=true;err.textContent='';
        try{
          if(button.dataset.prAction==='reference')await ProjectWorkspace.referenceResource(row,projectId);
          else if(button.dataset.prAction==='handoff')await ProjectWorkspace.handoffResource(row,projectId);
          else if(button.dataset.prAction==='workflow')await ProjectWorkspace.openWorkflowDock(projectId,row.produced_by.workflow_run_id,row.produced_by.node_id);
          else if(button.dataset.prAction==='workbench'){const taskId=row.node_result.task_id||row.node_result.id||row.task_id||row.metadata?.project_task_id;await ProjectWorkspace.openNodeResult({...row.node_result,project_id:projectId,task_id:taskId},projectId,{peek:false});}
          else if(button.dataset.prAction==='reveal')await api('/api/file/location',{project_id:projectId,path:row.path,open:true}).catch(()=>api('/api/conv/open',{path:row.path}));
          else await openItem(row,projectId);
        }catch(error){if(live())err.textContent=error.message;}
        finally{if(button.isConnected)button.disabled=false;}
      });
    }

    async function load({force=false}={}){
      const reviewKey=status.value||'all';
      categories.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.prKind===filter)));
      if(!force&&cache.has(reviewKey)){
        list.setAttribute('aria-busy','false');
        paint(cache.get(reviewKey));
        return;
      }
      const version=++request;
      list.setAttribute('aria-busy','true');
      list.innerHTML='<p class="hint pr-loading">正在读取项目资料…</p>';
      try{
        const params=new URLSearchParams({project_id:projectId,kind:'',q:'',review_status:reviewKey});
        const data=await api('/api/project/resources?'+params);
        if(!live()||version!==request)return;
        cache.set(reviewKey,data);
        paint(data);
      }catch(error){
        if(live()&&version===request){err.textContent=error.message;list.replaceChildren();}
      }finally{
        if(live()&&version===request)list.setAttribute('aria-busy','false');
      }
    }

    function applySearch(){
      query=search.value.trim();
      visibleLimit=PAGE_SIZE;
      clear.hidden=!query;
      err.textContent='';
      return load();
    }
    host.querySelector('form').onsubmit=event=>{event.preventDefault();applySearch();};
    let searchTimer=null;
    search.oninput=()=>{
      clear.hidden=!(search.value||query);
      clearTimeout(searchTimer);
      searchTimer=setTimeout(()=>{if(live())applySearch();},120);
    };
    clear.onclick=()=>{search.value='';applySearch();search.focus();};
    status.onchange=()=>{defaultOutputStatus=false;visibleLimit=PAGE_SIZE;err.textContent='';load();};
    categories.forEach(button=>button.onclick=()=>{
      const next=button.dataset.prKind;
      if(filter!==next){
        if(defaultOutputStatus)status.value='all';
        defaultOutputStatus=next==='output'&&status.value==='all';
        if(defaultOutputStatus)status.value='delivered';
        filter=next;
        visibleLimit=PAGE_SIZE;
      }
      err.textContent='';load();
    });
    host.querySelector('[data-pr-index]').onclick=async event=>{
      const button=event.currentTarget;
      button.disabled=true;button.textContent='正在整理…';err.textContent='';
      try{
        const result=await api('/api/project/resources/index',{project_id:projectId});
        if(live()){
          cache.clear();
          if(result.truncated)err.textContent='索引已达到 10000 个文件。可从“目录与文件”查看其余文件。';
          await load({force:true});
        }
      }catch(error){if(live())err.textContent=error.message;}
      finally{if(button.isConnected){button.disabled=false;button.textContent='整理索引';}}
    };
    await load();
  }
  return {mount};
})();
