"""Route-specific model capacities, independent of pricing and model discovery."""
from urllib.parse import urlsplit


CHECKED_AT = "2026-11-27"
CODEX_SOURCE = "https://github.com/openai/codex/blob/a705e0bb699b12996414a17e00b0bb2c625c0bbc/codex-rs/models-manager/models.json"
DEEPSEEK_SOURCE = "https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/llm/llm-deepseek/src/models.ts"
_CODEX_WINDOWS = {
    "gpt-6.2-sol": 500000, "gpt-6.2-astra": 272000,
    "gpt-6.1-sol": 500000, "gpt-6.1-terra": 500000,
    "gpt-6-astra": 272000, "gpt-6-sol": 272000, "gpt-6-luna": 272000,
    "gpt-5.6-sol": 272000, "gpt-5.6-terra": 272000, "gpt-5.6-luna": 272000,
    "gpt-5.5": 272000,
}
_OPENAI_API_MODELS = {"gpt-6.2-sol", "gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-terra"}


def lookup(profile):
    """Return a detached capacity only for its verified route and exact model."""
    model = str(profile.get("model") or "")
    try:
        address = urlsplit(str(profile.get("base_url") or ""))
        port = address.port
    except ValueError:
        return None
    if (address.scheme == "https" and address.hostname == "api.openai.com"
            and port in (None, 443) and not address.username and not address.password
            and not address.query and not address.fragment
            and address.path.rstrip("/") in ("", "/v1")
            and profile.get("api", "openai-completions") in ("openai-completions", "openai-responses")
            and model in _OPENAI_API_MODELS):
        return {"context_window": 1050000, "max_context_window": 1050000,
                "max_input_tokens": 922000, "max_output": 128000,
                "source": "https://developers.openai.com/api/docs/models/" + model,
                "checked_at": CHECKED_AT, "provider": "openai"}
    if profile.get("api") == "codex-cli" and address.scheme == "account" and address.netloc == "openai-codex":
        if model in _CODEX_WINDOWS:
            return {"context_window": _CODEX_WINDOWS[model],
                    "max_context_window": 272000 if model == "gpt-5.5" else 872000,
                    "source": CODEX_SOURCE, "checked_at": CHECKED_AT, "provider": "openai-codex"}
    if (address.scheme == "https" and address.hostname == "api.deepseek.com"
            and port in (None, 443) and not address.username and not address.password
            and not address.query and not address.fragment
            and address.path.rstrip("/") in ("", "/v1", "/anthropic", "/anthropic/v1")
            and profile.get("api", "openai-completions") in ("openai-completions", "anthropic-messages")
            and model in ("deepseek-flash", "deepseek-v4-pro")):
        return {"context_window": 1048576, "source": DEEPSEEK_SOURCE,
                "checked_at": CHECKED_AT, "provider": "deepseek"}
    return None
