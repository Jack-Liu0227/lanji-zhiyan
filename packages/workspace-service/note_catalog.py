"""Project-owned note taxonomy and portable Markdown metadata."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

import yaml

DEFAULTS = (
    ("planning", "研究规划", "选题、目标、技术路线与讨论决策", ["选题", "创新点", "组会纪要"]),
    ("literature", "文献研究", "检索、阅读、证据与文献综合", ["精读", "检索策略", "文献矩阵", "综述"]),
    ("experiment_analysis", "实验分析", "实验、数据、复现与结果分析", ["实验方案", "实验日志", "代码复现", "图表图注"]),
    ("writing_submission", "写作投稿", "写作、审稿、投稿与发表归档", ["大纲", "润色", "审稿", "返修", "成果归档"]),
    ("prompt_templates", "提示词模板", "供 Agent 引用的可复用任务指令", ["检索", "精读", "写作", "审稿", "返修"]),
)
LEGACY = {
    "研究计划": ("planning", ["研究计划"]), "检索策略": ("literature", ["检索策略"]),
    "文献矩阵": ("literature", ["文献矩阵"]), "文献精读笔记": ("literature", ["精读"]),
    "文献综述": ("literature", ["综述"]), "每日推送": ("literature", ["每日推送"]),
    "实验方案": ("experiment_analysis", ["实验方案"]), "数据记录": ("experiment_analysis", ["数据记录"]),
    "分析结果": ("experiment_analysis", ["分析结果"]), "写作投稿": ("writing_submission", []),
}
FIELDS = {"category_id", "tags", "note_type", "template_id", "variables", "template_status", "description"}
FRONT = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)


def split(content):
    match = FRONT.match(content)
    if not match:
        return {}, content
    try:
        data = yaml.safe_load(match[1]) or {}
    except yaml.YAMLError as exc:
        raise ValueError("笔记头部 YAML 无效，请修正后保存") from exc
    if not isinstance(data, dict):
        raise ValueError("笔记头部必须是键值对象")
    return data, content[match.end():]


def combine(data, body):
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip() + "\n---\n" + body if data else body


def tags(values):
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("标签须为最多 100 项的列表")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("标签须为文本")
        value = value.strip().lstrip("#").strip()
        if len(value) > 60 or any(ord(c) < 32 for c in value):
            raise ValueError("标签须为 1 至 60 字符的单行文本")
        if value and value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return result


def defaults():
    return {"version": 1, "categories": [
        {"id": key, "parent_id": "", "name": name, "description": desc, "sort_order": i,
         "preset_key": key, "folder": "笔记/" + name, "suggested_tags": suggested}
        for i, (key, name, desc, suggested) in enumerate(DEFAULTS)], "legacy_targets": {k: v[0] for k, v in LEGACY.items()}}


def config_path(db, project_id, writable=False):
    import project_notes
    root = project_notes.project_root(db, project_id, writable=writable)
    path = root / "笔记" / "分类配置.json"
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("分类配置不能越出项目或使用符号链接")
    return path


def validate(data):
    categories = data.get("categories")
    if not isinstance(categories, list) or len(categories) > 500:
        raise ValueError("分类列表无效（最多 500 项）")
    by_id = {}
    names = set()
    for c in categories:
        if not isinstance(c, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(c.get("id", ""))):
            raise ValueError("分类 ID 无效")
        name = str(c.get("name", "")).strip()
        parent = str(c.get("parent_id") or "")
        if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            raise ValueError("分类名称须为 1 至 80 字符")
        if c["id"] in by_id or (parent, name.casefold()) in names:
            raise ValueError("同级分类名称或 ID 重复")
        folder = str(c.get("folder") or "")
        parts = folder.replace("\\", "/").split("/")
        if not parts or parts[0] != "笔记" or len(parts) < 2 or any(not p or p.startswith(".") or ":" in p for p in parts):
            raise ValueError("分类目录无效")
        c.update(name=name, parent_id=parent, sort_order=int(c.get("sort_order") or 0))
        names.add((parent, name.casefold()))
        by_id[c["id"]] = c
    for c in categories:
        parent = c["parent_id"]
        if parent and (parent not in by_id or parent == c["id"] or by_id[parent]["parent_id"]):
            raise ValueError("分类只支持两级，父分类须为本项目一级分类")
    return data


def load(db, project_id):
    path = config_path(db, project_id)
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        data = validate(json.loads(raw))
        digest = hashlib.sha256(raw.encode()).hexdigest()
    else:
        data = defaults()
        digest = ""
    return {**data, "content_hash": digest}


def save(db, project_id, data, expected_hash=None):
    import asset_service
    import fileguard
    path = config_path(db, project_id, True)
    current = load(db, project_id)
    if expected_hash is not None and expected_hash != current["content_hash"]:
        raise ValueError("分类已被其他操作修改，请刷新后重试")
    data = validate(copy.deepcopy({k: v for k, v in data.items() if k != "content_hash"}))
    fileguard.before_write(path, "保存笔记分类")
    asset_service.atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    return load(db, project_id)


def initialize(db, project_id, *, new_project=False):
    data = load(db, project_id)
    if not config_path(db, project_id).exists():
        if new_project:
            import service_settings
            data = copy.deepcopy(service_settings.read(db, "note_catalog_defaults").get("catalog") or data)
        data = save(db, project_id, data, "")
    return data


def category(data, key):
    return next((c for c in data["categories"] if c["id"] == key), None)


def descendants(data, key):
    return {key, *(c["id"] for c in data["categories"] if c.get("parent_id") == key)}


def attributes(content, relative="", catalog=None):
    meta, _ = split(content)
    catalog = catalog or defaults()
    parts = relative.replace("\\", "/").split("/")
    old = LEGACY.get(parts[1] if len(parts) > 1 else "", ("", []))
    fallback = catalog.get("legacy_targets", {}).get(parts[1] if len(parts) > 1 else "", old[0])
    if "category_id" in meta:
        cid = str(meta["category_id"] or "")
    else:
        match = next((c for c in sorted(catalog["categories"], key=lambda c: len(c["folder"]), reverse=True)
                      if relative.startswith(c["folder"] + "/")), None)
        cid = match["id"] if match else fallback
    valid = category(catalog, cid)
    return {"category_id": cid if valid else "", "tags": tags(meta.get("tags", old[1])),
            "note_type": "prompt_template" if meta.get("note_type") == "prompt_template" else "note",
            "template_id": str(meta.get("template_id") or ""),
            "template_status": str(meta.get("template_status") or "active"),
            "category_missing": bool(cid and not valid)}


def update_content(content, previous="", changes=None, relative="", catalog=None):
    meta, body = split(content)
    old, _ = split(previous)
    for key in FIELDS:
        if key not in meta and key in old:
            meta[key] = old[key]
    if changes is not None:
        if not isinstance(changes, dict) or set(changes) - FIELDS:
            raise ValueError("笔记元数据字段无效")
        inferred = attributes(combine(meta, body), relative, catalog)
        meta.setdefault("category_id", inferred["category_id"])
        meta.setdefault("tags", inferred["tags"])
        meta.update(changes)
        cid = meta.get("category_id")
        if cid and not category(catalog or defaults(), cid):
            raise ValueError("分类不存在于当前项目")
    if "tags" in meta:
        meta["tags"] = tags(meta["tags"])
    if meta.get("note_type", "note") not in ("note", "prompt_template"):
        raise ValueError("不支持的笔记类型")
    return combine(meta, body)


def is_template(content):
    try:
        return split(content)[0].get("note_type") == "prompt_template"
    except ValueError:
        return bool(re.search(r"note_type\s*:\s*['\"]?prompt_template", content[:65536]))


def mutate(db, project_id, body):
    import project_notes
    with_lock = project_notes.synchronized(_mutate)
    return with_lock(db, project_id, body)


def _mutate(db, project_id, body):
    config_path(db, project_id, True)
    data = load(db, project_id)
    expected = body.get("expected_hash")
    if expected is not None and expected != data["content_hash"]:
        raise ValueError("分类已被其他操作修改，请刷新后重试")
    action = body.get("action", "save")
    if action == "set_default":
        import service_settings
        data = save(db, project_id, data, data["content_hash"])
        service_settings.write(db, "note_catalog_defaults", {"catalog": {k: v for k, v in data.items() if k != "content_hash"}})
        return data
    key = str(body.get("id") or "")
    hit = category(data, key)
    if action == "reorder":
        if not hit or body.get("direction", "up") not in ("up", "down"):
            raise ValueError("请选择分类和有效排序方向")
        siblings = sorted((c for c in data["categories"] if c["parent_id"] == hit["parent_id"]),
                          key=lambda c: (c["sort_order"], c["name"]))
        index = siblings.index(hit)
        target = index + (-1 if body.get("direction", "up") == "up" else 1)
        if 0 <= target < len(siblings):
            siblings[index], siblings[target] = siblings[target], siblings[index]
        for order, item in enumerate(siblings):
            item["sort_order"] = order
    elif action == "delete":
        if not hit:
            raise ValueError("分类不存在")
        targets = descendants(data, key)
        destination = str(body.get("destination_id") or "")
        if destination and (not category(data, destination) or destination in targets):
            raise ValueError("请选择其他有效的目标分类")
        import project_notes
        affected = [n for n in project_notes.dashboard(db, project_id)["items"] if n["category_id"] in targets]
        if affected and not destination:
            raise ValueError("分类包含笔记，请选择迁移目标；不会删除笔记")
        for item in affected:
            note = project_notes.read(db, project_id, item["path"])
            project_notes.write(db, project_id, item["path"], note["content"],
                                metadata={"category_id": destination}, expected_hash=note["content_hash"])
        data["categories"] = [c for c in data["categories"] if c["id"] not in targets]
        data["legacy_targets"] = {k: destination if v in targets else v for k, v in data.get("legacy_targets", {}).items()}
    elif action == "save":
        parent = str(body.get("parent_id") or "")
        name = str(body.get("name") or "").strip()
        if key and not hit:
            raise ValueError("分类不存在")
        if hit:
            hit.update(name=name, parent_id=parent, description=str(body.get("description") or ""),
                       sort_order=int(body.get("sort_order", hit["sort_order"])))
        else:
            safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ") or "分类"
            prefix = category(data, parent)["folder"] if category(data, parent) else "笔记"
            folder = prefix + "/" + safe
            if any(c["folder"] == folder for c in data["categories"]):
                folder += "-" + uuid.uuid4().hex[:6]
            data["categories"].append({"id": "cat_" + uuid.uuid4().hex, "parent_id": parent, "name": name,
                "description": str(body.get("description") or ""), "sort_order": int(body.get("sort_order", len(data["categories"]))),
                "preset_key": "", "folder": folder, "suggested_tags": []})
    else:
        raise ValueError("不支持的分类操作")
    return save(db, project_id, data, data["content_hash"])
