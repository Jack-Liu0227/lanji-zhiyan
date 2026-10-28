const UsageView=(()=>{
  const h=value=>esc(String(value??''));
  const compact=new Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1});
  const n=value=>value==null?'—':compact.format(value);
  const full=value=>value==null?'未返回':Number(value).toLocaleString('zh-CN');
  const money=(value,currency='USD')=>new Intl.NumberFormat('zh-CN',{style:'currency',currency,minimumFractionDigits:2,maximumFractionDigits:6}).format(value);
  const cost=row=>row.cost==null?'—':row.cost_min!=null&&row.cost_min!==row.cost?`${money(row.cost_min,row.currency)}–${money(row.cost,row.currency)}`:money(row.cost,row.currency);
  const when=value=>new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  const labels={overview:'概览',requests:'请求明细',sessions:'会话',context:'上下文',prices:'模型价格'};
  const components={system:'系统提示词',tools:'工具定义',tool_results:'工具结果',conversation:'会话内容'};
  let view='overview',days=30,page=1,model='',status='',project='',query='',generation=0,priceSearch='';

  function table(head,rows){
    return `<div class="usage-table" tabindex="0" aria-label="可横向滚动的数据表"><table><thead><tr>${head.map(item=>`<th scope="col">${h(item)}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${head.length}" class="usage-empty-cell">当前筛选范围没有记录</td></tr>`}</tbody></table></div>`;
  }
  function empty(title,description){return `<div class="usage-empty"><span aria-hidden="true">∿</span><strong>${h(title)}</strong><p>${h(description)}</p></div>`;}
  function badge(ok,label){return `<span class="usage-badge ${ok?'is-ok':'is-warn'}">${h(label)}</span>`;}
  function trend(data){
    if(!data.totals.metered)return empty(data.totals.requests?'尚未收到 Token 用量':'等待第一条请求',data.totals.requests?'已有请求记录，但供应商没有返回用量。可在请求明细查看状态。':'模型调用后，这里会展示每日用量趋势。');
    const rows=data.daily,max=Math.max(1,...rows.map(row=>row.tokens)),width=900,height=160,step=width/rows.length;
    const bars=rows.map((row,index)=>{
      const label=`${row.date} · ${full(row.tokens)} token · ${row.requests} 次请求 · ${row.metered} 次返回用量`;
      return row.tokens?`<rect x="${index*step+step*.13}" y="${height-row.tokens/max*height}" width="${step*.74}" height="${row.tokens/max*height}" rx="${Math.min(3,step*.15)}" tabindex="0" data-point="${h(label)}" aria-label="${h(label)}"><title>${h(label)}</title></rect>`:'';
    }).join('');
    const ticks=[...new Set([0,Math.floor((rows.length-1)/3),Math.floor((rows.length-1)*2/3),rows.length-1])];
    return `<p class="usage-chart-readout" data-chart-readout aria-live="polite">悬停或聚焦柱形，查看当日用量</p><div class="usage-trend"><div class="usage-yaxis"><span>${n(max)}</span><span>${n(max/2)}</span><span>0</span></div><div class="usage-plot"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="每日 Token 柱形图">${bars}</svg></div></div><div class="usage-xaxis">${ticks.map(index=>`<span>${h(rows[index].date.slice(5))}</span>`).join('')}</div>`;
  }
  function sessionTable(rows,detail=false){
    return table(['会话 / 项目','请求','Token','缓存读取',...(detail?['累计耗时']:[])],rows.map(row=>`<tr><td><strong>${h(row.title)}</strong><small>${h(row.project_title)}</small></td><td>${full(row.requests)}</td><td>${row.metered?n(row.tokens):'—'}<small>${row.metered}/${row.requests} 次返回用量</small></td><td>${row.cached_reported?n(row.cached):'—'}</td>${detail?`<td>${(row.duration_ms/1000).toFixed(1)} 秒</td>`:''}</tr>`));
  }
  function overview(data){
    const total=data.totals.input+data.totals.output;
    return `<div class="usage-overview-grid"><section class="usage-card usage-trend-card"><div class="usage-card-head"><h3>每日 Token</h3><span>输入 + 输出</span></div>${trend(data)}</section><section class="usage-card"><div class="usage-card-head"><h3>模型分布</h3><span>${data.model_totals.length} 个模型</span></div><div class="usage-model-ranks">${data.model_totals.slice(0,6).map((row,index)=>`<div><div class="usage-rank-label"><span class="usage-rank-index">${String(index+1).padStart(2,'0')}</span><strong title="${h(row.model)}">${h(row.model)}</strong><span>${row.metered?n(row.tokens):'未返回'}</span></div><div class="usage-track"><i style="width:${total?row.tokens/total*100:0}%"></i></div><small>${h(row.provider)} · ${row.requests} 次请求</small></div>`).join('')||empty('暂无模型用量','请求记录会按模型汇总。')}</div></section></div><section class="usage-card"><div class="usage-card-head"><h3>会话用量</h3><button class="pw-text-button" data-go="sessions">查看全部 →</button></div>${sessionTable(data.sessions.slice(0,8))}</section><div class="usage-price-shortcut"><div><strong>价格字典已就绪</strong><p>${Object.keys(data.price_catalog).length} 个模型 · 核查于 ${h(data.price_checked_at)} · 支持手动覆盖</p></div><button class="btn ghost" data-go="prices">管理模型价格 →</button></div>`;
  }
  function requests(data){
    return `<section class="usage-card">${table(['时间 / 状态','模型 / 供应商','会话','输入','输出','缓存读取','首字 / 总耗时','估算费用'],data.requests.map(row=>`<tr><td><time title="${h(new Date(row.created_at).toLocaleString())}">${h(when(row.created_at))}</time><small>${badge(row.status==='ok',row.status==='ok'?'成功':'失败')}${row.attempt?' · 重试 '+row.attempt:''}</small></td><td>${h(row.model)}<small>${h(row.provider)}</small></td><td>${h(row.title)}<small>${h(row.project_title)}</small></td><td title="${full(row.prompt_tokens)}">${n(row.prompt_tokens)}</td><td title="${full(row.output_tokens)}">${n(row.output_tokens)}</td><td title="${full(row.cached_tokens)}">${n(row.cached_tokens)}</td><td>${row.first_token_ms==null?'—':(row.first_token_ms/1000).toFixed(1)+' 秒'}<small>总计 ${(row.duration_ms/1000).toFixed(1)} 秒</small></td><td>${cost(row)}<small>${h(row.cost_reason)}</small></td></tr>`))}</section>`;
  }
  function contexts(data){
    return `<p class="usage-note">按字符数展示请求构成，比例不代表 Token 或上下文窗口占用。图片单独计数。</p><div class="usage-context-list">${data.requests.map(row=>{
      const context=row.context||{},values=Object.keys(components).map(key=>[key,Math.max(0,Number(context[key])||0)]),total=values.reduce((sum,[,value])=>sum+value,0);
      return `<details class="usage-card usage-context"><summary><span class="usage-context-title"><strong>${h(row.title)}</strong><span>${h(row.model)} · ${h(when(row.created_at))}</span></span><span class="usage-context-meta">${badge(row.status==='ok',row.status==='ok'?'成功':'失败')}<span>${row.prompt_tokens==null?'输入用量未返回':n(row.prompt_tokens)+' 输入 Token'}</span></span></summary><div class="usage-context-detail"><div class="usage-card-head"><h4>请求内容构成</h4><span>共 ${full(total)} 字符</span></div>${total?`<div class="usage-composition" role="img" aria-label="${h(values.map(([key,value])=>components[key]+' '+full(value)+' 字符').join('，'))}">${values.filter(([,value])=>value>0).map(([key,value])=>`<span class="usage-tone-${key}" style="width:${value/total*100}%" title="${components[key]} ${full(value)} 字符 · ${(value/total*100).toFixed(1)}%"></span>`).join('')}</div>`:'<p class="usage-note">没有可展示的文本构成记录</p>'}<div class="usage-legend">${values.map(([key,value])=>`<div><span><i class="usage-tone-${key}"></i>${components[key]}</span><strong>${full(value)} <small>字符</small></strong><span>${!value?'0%':value/total<.001?'＜0.1%':(value/total*100).toFixed(1)+'%'}</span></div>`).join('')}<p>图片：${full(context.images||0)} <span>· 未计入字符比例</span></p></div></div></details>`;
    }).join('')||empty('暂无上下文记录','当前筛选范围没有模型请求。')}</div>`;
  }
  function prices(data){
    const profiles=data.profile_prices;
    return `<section class="usage-card"><div class="usage-card-head"><h3>当前模型的计价方式</h3><span>官方接口自动匹配</span></div>${table(['模型档案','模型','来源','币种 / 百万 Token'],profiles.map(item=>`<tr><td>${h(item.name)}</td><td>${h(item.model)}</td><td>${badge(!!item.price,item.price?.origin==='manual'?'手动单价':item.price?'官方价格':'待配置')}</td><td>${item.price?`${item.price.currency} · 输入 ${item.price.input} / 输出 ${item.price.output}`:'未匹配官方接口或模型'}</td></tr>`))}<details class="usage-price-editor"><summary>自定义单价 / 恢复自动匹配</summary><p class="usage-note">转发服务、订阅折算或特殊优惠可在此设置。保存后按此单价重新估算该档案的记录。</p><form data-price class="usage-filters"><label>模型档案<select name="profile_id" class="control" required>${profiles.map(item=>`<option value="${h(item.id)}">${h(item.name)}</option>`).join('')}</select></label><label>币种<select name="currency" class="control"><option>USD</option><option>CNY</option></select></label>${[['input','输入',true],['output','输出',true],['cached','缓存读取',false],['cache_write','缓存写入',false]].map(([key,label,required])=>`<label>${label}<input class="control" type="number" name="${key}" min="0" step="any" ${required?'required':''} placeholder="${required?'必填':'未知可留空'}"></label>`).join('')}<button class="btn" ${profiles.length?'':'disabled'}>保存单价</button><button class="btn ghost" type="button" data-price-reset ${profiles.length?'':'disabled'}>恢复自动匹配</button><span data-price-status role="status"></span></form></details></section><section class="usage-card"><div class="usage-card-head"><div><h3>官方价格字典</h3><p class="usage-note">${h(data.price_checked_at)} 核查 · 单位：对应币种 / 百万 Token</p></div><input class="control usage-price-search" data-price-search type="search" aria-label="搜索价格字典" placeholder="搜索模型或供应商" value="${h(priceSearch)}"></div><p class="usage-note">仅估算标准 API 的 Token 费用；不同币种分别汇总。长上下文、促销、峰谷及缓存写入条件见各模型说明。</p><div data-price-table></div></section>`;
  }
  function priceTable(data,host){
    const rows=Object.values(data.price_catalog).filter(item=>(item.provider+' '+item.model+' '+item.aliases.join(' ')).toLowerCase().includes(priceSearch.toLowerCase()));
    const rates=item=>`${item.input} / ${item.output} / ${item.cached??'待核实'}`;
    host.innerHTML=table(['模型 / 供应商','币种','输入','输出','缓存读取','缓存写入','计价说明 / 来源'],rows.map(item=>`<tr><td><strong>${h(item.model)}</strong><small>${h(item.provider)}</small></td><td>${h(item.currency)}</td><td>${full(item.input)}</td><td>${full(item.output)}</td><td>${item.cached==null?'待核实':full(item.cached)}</td><td>${Array.isArray(item.cache_write)?item.cache_write.join('–'):item.cache_write??'—'}</td><td>${h(item.note||'标准 API 单价')}<small><a href="${h(item.source)}" target="_blank" rel="noopener noreferrer">官方定价 ↗</a></small>${item.peak?`<small>高峰输入 / 输出 / 缓存：${rates(item.peak)}</small>`:''}${(item.tiers||[]).map(tier=>`<small>输入 ＞${full(tier.above)}：${rates(tier)}</small>`).join('')}${(item.schedule||[]).map(rate=>`<small>${rate.from} 起：${rates(rate)}</small>`).join('')}</td></tr>`));
  }
  function bindPrices(data,host){
    const form=host.querySelector('[data-price]');if(!form)return;
    const fill=()=>{const price=data.profile_prices.find(item=>item.id===form.elements.profile_id.value)?.price||{};for(const key of ['input','output','cached','cache_write'])form.elements[key].value=typeof price[key]==='number'?price[key]:'';form.elements.currency.value=price.currency||'USD';};
    form.elements.profile_id.onchange=fill;fill();
    const save=async reset=>{
      const controls=[...form.querySelectorAll('button')];controls.forEach(button=>button.disabled=true);
      const payload=Object.fromEntries(new FormData(form));if(reset)payload.reset=true;
      try{await api('/api/usage/prices',payload);await render(host);}catch(error){if(form.isConnected)form.querySelector('[data-price-status]').textContent=error.message;}finally{controls.forEach(button=>button.disabled=false);}
    };
    form.onsubmit=event=>{event.preventDefault();save(false);};form.querySelector('[data-price-reset]').onclick=()=>save(true);
    priceTable(data,host.querySelector('[data-price-table]'));
    host.querySelector('[data-price-search]').oninput=event=>{priceSearch=event.target.value;priceTable(data,host.querySelector('[data-price-table]'));};
  }
  function exportCSV(data){
    const keys=['created_at','model','provider','title','status','attempt','prompt_tokens','output_tokens','cached_tokens','reasoning_tokens','duration_ms','first_token_ms','currency','cost_min','cost','cost_reason'];
    const cell=value=>'"'+String(value??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
    const csv='\uFEFF'+[keys,...data.requests.map(row=>keys.map(key=>row[key]))].map(row=>row.map(cell).join(',')).join('\r\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='模型调用用量.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function render(host){
    if(route()!=='usage')return;
    const ticket=++generation,focus=host.contains(document.activeElement)?document.activeElement:null;
    const focusKey=focus?.dataset.view?'[data-view="'+focus.dataset.view+'"]':focus?.hasAttribute('data-usage-refresh')?'[data-usage-refresh]':focus?.hasAttribute('data-usage-search')?'[data-usage-search]':null;
    if(!host.querySelector('.usage-page'))host.innerHTML='<div class="usage-page usage-loading" role="status"><div></div><div></div><div></div><p>正在读取用量…</p></div>';
    host.setAttribute('aria-busy','true');
    try{
      const data=await api('/api/usage?'+new URLSearchParams({days,page,model,status,project_id:project,q:query}));
      if(ticket!==generation||route()!=='usage')return;
      page=data.page;const totals=data.totals,known=totals.metered>0;
      const costs=Object.entries(totals.costs).map(([currency,value])=>cost({cost:value.max,cost_min:value.min,currency}));
      const metrics=[['Token 总量',known?n(totals.input+totals.output):'—',known?'输入 '+n(totals.input)+' · 输出 '+n(totals.output):totals.requests?'供应商未返回用量':'暂无调用记录'],['请求次数',n(totals.requests),`${totals.requests-totals.failed} 成功 · ${totals.failed} 失败`],['缓存读取',totals.cached_reported?n(totals.cached):'—','输入 Token 的子集'],['推理 Token',totals.reasoning_reported?n(totals.reasoning):'—','输出 Token 的子集'],['估算费用',costs.length?costs.join('<br>'):known?'待计价':'—',`${totals.priced} / ${totals.requests} 次可估算`]];
      host.innerHTML=`<section class="usage-page"><header class="usage-header"><div><p class="usage-eyebrow"><i></i>模型运行观测</p><h2>用量统计<span>每一次调用，都有迹可循</span></h2></div><button class="btn ghost" data-usage-refresh>↻ 刷新</button></header><nav class="usage-tabs" aria-label="用量统计视图">${Object.entries(labels).map(([id,label])=>`<button type="button" data-view="${id}" aria-pressed="${view===id}">${label}</button>`).join('')}</nav>${view==='prices'?'':`<div class="usage-filters"><label>时间范围<select data-days class="control">${[1,7,30,90,365].map(value=>`<option value="${value}" ${days===value?'selected':''}>${value===1?'最近 24 小时':'最近 '+value+' 天'}</option>`).join('')}</select></label><label>模型<select data-model class="control"><option value="">全部模型</option>${[...new Set(data.models.map(item=>item.model))].map(value=>`<option ${model===value?'selected':''}>${h(value)}</option>`).join('')}</select></label><label>项目<select data-project class="control"><option value="">全部项目</option>${S.tasks.map(item=>`<option value="${h(item.id)}" ${project===item.id?'selected':''}>${h(item.title)}</option>`).join('')}</select></label><label>状态<select data-status class="control"><option value="">全部请求</option><option value="ok" ${status==='ok'?'selected':''}>成功</option><option value="error" ${status==='error'?'selected':''}>失败</option></select></label><button class="btn ghost usage-export" data-export>↓ 导出当前页请求 CSV</button></div><div class="usage-kpis">${metrics.map(([label,value,hint],index)=>`<article class="${index===0?'usage-primary':''}"><small>${label}</small><strong>${value}</strong><span>${hint}</span></article>`).join('')}</div><div class="usage-coverage"><span><i class="${totals.requests&&!known?'is-warn':''}"></i>${totals.metered} / ${totals.requests} 次请求返回用量</span><p>未返回的数值显示「—」，不计入合计。费用按当前价格估算。</p>${Object.keys(totals.unpriced).length?`<span>${h(Object.entries(totals.unpriced).map(([reason,count])=>reason+' '+count+' 次').join(' · '))}</span>`:''}</div>`}<div data-usage-body></div></section>`;
      const body=host.querySelector('[data-usage-body]');
      body.innerHTML=view==='overview'?overview(data):view==='requests'?requests(data):view==='sessions'?`<section class="usage-card"><div class="usage-card-head"><h3>会话用量</h3><span>按 Token 排序 · 最多 200 条</span></div>${sessionTable(data.sessions,true)}</section>`:view==='context'?contexts(data):prices(data);
      if(['requests','context'].includes(view))body.insertAdjacentHTML('beforeend',`<div class="usage-pager"><span>共 ${full(totals.requests)} 次请求 · 每页 100 条</span><button class="btn ghost" data-prev ${page<=1?'disabled':''}>上一页</button><span>${page} / ${data.pages}</span><button class="btn ghost" data-next ${page>=data.pages?'disabled':''}>下一页</button></div>`);
      host.querySelectorAll('[data-view],[data-go]').forEach(button=>button.onclick=()=>{view=button.dataset.view||button.dataset.go;page=1;render(host);});
      for(const [selector,set] of [['days',value=>days=Number(value)],['model',value=>model=value],['project',value=>project=value],['status',value=>status=value]]){const input=host.querySelector('[data-'+selector+']');if(input)input.onchange=event=>{set(event.target.value);page=1;render(host);};}
      host.querySelector('[data-usage-refresh]').onclick=()=>render(host);
      for(const [selector,delta] of [['prev',-1],['next',1]]){const button=host.querySelector('[data-'+selector+']');if(button)button.onclick=()=>{page+=delta;render(host);};}
      const exporter=host.querySelector('[data-export]');if(exporter)exporter.onclick=()=>exportCSV(data);
      if(exporter){
        exporter.insertAdjacentHTML('beforebegin',`<label>搜索记录<input class="control" type="search" data-usage-search placeholder="模型、会话、项目 · Enter 搜索" value="${h(query)}"></label>`);
        const search=host.querySelector('[data-usage-search]');
        const apply=()=>{query=search.value.trim();page=1;render(host);};
        search.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();apply();}else if(event.key==='Escape'&&search.value){event.preventDefault();event.stopPropagation();search.value='';apply();}};
        search.oninput=()=>{if(!search.value&&query)apply();};
      }
      host.querySelectorAll('[data-point]').forEach(point=>{for(const type of ['focus','pointerenter'])point.addEventListener(type,()=>host.querySelector('[data-chart-readout]').textContent=point.dataset.point);});
      bindPrices(data,host);if(focusKey)host.querySelector(focusKey)?.focus({preventScroll:true});
    }catch(error){
      if(ticket!==generation||route()!=='usage')return;
      host.innerHTML=`<section class="usage-page"><div class="usage-empty" role="alert"><strong>用量统计暂时无法加载</strong><p>${h(error.message)}</p><button class="btn ghost" data-retry>重试</button></div></section>`;
      host.querySelector('[data-retry]').onclick=()=>render(host);
    }finally{if(ticket===generation)host.removeAttribute('aria-busy');}
  }
  return {render};
})();
