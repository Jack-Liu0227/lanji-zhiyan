# -*- coding: utf-8 -*-
"""专家自检：内置播种 / 只读不可删 / 保存更新 / 对话绑定 / 规则进 system。

    python -m unittest test_assistants -v
"""
from __future__ import annotations

import json
import os
import sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))
import pathlib
import tempfile
import unittest

_p = pathlib.Path(tempfile.gettempdir()) / "as_unit.sqlite3"
os.environ["RESEARCH_WORKBENCH_DB"] = str(_p)

import workflow  # noqa: E402


class AssistantTest(unittest.TestCase):
    def setUp(self) -> None:
        if _p.exists():
            _p.unlink()
        self.db = workflow.WorkflowDB(_p)

    def tearDown(self) -> None:
        self.db.close()

    def test_01_builtin_seeded_and_idempotent(self):
        a = self.db.assistants()
        N = len(workflow.BUILTIN_ASSISTANTS)
        self.assertEqual(N, 16)
        self.assertEqual(len(a), N)
        self.assertTrue(all(x["source"] == "builtin" for x in a))
        ids = {x["id"] for x in a}
        names = [x["name"] for x in a]
        self.assertIn("文献检索员", names)
        for core_id in ("builtin-steward", "builtin-scheduler", "builtin-figure", "builtin-stats", "builtin-reviewer"):
            self.assertIn(core_id, ids)
        for retired_id in ("builtin-pony", "builtin-minutes", "builtin-tutor", "builtin-grant"):
            self.assertNotIn(retired_id, ids)
        for b in workflow.BUILTIN_ASSISTANTS:                         # 名册自检：别漏键/漏规则
            self.assertTrue(b.get("rules", "").strip(), b["id"])
            self.assertTrue(b.get("description", "").strip(), b["id"])
            self.assertGreaterEqual(len(b.get("prompts") or []), 1, b["id"])
        self.assertEqual(len({b["id"] for b in workflow.BUILTIN_ASSISTANTS}), N)   # id 不重复
        self.db.assistant_seed(workflow.BUILTIN_ASSISTANTS)          # 再播一次
        self.assertEqual(len(self.db.assistants()), N)                # 不重复
        self.assertTrue(all(json.loads(x["prompts"] or "[]") for x in a))   # 每个都有推荐问题

    def test_01b_migration_prunes_unreferenced_retired_builtins_only(self):
        import migrations
        now = workflow.now()
        for aid, name in (("builtin-pony", "Pony 模式"), ("builtin-minutes", "会议纪要")):
            self.db.db.execute(
                "INSERT INTO assistants (id,source,name,avatar,description,rules,prompts,model_id,enabled,sort_order,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, "builtin", name, "", "desc", "rules", "[]", "", 1, 0, now, now),
            )
        cid = self.db.create_chat("Keep referenced builtin")
        self.db.db.execute("UPDATE chats SET assistant_id='builtin-minutes' WHERE id=?", (cid,))
        self.db.db.execute("DELETE FROM schema_patch_runs WHERE name='prune_legacy_builtin_assistants'")
        self.db.db.commit()

        applied = migrations.run_migrations(self.db.db)
        self.assertEqual(applied, ["prune_legacy_builtin_assistants"])
        self.assertIsNone(self.db.assistant("builtin-pony"))
        self.assertIsNotNone(self.db.assistant("builtin-minutes"))

    def test_retired_builtin_with_user_edits_survives_named_upgrade(self):
        import migrations
        now = workflow.now()
        self.db.db.execute(
            "INSERT INTO assistants (id,source,name,avatar,description,rules,prompts,model_id,enabled,sort_order,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("builtin-pony", "builtin", "Pony", "", "desc", "rules", "[]", "", 1, 0, now, now),
        )
        self.db.db.commit()
        self.db.assistant_save("builtin-pony", rules="Preserve my research instructions")
        self.db.db.execute("DELETE FROM schema_patch_runs WHERE name='prune_legacy_builtin_assistants'")
        self.db.db.commit()
        self.assertEqual(migrations.run_migrations(self.db.db), ["prune_legacy_builtin_assistants"])
        self.assertEqual(self.db.assistant("builtin-pony")["rules"], "Preserve my research instructions")

    def test_02_builtin_editable_deletable_and_restorable(self):
        with self.assertRaises(ValueError):
            self.db.assistant_delete("builtin-steward")
        self.db.assistant_save("builtin-searcher", name="改了", reasoning="high", enabled=0)
        self.assertEqual(self.db.assistant("builtin-searcher")["name"], "改了")
        self.assertNotIn("builtin-searcher", {a["id"] for a in self.db.selectable_assistants()})

        self.db.assistant_delete("builtin-chartread")
        self.assertIsNone(self.db.assistant("builtin-chartread"))
        self.assertNotIn("builtin-chartread", {a["id"] for a in self.db.assistants()})
        # Re-seeding does not resurrect user-deleted built-in experts.
        self.db.assistant_seed(workflow.BUILTIN_ASSISTANTS)
        self.assertIsNone(self.db.assistant("builtin-chartread"))

        # mode="missing" restores deleted built-ins without overwriting active edits.
        res_missing = self.db.assistant_restore_defaults(mode="missing")
        self.assertEqual(res_missing["restored"], 1)
        self.assertIsNotNone(self.db.assistant("builtin-chartread"))
        self.assertEqual(self.db.assistant("builtin-searcher")["name"], "改了")

        # mode="reset" restores default fields while keeping user experts intact.
        self.db.assistant_save("a-keep", name="自建保留", rules="保持不变")
        res_reset = self.db.assistant_restore_defaults(mode="reset")
        self.assertGreaterEqual(res_reset["restored"], 1)
        self.assertEqual(self.db.assistant("builtin-searcher")["name"], "文献检索员")
        self.assertEqual(self.db.assistant("builtin-searcher")["enabled"], 1)
        self.assertEqual(self.db.assistant("a-keep")["name"], "自建保留")

    def test_02b_assistant_delete_guards_active_leader_and_schedule(self):
        import team as team_mod
        pid = self.db.create_task("护栏项目", "问题", "体系", "")
        t = self.db.team_add("测试团队", "team", project_id=pid)
        self.db.team_member_add(t["id"], "文献研究负责人", "leader", assistant_id="builtin-literature-lead")
        self.db.team_member_add(t["id"], "分块员", "member", assistant_id="builtin-chunker")
        with self.assertRaises(ValueError):
            self.db.assistant_delete("builtin-literature-lead")
        # Non-leader member is unbound cleanly on delete.
        self.db.assistant_delete("builtin-chunker")
        self.assertIsNone(self.db.assistant("builtin-chunker"))
        self.assertFalse(any(m["assistant_id"] == "builtin-chunker" for m in self.db.team_members(t["id"])))
        # Enabled schedule blocks deletion until disabled.
        with self.assertRaises(ValueError):
            self.db.assistant_delete("builtin-scheduler")
        # Unified team deletion works for all kinds including fixed/custom/temp.
        old_db_path = team_mod.DB_PATH
        team_mod.DB_PATH = _p
        try:
            fixed_team = self.db.team_add("原常驻样式", "fixed", project_id=pid)
            self.db.team_member_add(fixed_team["id"], "文献研究负责人", "leader", assistant_id="builtin-literature-lead")
            team_mod.delete_team(fixed_team["id"])
            self.assertNotIn(fixed_team["id"], {x["id"] for x in self.db.teams()})
            team_mod.dissolve(t["id"])
            self.assertNotIn(t["id"], {x["id"] for x in self.db.teams()})
        finally:
            team_mod.DB_PATH = old_db_path

    def test_03_user_crud(self):
        a = self.db.assistant_save("a-test1", name="我的专家", avatar="🧪", description="d",
                                   rules="只做检索", prompts=json.dumps(["问1", "问2"], ensure_ascii=False))
        self.assertEqual(a["source"], "user")
        a2 = self.db.assistant_save("a-test1", name="改名了", rules="只做检索 + 去重")
        self.assertEqual(a2["name"], "改名了")
        self.assertEqual(a2["rules"], "只做检索 + 去重")
        self.assertEqual(json.loads(a2["prompts"]), ["问1", "问2"])   # 没传的字段不动
        self.db.assistant_delete("a-test1")
        self.assertIsNone(self.db.assistant("a-test1"))

    def test_04_bind_to_chat(self):
        cid = self.db.create_chat("绑定测试")
        self.assertEqual(self.db.assistant_of_chat(cid), "")
        self.db.set_chat_assistant(cid, "builtin-searcher")
        self.assertEqual(self.db.assistant_of_chat(cid), "builtin-searcher")

    def test_05_rules_reach_system_ctx(self):
        """最要紧的一条：专家绑到对话后，它的名字与规则必须真的进 system。"""
        cid = self.db.create_chat("规则测试")
        self.db.set_chat_assistant(cid, "builtin-searcher")
        self.db.add_model("假模型", "http://127.0.0.1:1/v1", "fake", "sk", activate=True)
        seen = {}

        def fake(name, messages, tools=None, **kw):
            seen["msgs"] = messages
            return {"role": "assistant", "content": "收到", "tool_calls": []}

        old = workflow.chat_raw
        workflow.chat_raw = fake
        try:
            workflow.run_agent(self.db, cid, "帮我检索一下", publish=False)
        finally:
            workflow.chat_raw = old
        system = "\n".join(m["content"] for m in seen["msgs"] if m["role"] == "system")
        self.assertIn("【当前专家】", system)
        self.assertIn("文献检索员", system)
        self.assertIn("开放获取", system)          # 规则正文
        self.assertIn("智研管家", system)      # 原有 Leader 口径没被挤掉

    def test_06_unbound_chat_has_no_assistant_block(self):
        cid = self.db.create_chat("没绑专家")
        self.db.add_model("假模型", "http://127.0.0.1:1/v1", "fake", "sk", activate=True)
        seen = {}

        def fake(name, messages, tools=None, **kw):
            seen["msgs"] = messages
            return {"role": "assistant", "content": "收到", "tool_calls": []}

        old = workflow.chat_raw
        workflow.chat_raw = fake
        try:
            workflow.run_agent(self.db, cid, "在吗", publish=False)
        finally:
            workflow.chat_raw = old
        system = "\n".join(m["content"] for m in seen["msgs"] if m["role"] == "system")
        self.assertNotIn("【当前专家】", system)

    def test_prompt_upgrade_updates_only_prompt_fields_and_preserves_custom_fields(self):
        import workflow_prompts

        aid = "builtin-searcher"
        old_rules = "Previous shipped rules"
        old_prompts = ["Previous shipped question"]
        self.db.db.execute("UPDATE assistants SET rules=?,prompts=?,description='local description',model_id='model-kept',"
                           "fallback_model_ids='[\"fallback-kept\"]',reasoning='high',permission='read_only',enabled=0 WHERE id=?",
                           (old_rules, json.dumps(old_prompts, ensure_ascii=False), aid))
        for field, value in (("rules", old_rules), ("prompts", old_prompts)):
            self.db.db.execute(
                "INSERT OR REPLACE INTO assistant_prompt_state (assistant_id,field,revision,source_hash,custom,updated_at) "
                "VALUES (?,?,?,?,0,?)",
                (aid, field, "2026-11-27.1", self.db._assistant_prompt_hash(field, value), workflow.now()),
            )
        self.db.db.commit()

        preview = self.db.assistant_prompt_upgrade_preview()
        selected = next(item for item in preview["assistants"] if item["id"] == aid)
        self.assertEqual(selected["fields"]["rules"]["status"], "update")
        self.assertEqual(selected["fields"]["prompts"]["status"], "update")
        result = self.db.assistant_prompt_upgrade([aid])
        updated = self.db.assistant(aid)
        defaults = next(item for item in workflow_prompts.BUILTIN_ASSISTANTS if item["id"] == aid)
        self.assertEqual(updated["rules"], defaults["rules"])
        self.assertEqual(json.loads(updated["prompts"]), defaults["prompts"])
        self.assertEqual({item["field"] for item in result["updated"]}, {"rules", "prompts"})
        self.assertEqual(updated["description"], "local description")
        self.assertEqual(updated["model_id"], "model-kept")
        self.assertEqual(updated["fallback_model_ids"], '["fallback-kept"]')
        self.assertEqual(updated["reasoning"], "high")
        self.assertEqual(updated["permission"], "read_only")
        self.assertEqual(updated["enabled"], 0)

        self.db.assistant_save("builtin-chunker", rules="User-authored chunking rules")
        chunker_before = self.db.assistant("builtin-chunker")
        chunker_preview = next(item for item in self.db.assistant_prompt_upgrade_preview()["assistants"]
                               if item["id"] == "builtin-chunker")
        self.assertEqual(chunker_preview["fields"]["rules"]["status"], "custom")
        self.db.assistant_prompt_upgrade(["builtin-chunker"])
        self.assertEqual(self.db.assistant("builtin-chunker")["rules"], chunker_before["rules"])
        self.assertEqual(workflow_prompts.BUILTIN_ASSISTANT_PROMPT_REVISION,
                         self.db.assistant_prompt_upgrade_preview()["revision"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
