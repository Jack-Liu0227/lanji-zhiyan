"""Persist user-selected API modules and expose version compatibility."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import service_settings

ADMIN_MODULE = "module-composition"
SETTING_KEY = "module-composition"


def _version_tuple(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def route_for(registry: Any):
    """Return an always-on registry route that syncs settings before dispatch."""
    initialized = False

    def route(method: str, path: str, body: dict[str, Any], query: dict[str, list[str]], db: Any):
        nonlocal initialized
        is_composition_path = path == "/api/modules" or path.startswith("/api/modules/")
        if initialized and not is_composition_path:
            return None
        settings_db = db if hasattr(db, "db") else SimpleNamespace(db=db)
        state = service_settings.read(settings_db, SETTING_KEY)
        saved_modules = state.get("modules", {})
        if not isinstance(saved_modules, dict):
            raise ValueError("Invalid saved module configuration")

        modules = [item for item in registry.describe() if item["id"] != ADMIN_MODULE]
        by_id = {item["id"]: item for item in modules}
        compatibility = {}
        for item in modules:
            saved = saved_modules.get(item["id"])
            previous = saved.get("version") if isinstance(saved, dict) else None
            old_version = _version_tuple(previous)
            current_version = _version_tuple(item["version"])
            enabled = saved.get("enabled", item["enabled"]) if isinstance(saved, dict) else item["enabled"]
            if not isinstance(enabled, bool):
                raise ValueError(f"Invalid enabled state for module: {item['id']}")
            if previous is None:
                status = "untracked"
            elif old_version is None or current_version is None:
                status = "unknown-version"
                enabled = False
            elif old_version[0] != current_version[0]:
                status = "major-change"
                enabled = False
            elif old_version == current_version:
                status = "current"
            else:
                status = "same-major-update"
            registry.set_enabled(item["id"], enabled)
            item["enabled"] = enabled
            item["previous_version"] = previous
            item["compatibility"] = status
            compatibility[item["id"]] = status
        initialized = True

        if method == "GET" and path == "/api/modules":
            return {"modules": modules,
                    "unknown_modules": sorted(set(saved_modules) - set(by_id))}

        if method == "POST" and path.startswith("/api/modules/"):
            module_id = path.removeprefix("/api/modules/")
            if not module_id or "/" in module_id or module_id == ADMIN_MODULE or module_id not in by_id:
                raise KeyError(f"Unknown API module: {module_id}")
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            status = compatibility[module_id]
            accept_major_change = body.get("accept_major_change", False)
            if not isinstance(accept_major_change, bool):
                raise ValueError("accept_major_change must be a boolean")
            if enabled and status == "major-change" and not accept_major_change:
                raise ValueError("Module has a breaking major version change; review compatibility first")
            if enabled and status == "unknown-version":
                raise ValueError("Module version history is unknown; repair its saved version before enabling")
            prior = saved_modules.get(module_id)
            previous = prior.get("version") if isinstance(prior, dict) else None
            version = by_id[module_id]["version"] if enabled or previous is None else previous
            updated = dict(state)
            updated_modules = dict(saved_modules)
            updated_modules[module_id] = {"enabled": enabled, "version": version}
            updated["modules"] = updated_modules
            service_settings.write(settings_db, SETTING_KEY, updated)
            registry.set_enabled(module_id, enabled)
            return {"ok": True, "module": module_id, "enabled": enabled}
        return None

    return route
