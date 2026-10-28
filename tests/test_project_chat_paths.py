"""Project conversations distinguish ordinary folders from actual Zotero storage."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tests
import fileguard
import paths
import workflow
import zotero


class ProjectChatPathsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / 'research.sqlite3')
        self.addCleanup(self.db.close)
        for name, value in [('find_zotero_data_dir', self.root / 'zotero/actual-data'),
                            ('linked_attachment_roots', [self.root / 'linked-attachments'])]:
            mock = patch.object(zotero, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_named_sibling_project_can_create_chat_and_save_outputs(self):
        root = self.root / 'zotero/zotero/LLM'
        pid = self.db.create_task('LLM', base_dir=str(root))
        cid = self.db.create_chat('你可以做什么', task_id=pid)
        self.assertEqual(self.db.chat(cid)['task_id'], pid)
        output = root / 'review.md'
        fileguard.assert_writable(output)
        self.assertEqual(paths.validate_project_dir(root), root.resolve())

    def test_protected_or_missing_project_never_leaves_an_orphan_chat(self):
        pid = self.db.create_task('Legacy project')
        marker = self.root / 'custom-data/zotero.sqlite'
        marker.parent.mkdir()
        marker.touch()
        for root in (self.root / 'zotero/actual-data/storage',
                     self.root / 'linked-attachments/library', marker.parent / 'storage'):
            with self.subTest(root=root):
                self.db.db.execute('UPDATE research_tasks SET base_dir=? WHERE id=?', (str(root), pid))
                self.db.db.commit()
                with self.assertRaisesRegex(ValueError, 'Zotero'):
                    self.db.create_chat('blocked', task_id=pid)
                with self.assertRaisesRegex(ValueError, 'Zotero'):
                    fileguard.assert_writable(root / 'paper.pdf')
                self.assertEqual(self.db.chats(), [])
        with self.assertRaisesRegex(ValueError, '没有这个项目'):
            self.db.create_chat('missing', task_id='missing')
        self.assertEqual(self.db.chats(), [])


if __name__ == '__main__':
    unittest.main()
