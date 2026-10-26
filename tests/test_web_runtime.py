from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
EDGE = next(
    (path for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
     if base and (path := Path(base) / "Microsoft/Edge/Application/msedge.exe").is_file()),
    None,
)


@unittest.skipUnless(sync_playwright and EDGE, "Requires Playwright and Microsoft Edge")
class WebRuntimeTest(unittest.TestCase):
    """Execute the actual page: syntax checks cannot catch failed DOM bindings."""

    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="rw-web-runtime-")
        cls.addClassCleanup(temporary.cleanup)
        cls.temporary_root = Path(temporary.name)
        cls.database_path = cls.temporary_root / "research.sqlite3"
        log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        cls.addClassCleanup(log.close)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/"
        env = dict(os.environ, RESEARCH_WORKBENCH_DB=str(Path(temporary.name) / "research.sqlite3"),
                   LANJI_ZHIYAN_DB=str(Path(temporary.name) / "research.sqlite3"),
                   RW_HOME=temporary.name, LANJI_ZHIYAN_HOME=temporary.name,
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        packaged = os.environ.get("LANJI_TEST_APP_DIRECTORY")
        if packaged:
            from tools.installer import APP
            command = [str(Path(packaged) / (APP + ".exe"))]
        else:
            command = [sys.executable, str(ROOT / "apps/desktop/server.py")]
        server = subprocess.Popen(
            [*command, "--port", str(port), "--no-open"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

        def stop_server() -> None:
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)

        cls.addClassCleanup(stop_server)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with urllib.request.urlopen(cls.url, timeout=1) as response:
                    response.read()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.seek(0)
            raise RuntimeError(f"Test server failed to start (rc={server.poll()}): {log.read()}")

        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self) -> None:
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def load_page(self, suffix: str = "") -> None:
        self.page.goto(self.url + suffix, wait_until="networkidle")
        self.assertEqual(self.errors, [], "Browser initialization failed: " + "\n".join(self.errors))

    def test_startup_renders_navigation_and_agent(self) -> None:
        self.load_page("?pw=1")
        self.assertTrue(self.page.evaluate("S.loaded"))
        self.assertGreater(self.page.locator("#sidebar [data-nav]").count(), 5)
        self.assertGreater(len(self.page.locator("#main").inner_text().strip()), 20)
        self.assertEqual(self.page.locator("#tabsbar [data-t]").count(), 1)
        self.assertIn("SQLite", self.page.locator("#statusbar").inner_text())

    def test_progress_board_stays_inside_project_space_in_every_view(self) -> None:
        self.load_page()
        self.assertEqual(self.page.locator('#sidebar [data-nav="kanban"]').count(), 0)
        self.page.locator('#sidebar [data-nav="space"]').click()
        self.page.locator('[data-sp-tab="plan"]').click()
        self.page.locator("#spPlan .kb-root").wait_for()
        for view in ("pipeline", "timeline", "kanban"):
            self.page.locator(f'#spPlan [data-view="{view}"]').click()
            self.assertTrue(self.page.locator("#spTabs").is_visible())
            self.assertTrue(self.page.locator("#spPlan .kb-root").is_visible())
            self.assertEqual(self.page.locator("#main > .kb-root").count(), 0)
        self.assertEqual(self.errors, [])

    def save_nested_note(self, filename: str) -> str:
        path = "笔记/界面验收/子目录/" + filename
        self.page.evaluate("""async path => {
          await api('/api/project/note/save', {
            project_id:S.tasks[0].id, path, content:'# UI recovery\\n\\nNESTED_NOTE_CONTENT'
          });
        }""", path)
        return path

    def test_project_notes_expand_nested_folders_and_open_file(self) -> None:
        self.load_page()
        path = self.save_nested_note("笔记层级.md")
        self.page.locator('#sidebar [data-nav="space"]').click()
        self.page.locator('[data-sp-tab="notes"]').click()
        self.page.locator('[data-nb-view="tree"]').click()
        self.page.locator("#nbTreeContainer").wait_for()
        for folder in ("笔记/界面验收", "笔记/界面验收/子目录"):
            row = self.page.locator(f'[data-tree-dir="{folder}"]')
            row.wait_for()
            if "open" not in row.get_attribute("class").split():
                row.click()
        self.page.locator(f'[data-tree-file="{path}"]').click()
        self.page.locator("#mdSource").wait_for()
        self.assertIn("NESTED_NOTE_CONTENT", self.page.locator("#mdSource").input_value())
        self.assertTrue(self.page.locator("#nbTreeContainer").is_visible())
        self.assertEqual(self.errors, [])

    def test_research_platform_project_folder_expands_and_previews_file(self) -> None:
        self.load_page()
        path = self.save_nested_note("项目文件.md")
        self.page.locator('#sidebar [data-nav="workflow"]').click()
        self.page.locator("#wfFileLocCard").click()
        self.page.locator("#panel-files").wait_for(state="visible")
        for folder in ("笔记", "笔记/界面验收", "笔记/界面验收/子目录"):
            self.page.locator(f'#flList .fl-tree-row[data-rel="{folder}"]').click()
        self.page.locator(f'#flList .fl-tree-row[data-rel="{path}"]').click()
        self.page.wait_for_function("document.querySelector('#flStage')?.textContent.includes('NESTED_NOTE_CONTENT')")
        self.assertTrue(self.page.locator("#wfFileLocCard.on").is_visible())
        self.assertTrue(self.page.locator("#wfProjSelect").is_visible())
        self.assertEqual(self.errors, [])

    def test_protected_project_notes_open_read_only_and_navigation_does_not_save(self) -> None:
        folder = self.temporary_root / "zotero/existing-project"
        note = folder / "笔记/研究计划/existing.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        original_content = "# Protected evidence\n\nREAD_ONLY_BROWSER_EVIDENCE"
        note.write_text(original_content, encoding="utf-8")
        with closing(sqlite3.connect(self.database_path)) as db, db:
            pid, previous = db.execute("SELECT id,base_dir FROM research_tasks LIMIT 1").fetchone()
            db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(folder), pid))

        def restore_project():
            with closing(sqlite3.connect(self.database_path)) as db, db:
                db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (previous, pid))

        self.addCleanup(restore_project)
        saves = []
        self.page.on("request", lambda request: saves.append(request.url)
                     if request.method == "POST" and "/api/project/note/" in request.url else None)
        self.load_page()
        self.page.locator('#sidebar [data-nav="space"]').click()
        self.page.locator('[data-sp-tab="notes"]').click()
        self.page.locator('[data-pn-file="笔记/研究计划/existing.md"]').click()
        self.page.wait_for_function("document.querySelector('#mdPreview')?.textContent.includes('READ_ONLY_BROWSER_EVIDENCE')")
        self.assertTrue(self.page.locator("#mdSource").evaluate("element => element.readOnly"))
        self.assertTrue(self.page.locator("#mdNoteTitle").evaluate("element => element.readOnly"))
        self.assertTrue(self.page.locator("#mdDashSave").is_disabled())
        self.assertTrue(self.page.locator("#btnMdExport").is_enabled())
        self.page.locator("#mdDashBackFiles").click()
        create = self.page.locator("#nbTreeNewFile, #pnBtnSideNewFile").first
        create.wait_for()
        self.assertTrue(create.is_disabled())
        self.assertEqual(saves, [])
        self.assertEqual(note.read_text(encoding="utf-8"), original_content)
        self.assertFalse((folder / "笔记/文献精读笔记").exists())
        self.assertEqual(self.errors, [])

    def test_zotero_modal_closes_from_button_and_mask(self) -> None:
        self.load_page()
        # Exercise the real modal and its bindings without requiring Zotero to run.
        modal = self.page.locator("#zoteroModal")
        for selector in ("#btnZotClose", "#zoteroModal .modal-mask"):
            modal.evaluate("element => element.classList.remove('hidden')")
            self.page.locator(selector).click(position={"x": 5, "y": 5})
            self.assertTrue(modal.evaluate("element => element.classList.contains('hidden')"))
        self.assertEqual(self.errors, [])

    def test_project_notes_dashboard_opens_existing_markdown(self) -> None:
        self.load_page()
        self.page.evaluate("openTab('notes')")
        self.page.locator('[data-nb-view="category"]').click()
        self.page.locator('[data-category="literature"]').wait_for()
        self.page.evaluate("""async () => {
          const project_id = S.tasks[0].id;
          await api('/api/project/note/save', {project_id, path:'笔记/文献矩阵/浏览器验证.md', content:'# 浏览器验证\\n\\n已有内容'});
        }""")
        self.page.locator('[data-category="literature"]').click()
        self.page.locator('[data-pn-file="笔记/文献矩阵/浏览器验证.md"]').click()
        self.page.locator("#mdSource").wait_for()
        self.assertIn("已有内容", self.page.locator("#mdSource").input_value())
        self.assertEqual(self.errors, [])

    def test_steward_binds_existing_project_and_restores_selection(self) -> None:
        self.load_page()
        project = self.page.evaluate("S.tasks[0]")
        picker = self.page.get_by_role("combobox", name="选择已有项目")
        picker.select_option(project["id"])
        self.page.wait_for_function("id => S.chats.find(c => c.id === curChat)?.task_id === id && document.querySelector('#outpick')?.disabled", arg=project["id"])
        self.assertTrue(self.page.locator("#outpick").is_disabled())
        self.assertIn(project["title"], self.page.locator(".at-project-scope").inner_text())
        self.assertIn("复制后处理", self.page.locator(".at-project-scope").inner_text())
        self.page.reload(wait_until="networkidle")
        self.assertEqual(self.page.locator("#projbind").input_value(), project["id"])
        error = self.page.evaluate("""async () => {
          try { await api('/api/chat/bind', {id:curChat, task_id:'missing-project'}); return ''; }
          catch (e) { return e.message; }
        }""")
        self.assertTrue(error)
        self.page.locator("#projbind").select_option("")
        self.page.wait_for_function("S.chats.find(c => c.id === curChat)?.task_id === '' && !document.querySelector('#outpick')?.disabled")
        self.assertFalse(self.page.locator("#outpick").is_disabled())
        self.assertEqual(self.errors, [])

    def new_project_note(self, title="Browser audit", content="Initial content"):
        self.load_page()
        self.page.evaluate("openTab('notes')")
        create = self.page.locator("#nbTreeNewFile, #pnBtnSideNewFile").first
        create.wait_for()
        self.page.wait_for_load_state("networkidle")
        create.click()
        self.page.locator("#mdNoteTitle").fill(title)
        self.page.locator("#mdSource").fill(content)

    def read_project_note(self, path):
        return self.page.evaluate("""async path => api(`/api/project/note?project_id=${encodeURIComponent(mdDashProject)}&path=${encodeURIComponent(path)}`)""", path)

    def delay_note_save(self):
        self.page.evaluate("""() => {
          clearTimeout(mdSaveTimer);
          window.auditOriginalApi = api;
          window.auditSaveCalls = 0;
          api = async (url, body) => {
            if(url !== '/api/project/note/save') return window.auditOriginalApi(url, body);
            window.auditSaveCalls++;
            const result = await window.auditOriginalApi(url, body);
            if(window.auditSaveCalls === 1) await new Promise(resolve => {window.auditRelease = resolve;});
            return result;
          };
          window.auditFirstSave = mdSaveProj();
        }""")
        self.page.wait_for_function("!!window.auditRelease")

    def test_same_title_notes_keep_both_files_and_rename_survives_reopen(self):
        self.new_project_note("Same title", "FIRST_CONTENT")
        self.assertTrue(self.page.evaluate("mdSaveProj()"))
        first = self.page.evaluate("mdProj.path")
        self.page.locator("#nbTreeNewFile, #pnBtnSideNewFile").first.click()
        self.page.wait_for_function("mdDraft && mdProj?.path === '' && document.querySelector('#mdNoteTitle')?.value === ''")
        self.page.locator("#mdNoteTitle").fill("Same title")
        self.page.locator("#mdSource").fill("SECOND_CONTENT")
        self.assertTrue(self.page.evaluate("mdSaveProj()"))
        second = self.page.evaluate("mdProj.path")
        self.assertNotEqual(first, second)
        self.assertIn("FIRST_CONTENT", self.read_project_note(first)["content"])
        self.assertIn("SECOND_CONTENT", self.read_project_note(second)["content"])
        self.page.locator("#mdNoteTitle").fill("Renamed title")
        self.assertTrue(self.page.evaluate("mdSaveProj()"))
        self.assertEqual(self.read_project_note(second)["title"], "Renamed title")
        self.page.locator("#mdDashBackFiles").click()
        card = self.page.locator(f'[data-pn-file="{second}"]')
        card.wait_for()
        self.assertIn("Renamed title", card.inner_text())
        card.click()
        self.page.wait_for_function("document.querySelector('#mdNoteTitle')?.value === 'Renamed title'")
        self.assertEqual(self.page.locator("#mdEditorShell").count(), 1)
        self.assertEqual(self.errors, [])

    def test_new_note_flushes_debounced_edits_and_preserves_editor(self):
        self.new_project_note()
        self.assertTrue(self.page.evaluate("mdSaveProj()"))
        previous = self.page.evaluate("mdProj.path")
        self.page.evaluate("""() => {
          $('mdSource').value = 'PENDING_EDITS_MUST_SURVIVE';
          $('mdSource').dispatchEvent(new Event('input', {bubbles:true}));
          document.querySelector('#nbTreeNewFile, #pnBtnSideNewFile').click();
        }""")
        self.page.wait_for_function("mdDraft && mdProj?.path === '' && document.querySelector('#mdSource')?.value === ''")
        self.assertIn("PENDING_EDITS_MUST_SURVIVE", self.read_project_note(previous)["content"])
        self.page.wait_for_timeout(750)
        self.assertEqual(self.page.locator("#mdEditorShell").count(), 1)
        self.page.locator("#mdSource").fill("New draft still editable")
        self.assertTrue(self.page.evaluate("mdSaveProj()"))
        self.assertEqual(self.errors, [])

    def test_late_save_cannot_change_another_drafts_title_or_saved_state(self):
        self.new_project_note("First draft", "First content")
        self.delay_note_save()
        self.page.evaluate("""async () => {
          mdProj = {pid:mdDashProject,path:'',folder:mdDashSelectedFolder,title:'',content:''};
          mdDraft = true; mdDashPath = '';
          await mdDashLoad();
          window.auditRelease();
          await window.auditFirstSave;
          api = window.auditOriginalApi;
        }""")
        current = self.page.evaluate("({title:mdProj.title,path:mdProj.path,draft:mdDraft,state:$('mdSaveState').textContent})")
        self.assertEqual(current, {"title": "", "path": "", "draft": True, "state": ""})
        self.assertEqual(self.errors, [])

    def test_overlapping_saves_keep_latest_edits_and_do_not_report_saved_early(self):
        self.new_project_note()
        self.delay_note_save()
        self.page.locator("#mdSource").fill("LATEST_EDITS")
        self.page.evaluate("window.auditSecondSave = mdSaveProj(); void 0")
        self.assertEqual(self.page.evaluate("window.auditSaveCalls"), 1)
        self.assertNotEqual(self.page.locator("#mdSaveState").inner_text(), "已保存")
        saved = self.page.evaluate("""async () => {
          window.auditRelease();
          const results = await Promise.all([window.auditFirstSave, window.auditSecondSave]);
          api = window.auditOriginalApi;
          return results;
        }""")
        self.assertEqual(saved, [True, True])
        self.assertEqual(self.read_project_note(self.page.evaluate("mdProj.path"))["body"], "LATEST_EDITS")
        self.assertEqual(self.page.locator("#mdSaveState").inner_text(), "已保存")
        self.assertEqual(self.errors, [])

    def test_failed_save_keeps_editor_and_blocks_navigation_to_new_note(self):
        self.new_project_note("Unsaved title", "UNSAVED_CONTENT")
        self.page.evaluate("""() => {
          clearTimeout(mdSaveTimer);
          window.auditNote = mdProj;
          window.auditOriginalApi = api;
          api = async (url, body) => {
            if(url === '/api/project/note/save') throw new Error('offline');
            return window.auditOriginalApi(url, body);
          };
        }""")
        self.page.locator("#nbTreeNewFile, #pnBtnSideNewFile").first.click()
        self.page.wait_for_function("document.querySelector('#mdSaveState')?.textContent === '保存失败'")
        self.assertTrue(self.page.evaluate("mdProj === window.auditNote"))
        self.assertIn("UNSAVED_CONTENT", self.page.locator("#mdSource").input_value())
        self.page.evaluate("api = window.auditOriginalApi; void 0")
        self.assertTrue(self.page.evaluate("mdSaveProj()"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
