"""Built-in skill catalogs and staged, non-executing skill installation."""
from __future__ import annotations

import copy
import base64
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

import paths

SOURCES = (
    {"id": "skills-sh", "adapter": "skills-sh", "name": "skills.sh", "url": "https://skills.sh",
     "api_url": "https://skills.sh/api/search", "download_url": "", "enabled": True,
     "description": "搜索社区技能，直接从原始仓库安装，无需配置密钥。"},
    {"id": "skillhub", "adapter": "skillhub", "name": "SkillHub", "url": "https://skillhub.cn",
     "api_url": "https://api.skillhub.cn/api/skills", "download_url": "https://api.skillhub.cn/api/v1/download?slug={slug}", "enabled": True,
     "description": "腾讯技能源，提供中文搜索、版本和技能包下载。"},
    {"id": "skillsmp", "adapter": "skillsmp", "name": "SkillsMP", "url": "https://skillsmp.com",
     "api_url": "https://skillsmp.com/api/v1/skills/search", "download_url": "", "enabled": True,
     "description": "搜索社区收录的 GitHub 技能，匿名访问受来源配额限制。"},
)
ADAPTERS = (
    {"id": "index", "name": "技能索引 JSON"},
    {"id": "skills-sh", "name": "skills.sh 兼容接口"},
    {"id": "skillhub", "name": "SkillHub 兼容接口"},
    {"id": "skillsmp", "name": "SkillsMP 兼容接口"},
)
MAX_DOWNLOAD = 50 * 1024 * 1024
MAX_EXTRACT = 100 * 1024 * 1024
MAX_FILES = 5000
PREVIEW_TTL = 3600
_IGNORED = {".git", "node_modules", ".venv", "__pycache__"}
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_TOKEN = re.compile(r"[a-f0-9]{32}\Z")
_LOCK = threading.RLock()
_SEARCH_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def _source_store(db):
    conn = db.db if hasattr(db, "db") else db
    conn.execute("""CREATE TABLE IF NOT EXISTS skill_sources (
        id TEXT PRIMARY KEY, adapter TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
        api_url TEXT NOT NULL, download_url TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1, builtin INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0
    )""")
    for index, source in enumerate(SOURCES):
        conn.execute("""INSERT OR IGNORE INTO skill_sources
            (id,adapter,name,url,api_url,download_url,description,enabled,builtin,sort_order)
            VALUES(:id,:adapter,:name,:url,:api_url,:download_url,:description,:enabled,1,:sort_order)""",
                     {**source, "sort_order": index})
    conn.commit()
    return conn


def sources(db) -> list[dict]:
    conn = _source_store(db)
    return [{**dict(row), "enabled": bool(row["enabled"]), "builtin": bool(row["builtin"])}
            for row in conn.execute("SELECT id,adapter,name,url,api_url,download_url,description,enabled,builtin FROM skill_sources WHERE deleted=0 ORDER BY sort_order,id")]


def _config_url(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(label + "必须是有效的 HTTPS 地址")
    value = value.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError(label + "必须是不含登录凭据和锚点的 HTTPS 地址")
    return value


def save_source(db, body: dict) -> dict:
    conn = _source_store(db)
    source_id = body.get("id", "")
    previous = next((item for item in sources(db) if item["id"] == source_id), None)
    if source_id and previous is None:
        raise ValueError("技能源不存在，请刷新来源列表")
    base = previous or {"id": "source-" + uuid.uuid4().hex[:16], "adapter": "index", "name": "", "url": "", "api_url": "", "download_url": "", "description": "", "enabled": True, "builtin": False}
    item = {**base, **{key: body[key] for key in ("adapter", "name", "url", "api_url", "download_url", "description", "enabled") if key in body}}
    for key, limit in (("name", 80), ("description", 1000)):
        if not isinstance(item[key], str) or len(item[key]) > limit:
            raise ValueError(f"来源{ '名称' if key == 'name' else '说明' }格式无效或过长")
        item[key] = item[key].strip()
    if not item["name"]:
        raise ValueError("请输入来源名称")
    if not isinstance(item["adapter"], str) or item["adapter"] not in {adapter["id"] for adapter in ADAPTERS} or not isinstance(item["enabled"], bool):
        raise ValueError("请选择有效的来源类型和启用状态")
    item["url"] = _config_url(item["url"], "网站地址")
    item["api_url"] = _config_url(item["api_url"], "搜索接口或索引地址")
    if item["adapter"] == "skillhub":
        template = item["download_url"]
        if not isinstance(template, str) or template.count("{slug}") != 1:
            raise ValueError("SkillHub 下载地址模板必须包含一个 {slug}")
        _config_url(template.replace("{slug}", "example"), "下载地址")
    else:
        item["download_url"] = ""
    with _LOCK:
        if previous is None and len(sources(db)) >= 20:
            raise ValueError("最多保存 20 个技能源，请先删除不需要的来源")
        order = conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM skill_sources").fetchone()[0]
        conn.execute("""INSERT INTO skill_sources(id,adapter,name,url,api_url,download_url,description,enabled,builtin,sort_order)
            VALUES(:id,:adapter,:name,:url,:api_url,:download_url,:description,:enabled,:builtin,:sort_order)
            ON CONFLICT(id) DO UPDATE SET adapter=excluded.adapter,name=excluded.name,url=excluded.url,
                api_url=excluded.api_url,download_url=excluded.download_url,description=excluded.description,enabled=excluded.enabled""",
                     {**item, "sort_order": order})
        conn.commit()
        _SEARCH_CACHE.clear()
    return {"source": item, "sources": sources(db)}


def delete_source(db, source_id: str) -> dict:
    conn = _source_store(db)
    if not isinstance(source_id, str) or not any(item["id"] == source_id for item in sources(db)):
        raise ValueError("技能源不存在，请刷新来源列表")
    with _LOCK:
        conn.execute("UPDATE skill_sources SET deleted=1 WHERE id=?", (source_id,))
        conn.commit()
        _SEARCH_CACHE.clear()
    return {"sources": sources(db)}


def restore_sources(db) -> dict:
    conn = _source_store(db)
    with _LOCK:
        for source in SOURCES:
            conn.execute("""UPDATE skill_sources SET adapter=:adapter,name=:name,url=:url,api_url=:api_url,
                download_url=:download_url,description=:description,enabled=1,deleted=0 WHERE id=:id""", source)
        conn.commit()
        _SEARCH_CACHE.clear()
    return {"sources": sources(db)}


def _ensure(db):
    conn = db.db if hasattr(db, "db") else db
    conn.execute("""CREATE TABLE IF NOT EXISTS skill_installations (
        path TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT NOT NULL,
        provider TEXT NOT NULL, source TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
        digest TEXT NOT NULL, files INTEGER NOT NULL, installed_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def installations(db) -> dict[str, dict]:
    conn = _ensure(db)
    return {row["path"]: dict(row) for row in conn.execute("SELECT * FROM skill_installations")}


def _public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("技能源只支持不含登录凭据的 HTTPS 链接")
    if parsed.port not in (None, 443):
        raise ValueError("技能源链接必须使用标准 HTTPS 端口")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析技能源地址，请检查网络连接") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("技能源链接必须指向公共网络地址")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(url: str, limit: int = MAX_DOWNLOAD) -> bytes:
    req = urllib.request.Request(_public_url(url), headers={"User-Agent": "LanjiZhiyan/skill-installer"})
    try:
        with urllib.request.build_opener(_Redirects()).open(req, timeout=35) as response:
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > limit:
                raise ValueError("技能源下载超过大小限制，请导入单个技能目录或压缩包")
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ValueError("技能源访问达到配额，请稍后重试或切换来源") from None
        if exc.code in (401, 403):
            raise ValueError("技能源拒绝访问，请稍后重试，或使用公开 GitHub 链接安装") from None
        raise ValueError(f"技能源请求失败（HTTP {exc.code}），请检查链接或稍后重试") from None
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError("无法连接技能源，请检查网络后重试") from exc
    if len(data) > limit:
        raise ValueError("技能源下载超过大小限制，请导入单个技能目录或压缩包")
    return data


def _json(url: str) -> dict:
    try:
        data = json.loads(_read(url, 4 * 1024 * 1024))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("技能源返回了无效数据，请稍后重试") from None
    if not isinstance(data, dict):
        raise ValueError("技能源返回的数据格式不受支持")
    return data


def _query_url(endpoint: str, parameters: dict) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    query = {**dict(urllib.parse.parse_qsl(parsed.query)), **parameters}
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def _search_one(config: dict, query: str) -> dict:
    key = (json.dumps(config, sort_keys=True), query)
    with _LOCK:
        cached = _SEARCH_CACHE.get(key)
        if cached and time.time() - cached[0] < 300:
            return copy.deepcopy(cached[1])
    items = []
    adapter = config["adapter"]
    if adapter == "skills-sh":
        data = _json(_query_url(config["api_url"], {"q": query, "limit": 20}))
        rows = data.get("skills", [])
        for row in rows:
            source, skill = str(row.get("source") or ""), str(row.get("skillId") or row.get("name") or "")
            url = "https://skills.sh/" + str(row.get("id") or f"{source}/{skill}")
            items.append({"name": row.get("name") or skill, "description": row.get("description") or "",
                          "author": source, "url": config["url"].rstrip("/") + "/" + str(row.get("id") or f"{source}/{skill}"),
                          "source": url, "downloads": row.get("installs", 0)})
    elif adapter == "skillhub":
        data = _json(_query_url(config["api_url"],
            {"keyword": query, "pageSize": 20, "sortBy": "downloads", "order": "desc"}))
        if data.get("code", 0) != 0:
            raise ValueError("SkillHub 搜索失败，请稍后重试")
        for row in (data.get("data") or {}).get("skills", []):
            slug = str(row.get("slug") or "")
            canonical = (row.get("namespace") or {}).get("canonicalName") or slug
            url = config["url"].rstrip("/") + "/skills/" + urllib.parse.quote(str(canonical), safe="@/")
            items.append({"name": row.get("name") or slug, "description": row.get("description_zh") or row.get("description") or "",
                          "author": row.get("ownerName") or "", "url": url, "source": url,
                          "download_url": config["download_url"].replace("{slug}", urllib.parse.quote(str(canonical), safe="")),
                          "downloads": row.get("downloads", 0), "version": row.get("version") or ""})
    elif adapter == "skillsmp":
        data = _json(_query_url(config["api_url"], {"q": query, "limit": 20}))
        if data.get("success") is False:
            raise ValueError("SkillsMP 搜索失败，请稍后重试")
        for row in (data.get("data") or {}).get("skills", []):
            items.append({"name": row.get("name") or "", "description": row.get("description") or "",
                          "author": row.get("author") or "", "url": row.get("skillUrl") or "https://skillsmp.com",
                          "source": row.get("githubUrl") or "", "downloads": None})
    else:
        data = _json(config["api_url"])
        if not isinstance(data.get("skills"), list):
            raise ValueError("技能索引缺少 skills 列表")
        for row in data["skills"]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            name = row["name"]
            description = str(row.get("description") or "")
            if query.casefold() not in (name + " " + description).casefold():
                continue
            items.append({"name": name, "description": description, "author": str(row.get("author") or config["name"]),
                          "url": config["url"], "source": config["api_url"], "downloads": None,
                          "index_url": config["api_url"], "skill_name": name})
            if len(items) == 20:
                break
    for item in items:
        item.update(provider=config["id"], provider_name=config["name"])
    result = {"provider": config["id"], "query": query, "skills": items}
    with _LOCK:
        if len(_SEARCH_CACHE) >= 64:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
        _SEARCH_CACHE[key] = (time.time(), result)
    return result


def search(db, provider: str, query: str) -> dict:
    query = str(query or "").strip()
    if not 2 <= len(query) <= 200:
        raise ValueError("请输入 2 至 200 个字符的搜索词")
    configured = sources(db)
    selected = [item for item in configured if item["enabled"] and (provider == "all" or item["id"] == provider)]
    if provider != "all" and not selected:
        raise ValueError("技能源已停用或删除，请刷新来源列表")
    if not selected:
        return {"provider": provider, "query": query, "skills": [], "sources": [], "errors": [],
                "message": "没有启用的技能源，请在来源管理中启用或添加来源"}
    groups, results, errors = [], [], []
    with ThreadPoolExecutor(max_workers=min(8, len(selected))) as executor:
        pending = [(config, executor.submit(_search_one, config, query)) for config in selected]
        for config, future in pending:
            try:
                items = future.result()["skills"]
                groups.append(items)
                results.append({"id": config["id"], "name": config["name"], "count": len(items), "ok": True})
            except Exception as failure:
                message = str(failure) if isinstance(failure, ValueError) else "来源搜索失败，请检查接口地址和返回格式"
                errors.append({"provider": config["id"], "name": config["name"], "error": message})
                results.append({"id": config["id"], "name": config["name"], "count": 0, "ok": False})
    items = [group[index] for index in range(max((len(group) for group in groups), default=0))
             for group in groups if index < len(group)]
    return {"provider": provider, "query": query, "skills": items, "sources": results, "errors": errors}


def _relative(value: str) -> PurePosixPath:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in (".", "..") or any(c in part for c in ':<>"|?*\x00') or part.endswith((".", " "))
        or re.match(r"(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)", part)
        for part in value.split("/") if part
    ):
        raise ValueError("技能包包含不安全的文件路径")
    return path


def _unpack(data: bytes, root: Path) -> None:
    total, count = 0, 0
    seen = set()

    def write(name, size, stream):
        nonlocal total, count
        relative = _relative(name)
        key = str(relative).casefold()
        if key in seen:
            raise ValueError("技能包包含重复文件路径")
        seen.add(key)
        count += 1
        total += size
        if count > MAX_FILES or total > MAX_EXTRACT:
            raise ValueError("技能包解压超过文件数或大小限制")
        target = root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = stream.read(size + 1)
        if len(content) != size:
            raise ValueError("技能包文件大小与清单不一致")
        target.write_bytes(content)

    blob = io.BytesIO(data)
    if zipfile.is_zipfile(blob):
        with zipfile.ZipFile(blob) as archive:
            if len(archive.infolist()) > MAX_FILES * 2:
                raise ValueError("技能包条目过多")
            for item in archive.infolist():
                _relative(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("技能包不能包含符号链接")
                if not item.is_dir():
                    with archive.open(item) as stream:
                        write(item.filename, item.file_size, stream)
    else:
        blob.seek(0)
        try:
            with tarfile.open(fileobj=blob, mode="r:*") as archive:
                for index, item in enumerate(archive):
                    if index >= MAX_FILES * 2:
                        raise ValueError("技能包条目过多")
                    name = item.name
                    while name.startswith("./"):
                        name = name[2:]
                    if item.isdir():
                        if name in ("", "."):
                            continue
                        _relative(name)
                        continue
                    _relative(name)
                    if not item.isfile():
                        raise ValueError("技能包不能包含链接或特殊文件")
                    with archive.extractfile(item) as stream:
                        write(name, item.size, stream)
        except tarfile.TarError:
            raise ValueError("请选择 ZIP、.skill 或 tar.gz 格式的技能包") from None


def _files(root: Path):
    total, count = 0, 0
    for parent, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(item for item in directories if item not in _IGNORED)
        for name in [*directories, *names]:
            path = Path(parent) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("技能目录不能包含符号链接或目录联接")
        for name in sorted(names):
            path = Path(parent) / name
            _relative(path.relative_to(root).as_posix())
            if not path.is_file():
                raise ValueError("技能目录包含特殊文件")
            count += 1
            total += path.stat().st_size
            if count > MAX_FILES or total > MAX_EXTRACT:
                raise ValueError("技能目录超过文件数或大小限制")
            yield path


def _copy(root: Path, target: Path) -> None:
    for source in _files(root):
        destination = target / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = list(_files(root))
    for file in sorted(files):
        digest.update(file.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest(), len(files)


def _cache_root() -> Path:
    root = (paths.CACHE / "skill-imports").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _remove(directory: Path, root: Path) -> None:
    if directory.is_symlink() or directory.resolve() == root.resolve() or not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("拒绝清理技能目录之外的路径")
    if directory.exists():
        shutil.rmtree(directory)


def _source(value: str) -> dict:
    value = str(value or "").strip()
    if not value or len(value) > 2048:
        raise ValueError("请输入社区链接、GitHub 仓库或本地技能路径")
    local = Path(value).expanduser()
    if not value.startswith(("https://", "http://")) and local.exists():
        return {"provider": "local", "source": str(local.resolve()), "local": local.resolve(), "skill": "", "subpath": ""}
    if value.startswith(("npx ", "npm ", "git ")):
        raise ValueError("请粘贴技能或仓库链接，不需要粘贴安装命令")
    if re.fullmatch(r"[\w.-]+/[\w.-]+(?:@[a-z0-9._-]+)?", value):
        repo, _, skill = value.partition("@")
        value = "https://github.com/" + repo
    else:
        skill = ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("请使用 HTTPS 技能链接或有效的本地技能路径")
    host = (parsed.hostname or "").removeprefix("www.")
    parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if host == "skills.sh":
        if len(parts) == 3 and parts[0] == "site":
            return {"provider": "skills-sh", "source": value, "site": "https://" + parts[1],
                    "skill": parts[2], "subpath": ""}
        if len(parts) not in (2, 3):
            raise ValueError("请使用 skills.sh 的技能或仓库链接")
        owner, repo = parts[:2]
        skill = parts[2] if len(parts) == 3 else ""
        provider = "skills-sh"
    elif host == "skillhub.cn":
        if not parts or parts[0] != "skills" or len(parts) not in (2, 3):
            raise ValueError("请使用 SkillHub 的具体技能链接")
        slug = "/".join(parts[1:])
        if not all(re.fullmatch(r"@?[\w.-]+", part) for part in parts[1:]):
            raise ValueError("SkillHub 技能标识无效")
        return {"provider": "skillhub", "source": value, "skill": "", "subpath": "",
                "download": "https://api.skillhub.cn/api/v1/download?" + urllib.parse.urlencode({"slug": slug})}
    elif host == "skillsmp.com":
        class Links(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for key, href in attrs if tag == "a" else []:
                    if key == "href" and re.match(r"https://github\.com/[^/]+/[^/]+/(tree|blob)/", href or ""):
                        links.add(href)
        links = set()
        Links().feed(_read(value, 4 * 1024 * 1024).decode("utf-8"))
        if len(links) != 1:
            raise ValueError("未找到唯一的技能仓库，请使用 SkillsMP 搜索或详情中的 GitHub 链接")
        result = _source(links.pop())
        result.update(provider="skillsmp", source=value)
        return result
    elif host == "github.com" and len(parts) >= 2:
        owner, repo = parts[:2]
        provider = "github"
    elif parsed.path.lower().endswith((".zip", ".skill", ".tar.gz", ".tgz", ".tar")):
        return {"provider": "archive", "source": value, "download": value, "skill": "", "subpath": ""}
    else:
        return {"provider": "website", "source": value, "site": value, "skill": "", "subpath": ""}
    if not re.fullmatch(r"[\w.-]+", owner) or not re.fullmatch(r"[\w.-]+", repo.removesuffix(".git")):
        raise ValueError("GitHub 仓库地址无效")
    repo = repo.removesuffix(".git")
    ref, subpath = "HEAD", ""
    if host == "github.com" and len(parts) > 2:
        if len(parts) < 4 or parts[2] not in ("tree", "blob"):
            raise ValueError("请使用 GitHub 仓库或技能目录链接")
        ref, subpath = parts[3], "/".join(parts[4:])
        if parts[-1] == "SKILL.md":
            subpath = "/".join(parts[4:-1])
        if subpath:
            _relative(subpath)
    return {"provider": provider, "source": value, "skill": skill, "subpath": subpath,
            "download": f"https://codeload.github.com/{owner}/{repo}/zip/{urllib.parse.quote(ref, safe='')}"}


def _website(origin: dict, payload: Path) -> None:
    site = urllib.parse.urlsplit(origin["site"])
    endpoint = None
    if origin.get("index_url") or site.path.endswith("/index.json"):
        endpoints = [origin["site"]]
    else:
        base = urllib.parse.urlunsplit((site.scheme, site.netloc, "", "", ""))
        endpoints = [base + "/.well-known/agent-skills/index.json", base + "/.well-known/skills/index.json"]
    for candidate in endpoints:
        try:
            index = _json(candidate)
            if isinstance(index.get("skills"), list):
                endpoint = candidate
                break
        except ValueError:
            continue
    if not endpoint:
        if site.path.endswith("/SKILL.md"):
            data = _read(origin["site"], 1024 * 1024)
            (payload / "SKILL.md").write_bytes(data)
            return
        raise ValueError("该网站未提供可安装的技能索引，请使用技能仓库或压缩包链接")
    entries = [entry for entry in index["skills"] if not origin["skill"] or entry.get("name") == origin["skill"]]
    if not entries or len(entries) > 100:
        raise ValueError("网站中未找到所选技能，或技能过多，请使用具体技能链接")
    total, count = 0, 0
    for entry in entries:
        name = str(entry.get("name") or "")
        if not _NAME.fullmatch(name):
            raise ValueError("网站技能索引包含无效名称")
        _relative(name)
        folder = payload / name
        if folder.exists():
            raise ValueError("网站技能索引包含重复名称")
        folder.mkdir()
        if entry.get("url"):
            data = _read(urllib.parse.urljoin(endpoint, entry["url"]))
            expected = str(entry.get("digest") or "")
            valid = ("sha256:" + hashlib.sha256(data).hexdigest(),
                     "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii"))
            if expected and expected not in valid:
                raise ValueError("网站技能包校验失败")
            if entry.get("type") == "archive":
                _unpack(data, folder)
            else:
                (folder / "SKILL.md").write_bytes(data)
            for file in _files(folder):
                total += file.stat().st_size
                count += 1
                if total > MAX_EXTRACT or count > MAX_FILES:
                    raise ValueError("网站技能超过文件数或大小限制")
        else:
            files = entry.get("files") or ["SKILL.md"]
            if not isinstance(files, list) or len(files) > MAX_FILES:
                raise ValueError("网站技能文件清单无效")
            for file in files:
                relative = _relative(str(file))
                content = _read(urllib.parse.urljoin(endpoint, name + "/" + urllib.parse.quote(relative.as_posix())), MAX_EXTRACT)
                total += len(content)
                count += 1
                if total > MAX_EXTRACT or count > MAX_FILES:
                    raise ValueError("网站技能超过文件数或大小限制")
                target = folder.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)


def _prune(cache: Path) -> None:
    for directory in cache.iterdir():
        if _TOKEN.fullmatch(directory.name) and not directory.is_symlink():
            try:
                if time.time() - directory.stat().st_mtime > PREVIEW_TTL:
                    _remove(directory, cache)
            except FileNotFoundError:
                continue


def preview(source: str, provider: str = "", selection: dict | None = None) -> dict:
    import capabilities
    selection = selection or {}
    if selection.get("download_url"):
        origin = {"source": source, "provider": "archive", "download": selection["download_url"], "skill": "", "subpath": ""}
    elif selection.get("index_url"):
        origin = {"source": source, "provider": "website", "site": selection["index_url"], "index_url": True,
                  "skill": selection.get("skill_name", ""), "subpath": ""}
    else:
        origin = _source(source)
    if provider:
        origin["provider"] = provider
    token = uuid.uuid4().hex
    cache = _cache_root()
    _prune(cache)
    directory = cache / token
    payload = directory / "payload"
    payload.mkdir(parents=True)
    try:
        if origin.get("local"):
            local = origin["local"]
            local_folder = local if local.is_dir() else local.parent if local.name == "SKILL.md" else None
            if local_folder is not None:
                if payload.is_relative_to(local_folder):
                    raise ValueError("不能导入包含应用缓存的目录，请选择具体技能目录")
                _copy(local_folder, payload)
            else:
                if local.stat().st_size > MAX_DOWNLOAD:
                    raise ValueError("技能压缩包超过 50 MiB，请选择具体技能目录")
                _unpack(local.read_bytes(), payload)
        elif origin.get("site"):
            _website(origin, payload)
        else:
            _unpack(_read(origin["download"]), payload)
        base = payload
        children = list(payload.iterdir())
        if len(children) == 1 and children[0].is_dir() and not (payload / "SKILL.md").exists():
            base = children[0]
        if origin["subpath"]:
            base = base.joinpath(*_relative(origin["subpath"]).parts)
            if not base.is_dir():
                raise ValueError("仓库中不存在指定的技能目录")
        candidates = []
        names = set()
        skill_files = [base / "SKILL.md"] if (base / "SKILL.md").is_file() else sorted(base.rglob("SKILL.md"))
        for file in skill_files:
            if any(part in _IGNORED for part in file.relative_to(base).parts):
                continue
            if file.stat().st_size > capabilities.MAX_SKILL_BYTES:
                raise ValueError("SKILL.md 超过 1 MiB")
            text = file.read_text(encoding="utf-8-sig")
            name, description = capabilities._skill_frontmatter(text, file.parent.name)
            if origin["skill"] and origin["skill"] not in (name, file.parent.name):
                continue
            if not _NAME.fullmatch(name):
                raise ValueError(f"技能名称不适合作为安装目录：{name[:80]}")
            _relative(name)
            if name in names:
                raise ValueError(f"技能包中存在多个同名技能：{name}")
            names.add(name)
            digest, count = _digest(file.parent)
            candidates.append({"name": name, "description": description, "files": count, "digest": digest,
                               "folder": file.parent.relative_to(payload).as_posix(), "content": text})
        if not candidates:
            raise ValueError("未找到所选技能的 SKILL.md，请检查技能链接或目录")
        manifest = {"created_at": time.time(), "provider": origin["provider"], "source": str(origin["source"]), "skills": candidates}
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return {"token": token, "provider": manifest["provider"], "source": manifest["source"],
                "skills": [{k: v for k, v in item.items() if k != "folder"} for item in candidates]}
    except Exception:
        _remove(directory, cache)
        raise


def install(db, token: str, selected: list[str], scope: str, project_dir: str = "") -> dict:
    import capabilities
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("安装预览无效，请重新预览技能")
    if scope not in ("user", "project") or (scope == "project" and not project_dir):
        raise ValueError("请选择有效的安装范围；项目安装需要已设置文件夹的项目")
    if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected):
        raise ValueError("请至少选择一个要安装的技能")
    directory = _cache_root() / token
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("安装预览已失效，请重新预览技能") from None
    if time.time() - manifest["created_at"] > PREVIEW_TTL:
        _remove(directory, _cache_root())
        raise ValueError("安装预览已过期，请重新预览技能")
    by_name = {item["name"]: item for item in manifest["skills"]}
    if len(set(selected)) != len(selected) or any(name not in by_name for name in selected):
        raise ValueError("选择的技能不在此次预览中")
    root = next(path for kind, path in capabilities.skill_roots(project_dir) if kind == scope).resolve()
    conn = _ensure(db)
    created = []
    with _LOCK:
        root.mkdir(parents=True, exist_ok=True)
        for file in root.rglob("SKILL.md"):
            if file.resolve().is_relative_to(root) and file.stat().st_size <= capabilities.MAX_SKILL_BYTES:
                existing, _ = capabilities._skill_frontmatter(file.read_text(encoding="utf-8-sig"), file.parent.name)
                if existing in selected:
                    raise ValueError(f"此安装范围已有同名技能：{existing}，已有文件未修改")
        for name in selected:
            if not _NAME.fullmatch(name) or (root / name).exists() or (root / name).is_symlink():
                raise ValueError(f"技能安装目录已存在或名称无效：{name}")
        conn.execute("SAVEPOINT skill_install")
        try:
            results = []
            for name in selected:
                item = by_name[name]
                source = directory / "payload"
                if item["folder"] != ".":
                    source = source.joinpath(*_relative(item["folder"]).parts)
                if not source.resolve().is_relative_to((directory / "payload").resolve()):
                    raise ValueError("技能预览路径无效")
                if _digest(source) != (item["digest"], item["files"]):
                    raise ValueError("预览后的技能内容发生变化，请重新预览")
                with tempfile.TemporaryDirectory(prefix=".skill-install-", dir=root.parent) as temporary:
                    staging = Path(temporary) / name
                    staging.mkdir()
                    _copy(source, staging)
                    target = root / name
                    staging.rename(target)
                    created.append(target)
                record = {"path": str(target / "SKILL.md"), "name": name, "scope": scope,
                          "provider": manifest["provider"], "source": manifest["source"], "version": "",
                          "digest": item["digest"], "files": item["files"],
                          "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                conn.execute("INSERT OR REPLACE INTO skill_installations VALUES(:path,:name,:scope,:provider,:source,:version,:digest,:files,:installed_at)", record)
                results.append(record)
            conn.execute("RELEASE SAVEPOINT skill_install")
            conn.commit()
            return {"ok": True, "installed": results}
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT skill_install")
            conn.execute("RELEASE SAVEPOINT skill_install")
            for target in reversed(created):
                _remove(target, root)
            raise


def route(method: str, path: str, body: dict, query: dict, db, project_dir: str):
    def value(key, default=""):
        item = query.get(key, default)
        return item[0] if isinstance(item, list) and item else item
    if method == "GET" and path == "/api/capabilities/skill-sources":
        return {"sources": sources(db), "adapters": copy.deepcopy(ADAPTERS), "defaults": copy.deepcopy(SOURCES)}
    if method == "POST" and path == "/api/capabilities/skill-sources":
        return save_source(db, body)
    if method == "POST" and path == "/api/capabilities/skill-sources/delete":
        return delete_source(db, body.get("id", ""))
    if method == "POST" and path == "/api/capabilities/skill-sources/restore":
        return restore_sources(db)
    if method == "GET" and path == "/api/capabilities/skill-search":
        return search(db, str(value("provider", "all")), str(value("q")))
    if method == "POST" and path == "/api/capabilities/skill-preview":
        provider = body.get("provider", "")
        if provider and not any(item["id"] == provider for item in sources(db)):
            raise ValueError("技能源已删除，请刷新搜索结果")
        selection = body.get("selection") or {}
        if not isinstance(selection, dict) or any(not isinstance(selection.get(key, ""), str) for key in ("download_url", "index_url", "skill_name")):
            raise ValueError("技能安装来源格式无效")
        return preview(body.get("source", ""), provider, selection)
    if method == "POST" and path == "/api/capabilities/skill-install":
        return install(db, body.get("token", ""), body.get("skills"), body.get("scope", "user"), project_dir)
    return None
