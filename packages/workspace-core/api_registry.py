"""Small ordered registry for independently maintained API modules."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Callable


Route = Callable[[str, str, dict[str, Any], dict[str, list[str]], Any], Any | None]
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ApiRegistry:
    """Dispatch requests to registered modules in registration order."""

    def __init__(self, available_capabilities: Iterable[str] = ()) -> None:
        self._available = frozenset(available_capabilities)
        self._modules: dict[str, dict[str, Any]] = {}

    def register(self, module_id: str, route: Route, *, version: str = "1.0.0",
                 requires: Iterable[str] = (), enabled: bool = True) -> None:
        key = str(module_id).strip()
        if not key:
            raise ValueError("API module id must not be empty")
        if key in self._modules:
            raise ValueError(f"API module already registered: {key}")
        if not callable(route):
            raise TypeError(f"API module route must be callable: {key}")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError(f"API module version must use MAJOR.MINOR.PATCH: {key} ({version})")
        dependencies = tuple(dict.fromkeys(str(item).strip() for item in requires))
        if any(not item for item in dependencies):
            raise ValueError(f"API module dependencies must not be empty: {key}")
        missing = set(dependencies) - self._available
        if missing:
            raise ValueError(f"API module {key} requires unavailable capabilities: {', '.join(sorted(missing))}")
        self._modules[key] = {
            "route": route,
            "version": version,
            "requires": dependencies,
            "enabled": bool(enabled),
        }

    def describe(self) -> list[dict[str, Any]]:
        return [{"id": module_id, "version": module["version"],
                 "requires": list(module["requires"]), "enabled": module["enabled"]}
                for module_id, module in self._modules.items()]

    def set_enabled(self, module_id: str, enabled: bool) -> None:
        key = str(module_id).strip()
        if key not in self._modules:
            raise KeyError(f"Unknown API module: {key}")
        if not isinstance(enabled, bool):
            raise TypeError(f"API module enabled state must be boolean: {key}")
        self._modules[key]["enabled"] = enabled

    def dispatch(self, method: str, path: str, body: dict[str, Any],
                 query: dict[str, list[str]], db: Any) -> Any | None:
        for module in self._modules.values():
            if not module["enabled"]:
                continue
            response = module["route"](method, path, body, query, db)
            if response is not None:
                return response
        return None
