"""览籍智研 · 工作流引擎

在 research_core.ResearchDB 之上补齐「创新点审查 → 论文搜索 → 下载精读 → 知识库笔记
→ 文章撰写 → 审查 → 格式转换」七步工作流，外加任务/日历体系、模型切换、主题色。

设计原则（沿用 1.0）：
  * 证据优先：候选证据一律带页码 + 原文片段，走 research_core 的严格闸门。
  * 只走开放获取：下载只对 OA 链接放行，拿不到就如实返回「无 OA 全文」。
  * 模型只做草稿与建议：审查/撰写产出是草稿，不是结论。
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os

import fileguard        # 文件安全：子进程环境洗白 / 写前快照 / 打开白名单 / 审计
import guards          # 出站脱敏 + 引用核验（见 guards.py）
import paths            # 唯一路径来源（data / user / executors 都在 HOME 下）
import securestore      # 凭据保护（DPAPI，见 securestore.py）
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

from research_core import ResearchDB

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1] if (HERE.parents[1] / "packages").is_dir() else HERE   # 程序根（只读资源）
DATA, PAPERS, NOTES, OUT = paths.DATA, paths.PAPERS, paths.NOTES, paths.OUT   # 唯一路径来源
UA = {"User-Agent": "research-workbench-1.0 (mailto:local@localhost)"}


# ---------------- 凭据：库里只存密文（DPAPI），读出来才解 ----------------
# ponytail: 只挡「整份拷走 data/ 就等于拿走 key」这一层；不挡「同一 Windows 用户下的本机进程」
# （那需要给 HTTP 加 token / 降权运行，见 docs/交接.md 的安全一节）。
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

# 内置 Provider 目录：从科研工作台内嵌的 pi-ai SDK 里导出（40 家），不手抄
# 见同目录 providers_catalog.json（id / name / base_url / wire / auth / 模型 id）
CATALOG_PATH = next((p for p in (
    ROOT / "packages" / "contracts" / "providers_catalog.json",   # 源码树
    ROOT / "contracts" / "providers_catalog.json",                # 打包后（数据按 datas 落位）
    HERE / "providers_catalog.json",
) if p.is_file()), ROOT / "packages" / "contracts" / "providers_catalog.json")
try:
    PROVIDER_CATALOG: dict[str, dict[str, Any]] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    PROVIDER_CATALOG = {}

# 接入协议（wire）—— 卡片上的徽标；本机真正跑得通的只有 openai-completions
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
USABLE_WIRES = {"openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"}
CUSTOM_WIRES = ["openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"]
# 「订阅 / OAuth 登录」时给用户打开的授权页；设备码流需要各家的 client_id，
# 这里不假装能自动授权（ponytail: 真有 client_id 时再把 RFC 8628 轮询加进来）
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

STAGES = ["idea", "search", "download", "notes", "outline", "map", "write", "review", "entrans", "convert"]
STAGE_LABEL = {
    "idea": "创新点审查", "search": "论文搜索", "download": "下载精读",
    "notes": "论文翻译", "outline": "确认大纲", "map": "梳理精读", "write": "文章撰写", "review": "审查",
    "entrans": "中译英", "convert": "格式转换",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
# 内置定时规则（DEFAULT 清单）：4 条，id 固定，
# 这样重复播种是「只补缺」而不是反复插入；prompt 换成我们这套（检索 → 精读 → 落盘）的说法。
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

PROJ_DIRS: list[tuple[str, str]] = [
    ("记忆", "项目记忆的落盘副本：长期记忆.md（项目要点与重要结论）、短期记忆.md（对话内容与细节）。由「创新点审查」等步骤自动维护，人工也可直接编辑，下一步再跑时会读回来。"),
    ("文献库", "开放获取(OA) 全文 PDF：文件名 = 论文标题；只放 OA 全文，付费墙文献请走学校图书馆。"),
    ("笔记", "逐篇精读的 Markdown 笔记：一份笔记一个 .md 文件，含证据表与页码。"),
    ("论文正文", "章节草稿 Markdown：draft-*.md，按章节名命名，是「文章撰写」这一步的直接产物。"),
    ("对话", "对话与过程记录：按日期分 YYYY-MM-DD 子目录，当天产生的记录都落在当天那一层。"),
    ("最终产物", "最终交付件：中文 Word(.docx)、LaTeX(.tex)、参考文献(.bib)、成稿 PDF(.pdf)、图片(PNG/SVG) 和画图代码(.py) 都放这里。"),
    ("其余", "工作流产生的其它杂项文件：中间态导出、脚本、临时数据等，不影响主流程。"),
]


class WorkflowDB(ResearchDB):

    def __init__(self, path: str | Path):
        super().__init__(path)
        self.db.executescript(WORKFLOW_SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(chats)")}
        if "assistant_id" not in cols:                # 老库补列：对话绑的助手（我的/公开）
            self.db.execute("ALTER TABLE chats ADD COLUMN assistant_id TEXT NOT NULL DEFAULT ''")
        if "task_id" not in cols:                     # 老库补列：对话绑的项目（'' = 随意对话）
            self.db.execute("ALTER TABLE chats ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
        # 老库补列（providers 是这次新做的表，早期版本只有 6 列）
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
        if "scope" not in cols:                         # 老库补列：各步骤的对话分开存（原来只有创新点审查一个）
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
        cols_runs = {r["name"] for r in self.db.execute("PRAGMA table_info(schedule_runs)")}
        if "name" not in cols_runs:
            self.db.execute("ALTER TABLE schedule_runs ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        if "trigger" not in cols_runs:
            self.db.execute("ALTER TABLE schedule_runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'scheduler'")
        if "reason" not in cols_runs:
            self.db.execute("ALTER TABLE schedule_runs ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
        self.assistant_seed(BUILTIN_ASSISTANTS)      # 公开助手（内置）：只补缺，不覆盖改过的
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
        if "assistant_id" not in cols:                 # 老库补列：成员 = 哪个助手
            self.db.execute("ALTER TABLE team_members ADD COLUMN assistant_id TEXT NOT NULL DEFAULT ''")
        if "color" not in cols:                        # 老库补列：成员身份色序号
            self.db.execute("ALTER TABLE team_members ADD COLUMN color INTEGER NOT NULL DEFAULT 0")
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(chat)")}
        if "sender" not in cols:                       # 老库补列：团队群里这条是谁说的
            self.db.execute("ALTER TABLE chat ADD COLUMN sender TEXT NOT NULL DEFAULT ''")
        import agent_settings
        agent_settings.migrate(self)
        for _t in ("providers", "model_profiles"):        # key 绑定的域名（换域名就不许拿旧 key 发）
            _cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({_t})")}
            if "key_host" not in _cols:
                self.db.execute(f"ALTER TABLE {_t} ADD COLUMN key_host TEXT NOT NULL DEFAULT ''")
        encrypt_legacy_secrets(self)                   # 老库里的明文 key → DPAPI 密文（幂等）
        self.db.commit()
        for folder in (PAPERS, NOTES, OUT):
            folder.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "WorkflowDB":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- 任务 / 步骤 ----------
    def log_step(self, task_id: str, stage: str, status: str, summary: str = "") -> dict[str, Any]:
        row = (uuid.uuid4().hex, task_id, stage, status, summary, now())
        self.db.execute("INSERT INTO steps VALUES (?,?,?,?,?,?)", row)
        self.db.commit()
        return {"id": row[0], "stage": stage, "status": status, "summary": summary, "created_at": row[5]}

    def steps(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY created_at DESC", (task_id,))
        return [dict(r) for r in rows]

    def out_dir(self, task_id: str) -> Path:
        """对话自己的产出目录（chats.out_dir）；没设就落在默认 data/out。"""
        row = self.db.execute("SELECT out_dir FROM chats WHERE id=?", (task_id,)).fetchone()
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

    def save_md_note(self, nid: str, title: str, content: str, task_id: str | None = None,
                     paper_id: str | None = None) -> dict[str, Any]:
        """task_id / paper_id 传 None 保持原绑定不变；传 "" 就是解绑。"""
        t = now()
        title = title.strip() or "未命名笔记"
        previous = self.md_note(nid) if nid and self.db.execute(
            "SELECT 1 FROM md_notes WHERE id=?", (nid,)).fetchone() else None
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
        note["canonical_path"] = str(path)
        return note

    def _md_note_path(self, note: dict[str, Any] | None) -> Path | None:
        if not note:
            return None
        folder = self.pdir(note["task_id"], "笔记") if note["task_id"] else NOTES
        return folder / f"{note['id']}.md"

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

    def delete_md_note(self, nid: str) -> None:
        note = self.md_note(nid)
        path = self._md_note_path(note)
        import asset_service
        assets = asset_service.find_by_metadata(self, kind="note", key="legacy_id", value=nid)
        self.db.execute("DELETE FROM md_notes WHERE id=?", (nid,))
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
        fallback = {"文献库": PAPERS, "笔记": NOTES, "论文正文": OUT, "最终产物": OUT, "其余": OUT, "记忆": OUT, "对话": OUT}
        base = self.base_dir(task_id)
        folder = (base / kind) if (base and kind in names) else fallback.get(kind, OUT)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ---------- 定时任务（规则 + 每次运行一条记录） ----------
    def schedules(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM schedules ORDER BY created_at").fetchall()]

    def schedule(self, sid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM schedules WHERE id=?", (str(sid or ""),)).fetchone()
        return dict(row) if row else None

    def schedule_save(self, data: dict[str, Any]) -> dict[str, Any]:
        sid = str(data.get("id") or "").strip() or uuid.uuid4().hex
        stamp = now()
        enabled = 1 if data.get("enabled", True) else 0
        fields = (str(data.get("name") or "未命名任务"), str(data.get("prompt") or ""),
                  str(data.get("project_id") or ""), str(data.get("frequency") or "daily"),
                  str(data.get("cron") or "0 9 * * *"), str(data.get("output_dir") or "定时任务"),
                  enabled, str(data.get("next_run_at") or ""))
        row = self.db.execute("SELECT id FROM schedules WHERE id=?", (sid,)).fetchone()
        if row:
            self.db.execute("""UPDATE schedules SET name=?, prompt=?, project_id=?, frequency=?, cron=?,
                output_dir=?, enabled=?, next_run_at=?, updated_at=? WHERE id=?""", (*fields, stamp, sid))
        else:
            self.db.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (sid, *fields[:6], enabled, "", fields[7], stamp, stamp))
        self.db.commit()
        return self.schedule(sid) or {}

    def schedule_set_enabled(self, sid: str, enabled: bool) -> None:
        self.db.execute("UPDATE schedules SET enabled=?, updated_at=? WHERE id=?",
                        (1 if enabled else 0, now(), str(sid or "")))
        self.db.commit()

    def schedule_delete(self, sid: str) -> None:
        self.db.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (str(sid or ""),))
        self.db.execute("DELETE FROM schedules WHERE id=?", (str(sid or ""),))
        self.db.commit()

    def schedule_seed(self, builtins: list[dict[str, Any]], project_id: str = "") -> int:
        """内置定时任务：只补缺，不覆盖用户改过的、也不复活用户删过的（照 assistant_seed）。"""
        n = 0
        for b in builtins:
            if self.db.execute("SELECT 1 FROM schedules WHERE id=?", (b["id"],)).fetchone():
                continue
            self.db.execute(
                "INSERT INTO schedules (id,name,prompt,project_id,frequency,cron,output_dir,enabled,"
                "last_run_at,next_run_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,'','',?,?)",
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
        rows = self.db.execute("SELECT * FROM schedule_runs ORDER BY started_at DESC LIMIT ?",
                               (max(1, min(500, int(limit))),)).fetchall()
        return [dict(r) for r in rows]

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
                        prompt: str = "", assistant_id: str = "", color: int = 0) -> dict[str, Any]:
        row = (uuid.uuid4().hex, task_id, name, role, step, prompt, "idle", "", now(), now(),
               assistant_id, int(color or 0))
        self.db.execute("INSERT INTO team_members (id,task_id,name,role,step,prompt,status,note,created_at,"
                       "updated_at,assistant_id,color) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM team_members WHERE id=?", (row[0],)).fetchone())

    def team_member_update(self, mid: str, **fields: Any) -> dict[str, Any]:
        import agent_settings
        allow = {k: v for k, v in fields.items() if k in ("name", "step", "prompt", "status", "note", "assistant_id", "model_id", "reasoning", "permission") and v is not None}
        agent_settings.validate_runtime_fields(self, allow)
        if allow.get("assistant_id"):
            self.require_assistant(allow["assistant_id"])
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
                      output_contract: dict[str, Any] | None = None, reviewer_id: str = "") -> dict[str, Any]:
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

    # ---------- 团队表（一个团队 = 一条记录；成员/记录都挂在它的 id 上） ----------
    def teams(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM teams ORDER BY created_at")]

    def team(self, tid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def team_add(self, name: str, kind: str = "fixed", project_id: str = "", purpose: str = "") -> dict[str, Any]:
        tid = uuid.uuid4().hex
        self.db.execute("INSERT INTO teams (id,name,kind,status,project_id,purpose,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (tid, (name or "新团队").strip(), kind, "idle", project_id or "", purpose or "", now(), now()))
        # 不再顺手建同名 chats 行：团队有独立的 team_sessions，混进「智研管家」对话列表只会删不掉
        self.db.commit()
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

    # ---------- 助手（我的 / 公开；点进去设提示词） ----------
    def assistants(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM assistants ORDER BY source='builtin', sort_order, created_at").fetchall()
        return [dict(r) for r in rows]

    def selectable_assistants(self) -> list[dict[str, Any]]:
        return [a for a in self.assistants() if a["source"] == "user" or a["enabled"]]

    def require_assistant(self, aid: str) -> dict[str, Any]:
        a = self.assistant(aid)
        if not a:
            raise ValueError("助手不存在")
        if a["source"] != "user" and not a["enabled"]:
            raise ValueError("这个公开助手尚未启用，请先在助手设置中启用")
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
            row = (aid, "user", allow.get("name") or "新助手", allow.get("avatar", ""), allow.get("description", ""),
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
            raise ValueError("公开助手不能删除 —— 可以「复制为我的」再改。")
        self.db.execute("DELETE FROM assistants WHERE id=?", (aid,))
        self.db.commit()

    def assistant_of_chat(self, chat_id: str) -> str:
        """这个对话绑的是哪个助手（空串=没绑）。"""
        row = self.db.execute("SELECT assistant_id FROM chats WHERE id=?", (chat_id,)).fetchone()
        return ((row["assistant_id"] if row else "") or "")

    def assistant_seed(self, builtins: list[dict[str, Any]]) -> int:
        """内置（公开）助手：只补缺，不覆盖用户改过的。"""
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

    # ---------- 项目记忆（长期 = 项目要点与结论；短期 = 对话内容与细节） ----------
    def memory(self, task_id: str, kind: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM project_memory WHERE task_id=?", [task_id]
        if kind:
            sql += " AND kind=?"; args.append(kind)
        sql += " ORDER BY created_at LIMIT ?"; args.append(limit)
        return [dict(r) for r in self.db.execute(sql, args)]

    def add_memory(self, task_id: str, kind: str, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("记忆内容不能为空")
        row = (uuid.uuid4().hex, task_id, "short" if kind == "short" else "long", text, now())
        self.db.execute("INSERT INTO project_memory VALUES (?,?,?,?,?)", row)
        self.db.commit()
        self.dump_memory(task_id)                       # 项目位置里同步一份可读文件
        return {"id": row[0], "kind": row[2], "text": text, "created_at": row[4]}

    def dump_memory(self, task_id: str) -> None:
        """把长期/短期记忆落盘到项目位置的「记忆」目录；没设项目位置就只留在库里。"""
        base = self.base_dir(task_id)
        if not base:
            return
        folder = base / "记忆"
        folder.mkdir(parents=True, exist_ok=True)
        long = [m["text"] for m in self.memory(task_id, "long")]
        shorts = [m["text"] for m in self.memory(task_id, "short")]
        msgs = self.messages(task_id)
        lines = [f"- {x}" for x in shorts] + [
            f"- {'我' if m['role'] == 'user' else '助手'}（{m['created_at'][:16]}）：{' '.join((m['content'] or '').split())[:300]}"
            for m in msgs]
        (folder / "长期记忆.md").write_text(
            "# 长期记忆\n\n项目要点与重要结论（自动维护，可直接编辑，下次运行会读回来）。\n\n"
            + ("\n".join(f"- {x}" for x in long) or "- （暂无）") + "\n", encoding="utf-8")
        (folder / "短期记忆.md").write_text(
            "# 短期记忆\n\n这个项目的对话内容与细节（按时间顺序，越靠下越新）。\n\n"
            + ("\n".join(lines) or "- （暂无）") + "\n", encoding="utf-8")

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
                self.add_memory(task_id, "long", f"{k}：{v.strip()}"); n += 1
        return n

    def memory_block(self, task_id: str, now_window: int = 10, line: int = 120) -> str:
        """拼给模型看的记忆块：长期全给，短期给窗口外的老对话（窗口内的走正常 messages）。"""
        self.seed_long_memory(task_id)
        long = [m["text"] for m in self.memory(task_id, "long")]
        msgs = self.messages(task_id)
        older = msgs[:-now_window] if len(msgs) > now_window else []
        short = [f"{m['role']}: {' '.join((m['content'] or '').split())[:line]}" for m in older]
        parts = ["【项目长期记忆】" + ("\n".join(f"- {x}" for x in long[-40:]) if long else "（空）")]
        if short:
            parts.append("【更早的对话（短期记忆）】\n" + "\n".join("- " + x for x in short[-20:]))
        return "\n\n".join(parts)


    def stage_map(self, task_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in reversed(self.steps(task_id)):
            out[row["stage"]] = row["status"]
        return out

    def stage_now(self, task_id: str, fallback: str = "") -> str:
        """项目现在走到七步的哪一步：取最近一条步骤记录；一条都没有就按任务自身阶段兜底。"""
        row = self.db.execute(
            "SELECT stage FROM steps WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (task_id,)
        ).fetchone()
        if row and row["stage"] in STAGE_LABEL:
            return STAGE_LABEL[row["stage"]]
        return {"definition": STAGE_LABEL["idea"], "reading": STAGE_LABEL["download"]}.get(fallback, "未开始")

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """删掉一个项目连带它的全部数据。SQLite 默认不启用外键约束，子表得自己清
        （paper_pages / evidence 没有 task_id，只能经 papers 关联）。
        磁盘上只删 data/papers 里我们自己存的 PDF 副本，用户自己的原文件不动。"""
        if self.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone() is None:
            raise KeyError(task_id)
        files = [r["source_path"] for r in self.db.execute("SELECT source_path FROM papers WHERE task_id=?", (task_id,))]
        self.db.execute("DELETE FROM paper_pages WHERE paper_id IN (SELECT id FROM papers WHERE task_id=?)", (task_id,))
        self.db.execute("DELETE FROM evidence WHERE paper_id IN (SELECT id FROM papers WHERE task_id=?)", (task_id,))
        tables_to_clean = (
            "papers", "artifacts", "runs", "calendar_events", "notes", "steps",
            "search_cache", "chat", "paper_graph", "kb_graph", "project_memory",
            "md_notes", "dl_queue", "team_tasks", "team_events",
            "project_workflow_layouts", "zotero_bindings", "zotero_item_links",
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
                self.db.execute(f"DELETE FROM workflow_nodes WHERE run_id IN ({marks})", run_ids)
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
        cur = self.event(event_id)
        value = (0 if cur["done"] else 1) if done is None else int(bool(done))
        self.db.execute("UPDATE calendar_events SET done=? WHERE id=?", (value, event_id))
        self.db.commit()
        return self.event(event_id)

    def event(self, event_id: str) -> dict[str, Any]:
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
        return [dict(r) for r in rows]

    def delete_event(self, event_id: str) -> None:
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
        return _reveal_key(dict(row))

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
        row = self.db.execute("SELECT * FROM model_profiles WHERE is_active=1").fetchone()
        return _reveal_key(dict(row)) if row else None

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
                "SELECT 1 FROM search_cache WHERE task_id=? AND source=? AND (doi=? AND doi!='' OR title=?)",
                (task_id, it.get("source", ""), it.get("doi", ""), it.get("title", "")),
            ).fetchone())
            if key:
                continue
            rid = uuid.uuid4().hex
            row = (rid, task_id, it.get("source", ""), it["title"], it.get("authors", ""), it.get("year", ""),
                   it.get("doi", ""), it.get("url", ""), it.get("pdf_url", ""), it.get("abstract", ""), now())
            self.db.execute("INSERT INTO search_cache VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            saved.append(self.result(rid))
        self.db.commit()
        return saved

    def cache_result(self, task_id: str, it: dict[str, Any]) -> str:
        """一条结果写进 search_cache 并返回 id；已存在（同 DOI/同标题）就返回旧 id，队列才能复用。"""
        title = (it.get("title") or "").strip()
        if not title:
            raise ValueError("这条没有标题，跳过")
        row = self.db.execute(
            "SELECT id FROM search_cache WHERE task_id=? AND source=? AND (doi=? AND doi!='' OR title=?)",
            (task_id, it.get("source", ""), it.get("doi", ""), title),
        ).fetchone()
        if row:
            return row["id"]
        rid = uuid.uuid4().hex
        self.db.execute("INSERT INTO search_cache VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, task_id, it.get("source", ""), title, it.get("authors", ""), it.get("year", ""),
                         it.get("doi", ""), it.get("url", ""), it.get("pdf_url", ""), it.get("abstract", ""), now()))
        self.db.commit()
        return rid

    def result(self, rid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM search_cache WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        return dict(row)

    def results(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM search_cache WHERE task_id=? ORDER BY created_at DESC, year DESC", (task_id,))
        return [dict(r) for r in rows]

    # ---------- 笔记 ----------
    def add_note(self, task_id: str, title: str, markdown: str, paper_id: str | None = None) -> dict[str, Any]:
        nid = uuid.uuid4().hex
        self.db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)", (nid, task_id, paper_id, title.strip() or "未命名笔记", markdown, now()))
        self.db.commit()
        import asset_service
        path = self.pdir(task_id, "笔记") / f"{nid}.md"
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
        return [dict(r) for r in rows]

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

    # ---------- 论文翻译：荧光笔标注（助手要看得到圈了什么）----------
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

def open_url(req: Any, timeout: int = 30) -> Any:
    """出网统一入口。

    环境里可能挂着代理（HTTP(S)_PROXY=127.0.0.1:10090），但代理（猫猫云/mihomo）没开时，
    所有请求都会被"积极拒绝"→ 页面显示「无法连接」，跟 key 一点关系都没有。
    所以这里先探一下代理端口：活着就用代理，死了这一程直连（国内接口直连没问题，真被墙的站点再开代理）。
    """
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
            os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    if proxy:
        parts = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        try:
            with socket.create_connection((parts.hostname, parts.port or 80), timeout=0.6):
                pass                                    # 代理活着：走它
        except OSError:
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def chat(profile: dict[str, Any], messages: list[dict[str, str]], temperature: float = 0.2,
         timeout: int = 180, max_tokens: int = 4096) -> str:
    """OpenAI 兼容 /chat/completions。没有配置模型时给出可执行的提示。"""
    return chat_raw(profile, messages, temperature=temperature, timeout=timeout, max_tokens=max_tokens).get("content") or ""


class ModelInvoker:
    """模型调用门面：支持注册全局拦截器，提供测试与插件的无缝替换机制。"""
    _handler: Any = None

    @classmethod
    def register(cls, handler: Any) -> None:
        cls._handler = handler

    @classmethod
    def unregister(cls) -> None:
        cls._handler = None

    @classmethod
    def invoke(cls, profile: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               temperature: float = 0.2, timeout: int = 180, max_tokens: int = 4096) -> dict[str, Any] | None:
        if cls._handler is not None:
            return cls._handler(profile, messages, tools=tools, temperature=temperature, timeout=timeout, max_tokens=max_tokens)
        return None

    @classmethod
    def call(cls, profile: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kwargs) -> dict[str, Any]:
        if cls._handler is not None:
            return cls._handler(profile, messages, tools=tools, **kwargs)
        return chat_raw(profile, messages, tools=tools, **kwargs)


def chat_raw(profile: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.2, timeout: int = 180, max_tokens: int = 4096) -> dict[str, Any]:
    """返回 assistant message 原始结构（含 tool_calls），供 agent 循环使用。"""
    if not profile:
        raise RuntimeError("尚未配置模型：到「模型」页添加一个 OpenAI 兼容接口并设为当前模型。")
    _assert_key_host(profile)          # key 只发它被填进来时的那个域名
    invoked = ModelInvoker.invoke(profile, messages, tools=tools, temperature=temperature, timeout=timeout, max_tokens=max_tokens)
    if invoked is not None:
        return invoked
    try:
        import runtime
        return runtime.call(profile, messages, tools=tools, temperature=temperature, timeout=timeout, max_tokens=max_tokens)
    except (ImportError, ModuleNotFoundError):
        pass
    guards.scrub(messages, note=f"→ {profile.get('model') or '?'}")   # 出站闸门：个人信息/密钥就地打码，再发出去
    if profile.get("api", "openai-completions") != "openai-completions":
        from agent_protocols import complete
        return complete(profile, messages, tools, temperature, timeout, max_tokens)
    payload = {"model": profile["model"], "temperature": temperature, "max_tokens": max_tokens, "messages": messages}
    if profile.get("reasoning", "auto") != "auto":
        payload["reasoning_effort"] = profile["reasoning"]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        profile["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {profile['api_key']}"} if profile.get("api_key") else {})},
        method="POST",
    )
    try:
        with open_url(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"模型接口 HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"模型接口不可达：{exc}") from exc
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型回包无法解析：{str(data)[:300]}") from exc
    usage = data.get("usage") or {}
    return {"role": "assistant", "content": (msg.get("content") or "").strip(),
            "tool_calls": msg.get("tool_calls") or [],
            "usage": {"prompt": int(usage.get("prompt_tokens") or 0),
                      "completion": int(usage.get("completion_tokens") or 0),
                      "total": int(usage.get("total_tokens") or 0), "exact": bool(usage)}}


def chat_json(profile: dict[str, Any], messages: list[dict[str, str]], **kw: Any) -> Any:
    raw = chat(profile, messages, **kw)
    raw = re.sub(r"^```(?:json)?\s*|```$", "", raw.strip(), flags=re.I).strip()
    match = re.search(r"[\[{].*[\]}]", raw, re.S)
    if not match:
        raise RuntimeError(f"模型没有返回 JSON：{raw[:200]}")
    return json.loads(match.group(0))


def discover_models(base_url: str, api_key: str = "", timeout: int = 25) -> list[dict[str, str]]:
    """GET {base_url}/models —— 一键发现模型。OpenAI 兼容网关基本都认这条。"""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
    )
    try:
        with open_url(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"发现模型失败：{exc}") from exc
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        # ponytail: 有些网关只给非标准结构，这时让用户手填模型 id
        raise RuntimeError("该网关没有返回标准模型列表，请手动填写模型 id")
    return [{"id": str(r.get("id") or r.get("name") or ""),
             "name": str(r.get("name") or r.get("id") or "")} for r in rows if (r.get("id") or r.get("name"))]


def test_provider(base_url: str, api_key: str, model: str, timeout: int = 60) -> str:
    """发一条最小消息，证明 Key/地址/模型名三者真的能打通。"""
    return chat({"base_url": base_url, "api_key": api_key, "model": model},
                [{"role": "user", "content": "回复两个字：可用"}], timeout=timeout, max_tokens=64)


# ============================ 论文搜索（开放接口） ============================

from workflow_search import (
    SOURCES, _openalex_works,
    litgraph, search, make_project_tree, download_pdf, _GRAPH_CACHE
)

# ============================ 七步工作流 ============================

from workflow_stages import (
    stage_idea, _relevant, stage_search, stage_download, stage_download_selected,
    stage_zotero_import, is_paper_pdf, deepread_state, deepread_stop, deepread_kick,
    stage_read, stage_notes, stage_write, stage_review, stage_convert,
    DeepreadStopped
)


from workflow_converters import (
    cite_sync, cite_read, cite_add, conv_run, conv_files, conv_compile, conv_open, _safe_path,
    en_state, en_run, compose_draft,
    CONV_FORMATS
)


ACTION = {
    "idea": stage_idea, "search": stage_search, "download": stage_download,
    "read": stage_read, "notes": stage_notes, "write": stage_write,
    "review": stage_review, "convert": stage_convert,
}


# ============================ Agent：把七步当工具调用 ============================

from workflow_prompts import (
    TOOL_SPECS, IDEA_TOOLS,
    skill_text
)



def idea_system(db: "WorkflowDB", task_id: str) -> str:
    """创新点审查专用 system：限定工具 + 博导 skill 框架 + 项目记忆。"""
    task = db.summary(task_id)["task"]
    skills = "\n\n".join(x for x in (
        skill_text("research", "research-topic-selection", "SKILL.md", cap=2000, db=db, task_id=task_id),
        skill_text("idea-evaluator", "SKILL.md", cap=4500, db=db, task_id=task_id),
    ) if x) or IDEA_FALLBACK
    return f"""你是「览籍智研」里这一步的创新点审查助手，用中文工作，站在博导（既严格又给建设性建议）的位置。

硬规则：
1. 这个对话 100% 绑定项目《{task['title']}》，你只做「创新点审查」这一件事；不下载文献、不精读、不撰写、不导出格式，用户要这些就说明去别的步骤。
2. 默认多轮：先问清硬约束（能拿到的数据 / 算力 / 时间 / 技能 / 目标期刊或学位要求），一次只问 1-3 个最关键的问题，别一口气拷问。
3. 信息够了再调用 idea_review 出正式判定（会落成项目笔记 + 长期记忆）；结论要落到「下一步做什么」。
4. 出现值得长期记住的结论、约束或用户偏好时，调用 memory_save 记一条（一句话，别刷屏）。
5. 不编造文献与数据；没有检索能力就写「未核实，需查文献」。你是草稿式意见，最终以导师与证据为准。

【评估框架（取自本机 skill）】
{skills}

【项目记忆】
%%MEMORY%%"""


def _overview(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    s = db.summary(task_id)
    return {"任务": s["task"]["title"], "阶段": s["task"]["stage"], "文献": s["papers"], "证据候选": s["evidence"],
            "检索结果": [{"id": r["id"], "title": r["title"][:90], "source": r["source"], "year": r["year"], "oa": bool(r["pdf_url"])} for r in db.results(task_id)[:10]],
            "文献库": [{"id": p["id"], "name": p["filename"], "pages": p["pages"]} for p in db.papers_of(task_id)[:10]],
            "笔记": [{"id": n["id"], "title": n["title"]} for n in db.notes(task_id)[:10]],
            "日历": [{"title": e["title"], "date": e["date"]} for e in db.events()[:10]]}


def _resolve_project(db: "WorkflowDB", needle: str) -> dict[str, Any] | None:
    """项目名或 id → 项目行（先精确再包含；找不到返回 None）。"""
    needle = (needle or "").strip()
    if not needle:
        return None
    tasks = db.tasks()
    for t in tasks:
        if t["id"] == needle:
            return dict(t)
    for t in tasks:
        if (t["title"] or "").strip() == needle:
            return dict(t)
    for t in tasks:
        if needle in (t["title"] or ""):
            return dict(t)
    return None


def _search_scope(db: "WorkflowDB", task_id: str, project: str) -> list[tuple[str, str]]:
    """这次检索覆盖哪些项目：指定项目 > 当前项目 > 全部项目（首页 Agent 没有项目时）。"""
    if project:
        row = _resolve_project(db, project)
        if not row:
            raise ValueError("找不到这个项目：%s（可先用 library_overview 或问用户）" % project)
        return [(row["id"], row["title"])]
    if task_id and db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
        return [(task_id, db.summary(task_id)["task"]["title"])]
    chat = db.db.execute("SELECT 1 FROM chats WHERE id=?", (task_id,)).fetchone() if task_id else None
    if chat:
        return [(t["id"], t["title"]) for t in db.tasks()]      # 首页/通用对话：跨项目
    return [(task_id, "")] if task_id else [(t["id"], t["title"]) for t in db.tasks()]


def _knowledge_query(db: "WorkflowDB", task_id: str, query: str, project: str = "") -> dict[str, Any]:
    q = (query or "").lower().strip()
    if not q:
        raise ValueError("要给检索关键词")
    hits: list[dict[str, Any]] = []
    for pid, ptitle in _search_scope(db, task_id, project):
        for n in db.notes(pid):
            if q in (n["title"] or "").lower() or q in (n["markdown"] or "").lower():
                idx = (n["markdown"] or "").lower().find(q)
                hits.append({"kind": "note", "project": ptitle, "id": n["id"], "title": n["title"],
                             "context": (n["markdown"] or "")[max(0, idx - 120): idx + 240]})
        for e in db.evidence(pid):
            if q in (e["value"] or "").lower() or q in (e["excerpt"] or "").lower() or q in (e["field"] or "").lower():
                hits.append({"kind": "evidence", "project": ptitle, "field": e["field"], "value": e["value"],
                             "citation": f"{e['filename']} p.{e['page_no']}", "context": e["excerpt"]})
    for note in db.md_notes():                                  # 笔记页那批「大笔记」
        body = (note.get("content") or "")
        if q in (note.get("title") or "").lower() or q in body.lower():
            idx = body.lower().find(q)
            hits.append({"kind": "big-note", "project": "", "id": note.get("id"), "title": note.get("title"),
                         "context": body[max(0, idx - 120): idx + 240]})
    return {"query": query, "hits": hits[:20], "total": len(hits)}


def _calendar_list(db: "WorkflowDB", args: dict[str, Any]) -> dict[str, Any]:
    """看日程：给月份就按月，给区间就按区间，都不给就是今天。"""
    from datetime import date as _date
    month = str(args.get("month") or "").strip()
    start = str(args.get("date_from") or "").strip()
    end = str(args.get("date_to") or "").strip() or start
    if month:
        rows, label = db.events(month[:7]), month[:7]
    else:
        if not start:
            start = end = _date.today().isoformat()
        rows = [e for e in db.events() if start <= (e.get("date") or "") <= end]
        label = start if start == end else "%s ~ %s" % (start, end)
    titles = {t["id"]: t["title"] for t in db.tasks()}
    items = [{"id": e["id"], "date": e["date"], "time": e.get("time") or "", "title": e["title"],
              "kind": e.get("kind") or "", "done": bool(e.get("done")),
              "project": titles.get(e.get("task_id") or "", "")} for e in rows]
    return {"range": label, "count": len(items), "events": items}


def _schedule_manage(db: "WorkflowDB", args: dict[str, Any]) -> dict[str, Any]:
    """定时任务：list / create / toggle / delete。"""
    import schedule as sched_lib
    action = str(args.get("action") or "list").strip()
    titles = {t["id"]: t["title"] for t in db.tasks()}

    def public(row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "cron": row["cron"],
                "enabled": bool(row["enabled"]), "project": titles.get(row.get("project_id") or "", ""),
                "next_run_at": row.get("next_run_at") or "", "prompt": (row.get("prompt") or "")[:200]}

    if action == "list":
        rows = [public(r) for r in db.schedules()]
        return {"count": len(rows), "schedules": rows}

    if action == "create":
        name = str(args.get("name") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        cron = str(args.get("cron") or "0 9 * * *").strip()
        if not name:
            raise ValueError("定时任务要有名字")
        if not prompt:
            raise ValueError("要写清楚到点了让 Agent 做什么（prompt）")
        ok, why = sched_lib.valid(cron)
        if not ok:
            raise ValueError(why)
        ok2, folder = sched_lib.inspect_output_dir(str(args.get("output_dir") or "定时任务"))
        if not ok2:
            raise ValueError(folder)
        project = str(args.get("project") or "").strip()
        pid = ""
        if project:
            row = _resolve_project(db, project)
            if not row:
                raise ValueError("找不到这个项目：%s" % project)
            pid = row["id"]
        enabled = args.get("enabled", True) is not False
        saved = db.schedule_save({"name": name, "prompt": prompt, "project_id": pid,
                                  "frequency": "cron", "cron": cron, "output_dir": folder,
                                  "enabled": enabled,
                                  "next_run_at": sched_lib.next_run_at(cron) if enabled else ""})
        return {"created": True, "schedule": public(saved)}

    sid = str(args.get("id") or "").strip()
    row = db.schedule(sid)
    if not row:
        raise ValueError("找不到这个定时任务（先 action=list 拿 id）")
    if action == "toggle":
        enabled = args.get("enabled")
        enabled = (not bool(row["enabled"])) if enabled is None else bool(enabled)
        db.schedule_set_enabled(sid, enabled)
        db.schedule_mark_run(sid, row.get("last_run_at") or "",
                             sched_lib.next_run_at(row["cron"]) if enabled else "")
        return {"toggled": True, "schedule": public(db.schedule(sid) or row)}
    if action == "delete":
        db.schedule_delete(sid)
        return {"deleted": True, "id": sid, "name": row["name"]}
    raise ValueError("action 只支持 list / create / toggle / delete")


# 子 agent 只给只读工具（写操作留在父对话手里，子 agent 不许越权）
_SUB_TOOL_NAMES = ("library_overview", "knowledge_query", "paper_search", "paper_read")
_SUB_TOOLS = [t for t in TOOL_SPECS if t.get("function", {}).get("name") in _SUB_TOOL_NAMES]


TOOL_RISK = {
    # Pure reads stay available to read-only assistants.
    "library_overview": "read_only", "knowledge_query": "read_only",
    "outline_read": "read_only", "map_read": "read_only", "map_sources": "read_only",
    "doc_read": "read_only", "cite_read": "read_only", "read_attachment": "read_only",
    "paper_search": "network_read", "search": "network_read",
    "mail_accounts": "network_read", "mail_list": "network_read", "mail_read": "network_read",
    "zotero_collections": "network_read",
    "delegate": "local_execution", "format_convert": "local_execution",
    "paper_download": "network_read", "download": "network_read",
    "outline_write": "workspace_write", "map_write": "workspace_write", "doc_write": "workspace_write",
    "cite_add": "workspace_write", "memory_save": "workspace_write", "notes": "workspace_write",
    "notes_compose": "workspace_write", "idea_review": "workspace_write", "paper_read": "workspace_write",
    "read": "workspace_write", "draft_write": "workspace_write", "draft_review": "workspace_write",
    "calendar_add": "workspace_write", "team_assign": "workspace_write", "team_form": "workspace_write",
    "project_create": "workspace_write", "schedule_manage": "workspace_write", "calendar_list": "read_only",
    "team_board": "read_only", "mail_send": "external_write", "feishu_send": "external_write",
    "zotero_write": "external_write", "zotero_sync": "external_write",
}


def tool_risk(name: str) -> str:
    """Return a stable policy category for every tool, including future MCP tools."""
    if name.startswith("mcp_"):
        return "external_write"
    return TOOL_RISK.get(name, "workspace_write")


def enforce_tool_policy(name: str, args: dict[str, Any], permission: str) -> None:
    """Apply policy at the execution boundary, not only by hiding tool schemas."""
    risk = tool_risk(name)
    if permission == "read_only" and risk != "read_only":
        raise PermissionError(f"只读权限禁止 {risk} 工具：{name}")
    if risk == "external_write" and permission != "full_access" and args.get("confirm") is not True:
        raise PermissionError("远程写入或外发必须取得明确确认（confirm=true）")


def _delegate(db: "WorkflowDB", task_id: str, jobs: list[dict[str, Any]], emit: Any = None,
              profile: dict[str, Any] | None = None, scope: str = "", parallel: int = 3) -> dict[str, Any]:
    """把子任务并行派给**独立上下文**的子 agent，只把摘要带回父对话。

    隔离靠三件已有机制拼出来，不新建东西：
      * 每个子 agent 一个全新的 `scope`（db.messages(task_id, scope) 因此是空的）→ 看不到父对话历史；
      * `publish=False` → 子 agent 不写对话历史、不落 assistant 回复（跟「撰写流水线内部子 agent」同一条路）；
      * 只读工具表 `_SUB_TOOLS` → 子 agent 不能改库。
    子 agent 各自**新开一条连接**（同库不同连接），事件打上 `sub`/`sub_title` 供前台分行显示。
    # ponytail: 单机 SQLite，父子各一条连接 + busy_timeout；真要高并发再上 WAL 或连接池
    """
    jobs = [j for j in (jobs or []) if str(j.get("prompt") or "").strip()][:4]
    if not jobs:
        return {"ok": False, "error": "delegate 需要至少一个子任务（jobs[].prompt）"}
    path = db.path

    sub_events: list[dict[str, Any]] = []          # 子 agent 的事件也回给父，回放（F5）时才有分行

    def one(i: int, job: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        title = str(job.get("title") or f"子任务 {i + 1}")[:40]
        sid = "sub-" + uuid.uuid4().hex[:8]
        rec: dict[str, Any] = {"i": i, "title": title, "scope": sid, "summary": "", "steps": 0, "ok": True}

        def child_emit(ev: dict[str, Any]) -> None:
            tagged = {**ev, "sub": sid, "sub_title": title}
            sub_events.append(tagged)              # 收进父的事件流 → F5 回放也能看到分行
            if emit:
                emit(tagged)

        try:
            with WorkflowDB(path) as cdb:                      # 子 agent 自己一条连接
                sub_events.append({"type": "sub", "phase": "start", "sub": sid, "sub_title": title})
                if emit:
                    emit(sub_events[-1])
                r = run_agent(cdb, task_id, str(job["prompt"]), emit=child_emit,
                              max_steps=int(job.get("max_steps") or 4), system=_DELEGATE_SYSTEM,
                              tools=_SUB_TOOLS, publish=False, scope=sid, profile=profile)
            rec["summary"] = (r.get("reply") or "").strip()[:2000]
            rec["steps"] = len(r.get("events") or [])
        except Exception as exc:                               # 一个子 agent 崩了不影响其它
            rec["ok"] = False
            rec["summary"] = f"（子任务失败：{exc}）"
        sub_events.append({"type": "sub", "phase": "done", "sub": sid, "sub_title": title,
                          "ok": rec["ok"], "summary": rec["summary"][:160]})
        if emit:
            emit(sub_events[-1])
        return i, rec

    got: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(parallel, len(jobs)))) as ex:
        for i, rec in ex.map(lambda p: one(*p), list(enumerate(jobs))):
            got[i] = rec
    order = [got[i] for i in range(len(jobs))]
    _nl = chr(10)
    digest = (_nl + _nl).join("## " + r["title"] + _nl + (r["summary"] or "（空）") for r in order)
    return {"ok": True, "delegated": order, "digest": digest, "sub_events": sub_events,
            "hint": "把 digest 里的要点综合进你的回答；需要细节再向用户说明是哪路子 agent 给的。"}


def _tool_impl(name: str, db: "WorkflowDB", task_id: str, args: dict[str, Any], scope: str = "",
               emit: Any = None, profile: dict[str, Any] | None = None) -> Any:
    """scope = 当前步骤（''/'idea'/'outline'/'write'/'review'）—— doc_read/doc_write 据此决定动哪个文件。"""
    if name == "delegate":
        return _delegate(db, task_id, args.get("jobs") or [], emit=emit, profile=profile,
                         scope=scope, parallel=int(args.get("parallel") or 3))
    if name in ("team_board", "team_assign", "team_form"):
        import team as _team                                  # 延迟导入：team 反向依赖 workflow
        tid = _team_project(db, task_id, args.get("project", ""))
        if name == "team_board":
            return _team.board(tid)
        if name == "team_form":
            return _team.form_team(tid, args.get("purpose", ""), args.get("members") or [])
        return _team.assign(tid, args.get("member", ""), args.get("prompt", ""), trigger="leader",
                            origin="leader@" + task_id[:8], depends_on=args.get("depends_on"),
                            output_contract=args.get("output_contract"), reviewer_id=args.get("reviewer_id", ""))
    if name == "memory_save":
        return db.add_memory(task_id, args.get("kind", "long"), args.get("text", ""))
    if name == "library_overview":
        return _overview(db, task_id)
    if name == "knowledge_query":
        return _knowledge_query(db, task_id, args.get("query", ""), str(args.get("project") or ""))
    if name == "calendar_list":
        return _calendar_list(db, args)
    if name == "schedule_manage":
        return _schedule_manage(db, args)
    if name == "project_create":
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("新建项目至少要有标题")
        base = str(args.get("base_dir") or "").strip()
        tid = db.create_task(title, str(args.get("question") or ""), str(args.get("material_system") or ""), base)
        made = make_project_tree(base, db.summary(tid)["task"]) if base else []
        return {"project_id": tid, "title": title, "base_dir": base, "dirs": made}
    if name == "calendar_add":
        return db.add_event(args.get("title", ""), args.get("date", ""), task_id, args.get("kind", "milestone"),
                            time=args.get("time", ""))
    if name == "search":
        return stage_search(db, task_id, args.get("query", ""), args.get("sources"), int(args.get("rows", 10)))
    if name == "paper_search":
        return stage_search(db, task_id, args.get("query", ""), args.get("sources"), int(args.get("rows", 10)))
    if name == "paper_download":
        return stage_download(db, task_id, args.get("result_id", ""))
    if name == "download":
        return stage_download(db, task_id, args.get("result_id", ""))
    if name == "paper_read":
        return stage_read(db, task_id, args.get("paper_id", ""))
    if name == "read":
        return stage_read(db, task_id, args.get("paper_id", ""))
    if name == "notes_compose":
        return stage_notes(db, task_id, args.get("note_id", ""), args.get("vault") or None)
    if name == "notes":
        return stage_notes(db, task_id, args.get("note_id", ""))
    if name == "idea_review":
        return stage_idea(db, task_id, args.get("idea", ""))
    if name == "outline_write":
        return outline_write(db, task_id, args.get("markdown", ""))
    if name == "outline_read":
        return outline_read(db, task_id)
    if name == "map_write":
        return map_write(db, task_id, args.get("data") or {})
    if name == "map_read":
        return map_read(db, task_id)
    if name == "map_sources":
        return map_sources(db, task_id)
    if name == "doc_write":
        return doc_write(db, task_id, scope or "outline", args.get("markdown", ""))
    if name == "doc_read":
        return doc_read(db, task_id, scope or "outline")
    if name == "cite_add":
        return cite_add(db, task_id, args)
    if name == "cite_read":
        return cite_read(db, task_id)
    if name == "read_attachment":
        return read_attachment(args.get("path", ""), int(args.get("chars") or 8000))
    if name == "draft_write":
        return stage_write(db, task_id, args.get("title", ""), args.get("section", "引言"))
    if name == "draft_review":
        return stage_review(db, task_id, args.get("draft") or compose_draft(db, task_id))
    if name == "format_convert":
        return stage_convert(db, task_id, args.get("markdown") or compose_draft(db, task_id), args.get("format", "docx"), args.get("name", "paper"))
    raise ValueError(f"未知工具：{name}")


from workflow_documents import (
    doc_path, doc_read, doc_write,
    outline_read, outline_write, doc_tools, map_tools, doc_seed, read_attachment, map_path, map_read, map_write, map_sources
)
from workflow_runners import (
    run_idea, run_map, run_outline, run_write, run_review, _wants_draft,
)



def _team_project(db: "WorkflowDB", task_id: str, raw: str) -> str:
    """团队派活的落点项目：给了名字/ID 用它；没给就用当前 task（项目对话时就是该项目）。
    首页通用对话没有项目 → 必须点名，否则直接报错（不许瞎猜一个项目去执行）。"""
    raw = (raw or "").strip()
    tasks = {t["id"]: t for t in db.tasks()}
    if raw:
        if raw in tasks:
            return raw
        low = raw.lower()
        for tid, t in tasks.items():
            if low and low in (t["title"] or "").lower():
                return tid
        raise ValueError("没有叫「%s」的项目（现有：%s）" % (raw, "、".join(t["title"] for t in tasks.values()) or "空"))
    row = db.db.execute("SELECT id FROM chats WHERE id=?", (task_id,)).fetchone()
    if row:
        raise ValueError("这是首页通用对话，没绑项目 —— 请用 project 参数指明要派给哪个项目")
    return task_id


def _global_brief(db: "WorkflowDB") -> str:
    """智研管家 的简报：有哪些项目、各自走到哪、库里有多少东西。
    首页 Agent 以前完全不知道项目存在（ctx 只说「没有绑定任何项目」）—— 这就是 Leader 缺的视野。"""
    lines: list[str] = []
    for t in db.tasks():
        tid = t["id"]
        try:
            papers = db.db.execute("SELECT COUNT(*) AS c FROM papers WHERE task_id=?", (tid,)).fetchone()["c"]
            notes = db.db.execute("SELECT COUNT(*) AS c FROM notes WHERE task_id=?", (tid,)).fetchone()["c"]
        except Exception:
            papers = notes = 0
        lines.append(f"  · {t['title']}｜阶段 {db.stage_now(tid, t['stage'])}｜文献 {papers}｜笔记 {notes}｜更新 {(t['updated_at'] or '')[:16]}")
    out = ("\n【全局简报】共 %d 个项目：%s" % (len(lines), ("\n" + "\n".join(lines)) if lines else "（还没有项目 —— 用 project_create 工具新建一个再往下走）"))
    try:
        out += "\n【大笔记】%d 篇（笔记页那批）" % len(db.md_notes())
    except Exception:
        pass
    return out


def agent_profile(db: "WorkflowDB", task_id: str) -> dict[str, Any] | None:
    """这个对话该用哪个模型：**绑了助手就用助手接的那个模型**（deepseek 助手跑 deepseek、gpt 助手跑 gpt），
    没绑（或助手没指定）就退回当前激活模型。项目工作流仍用激活模型。"""
    try:
        row = db.db.execute("SELECT assistant_id FROM chats WHERE id=?", (task_id,)).fetchone()
    except Exception:
        return db.active_model()
    aid = (row["assistant_id"] if row else "") or ""
    if aid:
        a = db.require_assistant(aid)
        if a and a.get("model_id"):
            try:
                m = db.model(a["model_id"])
            except KeyError:
                raise ValueError("助手绑定的模型已移除，请重新选择模型") from None
            if m and m.get("base_url") and m.get("model"):
                return m
    return db.active_model()


STEP_AGENT = {                       # 十步各有一个助手身份（跟前端 WF_AGENT 同名，改一处两边对齐）
    "idea": "创新点审查助手", "search": "文献检索助手", "download": "入库助手", "notes": "翻译助手",
    "outline": "框架助手", "map": "谱系助手", "write": "写作助手", "review": "审查助手",
    "entrans": "中译英助手", "convert": "格式助手",
}


def _system_for(who: str, system: str | None) -> str:
    """第一段 system。十步各有助手身份时，把 AGENT_SYSTEM 开头那两句「你叫览籍智研 / 答：你是览籍智研」
    换成本步的名字 —— 只加在后面 ctx 里没用，模型会照第一行复述。其余规则原样保留。"""
    base = system or AGENT_SYSTEM
    if not who or system:
        return base
    return base.replace(
        "你叫「览籍智研」，是这个软件的主 Agent，也是一位做科研的研究生的科研助手",
        "你叫「%s」，是这个软件里负责这一步的副手，服务对象是做科研的研究生" % who, 1,
    ).replace(
        "用户怎么问「你是谁」就怎么答：你是览籍智研，他的科研助手。",
        "用户怎么问「你是谁」就怎么答：你是%s。" % who, 1,
    )


def run_agent(db: "WorkflowDB", task_id: str, text: str, emit: Any = None, max_steps: int = 6,
              system: str | None = None, tools: list[dict[str, Any]] | None = None,
              extra_context: str = "", scope: str = "", publish: bool = True,
              profile: dict[str, Any] | None = None, assistant_id: str = "",
              member: dict[str, Any] | None = None, step: str = "") -> dict[str, Any]:
    """工具调用循环。emit(event) 每产生一条事件调用一次（SSE 用）。
    system/tools 不传就是通用 Agent（AGENT_SYSTEM + 全量工具）；创新点审查/确认大纲那两步会传受限工具 + 记忆块。
    scope 决定这轮对话和哪一步共用历史（'' = 创新点审查/通用，'outline' = 确认大纲）。
    publish=False = 后台子 agent（文章撰写流水线内部用）：不写对话历史、不落 assistant 回复、不发 done，
    工具调用与文本照旧流给前台看进度 —— 由调用方统一收尾。"""
    say = emit or (lambda _e: None)
    import agent_settings
    import capabilities
    import agent_integrations
    cfg = agent_settings.runtime_config(db, task_id, assistant_id or (profile or {}).get("assistant_id", ""), member)
    if profile is None:
        profile = db.model(cfg["model_id"]) if cfg["model_id"] else agent_profile(db, task_id)
    profile = dict(profile or {})
    for key in ("reasoning", "permission"):
        profile.setdefault(key, cfg[key])
    permission = profile.get("permission", "auto")
    aid = cfg["assistant_id"]
    profile["assistant_id"] = aid
    if publish:
        db.add_message(task_id, "user", text, scope=scope)
        say({"type": "user", "content": text})
    task = db.summary(task_id)["task"]
    chat_row = db.db.execute("SELECT * FROM chats WHERE id=?", (task_id,)).fetchone()
    bound = ""                                     # 这个对话绑的项目（'' = 随意对话）
    if chat_row:
        try:
            bound = (chat_row["task_id"] or "").strip()
        except Exception:
            bound = ""
        if bound and not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (bound,)).fetchone():
            bound = ""                             # 项目被删了 → 退回随意对话
    ctx_id = bound or task_id                       # 工具按这个 id 干活：绑了项目就落在项目里
    import run_service
    tracked_run = run_service.start(db, ctx_id, "agent", idempotency_key="")
    tracked_node = run_service.node_start(db, tracked_run["id"], scope or "agent")
    if chat_row and bound:                        # 绑了项目 → Partner Agent（对话历史独立，视野与产出跟项目走）
        pt = db.summary(bound)["task"]
        ctx = (f"你是《{pt['title']}》项目的 **Partner Agent**：这个对话绑在该项目上 —— 对话历史自己一份，"
               f"但文献/笔记/记忆/产出都按项目来。\n"
               f"当前项目：{pt['title']}\n研究问题：{pt['question']}\n关键词 / 材料体系：{pt['material_system']}\n"
               f"项目 ID：{bound}\n产出目录：{db.pdir(bound, '其余')}")
        try:
            ctx += "\n\n" + db.memory_block(bound)     # 项目的长期/短期记忆
        except Exception:
            pass
    elif chat_row:  # 首页 Agent = 智研管家：不绑项目，但有全局视野
        ctx = ("你叫「览籍智研」，是这个软件的主 Agent（首页就叫「智研管家」），用户的科研助手。"
               "有人问「你是谁」，答：我是览籍智研，你的科研助手。\n"
               "你是 **智研管家**（首页 Agent，不属于任何单个项目）：可以跨项目工作 —— 查任意项目、"
               "任意文献/笔记/日程，也能新建项目、加日程、检索下载精读文献、写笔记。\n"
               "项目工作流里跑的是 **绑定单个项目的 Partner Agent**；要在某个项目内部推进，就按「哪个项目、要什么产出」把话说清，"
               "或建议用户进那个项目的「项目空间 / 工作流」。\n"
               "你也可以用 `team_board` 看某项目团队的成员与执行状态、用 `team_assign` 直接把活派给某个项目的 Partner Agent"
               "（它们排队执行，产出落在该项目目录的「团队/」里）。\n"
               f"这是通用对话「{task['title']}」，产出目录：{db.out_dir(task_id)}" + _global_brief(db))
    else:
        ctx = (f"你是《{task['title']}》项目的 **Partner Agent**：绑定单个项目，只在这个项目范围内工作；"
               "跨项目的事让用户回首页 Agent（智研管家）做。\n"
               f"当前项目：{task['title']}\n研究问题：{task['question']}\n"
               f"关键词 / 材料体系：{task['material_system']}\n项目 ID：{task_id}\n产出目录：{db.out_dir(task_id)}")
    # builtin-steward 的 rules 就是 AGENT_SYSTEM，首页/通用对话上面已经整段写过了，再套一层【当前助手】等于把同一段话重复一遍
    if aid and aid != "builtin-steward":   # Also applies to each team's selected assistant.
        try:
            asst = db.require_assistant(aid)
        except Exception:
            asst = None
        if asst:
            ctx += "\n\n【当前助手】%s（%s）\n规则：%s" % (
                asst["name"], "公开内置" if asst["source"] == "builtin" else "我的助手", asst["rules"] or "（没写规则）")
    who = STEP_AGENT.get(step or scope or "", "")
    if who:      # 工作流里的每一步都是一个独立助手，别让它自称「览籍智研 / 智研管家 / Partner Agent」
        ctx += (f"\n\n【你的身份】这一步你是「{who}」，只负责这一步的事；上面提到的「览籍智研 / 智研管家 / Partner Agent」是"
                f"整台机器和首页的称呼，不是你的自称。有人问「你是谁」，就答「我是{who}」，再用一句话说这一步负责什么。")
    if extra_context:
        ctx += "\n\n" + extra_context
    project_dir = str((db.summary(bound or task_id).get("task") or {}).get("base_dir") or "")
    ctx += "\n\n" + capabilities.skill_context(db, aid, project_dir)
    ctx += "\n\n权限模式：%s。%s" % (permission, {
        "auto": "由你根据用户目标决定操作范围；外发邮件、消息和远程写入仍需明确确认。",
        "read_only": "只允许只读工具，禁止写入文件、数据库、邮件或外部服务。",
        "workspace_write": "可以修改工作区内容；外发邮件、消息和远程写入需要明确确认。",
        "full_access": "危险：用户授予完全访问权限，可使用所有已启用工具和外部写入，不再要求逐次确认；谨慎处理不可逆操作。",
    }[permission])
    history = [{"role": m["role"], "content": m["content"]} for m in db.messages(task_id, scope or "idea")[:-1]][-12:]
    tool_specs = list(TOOL_SPECS if tools is None else tools)
    if tools is None:
        tool_specs += agent_integrations.tool_specs()
        tool_specs += capabilities.capability_tool_specs(db, aid, project_dir)
    elif any(t.get("function", {}).get("name") == "skill_read" for t in capabilities.capability_tool_specs(db, aid, project_dir)):
        tool_specs += [t for t in capabilities.capability_tool_specs(db, aid, project_dir) if t.get("function", {}).get("name") == "skill_read"]
    tool_specs = capabilities.filter_tools(db, aid, tool_specs, project_dir)
    if permission == "read_only":
        tool_specs = [t for t in tool_specs if t.get("function", {}).get("name") in agent_settings.READ_TOOLS]
    tool_specs = list({t["function"]["name"]: t for t in tool_specs}.values())
    allowed_names = {t.get("function", {}).get("name") for t in tool_specs}
    integration_names = {t.get("function", {}).get("name") for t in agent_integrations.tool_specs()}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_for(who, system)},
        {"role": "system", "content": ctx},
        *history, {"role": "user", "content": text},
    ]
    events: list[dict[str, Any]] = []
    final = ""
    tokens = {"prompt": 0, "completion": 0, "total": 0, "exact": True}
    for step in range(max_steps):
        try:
            msg = chat_raw(profile, messages, tools=tool_specs)
        except Exception as exc:
            run_service.finish_node(db, tracked_node["id"], "failed", error=str(exc))
            run_service.finish(db, tracked_run["id"], "failed", error=str(exc))
            raise
        use = msg.get("usage") or {}
        if use:
            # 每次回包发「这一次」的用量（可累加），不发累计值——否则存进事件流后回放会重复计数
            delta = {"prompt": use["prompt"], "completion": use["completion"],
                     "total": use["total"] or (use["prompt"] + use["completion"]), "exact": bool(use["exact"])}
            tokens["prompt"] += delta["prompt"]; tokens["completion"] += delta["completion"]
            tokens["total"] += delta["total"]; tokens["exact"] = tokens["exact"] and delta["exact"]
            ev = {"type": "usage", **delta}
            say(ev); events.append(ev)
        calls = msg.get("tool_calls") or []
        if msg.get("content"):
            say({"type": "assistant", "content": msg["content"], "step": step})
            events.append({"type": "assistant", "content": msg["content"]})
        if not calls:
            final = msg.get("content") or "（模型没有返回内容）"
            messages.append({"role": "assistant", "content": final})
            break
        assistant_message = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
        if msg.get("_protocol"):
            assistant_message["_protocol"] = msg["_protocol"]
        messages.append(assistant_message)
        for call in calls:
            fn = (call.get("function") or {})
            name = fn.get("name") or "?"
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            ev = {"type": "tool_call", "name": name, "args": args, "id": call.get("id")}
            say(ev); events.append(ev)
            t_tool_start = time.monotonic()
            try:
                if name not in allowed_names:
                    raise PermissionError("当前助手未获准使用此工具：" + name)
                enforce_tool_policy(name, args, permission)
                if name in integration_names:
                    result = agent_integrations.invoke(name, db, ctx_id, args, assistant_id=aid, permission=permission)
                elif name == "skill_read" or name.startswith("mcp_"):
                    result = capabilities.invoke_tool(db, aid, name, args, project_dir)
                else:
                    result = _tool_impl(name, db, ctx_id, args, scope, emit=emit, profile=profile)
                if isinstance(result, dict) and result.get("sub_events"):
                    events.extend(result["sub_events"])      # 子 agent 的事件并进本轮 → 回放可还原
                ok, brief = True, _brief(name, result)
            except Exception as exc:
                result, ok, brief = {"error": f"{type(exc).__name__}: {exc}"}, False, f"失败：{exc}"
            duration_ms = int((time.monotonic() - t_tool_start) * 1000)
            try:
                inp_hash = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
                db.record_audit_event(actor=aid or "user", assistant_id=aid, tool=name, project_id=ctx_id,
                                      input_hash=inp_hash, outcome="ok" if ok else "error", duration_ms=duration_ms)
            except Exception:
                pass
            path = result.get("path") if isinstance(result, dict) else None
            ev2 = {"type": "tool_result", "name": name, "ok": ok, "summary": brief,
                   "path": path or "", "id": call.get("id")}
            say(ev2); events.append(ev2)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "name": name,
                             "content": json.dumps(result, ensure_ascii=False, default=str)[:4000]})
    else:
        final = "达到单轮工具调用上限，先停在这里。可以再说一句让我继续。"
        say({"type": "assistant", "content": final})
    if publish:
        db.add_message(task_id, "assistant", final, events, scope=scope)
        db.log_step(task_id, "agent", "done", text[:60])
        if chat_row:  # 对话置顶 + 首次提问当标题（默认名用久了列表没法认）
            db.db.execute("UPDATE chats SET updated_at=? WHERE id=?",
                          (datetime.now(timezone.utc).isoformat(), task_id))
            if chat_row["title"] in ("", "新对话"):
                db.db.execute("UPDATE chats SET title=? WHERE id=?", (text.strip().replace("\n", " ")[:24], task_id))
            db.db.commit()
        say({"type": "done", "content": final})
    run_service.finish_node(db, tracked_node["id"], "succeeded")
    run_service.finish(db, tracked_run["id"], "succeeded")
    return {"reply": final, "events": events, "run_id": tracked_run["id"]}


def _brief(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:120]
    if name == "team_board":
        c = result.get("counts") or {}
        return "团队 %d 人 · 排队 %d · 运行 %d · 完成 %d · 失败 %d" % (
            len(result.get("members") or []), c.get("queued", 0), c.get("running", 0),
            c.get("done", 0), c.get("failed", 0))
    if name == "team_assign":
        return "已派给「%s」，排队等待执行" % result.get("member_name", "")
    if name == "team_form":
        return "已组队「%s」：%d 人（加急办公室）" % (result.get("name", ""), len(result.get("added") or []))
    if name == "project_create":
        return "已建项目「%s」· %s" % (result.get("title", ""), str(result.get("project_id", ""))[:8])
    if name == "calendar_list":
        return "%s 共 %d 条日程" % (result.get("range", ""), result.get("count", 0))
    if name == "schedule_manage":
        if result.get("schedules") is not None:
            return "定时任务 %d 个" % result.get("count", 0)
        if result.get("created"):
            return "已建定时任务「%s」%s" % ((result.get("schedule") or {}).get("name", ""), (result.get("schedule") or {}).get("cron", ""))
        if result.get("toggled"):
            s = result.get("schedule") or {}
            return "「%s」已%s" % (s.get("name", ""), "启用" if s.get("enabled") else "暂停")
        if result.get("deleted"):
            return "已删除定时任务「%s」" % result.get("name", "")
    if name == "knowledge_query":
        return "命中 %d 条（%s）" % (result.get("total", 0), "、".join(sorted({h.get("project") or "大笔记" for h in (result.get("hits") or [])}))[:60])
    if name == "paper_search":
        return f"检索到 {result.get('found')} 条，新增入库 {len(result.get('saved') or [])} 条"
    if name == "paper_download":
        p = result.get("paper") or {}
        return f"已入库 {p.get('filename')}（{len(p.get('pages') or [])} 页）"
    if name in ("paper_read", "read"):
        return f"精读完成，笔记 {result.get('note_id', '')[:8]}，{result.get('chunks', 1)} 段"
    if name in ("notes_compose", "notes"):
        return f"知识库笔记已生成，证据 {result.get('evidence', 0)} 条"
    if name == "idea_review":
        return f"审查判定：{(result.get('verdict') or {}).get('verdict', '')}"
    if name == "draft_write":
        return f"草稿已写入 {result.get('path', '')}"
    if name == "draft_review":
        return f"审查结论：{(result.get('review') or {}).get('verdict', '')}"
    if name == "format_convert":
        return f"已导出 {result.get('path', '')}"
    if name == "calendar_add":
        return f"已加日程 {result.get('title')} @ {result.get('date')} {result.get('time') or ''}".strip()

    if name == "library_overview":
        return f"文献 {result.get('文献')} 篇 / 检索结果 {len(result.get('检索结果') or [])} 条 / 笔记 {len(result.get('笔记') or [])} 篇"
    if name == "memory_save":
        return f"已记入长期记忆：{result.get('text','')[:60]}"
    return "完成"


guards.LOG_PATH = OUT / "隐私命中.log"
