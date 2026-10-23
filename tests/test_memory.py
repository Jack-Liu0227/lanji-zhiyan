"""记忆体系验收：两层作用域、可改可删、检索、预算归档、纠正捕获、skill 自写。

全临时库/临时目录，不调真模型。
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))

import capabilities
import memory_store
import workflow


class MemorySystemTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rw-memory-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / "memory.sqlite3")
        self.addCleanup(self.db.close)
        sandbox = patch.object(memory_store, "_user_root", return_value=self.root / "user")
        sandbox.start()                       # 全局记忆.md 写到临时目录：别踩真实用户目录，也别被它污染
        self.addCleanup(sandbox.stop)
        self.a = self.db.create_task("Project A", "怎么掺杂", "O3-NFM", str(self.root / "A"))
        self.b = self.db.create_task("Project B")
        memory_store.ready(self.db)

    # ---- 作用域隔离
    def test_global_memory_reaches_every_project_but_project_memory_does_not_leak(self):
        memory_store.add(self.db, "回复先给结论", scope="global", kind="preference")
        memory_store.add(self.db, "A 的结论：Cu 掺杂最优", scope_id=self.a, kind="decision")
        self.assertIn("Cu 掺杂最优", self.db.memory_block(self.a))
        self.assertIn("回复先给结论", self.db.memory_block(self.a))
        other = self.db.memory_block(self.b)
        self.assertIn("回复先给结论", other)
        self.assertNotIn("Cu 掺杂最优", other)

    # ---- 改/删/归档
    def test_patch_remove_archive_roundtrip(self):
        row = memory_store.add(self.db, "导师要求中文稿不用小括号", scope_id=self.a, kind="constraint", importance=3)
        memory_store.patch(self.db, row["id"], text="导师要求中文稿不用小括号、不用分号")
        self.assertIn("分号", memory_store.search(self.db, "分号", self.a)[0]["text"])
        memory_store.patch(self.db, old_text="分号", scope_id=self.a, archived=True)
        self.assertNotIn("分号", self.db.memory_block(self.a))            # 归档 = 不常驻
        self.assertTrue(memory_store.search(self.db, "分号", self.a, include_archived=True))  # 还查得到
        memory_store.remove(self.db, row["id"])
        self.assertFalse(memory_store.search(self.db, "分号", self.a, include_archived=True))

    def test_write_time_dedupe_and_budget_archive(self):
        self.assertFalse(memory_store.add(self.db, "8G 显存只能小批量", scope_id=self.a).get("duplicate"))
        self.assertTrue(memory_store.add(self.db, "8G 显存只能小批量跑", scope_id=self.a).get("duplicate"))
        for i in range(50):
            memory_store.add(self.db, "条目%d：%s" % (i, chr(0x4e00 + i * 5) * 200), scope_id=self.b)
        out = memory_store.consolidate(self.db, self.b)
        self.assertLessEqual(out["stats"]["project"]["chars"], memory_store.BUDGET["project"])
        self.assertGreater(out["stats"]["project"]["archived"], 0)   # 写入路径上就压住了，不用等人手动整理

    # ---- 注入只放事实层：episode 不常驻，也不占注入预算
    def test_episode_stays_out_of_prompt_but_remains_searchable(self):
        memory_store.add(self.db, "对话经过：导师说要先做随机切分", scope_id=self.a, kind="episode")
        text = self.db.memory_block(self.a)
        self.assertNotIn("先做随机切分", text)                             # 不常驻注入
        self.assertIn("短期经过 1 条", text)                               # 但要报数，不静默丢
        self.assertTrue(memory_store.search(self.db, "随机切分", self.a))   # recall 查得到

    def test_episodes_do_not_eat_the_injection_budget(self):
        for i in range(40):
            memory_store.add(self.db, "经过%d：%s" % (i, chr(0x4e00 + i * 7) * 250),
                             scope_id=self.b, kind="episode")
        memory_store.add(self.db, "结论：Cu 掺杂最优", scope_id=self.b, kind="decision")
        text = self.db.memory_block(self.b)
        self.assertIn("Cu 掺杂最优", text)
        self.assertNotIn("超预算", text)                                   # 一万字符的经过都没挤进预算

    # ---- 全局记忆.md 手改要读得回来（否则下次写入把手改冲掉）
    def test_hand_edited_global_file_is_read_back_then_preserved(self):
        fake = self.root / "user"
        with patch.object(memory_store, "_user_root", return_value=fake):
            folder = fake / "记忆"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "全局记忆.md").write_text(
                "# 全局记忆\n\n- 以后中文稿一律不用小括号\n", encoding="utf-8")
            self.assertIn("不用小括号", self.db.memory_block(self.a))      # block → sync_files 收进来
            self.assertIn("不用小括号", memory_store.block(self.db, self.b))  # 全局就该到每个项目
            memory_store.dump(self.a)                                     # 重写文件不能把手改冲掉
            self.assertIn("不用小括号", (folder / "全局记忆.md").read_text(encoding="utf-8"))

    # ---- 旧库搬家
    def test_legacy_project_memory_rows_are_migrated_once(self):
        legacy = workflow.WorkflowDB(self.root / "legacy.sqlite3")
        self.addCleanup(legacy.close)
        tid = legacy.create_task("Old")
        legacy.db.execute("INSERT INTO project_memory VALUES ('x1',?,'long','旧结论',?)",
                          (tid, memory_store.now()))
        legacy.db.commit()
        rows = legacy.memory(tid, "long")                     # 老接口形状不变
        self.assertEqual([r["text"] for r in rows], ["旧结论"])
        self.assertEqual(rows[0]["kind"], "long")
        legacy.memory(tid, "long")                            # 二次调用不能重复搬
        self.assertEqual(len(legacy.memory(tid, "long")), 1)

    # ---- 工具面
    def test_memory_tool_applies_batch_and_filters_by_project(self):
        out = workflow._tool_impl("memory", self.db, self.a, {"ops": [
            {"action": "add", "text": "用户只有 8 周", "kind": "constraint", "importance": 3},
            {"action": "add", "text": "跨项目口径：图表用色盲安全配色", "scope": "global", "kind": "preference"},
            {"action": "remove", "old_text": "不存在的句子"},
        ]})
        self.assertEqual(out["applied"], 3)
        self.assertTrue(any("error" in r for r in out["results"]))
        self.assertEqual(out["stats"]["project"]["count"], 1)
        self.assertEqual(out["stats"]["global"]["count"], 1)
        hits = workflow._tool_impl("memory", self.db, self.b, {"query": "8 周"})
        self.assertEqual(hits["hits"], [])                    # B 看不到 A 的记忆

    def test_recall_searches_memory_and_past_turns(self):
        memory_store.add(self.db, "导师口径：结论句放最强表述", scope_id=self.a, kind="constraint")
        self.db.add_message(self.a, "user", "上次说过的纳电循环数据在哪", scope="")
        self.db.add_message(self.a, "assistant", "在笔记 纳电循环 那篇里", scope="")
        found = workflow._tool_impl("recall", self.db, self.a, {"query": "结论句"})
        self.assertTrue(found["memory"])
        found = workflow._tool_impl("recall", self.db, self.a, {"query": "纳电循环"})
        self.assertTrue(found["chats"])

    # ---- 纠正管道：用户当场定的规矩不等下轮
    def test_user_correction_is_captured_before_the_model_answers(self):
        with patch.object(workflow, "chat_raw", return_value={"content": "好"}):
            workflow.run_agent(self.db, self.a, "以后都按这个口径来，不要自己改数字", profile={})
        rows = [r for r in memory_store.search(self.db, "不要自己改数字", self.a)]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["kind"], "constraint")
        self.assertEqual(rows[0]["source"], "auto")

    # ---- 自我升级：模型写 skill → 下一轮被索引到
    def test_skill_write_lands_in_project_skills_and_shows_up_in_context(self):
        out = capabilities.write_skill("paper-figure-style", "画论文配图时用：顶刊配色与字号",
                                       "1. 300dpi PNG + SVG\n2. Okabe-Ito 配色", project_dir=str(self.root / "A"))
        self.assertTrue(out["created"])
        skill_file = Path(out["path"])
        self.assertTrue(skill_file.is_file())
        self.assertTrue(skill_file.is_relative_to((self.root / "A" / ".agents" / "skills").resolve()))
        context = capabilities.skill_context(self.db, "", str(self.root / "A"))
        self.assertIn("paper-figure-style", context)
        with self.assertRaises(ValueError):                    # 名字不许跳出技能目录
            capabilities.write_skill("../escape", "x", "y", project_dir=str(self.root / "A"))
        with self.assertRaises(ValueError):
            capabilities.write_skill("no-desc", "", "body", project_dir=str(self.root / "A"))

    # ---- 落盘副本双向：人改文件 → 读回库，不被下一次写入冲掉
    def test_manual_edit_of_memory_file_survives_later_writes(self):
        memory_store.add(self.db, "第一条", scope_id=self.a)
        path = self.db.base_dir(self.a) / "记忆" / "长期记忆.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n- 人工补的一条\n", encoding="utf-8")
        memory_store.add(self.db, "第二条", scope_id=self.a)
        self.assertIn("人工补的一条", self.db.memory_block(self.a))
        self.assertEqual(path.read_text(encoding="utf-8").count("人工补的一条"), 1)

    def test_deleting_a_memory_does_not_come_back_from_the_file_copy(self):
        row = memory_store.add(self.db, "临时结论：先按随机切分比较", scope_id=self.a)
        self.db.memory_block(self.a)                                  # 文件里已有这一行
        memory_store.remove(self.db, row["id"])
        memory_store.add(self.db, "另一条", scope_id=self.a)            # 下一次写盘会读文件
        self.assertEqual(memory_store.search(self.db, "随机切分", self.a, include_archived=True), [])
        self.assertNotIn("随机切分", (self.db.base_dir(self.a) / "记忆" / "长期记忆.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
