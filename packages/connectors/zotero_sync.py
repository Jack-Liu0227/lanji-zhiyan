"""Local-first Zotero metadata and project memberships; never opens zotero.sqlite."""
from __future__ import annotations

import hashlib
import json
import re
import time
import threading
import urllib.error
import urllib.request
import urllib.parse
from typing import Any

import service_settings as store

DOCS = {"key": "https://www.zotero.org/settings/keys/new",
        "api": "https://www.zotero.org/support/dev/web_api/v3/write_requests",
        "sync": "https://www.zotero.org/support/dev/web_api/v3/syncing"}
KEY = re.compile(r"^[A-Z0-9]{8}$")


def ensure(db: Any) -> None:
    db.db.executescript("""
    CREATE TABLE IF NOT EXISTS zotero_bindings (
      task_id TEXT PRIMARY KEY, library TEXT NOT NULL, collection TEXT NOT NULL,
      last_sync TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS zotero_item_links (
      task_id TEXT NOT NULL, result_id TEXT NOT NULL, item_key TEXT NOT NULL,
      remote_version INTEGER NOT NULL DEFAULT 0, local_hash TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(task_id,result_id));
    """)
    db.db.commit()


class LocalConfig(dict):
    def __init__(self, data, db):
        super().__init__(data)
        self.db = db


class Page(list):
    def __init__(self, rows=(), version=0):
        super().__init__(rows)
        self.version = int(version or 0)


class RetryLater(ValueError):
    def __init__(self, message, seconds=30):
        super().__init__(message)
        self.seconds = max(5, min(int(seconds or 30), 3600))


def config(db: Any) -> dict:
    import zotero
    saved = store.read(db, "zotero_web")
    transport = saved.get("transport") or ("web" if saved.get("library_id") else "local")
    if transport == "local":
        profile = hashlib.sha256(str(zotero.find_zotero_data_dir().resolve()).casefold().encode()).hexdigest()[:20]
        return LocalConfig({"transport": "local", "local_profile": profile, "base_url": zotero._base()}, db)
    return {**saved, "transport": "web"}


def library(data: dict) -> str:
    if data.get("transport") == "local":
        return "local:" + data["local_profile"] + "/users/0"
    kind = data.get("library_type") or "users"
    ident = str(data.get("library_id") or "")
    if kind not in {"users", "groups"} or not ident.isdigit():
        raise ValueError("请填写 Zotero 用户/群组的数字 Library ID")
    return f"{kind}/{ident}"


def request(data: dict, path: str, method: str = "GET", payload: Any = None) -> Any:
    if data.get("transport") == "local":
        import zotero
        if method != "GET":
            return zotero._write(data.db, "/users/0" + path, payload, method)
        code, headers, body = zotero._get(data["base_url"] + "/api/users/0" + path)
        if code == 429:
            raise RetryLater("Zotero 本机请求过于频繁", headers.get("retry-after", 30))
        if code != 200:
            raise ValueError(f"Zotero 本机读取失败 HTTP {code}")
        return Page(body, headers.get("last-modified-version", 0)) if isinstance(body, list) else body
    if not data.get("api_key"):
        raise ValueError("请配置具备所需读写权限的 Zotero API Key")
    base = "https://api.zotero.org/" + library(data)
    headers = {"Zotero-API-Key": data["api_key"], "Zotero-API-Version": "3", "Content-Type": "application/json"}
    raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    if method == "POST" and isinstance(payload, list) and not any(it.get("key") for it in payload):
        headers["Zotero-Write-Token"] = hashlib.sha256(raw).hexdigest()[:32]
    req = urllib.request.Request(base + path, data=raw, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw_result = response.read()
            result = json.loads(raw_result) if raw_result else {}
            return Page(result, response.headers.get("Last-Modified-Version", 0)) if isinstance(result, list) else result
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 503):
            raise RetryLater("Zotero 暂时限流，请稍后重试", exc.headers.get("Retry-After", 30)) from exc
        hints = {403: "Key 缺少写入/群组权限", 409: "写入冲突，请重新预览", 412: "远端版本已变更，请重新预览",
                 429: "请求过于频繁，请稍后重试"}
        raise ValueError(f"Zotero HTTP {exc.code}：{hints.get(exc.code, '检查 Library ID、Key 和条目字段')}") from exc


def all_pages(data: dict, path: str) -> list[dict]:
    rows = Page()
    for start in range(0, 10000, 100):
        page = request(data, path + ("&" if "?" in path else "?") + f"format=json&limit=100&start={start}")
        if not isinstance(page, list):
            raise ValueError("Zotero 返回了无效列表")
        version = getattr(page, "version", 0)
        if rows.version and version and rows.version != version:
            raise RetryLater("分页期间 Zotero 已变化，稍后重新读取", 5)
        rows.version = version
        rows.extend(page)
        if len(page) < 100:
            return rows
    raise ValueError("集合超过 10000 条，未进行不完整同步；请分集合处理")


def library_items(db, data):
    """Cache complete Zotero objects; subsequent reads request changed versions."""
    lib = library(data)
    state = store.read(db, "zotero_library_version", lib)
    version = int(state.get("version") or 0)
    rows = all_pages(data, "/items/top" + (f"?since={version}" if version else ""))
    current = getattr(rows, "version", 0)
    # Local versions may reset after restoring a Zotero backup.
    if version and current and current < version:
        rows = all_pages(data, "/items/top")
        version, current = 0, getattr(rows, "version", 0)
    deleted = []
    if version and current > version:
        if data.get("transport") == "local":
            versions = request(data, "/items/top?format=versions")
            deleted = [r["item_key"] for r in db.db.execute("SELECT item_key FROM zotero_library_items WHERE library=?", (lib,)) if r["item_key"] not in versions]
        else:
            deleted = request(data, f"/deleted?since={version}").get("items", [])
    if not version:
        db.db.execute("DELETE FROM zotero_library_items WHERE library=?", (lib,))
    for key in deleted:
        db.db.execute("DELETE FROM zotero_library_items WHERE library=? AND item_key=?", (lib, key))
    for row in rows:
        cache_item(db, lib, row, commit=False)
    db.db.commit()
    if current:
        store.write(db, "zotero_library_version", {"version": current}, lib)
    return [json.loads(r["data"]) for r in db.db.execute("SELECT data FROM zotero_library_items WHERE library=?", (lib,))]


def cache_item(db, lib, row, commit=True):
    db.db.execute("INSERT OR REPLACE INTO zotero_library_items VALUES (?,?,?,?,?)",
        (lib, row["key"], row.get("version", 0), json.dumps(row, ensure_ascii=False), str(time.time())))
    if commit:
        db.db.commit()


def project_tag(task_id):
    return "rw:project:" + task_id


def _key(value: str) -> str:
    if not KEY.fullmatch(str(value)):
        raise ValueError("Zotero Key 应为 8 位大写字母或数字")
    return str(value)


def _saved(response: dict) -> dict:
    if response.get("failed"):
        raise ValueError("Zotero 拒绝条目：" + json.dumps(response["failed"], ensure_ascii=False)[:600])
    successful = response.get("successful") or {}
    if successful:
        return next(iter(successful.values()))
    unchanged = response.get("unchanged") or {}
    if unchanged:
        return {"key": next(iter(unchanged.values()))}
    raise ValueError("Zotero 未确认任何写入，不能标记成功")


def write_item(db: Any, item: dict) -> dict:
    if not isinstance(item, dict) or not item.get("itemType"):
        raise ValueError("条目必须包含 itemType")
    if item.get("key"):
        _key(item["key"])
        if not isinstance(item.get("version"), int):
            raise ValueError("修改现有条目必须提供当前 version，防止覆盖其他修改")
    return {"ok": True, "item": _saved(request(config(db), "/items", "POST", [item]))}


def binding(db: Any, task_id: str) -> dict:
    ensure(db)
    row = db.db.execute("SELECT * FROM zotero_bindings WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return {}
    return {**dict(row), 'sources': [dict(r) for r in db.db.execute(
        'SELECT collection,mode FROM zotero_project_sources WHERE task_id=? AND library=? ORDER BY collection',
        (task_id, row['library']))]}


def bind(db: Any, task_id: str, collection: str, create: bool = False) -> dict:
    ensure(db)
    task = db.db.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise ValueError("项目不存在")
    data = config(db)
    lib = library(data)
    if create:
        row = _saved(request(data, "/collections", "POST", [{"name": task["title"], "parentCollection": False}]))
        collection = row["key"]
    else:
        _key(collection)
        request(data, "/collections/" + collection)
    import zotero_sources
    return zotero_sources.configure(db, task_id, [{'collection': collection, 'mode': 'all'}], collection)


def _local(item: dict) -> dict:
    return {k: str(item.get(k) or "") for k in ("title", "authors", "year", "doi", "url", "abstract")}


def _hash(item: dict) -> str:
    return hashlib.sha256(json.dumps(_local(item), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _from_remote(item: dict) -> dict:
    data = item.get("data") or item
    creators = [c.get("name") or " ".join(filter(None, (c.get("firstName"), c.get("lastName"))))
                for c in data.get("creators", []) if c.get("creatorType", "author") == "author"]
    return {"source": "zotero", "title": data.get("title") or "", "authors": "; ".join(creators),
            "year": (re.search(r"\d{4}", data.get("date") or "") or [""])[0],
            "doi": data.get("DOI") or "", "url": data.get("url") or "", "abstract": data.get("abstractNote") or ""}


def _to_remote(local: dict, collection: str, existing: dict | None = None, task_id="") -> dict:
    data = dict((existing or {}).get("data") or {})
    original = _from_remote(existing) if existing else {}
    data.setdefault("itemType", "journalArticle")
    for local_key, remote_key in (("title", "title"), ("doi", "DOI"), ("url", "url"), ("abstract", "abstractNote"), ("year", "date")):
        if not existing or str(local.get(local_key) or "") != original.get(local_key, ""):
            data[remote_key] = str(local.get(local_key) or "")
    if not existing or str(local.get("authors") or "") != original.get("authors", ""):
        previous = {c.get("name") or " ".join(filter(None, (c.get("firstName"), c.get("lastName")))): c
                    for c in data.get("creators", []) if c.get("creatorType", "author") == "author"}
        authors = [n.strip() for n in re.split(r"[;；]", local.get("authors") or "") if n.strip()]
        data["creators"] = [previous.get(n, {"creatorType": "author", "name": n}) for n in authors] + [
            c for c in data.get("creators", []) if c.get("creatorType", "author") != "author"]
    if not existing:
        data['collections'] = [collection] if collection else []
    if task_id:
        tag = project_tag(task_id)
        data["tags"] = [*data.get("tags", [])]
        if not any(t.get("tag") == tag for t in data["tags"]):
            data["tags"].append({"tag": tag})
    if existing:
        data.update(key=existing["key"], version=existing["version"])
    return data


_sync_lock = threading.RLock()


def sync(db, task_id, mode="merge", dry_run=True, plan_token="", skip_conflicts=False):
    with _sync_lock:
        return _sync(db, task_id, mode, dry_run, plan_token, skip_conflicts)


def _sync(db: Any, task_id: str, mode: str = "merge", dry_run: bool = True, plan_token: str = "", skip_conflicts=False) -> dict:
    """Merge metadata and membership; never deletes local papers or remote items."""
    if mode not in {"pull", "push", "merge"}:
        raise ValueError("同步方式应为 pull / push / merge")
    bound = binding(db, task_id)
    data = config(db)
    if not bound or bound["library"] != library(data):
        raise ValueError("请先保存当前项目的 Zotero 来源")
    collection = bound['collection']
    all_remote = [r for r in library_items(db, data)
              if (r.get("data") or {}).get("itemType") not in {"note", "attachment", "annotation"}]
    following = {s['collection'] for s in bound['sources'] if s['mode'] == 'all'}
    selected = {r['item_key'] for r in db.db.execute(
        "SELECT item_key FROM zotero_project_memberships WHERE task_id=? AND library=? AND status IN ('active','selected')",
        (task_id, bound['library']))}
    selected.update(r['item_key'] for r in db.db.execute('SELECT item_key FROM zotero_item_links WHERE task_id=?', (task_id,)))
    remote = [r for r in all_remote if r['key'] in selected
              or following.intersection(r.get('data', {}).get('collections', []))
              or any(t.get('tag') == project_tag(task_id) for t in r.get('data', {}).get('tags', []))]
    by_key = {r["key"]: r for r in all_remote}
    import project_library
    local = project_library.records(db, task_id)
    links = {r["result_id"]: dict(r) for r in db.db.execute("SELECT * FROM zotero_item_links WHERE task_id=?", (task_id,))}
    excluded = {r["item_key"] for r in db.db.execute("SELECT item_key FROM zotero_project_memberships WHERE task_id=? AND library=? AND status IN ('excluded','removing')", (task_id, bound["library"]))}
    links = {rid: row for rid, row in links.items() if row['item_key'] not in excluded}
    remote = [r for r in remote if r["key"] not in excluded]
    actions, conflicts, used = [], [], set()
    mapped_keys = {r['item_key'] for r in links.values()}
    for missing in sorted(selected - excluded - by_key.keys() - mapped_keys):
        conflicts.append({'result_id': '', 'key': missing, 'title': missing,
                          'reason': '已选 Zotero 条目不存在，请解除当前项目关联或检查文献库'})
    matched = {}
    for row in local:
        link = next((links[rid] for rid in row["result_ids"] if rid in links), None)
        existing = by_key.get(link["item_key"]) if link else None
        if link and not existing:
            conflicts.append({"result_id": row["id"], "key": link["item_key"], "title": row["title"],
                "reason": "已关联 Zotero 条目不存在，请解除当前项目关联或检查文献库"})
            continue
        if not existing:
            doi = project_library.identity(row) if row.get("doi") else ""
            pool = remote if mode == 'pull' else [r for r in all_remote if r['key'] not in excluded]
            candidates = [r for r in pool if doi and doi == project_library.identity(_from_remote(r))]
            if not candidates:
                candidates = [r for r in pool
                              if row["title"].strip().casefold() == _from_remote(r)["title"].strip().casefold()
                              and not (doi and _from_remote(r)["doi"].strip())]
            chosen = [r for r in candidates if r['key'] in selected]
            if len(chosen) == 1:
                candidates = chosen
            if len(candidates) > 1:
                conflicts.append({"result_id": row["id"], "key": "", "title": row["title"],
                                  "reason": "匹配到多个文献，请先明确条目映射再同步",
                                  "candidates": [{'key': r['key'], 'title': _from_remote(r)['title'], 'doi': _from_remote(r)['doi']} for r in candidates]})
                used.update(r["key"] for r in candidates)
                continue
            existing = candidates[0] if candidates else None
        if existing:
            used.add(existing["key"])
            matched.setdefault(existing["key"], []).append(row)
            lc = bool(link and link["local_hash"] != _hash(row))
            rc = bool(link and link['local_hash'] != _hash(_from_remote(existing)))
            if lc and rc and _local(row) != _local(_from_remote(existing)):
                conflicts.append({"result_id": row["id"], "key": existing["key"], "title": row["title"],
                                  "reason": "本地与远端均有变更，保留两端原文；先手动解决再同步"})
                continue
            if mode == "push" or (mode == "merge" and lc and not rc):
                payload = _to_remote(row, collection, existing, task_id)
                same = payload == existing.get("data", {})
                actions.append({"kind": "link" if same else "push", "result_id": row["id"], "key": existing["key"],
                                "version": existing["version"], "payload": payload})
            else:
                actions.append({"kind": "pull", "result_id": row["id"], "key": existing["key"],
                                "version": existing["version"], "payload": _from_remote(existing),
                                "membership": _to_remote(_from_remote(existing), collection, existing, task_id) if mode == "merge" else None})
        elif mode in {"push", "merge"}:
            if link:
                conflicts.append({"result_id": row["id"], "key": link["item_key"], "title": row["title"],
                                  "reason": "已映射条目不存在；不会复活远端删除"})
            else:
                actions.append({"kind": "push", "result_id": row["id"], "key": "", "version": 0,
                                "payload": _to_remote(row, collection, task_id=task_id)})
    ambiguous = {key for key, rows in matched.items() if len(rows) > 1}
    if ambiguous:
        actions = [a for a in actions if a["key"] not in ambiguous]
        for key in sorted(ambiguous):
            for row in matched[key]:
                conflicts.append({"result_id": row["id"], "key": key, "title": row["title"],
                                  "reason": "多个本地文献匹配同一 Zotero 条目，请先明确条目映射再同步"})
    if mode in {"pull", "merge"}:
        for row in remote:
            if row["key"] not in used:
                actions.append({"kind": "pull", "result_id": "", "key": row["key"], "version": row["version"],
                                "payload": _from_remote(row),
                                "membership": _to_remote(_from_remote(row), collection, row, task_id) if mode == "merge" else None})
    identities = {}
    for action in actions:
        payload = action['payload'] if action['kind'] == 'pull' else _from_remote({'data': action['payload']})
        if payload.get('doi') and action['key']:
            identities.setdefault(project_library.identity(payload), []).append(action)
    duplicates = {a['key'] for rows in identities.values() if len({a['key'] for a in rows}) > 1 for a in rows}
    if duplicates:
        for action in actions:
            if action['key'] in duplicates:
                conflicts.append({'result_id': action['result_id'], 'key': action['key'],
                                  'title': action['payload'].get('title', ''),
                                  'reason': '同一 DOI 对应多个已选 Zotero 条目，请解除多余的项目关联后同步'})
        actions = [a for a in actions if a['key'] not in duplicates]
    token = hashlib.sha256(json.dumps({"binding": bound, "actions": actions, "conflicts": conflicts},
                                      sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    plan = {"task_id": task_id, "mode": mode, "dry_run": dry_run, "actions": actions,
            "conflicts": conflicts, "plan_token": token, "policy": "合并项目成员题录及项目标签；保留已有 Collection 归属，不删除条目，不同步 PDF 附件"}
    if dry_run:
        store.write(db, "zotero_plan", {"token": token, "expires": time.time() + 600}, task_id)
        return plan
    saved_plan = store.read(db, "zotero_plan", task_id)
    if not plan_token or saved_plan.get("token") != plan_token or token != plan_token or saved_plan.get("expires", 0) < time.time():
        raise ValueError("同步预览已过期或数据已变更，请重新预览后确认")
    if conflicts and not skip_conflicts:
        raise ValueError("存在双端修改/成员移除冲突，未执行同步；请先处理冲突")
    applied = []
    expected_hashes = {row["id"]: _hash(row) for row in local}
    for action in actions:
        rid, key, version = action["result_id"], action["key"], action["version"]
        try:
            if rid and _hash(db.result(rid)) != expected_hashes[rid]:
                raise ValueError("执行期间项目文献发生变化，保留本地修改，请重新同步")
            if action["kind"] == "push":
                written = _saved(request(data, "/items", "POST", [action["payload"]]))
                key, version = written["key"], written.get("version", version)
            elif action["kind"] == "pull":
                payload = action["payload"]
                if rid:
                    project_library.update_record(db, task_id, rid, payload)
                else:
                    rid = import_record(db, task_id, key, payload)
                membership = action.get("membership")
                if membership and membership != by_key[key].get("data", {}):
                    written = _saved(request(data, "/items", "POST", [membership]))
                    version = written.get("version", version)
            if rid:
                project_library.index_record(db, task_id, rid)
                db.db.execute("INSERT OR REPLACE INTO zotero_item_links VALUES (?,?,?,?,?)", (task_id, rid, key, version, _hash(db.result(rid))))
                db.db.execute("INSERT OR REPLACE INTO zotero_project_memberships VALUES (?,?,?,?,?,'active','',?)",
                    (task_id, bound["library"], key, rid, project_library.identity(db.result(rid)), str(time.time())))
                db.db.commit()
            applied.append({"kind": action["kind"], "result_id": rid, "key": key})
        except Exception as exc:
            db.db.commit()
            return {"ok": False, "partial": True, "applied": applied, "failed": action, "error": str(exc)}
    stamp = str(int(time.time()))
    db.db.execute("UPDATE zotero_bindings SET last_sync=? WHERE task_id=?", (stamp, task_id))
    db.db.commit()
    store.write(db, "zotero_plan", {}, task_id)
    return {"ok": True, "applied": applied, "last_sync": stamp, "conflicts": conflicts}


def import_record(db, task_id, key, payload):
    """Zotero keys, not titles, identify items without a DOI."""
    import uuid
    import project_library
    mapped = db.db.execute('SELECT result_id FROM zotero_item_links WHERE task_id=? AND item_key=?', (task_id, key)).fetchone()
    if mapped:
        return mapped['result_id']
    doi = project_library.identity(payload) if payload.get('doi') else ''
    for row in project_library.records(db, task_id):
        if doi and project_library.identity(row) == doi:
            return row['id']
    rid = uuid.uuid4().hex
    db.db.execute('INSERT INTO search_cache (id,task_id,source,title,authors,year,doi,url,pdf_url,abstract,score,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (rid, task_id, 'zotero', payload.get('title') or key, payload.get('authors', ''), payload.get('year', ''),
         payload.get('doi', ''), payload.get('url', ''), '', payload.get('abstract', ''), 0, str(time.time())))
    db.db.commit()
    return rid


_auto_lock = threading.Lock()
_workers = set()


def auto_state(db, task_id):
    return {"enabled": False, "mode": "merge", "interval_seconds": 15, "status": "disabled",
            **store.read(db, "zotero_auto", task_id)}


def configure_auto(db, task_id, body):
    if not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
        raise ValueError("请选择有效项目")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    mode = str(body.get("mode", "merge"))
    interval = body.get("interval_seconds", 15)
    if mode not in ("pull", "push", "merge") or type(interval) is not int or not 10 <= interval <= 3600:
        raise ValueError("请选择同步方向及 10 至 3600 秒同步间隔")
    if enabled and not binding(db, task_id):
        raise ValueError("请先保存项目的 Zotero 来源")
    if enabled and config(db).get("transport") != "local" and not config(db).get("api_key"):
        raise ValueError("请先配置 Zotero Web API Key")
    value = {**auto_state(db, task_id), "enabled": enabled, "mode": mode,
             "interval_seconds": interval, "status": "waiting" if enabled else "disabled", "next_at": 0, "error": ""}
    return store.write(db, "zotero_auto", value, task_id)


def auto_tick(db):
    """Poll only explicitly enabled project bindings; conflicts pause writes."""
    ensure(db)
    if not _auto_lock.acquire(blocking=False):
        return 0
    count = 0
    try:
        for row in db.db.execute("SELECT task_id FROM zotero_bindings").fetchall():
            task_id = row["task_id"]
            state = auto_state(db, task_id)
            if not state["enabled"] or state.get("next_at", 0) > time.time():
                continue
            if not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
                continue
            pending = store.read(db, "zotero_plan", task_id)
            if pending.get("expires", 0) > time.time():
                continue
            try:
                plan = sync(db, task_id, state["mode"], True)
                result = sync(db, task_id, state["mode"], False, plan["plan_token"], skip_conflicts=True)
                state.update(status=("conflict" if plan["conflicts"] else "synced") if result.get("ok") else "error",
                             conflicts=plan["conflicts"], error=result.get("error", ""), last_sync=result.get("last_sync", ""))
                count += 1
            except Exception as exc:
                failures = int(state.get("failures") or 0) + 1
                state.update(status="error", error=str(exc)[:1000], failures=failures)
                state["retry_seconds"] = max(getattr(exc, "seconds", 0), min(3600, 15 * 2 ** min(failures, 8)))
            store.write(db, "zotero_plan", {}, task_id)
            # A configuration edited while an HTTP request was active wins.
            latest = auto_state(db, task_id)
            if all(latest.get(k) == state.get(k) for k in ("enabled", "mode", "interval_seconds")):
                if state["status"] != "error":
                    state.update(failures=0, retry_seconds=0)
                state.update(last_attempt=time.time(), next_at=time.time() + max(state["interval_seconds"], state.get("retry_seconds", 0)))
                store.write(db, "zotero_auto", state, task_id)
    finally:
        _auto_lock.release()
    return count


def start_auto(db_path):
    key = str(db_path)
    if key in _workers:
        return
    _workers.add(key)
    def loop():
        import workflow
        while True:
            try:
                with workflow.WorkflowDB(db_path) as db:
                    auto_tick(db)
            except Exception:
                __import__("logging").exception("Zotero auto sync failed")
            time.sleep(5)
    threading.Thread(target=loop, daemon=True, name="zotero-auto-sync").start()


def route(method: str, path: str, body: dict, query: dict, db: Any) -> dict | None:
    if not path.startswith("/api/zotero/web/"):
        return None
    import zotero_links
    result = zotero_links.route(method, path, body, query, db)
    if result is not None:
        return result
    task_id = str(body.get("task_id") or (query.get("task_id") or [""])[0])
    if path == '/api/zotero/web/sources' and method == 'POST':
        import zotero_sources
        return {'binding': zotero_sources.configure(db, task_id, body.get('sources', []), str(body.get('export_collection') or ''))}
    if path == '/api/zotero/web/candidates' and method == 'GET':
        import zotero_sources
        return zotero_sources.candidates(db, task_id)
    if path == '/api/zotero/web/select' and method == 'POST':
        import zotero_sources
        return zotero_sources.select(db, task_id, body.get('keys'))
    if path == "/api/zotero/web/auto":
        return {"auto_sync": configure_auto(db, task_id, body) if method == "POST" else auto_state(db, task_id),
                "binding": binding(db, task_id)}
    if method == "GET" and path == "/api/zotero/web/config":
        import zotero
        return {"config": store.public(config(db)), "binding": binding(db, task_id) if task_id else {}, "docs": DOCS,
                "local": {**zotero.status(), **zotero.local_write_state(db)}}
    if method == "POST" and path == "/api/zotero/web/authorize":
        import zotero
        return zotero.authorize_local_write(db)
    if method == "POST" and path == "/api/zotero/web/config":
        if body.get("transport") == "local":
            store.write(db, "zotero_web", {"transport": "local"})
            return {"config": store.public(config(db)), "docs": DOCS}
        data = store.merge_secrets(config(db), body, {"api_key", "library_type", "library_id", "transport"})
        data["transport"] = "web"
        library(data)
        store.write(db, "zotero_web", data)
        return {"config": store.public(data), "docs": DOCS}
    if method == "GET" and path == "/api/zotero/web/collections":
        return {"collections": all_pages(config(db), "/collections")}
    if method == "POST" and path == "/api/zotero/web/bind":
        if body.get("create") and body.get("confirm") is not True:
            raise ValueError("新建远端集合需要明确确认")
        return {"binding": bind(db, task_id, str(body.get("collection") or ""), body.get("create") is True)}
    if method == "POST" and path == "/api/zotero/web/sync":
        if body.get("dry_run") is False and body.get("confirm") is not True:
            raise ValueError("执行同步需要明确确认")
        return sync(db, task_id, str(body.get("mode") or "merge"), body.get("dry_run") is not False, str(body.get("plan_token") or ""))
    if method == "POST" and path == "/api/zotero/web/write":
        if body.get("confirm") is not True:
            raise ValueError("远端写入需要明确确认")
        return write_item(db, body.get("item") or {})
    return None
