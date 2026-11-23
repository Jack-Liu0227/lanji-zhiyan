"""交付后沉淀（wrap_up）与 skill 使用计数：闸门、落盘、冷资产。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import capabilities
import wrap_up
import workflow

MODEL = {"base_url": "http://localhost:1", "model": "m", "api": "openai-completions"}
GOOD = {"journal": "写完第二节，结论 A 成立。",
        "lesson": "写正文前必须先跑 lineage 校验",
        "craft": {"name": "Paper Section", "description": "写论文小节时用",
                  "body": "1. 先读框架文件\n2. 判据：lineage 校验退出码 0"}}
JUNK = {"journal": "写完了。", "lesson": "", "craft": {"name": "x", "description": "d",
        "body": "这次任务要认真核对数据，保持严谨。"}}


class WrapUpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.db = workflow.WorkflowDB(self.dir / "test.sqlite3")
        self.task = {"id": "pt-12345678", "project_id": "proj-test", "title": "写第二节"}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_record(self, payload):
        run = {"output_snapshot": {"content": "正文", "files": [{"name": "第二节.md"}]},
               "checkpoint": {"reply": "已交付"}}
        with patch.object(workflow.WorkflowDB, "active_model", return_value=MODEL), \
                patch("workflow.chat_json", return_value=payload):
            return wrap_up.record(self.db, self.task, run, project_dir=str(self.dir))

    def test_gate_and_slug(self):
        self.assertFalse(wrap_up.craft_ok("这次任务要认真核对数据，保持严谨。"), "套话必须被挡")
        self.assertFalse(wrap_up.craft_ok("   "), "空正文必须被挡")
        self.assertTrue(wrap_up.craft_ok("先跑 `tools/check.bat`，判据：退出码 0"), "有判据词/命令要放行")
        self.assertTrue(wrap_up.craft_ok("x" * wrap_up.MIN_CRAFT), "够长的正文要放行")
        self.assertEqual(wrap_up.slug("Paper Outline v2", "f"), "paper-outline-v2")
        self.assertEqual(wrap_up.slug("论文框架", "task-abcd1234"), "task-abcd1234")

    def test_record_writes_memory_and_skill(self):
        out = self.run_record(GOOD)
        self.assertEqual(sorted(out["written"]), ["craft:paper-section", "journal", "lesson"])
        skill = self.dir / ".agents" / "skills" / "paper-section" / "SKILL.md"
        self.assertTrue(skill.is_file(), "配方要落成 SKILL.md")
        self.assertIn("description: 写论文小节时用", skill.read_text(encoding="utf-8"))
        rows = [dict(r) for r in self.db.db.execute("SELECT kind,text FROM memory WHERE source='wrap-up'")]
        self.assertEqual(sorted(r["kind"] for r in rows), ["constraint", "episode"])

    def test_junk_craft_is_dropped(self):
        out = self.run_record(JUNK)
        self.assertEqual(out["written"], ["journal"])
        self.assertEqual(out["dropped"], ["craft:套话"])
        self.assertFalse((self.dir / ".agents").exists(), "套话不许建 skill 目录")

    def test_same_journal_twice_is_deduped(self):
        self.run_record(GOOD)
        self.run_record(GOOD)
        count = self.db.db.execute("SELECT COUNT(*) FROM memory WHERE source='wrap-up' AND kind='episode'").fetchone()[0]
        self.assertEqual(count, 1, "同正文应只登记一次")

    def test_drain_settles_each_delivery_once(self):
        run = {"output_snapshot": {"content": "正文"}, "checkpoint": {"reply": "已交付"}}
        self.db.db.execute(
            "INSERT INTO project_task_events(id,task_id,task_run_id,kind,data,created_at) VALUES(?,?,?,?,?,?)",
            ("pte-1", "pt-1", "run-1", "delivered", "{}", datetime.now(timezone.utc).isoformat()))
        self.db.db.commit()
        with patch("project_tasks._task", return_value=dict(self.task, id="pt-1")), \
                patch("project_tasks.get_run", return_value=run), \
                patch.object(workflow.WorkflowDB, "base_dir", return_value=self.dir), \
                patch.object(workflow.WorkflowDB, "active_model", return_value=MODEL), \
                patch("workflow.chat_json", return_value=GOOD):
            first = wrap_up.drain(self.db, limit=5)
            second = wrap_up.drain(self.db, limit=5)
        self.assertEqual(first["done"], ["pt-1"])
        self.assertEqual(first["failed"], [])
        self.assertEqual(second["done"], [], "已经沉淀过的任务不许重跑")
        marks = [dict(r) for r in self.db.db.execute("SELECT kind,data FROM project_task_events WHERE kind='wrap-up'")]
        self.assertEqual(len(marks), 1, "审计行只该有一条")

    def test_skill_usage_marks_cold_skills(self):
        root = self.dir / ".agents" / "skills"
        for name in ("used-skill", "cold-skill"):
            (root / name).mkdir(parents=True)
            (root / name / "SKILL.md").write_text(
                "---\nname: %s\ndescription: d\n---\n\n正文\n" % name, encoding="utf-8")
        capabilities.touch_skill(self.db, "used-skill")
        rows = {item["id"]: item for item in capabilities.skills_usage(self.db, str(self.dir), days=30)["skills"]}
        self.assertEqual(rows["used-skill"]["hits"], 1)
        self.assertFalse(rows["used-skill"]["stale"])
        self.assertTrue(rows["cold-skill"]["stale"], "没读过的要算冷资产")
        self.assertFalse(any(item["stale"] for item in rows.values() if item["scope"] == "builtin"),
                         "内置技能不算冷资产")


if __name__ == "__main__":
    unittest.main()
