# packages/ai-runtime — agent 运行时（占位）

**现在还没拆出来**：agent 的循环（工具调用、每步 scope、记忆注入、SSE 事件、子 agent 委派）
和十步工作流一起住在 `packages/workspace-service/workflow.py` 里，`run_agent()` / `chat_raw()` / `_delegate()` 是它的入口。

这个目录现在只放说明，作用是**把边界先画出来**：方案 B 搬家时，`workflow.py` 里的
agent 循环要落到目标仓 `packages/ai-runtime`，而 `stage_*` / 工具表 / 步骤面板留在 `workspace-service`。
拆分口径见 `docs/交接.md` 与 `Desktop\科研agent\增删目录\方案B_增删清单.md` §3。

不拆的理由（YAGNI）：现在拆只会把 3400 行的 `workflow.py` 撕成互相 import 的两半，
测试与运行时都没收益；等真搬到 TS 那套时按目标仓的分层一次拆干净。
