CREATE TABLE task_visibility_events (
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    event_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('trashed', 'restored')),
    previous_state TEXT NOT NULL CHECK (previous_state IN ('active', 'trashed')),
    state TEXT NOT NULL CHECK (state IN ('active', 'trashed')),
    trashed_at TEXT,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, revision),
    UNIQUE (task_id, project_id, principal_id, revision),
    CHECK (
        (action = 'trashed' AND previous_state = 'active'
         AND state = 'trashed' AND trashed_at = occurred_at)
        OR
        (action = 'restored' AND previous_state = 'trashed'
         AND state = 'active' AND trashed_at IS NULL)
    ),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id)
);

CREATE TABLE task_visibility (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'trashed')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    trashed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (task_id, project_id, principal_id),
    CHECK (
        (state = 'active' AND trashed_at IS NULL)
        OR (state = 'trashed' AND trashed_at IS NOT NULL)
    ),
    FOREIGN KEY (task_id, project_id, principal_id, revision)
        REFERENCES task_visibility_events(
            task_id, project_id, principal_id, revision
        )
);

CREATE TABLE task_visibility_mutations (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('trash_task', 'restore_task')),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id TEXT NOT NULL,
    visibility_revision INTEGER NOT NULL CHECK (visibility_revision >= 1),
    outcome_json TEXT NOT NULL,
    outcome_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, principal_id, operation, idempotency_key),
    FOREIGN KEY (task_id, project_id, principal_id, visibility_revision)
        REFERENCES task_visibility_events(
            task_id, project_id, principal_id, revision
        )
);

CREATE INDEX idx_task_visibility_events_scope
    ON task_visibility_events(
        project_id, principal_id, task_id, revision DESC
    );

CREATE INDEX idx_task_visibility_scope_state
    ON task_visibility(project_id, principal_id, state, task_id);

CREATE INDEX idx_task_visibility_mutations_task
    ON task_visibility_mutations(task_id);

CREATE TRIGGER task_visibility_events_are_append_only
BEFORE UPDATE ON task_visibility_events
BEGIN
    SELECT RAISE(ABORT, 'task visibility events are append-only');
END;

CREATE TRIGGER task_visibility_events_cannot_be_deleted
BEFORE DELETE ON task_visibility_events
BEGIN
    SELECT RAISE(ABORT, 'task visibility events are append-only');
END;

CREATE TRIGGER task_visibility_trash_requires_resolved_terminal
BEFORE INSERT ON task_visibility_events
WHEN NEW.action = 'trashed' AND NOT EXISTS (
    SELECT 1
    FROM tasks
    WHERE tasks.task_id = NEW.task_id
      AND tasks.project_id = NEW.project_id
      AND tasks.principal_id = NEW.principal_id
      AND tasks.status IN ('Succeeded', 'Failed', 'Cancelled')
      AND (
          (
              tasks.status = 'Cancelled'
              AND EXISTS (
                  SELECT 1 FROM task_abandonments
                  WHERE task_abandonments.task_id = tasks.task_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM dispatch_intents
                  WHERE dispatch_intents.task_id = tasks.task_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM approvals
                  WHERE approvals.task_id = tasks.task_id
                    AND approvals.decision = 'approved'
              )
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN dispatch_outcomes AS outcome
                ON outcome.intent_id = intent.intent_id
              WHERE intent.task_id = tasks.task_id
                AND outcome.outcome = 'dispatched'
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a resolved terminal task can be moved to trash');
END;

CREATE TRIGGER task_visibility_identity_is_immutable
BEFORE UPDATE OF task_id, project_id, principal_id ON task_visibility
BEGIN
    SELECT RAISE(ABORT, 'task visibility identity is immutable');
END;

CREATE TRIGGER task_visibility_cannot_be_deleted
BEFORE DELETE ON task_visibility
BEGIN
    SELECT RAISE(ABORT, 'task visibility projection cannot be deleted');
END;

CREATE TRIGGER task_visibility_mutations_are_immutable
BEFORE UPDATE ON task_visibility_mutations
BEGIN
    SELECT RAISE(ABORT, 'task visibility mutations are immutable');
END;

CREATE TRIGGER task_visibility_mutations_cannot_be_deleted
BEFORE DELETE ON task_visibility_mutations
BEGIN
    SELECT RAISE(ABORT, 'task visibility mutations are immutable');
END;

DROP TRIGGER task_abandonments_require_pre_runtime_task;

CREATE TRIGGER task_abandonments_require_pre_runtime_task
BEFORE INSERT ON task_abandonments
WHEN NOT EXISTS (
    SELECT 1 FROM tasks
    WHERE tasks.task_id = NEW.task_id
      AND tasks.project_id = NEW.project_id
      AND tasks.principal_id = NEW.principal_id
      AND tasks.status IN ('Draft', 'NeedsInput', 'AwaitingApproval')
      AND NOT EXISTS (
          SELECT 1 FROM approvals
          WHERE approvals.task_id = tasks.task_id
            AND approvals.decision = 'approved'
      )
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
    SELECT RAISE(ABORT, 'only an unapproved pre-runtime task can be abandoned');
END;
