CREATE INDEX idx_tasks_scope_created
    ON tasks(project_id, principal_id, created_at DESC, task_id DESC);
