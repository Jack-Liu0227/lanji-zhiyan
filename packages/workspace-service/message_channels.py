"""Message channel configuration and explicit outbound tests; Feishu keeps its bridge."""
import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
import securestore
import service_settings

CATALOG=[{'id':'feishu','name':'飞书','capability':'对话、任务与消息推送'},
         {'id':'slack','name':'Slack','capability':'Webhook 消息推送'},
         {'id':'dingtalk','name':'钉钉','capability':'群机器人消息推送'},
         {'id':'wecom','name':'企业微信','capability':'群机器人消息推送'},
         {'id':'telegram','name':'Telegram','capability':'Bot 消息推送'}]
SECRET_FIELDS=('webhook','token','secret')


def read(db, channel):
    return service_settings.read(db,'message_channels',channel)


def write(db, channel, value):
    return service_settings.write(db,'message_channels',value,channel)


def public(db, channel):
    cfg=read(db,channel)
    return {**{key:value for key,value in cfg.items() if key not in SECRET_FIELDS},
            **{key+'_set':bool(cfg.get(key)) for key in SECRET_FIELDS}}


def validate(channel, cfg):
    if channel=='telegram':
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{15,}',cfg.get('token','')):raise ValueError('请输入有效的 Telegram Bot token')
        if not re.fullmatch(r'-?\d+|@[A-Za-z0-9_]+',cfg.get('chat_id','')):raise ValueError('请输入目标 chat_id 或 @channel')
    else:
        parsed=urllib.parse.urlsplit(cfg.get('webhook',''))
        allowed={'slack':('hooks.slack.com','/services/'),'dingtalk':('oapi.dingtalk.com','/robot/send'),'wecom':('qyapi.weixin.qq.com','/cgi-bin/webhook/send')}[channel]
        if parsed.scheme!='https' or parsed.hostname!=allowed[0] or not parsed.path.startswith(allowed[1]) or parsed.username or parsed.port not in (None,443):
            raise ValueError('请使用该渠道官方 HTTPS Webhook 地址')


def reveal(cfg):return {**cfg,**{key:securestore.unprotect(cfg.get(key,'')) if cfg.get(key) else '' for key in SECRET_FIELDS}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def send(db,channel,text):
    saved=read(db,channel);cfg=reveal(saved)
    if not cfg.get('enabled'):raise ValueError('请先启用并保存渠道')
    validate(channel,cfg)
    if not text or len(text.encode('utf-8'))>2000:raise ValueError('消息须为 1 至 2000 字节')
    url=cfg.get('webhook','')
    if channel=='telegram':
        url='https://api.telegram.org/bot'+cfg['token']+'/sendMessage';payload={'chat_id':cfg['chat_id'],'text':text}
    elif channel=='slack':payload={'text':text}
    else:
        payload={'msgtype':'text','text':{'content':text}}
        if channel=='dingtalk' and cfg.get('secret'):
            stamp=str(int(time.time()*1000));secret=cfg['secret']
            sign=base64.b64encode(hmac.new(secret.encode(),(stamp+'\n'+secret).encode(),hashlib.sha256).digest()).decode()
            url+='&'+urllib.parse.urlencode({'timestamp':stamp,'sign':sign})
    request=urllib.request.Request(url,json.dumps(payload).encode('utf-8'),{'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=20) as response:raw=response.read(65536).decode('utf-8')
        if channel=='slack':ok=raw.strip()=='ok'
        else:
            data=json.loads(raw);ok=data.get('ok') is True if channel=='telegram' else data.get('errcode')==0
        if not ok:raise ValueError('渠道拒绝消息，请检查机器人权限、关键词及收件目标')
    except (urllib.error.URLError,ValueError,OSError):
        # URLs contain tokens. Never persist or echo transport exception strings.
        saved.update(last_status='failed',last_checked=datetime.now(timezone.utc).isoformat());write(db,channel,saved)
        raise ValueError('发送失败：请检查网络、机器人权限、关键词和收件目标') from None
    saved.update(last_status='ok',last_checked=datetime.now(timezone.utc).isoformat());write(db,channel,saved)
    return {'ok':True,'message':'消息已送达渠道'}


def route(method,path,body,query,db):
    if not path.startswith('/api/message-channels'):return None
    if method=='GET' and path=='/api/message-channels':
        return {'channels':[{**item,**public(db,item['id'])} for item in CATALOG]}
    parts=path.split('/');channel=parts[3] if len(parts)>3 else ''
    if channel not in ('slack','dingtalk','wecom','telegram'):raise ValueError('此渠道请使用飞书连接设置')
    if method=='POST' and path.endswith('/save'):
        cfg=read(db,channel)
        for key in SECRET_FIELDS:
            if key in body and str(body[key]).strip():cfg[key]=securestore.protect(str(body[key]).strip())
        cfg['chat_id']=str(body.get('chat_id',cfg.get('chat_id',''))).strip();cfg['enabled']=bool(body.get('enabled',True))
        if cfg['enabled']:validate(channel,reveal(cfg))
        cfg.pop('last_status',None);cfg.pop('last_checked',None);write(db,channel,cfg)
        return public(db,channel)
    if method=='POST' and path.endswith('/test'):return send(db,channel,str(body.get('text') or '览籍智研：消息渠道连接测试'))
    if method=='POST' and path.endswith('/send'):return send(db,channel,str(body.get('text') or ''))
    if method=='POST' and path.endswith('/disconnect'):
        write(db,channel,{});return {'ok':True}
    raise ValueError('不支持的消息渠道操作')
