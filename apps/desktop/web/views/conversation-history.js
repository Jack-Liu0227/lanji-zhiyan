/* Shared history commands for global, project and team conversations. */
const ConversationHistory=(()=>{
  const h=value=>esc(String(value??'')),mounts=new WeakMap();
  function mount(host,options){
    if(!host)return;
    const key=(options.kind||'chat')+':'+(options.projectId||options.teamId||'global');
    const existing=mounts.get(host);if(existing?.key===key){existing.options=options;existing.ready=existing.refresh();return existing;}
    const state={key,options,view:'active',query:'',rows:[],selected:new Set(),version:0,refresh:null};mounts.set(host,state);
    host.innerHTML=`<div class="history-filters"><input class="control" type="search" data-history-search aria-label="搜索对话历史" placeholder="搜索对话…"><select class="control" data-history-view aria-label="会话范围"><option value="active">最近对话</option><option value="archive">已归档</option><option value="trash">最近删除</option></select></div><div class="history-batch"><label><input type="checkbox" data-history-all> 全选</label><button class="pw-text-button" type="button" data-history-delete>删除选中</button></div><p class="pw-error" data-history-error role="alert"></p><div data-history-rows></div>`;
    async function refresh(){
      const version=++state.version,opts=state.options;
      try{const data=await api('/api/conversation-history?kind='+(opts.kind||'chat')+'&project_id='+encodeURIComponent(opts.projectId||'')+'&team_id='+encodeURIComponent(opts.teamId||'')+'&view='+state.view+'&q='+encodeURIComponent(state.query));if(!host.isConnected||version!==state.version)return;state.rows=data.conversations;
        host.querySelector('[data-history-rows]').innerHTML=state.rows.map(row=>`<div class="history-row ${row.pinned_at?'is-pinned':''}" data-history-id="${h(row.id)}" data-history-kind="${opts.kind||'chat'}"><input type="checkbox" data-history-select="${h(row.id)}" aria-label="选择 ${h(row.title)}" ${state.selected.has(row.id)?'checked':''}><button class="${opts.projectId?'pw-session':opts.kind==='team'?'tc-hist-item':'ah-item'} history-open" type="button" ${opts.projectId?'data-pw-chat':opts.kind==='team'?'data-tc-sess':'data-open'}="${h(row.id)}" aria-current="${opts.selected?.()===row.id}"><strong>${h(row.title)}</strong><small>${row.pinned_at?'置顶 · ':''}${row.running?'运行中 · ':''}${h(fmtAgo(row.updated_at))}</small></button></div>`).join('')||'<p class="hint history-empty">这里还没有对话</p>';
        host.querySelectorAll('[data-history-id]').forEach(node=>{node.__historyState=state;const row=state.rows.find(row=>row.id===node.dataset.historyId);node.querySelector('.history-open').onclick=()=>state.view==='active'?opts.onSelect(row.id):preview(row,state);node.querySelector('[data-history-select]').onchange=event=>event.target.checked?state.selected.add(row.id):state.selected.delete(row.id);});
      }catch(error){if(host.isConnected)host.querySelector('[data-history-error]').textContent=error.message;}
    }
    state.refresh=refresh;host.querySelector('[data-history-search]').oninput=event=>{state.query=event.target.value;refresh();};host.querySelector('[data-history-view]').onchange=event=>{state.view=event.target.value;state.selected.clear();refresh();};
    host.querySelector('[data-history-all]').onchange=event=>{state.rows.forEach(row=>event.target.checked?state.selected.add(row.id):state.selected.delete(row.id));refresh();};
    host.querySelector('[data-history-delete]').onclick=async()=>{if(!state.selected.size)return;if(!await uiConfirm('将选中的 '+state.selected.size+' 条对话移入最近删除？'))return;for(const id of [...state.selected]){try{await call(state,id,'trash');state.selected.delete(id);}catch(error){uiNotify(error.message);}}await changed(state);};
    state.ready=refresh();return state;
  }
  function call(state,id,action,extra={}){const opts=state.options;return api('/api/conversation-history',{kind:opts.kind||'chat',id,action,project_id:opts.projectId||'',team_id:opts.teamId||'',...extra});}
  async function changed(state){await refreshState(false);await state.options.onChanged?.();await state.refresh();}
  async function act(state,row,action){
    try{
      let extra={};if(action==='rename'){const title=await uiPrompt('重命名对话',row.title);if(title===null)return;extra={title};}
      if(['trash','purge'].includes(action)&&!await uiConfirm(action==='purge'?'永久删除这条对话记录？关联项目文件和研究产物会保留。':'将这条对话移入最近删除？可从最近删除中恢复。'))return;
      if(action==='export'){const data=await call(state,row.id,action);download(data);return;}
      await call(state,row.id,action,extra);await changed(state);
    }catch(error){uiNotify(error.message);}
  }
  function actions(node){const state=node.__historyState,row=state?.rows.find(item=>item.id===node.dataset.historyId);if(!row)return [];
    if(row.deleted_at)return [{text:'查看记录',run:()=>preview(row,state)},{text:'恢复对话',run:()=>act(state,row,'restore')},{text:'导出 Markdown',run:()=>act(state,row,'export')},{separator:true},{text:'永久删除…',danger:true,run:()=>act(state,row,'purge')}];
    return [{text:row.pinned_at?'取消置顶':'置顶对话',run:()=>act(state,row,row.pinned_at?'unpin':'pin')},{text:'重命名…',run:()=>act(state,row,'rename')},{text:'导出 Markdown',run:()=>act(state,row,'export')},...(!row.running?[{text:row.archived_at?'取消归档':'归档对话',run:()=>act(state,row,row.archived_at?'unarchive':'archive')},{separator:true},{text:'删除对话…',danger:true,run:()=>act(state,row,'trash')}]:[{text:'打开运行中的对话',run:()=>state.options.onSelect(row.id)}])];
  }
  function download(data){const url=URL.createObjectURL(new Blob([data.content],{type:'text/markdown;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=data.title.replace(/[<>:"/\\|?*]/g,'_')+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function preview(row,state){try{const data=await call(state,row.id,'export'),dialog=document.createElement('dialog');dialog.className='pt-dialog';dialog.innerHTML=`<header class="history-preview-head"><h3>${h(row.title)}</h3><button type="button" class="pw-text-button" aria-label="关闭">×</button></header><pre class="history-preview">${h(data.content)}</pre>`;dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();}catch(error){uiNotify(error.message);}}
  return {mount,actions};
})();
