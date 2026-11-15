"""The outer task center indexes canonical project tasks rather than a second task store."""
from pathlib import Path
import tempfile
import unittest

import project_bindings
import project_tasks
import project_task_routes
import workflow


class TaskCenterTest(unittest.TestCase):
    def test_global_and_project_filters_return_the_same_execution_ids(self):
        with tempfile.TemporaryDirectory(prefix='rw-task-center-') as temp:
            with workflow.WorkflowDB(Path(temp)/'test.sqlite3') as db:
                db.assistant_save('center-writer',name='Center writer',enabled=1)
                ids={}
                for title in ('First project','Second project'):
                    project=db.create_task(title,base_dir=str(Path(temp)/title))
                    binding=project_bindings.register(db,project,'assistant','center-writer')
                    task=project_tasks.create(db,{'project_id':project,'goal':title+' task','executor_binding_id':binding['id']},authority=project_tasks.CommandContext.ui(project))
                    ids[project]=task['id']
                all_rows=project_task_routes.route('GET','/api/project-tasks/all',{}, {},db)
                self.assertEqual({row['id'] for row in all_rows['tasks']},set(ids.values()))
                project=next(iter(ids))
                subset=project_task_routes.route('GET','/api/project-tasks/all',{}, {'project_id':[project]},db)
                ordinary=project_task_routes.route('GET','/api/project-tasks',{}, {'project_id':[project]},db)
                self.assertEqual(subset['tasks'][0]['id'],ordinary['tasks'][0]['id'])
                self.assertEqual(subset['total'],1)
                page=project_task_routes.route('GET','/api/project-tasks/all',{}, {'limit':['1'],'offset':['1']},db)
                self.assertEqual(len(page['tasks']),1)
                none=project_task_routes.route('GET','/api/project-tasks/all',{}, {'q':['NO_MATCH']},db)
                self.assertEqual(none['total'],0)


if __name__=='__main__':unittest.main()
