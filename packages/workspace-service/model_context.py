"""Offline model-window resolution and request token budgets."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import string
import tempfile
from typing import Any, Mapping

import model_catalog


DEFAULT_CONTEXT_WINDOW = 65_536
IMAGE_TOKEN_RESERVE = 4_096

# tiktoken reads these immutable public assets from its local cache. Checking
# every asset first prevents a missing or corrupt cache from causing a download.
_BASE = "https://openaipublic.blob.core.windows.net/encodings/"
_ASSETS = {
    "cl100k_base": ((_BASE + "cl100k_base.tiktoken", "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"),),
    "o200k_base": ((_BASE + "o200k_base.tiktoken", "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"),),
    "p50k_base": ((_BASE + "p50k_base.tiktoken", "94b5ca7dff4d00767bc256fdd1b27e5b17361d7b8a5f968547f9f23eb70d2069"),),
    "r50k_base": ((_BASE + "r50k_base.tiktoken", "306cd27f03c1a714eca7108e03d66b7dc042abe8c258b44c199a7ed9838dd930"),),
}
_ASSETS["p50k_edit"] = _ASSETS["p50k_base"]
_ASSETS["o200k_harmony"] = _ASSETS["o200k_base"]


@dataclass(frozen=True)
class ModelContextSpec:
    window: int
    max_output: int | None
    tokenizer: str
    source: str
    estimated_window: bool
    capacity_source: str = ""
    max_input_tokens: int | None = None


@dataclass(frozen=True)
class RequestBudget:
    window: int
    trigger_tokens: int
    target_tokens: int
    retained_tokens: int
    summary_tokens: int
    input_tokens: int
    count_method: str
    safety: int
    source: str
    output_limit: int
    max_output: int | None
    fits: bool
    needs_compaction: bool
    estimated_window: bool
    available_input_tokens: int = 0
    capacity_source: str = ""


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"模型上下文配置 {field} 必须是正整数")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"模型上下文配置 {field} 必须是正整数") from exc
    if number <= 0 or isinstance(value, float) and value != number:
        raise ValueError(f"模型上下文配置 {field} 必须是正整数")
    return number


def resolve_spec(profile: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None = None) -> ModelContextSpec:
    profile, overrides = profile or {}, overrides or {}
    catalog = model_catalog.lookup(profile) or {}

    def field(*names: str):
        for source, values in (("overrides", overrides), ("profile", profile), ("catalog", catalog)):
            for name in names:
                if name in values and values[name] not in (None, ""):
                    return values[name], f"{source}.{name}"
        return None, ""

    window, source = field("context_window")
    estimated = window is None
    if estimated:
        window, source = DEFAULT_CONTEXT_WINDOW, "estimated.default_context_window"
    else:
        window = _positive_integer(window, "context_window")
        if catalog.get("max_context_window"):
            window = min(window, catalog["max_context_window"])
    output, output_source = field("max_output", "max_output_tokens")
    output = _positive_integer(output, "max_output") if output is not None else None
    if output is not None and catalog.get("max_output"):
        output = min(output, catalog["max_output"])
    max_input, _ = field("max_input_tokens")
    max_input = _positive_integer(max_input, "max_input_tokens") if max_input is not None else None
    tokenizer, tokenizer_source = field("tokenizer")
    if tokenizer is not None and not isinstance(tokenizer, str):
        raise ValueError("模型上下文配置 tokenizer 必须是编码名称字符串")
    return ModelContextSpec(
        window, output, str(tokenizer or "auto").strip() or "auto",
        ";".join(part for part in (source, output_source, tokenizer_source) if part), estimated,
        catalog.get("source", "") if source.startswith("catalog.") else "",
        max_input,
    )


def endpoint_identity(profile):
    return {"endpoint_hash": hashlib.sha256(str(profile.get("base_url") or "").rstrip("/").encode()).hexdigest(),
            **{key: str(profile.get(key) or "") for key in ("api", "model")}}


def read_overrides(db, profile):
    if db is None:
        return {}, False
    try:
        row = db.db.execute("SELECT data FROM service_settings WHERE service='model_context' AND owner=?",
                            (str(profile.get("id") or ""),)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {}, False
        raise
    value = json.loads(row[0]) if row else {}
    if not isinstance(value, dict):
        raise ValueError("model_context settings must be a JSON object")
    stale = "_identity" in value and value["_identity"] != endpoint_identity(profile)
    return ({} if stale else value), stale


def describe(db, profile):
    overrides, stale = read_overrides(db, profile)
    spec = resolve_spec(profile, overrides)
    output = automatic_output_limit(profile, overrides)
    budget = measure_request(profile, [], [], output, overrides)
    return {"context_window": spec.window, "max_output": spec.max_output,
            "source": spec.source, "capacity_source": spec.capacity_source,
            "estimated_window": spec.estimated_window, "stale": stale,
            "overrides": {key: overrides[key] for key in ("context_window", "max_output", "max_output_tokens", "tokenizer") if key in overrides},
            "output_limit": output, "trigger_tokens": budget.trigger_tokens,
            "target_tokens": budget.target_tokens, "count_method": budget.count_method,
            "max_input_tokens": spec.max_input_tokens}


def save_overrides(db, profile, body):
    import service_settings
    allowed = {"id", "context_window", "max_output", "tokenizer"}
    if set(body) - allowed:
        raise ValueError("Only context_window, max_output and tokenizer can be configured")
    value = {}
    for key in ("context_window", "max_output"):
        if body.get(key) not in (None, ""):
            value[key] = _positive_integer(body[key], key)
    if body.get("tokenizer") not in (None, ""):
        if not isinstance(body["tokenizer"], str) or len(body["tokenizer"]) > 100:
            raise ValueError("tokenizer must be an encoding name")
        value["tokenizer"] = body["tokenizer"].strip()
    spec = resolve_spec(profile, value)
    if value.get("context_window") and value["context_window"] != spec.window:
        raise ValueError("context_window exceeds the model catalog maximum")
    if value.get("max_output") and value["max_output"] != spec.max_output:
        raise ValueError("max_output exceeds the model catalog maximum")
    if spec.max_output and spec.max_output >= spec.window:
        raise ValueError("max_output must be below context_window")
    measure_request(profile, [], [], automatic_output_limit(profile, value), value)
    service_settings.write(db, "model_context", {**value, "_identity": endpoint_identity(profile)} if value else {},
                           owner=str(profile["id"]))
    return describe(db, profile)


def _offline_encoding(model: str, tokenizer: str):
    try:
        import tiktoken
        from tiktoken import registry
        from tiktoken.model import encoding_name_for_model
    except ImportError:
        return None
    try:
        name = encoding_name_for_model(model) if tokenizer == "auto" else tokenizer
    except KeyError:
        return None
    loaded = registry.ENCODINGS.get(name)
    if loaded is not None:
        return loaded
    assets = _ASSETS.get(name)
    if not assets:
        return None
    cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get("DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    if not cache_dir:
        return None
    try:
        for url, digest in assets:
            cache_file = Path(cache_dir) / hashlib.sha1(url.encode()).hexdigest()
            if hashlib.sha256(cache_file.read_bytes()).hexdigest() != digest:
                return None
        return tiktoken.get_encoding(name)
    except (OSError, ValueError, KeyError):
        return None


def _request_payload(messages, tools):
    images = 0
    wire_messages = []
    for message in messages or []:
        copied = {key: value for key, value in message.items() if not str(key).startswith("_")}
        content = copied.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "input_image", "image"):
                    images += 1
                    image = part.get("image_url") or {}
                    detail = image.get("detail") if isinstance(image, dict) else part.get("detail")
                    parts.append({"type": part["type"], "detail": detail or "auto"})
                else:
                    parts.append(part)
            copied["content"] = parts
        wire_messages.append(copied)
    payload = json.dumps({"messages": wire_messages, "tools": tools or []}, ensure_ascii=False, separators=(",", ":"), default=str)
    overhead = 32 + 8 * len(wire_messages) + 16 * len(tools or []) + images * IMAGE_TOKEN_RESERVE
    return payload, overhead


def _count_request(profile, messages, tools, spec):
    payload, overhead = _request_payload(messages, tools)
    encoding = _offline_encoding(str((profile or {}).get("model") or ""), spec.tokenizer)
    if encoding is not None:
        return len(encoding.encode(payload, disallowed_special=())) + overhead, "tiktoken:" + encoding.name
    if spec.tokenizer in ("utf8_bytes", "utf8_bytes_conservative"):
        return len(payload.encode("utf-8")) + overhead, "utf8_bytes_conservative"
    ascii_text = punctuation = nonascii_bytes = 0
    for character in payload:
        if ord(character) >= 128:
            nonascii_bytes += len(character.encode("utf-8"))
        elif character in string.punctuation:
            punctuation += 1
        else:
            ascii_text += 1
    # This is a labeled estimate, not a byte-level upper bound. Count dense JSON
    # punctuation separately and reserve more for Unicode than for ASCII text.
    estimate = math.ceil(ascii_text / 3) + punctuation + math.ceil(nonascii_bytes / 2)
    return estimate + overhead, "estimated_utf8_mixed"


def request_tokens(profile, messages, tools, overrides=None) -> int:
    """Count the request, including tool declarations, without tokenizer downloads."""
    return _count_request(profile, messages, tools, resolve_spec(profile, overrides))[0]


def automatic_output_limit(profile, overrides=None) -> int:
    """Use declared output capacity, or a labeled window-based estimate.

    Unknown routes must not inherit another provider's output contract. Reserve
    a quarter of their configured/estimated window until capacity is configured.
    """
    spec = resolve_spec(profile, overrides)
    safety = max(4_096, math.ceil(spec.window * .05))
    return min(spec.max_output or max(1, spec.window // 4), max(1, spec.window - safety - 2048))


def measure_request(profile, messages, tools, output_limit, overrides=None) -> RequestBudget:
    spec = resolve_spec(profile, overrides)
    output = _positive_integer(output_limit, "output_limit")
    label = str((profile or {}).get("model") or "未命名模型")
    if spec.max_output is not None and output > spec.max_output:
        raise ValueError(f"模型 {label} 的输出请求 {output} Token 超过配置上限 {spec.max_output}；请调整 max_output 或输出预算")
    safety = max(4_096, math.ceil(spec.window * .05))
    available = spec.window - output - safety
    if spec.max_input_tokens is not None:
        available = min(available, spec.max_input_tokens)
    tokens, method = _count_request(profile, messages, tools, spec)
    trigger = min(math.floor(spec.window * .9), available)
    # Summaries need enough output room to preserve long-running workflow state.
    summary_floor = max(512, min(1024, spec.window // 16))
    summary = min(max(summary_floor, math.ceil(min(tokens, max(0, available)) * .08)),
                  spec.max_output or automatic_output_limit(profile, overrides),
                  max(summary_floor, math.floor(spec.window * .12)))
    retained = math.floor((spec.window - output) * .16)
    reserve = max(256, min(1_024, spec.window // 32))
    target = min(math.floor(available * .75), trigger - summary, trigger - reserve)
    if target < summary + reserve:
        raise ValueError(f"模型 {label} 的上下文窗口 {spec.window} Token 无法容纳输出 {output}、安全预留 {safety} 和压缩状态；请配置正确的 context_window 或减少输出预算")
    retained = min(retained, target - summary - reserve)
    return RequestBudget(
        spec.window, trigger, target, retained, summary, tokens, method, safety,
        spec.source, output, spec.max_output, tokens <= available, tokens >= trigger, spec.estimated_window,
        available, spec.capacity_source,
    )
