"""Keyboard and menu regressions exercised against the real app and an isolated DB."""
from pathlib import Path
import unittest
from tests.test_web_runtime import workspace_section

from tests import test_web_runtime as runtime


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Microsoft Edge")
class InteractionsRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page
    new_project_note = runtime.WebRuntimeTest.new_project_note
    read_project_note = runtime.WebRuntimeTest.read_project_note

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def test_palette_restores_focus_and_does_not_cancel_background_run(self):
        self.load_page()
        self.page.locator('#ask').focus()
        self.page.evaluate("() => { window.cancelled=0; cancelAgentRun=()=>window.cancelled++; activeAgentRun={}; }")
        self.page.keyboard.press('Control+k')
        self.page.locator('#cmdPaletteInput').wait_for(state='visible')
        self.assertEqual(self.page.locator('#cmdPaletteInput').get_attribute('role'), 'combobox')
        self.page.keyboard.press('ArrowDown')
        option = self.page.locator('#cmdPaletteInput').get_attribute('aria-activedescendant')
        self.assertEqual(self.page.locator('#'+option).get_attribute('aria-selected'), 'true')
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#cmdPaletteModal').is_hidden())
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'ask')
        self.assertEqual(self.page.evaluate('window.cancelled'), 0)

    def test_palette_search_runs_navigation_and_zotero_command(self):
        self.load_page()
        self.page.locator('[data-command="palette"]').click()
        self.page.locator('#cmdPaletteInput').fill('新建项目')
        self.page.keyboard.press('Enter')
        self.page.locator('#t-title').wait_for()
        self.page.keyboard.press('Escape')
        self.page.locator('[data-command="palette"]').click()
        self.page.locator('#cmdPaletteInput').fill('Zotero')
        self.page.keyboard.press('Enter')
        self.page.locator('#zoteroModal').wait_for(state='visible')
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#zoteroModal').is_hidden())

    def test_confirmation_enter_uses_focused_button(self):
        self.load_page()
        self.page.locator('#topbar [data-command="help"]').focus()
        self.page.evaluate("() => { window.choice=null; uiConfirm('Delete this item?').then(v=>window.choice=v); }")
        self.page.locator('dialog[open]').wait_for()
        self.assertTrue(self.page.locator('dialog [data-no]').evaluate('el=>el===document.activeElement'))
        self.page.keyboard.press('Enter')
        self.page.wait_for_function('window.choice===false')
        self.assertTrue(self.page.locator('#topbar [data-command="help"]').evaluate('el=>el===document.activeElement'))
        self.page.evaluate("() => { window.choice=null; uiConfirm('Delete this item?').then(v=>window.choice=v); }")
        self.page.locator('dialog [data-ok]').focus()
        self.page.keyboard.press('Enter')
        self.page.wait_for_function('window.choice===true')

    def test_ime_enter_is_not_submission_and_ctrl_enter_sends_once(self):
        self.load_page()
        self.page.evaluate('() => { window.sent=0; send=()=>window.sent++; }')
        self.page.locator('#ask').fill('中文草稿')
        self.page.locator('#ask').dispatch_event('keydown', {'key':'Enter', 'isComposing':True, 'bubbles':True})
        self.assertEqual(self.page.evaluate('window.sent'), 0)
        self.page.keyboard.press('Control+Enter')
        self.page.wait_for_function('window.sent===1')
        self.page.keyboard.press('Shift+Enter')
        self.assertEqual(self.page.evaluate('window.sent'), 1)
        self.page.evaluate("() => { window.answer=null; uiPrompt('Folder name').then(v=>window.answer=v); }")
        self.page.locator('#uiPromptIn').fill('中文目录')
        self.page.locator('#uiPromptIn').dispatch_event('keydown', {'key':'Enter', 'isComposing':True, 'bubbles':True})
        self.assertIsNone(self.page.evaluate('window.answer'))
        self.page.keyboard.press('Enter')
        self.page.wait_for_function('window.answer!==null')
        self.assertEqual(self.page.evaluate('window.answer'), '中文目录')

    def test_input_keeps_single_keys_workflow_keys_and_native_context_menu(self):
        self.load_page()
        self.page.locator('#ask').fill('draft')
        self.page.keyboard.type('/?')
        self.page.keyboard.press('Alt+2')
        self.assertEqual(self.page.evaluate('route()'), 'agent')
        self.assertEqual(self.page.locator('#ask').input_value(), 'draft/?')
        self.assertTrue(self.page.locator('#ask').evaluate("el=>el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}))"))
        self.assertEqual(self.page.locator('[role="menu"]').count(), 0)
        self.assertEqual(self.page.locator('#shortcutHelp').count(), 0)

    def test_project_manager_keyboard_cancel_preserves_project(self):
        self.load_page()
        self.page.locator('[data-nav="workspace"]').click()
        self.page.locator('#pwProjectSettings > summary').click()
        self.page.locator('#pwProjectEdit').click()
        self.page.locator('dialog.project-manager').wait_for()
        before=self.page.evaluate('S.tasks.length')
        self.page.locator('[data-pm-delete]').click()
        self.page.keyboard.press('Enter')
        self.assertEqual(self.page.evaluate('S.tasks.length'),before)
        self.assertTrue(self.page.locator('dialog.project-manager').is_visible())
        self.page.keyboard.press('Escape')
        self.page.locator('dialog.project-manager').wait_for(state='detached')
        self.assertEqual(self.page.evaluate('S.tasks.length'),before)
        self.assertEqual(self.errors,[])


    def test_help_traps_focus_and_preferences_persist(self):
        self.load_page()
        self.page.locator('#topbar [data-command="help"]').click()
        self.page.locator('#shortcutHelp .modal-close').focus()
        self.page.keyboard.press('Shift+Tab')
        self.assertTrue(self.page.locator('[data-single-keys]').evaluate('el=>el===document.activeElement'))
        self.page.locator('[data-shortcuts]').uncheck()
        self.page.locator('[data-single-keys]').uncheck()
        self.page.keyboard.press('Escape')
        self.page.reload(wait_until='networkidle')
        self.page.locator('#topbar [data-command="help"]').click()
        self.assertFalse(self.page.locator('[data-shortcuts]').is_checked())
        self.assertFalse(self.page.locator('[data-single-keys]').is_checked())
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true,cancelable:true}))"))
        self.assertTrue(self.page.locator('#cmdPaletteModal').is_hidden())
        self.page.evaluate('() => { window.sent=0; send=()=>window.sent++; }')
        self.page.locator('#ask').fill('Draft with shortcuts disabled')
        self.page.keyboard.press('Control+Enter')
        self.assertEqual(self.page.evaluate('window.sent'),0)

    def test_note_save_formatting_and_navigation_failure_guard(self):
        self.new_project_note(title='Shortcut test',content='Evidence')
        self.page.locator('#mdSource').select_text()
        self.page.keyboard.press('Control+b')
        self.assertEqual(self.page.locator('#mdSource').input_value(), '**Evidence**')
        self.page.keyboard.press('Control+s')
        self.page.wait_for_function("document.querySelector('#mdSaveState')?.textContent==='已保存'")
        path=self.page.evaluate('mdProj.path')
        self.assertIn('**Evidence**',self.read_project_note(path)['content'])
        self.page.route('**/api/project/note/save', lambda route: route.fulfill(status=500,content_type='application/json',body='{"error":"save unavailable"}'))
        self.page.locator('#mdSource').fill('Keep this unsaved draft')
        self.page.locator('[data-nav="calendar"]').click()
        self.page.wait_for_function("document.querySelector('#mdSaveState')?.textContent==='保存失败'")
        self.assertEqual(self.page.evaluate('route()'), 'workspace')
        self.assertEqual(self.page.locator('#mdSource').input_value(),'Keep this unsaved draft')

    def test_tabs_keyboard_and_context_close_others(self):
        self.load_page()
        self.page.locator('[data-nav="calendar"]').click()
        self.page.locator('[data-nav="calendar"]').click()
        self.page.locator('#tabsbar [aria-selected="true"]').focus()
        self.page.keyboard.press('Home')
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("route()==='agent'")
        self.page.locator('#tabActions').click()
        self.page.get_by_role('menuitem',name='关闭其他标签页').click()
        self.page.wait_for_function('tabs.length===1')
        self.assertEqual(self.page.locator('#tabsbar [data-t]').count(),1)

    def test_retired_stage_shortcuts_do_not_open_research_forms(self):
        self.load_page()
        self.page.locator('[data-nav="workspace"]').click()
        workspace_section(self.page,'workflow')
        self.page.locator('[data-pwg-label]').wait_for()
        self.page.locator('#main').focus()
        self.page.keyboard.press('Alt+2')
        self.assertEqual(self.page.locator('#pwToolChoice, #wsInputQuery').count(),0)
        workspace_section(self.page,'files')
        self.page.locator('#main').focus()
        self.page.keyboard.press('/')
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'pwSearchInput')
        self.assertEqual(self.errors,[])


    def test_readonly_note_menu_uses_disabled_actions(self):
        self.load_page()
        def readonly_notes(route):
            response=route.fetch()
            data=response.json()
            data['read_only']=True
            route.fulfill(response=response,json=data)
        self.page.route('**/api/project/notes?*',readonly_notes)
        self.addCleanup(lambda: self.page.unroute_all(behavior='ignoreErrors'))
        self.page.evaluate("openTab('notes')")
        self.page.locator('[data-nb-view="tree"]').click()
        self.page.locator('[data-tree-path]').first.wait_for()
        self.page.wait_for_load_state('networkidle')
        self.page.wait_for_function('mdDashReadOnly===true')
        row=self.page.locator('[data-tree-type="dir"]').first
        row.locator('[data-context-trigger]').click()
        self.assertGreater(self.page.locator('[role="menuitem"]:disabled').count(),0,
            self.page.locator('[role="menuitem"]').evaluate_all("rows=>({readOnly:mdDashReadOnly,actions:rows.map(e=>({text:e.textContent,disabled:e.disabled}))})"))
        self.page.keyboard.press('End')
        self.assertTrue(self.page.evaluate("document.activeElement === [...document.querySelectorAll('[role=menuitem]:not(:disabled)')].at(-1)"))
        self.assertFalse(self.page.evaluate('document.activeElement.disabled'))

    def test_visual_review_light_dark_and_small_window(self):
        self.load_page('?pw=1')
        destination=Path(runtime.ROOT)/'output'/'interaction-review'
        destination.mkdir(parents=True,exist_ok=True)
        for theme in ('light','dark'):
            self.page.evaluate('(theme)=>setTheme(theme)',theme)
            self.page.locator('#topbar [data-command="help"]').click()
            self.page.screenshot(path=str(destination/f'shortcuts-{theme}.png'))
            self.page.keyboard.press('Escape')
        self.page.set_viewport_size({'width':900,'height':640})
        self.page.locator('[data-nav="calendar"]').click()
        self.page.evaluate('ProjectManager.open()')
        self.page.locator('dialog.project-manager').wait_for()
        self.page.screenshot(path=str(destination/'project-context-menu.png'))
        self.assertEqual(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),True)


if __name__ == '__main__':
    unittest.main()
