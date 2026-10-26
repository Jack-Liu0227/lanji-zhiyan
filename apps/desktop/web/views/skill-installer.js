/* Community discovery and installation. Assistant configuration stays in its existing view. */
(() => {
  "use strict";
  const e = value => esc(String(value ?? ""));
  const sourceLink = value => {
    try { const url = new URL(value); return url.protocol === "https:" ? url.href : "#"; }
    catch (_) { return "#"; }
  };
  const style = document.createElement("style");
  style.textContent = `
    .si-tabs,.si-search,.si-target,.si-actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:12px 0}
    .si-search input,.si-source-input{flex:1;min-width:180px}
    .si-search select,.si-target select{max-width:290px}
    .si-results{display:grid;gap:9px;max-height:380px;overflow:auto;margin:12px 0}
    .si-result{padding:13px;border:1px solid var(--line);border-radius:9px;background:var(--panel2,var(--panel))}
    .si-result-head{display:flex;justify-content:space-between;gap:12px;align-items:center}
    .si-result h4{margin:0;font-size:14px;overflow-wrap:anywhere}
    .si-desc{font-size:12.5px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}
    .si-meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
    .si-preview{border-top:1px solid var(--line);padding-top:12px;margin-top:14px}
    .si-candidate{display:block;padding:12px;border:1px solid var(--line);border-radius:9px;margin:8px 0}
    .si-candidate summary{cursor:pointer;margin-top:8px;font-size:12px;color:var(--muted)}
    .si-candidate pre{max-height:250px;overflow:auto;white-space:pre-wrap;font-size:12px;line-height:1.6}
    .si-status{min-height:22px;overflow-wrap:anywhere}
    .si-source-list{display:grid;gap:9px;margin:12px 0}
    .si-source-row{padding:13px;border:1px solid var(--line);border-radius:9px}
    .si-source-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
    .si-source-buttons{display:flex;gap:7px;flex-wrap:wrap}
    .si-source-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}
    .si-source-fields label{display:grid;gap:5px;min-width:0;font-size:12.5px}
    .si-source-fields [hidden]{display:none}
    .si-source-fields .control{width:100%;box-sizing:border-box}
    .si-source-wide{grid-column:1/-1}
    .si-source-errors{white-space:pre-wrap;color:var(--warn,#d99a40);font-size:12.5px;line-height:1.6}
    .si-origin{font-size:11px;margin-right:7px}
    @media(max-width:600px){.si-source-fields{grid-template-columns:1fr}.si-source-buttons .btn{padding:5px 8px}}
    @media(max-width:600px){.si-search,.si-target{align-items:stretch;flex-direction:column}.si-search select,.si-target select{max-width:none;width:100%}.si-search input{min-width:0}.si-result-head{align-items:flex-start}}
  `;
  document.head.appendChild(style);

  function open({dialog, projectId = "", projects = [], onInstalled, initialTab = "community"}) {
    const initialProject = projects.find(project => project.id === projectId && project.base_dir);
    let sources = [], adapters = [], defaults = [], currentPreview = null, generation = 0, busy = false, sourcesLoaded = false;
    const host = dialog("安装技能", `
      <p class="hint">内置社区源可直接搜索。也可以导入社区链接、GitHub 仓库、本地技能目录或压缩包。</p>
      <div class="si-tabs">
        <button class="btn" data-si-tab="community" aria-pressed="true">社区搜索</button>
        <button class="btn ghost" data-si-tab="import" aria-pressed="false">链接 / 本地导入</button>
        <button class="btn ghost" data-si-tab="sources" aria-pressed="false">来源管理</button>
      </div>
      <section data-si-community>
        <form class="si-search" data-si-search-form>
          <select class="control" data-si-provider aria-label="技能源" disabled><option>正在读取内置源…</option></select>
          <input class="control" data-si-query aria-label="搜索社区技能" placeholder="搜索技能，例如：论文、pdf、writing" minlength="2" maxlength="200" required>
          <button class="btn" type="submit" data-si-search disabled>搜索</button>
        </form>
        <p class="hint" data-si-source-description></p>
        <p class="si-source-errors" data-si-source-errors role="status" aria-live="polite"></p>
        <div class="si-results" data-si-results></div>
      </section>
      <section data-si-sources hidden>
        <p class="hint">默认搜索所有启用的来源。编辑或删除会保存到本机，已安装的技能会保留。</p>
        <div class="si-actions">
          <button class="btn" data-si-source-add disabled>添加来源</button>
          <button class="btn ghost" data-si-source-restore disabled>恢复内置来源</button>
        </div>
        <div class="si-source-list" data-si-source-list></div>
        <section data-si-source-editor hidden></section>
      </section>
      <section data-si-import hidden>
        <form class="si-search" data-si-import-form>
          <input class="control si-source-input" data-si-source aria-label="技能链接或本地路径" placeholder="https://skills.sh/作者/仓库/技能 或本地路径" required>
          <button class="btn" type="submit" data-si-preview-button>读取技能</button>
        </form>
        <p class="hint">支持 skills.sh、SkillHub、SkillsMP 详情链接，GitHub 仓库 / 技能目录、owner/repo@skill，以及本地目录、ZIP、.skill、tar.gz。</p>
      </section>
      <p class="hint si-status" data-si-status role="status" aria-live="polite"></p>
      <section class="si-preview" data-si-preview hidden></section>
    `, (overlay, close) => {
      const field = selector => overlay.querySelector(selector);
      const status = text => { field("[data-si-status]").textContent = text; };
      const error = value => { field("[data-error]").textContent = value?.message || String(value || ""); };
      function setBusy(value) {
        busy = value;
        overlay.querySelectorAll("[data-si-search],[data-si-preview-button],[data-si-result-preview],[data-si-install],[data-si-provider],[data-si-source-action],[data-si-source-add],[data-si-source-restore],[data-si-source-save]").forEach(button => {
          button.disabled = value || button.hasAttribute("data-si-unavailable");
        });
        field("[data-si-search]").disabled = value || !sourcesLoaded || !sources.some(source => source.enabled);
        field("[data-si-provider]").disabled = value || !sourcesLoaded;
      }
      function clearPreview() {
        currentPreview = null;
        field("[data-si-preview]").hidden = true;
        field("[data-si-preview]").innerHTML = "";
      }
      function sourceDescription() {
        if (field("[data-si-provider]").value === "all") {
          const count = sources.filter(source => source.enabled).length;
          field("[data-si-source-description]").textContent = count
            ? `搜索全部 ${count} 个已启用来源，也可以选择单个来源。`
            : "没有启用的来源，请到“来源管理”中启用或添加。";
          return;
        }
        const source = sources.find(item => item.id === field("[data-si-provider]").value);
        field("[data-si-source-description]").innerHTML = source
          ? `${e(source.description)} <a href="${e(sourceLink(source.url))}" target="_blank" rel="noopener noreferrer">访问 ${e(source.name)}</a>` : "";
      }
      function showSources(result, reset = false) {
        sources = result.sources;
        adapters = result.adapters || adapters;
        defaults = result.defaults || defaults;
        sourcesLoaded = true;
        const previous = field("[data-si-provider]").value;
        field("[data-si-provider]").innerHTML = '<option value="all">全部来源</option>' +
          sources.filter(source => source.enabled).map(source => `<option value="${e(source.id)}">${e(source.name)}</option>`).join("");
        field("[data-si-provider]").value = sources.some(source => source.enabled && source.id === previous) ? previous : "all";
        sourceDescription(); renderSources(); setBusy(busy);
        if (reset) {
          ++generation; clearPreview();
          field("[data-si-results]").innerHTML = "";
          field("[data-si-source-errors]").textContent = "";
        }
      }
      async function changeSources(path, body, message) {
        if (busy) return;
        error(""); setBusy(true);
        try {
          const result = await api(path, body);
          if (!overlay.isConnected) return;
          showSources(result, true);
          field("[data-si-source-editor]").hidden = true;
          status(message);
        } catch (failure) { if (overlay.isConnected) error(failure); }
        finally { if (overlay.isConnected) setBusy(false); }
      }
      function renderSources() {
        field("[data-si-source-list]").innerHTML = sources.length ? sources.map(source => `
          <article class="si-source-row" data-si-source-id="${e(source.id)}">
            <div class="si-source-head">
              <strong>${e(source.name)} <span class="pill">${source.enabled ? "已启用" : "已停用"}</span> <span class="si-meta">${source.builtin ? "内置" : "自定义"}</span></strong>
              <div class="si-source-buttons">
                <button class="btn ghost" data-si-source-action="edit" data-id="${e(source.id)}">编辑</button>
                <button class="btn ghost" data-si-source-action="toggle" data-id="${e(source.id)}">${source.enabled ? "停用" : "启用"}</button>
                <button class="btn ghost" data-si-source-action="delete" data-id="${e(source.id)}">删除</button>
              </div>
            </div>
            <p class="si-meta">${e(source.description)}</p>
            <p class="si-meta">${e(source.api_url)}</p>
          </article>`).join("") : '<p class="hint">尚未配置来源，可以添加来源或恢复内置来源。</p>';
        overlay.querySelectorAll("[data-si-source-action]").forEach(button => button.onclick = () => {
          const source = sources.find(item => item.id === button.dataset.id);
          if (button.dataset.siSourceAction === "edit") editSource(source);
          if (button.dataset.siSourceAction === "toggle") changeSources("/api/capabilities/skill-sources", {id: source.id, enabled: !source.enabled}, source.enabled ? "来源已停用。" : "来源已启用。");
          if (button.dataset.siSourceAction === "delete") changeSources("/api/capabilities/skill-sources/delete", {id: source.id}, "来源已删除，已安装的技能保留。");
        });
      }
      function editSource(source = {}) {
        if (busy || !sourcesLoaded) return;
        error(""); status("");
        const editor = field("[data-si-source-editor]");
        editor.hidden = false;
        editor.innerHTML = `<form data-si-source-form>
          <h4>${source.id ? "编辑来源" : "添加来源"}</h4>
          <div class="si-source-fields">
            <label>来源名称<input class="control" data-si-config="name" maxlength="80" value="${e(source.name || "")}" required></label>
            <label>来源类型<select class="control" data-si-config="adapter">${adapters.map(adapter => `<option value="${e(adapter.id)}" ${adapter.id === (source.adapter || "index") ? "selected" : ""}>${e(adapter.name)}</option>`).join("")}</select></label>
            <label class="si-source-wide">网站地址<input class="control" type="url" data-si-config="url" value="${e(source.url || "")}" placeholder="https://example.com" required></label>
            <label class="si-source-wide">搜索接口 / 技能索引地址<input class="control" type="url" data-si-config="api_url" value="${e(source.api_url || "")}" placeholder="https://example.com/.well-known/skills/index.json" required></label>
            <label class="si-source-wide" data-si-download-field ${source.adapter === "skillhub" ? "" : "hidden"}>下载地址模板<input class="control" data-si-config="download_url" value="${e(source.download_url || "")}" placeholder="https://example.com/api/v1/download?slug={slug}"></label>
            <label class="si-source-wide">说明<input class="control" data-si-config="description" maxlength="1000" value="${e(source.description || "")}"></label>
          </div>
          <label><input type="checkbox" data-si-config="enabled" ${source.enabled !== false ? "checked" : ""}> 启用，参与全部来源搜索</label>
          <div class="si-actions"><button class="btn" type="submit" data-si-source-save>保存来源</button><button class="btn ghost" type="button" data-si-source-cancel>取消</button></div>
        </form>`;
        editor.querySelector("[data-si-source-cancel]").onclick = () => { editor.hidden = true; };
        const configField = key => editor.querySelector('[data-si-config="' + key + '"]');
        configField("adapter").onchange = event => {
          const preset = defaults.find(item => item.adapter === event.target.value);
          field("[data-si-download-field]").hidden = event.target.value !== "skillhub";
          if (!source.id && preset) {
            for (const key of ["url", "api_url", "download_url"]) configField(key).value = preset[key] || "";
          }
        };
        editor.querySelector("form").onsubmit = event => {
          event.preventDefault();
          const body = source.id ? {id: source.id} : {};
          for (const key of ["name", "adapter", "url", "api_url", "download_url", "description"]) body[key] = configField(key).value.trim();
          body.enabled = configField("enabled").checked;
          changeSources("/api/capabilities/skill-sources", body, "来源已保存，搜索会使用新配置。");
        };
        configField("name").focus();
      }
      async function preview(source, provider = "", selection = null) {
        if (busy) return;
        const ticket = ++generation;
        error(""); clearPreview(); setBusy(true); status("正在下载并检查技能目录…");
        try {
          const result = await api("/api/capabilities/skill-preview", {source, provider, selection});
          if (!overlay.isConnected || ticket !== generation) return;
          currentPreview = result;
          const target = field("[data-si-preview]");
          target.hidden = false;
          target.innerHTML = `
            <h4>选择要安装的技能 <span class="pill">${result.skills.length}</span></h4>
            <p class="si-meta">${e(result.source)}</p>
            <div class="si-target">
              <label>安装范围 <select class="control" data-si-scope aria-label="安装范围">
                <option value="user" ${initialProject ? "" : "selected"}>用户级，所有项目可用</option>
                <option value="project" ${initialProject ? "selected" : ""}>项目级，仅存放在所选项目</option>
              </select></label>
              <select class="control" data-si-project aria-label="安装到项目" ${initialProject ? "" : "hidden"}>
                <option value="">选择项目</option>
                ${projects.filter(item => item.base_dir).map(item => `<option value="${e(item.id)}" ${item.id === initialProject?.id ? "selected" : ""}>${e(item.title)}</option>`).join("")}
              </select>
            </div>
            <div data-si-candidates>${result.skills.map(skill => `
              <div class="si-candidate">
                <label><input type="checkbox" data-si-skill value="${e(skill.name)}" ${result.skills.length === 1 ? "checked" : ""}> <strong>${e(skill.name)}</strong> <span class="si-meta">${skill.files} 个文件</span></label>
                <p class="si-desc">${e(skill.description || "暂无说明")}</p>
                <details><summary>查看 SKILL.md</summary><pre>${e(skill.content)}</pre></details>
              </div>`).join("")}</div>
            <div class="si-actions"><button class="btn" data-si-install>安装所选技能</button><span class="hint">完整安装技能目录及附带资源。</span></div>
          `;
          field("[data-si-scope]").onchange = event => { field("[data-si-project]").hidden = event.target.value !== "project"; };
          field("[data-si-install]").onclick = install;
          status("技能已就绪，请选择安装范围后安装。");
        } catch (failure) { if (overlay.isConnected && ticket === generation) { error(failure); status(""); } }
        finally { if (overlay.isConnected && ticket === generation) setBusy(false); }
      }
      async function install() {
        if (busy || !currentPreview) return;
        const skills = [...overlay.querySelectorAll("[data-si-skill]:checked")].map(input => input.value);
        const scope = field("[data-si-scope]").value;
        const selectedProject = field("[data-si-project]").value;
        if (!skills.length) { error("请至少选择一个要安装的技能"); return; }
        if (scope === "project" && !selectedProject) { error("请选择已设置文件夹的项目"); return; }
        error(""); setBusy(true); status("正在安装技能…");
        try {
          const result = await api("/api/capabilities/skill-install", {
            token: currentPreview.token, skills, scope, ...(scope === "project" ? {project_id: selectedProject} : {})
          });
          if (overlay.isConnected) {
            const target = field("[data-si-preview]");
            target.innerHTML = `<h4>已安装 ${result.installed.length} 个技能</h4>${result.installed.map(item => `<p><strong>${e(item.name)}</strong><br><span class="si-meta">${e(item.path)}</span></p>`).join("")}<button class="btn" data-si-done>完成</button>`;
            field("[data-si-done]").onclick = close;
            status("安装完成，已保存完整技能目录。");
          }
          try { await onInstalled?.(); }
          catch (_) { if (overlay.isConnected) status("安装已完成，刷新技能列表后即可看到。"); }
        } catch (failure) { if (overlay.isConnected) { error(failure); status("安装未完成，请检查提示后重试。"); } }
        finally { if (overlay.isConnected) setBusy(false); }
      }
      field("[data-si-search-form]").onsubmit = async event => {
        event.preventDefault();
        if (busy) return;
        const query = field("[data-si-query]").value.trim(), provider = field("[data-si-provider]").value;
        if (query.length < 2) { error("请输入至少 2 个字符的搜索词"); return; }
        const ticket = ++generation;
        clearPreview(); error(""); setBusy(true); status("正在搜索社区技能…");
        field("[data-si-results]").innerHTML = "";
        field("[data-si-source-errors]").textContent = "";
        try {
          const result = await api("/api/capabilities/skill-search?" + new URLSearchParams({provider, q: query}));
          if (!overlay.isConnected || ticket !== generation) return;
          field("[data-si-results]").innerHTML = result.skills.map((skill, index) => `
            <article class="si-result">
              <div class="si-result-head"><h4>${e(skill.name)}</h4><button class="btn ghost" data-si-result-preview="${index}" ${skill.source ? "" : "disabled data-si-unavailable"}>预览安装</button></div>
              <p class="si-desc">${e(skill.description)}</p>
              <div class="si-meta"><span class="pill si-origin">${e(skill.provider_name || sources.find(source => source.id === skill.provider)?.name || "")}</span>${e(skill.author)}${skill.downloads != null ? ` · ${Number(skill.downloads).toLocaleString()} 次安装 / 下载` : ""} · <a href="${e(sourceLink(skill.url))}" target="_blank" rel="noopener noreferrer">来源详情</a></div>
            </article>`).join("");
          overlay.querySelectorAll("[data-si-result-preview]").forEach(button => {
            button.onclick = () => {
              const skill = result.skills[Number(button.dataset.siResultPreview)];
              preview(skill.source, skill.provider || provider, skill);
            };
          });
          const failures = result.errors || [];
          field("[data-si-source-errors]").textContent = failures.map(failure => failure.name + "：" + failure.error).join("\n");
          status(result.skills.length ? `找到 ${result.skills.length} 个技能，选择“预览安装”查看内容。${failures.length ? " 部分来源失败，其余结果已显示。" : ""}`
            : failures.length ? "来源搜索失败，请检查来源配置或稍后重试。" : result.message || "未找到匹配技能，可以更换关键词或技能源。");
        } catch (failure) { if (overlay.isConnected && ticket === generation) { error(failure); status(""); } }
        finally { if (overlay.isConnected && ticket === generation) setBusy(false); }
      };
      field("[data-si-import-form]").onsubmit = event => { event.preventDefault(); preview(field("[data-si-source]").value.trim()); };
      field("[data-si-provider]").onchange = () => {
        ++generation; busy = false; setBusy(false); clearPreview(); error(""); status("");
        field("[data-si-results]").innerHTML = ""; sourceDescription();
        field("[data-si-source-errors]").textContent = "";
      };
      function switchTab(tabName) {
        if (busy) return;
        for (const name of ["community", "import", "sources"]) field("[data-si-" + name + "]").hidden = name !== tabName;
        overlay.querySelectorAll("[data-si-tab]").forEach(tab => {
          const active = tab.dataset.siTab === tabName;
          tab.classList.toggle("ghost", !active); tab.setAttribute("aria-pressed", String(active));
        });
        const title = tabName === "sources" ? "技能源管理" : "安装技能";
        field(".cap-dialog-head h3").textContent = title;
        field(".cap-dialog").setAttribute("aria-label", title);
        error(""); status(""); clearPreview();
      }
      overlay.querySelectorAll("[data-si-tab]").forEach(button => button.onclick = () => switchTab(button.dataset.siTab));
      field("[data-si-source-add]").onclick = () => editSource();
      field("[data-si-source-restore]").onclick = () => changeSources("/api/capabilities/skill-sources/restore", {}, "内置来源已恢复为默认配置。");
      switchTab(initialTab);
      api("/api/capabilities/skill-sources").then(result => {
        if (!overlay.isConnected) return;
        showSources(result);
      }).catch(failure => { if (overlay.isConnected) error(failure); });
    });
    return host;
  }
  window.SkillInstaller = {open};
})();
