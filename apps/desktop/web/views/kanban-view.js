// -*- coding: utf-8 -*-
/**
 * 科研进度与计划看板 (Research Kanban Board)
 * 遵循 Obsidian Synapse 高保真科研交互规范
 * 提供：四列状态看板、拖拽流转、十步科研工作流管道视图、排期概览、专家指派与阶段联动
 */

(function () {
  let kbState = {
    view: 'kanban', // 'kanban' | 'pipeline' | 'timeline'
    filterPriority: 'ALL',
    filterAssistant: 'ALL',
    searchQuery: '',
    data: null,
    loading: false,
    draggedId: null
  };

  const STATUS_CONFIG = {
    TODO: { label: '待办计划', icon: '📋', color: '#38bdf8' },
    IN_PROGRESS: { label: '正在进行', icon: '🚀', color: '#8b5cf6' },
    REVIEW: { label: '审核验证', icon: '🔍', color: '#f59e0b' },
    DONE: { label: '已归档完成', icon: '✅', color: '#10b981' }
  };

  const PRIORITY_LABELS = {
    P0: 'P0 紧急',
    P1: 'P1 重要',
    P2: 'P2 普通'
  };

  async function fetchKanbanData(taskId) {
    kbState.loading = true;
    try {
      const tid = taskId || window.curTask || '';
      const res = await window.api(`/api/kanban/overview?task_id=${encodeURIComponent(tid)}`);
      kbState.data = res;
      if (res.task && res.task.id && !window.curTask) {
        window.curTask = res.task.id;
        localStorage.setItem('rw-task', window.curTask);
      }
    } catch (err) {
      console.error('Failed to load kanban overview:', err);
    } finally {
      kbState.loading = false;
    }
  }

  // embedded=true 时嵌进「项目空间」当一块内容：不再自己画页头和项目下拉
  // （外面已经有项目选择器和阶段），其余 KPI/三视图/拖拽/弹窗原样保留。
  let kbEmbed = false;

  // 记住这次画在哪个容器里：模态框保存后要重画回这块，不能再去抓 #main
  // （嵌进项目空间后 #main 是整个页面，抓它就等于把页面换成一块看板）。
  let kbHost = null;

  window.renderKanban = async function (container, opts) {
    if (!container) return;
    kbHost = container;
    kbEmbed = !!(opts && opts.embedded);
    await fetchKanbanData(window.curTask);      // 进度必须现取：缓存里那套是上次进来时的
    renderBoard(container);
    kbStartLive(container);
  };

  // 页面开着的时候别处（科研台记一步、写笔记、导文献）会改数：挂个 8s 轻轮询，
  // 数字真变了才整块重画。弹窗开着 / 正在拖拽时跳过，免得把用户手上的操作冲掉。
  // ponytail: 8s 轮询，不是 SSE —— 写入也可能来自别的进程；真嫌慢再换推送。
  let kbLive = null;
  function kbStopLive() { if (kbLive) { clearInterval(kbLive); kbLive = null; } }

  function kbSig() {
    const d = kbState.data || {}, s = d.stats || {};
    return [s.stage_progress, s.stages_done, s.completion_rate, s.total, s.done,
      s.papers_count, s.notes_count, d.current_stage ? d.current_stage.key : '',
      (d.todos || []).map(t => t.id + ':' + t.status).join(',')].join('|');
  }

  function kbStartLive(container) {
    kbStopLive();
    kbLive = setInterval(async () => {
      if (!container.isConnected) return kbStopLive();          // 切走了就别再画
      const modal = container.querySelector('#kbModalContainer');
      if ((modal && modal.children.length) || kbState.draggedId) return;
      const before = kbSig();
      await fetchKanbanData(window.curTask);
      if (container.isConnected && kbSig() !== before) renderBoard(container);
    }, 8000);
  }

  function renderBoard(container) {
    const d = kbState.data || {
      task: null,
      tasks: [],
      stats: { total: 0, todo: 0, in_progress: 0, review: 0, done: 0, completion_rate: 0, papers_count: 0, notes_count: 0 },
      stages: [],
      assistants: [],
      todos: []
    };

    const taskTitle = d.task ? d.task.title : (window.S && window.S.tasks && window.S.tasks.length ? '请选择项目' : '暂无项目');
    const stageNow = d.current_stage ? d.current_stage.label : '创新点构想';
    const stageRate = d.stats.stage_progress != null ? d.stats.stage_progress : 0;

    container.innerHTML = `
      <div class="kb-root">
        <!-- 顶部概览与指标卡片 -->
        <section class="kb-header">
          <div class="kb-title-row">
            <div class="kb-title-group">
              ${kbEmbed ? '' : `
              <h2 class="kb-main-title">
                <span>📋</span> 科研进度与计划看板
              </h2>
              <select class="kb-proj-select" id="kbProjSelect">
                ${(d.tasks || []).map(t => `
                  <option value="${t.id}" ${d.task && t.id === d.task.id ? 'selected' : ''}>
                    ${escapeHtml(t.title)}
                  </option>
                `).join('')}
              </select>`}
              <div class="kb-stage-pill" title="当前课题阶段">
                <span class="dot"></span>
                <span>阶段：${escapeHtml(stageNow)}</span>
              </div>
            </div>
            <div class="kb-header-actions">
              <button class="btn sm" id="kbBtnNewTask">＋ 新建任务</button>
              <button class="btn sm ghost" id="kbBtnImportWf" title="一键导入科研十步工作流待办">⚡ 导入工作流阶段</button>
              <button class="btn sm ghost" id="kbBtnRefresh" title="刷新看板数据">🔄</button>
            </div>
          </div>

          <!-- KPI 指标概览条 -->
          <div class="kb-kpi-grid">
            <div class="kb-kpi-card">
              <div class="kb-kpi-lbl">
                <span>科研阶段进度</span>
                <span style="color:var(--kb-sky)">${stageRate}%</span>
              </div>
              <div class="kb-kpi-val">
                ${d.stats.stages_done || 0} <span class="kb-kpi-sub">/ ${d.stats.stage_total || 10} 阶段已完成</span>
              </div>
              <div class="kb-progress-bar">
                <div class="kb-progress-fill" style="width: ${stageRate}%"></div>
              </div>
              <div class="kb-kpi-sub" style="margin-top:4px">待办 ${d.stats.done || 0}/${d.stats.total || 0} 项</div>
            </div>

            <div class="kb-kpi-card">
              <div class="kb-kpi-lbl">
                <span>进行与审核中</span>
                <span style="color:var(--kb-violet)">Active</span>
              </div>
              <div class="kb-kpi-val">
                ${d.stats.in_progress + d.stats.review} <span class="kb-kpi-sub">(${d.stats.in_progress} 进行中 · ${d.stats.review} 待审核)</span>
              </div>
              <div class="kb-kpi-sub" style="margin-top:4px">
                待办储备：${d.stats.todo} 项 ${d.stats.p0_count ? `· <span style="color:var(--kb-rose)">P0紧急 ${d.stats.p0_count} 项</span>` : ''}
              </div>
            </div>

            <div class="kb-kpi-card">
              <div class="kb-kpi-lbl">
                <span>智能专家协同</span>
                <span style="color:var(--kb-green)">Agents</span>
              </div>
              <div class="kb-kpi-val">
                ${(d.assistants || []).length} <span class="kb-kpi-sub">位专家在席</span>
              </div>
              <div class="kb-kpi-sub" style="margin-top:4px">
                智研管家 · 文献精读 · 写作润色
              </div>
            </div>

            <div class="kb-kpi-card">
              <div class="kb-kpi-lbl">
                <span>课题数字资产</span>
                <span style="color:var(--kb-amber)">Library</span>
              </div>
              <div class="kb-kpi-val">
                ${d.stats.papers_count} <span class="kb-kpi-sub">篇文献 · ${d.stats.notes_count} 篇大笔记</span>
              </div>
              <div class="kb-kpi-sub" style="margin-top:4px">
                知识拓扑星图已链接
              </div>
            </div>
          </div>
        </section>

        <!-- 工具栏：视图切换与多维筛选 -->
        <div class="kb-toolbar">
          <div class="kb-view-tabs">
            <button class="kb-view-tab ${kbState.view === 'kanban' ? 'active' : ''}" data-view="kanban">
              📋 待办状态看板
            </button>
            <button class="kb-view-tab ${kbState.view === 'pipeline' ? 'active' : ''}" data-view="pipeline">
              🔬 十步科研流程
            </button>
            <button class="kb-view-tab ${kbState.view === 'timeline' ? 'active' : ''}" data-view="timeline">
              📅 排期概览
            </button>
          </div>

          <div class="kb-filters">
            <input type="text" class="kb-search-input" id="kbSearchInput" placeholder="按标题 / 描述搜索..." value="${escapeHtml(kbState.searchQuery)}">
            <select class="kb-filter-select" id="kbFilterPriority">
              <option value="ALL" ${kbState.filterPriority === 'ALL' ? 'selected' : ''}>全部优先级</option>
              <option value="P0" ${kbState.filterPriority === 'P0' ? 'selected' : ''}>P0 紧急</option>
              <option value="P1" ${kbState.filterPriority === 'P1' ? 'selected' : ''}>P1 重要</option>
              <option value="P2" ${kbState.filterPriority === 'P2' ? 'selected' : ''}>P2 普通</option>
            </select>
            <select class="kb-filter-select" id="kbFilterAssistant">
              <option value="ALL" ${kbState.filterAssistant === 'ALL' ? 'selected' : ''}>全部执行人</option>
              <option value="智研管家" ${kbState.filterAssistant === '智研管家' ? 'selected' : ''}>🤖 智研管家</option>
              ${(d.assistants || []).map(a => `
                <option value="${a.name}" ${kbState.filterAssistant === a.name ? 'selected' : ''}>
                  👨‍🔬 ${escapeHtml(a.name)}
                </option>
              `).join('')}
            </select>
          </div>
        </div>

        <!-- 视图主显示区 -->
        <main class="kb-main-view" id="kbMainView" style="display:flex; flex-direction:column; flex:1">
          ${renderCurrentViewContent(d)}
        </main>
      </div>

      <!-- 任务创建/编辑模态框容器 -->
      <div id="kbModalContainer"></div>
    `;

    bindBoardEvents(container, d);
  }

  function renderCurrentViewContent(d) {
    if (kbState.view === 'pipeline') {
      return renderPipelineView(d);
    }
    if (kbState.view === 'timeline') {
      return renderTimelineView(d);
    }
    return renderKanbanView(d);
  }

  // ---- 模式一：四列看板视图 ----
  function renderKanbanView(d) {
    const rawTodos = d.todos || [];
    const filtered = rawTodos.filter(t => {
      if (kbState.filterPriority !== 'ALL' && t.priority !== kbState.filterPriority) return false;
      if (kbState.filterAssistant !== 'ALL' && t.assigned_assistant !== kbState.filterAssistant) return false;
      if (kbState.searchQuery) {
        const q = kbState.searchQuery.toLowerCase();
        const mTitle = (t.title || '').toLowerCase().includes(q);
        const mDesc = (t.description || '').toLowerCase().includes(q);
        if (!mTitle && !mDesc) return false;
      }
      return true;
    });

    const cols = ['TODO', 'IN_PROGRESS', 'REVIEW', 'DONE'];

    return `
      <div class="kb-board-container" id="kbColumnsContainer">
        ${cols.map(status => {
          const cfg = STATUS_CONFIG[status];
          const colTodos = filtered.filter(t => t.status === status);
          return `
            <div class="kb-col" data-col="${status}">
              <div class="kb-col-head">
                <h3 class="kb-col-title">
                  <span>${cfg.icon}</span>
                  <span>${cfg.label}</span>
                  <span class="kb-badge">${colTodos.length}</span>
                </h3>
                <button class="kb-col-btn-add" data-add-status="${status}" title="在此列快速添加任务">＋</button>
              </div>
              <div class="kb-col-body" data-drop-col="${status}">
                ${colTodos.length === 0 ? `
                  <div class="kb-empty-col">暂无 ${cfg.label} 任务<br><span style="font-size:11px;opacity:0.7">可拖拽卡片至此</span></div>
                ` : colTodos.map(todo => renderTodoCard(todo, status)).join('')}
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  function renderTodoCard(t, colStatus) {
    const prioCls = `kb-prio-${t.priority || 'P1'}`;
    const prioLabel = PRIORITY_LABELS[t.priority] || t.priority || 'P1';
    const isDone = colStatus === 'DONE';

    return `
      <div class="kb-card ${isDone ? 'done' : ''}" draggable="true" data-todo-id="${t.id}">
        <div class="kb-card-head">
          <input type="checkbox" class="kb-card-check" data-act="toggle" data-id="${t.id}" ${isDone ? 'checked' : ''} title="${isDone ? '标记为未完成' : '标记为已完成'}">
          <h4 class="kb-card-title">${escapeHtml(t.title)}</h4>
        </div>

        ${t.description ? `<p class="kb-card-desc">${escapeHtml(t.description)}</p>` : ''}

        <div class="kb-card-tags">
          <span class="kb-prio-badge ${prioCls}">${prioLabel}</span>
          ${t.assigned_assistant ? `
            <span class="kb-agent-tag" title="指派助手/专家">
              <span>🤖</span> ${escapeHtml(t.assigned_assistant)}
            </span>
          ` : ''}
          ${t.stage ? `
            <span class="kb-stage-pill" style="padding:1px 6px;font-size:10px">${escapeHtml(t.stage)}</span>
          ` : ''}
          ${t.due_date ? `
            <span class="kb-due-tag" title="截止日期">
              <span>📅</span> ${escapeHtml(t.due_date)}
            </span>
          ` : ''}
        </div>

        <div class="kb-card-foot">
          <div class="kb-card-actions">
            ${colStatus !== 'TODO' ? `<button class="kb-btn-mini" data-act="move-prev" data-id="${t.id}" title="移到上一列">‹</button>` : ''}
            ${colStatus !== 'DONE' ? `<button class="kb-btn-mini" data-act="move-next" data-id="${t.id}" title="移到下一列">›</button>` : ''}
          </div>
          <div class="kb-card-actions">
            <button class="kb-btn-mini" data-act="edit" data-id="${t.id}" title="编辑任务">✎</button>
            <button class="kb-btn-mini" data-act="del" data-id="${t.id}" title="删除任务" style="color:var(--kb-rose)">✕</button>
          </div>
        </div>
      </div>
    `;
  }

  // ---- 模式二：十步科研流程管道视图 ----
  function renderPipelineView(d) {
    const stages = d.stages || [];
    return `
      <div class="kb-pipeline-view">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
          <div>
            <h3 style="margin:0; font-size:15px; color:var(--fg)">🔬 标准十步科研创新与落地工作流</h3>
            <p style="margin:4px 0 0; font-size:12px; color:var(--muted)">与「科研平台」十步流程深度打通，点击任意卡片即可直达该阶段实战操作界面。</p>
          </div>
          <button class="btn sm btn-primary" onclick="window.openTab('workflow')">进入科研平台 →</button>
        </div>

        <div class="kb-pipeline-grid">
          ${stages.map(st => `
            <div class="kb-stage-card ${st.current ? 'is-current' : ''} ${st.done ? 'is-done' : ''}">
              <div class="kb-stage-card-head">
                <span class="kb-stage-num">STAGE ${String(st.index).padStart(2, '0')}</span>
                <span class="kb-stage-status-badge ${st.done ? 'done' : st.current ? 'current' : 'pending'}">
                  ${st.done ? '✓ 已达成' : st.current ? '🔥 当前阶段' : '待推进'}
                </span>
              </div>
              <h4 class="kb-stage-title">${escapeHtml(st.label)}</h4>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px">
                <span style="font-size:11px; color:var(--muted)">流程代码：${st.key}</span>
                <button class="btn sm ghost" onclick="window.openTab('workflow');" style="padding:2px 8px; font-size:11px">跳转工作流</button>
              </div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  // ---- 模式三：排期概览视图 ----
  function renderTimelineView(d) {
    const todos = d.todos || [];
    const urgent = todos.filter(t => t.priority === 'P0' && t.status !== 'DONE');
    const inProgress = todos.filter(t => t.status === 'IN_PROGRESS');
    const upcoming = todos.filter(t => t.status === 'TODO');

    return `
      <div class="kb-pipeline-view">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px">
          <div>
            <h3 style="margin:0; font-size:15px; color:var(--fg)">📅 课题任务排期与里程碑追踪</h3>
            <p style="margin:4px 0 0; font-size:12px; color:var(--muted)">按重要紧急程度与时间排期聚合展示当前课题规划。</p>
          </div>
          <button class="btn sm" onclick="window.openTab('calendar')">打开日历排期 →</button>
        </div>

        <div style="display:flex; flex-direction:column; gap:16px">
          ${urgent.length ? `
            <div style="border-left:3px solid #fb7185; padding-left:12px">
              <h4 style="margin:0 0 8px; color:var(--kb-rose); font-size:13px">🚨 P0 紧急攻关任务 (${urgent.length})</h4>
              <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:10px">
                ${urgent.map(t => renderTodoCard(t, t.status)).join('')}
              </div>
            </div>
          ` : ''}

          <div style="border-left:3px solid #8b5cf6; padding-left:12px">
            <h4 style="margin:0 0 8px; color:var(--kb-violet); font-size:13px">🚀 正在执行中 (${inProgress.length})</h4>
            <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:10px">
              ${inProgress.length ? inProgress.map(t => renderTodoCard(t, 'IN_PROGRESS')).join('') : '<div class="hint">暂无正在执行的任务</div>'}
            </div>
          </div>

          <div style="border-left:3px solid #38bdf8; padding-left:12px">
            <h4 style="margin:0 0 8px; color:var(--kb-sky); font-size:13px">📋 待开展计划 (${upcoming.length})</h4>
            <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:10px">
              ${upcoming.length ? upcoming.map(t => renderTodoCard(t, 'TODO')).join('') : '<div class="hint">待办池为空</div>'}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // ================= 事件与交互绑定 =================
  function bindBoardEvents(container, d) {
    // 切换项目
    const projSelect = container.querySelector('#kbProjSelect');
    if (projSelect) {
      projSelect.onchange = async () => {
        window.curTask = projSelect.value;
        localStorage.setItem('rw-task', window.curTask);
        await fetchKanbanData(window.curTask);
        renderBoard(container);
      };
    }

    // 视图 TAB 切换
    container.querySelectorAll('.kb-view-tab').forEach(b => {
      b.onclick = () => {
        kbState.view = b.dataset.view;
        renderBoard(container);
      };
    });

    // 搜索与过滤
    const searchInput = container.querySelector('#kbSearchInput');
    if (searchInput) {
      searchInput.oninput = () => {
        kbState.searchQuery = searchInput.value.trim();
        const mainView = container.querySelector('#kbMainView');
        if (mainView) mainView.innerHTML = renderCurrentViewContent(kbState.data);
        bindDragAndCardActions(container);
      };
    }

    const prioSelect = container.querySelector('#kbFilterPriority');
    if (prioSelect) {
      prioSelect.onchange = () => {
        kbState.filterPriority = prioSelect.value;
        const mainView = container.querySelector('#kbMainView');
        if (mainView) mainView.innerHTML = renderCurrentViewContent(kbState.data);
        bindDragAndCardActions(container);
      };
    }

    const assistSelect = container.querySelector('#kbFilterAssistant');
    if (assistSelect) {
      assistSelect.onchange = () => {
        kbState.filterAssistant = assistSelect.value;
        const mainView = container.querySelector('#kbMainView');
        if (mainView) mainView.innerHTML = renderCurrentViewContent(kbState.data);
        bindDragAndCardActions(container);
      };
    }

    // 新建任务按钮
    container.querySelector('#kbBtnNewTask')?.addEventListener('click', () => {
      openTaskModal(null, d);
    });

    // 刷新按钮
    container.querySelector('#kbBtnRefresh')?.addEventListener('click', async () => {
      await fetchKanbanData(window.curTask);
      renderBoard(container);
    });

    // 从工作流导入
    container.querySelector('#kbBtnImportWf')?.addEventListener('click', async () => {
      const tid = window.curTask || (d.task ? d.task.id : '');
      if (!tid) {
        uiNotify('请先创建或选择一个项目！');
        return;
      }
      try {
        await window.api('/api/todos/import_workflow', { task_id: tid });
        await fetchKanbanData(tid);
        renderBoard(container);
      } catch (err) {
        uiNotify('导入工作流失败: ' + err.message);
      }
    });

    // 列顶快速添加
    container.querySelectorAll('[data-add-status]').forEach(btn => {
      btn.onclick = () => {
        openTaskModal({ status: btn.dataset.addStatus }, d);
      };
    });

    bindDragAndCardActions(container);
  }

  function bindDragAndCardActions(container) {
    // 卡片内动作
    container.querySelectorAll('[data-act]').forEach(btn => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        const act = btn.dataset.act;
        const todoId = btn.dataset.id;
        const t = (kbState.data.todos || []).find(x => x.id === todoId);
        if (!t) return;

        if (act === 'toggle') {
          await window.api('/api/todos/toggle', { id: todoId });
          await fetchKanbanData(window.curTask);
          renderBoard(container);
        } else if (act === 'move-next') {
          const flow = ['TODO', 'IN_PROGRESS', 'REVIEW', 'DONE'];
          const idx = flow.indexOf(t.status);
          if (idx >= 0 && idx < flow.length - 1) {
            await window.api('/api/todos/update', { id: todoId, status: flow[idx + 1] });
            await fetchKanbanData(window.curTask);
            renderBoard(container);
          }
        } else if (act === 'move-prev') {
          const flow = ['TODO', 'IN_PROGRESS', 'REVIEW', 'DONE'];
          const idx = flow.indexOf(t.status);
          if (idx > 0) {
            await window.api('/api/todos/update', { id: todoId, status: flow[idx - 1] });
            await fetchKanbanData(window.curTask);
            renderBoard(container);
          }
        } else if (act === 'edit') {
          openTaskModal(t, kbState.data);
        } else if (act === 'del') {
          if (await uiConfirm(`确定要删除任务「${t.title}」吗？`)) {
            await window.api('/api/todos/delete', { id: todoId });
            await fetchKanbanData(window.curTask);
            renderBoard(container);
          }
        }
      };
    });

    // 拖拽支持 (Drag and Drop)
    const cards = container.querySelectorAll('.kb-card');
    cards.forEach(card => {
      card.addEventListener('dragstart', (e) => {
        kbState.draggedId = card.dataset.todoId;
        card.classList.add('dragging');
        e.dataTransfer.setData('text/plain', card.dataset.todoId);
        e.dataTransfer.effectAllowed = 'move';
      });

      card.addEventListener('dragend', () => {
        card.classList.remove('dragging');
        container.querySelectorAll('.kb-col').forEach(c => c.classList.remove('drag-over'));
      });
    });

    const dropCols = container.querySelectorAll('[data-drop-col]');
    dropCols.forEach(col => {
      const parentCol = col.closest('.kb-col');
      col.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (parentCol) parentCol.classList.add('drag-over');
      });

      col.addEventListener('dragleave', (e) => {
        if (!col.contains(e.relatedTarget) && parentCol) {
          parentCol.classList.remove('drag-over');
        }
      });

      col.addEventListener('drop', async (e) => {
        e.preventDefault();
        if (parentCol) parentCol.classList.remove('drag-over');
        const todoId = e.dataTransfer.getData('text/plain') || kbState.draggedId;
        const targetStatus = col.dataset.dropCol;
        if (!todoId || !targetStatus) return;

        const currentTodo = (kbState.data.todos || []).find(x => x.id === todoId);
        if (currentTodo && currentTodo.status !== targetStatus) {
          try {
            await window.api('/api/todos/update', { id: todoId, status: targetStatus });
            await fetchKanbanData(window.curTask);
            renderBoard(container);
          } catch (err) {
            console.error('Drag update failed:', err);
          }
        }
      });
    });
  }

  // ================= 任务新建与编辑模态框 =================
  function openTaskModal(todo, d) {
    const isEdit = !!(todo && todo.id);
    const modalHost = document.getElementById('kbModalContainer') || document.body;
    const assistants = d.assistants || [];

    const html = `
      <div id="kbTaskModal" class="modal">
        <div class="modal-mask"></div>
        <div class="modal-dialog kb-modal-dialog">
          <div class="modal-head">
            <h3>${isEdit ? '编辑科研任务' : '新建科研任务'}</h3>
            <button class="modal-close" id="btnKbModalClose">✕</button>
          </div>
          <div class="modal-body">
            <div class="kb-form-grid">
              <label class="field kb-form-full">
                <span>任务标题 *</span>
                <input id="kbTaskTitle" class="tb-input" placeholder="输入清晰明确的科研任务标题..." value="${escapeHtml(todo ? todo.title : '')}" style="width:100%">
              </label>

              <label class="field kb-form-full">
                <span>任务详述 / 说明</span>
                <textarea id="kbTaskDesc" class="tb-input" placeholder="补充关于实验、文献或撰写的具体要求..." rows="3" style="width:100%">${escapeHtml(todo ? (todo.description || '') : '')}</textarea>
              </label>

              <label class="field">
                <span>任务状态</span>
                <select id="kbTaskStatus" class="control" style="width:100%">
                  <option value="TODO" ${todo && todo.status === 'TODO' ? 'selected' : ''}>📋 待办计划</option>
                  <option value="IN_PROGRESS" ${todo && todo.status === 'IN_PROGRESS' ? 'selected' : ''}>🚀 正在进行</option>
                  <option value="REVIEW" ${todo && todo.status === 'REVIEW' ? 'selected' : ''}>🔍 审核验证</option>
                  <option value="DONE" ${todo && todo.status === 'DONE' ? 'selected' : ''}>✅ 已完成</option>
                </select>
              </label>

              <label class="field">
                <span>优先级</span>
                <select id="kbTaskPriority" class="control" style="width:100%">
                  <option value="P0" ${todo && todo.priority === 'P0' ? 'selected' : ''}>P0 紧急 (最高优先级)</option>
                  <option value="P1" ${!todo || todo.priority === 'P1' ? 'selected' : ''}>P1 重要 (常规主力推进)</option>
                  <option value="P2" ${todo && todo.priority === 'P2' ? 'selected' : ''}>P2 普通 (备忘与参考)</option>
                </select>
              </label>

              <label class="field">
                <span>指派助手 / 专家</span>
                <select id="kbTaskAssistant" class="control" style="width:100%">
                  <option value="">未指派</option>
                  <option value="智研管家" ${todo && todo.assigned_assistant === '智研管家' ? 'selected' : ''}>🤖 智研管家</option>
                  ${assistants.map(a => `
                    <option value="${a.name}" ${todo && todo.assigned_assistant === a.name ? 'selected' : ''}>
                      👨‍🔬 ${escapeHtml(a.name)}
                    </option>
                  `).join('')}
                </select>
              </label>

              <label class="field">
                <span>截止日期</span>
                <input type="date" id="kbTaskDueDate" class="control" value="${todo && todo.due_date ? todo.due_date : ''}" style="width:100%">
              </label>
            </div>
          </div>
          <div class="modal-foot">
            <button id="btnKbModalSave" class="btn btn-primary">${isEdit ? '保存修改' : '确认创建'}</button>
            <button id="btnKbModalCancel" class="btn ghost">取消</button>
          </div>
        </div>
      </div>
    `;

    modalHost.innerHTML = html;

    const modal = document.getElementById('kbTaskModal');
    const close = () => { if (modal) modal.remove(); };

    modal.querySelector('#btnKbModalClose').onclick = close;
    modal.querySelector('#btnKbModalCancel').onclick = close;
    modal.querySelector('.modal-mask').onclick = close;

    modal.querySelector('#btnKbModalSave').onclick = async () => {
      const title = modal.querySelector('#kbTaskTitle').value.trim();
      if (!title) {
        uiNotify('请输入任务标题！');
        return;
      }
      const description = modal.querySelector('#kbTaskDesc').value.trim();
      const status = modal.querySelector('#kbTaskStatus').value;
      const priority = modal.querySelector('#kbTaskPriority').value;
      const assigned_assistant = modal.querySelector('#kbTaskAssistant').value;
      const due_date = modal.querySelector('#kbTaskDueDate').value;

      try {
        if (isEdit) {
          await window.api('/api/todos/update', {
            id: todo.id,
            title,
            description,
            status,
            priority,
            assigned_assistant,
            due_date
          });
        } else {
          const tid = window.curTask || (d.task ? d.task.id : '');
          await window.api('/api/todos/add', {
            task_id: tid,
            title,
            description,
            status,
            priority,
            assigned_assistant,
            due_date
          });
        }
        close();
        await fetchKanbanData(window.curTask);
        if (kbHost && kbHost.isConnected) renderBoard(kbHost);
      } catch (err) {
        uiNotify('保存失败: ' + err.message);
      }
    };
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
})();
