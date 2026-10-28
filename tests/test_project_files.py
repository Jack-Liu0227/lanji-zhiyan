"""Creation keeps a requested project directory, existing files, and write boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import project_files


class ProjectFilesTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rw-file-create-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        (self.root/'materials').mkdir()
        self.db=Mock()
        self.db.base_dir.return_value=self.root

    def create(self,**changes):
        return project_files.create(self.db,{'project_id':'p1','parent':'materials','name':'new.md','kind':'file',**changes})

    def test_file_and_folder_are_created_in_the_requested_parent(self):
        self.assertEqual(self.create()['rel'],'materials/new.md')
        folder=self.create(name='实验记录',kind='folder')
        self.assertTrue(Path(folder['path']).is_dir())
        self.assertEqual(self.create(parent='materials/实验记录',name='note.txt')['rel'],'materials/实验记录/note.txt')

    def test_existing_material_is_never_overwritten(self):
        path=self.root/'materials/new.md'
        path.write_text('EXISTING',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(path.read_text(encoding='utf-8'),'EXISTING')

    def test_invalid_names_outside_paths_and_unknown_kinds_are_rejected(self):
        for changes in ({'project_id':''},{'parent':'../'},{'parent':str(self.root)},
                        {'name':'../escape'},{'name':'C:\\escape'},{'name':'CON.txt'},
                        {'name':'trailing.'},{'parent':'missing'},{'kind':'unknown'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.create(**changes)
        self.assertEqual(list((self.root/'materials').iterdir()),[])

    def test_protected_project_cannot_be_written(self):
        with patch('project_files.project_notes.project_root',side_effect=ValueError('read only')):
            with self.assertRaisesRegex(ValueError,'read only'):
                self.create()


if __name__=='__main__':
    unittest.main()
