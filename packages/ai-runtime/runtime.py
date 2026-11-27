"""packages/ai-runtime: Unified Provider Adapter, Retry Policy, Budget, and Telemetry."""
from __future__ import annotations

import http.client
import json
import random
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Lock
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    def __init__(self, message: str = "", *, error_kind: str = "", status_code: int | None = None,
                 error_code: str = "", retryable_on_fallback: bool = False):
        super().__init__(message)
        self.error_kind = error_kind
        self.status_code = status_code
        self.error_code = error_code
        self.retryable_on_fallback = retryable_on_fallback


class ModelUnavailableError(AIRuntimeError):
    """Raised when the target model or gateway channel is unavailable."""
    def __init__(self, message: str = "", *, status_code: int | None = None, error_code: str = ""):
        super().__init__(message, error_kind="model_unavailable", status_code=status_code,
                         error_code=error_code, retryable_on_fallback=True)


class QuotaOrAuthError(AIRuntimeError):
    """Raised when provider quota is exhausted or credentials are unauthorized."""
    def __init__(self, message: str = "", *, status_code: int | None = None, error_code: str = ""):
        super().__init__(message, error_kind="quota_or_auth", status_code=status_code,
                         error_code=error_code, retryable_on_fallback=True)


class TransientProviderError(AIRuntimeError):
    """Raised when rate limits, server errors, or network timeouts persist across retries."""
    def __init__(self, message: str = "", *, status_code: int | None = None, error_code: str = ""):
        super().__init__(message, error_kind="transient", status_code=status_code,
                         error_code=error_code, retryable_on_fallback=True)


class ContextOverflowError(AIRuntimeError):
    """Raised when prompt or context length exceeds the model window."""
    def __init__(self, message: str = "", *, status_code: int | None = None, error_code: str = ""):
        super().__init__(message, error_kind="context_overflow", status_code=status_code,
                         error_code=error_code, retryable_on_fallback=True)


class InvalidEncryptedContentError(AIRuntimeError):
    """Raised when Responses rejects provider-owned encrypted continuation state."""
    def __init__(self, message: str = "", *, status_code: int | None = None, error_code: str = ""):
        super().__init__(message, error_kind="invalid_encrypted_content", status_code=status_code,
                         error_code=error_code)


class BudgetExceededError(AIRuntimeError):
    """Raised when token or cost budget is exceeded."""


class CancelledError(AIRuntimeError):
    """Raised when an operation is cancelled."""


class ProviderStreamError(AIRuntimeError):
    def __init__(self, error: Any):
        super().__init__(str(error)[:300])
        self.error = error


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0
    backoff_factor: float = 2.0
    retryable_codes: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504})
    max_retry_after: float = 120.0

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

_provider_slots: dict[str, BoundedSemaphore] = {}
_provider_slots_lock = Lock()


@contextmanager
def _provider_slot(profile, cancel_check):
    from urllib.parse import urlsplit
    endpoint = urlsplit(profile['base_url']).netloc or profile.get('provider_id') or profile['base_url']
    with _provider_slots_lock:
        slot = _provider_slots.get(endpoint)
        if slot is None:
            slot = _provider_slots[endpoint] = BoundedSemaphore(2)
    while not slot.acquire(timeout=0.1):
        if cancel_check and cancel_check():
            raise CancelledError('Operation cancelled while waiting for the model provider')
    try:
        if cancel_check and cancel_check():
            raise CancelledError('Operation cancelled before contacting the model provider')
        yield
    finally:
        slot.release()


def open_guarded_url(req: urllib.request.Request, timeout: int = 30) -> Any:
    from workflow import open_url
    return open_url(req, timeout=timeout)


def _retry_after(headers: Any) -> float | None:
    if not headers:
        return None
    value = headers.get("Retry-After", "").strip()
    if value.isascii() and value.isdecimal() and len(value) <= 12:
        return float(value)
    try:
        until = parsedate_to_datetime(value)
        return max(0., (until - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def _wait(delay: float, cancel_check: Callable[[], bool] | None) -> None:
    if not cancel_check:
        time.sleep(delay)
        return
    deadline = time.monotonic() + delay
    while not cancel_check():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(.25, remaining))
    raise CancelledError("已停止等待模型重试")


def _call_wire_openai_completions(profile: dict[str, Any], messages: list[dict[str, Any]],
                                  tools: list[dict[str, Any]] | None = None,
                                  temperature: float = 0.2, timeout: int = 180,
                                   max_tokens: int = 4096, on_delta: Callable[[str], None] | None = None,
                                   cancel_check: Callable[[], bool] | None = None,
                                   on_reasoning: Callable[[str], None] | None = None) -> dict[str, Any]:
    base_url = profile["base_url"].rstrip("/")
    api_key = profile.get("api_key", "")
    model = profile["model"]

    clean_messages = [
        {k: v for k, v in msg.items() if not str(k).startswith("_")}
        if isinstance(msg, dict) else msg
        for msg in messages
    ]
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": clean_messages,
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
            finish_reason = None
            stream_complete = False
            for raw in res:
                if cancel_check and cancel_check():
                    raise CancelledError("用户已中断运行")
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    stream_complete = True
                    break
                try:
                    item = json.loads(body)
                except ValueError:
                    continue
                if item.get("error"):
                    raise ProviderStreamError(item["error"])
                usage = item.get("usage") or usage
                choices = item.get("choices") or []
                if choices and choices[0].get("finish_reason") is not None:
                    finish_reason = choices[0]["finish_reason"]
                    stream_complete = True
                for choice in choices:
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
                              "total": int(usage.get("total_tokens") or 0), "exact": bool(usage)},
                    "finish_reason": finish_reason, "stream_complete": stream_complete}
        data = json.loads(res.read().decode("utf-8", "replace"))

    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIRuntimeError(f"Model response malformed: {str(data)[:300]}") from exc

    usage = data.get("usage") or {}
    return {
        "role": "assistant",
        "content": (msg.get("content") or "").strip(),
        "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "usage": __import__("usage_stats").normalize(usage,"openai-completions"),
        "finish_reason": choice.get("finish_reason"),
        "stream_complete": True,
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
        messages = deepcopy(messages)
        guards.scrub(messages, note=f"→ {profile.get('model') or '?'}")

    policy = retry_policy or RetryPolicy()
    api = profile.get("api", "openai-completions")
    t0 = time.monotonic()

    import usage_stats
    prefer_direct = False
    for attempt in range(policy.max_retries + 1):
        if cancel_check and cancel_check():
            raise CancelledError("Operation cancelled during execution")
        reservation = None
        streamed_any = False
        attempt_start=time.monotonic();first_token_ms=None;result=None;meter_status="error"
        def forward_delta(piece: str) -> None:
            nonlocal streamed_any, first_token_ms
            streamed_any = True
            if first_token_ms is None:first_token_ms=int((time.monotonic()-attempt_start)*1000)
            if on_delta:
                on_delta(piece)
        def forward_reasoning(piece: str) -> None:
            nonlocal first_token_ms
            if first_token_ms is None:first_token_ms=int((time.monotonic()-attempt_start)*1000)
            if on_reasoning:
                on_reasoning(piece)
        prev_direct = None
        try:
            try:
                import workflow as _wf
                if prefer_direct and hasattr(_wf, "_PROXY_DIRECT"):
                    prev_direct = getattr(_wf._PROXY_DIRECT, "active", False)
                    _wf._PROXY_DIRECT.active = True
            except ImportError:
                _wf = None
            with _provider_slot(profile, cancel_check):
                reservation = usage_stats.reserve_request(profile)
                attempt_start = time.monotonic()
                if api in ("openai-completions", "openai-chat"):
                    result = _call_wire_openai_completions(profile, messages, tools, temperature, timeout,
                                                            max_tokens, forward_delta if on_delta else None, cancel_check,
                                                            forward_reasoning if on_reasoning else None)
                else:
                    from agent_protocols import complete
                    result = complete(profile, messages, tools, temperature, timeout, max_tokens,
                                       on_delta=forward_delta if on_delta else None, cancel_check=cancel_check,
                                       on_reasoning=forward_reasoning if on_reasoning else None)
                usage_stats.account_request(reservation, profile, result.get('usage') or {})

            duration_ms = int((time.monotonic() - t0) * 1000)
            use = result.get("usage") or {}
            meter_status="ok"
            if budget:
                budget.add_usage(use.get("prompt", 0), use.get("completion", 0))
            GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, use, "ok")
            return result

        except (urllib.error.HTTPError, ProviderStreamError) as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            message = ""
            error_cls: type[AIRuntimeError] = AIRuntimeError
            if isinstance(exc, urllib.error.HTTPError):
                with exc:
                    detail = exc.read(4096).decode('utf-8', 'replace')
                    retry_after = _retry_after(exc.headers)
                try:
                    remote_error = json.loads(detail).get('error') or {}
                except (ValueError, AttributeError):
                    remote_error = {}
                status = exc.code
            else:
                remote_error, detail, retry_after, status = exc.error, str(exc), None, None
            error = remote_error if isinstance(remote_error, dict) else {}
            codes = {str(error.get(key) or '').lower() for key in ('code', 'type')}
            codes.discard('')
            primary_code = next(iter(sorted(codes)), '')
            lower_detail = (str(detail) + ' ' + str(error.get('message') or '')).lower()
            if status is None and codes & {'429', 'rate_limit_exceeded', 'rate_limit_error', 'resource_exhausted'}:
                status = 429
            quota = bool(codes & {'insufficient_quota', 'billing_hard_limit_reached', 'quota_exceeded', 'insufficient_user_quota'})
            invalid_encrypted = bool(
                api == "openai-responses" and status == 400
                and ('invalid_encrypted_content' in codes
                     or ('encrypted content' in lower_detail and any(phrase in lower_detail for phrase in (
                         'could not be verified', 'could not be decrypted or parsed'))))
            )
            context_overflow = bool(
                codes & {'context_length_exceeded', 'string_above_max_length'}
                or (status == 400 and any(tok in lower_detail for tok in (
                    'context_length_exceeded', 'maximum context length', 'context window',
                    'too many tokens', 'prompt is too long')))
            )
            unavailable = bool(
                'get_channel_failed' in codes
                or 'model_not_found' in codes
                or status in (404, 410)
                or (status in (400, 422, 500, 503) and any(tok in lower_detail for tok in (
                    'get_channel_failed', 'no available channel', 'model_not_found', 'does not exist', 'model not found')))
            )
            auth_fail = status in (401, 402, 403)
            if 'get_channel_failed' in codes or 'get_channel_failed' in lower_detail:
                message = '模型网关当前没有该模型的可用渠道。请在“设置 → 模型列表”选择其他可用模型，或联系网关管理员检查模型分组和渠道；配置已保留。'
                error_cls = ModelUnavailableError
            elif unavailable:
                message = f"模型不可用（HTTP {status}）：{detail[:300]}" if status else f"模型不可用：{detail[:300]}"
                error_cls = ModelUnavailableError
            elif quota or auth_fail:
                if quota or status == 402:
                    message = '模型服务额度不足。请检查网关余额、配额或模型权限，再重试。'
                else:
                    message = f"模型鉴权或权限失败（HTTP {status}）：{detail[:300]}"
                error_cls = QuotaOrAuthError
            elif invalid_encrypted:
                message = f"Responses encrypted continuation state was rejected (HTTP 400): {detail[:300]}"
                error_cls = InvalidEncryptedContentError
            elif context_overflow:
                message = f"上下文长度超出模型限制：{detail[:300]}"
                error_cls = ContextOverflowError
            elif (not streamed_any and status in policy.retryable_codes and attempt < policy.max_retries
                  and (retry_after is None or retry_after <= policy.max_retry_after)):
                delay = max(policy.delay_for_attempt(attempt), retry_after or 0)
            elif status == 429:
                wait_hint = f'请至少等待 {int(retry_after + .999)} 秒再重试。' if retry_after else '请稍后重试；反复出现时请检查模型服务的并发与速率限制。'
                message = '模型服务限流（HTTP 429）。' + wait_hint
                error_cls = TransientProviderError
            else:
                message = f"Model interface HTTP {status}: {detail[:300]}" if status else f"Model stream failed: {detail}"
                if status in policy.retryable_codes or status is None:
                    error_cls = TransientProviderError
            if message:
                GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", message)
                raise error_cls(message, status_code=status, error_code=primary_code) from exc

        except (urllib.error.URLError, TimeoutError, socket.timeout,
                ssl.SSLError, http.client.HTTPException, ConnectionError) as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if not streamed_any and attempt < policy.max_retries:
                prefer_direct = True
                delay = policy.delay_for_attempt(attempt)
            else:
                err_msg = str(exc)
                GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", err_msg)
                raise TransientProviderError(f"Model unreachable: {err_msg}", error_code=type(exc).__name__) from exc

        except (BudgetExceededError, CancelledError):
            meter_status = 'not_sent' if result is None and reservation is None else 'error'
            raise
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            GLOBAL_TELEMETRY.record(api, profile.get("model", ""), duration_ms, {}, "error", str(exc))
            raise
        finally:
            if prev_direct is not None:
                try:
                    import workflow as _wf
                    _wf._PROXY_DIRECT.active = prev_direct
                except Exception:
                    pass
            if meter_status != 'not_sent':
                usage_stats.record(profile,messages,tools,int((time.monotonic()-attempt_start)*1000),
                                   (result or {}).get('usage') or {},meter_status,attempt,first_token_ms)
        _wait(delay, cancel_check)

    raise TransientProviderError("Exhausted model retries")


def _is_fallback_eligible_error(exc: Exception) -> bool:
    if isinstance(exc, (CancelledError, BudgetExceededError)):
        return False
    if isinstance(exc, (ModelUnavailableError, QuotaOrAuthError, TransientProviderError, ContextOverflowError)):
        return True
    if isinstance(exc, AIRuntimeError):
        return bool(exc.retryable_on_fallback)
    msg = str(exc)
    if any(tok in msg for tok in ("尚未安装", "未登录", "官方账号", "CLI", "不可用", "未配置")):
        return True
    return False


def call_with_fallback(profiles: list[dict[str, Any]], messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]] | None = None,
                       temperature: float = 0.2, timeout: int = 180,
                       max_tokens: int = 4096, retry_policy: RetryPolicy | None = None,
                       budget: Budget | None = None, cancel_check: Callable[[], bool] | None = None,
                       on_delta: Callable[[str], None] | None = None,
                       on_reasoning: Callable[[str], None] | None = None,
                       on_fallback: Callable[[dict[str, Any], dict[str, Any], Exception], None] | None = None,
                       on_attempt_result: Callable[[dict[str, Any], bool, Exception | None], None] | None = None,
                       caller: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    candidates = [p for p in (profiles or []) if p is not None]
    if not candidates:
        if caller is None:
            raise ModelUnavailableError("No model configured: please configure a model profile.")
        candidates = [{}]

    invoke_fn = caller or call
    errors: list[tuple[dict[str, Any], Exception]] = []
    for index, candidate in enumerate(candidates):
        if cancel_check and cancel_check():
            raise CancelledError("Operation cancelled before fallback attempt")
        streamed_any = False

        def tracked_delta(piece: str) -> None:
            nonlocal streamed_any
            streamed_any = True
            if on_delta:
                on_delta(piece)

        kwargs: dict[str, Any] = {
            "tools": tools,
            "temperature": temperature,
            "timeout": timeout,
            "max_tokens": max_tokens,
            "on_delta": tracked_delta if on_delta else None,
            "cancel_check": cancel_check,
            "on_reasoning": on_reasoning,
        }
        if invoke_fn is call:
            # When failing over across multiple candidates, cap per-candidate transient retries slightly
            # if a backup exists unless caller passed an explicit policy.
            effective_policy = retry_policy
            if effective_policy is None and len(candidates) > 1 and index < len(candidates) - 1:
                effective_policy = RetryPolicy(max_retries=1)
            kwargs["retry_policy"] = effective_policy
            kwargs["budget"] = budget

        try:
            result = invoke_fn(candidate, messages, **kwargs)
            if on_attempt_result:
                on_attempt_result(candidate, True, None)
            if isinstance(result, dict):
                result.setdefault("_selected_profile_id", candidate.get("id", ""))
                result.setdefault("_selected_model", candidate.get("model", ""))
            return result
        except Exception as exc:
            if on_attempt_result:
                on_attempt_result(candidate, False, exc)
            errors.append((candidate, exc))
            has_next = index + 1 < len(candidates)
            if streamed_any or not has_next or not _is_fallback_eligible_error(exc):
                if len(errors) > 1:
                    summary = "；".join(
                        f"{p.get('model') or p.get('id')}: {e}" for p, e in errors
                    )
                    raise ModelUnavailableError(
                        f"主模型及备用模型均不可用（{summary}）",
                        error_code=getattr(exc, "error_code", ""),
                    ) from exc
                raise
            if on_fallback:
                on_fallback(candidate, candidates[index + 1], exc)

    raise ModelUnavailableError("No available model succeeded.")
