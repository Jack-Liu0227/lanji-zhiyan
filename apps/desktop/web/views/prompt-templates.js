window.PromptUI = (() => {
  const pending = new Map();
  const apiFor = (action, project, body={}) => api(`/api/prompt-templates/${action}`, {project_id:project,...body});
  const scopeNames = {project:'项目',step:'工作流步骤',assistant:'专家',member:'团队成员',schedule:'日程'};
  const autoNames = new Set(['project.title','project.question','research_question','研究问题']);
  const ref = (key, project) => pending.get(key)?.project_id===project ? pending.get(key) : undefined;

  function fields(host, item, options, initial={}){
    host.innerHTML=Object.entries(item.variables).map(([name,spec],i)=>{
      const value=initial[name] ?? spec.default ?? '';
      const choices=spec.type==='asset_ref' ? (options.assets||[]).map(a=>[a.id,a.name]) : (spec.options||[]).map(v=>[v,v]);
      const input=['enum','asset_ref'].includes(spec.type)
        ? `<select aria-label="${esc(spec.label||name)}" data-variable="${i}"><option value="">请选择</option>${choices.map(([v,label])=>`<option value="${esc(String(v))}" ${String(value)===String(v)?'selected':''}>${esc(String(label))}</option>`).join('')}</select>`
        : `<textarea rows="2" data-variable="${i}" placeholder="${autoNames.has(name)?'留空使用项目资料':spec.required===false?'可选':'请填写'}">${esc(String(value))}</textarea>`;
      return `<label>${esc(spec.label||name)}${spec.required===false?'（可选）':''}${input}${spec.description?`<small class="hint">${esc(spec.description)}</small>`:''}</label>`;
    }).join('') || '<p class="hint">固定文本模板，可直接使用。</p>';
    return ()=>Object.fromEntries(Object.entries(item.variables).flatMap(([name,spec],i)=>{
      const value=host.querySelector(`[data-variable="${i}"]`).value;
      if(value==='')return [];
      const original=spec.type==='enum' ? spec.options.find(v=>String(v)===value) : value;
      return [[name,original]];
    }));
  }

  async function configure(project, templateId, choose, options={}){
    const d=Notebook.dialog('应用提示词模板','<p class="hint">正在读取模板…</p>');
    try{
      const [item,bindingOptions]=await Promise.all([apiFor('read',project,{template_id:templateId}),apiFor('options',project)]);
      d.querySelector('.hint').remove();
      const body=document.createElement('div');body.innerHTML=`<p><strong>${esc(item.title)}</strong> · 版本 ${item.revision}</p><div class="nb-form" data-variables></div>
        <details class="pt-preview"><summary>模板原文</summary><pre>${esc(item.body)}</pre></details><div class="nb-actions"><button class="btn ghost" data-preview>预览效果</button><button class="btn" data-apply>${esc(options.applyLabel||'应用到本轮任务')}</button><button class="btn ghost" data-binding ${options.bindings===false?'hidden':''}>默认绑定…</button></div><pre class="pt-preview" data-result hidden></pre>`;
      d.querySelector('.nb-error').before(body);
      const values=fields(body.querySelector('[data-variables]'),item,bindingOptions);
      async function validate(){
        const result=await apiFor('render',project,{template_id:templateId,revision_id:item.revision_id,variables:values()});
        if(!result.ok)throw new Error([...result.missing.map(n=>`请填写 ${n}`),...Object.entries(result.errors).map(([n,e])=>`${n}：${e}`)].join('\n'));
        Notebook.error(d,'');return result;
      }
      body.querySelector('[data-preview]').onclick=async()=>{try{const result=await validate(),out=body.querySelector('[data-result]');out.hidden=false;out.textContent=result.rendered;}catch(e){Notebook.error(d,e);}};
      body.querySelector('[data-apply]').onclick=async()=>{try{const rendered=await validate();await choose({project_id:project,template_id:templateId,revision_id:item.revision_id,variables:values(),title:item.title,revision:item.revision},rendered);d.close();}catch(e){Notebook.error(d,e);}};
      body.querySelector('[data-binding]').onclick=()=>bindingDialog(project,templateId);
    }catch(e){Notebook.error(d,e);}
  }

  async function picker(project, choose, options={}){
    if(!project)return uiNotify('先选择一个项目，再引用该项目的提示词模板。');
    const d=Notebook.dialog('选择提示词模板','<div class="nb-form"><label>搜索模板<input type="search" placeholder="标题或内容"></label></div><div data-tags></div><div class="pt-template-list"></div>');
    let items=[], tags=[], sequence=0;
    const input=d.querySelector('input');
    function paint(){d.querySelector('.pt-template-list').innerHTML=items.filter(t=>tags.every(v=>t.tags.includes(v))).map(t=>`<button class="pt-template-pick" data-template="${esc(t.template_id)}"><strong>${esc(t.title)}</strong><span class="hint">${esc(t.tags.join(' · ')||'固定文本或变量模板')}</span></button>`).join('')||'<p class="hint">没有可用模板。可在笔记中点击“新建模板”，保存后使用。</p>';
      d.querySelectorAll('[data-template]').forEach(b=>b.onclick=()=>configure(project,b.dataset.template,async(v,rendered)=>{await choose(v,rendered);d.close();},options));}
    try{items=(await apiFor('search',project)).templates;Notebook.tagPicker(d.querySelector('[data-tags]'),[],[...new Set(items.flatMap(t=>t.tags))],v=>{tags=v;paint();},{allowCreate:false,name:'筛选标签'});input.oninput=async()=>{const seq=++sequence;try{const result=await apiFor('search',project,{query:input.value});if(seq===sequence&&d.isConnected){items=result.templates;paint();}}catch(e){Notebook.error(d,e);}};paint();}catch(e){Notebook.error(d,e);}
  }

  function mount(textarea, key, project, onChange=()=>{}){
    if(!textarea)return;
    textarea.parentElement.querySelector('.pt-composer')?.remove();
    const host=document.createElement('div');host.className='pt-composer';textarea.before(host);
    function paint(){const value=ref(key,project);host.innerHTML=`<button type="button" class="btn ghost" data-pick ${project?'':'disabled'} title="${project?'为本轮任务选择提示词模板':'请先绑定项目'}">提示词模板</button>${value?`<span>${esc(value.title)} · v${value.revision}</span><button type="button" class="btn ghost" data-clear aria-label="移除本轮模板">×</button>`:'<small class="hint">未单独选择时使用已启用的默认绑定</small>'}`;
      host.querySelector('[data-pick]').onclick=()=>picker(project,v=>{pending.set(key,v);onChange(v);paint();});
      host.querySelector('[data-clear]')?.addEventListener('click',()=>{pending.delete(key);onChange(null);paint();});}
    paint();
  }

  async function applyToChat(project, templateId){
    await configure(project,templateId,async value=>{
      if(window.ProjectWorkspace)return ProjectWorkspace.applyTemplate(project,value);
      let chat=(S.chats||[]).find(c=>c.id===curChat&&c.task_id===project);
      if(!chat){const created=await api('/api/chat/create',{title:value.title,task_id:project});chat=created.chat;}
      curChat=chat.id;localStorage.setItem('rw-chat',curChat);pending.set(`chat:${curChat}`,value);
      await refreshState(false);openTab('agent');
      if($('ask')){$('ask').value=`请按照「${value.title}」完成任务。`;$('ask').focus();grow();}
    });
  }

  function noteActions(host,note){
    const template=note.metadata.note_type==='prompt_template';
    host.innerHTML=template?`<span class="nb-tag">提示词模板</span><button class="btn ghost" data-use>应用到 Agent</button><button class="btn ghost" data-settings ${note.read_only?'disabled':''}>模板设置</button><button class="btn ghost" data-history>使用记录</button><button class="btn ghost" data-copy ${note.read_only?'disabled':''}>复制模板</button>`:`<button class="btn ghost" data-copy ${note.read_only?'disabled':''}>复制为提示词模板</button>`;
    async function saved(){if(!await pnSaveCurrent())throw new Error('请先完成笔记保存');return mdProj?.metadata?.template_id;}
    host.querySelector('[data-use]')?.addEventListener('click',async()=>{try{const tid=await saved();await applyToChat(note.pid,tid);}catch(e){uiNotify(e.message);}});
    host.querySelector('[data-settings]')?.addEventListener('click',()=>settings(note));
    host.querySelector('[data-history]')?.addEventListener('click',async()=>{try{await usageHistory(note.pid,await saved());}catch(e){uiNotify(e.message);}});
    host.querySelector('[data-copy]').onclick=async()=>{try{await saved();const result=await apiFor('copy',note.pid,{path:note.path});await Notebook.open(result.path);}catch(e){uiNotify(e.message);}};
  }

  function settings(note){
    const meta=note.metadata, body=$('mdSource')?.value||note.content||'';
    let definitions={...meta.variables};
    for(const match of body.matchAll(/\{\{\s*([\w\u4e00-\u9fff][\w.\u4e00-\u9fff]{0,79})\s*\}\}/g))definitions[match[1]] ||= {type:'text',required:true};
    const names=Object.keys(definitions);
    const d=Notebook.dialog('模板设置',`<form class="nb-form"><label>模板状态<select name="status"><option value="active">可用</option><option value="draft">草稿</option><option value="disabled">停用</option></select></label><label>用途说明<textarea name="description" rows="2">${esc(meta.description||'')}</textarea></label>
      <p class="hint">变量可选。在正文写 {{变量名}} 后，这里会自动出现配置项。没有变量也能直接使用。</p>
      ${names.map((name,i)=>{const spec=definitions[name];return `<fieldset data-definition="${i}"><legend>${esc(name)}</legend><label>类型<select name="type">${[['text','文本'],['number','数字'],['enum','下拉选项'],['asset_ref','项目资料']].map(([v,l])=>`<option value="${v}" ${spec.type===v?'selected':''}>${l}</option>`).join('')}</select></label><label><input type="checkbox" name="required" ${spec.required===false?'':'checked'}>必填</label><label>默认值<input name="default" value="${esc(String(spec.default??''))}"></label><label>下拉选项（每行一项）<textarea name="options" rows="2">${esc((spec.options||[]).join('\n'))}</textarea></label></fieldset>`;}).join('')}
      <button class="btn" type="submit">保存模板设置</button></form>`);
    d.querySelector('[name=status]').value=meta.template_status||'active';
    d.querySelector('form').onsubmit=async e=>{e.preventDefault();const variables={};
      names.forEach((name,i)=>{const row=d.querySelector(`[data-definition="${i}"]`),type=row.querySelector('[name=type]').value,value=row.querySelector('[name=default]').value;
        variables[name]={type,required:row.querySelector('[name=required]').checked,...(value===''?{}:{default:value}),...(type==='enum'?{options:row.querySelector('[name=options]').value.split('\n').map(v=>v.trim()).filter(Boolean)}:{})};});
      const before={...note.metadata};Object.assign(note.metadata,{variables,template_status:d.querySelector('[name=status]').value,description:d.querySelector('[name=description]').value});
      mdScheduleSave();if(await pnSaveCurrent()){d.close();Notebook.mountMetadata();}else{note.metadata=before;Notebook.error(d,new Error($('mdSaveState')?.title||'保存失败'));}
    };
  }

  async function bindingDialog(project, templateId){
    const d=Notebook.dialog('默认模板绑定','<p class="hint">优先级：本轮选择 → 日程 → 工作流步骤 → 团队成员 → 专家 → 项目。默认固定当前版本，可选择跟随更新。</p>');
    try{
      const [item,options,state]=await Promise.all([apiFor('read',project,{template_id:templateId}),apiFor('options',project),apiFor('bindings',project)]);
      const wrapper=document.createElement('div');wrapper.innerHTML=`<p>模板：${esc(item.title)} · v${item.revision}</p><form class="nb-form"><label>绑定范围<select name="scope">${Object.entries(scopeNames).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label><label>绑定对象<select name="target"></select></label><div class="nb-form" data-values></div><label><span><input name="latest" type="checkbox">跟随模板最新版本</span></label><label><span><input name="enabled" type="checkbox" checked>启用此绑定</span></label><button class="btn" type="submit">保存默认绑定</button></form><div data-existing></div>`;
      d.querySelector('.nb-error').before(wrapper);
      const form=wrapper.querySelector('form'),valuesHost=wrapper.querySelector('[data-values]');let values=fields(valuesHost,item,options);
      function targets(){form.elements.target.innerHTML=options[form.elements.scope.value].map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');}
      form.elements.scope.onchange=targets;targets();
      async function paint(rows){wrapper.querySelector('[data-existing]').innerHTML='<h4>当前项目已有绑定</h4>'+rows.map((r,i)=>`<div class="nb-category-row"><span>${esc(scopeNames[r.scope])} · ${esc(options[r.scope]?.find(x=>x.id===r.scope_id)?.name||r.scope_id||'当前项目')} · ${r.enabled?'启用':'停用'}${r.template_id===templateId?'（此模板）':''}</span><button class="btn ghost" data-edit="${i}">修改</button><button class="btn ghost" data-remove="${i}">删除</button></div>`).join('');
        wrapper.querySelectorAll('[data-remove]').forEach(b=>b.onclick=async()=>{try{const result=await apiFor('bind',project,{...rows[Number(b.dataset.remove)],delete:true});paint(result.bindings);}catch(e){Notebook.error(d,e);}});
        wrapper.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const r=rows[Number(b.dataset.edit)];if(r.template_id!==templateId){bindingDialog(project,r.template_id);return;}form.elements.scope.value=r.scope;targets();form.elements.target.value=r.scope_id;form.elements.latest.checked=!r.revision_id;form.elements.enabled.checked=!!r.enabled;values=fields(valuesHost,item,options,r.variables);});
      }
      paint(state.bindings);
      form.onsubmit=async e=>{e.preventDefault();if(!form.elements.target.options.length)return Notebook.error(d,new Error('当前项目还没有可绑定的对象'));
        try{const result=await apiFor('bind',project,{scope:form.elements.scope.value,scope_id:form.elements.target.value,template_id:templateId,revision_id:item.revision_id,variables:values(),follow_latest:form.elements.latest.checked,enabled:form.elements.enabled.checked});paint(result.bindings);d.querySelector('.nb-error').textContent='默认绑定已保存。';}catch(err){Notebook.error(d,err);}};
    }catch(e){Notebook.error(d,e);}
  }

  async function usageHistory(project, templateId){
    const d=Notebook.dialog('模板使用记录','<div data-history></div>');
    try{const {history}=await apiFor('history',project,{template_id:templateId});d.querySelector('[data-history]').innerHTML=history.map(r=>`<details class="pt-preview"><summary>${esc(r.created_at)} · v${esc(String(r.input_snapshot.revision||''))} · ${esc(r.run_id||'')}${r.input_snapshot.applied_prompt!==undefined&&r.input_snapshot.applied_prompt!==r.input_snapshot.rendered?' · 本次已修改':''}</summary><pre>${esc(r.input_snapshot.applied_prompt??r.input_snapshot.rendered??'')}</pre></details>`).join('')||'<p class="hint">还没有运行使用过此模板。预览不会计入使用记录。</p>';}catch(e){Notebook.error(d,e);}
  }

  async function manageBindings(project){
    const d=Notebook.dialog('项目模板绑定','<p class="hint">可查看和清理已有绑定；新增绑定请从可用模板的“应用到 Agent → 默认绑定”进入。</p><div data-bindings></div>');
    async function paint(){
      try{const [state,options,catalog]=await Promise.all([apiFor('bindings',project),apiFor('options',project),apiFor('search',project,{include_disabled:true})]);
        d.querySelector('[data-bindings]').innerHTML=state.bindings.map((r,i)=>`<div class="nb-category-row"><span>${esc(scopeNames[r.scope])} · ${esc(options[r.scope]?.find(x=>x.id===r.scope_id)?.name||r.scope_id||'当前项目')}<br>${esc(catalog.templates.find(t=>t.template_id===r.template_id)?.title||'模板已失效')} · ${r.enabled?'启用':'停用'}</span><button class="btn ghost" data-edit="${i}">修改</button><button class="btn ghost" data-remove="${i}">解除</button></div>`).join('')||'<p class="hint">当前项目没有默认模板绑定。</p>';
        d.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>bindingDialog(project,state.bindings[Number(b.dataset.edit)].template_id));
        d.querySelectorAll('[data-remove]').forEach(b=>b.onclick=async()=>{try{await apiFor('bind',project,{...state.bindings[Number(b.dataset.remove)],delete:true});await paint();}catch(e){Notebook.error(d,e);}});
      }catch(e){Notebook.error(d,e);}
    }
    await paint();
  }

  function event(box,ev){const card=document.createElement('div');card.className='pt-used';card.textContent=`提示词模板：${ev.title} · v${ev.revision}`;card.title=`模板版本：${ev.revision_id}`;box.appendChild(card);}
  return {picker,configure,usageHistory,mount,ref,select(key,value){if(value)pending.set(key,value);else pending.delete(key);},noteActions,bindingDialog,manageBindings,event};
})();
