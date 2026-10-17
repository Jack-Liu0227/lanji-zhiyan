  // ============ 邮箱（多账户 IMAP / SMTP） ============
  let mailAccounts = [];
  let mailProviders = null;
  let mailCur = { accountId: '', folder: 'INBOX', folderName: '收件箱', page: 1, pageSize: 25, search: '' };
  let mailFolders = [];
  let mailFoldersOwner = '';
  let mailMessages = [];
  let mailTotal = 0;
  let mailDetail = null;
  let mailMaEditId = null;
  let mailInited = false;

  const FOLDER_ICONS = { '\\Inbox': '📥', '\\Sent': '📤', '\\Drafts': '📝', '\\Trash': '🗑', '\\Junk': '🚫', '\\Archive': '📦' };

  // 邮箱徽标：优先用域名首字母，数字域名（如 163.com）用 📧
  function mailBadge(email) {
    const d = String(email || '').split('@')[1] || '';
    const c = (d[0] || '').toUpperCase();
    return /[A-Z]/.test(c) ? c : '📧';
  }

  async function ensureMailProviders() {
    if (mailProviders) return mailProviders;
    try { mailProviders = await api('/api/mail/providers'); } catch { mailProviders = {}; }
    return mailProviders;
  }

  async function loadMailAccounts() {
    mailAccounts = await api('/api/mail/accounts');
    return mailAccounts;
  }

  // ============ 新邮件提醒（轮询 + 桌面通知 + 未读角标） ============
  // 设计要点：
  //  · 每个账户记录「上次已看到的最高 uid」作为基线，存 localStorage，重启后不回到零。
  //  · 首次运行没有基线时只记录基线、不弹提醒，避免把邮箱里的历史邮件一次性全弹出来。
  //  · uid 变大且未读 => 新邮件 => 应用内浮层 + 系统桌面通知。
  //  · 同一 uid 只提醒一次（notifiedUids 去重），避免轮询反复弹。
  const MAIL_SINCE_KEY = 'sci_mail_seen_uids';
  const NOTIFY_INTERVAL = 60 * 1000; // 轮询间隔：60 秒
  let mailNotifyTimer = null;
  let mailNotifyBusy = false;
  let mailNotifyEnabled = true;
  const notifiedUids = new Set(); // `${accountId}:${uid}` 已提醒过的邮件

  function readMailSince() {
    try { return JSON.parse(localStorage.getItem(MAIL_SINCE_KEY) || '{}') || {}; } catch { return {}; }
  }
  function writeMailSince(map) {
    try { localStorage.setItem(MAIL_SINCE_KEY, JSON.stringify(map)); } catch (_) { /* ignore */ }
  }

  // 侧边栏「邮箱」未读角标
  function renderNavMailBadge(totalUnread) {
    const n = parseInt(totalUnread, 10) || 0;
    const badge = $('navMailBadge');
    if (!badge) return;
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.classList.toggle('hidden', n <= 0);
  }

  // 应用内新邮件浮层（可点击跳转）
  function showMailNotification(acc, msg) {
    const stack = $('mailNotifStack');
    if (!stack) return;
    const el2 = document.createElement('div');
    el2.className = 'mail-notif';
    el2.innerHTML = `
      <div class="mail-notif-ico">📬</div>
      <div class="mail-notif-body">
        <div class="mail-notif-title">新邮件 · ${esc(acc.label || acc.email)}</div>
        <div class="mail-notif-subject">${esc(String(msg.subject || '(无主题)').slice(0, 80))}</div>
        <div class="mail-notif-from">${esc(msg.fromName || msg.fromAddress || '未知发件人')}</div>
      </div>
      <button class="mail-notif-close" title="关闭">✕</button>`;
    const close = () => { el2.classList.add('out'); setTimeout(() => el2.remove(), 220); };
    el2.querySelector('.mail-notif-close').addEventListener('click', (e) => { e.stopPropagation(); close(); });
    // 点击浮层：切到邮箱视图并打开这封邮件
    el2.addEventListener('click', async () => {
      close();
      try {
        openTab("mail");
        await selectMailAccount(acc.id);
        if (mailCur.folder !== 'INBOX') await openMailFolder(acc.id, 'INBOX', '收件箱');
        await openMailMessage(msg.uid);
      } catch (e) { toast('打开邮件失败：' + e.message, 'error'); }
    });
    stack.appendChild(el2);
    // 12 秒后自动收起（点击/关闭不受影响）
    setTimeout(close, 12000);
  }

  // 系统级桌面通知（Electron / 浏览器原生）
  function showDesktopNotification(acc, msg) {
    try {
      if (typeof Notification === 'undefined') return;
      if (Notification.permission !== 'granted') return;
      const n = new Notification('📬 新邮件 · ' + (acc.label || acc.email), {
        body: `${msg.fromName || msg.fromAddress || '未知发件人'}\n${msg.subject || '(无主题)'}`,
        tag: `mail-${acc.id}-${msg.uid}`,
      });
      n.onclick = () => { try { window.focus(); } catch (_) {} n.close(); };
    } catch (_) { /* 通知失败不影响功能 */ }
  }

  function requestNotifyPermission() {
    try {
      if (typeof Notification === 'undefined') return;
      if (Notification.permission === 'default') Notification.requestPermission().catch(() => {});
    } catch (_) { /* ignore */ }
  }

  // 单轮轮询
  async function checkNewMail() {
    if (mailNotifyBusy || !mailNotifyEnabled) return;
    mailNotifyBusy = true;
    try {
      const since = readMailSince();
      const data = await api('/api/mail/notify?since=' + encodeURIComponent(JSON.stringify(since)));
      renderNavMailBadge(data.totalUnread || 0);

      const nextSince = { ...since };
      let changed = false;
      for (const a of data.accounts || []) {
        if (a.error) continue;
        const prev = parseInt(since[a.id], 10);
        const hadBaseline = Number.isFinite(prev);
        // 推进基线到当前最大 uid
        if (!hadBaseline || (a.newestUid || 0) > prev) { nextSince[a.id] = a.newestUid || 0; changed = true; }
        if (!hadBaseline) continue; // 首次不提醒历史邮件
        for (const m of a.fresh || []) {
          const key = a.id + ':' + m.uid;
          if (notifiedUids.has(key)) continue;
          notifiedUids.add(key);
          showMailNotification(a, m);
          showDesktopNotification(a, m);
        }
      }
      if (changed) writeMailSince(nextSince);
    } catch (_) {
      // 网络/离线/账户报错时静默，下一轮再试
    } finally {
      mailNotifyBusy = false;
    }
  }

  function startMailNotify() {
    if (mailNotifyTimer) return;
    requestNotifyPermission();
    checkNewMail();
    mailNotifyTimer = setInterval(checkNewMail, NOTIFY_INTERVAL);
  }
  function stopMailNotify() {
    if (mailNotifyTimer) { clearInterval(mailNotifyTimer); mailNotifyTimer = null; }
  }
  // 调试/自动化钩子：便于手动触发一轮检查或临时改间隔
  window.__mailNotifyTick = () => checkNewMail();
  window.__mailNotifySetInterval = (ms) => {
    stopMailNotify();
    mailNotifyTimer = setInterval(checkNewMail, Math.max(3000, parseInt(ms, 10) || NOTIFY_INTERVAL));
  };
  // 调试/自动化钩子：直接打开某篇文献的 PDF 阅读器、切换右侧页签、读取当前模型
  window.__openReader = (id) => openPdfReader(id);
  window.__setPrTab = (t) => switchPrTab(t);
  window.__activeModel = () => ({
    activeProfileId, active: activeModelInfo,
    profiles: profiles.map((p) => ({ id: p.id, label: p.label, provider: p.provider, model: p.model })),
  });

  function fmtMailDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) {
      return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
  }
  function fmtMailFullDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fmtMailSize(n) {
    const b = Number(n) || 0;
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(0) + ' KB';
    return (b / 1024 / 1024).toFixed(1) + ' MB';
  }

  // ---------- 三栏渲染 ----------
  function renderMailView() {
    renderMailSide();
    if (!mailCur.accountId) {
      renderMailListHead();
      $('mailList').innerHTML = mailAccounts.length
        ? '<div class="mail-empty">选择左侧邮箱账户开始收信</div>'
        : mailGuideHtml();
      $('mailPager').innerHTML = '';
      renderMailDetail();
    }
  }
  function mailGuideHtml() {
    return `<div class="mail-guide">
      <div class="mail-guide-ico">📧</div>
      <b>还没有登录邮箱</b>
      <p>登录邮箱后可以在这里直接收发与阅读邮件，多个邮箱可以同时管理（例如个人 163 邮箱 + 学校邮箱）。</p>
      <p class="mail-guide-dim">暂时不登录也没关系，其他功能不受影响，随时可以在左侧「＋ 账户管理」里添加。</p>
      <button class="btn btn-primary" id="btnMailGuideAdd">＋ 添加邮箱账户</button>
    </div>`;
  }

  function renderMailSide() {
    const accBox = $('mailAccounts');
    accBox.innerHTML = mailAccounts.length
      ? mailAccounts.map((a) => `
        <div class="mail-acc${a.id === mailCur.accountId ? ' active' : ''}" data-acc="${a.id}" title="${esc(a.email)}">
          <span class="mail-badge">${mailBadge(a.email)}</span>
          <div class="mail-acc-info">
            <b>${esc(a.label || a.email)}</b>
            <span>${esc(a.email)}</span>
          </div>
        </div>`).join('')
      : '<div class="mail-empty-side">尚未添加邮箱账户</div>';

    const fBox = $('mailFolders');
    if (!mailCur.accountId) { fBox.innerHTML = ''; return; }
    fBox.innerHTML = mailFolders.length
      ? mailFolders.map((f) => `
        <div class="mail-folder${f.path === mailCur.folder ? ' active' : ''}" data-folder="${esc(f.path)}" data-foldername="${esc(f.name)}">
          <span class="mail-folder-ico">${FOLDER_ICONS[f.specialUse] || '📁'}</span><span class="mail-folder-name">${esc(f.name)}</span>
        </div>`).join('')
      : '<div class="mail-empty-side">暂无文件夹</div>';
  }

  function renderMailListHead() {
    const box = $('mailListHead');
    if (!mailCur.accountId) { box.innerHTML = ''; return; }
    const acc = mailAccounts.find((a) => a.id === mailCur.accountId);
    const scope = mailCur.search ? `搜索「${esc(mailCur.search)}」` : esc(mailCur.folderName || mailCur.folder);
    box.innerHTML = `<span class="mail-title-main">${scope}</span>
      <span class="mail-title-sub">${esc(acc ? (acc.label || acc.email) : '')} · 共 ${mailTotal} 封</span>`;
  }

  function renderMailMessages() {
    const box = $('mailList');
    if (!mailMessages.length) {
      box.innerHTML = `<div class="mail-empty">${mailCur.search ? '没有找到匹配的邮件' : '这个文件夹还没有邮件'}</div>`;
    } else {
      box.innerHTML = mailMessages.map((m) => `
        <div class="mail-item${m.seen ? '' : ' unread'}${mailDetail && mailDetail.uid === m.uid ? ' active' : ''}" data-mail="${m.uid}">
          <span class="mail-dot"></span>
          <div class="mail-item-main">
            <div class="mail-item-row1">
              <span class="mail-from" title="${esc(m.fromAddress)}">${esc(m.fromName || m.fromAddress || '(未知发件人)')}</span>
              <span class="mail-date">${esc(fmtMailDate(m.date))}</span>
            </div>
            <div class="mail-item-row2">
              <span class="mail-subject" title="${esc(m.subject)}">${esc(m.subject)}</span>
              ${m.hasAttachments ? '<span class="mail-attach-ico" title="含附件">📎</span>' : ''}
            </div>
          </div>
        </div>`).join('');
    }
    renderMailPager();
    renderMailListHead();
  }

  function renderMailPager() {
    const box = $('mailPager');
    if (!mailCur.accountId) { box.innerHTML = ''; return; }
    const pages = Math.max(1, Math.ceil(mailTotal / mailCur.pageSize));
    const p = Math.min(mailCur.page, pages);
    box.innerHTML = `
      <button class="tb-btn" data-mail-page="prev"${p <= 1 ? ' disabled' : ''}>‹ 上一页</button>
      <span class="mail-page-info">${p} / ${pages}</span>
      <button class="tb-btn" data-mail-page="next"${p >= pages ? ' disabled' : ''}>下一页 ›</button>
      <span class="mail-page-size">
        <select class="tb-select" id="mailPageSize">
          ${[25, 50, 100].map((n) => `<option value="${n}"${n === mailCur.pageSize ? ' selected' : ''}>每页 ${n} 封</option>`).join('')}
        </select>
      </span>`;
  }

  // 邮件 HTML 正文：注入基础样式（自适应宽度 / 图片不溢出 / 字体统一），并清理未替换的内嵌图片引用
  function mailFrameDoc(html) {
    let s = String(html || '');
    s = s.replace(/(src|href)\s*=\s*(["'])cid:[^"']*\2/gi, '$1=$2data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7$2');
    const inject = `<base target="_blank">
<style>
html, body { margin: 0; padding: 12px 16px; font-size: 14px; line-height: 1.75; word-break: break-word;
  font-family: "Times New Roman", "Microsoft YaHei", "PingFang SC", sans-serif; color: #17231f; background: #fff; }
img { max-width: 100%; height: auto; }
table { max-width: 100% !important; }
pre { white-space: pre-wrap; }
a { color: #176b87; }
</style>`;
    if (/<head[^>]*>/i.test(s)) return s.replace(/<head[^>]*>/i, (m) => m + inject);
    if (/<html[^>]*>/i.test(s)) return s.replace(/<html[^>]*>/i, (m) => m + inject);
    return inject + s;
  }

  function renderMailDetail() {
    const box = $('mailDetail');
    if (!mailDetail) {
      box.innerHTML = '<div class="mail-placeholder">📬 选中一封邮件后在此阅读</div>';
      return;
    }
    const d = mailDetail;
    const accId = mailCur.accountId;
    const folderQ = encodeURIComponent(mailCur.folder);
    const atts = (d.attachments || []).filter((a) => !a.inline);
    // 译文只对「当前这封」生效，切换邮件就自动失效
    const trans = mailTrans && mailTrans.uid === d.uid ? mailTrans : null;
    const transBlock = trans ? `
      <div class="mail-trans" id="mailTrans">
        <div class="mail-trans-head">
          <b>🈯 中文翻译</b>
          <button class="tb-btn" data-mail-trans-copy="1" title="复制译文">📋 复制</button>
          <button class="tb-btn" data-mail-trans-close="1" title="关闭译文">✕</button>
        </div>
        <div class="mail-trans-body">${trans.pending
          ? '<div class="mail-loading">翻译中…请稍候</div>'
          : (trans.error ? `<div class="mail-trans-err">⚠ ${esc(trans.error)}</div>` : esc(trans.text || ''))}</div>
        ${trans.truncated ? '<div class="mail-trans-note">正文较长，本次只翻译了前一部分内容。</div>' : ''}
      </div>` : '';
    box.innerHTML = `
      <div class="mail-detail-head">
        <div class="mail-detail-subject">${esc(d.subject)}</div>
        <div class="mail-detail-meta">
          <span class="mail-detail-from">${esc(d.fromName || d.fromAddress)}</span>
          <span class="mail-detail-addr">&lt;${esc(d.fromAddress)}&gt;</span>
          <span class="ob-spacer"></span>
          <span class="mail-detail-date">${esc(fmtMailFullDate(d.date))}</span>
        </div>
        <div class="mail-detail-to">收件人：${esc(d.to || '')}${d.cc ? `　抄送：${esc(d.cc)}` : ''}</div>
        <div class="mail-detail-ops">
          <button class="tb-btn" data-mail-reply="1">↩ 回复</button>
          <button class="tb-btn accent" data-mail-translate="1" ${trans && trans.pending ? 'disabled' : ''}>🈯 ${trans ? '重新翻译' : '一键翻译'}</button>
          <button class="tb-btn" data-mail-unread="1">标记未读</button>
          <button class="tb-btn" data-mail-del="1">🗑 删除</button>
        </div>
        ${atts.length ? `<div class="mail-atts">${atts.map((a) => `
          <a class="mail-att" href="/api/mail/accounts/${accId}/messages/${d.uid}/attachments/${a.index}?folder=${folderQ}" title="${esc(a.filename)}">
            <span class="mail-att-ico">📎</span>
            <span class="mail-att-name">${esc(a.filename)}</span>
            <span class="mail-att-size">${fmtMailSize(a.size)}</span>
          </a>`).join('')}</div>` : ''}
      </div>
      ${transBlock}
      <div class="mail-body">${d.html
        ? `<iframe class="mail-frame" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" srcdoc="${esc(mailFrameDoc(d.html))}"></iframe>`
        : `<pre class="mail-text">${esc(d.text || '(无正文)')}</pre>`}</div>`;
  }

  // ---------- 邮件一键翻译 ----------

  /** 译文缓存。只保留当前这一封，切换邮件即失效（见 renderMailDetail） */
  let mailTrans = null;
  /** 单次翻译的字符上限，避免超长邮件把翻译接口拖垮 */
  const MAIL_TRANS_MAX = 6000;

  /**
   * 取邮件正文的纯文本用于翻译。
   * 优先用服务端给的 text；只有 HTML 时剥标签（邮件 HTML 常带 <style>/<script>，先删掉）。
   */
  function mailPlainBody(d) {
    const text = String(d?.text || '').trim();
    if (text) return text;
    const html = String(d?.html || '');
    if (!html) return '';
    const cleaned = html
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, ' ')
      .replace(/<!--[\s\S]*?-->/g, ' ');
    return String(stripHtml(cleaned) || '').replace(/\n{3,}/g, '\n\n').trim();
  }

  /** 一键翻译当前邮件正文 */
  async function mailTranslateBody() {
    if (!mailDetail) return;
    const uid = mailDetail.uid;
    const plain = mailPlainBody(mailDetail);
    if (!plain) { toast('这封邮件没有可翻译的正文', 'error'); return; }

    const truncated = plain.length > MAIL_TRANS_MAX;
    const text = truncated ? plain.slice(0, MAIL_TRANS_MAX) : plain;
    mailTrans = { uid, pending: true, text: '', truncated };
    renderMailDetail();

    const target = $('prLangTo')?.value || 'zh';
    let provider;
    let profileId;
    const src = $('prTranslateSource')?.value || 'auto';
    if (src.startsWith('llm:')) { provider = 'siliconflow'; profileId = src.slice(4); }
    else if (src !== 'auto') provider = src;

    try {
      const data = await api('/api/translate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, target, provider, profileId }),
      });
      // 期间用户可能已经切到别的邮件，避免把结果串到错的地方
      if (!mailTrans || mailTrans.uid !== uid) return;
      mailTrans = { uid, text: data.translation || '(未返回译文)', truncated };
      toast('邮件翻译完成', 'success');
    } catch (e) {
      if (!mailTrans || mailTrans.uid !== uid) return;
      mailTrans = { uid, error: e.message, truncated };
    }
    renderMailDetail();
  }

  // ---------- 数据加载 ----------
  async function ensureMailFolders(accountId, force = false) {
    if (!force && mailFoldersOwner === accountId && mailFolders.length) { renderMailSide(); return; }
    mailFoldersOwner = accountId;
    try {
      mailFolders = await api(`/api/mail/accounts/${accountId}/folders`);
    } catch (e) {
      mailFolders = [];
      toast('读取文件夹失败：' + e.message, 'error');
    }
    renderMailSide();
  }

  async function openMailFolder(accountId, folder, name, page = 1) {
    if (!accountId) return;
    mailCur.accountId = accountId;
    mailCur.folder = folder || 'INBOX';
    mailCur.folderName = name || folder || '收件箱';
    mailCur.page = page;
    mailDetail = null;
    renderMailSide();
    renderMailDetail();
    await loadMailMessages();
  }

  async function selectMailAccount(id) {
    if (!id || (mailCur.accountId === id && mailMessages.length)) return;
    mailMessages = []; mailTotal = 0; mailDetail = null;
    $('mailSearch').value = '';
    mailCur = { accountId: id, folder: 'INBOX', folderName: '收件箱', page: 1, pageSize: mailCur.pageSize, search: '' };
    await ensureMailFolders(id, true);
    // 优先定位到真正的收件箱文件夹（不同邮箱命名可能不同）
    const inbox = mailFolders.find((f) => f.specialUse === '\\Inbox') || mailFolders.find((f) => f.path === 'INBOX');
    await openMailFolder(id, inbox ? inbox.path : 'INBOX', inbox ? inbox.name : '收件箱', 1);
  }

  async function loadMailMessages() {
    if (!mailCur.accountId) return;
    $('mailList').innerHTML = '<div class="mail-loading">正在收取邮件…</div>';
    renderMailListHead();
    const q = new URLSearchParams({ folder: mailCur.folder, page: String(mailCur.page), pageSize: String(mailCur.pageSize) });
    if (mailCur.search) q.set('search', mailCur.search);
    try {
      const data = await api(`/api/mail/accounts/${mailCur.accountId}/messages?${q.toString()}`);
      mailMessages = data.messages || [];
      mailTotal = data.total || 0;
      renderMailMessages();
    } catch (e) {
      mailMessages = []; mailTotal = 0;
      $('mailList').innerHTML = `<div class="mail-error">收取失败：${esc(e.message)}<div><button class="btn" data-mail-retry="1">重试</button></div></div>`;
      renderMailPager();
    }
  }

  async function openMailMessage(uid) {
    const id = mailCur.accountId;
    $('mailDetail').innerHTML = '<div class="mail-loading">正在打开邮件…</div>';
    try {
      const d = await api(`/api/mail/accounts/${id}/messages/${uid}?folder=${encodeURIComponent(mailCur.folder)}`);
      mailDetail = d;
      renderMailDetail();
      const item = mailMessages.find((m) => m.uid === Number(uid));
      if (item && !item.seen) {
        item.seen = true;
        api(`/api/mail/accounts/${id}/messages/${uid}/seen`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ seen: true, folder: mailCur.folder }),
        }).catch(() => { /* 标记失败不影响阅读 */ });
      }
      renderMailMessages();
    } catch (e) {
      mailDetail = null;
      $('mailDetail').innerHTML = `<div class="mail-error">打开失败：${esc(e.message)}</div>`;
    }
  }

  async function markAllMailSeen() {
    if (!mailCur.accountId) return toast('请先选择邮箱账户', 'error');
    const scope = mailCur.search ? `当前文件夹（搜索结果不会影响批量范围）` : (mailCur.folderName || mailCur.folder);
    if (!confirm(`确认将「${scope}」中的全部未读邮件标记为已读？`)) return;
    const button = $('btnMailMarkAllRead');
    if (button) button.disabled = true;
    try {
      const result = await api(`/api/mail/accounts/${mailCur.accountId}/messages/seen-all`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: mailCur.folder }),
      });
      mailMessages.forEach((item) => { item.seen = true; });
      renderMailMessages();
      toast(result.markedCount ? `已将 ${result.markedCount} 封邮件标记为已读` : '当前文件夹没有未读邮件', 'success');
      // 同步侧边栏未读角标，避免批量操作后仍显示旧数字。
      checkNewMail().catch(() => {});
    } catch (e) { toast('批量标记失败：' + e.message, 'error'); }
    finally { if (button) button.disabled = false; }
  }

  async function markMailUnread() {
    if (!mailDetail) return;
    const uid = mailDetail.uid;
    try {
      await api(`/api/mail/accounts/${mailCur.accountId}/messages/${uid}/seen`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seen: false, folder: mailCur.folder }),
      });
      const it = mailMessages.find((m) => m.uid === uid);
      if (it) it.seen = false;
      renderMailMessages();
      toast('已标记为未读', 'success');
    } catch (e) { toast(e.message, 'error'); }
  }

  async function removeMailMessage() {
    if (!mailDetail) return;
    if (!confirm('确认删除这封邮件？删除后无法恢复。')) return;
    const uid = mailDetail.uid;
    try {
      await api(`/api/mail/accounts/${mailCur.accountId}/messages/${uid}?folder=${encodeURIComponent(mailCur.folder)}`, { method: 'DELETE' });
      mailDetail = null;
      renderMailDetail();
      await loadMailMessages();
      toast('邮件已删除', 'success');
    } catch (e) { toast(e.message, 'error'); }
  }

  // ---------- 账户管理 ----------
  function renderMaProviderOptions() {
    const sel = $('maProvider');
    if (sel.dataset.ready) return;
    sel.innerHTML = Object.entries(mailProviders || {})
      .map(([key, p]) => `<option value="${esc(key)}">${esc(p.label || key)}</option>`).join('');
    sel.dataset.ready = '1';
    sel.value = '163';
    applyMailProviderPreset();
  }

  function applyMailProviderPreset() {
    const p = (mailProviders || {})[$('maProvider').value] || {};
    $('maNote').textContent = p.note || '';
    renderMailHelp(p);
    $('maImapHost').value = p.imapHost || '';
    $('maImapPort').value = p.imapPort || 993;
    $('maImapSecure').checked = p.imapSecure !== false;
    $('maSmtpHost').value = p.smtpHost || '';
    $('maSmtpPort').value = p.smtpPort || 465;
    $('maSmtpSecure').checked = p.smtpSecure !== false;
    $('maSelfSigned').checked = false;
  }

  function resetMailAccountForm() {
    mailMaEditId = null;
    $('maFormTitle').textContent = '添加邮箱账户';
    $('maLabel').value = '';
    $('maEmail').value = '';
    $('maPassword').value = '';
    $('maTestResult').classList.add('hidden');
    $('maProvider').value = '163';
    applyMailProviderPreset();
  }

  function fillMailAccountForm(acc) {
    if (!acc) return;
    mailMaEditId = acc.id;
    $('maFormTitle').textContent = `编辑账户：${acc.label || acc.email}`;
    $('maProvider').value = acc.provider || 'custom';
    if (!mailProviders || !mailProviders[acc.provider]) $('maProvider').value = 'custom';
    $('maLabel').value = acc.label || '';
    $('maEmail').value = acc.email || '';
    $('maPassword').value = '';
    $('maPassword').placeholder = acc.hasPassword ? '留空表示不修改授权码' : '请输入授权码';
    $('maImapHost').value = acc.imapHost || '';
    $('maImapPort').value = acc.imapPort || 993;
    $('maImapSecure').checked = acc.imapSecure !== false;
    $('maSmtpHost').value = acc.smtpHost || '';
    $('maSmtpPort').value = acc.smtpPort || 465;
    $('maSmtpSecure').checked = acc.smtpSecure !== false;
    $('maSelfSigned').checked = !!acc.allowSelfSigned;
    $('maNote').textContent = (mailProviders[acc.provider] || {}).note || '';
    renderMailHelp(mailProviders[acc.provider] || {});
    $('maTestResult').classList.add('hidden');
  }

  function renderMaList() {
    const box = $('maList');
    if (!mailAccounts.length) {
      box.innerHTML = '<div class="ma-empty">还没有添加任何邮箱账户。在下方表单填写信息即可登录，支持同时添加多个邮箱。</div>';
      return;
    }
    box.innerHTML = mailAccounts.map((a) => `
      <div class="ma-item${a.id === mailMaEditId ? ' active' : ''}">
        <span class="mail-badge">${mailBadge(a.email)}</span>
        <div class="ma-item-info">
          <b>${esc(a.label || a.email)}</b>
          <span>${esc(a.email)}${a.imapHost ? ' · ' + esc(a.imapHost) : ''}${a.lastSyncAt ? ' · 最近收信 ' + esc(fmtMailFullDate(a.lastSyncAt)) : ''}</span>
        </div>
        <div class="ma-item-ops">
          <button class="tb-btn" data-ma-test="${a.id}">测试</button>
          <button class="tb-btn" data-ma-edit="${a.id}">编辑</button>
          <button class="tb-btn danger" data-ma-del="${a.id}">删除</button>
        </div>
      </div>`).join('');
  }

  function renderMailHelp(provider) {
    const host = $('maHelpLinks');
    if (host) host.innerHTML = (provider.helpLinks || []).map(link =>
      `<a href="${esc(link.url)}" target="_blank" rel="noopener noreferrer" style="margin-right:14px">${esc(link.label)} ↗</a>`).join('') +
      '<p class="hint">自定义邮箱请向单位信息中心索取服务器、端口及客户端授权方式。授权码仅保存在本机，请勿发给他人。</p>';
  }

  async function openMailAccountModal(id) {
    await ensureMailProviders();
    renderMaProviderOptions();
    mailMaEditId = id || null;
    renderMaList();
    if (id) fillMailAccountForm(mailAccounts.find((a) => a.id === id));
    else resetMailAccountForm();
    renderMaList();
    $('maTestResult').classList.add('hidden');
    $('mailAccountModal').classList.remove('hidden');
  }

  function mailAccFormData() {
    return {
      provider: $('maProvider').value,
      label: $('maLabel').value.trim(),
      email: $('maEmail').value.trim(),
      password: $('maPassword').value,
      imapHost: $('maImapHost').value.trim(),
      imapPort: $('maImapPort').value.trim(),
      imapSecure: $('maImapSecure').checked,
      smtpHost: $('maSmtpHost').value.trim(),
      smtpPort: $('maSmtpPort').value.trim(),
      smtpSecure: $('maSmtpSecure').checked,
      allowSelfSigned: $('maSelfSigned').checked,
    };
  }

  async function testMailAccountForm() {
    const box = $('maTestResult');
    box.className = 'ma-test-result';
    box.classList.remove('hidden');
    box.textContent = '正在连接邮件服务器…';
    const btn = $('btnMaTest');
    btn.disabled = true;
    try {
      const r = await api('/api/mail/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mailAccFormData()),
      });
      box.classList.add('ok');
      box.textContent = `✅ 连接成功：收件箱 ${r.inboxMessages} 封邮件，共 ${r.folderCount} 个文件夹`;
    } catch (e) {
      box.classList.add('err');
      box.textContent = '❌ ' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  async function saveMailAccountForm() {
    const data = mailAccFormData();
    const btn = $('btnMaSave');
    btn.disabled = true;
    const editingId = mailMaEditId;
    try {
      if (editingId) {
        await api('/api/mail/accounts/' + editingId, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
        });
      } else {
        const created = await api('/api/mail/accounts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
        });
        mailMaEditId = created.id;
      }
      await loadMailAccounts();
      const targetId = editingId || mailMaEditId;
      resetMailAccountForm();
      renderMaList();
      renderMailSide();
      toast(editingId ? '账户已更新' : '账户已添加，正在连接邮箱…', 'success');
      await selectMailAccount(targetId);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- 写邮件 ----------
  function openMailCompose(to = '', subject = '') {
    if (!mailAccounts.length) {
      toast('请先添加一个邮箱账户', 'error');
      openMailAccountModal(null);
      return;
    }
    $('mcFrom').innerHTML = mailAccounts
      .map((a) => `<option value="${a.id}">${esc(a.label || a.email)}</option>`).join('');
    if (mailCur.accountId) $('mcFrom').value = mailCur.accountId;
    $('mcTo').value = to || '';
    $('mcCc').value = '';
    $('mcSubject').value = subject || '';
    $('mcBody').value = '';
    $('mailComposeModal').classList.remove('hidden');
    setTimeout(() => { if (!to) $('mcTo').focus(); else $('mcBody').focus(); }, 30);
  }

  async function sendMailCompose() {
    const id = $('mcFrom').value;
    const to = $('mcTo').value.trim();
    if (!to) return toast('请填写收件人', 'error');
    const btn = $('btnMcSend');
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '发送中…';
    try {
      await api(`/api/mail/accounts/${id}/send`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to, cc: $('mcCc').value.trim(),
          subject: $('mcSubject').value.trim(), text: $('mcBody').value,
        }),
      });
      $('mailComposeModal').classList.add('hidden');
      toast('邮件已发送', 'success');
    } catch (e) {
      toast('发送失败：' + e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  // ---------- 邮箱事件绑定 ----------
  function bindMailEvents() {
    $('btnMailManage').addEventListener('click', () => openMailAccountModal(null));
    $('btnMailCompose').addEventListener('click', () => openMailCompose());
    $('btnMailRefresh').addEventListener('click', async () => {
      if (!mailCur.accountId) { toast('请先添加邮箱账户', 'error'); return; }
      await ensureMailFolders(mailCur.accountId, true);
      await loadMailMessages();
      toast('已刷新', 'success');
    });
    $('btnMailMarkAllRead').addEventListener('click', markAllMailSeen);

    $('mailSearch').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      if (!mailCur.accountId) return;
      mailCur.search = $('mailSearch').value.trim();
      mailCur.page = 1;
      loadMailMessages();
    });
    $('mailSearch').addEventListener('input', (e) => {
      if (!e.target.value && mailCur.search && mailCur.accountId) {
        mailCur.search = '';
        mailCur.page = 1;
        loadMailMessages();
      }
    });

    $('mailAccounts').addEventListener('click', (e) => {
      const a = e.target.closest('[data-acc]');
      if (a) selectMailAccount(a.dataset.acc);
    });
    $('mailFolders').addEventListener('click', (e) => {
      const f = e.target.closest('[data-folder]');
      if (f) openMailFolder(mailCur.accountId, f.dataset.folder, f.dataset.foldername, 1);
    });
    $('mailList').addEventListener('click', (e) => {
      if (e.target.closest('[data-mail-retry]')) { loadMailMessages(); return; }
      if (e.target.closest('#btnMailGuideAdd')) { openMailAccountModal(null); return; }
      const item = e.target.closest('[data-mail]');
      if (item) openMailMessage(Number(item.dataset.mail));
    });
    $('mailPager').addEventListener('click', (e) => {
      const b = e.target.closest('[data-mail-page]');
      if (!b) return;
      const pages = Math.max(1, Math.ceil(mailTotal / mailCur.pageSize));
      if (b.dataset.mailPage === 'prev' && mailCur.page > 1) { mailCur.page--; loadMailMessages(); }
      if (b.dataset.mailPage === 'next' && mailCur.page < pages) { mailCur.page++; loadMailMessages(); }
    });
    $('mailPager').addEventListener('change', (e) => {
      if (e.target.id === 'mailPageSize') {
        mailCur.pageSize = parseInt(e.target.value, 10) || 25;
        mailCur.page = 1;
        loadMailMessages();
      }
    });
    $('mailDetail').addEventListener('click', (e) => {
      if (!mailDetail) return;
      if (e.target.closest('[data-mail-translate]')) return mailTranslateBody();
      if (e.target.closest('[data-mail-trans-close]')) { mailTrans = null; renderMailDetail(); return; }
      if (e.target.closest('[data-mail-trans-copy]')) {
        const txt = mailTrans?.text || '';
        if (!txt) { toast('还没有译文可复制', 'error'); return; }
        navigator.clipboard.writeText(txt).then(() => toast('译文已复制', 'success')).catch(() => toast('复制失败', 'error'));
        return;
      }
      if (e.target.closest('[data-mail-reply]')) {
        const subj = /^re:/i.test(mailDetail.subject) ? mailDetail.subject : 'Re: ' + mailDetail.subject;
        openMailCompose(mailDetail.fromAddress, subj);
      } else if (e.target.closest('[data-mail-unread]')) markMailUnread();
      else if (e.target.closest('[data-mail-del]')) removeMailMessage();
    });

    // 账户管理弹窗
    $('btnMaClose').addEventListener('click', () => $('mailAccountModal').classList.add('hidden'));
    $('btnMaCancel').addEventListener('click', () => { resetMailAccountForm(); renderMaList(); });
    $('btnMaSave').addEventListener('click', saveMailAccountForm);
    $('btnMaTest').addEventListener('click', testMailAccountForm);
    $('maProvider').addEventListener('change', () => {
      $('maTestResult').classList.add('hidden');
      applyMailProviderPreset();
    });
    $('maList').addEventListener('click', async (e) => {
      const ed = e.target.closest('[data-ma-edit]');
      const del = e.target.closest('[data-ma-del]');
      const ts = e.target.closest('[data-ma-test]');
      if (ed) { fillMailAccountForm(mailAccounts.find((a) => a.id === ed.dataset.maEdit)); renderMaList(); return; }
      if (ts) {
        const box = $('maTestResult');
        box.className = 'ma-test-result';
        box.classList.remove('hidden');
        box.textContent = '正在连接邮件服务器…';
        try {
          const r = await api(`/api/mail/accounts/${ts.dataset.maTest}/test`, { method: 'POST' });
          box.classList.add('ok');
          box.textContent = `✅ 连接成功：收件箱 ${r.inboxMessages} 封邮件，共 ${r.folderCount} 个文件夹`;
        } catch (err) {
          box.classList.add('err');
          box.textContent = '❌ ' + err.message;
        }
        return;
      }
      if (del) {
        const acc = mailAccounts.find((a) => a.id === del.dataset.maDel);
        if (!acc) return;
        if (!confirm(`确认删除邮箱账户「${acc.label || acc.email}」？\n（仅从本软件移除，不会影响邮箱里的邮件）`)) return;
        try {
          await api('/api/mail/accounts/' + acc.id, { method: 'DELETE' });
          await loadMailAccounts();
          if (mailCur.accountId === acc.id) {
            mailCur = { accountId: '', folder: 'INBOX', folderName: '收件箱', page: 1, pageSize: mailCur.pageSize, search: '' };
            mailFolders = []; mailFoldersOwner = ''; mailMessages = []; mailTotal = 0; mailDetail = null;
            renderMailView();
          }
          if (mailMaEditId === acc.id) resetMailAccountForm();
          renderMaList();
          renderMailSide();
          toast('账户已移除', 'success');
        } catch (err) { toast(err.message, 'error'); }
      }
    });

    // 写邮件弹窗
    $('btnMcClose').addEventListener('click', () => $('mailComposeModal').classList.add('hidden'));
    $('btnMcCancel').addEventListener('click', () => $('mailComposeModal').classList.add('hidden'));
    $('btnMcSend').addEventListener('click', sendMailCompose);
  }

  // 进入邮箱视图（首次进入时懒加载账户，避免拖慢启动）
  async function enterMailView() {
    renderMailView();
    if (mailInited) return;
    mailInited = true;
    try {
      await ensureMailProviders();
      renderMaProviderOptions();
      await loadMailAccounts();
    } catch (e) {
      toast('读取邮箱账户失败：' + e.message, 'error');
    }
    renderMailView();
    if (mailAccounts.length && !mailCur.accountId) await selectMailAccount(mailAccounts[0].id);
  }


  /* ---- 本项目的入口：渲染这一页 → 绑事件 → 拉账户 ---- */
  async function renderMail(m){
    m.innerHTML = MAIL_HTML;
    bindMailEvents();
    await enterMailView();
    startMailNotify();
  }

/* ---------------- 知识星图（知识库 → 项目 → 五簇分支 → 条目） ----------------
   整页是独立文档 kb.html：星图的样式与类名自成一套，塞进这个单页会跟现有样式打架。
   层级全部在星图内部选（项目是第一级），这里只负责挂 iframe、接收「打开某条」请求。 */