"""Small protocol adapters sharing the workbench's guarded HTTP transport."""
from __future__ import annotations

import json
from typing import Callable
from urllib.parse import quote
from urllib.request import Request


def _request(url, payload, headers, timeout):
    import workflow
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={"Content-Type": "application/json", **headers}, method="POST" if payload is not None else "GET")
    with workflow.open_url(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8", "replace"))


def _stream_events(url, payload, headers, timeout, cancel_check=None):
    import workflow
    req = Request(url, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json", "Accept": "text/event-stream", **headers}, method="POST")
    with workflow.open_url(req, timeout=timeout) as res:
        data = []
        for raw in res:
            if cancel_check and cancel_check():
                raise RuntimeError("运行已中断")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                body = "\n".join(data)
                data.clear()
                if body != "[DONE]":
                    yield json.loads(body)
        if data and "\n".join(data) != "[DONE]":
            yield json.loads("\n".join(data))


def _anthropic_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning=None):
    payload["stream"] = True
    blocks: dict[int, dict] = {}
    usage = {}
    for event in _stream_events(url, payload, headers, timeout, cancel_check):
        kind = event.get("type")
        if kind == "error":
            raise RuntimeError(str(event.get("error") or "Anthropic stream error"))
        if kind == "message_start":
            usage.update((event.get("message") or {}).get("usage") or {})
        elif kind == "content_block_start":
            blocks[int(event.get("index") or 0)] = dict(event.get("content_block") or {})
        elif kind == "content_block_delta":
            block = blocks.setdefault(int(event.get("index") or 0), {})
            delta = event.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                piece = delta.get("text") or ""
                block["text"] = block.get("text", "") + piece
                if piece:
                    on_delta(piece)
            elif dtype == "input_json_delta":
                block["_json"] = block.get("_json", "") + (delta.get("partial_json") or "")
            elif dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + (delta.get("thinking") or "")
                if on_reasoning and delta.get("thinking"):
                    on_reasoning(delta["thinking"])
            elif dtype == "signature_delta":
                block["signature"] = block.get("signature", "") + (delta.get("signature") or "")
        elif kind == "message_delta":
            usage.update(event.get("usage") or {})
    content = []
    for index in sorted(blocks):
        block = blocks[index]
        if "_json" in block:
            block["input"] = json.loads(block.pop("_json"))
        content.append(block)
    return {"content": content, "usage": usage}


def _responses_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning=None):
    payload["stream"] = True
    completed = None
    for event in _stream_events(url, payload, headers, timeout, cancel_check):
        kind = event.get("type")
        if kind in {"response.output_text.delta", "response.refusal.delta"}:
            piece = event.get("delta") or ""
            if piece:
                on_delta(piece)
        elif kind == "response.reasoning_summary_text.delta":
            if on_reasoning and event.get("delta"):
                on_reasoning(event["delta"])
        elif kind == "response.completed":
            completed = event.get("response")
        elif kind in {"response.failed", "error"}:
            raise RuntimeError(str((event.get("response") or {}).get("error") or event.get("error") or kind))
    if completed is None:
        raise RuntimeError("Responses 流未返回完成事件")
    return completed


def _gemini_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning=None):
    parts = []
    usage = {}
    for chunk in _stream_events(url, payload, headers, timeout, cancel_check):
        candidates = chunk.get("candidates") or []
        for part in ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []:
            parts.append(part)
            piece = part.get("text") or ""
            if piece and not part.get("thought"):
                on_delta(piece)
            elif piece and on_reasoning:
                on_reasoning(piece)
        usage = chunk.get("usageMetadata") or usage
    return {"candidates": [{"content": {"parts": parts}}], "usageMetadata": usage}


def discover(profile, timeout=25):
    import workflow
    api = profile.get("api", "openai-completions")
    import account_providers
    if api in account_providers.WIRES:
        return [{"id": m, "name": "跟随官方客户端默认模型" if m == "auto" else m}
                for m in account_providers.entry(api)["models"]]
    base, key = profile["base_url"].rstrip("/"), profile.get("api_key", "")
    if api == "google-generative-ai":
        raw = _request(base + "/models", None, {"x-goog-api-key": key}, timeout)
        return [{"id": r["name"].removeprefix("models/"), "name": r.get("displayName", r["name"])} for r in raw.get("models", [])
                if "generateContent" in r.get("supportedGenerationMethods", ["generateContent"])]
    if api == "anthropic-messages":
        raw = _request(base + "/models", None, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        return [{"id": r["id"], "name": r.get("display_name", r["id"])} for r in raw.get("data", [])]
    return workflow.discover_models(base, key, timeout)


def complete(profile, messages, tools=None, temperature=0.2, timeout=180, max_tokens=4096,
             on_delta: Callable[[str], None] | None = None, cancel_check=None, on_reasoning=None):
    api = profile.get("api", "openai-completions")
    import account_providers
    if api in account_providers.WIRES:
        return account_providers.complete(profile, messages, tools, timeout=timeout,
                                          on_delta=on_delta, cancel_check=cancel_check)
    base, key, model = profile["base_url"].rstrip("/"), profile.get("api_key", ""), profile["model"]
    effort = profile.get("reasoning", "auto")
    reasoning = []
    if api == "openai-responses":
        inputs = []
        for m in messages:
            wire = m.get("_protocol", {})
            if m["role"] == "assistant" and wire.get("api") == api:
                inputs.extend(wire["output"])
                continue
            if m["role"] == "tool":
                inputs.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
            else:
                if m.get("content"):
                    inputs.append({"role": "developer" if m["role"] == "system" else m["role"], "content": m["content"]})
                for c in m.get("tool_calls", []):
                    inputs.append({"type": "function_call", "call_id": c["id"], **c["function"]})
        payload = {"model": model, "input": inputs, "max_output_tokens": max_tokens, "include": ["reasoning.encrypted_content"]}
        if effort != "auto":
            payload["reasoning"] = {"effort": effort}
        if on_reasoning and effort != "none":
            payload.setdefault("reasoning", {})["summary"] = "auto"
        if tools:
            payload["tools"] = [{"type": "function", **t["function"]} for t in tools]
        url, headers = base + "/responses", {"Authorization": "Bearer " + key} if key else {}
        raw = (_responses_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning) if on_delta
               else _request(url, payload, headers, timeout))
        text, calls = [], []
        for o in raw.get("output", []):
            if o.get("type") == "reasoning":
                reasoning.extend(p.get("text", "") for p in o.get("summary", []) if p.get("type") == "summary_text")
            if o.get("type") == "message":
                text.extend(p.get("text", "") for p in o.get("content", []) if p.get("type") == "output_text")
            elif o.get("type") == "function_call":
                calls.append({"id": o["call_id"], "type": "function", "function": {"name": o["name"], "arguments": o["arguments"]}})
        usage = raw.get("usage", {})
        protocol = {"api": api, "output": raw.get("output", [])}
    elif api == "anthropic-messages":
        hist = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                entry = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}]}
            else:
                wire = m.get("_protocol", {})
                if m["role"] == "assistant" and wire.get("api") == api:
                    content = list(wire["content"])
                else:
                    content = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                    content.extend({"type": "tool_use", "id": c["id"], "name": c["function"]["name"], "input": json.loads(c["function"]["arguments"])} for c in m.get("tool_calls", []))
                entry = {"role": m["role"], "content": content or [{"type": "text", "text": " "}]}
            if hist and hist[-1]["role"] == entry["role"]:
                hist[-1]["content"].extend(entry["content"])
            else:
                hist.append(entry)
        payload = {"model": model, "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system"), "messages": hist, "max_tokens": max_tokens}
        if effort not in ("auto", "none"):
            budget = {"minimal": 1024, "low": 1024, "medium": 2048, "high": 8192, "xhigh": 16384}[effort]
            payload.update(thinking={"type": "enabled", "budget_tokens": budget}, max_tokens=max(max_tokens, budget + 2048))
        elif effort == "none":
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]} for t in tools]
        url, headers = base + "/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}
        raw = (_anthropic_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning) if on_delta
               else _request(url, payload, headers, timeout))
        text = [c["text"] for c in raw.get("content", []) if c["type"] == "text"]
        reasoning = [c["thinking"] for c in raw.get("content", []) if c.get("type") == "thinking" and c.get("thinking")]
        calls = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["input"])}} for c in raw.get("content", []) if c["type"] == "tool_use"]
        usage = raw.get("usage", {})
        protocol = {"api": api, "content": raw.get("content", [])}
    elif api == "google-generative-ai":
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                parts = [{"functionResponse": {"name": m["name"], "response": {"result": m["content"]}}}]
            else:
                wire = m.get("_protocol", {})
                if m["role"] == "assistant" and wire.get("api") == api:
                    parts = wire["parts"]
                else:
                    parts = [{"text": m["content"]}] if m.get("content") else []
                    parts.extend({"functionCall": {"name": c["function"]["name"], "args": json.loads(c["function"]["arguments"])}} for c in m.get("tool_calls", []))
            role = "model" if m["role"] == "assistant" else "user"
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": list(parts)})
        generation = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if effort != "auto":
            generation["thinkingConfig"] = {"thinkingBudget": {"none": 0, "minimal": 128, "low": 1024, "medium": 4096, "high": 8192, "xhigh": 16384}[effort]}
        if on_reasoning and effort != "none":
            generation.setdefault("thinkingConfig", {})["includeThoughts"] = True
        payload = {"contents": contents, "systemInstruction": {"parts": [{"text": "\n\n".join(m["content"] for m in messages if m["role"] == "system")}]}, "generationConfig": generation}
        if tools:
            payload["tools"] = [{"functionDeclarations": [t["function"] for t in tools]}]
        url = base + "/models/" + quote(model.removeprefix("models/"), safe="-._")
        headers = {"x-goog-api-key": key}
        raw = (_gemini_stream(url + ":streamGenerateContent?alt=sse", payload, headers, timeout, on_delta, cancel_check, on_reasoning)
               if on_delta else _request(url + ":generateContent", payload, headers, timeout))
        parts = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        reasoning = [p["text"] for p in parts if p.get("text") and p.get("thought")]
        protocol = {"api": api, "parts": parts}
        text = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
        calls = [{"id": "gemini_" + str(i), "type": "function", "function": {"name": p["functionCall"]["name"], "arguments": json.dumps(p["functionCall"].get("args", {}))}} for i, p in enumerate(parts) if "functionCall" in p]
        usage = raw.get("usageMetadata", {})
    else:
        raise ValueError("Unsupported protocol: " + api)
    prompt = int(usage.get("input_tokens", usage.get("promptTokenCount", 0)))
    output = int(usage.get("output_tokens", usage.get("candidatesTokenCount", 0)))
    # Signed thinking blocks must survive tool-result turns without reconstruction.
    return {"role": "assistant", "content": "".join(text) if api == "google-generative-ai" else "\n".join(text),
            "tool_calls": calls, "_protocol": protocol, "reasoning": "\n".join(reasoning),
            "usage": {"prompt": prompt, "completion": output, "total": int(usage.get("total_tokens", usage.get("totalTokenCount", prompt + output))), "exact": bool(usage)}}
