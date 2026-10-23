"""easyScholar API 客户端与本地缓存适配器。

文档基准：docs/easyScholar _ 显示期刊等级_SCI分区.html
接口协议：
  GET https://www.easyscholar.cc/open/getPublicationRank?secretKey={key}&publicationName={name}
约束：
  - 必须对期刊名进行 encodeURIComponent() 编码
  - 请求频率限制：每秒最多 2 次
  - 密钥使用 Windows DPAPI (securestore) 加密保护
  - 本地 SQLite 缓存 30 天，避免重复请求
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any

import securestore

logger = logging.getLogger(__name__)

EASYSCHOLAR_API_URL = "https://www.easyscholar.cc/open/getPublicationRank"
RATE_LIMIT_INTERVAL = 0.55  # 至少间隔 0.55 秒，确保每秒不超过 2 次请求
CACHE_EXPIRE_DAYS = 30

_rate_limit_lock = threading.Lock()
_last_request_time = 0.0

OFFICIAL_LABELS: dict[str, str] = {
    "sciUp": "中科院升级版",
    "sciUpTop": "中科院Top",
    "sciUpSmall": "中科院小类",
    "sciBase": "中科院基础版",
    "sciwarn": "中科院预警",
    "sci": "SCI分区-JCR",
    "sciif": "影响因子",
    "sciif5": "5年影响因子",
    "ssci": "SSCI分区-JCR",
    "jci": "JCI指数-JCR",
    "ccf": "CCF",
    "fms": "FMS",
    "ajg": "ABS",
    "utd24": "UTD24",
    "ft50": "FT50",
    "pku": "北大核心",
    "cssci": "南大核心",
    "cscd": "CSCD",
    "zhongguokejihexin": "科技核心",
}


def _throttle() -> None:
    global _last_request_time
    with _rate_limit_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - elapsed)
        _last_request_time = time.time()


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS easyscholar_cache (
      publication_name TEXT PRIMARY KEY,
      official_rank TEXT NOT NULL DEFAULT '{}',
      custom_rank TEXT NOT NULL DEFAULT '{}',
      updated_at TEXT NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS integration_settings (
      service_id TEXT PRIMARY KEY,
      enabled INTEGER NOT NULL DEFAULT 0,
      credentials_protected TEXT NOT NULL DEFAULT '',
      options TEXT NOT NULL DEFAULT '{}',
      updated_at TEXT NOT NULL
    );
    """)
    conn.commit()


def get_config(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_table(conn)
    row = conn.execute("SELECT enabled, credentials_protected, options FROM integration_settings WHERE service_id='easyscholar'").fetchone()
    if not row:
        return {"enabled": False, "key_present": False, "secret_key": "", "options": {}}
    enabled, cred_prot, opts_json = row[0], row[1], row[2]
    secret_key = ""
    if cred_prot:
        try:
            secret_key = securestore.unprotect(cred_prot)
        except Exception:
            secret_key = ""
    opts = {}
    if opts_json:
        try:
            opts = json.loads(opts_json)
        except Exception:
            opts = {}
    return {
        "enabled": bool(enabled),
        "key_present": bool(secret_key),
        "secret_key": secret_key,
        "options": opts,
    }


def save_config(conn: sqlite3.Connection, enabled: bool, secret_key: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_table(conn)
    current = get_config(conn)
    final_key = secret_key.strip() if secret_key is not None else current["secret_key"]
    if final_key in ("********", "***"):
        final_key = current["secret_key"]
    
    cred_prot = securestore.protect(final_key) if final_key else ""
    opts_json = json.dumps(options or current.get("options") or {}, ensure_ascii=False)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("""
    INSERT INTO integration_settings (service_id, enabled, credentials_protected, options, updated_at)
    VALUES ('easyscholar', ?, ?, ?, ?)
    ON CONFLICT(service_id) DO UPDATE SET
      enabled=excluded.enabled,
      credentials_protected=excluded.credentials_protected,
      options=excluded.options,
      updated_at=excluded.updated_at
    """, (1 if enabled else 0, cred_prot, opts_json, now_iso))
    conn.commit()
    return {"ok": True, "enabled": enabled, "key_present": bool(final_key)}


def parse_badges(official: dict[str, Any], custom: dict[str, Any]) -> list[dict[str, Any]]:
    """将 easyScholar 原始返回结构转化成标准化展示标签 (Badges)。"""
    badges: list[dict[str, Any]] = []
    
    all_ranks = official.get("select") or official.get("all") or {}
    
    sci_up = all_ranks.get("sciUp")
    if sci_up:
        val = str(sci_up).strip()
        label = f"中科院{val}区" if val.isdigit() or val in ("1", "2", "3", "4") else f"中科院 {val}"
        badges.append({"key": "sciUp", "label": label, "type": "sci", "weight": 100})
    
    sci_top = all_ranks.get("sciUpTop")
    if sci_top and str(sci_top).lower() not in ("0", "false", "no", "none", ""):
        badges.append({"key": "sciUpTop", "label": "TOP", "type": "top", "weight": 95})

    # 中科院预警期刊(高危提示):weight 最高,排最前
    warn = all_ranks.get("sciwarn")
    if warn and str(warn).lower() not in ("0", "false", "no", "none", ""):
        badges.append({"key": "sciwarn", "label": "预警期刊", "type": "warn", "weight": 110})

    sci = all_ranks.get("sci")
    if sci:
        val = str(sci).strip().upper()
        badges.append({"key": "sci", "label": f"JCR {val}" if not val.startswith("Q") else f"JCR {val}", "type": "jcr", "weight": 80})

    sciif = all_ranks.get("sciif")
    if sciif:
        try:
            val = round(float(sciif), 2)
            badges.append({"key": "sciif", "label": f"IF {val}", "type": "if", "weight": 75})
        except (ValueError, TypeError):
            badges.append({"key": "sciif", "label": f"IF {sciif}", "type": "if", "weight": 75})

    ccf = all_ranks.get("ccf")
    if ccf:
        badges.append({"key": "ccf", "label": f"CCF-{str(ccf).strip().upper()}", "type": "ccf", "weight": 90})

    # 7. 国内核心
    for dom_k, dom_l in [("pku", "北大核心"), ("cssci", "南大核心"), ("cscd", "CSCD")]:
        dom_val = all_ranks.get(dom_k)
        if dom_val and str(dom_val).lower() not in ("0", "false", "no", "none", ""):
            badges.append({"key": dom_k, "label": dom_l, "type": "domestic", "weight": 70})

    # 自定义数据集:rank 每条是 "数据集uuid&&&档位序号"
    rank_infos = {it.get("uuid"): it for it in custom.get("rankInfo", []) if isinstance(it, dict) and it.get("uuid")}
    for r_entry in custom.get("rank", []):
        if isinstance(r_entry, str) and "&&&" in r_entry:
            uuid_part, rank_num = r_entry.split("&&&", 1)
            info = rank_infos.get(uuid_part)
            if info:
                abb = info.get("abbName", "")
                rank_field_map = {"1": "oneRankText", "2": "twoRankText", "3": "threeRankText", "4": "fourRankText", "5": "fiveRankText"}
                rank_text = info.get(rank_field_map.get(rank_num, ""), rank_num)
                badges.append({"key": f"custom_{uuid_part}", "label": f"{abb} {rank_text}", "type": "custom", "weight": 60})

    badges.sort(key=lambda b: -b.get("weight", 0))
    return badges


def query_publication_rank(conn: sqlite3.Connection, publication_name: str, force_refresh: bool = False) -> dict[str, Any]:
    """查询指定期刊的等级与分区。优先读缓存，过期或强制时请求 easyScholar API。"""
    name = (publication_name or "").strip()
    if not name:
        return {"ok": False, "msg": "期刊名称不能为空", "badges": []}

    ensure_table(conn)
    norm_name = name.casefold()

    if not force_refresh:
        row = conn.execute("SELECT official_rank, custom_rank, updated_at FROM easyscholar_cache WHERE publication_name=?", (norm_name,)).fetchone()
        if row:
            try:
                updated_at = datetime.fromisoformat(row[2])
                if datetime.now(timezone.utc) - updated_at.replace(tzinfo=timezone.utc) < timedelta(days=CACHE_EXPIRE_DAYS):
                    official = json.loads(row[0] or "{}")
                    custom = json.loads(row[1] or "{}")
                    return {
                        "ok": True,
                        "source": "cache",
                        "publication_name": name,
                        "officialRank": official,
                        "customRank": custom,
                        "badges": parse_badges(official, custom),
                    }
            except Exception as e:
                logger.warning("Cache parse error for %s: %s", name, e)

    cfg = get_config(conn)
    if not cfg["enabled"]:
        return {"ok": False, "msg": "easyScholar 接口未在设置中开启", "badges": []}
    secret_key = cfg["secret_key"]
    if not secret_key:
        return {"ok": False, "msg": "easyScholar SecretKey 尚未配置", "badges": []}

    _throttle()
    params = urllib.parse.urlencode({"secretKey": secret_key, "publicationName": name})
    url = f"{EASYSCHOLAR_API_URL}?{params}"
    
    req = urllib.request.Request(url, headers={"User-Agent": "ResearchWorkbench/1.3.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "msg": f"easyScholar 网络请求异常: {exc}", "badges": []}

    code = data.get("code")
    if code != 200:
        return {"ok": False, "code": code, "msg": data.get("msg", "easyScholar 接口调用失败"), "badges": []}

    res_data = data.get("data") or {}
    official = res_data.get("officialRank") or {}
    custom = res_data.get("customRank") or {}

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("""
    INSERT INTO easyscholar_cache (publication_name, official_rank, custom_rank, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(publication_name) DO UPDATE SET
      official_rank=excluded.official_rank,
      custom_rank=excluded.custom_rank,
      updated_at=excluded.updated_at
    """, (norm_name, json.dumps(official, ensure_ascii=False), json.dumps(custom, ensure_ascii=False), now_iso))
    conn.commit()

    return {
        "ok": True,
        "source": "api",
        "publication_name": name,
        "officialRank": official,
        "customRank": custom,
        "badges": parse_badges(official, custom),
    }


def test_connection(secret_key: str) -> dict[str, Any]:
    """测试指定 SecretKey 是否有效（使用 Nature 期刊验证）。"""
    key = (secret_key or "").strip()
    if not key:
        return {"ok": False, "msg": "SecretKey 不能为空"}
    _throttle()
    params = urllib.parse.urlencode({"secretKey": key, "publicationName": "Nature"})
    url = f"{EASYSCHOLAR_API_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "ResearchWorkbench/1.3.0"})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cost = int((time.time() - t0) * 1000)
    except Exception as exc:
        return {"ok": False, "msg": f"连接超时或网络异常: {exc}"}

    if data.get("code") == 200:
        res_data = data.get("data") or {}
        badges = parse_badges(res_data.get("officialRank") or {}, res_data.get("customRank") or {})
        return {
            "ok": True,
            "cost_ms": cost,
            "msg": "连接成功！接口鉴权通过",
            "sample_badges": [b["label"] for b in badges],
        }
    return {
        "ok": False,
        "code": data.get("code"),
        "msg": data.get("msg", "SecretKey 无效或鉴权失败"),
    }
