# -*- coding: utf-8 -*-
"""科研工作台 · 文献与运行环境一键自检工具。

换机部署或排查「文献用不了」时运行：
    python tools/check_literature_env.py
"""
import os
import sys
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "packages" / "workspace-core"))
sys.path.insert(0, str(_ROOT / "packages" / "workspace-service"))
sys.path.insert(0, str(_ROOT / "packages" / "connectors"))

def run_check():
    print("=" * 60)
    print("  Research Workbench 文献与运行环境诊断")
    print("=" * 60)

    # 1. 本地数据库与文献
    db_env = os.environ.get("RESEARCH_WORKBENCH_DB")
    if db_env:
        db_path = Path(db_env)
    else:
        appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        db_path = Path(appdata) / "ResearchWorkbench" / "data" / "research.sqlite3"

    print("\n[1/5] 本地数据库与文献数据 (P1)")
    print(f"  数据库路径: {db_path}")
    if not db_path.is_file():
        print("  [-] 数据库文件尚不存在（全新部署）。")
        print("      修复动作: 启动一次应用会自动创建基础结构，文献通过「论文搜索」或「Zotero 导入」重新入库。")
    else:
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            def count(t):
                try: return c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                except Exception: return 0
            n_papers = count("papers")
            n_pages = count("paper_pages")
            n_prov = count("providers")
            n_mdl = count("model_profiles")
            conn.close()
            papers_dir = db_path.parent / "papers"
            pdf_count = len(list(papers_dir.glob("*.pdf"))) if papers_dir.is_dir() else 0
            print(f"  [+] 库内统计: papers={n_papers}, paper_pages={n_pages}, PDF附件={pdf_count}个")
            if n_papers == 0:
                print("  [i] 库中文献数为 0（源码不含文献数据，属正常现象）。")
                print("      修复动作: 在界面使用「论文搜索」或从 Zotero 导入文献。")
        except Exception as e:
            print(f"  [-] 读取数据库失败: {e}")

    # 2. 模型凭据与 DPAPI (P2)
    print("\n[2/5] 模型配置与 Windows DPAPI 凭据 (P2)")
    try:
        import securestore
        sec_mode = securestore.security()
        print(f"  [+] 凭据安全存储模式: {sec_mode}")
        if db_path.is_file():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, name, api_key FROM providers").fetchall()
            conn.close()
            if not rows:
                print("  [i] 尚未配置 Provider/模型。请在「设置 → 模型」中填入自己的 API Key。")
            else:
                for r in rows:
                    raw_key = r["api_key"] or ""
                    if raw_key.startswith("dpapi:"):
                        try:
                            dec = securestore.unprotect(raw_key)
                            print(f"  [+] Provider「{r['name']}」: DPAPI 密文解密成功。")
                        except Exception as exc:
                            print(f"  [!] Provider「{r['name']}」: DPAPI 解密失败 (Errno 87)。")
                            print("      原因: 该库从其他机器或不同 Windows 账户拷入，无法跨机解开。")
                            print("      修复动作: 在「设置 → 模型」中重新填写并保存该 Provider 的 API Key。")
                    else:
                        print(f"  [+] Provider「{r['name']}」: 未加密或无 Key。")
    except Exception as e:
        print(f"  [-] 检查模型凭据失败: {e}")

    # 3. 翻译引擎 (P3)
    print("\n[3/5] 论文翻译环境 (P3 · babeldoc / uv)")
    uv_path = os.popen("where uv 2>nul").read().strip()
    babel_path = os.popen("where babeldoc 2>nul").read().strip()
    cache_dir = Path.home() / ".cache" / "babeldoc"
    cache_ok = cache_dir.is_dir() and any(cache_dir.iterdir()) if cache_dir.is_dir() else False

    if uv_path:
        print(f"  [+] uv: {uv_path.splitlines()[0]}")
    else:
        print("  [-] 未找到 uv 工具。")
        print("      修复动作: pip install uv")

    if babel_path or uv_path:
        print(f"  [+] babeldoc 命令可用性: {'已安装' if babel_path else '可通过 uv tool 运行'}")
    else:
        print("  [-] 未找到 babeldoc。修复动作: uv tool install babeldoc")

    if cache_ok:
        print(f"  [+] ~/.cache/babeldoc 模型缓存就绪: {cache_dir}")
    else:
        print(f"  [-] ~/.cache/babeldoc 缓存为空或不存在。")
        print("      修复动作: 联网状态下运行 `uv tool run babeldoc 任意文件.pdf` 进行一次离线初始化。")

    # 4. Zotero 7 本地接口 (P4)
    print("\n[4/5] Zotero 7 自适应接入 (P4)")
    try:
        import zotero
        z_st = zotero.status()
        if z_st.get("executable"):
            print(f"  [+] 检测到本机 Zotero 程序: {z_st['executable']}")
        else:
            print("  [-] 未检测到 Zotero 7 安装路径。")
        if z_st.get("data_dir"):
            st_flag = "存在" if z_st.get("storage_exists") else "不存在"
            print(f"  [+] 自适应定位 Zotero 数据目录: {z_st['data_dir']} (storage 附件目录: {st_flag})")
        if z_st.get("connected") and z_st.get("readable"):
            print(f"  [+] Zotero 7 本地 API 正常就绪 (库内条目数: {z_st.get('total') or '未知'})")
        else:
            print(f"  [-] Zotero 7 状态: {z_st.get('message')}")
            if z_st.get("can_launch"):
                print("      提示: 检测到本机已安装 Zotero，可直接在工作台点「启动 Zotero」或运行 `zotero.launch_zotero()` 唤醒。")
    except Exception as e:
        print(f"  [-] 检查 Zotero 失败: {e}")

    # 5. 外网文献检索源 (P5)
    print("\n[5/5] 外网文献检索源连通性 (P5)")
    for name, url in [("Crossref", "https://api.crossref.org/works?rows=1"),
                      ("OpenAlex", "https://api.openalex.org/works?per-page=1")]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ResearchWorkbench"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"  [+] {name}: 连通正常 (HTTP {resp.status})")
        except Exception as e:
            print(f"  [-] {name} 连通受阻: {e}")
            print("      提示: 论文检索需要外网或系统代理支持。")

    print("\n" + "=" * 60)
    print("  自检完成。如遇 [-] 项目请按对应提示处理。")
    print("=" * 60)

if __name__ == "__main__":
    run_check()
