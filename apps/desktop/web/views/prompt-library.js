/* Visible template management shares project notes, versions and taxonomy. */
window.PromptLibrary=(()=>{
  const h=value=>esc(String(value??''));
  const request=(action,project,body={})=>api('/api/prompt-templates/'+action,{project_id:project,...body});
  const statuses={active:'可用',draft:'草稿',disabled:'已停用'};
  const labelFields=(form,names)=>Object.entries(names).forEach(([name,label])=>form.elements.namedItem(name)?.setAttribute('aria-label',label));
  async function open(options={}){
    const projects=S.tasks||[];
    const state={project:options.project_id||curTask||projects[0]?.id||'',data:null,key:'',note:null,dirty:false,saving:false,loading:false,sequence:0,loadSequence:0};
    const dialog=Notebook.dialog('提示词模板库',`<div class="pl-toolbar"><label>模板归属项目<select class="control" data-pl-project aria-label="模板归属项目"><option value="">仅查看内置模板</option>${projects.map(p=>`<option value="${h(p.id)}">${h(p.title)}</option>`).join('')}</select></label><button type="button" class="btn" data-pl-new>新建模板</button><button type="button" class="btn ghost" data-pl-categories>管理分类</button><button type="button" class="btn ghost" data-pl-bindings>模板绑定</button><button type="button" class="btn ghost" data-pl-refresh>刷新</button></div><div class="pl-filters"><input type="search" class="control" data-pl-search aria-label="搜索模板" placeholder="搜索名称、分类或标签"><select class="control" data-pl-source aria-label="模板来源"><option value="all">全部模板</option><option value="project">项目模板</option><option value="builtin">内置模板</option></select><span class="hint" data-pl-count></span></div><div class="pl-layout"><nav class="pl-list" data-pl-list aria-label="模板列表"></nav><section class="pl-editor" data-pl-editor aria-label="模板详情"></section></div>`);
    dialog.classList.add('prompt-library');
    const el=name=>dialog.querySelector('[data-pl-'+name+']');
    const fail=err=>Notebook.error(dialog,err);
    el('project').value=state.project;
    const categoryName=id=>{const rows=state.data?.catalog.categories||[],item=rows.find(c=>c.id===id),parent=rows.find(c=>c.id===item?.parent_id);return item?(parent?parent.name+' / ':'')+item.name:'未分类';};
    const defaultCategory=()=>state.data?.catalog.categories.find(c=>c.preset_key==='prompt_templates')?.id||'';
    const categoryOptions=(selected='',exclude=new Set())=>'<option value="">未分类</option>'+(state.data?.catalog.categories||[]).filter(c=>!exclude.has(c.id)).map(c=>`<option value="${h(c.id)}" ${c.id===selected?'selected':''}>${h(categoryName(c.id))}</option>`).join('');
    async function leave(){if(state.saving)return false;return !state.dirty||await uiConfirm('有未保存的模板修改，离开后将丢弃这些修改。是否继续？',{ok:'丢弃修改',cancel:'继续编辑',danger:true});}
    async function close(){if(await leave())dialog.close();}
    dialog.querySelector('[data-close]').onclick=close;
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    function rows(){return [...(state.data?.templates||[]).map(item=>({...item,key:'project:'+item.template_id,source:'project',name:item.title,category:categoryName(item.category_id)})),...(state.data?.builtins||[]).map(item=>({...item,key:'builtin:'+item.id,source:'builtin'}))];}
    function paintList(){
      const query=el('search').value.trim().toLocaleLowerCase(),source=el('source').value;
      const visible=rows().filter(item=>(source==='all'||source===item.source)&&(!query||[item.name,item.description,item.excerpt,item.prompt,item.category,...item.tags||[]].join(' ').toLocaleLowerCase().includes(query)));
      el('count').textContent=visible.length+' 个模板';
      el('list').innerHTML=visible.map(item=>`<button type="button" class="pl-item ${item.key===state.key?'is-selected':''}" data-pl-row="${h(item.key)}" aria-pressed="${item.key===state.key}"><strong>${h(item.name)}</strong><small>${h(item.source==='builtin'?'内置 · '+item.category:categoryName(item.category_id)+' · '+(statuses[item.template_status]||'可用'))}</small>${item.description||item.tags?.length?`<span>${h(item.source==='builtin'?item.description:(item.tags||[]).join(' · '))}</span>`:''}</button>`).join('')||'<p class="pl-empty hint">没有符合条件的模板。可点击“新建模板”添加。</p>';
      el('list').querySelectorAll('[data-pl-row]').forEach(button=>button.onclick=()=>select(button.dataset.plRow));
    }
    function headerState(){const locked=state.loading||!state.data||!state.project||state.data.read_only;el('new').disabled=locked;el('categories').disabled=locked;el('bindings').disabled=!state.project;el('project').value=state.project;}
    async function load(key=state.key){
      const sequence=++state.sequence,loadSequence=++state.loadSequence;
      state.loading=true;headerState();el('list').innerHTML='<p class="pl-empty hint">正在读取模板库…</p>';el('editor').innerHTML='<p class="pl-empty hint" role="status">正在读取模板…</p>';
      try{const data=await request('library',state.project);if(sequence!==state.sequence||!dialog.isConnected)return;
        state.data=data;headerState();paintList();
        const next=rows().find(item=>item.key===key)||rows()[0];
        if(next)await select(next.key,false);else{state.key='';state.note=null;el('editor').innerHTML='<p class="pl-empty hint">点击“新建模板”创建项目模板。</p>';}
      }catch(err){fail(err);}finally{if(loadSequence===state.loadSequence){state.loading=false;if(dialog.isConnected)headerState();}}
    }
    async function select(key,check=true){
      if(check&&!await leave())return;
      const item=rows().find(row=>row.key===key);if(!item)return;
      const sequence=++state.sequence;state.dirty=false;state.key=key;paintList();el('editor').innerHTML='<p class="pl-empty hint" role="status">正在读取模板…</p>';
      try{const note=item.source==='builtin'?{title:item.name,body:item.prompt,metadata:{category_id:defaultCategory(),description:item.description,tags:['论文工具箱',item.tool==='translate'?'翻译':'润色',item.category],template_status:'active'},variables:{},builtin:true}:await request('inspect',state.project,{template_id:item.template_id});
        if(sequence!==state.sequence||!dialog.isConnected)return;state.note=note;paintEditor();Notebook.error(dialog,'');
      }catch(err){fail(err);}
    }
    function newDraft(source=null){
      ++state.sequence;
      state.key='';state.dirty=false;state.note={title:source?source.title+'（自定义）':'',body:source?.body||'',metadata:{category_id:defaultCategory(),tags:[],template_status:'active',...source?.metadata},variables:source?.variables||{}};
      delete state.note.metadata.template_id;paintList();paintEditor();
    }
    function paintEditor(){
      const note=state.note,meta=note.metadata||{},builtin=!!note.builtin,locked=builtin||state.data.read_only;
      el('editor').innerHTML=`<form class="pl-editor-form"><div class="pl-editor-heading"><div><span class="pw-eyebrow">${builtin?'内置模板':'项目模板'}</span><h4>${h(note.title||'新建提示词模板')}</h4></div><span class="hint" data-pl-save-state>${builtin?'复制后可编辑保存':'修改后点击保存'}</span></div><div class="pl-fields"><label class="pl-wide">模板名称<input class="control" name="title" required maxlength="100" value="${h(note.title)}"></label><label>分类<select class="control" name="category">${categoryOptions(meta.category_id)}</select></label><label>状态<select class="control" name="status">${Object.entries(statuses).map(([value,label])=>`<option value="${value}" ${value===(meta.template_status||'active')?'selected':''}>${label}</option>`).join('')}</select></label><label class="pl-wide">标签<input class="control" name="tags" value="${h((meta.tags||[]).join('，'))}" placeholder="多个标签用逗号分隔"></label><label class="pl-wide">用途说明<textarea class="control" name="description" rows="2">${h(meta.description||'')}</textarea></label></div><label class="pl-body-label">提示词正文<textarea class="control pl-body-input" name="content" rows="11" required placeholder="填写可复用的指令；使用 {{变量名}} 添加变量">${h(note.body)}</textarea></label><details class="pl-variables"><summary>变量设置（可选）</summary><p class="hint">正文中的 {{变量名}} 会自动识别为文本变量。可在下方设置类型、默认值与枚举选项。</p><label>变量定义（JSON）<textarea class="control" name="variables" rows="5">${h(JSON.stringify(note.variables||{},null,2))}</textarea></label></details><footer class="pl-editor-actions">${builtin?'<button type="button" class="btn" data-pl-copy>复制为自定义模板</button>':`<button type="submit" class="btn" data-pl-save>保存模板</button><button type="button" class="btn ghost" data-pl-copy>复制模板</button>${note.template_id?'<button type="button" class="btn ghost pl-delete" data-pl-delete>删除模板</button><button type="button" class="btn ghost" data-pl-history>使用记录</button>':''}`} ${!builtin&&note.template_id&&options.onChoose?'<button type="button" class="btn ghost" data-pl-apply>用于当前工具</button>':''}</footer>${locked&&!builtin?'<p class="hint">当前项目为只读，无法修改模板。</p>':''}</form>`;
      const form=el('editor').querySelector('form');
      labelFields(form,{title:'模板名称',category:'分类',status:'状态',tags:'标签',description:'用途说明',content:'提示词正文',variables:'变量定义（JSON）'});
      if(locked)form.querySelectorAll('input,textarea,select').forEach(input=>{if(input.tagName==='SELECT')input.disabled=true;else input.readOnly=true;});
      form.querySelector('[data-pl-copy]').disabled=!state.project||state.data.read_only;
      form.querySelector('[data-pl-copy]').onclick=async()=>{if(await leave())newDraft(note);};
      form.elements.content.required=(meta.template_status||'active')!=='draft';
      form.oninput=form.onchange=()=>{if(locked)return;form.elements.content.required=form.elements.status.value!=='draft';state.dirty=true;el('save-state').textContent='有未保存的修改';if(el('apply'))el('apply').disabled=true;};
      if(el('save'))el('save').disabled=locked;
      if(el('delete')){el('delete').disabled=locked;el('delete').onclick=remove;}
      if(el('history'))el('history').onclick=()=>PromptUI.usageHistory(state.project,note.template_id);
      if(el('apply')){el('apply').disabled=(meta.template_status||'active')!=='active';el('apply').onclick=()=>PromptUI.configure(state.project,note.template_id,async(reference,rendered)=>{await options.onChoose(reference,rendered);dialog.close();},{applyLabel:'用于当前工具',bindings:false});}
      form.onsubmit=async event=>{
        event.preventDefault();if(locked||state.saving)return;
        const values=form.elements;let variables;
        try{variables=JSON.parse(values.variables.value||'{}');if(!variables||typeof variables!=='object'||Array.isArray(variables))throw new Error();}catch{return fail(new Error('变量定义需要有效的 JSON 对象'));}
        state.saving=true;form.inert=true;el('save-state').textContent='正在保存…';
        try{const saved=await request('save',state.project,{template_id:note.template_id||'',expected_hash:note.content_hash||'',title:values.title.value,content:values.content.value,metadata:{category_id:values.category.value,template_status:values.status.value,tags:values.tags.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean),description:values.description.value,variables}});
          state.dirty=false;options.onChanged?.({project_id:state.project,template_id:saved.template_id,status:saved.template_status});
          await load('project:'+saved.template_id);el('save-state').textContent='已保存';
        }catch(err){fail(err);el('save-state').textContent='保存失败，当前内容已保留';}finally{state.saving=false;form.inert=false;}
      };
    }
    async function remove(){
      if(!await leave())return;const note=state.note;
      if(!await uiConfirm('删除模板「'+note.title+'」？同时解除其默认绑定，已提交任务和历史运行记录保留。',{ok:'删除模板',danger:true}))return;
      state.saving=true;
      try{await request('delete',state.project,{template_id:note.template_id,expected_hash:note.content_hash});state.dirty=false;options.onChanged?.({project_id:state.project,template_id:note.template_id,deleted:true});await load('');}catch(err){fail(err);}finally{state.saving=false;}
    }
    async function categories(){
      if(!await leave())return;state.dirty=false;
      const project=state.project,box=Notebook.dialog('模板分类管理','<p class="hint">与当前项目笔记共用分类。修改分类不会搬移文件。</p><button type="button" class="btn" data-pl-add-category>新建分类</button><div data-pl-category-list></div>');
      async function change(body){const catalog=await api('/api/project/note-categories',{project_id:project,expected_hash:state.data.catalog.content_hash,...body});state.data.catalog=catalog;paint();}
      function descendants(id){const result=new Set([id]);let previous;do{previous=result.size;state.data.catalog.categories.forEach(c=>{if(result.has(c.parent_id))result.add(c.id);});}while(previous!==result.size);return result;}
      function edit(category=null){
        const child=Notebook.dialog(category?'编辑模板分类':'新建模板分类',`<form class="nb-form"><label>分类名称<input class="control" name="name" required maxlength="80" value="${h(category?.name||'')}"></label><label>上级分类<select class="control" name="parent"><option value="">无（一级分类）</option>${state.data.catalog.categories.filter(c=>!c.parent_id&&c.id!==category?.id).map(c=>`<option value="${h(c.id)}" ${c.id===(category?.parent_id??defaultCategory())?'selected':''}>${h(c.name)}</option>`).join('')}</select></label><label>说明<textarea class="control" name="description" rows="2">${h(category?.description||'')}</textarea></label><button type="submit" class="btn">保存分类</button></form>`);
        labelFields(child.querySelector('form'),{name:'分类名称',parent:'上级分类',description:'说明'});
        child.querySelector('form').onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;try{await change({action:'save',id:category?.id||'',name:f.elements.name.value,parent_id:f.elements.parent.value,description:f.elements.description.value});child.close();}catch(err){Notebook.error(child,err);}};
      }
      function erase(category){
        const excluded=descendants(category.id),child=Notebook.dialog('删除模板分类',`<form class="nb-form"><p class="hint">分类中的笔记和模板将迁移到所选分类，文件保留。</p><label>迁移到<select class="control" name="destination">${categoryOptions(defaultCategory(),excluded)}</select></label><button type="submit" class="btn">迁移并删除分类</button></form>`);
        labelFields(child.querySelector('form'),{destination:'迁移到'});
        child.querySelector('form').onsubmit=async event=>{event.preventDefault();try{await change({action:'delete',id:category.id,destination_id:event.currentTarget.elements.destination.value});child.close();}catch(err){Notebook.error(child,err);}};
      }
      function paint(){box.querySelector('[data-pl-category-list]').innerHTML=state.data.catalog.categories.map(c=>`<div class="pl-category-row"><span>${h(categoryName(c.id))}</span><button type="button" class="btn ghost" data-pl-edit-category="${h(c.id)}">编辑</button><button type="button" class="btn ghost" data-pl-delete-category="${h(c.id)}" ${c.preset_key==='prompt_templates'?'disabled title="保留模板根分类"':''}>删除</button></div>`).join('');
        box.querySelectorAll('[data-pl-edit-category]').forEach(button=>button.onclick=()=>edit(state.data.catalog.categories.find(c=>c.id===button.dataset.plEditCategory)));
        box.querySelectorAll('[data-pl-delete-category]').forEach(button=>button.onclick=()=>erase(state.data.catalog.categories.find(c=>c.id===button.dataset.plDeleteCategory)));
      }
      let refreshed=false;const refresh=()=>{if(!refreshed){refreshed=true;load();}};
      box.querySelector('[data-pl-add-category]').onclick=()=>edit();box.querySelector('[data-close]').onclick=()=>{refresh();box.close();};box.addEventListener('close',refresh);paint();
    }
    el('new').onclick=async()=>{if(await leave())newDraft();};
    el('categories').onclick=categories;el('bindings').onclick=()=>PromptUI.manageBindings(state.project);
    el('refresh').onclick=async()=>{if(await leave()){state.dirty=false;await load();}};
    el('project').onchange=async()=>{const next=el('project').value;if(!await leave()){el('project').value=state.project;return;}state.project=next;state.dirty=false;state.key='';state.data=null;headerState();el('list').innerHTML='';el('editor').innerHTML='';await load('');};
    el('search').oninput=paintList;el('source').onchange=paintList;
    headerState();el('list').innerHTML='<p class="pl-empty hint">正在读取模板库…</p>';await load();return dialog;
  }
  return {open};
})();
