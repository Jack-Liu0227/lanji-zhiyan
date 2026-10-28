CREATE TABLE team_execution_instances (
  project_id TEXT NOT NULL, template_team_id TEXT NOT NULL, instance_team_id TEXT NOT NULL UNIQUE,
  template_revision TEXT NOT NULL DEFAULT '', PRIMARY KEY(project_id,template_team_id)
);
CREATE TABLE team_execution_members (
  instance_team_id TEXT NOT NULL, template_member_id TEXT NOT NULL, instance_member_id TEXT NOT NULL,
  PRIMARY KEY(instance_team_id,template_member_id)
);
