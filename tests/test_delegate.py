# -*- coding: utf-8 -*-
"""delegate（子 agent 委派）自检：独立上下文 / 只回摘要 / 真并行 / 可写产物但拿不到结构配置 / 不写父对话历史 / 超长摘要落盘。

    python -m unittest tests.test_delegate -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import workflow                                   # noqa: E402
from workflow import WorkflowDB, run_agent        # noqa: E402


class TestDelegate(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = WorkflowDB(Path(self.td.name) / "d.sqlite3")
        self.task = self.db.create_task("委派自检", "验证 delegate 的隔离与摘要", "无")
        self.db.add_model("假模型", "http://127.0.0.1:1/v1", "fake", "sk", activate=True)
        self.child_calls: list[dict] = []          # 每个子 agent 实际收到的东西
        self.parent_calls: list[dict] = []         # 父 agent 每次收到的 messages
        self._live = 0
        self.max_live = 0
        self._lock = threading.Lock()
        self._old_chat_raw = workflow.chat_raw

    def tearDown(self) -> None:
        workflow.chat_raw = self._old_chat_raw
        self.db.close()
        self.td.cleanup()

    def _fake(self, boom_title: str = "") -> None:
        """假模型：父第一轮要 delegate；子 agent（system 里带标记）直接给摘要。"""
        def fake_chat_raw(profile, messages, tools=None, **kw):
            system = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
            if workflow._DELEGATE_SYSTEM[:16] in system:                 # ===== 子 agent =====
                self.child_calls.append({"messages": list(messages),
                                         "tools": [t["function"]["name"] for t in (tools or [])]})
                with self._lock:
                    self._live += 1
                    self.max_live = max(self.max_live, self._live)
                import time
                time.sleep(0.15)                                          # 让并行的另一个子 agent 挤进来
                with self._lock:
                    self._live -= 1
                task_text = str(messages[-1].get("content") or "")
                if boom_title and boom_title in task_text:
                    raise RuntimeError("模拟子 agent 崩了")
                return {"role": "assistant", "content": "摘要[" + task_text[:14] + "]", "tool_calls": []}
            self.parent_calls.append({"messages": list(messages),                       # ===== 父 agent =====
                                      "tools": [t["function"]["name"] for t in (tools or [])]})
            if len(self.parent_calls) == 1:
                assert any(t["function"]["name"] == "delegate" for t in (tools or [])), "delegate 必须在工具表里"
                args = json.dumps({"jobs": [{"title": "甲", "prompt": "读 A 给要点"},
                                            {"title": "乙", "prompt": "读 B 给要点"}]},
                                  ensure_ascii=False)
                return {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "d1", "type": "function", "function": {"name": "delegate", "arguments": args}}]}
            return {"role": "assistant", "content": "综合两份摘要：OK", "tool_calls": []}

        workflow.chat_raw = fake_chat_raw

    def test_isolation_digest_and_no_publish(self) -> None:
        """子 agent 看不到父对话历史、拿得到写产物工具但拿不到结构配置；父 agent 第二轮拿到摘要；父历史照常只有 user/assistant。"""
        self._fake()
        stream: list = []
        def persist(event):
            self.db.db.execute("SELECT 1").fetchone()
            stream.append(event)
        out = run_agent(self.db, self.task, "把 A、B 分别读一下", emit=persist)
        self.assertEqual(len(self.child_calls), 2)
        for c in self.child_calls:
            roles = [m["role"] for m in c["messages"]]
            self.assertEqual(roles, ["system", "system", "user"], roles)   # 没有父对话历史
            self.assertNotIn("delegate", c["tools"])                       # 子 agent 不能再往下派
            self.assertIn("draft_write", c["tools"])                       # 写产物放开了
            self.assertNotIn("project_create", c["tools"])                 # 结构配置仍留在父手里
            self.assertNotIn("workflow_start", c["tools"])
            self.assertIn("library_overview", c["tools"])
        self.assertEqual(len(self.parent_calls), 2)
        second = "\n".join(str(m.get("content") or "") for m in self.parent_calls[1]["messages"])
        self.assertIn("摘要[读 A 给要点]", second)                          # 摘要回了父 agent
        self.assertIn("摘要[读 B 给要点]", second)
        self.assertIn("综合两份摘要", out["reply"])
        self.assertEqual([m["role"] for m in self.db.messages(self.task)], ["user", "assistant"])
        subs = [e for e in stream if e.get("type") == "sub"]
        self.assertEqual(sorted({e["phase"] for e in subs}), ["done", "start"])      # 前台能分行看进度
        self.assertEqual({e["sub_title"] for e in subs}, {"甲", "乙"})

    def test_runs_in_parallel_and_survives_one_failure(self) -> None:
        """两个子 agent 真并行（同时在跑 ≥2）；一个崩了另一个照常交活，摘要里写明失败。"""
        self._fake(boom_title="读 A")
        with workflow.WorkflowDB(self.db.path) as fresh:
            res = workflow._delegate(fresh, self.task, [{"title": "甲", "prompt": "读 A 给要点"},
                                                        {"title": "乙", "prompt": "读 B 给要点"}],
                                     parallel=2)
        self.assertGreaterEqual(self.max_live, 2)                     # 真并行
        self.assertTrue(res["ok"])
        by_title = {r["title"]: r for r in res["delegated"]}
        self.assertFalse(by_title["甲"]["ok"])
        self.assertIn("失败", by_title["甲"]["summary"])
        self.assertTrue(by_title["乙"]["ok"])
        self.assertIn("摘要[读 B 给要点]", res["digest"])

    def test_long_summary_spills_to_file(self) -> None:
        """摘要超上限要落盘、父拿到截断+路径，不再静默切掉后半段。"""
        long_text = "结论开头" + "细" * (workflow._SUB_SUMMARY_MAX + 500)
        old = workflow.chat_raw
        try:
            workflow.chat_raw = lambda profile, messages, tools=None, **kw: {
                "role": "assistant", "content": long_text, "tool_calls": []}
            r = workflow._delegate(self.db, self.task, [{"title": "长活", "prompt": "写长摘要"}])
            workflow.chat_raw = lambda profile, messages, tools=None, **kw: {
                "role": "assistant", "content": long_text + "第二轮", "tool_calls": []}
            second = workflow._delegate(self.db, self.task, [{"title": "长活", "prompt": "写长摘要"}])
        finally:
            workflow.chat_raw = old
        rec = r["delegated"][0]
        self.assertTrue(rec["ok"])
        self.assertIn("已截断", rec["summary"])
        self.assertTrue(rec["summary"].startswith(long_text[:workflow._SUB_SUMMARY_MAX]))
        path = re.search(r"见 (.+?)）", rec["summary"]).group(1)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), long_text)
        second_path = re.search(r"见 (.+?)）", second["delegated"][0]["summary"]).group(1)
        self.assertNotEqual(path, second_path)
        self.assertEqual(Path(second_path).read_text(encoding="utf-8"), long_text + "第二轮")

    def test_empty_jobs_is_rejected(self) -> None:
        r = workflow._delegate(self.db, self.task, [], parallel=2)
        self.assertFalse(r["ok"])
        self.assertIn("至少一个子任务", r["error"])

    def test_research_roles_use_shared_evidence_and_resume_cache(self) -> None:
        calls = []
        tool = {"id": "inspect", "type": "function",
                "function": {"name": "knowledge_query", "arguments": '{"query":"matched control"}'}}

        def model(profile, messages, tools=None, **kw):
            identity = messages[0]["content"]
            names = {spec["function"]["name"] for spec in tools or []}
            if "职责：原文取证" in identity:
                self.assertEqual(names, {"knowledge_query", "paper_excerpt", "paper_focus", "paper_evidence_get"})
                if messages[-1]["role"] == "user":
                    calls.append("evidence")
                    return {"role": "assistant", "content": "", "tool_calls": [tool]}
                return {"role": "assistant", "content": "私有判断：强烈推荐该题", "tool_calls": []}
            self.assertIn("职责：独立评审", identity)
            self.assertEqual(names, {"knowledge_query", "paper_excerpt", "research_data_profile"})
            text = json.dumps(messages, ensure_ascii=False)
            self.assertIn("对照组和实验组使用不同电压", text)
            self.assertNotIn("私有判断", text)
            calls.append("review")
            return {"role": "assistant", "content": "需要匹配电压区间", "tool_calls": []}

        jobs = [
            {"id": "extract", "role": "evidence", "prompt": "读同一原文"},
            {"id": "duplicate", "role": "evidence", "prompt": "读同一原文"},
            {"id": "review", "role": "review", "prompt": "是否有匹配的对照", "depends_on": ["extract"]},
        ]
        with patch.object(workflow, "chat_raw", side_effect=model), \
             patch.object(workflow, "_knowledge_query", return_value={"hits": [
                 {"citation": "example.pdf p.7", "snippet": "对照组和实验组使用不同电压"}]}):
            state = {}
            first = workflow._delegate(self.db, self.task, jobs, state=state)
            second = workflow._delegate(self.db, self.task, jobs, state=json.loads(json.dumps(state)))
        self.assertEqual(calls, ["evidence", "review"])
        self.assertTrue(first["delegated"][1]["reused"])
        self.assertIn("需要匹配电压区间", first["digest"])
        self.assertTrue(all(item["reused"] for item in second["delegated"]))
        self.assertEqual(second["sub_events"], [])

    def test_dependency_cycle_rejected_before_model_calls(self) -> None:
        with patch.object(workflow, "chat_raw") as model:
            result = workflow._delegate(self.db, self.task, [
                {"id": "a", "prompt": "a", "depends_on": ["b"]},
                {"id": "b", "prompt": "b", "depends_on": ["a"]},
            ])
        self.assertFalse(result["ok"])
        model.assert_not_called()

    def test_data_profile_counts_filtered_rows_and_independent_groups(self) -> None:
        data = {"asset_id": "dataset", "path": "samples.csv", "content_hash": "v1",
                "content": "\ufeffpaper,element,value\np1,Cu,80\np1,Cu,84\np2,Cu,\np3,Fe,75\n"}
        with patch("project_resources.read", return_value=data) as reader:
            result = workflow._tool_impl("research_data_profile", self.db, self.task, {
                "asset_id": "dataset", "group_by": "paper", "where": {"element": "Cu"},
            })
        reader.assert_called_once_with(self.db, self.task, "dataset")
        self.assertEqual((result["rows"], result["distinct_groups"]), (3, 2))
        self.assertEqual(result["missing"], {"value": 1})
        self.assertEqual(result["content_hash"], "v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
