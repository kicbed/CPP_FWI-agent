CREATE TABLE task_purge_requests (
    purge_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    visibility_revision INTEGER NOT NULL CHECK (visibility_revision >= 1),
    request_hash TEXT NOT NULL,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (
        purge_id, task_id, project_id, principal_id, visibility_revision
    ),
    UNIQUE (
        purge_id, task_id, project_id, principal_id,
        visibility_revision, request_hash
    ),
    FOREIGN KEY (task_id, project_id, principal_id, visibility_revision)
        REFERENCES task_visibility_events(
            task_id, project_id, principal_id, revision
        )
);

CREATE TABLE task_purge_idempotency (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation = 'purge_task'),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    purge_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    visibility_revision INTEGER NOT NULL CHECK (visibility_revision >= 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, principal_id, operation, idempotency_key),
    FOREIGN KEY (
        purge_id, task_id, project_id, principal_id,
        visibility_revision, request_hash
    ) REFERENCES task_purge_requests(
        purge_id, task_id, project_id, principal_id,
        visibility_revision, request_hash
    )
);

CREATE TABLE task_purge_outcomes (
    purge_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    visibility_revision INTEGER NOT NULL CHECK (visibility_revision >= 1),
    local_run_state TEXT NOT NULL CHECK (
        local_run_state IN ('deleted', 'not_created')
    ),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    purged_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (
        purge_id, task_id, project_id, principal_id, visibility_revision
    ) REFERENCES task_purge_requests(
        purge_id, task_id, project_id, principal_id, visibility_revision
    )
);

CREATE INDEX idx_task_purge_requests_scope
    ON task_purge_requests(project_id, principal_id, task_id);

CREATE INDEX idx_task_purge_idempotency_purge
    ON task_purge_idempotency(purge_id);

CREATE INDEX idx_task_purge_idempotency_task
    ON task_purge_idempotency(task_id);

CREATE INDEX idx_task_purge_outcomes_scope
    ON task_purge_outcomes(project_id, principal_id, task_id);

CREATE TRIGGER task_purge_request_requires_current_trash
BEFORE INSERT ON task_purge_requests
WHEN NOT EXISTS (
    SELECT 1
    FROM task_visibility
    WHERE task_visibility.task_id = NEW.task_id
      AND task_visibility.project_id = NEW.project_id
      AND task_visibility.principal_id = NEW.principal_id
      AND task_visibility.state = 'trashed'
      AND task_visibility.revision = NEW.visibility_revision
)
BEGIN
    SELECT RAISE(ABORT, 'only the current trashed task can be purged');
END;

CREATE TRIGGER task_purge_alias_requires_pending_request
BEFORE INSERT ON task_purge_idempotency
WHEN EXISTS (
    SELECT 1 FROM task_purge_outcomes
    WHERE task_purge_outcomes.purge_id = NEW.purge_id
)
BEGIN
    SELECT RAISE(ABORT, 'a completed purge cannot accept another key');
END;

CREATE TRIGGER task_purge_outcome_requires_current_trash
BEFORE INSERT ON task_purge_outcomes
WHEN NOT EXISTS (
    SELECT 1
    FROM task_visibility
    WHERE task_visibility.task_id = NEW.task_id
      AND task_visibility.project_id = NEW.project_id
      AND task_visibility.principal_id = NEW.principal_id
      AND task_visibility.state = 'trashed'
      AND task_visibility.revision = NEW.visibility_revision
)
BEGIN
    SELECT RAISE(ABORT, 'purge completion requires the reserved trash revision');
END;

CREATE TRIGGER task_visibility_restore_rejects_purge_request
BEFORE INSERT ON task_visibility_events
WHEN NEW.action = 'restored' AND EXISTS (
    SELECT 1
    FROM task_purge_requests
    WHERE task_purge_requests.task_id = NEW.task_id
      AND task_purge_requests.project_id = NEW.project_id
      AND task_purge_requests.principal_id = NEW.principal_id
)
BEGIN
    SELECT RAISE(ABORT, 'a purge request cannot be restored');
END;

CREATE TRIGGER task_purge_requests_are_immutable
BEFORE UPDATE ON task_purge_requests
BEGIN
    SELECT RAISE(ABORT, 'task purge requests are immutable');
END;

CREATE TRIGGER task_purge_requests_cannot_be_deleted
BEFORE DELETE ON task_purge_requests
BEGIN
    SELECT RAISE(ABORT, 'task purge requests are immutable');
END;

CREATE TRIGGER task_purge_idempotency_is_immutable
BEFORE UPDATE ON task_purge_idempotency
BEGIN
    SELECT RAISE(ABORT, 'task purge idempotency records are immutable');
END;

CREATE TRIGGER task_purge_idempotency_cannot_be_deleted
BEFORE DELETE ON task_purge_idempotency
BEGIN
    SELECT RAISE(ABORT, 'task purge idempotency records are immutable');
END;

CREATE TRIGGER task_purge_outcomes_are_immutable
BEFORE UPDATE ON task_purge_outcomes
BEGIN
    SELECT RAISE(ABORT, 'task purge outcomes are immutable');
END;

CREATE TRIGGER task_purge_outcomes_cannot_be_deleted
BEFORE DELETE ON task_purge_outcomes
BEGIN
    SELECT RAISE(ABORT, 'task purge outcomes are immutable');
END;
