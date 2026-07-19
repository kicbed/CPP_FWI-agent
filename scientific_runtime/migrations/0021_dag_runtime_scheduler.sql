-- P3 deterministic multi-node runtime scheduling and recovery.
--
-- The four v20 tables below intentionally keep their original names.  A
-- temporary data copy plus DROP/reCREATE avoids SQLite's rename-time rewrite
-- of child triggers and views.  The migration runner disables FK actions
-- before BEGIN IMMEDIATE and restores them only after foreign_key_check.

CREATE TEMP TABLE scientific_runtime_v21_dispatch_intents AS
SELECT * FROM dispatch_intents;

CREATE TEMP TABLE scientific_runtime_v21_dag_admissions AS
SELECT * FROM dag_node_execution_admissions;

CREATE TEMP TABLE scientific_runtime_v21_dag_execution_transitions AS
SELECT * FROM dag_node_execution_transition_facts;

CREATE TEMP TABLE scientific_runtime_v21_dag_terminal_facts AS
SELECT * FROM dag_node_terminal_facts;

DROP TABLE dag_node_terminal_facts;
DROP TABLE dag_node_execution_transition_facts;
DROP TABLE dag_node_execution_admissions;
DROP TABLE dispatch_intents;

CREATE TABLE dispatch_intents (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
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

INSERT INTO dispatch_intents
SELECT * FROM scientific_runtime_v21_dispatch_intents;

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

CREATE TABLE dag_node_execution_admissions (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71 AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    approval_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    pending_revision INTEGER NOT NULL CHECK (
        typeof(pending_revision) = 'integer' AND pending_revision >= 1
    ),
    queued_revision INTEGER NOT NULL CHECK (
        typeof(queued_revision) = 'integer'
        AND queued_revision = pending_revision + 1
    ),
    node_idempotency_key TEXT NOT NULL,
    input_binding_document_hash TEXT NOT NULL CHECK (
        length(input_binding_document_hash) = 71
        AND substr(input_binding_document_hash, 1, 7) = 'sha256:'
    ),
    input_fencing_token INTEGER NOT NULL CHECK (
        typeof(input_fencing_token) = 'integer' AND input_fencing_token >= 1
    ),
    input_owner_id TEXT NOT NULL,
    input_term_acquired_at TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    admission_fencing_token INTEGER NOT NULL CHECK (
        typeof(admission_fencing_token) = 'integer'
        AND admission_fencing_token >= 1
    ),
    admission_owner_id TEXT NOT NULL,
    admission_term_acquired_at TEXT NOT NULL,
    max_node_attempts INTEGER NOT NULL CHECK (max_node_attempts = 1),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL CHECK (
        length(document_hash) = 71 AND substr(document_hash, 1, 7) = 'sha256:'
    ),
    admitted_at TEXT NOT NULL,
    admitted_at_us INTEGER NOT NULL CHECK (
        typeof(admitted_at_us) = 'integer' AND admitted_at_us >= 0
    ),
    UNIQUE (
        task_id, plan_id, approval_id, node_id,
        pending_revision, input_fencing_token,
        input_binding_document_hash
    ),
    UNIQUE (task_id, plan_id, node_id, queued_revision),
    UNIQUE (task_id, plan_id, approval_id, node_id),
    FOREIGN KEY (intent_id) REFERENCES dispatch_intents(intent_id),
    FOREIGN KEY (task_id, plan_id, plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (task_id, plan_id, node_id)
        REFERENCES plan_node_idempotency(task_id, plan_id, node_id),
    FOREIGN KEY (task_id, plan_id, node_idempotency_key)
        REFERENCES plan_node_idempotency(task_id, plan_id, idempotency_key),
    FOREIGN KEY (
        task_id, plan_id, approval_id, node_id,
        pending_revision, input_fencing_token,
        input_binding_document_hash
    ) REFERENCES dag_node_input_binding_facts(
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token, binding_document_hash
    ),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, input_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        ),
    FOREIGN KEY (project_id, principal_id, admission_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

INSERT INTO dag_node_execution_admissions
SELECT * FROM scientific_runtime_v21_dag_admissions;

CREATE INDEX idx_dag_node_execution_admissions_term
    ON dag_node_execution_admissions(
        project_id, principal_id, admission_fencing_token,
        task_id, approval_id, node_id
    );

CREATE TABLE dag_node_execution_transition_facts (
    intent_id TEXT NOT NULL,
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer' AND node_revision >= 2
    ),
    previous_state TEXT NOT NULL CHECK (
        previous_state IN ('Pending', 'Queued', 'Running')
    ),
    state TEXT NOT NULL CHECK (
        state IN ('Queued', 'Running', 'Succeeded', 'Failed', 'Cancelled')
    ),
    event_sequence INTEGER NOT NULL CHECK (
        typeof(event_sequence) = 'integer' AND event_sequence >= 1
    ),
    event_hash TEXT NOT NULL CHECK (
        length(event_hash) = 71
        AND substr(event_hash, 1, 7) = 'sha256:'
    ),
    reason TEXT NOT NULL CHECK (
        (state = 'Queued' AND reason = 'execution_admitted')
        OR (state = 'Running' AND reason = 'dispatch_receipt_adopted')
        OR (state = 'Succeeded' AND reason = 'adapter_succeeded')
        OR (state = 'Failed' AND reason IN (
            'adapter_failed', 'dispatch_not_started',
            'worker_exit_no_retry'
        ))
        OR (state = 'Cancelled' AND reason = 'adapter_cancelled')
    ),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    term_acquired_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (intent_id, node_revision),
    UNIQUE (intent_id, event_sequence),
    UNIQUE (intent_id, event_hash),
    UNIQUE (
        intent_id, node_revision, previous_state, state,
        event_sequence, event_hash
    ),
    FOREIGN KEY (intent_id)
        REFERENCES dag_node_execution_admissions(intent_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

INSERT INTO dag_node_execution_transition_facts
SELECT * FROM scientific_runtime_v21_dag_execution_transitions;

CREATE INDEX idx_dag_node_execution_transition_facts_term
    ON dag_node_execution_transition_facts(
        project_id, principal_id, fencing_token, intent_id, node_revision
    );

CREATE TRIGGER dag_node_execution_transition_requires_active_term
BEFORE INSERT ON dag_node_execution_transition_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND term.owner_id = NEW.owner_id
      AND term.acquired_at = NEW.term_acquired_at
      AND lease.heartbeat_at_us <= NEW.recorded_at_us
      AND lease.expires_at_us > NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node transition requires the active term');
END;

CREATE TRIGGER dag_node_execution_transition_facts_are_append_only
BEFORE UPDATE ON dag_node_execution_transition_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution transitions are append-only');
END;

CREATE TRIGGER dag_node_execution_transition_facts_cannot_be_deleted
BEFORE DELETE ON dag_node_execution_transition_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution transitions are append-only');
END;

CREATE TABLE dag_node_terminal_facts (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71 AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    approval_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    input_binding_node_revision INTEGER NOT NULL CHECK (
        typeof(input_binding_node_revision) = 'integer'
        AND input_binding_node_revision >= 1
    ),
    input_binding_document_hash TEXT NOT NULL CHECK (
        length(input_binding_document_hash) = 71
        AND substr(input_binding_document_hash, 1, 7) = 'sha256:'
    ),
    input_fencing_token INTEGER NOT NULL CHECK (
        typeof(input_fencing_token) = 'integer' AND input_fencing_token >= 1
    ),
    input_owner_id TEXT NOT NULL,
    input_term_acquired_at TEXT NOT NULL,
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer'
        AND node_revision > input_binding_node_revision
    ),
    node_state TEXT NOT NULL CHECK (
        node_state IN ('Succeeded', 'Failed', 'Cancelled')
    ),
    event_sequence INTEGER NOT NULL CHECK (
        typeof(event_sequence) = 'integer' AND event_sequence >= 1
    ),
    event_hash TEXT NOT NULL CHECK (
        length(event_hash) = 71 AND substr(event_hash, 1, 7) = 'sha256:'
    ),
    attempt_id TEXT,
    attempt_number INTEGER,
    worker_observation_sequence INTEGER,
    worker_observation_hash TEXT,
    dispatch_handle_json TEXT,
    dispatch_handle_hash TEXT,
    dispatch_outcome_document_hash TEXT NOT NULL CHECK (
        length(dispatch_outcome_document_hash) = 71
        AND substr(dispatch_outcome_document_hash, 1, 7) = 'sha256:'
    ),
    adapter_status_json TEXT NOT NULL CHECK (
        json_valid(adapter_status_json)
        AND json_type(adapter_status_json, '$') = 'object'
    ),
    adapter_status_hash TEXT NOT NULL CHECK (
        length(adapter_status_hash) = 71
        AND substr(adapter_status_hash, 1, 7) = 'sha256:'
    ),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    completion_fencing_token INTEGER NOT NULL CHECK (
        typeof(completion_fencing_token) = 'integer'
        AND completion_fencing_token >= 1
    ),
    completion_owner_id TEXT NOT NULL,
    completion_term_acquired_at TEXT NOT NULL,
    receipt_document_json TEXT,
    receipt_document_hash TEXT,
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    UNIQUE (task_id, plan_id, node_id, node_revision),
    UNIQUE (task_id, event_sequence),
    CHECK (
        (attempt_id IS NULL AND attempt_number IS NULL
         AND worker_observation_sequence IS NULL
         AND worker_observation_hash IS NULL
         AND dispatch_handle_json IS NULL AND dispatch_handle_hash IS NULL)
        OR
        (attempt_id IS NOT NULL AND attempt_number = 1
         AND typeof(worker_observation_sequence) = 'integer'
         AND worker_observation_sequence >= 1
         AND length(worker_observation_hash) = 71
         AND substr(worker_observation_hash, 1, 7) = 'sha256:'
         AND json_valid(dispatch_handle_json)
         AND json_type(dispatch_handle_json, '$') = 'object'
         AND length(dispatch_handle_hash) = 71
         AND substr(dispatch_handle_hash, 1, 7) = 'sha256:')
    ),
    CHECK (
        (node_state = 'Succeeded' AND attempt_id IS NOT NULL
         AND receipt_document_json IS NOT NULL
         AND length(receipt_document_hash) = 71
         AND substr(receipt_document_hash, 1, 7) = 'sha256:')
        OR
        (node_state IN ('Failed', 'Cancelled') AND receipt_document_json IS NULL
         AND receipt_document_hash IS NULL)
    ),
    FOREIGN KEY (intent_id)
        REFERENCES dag_node_execution_admissions(intent_id),
    FOREIGN KEY (task_id, plan_id, plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (
        task_id, plan_id, approval_id, node_id,
        input_binding_node_revision, input_fencing_token,
        input_binding_document_hash
    ) REFERENCES dag_node_input_binding_facts(
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token, binding_document_hash
    ),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (task_id, event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, event_sequence)
        REFERENCES supervised_run_event_commits(task_id, sequence),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id),
    FOREIGN KEY (attempt_id, worker_observation_sequence)
        REFERENCES worker_attempt_observations(attempt_id, observation_sequence),
    FOREIGN KEY (project_id, principal_id, input_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        ),
    FOREIGN KEY (project_id, principal_id, completion_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

INSERT INTO dag_node_terminal_facts
SELECT * FROM scientific_runtime_v21_dag_terminal_facts;

CREATE INDEX idx_dag_node_terminal_facts_term
    ON dag_node_terminal_facts(
        project_id, principal_id, completion_fencing_token,
        task_id, approval_id, node_id
    );

DROP TABLE scientific_runtime_v21_dag_terminal_facts;
DROP TABLE scientific_runtime_v21_dag_execution_transitions;
DROP TABLE scientific_runtime_v21_dag_admissions;
DROP TABLE scientific_runtime_v21_dispatch_intents;

-- The first admission is the one whole-Task approval-budget consumption and
-- owns sequence-one task_queued.  Successor admissions never consume again.
CREATE TABLE dag_task_execution_runs (
    task_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    first_intent_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    admitted_at TEXT NOT NULL,
    admitted_at_us INTEGER NOT NULL CHECK (
        typeof(admitted_at_us) = 'integer' AND admitted_at_us >= 0
    ),
    FOREIGN KEY (first_intent_id)
        REFERENCES dag_node_execution_admissions(intent_id),
    FOREIGN KEY (task_id, plan_id, plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id)
);

INSERT INTO dag_task_execution_runs(
    task_id, plan_id, plan_hash, approval_id, first_intent_id,
    project_id, principal_id, admitted_at, admitted_at_us
)
SELECT admission.task_id, admission.plan_id, admission.plan_hash,
       admission.approval_id, admission.intent_id,
       admission.project_id, admission.principal_id,
       admission.admitted_at, admission.admitted_at_us
FROM dag_node_execution_admissions AS admission
WHERE NOT EXISTS (
    SELECT 1 FROM dag_node_execution_admissions AS earlier
    WHERE earlier.task_id = admission.task_id
      AND (earlier.admitted_at_us < admission.admitted_at_us
           OR (earlier.admitted_at_us = admission.admitted_at_us
               AND earlier.intent_id < admission.intent_id))
);

CREATE TRIGGER dag_task_execution_runs_are_append_only
BEFORE UPDATE ON dag_task_execution_runs
BEGIN
    SELECT RAISE(ABORT, 'DAG Task execution runs are append-only');
END;

CREATE TRIGGER dag_task_execution_runs_cannot_be_deleted
BEFORE DELETE ON dag_task_execution_runs
BEGIN
    SELECT RAISE(ABORT, 'DAG Task execution runs are append-only');
END;

CREATE TABLE dag_node_scheduler_transition_facts (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71 AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    approval_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    previous_revision INTEGER NOT NULL CHECK (
        typeof(previous_revision) = 'integer' AND previous_revision >= 1
    ),
    previous_state TEXT NOT NULL CHECK (previous_state = 'Pending'),
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer'
        AND node_revision = previous_revision + 1
    ),
    state TEXT NOT NULL CHECK (state = 'Blocked'),
    blocker_document_json TEXT NOT NULL,
    blocker_document_hash TEXT NOT NULL CHECK (
        length(blocker_document_hash) = 71
        AND substr(blocker_document_hash, 1, 7) = 'sha256:'
    ),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    term_acquired_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (task_id, plan_id, node_id, node_revision),
    UNIQUE (task_id, plan_id, approval_id, node_id, previous_revision),
    FOREIGN KEY (
        task_id, plan_id, plan_hash, node_id,
        previous_revision, previous_state
    ) REFERENCES dag_node_state_events(
        task_id, plan_id, plan_hash, node_id, revision, state
    ),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_dag_node_scheduler_transition_facts_term
    ON dag_node_scheduler_transition_facts(
        project_id, principal_id, fencing_token, task_id, node_id
    );

CREATE TRIGGER dag_node_scheduler_transition_requires_exact_case
BEFORE INSERT ON dag_node_scheduler_transition_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dag_node_state_events AS pending
      ON pending.task_id = task.task_id AND pending.plan_id = plan.plan_id
     AND pending.plan_hash = plan.plan_hash AND pending.node_id = NEW.node_id
     AND pending.revision = NEW.previous_revision
     AND pending.state = 'Pending'
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('AwaitingApproval', 'Queued', 'Running')
      AND plan.plan_id = NEW.plan_id AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND NEW.previous_state = 'Pending' AND NEW.state = 'Blocked'
      AND NEW.node_revision = NEW.previous_revision + 1
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = pending.task_id
            AND later.plan_id = pending.plan_id
            AND later.node_id = pending.node_id
            AND later.revision > pending.revision
      )
      AND json_valid(NEW.blocker_document_json)
      AND json_type(NEW.blocker_document_json, '$') = 'object'
      AND json_extract(NEW.blocker_document_json, '$.schema_version') = '1.0.0'
      AND json_extract(NEW.blocker_document_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.blocker_document_json, '$.plan_id') = NEW.plan_id
      AND json_extract(NEW.blocker_document_json, '$.plan_hash') = NEW.plan_hash
      AND json_extract(NEW.blocker_document_json, '$.approval_id') = NEW.approval_id
      AND json_extract(NEW.blocker_document_json, '$.node_id') = NEW.node_id
      AND json_type(
          NEW.blocker_document_json, '$.blocked_by_node_ids'
      ) = 'array'
      AND json_array_length(
          NEW.blocker_document_json, '$.blocked_by_node_ids'
      ) >= 1
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(
              NEW.blocker_document_json, '$.blocked_by_node_ids'
          ) AS blocker
          WHERE blocker.type != 'text'
             OR NOT EXISTS (
                 SELECT 1 FROM dag_node_state_events AS blocked_state
                 WHERE blocked_state.task_id = NEW.task_id
                   AND blocked_state.plan_id = NEW.plan_id
                   AND blocked_state.node_id = blocker.value
                   AND blocked_state.state IN ('Failed', 'Cancelled', 'Blocked')
                   AND NOT EXISTS (
                       SELECT 1 FROM dag_node_state_events AS newer
                       WHERE newer.task_id = blocked_state.task_id
                         AND newer.plan_id = blocked_state.plan_id
                         AND newer.node_id = blocked_state.node_id
                         AND newer.revision > blocked_state.revision
                   )
             )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG Blocked transition requires exact durable blockers');
END;

CREATE TRIGGER dag_node_scheduler_transition_requires_active_term
BEFORE INSERT ON dag_node_scheduler_transition_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND term.owner_id = NEW.owner_id
      AND term.acquired_at = NEW.term_acquired_at
      AND lease.heartbeat_at_us <= NEW.recorded_at_us
      AND lease.expires_at_us > NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG scheduler transition requires the active term');
END;

CREATE TRIGGER dag_node_scheduler_transition_facts_are_append_only
BEFORE UPDATE ON dag_node_scheduler_transition_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG scheduler transitions are append-only');
END;

CREATE TRIGGER dag_node_scheduler_transition_facts_cannot_be_deleted
BEFORE DELETE ON dag_node_scheduler_transition_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG scheduler transitions are append-only');
END;

-- Claims and bindings remain exact durable audit facts after the Task enters
-- runtime.  Their active-term and latest-Pending triggers from v18/v19 remain
-- in force; replace only the pre-runtime current-case gates.
DROP TRIGGER dag_node_execution_rejects_cancel_request;

DROP TRIGGER dag_node_claim_requires_current_approved_plan;

CREATE TRIGGER dag_node_claim_requires_current_approved_plan
BEFORE INSERT ON dag_node_claim_candidates
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('AwaitingApproval', 'Running')
      AND plan.plan_id = NEW.plan_id AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND EXISTS (
          SELECT 1 FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id') = NEW.node_id
      )
      AND json_valid(NEW.readiness_document_json)
      AND json_type(NEW.readiness_document_json, '$') = 'object'
      AND json_extract(NEW.readiness_document_json, '$.schema_version') = '1.0.0'
      AND json_extract(NEW.readiness_document_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.readiness_document_json, '$.plan_id') = NEW.plan_id
      AND json_extract(NEW.readiness_document_json, '$.plan_hash') = NEW.plan_hash
      AND json_extract(NEW.readiness_document_json, '$.approval_id') = NEW.approval_id
      AND json_extract(
          NEW.readiness_document_json, '$.selected_node_id'
      ) = NEW.node_id
      AND json_type(
          NEW.readiness_document_json, '$.node_states'
      ) = 'array'
      AND json_array_length(
          NEW.readiness_document_json, '$.node_states'
      ) = json_array_length(plan.document_json, '$.nodes')
      AND EXISTS (
          SELECT 1 FROM json_each(
              NEW.readiness_document_json, '$.runnable_node_ids'
          ) AS runnable WHERE runnable.value = NEW.node_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM json_each(
              NEW.readiness_document_json, '$.node_states'
          ) AS claimed
          WHERE NOT EXISTS (
              SELECT 1 FROM dag_node_state_events AS durable
              WHERE durable.task_id = NEW.task_id
                AND durable.plan_id = NEW.plan_id
                AND durable.plan_hash = NEW.plan_hash
                AND durable.node_id = json_extract(claimed.value, '$.node_id')
                AND durable.revision = json_extract(claimed.value, '$.revision')
                AND durable.state = json_extract(claimed.value, '$.state')
                AND NOT EXISTS (
                    SELECT 1 FROM dag_node_state_events AS later
                    WHERE later.task_id = durable.task_id
                      AND later.plan_id = durable.plan_id
                      AND later.node_id = durable.node_id
                      AND later.revision > durable.revision
                )
          )
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node claim requires the current approved plan');
END;

DROP TRIGGER dag_node_input_binding_requires_current_claim;

CREATE TRIGGER dag_node_input_binding_requires_current_claim
BEFORE INSERT ON dag_node_input_binding_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dag_node_claim_candidates AS claim
      ON claim.task_id = task.task_id AND claim.plan_id = plan.plan_id
     AND claim.approval_id = approval.approval_id
     AND claim.node_id = NEW.target_node_id
     AND claim.node_revision = NEW.target_node_revision
     AND claim.fencing_token = NEW.fencing_token
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('AwaitingApproval', 'Running')
      AND plan.plan_id = NEW.plan_id AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND claim.plan_hash = NEW.plan_hash
      AND claim.node_state = NEW.target_node_state
      AND claim.project_id = NEW.project_id
      AND claim.principal_id = NEW.principal_id
      AND claim.owner_id = NEW.owner_id
      AND claim.term_acquired_at = NEW.term_acquired_at
      AND claim.readiness_document_hash = NEW.claim_readiness_document_hash
      AND json_valid(NEW.binding_document_json)
      AND json_type(NEW.binding_document_json, '$') = 'object'
      AND json_extract(NEW.binding_document_json, '$.schema_version') = '1.0.0'
      AND json_extract(NEW.binding_document_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.binding_document_json, '$.plan.plan_id') = NEW.plan_id
      AND json_extract(NEW.binding_document_json, '$.plan.plan_hash') = NEW.plan_hash
      AND json_extract(NEW.binding_document_json, '$.approval_id') = NEW.approval_id
      AND json_extract(
          NEW.binding_document_json, '$.target.node_id'
      ) = NEW.target_node_id
      AND json_extract(
          NEW.binding_document_json, '$.target.revision'
      ) = NEW.target_node_revision
      AND json_extract(
          NEW.binding_document_json, '$.target.state'
      ) = NEW.target_node_state
      AND json_extract(
          NEW.binding_document_json, '$.scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.binding_document_json, '$.scope.principal_id'
      ) = NEW.principal_id
      AND json_extract(
          NEW.binding_document_json, '$.supervisor_term.fencing_token'
      ) = NEW.fencing_token
      AND json_extract(
          NEW.binding_document_json, '$.supervisor_term.owner_id'
      ) = NEW.owner_id
      AND json_extract(
          NEW.binding_document_json, '$.supervisor_term.acquired_at'
      ) = NEW.term_acquired_at
      AND json_extract(
          NEW.binding_document_json, '$.claim_readiness_document_hash'
      ) = NEW.claim_readiness_document_hash
      AND json_type(NEW.binding_document_json, '$.inputs') = 'array'
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG input binding requires the current approved claim');
END;

CREATE TRIGGER dag_node_execution_admission_requires_exact_current_case
BEFORE INSERT ON dag_node_execution_admissions
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dispatch_intents AS intent ON intent.intent_id = NEW.intent_id
    JOIN dag_node_input_binding_facts AS binding
      ON binding.task_id = task.task_id AND binding.plan_id = plan.plan_id
     AND binding.approval_id = approval.approval_id
     AND binding.target_node_id = NEW.node_id
     AND binding.target_node_revision = NEW.pending_revision
     AND binding.fencing_token = NEW.input_fencing_token
     AND binding.binding_document_hash = NEW.input_binding_document_hash
    JOIN dag_node_state_events AS pending
      ON pending.task_id = task.task_id AND pending.plan_id = plan.plan_id
     AND pending.plan_hash = plan.plan_hash AND pending.node_id = NEW.node_id
     AND pending.revision = NEW.pending_revision AND pending.state = 'Pending'
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('AwaitingApproval', 'Running')
      AND plan.plan_id = NEW.plan_id AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND intent.task_id = NEW.task_id AND intent.plan_id = NEW.plan_id
      AND intent.plan_hash = NEW.plan_hash
      AND intent.approval_id = NEW.approval_id AND intent.node_id = NEW.node_id
      AND intent.node_idempotency_key = NEW.node_idempotency_key
      AND binding.plan_hash = NEW.plan_hash
      AND binding.target_node_state = 'Pending'
      AND binding.project_id = NEW.project_id
      AND binding.principal_id = NEW.principal_id
      AND binding.owner_id = NEW.input_owner_id
      AND binding.term_acquired_at = NEW.input_term_acquired_at
      AND NEW.input_fencing_token = NEW.admission_fencing_token
      AND NEW.input_owner_id = NEW.admission_owner_id
      AND NEW.input_term_acquired_at = NEW.admission_term_acquired_at
      AND NEW.queued_revision = NEW.pending_revision + 1
      AND NEW.max_node_attempts = 1
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = pending.task_id AND later.plan_id = pending.plan_id
            AND later.node_id = pending.node_id
            AND later.revision > pending.revision
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM dag_node_execution_admissions AS active_admission
          JOIN dag_node_state_events AS active_state
            ON active_state.task_id = active_admission.task_id
           AND active_state.plan_id = active_admission.plan_id
           AND active_state.node_id = active_admission.node_id
          WHERE active_admission.task_id = NEW.task_id
            AND active_state.state IN ('Queued', 'Running', 'Waiting', 'Retrying')
            AND NOT EXISTS (
                SELECT 1 FROM dag_node_state_events AS newer
                WHERE newer.task_id = active_state.task_id
                  AND newer.plan_id = active_state.plan_id
                  AND newer.node_id = active_state.node_id
                  AND newer.revision > active_state.revision
            )
      )
)
BEGIN
    SELECT RAISE(
        ABORT, 'DAG node admission requires the exact current ready case'
    );
END;

CREATE TRIGGER dag_node_execution_admission_requires_active_term
BEFORE INSERT ON dag_node_execution_admissions
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.admission_fencing_token
      AND term.owner_id = NEW.admission_owner_id
      AND term.acquired_at = NEW.admission_term_acquired_at
      AND lease.heartbeat_at_us <= NEW.admitted_at_us
      AND lease.expires_at_us > NEW.admitted_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node admission requires the active term');
END;

CREATE TRIGGER dag_node_execution_admission_requires_exact_document
BEFORE INSERT ON dag_node_execution_admissions
WHEN NOT (
    json_valid(NEW.document_json)
    AND json_type(NEW.document_json, '$') = 'object'
    AND json_extract(NEW.document_json, '$.schema_version') = '1.0.0'
    AND json_extract(NEW.document_json, '$.intent_id') = NEW.intent_id
    AND json_extract(NEW.document_json, '$.task_id') = NEW.task_id
    AND json_extract(NEW.document_json, '$.plan.plan_id') = NEW.plan_id
    AND json_extract(NEW.document_json, '$.plan.plan_hash') = NEW.plan_hash
    AND json_extract(NEW.document_json, '$.approval_id') = NEW.approval_id
    AND json_extract(NEW.document_json, '$.node.node_id') = NEW.node_id
    AND json_extract(
        NEW.document_json, '$.node.pending_revision'
    ) = NEW.pending_revision
    AND json_extract(
        NEW.document_json, '$.node.queued_revision'
    ) = NEW.queued_revision
    AND json_extract(
        NEW.document_json, '$.node.idempotency_key'
    ) = NEW.node_idempotency_key
    AND json_extract(
        NEW.document_json, '$.input_binding.document_hash'
    ) = NEW.input_binding_document_hash
    AND json_extract(
        NEW.document_json, '$.admission_supervisor_term.fencing_token'
    ) = NEW.admission_fencing_token
    AND json_extract(
        NEW.document_json, '$.admission_supervisor_term.owner_id'
    ) = NEW.admission_owner_id
    AND json_extract(
        NEW.document_json, '$.admission_supervisor_term.acquired_at'
    ) = NEW.admission_term_acquired_at
    AND json_extract(NEW.document_json, '$.admitted_at') = NEW.admitted_at
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node admission document is not exact');
END;

CREATE TRIGGER dag_node_execution_admissions_are_append_only
BEFORE UPDATE ON dag_node_execution_admissions
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution admissions are append-only');
END;

CREATE TRIGGER dag_node_execution_admissions_cannot_be_deleted
BEFORE DELETE ON dag_node_execution_admissions
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution admissions are append-only');
END;

CREATE TRIGGER dag_node_execution_admission_rejects_owned_control
BEFORE INSERT ON dag_node_execution_admissions
WHEN EXISTS (
    SELECT 1 FROM task_cancel_requests AS cancel
    WHERE cancel.task_id = NEW.task_id
)
OR EXISTS (
    SELECT 1 FROM worker_attempt_timeout_windows AS timeout
    WHERE timeout.task_id = NEW.task_id
)
OR EXISTS (
    SELECT 1 FROM worker_checkpoint_waits AS checkpoint
    WHERE checkpoint.task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution cannot adopt a control-owned Task');
END;

CREATE TRIGGER dag_node_terminal_fact_requires_exact_current_case
BEFORE INSERT ON dag_node_terminal_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    JOIN dispatch_intents AS intent ON intent.intent_id = admission.intent_id
    JOIN tasks AS task ON task.task_id = admission.task_id
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dag_node_input_binding_facts AS binding
      ON binding.task_id = admission.task_id
     AND binding.plan_id = admission.plan_id
     AND binding.approval_id = admission.approval_id
     AND binding.target_node_id = admission.node_id
     AND binding.target_node_revision = admission.pending_revision
     AND binding.fencing_token = admission.input_fencing_token
     AND binding.binding_document_hash = admission.input_binding_document_hash
    JOIN run_events AS event
      ON event.task_id = admission.task_id AND event.sequence = NEW.event_sequence
    JOIN supervised_run_event_commits AS event_commit
      ON event_commit.task_id = event.task_id
     AND event_commit.sequence = event.sequence
    JOIN dag_node_state_events AS prior
      ON prior.task_id = admission.task_id
     AND prior.plan_id = admission.plan_id
     AND prior.plan_hash = admission.plan_hash
     AND prior.node_id = admission.node_id
    WHERE admission.intent_id = NEW.intent_id
      AND admission.task_id = NEW.task_id
      AND admission.plan_id = NEW.plan_id
      AND admission.plan_hash = NEW.plan_hash
      AND admission.approval_id = NEW.approval_id
      AND admission.node_id = NEW.node_id
      AND admission.pending_revision = NEW.input_binding_node_revision
      AND admission.input_binding_document_hash = NEW.input_binding_document_hash
      AND admission.input_fencing_token = NEW.input_fencing_token
      AND admission.input_owner_id = NEW.input_owner_id
      AND admission.input_term_acquired_at = NEW.input_term_acquired_at
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('Queued', 'Running')
      AND plan.plan_id = NEW.plan_id AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND intent.task_id = NEW.task_id AND intent.node_id = NEW.node_id
      AND event.node_id = NEW.node_id
      AND event.document_hash = NEW.event_hash
      AND event.event_type = CASE NEW.node_state
          WHEN 'Succeeded' THEN 'node_succeeded'
          WHEN 'Failed' THEN 'node_failed'
          ELSE 'node_cancelled' END
      AND event.task_status IN ('Running', 'Succeeded', 'Failed')
      AND (
          NEW.node_state != 'Cancelled'
          OR (
              json_extract(NEW.adapter_status_json, '$.status') = 'Cancelled'
              AND json_extract(NEW.adapter_status_json, '$.terminal') = 1
              AND NOT EXISTS (
                  SELECT 1 FROM task_cancel_requests AS cancellation
                  WHERE cancellation.task_id = NEW.task_id
              )
          )
      )
      AND event.sequence = (
          SELECT MAX(latest.sequence) FROM run_events AS latest
          WHERE latest.task_id = NEW.task_id
      )
      AND event_commit.project_id = NEW.project_id
      AND event_commit.principal_id = NEW.principal_id
      AND event_commit.fencing_token = NEW.completion_fencing_token
      AND event_commit.recorded_at = NEW.recorded_at
      AND event_commit.recorded_at_us = NEW.recorded_at_us
      AND prior.revision = NEW.node_revision - 1
      AND prior.state IN ('Queued', 'Running')
      AND NEW.node_revision = CASE prior.state
          WHEN 'Queued' THEN admission.queued_revision + 1
          ELSE admission.queued_revision + 2 END
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = prior.task_id AND later.plan_id = prior.plan_id
            AND later.node_id = prior.node_id AND later.revision > prior.revision
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG terminal fact requires the exact current admitted node');
END;

CREATE TRIGGER dag_node_terminal_fact_requires_exact_p2_evidence
BEFORE INSERT ON dag_node_terminal_facts
WHEN NOT (
    (NEW.attempt_id IS NOT NULL
     AND NEW.attempt_number = 1
     AND EXISTS (
         SELECT 1
         FROM effective_dispatched_intents AS effective
         JOIN worker_launch_attempts AS attempt
           ON attempt.intent_id = effective.intent_id
          AND attempt.attempt_id = NEW.attempt_id
         JOIN worker_attempt_observations AS observation
           ON observation.attempt_id = attempt.attempt_id
          AND observation.observation_sequence = NEW.worker_observation_sequence
         JOIN supervised_dispatch_adoptions AS adoption
           ON adoption.intent_id = effective.intent_id
          AND adoption.attempt_id = attempt.attempt_id
         WHERE effective.intent_id = NEW.intent_id
           AND effective.outcome_document_hash = NEW.dispatch_outcome_document_hash
           AND attempt.task_id = NEW.task_id
           AND attempt.project_id = NEW.project_id
           AND attempt.principal_id = NEW.principal_id
           AND attempt.attempt_number = 1
           AND observation.document_hash = NEW.worker_observation_hash
           AND NEW.dispatch_handle_json = json_extract(
               effective.outcome_document_json, '$.handle'
           )
           AND observation.observation_sequence = (
               SELECT MAX(latest.observation_sequence)
               FROM worker_attempt_observations AS latest
               WHERE latest.attempt_id = attempt.attempt_id
           )
     ))
    OR
    (NEW.node_state = 'Failed' AND NEW.attempt_id IS NULL
     AND EXISTS (
         SELECT 1
         FROM dispatch_outcomes AS outcome
         JOIN dispatch_reconciliation_observations AS observation
           ON observation.intent_id = outcome.intent_id
         WHERE outcome.intent_id = NEW.intent_id
           AND outcome.outcome = 'reconciliation_required'
           AND outcome.document_hash = NEW.dispatch_outcome_document_hash
           AND observation.task_id = NEW.task_id
           AND observation.project_id = NEW.project_id
           AND observation.principal_id = NEW.principal_id
           AND observation.classification = 'exact_negative'
           AND observation.failure_code = 'DISPATCH_NOT_STARTED'
           AND observation.fencing_token = NEW.completion_fencing_token
     ))
)
BEGIN
    SELECT RAISE(ABORT, 'DAG terminal fact requires exact P2 evidence');
END;

CREATE TRIGGER dag_node_terminal_fact_requires_active_completion_term
BEFORE INSERT ON dag_node_terminal_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.completion_fencing_token
      AND term.owner_id = NEW.completion_owner_id
      AND term.acquired_at = NEW.completion_term_acquired_at
      AND lease.heartbeat_at_us <= NEW.recorded_at_us
      AND lease.expires_at_us > NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG terminal fact requires the active completion term');
END;

CREATE TRIGGER dag_node_terminal_success_requires_complete_receipt
BEFORE INSERT ON dag_node_terminal_facts
WHEN NEW.node_state = 'Succeeded' AND NOT (
    json_valid(NEW.receipt_document_json)
    AND json_type(NEW.receipt_document_json, '$') = 'object'
    AND json_extract(NEW.receipt_document_json, '$.schema_version') = '2.0.0'
    AND json_extract(NEW.receipt_document_json, '$.task_id') = NEW.task_id
    AND json_extract(
        NEW.receipt_document_json, '$.plan.plan_id'
    ) = NEW.plan_id
    AND json_extract(
        NEW.receipt_document_json, '$.plan.plan_hash'
    ) = NEW.plan_hash
    AND json_extract(
        NEW.receipt_document_json, '$.approval_id'
    ) = NEW.approval_id
    AND json_extract(
        NEW.receipt_document_json, '$.node.node_id'
    ) = NEW.node_id
    AND json_extract(
        NEW.receipt_document_json, '$.node.succeeded_revision'
    ) = NEW.node_revision
    AND json_extract(
        NEW.receipt_document_json, '$.input_binding_document_hash'
    ) = NEW.input_binding_document_hash
    AND json_extract(
        NEW.receipt_document_json, '$.dispatch.intent_id'
    ) = NEW.intent_id
    AND json_extract(
        NEW.receipt_document_json, '$.dispatch.handle_hash'
    ) = NEW.dispatch_handle_hash
    AND json_extract(
        NEW.receipt_document_json, '$.dispatch.attempt_id'
    ) = NEW.attempt_id
    AND json_extract(
        NEW.receipt_document_json, '$.dispatch.worker_observation_hash'
    ) = NEW.worker_observation_hash
    AND json_type(NEW.receipt_document_json, '$.outputs') = 'array'
    AND json_array_length(NEW.receipt_document_json, '$.outputs') >= 1
)
BEGIN
    SELECT RAISE(ABORT, 'DAG Succeeded terminal requires one complete receipt');
END;

CREATE TRIGGER dag_node_terminal_facts_are_append_only
BEFORE UPDATE ON dag_node_terminal_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG node terminal facts are append-only');
END;

CREATE TRIGGER dag_node_terminal_facts_cannot_be_deleted
BEFORE DELETE ON dag_node_terminal_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG node terminal facts are append-only');
END;

-- v20's transition gate assumed the first admitted node forever.  Preserve
-- the P2 evidence joins while allowing a successor under a Running aggregate
-- Task and allowing the terminal RunEvent to carry the final aggregate state.
DROP TRIGGER IF EXISTS dag_node_execution_transition_requires_exact_current_case;

CREATE TRIGGER dag_node_execution_transition_requires_exact_current_case
BEFORE INSERT ON dag_node_execution_transition_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    JOIN dispatch_intents AS intent ON intent.intent_id = admission.intent_id
    JOIN tasks AS task ON task.task_id = admission.task_id
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN run_events AS event
      ON event.task_id = admission.task_id AND event.sequence = NEW.event_sequence
    JOIN supervised_run_event_commits AS event_commit
      ON event_commit.task_id = event.task_id
     AND event_commit.sequence = event.sequence
    JOIN dag_node_state_events AS prior
      ON prior.task_id = admission.task_id
     AND prior.plan_id = admission.plan_id
     AND prior.plan_hash = admission.plan_hash
     AND prior.node_id = admission.node_id
     AND prior.revision = NEW.node_revision - 1
     AND prior.state = NEW.previous_state
    WHERE admission.intent_id = NEW.intent_id
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND admission.plan_id = plan.plan_id
      AND admission.plan_hash = plan.plan_hash
      AND admission.approval_id = approval.approval_id
      AND approval.decision = 'approved'
      AND intent.task_id = admission.task_id
      AND intent.node_id = admission.node_id
      AND event.document_hash = NEW.event_hash
      AND event.sequence = (
          SELECT MAX(latest.sequence) FROM run_events AS latest
          WHERE latest.task_id = admission.task_id
      )
      AND event_commit.project_id = NEW.project_id
      AND event_commit.principal_id = NEW.principal_id
      AND event_commit.fencing_token = NEW.fencing_token
      AND event_commit.recorded_at = NEW.recorded_at
      AND event_commit.recorded_at_us = NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = prior.task_id AND later.plan_id = prior.plan_id
            AND later.node_id = prior.node_id AND later.revision > prior.revision
      )
      AND (
          (NEW.previous_state = 'Pending' AND NEW.state = 'Queued'
           AND NEW.node_revision = admission.queued_revision
           AND NEW.reason = 'execution_admitted'
           AND task.status = 'AwaitingApproval'
           AND event.event_type = 'task_queued'
           AND event.task_status = 'Queued')
          OR
          (NEW.previous_state = 'Queued' AND NEW.state = 'Running'
           AND NEW.node_revision = admission.queued_revision + 1
           AND NEW.reason = 'dispatch_receipt_adopted'
           AND task.status IN ('Queued', 'Running')
           AND event.event_type = 'node_started'
           AND event.task_status = 'Running'
           AND EXISTS (
               SELECT 1 FROM effective_dispatched_intents AS effective
               JOIN worker_launch_attempts AS attempt
                 ON attempt.intent_id = effective.intent_id
               JOIN supervised_dispatch_adoptions AS adoption
                 ON adoption.intent_id = attempt.intent_id
                AND adoption.attempt_id = attempt.attempt_id
               WHERE effective.intent_id = admission.intent_id
                 AND attempt.attempt_number = 1
           ))
          OR
          (NEW.previous_state = 'Running'
           AND NEW.state IN ('Succeeded', 'Failed', 'Cancelled')
           AND NEW.node_revision = admission.queued_revision + 2
           AND task.status = 'Running'
           AND event.event_type = CASE NEW.state
               WHEN 'Succeeded' THEN 'node_succeeded'
               WHEN 'Failed' THEN 'node_failed'
               ELSE 'node_cancelled' END
           AND event.task_status IN ('Running', 'Succeeded', 'Failed')
           AND EXISTS (
               SELECT 1 FROM dag_node_terminal_facts AS terminal
               WHERE terminal.intent_id = admission.intent_id
                 AND terminal.node_revision = NEW.node_revision
                 AND terminal.node_state = NEW.state
                 AND terminal.event_sequence = NEW.event_sequence
                 AND terminal.event_hash = NEW.event_hash
                 AND terminal.completion_fencing_token = NEW.fencing_token
           ))
          OR
          (NEW.previous_state = 'Queued' AND NEW.state = 'Failed'
           AND NEW.node_revision = admission.queued_revision + 1
           AND NEW.reason = 'dispatch_not_started'
           AND task.status IN ('Queued', 'Running')
           AND event.event_type = 'node_failed'
           AND event.task_status IN ('Running', 'Failed')
           AND EXISTS (
               SELECT 1 FROM dag_node_terminal_facts AS terminal
               WHERE terminal.intent_id = admission.intent_id
                 AND terminal.node_revision = NEW.node_revision
                 AND terminal.node_state = 'Failed'
                 AND terminal.event_sequence = NEW.event_sequence
                 AND terminal.event_hash = NEW.event_hash
                 AND terminal.completion_fencing_token = NEW.fencing_token
           ))
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node transition requires the exact current cause');
END;

DROP TRIGGER dag_node_transition_state_requires_exact_active_fact;

CREATE TRIGGER dag_node_transition_state_requires_exact_active_fact
BEFORE INSERT ON dag_node_state_events
WHEN NEW.revision > 1 AND NOT (
    (NEW.previous_state = 'Pending' AND NEW.state = 'Queued'
     AND EXISTS (
         SELECT 1
         FROM dag_node_execution_admissions AS admission
         JOIN runtime_supervisor_leases AS lease
           ON lease.project_id = admission.project_id
          AND lease.principal_id = admission.principal_id
          AND lease.fencing_token = admission.admission_fencing_token
         JOIN runtime_supervisor_terms AS term
           ON term.project_id = lease.project_id
          AND term.principal_id = lease.principal_id
          AND term.fencing_token = lease.fencing_token
         WHERE admission.task_id = NEW.task_id
           AND admission.plan_id = NEW.plan_id
           AND admission.plan_hash = NEW.plan_hash
           AND admission.node_id = NEW.node_id
           AND admission.pending_revision = NEW.revision - 1
           AND admission.queued_revision = NEW.revision
           AND admission.admitted_at = NEW.recorded_at
           AND term.owner_id = admission.admission_owner_id
           AND term.acquired_at = admission.admission_term_acquired_at
           AND lease.heartbeat_at_us <= admission.admitted_at_us
           AND lease.expires_at_us > admission.admitted_at_us
     ))
    OR
    (NEW.previous_state = 'Pending' AND NEW.state = 'Blocked'
     AND EXISTS (
         SELECT 1 FROM dag_node_scheduler_transition_facts AS scheduler
         WHERE scheduler.task_id = NEW.task_id
           AND scheduler.plan_id = NEW.plan_id
           AND scheduler.plan_hash = NEW.plan_hash
           AND scheduler.node_id = NEW.node_id
           AND scheduler.previous_revision = NEW.revision - 1
           AND scheduler.node_revision = NEW.revision
           AND scheduler.state = NEW.state
           AND scheduler.recorded_at = NEW.recorded_at
           AND scheduler.recorded_at_us = NEW.recorded_at_us
     ))
    OR
    (EXISTS (
         SELECT 1
         FROM dag_node_execution_admissions AS admission
         JOIN dag_node_execution_transition_facts AS transition
           ON transition.intent_id = admission.intent_id
          AND transition.node_revision = NEW.revision
         WHERE admission.task_id = NEW.task_id
           AND admission.plan_id = NEW.plan_id
           AND admission.plan_hash = NEW.plan_hash
           AND admission.node_id = NEW.node_id
           AND transition.previous_state = NEW.previous_state
           AND transition.state = NEW.state
           AND transition.recorded_at = NEW.recorded_at
           AND transition.recorded_at_us = NEW.recorded_at_us
           AND (NEW.state NOT IN ('Succeeded', 'Failed', 'Cancelled') OR EXISTS (
               SELECT 1 FROM dag_node_terminal_facts AS terminal
               WHERE terminal.intent_id = admission.intent_id
                 AND terminal.node_revision = NEW.revision
                 AND terminal.node_state = NEW.state
                 AND terminal.event_sequence = transition.event_sequence
                 AND terminal.event_hash = transition.event_hash
           ))
     ))
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node state transition requires an exact active fact');
END;

DROP TRIGGER supervised_dispatch_attempt_requires_matching_intent;

CREATE TRIGGER supervised_dispatch_attempt_requires_matching_intent
BEFORE INSERT ON supervised_dispatch_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE intent.intent_id = NEW.intent_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND (
          task.status = 'Queued'
          OR
          (task.status = 'Running' AND EXISTS (
              SELECT 1
              FROM dag_node_execution_admissions AS admission
              JOIN dag_node_state_events AS state
                ON state.task_id = admission.task_id
               AND state.plan_id = admission.plan_id
               AND state.node_id = admission.node_id
              WHERE admission.intent_id = intent.intent_id
                AND state.state = 'Queued'
                AND NOT EXISTS (
                    SELECT 1 FROM dag_node_state_events AS later
                    WHERE later.task_id = state.task_id
                      AND later.plan_id = state.plan_id
                      AND later.node_id = state.node_id
                      AND later.revision > state.revision
                )
          ))
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_outcomes AS outcome
          WHERE outcome.intent_id = intent.intent_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'supervised dispatch must match an active queued intent');
END;

-- A task-wide cancel can lose its exact Worker race after the admitted DAG
-- node has already committed through the normal terminal-fact path.  Preserve
-- the original P2 proof shapes, and admit the DAG exception only by joining
-- the same immutable intent/attempt/node fact and its exact aggregate event.
DROP TRIGGER task_cancel_outcome_requires_terminal_event;

CREATE TRIGGER task_cancel_outcome_requires_terminal_event
BEFORE INSERT ON task_cancel_outcomes
WHEN NOT EXISTS (
    SELECT 1
    FROM task_cancel_requests AS request
    JOIN tasks AS task ON task.task_id = request.task_id
    JOIN run_events AS event
      ON event.task_id = task.task_id
     AND event.sequence = NEW.terminal_event_sequence
    WHERE request.request_id = NEW.request_id
      AND request.task_id = NEW.task_id
      AND request.project_id = NEW.project_id
      AND request.principal_id = NEW.principal_id
      AND request.intent_id = NEW.intent_id
      AND request.attempt_id = NEW.attempt_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = NEW.terminal_status
      AND json_valid(NEW.adapter_proof_json)
      AND json_type(NEW.adapter_proof_json, '$') = 'object'
      AND json_extract(NEW.adapter_proof_json, '$.schema_version') = '1.0.0'
      AND json_extract(NEW.adapter_proof_json, '$.request_id') = NEW.request_id
      AND json_extract(NEW.adapter_proof_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.adapter_proof_json, '$.attempt_id') = NEW.attempt_id
      AND json_extract(NEW.adapter_proof_json, '$.reason') = request.reason
      AND json_extract(NEW.adapter_proof_json, '$.local_run_state') = 'retained'
      AND json_type(NEW.adapter_proof_json, '$.replayed') IN ('true', 'false')
      AND length(json_extract(
          NEW.adapter_proof_json, '$.receipt_record_hash'
      )) = 71
      AND substr(json_extract(
          NEW.adapter_proof_json, '$.receipt_record_hash'
      ), 1, 7) = 'sha256:'
      AND length(json_extract(NEW.adapter_proof_json, '$.proof_hash')) = 71
      AND substr(json_extract(
          NEW.adapter_proof_json, '$.proof_hash'
      ), 1, 7) = 'sha256:'
      AND event.sequence = (
          SELECT MAX(latest.sequence) FROM run_events AS latest
          WHERE latest.task_id = task.task_id
      )
      AND event.task_status = NEW.terminal_status
      AND (
          (NEW.result = 'cancel_confirmed'
           AND NEW.terminal_status = 'Cancelled'
           AND event.event_type = 'task_cancelled'
           AND json_extract(NEW.adapter_proof_json, '$.state') = 'cancelled'
           AND json_extract(
               NEW.adapter_proof_json, '$.code'
           ) = 'CANCEL_COMPLETED'
           AND json_extract(
               NEW.adapter_proof_json, '$.terminal_status'
           ) = 'Cancelled'
           AND json_type(
               NEW.adapter_proof_json, '$.capability_record_hash'
           ) = 'text'
           AND json_type(
               NEW.adapter_proof_json, '$.request_record_hash'
           ) = 'text'
           AND json_type(
               NEW.adapter_proof_json, '$.acknowledgement_record_hash'
           ) = 'text')
          OR
          (NEW.result = 'terminal_preempted'
           AND NEW.terminal_status IN ('Succeeded', 'Failed')
           AND json_extract(
               NEW.adapter_proof_json, '$.terminal_status'
           ) = NEW.terminal_status
           AND json_extract(
               NEW.adapter_proof_json, '$.state'
           ) = 'terminal_won'
           AND json_extract(
               NEW.adapter_proof_json, '$.code'
           ) = 'CANCEL_TERMINAL_WON'
           AND event.event_type = CASE NEW.terminal_status
               WHEN 'Succeeded' THEN 'node_succeeded' ELSE 'node_failed' END
           AND NOT EXISTS (
               SELECT 1 FROM dag_node_execution_admissions AS dag
               WHERE dag.intent_id = NEW.intent_id
           ))
          OR
          (NEW.result = 'terminal_preempted'
           AND NEW.terminal_status IN ('Succeeded', 'Failed')
           AND json_extract(
               NEW.adapter_proof_json, '$.state'
           ) = 'terminal_won'
           AND json_extract(
               NEW.adapter_proof_json, '$.code'
           ) = 'CANCEL_TERMINAL_WON'
           AND EXISTS (
               SELECT 1
               FROM dag_node_execution_admissions AS admission
               JOIN dag_node_terminal_facts AS terminal
                 ON terminal.intent_id = admission.intent_id
               JOIN dag_node_execution_transition_facts AS transition
                 ON transition.intent_id = terminal.intent_id
                AND transition.node_revision = terminal.node_revision
               JOIN dag_node_state_events AS state
                 ON state.task_id = terminal.task_id
                AND state.plan_id = terminal.plan_id
                AND state.node_id = terminal.node_id
                AND state.revision = terminal.node_revision
               WHERE admission.intent_id = NEW.intent_id
                 AND admission.task_id = NEW.task_id
                 AND terminal.task_id = NEW.task_id
                 AND terminal.attempt_id = NEW.attempt_id
                 AND terminal.attempt_number = 1
                 AND terminal.node_state = json_extract(
                     NEW.adapter_proof_json, '$.terminal_status'
                 )
                 AND terminal.node_state IN ('Succeeded', 'Failed')
                 AND terminal.event_sequence = event.sequence
                 AND terminal.event_hash = event.document_hash
                 AND transition.state = terminal.node_state
                 AND transition.event_sequence = terminal.event_sequence
                 AND transition.event_hash = terminal.event_hash
                 AND state.state = terminal.node_state
                 AND event.event_type = CASE terminal.node_state
                     WHEN 'Succeeded' THEN 'node_succeeded'
                     ELSE 'node_failed' END
                 AND NOT EXISTS (
                     SELECT 1 FROM dag_node_state_events AS later
                     WHERE later.task_id = state.task_id
                       AND later.plan_id = state.plan_id
                       AND later.node_id = state.node_id
                       AND later.revision > state.revision
                 )
           ))
          OR
          (NEW.result = 'cancel_confirmed'
           AND NEW.terminal_status = 'Cancelled'
           AND event.event_type = 'task_cancelled'
           AND json_extract(
               NEW.adapter_proof_json, '$.state'
           ) = 'terminal_won'
           AND json_extract(
               NEW.adapter_proof_json, '$.code'
           ) = 'CANCEL_TERMINAL_WON'
           AND EXISTS (
               SELECT 1
               FROM dag_node_execution_admissions AS admission
               JOIN dag_node_terminal_facts AS terminal
                 ON terminal.intent_id = admission.intent_id
               JOIN run_events AS natural
                 ON natural.task_id = terminal.task_id
                AND natural.sequence = terminal.event_sequence
               JOIN dag_node_execution_transition_facts AS transition
                 ON transition.intent_id = terminal.intent_id
                AND transition.node_revision = terminal.node_revision
               JOIN dag_node_state_events AS state
                 ON state.task_id = terminal.task_id
                AND state.plan_id = terminal.plan_id
                AND state.node_id = terminal.node_id
                AND state.revision = terminal.node_revision
               WHERE admission.intent_id = NEW.intent_id
                 AND admission.task_id = NEW.task_id
                 AND terminal.task_id = NEW.task_id
                 AND terminal.attempt_id = NEW.attempt_id
                 AND terminal.attempt_number = 1
                 AND terminal.node_state = json_extract(
                     NEW.adapter_proof_json, '$.terminal_status'
                 )
                 AND terminal.node_state IN ('Succeeded', 'Failed')
                 AND terminal.event_sequence + 1 = event.sequence
                 AND terminal.event_hash = natural.document_hash
                 AND natural.task_status = 'Running'
                 AND natural.event_type = CASE terminal.node_state
                     WHEN 'Succeeded' THEN 'node_succeeded'
                     ELSE 'node_failed' END
                 AND transition.state = terminal.node_state
                 AND transition.event_sequence = terminal.event_sequence
                 AND transition.event_hash = terminal.event_hash
                 AND state.state = terminal.node_state
                 AND NOT EXISTS (
                     SELECT 1 FROM dag_node_state_events AS later
                     WHERE later.task_id = state.task_id
                       AND later.plan_id = state.plan_id
                       AND later.node_id = state.node_id
                       AND later.revision > state.revision
                 )
           ))
      )
)
BEGIN
    SELECT RAISE(ABORT, 'cancel outcome requires its exact terminal event');
END;
