"""Zotero Web API writes and explicit, non-destructive project/collection sync.

Never opens zotero.sqlite. The desktop Local API remains the read-only path.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
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


def config(db: Any) -> dict:
    return store.read(db, "zotero_web")


def library(data: dict) -> str:
    kind = data.get("library_type") or "users"
    ident = str(data.get("library_id") or "")
    if kind not in {"users", "groups"} or not ident.isdigit():
        raise ValueError("请填写 Zotero 用户/群组的数字 Library ID")
    return f"{kind}/{ident}"


def request(data: dict, path: str, method: str = "GET", payload: Any = None) -> Any:
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
            return json.loads(raw_result) if raw_result else {}
    except urllib.error.HTTPError as exc:
        hints = {403: "Key 缺少写入/群组权限", 409: "写入冲突，请重新预览", 412: "远端版本已变更，请重新预览",
                 429: "请求过于频繁，请稍后重试"}
        raise ValueError(f"Zotero HTTP {exc.code}：{hints.get(exc.code, '检查 Library ID、Key 和条目字段')}") from exc


def all_pages(data: dict, path: str) -> list[dict]:
    rows = []
    for start in range(0, 10000, 100):
        page = request(data, path + ("&" if "?" in path else "?") + f"format=json&limit=100&start={start}")
        if not isinstance(page, list):
            raise ValueError("Zotero 返回了无效列表")
        rows.extend(page)
        if len(page) < 100:
            return rows
    raise ValueError("集合超过 10000 条，未进行不完整同步；请分集合处理")


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
    return dict(row) if row else {}


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
    old = binding(db, task_id)
    if old and (old["library"] != lib or old["collection"] != collection):
        db.db.execute("DELETE FROM zotero_item_links WHERE task_id=?", (task_id,))
    db.db.execute("INSERT OR REPLACE INTO zotero_bindings VALUES (?,?,?,?)", (task_id, lib, collection, ""))
    db.db.commit()
    return binding(db, task_id)


def _local(item: dict) -> dict:
    return {k: str(item.get(k) or "") for k in ("title", "authors", "year", "doi", "url", "abstract")}


def _hash(item: dict) -> str:
    return hashlib.sha256(json.dumps(_local(item), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _from_remote(item: dict) -> dict:
    data = item.get("data") or item
    creators = [c.get("name") or " ".join(filter(None, (c.get("firstName"), c.get("lastName")))) for c in data.get("creators", [])]
    return {"source": "zotero", "title": data.get("title") or "", "authors": "; ".join(creators),
            "year": (re.search(r"\d{4}", data.get("date") or "") or [""])[0],
            "doi": data.get("DOI") or "", "url": data.get("url") or "", "abstract": data.get("abstractNote") or ""}


def _to_remote(local: dict, collection: str, existing: dict | None = None) -> dict:
    data = dict((existing or {}).get("data") or {})
    data.update(itemType=data.get("itemType") or "journalArticle", title=local["title"],
                DOI=local.get("doi") or "", date=local.get("year") or "", url=local.get("url") or "",
                abstractNote=local.get("abstract") or "")
    data["creators"] = [{"creatorType": "author", "name": n.strip()} for n in re.split(r"[;；]", local.get("authors") or "") if n.strip()]
    data["collections"] = list(dict.fromkeys([*data.get("collections", []), collection]))
    if existing:
        data.update(key=existing["key"], version=existing["version"])
    return data


def sync(db: Any, task_id: str, mode: str = "merge", dry_run: bool = True, plan_token: str = "") -> dict:
    """Merge metadata and membership; never deletes local papers or remote items."""
    if mode not in {"pull", "push", "merge"}:
        raise ValueError("同步方式应为 pull / push / merge")
    bound = binding(db, task_id)
    data = config(db)
    if not bound or bound["library"] != library(data):
        raise ValueError("请先绑定当前 Zotero Library 下的项目 Collection")
    collection = _key(bound["collection"])
    remote = [r for r in all_pages(data, "/collections/" + collection + "/items/top")
              if (r.get("data") or {}).get("itemType") not in {"note", "attachment", "annotation"}]
    by_key = {r["key"]: r for r in remote}
    local = db.results(task_id)
    links = {r["result_id"]: dict(r) for r in db.db.execute("SELECT * FROM zotero_item_links WHERE task_id=?", (task_id,))}
    actions, conflicts, used = [], [], set()
    for row in local:
        link = links.get(row["id"])
        existing = by_key.get(link["item_key"]) if link else None
        if not existing:
            existing = next((r for r in remote if (row.get("doi") and row["doi"].lower() == _from_remote(r)["doi"].lower())
                             or row["title"].strip().casefold() == _from_remote(r)["title"].strip().casefold()), None)
        if existing:
            used.add(existing["key"])
            lc = bool(link and link["local_hash"] != _hash(row))
            rc = bool(link and link["remote_version"] != existing["version"])
            if lc and rc and _local(row) != _local(_from_remote(existing)):
                conflicts.append({"result_id": row["id"], "key": existing["key"], "title": row["title"],
                                  "reason": "本地与远端均有变更，保留两端原文；先手动解决再同步"})
                continue
            if mode == "push" or (mode == "merge" and lc and not rc):
                payload = _to_remote(row, collection, existing)
                same = _local(row) == _local(_from_remote(existing))
                actions.append({"kind": "link" if same else "push", "result_id": row["id"], "key": existing["key"],
                                "version": existing["version"], "payload": payload})
            else:
                actions.append({"kind": "pull", "result_id": row["id"], "key": existing["key"],
                                "version": existing["version"], "payload": _from_remote(existing)})
        elif mode in {"push", "merge"}:
            if link:
                conflicts.append({"result_id": row["id"], "key": link["item_key"], "title": row["title"],
                                  "reason": "已映射条目不再属于此集合；不会擅自重新添加或复活远端删除"})
            else:
                actions.append({"kind": "push", "result_id": row["id"], "key": "", "version": 0,
                                "payload": _to_remote(row, collection)})
    if mode in {"pull", "merge"}:
        for row in remote:
            if row["key"] not in used:
                actions.append({"kind": "pull", "result_id": "", "key": row["key"], "version": row["version"],
                                "payload": _from_remote(row)})
    token = hashlib.sha256(json.dumps({"binding": bound, "actions": actions, "conflicts": conflicts},
                                      sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    plan = {"task_id": task_id, "mode": mode, "dry_run": dry_run, "actions": actions,
            "conflicts": conflicts, "plan_token": token, "policy": "只合并元数据和集合成员，不删除任一端条目，不同步PDF附件，不自动重命名集合"}
    if dry_run:
        store.write(db, "zotero_plan", {"token": token, "expires": time.time() + 600}, task_id)
        return plan
    saved_plan = store.read(db, "zotero_plan", task_id)
    if not plan_token or saved_plan.get("token") != plan_token or token != plan_token or saved_plan.get("expires", 0) < time.time():
        raise ValueError("同步预览已过期或数据已变更，请重新预览后确认")
    if conflicts:
        raise ValueError("存在双端修改/成员移除冲突，未执行同步；请先处理冲突")
    applied = []
    for action in actions:
        rid, key, version = action["result_id"], action["key"], action["version"]
        try:
            if action["kind"] == "push":
                written = _saved(request(data, "/items", "POST", [action["payload"]]))
                key, version = written["key"], written.get("version", version)
            elif action["kind"] == "pull":
                payload = action["payload"]
                if rid:
                    db.db.execute("UPDATE search_cache SET title=?,authors=?,year=?,doi=?,url=?,abstract=? WHERE id=? AND task_id=?",
                                  tuple(payload[k] for k in ("title", "authors", "year", "doi", "url", "abstract")) + (rid, task_id))
                else:
                    created = db.cache_results(task_id, [payload])
                    rid = created[0]["id"] if created else next((r["id"] for r in db.results(task_id) if r["title"] == payload["title"]), "")
            if rid:
                db.db.execute("INSERT OR REPLACE INTO zotero_item_links VALUES (?,?,?,?,?)", (task_id, rid, key, version, _hash(db.result(rid))))
                db.db.commit()
            applied.append({"kind": action["kind"], "result_id": rid, "key": key})
        except Exception as exc:
            db.db.commit()
            return {"ok": False, "partial": True, "applied": applied, "failed": action, "error": str(exc)}
    stamp = str(int(time.time()))
    db.db.execute("UPDATE zotero_bindings SET last_sync=? WHERE task_id=?", (stamp, task_id))
    db.db.commit()
    store.write(db, "zotero_plan", {}, task_id)
    return {"ok": True, "applied": applied, "last_sync": stamp}


def route(method: str, path: str, body: dict, query: dict, db: Any) -> dict | None:
    if not path.startswith("/api/zotero/web/"):
        return None
    task_id = str(body.get("task_id") or (query.get("task_id") or [""])[0])
    if method == "GET" and path == "/api/zotero/web/config":
        return {"config": store.public(config(db)), "binding": binding(db, task_id) if task_id else {}, "docs": DOCS}
    if method == "POST" and path == "/api/zotero/web/config":
        data = store.merge_secrets(config(db), body, {"api_key", "library_type", "library_id"})
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
