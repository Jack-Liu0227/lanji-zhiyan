# -*- coding: utf-8 -*-
"""览籍智研 · 文献与运行环境一键自检工具。

换机部署或排查「文献用不了」时运行：
    python tools/check_literature_env.py
"""
import os
import sys
import sqlite3
import subprocess
import shutil
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(_ROOT / "packages" / package))


def warmup_translation():
    import papertran
    import paths
    import lowbox
    engine = papertran.MANAGED_BABELDOC
    if not engine.is_file():
        uv = shutil.which("uv")
        if not uv:
            print("请先安装 uv，再初始化翻译环境。")
            return 1
        environment = engine.parents[1]
        python = environment / "Scripts" / "python.exe"
        if not python.is_file():
            interpreter = sys.executable if sys.version_info < (3, 14) else "3.13"
            run = subprocess.run([uv, "venv", "--python", interpreter, str(environment)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if run.returncode:
                return run.returncode
        run = subprocess.run([uv, "pip", "install", "--python", str(python), "babeldoc", "bitstring<5"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if run.returncode:
            return run.returncode
    paths.LOWTMP.mkdir(parents=True, exist_ok=True)
    if lowbox.available() and not lowbox.mark_low(paths.LOWTMP, recursive=True):
        print("无法为翻译缓存设置桌面沙箱写入权限，请检查数据目录权限。")
        return 1
    return subprocess.run([str(engine), "--warmup"], env=papertran._env(),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode

def run_check():
    print("=" * 60)
    print("  览籍智研 文献与运行环境诊断")
    print("=" * 60)

    # 1. 本地数据库与文献
    import paths
    db_path = paths.DB_PATH

    print("\n[1/5] 本地数据库与文献数据 (P1)")
    print(f"  数据库路径: {db_path}")
    if not db_path.is_file():
        print("  [-] 数据库文件尚不存在（全新部署）。")
        print("      修复动作: 启动一次应用会自动创建基础结构，文献通过「论文搜索」或「Zotero 导入」重新入库。")
    else:
        try:
            conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
            c = conn.cursor()
            def count(t):
                try: return c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                except Exception: return 0
            n_papers = count("papers")
            n_pages = count("paper_pages")
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
            conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
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
                            securestore.unprotect(raw_key)
                            print(f"  [+] Provider「{r['name']}」: DPAPI 密文解密成功。")
                        except Exception:
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
    import papertran
    babel_path = str(papertran.MANAGED_BABELDOC) if papertran.MANAGED_BABELDOC.is_file() else os.popen("where babeldoc 2>nul").read().strip()
    cache_dir = paths.LOWTMP / "home" / ".cache" / "babeldoc"
    cache_ok = cache_dir.is_dir() and any(cache_dir.iterdir()) if cache_dir.is_dir() else False

    if uv_path:
        print(f"  [+] uv: {uv_path.splitlines()[0]}")
    else:
        print("  [-] 未找到 uv 工具。")
        print("      修复动作: pip install uv")

    if babel_path or uv_path:
        print(f"  [+] babeldoc 命令可用性: {'已安装' if babel_path else '可通过 uv tool 运行'}")
    else:
        print('  [-] 未找到 babeldoc。修复动作: uv tool install --with "bitstring<5" babeldoc')

    if cache_ok:
        print(f"  [+] 应用翻译缓存已存在: {cache_dir}")
    else:
        print("  [-] 应用翻译缓存为空或不存在。")
        print('      修复动作: 运行 `python tools/check_literature_env.py --warmup` 初始化应用使用的模型与字体缓存。')

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
            req = urllib.request.Request(url, headers={"User-Agent": "LanjiZhiyan/0.1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"  [+] {name}: 连通正常 (HTTP {resp.status})")
        except Exception as e:
            print(f"  [-] {name} 连通受阻: {e}")
            print("      提示: 论文检索需要外网或系统代理支持。")

    print("\n" + "=" * 60)
    print("  自检完成。如遇 [-] 项目请按对应提示处理。")
    print("=" * 60)

if __name__ == "__main__":
    if "--warmup" in sys.argv:
        raise SystemExit(warmup_translation())
    run_check()
