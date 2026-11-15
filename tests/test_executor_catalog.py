"""Display metadata groups legacy presets without changing actual grants or profiles."""
from pathlib import Path
import tempfile
import unittest

import project_bindings
import workflow


class ExecutorCatalogTest(unittest.TestCase):
    def test_legacy_preset_keys_preserve_local_overrides_and_distinct_custom_teams(self):
        with tempfile.TemporaryDirectory() as temp,workflow.WorkflowDB(Path(temp)/'db.sqlite3') as db:
            a=db.create_task('A');b=db.create_task('B')
            db.assistant_save('catalog-expert',name='Catalog expert')
            teams=[]
            for pid,kind in ((a,'fixed'),(b,'fixed'),(b,'custom')):
                team=db.team_add('文件撰写',kind,project_id=pid)
                db.team_member_add(team['id'],'Writer',role='leader',assistant_id='catalog-expert')
                teams.append(team)
            grant=project_bindings.register(db,a,'team',teams[0]['id'],enabled=False)
            before=db.db.total_changes
            catalog=project_bindings.list_bindings(db,a)
            self.assertEqual(db.db.total_changes,before)
            rows={row['target_id']:row for row in catalog if row['kind']=='team'}
            self.assertEqual(rows[teams[0]['id']]['selection_key'],rows[teams[1]['id']]['selection_key'])
            self.assertNotEqual(rows[teams[1]['id']]['selection_key'],rows[teams[2]['id']]['selection_key'])
            self.assertEqual(rows[teams[0]['id']]['owner_project_id'],a)
            self.assertEqual(rows[teams[1]['id']]['owner_project_title'],'B')
            self.assertFalse(rows[teams[0]['id']]['enabled'])
            self.assertEqual(rows[teams[0]['id']]['id'],grant['id'])
            self.assertEqual(len(db.teams()),3)
