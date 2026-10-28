/* Project-scoped Zotero membership controls, backed by the shared sync APIs. */
(() => {
  "use strict";
  const e = value => esc(String(value ?? ""));
  const mounts = new WeakMap();
  const controls = new WeakMap();
  const pending = new WeakMap();
  function mount(container, projectId, onChange = async () => {}) {
    const ticket = {};
    mounts.set(container, ticket);
    const promise = render(container, projectId, onChange, ticket).catch(err => {
      if (mounts.get(container) === ticket && container.isConnected && curTask === projectId) {
        container.innerHTML = `<p class="integration-error" role="alert">读取文献管理配置失败：${e(err.message)}</p><button class="btn ghost" data-zs-reload>重新读取</button>`;
        container.querySelector('[data-zs-reload]').onclick = () => mount(container, projectId, onChange).catch(()=>{});
      }
      throw err;
    });
    pending.set(container,promise);
    return promise;
  }
  async function render(container, projectId, onChange, ticket) {
    const field = id => container.querySelector('#' + id);
    const isCurrent = () => mounts.get(container) === ticket && container.isConnected && curTask === projectId && uiRouteIs('space') && spTab === 'papers';
    const error = message => { if (isCurrent() && field('integration-error')) field('integration-error').textContent = String(message?.message || message || ''); };
    async function call(path, body) {
      if (!isCurrent()) throw new Error('项目已切换，请在当前项目重新操作');
      return api(path, body);
    }
    async function action(button, work) {
      if (!isCurrent()) return;
      button.disabled = true; error('');
      try { await work(); } catch (err) { error(err); }
      finally {
        button.disabled = false;
        if (isCurrent()) {
          if (field('zs-select')) field('zs-select').disabled = !chosenKeys.size;
          if (field('zs-apply')) field('zs-apply').disabled = !preview || !!preview.conflicts.length || !preview.actions.length;
        }
      }
    }
    const [result, automatic] = await Promise.all([
      call('/api/zotero/web/config?task_id=' + encodeURIComponent(projectId)),
      call('/api/zotero/web/auto?task_id=' + encodeURIComponent(projectId))
    ]);
    if (!isCurrent()) return;
    const bound = result.binding || {}, autoSync = automatic.auto_sync || {};
    const activeProject = projectId;
    let sourceDraft = (bound.sources || []).map(source => ({...source}));
    let collectionNames = new Map(), preview = null;
    const loadZotero = () => isCurrent() ? mount(container, projectId, onChange) : undefined;
    container.innerHTML = `<p class="hint">${result.config?.transport === 'local' ? '本机 Zotero' : 'Zotero Web API'} · ${result.local?.readable ? '本机已连接' : '本机未连接'} · ${result.local?.authorized ? '已有写入授权' : '首次写入时由 Zotero 授权'}</p>
        <div class="integration-status">当前文献库：${bound.library ? `<code>${e(bound.library)}</code>` : "未设置"}<span id="zs-last-sync">${bound.last_sync ? ` · 最近同步 ${e(new Date(Number(bound.last_sync) * 1000).toLocaleString())}` : ""}</span></div>
        <fieldset style="border:1px solid var(--line);padding:12px;margin:12px 0"><legend>项目文献来源</legend>
          <p class="hint">可添加多个 Collection，默认逐篇勾选加入项目。来源可以被多个项目共用；移除来源不会移除已加入的文献。不添加来源时，可从整个文献库勾选。</p>
          <div class="integration-actions"><button class="btn ghost" id="zs-load">读取 Collections</button><select class="control" id="zs-collection" aria-label="文献来源 Collection"><option value="">先读取 Collections</option></select><button class="btn ghost" id="zs-add-source">添加来源</button></div>
          <div id="zs-sources"></div><button class="btn" id="zs-save-sources">保存项目来源</button>
          <p class="hint">“全部跟随”会将该 Collection 当前及以后新增的文献纳入项目，请按需开启。子 Collection 需单独添加。</p>
          <div class="integration-actions"><button class="btn ghost" id="zs-browse" ${bound.library?'':'disabled'}>从已保存来源选择文献</button><input class="control" id="zs-filter" aria-label="筛选来源文献" placeholder="筛选标题、作者或 DOI"></div>
          <div id="zs-candidates" style="max-height:360px;overflow:auto"></div><button class="btn" id="zs-select" disabled>加入所选文献并预览同步</button>
        </fieldset>
        <fieldset style="border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0"><legend>自动同步到工作流与项目空间</legend>
          <label><input type="checkbox" id="zs-auto-enabled" ${autoSync.enabled ? "checked" : ""}> 应用运行期间自动同步</label>
          <div class="integration-actions"><select class="control" id="zs-auto-mode" aria-label="自动同步方向"><option value="merge" ${autoSync.mode==="merge"?"selected":""}>双向合并</option><option value="pull" ${autoSync.mode==="pull"?"selected":""}>仅拉取 Zotero</option><option value="push" ${autoSync.mode==="push"?"selected":""}>仅推送项目</option></select>
          <label>间隔 <input class="control" type="number" min="10" max="3600" id="zs-auto-interval" value="${Number(autoSync.interval_seconds)||15}" style="width:90px"> 秒</label><button class="btn" id="zs-auto-save">保存自动同步</button></div>
          <p class="hint" role="status" id="zs-auto-state">${e({disabled:"未启用",waiting:"等待下次检查",synced:"已同步",conflict:"需要处理冲突",error:"同步失败"}[autoSync.status] || autoSync.status || "未启用")}${autoSync.last_sync?" · "+e(new Date(Number(autoSync.last_sync)*1000).toLocaleString()):""}${autoSync.error?" · "+e(autoSync.error):""}</p>
          <p class="hint">本机变化按间隔增量同步，工作流和项目空间共享题录。冲突条目等待处理，其余条目继续同步；PDF 保留项目副本。</p></fieldset>
        <div class="integration-actions"><select class="control" id="zs-mode" aria-label="本次同步方向"><option value="merge">双向合并元数据</option><option value="pull">拉取 Zotero 到项目</option><option value="push">推送项目到 Zotero</option></select><button class="btn" id="zs-preview">预览同步</button><button class="btn ghost" id="zs-apply" disabled>确认执行预览</button></div>
        <div id="zs-plan" aria-live="polite"></div>
        <div id="zs-conflicts"></div>
        <fieldset style="border:1px solid var(--line);padding:12px;margin:12px 0"><legend>按项目标签批量移除关联</legend><p class="hint">双向合并或推送时，为项目成员添加 <code>rw:project:${e(projectId)}</code>。移除时仅清理当前项目标签和关联，并从当前项目文献列表隐藏；保留全部 Collection 归属、其他项目关联、文件及历史笔记。可恢复本次移除。</p><button class="btn ghost" id="zs-unlink-preview" ${bound.library?'':'disabled'}>预览当前项目标签关联</button><div id="zs-unlink"></div><div id="zs-batches"></div></fieldset>
        <p class="hint">工作流与项目空间共享项目成员题录。现有条目保留原 Collection 归属；移动 Collection 不影响项目关联。仅拉取不会写入项目标签。两端同时修改或远端删除会显示冲突。${bound.collection?'新建 Zotero 条目存入 '+e(bound.collection)+'。':'新建 Zotero 条目存入文献库根目录并打项目标签。'}</p>

      <div id="integration-error" class="integration-error" role="alert"></div>
      <div id="integration-result" class="integration-result" aria-live="polite"></div>`;
    field("zs-auto-save").onclick = event => action(event.target, async () => {
      await call("/api/zotero/web/auto",{task_id:projectId,enabled:field("zs-auto-enabled").checked,
        mode:field("zs-auto-mode").value,interval_seconds:Number(field("zs-auto-interval").value)});
      await loadZotero();
    });
    async function loadCollections() {
      const data = await call("/api/zotero/web/collections");
      if (!isCurrent()) return;
      const collections = data.collections || [];
      collectionNames = new Map(collections.map(item => [item.key, item.data?.name || item.key]));
      field("zs-collection").innerHTML = collections.map(item => `<option value="${e(item.key)}">${e(item.data?.name || item.key)} · ${e(item.key)}</option>`).join("");
      renderSources();
      return collections.length;
    }
    field("zs-load").onclick = event => action(event.target, async () => {
      const count = await loadCollections();
      if (isCurrent()) field("integration-result").textContent = `已拉取 ${count} 个集合。`;
    });
    function renderSources() {
      field('zs-sources').innerHTML = sourceDraft.map((s,i)=>`<div class="integration-actions"><span>${e(collectionNames.get(s.collection)||s.collection)}</span><select class="control" data-zs-source-mode="${i}" aria-label="${e(s.collection)} 同步范围"><option value="selected" ${s.mode==='selected'?'selected':''}>逐篇选择（默认）</option><option value="all" ${s.mode==='all'?'selected':''}>全部跟随，包括新增文献</option></select><button class="btn ghost" data-zs-remove-source="${i}">移除来源</button></div>`).join('');
      field('zs-sources').querySelectorAll('[data-zs-source-mode]').forEach(el=>el.onchange=()=>{sourceDraft[Number(el.dataset.zsSourceMode)].mode=el.value;});
      field('zs-sources').querySelectorAll('[data-zs-remove-source]').forEach(el=>el.onclick=()=>{sourceDraft.splice(Number(el.dataset.zsRemoveSource),1);renderSources();});
    }
    renderSources();
    field('zs-add-source').onclick=()=>{
      const collection=field('zs-collection').value;
      if(collection && !sourceDraft.some(s=>s.collection===collection)){sourceDraft.push({collection,mode:'selected'});renderSources();}
    };
    field('zs-save-sources').onclick=event=>action(event.target,async()=>{
      if(sourceDraft.some(s=>s.mode==='all') && !await uiConfirm('“全部跟随”来源中的所有文献及新增文献将在同步时加入项目。保存这些来源？')) return;
      if(!isCurrent()) return;
      await call('/api/zotero/web/sources',{task_id:activeProject,sources:sourceDraft,export_collection:bound.collection||''});
      if(isCurrent()) await loadZotero();
    });
    let candidateRows = [], chosenKeys = new Set();
    function renderCandidates(){
      const query=field('zs-filter').value.trim().toLocaleLowerCase();
      const visible=candidateRows.filter(r=>[r.title,r.authors,r.doi,r.key].join(' ').toLocaleLowerCase().includes(query));
      field('zs-candidates').innerHTML=`<p class="hint">${visible.length} 条候选 · 已勾选 ${chosenKeys.size} 条</p>`+visible.map(r=>`<label style="display:block;margin:8px 0"><input type="checkbox" data-zs-member="${e(r.key)}" ${chosenKeys.has(r.key)?'checked':''} ${r.selected||r.status==='removing'?'disabled':''}> ${e(r.title)} <small>${e(r.doi||r.key)} · ${e(r.collections.map(k=>collectionNames.get(k)||k).join('、'))}${r.status==='selected'?' · 已选，待同步':r.selected?' · 已加入项目':r.status==='excluded'?' · 已移除，可重新加入':r.status==='removing'?' · 正在移除':''}</small></label>`).join('');
      field('zs-select').disabled=!chosenKeys.size;
      field('zs-candidates').querySelectorAll('[data-zs-member]').forEach(el=>el.onchange=()=>{el.checked?chosenKeys.add(el.dataset.zsMember):chosenKeys.delete(el.dataset.zsMember);renderCandidates();});
    }
    field('zs-filter').oninput=renderCandidates;
    field('zs-browse').onclick=event=>action(event.target,async()=>{
      const data=await call('/api/zotero/web/candidates?task_id='+encodeURIComponent(activeProject));
      if(!isCurrent()) return;
      candidateRows=data.items;chosenKeys.clear();renderCandidates();
    });
    field('zs-select').onclick=event=>action(event.target,async()=>{
      await call('/api/zotero/web/select',{task_id:activeProject,keys:[...chosenKeys]});
      if(!isCurrent()) return;
      candidateRows.forEach(r=>{if(chosenKeys.has(r.key)){r.status='selected';r.selected=true;}});
      chosenKeys.clear();renderCandidates();
      const plan=await call('/api/zotero/web/sync',{task_id:activeProject,mode:'merge',dry_run:true});
      if(!isCurrent()) return;
      preview=plan;field('zs-mode').value='merge';
      field('integration-result').textContent='已保存选择，请核对同步预览。自动同步已启用时，也会按所设方向处理。';
      renderPlan(plan);
      field('zs-apply').disabled=!!plan.conflicts.length||!plan.actions.length;showConflicts(plan.conflicts);
    });
    field("zs-mode").onchange = () => { preview = null; field("zs-apply").disabled = true; };
    field("zs-preview").onclick = event => action(event.target, async () => {
      const plan = await call("/api/zotero/web/sync", {task_id: projectId, mode: field("zs-mode").value, dry_run: true});
      if (!isCurrent()) return;
      preview = plan; renderPlan(plan);
      field("zs-apply").disabled = !!preview.conflicts.length || !preview.actions.length;
      showConflicts(preview.conflicts);
    });
    field("zs-apply").onclick = event => action(event.target, async () => {
      if (!preview || !await uiConfirm(`将执行预览中的 ${preview.actions.length} 项同步（可能修改项目或连接的 Zotero），是否继续？`)) return;
      const result = await call("/api/zotero/web/sync", {task_id: projectId, mode: preview.mode, dry_run: false, plan_token: preview.plan_token, confirm: true});
      if (!isCurrent()) return;
      field("integration-result").textContent = result.ok ? `同步完成，已处理 ${(result.applied || []).length} 项。` : `同步部分完成：${result.error || '请重新预览后重试'}`;
      preview = null; field('zs-plan').innerHTML = '';
      field("zs-apply").disabled = true;
      await onChange();
    });
    function renderPlan(plan) {
      field('zs-plan').innerHTML = `<p>待同步 ${plan.actions.length} 项 · 冲突 ${plan.conflicts.length} 项</p><ul>${plan.actions.map(a=>`<li>${e({pull:'更新项目题录',push:'更新 Zotero',link:'确认关联'}[a.kind] || a.kind)} · ${e(a.payload?.title || a.key || '文献')}${a.membership?' · 同步项目标签':''}</li>`).join('')}</ul>`;
    }
    async function batchAction(batch, restore=false) {
      const data=await call('/api/zotero/web/links/'+(restore?'restore':'apply'),{task_id:projectId,batch_id:batch.id,confirm:true});
      if(!isCurrent()) return;
      const result=data.batch;
      field('integration-result').textContent=(result.status==='partial'?'部分条目未完成，可重试或恢复。':restore?'已恢复本次项目关联。':'已移除所选项目关联。')+'\n'+result.data.items.filter(r=>r.error).map(r=>r.title+'：'+r.error).join('\n');
      await loadBatches();
      preview=null;field('zs-apply').disabled=true;field('zs-plan').innerHTML='';
      await onChange();
    }
    async function loadBatches(){
      if(!bound.library) return;
      const data=await call('/api/zotero/web/links?task_id='+encodeURIComponent(projectId));
      if(!isCurrent()||!field('zs-batches')) return;
      field('zs-batches').innerHTML=(data.batches||[]).map(b=>`<div class="integration-actions"><span>${e(new Date(Number(b.created_at)*1000).toLocaleString())} · ${e({applied:"已移除",partial:"部分完成",restored:"已恢复",preview:"待确认",applying:"处理中",restoring:"恢复中"}[b.status]||b.status)} · ${(b.data.items||[]).length} 条</span>${b.status!=='preview'?`<button class="btn ghost" data-zs-restore="${e(b.id)}">恢复本次关联</button>`:''}${b.status==='partial'?`<button class="btn ghost" data-zs-retry="${e(b.id)}">重试未完成项</button>`:''}</div>`).join('');
      for(const b of data.batches||[]){
        field('zs-batches').querySelector(`[data-zs-restore="${CSS.escape(b.id)}"]`)?.addEventListener('click',event=>action(event.target,()=>batchAction(b,true)));
        field('zs-batches').querySelector(`[data-zs-retry="${CSS.escape(b.id)}"]`)?.addEventListener('click',event=>action(event.target,()=>batchAction(b,b.data.operation==='restore')));
      }
    }
    field('zs-unlink-preview').onclick=event=>action(event.target,()=>previewRemoval());
    async function previewRemoval(keys) {
      const data=await call('/api/zotero/web/links/preview',{task_id:projectId,...(keys ? {keys} : {})});
      if(!isCurrent()) return;
      const batch=data.batch;
      field('zs-unlink').innerHTML=`<p>${batch.data.items.length} 条关联。取消勾选可以保留对应文献。</p>${batch.data.items.map((r,i)=>`<label style="display:block;margin:6px 0"><input type="checkbox" data-zs-unlink-key="${e(r.key)}" checked> ${e(r.title)} <small>其他项目：${e((r.shared_projects||[]).join('、')||'无')} · 笔记 ${r.references?.notes||0} · 工作流 ${(r.references?.runs||[]).length}</small></label>`).join('')}<button class="btn" id="zs-unlink-apply" ${batch.data.items.length?'':'disabled'}>移除所选项目关联</button>`;
      field('zs-unlink-apply').onclick=event=>action(event.target,async()=>{
        const keys=[...field('zs-unlink').querySelectorAll('[data-zs-unlink-key]:checked')].map(el=>el.dataset.zsUnlinkKey);
        if(!keys.length) throw new Error('请至少选择一条关联');
        const selected=keys.length===batch.data.items.length?batch:(await call('/api/zotero/web/links/preview',{task_id:projectId,keys})).batch;
        if(!await uiConfirm(`移除所选 ${keys.length} 条文献与当前项目的关联？Zotero 条目与其他项目保留，可从操作记录恢复。`)) return;
        await batchAction(selected); if(isCurrent()) field('zs-unlink').innerHTML='';
      });
      field('zs-unlink').scrollIntoView({block:'nearest'});
    }
    function showConflicts(rows){
      field('zs-conflicts').innerHTML=rows.map((r,i)=>`<div class="integration-actions"><span>${e(r.title||r.key||'文献冲突')} · ${e(r.reason)}</span>${r.candidates?.length?`<select class="control" data-zs-candidate="${i}" aria-label="选择对应 Zotero 条目">${r.candidates.map(c=>`<option value="${e(c.key)}">${e(c.title)} · ${e(c.doi)} · ${e(c.key)}</option>`).join('')}</select>`:''}${r.result_id?`<button class="btn ghost" data-zs-compare="${i}">比较并处理</button>`:''}${r.key?`<button class="btn ghost" data-zs-unlink-conflict="${i}">解除当前项目关联</button>`:''}</div>`).join('');
      field('zs-conflicts').querySelectorAll('[data-zs-unlink-conflict]').forEach(button=>button.onclick=event=>action(event.target,async()=>{
        const r=rows[Number(button.dataset.zsUnlinkConflict)];
        const data=await call('/api/zotero/web/links/preview',{task_id:projectId,keys:[r.key]});
        if(!await uiConfirm(`解除“${r.title||r.key}”与当前项目的关联？保留原文献与历史记录。`)) return;
        await batchAction(data.batch);button.parentElement.textContent='已解除关联，请重新预览同步。';preview=null;field('zs-apply').disabled=true;
      }));
      field('zs-conflicts').querySelectorAll('[data-zs-compare]').forEach(button=>button.onclick=event=>action(event.target,async()=>{
        const r={...rows[Number(button.dataset.zsCompare)]};
        r.key=button.parentElement.querySelector('[data-zs-candidate]')?.value || r.key;
        const params=new URLSearchParams({task_id:projectId,result_id:r.result_id||'',key:r.key||r.item_key||''});
        const data=await call('/api/zotero/web/links/compare?'+params);
        if(!isCurrent()) return;
        const panel=document.createElement('div');
        panel.innerHTML=`<table style="width:100%;table-layout:fixed"><thead><tr><th>字段</th><th>项目</th><th>Zotero</th></tr></thead><tbody>${[['title','标题'],['authors','作者'],['year','年份'],['doi','DOI'],['url','链接'],['abstract','摘要']].map(([key,label])=>`<tr><th>${label}${data.local[key]!==data.remote[key]?' · 有差异':''}</th><td style="overflow-wrap:anywhere">${e(data.local[key])}</td><td style="overflow-wrap:anywhere">${e(data.remote[key])}</td></tr>`).join('')}</tbody></table><div class="integration-actions"><button class="btn" data-zs-decision="local">采用项目版本</button><button class="btn" data-zs-decision="zotero">采用 Zotero 版本</button><button class="btn ghost" data-zs-detach>解除当前项目关联</button></div>`;
        button.parentElement.replaceWith(panel);
        panel.querySelectorAll('[data-zs-decision]').forEach(b=>b.onclick=event=>action(event.target,async()=>{
          await call('/api/zotero/web/links/resolve',{version:data.version,local_hash:data.local_hash,task_id:projectId,result_id:r.result_id,key:r.key||r.item_key,decision:b.dataset.zsDecision,confirm:true});
          if(!isCurrent()) return;
          panel.textContent='已处理，请重新预览同步。'; preview=null; field('zs-apply').disabled=true;
          await onChange();
        }));
        panel.querySelector('[data-zs-detach]').onclick=event=>action(event.target,async()=>{const p=await call('/api/zotero/web/links/preview',{task_id:projectId,keys:[r.key||r.item_key]});await batchAction(p.batch);panel.textContent='已解除关联';});
      }));
    }
    let statusBusy=false;
    async function refreshStatus(){
      if(!isCurrent()||statusBusy)return;
      statusBusy=true;
      try {
        const data=await call('/api/zotero/web/auto?task_id='+encodeURIComponent(projectId));
        if(!isCurrent())return;
        const state=data.auto_sync||{}, last=data.binding?.last_sync;
        field('zs-auto-state').textContent=({disabled:'未启用',waiting:'等待下次检查',synced:'已同步',conflict:'需要处理冲突',error:'同步失败'}[state.status]||state.status||'未启用')+(state.error?' · '+state.error:'');
        field('zs-last-sync').textContent=last?' · 最近同步 '+new Date(Number(last)*1000).toLocaleString():'';
      } finally {statusBusy=false;}
    }
    controls.set(container, {previewRemoval,refreshStatus});
    await loadBatches();
    if (sourceDraft.length && isCurrent()) {
      try { await loadCollections(); } catch (err) { if (isCurrent()) error(err); }
    }
    return { previewRemoval };
  }
  window.ProjectZotero = { mount, refreshStatus: container => controls.get(container)?.refreshStatus(), async previewRemoval(container, keys) {
    await pending.get(container);
    if(!controls.has(container)) throw new Error('请先读取项目来源配置');
    return controls.get(container).previewRemoval(keys);
  } };
})();
