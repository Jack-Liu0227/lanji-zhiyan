/* Runtime choices belong to a conversation and apply to its next request. */
const AgentRuntime=(()=>{
  const h=value=>esc(String(value??''));
  const reasoning=[['auto','自动'],['none','关闭'],['minimal','极低'],['low','低'],['medium','中'],['high','高'],['xhigh','极高']];
  const permissions=[['auto','跟随专家'],['read_only','只读'],['workspace_write','项目内读写'],['full_access','完全访问']];
  function markup(prefix,chat={},modelId=prefix+'Model'){
    const select=(id,label,values,value)=>`<label class="agent-runtime-choice"><span>${label}</span><select id="${id}" class="control" aria-label="本会话${label}">${values.map(([id,text])=>`<option value="${h(id)}" ${id===value?'selected':''}>${h(text)}</option>`).join('')}</select></label>`;
    return `<div class="agent-runtime" id="${prefix}Runtime">${select(modelId,'模型',[['','跟随专家'],...(S.models||[]).map(m=>[m.id,m.name||m.model])],chat.model_id||'')}${select(prefix+'Reasoning','推理强度',reasoning,chat.reasoning||'auto')}${select(prefix+'Permission','工具权限',permissions,chat.permission||'auto')}<span class="agent-runtime-hint" id="${prefix}RuntimeHint" role="status"></span></div>`;
  }
  async function save(chatId,key,value){const result=await api('/api/agent-settings/runtime-save',{id:chatId,[key]:value});const chat=S.chats.find(row=>row.id===chatId);if(chat)Object.assign(chat,result.chat);return result.chat;}
  return {markup,save};
})();
