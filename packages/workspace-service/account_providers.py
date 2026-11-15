"""Official local CLI account sessions, without importing or exposing tokens."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from queue import Empty, Queue
import urllib.error
import urllib.request
import uuid


CATALOG = {
    "openai-codex": {"name": "Codex · ChatGPT 账号", "command": "codex", "wire": "codex-cli",
        "login": ["login"], "status": ["login", "status"],
        "script": "node_modules/@openai/codex/bin/codex.js",
        "docs": "https://developers.openai.com/codex/auth", "models": ["auto"]},
    "claude-code": {"name": "Claude Code · Claude 账号", "command": "claude", "wire": "claude-code-cli",
        "login": ["auth", "login"], "status": ["auth", "status"],
        "script": "node_modules/@anthropic-ai/claude-code/cli.js",
        "docs": "https://code.claude.com/docs/en/authentication", "models": ["auto", "sonnet", "opus", "haiku"]},
    "antigravity": {"name": "Antigravity · Google 账号", "command": "agy", "wire": "antigravity-cli",
        "login": [], "status": None, "script": "",
        "docs": "https://www.antigravity.google/docs/install?tab=cli", "models": ["auto"]},
}
WIRES = {c["wire"] for c in CATALOG.values()}
_host_calls = {}
_host_cancelled = {}
_host_lock = threading.Lock()


def _needs_host():
    import paths
    return os.environ.get("RW_LOW_SERVER") == "1" and bool(paths.GRANT)


def _host(action, payload, timeout=30, cancel_check=None):
    """Use the authenticated desktop host for official clients outside Low IL."""
    import execution
    import paths
    ident = uuid.uuid4().hex
    def request(body):
        req = urllib.request.Request("http://" + paths.GRANT + "/account", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-RW-Key": paths.GRANT_KEY})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout + 10) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                result = json.loads(exc.read())
            except ValueError:
                raise RuntimeError("官方账号宿主通道不可用，请重新启动桌面应用") from None
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result
    answer = Queue()
    def work():
        try:
            answer.put((True, request({"action": action, "request_id": ident, **payload})))
        except Exception as exc:
            answer.put((False, exc))
    threading.Thread(target=work, daemon=True, name="account-host-io").start()
    while True:
        if cancel_check and cancel_check():
            # The host owns the CLI process and terminates its process tree.
            threading.Thread(target=lambda: _cancel_host(ident), daemon=True).start()
            raise execution.Cancelled("运行已中断")
        try:
            ok, result = answer.get(timeout=0.05)
        except Empty:
            continue
        if not ok:
            raise result
        return result


def _cancel_host(ident):
    import paths
    try:
        req = urllib.request.Request("http://" + paths.GRANT + "/account",
            data=json.dumps({"action": "cancel", "request_id": ident}).encode(),
            headers={"Content-Type": "application/json", "X-RW-Key": paths.GRANT_KEY})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5):
            pass
    except OSError:
        pass


def host_request(data):
    """Narrow host protocol: fixed official clients only, no executable or shell input."""
    ident = str(data.get("request_id") or "")
    if len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
        return {"error": "账号请求标识无效"}
    action = data.get("action")
    if action == "cancel":
        with _host_lock:
            signal = _host_calls.get(ident)
            if signal:
                signal.set()
            else:
                _host_cancelled[ident] = time.monotonic()
            for old, stamp in list(_host_cancelled.items()):
                if time.monotonic() - stamp > 1800:
                    _host_cancelled.pop(old, None)
        return {"cancelled": bool(signal)}
    try:
        provider = entry(str(data.get("provider") or ""))
        if action == "status":
            return status(provider["id"])
        if action == "login":
            return login(provider["id"])
        if action != "complete":
            raise ValueError("不支持的账号宿主操作")
        model = data.get("model", "auto")
        messages, tools = data.get("messages"), data.get("tools")
        if not isinstance(model, str) or len(model) > 200 or not isinstance(messages, list) or not isinstance(tools, list):
            raise ValueError("账号模型请求格式无效")
        timeout = max(1, min(int(data.get("timeout") or 180), 1800))
        signal = threading.Event()
        with _host_lock:
            if ident in _host_calls:
                raise ValueError("账号请求正在执行")
            _host_calls[ident] = signal
            if ident in _host_cancelled:
                signal.set()
                _host_cancelled.pop(ident, None)
        try:
            return complete({"api": provider["wire"], "model": model}, messages, tools,
                            timeout=timeout, cancel_check=signal.is_set)
        finally:
            with _host_lock:
                _host_calls.pop(ident, None)
    except Exception as exc:
        return {"error": str(exc) if isinstance(exc, (ValueError, RuntimeError, TimeoutError)) else "官方账号调用失败，请检查客户端安装与登录状态"}


def entry(value: str) -> dict:
    for pid, item in CATALOG.items():
        if value in (pid, item["wire"]):
            return {"id": pid, **item}
    raise ValueError("不支持的账号 Provider")


def command(value: str) -> list[str]:
    item = entry(value)
    executable = shutil.which(item["command"])
    if not executable:
        raise ValueError(f"尚未安装 {item['command']} CLI，请按官方文档安装后重试")
    path = Path(executable)
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        if item['id'] == 'claude-code':
            native = path.parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
            if native.is_file():
                return [str(native)]
        script = path.parent / item["script"] if item["script"] else None
        node = shutil.which("node")
        if not script or not script.is_file() or not node:
            raise ValueError("无法直接运行 CLI，请安装官方可执行版本，或确保 Node.js 与官方 npm 包可用")
        return [node, str(script)]
    return [str(path)]


def _environment() -> dict:
    env = dict(os.environ)
    # Account providers must use the session selected in the official client.
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CLAUDECODE"):
        env.pop(key, None)
    env.update(NO_COLOR="1", PYTHONIOENCODING="utf-8")
    return env


def status(value: str) -> dict:
    if _needs_host():
        return _host("status", {"provider": value})
    item = entry(value)
    result = {"provider_id": item["id"], "installed": False, "authenticated": None,
              "docs": item["docs"], "login_command": " ".join([item["command"], *item["login"]])}
    try:
        args = command(value)
        result["installed"] = True
        if item["status"] is None:
            return {**result, "message": "CLI 已安装；登录状态由 Antigravity 安全凭据管理器维护，运行时验证。"}
        check = subprocess.run([*args, *item["status"]], capture_output=True, timeout=12,
                               env=_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        authenticated = check.returncode == 0
        if item["id"] == "claude-code" and authenticated:
            try:
                data = json.loads(check.stdout.decode("utf-8", "replace"))
                authenticated = bool(data.get("loggedIn", data.get("authMethod", "none") != "none"))
            except ValueError:
                authenticated = False
        return {**result, "authenticated": authenticated,
                "message": "官方客户端已登录，可复用本机账号。" if authenticated else "尚未登录，请完成官方账号登录后重新检测。"}
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return {**result, "message": str(exc) if isinstance(exc, ValueError) else "CLI 状态检测失败，请检查安装后重试。"}


def login(value: str) -> dict:
    if _needs_host():
        return _host("login", {"provider": value})
    item = entry(value)
    args = [*command(value), *item["login"]]
    if os.name != "nt":
        return {"started": False, "message": "请在本机终端执行官方登录命令。", "login_command": " ".join([item["command"], *item["login"]])}
    # Explicit UI login opens an interactive official client; tokens stay there.
    subprocess.Popen(args, env=_environment(), creationflags=subprocess.CREATE_NEW_CONSOLE)
    return {"started": True, "message": "已打开官方登录窗口，完成浏览器登录后点击重新检测。"}


def _run(args: list[str], prompt: str, timeout: int, cancel_check=None) -> str:
    import execution
    with tempfile.TemporaryDirectory(prefix="lanji-model-", ignore_cleanup_errors=True) as folder:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=errors,
                cwd=folder, env=_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + timeout
                payload = prompt.encode("utf-8")
                while True:
                    if cancel_check and cancel_check():
                        raise execution.Cancelled("运行已中断")
                    if time.monotonic() > deadline:
                        raise TimeoutError("账号模型响应超时")
                    try:
                        process.communicate(input=payload, timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        payload = None
                if process.returncode:
                    output.seek(0); errors.seek(0)
                    diagnostic = (output.read(8192) + errors.read(8192)).decode('utf-8', 'replace').lower()
                    if 'insufficient' in diagnostic and any(word in diagnostic for word in ('balance', 'credit', 'quota')):
                        raise RuntimeError('官方客户端返回账号余额或额度不足，请在客户端检查当前账号和计费配置。')
                    if any(word in diagnostic for word in ('not logged in', 'login required', 'not authenticated')):
                        raise RuntimeError('官方客户端尚未登录，请先完成账号登录。')
                    if 'not eligible' in diagnostic and 'location' in diagnostic:
                        raise RuntimeError('Antigravity 官方客户端提示当前账号所在地区暂不支持此服务。')
                    raise RuntimeError("官方 CLI 调用失败，请检查账号登录、额度及所选模型。")
                output.seek(0)
                return output.read().decode("utf-8", "replace")
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                       timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    else:
                        process.kill()
                process.wait()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()


def complete(profile, messages, tools=None, timeout=180, on_delta=None, cancel_check=None, **kwargs):
    if _needs_host():
        result = _host("complete", {"provider": profile["api"], "model": profile.get("model", "auto"),
            "messages": messages, "tools": tools or [], "timeout": timeout}, timeout=timeout, cancel_check=cancel_check)
        if on_delta and result.get("content"):
            on_delta(result["content"])
        return result
    item = entry(profile["api"])
    args = command(item["id"])
    model = profile.get("model", "auto")
    if item["id"] == "openai-codex":
        args += ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                 "-c", 'approval_policy="never"', "-c", "features.shell_tool=false",
                 "-c", "features.multi_agent=false", "-c", 'web_search="disabled"',
                 "-c", "apps._default.enabled=false", "-c", "mcp_servers={}", "-"]
    elif item["id"] == "claude-code":
        args += ["-p", "--output-format", "json", "--tools", "", "--disallowedTools", "mcp__*",
                 "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence"]
    else:
        args += ["--input-format", "stream-json", "--output-format", "stream-json", "--mode", "plan", "--disable-slash-commands"]
    if model != "auto":
        args += ["--model", model]
    prompt = ("You are a model adapter for a research workbench. Use only the supplied conversation. "
              "Do not use native CLI tools, inspect files, or run commands. "
              "Return one JSON object with content (string) and tool_calls (array). "
              "To request a listed workbench tool, add {name: string, arguments: object} to tool_calls. "
              "The workbench executes it and supplies the result in the next conversation. "
              "If finished, return an empty tool_calls array.\n" +
              json.dumps({"messages": messages, "tools": tools or []}, ensure_ascii=False))
    wire_prompt = json.dumps({'event': 'user', 'message': {'content': prompt}}, ensure_ascii=False) + '\n' if item['id'] == 'antigravity' else prompt
    raw = _run(args, wire_prompt, timeout, cancel_check)
    usage, text = {}, ""
    if item["id"] == "openai-codex":
        for line in raw.splitlines():
            event = json.loads(line)
            if event.get("type") in ("error", "turn.failed"):
                raise RuntimeError("Codex 调用失败，请检查登录和额度。")
            record = event.get("item") or {}
            if event.get("type") == "item.completed" and record.get("type") == "agent_message":
                text = record.get("text", "")
            usage = event.get("usage") or usage
    else:
        if item['id'] == 'antigravity':
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            event = next((e['result'] for e in reversed(events) if e.get('event') == 'result'), {})
        else:
            event = json.loads(raw)
        if event.get("is_error") or event.get("status", "SUCCESS") != "SUCCESS":
            raise RuntimeError("账号模型未成功完成请求，请检查官方客户端。")
        text = event.get("result") if item["id"] == "claude-code" else event.get("response")
        usage = event.get("usage") or {}
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("账号模型未返回内容")
    clean = text.strip()
    if clean.startswith("```json") and clean.endswith("```"):
        clean = clean[7:-3].strip()
    try:
        response = json.loads(clean)
    except ValueError:
        if tools:
            raise RuntimeError("账号模型未返回工作台所需的工具调用格式，请重试") from None
        response = {"content": text, "tool_calls": []}
    if not isinstance(response, dict) or not isinstance(response.get("content", ""), str) or not isinstance(response.get("tool_calls", []), list):
        raise RuntimeError("账号模型返回了无效的工作台响应")
    allowed = {t["function"]["name"] for t in tools or []}
    calls = []
    for call in response.get("tool_calls", []):
        if not isinstance(call, dict) or call.get("name") not in allowed or not isinstance(call.get("arguments"), dict):
            raise RuntimeError("账号模型请求了未授权或无效的工具")
        calls.append({"id": "account-" + uuid.uuid4().hex, "type": "function",
                      "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)}})
    content = response.get("content", "")
    if on_delta and content:
        on_delta(content)
    prompt_tokens, output_tokens = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return {"role": "assistant", "content": content, "tool_calls": calls,
            "usage": {"prompt": prompt_tokens, "completion": output_tokens, "total": prompt_tokens + output_tokens, "exact": bool(usage)}}
