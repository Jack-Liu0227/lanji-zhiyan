"""Workflow definitions are persistent DAGs with real task/step-backed state."""
import copy
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from workflow import WorkflowDB, now
import workflow_layout as layouts


class WorkflowLayoutTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rw-layout-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "test.sqlite3"
        self.db = WorkflowDB(self.path)
        self.addCleanup(self.db.close)
        self.tid = self.db.create_task("Workflow test")

    def call(self, method, endpoint="", body=None, query=None):
        return layouts.route(method, "/api/workflow/layouts" + endpoint, body, query or {}, self.db)

    def definition(self):
        return {"name": "Two parallel inputs and review", "description": "Test",
                "nodes": [{"id": "a", "step": "search", "label": "Search"},
                          {"id": "b", "step": "outline", "label": "Outline"},
                          {"id": "c", "step": "review", "label": "Review"}],
                "edges": [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}],
                "order": ["a", "b", "c"]}

    def test_additive_seed_is_idempotent_and_preserves_projects(self):
        self.assertEqual(len(self.call("GET")["layouts"]), 3)
        self.assertEqual(len(self.call("GET")["layouts"]), 3)
        self.assertEqual(self.db.summary(self.tid)["task"]["title"], "Workflow test")
        self.assertIsNone(layouts.route("GET", "/unmatched", None, {}, self.db))

    def test_definition_and_project_selection_survive_reopen(self):
        saved = self.call("POST", "/save", self.definition())["layout"]
        self.call("POST", "/select", {"task_id": self.tid, "layout_id": saved["id"], "node_id": "b"})
        with WorkflowDB(self.path) as reopened:
            state = layouts.route("GET", "/api/workflow/layouts/state", None, {"task_id": [self.tid]}, reopened)
        self.assertEqual(state["layout"]["id"], saved["id"])
        self.assertEqual(state["selected_node"], "b")
        self.assertEqual(state["layout"]["edges"], self.definition()["edges"])
        other = self.db.create_task("Separate project")
        self.assertNotEqual(self.call("GET", "/state", query={"task_id": other})["layout"]["id"], saved["id"])

    def test_invalid_steps_duplicate_ids_edges_and_cycles_are_rejected(self):
        invalid = []
        data = self.definition(); data["nodes"][0]["step"] = "shell"; invalid.append(data)
        data = self.definition(); data["nodes"][1]["id"] = "a"; invalid.append(data)
        data = self.definition(); data["nodes"][1]["step"] = "search"; invalid.append(data)
        data = self.definition(); data["edges"].append({"source": "missing", "target": "a"}); invalid.append(data)
        data = self.definition(); data["edges"].append({"source": "c", "target": "a"}); invalid.append(data)
        data = self.definition(); data["order"] = ["c", "a", "b"]; invalid.append(data)
        data = self.definition(); data["order"] = ["a", "b", "b"]; invalid.append(data)
        data = self.definition(); data["edges"].append(data["edges"][0]); invalid.append(data)
        data = self.definition(); data["edges"].append({"source": "a", "target": "a"}); invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.call("POST", "/save", data)

    def test_graph_state_maps_steps_latest_status_and_dependencies(self):
        saved = self.call("POST", "/save", self.definition())["layout"]
        self.call("POST", "/select", {"task_id": self.tid, "layout_id": saved["id"]})
        state = self.call("GET", "/state", query={"task_id": self.tid})
        self.assertEqual([n["status"] for n in state["nodes"]], ["ready", "ready", "waiting"])
        self.assertEqual([n["level"] for n in state["nodes"]], [0, 0, 1])
        self.db.log_step(self.tid, "search", "running", "Requesting articles")
        self.db.log_step(self.tid, "outline", "done", "Outline saved")
        state = self.call("GET", "/state", query={"task_id": self.tid})
        self.assertEqual([n["status"] for n in state["nodes"]], ["running", "done", "waiting"])
        self.db.log_step(self.tid, "search", "done", "Found articles")
        state = self.call("GET", "/state", query={"task_id": self.tid})
        self.assertEqual([n["status"] for n in state["nodes"]], ["done", "done", "ready"])
        self.db.log_step(self.tid, "review", "failed", "Provider rejected the request")
        node = self.call("GET", "/state", query={"task_id": self.tid})["nodes"][2]
        self.assertEqual(node["status"], "failed")
        self.assertEqual(node["summary"], "Provider rejected the request")
        self.assertEqual(node["source"], "steps")

    def test_team_queue_and_scoped_chat_supply_actual_evidence(self):
        self.call("GET")
        stamp = now()
        self.db.db.execute("INSERT INTO dl_queue VALUES (?,?,?,?,?,?)", (self.tid, "result", "downloading", "", "", stamp))
        self.db.add_message(self.tid, "assistant", "An outline", [], scope="outline")
        self.db.db.commit()
        state = self.call("GET", "/state", query={"task_id": self.tid})
        nodes = {n["step"]: n for n in state["nodes"]}
        self.assertEqual(nodes["download"]["status"], "running")
        self.assertEqual(nodes["download"]["source"], "dl_queue")
        self.assertEqual(nodes["outline"]["source"], "chat")
        self.db.db.execute("INSERT INTO team_members (id,task_id,name,role,step,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                           ("member", self.tid, "Writer", "member", "write", stamp, stamp))
        self.db.db.execute("INSERT INTO team_tasks (id,task_id,member_id,title,prompt,status,created_at,started_at) VALUES (?,?,?,?,?,?,?,?)",
                           ("job", self.tid, "member", "Draft manuscript", "write", "running", now(), now()))
        self.db.db.commit()
        node = next(n for n in self.call("GET", "/state", query={"task_id": self.tid})["nodes"] if n["step"] == "write")
        self.assertEqual(node["status"], "running")
        self.assertEqual(node["record_id"], "job")

    def test_edit_replaces_removed_selection_and_delete_rebinds(self):
        saved = self.call("POST", "/save", self.definition())["layout"]
        self.call("POST", "/select", {"task_id": self.tid, "layout_id": saved["id"], "node_id": "b"})
        edited = copy.deepcopy(saved)
        edited["nodes"] = edited["nodes"][:1]; edited["edges"] = []; edited["order"] = ["a"]
        self.call("POST", "/save", edited)
        self.assertEqual(self.call("GET", "/state", query={"task_id": self.tid})["selected_node"], "a")
        self.call("POST", "/delete", {"id": saved["id"]})
        self.assertNotEqual(self.call("GET", "/state", query={"task_id": self.tid})["layout"]["id"], saved["id"])

    def test_project_node_and_layout_selection_must_exist(self):
        self.call("GET")
        for body in ({"task_id": "missing", "layout_id": "builtin.workflow.research"},
                     {"task_id": self.tid, "layout_id": "builtin.workflow.research", "node_id": "missing"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.call("POST", "/select", body)
        with self.assertRaises(KeyError):
            self.call("POST", "/select", {"task_id": self.tid, "layout_id": "missing"})

    def test_evidence_compares_actual_times_not_iso_wall_clock_strings(self):
        self.call("GET")
        earlier = "2026-10-17T09:00:00+08:00"
        later = "2026-10-17T02:00:00+00:00"
        self.db.log_step(self.tid, "review", "failed", "Newer failed run")
        self.db.db.execute("UPDATE steps SET created_at=? WHERE task_id=?", (later, self.tid))
        self.db.db.execute("INSERT INTO team_members (id,task_id,name,role,step,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                           ("reviewer", self.tid, "Reviewer", "member", "review", earlier, earlier))
        self.db.db.execute("INSERT INTO team_tasks (id,task_id,member_id,title,prompt,status,created_at,ended_at) VALUES (?,?,?,?,?,?,?,?)",
                           ("old-job", self.tid, "reviewer", "Old review", "review", "done", earlier, earlier))
        self.db.db.commit()
        node = next(n for n in self.call("GET", "/state", query={"task_id": self.tid})["nodes"] if n["step"] == "review")
        self.assertEqual(node["status"], "failed")
        self.assertEqual(node["source"], "steps")
        # Legacy team timestamps are local naive ISO strings, not UTC strings.
        local_earlier = datetime.fromisoformat(earlier).astimezone().replace(tzinfo=None).isoformat()
        self.db.db.execute("UPDATE team_tasks SET ended_at=? WHERE id='old-job'", (local_earlier,))
        self.db.db.commit()
        node = next(n for n in self.call("GET", "/state", query={"task_id": self.tid})["nodes"] if n["step"] == "review")
        self.assertEqual(node["source"], "steps")

    def test_project_deletion_cleans_binding_but_preserves_workflow_definition(self):
        saved = self.call("POST", "/save", self.definition())["layout"]
        self.call("POST", "/select", {"task_id": self.tid, "layout_id": saved["id"]})
        self.db.delete_task(self.tid)
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM project_workflow_layouts WHERE task_id=?", (self.tid,)).fetchone()[0], 0)
        self.assertTrue(any(layout["id"] == saved["id"] for layout in self.call("GET")["layouts"]))


if __name__ == "__main__":
    unittest.main()
