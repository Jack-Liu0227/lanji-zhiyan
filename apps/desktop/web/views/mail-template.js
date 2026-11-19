const MAIL_HTML = `<div class="mail-page"><!-- ===== 视图：邮箱 ===== -->
      <section id="viewMail" class="view">
        <div class="mail-wrap">
          <!-- 左栏：账户与文件夹 -->
          <aside class="mail-side">
            <div class="mail-side-head">
              <b>📧 邮箱</b>
              <button class="btn-ghost as-link" id="btnMailManage">＋ 账户管理</button>
            </div>
            <div id="mailAccounts" class="mail-accounts"></div>
            <div class="mail-side-sep"></div>
            <div id="mailFolders" class="mail-folders"></div>
          </aside>

          <!-- 中栏：邮件列表 -->
          <div class="mail-list-col">
            <div class="mail-list-head">
              <input id="mailSearch" class="tb-input mail-search" placeholder="搜索主题 / 发件人，回车确认…" />
              <button class="tb-btn" id="btnMailRefresh" title="刷新当前文件夹">⟳</button>
              <button class="tb-btn" id="btnMailMarkAllRead" title="将当前文件夹所有未读邮件标记为已读">✓ 全部已读</button>
              <button class="tb-btn" id="btnMailCompose" title="写邮件">✎ 写邮件</button>
            </div>
            <div id="mailListHead" class="mail-list-title"></div>
            <div id="mailList" class="mail-list"></div>
            <div id="mailPager" class="mail-pager"></div>
          </div>

          <!-- 右栏：邮件详情 -->
          <div class="mail-detail-col">
            <div id="mailDetail" class="mail-detail"></div>
          </div>
        </div>
      </section>

<div id="mailAccountModal" class="modal hidden">
    <div class="modal-mask"></div>
    <div class="modal-dialog modal-wide">
      <div class="modal-head"><h3>📧 邮箱账户管理</h3><button class="modal-close" id="btnMaClose">✕</button></div>
      <div class="modal-body">
        <p class="modal-desc">支持添加多个邮箱账户（如个人 163 邮箱 + 学校邮箱），随时切换查看。不需要邮箱也可以留空，不影响其他功能。<br />账号与授权码仅保存在<b>本机数据目录</b>（mail.json），不会上传到任何服务器。</p>
        <div id="maList" class="ma-list"></div>

        <div class="field-sep"></div>
        <h4 class="ma-form-title" id="maFormTitle">添加邮箱账户</h4>
        <div class="prof-grid">
          <label class="field"><span>邮箱服务商</span>
            <select id="maProvider" class="tb-select"></select>
          </label>
          <label class="field"><span>备注名称（可留空）</span>
            <input id="maLabel" class="tb-input" placeholder="如：个人邮箱 / 学校邮箱" />
          </label>
          <label class="field"><span>邮箱地址 *</span>
            <input id="maEmail" class="tb-input" placeholder="xxx@163.com" autocomplete="off" />
          </label>
          <label class="field"><span>授权码 / 密码 *</span>
            <input id="maPassword" type="password" class="tb-input" placeholder="网易/QQ 邮箱填授权码" autocomplete="new-password" />
          </label>
        </div>
        <p class="modal-desc" id="maNote"></p>
        <div class="modal-desc" id="maHelpLinks"></div>

        <details class="ma-advanced">
          <summary>高级设置（服务器地址与端口，一般无需修改）</summary>
          <div class="prof-grid">
            <label class="field"><span>IMAP 服务器</span><input id="maImapHost" class="tb-input" placeholder="imap.163.com" /></label>
            <label class="field"><span>IMAP 端口</span><input id="maImapPort" class="tb-input" placeholder="993" /></label>
            <label class="field"><span>SMTP 服务器</span><input id="maSmtpHost" class="tb-input" placeholder="smtp.163.com" /></label>
            <label class="field"><span>SMTP 端口</span><input id="maSmtpPort" class="tb-input" placeholder="465" /></label>
          </div>
          <div class="ma-checks">
            <label><input type="checkbox" id="maImapSecure" checked /> IMAP 使用 SSL/TLS</label>
            <label><input type="checkbox" id="maSmtpSecure" checked /> SMTP 使用 SSL/TLS</label>
            <label><input type="checkbox" id="maSelfSigned" /> 忽略证书校验（学校/企业自建邮箱）</label>
          </div>
        </details>
        <div id="maTestResult" class="ma-test-result hidden"></div>
      </div>
      <div class="modal-foot">
        <button id="btnMaTest" class="btn">🔌 测试连接</button>
        <span class="ob-spacer"></span>
        <button id="btnMaSave" class="btn btn-primary">保存账户</button>
        <button id="btnMaCancel" class="btn">取消编辑</button>
      </div>
    </div>
  </div>
<div id="mailComposeModal" class="modal hidden">
    <div class="modal-mask"></div>
    <div class="modal-dialog modal-wide">
      <div class="modal-head"><h3>✎ 写邮件</h3><button class="modal-close" id="btnMcClose">✕</button></div>
      <div class="modal-body">
        <div class="prof-grid">
          <label class="field"><span>发件账户</span><select id="mcFrom" class="tb-select"></select></label>
          <label class="field"><span>收件人 *</span><input id="mcTo" class="tb-input" placeholder="多个收件人用英文逗号分隔" /></label>
          <label class="field"><span>抄送</span><input id="mcCc" class="tb-input" placeholder="可留空" /></label>
          <label class="field"><span>主题</span><input id="mcSubject" class="tb-input" placeholder="邮件主题" /></label>
        </div>
        <label class="field"><span>正文</span><textarea id="mcBody" rows="10" placeholder="在此输入邮件内容…"></textarea></label>
      </div>
      <div class="modal-foot">
        <button id="btnMcSend" class="btn btn-primary">发送</button>
        <button id="btnMcCancel" class="btn">取消</button>
      </div>
    </div>
  </div></div>`;

  /* ---- 小工具 ---- */
  function toast(msg, type){
    let el = document.getElementById("toast");
    if(!el){ el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
    el.textContent = msg; el.className = "toast " + (type || "");
    requestAnimationFrame(()=>el.classList.add("show"));
    clearTimeout(el._t); el._t = setTimeout(()=>el.classList.remove("show"), 2800);
  }
  function stripHtml(html){ const d = document.createElement("div"); d.innerHTML = html || ""; return d.textContent || ""; }
