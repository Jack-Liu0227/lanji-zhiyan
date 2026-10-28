import base64
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import unittest
from PIL import Image
from pypdf import PdfWriter

import usage_stats
import workflow
from tests import test_web_runtime as web


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ToolboxUsageRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page

    def screenshot(self,name):
        folder=Path(web.ROOT)/'.codex-tasks/20261028-toolbox-usage'
        folder.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(folder/name),full_page=True)

    def drop(self,name,raw):
        self.page.locator('[data-tb-import-result]').evaluate('(el)=>el.textContent=""')
        self.page.evaluate('''({name,data})=>{
          const transfer=new DataTransfer();
          transfer.items.add(new File([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],name));
          document.querySelector('.paper-toolbox').dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,bubbles:true,cancelable:true}));
        }''',{'name':name,'data':base64.b64encode(raw).decode()})

    def test_all_eight_tools_accept_their_own_files(self):
        self.load_page();self.page.evaluate("()=>openTab('toolbox')")
        self.page.locator('[data-tb-import-zone]').wait_for()
        for tool,name,text in [('translate','translation.txt','Translation draft'),('polish','polish.md','Polishing draft'),('convert','document.md','# Converted document'),('references','refs.bib','@article{sample, title={A study}, author={Li}, year={2026}}')]:
            self.page.locator('[data-tb-tool='+tool+']').click()
            self.page.locator('[data-tb-text]').fill('Existing draft')
            self.drop(name,text.encode())
            self.page.wait_for_function("text=>document.querySelector('[data-tb-text]').value.endsWith(text)",arg=text)
            self.assertTrue(self.page.locator('[data-tb-text]').input_value().startswith('Existing draft'))
            self.page.wait_for_function("!document.querySelector('[data-tb-run]').disabled")
            self.assertEqual(self.page.locator('[data-tb-tool='+tool+']').get_attribute('aria-pressed'),'true')
        self.page.locator('[data-tb-tool=mermaid]').click()
        self.drop('flow.mmd',b'flowchart LR\n A[Import] --> B[Preview]')
        self.page.wait_for_function("document.querySelector('[data-mm-code]').value.includes('Import')")
        self.page.locator('[data-mm-image]').wait_for(state='visible')
        buffer=io.BytesIO();Image.new('RGB',(48,36),'blue').save(buffer,format='PNG')
        for index,tool in enumerate(('caption','image_convert'),1):
            self.page.wait_for_function("!document.querySelector('[data-tb-run]').disabled")
            self.page.locator('[data-tb-tool='+tool+']').click();self.drop(tool+'.png',buffer.getvalue())
            self.page.wait_for_function('count=>document.querySelectorAll("[data-tb-image-list] li").length===count',arg=index)
            self.page.wait_for_function("!document.querySelector('[data-tb-run]').disabled")
        self.page.locator('[data-tb-tool=pdf_translate]').click()
        pid=self.page.evaluate('S.tasks[0].id');self.page.locator('.paper-toolbox [name=project_id]').select_option(pid)
        writer=PdfWriter();writer.add_blank_page(width=300,height=400);buffer=io.BytesIO();writer.write(buffer)
        self.drop('uploaded-paper.pdf',buffer.getvalue())
        self.page.wait_for_function("document.querySelector('[data-tb-import-result]').textContent.includes('已导入')")
        self.assertTrue(self.page.locator('[data-tb-paper]').input_value())
        self.assertIn('uploaded-paper',self.page.locator('[data-tb-paper]').inner_text())
        self.page.locator('[data-tb-tool=polish]').click();self.drop('wrong.png',b'wrong')
        self.page.wait_for_function("document.querySelector('[data-tb-error]').textContent.includes('文件类型')")
        self.assertEqual(self.page.locator('[data-tb-tool=polish]').get_attribute('aria-pressed'),'true')
        self.screenshot('toolbox-upload.png');self.assertEqual(self.errors,[])

    def seed_usage(self,missing=False):
        with workflow.WorkflowDB(self.database_path) as db:
            db.db.execute('DELETE FROM model_requests');db.db.commit()
            model=db.add_model('DeepSeek Flash','https://api.deepseek.com/v1','deepseek-flash','fixture-key')
            for index in range(2 if missing else 9):
                usage_stats.record(model,[{'role':'system','content':'S'*45000},{'role':'user','content':'Hello'}],[],1300,
                                   {} if missing else {'prompt':18000+index*2000,'completion':2200+index*70,'cached':9000,'reasoning':1000,'exact':True},
                                   'error' if missing else 'ok',0,240)
            rows=db.db.execute('SELECT id FROM model_requests ORDER BY created_at').fetchall()
            for index,row in enumerate(rows):
                context={'system':45000,'tools':30000,'tool_results':0,'conversation':4,'images':0}
                db.db.execute('UPDATE model_requests SET created_at=?,context_json=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(days=index)).isoformat(),json.dumps(context),row['id']))
            db.db.commit()
        self.load_page();self.page.evaluate("()=>openTab('usage')");self.page.locator('.usage-kpis').wait_for()

    def test_usage_visual_states_filters_and_prices(self):
        self.seed_usage()
        self.page.set_viewport_size({'width':1440,'height':1400})
        self.assertEqual(self.page.locator('.usage-plot rect').count(),9)
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme);self.screenshot('usage-overview-'+theme+'.png')
        self.page.locator('[data-view=context]').click();self.page.locator('.usage-context summary').first.click()
        self.assertEqual(self.page.locator('.usage-composition').first.locator('span').count(),3)
        self.assertIn('45,000',self.page.locator('.usage-legend').first.inner_text())
        self.assertIn('＜0.1%',self.page.locator('.usage-legend').first.inner_text())
        for theme in ('dark','light'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            self.page.locator('.usage-context summary').first.click()
            self.screenshot('usage-context-'+theme+'.png')
        for width in (900,600):
            self.page.set_viewport_size({'width':width,'height':900})
            if width==600:self.page.locator('#btnTopToggleSidebar').click()
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            self.assertTrue(self.page.locator('.usage-page').evaluate('el=>el.scrollWidth<=el.clientWidth'))
            self.screenshot('usage-context-'+str(width)+'.png')
        self.page.set_viewport_size({'width':1440,'height':1000})
        self.page.locator('[data-view=prices]').click();self.page.locator('[data-price-search]').fill('deepseek')
        self.assertEqual(self.page.locator('[data-price-table] tbody tr').count(),2)
        self.assertIn('官方价格',self.page.locator('[data-usage-body]').inner_text())
        self.page.locator('.usage-price-editor summary').click()
        form=self.page.locator('[data-price]');form.locator('[name=input]').fill('2')
        form.locator('[name=output]').fill('8');form.locator('[name=cached]').fill('.2')
        form.locator('button').first.click()
        self.page.wait_for_function("document.querySelector('[data-usage-body]').textContent.includes('手动单价')")
        self.page.locator('.usage-price-editor summary').click();self.page.locator('[data-price-reset]').click()
        self.page.wait_for_function("!document.querySelector('[data-usage-body]').textContent.includes('手动单价')")
        self.screenshot('usage-prices.png')
        self.page.locator('[data-view=requests]').click()
        search=self.page.locator('[data-usage-search]');search.fill('no-matching-model');search.press('Enter')
        self.page.locator('.usage-empty-cell').wait_for()
        self.assertTrue(search.evaluate('el=>el===document.activeElement'))
        search.press('Escape')
        self.page.wait_for_function("document.querySelectorAll('.usage-table tbody tr').length>1")
        self.page.locator('[data-status]').select_option('error')
        self.page.locator('.usage-empty-cell').wait_for()
        self.assertEqual(self.errors,[])

    def test_missing_usage_has_no_fake_zero_bars(self):
        self.seed_usage(missing=True)
        self.assertEqual(self.page.locator('.usage-kpis strong').first.inner_text(),'—')
        self.assertIn('尚未收到',self.page.locator('.usage-trend-card').inner_text())
        self.assertEqual(self.page.locator('.usage-plot rect').count(),0)
        self.screenshot('usage-missing.png')
        self.assertEqual(self.errors,[])
