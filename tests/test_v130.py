"""Unit tests for v1.3.0 features: easyScholar, project TODOs, space search, and Zotero MCP."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import easyscholar
import project_todos
import space_service
import capabilities
import v130_routes


class V130FeaturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-v130-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        
        # Apply schema
        import migrations
        migrations.run_migrations(self.conn, db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_easyscholar_config_and_badge_parsing(self):
        # 1. Test get_config initially empty
        cfg = easyscholar.get_config(self.conn)
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["key_present"])

        # 2. Save config with SecretKey
        saved = easyscholar.save_config(self.conn, enabled=True, secret_key="test-secret-key-12345")
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["enabled"])
        self.assertTrue(saved["key_present"])

        # 3. Read back
        cfg2 = easyscholar.get_config(self.conn)
        self.assertTrue(cfg2["enabled"])
        self.assertTrue(cfg2["key_present"])
        self.assertEqual(cfg2["secret_key"], "test-secret-key-12345")

        # 4. Test badge parsing with sample easyScholar official response
        official = {
            "all": {
                "sciUp": "1",
                "sciUpTop": "1",
                "sciwarn": "0",
                "sci": "Q1",
                "sciif": "15.82",
                "ccf": "A",
                "pku": "1",
                "cssci": "1",
            }
        }
        custom = {
            "rankInfo": [{"uuid": "custom_001", "abbName": "DUFE", "threeRankText": "B"}],
            "rank": ["custom_001&&&3"],
        }
        badges = easyscholar.parse_badges(official, custom)
        labels = [b["label"] for b in badges]
        self.assertIn("中科院1区", labels)
        self.assertIn("TOP", labels)
        self.assertIn("JCR Q1", labels)
        self.assertIn("IF 15.82", labels)
        self.assertIn("CCF-A", labels)
        self.assertIn("北大核心", labels)
        self.assertIn("南大核心", labels)
        self.assertIn("DUFE B", labels)

    def test_project_todos_lifecycle(self):
        # 1. Add todo
        t1 = project_todos.add_todo(self.conn, "task_1", "测试待办1", "待办描述", "P0")
        self.assertEqual(t1["title"], "测试待办1")
        self.assertEqual(t1["status"], "TODO")
        self.assertEqual(t1["priority"], "P0")

        # 2. List todos
        todos = project_todos.list_todos(self.conn, "task_1")
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["id"], t1["id"])

        # 3. Toggle todo
        t_tog = project_todos.toggle_todo(self.conn, t1["id"])
        self.assertEqual(t_tog["status"], "DONE")

        # 4. Update todo
        t_up = project_todos.update_todo(self.conn, t1["id"], title="已修改待办", priority="P2")
        self.assertEqual(t_up["title"], "已修改待办")
        self.assertEqual(t_up["priority"], "P2")

        # 5. Import workflow stages
        created = project_todos.import_workflow_stages_as_todos(self.conn, "task_1")
        self.assertEqual(len(created), 10)

        # 6. Delete todo
        del_res = project_todos.delete_todo(self.conn, t1["id"])
        self.assertTrue(del_res["ok"])
        todos_after = project_todos.list_todos(self.conn, "task_1")
        self.assertEqual(len(todos_after), 10)

    def test_zotero_mcp_auto_registration(self):
        # Test capabilities ensure_zotero_mcp
        class MockDB:
            def __init__(self, conn):
                self.db = conn

        mock = MockDB(self.conn)
        res = capabilities.ensure_zotero_mcp(mock)
        self.assertTrue(res["ok"])
        server_txt = (res["server"].get("description", "") or "") + " " + (res["server"].get("args", [""])[0] if res["server"].get("args") else "")
        self.assertIn("zotero", server_txt.lower())

        # Check in config
        cfg = capabilities._config(mock)
        self.assertIn("zotero", cfg.get("mcpServers", {}))
        self.assertTrue(cfg["mcpServers"]["zotero"]["enabled"])

    def test_space_service_notes_and_search(self):
        # Insert a test research task
        self.conn.execute("INSERT INTO research_tasks (id, title, question, created_at, updated_at) VALUES ('t1', '钙钛矿电池研究', '如何提升光电转化效率', '2026-10-18T00:00:00', '2026-10-18T00:00:00')")
        # Insert a markdown note bound to t1
        self.conn.execute("INSERT INTO md_notes (id, title, content, task_id, paper_id, created_at, updated_at) VALUES ('n1', '钙钛矿实验记录', '测试了碘化铅在不同温度下的结晶行为', 't1', '', '2026-10-18T00:00:00', '2026-10-18T00:00:00')")
        self.conn.commit()

        class MockDB:
            def __init__(self, conn):
                self.db = conn
            def papers_of(self, tid):
                return [{"id": "p1", "title": "Perovskite Solar Cells Efficiency", "authors": "Zhang et al.", "year": "2024", "doi": "10.1038/s41586-024-001", "venue": "Nature", "abstract": "High efficiency perovskite cells"}]
            def notes(self, tid):
                return []
            def evidence(self, tid):
                return [{"id": "e1", "paper_id": "p1", "page": 3, "claim": "效率突破 25%", "quote": "Achieved 25.4% certified PCE"}]

        mock = MockDB(self.conn)
        # 1. Search in space
        res = space_service.search_project_space(mock, "t1", "perovskite")
        self.assertEqual(len(res["papers"]), 1)
        self.assertEqual(res["papers"][0]["id"], "p1")

        res_note = space_service.search_project_space(mock, "t1", "结晶")
        self.assertEqual(len(res_note["notes"]), 1)
        self.assertEqual(res_note["notes"][0]["id"], "n1")

        # 2. List project notes
        p_notes = space_service.list_project_notes(mock, "t1")
        self.assertEqual(len(p_notes), 1)
        self.assertEqual(p_notes[0]["title"], "钙钛矿实验记录")

        # 3. Get note detail
        detail = space_service.get_project_note_detail(mock, "n1")
        self.assertEqual(detail["title"], "钙钛矿实验记录")
        self.assertIn("碘化铅", detail["content"])

    def test_v130_routes_dispatch(self):
        self.conn.execute("INSERT OR IGNORE INTO research_tasks (id, title, created_at, updated_at) VALUES ('t1', '测试项目', datetime('now'), datetime('now'))")
        self.conn.execute("INSERT OR IGNORE INTO md_notes (id, title, content, task_id, created_at, updated_at) VALUES ('n1', '测试笔记', '正文', 't1', datetime('now'), datetime('now'))")
        self.conn.commit()

        class MockDB:
            def __init__(self, conn):
                self.db = conn
            def papers_of(self, tid):
                return []
            def notes(self, tid):
                return []
            def evidence(self, tid):
                return []

        mock = MockDB(self.conn)
        # 1. Test todos routes
        add_res = v130_routes.route("POST", "/api/todos/add", {"task_id": "t1", "title": "路由测试待办"}, {}, mock)
        self.assertIsNotNone(add_res)
        self.assertEqual(add_res["title"], "路由测试待办")
        todo_id = add_res["id"]

        list_res = v130_routes.route("GET", "/api/todos", {}, {"task_id": ["t1"]}, mock)
        self.assertEqual(len(list_res["todos"]), 1)

        tog_res = v130_routes.route("POST", "/api/todos/toggle", {"id": todo_id}, {}, mock)
        self.assertEqual(tog_res["status"], "DONE")

        del_res = v130_routes.route("POST", "/api/todos/delete", {"id": todo_id}, {}, mock)
        self.assertTrue(del_res["ok"])

        # 2. Test easyscholar routes
        cfg_res = v130_routes.route("POST", "/api/easyscholar/config", {"enabled": True, "secret_key": "sec_abc"}, {}, mock)
        self.assertTrue(cfg_res["ok"])

        cfg_get = v130_routes.route("GET", "/api/easyscholar/config", {}, {}, mock)
        self.assertTrue(cfg_get["enabled"])
        self.assertEqual(cfg_get["secret_key"], "sec_abc")

        rank_res = v130_routes.route("GET", "/api/easyscholar/rank", {}, {"journal": ["Nature"]}, mock)
        self.assertIn("badges", rank_res)

        # 3. Test Zotero MCP routes
        mcp_status = v130_routes.route("GET", "/api/zotero/mcp/status", {}, {}, mock)
        self.assertIn("registered", mcp_status)

        mcp_en = v130_routes.route("POST", "/api/zotero/mcp/enable", {}, {}, mock)
        self.assertTrue(mcp_en["ok"])

        # 4. Test space routes
        sp_search = v130_routes.route("GET", "/api/space/search", {}, {"task_id": ["t1"], "q": ["测试"]}, mock)
        self.assertIn("results", sp_search)

        sp_notes = v130_routes.route("GET", "/api/space/notes", {}, {"task_id": ["t1"]}, mock)
        self.assertIn("notes", sp_notes)

        sp_note = v130_routes.route("GET", "/api/space/note", {}, {"id": ["n1"]}, mock)
        self.assertEqual(sp_note["note"]["id"], "n1")

    def test_easyscholar_cache_and_expiration(self):
        # Insert a cached publication rank manually
        import json
        official = {"all": {"sciUp": "1", "sci": "Q1", "sciif": "50.5"}}
        custom = {"rankInfo": [], "rank": []}
        self.conn.execute(
            "INSERT OR REPLACE INTO easyscholar_cache (publication_name, official_rank, custom_rank, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            ("nature", json.dumps(official), json.dumps(custom))
        )
        self.conn.commit()

        # Query should hit cache and parse badges without network request
        res = easyscholar.query_publication_rank(self.conn, "Nature")
        self.assertTrue(res["ok"])
        labels = [b["label"] for b in res["badges"]]
        self.assertIn("中科院1区", labels)
        self.assertIn("JCR Q1", labels)
        self.assertIn("IF 50.5", labels)

    def test_managed_connection_and_transaction_context(self):
        import research_core
        # 1. ManagedConnection commits on successful block
        mc = research_core.ManagedConnection(self.db_path)
        with mc.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO background_jobs (id, task_id, status, created_at, updated_at) VALUES ('job-t1', 't1', 'running', datetime('now'), datetime('now'))")

        row = self.conn.execute("SELECT status FROM background_jobs WHERE id='job-t1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")

        # 2. ManagedConnection rolls back on exception
        with self.assertRaises(ZeroDivisionError):
            with mc.transaction() as conn:
                conn.execute("UPDATE background_jobs SET status='done' WHERE id='job-t1'")
                _ = 1 / 0

        row2 = self.conn.execute("SELECT status FROM background_jobs WHERE id='job-t1'").fetchone()
        self.assertEqual(row2["status"], "running")

        # 3. ResearchDB.transaction context
        db = research_core.ResearchDB(self.db_path)
        with db.transaction():
            db.db.execute("UPDATE background_jobs SET status='completed' WHERE id='job-t1'")
        db.close()

        row3 = self.conn.execute("SELECT status FROM background_jobs WHERE id='job-t1'").fetchone()
        self.assertEqual(row3["status"], "completed")

    def test_model_invoker_facade_and_custom_handler(self):
        import workflow
        # 1. Test registration of custom handler
        called_args = []
        def mock_handler(profile, messages, tools=None, **kwargs):
            called_args.append((profile, messages, tools, kwargs))
            return {"content": "mocked response", "tool_calls": []}

        workflow.ModelInvoker.register(mock_handler)
        res = workflow.ModelInvoker.call({"model": "test-m"}, [{"role": "user", "content": "hi"}], temperature=0.7)
        self.assertEqual(res["content"], "mocked response")
        self.assertEqual(len(called_args), 1)
        self.assertEqual(called_args[0][0]["model"], "test-m")
        self.assertEqual(called_args[0][3]["temperature"], 0.7)

        # 2. Test unregister
        workflow.ModelInvoker.unregister()
        self.assertIsNone(workflow.ModelInvoker._handler)

    def test_background_jobs_persistence_and_run_manager(self):
        import research_core
        import run_manager

        db = research_core.ResearchDB(self.db_path)
        # 1. Create background job
        job = db.create_background_job("bg-123", task_id="proj-1", status="running", progress=0.1, last_event_id=5)
        self.assertEqual(job["id"], "bg-123")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["last_event_id"], 5)

        # 2. Update background job
        updated = db.update_background_job("bg-123", status="suspended", progress=0.5, last_event_id=10)
        self.assertEqual(updated["status"], "suspended")
        self.assertEqual(updated["progress"], 0.5)
        self.assertEqual(updated["last_event_id"], 10)

        # 3. Test RunManager create and emit synchronization
        rm = run_manager.RunManager.get_instance()
        run = rm.create_run(task_id="proj-1", idempotency_key="idemp-bg-test", db=db)
        self.assertTrue(run.run_id)

        # Verify job was created in db
        bg_job = db.get_background_job(run.run_id)
        self.assertIsNotNone(bg_job)
        self.assertEqual(bg_job["status"], "running")

        # Emit an event and verify last_event_id updated
        ev_rec = run.emit({"type": "info", "message": "processing step 1"}, db=db)
        self.assertEqual(ev_rec["event_id"], 1)
        bg_after_emit = db.get_background_job(run.run_id)
        self.assertEqual(bg_after_emit["last_event_id"], 1)

        # Emit completion and verify job updated to done
        run.emit({"type": "done", "content": "finished"}, db=db)
        bg_done = db.get_background_job(run.run_id)
        self.assertEqual(bg_done["status"], "done")
        self.assertEqual(bg_done["progress"], 1.0)
        db.close()


if __name__ == "__main__":
    unittest.main()

