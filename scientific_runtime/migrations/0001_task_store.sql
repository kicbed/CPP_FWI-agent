CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'Draft', 'NeedsInput', 'AwaitingApproval', 'Queued', 'Running',
        'Waiting', 'Retrying', 'Succeeded', 'Failed', 'Cancelled'
    )),
    current_draft_id TEXT,
    current_draft_revision INTEGER CHECK (
        current_draft_revision IS NULL OR current_draft_revision >= 1
    ),
    current_plan_id TEXT,
    current_approval_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (current_draft_id IS NULL AND current_draft_revision IS NULL)
        OR (current_draft_id IS NOT NULL AND current_draft_revision IS NOT NULL)
    ),
    UNIQUE (task_id, current_draft_id, current_draft_revision),
    UNIQUE (task_id, current_plan_id),
    UNIQUE (task_id, current_approval_id),
    UNIQUE (task_id, project_id, principal_id),
    FOREIGN KEY (task_id, current_draft_id, current_draft_revision)
        REFERENCES draft_revisions(task_id, draft_id, revision),
    FOREIGN KEY (task_id, current_plan_id)
        REFERENCES plans(task_id, plan_id),
    FOREIGN KEY (task_id, current_approval_id)
        REFERENCES approvals(task_id, approval_id)
);

CREATE TABLE IF NOT EXISTS draft_revisions (
    task_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, revision),
    UNIQUE (draft_id, revision),
    UNIQUE (task_id, draft_id, revision),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS plans (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK (draft_revision >= 1),
    plan_hash TEXT NOT NULL,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, plan_id),
    UNIQUE (plan_id),
    UNIQUE (task_id, plan_id, plan_hash),
    FOREIGN KEY (task_id, draft_id, draft_revision)
        REFERENCES draft_revisions(task_id, draft_id, revision)
);

CREATE TABLE IF NOT EXISTS plan_node_idempotency (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    PRIMARY KEY (task_id, plan_id, node_id),
    UNIQUE (task_id, plan_id, idempotency_key),
    FOREIGN KEY (task_id, plan_id) REFERENCES plans(task_id, plan_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, approval_id),
    UNIQUE (approval_id),
    FOREIGN KEY (task_id, plan_id, plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash)
);

CREATE TABLE IF NOT EXISTS run_events (
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_status TEXT NOT NULL CHECK (task_status IN (
        'Queued', 'Running', 'Waiting', 'Retrying',
        'Succeeded', 'Failed', 'Cancelled'
    )),
    node_id TEXT,
    fingerprint_hash TEXT NOT NULL,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, sequence),
    UNIQUE (event_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('create_task', 'submit_task')),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, principal_id, operation, idempotency_key),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id)
);

CREATE INDEX IF NOT EXISTS idx_draft_revisions_task
    ON draft_revisions(task_id, revision);
CREATE INDEX IF NOT EXISTS idx_plans_task
    ON plans(task_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_approvals_task
    ON approvals(task_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_run_events_task
    ON run_events(task_id, sequence);
CREATE INDEX IF NOT EXISTS idx_run_events_node
    ON run_events(task_id, node_id, sequence);

CREATE TRIGGER IF NOT EXISTS tasks_identity_is_immutable
BEFORE UPDATE OF task_id, project_id, principal_id, created_at ON tasks
BEGIN
    SELECT RAISE(ABORT, 'task identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS tasks_must_start_before_runtime
BEFORE INSERT ON tasks
WHEN NEW.status NOT IN ('Draft', 'NeedsInput', 'AwaitingApproval')
BEGIN
    SELECT RAISE(ABORT, 'task must start before runtime');
END;

CREATE TRIGGER IF NOT EXISTS runtime_status_requires_latest_event
BEFORE UPDATE OF status ON tasks
WHEN NEW.status != OLD.status
 AND NEW.status IN (
    'Queued', 'Running', 'Waiting', 'Retrying',
    'Succeeded', 'Failed', 'Cancelled'
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

CREATE TRIGGER IF NOT EXISTS draft_revisions_are_append_only
BEFORE UPDATE ON draft_revisions
BEGIN
    SELECT RAISE(ABORT, 'draft revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS draft_revisions_cannot_be_deleted
BEFORE DELETE ON draft_revisions
BEGIN
    SELECT RAISE(ABORT, 'draft revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS plans_are_append_only
BEFORE UPDATE ON plans
BEGIN
    SELECT RAISE(ABORT, 'plans are append-only');
END;

CREATE TRIGGER IF NOT EXISTS plans_cannot_be_deleted
BEFORE DELETE ON plans
BEGIN
    SELECT RAISE(ABORT, 'plans are append-only');
END;

CREATE TRIGGER IF NOT EXISTS plan_node_idempotency_is_immutable
BEFORE UPDATE ON plan_node_idempotency
BEGIN
    SELECT RAISE(ABORT, 'plan node idempotency records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS plan_node_idempotency_cannot_be_deleted
BEFORE DELETE ON plan_node_idempotency
BEGIN
    SELECT RAISE(ABORT, 'plan node idempotency records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS approvals_are_append_only
BEFORE UPDATE ON approvals
BEGIN
    SELECT RAISE(ABORT, 'approvals are append-only');
END;

CREATE TRIGGER IF NOT EXISTS approvals_cannot_be_deleted
BEFORE DELETE ON approvals
BEGIN
    SELECT RAISE(ABORT, 'approvals are append-only');
END;

CREATE TRIGGER IF NOT EXISTS run_events_are_append_only
BEFORE UPDATE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS run_events_cannot_be_deleted
BEFORE DELETE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS idempotency_records_are_immutable
BEFORE UPDATE ON idempotency_records
BEGIN
    SELECT RAISE(ABORT, 'idempotency records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS idempotency_records_cannot_be_deleted
BEFORE DELETE ON idempotency_records
BEGIN
    SELECT RAISE(ABORT, 'idempotency records are immutable');
END;
