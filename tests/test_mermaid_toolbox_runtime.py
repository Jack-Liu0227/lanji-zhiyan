"""Mermaid preview/export runs offline and exports actual high-resolution files."""
from pathlib import Path
import unittest

from PIL import Image
from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class MermaidToolboxRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page

    def open_tool(self,quick=False):
        if quick:self.page.locator('#paperToolbox').click()
        else:self.page.locator('#sidebar [data-nav="toolbox"]').click();self.page.locator('#paperToolboxPage').wait_for()
        self.page.get_by_role('button',name='Mermaid 图表',exact=False).click()
        self.preview()

    def preview(self):
        self.page.wait_for_function("document.querySelector('[data-mm-png]')&&!document.querySelector('[data-mm-png]').disabled||document.querySelector('[data-mm-error]')?.textContent",timeout=30000)
        self.assertEqual(self.page.locator('[data-mm-error]').inner_text(),'')

    def export(self,kind,name):
        folder=Path(web.ROOT)/'.codex-tasks/20261028-template-library/mermaid-artifacts';folder.mkdir(parents=True,exist_ok=True)
        with self.page.expect_download() as download:self.page.locator('[data-mm-'+kind+']').click()
        target=folder/name;download.value.save_as(str(target));return target

    def test_offline_preview_and_png_svg_exports_have_real_dimensions(self):
        self.load_page();remote=[]
        def local_only(route):
            if route.request.url.startswith(self.url):route.continue_()
            else:remote.append(route.request.url);route.abort()
        self.page.route('**/*',local_only)
        self.open_tool()
        self.assertTrue(self.page.locator('[data-tb-run]').is_hidden())
        self.assertTrue(self.page.locator('[name=model_id]').is_hidden())
        self.page.locator('[data-mm-code]').fill('```mermaid\nflowchart LR\n A["研究问题"] --> B["文献证据"]\n B --> C["科研报告"]\n```')
        self.preview();self.page.locator('[data-mm-width]').fill('2400');self.page.locator('[data-mm-zoom]').select_option('2')
        png=self.export('png','diagram-white.png')
        with Image.open(png) as image:
            self.assertEqual(image.width,2400);self.assertGreater(image.height,100)
            self.assertEqual(image.convert('RGBA').getpixel((0,0)),(255,255,255,255))
            self.assertGreater(len(image.getcolors(image.width*image.height) or []),5)
        self.page.locator('[data-mm-background]').select_option('transparent');self.preview()
        transparent=self.export('png','diagram-transparent.png')
        with Image.open(transparent) as image:self.assertEqual(image.convert('RGBA').getpixel((0,0))[3],0)
        svg=self.export('svg','diagram.svg').read_text(encoding='utf-8')
        self.assertIn('研究问题',svg);self.assertIn('viewBox',svg);self.assertNotIn('foreignObject',svg)
        self.assertEqual(remote,[]);self.assertEqual(self.errors,[])
        folder=png.parent
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            self.page.locator('.ptb-body').evaluate('element=>element.scrollTop=0')
            self.page.screenshot(path=str(folder/('mermaid-tool-'+theme+'.png')))

    def test_invalid_input_blocks_stale_exports_and_config_cannot_execute_html(self):
        self.load_page();self.open_tool(quick=True)
        self.page.locator('[data-mm-code]').fill('This is not a valid Mermaid diagram')
        self.page.wait_for_function("document.querySelector('[data-mm-error]')?.textContent")
        self.assertTrue(self.page.locator('[data-mm-png]').is_disabled());self.assertTrue(self.page.locator('[data-mm-svg]').is_disabled())
        attempts=[]
        self.page.route('https://example.invalid/**',lambda route:(attempts.append(route.request.url),route.abort()))
        source='%%{init: {"securityLevel":"loose","htmlLabels":true}}%%\nflowchart LR\n A["<img src=\'https://example.invalid/tracker\' onerror=\'window.mermaidInjected=1\'>"] --> B["安全预览"]'
        self.page.locator('[data-mm-code]').fill(source);self.preview()
        self.assertIsNone(self.page.evaluate('window.mermaidInjected'))
        self.assertEqual(attempts,[])
        self.page.locator('[data-mm-width]').fill('16000')
        self.page.locator('[data-mm-code]').fill('flowchart TD\n A[一] --> B[二]\n B --> C[三]\n C --> D[四]')
        self.preview();self.page.locator('[data-mm-png]').click()
        self.page.wait_for_function("document.querySelector('[data-mm-error]').textContent.includes('尺寸过大')")
        self.page.locator('[data-mm-width]').fill('1200');self.export('png','recovered.png')
        self.assertEqual(self.errors,[])

    def test_code_and_settings_survive_navigation_without_overwriting_text_draft(self):
        self.load_page();self.page.locator('#sidebar [data-nav="toolbox"]').click();self.page.locator('#paperToolboxPage').wait_for()
        self.page.locator('[data-tb-text]').fill('TRANSLATION_DRAFT')
        self.open_tool()
        code='sequenceDiagram\n participant A as 研究者\n participant B as 专家\n A->>B: 分析文献\n B-->>A: 返回证据'
        self.page.locator('[data-mm-code]').fill(code);self.page.locator('[data-mm-theme]').select_option('dark');self.preview()
        self.page.locator('[data-mm-width]').fill('4096');self.page.locator('[data-mm-filename]').fill('科研协作图')
        self.page.get_by_role('button',name='中英互译',exact=False).click()
        self.assertEqual(self.page.locator('[data-tb-text]').input_value(),'TRANSLATION_DRAFT')
        self.page.get_by_role('button',name='Mermaid 图表',exact=False).click();self.preview()
        self.page.reload(wait_until='networkidle');self.preview()
        self.assertEqual(self.page.locator('[data-mm-code]').input_value(),code)
        self.assertEqual(self.page.locator('[data-mm-width]').input_value(),'4096')
        self.assertEqual(self.page.locator('[data-mm-filename]').input_value(),'科研协作图')
        self.assertEqual(self.page.locator('[data-mm-theme]').input_value(),'dark')
        self.page.set_viewport_size({'width':760,'height':760})
        self.assertTrue(self.page.locator('[data-mm-png]').is_visible())
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[])
