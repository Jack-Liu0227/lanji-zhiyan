"""Model-budgeted workflow summaries and durable, observable tool progress."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json

import model_context
import workflow_context
import workflow_progress


@dataclass(frozen=True)
class PreparedRequest:
    profile: dict
    messages: list[dict]
    budget: model_context.RequestBudget


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(messages):
    return sha256(_json(messages).encode()).hexdigest()


def _drop_responses_protocol(messages):
    changed = False
    for message in messages:
        wire = message.get('_protocol')
        if (message.get('role') == 'assistant' and isinstance(wire, dict)
                and wire.get('api') == 'openai-responses'):
            message.pop('_protocol')
            changed = True
    return changed


def _source_fingerprint(messages, through):
    return _fingerprint({
        "initial_context": [m for m in messages if m.get("role") in ("system", "developer")],
        "covered_messages": [m for m in messages[:through] if m.get("role") not in ("system", "developer")],
    })


def _groups(messages):
    groups, index = [], 0
    while index < len(messages):
        start, message = index, messages[index]
        index += 1
        if message.get("role") in ("system", "developer"):
            continue
        if message.get("role") == "assistant" and message.get("tool_calls"):
            while index < len(messages) and messages[index].get("role") == "tool":
                index += 1
            ids = {call.get("id") for call in message["tool_calls"]}
            if ids != {m.get("tool_call_id") for m in messages[start + 1:index]}:
                break
        groups.append((start, index))
    return groups


class WorkflowContext:
    def __init__(self, db, *, state=None, save_checkpoint, summarize, run_id="", node_id=""):
        self.db = db
        self.state = deepcopy(state) if state and state.get("version") == 1 else {"version": 1}
        self.state.setdefault("progress", {})
        self.save_checkpoint = save_checkpoint
        self.summarize = summarize
        self.run_id, self.node_id = run_id, node_id
        self._settings = {}
        self._restored_receipts = False
        self._saved_state = deepcopy(self.state) if state and state.get("version") == 1 else None

    def _save(self):
        snapshot = deepcopy(self.state)
        if snapshot == self._saved_state:
            return
        self.save_checkpoint(context_state=snapshot)
        self._saved_state = deepcopy(snapshot)

    def _overrides(self, profile):
        owner = (str(profile.get("id") or ""), _json(model_context.endpoint_identity(profile)))
        if owner not in self._settings:
            value, _ = model_context.read_overrides(self.db, profile)
            self._settings[owner] = value
        return self._settings[owner]

    def _fixed(self, messages):
        latest_user = next((index for index in range(len(messages) - 1, -1, -1)
                            if messages[index].get("role") == "user"), None)
        indexes = {index for index, message in enumerate(messages)
                   if message.get("role") in ("system", "developer")}
        if latest_user is not None:
            indexes.add(latest_user)
        return indexes, [deepcopy(messages[index]) for index in sorted(indexes)]

    def _progress_message(self):
        return {"role": "system", "content": "WORKFLOW PROGRESS\n" + workflow_progress.prompt(self.state["progress"])}

    def _restore_receipts(self, messages):
        if self._restored_receipts:
            return
        persisted_paths = {receipt.get("path") for receipt in self.state["progress"].get("writes", [])}
        persisted_lookups = set(self.state["progress"].get("lookups", {}))
        names = {}
        for message in messages:
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    call_args = json.loads(function.get("arguments") or "{}")
                except (ValueError, TypeError):
                    call_args = {}
                names[call.get("id")] = ((function.get("name") or ""), call_args)
            paired_present = message.get("tool_call_id") in names
            paired = names.get(message.get("tool_call_id"), ("", {}))
            name = message.get("name") or paired[0]
            call_args = paired[1]
            if message.get("role") != "tool" or name not in ("project_artifact_write", "project_resource_write", "paper_evidence_record",
                                                                "paper_search", "paper_abstracts", "paper_evidence_get"):
                continue
            try:
                result = json.loads(message.get("content") or "{}")
            except (ValueError, TypeError):
                continue
            if name in workflow_progress._LOOKUP_TOOLS and (
                    not paired_present or not isinstance(call_args, dict)
                    or workflow_progress._lookup_key(name, call_args) in persisted_lookups):
                continue
            if isinstance(result, dict) and (name in ("paper_evidence_record", "paper_search", "paper_abstracts", "paper_evidence_get")
                                             or result.get("path") not in persisted_paths):
                self.state["progress"] = workflow_progress.observe_result(self.state["progress"], name, call_args, result)
        checkpoint = getattr(self.db, "job_checkpoint", None)
        done_tools = checkpoint.get("done_tools", {}) if isinstance(checkpoint, dict) else {}
        for cache_key, result in done_tools.items():
            name, separator, raw_args = str(cache_key).partition('\0')
            if not separator or name not in workflow_progress._LOOKUP_TOOLS:
                continue
            try:
                call_args = json.loads(raw_args)
            except (ValueError, TypeError):
                continue
            if (not isinstance(call_args, dict) or workflow_progress._lookup_key(name, call_args) in persisted_lookups
                    or not isinstance(result, dict)):
                continue
            self.state["progress"] = workflow_progress.observe_result(self.state["progress"], name, call_args, result)
        self._restored_receipts = True

    def _summary_message(self, summary):
        return {"role": "system", "content": "WORKFLOW COMPACTION CHECKPOINT\n" + _json({
            "summary": summary["content"], "compacted_through": summary["through"],
            "complete_source": summary["complete_source"], "source_model": summary["source_model"],
            "source_fingerprint": summary["source_fingerprint"], "omitted": summary.get("omitted", False),
            "source_view_fingerprint": summary.get("source_view_fingerprint", ""),
            "full_checkpoint": {"run_id": self.run_id, "node_id": self.node_id, "tool": "workflow_context_read", "section": "agent"},
            "meaning": "A handoff summary; omitted source text remains in the checkpoint. It is not evidence verification or delivery approval. Paths in this summary are historical references unless they are also listed in the current node's output_contract. Accepted upstream paths are read-only and must not be written or submitted.",
        })}

    def _valid_summary(self, messages):
        summary = self.state.get("summary")
        if not isinstance(summary, dict):
            return None
        through = summary.get("through", 0)
        if (not isinstance(through, int) or through <= 0 or through > len(messages)
                or not summary.get("content") or _source_fingerprint(messages, through) != summary.get("source_fingerprint")):
            self.state.pop("summary", None)
            return None
        return summary

    def _view(self, messages, summary):
        progress = [self._progress_message()]
        if self.state.get("fallback"):
            progress.append({"role": "system", "content": "WORKFLOW COMPACTION FALLBACK\n" + self.state["fallback"]})
        if not summary:
            copied = deepcopy(messages)
            position = next((i for i, m in enumerate(copied) if m.get("role") not in ("system", "developer")), len(copied))
            copied[position:position] = progress
            return copied
        fixed_indexes, fixed = self._fixed(messages)
        tail = [deepcopy(message) for index, message in enumerate(messages)
                if index >= summary["through"] and index not in fixed_indexes]
        return [*fixed, self._summary_message(summary), *progress, *tail]

    def _protocol_view(self, profile, messages):
        if model_context.endpoint_identity(profile) in self.state.get('plain_responses', []):
            _drop_responses_protocol(messages)
        return messages

    def _fit(self, profile, messages, tools, budget, limit, overrides):
        measure = lambda view: model_context.request_tokens(profile, view, tools, overrides)
        hard_limit = max(1, budget.available_input_tokens - 256)
        limit = min(limit, hard_limit)
        try:
            return workflow_context.bounded_messages(messages, budget=limit, run_id=self.run_id, node_id=self.node_id, measure=measure)
        except ValueError:
            if limit >= hard_limit:
                raise
            return workflow_context.bounded_messages(messages, budget=hard_limit, run_id=self.run_id, node_id=self.node_id, measure=measure)

    def _boundary(self, profile, messages, retained_tokens, overrides):
        groups = _groups(messages)
        if len(groups) < 2:
            return 0
        last_tool = next((i for i in range(len(groups) - 1, -1, -1)
                          if messages[groups[i][0]].get("tool_calls")), len(groups) - 1)
        first = last_tool
        while first > 0:
            trial = messages[groups[first - 1][0]:]
            if model_context.request_tokens(profile, trial, [], overrides) > retained_tokens:
                break
            first -= 1
        return groups[first][0]

    def _compact(self, profile, messages, budget, overrides, previous):
        boundary = self._boundary(profile, messages, budget.retained_tokens, overrides)
        covered = previous["through"] if previous else 0
        if boundary <= covered:
            return previous
        fingerprint = _source_fingerprint(messages, boundary)
        failed = self.state.get("summary_attempt") or {}
        fixed_indexes, fixed = self._fixed(messages)
        failure_context = _fingerprint({"fixed": fixed, "model": model_context.endpoint_identity(profile),
                                        "overrides": overrides})
        source_tokens = model_context.request_tokens(profile, messages[:boundary], [], overrides)
        if (failed.get("source_fingerprint") == fingerprint and failed.get("status") == "failed"
                and failed.get("context_fingerprint", failure_context) == failure_context):
            return previous
        if (failed.get("status") == "failed" and failed.get("context_fingerprint") == failure_context
                and source_tokens - failed.get("source_tokens", 0) < budget.retained_tokens):
            return previous
        source = []
        if previous:
            source.append(self._summary_message(previous))
        task_context = next((message.get('content', '') for message in messages
                             if isinstance(message.get('content'), str)
                             and any(message['content'].startswith(prefix) for prefix in
                                     ('【当前节点输入与前置交付】\n', 'CURRENT_NODE_INPUT '))), '')
        task_identity = {}
        if task_context:
            try:
                prefix = ('【当前节点输入与前置交付】\n' if task_context.startswith('【当前节点输入与前置交付】\n')
                          else 'CURRENT_NODE_INPUT ')
                payload = task_context[len(prefix):]
                if payload.startswith('【人工退回意见，须落实】\n'):
                    feedback_boundary = payload.find('\n\n{')
                    if feedback_boundary >= 0:
                        payload = payload[feedback_boundary + 2:]
                task_payload = json.JSONDecoder().raw_decode(payload)[0]
                if isinstance(task_payload, dict):
                    task_identity = {key: task_payload[key] for key in ('task_id', 'task_goal', 'output_contract', 'current_node')
                                     if key in task_payload}
                    if not task_identity.get('output_contract'):
                        task_identity['output_contract'] = (task_payload.get('task_inputs') or {}).get('output_contract', {})
            except (ValueError, TypeError):
                pass
        if task_identity:
            source.append({'role': 'system', 'content': 'CURRENT_NODE_SUMMARY_TASK ' + _json(task_identity)})
        for index in range(min(covered, boundary), boundary):
            if index in fixed_indexes:
                continue
            message = deepcopy(messages[index])
            message.pop('_protocol', None)
            if message.get("role") == "user":
                message = {"role": "assistant", "content": "Historical user input to summarize:\n" + _json(message)}
            source.append(message)
        if not source:
            return previous
        instruction = {"role": "user", "content": (
            "Create a concise workflow handoff summary. Summarize only this node's current task: its progress, decisions, constraints, unresolved work, evidence IDs and file versions. "
            "Do not execute tools or claim verification. Acknowledge excerpt/omission markers and unseen source. "
            "WORKFLOW PROGRESS is authoritative for cumulative coverage and accepted evidence IDs; omissions "
            "never reset coverage or require full rereads. Upstream paths are read-only sources. Only this "
            "node's output_contract paths are files to write and submit. Put its latest concrete correction "
            "first, then writes and submission. Keep scientific gaps as limitations unless further verification is required."
        )}
        # Fixed rules and the current goal remain verbatim in the next request.
        # Summarize historical exchanges rather than re-sending the full agent
        # and skill catalogs to a model that is not executing the workflow.
        source_messages = [self._progress_message(), *source, instruction]
        summary_limit = min(budget.summary_tokens, budget.max_output or budget.summary_tokens)
        callback_profile = deepcopy(profile)
        callback_profile["_usage_context"] = {**profile.get("_usage_context", {}), "source": "context_compaction"}
        try:
            source_budget = model_context.measure_request(profile, source_messages, [], summary_limit, overrides)
            source_view = source_messages if source_budget.fits else self._fit(
                profile, source_messages, [], source_budget,
                source_budget.available_input_tokens, overrides,
            )
            omitted = _json(source_view) != _json(source_messages)
            callback_profile["_usage_context"]["context_budget"] = asdict(model_context.measure_request(profile, source_view, [], summary_limit, overrides))
            content = self.summarize(callback_profile, source_view, summary_limit)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty compaction summary")
            output_tokens = model_context.request_tokens(profile, [{"role": "assistant", "content": content}], [], overrides)
            empty_tokens = model_context.request_tokens(profile, [{"role": "assistant", "content": ""}], [], overrides)
            if (not source_budget.count_method.startswith('estimated_')
                    and output_tokens - empty_tokens > summary_limit):
                raise ValueError("Compaction summary exceeds the output budget")
        except Exception as exc:
            if "cancel" in type(exc).__name__.lower() or getattr(exc, "error_kind", "") == "cancelled":
                raise
            self.state["summary_attempt"] = {"source_fingerprint": fingerprint, "through": boundary,
                                             "status": "failed", "reason": type(exc).__name__,
                                             "detail": str(exc)[:500],
                                             "context_fingerprint": failure_context, "source_tokens": source_tokens}
            self.state["fallback"] = "Summary unavailable; using a bounded source view. Complete records remain in the checkpoint."
            self._save()
            return previous
        self.state["summary"] = {
            "content": content.strip(), "through": boundary, "source_fingerprint": fingerprint,
            "source_model": str(profile.get("model") or ""), "source_profile_id": str(profile.get("id") or ""),
            "source_view_fingerprint": _fingerprint(source_view), "source_message_count": len(source_messages),
            "complete_source": not omitted and (not previous or previous["complete_source"]),
            "omitted": omitted or bool(previous and previous.get("omitted")),
        }
        self.state["summary_attempt"] = {"source_fingerprint": fingerprint, "through": boundary, "status": "succeeded"}
        self.state.pop("fallback", None)
        self._save()
        return self.state["summary"]

    def _request_budget(self, profile, view, tools, output_limit, automatic_output, overrides):
        spec = model_context.resolve_spec(profile, overrides)
        budget = model_context.measure_request(profile, view, tools, output_limit, overrides)
        if automatic_output and budget.needs_compaction:
            measure = lambda candidate: model_context.request_tokens(profile, candidate, tools, overrides)
            input_limit = min(spec.window - budget.safety - 1024,
                              spec.max_input_tokens or spec.window)
            minimum = workflow_context.bounded_messages(view, budget=input_limit,
                run_id=self.run_id, node_id=self.node_id, measure=measure, minimum=True)
            minimum_tokens = measure(minimum)
            growth_input = (minimum_tokens * 4 + 2) // 3
            output_floor = min(output_limit, max(1024, spec.window // 8))
            preferred_output = max(output_floor, spec.window - budget.safety - growth_input)
            output_limit = min(output_limit, preferred_output,
                               spec.window - budget.safety - minimum_tokens - 256)
            budget = model_context.measure_request(profile, view, tools, output_limit, overrides)
        return output_limit, budget

    def prepare(self, profile, messages, tools, output_limit):
        self._restore_receipts(messages)
        overrides = self._overrides(profile)
        spec = model_context.resolve_spec(profile, overrides)
        automatic_output = output_limit is None
        output_limit = (model_context.automatic_output_limit(profile, overrides) if automatic_output
                        else min(output_limit, spec.max_output or output_limit))
        summary = self._valid_summary(messages)
        view = self._protocol_view(profile, self._view(messages, summary))
        initial_budget = model_context.measure_request(profile, view, tools, output_limit, overrides)
        view = workflow_context.source_messages(view, max(1024, initial_budget.target_tokens - 256),
            run_id=self.run_id, node_id=self.node_id,
            measure=lambda candidate: model_context.request_tokens(profile, candidate, tools, overrides))
        output_limit, budget = self._request_budget(profile, view, tools, output_limit, automatic_output, overrides)
        if budget.needs_compaction:
            summary = self._compact(profile, messages, budget, overrides, summary)
            view = self._protocol_view(profile, self._view(messages, summary))
            view = workflow_context.source_messages(view, max(1024, budget.target_tokens - 256),
                run_id=self.run_id, node_id=self.node_id,
                measure=lambda candidate: model_context.request_tokens(profile, candidate, tools, overrides))
            output_limit, budget = self._request_budget(profile, view, tools, output_limit, automatic_output, overrides)
            fit_limit = budget.target_tokens
            if automatic_output:
                hard_limit = max(1, budget.available_input_tokens - 256)
                measure = lambda candidate: model_context.request_tokens(profile, candidate, tools, overrides)
                recent = workflow_context.bounded_messages(view, budget=hard_limit,
                    run_id=self.run_id, node_id=self.node_id, measure=measure, recent_only=True)
                fit_limit = max(fit_limit, measure(recent))
            view = self._fit(profile, view, tools, budget, fit_limit, overrides)
        measured = model_context.measure_request(profile, view, tools, output_limit, overrides)
        if not measured.fits:
            raise ValueError("Prepared workflow context exceeds the model input window")
        metadata = asdict(measured)
        prepared_profile = deepcopy(profile)
        prepared_profile["_context_budget"] = metadata
        prepared_profile["_usage_context"] = {**profile.get("_usage_context", {}), "context_budget": metadata}
        self.state["last_budget"] = metadata
        self._save()
        return PreparedRequest(prepared_profile, deepcopy(view), measured)

    def recover_overflow(self, prepared, tools):
        """Make one strictly smaller provider view; never infer a new model capacity."""
        profile = prepared.profile
        overrides = self._overrides(profile)
        limit = min(prepared.budget.target_tokens, prepared.budget.input_tokens * 3 // 4)
        try:
            view = workflow_context.bounded_messages(prepared.messages, budget=limit,
                run_id=self.run_id, node_id=self.node_id,
                measure=lambda messages: model_context.request_tokens(profile, messages, tools, overrides))
        except ValueError:
            return None
        tokens = model_context.request_tokens(profile, view, tools, overrides)
        if tokens >= prepared.budget.input_tokens:
            return None
        budget = replace(prepared.budget, input_tokens=tokens, fits=True,
                         needs_compaction=tokens >= prepared.budget.trigger_tokens)
        metadata = {**asdict(budget), "overflow_recovery": 1}
        candidate = deepcopy(profile)
        candidate["_context_budget"] = metadata
        candidate["_usage_context"] = {**profile.get("_usage_context", {}), "context_budget": metadata}
        self.state["last_budget"] = metadata
        self._save()
        return PreparedRequest(candidate, view, budget)

    def recover_protocol(self, prepared, tools):
        """Retry a copy using visible history after provider-owned state is rejected."""
        view = deepcopy(prepared.messages)
        if not _drop_responses_protocol(view):
            return None
        profile = prepared.profile
        budget = model_context.measure_request(profile, view, tools, prepared.budget.output_limit,
                                               self._overrides(profile))
        if not budget.fits:
            return None
        metadata = {**asdict(budget), 'protocol_recovery': 1}
        candidate = deepcopy(profile)
        candidate['_context_budget'] = metadata
        candidate['_usage_context'] = {**profile.get('_usage_context', {}), 'context_budget': metadata}
        return PreparedRequest(candidate, view, budget)

    def accepted(self, prepared):
        if prepared.profile.get('_context_budget', {}).get('protocol_recovery') == 1:
            identity = model_context.endpoint_identity(prepared.profile)
            compatible = self.state.setdefault('plain_responses', [])
            if identity not in compatible:
                compatible.append(identity)
        self.state["progress"] = workflow_progress.observe(self.state["progress"], prepared.messages)
        self._save()

    def result(self, name, args, result):
        if name not in ("project_artifact_write", "project_resource_write", "paper_evidence_record",
                        "paper_search", "paper_abstracts", "paper_evidence_get") or not isinstance(result, dict):
            return
        updated = workflow_progress.observe_result(self.state["progress"], name, args, result)
        if updated == self.state["progress"]:
            return
        self.state["progress"] = updated
        self._save()
