"""Reusable project task instructions with immutable invocation snapshots."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import asset_service
import note_catalog
import project_notes

TOKEN = re.compile(r"\{\{\s*([\w\u4e00-\u9fff][\w.\u4e00-\u9fff]{0,79})\s*\}\}")
_HEX_STEM = re.compile(r"^[0-9a-f]{32}$", re.I)


def safe_filename(title: str) -> str:
    raw = re.sub(r'\.(?:md|markdown)$', '', str(title or '').strip(), flags=re.I)
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', raw).strip(' .')
    clean = re.sub(r'-{2,}', '-', clean).strip('-')
    if not clean or clean in ('.', '..'):
        clean = '提示词模板'
    return clean[:80].rstrip(' .') or '提示词模板'


def unique_path(db, project_id: str, folder: str, title: str, exclude_path: str = '') -> str:
    root = project_notes.project_root(db, project_id)
    base_folder = str(folder or '笔记').strip('/')
    stem = safe_filename(title)
    candidate = f"{base_folder}/{stem}.md"
    if candidate == exclude_path or not (root / candidate).exists():
        return candidate
    for idx in range(2, 1000):
        candidate = f"{base_folder}/{stem}-{idx}.md"
        if candidate == exclude_path or not (root / candidate).exists():
            return candidate
    return f"{base_folder}/{stem}-{uuid.uuid4().hex[:6]}.md"


def _sync_template_filename(db, project_id: str, relative: str, title: str) -> str:
    if not relative or not title:
        return relative
    folder = relative.rsplit('/', 1)[0] if '/' in relative else '笔记'
    current_stem = Path(relative).stem
    desired_stem = safe_filename(title)
    if not desired_stem or _HEX_STEM.match(desired_stem) or current_stem == desired_stem or current_stem.startswith(desired_stem + '-'):
        return relative
    target_rel = unique_path(db, project_id, folder, title, exclude_path=relative)
    if target_rel == relative:
        return relative
    target_name = Path(target_rel).name
    try:
        renamed = project_notes.rename(db, project_id, relative, target_name)
        return renamed.get('new_path') or relative
    except Exception:
        return relative


def _normalize_legacy_filenames(db, project_id: str) -> None:
    if getattr(db, '_normalizing_templates', False):
        return
    try:
        project_notes.project_root(db, project_id, writable=True)
    except ValueError:
        return
    db._normalizing_templates = True
    try:
        for item in project_notes.dashboard(db, project_id).get('items', []):
            if item.get('note_type') != 'prompt_template':
                continue
            rel = str(item.get('path') or '')
            if not rel or not _HEX_STEM.match(Path(rel).stem):
                continue
            title = str(item.get('title') or '').strip()
            if not title or title == item.get('name') or _HEX_STEM.match(Path(title).stem):
                try:
                    content = project_notes.read(db, project_id, rel).get('content', '')
                    meta, _ = note_catalog.split(content)
                    title = str(meta.get('title') or meta.get('name') or '').strip()
                except Exception:
                    title = ''
            if title and not _HEX_STEM.match(Path(title).stem):
                _sync_template_filename(db, project_id, rel, title)
    except Exception:
        pass
    finally:
        db._normalizing_templates = False


_DEFAULT_VERSION = 2
_DEFAULT_TEMPLATES = (
    ('图片图注与润色', ['内置', '论文写作', '科研绘图'], '请分析本轮附带的科研图片，逐图生成可用于论文的 caption。说明各子图、坐标、符号、缩写和可见趋势；无法从图中确认的实验条件、样本量、统计方法等列为待补充信息，不编造结果。若提供了原图注，先核对与图片的一致性，再润色并列出实质修改。按图片顺序输出图号、英文图注、中文说明及待确认项。没有收到图片时请先提示提供图片。'),
    ('学术文本润色', ['内置', '论文写作'], '请润色本轮提供的学术文本，保持原语言、事实、数字、公式、引用与结论强度。修正语法和术语，改善句子衔接；不得补造文献或实验结果。输出润色后的正文，以及需要作者确认的含义变化。'),
    ('文献精读结构化笔记', ['内置', '文献精读'], '请基于本轮提供的论文全文或摘录生成结构化精读笔记：\n1. 核心研究问题与待解决痛点；\n2. 方法路线、关键模块/实验设计与核心参数；\n3. 关键定量结果与对照基线；\n4. 实验-结论证据链与适用边界（仅写原文可证内容，标注页码/图表依据）；\n5. 作者自述局限性与潜在待检验问题。'),
    ('审稿意见逐点回复', ['内置', '返修投稿'], '请根据本轮提供的审稿人意见（Reviewer Comments）与原文/补充实验材料起草逐点回复信（Response Letter）：\n- 先逐条提炼审稿人核心关切与隐含要求；\n- 每条按「礼貌致谢与回应态度 -> 依据证据的具体回答 -> 拟修订位置与修改前后对比」组织；\n- 若需补充数据、统计检验或作者确认，明确标出 `[待补充/待确认]`，严禁编造结果。'),
    ('中英文摘要对照与优化', ['内置', '论文写作'], '请将本轮提供的摘要优化为高水准学术表达，输出中英文对照版本：\n1. 控制篇幅并突出「背景痛点—核心方法—关键发现/定量指标—科学意义」四段式信息密度；\n2. 保持结论强度与原文一致，不过度拔高；\n3. 列出关键术语中英译名对照表与句式修改说明。'),
    ('实验设计与统计核查', ['内置', '实验分析'], '请以方法学审稿人视角核查本轮提供的实验方案、实验记录或统计结果：\n1. 检查对照设置、随机化、盲法、重复次数与样本量合理性；\n2. 识别潜在混杂因素、批次效应、伪重复（pseudoreplication）与数据泄漏；\n3. 核对统计检验选择、假设前提及效应量/置信区间报告是否规范，并给出可执行改进清单。'),
    ('引言逻辑与研究空白梳理', ['内置', '论文写作', '文献综述'], '请根据本轮主题与文献证据梳理论文引言（Introduction）写作框架：\n1. 领域重要性与既有主流方案脉络；\n2. 精准界定现有研究空白（Research Gap）及机理/方法层面的瓶颈（附可核验证据锚点，避免机械罗列人名）；\n3. 引出本文目标、核心思路与具体贡献列表（2–4 条）。'),
    ('论文结论与局限性凝练', ['内置', '论文写作'], '请基于本文已获证据起草 Conclusion 与 Limitations 段落：\n1. 凝练可由本文数据直接支撑的核心发现与机制/工程价值；\n2. 明确结论成立边界、实验条件限制与外推风险；\n3. 提出 2–3 个具体且可验证的后续研究方向。'),
)


@project_notes.synchronized
def ensure_defaults(db, project_id):
    import service_settings
    if getattr(db, '_seeding_prompts', False):
        return
    _normalize_legacy_filenames(db, project_id)
    try:
        root = project_notes.project_root(db, project_id, writable=True)
    except ValueError:
        return
    tpl_dir = root / '笔记' / '提示词模板'
    has_files = tpl_dir.is_dir() and any(tpl_dir.glob('*.md'))
    saved_ver = int(service_settings.read(db, 'project_prompt_defaults', project_id).get('version') or 0)
    if saved_ver >= _DEFAULT_VERSION and has_files:
        return
    db._seeding_prompts = True
    context = getattr(db, 'asset_run', None)
    db.asset_run = None
    try:
        existing = {item['title'] for item in search(db, project_id, include_disabled=True)['templates']}
        for title, tags, content in _DEFAULT_TEMPLATES:
            if title not in existing:
                save_template(db, project_id, {'title': title, 'content': content,
                    'metadata': {'tags': tags, 'template_status': 'active'}})
        service_settings.write(db, 'project_prompt_defaults', {'version': _DEFAULT_VERSION}, project_id)
    finally:
        db.asset_run = context
        db._seeding_prompts = False


def definition(content):
    meta, body = note_catalog.split(content)
    names = list(dict.fromkeys(TOKEN.findall(body)))
    variables = meta.get("variables")
    if variables is None:
        variables = {name: {"type": "text", "required": True} for name in names}
    if not isinstance(variables, dict) or len(variables) > 40:
        raise ValueError("模板变量须为最多 40 项的对象")
    for name, spec in variables.items():
        if not isinstance(name, str) or not TOKEN.fullmatch("{{" + name + "}}") or not isinstance(spec, dict):
            raise ValueError("模板变量定义无效")
        if spec.get("type", "text") not in ("text", "number", "enum", "asset_ref"):
            raise ValueError("变量类型须为 text、number、enum 或 asset_ref")
        if spec.get("type") == "enum" and (not isinstance(spec.get("options"), list) or not spec["options"]):
            raise ValueError("枚举变量需要 options 列表")
    if set(names) - set(variables):
        raise ValueError("模板存在未声明变量：" + "、".join(set(names) - set(variables)))
    if meta.get("template_status", "active") not in ("active", "draft", "disabled"):
        raise ValueError("模板状态须为 active、draft 或 disabled")
    return meta, body, variables


def prepare(db, project_id, relative, content, previous=""):
    meta, body = note_catalog.split(content)
    old, _ = note_catalog.split(previous)
    old_id = old.get("template_id")
    if old_id and meta.get("template_id", old_id) != old_id:
        raise ValueError("已有模板 ID 不可更改；请复制为新模板")
    tid = meta.get("template_id") or old_id or "tpl_" + uuid.uuid4().hex
    if not isinstance(tid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tid):
        raise ValueError("模板 ID 无效")
    for item in project_notes.dashboard(db, project_id)["items"]:
        if item.get("template_id") == tid and item["path"] != relative:
            raise ValueError("模板 ID 已存在，请使用复制模板功能")
    meta.update(note_type="prompt_template", template_id=tid)
    result = note_catalog.combine(meta, body)
    if meta.get("template_status", "active") != "draft":
        definition(result)
        if not body.strip():
            raise ValueError("可用模板的正文不能为空")
    return result


def search(db, project_id, query="", tags=None, include_disabled=False):
    ensure_defaults(db, project_id)
    project_notes.project_root(db, project_id)
    wanted = {t.casefold() for t in note_catalog.tags(tags or [])}
    found = []
    for item in project_notes.dashboard(db, project_id, query=query)["items"]:
        if item.get("note_type") != "prompt_template" or not item.get("template_id"):
            continue
        if not include_disabled and item.get("template_status") != "active":
            continue
        if item.get("matches_query", True) and wanted <= {t.casefold() for t in item["tags"]}:
            found.append(item)
    return {"templates": found, "project_id": project_id}


@project_notes.synchronized
def read(db, project_id, template_id, revision_id=""):
    hits = [item for item in search(db, project_id, include_disabled=True)["templates"] if item["template_id"] == template_id]
    if len(hits) != 1:
        raise ValueError("模板不存在或 ID 重复，请在当前项目整理模板")
    item = hits[0]
    note = project_notes.read(db, project_id, item["path"])
    if note["template_status"] != "active":
        raise ValueError("模板处于草稿或停用状态")
    path = project_notes.note_path(db, project_id, item["path"])
    context = getattr(db, "asset_run", None)
    db.asset_run = None
    try:
        asset = asset_service.register_file(db, path, project_id=project_id, kind="note", title=note["title"],
                                            source="prompt_template", body=note["content"])
    finally:
        db.asset_run = context
    rid = revision_id or asset["current_revision_id"]
    revision = db.db.execute("SELECT * FROM asset_revisions WHERE id=? AND asset_id=?", (rid, asset["id"])).fetchone()
    if not revision:
        raise ValueError("模板版本不存在或不属于此模板")
    raw = Path(revision["content_uri"]).read_text(encoding="utf-8")
    meta, body, variables = definition(raw)
    if meta.get("template_id") != template_id:
        raise ValueError("模板版本身份不匹配")
    return {"project_id": project_id, "template_id": template_id, "asset_id": asset["id"],
            "revision_id": rid, "revision": revision["revision"], "content_hash": revision["content_hash"],
            "title": note["title"], "body": body, "variables": variables, "path": item["path"], "tags": item["tags"]}


def _asset_input(db, project_id, value):
    aid = value.get("asset_id") if isinstance(value, dict) else value
    asset = asset_service.get(db, str(aid))
    if not asset or asset["project_id"] != project_id or asset["status"] != "active":
        raise ValueError("输入资产不属于当前项目或已不可用")
    root = project_notes.project_root(db, project_id)
    path = Path(asset["canonical_uri"])
    if not path.is_file() or not path.resolve().is_relative_to(root) or path.suffix.lower() not in (".md", ".txt", ".csv", ".json"):
        raise ValueError("请选择项目内的 Markdown、文本、CSV 或 JSON 资产")
    if path.stat().st_size > 200000:
        raise ValueError("输入材料过大，请选择相关片段")
    content = path.read_text(encoding="utf-8")
    if note_catalog.is_template(content):
        raise ValueError("提示词模板不能作为研究材料变量")
    return content, {"asset_id": asset["id"], "content_hash": asset_service.sha256_file(path)}


def render(db, project_id, template_id, variables=None, revision_id=""):
    item = read(db, project_id, template_id, revision_id)
    if not isinstance(variables or {}, dict):
        raise ValueError("变量须为键值对象")
    values = dict(variables or {})
    task = db.summary(project_id)["task"]
    automatic = {"project.title": task["title"], "project.question": task.get("question", ""),
                 "research_question": task.get("question", ""), "研究问题": task.get("question", "")}
    extra = set(values) - set(item["variables"])
    if extra:
        raise ValueError("模板未声明这些变量：" + "、".join(sorted(extra)))
    missing, errors, inputs, resolved = [], {}, [], {}
    for name, spec in item["variables"].items():
        value = values.get(name, spec.get("default", automatic.get(name)))
        if value is None or value == "":
            if spec.get("required", True):
                missing.append(name)
            resolved[name] = ""
            continue
        kind = spec.get("type", "text")
        try:
            if kind == "asset_ref":
                rendered, reference = _asset_input(db, project_id, value)
                inputs.append(reference)
            elif kind == "number":
                if isinstance(value, bool):
                    raise ValueError("需要数字")
                number = float(value)
                import math
                if not math.isfinite(number):
                    raise ValueError("需要有限数字")
                rendered = str(value)
            elif kind == "enum":
                if value not in spec["options"]:
                    raise ValueError("请选择给定选项")
                rendered = str(value)
            else:
                if not isinstance(value, str):
                    raise ValueError("需要文本")
                rendered = value
            if len(rendered) > 200000:
                raise ValueError("变量内容过长")
            resolved[name] = rendered
        except (ValueError, OSError) as exc:
            errors[name] = str(exc)
    if missing or errors:
        return {**item, "ok": False, "missing": missing, "errors": errors}
    text = TOKEN.sub(lambda m: resolved[m[1]], item["body"])
    if len(text) > 250000:
        raise ValueError("渲染后的模板过长，请缩小材料范围")
    return {**item, "ok": True, "rendered": text, "values": values, "input_assets": inputs}


def copy_template(db, project_id, path, title="", destination_project=""):
    source = project_notes.read(db, project_id, path)
    meta, body = note_catalog.split(source["content"])
    meta.pop("template_id", None)
    destination = destination_project or project_id
    catalog = note_catalog.load(db, destination)
    default = next((c for c in catalog["categories"] if c.get("preset_key") == "prompt_templates"), None)
    meta.update(note_type="prompt_template", category_id=default["id"] if default else "")
    return project_notes.write(db, destination, "", body, title=title or source["title"] + "（副本）",
                               metadata={k: v for k, v in meta.items() if k in note_catalog.FIELDS})


def bindings(db, project_id):
    if not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        raise ValueError("项目不存在")
    return [{**dict(r), "variables": json.loads(r["variables"])} for r in db.db.execute(
        "SELECT * FROM prompt_template_bindings WHERE project_id=? ORDER BY scope,scope_id", (project_id,))]


def binding_options(db, project_id):
    project_notes.project_root(db, project_id)
    from workflow_catalog import STAGE_CATALOG
    members = [dict(r) for r in db.db.execute(
        "SELECT m.id,m.name FROM team_members m LEFT JOIN teams t ON t.id=m.task_id "
        "WHERE m.task_id=? OR t.project_id=?", (project_id, project_id))]
    return {"project": [{"id": "", "name": "当前项目"}],
            "step": [{"id": s["id"], "name": s["name"]} for s in STAGE_CATALOG],
            "assistant": [dict(r) for r in db.db.execute("SELECT id,name FROM assistants ORDER BY name")],
            "member": members,
            "schedule": [dict(r) for r in db.db.execute("SELECT id,name FROM schedules WHERE project_id=?", (project_id,))],
            "assets": [{"id": a["id"], "name": a["title"]} for a in asset_service.research_assets(db, project_id, 500)
                       if Path(a["canonical_uri"]).suffix.lower() in (".md", ".txt", ".csv", ".json")]}


def save_binding(db, project_id, body):
    project_notes.project_root(db, project_id, writable=True)
    scope, sid = str(body.get("scope") or "project"), str(body.get("scope_id") or "")
    if scope == "project":
        sid = ""
    elif scope == "step":
        from workflow_catalog import STAGES
        if sid not in STAGES:
            raise ValueError("工作流步骤无效")
    elif scope == "assistant":
        if not db.assistant(sid):
            raise ValueError("专家不存在")
    elif scope in ("member", "schedule"):
        if sid not in {r["id"] for r in binding_options(db, project_id)[scope]}:
            raise ValueError("绑定对象不属于当前项目")
    else:
        raise ValueError("不支持的绑定范围")
    if body.get("delete"):
        db.db.execute("DELETE FROM prompt_template_bindings WHERE project_id=? AND scope=? AND scope_id=?", (project_id, scope, sid))
    else:
        tid = str(body.get("template_id") or "")
        item = read(db, project_id, tid, str(body.get("revision_id") or ""))
        values = body.get("variables") or {}
        if not isinstance(values, dict) or set(values) - set(item["variables"]):
            raise ValueError("绑定默认变量不符合模板定义")
        rid = "" if body.get("follow_latest") else item["revision_id"]
        db.db.execute("INSERT OR REPLACE INTO prompt_template_bindings VALUES (?,?,?,?,?,?,?)",
                      (project_id, scope, sid, tid, rid, json.dumps(values, ensure_ascii=False), int(body.get("enabled", True))))
    db.db.commit()
    return {"bindings": bindings(db, project_id)}


def resolve(db, project_id, explicit=None, *, step="", member_id="", assistant_id="", schedule_id=""):
    request = explicit
    if request is None:
        choices = [("schedule", schedule_id), ("step", step), ("member", member_id), ("assistant", assistant_id), ("project", "")]
        rows = bindings(db, project_id)
        request = next((r for scope, sid in choices for r in rows if (sid or scope == "project")
                        and r["scope"] == scope and r["scope_id"] == sid and r["enabled"]), None)
    if request is None:
        return None
    if not isinstance(request, dict) or not request.get("template_id"):
        raise ValueError("需要有效的模板引用")
    if request.get("project_id", project_id) != project_id:
        raise ValueError("模板引用不属于当前项目")
    result = render(db, project_id, request["template_id"], request.get("variables"), request.get("revision_id", ""))
    if not result["ok"]:
        raise ValueError("模板变量待补充：" + "、".join(result["missing"] + [f"{k}: {v}" for k, v in result["errors"].items()]))
    return result


def usage(db, snapshot, run_id, node_id=""):
    asset_service.provenance(db, snapshot["asset_id"], run_id, "template_used", actor="agent",
                             input_snapshot={k: v for k, v in snapshot.items() if k not in ("body", "variables")})
    if node_id:
        row = db.db.execute("SELECT checkpoint FROM workflow_nodes WHERE id=?", (node_id,)).fetchone()
        checkpoint = json.loads(row["checkpoint"] or "{}") if row else {}
        checkpoint["template_snapshot"] = snapshot
        db.db.execute("UPDATE workflow_nodes SET checkpoint=? WHERE id=?", (json.dumps(checkpoint, ensure_ascii=False), node_id))
        db.db.commit()


def history(db, project_id, template_id):
    asset_ids = {a["id"] for a in asset_service.find_by_metadata(db, project_id=project_id, kind="note", key="template_id", value=template_id)
                 if a["project_id"] == project_id}
    return [{**dict(r), "input_snapshot": json.loads(r["input_snapshot"])} for r in db.db.execute(
        "SELECT p.* FROM asset_provenance p JOIN assets a ON a.id=p.asset_id WHERE a.project_id=? AND p.operation='template_used' ORDER BY p.created_at DESC LIMIT 100", (project_id,)) if r["asset_id"] in asset_ids]


def library(db, project_id=""):
    from paper_toolbox import PROMPT_PRESETS
    if not project_id:
        return {"project_id":"","templates":[],"builtins":PROMPT_PRESETS,"catalog":{"categories":[]},"read_only":True}
    ensure_defaults(db,project_id)
    board=project_notes.dashboard(db,project_id)
    counts={}
    for row in bindings(db,project_id):counts[row['template_id']]=counts.get(row['template_id'],0)+1
    return {"project_id":project_id,"templates":[{**item,"binding_count":counts.get(item['template_id'],0)} for item in board['items']
            if item.get('note_type')=='prompt_template' and item.get('template_id')],
            "builtins":PROMPT_PRESETS,"catalog":board['catalog'],"read_only":board['read_only']}


def _library_note(db,project_id,template_id):
    hits=[item for item in search(db,project_id,include_disabled=True)['templates'] if item['template_id']==template_id]
    if len(hits)!=1:raise ValueError('模板不存在或不属于当前项目')
    return project_notes.read(db,project_id,hits[0]['path'])


def inspect_template(db,project_id,template_id):
    note=_library_note(db,project_id,template_id)
    variables=note['metadata'].get('variables')
    if variables is None:variables={name:{'type':'text','required':True} for name in dict.fromkeys(TOKEN.findall(note['body']))}
    return {**note,'variables':variables}


@project_notes.synchronized
def save_template(db,project_id,body):
    template_id=str(body.get('template_id') or '')
    note=_library_note(db,project_id,template_id) if template_id else None
    if note and str(body.get('expected_hash') or '')!=note['content_hash']:
        raise ValueError('模板已被其他操作修改，请保留当前内容并重新打开后合并')
    title=str(body.get('title') or '').strip()
    if not title or len(title)>100:raise ValueError('模板名称须为 1 至 100 字符')
    content=str(body.get('content') or '')
    metadata=dict(note['metadata']) if note else {}
    changes=body.get('metadata') or {}
    if not isinstance(changes,dict):raise ValueError('模板属性须为键值对象')
    metadata.update({key:value for key,value in changes.items() if key in ('category_id','tags','template_status','description','variables')})
    metadata['note_type']='prompt_template'
    if not note and not metadata.get('category_id'):
        metadata['category_id']=next((c['id'] for c in note_catalog.load(db,project_id)['categories'] if c.get('preset_key')=='prompt_templates'),'')
    variables=metadata.get('variables')
    if isinstance(variables,dict):
        metadata['variables']={name:variables.get(name,{'type':'text','required':True}) for name in dict.fromkeys(TOKEN.findall(content))}
    saved=project_notes.write(db,project_id,note['path'] if note else '',content,title=title,create=not note,
                              expected_hash=note['content_hash'] if note else None,metadata=metadata)
    if note:
        _sync_template_filename(db, project_id, saved['path'], title)
    return inspect_template(db,project_id,saved['template_id'])


@project_notes.synchronized
def delete_template(db,project_id,body):
    note=_library_note(db,project_id,str(body.get('template_id') or ''))
    if str(body.get('expected_hash') or '')!=note['content_hash']:
        raise ValueError('模板已被其他操作修改，请重新打开后再删除')
    result=project_notes.delete(db,project_id,note['path'])
    db.db.execute('DELETE FROM prompt_template_bindings WHERE project_id=? AND template_id=?',(project_id,note['template_id']))
    db.db.commit()
    return result


def route(db, path, body):
    pid = str(body.get("project_id") or "")
    action = path.rsplit("/", 1)[-1]
    if action == 'library':return library(db,pid)
    if action == 'inspect':return inspect_template(db,pid,str(body.get('template_id') or ''))
    if action == 'save':return save_template(db,pid,body)
    if action == 'delete':return delete_template(db,pid,body)
    if action == "search":
        return search(db, pid, str(body.get("query") or ""), body.get("tags"), bool(body.get("include_disabled")))
    if action == "read":
        return read(db, pid, str(body.get("template_id") or ""), str(body.get("revision_id") or ""))
    if action == "render":
        return render(db, pid, str(body.get("template_id") or ""), body.get("variables"), str(body.get("revision_id") or ""))
    if action == "copy":
        return copy_template(db, pid, str(body.get("path") or ""), str(body.get("title") or ""), str(body.get("destination_project") or ""))
    if action == "bindings":
        return {"bindings": bindings(db, pid)}
    if action == "options":
        return binding_options(db, pid)
    if action == "bind":
        return save_binding(db, pid, body)
    if action == "history":
        return {"history": history(db, pid, str(body.get("template_id") or ""))}
    raise ValueError("未知模板操作")
