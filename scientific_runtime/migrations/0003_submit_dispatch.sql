CREATE TEMP TABLE scientific_runtime_v3_upgrade_guard (
    valid INTEGER NOT NULL CHECK (valid = 1)
);

INSERT INTO scientific_runtime_v3_upgrade_guard(valid)
SELECT 0
WHERE EXISTS (
    SELECT 1 FROM tasks
    WHERE status NOT IN ('Draft', 'NeedsInput', 'AwaitingApproval')
)
OR EXISTS (
    SELECT 1 FROM approval_budgets WHERE tasks_used != 0
)
OR EXISTS (
    SELECT 1 FROM idempotency_records WHERE operation = 'submit_task'
);

DROP TABLE scientific_runtime_v3_upgrade_guard;

CREATE TABLE dispatch_intents (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_idempotency_key TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, plan_id, node_id),
    FOREIGN KEY (task_id, plan_id, plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (task_id, plan_id, node_id)
        REFERENCES plan_node_idempotency(task_id, plan_id, node_id)
);

CREATE TABLE dispatch_attempts (
    intent_id TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL,
    FOREIGN KEY (intent_id) REFERENCES dispatch_intents(intent_id)
);

CREATE TABLE dispatch_outcomes (
    intent_id TEXT PRIMARY KEY,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('dispatched', 'reconciliation_required')
    ),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (intent_id) REFERENCES dispatch_attempts(intent_id)
);

CREATE TABLE submit_idempotency_links (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation = 'submit_task'),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id TEXT NOT NULL,
    intent_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, principal_id, operation, idempotency_key),
    FOREIGN KEY (project_id, principal_id, operation, idempotency_key)
        REFERENCES idempotency_records(
            project_id, principal_id, operation, idempotency_key
        ),
    FOREIGN KEY (intent_id) REFERENCES dispatch_intents(intent_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_dispatch_intents_task
    ON dispatch_intents(task_id, intent_id);

CREATE TRIGGER dispatch_intents_are_immutable
BEFORE UPDATE ON dispatch_intents
BEGIN
    SELECT RAISE(ABORT, 'dispatch intents are immutable');
END;

CREATE TRIGGER dispatch_intents_cannot_be_deleted
BEFORE DELETE ON dispatch_intents
BEGIN
    SELECT RAISE(ABORT, 'dispatch intents are immutable');
END;

CREATE TRIGGER dispatch_outcomes_are_immutable
BEFORE UPDATE ON dispatch_outcomes
BEGIN
    SELECT RAISE(ABORT, 'dispatch outcomes are immutable');
END;

CREATE TRIGGER dispatch_attempts_are_immutable
BEFORE UPDATE ON dispatch_attempts
BEGIN
    SELECT RAISE(ABORT, 'dispatch attempts are immutable');
END;

CREATE TRIGGER dispatch_attempts_cannot_be_deleted
BEFORE DELETE ON dispatch_attempts
BEGIN
    SELECT RAISE(ABORT, 'dispatch attempts are immutable');
END;

CREATE TRIGGER dispatch_outcomes_cannot_be_deleted
BEFORE DELETE ON dispatch_outcomes
BEGIN
    SELECT RAISE(ABORT, 'dispatch outcomes are immutable');
END;

CREATE TRIGGER submit_idempotency_links_are_immutable
BEFORE UPDATE ON submit_idempotency_links
BEGIN
    SELECT RAISE(ABORT, 'submit idempotency links are immutable');
END;

CREATE TRIGGER submit_idempotency_links_cannot_be_deleted
BEFORE DELETE ON submit_idempotency_links
BEGIN
    SELECT RAISE(ABORT, 'submit idempotency links are immutable');
END;
