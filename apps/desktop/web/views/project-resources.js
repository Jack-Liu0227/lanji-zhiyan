/* One catalog; categories refer to the original files and canonical assets. */
const ProjectResources=(()=>{
  const labels={all:'全部资料',literature:'文献',note:'研究笔记',manuscript:'论文稿件',experiment:'实验记录',output:'交付产物',file:'其他文件'};
  const h=value=>esc(String(value??''));
  async function mount(host,projectId,{kind='',live=()=>host.isConnected}={}){
    let filter=kind,query='',request=0;
    host.innerHTML=`<section class="pr-catalog"><header class="pr-heading"><div><span class="pw-eyebrow">PROJECT LIBRARY</span><h3>统一资料库</h3><p class="hint">按类型查看同一份资料，文件、版本与来源保持关联。</p></div><button class="btn ghost" type="button" data-pr-index>整理索引</button></header><nav class="pr-filters" aria-label="资料类型"></nav><form class="pr-search" role="search"><input class="control" type="search" aria-label="搜索统一资料库" placeholder="搜索标题、路径或标签"><button class="btn" type="submit">搜索</button></form><p class="pw-error" data-pr-error role="alert"></p><div class="pr-items" aria-live="polite"></div></section>`;
    const list=host.querySelector('.pr-items'),nav=host.querySelector('.pr-filters'),err=host.querySelector('[data-pr-error]');
    async function load(){
      const version=++request;list.innerHTML='<p class="hint">正在读取项目资料…</p>';
      try{
        const data=await api('/api/project/resources?project_id='+encodeURIComponent(projectId)+'&kind='+encodeURIComponent(filter)+'&q='+encodeURIComponent(query));
        if(!live()||version!==request)return;
        nav.innerHTML=Object.entries(labels).map(([key,label])=>`<button type="button" data-pr-kind="${key==='all'?'':key}" aria-pressed="${filter===(key==='all'?'':key)}">${label}<span>${key==='all'?Object.values(data.counts).reduce((a,b)=>a+b,0):data.counts[key]||0}</span></button>`).join('');
        nav.querySelectorAll('button').forEach(button=>button.onclick=()=>{filter=button.dataset.prKind;load();});
        list.innerHTML=data.items.length?data.items.map((row,index)=>`<button type="button" class="pr-item" data-pr-item="${index}"><span class="pr-kind">${labels[row.kind]||'文件'}</span><span class="pr-description"><strong>${h(row.title||'未命名')}</strong><small>${h(row.relative||row.metadata?.doi||row.path||'文献元数据 · 尚未导入 PDF')}</small></span><span class="pr-state">${row.exists?'打开':'查看记录'} ↗</span></button>`).join(''):'<div class="pw-empty"><h3>这里还没有资料</h3><p>导入文献、保存笔记或让管家生成研究成果后，在这里统一查看。已有文件可通过“整理索引”纳入。</p></div>';
        list.querySelectorAll('[data-pr-item]').forEach(button=>button.onclick=async()=>{
          const row=data.items[Number(button.dataset.prItem)];
          if(row.paper_id)await ProjectWorkspace.openPaper(row);
          else if(row.exists&&row.relative)await ProjectWorkspace.openFile({path:row.path,rel:row.relative,name:row.title,read_only:!!row.read_only},projectId,{peek:false});
          else await ProjectWorkspace.showResource('papers');
        });
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
