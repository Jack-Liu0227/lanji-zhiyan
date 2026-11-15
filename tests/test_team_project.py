# 团队挂项目：每项目一套常驻办公室 / 老库固定团队接手 / 管家组队（加急位）。
# python -m unittest test_team_project -v
from __future__ import annotations
import os
import pathlib
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

_DB = pathlib.Path(tempfile.gettempdir()) / "team_project_test.sqlite3"
import team                                  # noqa: E402
import workflow                              # noqa: E402

_OLD_DB = None
_TEMP = None
_PROJECTS = {}


def setUpModule():
    """本模块独占一个库：整个测试跑完再还原 team.DB_PATH（别的测试文件也在改它）。"""
    global _OLD_DB, _TEMP, _DB
    _TEMP = tempfile.TemporaryDirectory(prefix="rw-team-project-")
    _DB = pathlib.Path(_TEMP.name) / "app.sqlite3"
    _OLD_DB = team.DB_PATH
    team.DB_PATH = _DB
    with workflow.WorkflowDB(_DB) as db:
        for name in ("p1", "p2", "p3"):
            _PROJECTS[name] = db.create_task(name, base_dir=str(pathlib.Path(_TEMP.name) / name))


def tearDownModule():
    team.DB_PATH = _OLD_DB
    _TEMP.cleanup()


class TeamProjectTest(unittest.TestCase):
    def setUp(self):
        team.DB_PATH = _DB

    def test_01_each_project_gets_its_own_two_offices(self):
        self.assertEqual(team.teams(_PROJECTS["p1"])["teams"], [])
        team.ensure_defaults(_PROJECTS["p1"])
        d = team.teams(_PROJECTS["p1"])
        self.assertEqual(len(d["teams"]), 2, d)
        self.assertEqual(sorted(t["name"] for t in d["teams"]), sorted(["文献抽取", "文件撰写"]))
        self.assertTrue(all(t["kind"] == "fixed" and t["project_id"] == _PROJECTS["p1"] for t in d["teams"]))
        # 预置成员按专家名字找，找到几个算几个
        self.assertTrue(all(t["picks"] >= 3 for t in d["teams"]), [(t["name"], t["picks"]) for t in d["teams"]])
        self.assertTrue(all(t["leader"] for t in d["teams"]))
        # 再问一次不重复建
        self.assertEqual(len(team.teams(_PROJECTS["p1"])["teams"]), 2)

    def test_02_projects_dont_share_rooms(self):
        team.ensure_defaults(_PROJECTS["p1"])
        team.ensure_defaults(_PROJECTS["p2"])
        a = {t["id"] for t in team.teams(_PROJECTS["p1"])["teams"]}
        b = {t["id"] for t in team.teams(_PROJECTS["p2"])["teams"]}
        self.assertEqual(len(b), 2)
        self.assertFalse(a & b, "两个项目的办公室串了")

    def test_03_legacy_fixed_team_is_adopted_not_orphaned(self):
        with workflow.WorkflowDB(_DB) as db:                     # 老库里没绑项目的固定团队
            old = db.team_add("团队 1", "fixed")                  # 占位名也照接手
            db.team_member_add(old["id"], "老成员", "member", "", "")
        team.ensure_defaults(_PROJECTS["p3"])
        names = [t["name"] for t in team.teams(_PROJECTS["p3"])["teams"]]
        self.assertIn("文献抽取", names)
        with workflow.WorkflowDB(_DB) as db:
            hit = next(t for t in db.teams() if t["name"] == "文献抽取" and t["project_id"] == _PROJECTS["p3"])
            self.assertTrue(hit)
            rows = [m["name"] for m in db.team_members(hit["id"])]
            self.assertIn("老成员", rows, "接手时把人弄丢了")

    def test_04_form_team_creates_temp_office_with_purpose(self):
        r = team.form_team(_PROJECTS["p1"], "第 3 章配图，交三张对比图", ["图表设计", "统计把关", "论文审查"])
        self.assertTrue(r["purpose"].startswith("第 3 章配图"))
        self.assertEqual(len(r["added"]), 3, r)
        self.assertEqual(r["kind"], "temp")
        d = team.teams(_PROJECTS["p1"])
        temp = [t for t in d["teams"] if t["kind"] == "temp"]
        self.assertEqual(len(temp), 1)
        self.assertEqual(temp[0]["picks"], 3)
        self.assertEqual(d["temp"], 1)

    def test_05_temp_slots_are_capped_and_recycled(self):
        self.assertEqual(len(team.form_team(_PROJECTS["p1"], "第二队", ["图表设计"])["added"]), 1)
        self.assertEqual(len(team.form_team(_PROJECTS["p1"], "第三队", ["统计把关"])["added"]), 1)
        with self.assertRaises(ValueError):
            team.form_team(_PROJECTS["p1"], "第四队", ["论文审查"])
        # 收工一间 → 位子还回来
        with workflow.WorkflowDB(_DB) as db:
            t = [x for x in db.teams() if x["kind"] == "temp" and x["project_id"] == _PROJECTS["p1"]][0]
            db.team_update(t["id"], status="done")
        r = team.form_team(_PROJECTS["p1"], "第四队", ["论文审查"])
        self.assertIn("论文审查", r["added"])

    def test_06_form_team_rejects_empty_or_unknown(self):
        with self.assertRaises(ValueError):
            team.form_team(_PROJECTS["p1"], "", ["图表设计"])
        with self.assertRaises(ValueError):
            team.form_team(_PROJECTS["p2"], "没人", ["查无此人"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
