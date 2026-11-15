"""packages/ai-runtime: Unified Provider Adapter, Retry Policy, Budget, and Telemetry."""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
for _p in ("database", "workspace-core", "workspace-service", "connectors"):
    _d = ROOT / "packages" / _p
    if _d.is_dir() and str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

try:
    import guards
except ImportError:
    guards = None


class AIRuntimeError(RuntimeError):
    """Base exception for AI Runtime."""


class BudgetExceededError(AIRuntimeError):
    """Raised when token or cost budget is exceeded."""


class CancelledError(AIRuntimeError):
    """Raised when an operation is cancelled."""


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0
    backoff_factor: float = 2.0
    retryable_codes: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504})

    def delay_for_attempt(self, attempt: int) -> float:
        delay = self.base_delay * (self.backoff_factor ** attempt)
        jitter = random.uniform(0, 0.5 * self.base_delay)
        return min(self.max_delay, delay + jitter)


@dataclass
class Budget:
    max_tokens: int = 200_000
    used_tokens: int = 0
    max_cost_usd: float = 10.0
    estimated_cost_usd: float = 0.0

    def add_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        total = prompt_tokens + completion_tokens
        if self.max_tokens > 0 and (self.used_tokens + total) > self.max_tokens:
            raise BudgetExceededError(f"Token budget exceeded: limit {self.max_tokens}, attempted {self.used_tokens + total}")
        self.used_tokens += total


@dataclass
class TelemetryRecord:
    timestamp: float
    provider: str
    model: str
    duration_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    outcome: str
    error: str = ""


class Telemetry:
    def __init__(self):
        self._records: list[TelemetryRecord] = []

    def record(self, provider: str, model: str, duration_ms: int, usage: dict[str, int], outcome: str, error: str = "") -> None:
        rec = TelemetryRecord(
            timestamp=time.time(),
            provider=provider,
            model=model,
            duration_ms=duration_ms,
            prompt_tokens=int(usage.get("prompt", 0)),
            completion_tokens=int(usage.get("completion", 0)),
            total_tokens=int(usage.get("total", 0)),
            outcome=outcome,
            error=error,
        )
        self._records.append(rec)

    def summary(self) -> dict[str, Any]:
        total_calls = len(self._records)
        total_tokens = sum(r.total_tokens for r in self._records)
        successful = sum(1 for r in self._records if r.outcome == "ok")
        return {
            "total_calls": total_calls,
            "successful_calls": successful,
            "failed_calls": total_calls - successful,
            "total_tokens": total_tokens,
            "records_count": len(self._records),
        }


GLOBAL_TELEMETRY = Telemetry()


def open_guarded_url(req: urllib.request.Request, timeout: int = 30) -> Any:
    try:
        import workflow
        if hasattr(workflow, "open_url"):
            return workflow.open_url(req, timeout=timeout)
    except Exception:
        pass
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
            os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    if proxy:
        parts = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        try:
            with socket.create_connection((parts.hostname, parts.port or 80), timeout=0.6):
                pass
        except OSError:
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _call_wire_openai_completions(profile: dict[str, Any], messages: list[dict[str, Any]],
                                  tools: list[dict[str, Any]] | None = None,
                                  temperature: float = 0.2, timeout: int = 180,
                                   max_tokens: int = 4096, on_delta: Callable[[str], None] | None = None,
                                   cancel_check: Callable[[], bool] | None = None,
                                   on_reasoning: Callable[[str], None] | None = None) -> dict[str, Any]:
    base_url = profile["base_url"].rstrip("/")
    api_key = profile.get("api_key", "")
    model = profile["model"]

    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if profile.get("reasoning", "auto") != "auto":
        payload["reasoning_effort"] = profile["reasoning"]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if on_delta:
        payload["stream"] = True

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with open_guarded_url(req, timeout=timeout) as res:
        if on_delta:
            content: list[str] = []
            reasoning: list[str] = []
            calls: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] = {}
            for raw in res:
                if cancel_check and cancel_check():
                    raise CancelledError("用户已中断运行")
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    item = json.loads(body)
                except ValueError:
                    continue
                usage = item.get("usage") or usage
                for choice in item.get("choices") or []:
                    delta = choice.get("delta") or {}
                    thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if isinstance(thought, str) and thought:
                        reasoning.append(thought)
                        if on_reasoning:
                            on_reasoning(thought)
                    piece = delta.get("content") or ""
                    if piece:
                        content.append(piece)
                        on_delta(piece)
                    for part in delta.get("tool_calls") or []:
                        index = int(part.get("index") or 0)
                        call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        call["id"] += part.get("id") or ""
                        func = part.get("function") or {}
                        call["function"]["name"] += func.get("name") or ""
                        call["function"]["arguments"] += func.get("arguments") or ""
            return {"role": "assistant", "content": "".join(content).strip(),
                    "reasoning": "".join(reasoning),
                    "tool_calls": [calls[i] for i in sorted(calls)],
                    "usage": {"prompt": int(usage.get("prompt_tokens") or 0),
                              "completion": int(usage.get("completion_tokens") or 0),
                              "total": int(usage.get("total_tokens") or 0), "exact": bool(usage)}}
        data = json.loads(res.read().decode("utf-8", "replace"))

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIRuntimeError(f"Model response malformed: {str(data)[:300]}") from exc

    usage = data.get("usage") or {}
    return {
        "role": "assistant",
        "content": (msg.get("content") or "").strip(),
        "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "usage": __import__("usage_stats").normalize(usage,"openai-completions"),
    }


def call(profile: dict[str, Any], messages: list[dict[str, Any]],
         tools: list[dict[str, Any]] | None = None,
         temperature: float = 0.2, timeout: int = 180,
         max_tokens: int = 4096, retry_policy: RetryPolicy | None = None,
         budget: Budget | None = None, cancel_check: Callable[[], bool] | None = None,
         on_delta: Callable[[str], None] | None = None,
         on_reasoning: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not profile or not profile.get("base_url") or not profile.get("model"):
        raise AIRuntimeError("No model configured: please configure a model profile.")

    if cancel_check and cancel_check():
        raise CancelledError("Operation cancelled before execution")

    if guards is not None and hasattr(guards, "scrub"):
        guards.scrub(messages, note=f"→ {profile.get('model') or '?'}")

    policy = retry_policy or RetryPolicy()
    api = profile.get("api", "openai-completions")
    t0 = time.monotonic()
    last_exc = None

    for attempt in range(policy.max_retries + 1):
        if cancel_check and cancel_check():
            raise CancelledError("Operation cancelled during execution")
        streamed_any = False
        attempt_start=time.monotonic();first_token_ms=None;result=None;meter_status="error"
        def forward_delta(piece: str) -> None:
            nonlocal streamed_any, first_token_ms
            streamed_any = True
            if first_token_ms is None:first_token_ms=int((time.monotonic()-attempt_start)*1000)
            if on_delta:
                on_delta(piece)
        def forward_reasoning(piece: str) -> None:
            nonlocal streamed_any, first_token_ms
            streamed_any = True
            if first_token_ms is None:first_token_ms=int((time.monotonic()-attempt_start)*1000)
            if on_reasoning:
                on_reasoning(piece)
        try:
            if api == "openai-completions":
                result = _call_wire_openai_completions(profile, messages, tools, temperature, timeout,
                                                        max_tokens, forward_delta if on_delta else None, cancel_check,
                                                        forward_reasoning if on_reasoning else None)
            else:
                from agent_protocols import complete
                result = complete(profile, messages, tools, temperature, timeout, max_tokens,
                                  on_delta=forward_delta if on_delta else None, cancel_check=cancel_check,
                                  on_reasoning=forward_reasoning if on_reasoning else None)

            duration_ms = int((time.monotonic() - t0) * 1000)
            use = result.get("usage") or {}
            meter_status="ok"
            if budget:
                budget.add_usage(use.get("prompt", 0), use.get("completion", 0))
            GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, use, "ok")
            return result

        except urllib.error.HTTPError as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            last_exc = exc
            detail = exc.read().decode('utf-8', 'replace')[:4096]
            try:
                remote_error = json.loads(detail).get('error') or {}
            except (ValueError, AttributeError):
                remote_error = {}
            if isinstance(remote_error, dict) and remote_error.get('code') == 'get_channel_failed':
                message = '模型网关当前没有该模型的可用渠道。请在“设置 → 模型列表”选择其他可用模型，或联系网关管理员检查模型分组和渠道；配置已保留。'
                GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", message)
                raise AIRuntimeError(message) from exc
            if not streamed_any and exc.code in policy.retryable_codes and attempt < policy.max_retries:
                time.sleep(policy.delay_for_attempt(attempt))
                continue
            err_msg = f"HTTP {exc.code}: {detail[:300]}"
            GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", err_msg)
            raise AIRuntimeError(f"Model interface {err_msg}") from exc

        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            last_exc = exc
            if not streamed_any and attempt < policy.max_retries:
                time.sleep(policy.delay_for_attempt(attempt))
                continue
            err_msg = str(exc)
            GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", err_msg)
            raise AIRuntimeError(f"Model unreachable: {err_msg}") from exc

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", str(exc))
            raise
        finally:
            import usage_stats
            usage_stats.record(profile,messages,tools,int((time.monotonic()-attempt_start)*1000),
                               (result or {}).get('usage') or {},meter_status,attempt,first_token_ms)

    raise AIRuntimeError(f"Exhausted retries: {last_exc}")
