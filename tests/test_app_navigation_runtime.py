"""Follow and return from application links in an isolated browser profile."""
import unittest
from urllib.parse import urlencode

import project_bindings
import project_tasks
import workflow
from tests import test_project_workspace_runtime as workspace
from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class AppNavigationRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = workspace.ProjectWorkspaceRuntimeTest.seed

    def intelligence(self):
        rows = [dict(id=f'entry-{i}', title=f'Navigation result {i}', authors='A. Researcher',
                     summary='A result for testing the reading position.', source_title='Fixture',
                     published_at='2026-06-01', link='https://example.test/article', imported_result_id='record')
                for i in range(40)]
        self.page.route('**/api/rss/items?*', lambda route: route.fulfill(json={'items': rows, 'total': len(rows)}))
        self.page.route('**/api/rss/sources', lambda route: route.fulfill(json={'sources': []}))
        self.page.locator('#sidebar [data-nav="intel"]').click()
        self.page.locator('[data-intel-id="entry-39"]').wait_for()

    def task_link(self):
        with workflow.WorkflowDB(self.database_path) as db:
            binding = project_bindings.register(db, self.other, 'assistant', 'workspace-writer')['id']
            task = project_tasks.create(db, {'project_id': self.other, 'title': 'Linked task', 'executor_binding_id': binding},
                                        authority=project_tasks.CommandContext.ui(self.other))
            fragment = '#' + urlencode({'project': self.other, 'task': task['id']})
            (db.base_dir(self.pid) / 'evidence.md').write_text(
                f'# Jump here\n\n[Open linked task]({fragment})\n\n[Read externally](https://example.test/article)\n', encoding='utf-8')
            (db.base_dir(self.other) / 'target.md').write_text('# Goal', encoding='utf-8')
        return fragment

    def test_intelligence_returns_from_rss_settings_and_project_literature(self):
        self.seed()
        self.intelligence()
        page = self.page
        back = page.locator('#appReturnButton')
        page.locator('#itQ').fill('navigation result')
        page.locator('#itProject').select_option(self.other)
        page.locator('#itSourceSettings').click()
        page.locator('[data-rss-settings]').wait_for()
        self.assertEqual(back.locator('#appReturnLabel').inner_text(), '返回情报日报')
        back.click()
        page.locator('#itOpenLibrary').wait_for()
        self.assertEqual(page.locator('#itQ').input_value(), 'navigation result')
        self.assertEqual(page.locator('#itProject').input_value(), self.other)
        self.assertTrue(back.is_hidden())

        card = page.locator('[data-intel-id="entry-24"]')
        card.locator('summary').click()
        action = card.locator('[data-it-import]')
        action.evaluate("e=>e.scrollIntoView({block:'center'})")
        scroll = page.locator('#main').evaluate('e=>e.scrollTop')
        self.assertGreater(scroll, 500)
        action.click()
        page.locator('#plList').wait_for()
        self.assertEqual(page.locator('#pwProject').input_value(), self.other)
        back.click()
        card.locator('details[open]').wait_for()
        self.assertAlmostEqual(page.locator('#main').evaluate('e=>e.scrollTop'), scroll, delta=2)
        self.assertEqual(page.locator('#itProject').input_value(), self.other)
        self.assertEqual(page.evaluate('curTask'), self.pid)

        page.locator('#itOpenLibrary').click()
        page.locator('#plIntelligence').click()
        back.click()
        page.locator('#plList').wait_for()
        self.assertEqual(page.locator('#pwProject').input_value(), self.other)
        back.click()
        page.locator('#itList').wait_for()
        self.assertTrue(back.is_hidden())
        self.assertEqual(self.errors, [])

    def test_task_link_returns_to_origin_and_checks_the_destination_edit(self):
        self.seed()
        fragment = self.task_link()
        page = self.page
        page.locator('#pwAsk').fill('Draft from the first project')
        web.workspace_section(page, 'files')
        page.locator('#flList [data-rel="evidence.md"]').click()
        link = page.get_by_role('link', name='Open linked task')
        link.click()
        page.locator('.pw-task-detail').get_by_role('heading', name='Linked task').wait_for()
        self.assertEqual(page.locator('#pwProject').input_value(), self.other)
        self.assertEqual(page.url, self.url)

        web.workspace_section(page, 'files')
        page.locator('#flList [data-rel="target.md"]').click()
        page.locator('[data-fl-mode="edit"]').click()
        page.locator('#flEditText').fill('# Unsaved target edit')
        back = page.locator('#appReturnButton')
        page.route('**/api/conv/save', lambda route: route.fulfill(status=500, json={'error': 'Save guard'}))
        back.click()
        page.locator('#pwError').filter(has_text='保存失败').wait_for()
        self.assertTrue(back.is_visible())
        self.assertEqual(page.locator('#flEditText').input_value(), '# Unsaved target edit')
        self.assertEqual(page.locator('#pwProject').input_value(), self.other)

        page.unroute('**/api/conv/save')
        back.click()
        link.wait_for()
        self.assertEqual(page.locator('#pwProject').input_value(), self.pid)
        self.assertTrue(back.is_hidden())
        self.assertEqual(link.evaluate('e=>document.activeElement===e'), True)
        page.locator('#pwTab-chat').click()
        self.assertEqual(page.locator('#pwAsk').input_value(), 'Draft from the first project')
        self.assertEqual(self.errors, [])

        launch = self.context.new_page()
        launch.goto(self.url + fragment)
        launch.locator('.pw-task-detail').get_by_role('heading', name='Linked task').wait_for()
        self.assertTrue(launch.locator('#appReturn').is_hidden())

    def test_external_markdown_link_opens_a_new_tab(self):
        self.seed()
        self.task_link()
        self.context.route('https://example.test/article', lambda route: route.fulfill(body='External article'))
        web.workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="evidence.md"]').click()
        with self.page.expect_popup() as popup:
            self.page.get_by_role('link', name='Read externally').click()
        popup.value.wait_for_url('https://example.test/article')
        self.assertTrue(self.page.get_by_role('link', name='Open linked task').is_visible())
        self.assertTrue(self.page.locator('#appReturn').is_hidden())
        self.assertEqual(self.errors, [])

    def test_task_board_return_restores_project_and_filters(self):
        self.seed()
        self.task_link()
        page = self.page
        page.goto(self.url + '?navigation-test=1#task-center', wait_until='domcontentloaded')
        page.locator('[data-ec-project]').select_option(self.other)
        page.locator('[data-ec-search]').fill('Linked task')
        page.locator('[data-ec-layout]').select_option('workflow')
        page.locator('[data-ec-task]').filter(has_text='Linked task').dblclick()
        page.locator('[data-execution-id]').wait_for()
        self.assertEqual(page.locator('#pwProject').input_value(), self.other)
        page.locator('#appReturnButton').click()
        page.locator('[data-ec-task]').filter(has_text='Linked task').wait_for()
        self.assertEqual(page.locator('[data-ec-project]').input_value(), self.other)
        self.assertEqual(page.locator('[data-ec-search]').input_value(), 'Linked task')
        self.assertEqual(page.locator('[data-ec-layout]').input_value(), 'workflow')
        self.assertTrue(page.locator('#appReturn').is_hidden())
        self.assertEqual(self.errors, [])

    def test_return_reopens_the_origin_note_after_visiting_another_project_note(self):
        self.seed()
        fragment = self.task_link()
        page = self.page
        for project, name, content in ((self.pid, 'Origin', f'[Open linked task]({fragment})'),
                                       (self.other, 'Destination', 'Destination note body')):
            response = page.request.post(self.url + 'api/project/note/save',
                                         data={'project_id': project, 'path': f'笔记/{name}.md', 'title': name,
                                               'content': content, 'create': True})
            self.assertTrue(response.ok, response.text())
        web.workspace_section(page, 'notes')
        page.locator('[data-pn-file="笔记/Origin.md"]').click()
        link = page.locator('#mdPreview').get_by_role('link', name='Open linked task')
        link.click()
        page.locator('.pw-task-detail').wait_for()
        web.workspace_section(page, 'notes')
        page.locator('[data-pn-file="笔记/Destination.md"]').click()
        page.locator('#mdPreview').get_by_text('Destination note body').wait_for()
        page.locator('#appReturnButton').click()
        link.wait_for()
        self.assertEqual(page.locator('#pwProject').input_value(), self.pid)
        self.assertEqual(self.errors, [])

    def test_changing_to_a_task_fragment_keeps_the_address_and_draft_on_save_failure(self):
        self.seed()
        fragment = self.task_link()
        page = self.page
        web.workspace_section(page, 'files')
        page.locator('#flList [data-rel="evidence.md"]').click()
        page.locator('[data-fl-mode="edit"]').click()
        page.locator('#flEditText').fill('# Stay here until this saves')
        page.route('**/api/conv/save', lambda route: route.fulfill(status=500, json={'error': 'Save guard'}))
        page.evaluate('fragment=>{location.hash=fragment;}', fragment)
        page.locator('#pwError').filter(has_text='保存失败').wait_for()
        page.wait_for_url(self.url)
        self.assertEqual(page.locator('#flEditText').input_value(), '# Stay here until this saves')
        self.assertTrue(page.locator('#appReturn').is_hidden())

        page.unroute('**/api/conv/save')
        page.evaluate('fragment=>{location.hash=fragment;}', fragment)
        page.locator('.pw-task-detail').wait_for()
        page.locator('#appReturnButton').click()
        page.wait_for_url(self.url)
        self.assertEqual(page.locator('#pwProject').input_value(), self.pid)
        self.assertEqual(self.errors, [])
