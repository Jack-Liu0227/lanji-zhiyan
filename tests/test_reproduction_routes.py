"""Tests for Section 5 RESTful API reproduction routes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import reproduction_routes
from run_manager import RunManager
from workflow import WorkflowDB
from _pdf_fixture import write_text_pdf


class ReproductionRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-test-repr-", ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test_repr.sqlite3"
        self.db = WorkflowDB(self.db_path)
        self.pdf_file = write_text_pdf(Path(self.tmp.name) / "sample.pdf", ["Page 1 content on lithium kinetics", "Page 2 data"])

    def tearDown(self):
        self.db.close()
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def call_route(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        res = reproduction_routes.route(method, path, body or {}, query or {}, self.db)
        self.assertIsNotNone(res, f"Route not matched: {method} {path}")
        return res

    def test_project_lifecycle_routes(self):
        created = self.call_route("POST", "/api/projects", {"title": "Reproduction Project", "question": "Kinetics question"})
        pid = created["task_id"]
        self.assertTrue(pid)

        summary = self.call_route("GET", f"/api/projects/{pid}/summary")
        self.assertEqual(summary["task"]["title"], "Reproduction Project")

        projects = self.call_route("GET", "/api/projects")
        self.assertTrue(any(p["id"] == pid for p in projects["projects"]))

        ingested = self.call_route("POST", f"/api/projects/{pid}/papers/ingest", {"path": str(self.pdf_file)})
        paper_id = ingested["paper"]["id"]
        self.assertTrue(paper_id)

        papers = self.call_route("GET", f"/api/projects/{pid}/papers")
        self.assertEqual(len(papers["papers"]), 1)

        ev = self.call_route("POST", f"/api/papers/{paper_id}/evidence", {
            "page_no": 1, "field": "composition", "value": "lithium", "excerpt": "lithium kinetics"
        })
        self.assertEqual(ev["evidence"]["field"], "composition")

        evidence_list = self.call_route("GET", f"/api/projects/{pid}/evidence")
        self.assertEqual(len(evidence_list["evidence"]), 1)

        exp = self.call_route("POST", f"/api/projects/{pid}/export", {"kind": "evidence", "target": "ev.csv"})
        self.assertTrue(exp["ok"])
        self.assertTrue(Path(exp["path"]).is_file())

    def test_chat_and_assistant_routes(self):
        chat_res = self.call_route("POST", "/api/chats", {"title": "Test Chat"})
        cid = chat_res["chat"]["id"]
        self.assertTrue(cid)

        chats = self.call_route("GET", "/api/chats")
        self.assertTrue(any(c["id"] == cid for c in chats["chats"]))

        asst = self.call_route("POST", "/api/assistants", {"name": "Test Assistant", "rules": "Always verify citations"})
        aid = asst["assistant"]["id"]
        self.assertTrue(aid)

        assistants = self.call_route("GET", "/api/assistants")
        self.assertTrue(any(a["id"] == aid for a in assistants["assistants"]))

        bind_res = self.call_route("POST", f"/api/chats/{cid}/bind", {"assistant_id": aid})
        self.assertEqual(bind_res["chat"]["assistant_id"], aid)

    def test_run_cancellation_and_events(self):
        run = RunManager.get_instance().create_run(task_id="proj-cancel", db=self.db)
        run.emit({"type": "progress", "pct": 20}, db=self.db)
        run.emit({"type": "progress", "pct": 60}, db=self.db)

        events_res = self.call_route("GET", f"/api/runs/{run.run_id}/events", query={"after": 1})
        self.assertEqual(len(events_res["events"]), 1)
        self.assertEqual(events_res["events"][0]["event_id"], 2)

        cancel_res = self.call_route("POST", f"/api/runs/{run.run_id}/cancel")
        self.assertTrue(cancel_res["cancelled"])
        self.assertEqual(run.status, "cancelled")

    def test_audit_events_route(self):
        self.db.record_audit_event("user-actor", assistant_id="a-1", tool="paper_search",
                                  project_id="proj-audit-route", input_hash="h1", outcome="ok", duration_ms=50)

        res = self.call_route("GET", "/api/audit/events", query={"project_id": "proj-audit-route"})
        self.assertEqual(len(res["audit_events"]), 1)
        self.assertEqual(res["audit_events"][0]["tool"], "paper_search")


if __name__ == "__main__":
    unittest.main()
