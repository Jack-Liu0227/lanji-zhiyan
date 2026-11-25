"""Small capability probes through the production model adapter."""
from __future__ import annotations


def generate(profile, capability="text", *, source="model_check", db=None):
    import account_providers
    import model_health
    import workflow

    if not profile:
        raise ValueError("请先选择模型")
    if capability not in ("text", "tools", "vision"):
        raise ValueError("未知的模型测试能力")
    if capability == "vision" and profile.get("api") in account_providers.WIRES:
        raise ValueError("当前账号接口不支持图片输入")
    prompt = "请回复一行短文本，确认能正常生成内容。"
    tools = None
    if capability == "tools":
        prompt = "请仅调用 check_connection 工具，不要输出文本。"
        tools = [{"type": "function", "function": {"name": "check_connection", "description": "确认工具调用连接",
                  "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]
    if capability == "vision":
        prompt = [{"type": "text", "text": "请确认能读取这张测试图片。"},
                  {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                   "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRuoAAAAASUVORK5CYII="}}]
    context = {**profile.get("_usage_context", {}), "source": source}
    try:
        result = workflow.chat_raw({**profile, "_usage_context": context}, [{"role": "user", "content": prompt}],
                                   tools=tools, max_tokens=512, timeout=45)
        reply = (result.get("content") or "").strip()
        if capability == "tools":
            if not any(call.get("function", {}).get("name") == "check_connection" for call in result.get("tool_calls", [])):
                raise ValueError("模型未返回预期的工具调用")
        elif not reply:
            raise ValueError("模型未生成文本，测试未通过")
    except Exception as exc:
        if db is not None and profile.get("id"):
            model_health.record_failure(
                db, profile["id"], exc,
                error_kind=getattr(exc, "error_kind", "") or ("model_unavailable" if isinstance(exc, ValueError) else "transient"),
                error_code=getattr(exc, "error_code", ""),
                provider_id=profile.get("provider_id", ""),
                capability=capability,
            )
        raise
    if db is not None and profile.get("id"):
        model_health.record_success(db, profile["id"], capability=capability)
    return {"ok": True, "model": profile["model"], "capability": capability, "reply": reply[:300], "tested_at": workflow.now()}
