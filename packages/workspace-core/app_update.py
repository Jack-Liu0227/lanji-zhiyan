"""Application version discovery and self-update helpers.

The desktop server owns the HTTP routes, while this module keeps release
parsing and download/verification logic independent of the UI.  The update
flow intentionally downloads the signed project installer rather than
modifying the running application directory in place.  ``tools/installer.py``
already performs the staged replacement and keeps user data outside the app
directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_RELEASE_API = "https://api.github.com/repos/Jack-Liu0227/lanji-zhiyan/releases/latest"
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024
USER_AGENT = "LanjiZhiyan-App-Updater"
_VERSION_RE = re.compile(r"^[vV]?([0-9]+(?:\.[0-9]+){0,3})(?:[-+].*)?$")
_SETUP_RE = re.compile(r"(?:setup|installer)[-_].*\.exe$|[-_]setup\.exe$", re.I)


class UpdateError(RuntimeError):
    """A user-facing update failure."""


def read_current_version(root: Path) -> str:
    """Read and normalize the version bundled beside the executable."""
    try:
        value = (root / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise UpdateError("无法读取当前版本：%s" % exc) from exc
    if not value:
        raise UpdateError("安装包缺少 VERSION 文件")
    return value.lstrip("vV").strip()


def version_key(value: str) -> tuple[int, ...]:
    """Return a comparable numeric key for release tags such as ``v0.4.0``."""
    match = _VERSION_RE.match(str(value or "").strip())
    if not match:
        raise UpdateError("版本号格式无效：%s" % value)
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """Compare versions while treating omitted patch components as zero."""
    left, right = version_key(latest), version_key(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _release_api_url() -> str:
    value = os.environ.get("LANJI_UPDATE_RELEASE_API", "").strip()
    return value or DEFAULT_RELEASE_API


def _fetch_json(url: str, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=20) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except Exception as exc:
        raise UpdateError("检查更新失败：%s" % exc) from exc
    if len(payload) > 2 * 1024 * 1024:
        raise UpdateError("更新服务返回的数据过大")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("更新服务返回的数据无效") from exc
    if not isinstance(data, dict):
        raise UpdateError("更新服务返回的数据格式无效")
    if data.get("message") and not data.get("tag_name"):
        raise UpdateError("更新服务：%s" % data["message"])
    return data


def _allowed_download_hosts() -> set[str]:
    extra = {part.strip().lower() for part in os.environ.get("LANJI_UPDATE_ALLOWED_HOSTS", "").split(",") if part.strip()}
    return {
        "github.com", "api.github.com", "objects.githubusercontent.com",
        "release-assets.githubusercontent.com", *extra,
    }


def _is_allowed_download_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.scheme == "https" and parsed.hostname in _allowed_download_hosts()


def _select_setup_asset(release: dict[str, Any]) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("最新版本没有可用的安装包")
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not name.lower().endswith(".exe") or "portable" in name.lower() or not _is_allowed_download_url(url):
            continue
        candidates.append(asset)
    if not candidates:
        raise UpdateError("最新版本没有 Windows 安装程序")
    candidates.sort(key=lambda item: (not bool(_SETUP_RE.search(str(item.get("name") or ""))),
                                     str(item.get("name") or "").lower()))
    selected = candidates[0]
    size = selected.get("size")
    if size is not None:
        try:
            if int(size) <= 0 or int(size) > MAX_DOWNLOAD_BYTES:
                raise UpdateError("安装包大小不受支持")
        except (TypeError, ValueError) as exc:
            raise UpdateError("安装包大小无效") from exc
    return selected


def release_info(root: Path, *, frozen: bool | None = None,
                 opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    """Fetch and normalize the latest release for the settings page."""
    current = read_current_version(root)
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    result: dict[str, Any] = {
        "current_version": current,
        "supported": bool(frozen),
        "available": False,
        "latest_version": current,
        "release_name": "",
        "published_at": "",
        "notes": "",
        "asset_name": "",
        "asset_size": 0,
        "asset_url": "",
        "asset_digest": "",
    }
    if not frozen:
        result["message"] = "源码运行模式不支持自动安装更新"
        return result
    release = _fetch_json(_release_api_url(), opener=opener)
    tag = str(release.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    version_key(latest)
    asset = _select_setup_asset(release)
    result.update({
        "available": is_newer(latest, current),
        "latest_version": latest,
        "release_name": str(release.get("name") or tag),
        "published_at": str(release.get("published_at") or ""),
        "notes": str(release.get("body") or ""),
        "asset_name": str(asset.get("name") or ""),
        "asset_size": int(asset.get("size") or 0),
        "asset_url": str(asset.get("browser_download_url") or ""),
        "asset_digest": str(asset.get("digest") or ""),
        "message": "发现新版本" if is_newer(latest, current) else "当前已是最新版本",
    })
    return result


def _expected_sha256(info: dict[str, Any]) -> str:
    digest = str(info.get("asset_digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


def download_update(info: dict[str, Any], destination: Path,
                    *, opener: Callable[..., Any] = urlopen) -> tuple[Path, str]:
    """Download an installer into ``destination`` and verify its digest."""
    if not info.get("supported"):
        raise UpdateError("当前运行模式不支持自动安装更新")
    url = str(info.get("asset_url") or "")
    if not _is_allowed_download_url(url):
        raise UpdateError("更新地址不受信任")
    name = Path(str(info.get("asset_name") or "更新安装包.exe")).name
    if not name.lower().endswith(".exe"):
        raise UpdateError("更新资产不是 Windows 安装程序")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / name
    fd, temp_name = tempfile.mkstemp(prefix=".lanji-update-", suffix=".tmp", dir=str(destination))
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as output:
            request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT})
            try:
                with opener(request, timeout=60) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise UpdateError("安装包超过大小限制")
                        digest.update(chunk)
                        output.write(chunk)
            except UpdateError:
                raise
            except Exception as exc:
                raise UpdateError("下载更新失败：%s" % exc) from exc
        expected_size = int(info.get("asset_size") or 0)
        if total <= 0 or (expected_size > 0 and total != expected_size):
            raise UpdateError("安装包大小校验失败，请稍后重试")
        actual = digest.hexdigest()
        expected = _expected_sha256(info)
        if expected and actual != expected:
            raise UpdateError("安装包校验失败，请稍后重试")
        os.replace(temp_name, target)
        return target, actual
    except Exception:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def start_installer(installer: Path, *, install_dir: Path | None = None,
                    create_shortcut: bool = True) -> None:
    """Start the staged installer and let it close/replace this app."""
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise UpdateError("更新安装包不存在")
    creationflags = 0x08000000 if os.name == "nt" else 0
    args = [str(installer), "--silent"]
    if install_dir is not None:
        args.extend(["--install-dir", str(install_dir)])
    if not create_shortcut:
        args.append("--no-shortcut")
    try:
        subprocess.Popen(args, cwd=str(installer.parent),
                         close_fds=True, creationflags=creationflags,
                         start_new_session=True)
    except OSError as exc:
        raise UpdateError("无法启动安装程序：%s" % exc) from exc
