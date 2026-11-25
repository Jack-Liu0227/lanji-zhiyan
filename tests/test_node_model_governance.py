"""Tests for node-level model configuration, automatic failover, circuit breaker, preflight, and hot-swap resume."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    d = ROOT / "packages" / name
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))

import agent_settings
import execution_conversations
import model_health
import project_bindings
import project_task_execution as worker
import project_task_routes
import project_tasks as tasks
import runtime
import workflow
import workflow_execution as engine
import workflow_layout


def _http_error(status: int, code: str, message: str = "failure") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    return urllib.error.HTTPError("https://example.test/v1/chat/completions", status, message, {}, io.BytesIO(body))


class NodeModelGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="rw-model-gov-")
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "app.sqlite3"
        self.db = workflow.WorkflowDB(self.db_path)
        self.addCleanup(self.db.close)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.pid = self.db.create_task("Model Governance Project", base_dir=str(self.root))
        self.ui = tasks.CommandContext.ui(self.pid)

    def _add_provider_model(self, name: str, base_url: str, model: str, api: str = "openai-completions", api_key: str = "k"):
        p = agent_settings.route("POST", "/api/agent-settings/provider-save", {
            "name": name, "base_url": base_url, "api": api, "api_key": api_key,
        }, {}, self.db)["provider"]
        res = agent_settings.route("POST", "/api/agent-settings/provider-models", {
            "id": p["id"], "models": [model],
        }, {}, self.db)
        prof = next(m for m in res["models"] if m["provider_id"] == p["id"] and m["model"] == model)
        return p, prof

    def test_node_model_chain_priority_and_strict_policy(self):
        _, m_default = self._add_provider_model("P0", "https://p0.test/v1", "m-default")
        _, m_asst = self._add_provider_model("P1", "https://p1.test/v1", "m-assistant")
        _, m_node = self._add_provider_model("P2", "https://p2.test/v1", "m-node")
        _, m_fb = self._add_provider_model("P3", "https://p3.test/v1", "m-fallback")
        self.db.set_active(m_default["id"])
        self.db.assistant_save("exp-1", name="Expert", model_id=m_asst["id"], fallback_model_ids=[m_fb["id"]], enabled=1)

        node = {"id": "n1", "step": "search", "label": "Node 1",
                "model_id": m_node["id"], "fallback_model_ids": [m_fb["id"]], "reasoning": "high", "model_policy": "fallback"}
        cfg = agent_settings.runtime_config(self.db, self.pid, "exp-1", node=node)
        self.assertEqual(cfg["model_id"], m_node["id"])
        self.assertEqual(cfg["model_source"], "workflow_node")
        self.assertEqual(cfg["reasoning"], "high")
        chain = agent_settings.resolve_model_chain(self.db, cfg)
        self.assertEqual([m["id"] for m in chain], [m_node["id"], m_fb["id"], m_default["id"]])

        # Strict policy prohibits automatic fallback
        strict_cfg = {**cfg, "model_policy": "strict"}
        strict_chain = agent_settings.resolve_model_chain(self.db, strict_cfg)
        self.assertEqual([m["id"] for m in strict_chain], [m_node["id"]])

        # Health cooldown moves m_node behind healthy m_fb
        model_health.record_failure(self.db, m_node["id"], "no channel", error_kind="model_unavailable", error_code="get_channel_failed")
        reordered = agent_settings.resolve_model_chain(self.db, cfg)
        self.assertEqual([m["id"] for m in reordered], [m_fb["id"], m_default["id"], m_node["id"]])

    def test_automatic_failover_across_protocols_records_health_and_event(self):
        _, primary = self._add_provider_model("BadGW", "https://bad.test/v1", "broken-model", api="anthropic-messages")
        _, backup = self._add_provider_model("GoodGW", "https://good.test/v1", "healthy-model", api="openai-completions")
        self.db.set_active(primary["id"])
        self.db.assistant_save("node-expert", name="Node Expert", model_id=primary["id"], enabled=1, permission="full_access")
        binding = project_bindings.register(self.db, self.pid, "assistant", "node-expert")

        layout = workflow_layout.save_layout(self.db, {
            "name": "Failover Flow",
            "nodes": [{
                "id": "outline", "step": "outline", "label": "大纲生成",
                "executor_binding_id": binding["id"],
                "model_id": primary["id"], "fallback_model_ids": [backup["id"]],
                "review_required": False,
                "output_contract": {"required_files": ["outputs/outline.md"]},
            }],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        run = engine.create(self.db, self.pid, "Write outline")

        calls = []

        def mock_open(req, timeout=30):
            url = req.full_url
            calls.append((url, json.loads(req.data.decode("utf-8"))))
            if "bad.test" in url:
                raise _http_error(500, "get_channel_failed", "no channel for broken-model")
            payload = json.loads(req.data.decode("utf-8"))
            # Ensure cross-protocol internal _protocol metadata was stripped
            for msg in payload["messages"]:
                self.assertFalse(any(k.startswith("_") for k in msg))
            if not any(m.get("role") == "tool" for m in payload["messages"]):
                return io.BytesIO(json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "project_artifact_write", "arguments": json.dumps({
                            "path": "outputs/outline.md", "content": "# Outline\nRecovered via fallback model."
                        })}
                    }]}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }).encode("utf-8"))
            return io.BytesIO(json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "已完成大纲。"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            }).encode("utf-8"))

        with patch("workflow.open_url", side_effect=mock_open):
            completed = engine.advance(self.db, run["id"])

        self.assertEqual(completed["status"], "succeeded", completed["error"])
        self.assertTrue((self.root / "outputs/outline.md").is_file())
        self.assertEqual(model_health.get(self.db, primary["id"])["status"], "unavailable")
        self.assertTrue(model_health.get(self.db, primary["id"])["in_cooldown"])
        self.assertEqual(model_health.get(self.db, backup["id"])["status"], "healthy")

        # Verify model_fallback event persisted in execution conversation
        task_id = completed["settings"]["task_ids"]["outline"]
        rows = self.db.db.execute(
            "SELECT payload FROM execution_conversation_events WHERE task_id=?", (task_id,)
        ).fetchall()
        events = [json.loads(r["payload"]) for r in rows]
        fb_events = [e for e in events if e.get("type") == "model_fallback"]
        self.assertEqual(len(fb_events), 1)
        self.assertEqual(fb_events[0]["from_model"], "broken-model")
        self.assertEqual(fb_events[0]["to_model"], "healthy-model")

    def test_preflight_blocks_unusable_strict_node_and_allows_fallback(self):
        p_bad, m_bad = self._add_provider_model("DisabledGW", "https://disabled.test/v1", "dead-model")
        _, m_good = self._add_provider_model("GoodGW", "https://good.test/v1", "good-model")
        self.db.set_active(m_good["id"])
        self.db.assistant_save("preflight-exp", name="Preflight Expert", enabled=1)
        binding = project_bindings.register(self.db, self.pid, "assistant", "preflight-exp")

        strict_layout = workflow_layout.save_layout(self.db, {
            "name": "Strict Dead Flow",
            "nodes": [{"id": "n1", "step": "idea", "label": "Strict Node",
                       "executor_binding_id": binding["id"], "model_id": m_bad["id"], "model_policy": "strict"}],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": strict_layout["id"]})
        # Disable provider after binding
        agent_settings.route("POST", "/api/agent-settings/provider-save", {**p_bad, "enabled": False}, {}, self.db)

        with self.assertRaisesRegex(ValueError, "Strict Node"):
            engine.create(self.db, self.pid, "Should fail preflight")

        report = engine.route("GET", "/api/workflow/execution/preflight", {}, {"project_id": [self.pid]}, self.db)
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])

        # With fallback policy and global or node fallback, preflight passes with warning
        fb_layout = workflow_layout.save_layout(self.db, {
            "name": "Fallback Ready Flow",
            "nodes": [{"id": "n1", "step": "idea", "label": "Fallback Node",
                       "executor_binding_id": binding["id"], "model_id": m_bad["id"],
                       "fallback_model_ids": [m_good["id"]], "model_policy": "fallback"}],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": fb_layout["id"]})
        run = engine.create(self.db, self.pid, "Should pass preflight")
        self.assertTrue(run["settings"]["preflight"]["ok"])
        self.assertTrue(run["settings"]["preflight"]["warnings"])

    def test_credential_refresh_and_hotswap_resume_preserve_checkpoint(self):
        p_main, m_main = self._add_provider_model("MainGW", "https://main.test/v1", "main-model", api_key="bad-key")
        _, m_bak = self._add_provider_model("BakGW", "https://bak.test/v1", "bak-model", api_key="good-key")
        self.db.set_active(m_main["id"])
        self.db.assistant_save("resumer", name="Resumer", model_id=m_main["id"], enabled=1, permission="full_access")
        binding = project_bindings.register(self.db, self.pid, "assistant", "resumer")
        layout = workflow_layout.save_layout(self.db, {
            "name": "Resume Flow",
            "nodes": [{"id": "n0", "step": "task", "label": "节点A",
                       "executor_binding_id": binding["id"], "model_policy": "strict", "review_required": True}],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        run = engine.create(self.db, self.pid, "Test resume")
        task_id = run["settings"]["task_ids"]["n0"]

        # First run fails due to auth error inside real worker.execute, saving checkpoint & last_error_kind
        def fail_open(req, timeout=30):
            raise _http_error(401, "invalid_api_key", "invalid key")

        with patch("workflow.open_url", side_effect=fail_open):
            failed_run = engine.advance(self.db, run["id"])
        self.assertEqual(failed_run["status"], "failed")
        failed_task = tasks.get(self.db, task_id)
        self.assertEqual(failed_task["latest_run"]["checkpoint"].get("last_error_kind"), "quota_or_auth")

        # Fixing API key on the SAME endpoint does not block execution!
        # Queue first, then update key to verify load_frozen_model credential_refreshed on a queued run;
        # also verify resume without allow_model_switch works after key update.
        with patch.object(engine, "kick"):
            same_model_resume = engine.route("POST", "/api/workflow/execution/resume", {
                "id": run["id"], "node_id": "n0", "request_key": "retry-fixed-key",
            }, {}, self.db)["run"]
        self.assertEqual(same_model_resume["status"], "queued")
        agent_settings.route("POST", "/api/agent-settings/provider-save", {
            **p_main, "api_key": "fixed-key",
        }, {}, self.db)

        def succeed_after_fix(req, timeout=30):
            return io.BytesIO(json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "Recovered with fixed key."}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
            }).encode("utf-8"))

        with patch("workflow.open_url", side_effect=succeed_after_fix):
            resumed_exec = worker.execute(self.db, tasks.get(self.db, task_id)["latest_run_id"])
        self.assertEqual(resumed_exec["status"], "awaiting_review", resumed_exec["latest_run"]["error"])
        self.assertTrue(any(e["kind"] == "credential_refreshed" for e in resumed_exec["events"]))

        # Now test hot-swap resume preserving checkpoint when model is switched via resume API
        tasks.save_checkpoint(self.db, resumed_exec["latest_run_id"], phase="model", turn=2,
                              done_tools={"library_overview\x00{}": {"ok": True}},
                              messages=[{"role": "user", "content": "Resume me"}])
        self.db.db.execute("UPDATE project_tasks SET status='failed' WHERE id=?", (task_id,))
        self.db.db.execute("UPDATE project_task_runs SET status='failed' WHERE id=?", (resumed_exec["latest_run_id"],))
        self.db.db.execute("UPDATE workflow_runs SET status='failed' WHERE id=?", (run["id"],))
        self.db.db.commit()

        with patch.object(engine, "kick"):
            resumed = engine.route("POST", "/api/workflow/execution/resume", {
                "id": run["id"], "node_id": "n0", "model_id": m_bak["id"], "request_key": "swap-1",
            }, {}, self.db)["run"]
        self.assertEqual(resumed["status"], "queued")
        reloaded_task = tasks.get(self.db, task_id)
        self.assertEqual(reloaded_task["latest_run"]["checkpoint"]["turn"], 2)
        self.assertIn("library_overview\x00{}", reloaded_task["latest_run"]["checkpoint"]["done_tools"])
        self.assertEqual(reloaded_task["latest_run"]["checkpoint"]["model_switched_from"], "main-model")
        self.assertEqual(reloaded_task["latest_run"]["executor_snapshot"]["runtime_config"]["model_id"], m_bak["id"])

        # Switch model route directly on a failed/ready task (not while queued)
        self.db.db.execute("UPDATE project_tasks SET status='failed' WHERE id=?", (task_id,))
        self.db.db.execute("UPDATE project_task_runs SET status='failed' WHERE id=?", (reloaded_task["latest_run_id"],))
        self.db.db.commit()
        switched = project_task_routes.route("POST", "/api/project-tasks/switch-model", {
            "id": task_id, "project_id": self.pid, "model_id": m_main["id"],
        }, {}, self.db)["task"]
        self.assertEqual(switched["metadata"]["_model_override"]["model_id"], m_main["id"])

    def test_routing_policy_and_batch_check_endpoints(self):
        _, m1 = self._add_provider_model("P1", "https://p1.test/v1", "m1")
        _, m2 = self._add_provider_model("P2", "https://p2.test/v1", "m2")
        saved = agent_settings.route("POST", "/api/agent-settings/model-routing", {
            "fallback_model_ids": [m2["id"]],
            "auto_fallback": True,
            "cooldown_seconds": 180,
        }, {}, self.db)["routing"]
        self.assertEqual(saved["fallback_model_ids"], [m2["id"]])
        self.assertEqual(saved["cooldown_seconds"], 180)

        def fake_chat_raw(profile, *_a, **_kw):
            if profile["id"] == m1["id"]:
                raise runtime.ModelUnavailableError("no channel", status_code=404, error_code="model_not_found")
            return {"content": "OK", "tool_calls": []}

        with patch("workflow.chat_raw", side_effect=fake_chat_raw):
            res = agent_settings.route("POST", "/api/agent-settings/model-batch-test", {}, {}, self.db)
        by_id = {r["id"]: r for r in res["results"]}
        self.assertFalse(by_id[m1["id"]]["ok"])
        self.assertEqual(by_id[m1["id"]]["health"]["status"], "unavailable")
        self.assertTrue(by_id[m2["id"]]["ok"])
        self.assertEqual(by_id[m2["id"]]["health"]["status"], "healthy")

        agent_settings.route("POST", "/api/agent-settings/model-health-reset", {"id": m1["id"]}, {}, self.db)
        self.assertEqual(model_health.get(self.db, m1["id"])["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
