# -*- coding: utf-8 -*-
"""Version and in-app update routes."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import app_update


class UpdateRoutes:
    def _update_root(self) -> Path:
        from server import ROOT
        return Path(ROOT)

    def _r_update(self, path: str, q: dict[str, list[str]]) -> Any:
        if path == "/api/app/version":
            root = self._update_root()
            try:
                return self._json({
                    "current_version": app_update.read_current_version(root),
                    "supported": bool(getattr(sys, "frozen", False)),
                })
            except app_update.UpdateError as exc:
                return self._json({"error": str(exc)}, 500)
        if path == "/api/app/update/check":
            try:
                return self._json(app_update.release_info(self._update_root()))
            except app_update.UpdateError as exc:
                return self._json({"error": str(exc)}, 502)
        return None

    def _p_update(self, path: str, data: dict[str, Any], q: dict[str, list[str]]) -> Any:
        if path != "/api/app/update/install":
            return None
        if not bool(getattr(sys, "frozen", False)):
            return self._json({"error": "源码运行模式不支持自动安装更新"}, 400)
        try:
            root = self._update_root()
            info = app_update.release_info(root)
            if not info.get("available"):
                return self._json({**info, "started": False})
            from server import paths
            # Keep the downloaded installer in the user cache, separate from
            # the running executable and project directories.
            updates_dir = Path(paths.CACHE) / "updates"
            installer, digest = app_update.download_update(info, updates_dir)
            app_dir = Path(sys.executable).resolve().parent
            # Pass the active directory explicitly so both installed and
            # portable builds replace the copy the user is currently using.
            create_shortcut = (app_dir.parent / "layout.json").is_file()
            if paths.GRANT:
                ok = paths._grant_post("/update", {
                    "installer": str(installer), "install_dir": str(app_dir),
                    "create_shortcut": create_shortcut,
                })
                if not ok:
                    raise app_update.UpdateError("无法启动安装程序")
            else:
                app_update.start_installer(installer, install_dir=app_dir,
                                           create_shortcut=create_shortcut)
            response = {
                "current_version": info["current_version"],
                "latest_version": info["latest_version"],
                "asset_name": info["asset_name"],
                "sha256": digest,
                "started": True,
                "message": "安装程序已启动，应用将自动重启",
            }
            return self._json(response)
        except app_update.UpdateError as exc:
            return self._json({"error": str(exc)}, 502)
