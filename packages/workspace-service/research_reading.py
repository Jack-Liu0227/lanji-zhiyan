"""Abstract screening and bounded retrieval from indexed paper pages."""
from hashlib import sha256
import json
import re
import unicodedata

import asset_service
import execution


ABSTRACT = re.compile(r"(?im)^[ \t]*(?:a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|摘\s*要)\b[ \t]*[:：.—-]?[ \t]*")
ABSTRACT_END = re.compile(
    r"(?im)^[ \t]*(?:key[ \t-]*words?|关键词|(?:1[.)]?[ \t]+)?introduction|引言|"
    r"(?:2[.)]?[ \t]+)?materials and methods)\b"
)
ABSTRACT_LIMIT = 2400


def _project(db, project_id):
    if not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        raise ValueError("请先选择文献所属项目")


def _available(db, project_id):
    index = asset_service.eligibility_index(db, project_id)
    return [paper for paper in db.papers_of(project_id)
            if asset_service.legacy_context_available(db, project_id, "paper", paper["id"], index=index)]


def abstract(db, project_id, paper):
    """Read at most the first three indexed pages locally. Require an identifiable abstract."""
    indexed = db.paper(paper['id'])
    if indexed['task_id'] != project_id:
        raise PermissionError('只能读取当前项目的文献')
    rows = indexed['pages'][:3]
    info = {"paper_id": paper["id"], "title": paper.get("title") or paper["filename"],
            "doi": paper.get("doi", ""), "version": indexed['sha256'], "status": "missing",
            "authors": paper.get("authors", ""), "year": paper.get("year", ""),
            "fulltext_status": "indexed", "read_with": {"tool": "paper_focus", "paper_ids": [paper["id"]]}}
    for index, row in enumerate(rows):
        text = row["text"] or ""
        start = ABSTRACT.search(text)
        if not start:
            continue
        body = "\n".join([text[start.end():], *(page["text"] or "" for page in rows[index + 1:])])
        end = ABSTRACT_END.search(body)
        if end is None:
            # In ambiguous layouts the candidate may include body text; keep it out of screening.
            return info
        value = " ".join(body[:end.start()].split())
        if len(value) >= 40:
            return {**info, "status": "available", "abstract": value[:ABSTRACT_LIMIT],
                    "page": row["page_no"], "truncated": len(value) > ABSTRACT_LIMIT}
    return info


def abstracts(db, project_id, *, offset=0, limit=6, result_ids=None):
    _project(db, project_id)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 20))
    papers = _available(db, project_id)
    if result_ids is not None:
        if (not isinstance(result_ids, list) or len(result_ids) > 100 or
                any(not isinstance(item, str) or not item for item in result_ids)):
            raise ValueError('result_ids 须为最多 100 个当前项目题录 ID')
        available_records = {record['id']: record for record in db.results(project_id)}
        records = [available_records.get(result_id) for result_id in dict.fromkeys(result_ids)]
        if any(not record or record.get('task_id') != project_id for record in records):
            raise ValueError('只能读取当前项目中的题录')
    else:
        records = db.results(project_id)
    metadata = []
    paper_ids = {paper['id'] for paper in papers}
    linked = {row['result_id']: row['paper_id'] for row in db.db.execute(
        'SELECT result_id,paper_id FROM project_paper_links WHERE task_id=?', (project_id,))
        if row['paper_id'] in paper_ids}
    known = {str(paper.get("doi") or "").casefold() for paper in papers}
    for record in records:
        value = " ".join(str(record.get("abstract") or "").split())
        doi = record.get("doi") or ""
        if result_ids is None and doi and doi.casefold() in known:
            continue
        if doi:
            known.add(doi.casefold())
        item = {"result_id": record["id"], "title": record["title"], "doi": doi, "status": "missing",
                "authors": record.get("authors", ""), "year": record.get("year", ""),
                "source": record.get("source", ""), "url": record.get("url", ""),
                "fulltext_status": "not_indexed", "download_with": {"tool": "paper_download", "result_id": record["id"]},
                "next_step": "此记录是检索题录。精读前先下载开放全文，使用下载结果中的 paper.id 调用 paper_focus；下载不可用时记录证据缺口"}
        if value:
            item.update(status="metadata", abstract=value[:ABSTRACT_LIMIT], truncated=len(value) > ABSTRACT_LIMIT)
        if record['id'] in linked:
            paper_id = linked[record['id']]
            item.update(paper_id=paper_id, fulltext_status='indexed',
                        read_with={'tool': 'paper_focus', 'paper_ids': [paper_id]},
                        next_step='全文已在当前项目入库；用 paper_id 调用 paper_focus，无需重复下载')
            item.pop('download_with', None)
        metadata.append(item)
    choices = [(False, paper) for paper in papers] + [(True, item) for item in metadata]
    selected = [item if from_search else abstract(db, project_id, item)
                for from_search, item in choices[offset:offset + limit]]
    return {"papers": selected, "next_offset": offset + limit if offset + limit < len(choices) else None}


def focus(db, project_id, question, paper_ids, keywords, limit=6):
    _project(db, project_id)
    question = str(question or "").strip()
    if not question or len(question) > 500:
        raise ValueError("请给出不超过 500 字的单个待核查问题")
    if not isinstance(paper_ids, list) or not 1 <= len(paper_ids) <= 8:
        raise ValueError("请从摘要初筛结果中指定 1–8 篇已入库论文")
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 12:
        raise ValueError("请给出 1–12 个原文可能使用的关键词或短语")
    terms = [str(item).strip() for item in keywords]
    terms = list(dict.fromkeys(term.casefold() for term in terms if 2 <= len(term) <= 80))
    if not terms:
        raise ValueError("需要至少一个长度为 2–80 字符的有效关键词")

    available = {item["id"]: item for item in _available(db, project_id)}
    ids = list(dict.fromkeys(str(item) for item in paper_ids))
    if any(paper_id not in available for paper_id in ids):
        missing = [paper_id for paper_id in ids if paper_id not in available]
        records = {record['id'] for record in db.results(project_id)}
        if all(paper_id in records for paper_id in missing):
            raise ValueError('这些 ID 是当前项目的检索题录，尚未索引可用全文。请先用 paper_download(result_id=题录ID)，再用返回的 paper.id 调用 paper_focus；本节点没有下载权限时请让检索节点补齐全文。这不是项目权限错误')
        raise PermissionError("精读只能使用当前项目中可用的论文")
    patterns = [
        re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I)
        if term.isascii() else re.compile(re.escape(term), re.I)
        for term in terms
    ]
    picks = []
    for paper_id in ids:
        paper = db.paper(paper_id)
        by_page = paper['pages']
        for row in by_page:
            execution.check()
            text = row["text"] or ""
            hits = [(match.start(), match.end()) for pattern in patterns if (match := pattern.search(text))]
            if not hits:
                continue
            best_start, best_end = max(hits, key=lambda hit: sum(abs(hit[0] - other[0]) <= 300 for other in hits))
            start = max(0, best_start - 260)
            end = min(len(text), best_end + 400)
            excerpt = text[start:end].strip()
            weight = sum(bool(pattern.search(excerpt)) for pattern in patterns)
            citation = f"{paper['filename']} p.{row['page_no']}"
            evidence_id = sha256(f"{paper_id}:{paper.get('sha256')}:{row['page_no']}:{start}:{end}".encode()).hexdigest()[:20]
            picks.append((weight, {"excerpt_id": evidence_id, "paper_id": paper_id, "page_no": row["page_no"],
                                  "citation": citation, "excerpt": excerpt, "start": start, "end": end,
                                  "version": paper.get('sha256', '')}))
    picks.sort(key=lambda pair: -pair[0])
    limit = max(1, min(int(limit), 6))
    return {"question": question, "matches": [item for _, item in picks[:limit]], "matched_pages": len(picks),
            "status": "found" if picks else "unresolved"}


def evidence(db, project_id, ids):
    _project(db, project_id)
    if not isinstance(ids, list) or not ids or any(not isinstance(item, (str, int)) for item in ids):
        raise ValueError("请提供一个或多个已登记的证据 ID")
    index = asset_service.eligibility_index(db, project_id)
    records = {row["id"]: dict(row) for row in db.evidence(project_id)}
    result = []
    for evidence_id in dict.fromkeys(str(item) for item in ids):
        row = records.get(evidence_id)
        if not row or not asset_service.legacy_context_available(db, project_id, "evidence", evidence_id, index=index):
            raise ValueError(f"证据不属于当前项目或尚不可用：{evidence_id}")
        db.paper(row['paper_id'])  # Refresh a changed Zotero attachment before citing its page.
        if db.db.execute('SELECT 1 FROM paper_evidence_recheck WHERE evidence_id=?', (evidence_id,)).fetchone():
            raise ValueError(f'PDF 内容已变化，请重新核对证据：{evidence_id}')
        result.append({key: row[key] for key in ("id", "paper_id", "filename", "page_no", "excerpt", "field", "value")})
    return result


_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[-−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")


def _normalize(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def _numbers(value):
    text = unicodedata.normalize("NFKC", str(value)).replace("−", "-")
    text = re.sub(r"(?<=\d)\s+%", "%", text)
    return set(_NUMBER.findall(text))


def _focused_excerpt(db, project_id, paper_id, excerpt_id):
    context = getattr(db, 'project_task_context', None)
    task_id = getattr(context, 'parent_task_id', '') if context else ''
    checkpoint = getattr(db, 'job_checkpoint', {}) or {}
    if task_id:
        task = db.db.execute('SELECT latest_run_id FROM project_tasks WHERE id=? AND project_id=?',
                             (task_id, project_id)).fetchone()
        if not task or not task['latest_run_id']:
            return None
        run = db.db.execute('SELECT checkpoint FROM project_task_runs WHERE id=? AND task_id=?',
                            (task['latest_run_id'], task_id)).fetchone()
        if not run:
            return None
        checkpoint = json.loads(run['checkpoint'] or '{}')
    for key, result in reversed(list((checkpoint.get('done_tools') or {}).items())):
        if str(key).split('\0', 1)[0] != 'paper_focus' or not isinstance(result, dict):
            continue
        for match in result.get('matches') or []:
            if match.get('paper_id') == paper_id and match.get('excerpt_id') == excerpt_id:
                return match
    return None


def record(db, project_id, paper_id, items):
    """Register precise, reusable evidence through the existing PDF-page gate."""
    import asset_service
    import project_tasks
    from research_core import compact

    paper = next((row for row in db.papers_of(project_id) if row["id"] == paper_id), None)
    if not paper:
        raise ValueError("论文不属于当前项目或当前不可用")
    if paper["source_path"]:
        asset_service.require_path_context(db, project_id, paper["source_path"])
    if not isinstance(items, list) or not 1 <= len(items) <= 30:
        raise ValueError("每次请提交 1 至 30 条原文证据")
    accepted, rejected = [], []
    with project_tasks._lock:
        for position, item in enumerate(items, 1):
            try:
                if isinstance(item, dict) and item.get('excerpt_id'):
                    match = _focused_excerpt(db, project_id, paper_id, str(item['excerpt_id']))
                    if not match:
                        raise ValueError('excerpt_id was not returned by paper_focus in the current task attempt')
                    current_version = db.paper(paper_id).get('sha256', '')
                    if match.get('version') and match['version'] != current_version:
                        raise ValueError('PDF changed after paper_focus; retrieve the excerpt again')
                    item = {**item, **{key: match[key] for key in ('page_no', 'start', 'end', 'excerpt')}}
                if not isinstance(item, dict) or type(item.get("page_no")) is not int:
                    raise ValueError("请填写原文物理页码")
                field = str(item.get("field") or "").strip()
                value = str(item.get("value") or "").strip()
                excerpt = str(item.get("excerpt") or "")
                start, end = item.get("start"), item.get("end")
                if start is not None or end is not None:
                    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                        raise ValueError("原文起止位置无效")
                    pages = {page["page_no"]: page["text"] or "" for page in db.paper(paper_id)["pages"]}
                    page_text = pages.get(item["page_no"])
                    if page_text is None or end > len(page_text):
                        raise ValueError("原文起止位置超出该 PDF 页")
                    source_excerpt = page_text[start:end]
                    if excerpt and compact(excerpt) != compact(source_excerpt):
                        raise ValueError("提交的原文与 PDF 页位置不一致")
                    excerpt = source_excerpt
                excerpt = compact(excerpt)
                if len(field) > 120 or len(value) > 400 or len(excerpt) > 1500:
                    raise ValueError("证据字段、数值或原文超出长度限制")
                if not _numbers(value).issubset(_numbers(excerpt)):
                    raise ValueError("登记数值未在所提交的原文中找到")
                key = (paper_id, item["page_no"], field, value, excerpt)
                existing = db.db.execute("SELECT * FROM evidence WHERE paper_id=? AND page_no=? AND field=? AND value=? AND excerpt=? "
                                         "AND NOT EXISTS(SELECT 1 FROM paper_evidence_recheck r WHERE r.evidence_id=evidence.id)",
                                         key).fetchone()
                accepted.append(dict(existing) if existing else db.add_evidence(paper_id, {
                    "page_no": item["page_no"], "field": field, "value": value, "excerpt": excerpt}))
            except ValueError as exc:
                rejected.append({"index": position, "reason": str(exc)})
    return {"accepted": accepted, "rejected": rejected}
