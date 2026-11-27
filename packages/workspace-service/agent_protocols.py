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
    stop_reason = None
    completed = False
    for event in _stream_events(url, payload, headers, timeout, cancel_check):
        kind = event.get("type")
        if kind == "error":
            from runtime import ProviderStreamError
            raise ProviderStreamError(event.get("error") or event)
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
            stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
        elif kind == "message_stop":
            completed = True
    content = []
    for index in sorted(blocks):
        block = blocks[index]
        if "_json" in block:
            arguments = block.pop("_json")
            try:
                block["input"] = json.loads(arguments)
            except ValueError:
                block["_partial_json"] = arguments
                completed = False
                stop_reason = "incomplete"
        content.append(block)
    return {"content": content, "usage": usage, "stop_reason": stop_reason,
            "_stream_complete": completed or bool(stop_reason and stop_reason != "incomplete")}


def _responses_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning=None):
    payload["stream"] = True
    completed = None
    collected_text = {}
    output_items = {}
    usage = {}
    for event in _stream_events(url, payload, headers, timeout, cancel_check):
        kind = event.get("type")
        if kind in {"response.output_text.delta", "response.refusal.delta"}:
            piece = event.get("delta") or ""
            if piece:
                position = (int(event.get("output_index") or 0),
                            int(event.get("content_index") or 0),
                            "refusal" if kind == "response.refusal.delta" else "output_text")
                collected_text[position] = collected_text.get(position, "") + piece
                on_delta(piece)
        elif kind == "response.reasoning_summary_text.delta":
            piece = event.get("delta") or ""
            if piece and on_reasoning:
                on_reasoning(piece)
        elif kind in {"response.completed", "response.done", "response.incomplete"}:
            completed = dict(event.get("response") or event)
            completed.setdefault("status", "incomplete" if kind == "response.incomplete" else "completed")
        elif kind in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if item:
                output_items[int(event.get("output_index") or 0)] = dict(item)
        elif kind in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
            item = output_items.setdefault(int(event.get("output_index") or 0), {
                "type": "function_call", "call_id": event.get("call_id") or event.get("item_id") or "",
                "name": event.get("name") or "", "arguments": ""})
            if kind.endswith(".delta"):
                item["arguments"] = item.get("arguments", "") + (event.get("delta") or "")
            else:
                item["arguments"] = event.get("arguments", item.get("arguments", ""))
        elif kind in {"response.failed", "error"}:
            from runtime import ProviderStreamError
            raise ProviderStreamError((event.get("response") or {}).get("error") or event.get("error") or event)
        if event.get("usage"):
            usage.update(event["usage"])
    if completed is None:
        if output_items or collected_text:
            completed = {"usage": usage, "status": "incomplete",
                         "incomplete_details": {"reason": "stream_terminated"}}
        else:
            from runtime import ProviderStreamError
            raise ProviderStreamError("Responses 流未返回完成事件")
    output_items.update({index: dict(item) for index, item in enumerate(completed.get("output") or [])})
    for (output_index, content_index, kind), text in collected_text.items():
        item = output_items.setdefault(output_index, {"type": "message", "role": "assistant", "content": []})
        content = item.setdefault("content", [])
        while len(content) <= content_index:
            content.append({})
        field = "refusal" if kind == "refusal" else "text"
        if not content[content_index].get(field):
            content[content_index] = {"type": kind, field: text}
    completed["output"] = [output_items[index] for index in sorted(output_items)]
    if not completed.get("usage") and usage:
        completed["usage"] = usage
    return completed


def _gemini_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning=None):
    parts = []
    usage = {}
    finish_reason = None
    for chunk in _stream_events(url, payload, headers, timeout, cancel_check):
        if chunk.get("error"):
            from runtime import ProviderStreamError
            raise ProviderStreamError(chunk["error"])
        candidates = chunk.get("candidates") or []
        if candidates and candidates[0].get("finishReason"):
            finish_reason = candidates[0]["finishReason"]
        for part in ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []:
            parts.append(part)
            piece = part.get("text") or ""
            if piece and not part.get("thought"):
                on_delta(piece)
            elif piece and on_reasoning:
                on_reasoning(piece)
        usage = chunk.get("usageMetadata") or usage
    return {"candidates": [{"content": {"parts": parts}, "finishReason": finish_reason}],
            "usageMetadata": usage, "_stream_complete": finish_reason is not None}


def discover(profile, timeout=25):
    import workflow
    workflow._assert_key_host(profile)
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


def image_content(content, api):
    if not isinstance(content, list):
        content = [{'type':'text','text':content or ''}]
    blocks = []
    for part in content:
        if part.get('type') == 'text':
            blocks.append({'type':'input_text','text':part['text']} if api == 'openai-responses'
                          else {'text':part['text']} if api == 'google-generative-ai' else part)
        elif part.get('type') == 'image_url':
            url = part['image_url']['url']
            if api == 'openai-responses':
                blocks.append({'type':'input_image','image_url':url})
            else:
                if not url.startswith('data:') or ';base64,' not in url:
                    raise ValueError('图片必须是本地上传的文件')
                mime, data = url[5:].split(';base64,',1)
                blocks.append({'inlineData':{'mimeType':mime,'data':data}} if api == 'google-generative-ai'
                              else {'type':'image','source':{'type':'base64','media_type':mime,'data':data}})
    return blocks


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
    finish_reason, stream_complete, completion_metadata = None, True, {}
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
                    inputs.append({"role": "developer" if m["role"] == "system" else m["role"], "content": image_content(m['content'],api) if isinstance(m['content'],list) else m['content']})
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
        if raw.get("status") == "failed" or raw.get("error"):
            from runtime import ProviderStreamError
            raise ProviderStreamError(raw.get("error") or raw)
        incomplete = raw.get("status") not in (None, "completed") or bool(raw.get("incomplete_details"))
        provider_reason = (raw.get("incomplete_details") or {}).get("reason")
        finish_reason = "length" if provider_reason in {"max_output_tokens", "max_tokens"} else (
            "incomplete" if incomplete else raw.get("finish_reason") or "stop")
        stream_complete = not incomplete
        completion_metadata = {key: raw[key] for key in ("status", "incomplete_details") if key in raw}
        text, calls = [], []
        for o in raw.get("output", []):
            if o.get("type") == "reasoning":
                reasoning.extend(p.get("text", "") for p in o.get("summary", []) if p.get("type") == "summary_text")
            if o.get("type") == "message":
                text.extend(p.get("text", p.get("refusal", "")) for p in o.get("content", [])
                            if p.get("type") in {"output_text", "refusal"})
            elif o.get("type") == "function_call":
                calls.append({"id": o.get("call_id") or o.get("id") or "", "type": "function", "function": {
                    "name": o.get("name") or "", "arguments": o.get("arguments") or ""}})
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
                    content = image_content(m['content'],api) if m.get('content') else []
                    content.extend({"type": "tool_use", "id": c["id"], "name": c["function"]["name"], "input": json.loads(c["function"].get("arguments") or "{}")} for c in m.get("tool_calls", []))
                entry = {"role": m["role"], "content": content or [{"type": "text", "text": " "}]}
            if hist and hist[-1]["role"] == entry["role"]:
                hist[-1]["content"].extend(entry["content"])
            else:
                hist.append(entry)
        payload = {"model": model, "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system"), "messages": hist, "max_tokens": max_tokens}
        if effort not in ("auto", "none"):
            budget = {"minimal": 1024, "low": 1024, "medium": 2048, "high": 8192, "xhigh": 16384}[effort]
            budget = min(budget, max_tokens - 1)
            payload["thinking"] = ({"type": "enabled", "budget_tokens": budget} if budget >= 1024
                                   else {"type": "disabled"})
        elif effort == "none":
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]} for t in tools]
        url, headers = base + "/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}
        raw = (_anthropic_stream(url, payload, headers, timeout, on_delta, cancel_check, on_reasoning) if on_delta
               else _request(url, payload, headers, timeout))
        provider_reason = raw.get("stop_reason")
        finish_reason = provider_reason or ("stop" if raw.get("_stream_complete", True) else "incomplete")
        stream_complete = raw.get("_stream_complete", True) and provider_reason not in {
            "max_tokens", "pause_turn", "incomplete", "model_context_window_exceeded"}
        text = [c["text"] for c in raw.get("content", []) if c["type"] == "text"]
        reasoning = [c["thinking"] for c in raw.get("content", []) if c.get("type") == "thinking" and c.get("thinking")]
        calls = [{"id": c["id"], "type": "function", "function": {"name": c["name"],
                  "arguments": c["_partial_json"] if "_partial_json" in c else json.dumps(c.get("input", {}))}}
                 for c in raw.get("content", []) if c["type"] == "tool_use"]
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
                    parts = image_content(m['content'],api) if m.get('content') else []
                    parts.extend({"functionCall": {"name": c["function"]["name"], "args": json.loads(c["function"].get("arguments") or "{}")}} for c in m.get("tool_calls", []))
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
        provider_reason = (raw.get("candidates") or [{}])[0].get("finishReason")
        finish_reason = "length" if provider_reason == "MAX_TOKENS" else provider_reason or (
            "stop" if raw.get("_stream_complete", True) else "incomplete")
        stream_complete = raw.get("_stream_complete", True) and provider_reason in (None, "STOP")
        parts = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        reasoning = [p["text"] for p in parts if p.get("text") and p.get("thought")]
        protocol = {"api": api, "parts": parts}
        text = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
        calls = [{"id": "gemini_" + str(i), "type": "function", "function": {"name": p["functionCall"]["name"], "arguments": json.dumps(p["functionCall"].get("args", {}))}} for i, p in enumerate(parts) if "functionCall" in p]
        usage = raw.get("usageMetadata", {})
    else:
        raise ValueError("Unsupported protocol: " + api)
    import usage_stats
    prompt = int(usage.get("input_tokens", usage.get("promptTokenCount", 0)))
    output = int(usage.get("output_tokens", usage.get("candidatesTokenCount", 0)))
    # Signed thinking blocks must survive tool-result turns without reconstruction.
    return {"role": "assistant", "content": "".join(text) if api == "google-generative-ai" else "\n".join(text),
            "tool_calls": calls, "_protocol": protocol, "reasoning": "\n".join(reasoning),
            "finish_reason": finish_reason, "stream_complete": stream_complete, **completion_metadata,
            "usage": usage_stats.normalize(usage,api)}
