const MessageChannels=(()=>{
  const h=value=>esc(String(value??''));
  async function mount(host){
    try{
      const data=await api('/api/message-channels');if(!host.isConnected)return;
      host.innerHTML=`<h2>消息渠道</h2><p class="hint">连接研究工作与消息应用。飞书支持双向对话；其他渠道支持向指定收件目标推送消息。</p><div class="channel-list">${data.channels.map(channel=>`<button type="button" class="channel-row" data-channel="${channel.id}"><strong>${h(channel.name)}</strong><span>${h(channel.capability)}</span><small>${channel.id==='feishu'?'查看绑定与连接状态':!channel.enabled?'未连接':channel.last_status==='ok'?'已验证':channel.last_status==='failed'?'发送失败':'已配置 · 待验证'}</small><b>›</b></button>`).join('')}</div><div data-channel-detail></div>`;
      host.querySelectorAll('[data-channel]').forEach(button=>button.onclick=()=>{
        if(button.dataset.channel==='feishu')return IntegrationSettings.openFeishu();
        const channel=data.channels.find(item=>item.id===button.dataset.channel),detail=host.querySelector('[data-channel-detail]');
        detail.innerHTML=`<form class="card channel-form"><h3>${h(channel.name)}</h3><label><input type="checkbox" name="enabled" ${channel.enabled?'checked':''}> 启用消息推送</label>${channel.id==='telegram'?`<label>Bot token<input class="control" name="token" type="password" autocomplete="new-password" placeholder="${channel.token_set?'已保存，留空保留':'BotFather 提供的 token'}"></label><label>收件 chat_id<input class="control" name="chat_id" value="${h(channel.chat_id||'')}"></label>`:`<label>Webhook 地址<input class="control" name="webhook" type="password" autocomplete="new-password" placeholder="${channel.webhook_set?'已保存，留空保留':'官方 HTTPS Webhook 地址'}"></label>${channel.id==='dingtalk'?`<label>加签密钥<input class="control" name="secret" type="password" autocomplete="new-password" placeholder="${channel.secret_set?'已保存，留空保留':'如机器人开启加签，请填写 SEC…'}"></label>`:''}`}<p class="hint">凭据在本机加密保存。保存配置不会发送消息。</p><div class="integration-actions"><button class="btn" type="submit">保存配置</button><button type="button" class="btn ghost" data-test>发送测试消息</button><button type="button" class="btn ghost" data-disconnect>断开连接</button></div><details><summary>向已保存的收件目标发送消息</summary><label>消息正文<textarea class="control" data-message rows="4" placeholder="填写要发送的研究进展或结果摘要"></textarea></label><button type="button" class="btn ghost" data-send>发送消息</button></details><p role="status" data-result></p></form>`;
        const status=detail.querySelector('[data-result]');
        const work=async(fn,success)=>{detail.querySelectorAll('button').forEach(item=>item.disabled=true);try{await fn();status.textContent=success;}catch(err){status.textContent=err.message;}finally{detail.querySelectorAll('button').forEach(item=>item.disabled=false);}};
        detail.querySelector('form').onsubmit=event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));values.enabled=event.target.elements.enabled.checked;work(()=>api('/api/message-channels/'+channel.id+'/save',values),'配置已保存；可发送测试消息验证。');};
        detail.querySelector('[data-send]').onclick=()=>work(()=>api('/api/message-channels/'+channel.id+'/send',{text:detail.querySelector('[data-message]').value}),'消息已发送。');
        detail.querySelector('[data-test]').onclick=()=>work(()=>api('/api/message-channels/'+channel.id+'/test',{}),'测试消息已发送。');
        detail.querySelector('[data-disconnect]').onclick=()=>work(async()=>{await api('/api/message-channels/'+channel.id+'/disconnect',{});await mount(host);},'已断开');
        detail.scrollIntoView({block:'nearest'});
      });
    }catch(err){host.textContent=err.message;}
  }
  return {mount};
})();
