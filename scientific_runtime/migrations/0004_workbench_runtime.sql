CREATE TABLE workbench_mutations (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN (
        'revise_draft', 'persist_plan', 'persist_approval', 'abandon_task'
    )),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    outcome_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, principal_id, operation, idempotency_key),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id)
);

CREATE TABLE task_abandonments (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    abandoned_at TEXT NOT NULL,
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id)
);

CREATE TRIGGER workbench_mutations_are_immutable
BEFORE UPDATE ON workbench_mutations
BEGIN
    SELECT RAISE(ABORT, 'workbench mutations are immutable');
END;

CREATE TRIGGER workbench_mutations_cannot_be_deleted
BEFORE DELETE ON workbench_mutations
BEGIN
    SELECT RAISE(ABORT, 'workbench mutations are immutable');
END;

CREATE TRIGGER task_abandonments_require_pre_runtime_task
BEFORE INSERT ON task_abandonments
WHEN NOT EXISTS (
    SELECT 1 FROM tasks
    WHERE tasks.task_id = NEW.task_id
      AND tasks.project_id = NEW.project_id
      AND tasks.principal_id = NEW.principal_id
      AND tasks.status IN ('Draft', 'NeedsInput', 'AwaitingApproval')
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_intents
          WHERE dispatch_intents.task_id = tasks.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM run_events
          WHERE run_events.task_id = tasks.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a pre-runtime task can be abandoned');
END;

CREATE TRIGGER task_abandonments_are_immutable
BEFORE UPDATE ON task_abandonments
BEGIN
    SELECT RAISE(ABORT, 'task abandonments are immutable');
END;

CREATE TRIGGER task_abandonments_cannot_be_deleted
BEFORE DELETE ON task_abandonments
BEGIN
    SELECT RAISE(ABORT, 'task abandonments are immutable');
END;

DROP TRIGGER runtime_status_requires_latest_event;

CREATE TRIGGER runtime_status_requires_latest_event
BEFORE UPDATE OF status ON tasks
WHEN NEW.status != OLD.status
 AND NEW.status IN (
    'Queued', 'Running', 'Waiting', 'Retrying',
    'Succeeded', 'Failed', 'Cancelled'
 )
 AND NOT (
    NEW.status = 'Cancelled'
    AND OLD.status IN ('Draft', 'NeedsInput', 'AwaitingApproval')
    AND EXISTS (
        SELECT 1 FROM task_abandonments AS abandonment
        WHERE abandonment.task_id = OLD.task_id
          AND abandonment.project_id = OLD.project_id
          AND abandonment.principal_id = OLD.principal_id
    )
 )
 AND NOT EXISTS (
    SELECT 1 FROM run_events AS event
    WHERE event.task_id = OLD.task_id
      AND event.sequence = (
        SELECT MAX(latest.sequence) FROM run_events AS latest
        WHERE latest.task_id = OLD.task_id
      )
      AND event.task_status = NEW.status
 )
BEGIN
    SELECT RAISE(ABORT, 'runtime status requires its latest run event');
END;
