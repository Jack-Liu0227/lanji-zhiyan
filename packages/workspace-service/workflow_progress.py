"""Track text actually supplied to a model, separately from artifact acceptance."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


_WRITE_TOOLS = {"project_artifact_write", "project_resource_write"}
_READ_TOOLS = {"workflow_context_read", "read_attachment"}
_LOOKUP_TOOLS = {"paper_search", "paper_abstracts", "paper_evidence_get"}
_CLIP_MARKERS = ("\n[已节选", "\n[... tool result middle pruned ...]")


def _copy(state: dict[str, Any] | None) -> dict[str, Any]:
    value = deepcopy(state) if state and state.get("version") == 1 else {}
    value.update(version=1)
    for key, default in (("reads", {}), ("observations", []), ("writes", []), ("evidence", {}), ("lookups", {})):
        value.setdefault(key, default)
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            pass
    return {}


def _identity(result: dict[str, Any]) -> dict[str, str]:
    return {key: str(result.get(key) or "") for key in
            ("run_id", "node_id", "section", "artifact", "delivery_id", "content_hash")}


def _base(identity: dict[str, str]) -> str:
    return _json({key: identity[key] for key in ("run_id", "node_id", "section", "artifact")})


def _request(args: dict[str, Any], identity: dict[str, str]) -> str:
    value = {key: item for key, item in args.items()
             if key not in {"reread", "run_id", "node_id", "section", "artifact", "offset", "limit"}}
    value.update({key: identity[key] for key in ("run_id", "node_id", "section", "artifact")})
    value["offset"] = max(0, int(args.get("offset") or 0))
    value["limit"] = max(1, int(args.get("limit") or args.get("chars") or
                               (20000 if identity["section"] == "attachment" else 8000)))
    return _json(value)


def _lookup_key(name: str, args: dict[str, Any]) -> str:
    """Identify one completed lookup without retaining its bulky response body."""
    return _json({"tool": name, "args": args if isinstance(args, dict) else {}})


def _lookup_id(key: str) -> str:
    """Stable reference for one exact tool/argument lookup."""
    return sha256(key.encode("utf-8")).hexdigest()


def _bibliographic(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {key: item[key] for key in
            ("id", "result_id", "paper_id", "task_id", "source", "title", "authors", "year",
             "doi", "url", "pdf_url", "fulltext_status", "status") if key in item}


def _lookup_facts(name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("error"):
        return None
    if name == "paper_search":
        matches = result.get("matches")
        if not isinstance(matches, list):
            return None
        return {"query": result.get("query", ""), "found": result.get("found"),
                "matches": [_bibliographic(item) for item in matches if _bibliographic(item)]}
    if name == "paper_abstracts":
        papers = result.get("papers")
        if not isinstance(papers, list):
            return None
        return {"papers": [_bibliographic(item) for item in papers if _bibliographic(item)],
                "next_offset": result.get("next_offset")}
    if name == "paper_evidence_get":
        evidence = result.get("evidence")
        if not isinstance(evidence, list):
            return None
        fields = ("id", "paper_id", "filename", "page_no", "field", "value")
        return {"evidence": [{key: item[key] for key in fields if key in item}
                              for item in evidence if isinstance(item, dict) and item.get("id")]}
    return None


def _merge(ranges: list[list[int]], start: int, end: int) -> list[list[int]]:
    merged: list[list[int]] = []
    for left, right in sorted([*ranges, *([[start, end]] if end > start else [])]):
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return merged


def _cursor(ranges: list[list[int]], total: int) -> int | None:
    cursor = 0
    for start, end in ranges:
        if start > cursor:
            break
        cursor = max(cursor, end)
    return cursor if cursor < total else None


def _covers(ranges: list[list[int]], start: int, end: int) -> bool:
    return any(left <= start and right >= end for left, right in ranges)


def _read(state: dict[str, Any], args: dict[str, Any], result: dict[str, Any], call_id: str) -> None:
    content = result.get("content")
    if not isinstance(content, str) or result.get("error") or result.get("repeat"):
        return
    identity = _identity(result)
    if not identity["run_id"] or not identity["node_id"] or not identity["content_hash"]:
        return
    start = max(0, int(result.get("offset", args.get("offset") or 0)))
    total = int(result.get("total", 0))
    for marker in _CLIP_MARKERS:
        content = content.split(marker, 1)[0]
    end = min(total, start + len(content), int(result.get("end_offset", start + len(content))))
    if end < start or total < 0:
        return
    key = _base(identity)
    entry = state["reads"].get(key)
    retired = entry.get("retired_versions", []) if entry else []
    if identity in retired:
        return
    observation = sha256(_json([call_id, identity, start, end, content]).encode()).hexdigest()
    if observation in state["observations"]:
        return
    state["observations"].append(observation)
    if not entry or entry["identity"] != identity or entry["total"] != total:
        if entry and entry["identity"] != identity:
            retired = [*retired, entry["identity"]]
        entry = state["reads"][key] = {"identity": identity, "total": total, "coverage": [],
                                      "requests": [], "retired_versions": retired}
    entry["coverage"] = _merge(entry["coverage"], start, end)
    request = _request(args, identity)
    if request not in entry["requests"]:
        entry["requests"].append(request)


def observe_result(state: dict[str, Any] | None, name: str, args: dict[str, Any],
                   result: dict[str, Any]) -> dict[str, Any]:
    """Remember returned lookup facts, accepted registry records and actual writes."""
    value = _copy(state)
    if name in _LOOKUP_TOOLS:
        facts = _lookup_facts(name, result)
        if facts is not None:
            entry = {"tool": name, "args": deepcopy(args), **facts}
            key = _lookup_key(name, args)
            if value["lookups"].get(key) != entry:
                value["lookups"].pop(key, None)
                value["lookups"][key] = entry
        return value
    if name == "paper_evidence_record" and isinstance(result, dict):
        for evidence in result.get("accepted") or []:
            if not isinstance(evidence, dict) or not evidence.get("id"):
                continue
            evidence_id = str(evidence["id"])
            value["evidence"][evidence_id] = {
                key: evidence.get(key, "")
                for key in ("id", "paper_id", "filename", "page_no", "field", "value")
            }
        return value
    if name not in _WRITE_TOOLS or result.get("error") or result.get("ok") is False:
        return value
    receipt = {key: str(result.get(key) or "") for key in ("path", "content_hash", "asset_id")}
    if not all(receipt.values()):
        return value
    if receipt not in value["writes"]:
        value["writes"].append(receipt)
    return value


def annotate_read(state: dict[str, Any] | None, args: dict[str, Any],
                  result: dict[str, Any]) -> dict[str, Any]:
    """Attach visible coverage for the current source version.

    Coverage is a record of text supplied to a model. It is never evidence
    validation or delivery approval, and a reread may still return the body.
    """
    value = deepcopy(result) if isinstance(result, dict) else {"content": result}
    if not isinstance(result, dict) or result.get("error") or not isinstance(result.get("content"), str):
        return value
    identity = _identity(result)
    if not identity["run_id"] or not identity["node_id"] or not identity["content_hash"] or "total" not in result:
        return value
    total = int(result["total"])
    if total < 0:
        return value
    entry = _copy(state)["reads"].get(_base(identity))
    coverage: list[list[int]] = []
    if entry and entry.get("identity") == identity and int(entry.get("total", -1)) == total:
        coverage = deepcopy(entry.get("coverage") or [])
    next_unread = _cursor(coverage, total)
    value["read_progress"] = {
        "visible_ranges": coverage,
        "next_unread_offset": next_unread,
        "coverage_complete": next_unread is None,
        "meaning": "Coverage of visible text only; not evidence verification or delivery approval.",
        **({"continuation": "Already fully supplied; do not restart a full scan. This page cursor is only for deliberate rechecks."}
           if next_unread is None else {}),
    }
    return value


def observe(state: dict[str, Any] | None, model_messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Observe the final provider view, not the original uncompressed read response."""
    value = _copy(state)
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in model_messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            calls[str(call.get("id") or "")] = (function.get("name", ""), _object(function.get("arguments")))
        if message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        paired_name, args = calls.get(call_id, ("", {}))
        name = message.get("name") or paired_name
        result = _object(message.get("content"))
        if name in _READ_TOOLS:
            _read(value, args, result, call_id)
    return value


def prompt(state: dict[str, Any] | None) -> str:
    """Compact progress facts; coverage is not verification or permission to deliver."""
    value = _copy(state)
    reads = [{**entry["identity"], "total": entry["total"], "visible_ranges": entry["coverage"],
              "next_unread_offset": _cursor(entry["coverage"], entry["total"]),
              "coverage_complete": _cursor(entry["coverage"], entry["total"]) is None}
             for entry in value["reads"].values()]
    latest_writes = {receipt["path"]: receipt for receipt in value["writes"]}
    content_reads = [item for item in reads if item.get("section") in {"artifact", "output", "attachment"}]
    artifact_completion = bool(content_reads) and all(item["coverage_complete"] for item in content_reads)
    lookups, retrieved_evidence, returned_bibliography = [], {}, {}
    bibliography_ids: dict[str, str] = {}
    lookup_ids = {key: f"L{index}" for index, key in enumerate(value["lookups"], 1)}
    for key, entry in value["lookups"].items():
        lookup_id = lookup_ids[key]
        if entry["tool"] == "paper_evidence_get":
            for evidence in entry["evidence"]:
                retrieved_evidence[evidence["id"]] = evidence
            lookups.append({"lookup_id": lookup_id, "tool": entry["tool"], "args": entry["args"],
                            "evidence_ids": [evidence["id"] for evidence in entry["evidence"]]})
        else:
            items = entry.get("matches") if entry["tool"] == "paper_search" else entry.get("papers")
            refs = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                # Keep the source's own identity fields. Add a project scope only
                # when the response omitted task_id and the request supplied one.
                scope = ""
                if not item.get("task_id"):
                    scope = str(entry.get("args", {}).get("project") or
                                entry.get("args", {}).get("task_id") or "")
                record = {**item, **({"project": scope} if scope else {})}
                fingerprint = sha256(_json(record).encode("utf-8")).hexdigest()
                if fingerprint not in bibliography_ids:
                    bibliography_ids[fingerprint] = f"B{len(bibliography_ids) + 1}"
                    returned_bibliography[bibliography_ids[fingerprint]] = {**record, "lookup_refs": []}
                record_id = bibliography_ids[fingerprint]
                refs.append(record_id)
                if lookup_id not in returned_bibliography[record_id]["lookup_refs"]:
                    returned_bibliography[record_id]["lookup_refs"].append(lookup_id)
            lookup_view = {"lookup_id": lookup_id, "tool": entry["tool"], "args": entry["args"],
                           "record_refs": refs}
            if entry["tool"] == "paper_search":
                lookup_view["found"] = entry.get("found")
            else:
                lookup_view["next_offset"] = entry.get("next_offset")
            lookups.append(lookup_view)
    # Keep the output deterministic while retaining every conflicting record.
    for record in returned_bibliography.values():
        record["lookup_refs"] = sorted(record["lookup_refs"], key=lambda ref: int(ref[1:]))
    return _json({"version": 1, "meaning": "Visible text ranges only; not evidence verification or delivery approval.",
                  "reads": reads, "saved_writes": list(latest_writes.values()),
                  "accepted_evidence": list(value["evidence"].values()),
                  **({"completed_lookups": lookups, "returned_bibliography": returned_bibliography,
                      "retrieved_evidence": list(retrieved_evidence.values()),
                      "lookup_instruction": "These are successful tool-returned bibliographic and registry facts, not this node's verification, newly accepted evidence, or delivery approval. Reuse returned identifiers and metadata when unchanged; summary omissions never make a completed lookup unresolved. Repeat or broaden a lookup only for a concrete mismatch, missing field, source change, or required recheck. Record any remaining ambiguity in the required outputs instead of restarting the same lookup."}
                     if lookups else {}),
                  **({"completion_instruction": "All current artifact/output/attachment versions recorded in this progress ledger have complete visible coverage. Use the retained bibliography, returned evidence facts, and explicit source limitations to complete this node's required outputs now. Do not repeat already-resolved searches, evidence retrievals, or full artifact rereads merely because the summary omitted them. Put unresolved or unavailable citations in citation_check and continue to the output contract; this instruction does not verify evidence, approve claims, or authorize delivery."}
                     if artifact_completion and lookups else {}),
                  **({"evidence_instruction": "Reuse accepted IDs via paper_evidence_get; do not register them again."}
                     if value["evidence"] else {}),
                  **({"read_instruction": "Complete coverage survives resumes, page excerpts, and summary omissions. Recheck specific claims only; save the required outputs with explicit limitations."}
                     if reads else {}),
                  **({"delivery_instruction": "Saved write receipts confirm content was written, not delivered. This checkpoint is for the current node only; upstream delivery paths are read-only sources and must never be revised or submitted by this node. Write and submit only the exact required_files in this node's output contract. Complete only the concrete correction already identified for this node, update receipts if a file changes, then call workflow_submit_outputs as soon as all required files are present. Do not repeat evidence or full-file checks without a concrete unresolved discrepancy."}
                     if latest_writes else {})})


def repeated_read(state: dict[str, Any] | None, args: dict[str, Any],
                  cached_result: dict[str, Any]) -> dict[str, Any] | None:
    """Elide only an unchanged, complete page already supplied to the model."""
    if args.get("reread") or cached_result.get("error") or cached_result.get("truncated"):
        return None
    if classify("workflow_context_read", {**args, **cached_result}) != "immutable":
        return None
    value = _copy(state)
    identity = _identity(cached_result)
    entry = value["reads"].get(_base(identity))
    if not entry or entry["identity"] != identity or _request(args, identity) not in entry["requests"]:
        return None
    content = cached_result.get("content")
    if not isinstance(content, str):
        return None
    start = max(0, int(cached_result.get("offset", args.get("offset") or 0)))
    end = min(entry["total"], start + len(content))
    if entry["total"] != cached_result.get("total") or not _covers(entry["coverage"], start, end):
        return None
    cursor = _cursor(entry["coverage"], entry["total"])
    hint = None if cursor is None else {**{key: identity[key] for key in ("run_id", "node_id", "section", "artifact")},
                                       "offset": cursor, "limit": max(1, int(args.get("limit") or 8000))}
    return {**{key: item for key, item in cached_result.items() if key != "content"}, "content": "",
            "offset": start, "end_offset": end, "page_next_offset": cached_result.get("next_offset"),
            "next_offset": cursor, "repeat": True, "visible_ranges": deepcopy(entry["coverage"]),
            "read_hint": hint, "meaning": "This page was already supplied; coverage does not mean verified."}


def classify(name: str, args: dict[str, Any]) -> str:
    """Only delivery-bound content is immutable; current files and actions are not."""
    if name in _READ_TOOLS:
        if name == "workflow_context_read" and args.get("section") in {"artifact", "output"} and args.get("delivery_id") and args.get("content_hash"):
            return "immutable"
        return "mutable"
    if name in {"project_resources", "project_note_read", "project_note_list", "library_overview",
                "knowledge_query", "paper_evidence_get", "paper_excerpt", "skill_read"}:
        return "mutable"
    return "side_effect"
