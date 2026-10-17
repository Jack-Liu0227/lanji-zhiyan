"""Small protocol adapters sharing the workbench's guarded HTTP transport."""
from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import Request


def _request(url, payload, headers, timeout):
    import workflow
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={"Content-Type": "application/json", **headers}, method="POST" if payload is not None else "GET")
    with workflow.open_url(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8", "replace"))


def discover(profile, timeout=25):
    import workflow
    api = profile.get("api", "openai-completions")
    base, key = profile["base_url"].rstrip("/"), profile.get("api_key", "")
    if api == "google-generative-ai":
        raw = _request(base + "/models", None, {"x-goog-api-key": key}, timeout)
        return [{"id": r["name"].removeprefix("models/"), "name": r.get("displayName", r["name"])} for r in raw.get("models", [])
                if "generateContent" in r.get("supportedGenerationMethods", ["generateContent"])]
    if api == "anthropic-messages":
        raw = _request(base + "/models", None, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        return [{"id": r["id"], "name": r.get("display_name", r["id"])} for r in raw.get("data", [])]
    return workflow.discover_models(base, key, timeout)


def complete(profile, messages, tools=None, temperature=0.2, timeout=180, max_tokens=4096):
    api = profile.get("api", "openai-completions")
    base, key, model = profile["base_url"].rstrip("/"), profile.get("api_key", ""), profile["model"]
    effort = profile.get("reasoning", "auto")
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
        if tools:
            payload["tools"] = [{"type": "function", **t["function"]} for t in tools]
        raw = _request(base + "/responses", payload, {"Authorization": "Bearer " + key} if key else {}, timeout)
        text, calls = [], []
        for o in raw.get("output", []):
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
        raw = _request(base + "/messages", payload, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        text = [c["text"] for c in raw.get("content", []) if c["type"] == "text"]
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
        payload = {"contents": contents, "systemInstruction": {"parts": [{"text": "\n\n".join(m["content"] for m in messages if m["role"] == "system")}]}, "generationConfig": generation}
        if tools:
            payload["tools"] = [{"functionDeclarations": [t["function"] for t in tools]}]
        raw = _request(base + "/models/" + quote(model.removeprefix("models/"), safe="-._") + ":generateContent", payload, {"x-goog-api-key": key}, timeout)
        parts = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        protocol = {"api": api, "parts": parts}
        text = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
        calls = [{"id": "gemini_" + str(i), "type": "function", "function": {"name": p["functionCall"]["name"], "arguments": json.dumps(p["functionCall"].get("args", {}))}} for i, p in enumerate(parts) if "functionCall" in p]
        usage = raw.get("usageMetadata", {})
    else:
        raise ValueError("Unsupported protocol: " + api)
    prompt = int(usage.get("input_tokens", usage.get("promptTokenCount", 0)))
    output = int(usage.get("output_tokens", usage.get("candidatesTokenCount", 0)))
    # Signed thinking blocks must survive tool-result turns without reconstruction.
    return {"role": "assistant", "content": "\n".join(text), "tool_calls": calls, "_protocol": protocol,
            "usage": {"prompt": prompt, "completion": output, "total": int(usage.get("total_tokens", usage.get("totalTokenCount", prompt + output))), "exact": bool(usage)}}
