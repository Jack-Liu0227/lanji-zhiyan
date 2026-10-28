/* Local Mermaid preview and pixel-sized exports share the toolbox draft. */
const MermaidTool=(()=>{
  const samples={
    flow:'flowchart LR\n  A["研究问题"] --> B["文献检索"]\n  B --> C["证据整理"]\n  C --> D{"人工核查"}\n  D -->|通过| E["论文起草"]\n  D -->|修改| C',
    sequence:'sequenceDiagram\n  participant U as 研究者\n  participant A as 专家专家\n  participant L as 文献库\n  U->>A: 提出研究目标\n  A->>L: 检索文献\n  L-->>A: 返回证据\n  A-->>U: 提交综述\n  U->>A: 审查与反馈',
    gantt:'gantt\n  title 科研进度计划\n  dateFormat YYYY-MM-DD\n  section 文献研究\n  文献检索 :a, 2026-11-01, 7d\n  证据对比 :b, after a, 5d\n  section 写作投稿\n  初稿撰写 :c, after b, 10d\n  同行核查 :d, after c, 4d'
  };
  let libraryPromise=null,renderQueue=Promise.resolve();
  function library(){
    if(window.mermaid)return Promise.resolve(window.mermaid);
    if(!libraryPromise)libraryPromise=new Promise((resolve,reject)=>{
      const script=document.createElement('script');script.src='/vendor/mermaid/mermaid.min.js';
      script.onload=()=>window.mermaid?resolve(window.mermaid):reject(new Error('图表引擎未能加载'));
      script.onerror=()=>reject(new Error('图表引擎加载失败，请重新打开工具箱'));document.head.append(script);
    }).catch(error=>{libraryPromise=null;throw error;});return libraryPromise;
  }
  function read(){try{return JSON.parse(localStorage.getItem('rw-mermaid-tool'))||{};}catch{return {};}}
  function sanitize(value){
    const cleaned=DOMPurify.sanitize(value,{USE_PROFILES:{svg:true,svgFilters:true},ADD_TAGS:['style'],FORBID_TAGS:['foreignObject','script','image','iframe','object','embed','a']});
    const documentSvg=new DOMParser().parseFromString(cleaned,'image/svg+xml'),root=documentSvg.documentElement;
    if(root.localName!=='svg'||documentSvg.querySelector('parsererror'))throw new Error('图表生成的 SVG 无效');
    const localUrls=text=>text.replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi,(_,quote,url)=>url.trim().startsWith('#')?'url('+url.trim()+')':'none').replace(/@import[^;]+;/gi,'');
    for(const node of [root,...root.querySelectorAll('*')]){
      for(const attr of [...node.attributes]){
        if(/^on/i.test(attr.name))node.removeAttribute(attr.name);
        else if(/(?:^|:)href$|^src$/i.test(attr.name)&&!attr.value.startsWith('#'))node.removeAttribute(attr.name);
        else if(/url\(/i.test(attr.value))node.setAttribute(attr.name,localUrls(attr.value));
      }
      if(node.localName==='style')node.textContent=localUrls(node.textContent);
    }
    const box=root.viewBox.baseVal,width=box.width||parseFloat(root.getAttribute('width')),height=box.height||parseFloat(root.getAttribute('height'));
    if(!(width>0&&height>0&&Number.isFinite(width)&&Number.isFinite(height)))throw new Error('无法确定图表尺寸');
    root.setAttribute('xmlns','http://www.w3.org/2000/svg');root.setAttribute('width',String(width));root.setAttribute('height',String(height));root.style.maxWidth='none';root.style.background='transparent';
    return {root,width,height};
  }
  function mount(host){
    const saved=read(),state={code:saved.code||samples.flow,theme:['default','neutral','dark','forest'].includes(saved.theme)?saved.theme:'neutral',background:saved.background==='transparent'?'transparent':'theme',width:Number(saved.width)||3200,filename:saved.filename||'科研流程图',zoom:saved.zoom||'fit'};
    let sequence=0,timer=null,diagram=null,imageUrl='',disposed=false,rendering=false;
    const h=value=>esc(String(value??''));
    host.className='mermaid-tool';host.innerHTML=`<div class="mmt-toolbar"><label>图表主题<select class="control" data-mm-theme aria-label="图表主题"><option value="neutral">论文灰阶</option><option value="default">经典配色</option><option value="forest">森林绿</option><option value="dark">深色</option></select></label><label>背景<select class="control" data-mm-background aria-label="图表背景"><option value="theme">跟随主题</option><option value="transparent">透明</option></select></label><label>插入示例<select class="control" data-mm-example aria-label="Mermaid 示例"><option value="">选择图表示例</option><option value="flow">科研流程图</option><option value="sequence">协作时序图</option><option value="gantt">研究甘特图</option></select></label><button type="button" class="btn ghost" data-mm-render>更新预览</button></div><div class="mmt-workspace"><label class="mmt-code-label">Mermaid 源码<textarea class="control" data-mm-code aria-label="Mermaid 源码" rows="17" spellcheck="false">${h(state.code)}</textarea></label><section class="mmt-preview"><div class="mmt-preview-head"><span>图表预览</span><label>缩放<select class="control" data-mm-zoom aria-label="预览缩放"><option value="fit">适合窗口</option><option value="0.5">50%</option><option value="1">100%</option><option value="1.5">150%</option><option value="2">200%</option></select></label></div><div class="mmt-viewport" data-mm-viewport><p class="hint" data-mm-empty>正在准备预览…</p><img data-mm-image alt="Mermaid 图表预览" hidden></div></section></div><div class="mmt-export"><label>文件名<input class="control" data-mm-filename aria-label="图表文件名" maxlength="100" value="${h(state.filename)}"></label><label>PNG 宽度（像素）<input class="control" data-mm-width aria-label="PNG 导出宽度" type="number" min="320" max="16000" step="1" value="${h(state.width)}"></label><span class="hint" data-mm-size></span><button type="button" class="btn ghost" data-mm-svg disabled>导出 SVG</button><button type="button" class="btn" data-mm-png disabled>导出高清 PNG</button></div><p class="pw-error" data-mm-error role="alert"></p><p class="hint" data-mm-status role="status"></p>`;
    const el=name=>host.querySelector('[data-mm-'+name+']');
    const persist=()=>{try{localStorage.setItem('rw-mermaid-tool',JSON.stringify(state));}catch{}};
    function fail(error){el('error').textContent=error.message||String(error);}
    function background(){return state.background==='transparent'?null:state.theme==='dark'?'#1f2937':'#ffffff';}
    function svg(){
      if(!diagram)throw new Error('请先生成有效的图表预览');
      const root=diagram.root.cloneNode(true),color=background();
      if(color){const rect=root.ownerDocument.createElementNS('http://www.w3.org/2000/svg','rect');const box=root.viewBox.baseVal;for(const [key,value] of Object.entries({x:box.x,y:box.y,width:diagram.width,height:diagram.height,fill:color}))rect.setAttribute(key,String(value));root.insertBefore(rect,root.firstChild);}
      return new XMLSerializer().serializeToString(root);
    }
    function size(){
      const width=Number(state.width),height=diagram?Math.round(width*diagram.height/diagram.width):0;
      if(!Number.isInteger(width)||width<320||width>16000)throw new Error('PNG 宽度请输入 320 至 16000 的整数');
      if(height>16000||width*height>40000000)throw new Error('图表导出尺寸过大，请降低 PNG 宽度');
      return {width,height:Math.max(1,height)};
    }
    function paintSize(){if(!diagram){el('size').textContent='';return;}try{const dimensions=size();el('size').textContent=dimensions.width+' × '+dimensions.height+' px';}catch(error){el('size').textContent=error.message;}}
    function fit(){if(!diagram)return;el('image').style.width=(state.zoom==='fit'?Math.min(diagram.width,Math.max(1,el('viewport').clientWidth-32)):diagram.width*Number(state.zoom))+'px';}
    function clear(){diagram=null;el('image').hidden=true;el('empty').hidden=false;el('svg').disabled=el('png').disabled=true;paintSize();if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl='';}}
    function schedule(){persist();++sequence;clearTimeout(timer);clear();el('empty').textContent='等待更新预览…';el('error').textContent='';timer=setTimeout(render,400);}
    async function render(){
      clearTimeout(timer);const ticket=++sequence,code=el('code').value.trim().replace(/^```(?:mermaid)?[ \t]*\r?\n([\s\S]*?)\r?\n```$/i,'$1').trim();state.code=el('code').value;persist();clear();el('error').textContent='';
      if(!code){el('empty').textContent='粘贴 Mermaid 代码以开始预览';el('status').textContent='';return;}
      if(code.length>200000){fail(new Error('图表代码不能超过 200000 字符'));return;}
      rendering=true;el('empty').textContent='正在渲染图表…';el('status').textContent='正在更新预览…';
      const operation=async()=>{
        if(disposed||ticket!==sequence||!host.isConnected)return;
        const engine=await library(),stage=document.createElement('div');stage.className='mmt-stage';document.body.append(stage);
        try{
          engine.initialize({startOnLoad:false,securityLevel:'strict',htmlLabels:false,theme:state.theme,fontFamily:'Microsoft YaHei, Segoe UI, Arial, sans-serif',maxTextSize:200000,maxEdges:1000,arrowMarkerAbsolute:false,
            secure:['secure','securityLevel','startOnLoad','htmlLabels','flowchart','themeCSS','theme','themeVariables','fontFamily','maxTextSize','maxEdges'],flowchart:{htmlLabels:false,useMaxWidth:false}});
          const rendered=await engine.render('mm_'+crypto.randomUUID().replaceAll('-',''),code,stage);
          if(disposed||ticket!==sequence||!host.isConnected)return;
          diagram=sanitize(rendered.svg);imageUrl=URL.createObjectURL(new Blob([svg()],{type:'image/svg+xml;charset=utf-8'}));
          el('image').src=imageUrl;el('image').hidden=false;el('empty').hidden=true;el('svg').disabled=el('png').disabled=false;fit();paintSize();el('status').textContent='预览已更新 · 代码和设置自动保存在本机';
        }finally{stage.remove();}
      };
      const pending=renderQueue.then(operation);renderQueue=pending.catch(()=>{});
      try{await pending;}catch(error){if(!disposed&&ticket===sequence){clear();el('empty').textContent='请修正代码后重新预览';fail(new Error(String(error.message||error).replace(/<[^>]+>/g,'').slice(0,1200)));el('status').textContent='图表未生成，导出按钮暂不可用';}}finally{if(ticket===sequence)rendering=false;}
    }
    function download(blob,extension){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=(state.filename.trim().replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').replace(/[. ]+$/,'')||'科研流程图')+'.'+extension;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
    el('theme').value=state.theme;el('background').value=state.background;el('zoom').value=state.zoom;
    el('code').oninput=()=>{state.code=el('code').value;schedule();};el('render').onclick=render;
    for(const field of ['theme','background'])el(field).onchange=()=>{state[field]=el(field).value;schedule();};
    el('zoom').onchange=()=>{state.zoom=el('zoom').value;persist();fit();};
    el('width').oninput=()=>{state.width=el('width').value;persist();paintSize();};el('filename').oninput=()=>{state.filename=el('filename').value;persist();};
    el('example').onchange=async()=>{const key=el('example').value;el('example').value='';if(!samples[key])return;if(state.code.trim()&&!Object.values(samples).includes(state.code)&&!await uiConfirm('用示例替换当前 Mermaid 代码？',{ok:'替换代码',cancel:'保留当前代码',danger:true}))return;state.code=samples[key];el('code').value=state.code;schedule();};
    el('svg').onclick=()=>{try{if(rendering)return;download(new Blob([svg()],{type:'image/svg+xml;charset=utf-8'}),'svg');el('status').textContent='SVG 已导出，可无损缩放';}catch(error){fail(error);}};
    el('png').onclick=async()=>{
      if(rendering||!diagram)return;const button=el('png');button.disabled=true;el('error').textContent='';let url='';
      try{const dimensions=size(),source=svg(),image=new Image();url=URL.createObjectURL(new Blob([source],{type:'image/svg+xml;charset=utf-8'}));image.src=url;await image.decode();
        const canvas=document.createElement('canvas');canvas.width=dimensions.width;canvas.height=dimensions.height;const context=canvas.getContext('2d');if(!context)throw new Error('无法创建图片画布');
        context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';context.drawImage(image,0,0,canvas.width,canvas.height);
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('PNG 导出失败，请降低导出宽度');download(blob,'png');el('status').textContent='PNG 已导出 · '+dimensions.width+' × '+dimensions.height+' px';
      }catch(error){fail(error);}finally{if(url)URL.revokeObjectURL(url);if(!disposed)button.disabled=!diagram;}
    };
    const observer=new ResizeObserver(fit);observer.observe(el('viewport'));render();
    return {focus:()=>el('code').focus({preventScroll:true}),importCode(code,name){
      state.code=code;state.filename=name.replace(/\.[^.]+$/,'');el('code').value=code;el('filename').value=state.filename;schedule();
    },destroy(){disposed=true;++sequence;clearTimeout(timer);observer.disconnect();if(imageUrl)URL.revokeObjectURL(imageUrl);}};
  }
  return {mount};
})();
