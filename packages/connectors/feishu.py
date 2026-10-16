"""Feishu app-bot delivery and real, state-checked OAuth authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import service_settings as store

API = "https://open.feishu.cn/open-apis"
AUTHORIZE = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
DOCS = {
    "console": "https://open.feishu.cn/app",
    "oauth": "https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code",
    "bot": "https://open.feishu.cn/document/server-docs/im-v1/message/create",
    "webhook": "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
}


def request(path: str, payload: dict | None = None, token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API + path, headers=headers,
                                 data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Do not reflect response bodies that can contain credentials.
        raise ValueError(f"飞书请求失败 HTTP {exc.code}，请检查应用权限、凭据和回调白名单") from exc
    if data.get("code", 0) != 0 or data.get("error"):
        raise ValueError("飞书拒绝请求：" + str(data.get("msg") or data.get("error_description") or data.get("error")))
    return data


def config(db: Any, assistant_id: str) -> dict:
    return store.read(db, "feishu", assistant_id)


def save(db: Any, body: dict) -> dict:
    aid = str(body.get("assistant_id") or "")
    if aid and not db.assistant(aid):
        raise ValueError("助手不存在")
    allowed = {"app_id", "app_secret", "redirect_uri", "scopes", "receive_id", "receive_id_type",
               "webhook", "signing_secret", "enabled"}
    old = config(db, aid)
    data = store.merge_secrets(old, body, allowed)
    if data.get("webhook"):
        parts = urllib.parse.urlparse(data["webhook"])
        if parts.scheme != "https" or parts.hostname != "open.feishu.cn" or not parts.path.startswith("/open-apis/bot/v2/hook/"):
            raise ValueError("只接受飞书官方 HTTPS 自定义机器人 Webhook")
    if data.get("receive_id_type", "open_id") not in {"open_id", "user_id", "union_id", "email", "chat_id"}:
        raise ValueError("不支持的飞书收件人类型")
    if old.get("app_id") != data.get("app_id") or (body.get("app_secret") and old.get("app_secret") != body["app_secret"]):
        for key in ("access_token", "refresh_token", "authorized", "expires_at"):
            data.pop(key, None)
    store.write(db, "feishu", data, aid)
    return {"config": store.public(data), "docs": DOCS}


def authorize(db: Any, aid: str) -> dict:
    data = config(db, aid)
    if not data.get("app_id") or not data.get("app_secret") or not data.get("redirect_uri"):
        raise ValueError("请先保存 App ID、App Secret 和已在飞书安全设置登记的回调地址")
    redirect = urllib.parse.urlparse(data["redirect_uri"])
    if redirect.scheme not in {"https", "http"} or not redirect.netloc:
        raise ValueError("回调地址必须是完整 HTTP(S) URL")
    state = secrets.token_urlsafe(32)
    store.write(db, "feishu_oauth", {"assistant_id": aid, "expires_at": time.time() + 600}, hashlib.sha256(state.encode()).hexdigest())
    params = {"client_id": data["app_id"], "response_type": "code", "redirect_uri": data["redirect_uri"],
              "state": state, "scope": data.get("scopes") or "contact:user.base:readonly offline_access"}
    return {"url": AUTHORIZE + "?" + urllib.parse.urlencode(params), "expires_in": 600,
            "hint": "在飞书官方授权页用手机扫码并确认；完成回调后才会显示已授权。扫码不会自动创建或发布应用机器人。"}


def callback(db: Any, query: dict) -> dict:
    state = str((query.get("state") or [""])[0])
    key = hashlib.sha256(state.encode()).hexdigest()
    pending = store.read(db, "feishu_oauth", key)
    if not state or not pending or pending.get("expires_at", 0) < time.time():
        raise ValueError("授权状态无效或已过期，请重新扫码")
    store.write(db, "feishu_oauth", {}, key)  # Single-use, including failed exchanges.
    if query.get("error"):
        raise ValueError("用户取消了飞书授权，请返回应用重新授权")
    code = str((query.get("code") or [""])[0])
    if not code:
        raise ValueError("飞书没有返回授权码")
    aid = pending["assistant_id"]
    data = config(db, aid)
    result = request("/authen/v2/oauth/token", {"grant_type": "authorization_code", "client_id": data["app_id"],
                     "client_secret": data["app_secret"], "code": code, "redirect_uri": data["redirect_uri"]})
    token = result.get("access_token") or (result.get("data") or {}).get("access_token")
    if not token:
        raise ValueError("飞书未返回 access_token，授权未完成")
    data.update(access_token=token, refresh_token=result.get("refresh_token", ""),
                expires_at=time.time() + int(result.get("expires_in") or 7200), authorized=True)
    if not data.get("receive_id"):
        user = request("/authen/v1/user_info", token=token).get("data") or {}
        if user.get("open_id"):
            data.update(receive_id=user["open_id"], receive_id_type="open_id")
    store.write(db, "feishu", data, aid)
    return {"authorized": True, "assistant_id": aid, "hint": "扫码授权完成。机器人发信仍需应用已启用机器人、发布并具备 im:message:send_as_bot 权限。"}


def send(db: Any, aid: str, text: str, receive_id: str = "") -> dict:
    data = config(db, aid)
    if not data.get("enabled"):
        raise ValueError("该助手的飞书机器人未启用")
    if not str(text).strip():
        raise ValueError("消息不能为空")
    if data.get("webhook"):
        payload = {"msg_type": "text", "content": {"text": text}}
        if data.get("signing_secret"):
            stamp = str(int(time.time()))
            signature = hmac.new((stamp + "\n" + data["signing_secret"]).encode(), b"", hashlib.sha256).digest()
            payload.update(timestamp=stamp, sign=base64.b64encode(signature).decode())
        req = urllib.request.Request(data["webhook"], data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ValueError(f"飞书 Webhook 请求失败 HTTP {exc.code}") from exc
        if result.get("code", result.get("StatusCode", 0)) != 0:
            raise ValueError("飞书 Webhook 发送失败：" + str(result.get("msg") or result.get("StatusMessage")))
        return {"ok": True, "mode": "webhook"}
    if not data.get("app_id") or not data.get("app_secret"):
        raise ValueError("请配置应用凭据或自定义机器人 Webhook")
    recipient = receive_id or data.get("receive_id")
    if not recipient:
        raise ValueError("请先扫码绑定收件人，或填写收件人/群聊 ID")
    tenant = request("/auth/v3/tenant_access_token/internal", {"app_id": data["app_id"], "app_secret": data["app_secret"]})
    token = tenant.get("tenant_access_token")
    if not token:
        raise ValueError("飞书未返回机器人租户令牌")
    result = request("/im/v1/messages?receive_id_type=" + urllib.parse.quote(data.get("receive_id_type") or "open_id"),
                     {"receive_id": recipient, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}, token)
    return {"ok": True, "message_id": (result.get("data") or {}).get("message_id", "")}


def route(method: str, path: str, body: dict, query: dict, db: Any) -> dict | None:
    if not path.startswith("/api/feishu/"):
        return None
    aid = str(body.get("assistant_id") or (query.get("assistant_id") or [""])[0])
    if method == "GET" and path == "/api/feishu/config":
        return {"config": store.public(config(db, aid)), "docs": DOCS}
    if method == "POST" and path == "/api/feishu/config":
        return save(db, body)
    if method == "POST" and path == "/api/feishu/authorize":
        return authorize(db, aid)
    if method == "GET" and path == "/api/feishu/callback":
        return callback(db, query)
    if method == "POST" and path == "/api/feishu/test":
        return send(db, aid, str(body.get("text") or "览籍智研飞书机器人连接测试"))
    return None
