"""Feishu app-bot delivery and real, state-checked OAuth authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import service_settings as store

API = "https://open.feishu.cn/open-apis"
AUTHORIZE = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
REGISTRATION_FEISHU = "https://accounts.feishu.cn"
REGISTRATION_LARK = "https://accounts.larksuite.com"
REGISTRATION_PATH = "/oauth/v1/app/registration"
REGISTRATION_DEFAULT_INTERVAL = 5
REGISTRATION_DEFAULT_EXPIRES = 600
REGISTRATION_SERVICE = "feishu_registration"
_REGISTRATION_LOCK = threading.RLock()
DOCS = {
    "console": "https://open.feishu.cn/app",
    "oauth": "https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code",
    "bot": "https://open.feishu.cn/document/server-docs/im-v1/message/create",
    "webhook": "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
}


def request(path: str, payload: dict | None = None, token: str = "", api: str = API) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(api + path, headers=headers,
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
        for key in ("access_token", "refresh_token", "authorized", "expires_at", "registration_region"):
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


def _registration_domain(region: str) -> str:
    return REGISTRATION_LARK if str(region or "").strip().lower() in {"lark", "international", "overseas"} else REGISTRATION_FEISHU


def _registration_region(domain: str) -> str:
    return "lark" if REGISTRATION_LARK in str(domain or "") else "feishu"


def _registration_http(domain: str, payload: dict[str, str]) -> dict:
    """Make a device-flow form request; non-2xx JSON can be a poll status."""
    endpoint = str(domain).rstrip("/") + REGISTRATION_PATH
    req = urllib.request.Request(endpoint, data=urllib.parse.urlencode(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw or b"{}")
        except (TypeError, ValueError):
            raise ValueError(f"飞书注册请求失败 HTTP {exc.code}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"飞书注册请求失败 HTTP {exc.code}") from exc
        return data
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError("连接飞书注册服务失败，请稍后重试") from exc
    try:
        data = json.loads(raw or b"{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("飞书注册服务返回了无效响应") from exc
    if not isinstance(data, dict):
        raise ValueError("飞书注册服务返回了无效响应")
    return data


def _registration_public(session: dict, aid: str, *, switched: bool = False) -> dict:
    expires_at = float(session.get("expires_at") or time.time())
    return {"status": "pending", "assistant_id": aid, "session_id": session.get("session_id", ""),
            "region": session.get("region") or _registration_region(session.get("domain", "")),
            "qr_url": session.get("qr_url", ""),
            "verification_uri_complete": session.get("qr_url", ""),
            "qr_data_uri": session.get("qr_data_uri", ""),
            "interval": max(1, int(session.get("interval") or REGISTRATION_DEFAULT_INTERVAL)),
            "expires_in": max(0, int(expires_at - time.time())), "switched": switched}


def _registration_write_current(db: Any, aid: str, session_id: str, session: dict) -> None:
    """Persist an update only while this opaque session remains active."""
    with _REGISTRATION_LOCK:
        current = store.read(db, REGISTRATION_SERVICE, aid)
        if not current or not secrets.compare_digest(str(current.get("session_id") or ""), str(session_id)):
            raise ValueError("飞书扫码绑定会话已替换")
        store.write(db, REGISTRATION_SERVICE, session, aid)


def _registration_clear_current(db: Any, aid: str, session_id: str) -> None:
    _registration_write_current(db, aid, session_id, {})


def registration_begin(db: Any, aid: str, body: dict | None = None) -> dict:
    """Open an assistant-scoped PersonalAgent device authorization session."""
    if not aid or not db.assistant(aid):
        raise ValueError("助手不存在")
    body = body or {}
    requested_region = str(body.get("region") or body.get("tenant") or "feishu").strip().lower()
    if requested_region not in {"feishu", "lark", "international", "overseas"}:
        raise ValueError("不支持的飞书区域")
    domain = _registration_domain(requested_region)
    result = _registration_http(domain, {"action": "begin", "archetype": "PersonalAgent",
                                         "auth_method": "client_secret", "request_user_info": "open_id"})
    if result.get("error"):
        raise ValueError(str(result.get("error_description") or result.get("error")))
    device_code = str(result.get("device_code") or "")
    qr_url = str(result.get("verification_uri_complete") or "")
    if not device_code or not qr_url:
        raise ValueError("飞书注册服务未返回完整扫码信息")
    parsed = urllib.parse.urlparse(qr_url)
    if parsed.scheme != "https" or parsed.hostname not in {"accounts.feishu.cn", "accounts.larksuite.com"}:
        raise ValueError("飞书注册服务返回了不受信任的扫码地址")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query.update({"from": ["sdk"], "tp": ["sdk"], "source": ["research-workbench"]})
    name = str(body.get("name") or body.get("name_preset") or "").strip()
    if name:
        query["name"] = [name[:80]]
    qr_url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))
    qr_data_uri = ""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage

        code = qrcode.QRCode(box_size=5, border=2)
        code.add_data(qr_url)
        code.make(fit=True)
        image = code.make_image(image_factory=SvgPathImage)
        output = io.BytesIO()
        image.save(output)
        qr_data_uri = "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except Exception:
        # Keep the official URI available as a direct-link fallback when the
        # optional QR package is missing or SVG rendering is unavailable.
        pass
    interval = max(1, min(int(result.get("interval") or REGISTRATION_DEFAULT_INTERVAL), 120))
    expires = max(30, min(int(result.get("expires_in") or result.get("expire_in") or REGISTRATION_DEFAULT_EXPIRES), 86400))
    session = {"assistant_id": aid, "session_id": secrets.token_urlsafe(24), "device_code": device_code,
               "qr_url": qr_url, "qr_data_uri": qr_data_uri, "domain": domain,
               "region": _registration_region(domain), "interval": interval,
               "expires_at": time.time() + expires, "created_at": time.time()}
    with _REGISTRATION_LOCK:
        store.write(db, REGISTRATION_SERVICE, session, aid)
    return _registration_public(session, aid)


def registration_poll(db: Any, aid: str, session_id: str = "") -> dict:
    if not aid or not db.assistant(aid):
        raise ValueError("助手不存在")
    session = store.read(db, REGISTRATION_SERVICE, aid)
    if not session or session.get("assistant_id") != aid:
        raise ValueError("没有进行中的飞书扫码绑定")
    if not session_id or not secrets.compare_digest(str(session.get("session_id") or ""), str(session_id)):
        raise ValueError("飞书扫码绑定会话无效或已替换")
    if float(session.get("expires_at") or 0) <= time.time():
        _registration_clear_current(db, aid, session_id)
        return {"status": "expired", "assistant_id": aid, "code": "expired_token",
                "message": "扫码已过期，请重新开始绑定"}
    result = _registration_http(str(session.get("domain") or REGISTRATION_FEISHU),
                                {"action": "poll", "device_code": str(session.get("device_code") or "")})
    # A new begin/cancel may have replaced this session while the network call
    # was in flight. Never let an older poll complete or mutate its successor.
    with _REGISTRATION_LOCK:
        current = store.read(db, REGISTRATION_SERVICE, aid)
        if not current or not secrets.compare_digest(str(current.get("session_id") or ""), str(session_id)):
            raise ValueError("飞书扫码绑定会话已替换")
    brand = str((result.get("user_info") or {}).get("tenant_brand") or "").lower()
    domain = str(session.get("domain") or REGISTRATION_FEISHU)
    if brand in {"lark", "feishu"} and _registration_region(domain) != brand:
        session["domain"], session["region"] = _registration_domain(brand), brand
        _registration_write_current(db, aid, session_id, session)
        out = _registration_public(session, aid, switched=True)
        out["immediate"] = True
        return out
    status = str(result.get("error") or "")
    if status == "slow_down":
        session["interval"] = min(120, int(session.get("interval") or REGISTRATION_DEFAULT_INTERVAL) + 5)
        _registration_write_current(db, aid, session_id, session)
        out = _registration_public(session, aid)
        out["status"] = "slow_down"
        return out
    if status == "authorization_pending" or (not status and not result.get("client_id")):
        return _registration_public(session, aid)
    if status in {"access_denied", "expired_token"}:
        _registration_clear_current(db, aid, session_id)
        return {"status": "denied" if status == "access_denied" else "expired", "assistant_id": aid,
                "code": status, "message": "你取消了绑定" if status == "access_denied" else "扫码已过期，请重新开始绑定"}
    if status:
        _registration_clear_current(db, aid, session_id)
        return {"status": "error", "assistant_id": aid, "code": status,
                "message": str(result.get("error_description") or status)}
    app_id = str(result.get("client_id") or "")
    app_secret = str(result.get("client_secret") or "")
    open_id = str((result.get("user_info") or {}).get("open_id") or "")
    if not app_id or not app_secret or not open_id:
        return {"status": "error", "assistant_id": aid, "code": "invalid_response",
                "message": "飞书注册服务未返回完整凭据"}
    data = config(db, aid)
    data.update({"app_id": app_id, "app_secret": app_secret, "receive_id": open_id,
                 "receive_id_type": "open_id", "authorized": True,
                 "registration_region": _registration_region(domain), "updated_at": int(time.time())})
    for key in ("access_token", "refresh_token", "expires_at"):
        data.pop(key, None)
    with _REGISTRATION_LOCK:
        current = store.read(db, REGISTRATION_SERVICE, aid)
        if not current or not secrets.compare_digest(str(current.get("session_id") or ""), str(session_id)):
            raise ValueError("飞书扫码绑定会话已替换")
        store.write(db, "feishu", data, aid)
        store.write(db, REGISTRATION_SERVICE, {}, aid)
    return {"status": "success", "assistant_id": aid, "open_id": open_id,
            "region": _registration_region(domain), "config": store.public(data)}


def registration_cancel(db: Any, aid: str, session_id: str = "") -> dict:
    if not aid or not db.assistant(aid):
        raise ValueError("助手不存在")
    session = store.read(db, REGISTRATION_SERVICE, aid)
    if not session or not session_id or not secrets.compare_digest(str(session.get("session_id") or ""), str(session_id)):
        raise ValueError("飞书扫码绑定会话无效或已替换")
    with _REGISTRATION_LOCK:
        current = store.read(db, REGISTRATION_SERVICE, aid)
        if not current or not secrets.compare_digest(str(current.get("session_id") or ""), str(session_id)):
            raise ValueError("飞书扫码绑定会话已替换")
        store.write(db, REGISTRATION_SERVICE, {}, aid)
    return {"status": "cancelled", "assistant_id": aid}


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
    api = "https://open.larksuite.com/open-apis" if data.get("registration_region") == "lark" else API
    tenant = request("/auth/v3/tenant_access_token/internal", {"app_id": data["app_id"], "app_secret": data["app_secret"]}, api=api)
    token = tenant.get("tenant_access_token")
    if not token:
        raise ValueError("飞书未返回机器人租户令牌")
    result = request("/im/v1/messages?receive_id_type=" + urllib.parse.quote(data.get("receive_id_type") or "open_id"),
                     {"receive_id": recipient, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}, token, api=api)
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
    if method == "POST" and path == "/api/feishu/registration/begin":
        return registration_begin(db, aid, body)
    if method == "POST" and path == "/api/feishu/registration/poll":
        return registration_poll(db, aid, str(body.get("session_id") or ""))
    if method == "POST" and path == "/api/feishu/registration/cancel":
        return registration_cancel(db, aid, str(body.get("session_id") or ""))
    if method == "GET" and path == "/api/feishu/callback":
        return callback(db, query)
    if method == "POST" and path == "/api/feishu/test":
        return send(db, aid, str(body.get("text") or "览籍智研飞书机器人连接测试"))
    return None
