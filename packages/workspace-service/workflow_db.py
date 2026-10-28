# -*- coding: utf-8 -*-
"""WorkflowDB（项目库：schema、迁移、项目/对话/步骤/笔记/记忆/文献的读写）+ 它依赖的常量与小工具。

原本 1300 行挤在 workflow.py 里，跟工具实现、agent 循环混在一处。搬出来只为一件事：改库表/加列时别再翻
2900 行的文件。**对外接口不变** —— workflow.py 末尾 `from workflow_db import ...` 把它们重新导出，
41 处 `import workflow` 的调用点一个字都不用改。

依赖方向：本模块不 import workflow（会成环）；workflow.py 单向依赖本模块。
"""
from __future__ import annotations

import json
import os
import threading
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fileguard        # 文件安全：子进程环境洗白 / 写前快照 / 打开白名单 / 审计
import guards          # 出站脱敏 + 引用核验（见 guards.py）
import memory_store    # 记忆体系：全局/项目两层、可改可删、FTS 检索
import paths            # 唯一路径来源（data / user / executors 都在 HOME 下）
import project_notes
import securestore      # 凭据保护（DPAPI，见 securestore.py）
from research_core import ResearchDB
from workflow_catalog import STAGE_LABEL, STAGES
from workflow_search import make_project_tree   # 建项目骨架（pdir 里懒建目录用）


HERE = Path(__file__).resolve().parent
ROOT = paths.APP
DATA, PAPERS, NOTES, OUT = paths.DATA, paths.PAPERS, paths.NOTES, paths.OUT   # 唯一路径来源
NOTE_CATEGORY = "文献精读笔记"    # 项目笔记按分类落盘（同 project_notes.CATEGORIES）：精读/翻译产出的走这一类

UA = {"User-Agent": "research-workbench-1.0 (mailto:local@localhost)"}


# ---------------- 凭据：库里只存密文（DPAPI），读出来才解 ----------------
# ponytail: 只挡「整份拷走 data/ 就等于拿走 key」这一层；不挡「同一 Windows 用户下的本机进程」
# This does not isolate separate processes running as the same Windows user.
def _stored_key(value: Any) -> str:
    """明文 → 入库值：加密；空串/已加密原样返回（幂等）。"""
    text = str(value or "").strip()
    return securestore.protect(text) if text else ""


def _reveal_key(row: dict[str, Any]) -> dict[str, Any]:
    """出库 → 明文（给 chat/search 用；给前端前 server.py 还会再打码成 ***）。"""
    if row.get("api_key"):
        row["api_key"] = securestore.unprotect(row["api_key"])
    return row


def _host_of(url: object) -> str:
    """取 base_url 的域名（小写）；解析不了给空串。"""
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _assert_key_host(profile: dict[str, Any]) -> None:
    """key 绑定它被填进来时的域名：接口地址换了域名就拒发，防止 key 被引到别的站点。"""
    bound = str(profile.get("key_host") or "").lower()
    host = _host_of(profile.get("base_url"))
    if profile.get("api_key") and bound and host and host != bound:
        raise RuntimeError(f"API key 是给 {bound} 设的，而当前接口地址是 {host} —— "
                           f"换域名后请到「设置 → 模型与 Agent」重新填一次 key（防止 key 被发到别的站点）")


def encrypt_legacy_secrets(db: "WorkflowDB") -> dict[str, int]:
    """把老库里的明文 key 升格成密文（幂等：每次开库跑一遍，干净时就两条 SELECT）。
    顺手给 key 补上「绑定域名」——老数据没记过，只能按当前 base_url 认。"""
    fixed = 0
    for table in ("providers", "model_profiles"):
        for r in db.db.execute(f"SELECT id, api_key, base_url, key_host FROM {table} WHERE api_key<>''").fetchall():
            value, host = r["api_key"], str(r["key_host"] or "")
            if not securestore.is_protected(value):
                value = securestore.protect(value)
                fixed += 1
            if not host:
                host = _host_of(r["base_url"])
            if value != r["api_key"] or host != r["key_host"]:
                db.db.execute(f"UPDATE {table} SET api_key=?, key_host=? WHERE id=?", (value, host, r["id"]))
    if fixed:
        db.db.commit()
    return {"encrypted": fixed}

from workflow_prompts import (
    WORKFLOW_SCHEMA, IDEA_FALLBACK, AGENT_SYSTEM, _DELEGATE_SYSTEM, BUILTIN_ASSISTANTS
)

# 内置 Provider 目录：从内嵌 pi-ai SDK 导出（40 家），字段见同目录 providers_catalog.json
CATALOG_PATH = next((p for p in (
    ROOT / "packages" / "contracts" / "providers_catalog.json",   # 源码树
    ROOT / "contracts" / "providers_catalog.json",                # 打包后（数据按 datas 落位）
    HERE / "providers_catalog.json",
) if p.is_file()), ROOT / "packages" / "contracts" / "providers_catalog.json")
try:
    PROVIDER_CATALOG: dict[str, dict[str, Any]] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    PROVIDER_CATALOG = {}

import account_providers
for _pid, _account in account_providers.CATALOG.items():
    PROVIDER_CATALOG[_pid] = {**PROVIDER_CATALOG.get(_pid, {}), **_account,
                              "id": _pid, "base_url": "account://" + _pid, "auth": ["account"], "n_models": len(_account["models"])}

# 接入协议包括 HTTP 模型 API 与复用官方本机账号的 CLI。
WIRE_LABEL = {
    "openai-completions": "OpenAI Chat Completions",
    "openai-responses": "OpenAI Responses",
    "anthropic-messages": "Anthropic Messages",
    "google-generative-ai": "Google Gemini",
    "google-vertex": "Google Vertex AI",
    "bedrock-converse-stream": "Bedrock Converse",
    "azure-openai-responses": "Azure OpenAI Responses",
    "mistral-conversations": "Mistral Conversations",
    "openai-codex-responses": "OpenAI Codex Responses",
    "pi-messages": "Pi Messages",
}
WIRE_LABEL.update({c["wire"]: c["name"] for c in account_providers.CATALOG.values()})
USABLE_WIRES = {"openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"} | account_providers.WIRES
CUSTOM_WIRES = ["openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai", *sorted(account_providers.WIRES)]
# 「订阅 / OAuth 登录」的授权页；设备码流要各家 client_id，暂不自动授权
# ponytail: 拿到 client_id 再加 RFC 8628 轮询
OAUTH_CONSOLE = {
    "openai": "https://platform.openai.com/api-keys",
    "openai-codex": "https://chatgpt.com/",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "google": "https://aistudio.google.com/apikey",
    "google-vertex": "https://console.cloud.google.com/apis/credentials",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "moonshotai": "https://platform.moonshot.ai/console/api-keys",
    "moonshotai-cn": "https://platform.moonshot.cn/console/api-keys",
    "zai": "https://z.ai/manage-apikey/apikey-list",
    "zai-coding-cn": "https://open.bigmodel.cn/usercenter/apikeys",
    "groq": "https://console.groq.com/keys",
    "mistral": "https://console.mistral.ai/api-keys",
    "together": "https://api.together.ai/settings/api-keys",
    "fireworks": "https://fireworks.ai/account/api-keys",
    "cerebras": "https://cloud.cerebras.ai/",
    "nvidia": "https://build.nvidia.com/",
    "openrouter": "https://openrouter.ai/keys",
    "huggingface": "https://huggingface.co/settings/tokens",
    "baseten": "https://app.baseten.co/settings/api_keys",
    "minimax": "https://platform.minimax.io/user-center/basic-information/interface-key",
    "minimax-cn": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
    "xai": "https://console.x.ai/",
    "github-copilot": "https://github.com/settings/copilot",
    "vercel-ai-gateway": "https://vercel.com/dashboard",
    "amazon-bedrock": "https://console.aws.amazon.com/bedrock/",
    "azure-openai-responses": "https://portal.azure.com/",
    "qwen-token-plan": "https://bailian.console.aliyun.com/",
    "qwen-token-plan-cn": "https://bailian.console.aliyun.com/",
    "qwen-token-plan-individual": "https://bailian.console.aliyun.com/",
    "xiaomi": "https://xiaomimimo.com/",
    "cloudflare-ai-gateway": "https://dash.cloudflare.com/",
    "cloudflare-workers-ai": "https://dash.cloudflare.com/",
}

# 新用户首启那张欢迎对话的正文（改文案只改这里）
WELCOME_TEXT = (
    "新用户你好，我是你的个人科研管家：览籍智研，叫我小智就行。\n\n"
    "我能陪你走完一篇论文的全流程：查文献、下全文、精读做笔记、定大纲、写正文、审稿、中译英、导出格式，"
    "每一步都留痕，能回退。\n\n"
    "先说说你的研究方向，再给一句话研究问题，我帮你把项目建起来。只想随便聊也行。这条对话随时可以删。"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_stage_key(conn: Any, task_id: str, order: list[str] | None = None,
                      states: dict | None = None) -> str:
    """当前阶段 = 十步里「按顺序最靠后的已完成步」（用户 2026-10-21 定的口径）。

    他跳着做：正文写了、格式转了，精读/翻译还没补完。按「第一处缺口」算会永远停在
    缺口那一步，跟他真实进度对不上。正在跑且排在其后的那一步优先；一步都没做完就退回
    正在跑的那步、再退回第一步。
    项目空间的页头 pill 与「进度与计划」看板都走这一个函数，别各算一套。
    """
    order = order or STAGES
    done: set[str] = set()
    running: list[str] = []
    try:
        latest = {r["stage"]: r["status"] for r in conn.execute(
            "SELECT stage,status FROM steps WHERE task_id=? ORDER BY rowid", (task_id,))}
        if states is not None:
            latest = {key: value["status"] for key, value in states.items()}
        for stage, status in latest.items():
            if stage not in order:
                continue                       # agent 之类不属于十步
            if status in ("done", "succeeded", "delivered", "stale"):
                done.add(stage)
            elif status == "running":
                running.append(stage)
    except Exception:
        pass
    ordered = [s for s in order if s in done]
    pending = [k for k in running if k not in done]
    # 当前步 = 最后一个已完成步之后的 running；其前的 running 是崩溃残留，不算。
    last_done_idx = order.index(ordered[-1]) if ordered else -1
    frontier = [k for k in order if k in pending and order.index(k) >= last_done_idx]
    if frontier:
        return frontier[-1]
    if ordered:
        return ordered[-1]
    return pending[-1] if pending else (order[0] if order else "")


def _text(value: str) -> str:
    """去标签 + 折叠空白，用于解析 Crossref/OpenAlex/arXiv 返回。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


def _norm_hhmm(value: str) -> str:
    """日程钟点归一成 HH:MM；认不出来就当没定时间（空串），不因为一个格式问题丢掉整条日程。
    认：14:00 / 9:5 / 14点 / 930 / 1400 / 14 —— 这些模型和人都会写。"""
    s = re.sub(r"[\s点時时：]", ":", str(value or "").strip()).strip(":")
    m = re.match(r"^(\d{1,3}):(\d{1,2})$", s)
    if m:
        hh, mi = int(m.group(1)), int(m.group(2))
    else:
        d = s.replace(":", "")
        if not d.isdigit():
            return ""
        if len(d) <= 2:
            hh, mi = int(d), 0
        elif len(d) in (3, 4):
            hh, mi = int(d[:-2]), int(d[-2:])
        else:
            return ""
    return f"{hh:02d}:{mi:02d}" if 0 <= hh <= 23 and 0 <= mi <= 59 else ""


# ------------------------------------------------------------ 内置定时任务 ----
# 内置定时规则：id 固定，重复播种只补缺、不重复插入。
BUILTIN_SCHEDULES = [
    {"id": "builtin.schedule.last30days", "name": "Last 30 days 每日资讯推送",
     "cron": "0 9 * * *", "output_dir": "每日资讯推送",
     "prompt": "检索近 30 天的 AI 最新资讯（大模型 / 智能体 / 材料信息学等方向），逐条给出标题、来源链接、一句话要点，写成中文简报落盘到 每日资讯推送/。抓不到全文的只记标题与链接，别编内容。"},
    {"id": "builtin.schedule.literature-matrix", "name": "文献矩阵推送",
     "cron": "0 9 * * *", "output_dir": "文献矩阵",
     "prompt": "按主题「长上下文检索」做文献矩阵：每篇抽 题目 / 方法 / 数据 / 结论 / 局限 五列，取近 30 天的文献，写成 Markdown 表格落盘到 文献矩阵/。原文没写的字段留空，不补。"},
    {"id": "builtin.schedule.literature-review-push", "name": "文献综述推送",
     "cron": "0 9 * * *", "output_dir": "文献综述",
     "prompt": "按主题「长上下文检索」写文献综述：分主题归纳现状与空白，逐条带引用（作者+年份），优先近 30 天，落盘到 文献综述/。观点与原文一致，不夸大。"},
    {"id": "builtin.schedule.feishu-daily-msg", "name": "每日文献推送（飞书消息）",
     "cron": "0 9 * * *", "output_dir": "每日文献推送",
     "prompt": "挑近 1 天里最值得推的文献，写成一条可直接发飞书的短消息（≤300 字，含 3 条链接），落盘到 每日文献推送/。"},
]


# 项目位置里固定生成的六个子目录：名字 + 写进各自 README 的说明
_LOW_MARKED: set[str] = set()     # 已标成低完整性的目录（每个进程只 icacls 一次）

PROJ_DIRS: list[tuple[str, str]] = [
    ("记忆", "项目记忆的落盘副本：长期记忆.md（项目要点与重要结论）、短期记忆.md（对话内容与细节）。由「创新点审查」等步骤自动维护，人工也可直接编辑，下一步再跑时会读回来。"),
    ("文献库", "开放获取(OA) 全文 PDF：文件名 = 论文标题；只放 OA 全文，付费墙文献请走学校图书馆。"),
    ("笔记", "逐篇精读的 Markdown 笔记：一份笔记一个 .md 文件，含证据表与页码。"),
    ("论文正文", "章节草稿 Markdown：draft-*.md，按章节名命名，是「文章撰写」这一步的直接产物。"),
    ("对话", "对话与过程记录：按日期分 YYYY-MM-DD 子目录，当天产生的记录都落在当天那一层。"),
    ("最终产物", "最终交付件：中文 Word(.docx)、LaTeX(.tex)、参考文献(.bib)、成稿 PDF(.pdf)、图片(PNG/SVG) 和画图代码(.py) 都放这里。"),
    ("图", "论文里要用的图（PNG/SVG/JPG/PDF）：文件名就是图题里引用的名字；「格式转换」左栏列出这里，可改名、点开看；「文章撰写」会读这份清单来落图位与图题。"),
    ("其余", "工作流产生的其它杂项文件：中间态导出、脚本、临时数据等，不影响主流程。"),
]


_WORKFLOW_SCHEMA_INITIALIZED: set[str] = set()
_WORKFLOW_INIT_LOCK = threading.Lock()


class WorkflowDB(ResearchDB):

    def __init__(self, path: str | Path):
        super().__init__(path)
        is_memory = str(self.path) == ":memory:"
        db_key = ":memory:" if is_memory else f"{self.path.resolve()}:{self.path.stat().st_ino}"
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='steps'").fetchone():
            _WORKFLOW_SCHEMA_INITIALIZED.discard(db_key)
        if is_memory or db_key not in _WORKFLOW_SCHEMA_INITIALIZED:
            with _WORKFLOW_INIT_LOCK:
                if is_memory or db_key not in _WORKFLOW_SCHEMA_INITIALIZED:
                    self.db.executescript(WORKFLOW_SCHEMA)
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(chats)")}
                    if "assistant_id" not in cols:                # 老库补列：对话绑的专家（我的/公开）
                        self.db.execute("ALTER TABLE chats ADD COLUMN assistant_id TEXT NOT NULL DEFAULT ''")
                    if "task_id" not in cols:                     # 老库补列：对话绑的项目（'' = 随意对话）
                        self.db.execute("ALTER TABLE chats ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
                    # 老库补列（早期版本 providers 只有 6 列）
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(providers)")}
                    if "source" not in cols:
                        self.db.execute("ALTER TABLE providers ADD COLUMN source TEXT NOT NULL DEFAULT 'builtin'")
                    if "enabled" not in cols:
                        self.db.execute("ALTER TABLE providers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 0")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(calendar_events)")}
                    if "done" not in cols:
                        self.db.execute("ALTER TABLE calendar_events ADD COLUMN done INTEGER NOT NULL DEFAULT 0")
                    if "time" not in cols:                          # 老库补列：当天的钟点 HH:MM（空 = 没定时间）
                        self.db.execute("ALTER TABLE calendar_events ADD COLUMN time TEXT NOT NULL DEFAULT ''")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(research_tasks)")}
                    if "base_dir" not in cols:                      # 老库补列：项目位置
                        self.db.execute("ALTER TABLE research_tasks ADD COLUMN base_dir TEXT NOT NULL DEFAULT ''")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(md_notes)")}
                    if "task_id" not in cols:                       # 老库补列：大笔记可选绑项目
                        self.db.execute("ALTER TABLE md_notes ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
                    if "paper_id" not in cols:                      # 老库补列：阅读笔记绑论文（论文又绑项目）
                        self.db.execute("ALTER TABLE md_notes ADD COLUMN paper_id TEXT NOT NULL DEFAULT ''")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(chat)")}
                    if "scope" not in cols:                         # 老库补列：各步骤的对话分开存
                        self.db.execute("ALTER TABLE chat ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
                    self.db.execute("""CREATE TABLE IF NOT EXISTS dl_queue (
                          task_id TEXT NOT NULL, result_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                          path TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                          PRIMARY KEY (task_id, result_id))""")
                    # 团队（Leader + 成员 + 派活队列 + 运行事件）
                    self.db.execute("""CREATE TABLE IF NOT EXISTS assistants (
              id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'user', name TEXT NOT NULL,
              avatar TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
              rules TEXT NOT NULL DEFAULT '', prompts TEXT NOT NULL DEFAULT '[]',
              model_id TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
                    self.db.execute("""CREATE TABLE IF NOT EXISTS team_members (
                          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, name TEXT NOT NULL,
                          role TEXT NOT NULL DEFAULT 'member', step TEXT NOT NULL DEFAULT '',
                          prompt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'idle',
                          note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                    self.db.execute("""CREATE TABLE IF NOT EXISTS team_tasks (
                          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, member_id TEXT NOT NULL DEFAULT '',
                          member_name TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                          prompt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
                          created_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '', ended_at TEXT NOT NULL DEFAULT '',
                          output_path TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
                          reason TEXT NOT NULL DEFAULT '', trigger TEXT NOT NULL DEFAULT 'leader',
                          origin TEXT NOT NULL DEFAULT '')""")
                    self.db.execute("""CREATE TABLE IF NOT EXISTS team_events (
                          id TEXT PRIMARY KEY, team_task_id TEXT NOT NULL, task_id TEXT NOT NULL,
                          kind TEXT NOT NULL DEFAULT 'note', text TEXT NOT NULL DEFAULT '',
                          created_at TEXT NOT NULL)""")
                    self.db.execute("CREATE INDEX IF NOT EXISTS idx_team_tasks_status ON team_tasks(status)")
                    self.db.execute("""CREATE TABLE IF NOT EXISTS schedules (
                          id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '',
                          project_id TEXT NOT NULL DEFAULT '', frequency TEXT NOT NULL DEFAULT 'daily',
                          cron TEXT NOT NULL DEFAULT '0 9 * * *', output_dir TEXT NOT NULL DEFAULT '定时任务',
                          enabled INTEGER NOT NULL DEFAULT 1, last_run_at TEXT NOT NULL DEFAULT '',
                          next_run_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                    self.db.execute("""CREATE TABLE IF NOT EXISTS schedule_runs (
                          id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                          trigger TEXT NOT NULL DEFAULT 'scheduler', status TEXT NOT NULL DEFAULT 'running',
                          started_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '',
                          summary TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '')""")
                    cols_sch = {r["name"] for r in self.db.execute("PRAGMA table_info(schedules)")}
                    if "last_run_at" not in cols_sch:
                        self.db.execute("ALTER TABLE schedules ADD COLUMN last_run_at TEXT NOT NULL DEFAULT ''")
                    if "next_run_at" not in cols_sch:
                        self.db.execute("ALTER TABLE schedules ADD COLUMN next_run_at TEXT NOT NULL DEFAULT ''")
                    if "assistant_id" not in cols_sch:
                        self.db.execute("ALTER TABLE schedules ADD COLUMN assistant_id TEXT NOT NULL DEFAULT ''")
                    cols_runs = {r["name"] for r in self.db.execute("PRAGMA table_info(schedule_runs)")}
                    if "name" not in cols_runs:
                        self.db.execute("ALTER TABLE schedule_runs ADD COLUMN name TEXT NOT NULL DEFAULT ''")
                    if "trigger" not in cols_runs:
                        self.db.execute("ALTER TABLE schedule_runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'scheduler'")
                    if "reason" not in cols_runs:
                        self.db.execute("ALTER TABLE schedule_runs ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
                    self.assistant_seed(BUILTIN_ASSISTANTS)      # 公开专家（内置）：只补缺，不覆盖改过的
                    self.db.execute("UPDATE schedules SET assistant_id='builtin-scheduler' WHERE assistant_id IN ('','builtin-steward')")
                    _recent = self.db.execute("SELECT id FROM research_tasks ORDER BY updated_at DESC LIMIT 1").fetchone()
                    self.schedule_seed(BUILTIN_SCHEDULES, _recent["id"] if _recent else "")   # 内置定时任务：只补缺（没有项目就先留空，等用户绑）
                    self.db.execute("""CREATE TABLE IF NOT EXISTS teams (
                          id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fixed',
                          status TEXT NOT NULL DEFAULT 'idle', project_id TEXT NOT NULL DEFAULT '',
                          purpose TEXT NOT NULL DEFAULT '',
                          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(teams)")}
                    if "project_id" not in cols:                   # 老库补列：团队挂在项目上
                        self.db.execute("ALTER TABLE teams ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
                    if "purpose" not in cols:                      # 老库补列：加急团队「为什么组」
                        self.db.execute("ALTER TABLE teams ADD COLUMN purpose TEXT NOT NULL DEFAULT ''")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(team_members)")}
                    if "assistant_id" not in cols:                 # 老库补列：成员 = 哪个专家
                        self.db.execute("ALTER TABLE team_members ADD COLUMN assistant_id TEXT NOT NULL DEFAULT ''")
                    if "color" not in cols:                        # 老库补列：成员身份色序号
                        self.db.execute("ALTER TABLE team_members ADD COLUMN color INTEGER NOT NULL DEFAULT 0")
                    cols = {r["name"] for r in self.db.execute("PRAGMA table_info(chat)")}
                    if "sender" not in cols:                       # 老库补列：团队群里这条是谁说的
                        self.db.execute("ALTER TABLE chat ADD COLUMN sender TEXT NOT NULL DEFAULT ''")
                    import agent_settings
                    agent_settings.migrate(self)
                    import team_configuration
                    team_configuration.migrate(self)
                    import project_bindings
                    for project in self.db.execute("SELECT id FROM research_tasks").fetchall():
                        project_bindings.import_legacy(self, project["id"])
                    for _t in ("providers", "model_profiles"):        # key 绑定的域名（换域名就不许拿旧 key 发）
                        _cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({_t})")}
                        if "key_host" not in _cols:
                            self.db.execute(f"ALTER TABLE {_t} ADD COLUMN key_host TEXT NOT NULL DEFAULT ''")
                    encrypt_legacy_secrets(self)                   # 老库里的明文 key → DPAPI 密文（幂等）
                    self.db.commit()
                    for folder in (PAPERS, NOTES, OUT):
                        folder.mkdir(parents=True, exist_ok=True)
                    if not is_memory:
                        _WORKFLOW_SCHEMA_INITIALIZED.add(db_key)
        import project_sources
        project_sources.restore_source_protection(self)

    def create_task(self, title: str, question: str = "", material_system: str = "", base_dir: str = "") -> str:
        chosen = str(base_dir or "").strip()
        if chosen:
            paths.validate_project_dir(chosen)
        task_id = super().create_task(title, question, material_system, chosen)
        if not chosen and str(self.path) != ":memory:":
            base = self.path.resolve().parent / "projects" / task_id
            self.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(base), task_id))
            self.db.commit()
            make_project_tree(base, self.summary(task_id)["task"])
        root = self.base_dir(task_id)
        if root:
            import note_catalog
            root.mkdir(parents=True, exist_ok=True)
            note_catalog.initialize(self, task_id, new_project=True)
        return task_id

    def _chat_project(self, task_id: str | None) -> str:
        tid = str(task_id or "").strip()
        if tid and self.base_dir(tid):
            paths.validate_project_dir(self.base_dir(tid))
        if tid and not self.base_dir(tid):
            if not self.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (tid,)).fetchone():
                raise ValueError("没有这个项目")
            base = self.path.resolve().parent / "projects" / tid
            self.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(base), tid))
            self.db.commit()
            make_project_tree(base, self.summary(tid)["task"])
        return tid

    def create_chat(self, title: str = "", *, task_id: str = "") -> str:
        return super().create_chat(title, task_id=self._chat_project(task_id))

    def bind_chat(self, cid: str, task_id: str | None = None) -> dict[str, Any]:
        self.chat(cid)
        tid = self._chat_project(task_id)
        return super().bind_chat(cid, tid)

    def __enter__(self) -> "WorkflowDB":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- 任务 / 步骤 ----------
    def log_step(self, task_id: str, stage: str, status: str, summary: str = "",
                 delivery: dict[str, Any] | None = None) -> dict[str, Any]:
        # quiet_steps：定时任务这类后台跑不记到十步账上（否则进度虚高）。
        blob = json.dumps(delivery or {}, ensure_ascii=False) if delivery else ""
        if getattr(self, "quiet_steps", False):
            return {"id": "", "stage": stage, "status": status, "summary": summary,
                    "delivery": blob, "created_at": now(), "skipped": True}
        if stage in STAGES and status == "done" and not delivery:
            status = "succeeded"
        row = (uuid.uuid4().hex, task_id, stage, status, summary, now())
        self.db.execute("INSERT INTO steps (id,task_id,stage,status,summary,created_at,delivery)"
                        " VALUES (?,?,?,?,?,?,?)", row + (blob,))
        self.db.commit()
        if stage in STAGES:
            import project_todos
            project_todos.sync_stage(self, task_id, stage, {
                "running": "IN_PROGRESS", "succeeded": "REVIEW", "delivered": "DONE",
                "failed": "TODO", "cancelled": "TODO"}.get(status, "TODO"))
        return {"id": row[0], "stage": stage, "status": status, "summary": summary,
                "delivery": blob, "created_at": row[5]}

    def steps(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY rowid DESC", (task_id,))
        return [dict(r) for r in rows]

    def out_dir(self, task_id: str) -> Path:
        """对话自己的产出目录（chats.out_dir）；没设就落在默认 data/out。"""
        if self.base_dir(task_id):
            return self.pdir(task_id, "其余")
        row = self.db.execute("SELECT out_dir, task_id FROM chats WHERE id=?", (task_id,)).fetchone()
        if row and row["task_id"]:
            return self.pdir(row["task_id"], "其余")
        raw = (row["out_dir"] if row else "") or ""
        folder = Path(raw).expanduser() if raw else OUT
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ---------- 大笔记（「笔记」页，不绑项目）----------
    def md_notes(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM md_notes ORDER BY updated_at DESC")]

    def md_note(self, nid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM md_notes WHERE id=?", (nid,)).fetchone()
        if not row:
            raise KeyError(nid)
        return dict(row)

    def md_note_by_paper(self, paper_id: str) -> dict[str, Any] | None:
        """一篇论文对应一篇「阅读笔记」；没有就返回 None（不重复建）。"""
        if not paper_id:
            return None
        row = self.db.execute("SELECT * FROM md_notes WHERE paper_id=? ORDER BY created_at LIMIT 1",
                              (paper_id,)).fetchone()
        return dict(row) if row else None

    @project_notes.synchronized
    def save_md_note(self, nid: str, title: str, content: str, task_id: str | None = None,
                     paper_id: str | None = None) -> dict[str, Any]:
        """task_id / paper_id 传 None 保持原绑定不变；传 "" 就是解绑。"""
        t = now()
        title = title.strip() or "未命名笔记"
        previous = self.md_note(nid) if nid and self.db.execute(
            "SELECT 1 FROM md_notes WHERE id=?", (nid,)).fetchone() else None
        import note_catalog
        previous_path = self._md_note_path(previous) if previous else None
        previous_content = previous_path.read_text(encoding="utf-8") if previous_path and previous_path.is_file() else (previous or {}).get("content", "")
        content = note_catalog.update_content(content, previous_content)
        if note_catalog.is_template(content):
            import prompt_templates
            pid = task_id if task_id is not None else (previous or {}).get("task_id", "")
            if not pid or not previous_path or task_id not in (None, (previous or {}).get("task_id")):
                raise ValueError("提示词模板请通过项目模板入口创建或复制到其他项目")
            content = prompt_templates.prepare(self, pid, previous_path.relative_to(self.base_dir(pid)).as_posix(), content, previous_content)
        if nid and self.db.execute("SELECT 1 FROM md_notes WHERE id=?", (nid,)).fetchone():
            self.db.execute("UPDATE md_notes SET title=?, content=?, task_id=COALESCE(?, task_id),"
                            " paper_id=COALESCE(?, paper_id), updated_at=? WHERE id=?",
                            (title, content, task_id, paper_id, t, nid))
        else:
            nid = nid or uuid.uuid4().hex
            self.db.execute(
                "INSERT OR REPLACE INTO md_notes (id,title,content,task_id,paper_id,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (nid, title, content, task_id or "", paper_id or "", t, t))
        self.db.commit()
        note = self.md_note(nid)
        # Markdown notes now have a canonical, atomically-written file mirror.
        # The database row remains for backward-compatible queries and indexing.
        import asset_service
        old_path = self._md_note_path(previous) if previous else None
        path = self._md_note_path(note)
        self._rehome_md_note_asset(previous, note, old_path, path)
        asset_service.atomic_write_text(path, note["content"])
        if old_path and old_path != path and old_path.is_file():
            old_path.unlink()
        asset_service.register_file(self, path, project_id=note["task_id"], kind="note",
                                    title=note["title"], source="md_notes", body=note["content"],
                                    metadata={"paper_id": note["paper_id"], "legacy_id": nid})
        self._link_note_paper_asset(note["task_id"], note["paper_id"], path)
        import semantic
        semantic.invalidate_source(self, "md_note", nid)
        self.db.commit()
        note["canonical_path"] = str(path)
        return note

    def _md_note_path(self, note: dict[str, Any] | None) -> Path | None:
        if not note:
            return None
        import asset_service
        for asset in asset_service.find_by_metadata(self, project_id=note["task_id"], kind="note",
                                                    key="legacy_id", value=note["id"]):
            if asset["storage"] == "file" and asset["status"] == "active" and asset["project_id"] == note["task_id"]:
                return Path(asset["canonical_uri"])
        if not note["task_id"]:
            return NOTES / f"{note['id']}.md"
        return self._project_note_dir(note["task_id"], NOTE_CATEGORY if note.get("paper_id") else "") / f"{note['id']}.md"

    def _project_note_dir(self, task_id: str, category: str = NOTE_CATEGORY) -> Path:
        """项目笔记按分类落盘（=project_notes.CATEGORIES 里的目录名）。都写进子目录，
        否则笔记页只能把它们算成「未分类」。"""
        import note_catalog
        base = self.base_dir(task_id)
        if base:
            catalog = note_catalog.initialize(self, task_id)
            key = catalog.get("legacy_targets", {}).get(category, category)
            found = note_catalog.category(catalog, key) or next((c for c in catalog["categories"] if c["name"] == category), None)
            return project_notes.folder_path(self, task_id, found["folder"] if found else "笔记", create=True)
        return self.pdir(task_id, "笔记") / category

    def _rehome_md_note_asset(self, previous: dict[str, Any] | None, note: dict[str, Any],
                              old_path: Path | None, new_path: Path) -> None:
        """Keep one note asset when its project binding changes."""
        if not previous or not old_path or old_path == new_path:
            return
        import asset_service
        old_row = self.db.execute("SELECT id FROM assets WHERE canonical_uri=?",
                                  (str(old_path.resolve()),)).fetchone()
        if not old_row:
            matches = asset_service.find_by_metadata(self, kind="note", key="legacy_id", value=note["id"])
            old_row = matches[0] if matches else None
        if not old_row:
            return
        conflict = self.db.execute("SELECT id FROM assets WHERE canonical_uri=?",
                                   (str(new_path.resolve()),)).fetchone()
        if conflict and conflict["id"] != old_row["id"]:
            return
        stamp = now()
        self.db.execute("UPDATE assets SET canonical_uri=?, project_id=?, title=?, updated_at=? WHERE id=?",
                        (str(new_path.resolve()), note["task_id"], note["title"], stamp, old_row["id"]))
        self.db.commit()

    def _link_note_paper_asset(self, task_id: str, paper_id: str, note_path: str | Path) -> None:
        """Keep the canonical note -> paper edge in sync with legacy bindings."""
        import asset_service
        note_row = self.db.execute(
            "SELECT id FROM assets WHERE canonical_uri=?", (str(Path(note_path).resolve()),)
        ).fetchone()
        if not note_row:
            return
        self.db.execute("DELETE FROM asset_relations WHERE from_asset_id=? AND relation='derived_from'",
                        (note_row["id"],))
        if not paper_id:
            self.db.commit()
            return
        papers = asset_service.find_by_metadata(self, project_id=task_id, kind="paper",
                                                key="paper_id", value=paper_id)
        if papers:
            asset_service.link(self, note_row["id"], "derived_from", papers[0]["id"],
                               {"paper_id": paper_id})
        else:
            self.db.commit()

    @project_notes.synchronized
    def delete_md_note(self, nid: str) -> None:
        note = self.md_note(nid)
        path = self._md_note_path(note)
        import asset_service
        assets = asset_service.find_by_metadata(self, kind="note", key="legacy_id", value=nid)
        self.db.execute("DELETE FROM md_notes WHERE id=?", (nid,))
        import semantic
        semantic.invalidate_source(self, "md_note", nid)
        for asset in assets:
            self.db.execute("UPDATE assets SET status='archived', updated_at=? WHERE id=?",
                            (now(), asset["id"]))
        self.db.commit()
        if path and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass

    # ---------- 情报日报（RSS 来源 + 条目，都不绑项目）----------
    def rss_sources(self) -> list[dict[str, Any]]:
        counts = {r["source_id"]: r["n"] for r in self.db.execute(
            "SELECT source_id, COUNT(*) AS n FROM rss_items GROUP BY source_id")}
        rows = self.db.execute("SELECT * FROM rss_sources ORDER BY created_at").fetchall()
        return [{**dict(r), "count": counts.get(r["id"], 0)} for r in rows]

    def rss_source(self, sid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM rss_sources WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError(sid)
        return dict(row)

    def add_rss_source(self, url: str, title: str = "", category: str = "学术论文", site: str = "") -> dict[str, Any]:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("RSS 地址要以 http:// 或 https:// 开头")
        old = self.db.execute("SELECT * FROM rss_sources WHERE url=?", (url,)).fetchone()
        if old:                                    # 同一个 URL 再加：认作同一个源，不重复抓
            return dict(old)
        sid = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO rss_sources (id,url,title,site,category,enabled,in_daily,last_error,last_fetch,created_at)"
            " VALUES (?,?,?,?,?,1,1,'','',?)",
            (sid, url, title.strip() or url, site.strip(), category.strip() or "学术论文", now()))
        self.db.commit()
        return self.rss_source(sid)

    def save_rss_items(self, src: dict[str, Any], items: list[dict[str, str]]) -> int:
        """按 link 去重入库（UNIQUE 索引 + OR IGNORE），返回真正新增的条数。"""
        n = 0
        for it in items:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO rss_items (id,source_id,link,title,authors,summary,published_at,"
                "source_title,category,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, src["id"], it["link"], it["title"], it.get("authors", ""),
                 it.get("summary", ""), it.get("published_at", ""), src["title"], src["category"], now()))
            n += cur.rowcount or 0
        self.db.commit()
        return n

    def mark_rss_fetch(self, sid: str, error: str = "") -> None:
        self.db.execute("UPDATE rss_sources SET last_fetch=?, last_error=? WHERE id=?", (now(), error[:200], sid))
        self.db.commit()

    def _rss_where(self, source_id: str = "", q: str = "", year: str = "") -> tuple[str, list[Any]]:
        sql, args = " WHERE 1=1", []
        if source_id:
            sql += " AND i.source_id=?"; args.append(source_id)
        if q:
            sql += " AND (i.title LIKE ? OR i.summary LIKE ? OR i.authors LIKE ?)"; args += [f"%{q}%"] * 3
        if year:
            sql += " AND i.published_at LIKE ?"; args.append(f"{year}%")
        return sql, args

    def rss_items(self, source_id: str = "", q: str = "", year: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql, args = self._rss_where(source_id, q, year)
        sql = ("SELECT i.*, s.title AS src_now FROM rss_items i LEFT JOIN rss_sources s ON s.id=i.source_id"
               + sql + " ORDER BY (i.published_at='') ASC, i.published_at DESC, i.fetched_at DESC LIMIT ?")
        out = []
        for r in self.db.execute(sql, args + [int(limit)]):
            d = dict(r)
            d["source_title"] = d["src_now"] or d["source_title"]     # 来源删了就用入库时的快照
            d["source_deleted"] = not d["src_now"]
            d.pop("src_now", None)
            out.append(d)
        return out

    def rss_count(self, source_id: str = "", q: str = "", year: str = "") -> int:
        sql, args = self._rss_where(source_id, q, year)
        sql = "SELECT COUNT(*) AS n FROM rss_items i" + sql
        return int(self.db.execute(sql, args).fetchone()["n"])

    # ---------- 勾选下载队列（论文搜索 → 下载精读）----------
    def enqueue(self, task_id: str, result_ids: list[str]) -> int:
        """按本次勾选重建队列（上一批的勾选记录清掉），状态先置 queued。"""
        self.db.execute("DELETE FROM dl_queue WHERE task_id=?", (task_id,))
        for rid in result_ids:
            self.db.execute("INSERT OR REPLACE INTO dl_queue VALUES (?,?,?,?,?,?)",
                            (task_id, rid, "queued", "", "", now()))
        self.db.commit()
        return len(result_ids)

    def set_queue(self, task_id: str, result_id: str, status: str, path: str = "", error: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO dl_queue VALUES (?,?,?,?,?,?)",
                        (task_id, result_id, status, path, error, now()))
        self.db.commit()

    def queue(self, task_id: str) -> list[dict[str, Any]]:
        """队列 + 卡片字段（和论文搜索的卡片同源，前端好复用同一套卡片）。"""
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM dl_queue WHERE task_id=? ORDER BY updated_at", (task_id,))]
        out = []
        for r in rows:
            try:
                item = self.result(r["result_id"])
            except KeyError:
                continue
            item.update({"status": r["status"], "saved_path": r["path"], "error": r["error"]})
            out.append(item)
        return out

    # ---------- 项目位置（用户选的文件夹）与六个固定子目录 ----------
    def base_dir(self, task_id: str) -> Path | None:
        row = self.db.execute("SELECT base_dir FROM research_tasks WHERE id=?", (task_id,)).fetchone()
        raw = ((row["base_dir"] if row else "") or "").strip()
        return Path(raw).expanduser() if raw else None

    def pdir(self, task_id: str, kind: str) -> Path:
        """按用途拿目录。设了项目位置就落到那固定子夹里，没设就退回工作区默认目录（老项目不受影响）。"""
        names = {n for n, _ in PROJ_DIRS}
        fallback = {"文献库": PAPERS, "笔记": NOTES, "论文正文": OUT, "最终产物": OUT, "其余": OUT, "记忆": OUT, "对话": OUT, "图": OUT}
        base = self.base_dir(task_id)
        folder = (base / kind) if (base and kind in names) else fallback.get(kind, OUT)
        if base:
            paths.validate_project_dir(base)
            try:
                folder.resolve().relative_to(base.resolve())
            except ValueError:
                raise ValueError("项目目录中的子目录越界") from None
        folder.mkdir(parents=True, exist_ok=True)
        # 启动之后新建的项目，mark_write_roots_low 那一轮没覆盖到它：babeldoc / xelatex 都跑在
        # Low IL 沙箱里，往 Medium 目录写就是 WinError 5 —— 用户看到的是「一按翻译就报退出码 1」。
        # 这里是所有项目子目录的唯一出口；icacls 一次 ~40ms，所以每个目录每个进程只标一次。
        key = str(folder).lower()
        if key not in _LOW_MARKED:
            try:
                import lowbox
                if lowbox.mark_low(folder, recursive=False):
                    _LOW_MARKED.add(key)
            except OSError:
                pass
        return folder

    # ---------- 定时任务（规则 + 每次运行一条记录） ----------
    def schedules(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT s.*,COALESCE(l.todo_id,'') AS todo_id,COALESCE(w.layout_id,'') AS workflow_id FROM schedules s "
                    "LEFT JOIN task_links l ON l.schedule_id=s.id LEFT JOIN schedule_workflows w ON w.schedule_id=s.id ORDER BY s.created_at").fetchall()]

    def schedule(self, sid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT s.*,COALESCE(l.todo_id,'') AS todo_id,COALESCE(w.layout_id,'') AS workflow_id FROM schedules s "
                              "LEFT JOIN task_links l ON l.schedule_id=s.id LEFT JOIN schedule_workflows w ON w.schedule_id=s.id WHERE s.id=?", (str(sid or ""),)).fetchone()
        return dict(row) if row else None

    def schedule_save(self, data: dict[str, Any]) -> dict[str, Any]:
        sid = str(data.get("id") or "").strip() or uuid.uuid4().hex
        stamp = now()
        enabled = 1 if data.get("enabled", True) else 0
        assistant_id = str(data.get("assistant_id") or "builtin-scheduler").strip()
        if assistant_id == 'builtin-steward':
            assistant_id = 'builtin-scheduler'
        layout_id = str(data.get("workflow_id") or "").strip()
        if layout_id:
            import workflow_layout
            workflow_layout._project(self, str(data.get("project_id") or ""))
            workflow_layout.ensure_schema(self)
            workflow_layout._layout(self, layout_id)
        if assistant_id and (not self.assistant(assistant_id) or not self.assistant(assistant_id).get("enabled")):
            raise ValueError("绑定的专家不存在或已停用")
        linked = self.db.execute("SELECT project_id FROM task_links WHERE schedule_id=?", (sid,)).fetchone()
        if linked and linked["project_id"] != str(data.get("project_id") or ""):
            raise ValueError("已关联待办的定时任务不能转移到其他项目")
        if data.get("todo_id"):
            import project_todos
            project_todos.bind_execution(self, str(data["todo_id"]), str(data.get("project_id") or ""),
                                         schedule_id=sid, commit=False)
        fields = (str(data.get("name") or "未命名任务"), str(data.get("prompt") or ""),
                  str(data.get("project_id") or ""), str(data.get("frequency") or "daily"),
                  str(data.get("cron") or "0 9 * * *"), str(data.get("output_dir") or "定时任务"),
                  enabled, str(data.get("next_run_at") or ""))
        row = self.db.execute("SELECT id FROM schedules WHERE id=?", (sid,)).fetchone()
        if row:
            self.db.execute("""UPDATE schedules SET name=?, prompt=?, project_id=?, frequency=?, cron=?,
                output_dir=?, enabled=?, next_run_at=?, updated_at=? WHERE id=?""", (*fields, stamp, sid))
        else:
            self.db.execute("""INSERT INTO schedules
                (id,name,prompt,project_id,frequency,cron,output_dir,enabled,last_run_at,next_run_at,created_at,updated_at,assistant_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, *fields[:6], enabled, "", fields[7], stamp, stamp, assistant_id))
        if row:
            self.db.execute("UPDATE schedules SET assistant_id=? WHERE id=?", (assistant_id, sid))
        if layout_id:
            self.db.execute("INSERT OR REPLACE INTO schedule_workflows VALUES (?,?)", (sid, layout_id))
        else:
            self.db.execute("DELETE FROM schedule_workflows WHERE schedule_id=?", (sid,))
        self.db.commit()
        return self.schedule(sid) or {}

    def schedule_set_enabled(self, sid: str, enabled: bool) -> None:
        self.db.execute("UPDATE schedules SET enabled=?, updated_at=? WHERE id=?",
                        (1 if enabled else 0, now(), str(sid or "")))
        self.db.commit()

    def schedule_delete(self, sid: str) -> None:
        import project_todos
        import schedule
        schedule.cancel_run(sid, self.path)
        project_todos.sync_execution(self, schedule_id=sid, status="cancelled")
        self.db.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (str(sid or ""),))
        self.db.execute("DELETE FROM schedules WHERE id=?", (str(sid or ""),))
        self.db.execute("DELETE FROM schedule_workflows WHERE schedule_id=?", (str(sid or ""),))
        self.db.commit()

    def schedule_seed(self, builtins: list[dict[str, Any]], project_id: str = "") -> int:
        """内置定时任务：只补缺，不覆盖用户改过的、也不复活用户删过的（照 assistant_seed）。"""
        n = 0
        for b in builtins:
            if self.db.execute("SELECT 1 FROM schedules WHERE id=?", (b["id"],)).fetchone():
                continue
            self.db.execute(
                "INSERT INTO schedules (id,name,prompt,project_id,frequency,cron,output_dir,enabled,"
                "last_run_at,next_run_at,created_at,updated_at,assistant_id) VALUES (?,?,?,?,?,?,?,1,'','',?,?,'builtin-scheduler')",
                (b["id"], b["name"], b["prompt"], project_id, b.get("frequency", "daily"),
                 b.get("cron", "0 9 * * *"), b.get("output_dir", "定时任务"), now(), now()))
            n += 1
        self.db.commit()
        return n

    def schedule_mark_run(self, sid: str, last_run_at: str, next_run_at: str) -> None:
        self.db.execute("UPDATE schedules SET last_run_at=?, next_run_at=?, updated_at=? WHERE id=?",
                        (str(last_run_at or ""), str(next_run_at or ""), now(), str(sid or "")))
        self.db.commit()

    def schedule_runs(self, limit: int = 80) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT r.*, n.id AS note_id,n.task_id AS note_project_id,w.workflow_run_id FROM schedule_runs r LEFT JOIN md_notes n "
                               "ON n.id='schedule-' || r.id LEFT JOIN schedule_workflow_runs w ON w.schedule_run_id=r.id ORDER BY r.started_at DESC LIMIT ?",
                               (max(1, min(500, int(limit))),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item["note_id"]:
                path = self._md_note_path(self.md_note(item["note_id"]))
                root = self.base_dir(item["note_project_id"])
                item["note_path"] = path.relative_to(root).as_posix() if root and path and path.is_relative_to(root) else ""
            result.append(item)
        return result

    def schedule_run_add(self, run: dict[str, Any]) -> None:
        """同一个 id 覆盖写（先落 running，跑完再覆盖成终态）。"""
        self.db.execute("""INSERT INTO schedule_runs VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status, ended_at=excluded.ended_at,
            output_path=excluded.output_path, summary=excluded.summary, reason=excluded.reason""",
                        (str(run.get("id") or uuid.uuid4().hex), str(run.get("schedule_id") or ""),
                         str(run.get("name") or ""), str(run.get("trigger") or "scheduler"),
                         str(run.get("status") or "running"), str(run.get("started_at") or now()),
                         str(run.get("ended_at") or ""), str(run.get("output_path") or ""),
                         str(run.get("summary") or ""), str(run.get("reason") or "")))
        self.db.commit()

    def schedule_run_delete(self, ids: list[str]) -> int:
        ids = [str(i) for i in (ids or []) if str(i).strip()]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        active = self.db.execute(f"SELECT 1 FROM schedule_workflow_runs l JOIN workflow_runs w ON w.id=l.workflow_run_id WHERE l.schedule_run_id IN ({marks}) AND w.status NOT IN ('succeeded','failed','cancelled') LIMIT 1", tuple(ids)).fetchone()
        if active:
            raise ValueError('请先完成或停止关联工作流，再清理定时运行记录')
        self.db.execute(f'DELETE FROM schedule_workflow_runs WHERE schedule_run_id IN ({marks})', tuple(ids))
        cur = self.db.execute(f"DELETE FROM schedule_runs WHERE id IN ({marks})", tuple(ids))
        self.db.commit()
        return int(cur.rowcount or 0)

    # ---------- 团队 ----------
    def team_members(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM team_members WHERE task_id=? ORDER BY (role='leader') DESC, created_at",
            (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def team_member(self, mid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM team_members WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def team_member_add(self, task_id: str, name: str, role: str = "member", step: str = "",
                        prompt: str = "", assistant_id: str = "", color: int = 0, *,
                        model_id: str = "", reasoning: str = "auto", permission: str = "auto") -> dict[str, Any]:
        import agent_settings
        agent_settings.validate_runtime_fields(self, {"model_id": model_id, "reasoning": reasoning, "permission": permission})
        row = (uuid.uuid4().hex, task_id, name, role, step, prompt, "idle", "", now(), now(),
               assistant_id, int(color or 0), model_id, reasoning, permission)
        self.db.execute("INSERT INTO team_members (id,task_id,name,role,step,prompt,status,note,created_at,"
                       "updated_at,assistant_id,color,model_id,reasoning,permission) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM team_members WHERE id=?", (row[0],)).fetchone())

    def team_member_update(self, mid: str, **fields: Any) -> dict[str, Any]:
        import agent_settings
        allow = {k: v for k, v in fields.items() if k in ("name", "step", "prompt", "status", "note", "assistant_id", "model_id", "reasoning", "permission") and v is not None}
        agent_settings.validate_runtime_fields(self, allow)
        if allow.get("assistant_id"):
            self.require_assistant(allow["assistant_id"])
            member = self.team_member(mid)
            if member and self.db.execute("SELECT 1 FROM team_members WHERE task_id=? AND assistant_id=? AND id<>?",
                                          (member["task_id"], allow["assistant_id"], mid)).fetchone():
                raise ValueError("这个专家已在当前团队中")
        if allow:
            allow["updated_at"] = now()
            sets = ", ".join("%s=?" % k for k in allow)
            self.db.execute("UPDATE team_members SET %s WHERE id=?" % sets, (*allow.values(), mid))
            self.db.commit()
        return self.team_member(mid) or {}

    def team_member_remove(self, mid: str) -> None:
        self.db.execute("DELETE FROM team_members WHERE id=?", (mid,))
        self.db.commit()

    def team_tasks(self, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if task_id:
            if limit is None:
                rows = self.db.execute("SELECT * FROM team_tasks WHERE task_id=? ORDER BY created_at DESC",
                                       (task_id,)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM team_tasks WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                                       (task_id, limit)).fetchall()
        else:
            if limit is None:
                rows = self.db.execute("SELECT * FROM team_tasks ORDER BY created_at DESC").fetchall()
            else:
                rows = self.db.execute("SELECT * FROM team_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def team_task(self, tid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM team_tasks WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def team_task_add(self, task_id: str, member_id: str, member_name: str, title: str, prompt: str,
                      trigger: str = "leader", origin: str = "", depends_on: list[str] | None = None,
                      output_contract: dict[str, Any] | None = None, reviewer_id: str = "",
                      *, commit: bool = True) -> dict[str, Any]:
        depends = json.dumps(depends_on or [], ensure_ascii=False)
        contract = json.dumps(output_contract or {}, ensure_ascii=False)
        review_status = "pending" if reviewer_id else "not_required"
        row = (uuid.uuid4().hex, task_id, member_id, member_name, title[:120], prompt, "queued",
               now(), "", "", "", "", "", trigger, origin, 0, "{}", "", "", "[]", "[]", "pending",
               depends, contract, reviewer_id or "", 0, "", "")
        row = row[:21] + (review_status,) + row[22:]
        self.db.execute("""INSERT INTO team_tasks
            (id,task_id,member_id,member_name,title,prompt,status,created_at,started_at,ended_at,
             output_path,result,reason,trigger,origin,attempt,checkpoint,retry_at,run_id,input_assets,output_assets,review_status,
             depends_on,output_contract,reviewer_id,review_attempt,approved_at,review_feedback)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
        if commit:
            self.db.commit()
        return dict(self.db.execute("SELECT * FROM team_tasks WHERE id=?", (row[0],)).fetchone())

    def team_task_mark(self, tid: str, status: str, **fields: Any) -> None:
        allow = {k: v for k, v in fields.items()
                 if k in ("started_at", "ended_at", "output_path", "result", "reason", "attempt",
                          "checkpoint", "retry_at", "run_id", "input_assets", "output_assets", "review_status",
                          "depends_on", "output_contract", "reviewer_id", "review_attempt", "approved_at",
                          "review_feedback")}
        if isinstance(allow.get("checkpoint"), dict):
            allow["checkpoint"] = json.dumps(allow["checkpoint"], ensure_ascii=False)
        if isinstance(allow.get("input_assets"), list):
            allow["input_assets"] = json.dumps(allow["input_assets"], ensure_ascii=False)
        if isinstance(allow.get("output_assets"), list):
            allow["output_assets"] = json.dumps(allow["output_assets"], ensure_ascii=False)
        if isinstance(allow.get("depends_on"), list):
            allow["depends_on"] = json.dumps(allow["depends_on"], ensure_ascii=False)
        if isinstance(allow.get("output_contract"), dict):
            allow["output_contract"] = json.dumps(allow["output_contract"], ensure_ascii=False)
        allow["status"] = status
        sets = ", ".join("%s=?" % k for k in allow)
        self.db.execute("UPDATE team_tasks SET %s WHERE id=?" % sets, (*allow.values(), tid))
        self.db.commit()
        import project_todos
        project_todos.sync_execution(self, team_task_id=tid, status=status,
                                     run_id=str(fields.get("run_id") or ""), assets=fields.get("output_assets"))

    # ---------- 团队表（一个团队 = 一条记录；成员/记录都挂在它的 id 上） ----------
    def teams(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM teams WHERE id NOT IN (SELECT instance_team_id FROM team_execution_instances) ORDER BY created_at")]

    def team(self, tid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def team_add(self, name: str, kind: str = "fixed", project_id: str = "", purpose: str = "") -> dict[str, Any]:
        tid = uuid.uuid4().hex
        self.db.execute("INSERT INTO teams (id,name,kind,status,project_id,purpose,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (tid, (name or "新团队").strip(), kind, "idle", project_id or "", purpose or "", now(), now()))
        # 不建同名 chats 行：团队用独立 team_sessions，混进对话列表删不掉
        self.db.commit()
        if project_id and self.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
            import project_bindings
            project_bindings.register(self, project_id, "team", tid, source="project_team")
        return self.team(tid) or {}

    def team_update(self, tid: str, **fields: Any) -> dict[str, Any]:
        allow = {k: v for k, v in fields.items()
                 if k in ("name", "status", "kind", "project_id", "purpose") and v is not None}
        if allow:
            allow["updated_at"] = now()
            self.db.execute("UPDATE teams SET %s WHERE id=?" % ", ".join("%s=?" % k for k in allow),
                            (*allow.values(), tid))
            self.db.commit()
        return self.team(tid) or {}

    def team_delete(self, tid: str) -> None:
        self.db.execute("DELETE FROM teams WHERE id=?", (tid,))
        self.db.execute("DELETE FROM team_members WHERE task_id=?", (tid,))
        self.db.commit()

    def team_events(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM team_events WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                               (task_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]        # 从旧到新（读 trace 的顺序）

    def team_event_add(self, team_task_id: str, task_id: str, kind: str, text: str) -> None:
        self.db.execute("INSERT INTO team_events VALUES (?,?,?,?,?,?)",
                        (uuid.uuid4().hex, team_task_id, task_id, kind, text[:2000], now()))
        self.db.commit()

    # ---------- 专家（我的 / 公开；点进去设提示词） ----------
    def assistants(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM assistants ORDER BY source='builtin', sort_order, created_at").fetchall()
        return [dict(r) for r in rows]

    def selectable_assistants(self) -> list[dict[str, Any]]:
        return [a for a in self.assistants() if a["source"] == "user" or a["enabled"]]

    def require_assistant(self, aid: str) -> dict[str, Any]:
        a = self.assistant(aid)
        if not a:
            raise ValueError("专家不存在")
        if a["source"] != "user" and not a["enabled"]:
            raise ValueError("这个公开专家尚未启用，请先在专家设置中启用")
        return a

    def assistant(self, aid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM assistants WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else None

    def assistant_save(self, aid: str, **f: Any) -> dict[str, Any]:
        import agent_settings
        allow = {k: v for k, v in f.items() if k in ("name", "avatar", "description", "rules", "prompts", "model_id", "enabled", "reasoning", "permission") and v is not None}
        agent_settings.validate_runtime_fields(self, allow)
        if "enabled" in allow:
            allow["enabled"] = int(bool(allow["enabled"]))
        if isinstance(allow.get("prompts"), list):
            allow["prompts"] = json.dumps(allow["prompts"], ensure_ascii=False)
        cur = self.assistant(aid)
        if cur:
            if allow:
                allow["updated_at"] = now()
                self.db.execute("UPDATE assistants SET %s WHERE id=?" % ", ".join("%s=?" % k for k in allow), (*allow.values(), aid))
        else:
            row = (aid, "user", allow.get("name") or "新专家", allow.get("avatar", ""), allow.get("description", ""),
                   allow.get("rules", ""), allow.get("prompts", "[]"), allow.get("model_id", ""), 1, 99, now(), now())
            self.db.execute("INSERT INTO assistants (id,source,name,avatar,description,rules,prompts,model_id,enabled,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            extra = {k: allow[k] for k in ("enabled", "reasoning", "permission") if k in allow}
            if extra:
                self.db.execute("UPDATE assistants SET %s WHERE id=?" % ", ".join("%s=?" % k for k in extra), (*extra.values(), aid))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM assistants WHERE id=?", (aid,)).fetchone())

    def assistants_batch_toggle(self, ids: list[str] | None = None, enabled: int = 1) -> int:
        target = 1 if enabled else 0
        if ids is not None:
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            self.db.execute(f"UPDATE assistants SET enabled=?, updated_at=? WHERE id IN ({placeholders})", (target, now(), *ids))
        else:
            self.db.execute("UPDATE assistants SET enabled=?, updated_at=?", (target, now()))
        self.db.commit()
        return self.db.total_changes

    def assistant_delete(self, aid: str) -> None:
        cur = self.assistant(aid)
        if cur and cur["source"] == "builtin":
            raise ValueError("公开专家不能删除 —— 可以「复制为我的」再改。")
        self.db.execute("DELETE FROM assistants WHERE id=?", (aid,))
        self.db.commit()

    def assistant_of_chat(self, chat_id: str) -> str:
        """这个对话绑的是哪个专家（空串=没绑）。"""
        row = self.db.execute("SELECT assistant_id FROM chats WHERE id=?", (chat_id,)).fetchone()
        return ((row["assistant_id"] if row else "") or "")

    def assistant_seed(self, builtins: list[dict[str, Any]]) -> int:
        """内置（公开）专家：只补缺，不覆盖用户改过的。"""
        n = 0
        for b in builtins:
            if not self.assistant(b["id"]):
                self.db.execute("INSERT INTO assistants (id,source,name,avatar,description,rules,prompts,model_id,enabled,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (b["id"], "builtin", b["name"], b.get("avatar", ""), b.get("description", ""),
                                 b.get("rules", ""), json.dumps(b.get("prompts", []), ensure_ascii=False),
                                 "", 1, b.get("sort_order", 0), now(), now()))
                n += 1
        self.db.commit()
        return n

    def set_chat_assistant(self, cid: str, aid: str) -> None:
        if aid:
            self.require_assistant(aid)
        self.db.execute("UPDATE chats SET assistant_id=?, updated_at=? WHERE id=?", (aid, now(), cid))
        self.db.commit()

    # ---------- 记忆（全局/项目两层；真实现全在 memory_store） ----------
    def memory(self, task_id: str, kind: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """老接口保持形状不变（kind 回映射成 long/short），server 和前端不用改。
        kind='long' → 非 episode；'short' → episode。"""
        memory_store.ready(self)
        if kind == "short":
            sql, args = "SELECT * FROM memory WHERE scope_id=? AND kind='episode'", [task_id]
        elif kind == "long":
            sql, args = "SELECT * FROM memory WHERE scope_id=? AND kind<>'episode'", [task_id]
        else:
            sql, args = "SELECT * FROM memory WHERE scope_id=?", [task_id]
        rows = self.db.execute(sql + " ORDER BY importance DESC, updated_at LIMIT ?", args + [limit])
        out = []
        for row in rows:
            item = dict(row)
            item["kind"] = "short" if item["kind"] == "episode" else "long"
            out.append(item)
        return out

    def add_memory(self, task_id: str, kind: str, text: str) -> dict[str, Any]:
        return memory_store.add(self, text, scope_id=task_id,
                                kind="episode" if kind == "short" else "fact")

    def dump_memory(self, task_id: str) -> None:
        memory_store.dump(self, task_id)

    def seed_long_memory(self, task_id: str) -> int:
        """长期记忆的底子：新建项目时填的那几个点（只在空的时候种一次）。"""
        if self.memory(task_id, "long"):
            return 0
        t = self.db.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            return 0
        points = [("项目", t["title"]), ("研究问题", t["question"]),
                  ("关键词 / 材料体系", t["material_system"])]
        n = 0
        for k, v in points:
            if (v or "").strip():
                memory_store.add(self, f"{k}：{v.strip()}", scope_id=task_id, source="seed", importance=2)
                n += 1
        return n

    def memory_block(self, task_id: str, now_window: int = 10, line: int = 120) -> str:
        """拼给模型看的记忆块：全局 → 项目 → 窗口外的老对话。"""
        self.seed_long_memory(task_id)                  # 新项目先种三条底子（幂等）
        return memory_store.block(self, task_id, now_window, line)


    def stage_map(self, task_id: str) -> dict[str, str]:
        import workflow_state
        return {step: item["status"] for step, item in workflow_state.states(self, task_id).items()}

    def stage_now(self, task_id: str, fallback: str = "") -> str:
        """项目现在走到哪一步：十步里「按顺序最靠后的已完成步」（和看板同一个口径）。
        一条步骤记录都没有的项目才按任务自身阶段兜底。"""
        has_step = self.db.execute("SELECT 1 FROM steps WHERE task_id=? LIMIT 1", (task_id,)).fetchone()
        if has_step:
            import workflow_state
            states = workflow_state.states(self, task_id)
            return STAGE_LABEL.get(current_stage_key(self.db, task_id, list(states), states), "未开始")
        return {"definition": STAGE_LABEL["idea"], "reading": STAGE_LABEL["download"]}.get(fallback, "未开始")

    @project_notes.synchronized
    def delete_task(self, task_id: str) -> dict[str, Any]:
        """删掉一个项目连带它的全部数据。SQLite 默认不启用外键约束，子表得自己清
        （paper_pages / evidence 没有 task_id，只能经 papers 关联）。
        磁盘上只删 data/papers 里我们自己存的 PDF 副本，用户自己的原文件不动。"""
        if self.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone() is None:
            raise KeyError(task_id)
        from run_manager import RunManager
        manager = RunManager.get_instance()
        async_ids = [r["id"] for r in self.db.execute("SELECT id FROM tasks WHERE owner_id=?", (task_id,))]
        for rid in async_ids:
            manager.cancel_run(rid)
        import team
        import schedule
        for row in self.db.execute("SELECT id FROM schedules WHERE project_id=?", (task_id,)):
            schedule.cancel_run(row["id"], self.path)
        for row in self.db.execute("SELECT id FROM team_tasks WHERE task_id=?", (task_id,)):
            signal = team._cancel_signals.get(row["id"])
            if signal:
                signal.set()
        import semantic
        semantic.invalidate_project(self, task_id)
        self.db.execute("DELETE FROM task_attempts WHERE task_id IN (SELECT id FROM tasks WHERE owner_id=?)", (task_id,))
        self.db.execute("DELETE FROM run_events WHERE run_id IN (SELECT id FROM tasks WHERE owner_id=?)", (task_id,))
        self.db.execute("DELETE FROM tasks WHERE owner_id=?", (task_id,))
        self.db.execute("DELETE FROM schedule_workflow_runs WHERE schedule_run_id IN (SELECT r.id FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id WHERE s.project_id=?)", (task_id,))
        self.db.execute("DELETE FROM schedule_workflows WHERE schedule_id IN (SELECT id FROM schedules WHERE project_id=?)", (task_id,))
        self.db.execute("DELETE FROM schedule_runs WHERE schedule_id IN (SELECT id FROM schedules WHERE project_id=?)", (task_id,))
        self.db.execute("DELETE FROM schedules WHERE project_id=?", (task_id,))
        self.db.execute("DELETE FROM task_links WHERE project_id=?", (task_id,))
        files = [r["source_path"] for r in self.db.execute("SELECT source_path FROM papers WHERE task_id=?", (task_id,))]
        self.db.execute("DELETE FROM paper_pages WHERE paper_id IN (SELECT id FROM papers WHERE task_id=?)", (task_id,))
        self.db.execute("DELETE FROM evidence WHERE paper_id IN (SELECT id FROM papers WHERE task_id=?)", (task_id,))
        tables_to_clean = (
            "papers", "artifacts", "runs", "calendar_events", "notes", "steps",
            "search_cache", "chat", "paper_graph", "kb_graph", "project_memory",
            "md_notes", "dl_queue", "team_tasks", "team_events",
            "project_workflow_layouts", "zotero_bindings", "zotero_project_sources", "zotero_item_links", "zotero_project_memberships", "zotero_link_batches",
            "project_paper_links",
            "task_attempts", "audit_events", "project_todos", "background_jobs",
            "idea_input", "tr_hl",
        )
        for table in tables_to_clean:
            try:
                self.db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
            except Exception:
                pass
        # workflow_runs is keyed by project_id rather than task_id. Keep this
        # explicit so project deletion cannot leave orphaned resumable runs.
        try:
            run_ids = [r["id"] for r in self.db.execute(
                "SELECT id FROM workflow_runs WHERE project_id=?", (task_id,))]
            if run_ids:
                marks = ",".join("?" for _ in run_ids)
                self.db.execute(f"DELETE FROM review_reminders WHERE node_id IN (SELECT id FROM workflow_nodes WHERE run_id IN ({marks}))", run_ids)
                self.db.execute(f"DELETE FROM schedule_workflow_runs WHERE workflow_run_id IN ({marks})", run_ids)
                self.db.execute(f"DELETE FROM workflow_nodes WHERE run_id IN ({marks})", run_ids)
                self.db.execute(f"DELETE FROM workflow_executions WHERE run_id IN ({marks})", run_ids)
                self.db.execute(f"DELETE FROM workflow_runs WHERE id IN ({marks})", run_ids)
        except Exception:
            pass
        try:
            asset_ids = [r["id"] for r in self.db.execute("SELECT id FROM assets WHERE project_id=?", (task_id,))]
            if asset_ids:
                marks = ",".join("?" for _ in asset_ids)
                self.db.execute(f"DELETE FROM asset_fts WHERE asset_id IN ({marks})", asset_ids)
                self.db.execute(f"DELETE FROM asset_provenance WHERE asset_id IN ({marks})", asset_ids)
                self.db.execute(f"DELETE FROM asset_relations WHERE from_asset_id IN ({marks}) OR to_asset_id IN ({marks})", asset_ids * 2)
                self.db.execute(f"DELETE FROM asset_revisions WHERE asset_id IN ({marks})", asset_ids)
                self.db.execute(f"DELETE FROM assets WHERE id IN ({marks})", asset_ids)
        except Exception:
            pass
        try:
            self.db.execute("UPDATE chats SET task_id='' WHERE task_id=?", (task_id,))
        except Exception:
            pass
        self.db.execute("DELETE FROM research_tasks WHERE id=?", (task_id,))
        self.db.commit()
        removed = 0
        for raw in files:
            p = Path(raw)
            try:
                if p.is_file() and PAPERS in p.resolve().parents:
                    p.unlink()
                    fileguard.audit("delete_paper_file", p, task_id=task_id)
                    removed += 1
            except OSError:
                pass
        return {"deleted": True, "id": task_id, "files_removed": removed}

    # ---------- 日历 ----------
    def add_event(self, title: str, date: str, task_id: str | None = None, kind: str = "milestone",
                  note: str = "", time: str = "") -> dict[str, Any]:
        if not title.strip() or not re.match(r"^\d{4}-\d{2}-\d{2}$", date or ""):
            raise ValueError("日历事件需要标题和 YYYY-MM-DD 日期")
        title = title.strip()
        if not time:                                             # 模型有时仍把钟点写进标题：「14:00 参加学术会议」
            m = re.match(r"^(\d{1,2}\s*[:：]\s*\d{1,2})\s*(.+)$", title)
            if m:
                time, title = _norm_hhmm(m.group(1)), m.group(2).strip()
        time = _norm_hhmm(time)                                  # 空 = 没定时间；'9:5' → '09:05'
        row = (uuid.uuid4().hex, task_id or None, title, date, kind, note, time, now())
        self.db.execute("INSERT INTO calendar_events (id,task_id,title,date,kind,note,time,created_at) VALUES (?,?,?,?,?,?,?,?)", row)
        self.db.commit()
        return self.event(row[0])

    def toggle_event(self, event_id: str, done: bool | None = None) -> dict[str, Any]:
        """勾掉/取消勾掉今日事项（今日工作计划那个复选框）。"""
        import calendar_service
        if event_id.startswith(calendar_service.PREFIX):
            return calendar_service.toggle(self, event_id, done)
        cur = self.event(event_id)
        value = (0 if cur["done"] else 1) if done is None else int(bool(done))
        self.db.execute("UPDATE calendar_events SET done=? WHERE id=?", (value, event_id))
        self.db.commit()
        return self.event(event_id)

    def event(self, event_id: str) -> dict[str, Any]:
        import calendar_service
        if event_id.startswith(calendar_service.PREFIX):
            return calendar_service.event(self, event_id)
        row = self.db.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        return dict(row)

    def events(self, month: str | None = None) -> list[dict[str, Any]]:
        # 同一天按钟点排，没定时间的排当天最后；再往后按落库时间
        order = "ORDER BY date, COALESCE(NULLIF(time,''),'99:99'), created_at"
        if month:
            rows = self.db.execute(f"SELECT * FROM calendar_events WHERE date LIKE ? {order}", (month + "%",))
        else:
            rows = self.db.execute(f"SELECT * FROM calendar_events {order}")
        import calendar_service
        return sorted([*[dict(r) for r in rows], *calendar_service.derived(self, month)],
                      key=lambda e: (e["date"], e.get("time") or "99:99", e["created_at"]))

    def delete_event(self, event_id: str) -> None:
        import calendar_service
        if event_id.startswith(calendar_service.PREFIX):
            raise ValueError("此日程由关联任务自动维护，请到来源中修改、暂停或删除")
        self.db.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
        self.db.commit()

    # ---------- 模型档案 ----------
    def add_model(self, name: str, base_url: str, model: str, api_key: str = "", activate: bool = False) -> dict[str, Any]:
        if not name.strip() or not base_url.strip() or not model.strip():
            raise ValueError("模型档案需要 名称/接口地址/模型名")
        mid = uuid.uuid4().hex
        self.db.execute("INSERT INTO model_profiles (id,name,base_url,api_key,model,is_active,created_at,key_host) VALUES (?,?,?,?,?,?,?,?)", (mid, name.strip(), base_url.strip().rstrip("/"), _stored_key(api_key), model.strip(), 0, now(),
                        _host_of(base_url) if api_key.strip() else ""))
        self.db.commit()
        if activate or not self.active_model():
            self.set_active(mid)
        return self.model(mid)

    def model(self, mid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM model_profiles WHERE id=?", (mid,)).fetchone()
        if not row:
            raise KeyError(mid)
        import usage_stats
        return usage_stats.profile(self,_reveal_key(dict(row)))

    def models(self) -> list[dict[str, Any]]:
        return [_reveal_key(dict(r)) for r in self.db.execute("SELECT * FROM model_profiles ORDER BY created_at")]

    def set_active(self, mid: str) -> dict[str, Any]:
        self.model(mid)  # 不存在则抛 KeyError
        self.db.execute("UPDATE model_profiles SET is_active=0")
        self.db.execute("UPDATE model_profiles SET is_active=1 WHERE id=?", (mid,))
        self.db.commit()
        return self.model(mid)

    def delete_model(self, mid: str) -> None:
        self.db.execute("DELETE FROM model_profiles WHERE id=?", (mid,))
        self.db.commit()

    def active_model(self) -> dict[str, Any] | None:
        # Model-backed tools in a workflow node use its selected assistant/member.
        if getattr(self, "runtime_model", None) is not None:
            return self.runtime_model
        row = self.db.execute("SELECT * FROM model_profiles WHERE is_active=1").fetchone()
        import usage_stats
        return usage_stats.profile(self,_reveal_key(dict(row))) if row else None

    # ---------- Provider（模型与 Agent 卡片） ----------
    def providers(self) -> list[dict[str, Any]]:
        """只返回库里已有的行；内置目录里没动过的 Provider 由前端补卡片。"""
        out = []
        for r in self.db.execute("SELECT * FROM providers ORDER BY created_at"):
            d = dict(r)
            try:
                d["models"] = json.loads(d["models"])
            except ValueError:
                d["models"] = []
            out.append(_reveal_key(d))
        return out

    def provider(self, pid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
        if row:
            d = dict(row)
            try:
                d["models"] = json.loads(d["models"])
            except ValueError:
                d["models"] = []
            return _reveal_key(d)
        if pid in PROVIDER_CATALOG:            # 还没落库的内置 Provider：给一份只读视图
            c = PROVIDER_CATALOG[pid]
            return {"id": pid, "name": c["name"], "base_url": c["base_url"] or "",
                    "api": c["wire"] or "openai-completions", "api_key": "", "models": [],
                    "source": "builtin", "enabled": 0, "created_at": now()}
        raise KeyError(pid)

    def ensure_provider(self, pid: str) -> dict[str, Any]:
        """首次写入（存 Key / 采用模型）时才把内置 Provider 落库。"""
        row = self.db.execute("SELECT id FROM providers WHERE id=?", (pid,)).fetchone()
        if row:
            return self.provider(pid)
        if pid not in PROVIDER_CATALOG:
            raise KeyError(pid)
        c = PROVIDER_CATALOG[pid]
        self.db.execute("INSERT INTO providers (id,name,base_url,api,api_key,models,source,enabled,created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (pid, c["name"], c["base_url"] or "", c["wire"] or "openai-completions",
                         "", "[]", "builtin", 0, now()))
        self.db.commit()
        return self.provider(pid)

    def add_provider(self, name: str, base_url: str, api: str = "openai-completions", api_key: str = "") -> dict[str, Any]:
        if not name.strip() or not base_url.strip():
            raise ValueError("Provider 需要名称和接口地址")
        pid = "custom:" + uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO providers (id,name,base_url,api,api_key,models,source,enabled,created_at,key_host)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (pid, name.strip(), base_url.strip().rstrip("/"), api, _stored_key(api_key),
                         "[]", "custom", 1 if api_key.strip() else 0, now(),
                         _host_of(base_url) if api_key.strip() else ""))
        self.db.commit()
        return self.provider(pid)

    def set_provider_key(self, pid: str, api_key: str) -> dict[str, Any]:
        p = self.ensure_provider(pid)
        self.db.execute("UPDATE providers SET api_key=?, key_host=?, enabled=? WHERE id=?",
                        (_stored_key(api_key), _host_of(p["base_url"]) if api_key.strip() else "",
                         1 if api_key.strip() else 0, pid))
        self.db.commit()
        import agent_settings
        agent_settings.sync_provider_models(self, pid)
        return self.provider(pid)

    def delete_provider(self, pid: str) -> None:
        """内置的只能复位（卡片要一直在）；自定义的整行删掉。"""
        row = self.db.execute("SELECT source FROM providers WHERE id=?", (pid,)).fetchone()
        if not row or row["source"] == "custom":
            self.db.execute("DELETE FROM providers WHERE id=?", (pid,))
        else:
            self.db.execute("UPDATE providers SET api_key='', models='[]', enabled=0 WHERE id=?", (pid,))
        self.db.execute("DELETE FROM model_profiles WHERE provider_id=?", (pid,))
        self.db.commit()

    def adopt_model(self, pid: str, model: str, on: bool = True) -> dict[str, Any]:
        model = model.strip()
        if not model:
            raise ValueError("模型 id 不能为空")
        p = self.ensure_provider(pid)
        names = list(p["models"])
        if on and model not in names:
            names.append(model)
        if not on and model in names:
            names.remove(model)
        self.db.execute("UPDATE providers SET models=?, enabled=? WHERE id=?",
                        (json.dumps(names, ensure_ascii=False), 1 if (names or p["api_key"]) else 0, pid))
        self.db.commit()
        import agent_settings
        agent_settings.sync_provider_models(self, pid)
        if on and not self.active_model():          # 还没有默认模型 → 这次采用的就当默认，省得 Agent 说「没配模型」
            try:
                self.set_default(pid, model)
            except ValueError:
                pass
        return self.provider(pid)

    def set_default(self, pid: str, model: str) -> dict[str, Any]:
        """把「某 Provider 的某个模型」落成当前模型 —— 写在 model_profiles 上，下游 agent 不用改。"""
        p = self.provider(pid)
        if not model.strip():
            raise ValueError("先采用一个模型，再设为默认")
        if p["api"] not in USABLE_WIRES:
            raise ValueError(f"暂不支持运行协议：{WIRE_LABEL.get(p['api'], p['api'])}")
        if model not in p["models"]:
            self.adopt_model(pid, model)
        import agent_settings
        agent_settings.sync_provider_models(self, pid)
        row = self.db.execute("SELECT id FROM model_profiles WHERE provider_id=? AND model=?", (pid, model)).fetchone()
        return self.set_active(row["id"])

    def provider_default(self) -> dict[str, Any] | None:
        m = self.active_model()
        if not m:
            return None
        row = self.db.execute("SELECT id FROM providers WHERE base_url=?", (m["base_url"],)).fetchone()
        if row:
            return {"provider_id": row["id"], "model": m["model"]}
        for pid, c in PROVIDER_CATALOG.items():       # 还没落库的内置 Provider
            if c["base_url"] == m["base_url"]:
                return {"provider_id": pid, "model": m["model"]}
        return {"provider_id": None, "model": m["model"]}

    # ---------- 检索缓存 ----------
    def cache_results(self, task_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        saved = []
        for it in items:
            if not it.get("title"):
                continue
            key = (self.db.execute(
                "SELECT 1 FROM search_cache WHERE task_id=? AND source=? "
                "AND (lower(trim(doi))=lower(trim(?)) AND trim(doi)!='' OR title=? AND (trim(doi)='' OR trim(?)=''))",
                (task_id, it.get("source", ""), it.get("doi", ""), it.get("title", ""), it.get("doi", "")),
            ).fetchone())
            if key:
                continue
            rid = uuid.uuid4().hex
            row = (rid, task_id, it.get("source", ""), it["title"], it.get("authors", ""), it.get("year", ""),
                   it.get("doi", ""), it.get("url", ""), it.get("pdf_url", ""), it.get("abstract", ""),
                   float(it.get("score") or 0), now())
            self.db.execute(
                "INSERT INTO search_cache (id,task_id,source,title,authors,year,doi,url,pdf_url,abstract,score,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            saved.append(self.result(rid))
        self.db.commit()
        import project_library
        for item in saved:
            project_library.index_record(self, task_id, item["id"])
        return saved

    def cache_result(self, task_id: str, it: dict[str, Any], *, match_title: bool = True) -> str:
        """一条结果写进 search_cache 并返回 id；已存在（同 DOI/同标题）就返回旧 id，队列才能复用。"""
        title = (it.get("title") or "").strip()
        if not title:
            raise ValueError("这条没有标题，跳过")
        row = self.db.execute(
            "SELECT id FROM search_cache WHERE task_id=? AND source=? "
            "AND (lower(trim(doi))=lower(trim(?)) AND trim(doi)!='' OR ? AND title=? AND (trim(doi)='' OR trim(?)=''))",
            (task_id, it.get("source", ""), it.get("doi", ""), match_title, title, it.get("doi", "")),
        ).fetchone()
        if row:
            return row["id"]
        rid = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO search_cache (id,task_id,source,title,authors,year,doi,url,pdf_url,abstract,score,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, task_id, it.get("source", ""), title, it.get("authors", ""), it.get("year", ""),
             it.get("doi", ""), it.get("url", ""), it.get("pdf_url", ""), it.get("abstract", ""),
             float(it.get("score") or 0), now()))
        self.db.commit()
        import project_library
        project_library.index_record(self, task_id, rid)
        return rid

    def result(self, rid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM search_cache WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        return dict(row)

    def results(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM search_cache WHERE task_id=? ORDER BY score DESC, created_at DESC, year DESC", (task_id,))
        import project_library
        return project_library.visible(self, task_id, [dict(r) for r in rows])

    # ---------- 笔记 ----------
    @project_notes.synchronized
    def add_note(self, task_id: str, title: str, markdown: str, paper_id: str | None = None,
                 category: str = NOTE_CATEGORY) -> dict[str, Any]:
        import note_catalog
        if self.base_dir(task_id):
            catalog = note_catalog.initialize(self, task_id)
            key = catalog.get("legacy_targets", {}).get(category, category)
            found = note_catalog.category(catalog, key) or next((c for c in catalog["categories"] if c["name"] == category), None)
            markdown = note_catalog.update_content(markdown, changes={
                "category_id": found["id"] if found else "", "tags": note_catalog.LEGACY.get(category, ("", []))[1]}, catalog=catalog)
        nid = uuid.uuid4().hex
        self.db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)", (nid, task_id, paper_id, title.strip() or "未命名笔记", markdown, now()))
        self.db.commit()
        import asset_service
        path = self._project_note_dir(task_id, category) / f"{nid}.md"

        asset_service.atomic_write_text(path, markdown)
        asset_service.register_file(self, path, project_id=task_id, kind="note",
                                    title=title.strip() or "未命名笔记", source="workflow", body=markdown,
                                    metadata={"paper_id": paper_id or "", "legacy_id": nid})
        self._link_note_paper_asset(task_id, paper_id or "", path)
        out = self.note(nid)
        out["canonical_path"] = str(path)
        return out

    def note(self, nid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        if not row:
            raise KeyError(nid)
        return dict(row)

    def notes(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id:
            rows = self.db.execute("SELECT * FROM notes WHERE task_id=? ORDER BY created_at DESC", (task_id,))
        else:
            rows = self.db.execute("SELECT * FROM notes ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def papers_of(self, task_id: str) -> list[dict[str, Any]]:
        """项目文献列表：带上标题/作者/DOI（paper_meta，缺的当场解析一次）。"""
        import papergraph
        papergraph.ensure_meta(self.db, task_id)
        rows = self.db.execute(
            "SELECT p.*, COALESCE(m.title,'') AS title, COALESCE(m.authors,'') AS authors,"
            " COALESCE(m.doi,'') AS doi, COALESCE(m.year,'') AS year"
            " FROM papers p LEFT JOIN paper_meta m ON m.paper_id=p.id"
            " WHERE p.task_id=? ORDER BY p.created_at DESC", (task_id,))
        import project_library
        return project_library.visible(self, task_id, [dict(r) for r in rows], papers=True)

    def paper_graph(self, task_id: str) -> dict[str, Any]:
        """论文星图（读缓存）：只在用户点「生成」时算一次（build_paper_graph），平时返回上次结果。
        没生成过 → 空图 + built_at 空，前端据此显示空态和「生成星图」按钮。"""
        row = self.db.execute("SELECT json, built_at, took_ms FROM paper_graph WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return {"nodes": [], "edges": [], "stats": {}, "built_at": "", "took_ms": 0}
        try:
            g = json.loads(row["json"] or "{}")
        except ValueError:
            g = {}
        g["built_at"] = row["built_at"]
        g["took_ms"] = row["took_ms"]
        return g

    def build_paper_graph(self, task_id: str) -> dict[str, Any]:
        """编译一次星图并落库。扫库内 PDF 全文算引用关系，几秒到几十秒，所以不给自动跑。"""
        import papergraph
        t0 = time.time()
        g = papergraph.graph(self.db, task_id)
        took = int((time.time() - t0) * 1000)
        built = now()
        self.db.execute("INSERT OR REPLACE INTO paper_graph (task_id,json,built_at,took_ms) VALUES (?,?,?,?)",
                        (task_id, json.dumps(g, ensure_ascii=False), built, took))
        self.db.commit()
        g["built_at"], g["took_ms"] = built, took
        return g

    def kb_graph(self, task_id: str, rebuild: bool = False) -> dict[str, Any]:
        """知识星图（编译产物）：第一次进「知识库」时算一次落库，之后只读这份，不实时重算。
        rebuild=True（点了「重算星图」）才重新扫库编译。"""
        if not rebuild:
            row = self.db.execute("SELECT json, built_at FROM kb_graph WHERE task_id=?", (task_id,)).fetchone()
            if row:
                try:
                    g = json.loads(row["json"] or "{}")
                except ValueError:
                    g = {}
                g["built_at"] = row["built_at"]
                return g
        import starmap
        g = starmap.graph(self.db, task_id)
        g["built_at"] = now()
        self.db.execute("INSERT OR REPLACE INTO kb_graph (task_id,json,built_at) VALUES (?,?,?)",
                        (task_id, json.dumps(g, ensure_ascii=False), g["built_at"]))
        self.db.commit()
        return g

    # ---------- 创新点审查：上区原文 + 下区判定 ----------
    def idea_input(self, task_id: str) -> str:
        row = self.db.execute("SELECT text FROM idea_input WHERE task_id=?", (task_id,)).fetchone()
        return (row["text"] if row else "") or ""

    def idea_input_save(self, task_id: str, text: str) -> int:
        text = str(text or "")
        self.db.execute("INSERT OR REPLACE INTO idea_input (task_id,text,updated_at) VALUES (?,?,?)",
                        (task_id, text, now()))
        self.db.commit()
        return len(text)

    def idea_verdict(self, task_id: str) -> dict[str, Any]:
        """左边下区那份「判定结果」= 这一步最近一次 idea_review 落下的项目笔记。"""
        for n in self.notes(task_id):
            if (n.get("title") or "").startswith("创新点审查"):
                return {"id": n["id"], "title": n["title"], "markdown": n["markdown"],
                        "created_at": n["created_at"]}
        return {}

    # ---------- 论文翻译：荧光笔标注（专家要看得到圈了什么）----------
    def tr_hl(self, paper_id: str, task_id: str = "") -> list[dict[str, Any]]:
        row = self.db.execute("SELECT json FROM tr_hl WHERE paper_id=? AND task_id=?",
                              (paper_id, task_id)).fetchone()
        try:
            return json.loads(row["json"]) if row else []
        except (ValueError, TypeError):
            return []

    def tr_hl_save(self, paper_id: str, task_id: str, items: list[dict[str, Any]]) -> int:
        self.db.execute("INSERT OR REPLACE INTO tr_hl (paper_id,task_id,json,updated_at) VALUES (?,?,?,?)",
                        (paper_id, task_id, json.dumps(items or [], ensure_ascii=False), now()))
        self.db.commit()
        return len(items or [])

    def tr_hl_context(self, task_id: str, limit: int = 12) -> str:
        """给通用 Agent 的一段上下文：用户最近在翻译页用荧光笔圈的内容（按论文取最近更新的那篇）。"""
        row = self.db.execute("SELECT paper_id FROM tr_hl WHERE task_id=? ORDER BY updated_at DESC LIMIT 1",
                              (task_id,)).fetchone()
        if not row:
            return ""
        seen, out = set(), []
        for h in self.tr_hl(row["paper_id"], task_id):
            t = (h.get("t") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(f"第 {h.get('p') or '?'} 页：{t}")
            if len(out) >= limit:
                break
        if not out:
            return ""
        return ("用户在论文翻译里用荧光笔圈了这些（他说「这句/这里」就是指它们；引用原文，别改数字）：\n"
                + "\n".join(out))

    # ---------- Agent 对话记录 ----------
    def add_message(self, task_id: str, role: str, content: str, events: list | None = None,
                    scope: str = "", sender: str = "") -> dict[str, Any]:
        row = (uuid.uuid4().hex, task_id, role, content, json.dumps(events or [], ensure_ascii=False), now(), scope, sender)
        self.db.execute("INSERT INTO chat (id,task_id,role,content,events,created_at,scope,sender)"
                        " VALUES (?,?,?,?,?,?,?,?)", row)
        self.db.commit()
        return self.messages(task_id, scope)[-1]

    def add_chat(self, task_id: str, role: str, content: str, events: list | None = None,
                 scope: str = "", sender: str = "") -> dict[str, Any]:
        """别名兼容：避免外部模块或旧调用误把 add_message 写成 add_chat 抛 AttributeError"""
        return self.add_message(task_id, role, content, events, scope, sender)

    def seed_welcome(self) -> str:
        """新用户首启：种一条「小智」欢迎对话 —— 真实 chat + 一条 assistant 消息，
        在对话列表里能看见、能像普通对话一样删掉。已经有对话就什么都不做。
        只该在「这个库一个项目都还没有」的首启路径里调用（见 server.main），删掉后不会再冒出来。"""
        if self.chats():
            return ""
        cid = self.create_chat("小智")
        self.add_message(cid, "assistant", WELCOME_TEXT, [], scope="")
        return cid

    def messages(self, task_id: str, scope: str | None = None) -> list[dict[str, Any]]:
        """scope=None 拿全部（老行为）；给了 scope 就只看这个步骤的对话。
        「创新点审查」把老库里 scope='' 的也算上（那时还没分家），其余步骤各看各的。"""
        if scope in ("", "idea"):
            sql, args = "SELECT * FROM chat WHERE task_id=? AND (scope='' OR scope=?) ORDER BY created_at", (task_id, scope or "idea")
        elif scope is None:
            sql, args = "SELECT * FROM chat WHERE task_id=? ORDER BY created_at", (task_id,)
        else:
            sql, args = "SELECT * FROM chat WHERE task_id=? AND scope=? ORDER BY created_at", (task_id, scope)
        rows = self.db.execute(sql, args)
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["events"] = json.loads(d["events"])
            except ValueError:
                d["events"] = []
            out.append(d)
        return out

    def clear_messages(self, task_id: str) -> int:
        n = self.db.execute("SELECT COUNT(*) n FROM chat WHERE task_id=?", (task_id,)).fetchone()["n"]
        self.db.execute("DELETE FROM chat WHERE task_id=?", (task_id,))
        self.db.commit()
        return n


# ============================ 模型调用 ============================

