CREATE TRIGGER project_task_node_run_deleted AFTER DELETE ON project_task_runs BEGIN
  DELETE FROM project_task_output_versions WHERE task_run_id=OLD.id;
  DELETE FROM workflow_node_drafts WHERE task_run_id=OLD.id;
  DELETE FROM workflow_node_commands WHERE task_run_id=OLD.id;
  DELETE FROM project_task_rework_requests WHERE new_run_id=OLD.id OR base_run_id=OLD.id;
  DELETE FROM project_task_file_operations WHERE task_run_id=OLD.id;
END;

CREATE TRIGGER project_task_node_result_deleted AFTER DELETE ON project_tasks BEGIN
  DELETE FROM project_task_output_versions WHERE task_id=OLD.id;
  DELETE FROM workflow_node_commands WHERE task_id=OLD.id;
  DELETE FROM project_task_rework_requests WHERE task_id=OLD.id;
  DELETE FROM project_task_file_operations WHERE task_id=OLD.id;
END;
