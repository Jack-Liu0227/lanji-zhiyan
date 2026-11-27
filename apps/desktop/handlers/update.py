"""Application update routes and the installer handoff."""
from pathlib import Path
import os
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import app_update

_install_lock = threading.Lock()


def install_current_release():
    from server import ROOT, paths

    if not getattr(sys, "frozen", False):
        raise app_update.UpdateError("源码运行模式不支持自动安装更新")
    if not _install_lock.acquire(blocking=False):
        raise app_update.UpdateError("更新安装程序正在准备中")
    try:
        info = app_update.release_info(Path(ROOT))
        if not info["available"]:
            return {**info, "started": False}
        installer, digest = app_update.download_update(info, Path(paths.CACHE) / "updates")
        app_dir = Path(sys.executable).resolve().parent
        standard_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "LanjiZhiyan" / "app"
        app_update.start_installer(installer, install_dir=app_dir,
                                   create_shortcut=app_dir == standard_dir.resolve())
        return {"current_version": info["current_version"], "latest_version": info["latest_version"],
                "asset_name": info["asset_name"], "sha256": digest, "started": True,
                "message": "安装程序已启动，完成后应用将自动重启",
                "installer_log": str(installer.parent / "installer.log")}
    finally:
        _install_lock.release()


class UpdateRoutes:
    def _r_update(self, path, _query):
        if path not in ("/api/app/version", "/api/app/update/check"):
            return None
        from server import ROOT

        try:
            if path == "/api/app/version":
                return self._json({"current_version": app_update.read_current_version(Path(ROOT)),
                                   "supported": bool(getattr(sys, "frozen", False))})
            return self._json(app_update.release_info(Path(ROOT)))
        except app_update.UpdateError as exc:
            return self._json({"error": str(exc)}, 502)

    def _p_update(self):
        from server import paths

        if not getattr(sys, "frozen", False):
            return self._json({"error": "源码运行模式不支持自动安装更新"}, 400)
        try:
            if paths.GRANT:
                # The host independently selects and verifies the installer. The worker supplies no paths.
                request = Request("http://" + paths.GRANT + "/update", data=b"{}",
                                  headers={"Content-Type": "application/json", "X-RW-Key": paths.GRANT_KEY})
                try:
                    with urlopen(request, timeout=650) as response:
                        return self._json(json.load(response))
                except HTTPError as exc:
                    return self._json(json.load(exc), 502)
            return self._json(install_current_release())
        except (OSError, ValueError, app_update.UpdateError) as exc:
            return self._json({"error": str(exc)}, 502)
