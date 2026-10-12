# -*- coding: utf-8 -*-
"""多团队（2 固定 + 3 机动）+ 固定团队预置成员 + 助手=模型 的自检。

    python -m unittest test_teams -v
"""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pathlib
import tempfile
import unittest

_p = pathlib.Path(tempfile.gettempdir()) / "teams_unit.sqlite3"
os.environ["RESEARCH_WORKBENCH_DB"] = str(_p)

import team  # noqa: E402
import workflow  # noqa: E402

team.DB_PATH = _p


class TeamsTest(unittest.TestCase):
    def setUp(self) -> None:
        if _p.exists():
            _p.unlink()
        team.DB_PATH = _p
        with workflow.WorkflowDB(_p) as db:
            self.db = db

    def _t(self, tid):
        with workflow.WorkflowDB(_p) as db:
            return db.team(tid)

    # ---------- 固定位 ----------
    def test_01_two_fixed_seeded_idempotent(self):
        a = team.ensure_defaults()
        fixed = [x for x in a if x["kind"] == "fixed"]
        self.assertEqual([x["name"] for x in fixed], ["论文精读", "论文写作"])
        b = team.ensure_defaults()
        self.assertEqual(len(b), 2)                       # 幂等，不重复播
        self.assertEqual(team.teams()["fixed"], 2)
        with self.assertRaises(ValueError):
            team.new_team("fixed")                        # 第 3 个固定位不给

    def test_01b_fixed_teams_preseeded_roster(self):
        """两个固定团队各预置 3 个成员（按助手名字找），再调一次不会重复拉人。"""
        team.ensure_defaults()
        want = {"论文精读": ["分块员", "图表解读员", "论文精读"],
                "论文写作": ["结构规划员", "论文写作", "论文审查"]}
        for t in team.teams()["teams"]:
            if t["kind"] != "fixed":
                continue
            names = [m["name"] for m in team.picks(t["id"])]
            self.assertEqual(names, want[t["name"]], t["name"])
            for m in team.picks(t["id"]):
                self.assertTrue(m["assistant_id"])        # 每个成员都挂在助手上
                self.assertGreaterEqual(m["color"], 1)    # Leader 占 0 号色
        again = [m["name"] for t in team.teams()["teams"] if t["name"] == "论文精读"
                 for m in team.picks(t["id"])]
        self.assertEqual(again, want["论文精读"])          # 幂等：还是 3 个

    # ---------- 机动位 ----------
    def test_02_three_temp_then_full(self):
        t1 = team.new_team("temp")
        t2 = team.new_team("temp")
        t3x = team.new_team("temp")
        self.assertEqual(team.teams()["temp"], 3)
        with self.assertRaises(ValueError) as e:
            team.new_team("temp")                         # 三个都在用 → 不给
        self.assertIn("都在用", str(e.exception))
        team.release(t1["id"])                            # t1 用完 → 释放
        self.assertEqual(team.teams()["temp"], 3)
        t3 = team.new_team("temp")                        # 用完的自动回收 → 又能建
        self.assertNotEqual(t3["id"], t1["id"])
        ids = [x["id"] for x in team.teams()["teams"]]
        self.assertNotIn(t1["id"], ids)                   # 旧的已收掉
        self.assertIn(t3["id"], ids)

    def test_03_release_frees_members(self):
        with workflow.WorkflowDB(_p) as db:
            db.assistant_save("w-1", name="甲")
        t = team.new_team("temp")
        team.add_assistant(t["id"], "w-1")
        self.assertEqual(len(team.picks(t["id"])), 1)
        team.release(t["id"])
        self.assertEqual(len(team.picks(t["id"])), 0)     # 成员让出来了
        self.assertEqual(self._t(t["id"])["status"], "done")

    def test_04_total_cap_five(self):
        team.ensure_defaults()
        team.new_team("temp")
        team.new_team("temp")
        team.new_team("temp")
        self.assertEqual(len(team.teams()["teams"]), 5)
        team.release([x for x in team.teams()["teams"] if x["kind"] == "temp"][0]["id"])
        with self.assertRaises(ValueError):
            team.new_team("fixed")

    # ---------- 解散 ----------
    def test_05_fixed_cannot_dissolve(self):
        fx = [x for x in team.ensure_defaults() if x["kind"] == "fixed"][0]
        with self.assertRaises(ValueError):
            team.dissolve(fx["id"])
        t = team.new_team("temp")
        self.assertTrue(team.dissolve(t["id"])["ok"])
        self.assertIsNone(self._t(t["id"]))

    def test_06_rename(self):
        t = team.new_team("temp")
        team.rename_team(t["id"], "换名字")
        self.assertEqual(self._t(t["id"])["name"], "换名字")
        with self.assertRaises(ValueError):
            team.rename_team(t["id"], "  ")

    # ---------- 助手 = 模型接入 ----------
    def test_07_agent_profile_follows_assistant_model(self):
        with workflow.WorkflowDB(_p) as db:
            db.add_model("激活的", "http://127.0.0.1:1/v1", "active-model", "k", activate=True)
            m2 = db.add_model("deepseek", "https://api.deepseek.com/v1", "deepseek-chat", "k2")
            db.assistant_save("a-ds", name="deepseek 助手", model_id=m2["id"])
            cid = db.create_chat("接了 deepseek 的对话")
            # 没绑助手 → 用激活模型
            self.assertEqual(workflow.agent_profile(db, cid)["model"], "active-model")
            # 绑了助手 → 用助手接的那个模型（这就是「deepseek 接进去就是个助手」）
            db.set_chat_assistant(cid, "a-ds")
            self.assertEqual(workflow.agent_profile(db, cid)["model"], "deepseek-chat")
            self.assertEqual(workflow.agent_profile(db, cid)["name"], "deepseek")
            # 助手没指定模型 → 退回激活模型
            db.assistant_save("a-none", name="没指模型")
            db.set_chat_assistant(cid, "a-none")
            self.assertEqual(workflow.agent_profile(db, cid)["model"], "active-model")

    def test_08_member_model_shown(self):
        with workflow.WorkflowDB(_p) as db:
            db.add_model("deepseek", "https://api.deepseek.com/v1", "deepseek-chat", "k")
            db.assistant_save("a-ds", name="ds 助手", model_id=db.models()[0]["id"])
        t = team.new_team("temp")
        team.add_assistant(t["id"], "a-ds")
        st = team.chat_state(t["id"])
        self.assertEqual(st["team"]["id"], t["id"])
        mem = [m for m in st["members"] if m["role"] == "member"][0]
        self.assertEqual(mem["model"], "deepseek")        # 卡片上能看到它背后是哪个模型
        self.assertEqual(len(st["members"]), 2)           # Leader + 1 人


    # ---------- 老库迁移 ----------
    def test_09_legacy_placeholders_handed_over(self):
        """老库里的空占位「团队 1/2/3」：前两个被改名接手（并预置成员），多的转成「已收工」的机动，记录不删。"""
        for i in (1, 2, 3):
            with workflow.WorkflowDB(_p) as db:
                db.team_add("团队 %d" % i, "fixed")
        team.ensure_defaults()
        ts = team.teams()["teams"]
        fixed = [x["name"] for x in ts if x["kind"] == "fixed"]
        self.assertEqual(sorted(fixed), ["论文写作", "论文精读"])
        left = [x for x in ts if x["kind"] == "temp" and x["name"].startswith("团队")]
        self.assertEqual(len(left), 1)                    # 多出来的那一个转成了机动位
        self.assertEqual(left[0]["status"], "done")       # 标已收工，等着被回收
        self.assertEqual(team.teams()["fixed"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
