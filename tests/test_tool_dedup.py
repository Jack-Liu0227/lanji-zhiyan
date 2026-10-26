"""同一轮 run 内模型重复发「同名同参数」的工具调用，只真跑一次。

实测历史里有轮次把同一份 map_write 发了 4 次、doc_write 发了 2 次——都是白跑。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import workflow


class ToolDedupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "t.sqlite3")
        self.cid = self.db.create_chat()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_same_call_twice_in_one_run_executes_once(self):
        call = {"type": "function", "function": {"name": "file_list", "arguments": '{"path": ""}'}}
        turns = [[dict(call, id="c1"), dict(call, id="c2")], []]

        def complete(profile, messages, tools=None, **kwargs):
            calls = turns.pop(0) if turns else []
            return {"content": "已完成" if not calls else "", "tool_calls": calls}

        with patch.object(workflow, "chat_raw", complete), \
                patch.object(workflow, "_tool_impl", return_value={"files": ["a.md"]}) as impl:
            out = workflow.run_agent(self.db, self.cid, "列表", publish=False)

        self.assertEqual(impl.call_count, 1, "同参数重复调用应只执行一次")
        results = [e for e in out["events"] if e["type"] == "tool_result"]
        self.assertEqual(len(results), 2, "两次调用都要给模型回包")
        self.assertTrue(all(r["ok"] for r in results))

    def test_different_args_not_deduped(self):
        a = {"type": "function", "function": {"name": "file_list", "arguments": '{"path": "x"}'}}
        b = {"type": "function", "function": {"name": "file_list", "arguments": '{"path": "y"}'}}
        turns = [[dict(a, id="c1"), dict(b, id="c2")], []]

        def complete(profile, messages, tools=None, **kwargs):
            calls = turns.pop(0) if turns else []
            return {"content": "已完成" if not calls else "", "tool_calls": calls}

        with patch.object(workflow, "chat_raw", complete), \
                patch.object(workflow, "_tool_impl", return_value={"files": []}) as impl:
            workflow.run_agent(self.db, self.cid, "列表", publish=False)
        self.assertEqual(impl.call_count, 2)


if __name__ == "__main__":
    unittest.main()
