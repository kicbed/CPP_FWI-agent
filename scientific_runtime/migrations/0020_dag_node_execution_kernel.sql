-- P3 executable DAG vertical kernel.
--
-- This migration deliberately retains v3's one-dispatch-intent-per-Task
-- contract.  One exact ready Pending DAG node may be admitted into that
-- existing P2 execution kernel; successor-node scheduling remains dormant.
-- Every node projection is append-only and is authorized by the current
-- approved Plan plus an active Supervisor term.  Admission must use the
-- binding's still-active exact term; completion may use a later active term,
-- while preserving both identities in the terminal receipt.

CREATE TABLE dag_node_execution_admissions (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71
        AND substr(plan_hash, 1, 7) = 'sha256:'
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
        typeof(input_fencing_token) = 'integer'
        AND input_fencing_token >= 1
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
        length(document_hash) = 71
        AND substr(document_hash, 1, 7) = 'sha256:'
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

CREATE INDEX idx_dag_node_execution_admissions_term
    ON dag_node_execution_admissions(
        project_id, principal_id, admission_fencing_token,
        task_id, approval_id, node_id
    );

CREATE TRIGGER dag_node_execution_admission_requires_exact_current_case
BEFORE INSERT ON dag_node_execution_admissions
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN plan_node_idempotency AS node_identity
      ON node_identity.task_id = task.task_id
     AND node_identity.plan_id = plan.plan_id
     AND node_identity.node_id = NEW.node_id
    JOIN dispatch_intents AS intent
      ON intent.intent_id = NEW.intent_id
     AND intent.task_id = task.task_id
     AND intent.plan_id = plan.plan_id
     AND intent.plan_hash = plan.plan_hash
     AND intent.approval_id = approval.approval_id
     AND intent.node_id = node_identity.node_id
     AND intent.node_idempotency_key = node_identity.idempotency_key
    JOIN dag_node_input_binding_facts AS binding
      ON binding.task_id = task.task_id
     AND binding.plan_id = plan.plan_id
     AND binding.approval_id = approval.approval_id
     AND binding.target_node_id = node_identity.node_id
     AND binding.target_node_revision = NEW.pending_revision
     AND binding.fencing_token = NEW.input_fencing_token
     AND binding.binding_document_hash
         = NEW.input_binding_document_hash
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'AwaitingApproval'
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND node_identity.idempotency_key = NEW.node_idempotency_key
      AND binding.plan_hash = NEW.plan_hash
      AND binding.target_node_state = 'Pending'
      AND binding.project_id = NEW.project_id
      AND binding.principal_id = NEW.principal_id
      AND binding.owner_id = NEW.input_owner_id
      AND binding.term_acquired_at = NEW.input_term_acquired_at
      AND NEW.input_fencing_token = NEW.admission_fencing_token
      AND NEW.input_owner_id = NEW.admission_owner_id
      AND NEW.input_term_acquired_at = NEW.admission_term_acquired_at
      AND binding.recorded_at_us <= NEW.admitted_at_us
      AND NEW.queued_revision = NEW.pending_revision + 1
      AND NEW.max_node_attempts = 1
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND (
          SELECT COUNT(*)
          FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id') = NEW.node_id
            AND json_extract(plan_node.value, '$.idempotency_key')
                = NEW.node_idempotency_key
      ) = 1
      AND EXISTS (
          SELECT 1
          FROM dag_node_state_events AS pending
          WHERE pending.task_id = NEW.task_id
            AND pending.plan_id = NEW.plan_id
            AND pending.plan_hash = NEW.plan_hash
            AND pending.node_id = NEW.node_id
            AND pending.revision = NEW.pending_revision
            AND pending.state = 'Pending'
            AND NOT EXISTS (
                SELECT 1
                FROM dag_node_state_events AS later
                WHERE later.task_id = pending.task_id
                  AND later.plan_id = pending.plan_id
                  AND later.node_id = pending.node_id
                  AND later.revision > pending.revision
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_attempts AS attempt
          WHERE attempt.intent_id = NEW.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_launch_attempts AS attempt
          WHERE attempt.intent_id = NEW.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_retry_reservations AS retry
          WHERE retry.intent_id = NEW.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_exit_retry_reservations AS retry
          WHERE retry.intent_id = NEW.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node admission requires the exact current approved Pending case'
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
    AND json_type(NEW.document_json, '$.node.pending_revision') = 'integer'
    AND json_extract(NEW.document_json, '$.node.pending_revision')
        = NEW.pending_revision
    AND json_type(NEW.document_json, '$.node.queued_revision') = 'integer'
    AND json_extract(NEW.document_json, '$.node.queued_revision')
        = NEW.queued_revision
    AND json_extract(NEW.document_json, '$.node.idempotency_key')
        = NEW.node_idempotency_key
    AND json_extract(NEW.document_json, '$.input_binding.document_hash')
        = NEW.input_binding_document_hash
    AND json_type(
        NEW.document_json, '$.input_binding.supervisor_term.fencing_token'
    ) = 'integer'
    AND json_extract(
        NEW.document_json, '$.input_binding.supervisor_term.fencing_token'
    ) = NEW.input_fencing_token
    AND json_extract(
        NEW.document_json, '$.input_binding.supervisor_term.owner_id'
    ) = NEW.input_owner_id
    AND json_extract(
        NEW.document_json, '$.input_binding.supervisor_term.acquired_at'
    ) = NEW.input_term_acquired_at
    AND json_extract(NEW.document_json, '$.scope.project_id') = NEW.project_id
    AND json_extract(NEW.document_json, '$.scope.principal_id')
        = NEW.principal_id
    AND json_type(
        NEW.document_json, '$.admission_supervisor_term.fencing_token'
    ) = 'integer'
    AND json_extract(
        NEW.document_json, '$.admission_supervisor_term.fencing_token'
    ) = NEW.admission_fencing_token
    AND json_extract(
        NEW.document_json, '$.admission_supervisor_term.owner_id'
    ) = NEW.admission_owner_id
    AND json_extract(
        NEW.document_json, '$.admission_supervisor_term.acquired_at'
    ) = NEW.admission_term_acquired_at
    AND json_type(NEW.document_json, '$.max_node_attempts') = 'integer'
    AND json_extract(NEW.document_json, '$.max_node_attempts')
        = NEW.max_node_attempts
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

-- Cancel/timeout/checkpoint projection for a node is deliberately outside this
-- bounded vertical kernel. Fail closed so generic P2 completion cannot leave
-- an admitted node Running under a terminal Task projection.
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
    SELECT RAISE(
        ABORT,
        'DAG node execution cannot adopt a control-owned Task'
    );
END;

CREATE TRIGGER dag_node_execution_rejects_cancel_request
BEFORE INSERT ON task_cancel_requests
WHEN EXISTS (
    SELECT 1 FROM dag_node_execution_admissions AS admission
    WHERE admission.task_id = NEW.task_id
      AND admission.intent_id = NEW.intent_id
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node cancellation is outside the current execution kernel'
    );
END;

CREATE TRIGGER dag_node_execution_rejects_timeout_window
BEFORE INSERT ON worker_attempt_timeout_windows
WHEN EXISTS (
    SELECT 1 FROM dag_node_execution_admissions AS admission
    WHERE admission.task_id = NEW.task_id
      AND admission.intent_id = NEW.intent_id
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node timeout is outside the current execution kernel'
    );
END;

CREATE TRIGGER dag_node_execution_rejects_checkpoint_wait
BEFORE INSERT ON worker_checkpoint_waits
WHEN EXISTS (
    SELECT 1 FROM dag_node_execution_admissions AS admission
    WHERE admission.task_id = NEW.task_id
      AND admission.intent_id = NEW.intent_id
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node checkpoint is outside the current execution kernel'
    );
END;

-- A transition fact is written after its RunEvent but before the projected
-- dag_node_state_events row.  That ordering makes the state trigger see one
-- exact cause and lets an interrupted transaction be replayed idempotently.
CREATE TABLE dag_node_execution_transition_facts (
    intent_id TEXT NOT NULL,
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer' AND node_revision >= 2
    ),
    previous_state TEXT NOT NULL CHECK (
        previous_state IN ('Pending', 'Queued', 'Running')
    ),
    state TEXT NOT NULL CHECK (
        state IN ('Queued', 'Running', 'Succeeded', 'Failed')
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

CREATE INDEX idx_dag_node_execution_transition_facts_term
    ON dag_node_execution_transition_facts(
        project_id, principal_id, fencing_token, intent_id, node_revision
    );

-- One terminal fact binds the P2 receipt/evidence to the exact node
-- transition.  It intentionally has no FK to the transition or state row:
-- it is inserted first and is the cause checked by both later inserts.  The
-- exact RunEvent, attempt evidence, admission, and terms still have immediate
-- foreign keys.  A direct exact-negative reconciliation has no adopted
-- attempt, while Running terminal outcomes require the complete attempt tuple.
CREATE TABLE dag_node_terminal_facts (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71
        AND substr(plan_hash, 1, 7) = 'sha256:'
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
        typeof(input_fencing_token) = 'integer'
        AND input_fencing_token >= 1
    ),
    input_owner_id TEXT NOT NULL,
    input_term_acquired_at TEXT NOT NULL,
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer'
        AND node_revision > input_binding_node_revision
    ),
    node_state TEXT NOT NULL CHECK (node_state IN ('Succeeded', 'Failed')),
    event_sequence INTEGER NOT NULL CHECK (
        typeof(event_sequence) = 'integer' AND event_sequence >= 1
    ),
    event_hash TEXT NOT NULL CHECK (
        length(event_hash) = 71
        AND substr(event_hash, 1, 7) = 'sha256:'
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
        (attempt_id IS NULL
         AND attempt_number IS NULL
         AND worker_observation_sequence IS NULL
         AND worker_observation_hash IS NULL
         AND dispatch_handle_json IS NULL
         AND dispatch_handle_hash IS NULL)
        OR
        (attempt_id IS NOT NULL
         AND attempt_number = 1
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
        (node_state = 'Succeeded'
         AND attempt_id IS NOT NULL
         AND receipt_document_json IS NOT NULL
         AND length(receipt_document_hash) = 71
         AND substr(receipt_document_hash, 1, 7) = 'sha256:')
        OR
        (node_state = 'Failed'
         AND receipt_document_json IS NULL
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
        REFERENCES worker_attempt_observations(
            attempt_id, observation_sequence
        ),
    FOREIGN KEY (project_id, principal_id, input_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        ),
    FOREIGN KEY (project_id, principal_id, completion_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_dag_node_terminal_facts_term
    ON dag_node_terminal_facts(
        project_id, principal_id, completion_fencing_token,
        task_id, approval_id, node_id
    );

CREATE TRIGGER dag_node_terminal_fact_requires_exact_current_case
BEFORE INSERT ON dag_node_terminal_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    JOIN dispatch_intents AS intent
      ON intent.intent_id = admission.intent_id
    JOIN tasks AS task
      ON task.task_id = admission.task_id
     AND task.project_id = admission.project_id
     AND task.principal_id = admission.principal_id
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
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
     AND binding.binding_document_hash
         = admission.input_binding_document_hash
    JOIN run_events AS event
      ON event.task_id = admission.task_id
     AND event.sequence = NEW.event_sequence
    JOIN supervised_run_event_commits AS event_commit
      ON event_commit.task_id = event.task_id
     AND event_commit.sequence = event.sequence
    WHERE admission.intent_id = NEW.intent_id
      AND admission.task_id = NEW.task_id
      AND admission.plan_id = NEW.plan_id
      AND admission.plan_hash = NEW.plan_hash
      AND admission.approval_id = NEW.approval_id
      AND admission.node_id = NEW.node_id
      AND admission.pending_revision = NEW.input_binding_node_revision
      AND admission.input_binding_document_hash
          = NEW.input_binding_document_hash
      AND admission.input_fencing_token = NEW.input_fencing_token
      AND admission.input_owner_id = NEW.input_owner_id
      AND admission.input_term_acquired_at = NEW.input_term_acquired_at
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND admission.max_node_attempts = 1
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND binding.owner_id = NEW.input_owner_id
      AND binding.term_acquired_at = NEW.input_term_acquired_at
      AND binding.plan_hash = NEW.plan_hash
      AND binding.target_node_state = 'Pending'
      AND intent.task_id = NEW.task_id
      AND intent.plan_id = NEW.plan_id
      AND intent.plan_hash = NEW.plan_hash
      AND intent.approval_id = NEW.approval_id
      AND intent.node_id = NEW.node_id
      AND event.node_id = NEW.node_id
      AND (
          (NEW.attempt_id IS NULL
           AND event.fingerprint_hash = intent.fingerprint_hash)
          OR
          (NEW.attempt_id IS NOT NULL
           AND EXISTS (
               SELECT 1
               FROM effective_dispatched_intents AS effective_fingerprint
               WHERE effective_fingerprint.intent_id = admission.intent_id
                 AND json_extract(
                     effective_fingerprint.outcome_document_json,
                     '$.handle.fingerprint'
                 ) = json_extract(event.document_json, '$.fingerprint')
           ))
      )
      AND event.document_hash = NEW.event_hash
      AND event.event_type = CASE NEW.node_state
          WHEN 'Succeeded' THEN 'node_succeeded'
          ELSE 'node_failed'
      END
      AND (
          (NEW.node_state = 'Succeeded'
           AND NEW.node_revision = admission.queued_revision + 2
           AND task.status = 'Running'
           AND event.task_status = 'Running'
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS running
               WHERE running.task_id = admission.task_id
                 AND running.plan_id = admission.plan_id
                 AND running.plan_hash = admission.plan_hash
                 AND running.node_id = admission.node_id
                 AND running.revision = admission.queued_revision + 1
                 AND running.state = 'Running'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = running.task_id
                       AND later.plan_id = running.plan_id
                       AND later.node_id = running.node_id
                       AND later.revision > running.revision
                 )
           ))
          OR
          (NEW.node_state = 'Failed'
           AND NEW.node_revision = admission.queued_revision + 2
           AND task.status = 'Running'
           AND event.task_status = 'Running'
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS running
               WHERE running.task_id = admission.task_id
                 AND running.plan_id = admission.plan_id
                 AND running.plan_hash = admission.plan_hash
                 AND running.node_id = admission.node_id
                 AND running.revision = admission.queued_revision + 1
                 AND running.state = 'Running'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = running.task_id
                       AND later.plan_id = running.plan_id
                       AND later.node_id = running.node_id
                       AND later.revision > running.revision
                 )
           ))
          OR
          (NEW.node_state = 'Failed'
           AND NEW.node_revision = admission.queued_revision + 1
           AND task.status = 'Queued'
           AND event.task_status = 'Running'
           AND (
               (NEW.attempt_id IS NULL
                AND json_extract(
                    event.document_json, '$.error.code'
                ) = 'dispatch_not_started')
               OR
               (NEW.attempt_id IS NOT NULL
                AND json_extract(
                    event.document_json, '$.error.code'
                ) = 'worker_exit')
           )
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS queued
               WHERE queued.task_id = admission.task_id
                 AND queued.plan_id = admission.plan_id
                 AND queued.plan_hash = admission.plan_hash
                 AND queued.node_id = admission.node_id
                 AND queued.revision = admission.queued_revision
                 AND queued.state = 'Queued'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = queued.task_id
                       AND later.plan_id = queued.plan_id
                       AND later.node_id = queued.node_id
                       AND later.revision > queued.revision
                 )
           ))
      )
      AND event.sequence = (
          SELECT MAX(latest.sequence)
          FROM run_events AS latest
          WHERE latest.task_id = admission.task_id
      )
      AND event_commit.project_id = NEW.project_id
      AND event_commit.principal_id = NEW.principal_id
      AND event_commit.fencing_token = NEW.completion_fencing_token
      AND event_commit.recorded_at = NEW.recorded_at
      AND event_commit.recorded_at_us = NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG terminal fact requires the exact current admitted node'
    );
END;

CREATE TRIGGER dag_node_terminal_fact_requires_exact_p2_evidence
BEFORE INSERT ON dag_node_terminal_facts
WHEN NOT (
    (
        NEW.attempt_id IS NOT NULL
        AND NEW.attempt_number = 1
        AND EXISTS (
            SELECT 1
            FROM effective_dispatched_intents AS effective
            JOIN worker_launch_attempts AS attempt
              ON attempt.intent_id = effective.intent_id
             AND attempt.attempt_id = NEW.attempt_id
            JOIN worker_attempt_observations AS observation
              ON observation.attempt_id = attempt.attempt_id
             AND observation.observation_sequence
                 = NEW.worker_observation_sequence
            JOIN supervised_dispatch_adoptions AS adoption
              ON adoption.intent_id = effective.intent_id
             AND adoption.attempt_id = attempt.attempt_id
            JOIN run_events AS terminal_event
              ON terminal_event.task_id = NEW.task_id
             AND terminal_event.sequence = NEW.event_sequence
            WHERE effective.intent_id = NEW.intent_id
              AND effective.outcome_document_hash
                  = NEW.dispatch_outcome_document_hash
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
              AND observation.heartbeat_record_hash IS NOT NULL
              AND (
                  (NEW.node_state = 'Succeeded'
                   AND observation.heartbeat_state = 'succeeded')
                  OR
                  (NEW.node_state = 'Failed'
                   AND (
                       (observation.heartbeat_state IN ('failed', 'stopped')
                        AND json_extract(
                            terminal_event.document_json, '$.error.code'
                        ) = 'worker_failed'
                        AND json_extract(
                            NEW.adapter_status_json, '$.status'
                        ) = 'Failed'
                        AND json_extract(
                            NEW.adapter_status_json, '$.updated_at'
                        ) = terminal_event.occurred_at)
                       OR
                       (observation.heartbeat_state = 'running'
                        AND json_extract(
                            terminal_event.document_json, '$.error.code'
                        ) = 'worker_exit'
                        AND json_extract(
                            NEW.adapter_status_json,
                            '$.adapter_status.status'
                        ) = 'Failed'
                        AND json_extract(
                            NEW.adapter_status_json,
                            '$.adapter_status.stage'
                        ) = 'worker_exit'
                        AND json_extract(
                            NEW.adapter_status_json,
                            '$.adapter_status.updated_at'
                        ) = terminal_event.occurred_at
                        AND json_extract(
                            NEW.adapter_status_json,
                            '$.worker_exit.evidence_hash'
                        ) = NEW.worker_observation_hash
                        AND json_extract(
                            NEW.adapter_status_json,
                            '$.worker_exit.private_schema_version'
                        ) IN ('1.1.0', '1.2.0')
                        AND length(json_extract(
                            NEW.adapter_status_json,
                            '$.worker_exit.private_proof_hash'
                        )) = 71
                        AND substr(json_extract(
                            NEW.adapter_status_json,
                            '$.worker_exit.private_proof_hash'
                        ), 1, 7) = 'sha256:'
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".intent_id'
                        ) = NEW.intent_id
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".attempt_id'
                        ) = NEW.attempt_id
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".attempt_number'
                        ) = 1
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".observation_sequence'
                        ) = NEW.worker_observation_sequence
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".evidence_hash'
                        ) = NEW.worker_observation_hash
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".private_schema_version'
                        ) = json_extract(
                            NEW.adapter_status_json,
                            '$.worker_exit.private_schema_version'
                        )
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".private_proof_hash'
                        ) = json_extract(
                            NEW.adapter_status_json,
                            '$.worker_exit.private_proof_hash'
                        )
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".failure_kind'
                        ) = 'worker_exit'
                        AND json_extract(
                            terminal_event.document_json,
                            '$.extensions."org.agent_rpc.dag_no_retry".max_node_attempts'
                        ) = 1)
                   ))
              )
        )
    )
    OR
    (
        NEW.node_state = 'Failed'
        AND NEW.attempt_id IS NULL
        AND EXISTS (
            SELECT 1
            FROM dispatch_outcomes AS outcome
            JOIN dispatch_reconciliation_observations AS observation
              ON observation.intent_id = outcome.intent_id
            WHERE outcome.intent_id = NEW.intent_id
              AND outcome.outcome = 'reconciliation_required'
              AND outcome.document_hash
                  = NEW.dispatch_outcome_document_hash
              AND observation.task_id = NEW.task_id
              AND observation.project_id = NEW.project_id
              AND observation.principal_id = NEW.principal_id
              AND observation.source_outcome_hash = outcome.document_hash
              AND observation.classification = 'exact_negative'
              AND observation.failure_code = 'DISPATCH_NOT_STARTED'
              AND observation.evidence_kind
                  = 'managed_pre_running_failure'
              AND observation.attempt_number = 1
              AND observation.fencing_token = NEW.completion_fencing_token
              AND observation.observed_at_us <= NEW.recorded_at_us
              AND observation.observation_sequence = (
                  SELECT MAX(latest.observation_sequence)
                  FROM dispatch_reconciliation_observations AS latest
                  WHERE latest.intent_id = outcome.intent_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM dispatch_reconciliation_resolutions AS positive
                  WHERE positive.intent_id = outcome.intent_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM dispatch_reconciliation_negative_resolutions AS negative
                  WHERE negative.intent_id = outcome.intent_id
              )
        )
    )
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
WHEN NEW.node_state = 'Succeeded'
 AND NOT EXISTS (
    SELECT 1
    FROM plans AS plan
    JOIN approvals AS approval
      ON approval.task_id = plan.task_id
     AND approval.approval_id = NEW.approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN json_each(plan.document_json, '$.nodes') AS plan_node
      ON json_extract(plan_node.value, '$.node_id') = NEW.node_id
    JOIN run_events AS success_event
      ON success_event.task_id = NEW.task_id
     AND success_event.sequence = NEW.event_sequence
    WHERE plan.task_id = NEW.task_id
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.decision = 'approved'
      AND json_type(plan_node.value, '$.outputs') = 'array'
      AND json_array_length(plan_node.value, '$.outputs') >= 1
      AND json_valid(NEW.receipt_document_json)
      AND json_type(NEW.receipt_document_json, '$') = 'object'
      AND json_extract(
          NEW.receipt_document_json, '$.schema_version'
      ) = '2.0.0'
      AND json_extract(
          NEW.receipt_document_json, '$.task_id'
      ) = NEW.task_id
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
      AND json_type(
          NEW.receipt_document_json, '$.node.input_binding_revision'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json, '$.node.input_binding_revision'
      ) = NEW.input_binding_node_revision
      AND json_type(
          NEW.receipt_document_json, '$.node.succeeded_revision'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json, '$.node.succeeded_revision'
      ) = NEW.node_revision
      AND json_extract(
          NEW.receipt_document_json, '$.node.state'
      ) = NEW.node_state
      AND json_extract(
          NEW.receipt_document_json, '$.input_binding_document_hash'
      ) = NEW.input_binding_document_hash
      AND json_extract(
          NEW.receipt_document_json, '$.scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.receipt_document_json, '$.scope.principal_id'
      ) = NEW.principal_id
      AND json_type(
          NEW.receipt_document_json,
          '$.input_supervisor_term.fencing_token'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json,
          '$.input_supervisor_term.fencing_token'
      ) = NEW.input_fencing_token
      AND json_extract(
          NEW.receipt_document_json, '$.input_supervisor_term.owner_id'
      ) = NEW.input_owner_id
      AND json_extract(
          NEW.receipt_document_json, '$.input_supervisor_term.acquired_at'
      ) = NEW.input_term_acquired_at
      AND json_type(
          NEW.receipt_document_json,
          '$.completion_supervisor_term.fencing_token'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json,
          '$.completion_supervisor_term.fencing_token'
      ) = NEW.completion_fencing_token
      AND json_extract(
          NEW.receipt_document_json,
          '$.completion_supervisor_term.owner_id'
      ) = NEW.completion_owner_id
      AND json_extract(
          NEW.receipt_document_json,
          '$.completion_supervisor_term.acquired_at'
      ) = NEW.completion_term_acquired_at
      AND json_extract(
          NEW.receipt_document_json, '$.dispatch.intent_id'
      ) = NEW.intent_id
      AND json_extract(
          NEW.receipt_document_json, '$.dispatch.node_idempotency_key'
      ) = (
          SELECT admission.node_idempotency_key
          FROM dag_node_execution_admissions AS admission
          WHERE admission.intent_id = NEW.intent_id
      )
      AND json_extract(
          NEW.receipt_document_json, '$.dispatch.handle_hash'
      ) = NEW.dispatch_handle_hash
      AND json_extract(
          NEW.receipt_document_json, '$.dispatch.attempt_id'
      ) = NEW.attempt_id
      AND json_type(
          NEW.receipt_document_json, '$.dispatch.attempt_number'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json, '$.dispatch.attempt_number'
      ) = NEW.attempt_number
      AND json_type(
          NEW.receipt_document_json,
          '$.dispatch.worker_observation_sequence'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json,
          '$.dispatch.worker_observation_sequence'
      ) = NEW.worker_observation_sequence
      AND json_extract(
          NEW.receipt_document_json, '$.dispatch.worker_observation_hash'
      ) = NEW.worker_observation_hash
      AND json_extract(
          NEW.receipt_document_json,
          '$.dispatch.outcome_document_hash'
      ) = NEW.dispatch_outcome_document_hash
      AND length(json_extract(
          NEW.receipt_document_json, '$.receipt_record_hash'
      )) = 71
      AND substr(json_extract(
          NEW.receipt_document_json, '$.receipt_record_hash'
      ), 1, 7) = 'sha256:'
      AND json_extract(
          NEW.receipt_document_json, '$.succeeded_at'
      ) = success_event.occurred_at
      AND json_extract(NEW.adapter_status_json, '$.status') = 'Succeeded'
      AND json_extract(NEW.adapter_status_json, '$.updated_at')
          = success_event.occurred_at
      AND json_type(NEW.receipt_document_json, '$.outputs') = 'array'
      AND json_array_length(NEW.receipt_document_json, '$.outputs')
          = json_array_length(plan_node.value, '$.outputs')
      AND (
          SELECT COUNT(DISTINCT json_extract(output.value, '$.port'))
          FROM json_each(plan_node.value, '$.outputs') AS output
      ) = json_array_length(plan_node.value, '$.outputs')
      AND (
          SELECT COUNT(DISTINCT json_extract(
              receipt_output.value, '$.output_port'
          ))
          FROM json_each(
              NEW.receipt_document_json, '$.outputs'
          ) AS receipt_output
      ) = json_array_length(NEW.receipt_document_json, '$.outputs')
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(
              NEW.receipt_document_json, '$.outputs'
          ) AS receipt_output
          WHERE json_type(receipt_output.value, '$') != 'object'
             OR json_type(receipt_output.value, '$.output_port') != 'text'
             OR json_type(receipt_output.value, '$.data_type') != 'text'
             OR json_type(
                 receipt_output.value, '$.artifact_manifest'
             ) != 'object'
             OR json_type(
                 receipt_output.value, '$.artifact_manifest_hash'
             ) != 'text'
             OR length(json_extract(
                 receipt_output.value, '$.artifact_manifest_hash'
             )) != 71
             OR substr(json_extract(
                 receipt_output.value, '$.artifact_manifest_hash'
             ), 1, 7) != 'sha256:'
             OR NOT EXISTS (
                 SELECT 1
                 FROM json_each(plan_node.value, '$.outputs') AS planned_output
                 WHERE json_extract(planned_output.value, '$.port')
                       = json_extract(
                           receipt_output.value, '$.output_port'
                       )
                   AND json_extract(planned_output.value, '$.data_type')
                       = json_extract(receipt_output.value, '$.data_type')
             )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(plan_node.value, '$.outputs') AS planned_output
          WHERE NOT EXISTS (
              SELECT 1
              FROM json_each(
                  NEW.receipt_document_json, '$.outputs'
              ) AS receipt_output
              WHERE json_extract(receipt_output.value, '$.output_port')
                    = json_extract(planned_output.value, '$.port')
                AND json_extract(receipt_output.value, '$.data_type')
                    = json_extract(planned_output.value, '$.data_type')
          )
      )
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG Succeeded terminal requires one complete canonical receipt'
    );
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

CREATE TRIGGER dag_node_execution_transition_requires_exact_current_case
BEFORE INSERT ON dag_node_execution_transition_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    JOIN dispatch_intents AS intent
      ON intent.intent_id = admission.intent_id
    JOIN tasks AS task
      ON task.task_id = admission.task_id
     AND task.project_id = admission.project_id
     AND task.principal_id = admission.principal_id
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN run_events AS event
      ON event.task_id = admission.task_id
     AND event.sequence = NEW.event_sequence
    JOIN supervised_run_event_commits AS event_commit
      ON event_commit.task_id = event.task_id
     AND event_commit.sequence = event.sequence
    WHERE admission.intent_id = NEW.intent_id
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND admission.plan_id = plan.plan_id
      AND admission.plan_hash = plan.plan_hash
      AND admission.approval_id = approval.approval_id
      AND admission.max_node_attempts = 1
      AND approval.decision = 'approved'
      AND intent.task_id = admission.task_id
      AND intent.plan_id = admission.plan_id
      AND intent.plan_hash = admission.plan_hash
      AND intent.approval_id = admission.approval_id
      AND intent.node_id = admission.node_id
      AND (
          event.node_id = admission.node_id
          OR (NEW.state = 'Queued' AND event.node_id IS NULL)
      )
      AND (
          ((NEW.state = 'Queued'
            OR (NEW.previous_state = 'Queued' AND NEW.state = 'Failed'))
           AND event.fingerprint_hash = intent.fingerprint_hash)
          OR
          (NEW.state != 'Queued'
           AND NOT (NEW.previous_state = 'Queued' AND NEW.state = 'Failed')
           AND EXISTS (
               SELECT 1
               FROM effective_dispatched_intents AS effective_fingerprint
               WHERE effective_fingerprint.intent_id = admission.intent_id
                 AND json_extract(
                     effective_fingerprint.outcome_document_json,
                     '$.handle.fingerprint'
                 ) = json_extract(event.document_json, '$.fingerprint')
           ))
      )
      AND event.document_hash = NEW.event_hash
      AND event.sequence = (
          SELECT MAX(latest.sequence)
          FROM run_events AS latest
          WHERE latest.task_id = admission.task_id
      )
      AND event_commit.project_id = NEW.project_id
      AND event_commit.principal_id = NEW.principal_id
      AND event_commit.fencing_token = NEW.fencing_token
      AND event_commit.recorded_at = NEW.recorded_at
      AND event_commit.recorded_at_us = NEW.recorded_at_us
      AND (
          (NEW.previous_state = 'Pending'
           AND NEW.state = 'Queued'
           AND NEW.node_revision = admission.queued_revision
           AND NEW.reason = 'execution_admitted'
           AND task.status = 'AwaitingApproval'
           AND event.event_type = 'task_queued'
           AND event.task_status = 'Queued'
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS pending
               WHERE pending.task_id = admission.task_id
                 AND pending.plan_id = admission.plan_id
                 AND pending.plan_hash = admission.plan_hash
                 AND pending.node_id = admission.node_id
                 AND pending.revision = admission.pending_revision
                 AND pending.state = 'Pending'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = pending.task_id
                       AND later.plan_id = pending.plan_id
                       AND later.node_id = pending.node_id
                       AND later.revision > pending.revision
                 )
           ))
          OR
          (NEW.previous_state = 'Queued'
           AND NEW.state = 'Running'
           AND NEW.node_revision = admission.queued_revision + 1
           AND NEW.reason = 'dispatch_receipt_adopted'
           AND task.status = 'Queued'
           AND event.event_type = 'node_started'
           AND event.task_status = 'Running'
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS queued
               WHERE queued.task_id = admission.task_id
                 AND queued.plan_id = admission.plan_id
                 AND queued.plan_hash = admission.plan_hash
                 AND queued.node_id = admission.node_id
                 AND queued.revision = admission.queued_revision
                 AND queued.state = 'Queued'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = queued.task_id
                       AND later.plan_id = queued.plan_id
                       AND later.node_id = queued.node_id
                       AND later.revision > queued.revision
                 )
           )
           AND EXISTS (
               SELECT 1
               FROM effective_dispatched_intents AS effective
               JOIN worker_launch_attempts AS attempt
                 ON attempt.intent_id = effective.intent_id
               JOIN supervised_dispatch_adoptions AS adoption
                 ON adoption.intent_id = attempt.intent_id
                AND adoption.attempt_id = attempt.attempt_id
               WHERE effective.intent_id = admission.intent_id
                 AND attempt.task_id = admission.task_id
                 AND attempt.project_id = admission.project_id
                 AND attempt.principal_id = admission.principal_id
                 AND attempt.attempt_number = 1
           ))
          OR
          (NEW.previous_state = 'Running'
           AND NEW.state IN ('Succeeded', 'Failed')
           AND NEW.node_revision = admission.queued_revision + 2
           AND NEW.reason = CASE
               WHEN NEW.state = 'Succeeded' THEN 'adapter_succeeded'
               WHEN json_extract(
                   event.document_json, '$.error.code'
               ) = 'worker_exit' THEN 'worker_exit_no_retry'
               ELSE 'adapter_failed'
           END
           AND task.status = 'Running'
           AND event.event_type = CASE NEW.state
               WHEN 'Succeeded' THEN 'node_succeeded'
               ELSE 'node_failed'
           END
           AND event.task_status = 'Running'
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS running
               WHERE running.task_id = admission.task_id
                 AND running.plan_id = admission.plan_id
                 AND running.plan_hash = admission.plan_hash
                 AND running.node_id = admission.node_id
                 AND running.revision = admission.queued_revision + 1
                 AND running.state = 'Running'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = running.task_id
                       AND later.plan_id = running.plan_id
                       AND later.node_id = running.node_id
                       AND later.revision > running.revision
                 )
           )
           AND EXISTS (
               SELECT 1
               FROM dag_node_terminal_facts AS terminal
               WHERE terminal.intent_id = admission.intent_id
                 AND terminal.task_id = admission.task_id
                 AND terminal.plan_id = admission.plan_id
                 AND terminal.plan_hash = admission.plan_hash
                 AND terminal.approval_id = admission.approval_id
                 AND terminal.node_id = admission.node_id
                 AND terminal.node_revision = NEW.node_revision
                 AND terminal.node_state = NEW.state
                 AND terminal.event_sequence = NEW.event_sequence
                 AND terminal.event_hash = NEW.event_hash
                 AND terminal.project_id = NEW.project_id
                 AND terminal.principal_id = NEW.principal_id
                 AND terminal.completion_fencing_token = NEW.fencing_token
                 AND terminal.completion_owner_id = NEW.owner_id
                 AND terminal.completion_term_acquired_at
                     = NEW.term_acquired_at
                 AND terminal.recorded_at = NEW.recorded_at
                 AND terminal.recorded_at_us = NEW.recorded_at_us
           ))
          OR
          (NEW.previous_state = 'Queued'
           AND NEW.state = 'Failed'
           AND NEW.node_revision = admission.queued_revision + 1
           AND NEW.reason = 'dispatch_not_started'
           AND task.status = 'Queued'
           AND event.event_type = 'node_failed'
           AND event.task_status = 'Running'
           AND EXISTS (
               SELECT 1
               FROM dag_node_state_events AS queued
               WHERE queued.task_id = admission.task_id
                 AND queued.plan_id = admission.plan_id
                 AND queued.plan_hash = admission.plan_hash
                 AND queued.node_id = admission.node_id
                 AND queued.revision = admission.queued_revision
                 AND queued.state = 'Queued'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM dag_node_state_events AS later
                     WHERE later.task_id = queued.task_id
                       AND later.plan_id = queued.plan_id
                       AND later.node_id = queued.node_id
                       AND later.revision > queued.revision
                 )
           )
           AND EXISTS (
               SELECT 1
               FROM dag_node_terminal_facts AS terminal
               WHERE terminal.intent_id = admission.intent_id
                 AND terminal.task_id = admission.task_id
                 AND terminal.plan_id = admission.plan_id
                 AND terminal.plan_hash = admission.plan_hash
                 AND terminal.approval_id = admission.approval_id
                 AND terminal.node_id = admission.node_id
                 AND terminal.node_revision = NEW.node_revision
                 AND terminal.node_state = NEW.state
                 AND terminal.event_sequence = NEW.event_sequence
                 AND terminal.event_hash = NEW.event_hash
                 AND terminal.project_id = NEW.project_id
                 AND terminal.principal_id = NEW.principal_id
                 AND terminal.completion_fencing_token = NEW.fencing_token
                 AND terminal.completion_owner_id = NEW.owner_id
                 AND terminal.completion_term_acquired_at
                     = NEW.term_acquired_at
                 AND terminal.recorded_at = NEW.recorded_at
                 AND terminal.recorded_at_us = NEW.recorded_at_us
           ))
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_retry_reservations AS retry
          WHERE retry.intent_id = admission.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_exit_retry_reservations AS retry
          WHERE retry.intent_id = admission.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node transition requires the exact current execution cause'
    );
END;

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

-- Replace v18's dormant-only projection gate.  Revision one remains the
-- exact current approved Pending snapshot.  Every later row must have a
-- matching active-term transition fact, and terminal rows must additionally
-- have the exact P2 terminal fact.
DROP TRIGGER dag_node_state_events_are_initial_pending_only;
DROP TRIGGER dag_node_initial_state_requires_current_approved_plan;

CREATE TRIGGER dag_node_initial_state_has_exact_shape
BEFORE INSERT ON dag_node_state_events
WHEN NEW.revision = 1
 AND (NEW.previous_state IS NOT NULL OR NEW.state != 'Pending')
BEGIN
    SELECT RAISE(ABORT, 'DAG node revision one must be initial Pending');
END;

CREATE TRIGGER dag_node_initial_state_requires_current_approved_plan
BEFORE INSERT ON dag_node_state_events
WHEN NEW.revision = 1
 AND NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    WHERE task.task_id = NEW.task_id
      AND task.status = 'AwaitingApproval'
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.decision = 'approved'
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND (
          SELECT COUNT(*)
          FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id') = NEW.node_id
      ) = 1
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node initial state requires the current approved plan');
END;

CREATE TRIGGER dag_node_transition_state_requires_exact_active_fact
BEFORE INSERT ON dag_node_state_events
WHEN NEW.revision > 1
 AND NOT EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    JOIN dag_node_execution_transition_facts AS transition
      ON transition.intent_id = admission.intent_id
     AND transition.node_revision = NEW.revision
    JOIN tasks AS task
      ON task.task_id = admission.task_id
     AND task.project_id = admission.project_id
     AND task.principal_id = admission.principal_id
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN runtime_supervisor_leases AS lease
      ON lease.project_id = transition.project_id
     AND lease.principal_id = transition.principal_id
     AND lease.fencing_token = transition.fencing_token
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE admission.task_id = NEW.task_id
      AND admission.plan_id = NEW.plan_id
      AND admission.plan_hash = NEW.plan_hash
      AND admission.node_id = NEW.node_id
      AND admission.plan_id = plan.plan_id
      AND admission.plan_hash = plan.plan_hash
      AND admission.approval_id = approval.approval_id
      AND approval.decision = 'approved'
      AND transition.previous_state = NEW.previous_state
      AND transition.state = NEW.state
      AND transition.recorded_at = NEW.recorded_at
      AND transition.recorded_at_us = NEW.recorded_at_us
      AND transition.project_id = admission.project_id
      AND transition.principal_id = admission.principal_id
      AND term.owner_id = transition.owner_id
      AND term.acquired_at = transition.term_acquired_at
      AND lease.heartbeat_at_us <= transition.recorded_at_us
      AND lease.expires_at_us > transition.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
      AND (
          NEW.state NOT IN ('Succeeded', 'Failed')
          OR EXISTS (
              SELECT 1
              FROM dag_node_terminal_facts AS terminal
              WHERE terminal.intent_id = admission.intent_id
                AND terminal.task_id = admission.task_id
                AND terminal.plan_id = admission.plan_id
                AND terminal.plan_hash = admission.plan_hash
                AND terminal.approval_id = admission.approval_id
                AND terminal.node_id = admission.node_id
                AND terminal.node_revision = NEW.revision
                AND terminal.node_state = NEW.state
                AND terminal.event_sequence = transition.event_sequence
                AND terminal.event_hash = transition.event_hash
                AND terminal.project_id = transition.project_id
                AND terminal.principal_id = transition.principal_id
                AND terminal.completion_fencing_token
                    = transition.fencing_token
                AND terminal.completion_owner_id = transition.owner_id
                AND terminal.completion_term_acquired_at
                    = transition.term_acquired_at
                AND terminal.recorded_at = transition.recorded_at
                AND terminal.recorded_at_us = transition.recorded_at_us
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node state transition requires an exact active fact');
END;

-- D-012's bounded P2 retry is Task/Approval admission policy, not a second
-- attempt for every DAG node.  This vertical kernel authorizes exactly one
-- Worker launch and rejects both P2 retry reservation paths.
CREATE TRIGGER dag_node_execution_blocks_second_launch_attempt
BEFORE INSERT ON worker_launch_attempts
WHEN NEW.attempt_number > 1
 AND EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    WHERE admission.intent_id = NEW.intent_id
      AND admission.task_id = NEW.task_id
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND admission.max_node_attempts = 1
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution permits exactly one launch attempt');
END;

CREATE TRIGGER dag_node_execution_blocks_pre_running_retry
BEFORE INSERT ON worker_retry_reservations
WHEN EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    WHERE admission.intent_id = NEW.intent_id
      AND admission.task_id = NEW.task_id
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND admission.approval_id = NEW.approval_id
      AND admission.max_node_attempts = 1
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution does not authorize a retry reservation');
END;

CREATE TRIGGER dag_node_execution_blocks_worker_exit_retry
BEFORE INSERT ON worker_exit_retry_reservations
WHEN EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    WHERE admission.intent_id = NEW.intent_id
      AND admission.task_id = NEW.task_id
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND admission.approval_id = NEW.approval_id
      AND admission.max_node_attempts = 1
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution does not authorize a retry reservation');
END;

-- v16 made exact-negative reconciliation terminal for a single-node Task.
-- Preserve that branch unchanged (including its Task Failed projection) and
-- add one strictly narrower DAG branch whose RunEvent keeps the Task Running
-- while the admitted node becomes Failed.  The DAG branch can be reached only
-- after the terminal, transition, and state facts have all bound the same
-- exact-negative observation and active term.
DROP TRIGGER dispatch_reconciliation_negative_requires_exact_case;

CREATE TRIGGER dispatch_reconciliation_negative_requires_exact_case
BEFORE INSERT ON dispatch_reconciliation_negative_resolutions
WHEN NOT (
    EXISTS (
        SELECT 1
        FROM dispatch_outcomes AS outcome
        JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
        JOIN tasks AS task ON task.task_id = intent.task_id
        JOIN approval_budgets AS budget
          ON budget.task_id = task.task_id
         AND budget.approval_id = intent.approval_id
        JOIN dispatch_reconciliation_observations AS observation
          ON observation.intent_id = outcome.intent_id
         AND observation.observation_sequence = NEW.observation_sequence
        JOIN run_events AS terminal
          ON terminal.task_id = task.task_id
         AND terminal.sequence = NEW.terminal_event_sequence
        JOIN supervised_run_event_commits AS commit_record
          ON commit_record.task_id = terminal.task_id
         AND commit_record.sequence = terminal.sequence
        WHERE outcome.intent_id = NEW.intent_id
          AND outcome.outcome = 'reconciliation_required'
          AND outcome.document_hash = NEW.source_outcome_hash
          AND intent.task_id = NEW.task_id
          AND intent.approval_id = NEW.approval_id
          AND intent.adapter_id = 'fwi.deepwave_adapter'
          AND intent.adapter_version IN ('1.4.0', '1.5.0', '1.6.0')
          AND json_extract(intent.request_json, '$.request.algorithm.id')
              = 'deepwave.acoustic_fwi'
          AND json_extract(intent.request_json, '$.request.algorithm.version')
              = intent.adapter_version
          AND task.project_id = NEW.project_id
          AND task.principal_id = NEW.principal_id
          AND task.status = 'Queued'
          AND observation.classification = 'exact_negative'
          AND observation.failure_code = 'DISPATCH_NOT_STARTED'
          AND observation.evidence_kind = NEW.evidence_kind
          AND observation.adapter_version = intent.adapter_version
          AND observation.source_outcome_hash = outcome.document_hash
          AND observation.fencing_token = NEW.fencing_token
          AND observation.observed_at_us <= NEW.resolved_at_us
          AND budget.tasks_used = NEW.approval_tasks_used
          AND NEW.approval_budget_refunded = 0
          AND terminal.event_type = 'node_failed'
          AND terminal.task_status = 'Failed'
          AND terminal.node_id = intent.node_id
          AND terminal.fingerprint_hash = intent.fingerprint_hash
          AND terminal.document_hash = NEW.terminal_event_hash
          AND terminal.sequence = (
              SELECT MAX(latest.sequence)
              FROM run_events AS latest
              WHERE latest.task_id = task.task_id
          )
          AND commit_record.project_id = task.project_id
          AND commit_record.principal_id = task.principal_id
          AND commit_record.fencing_token = NEW.fencing_token
          AND commit_record.recorded_at = NEW.resolved_at
          AND commit_record.recorded_at_us = NEW.resolved_at_us
          AND NOT EXISTS (
              SELECT 1 FROM dispatch_reconciliation_resolutions AS positive
              WHERE positive.intent_id = outcome.intent_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM worker_retry_reservations AS retry
              WHERE retry.intent_id = outcome.intent_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM worker_exit_retry_reservations AS retry
              WHERE retry.intent_id = outcome.intent_id
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
    OR EXISTS (
        SELECT 1
        FROM dispatch_outcomes AS outcome
        JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
        JOIN dag_node_execution_admissions AS admission
          ON admission.intent_id = intent.intent_id
        JOIN tasks AS task ON task.task_id = intent.task_id
        JOIN plans AS plan
          ON plan.task_id = task.task_id
         AND plan.plan_id = task.current_plan_id
        JOIN approvals AS approval
          ON approval.task_id = task.task_id
         AND approval.approval_id = task.current_approval_id
         AND approval.plan_id = plan.plan_id
         AND approval.plan_hash = plan.plan_hash
        JOIN approval_budgets AS budget
          ON budget.task_id = task.task_id
         AND budget.approval_id = intent.approval_id
        JOIN dispatch_reconciliation_observations AS observation
          ON observation.intent_id = outcome.intent_id
         AND observation.observation_sequence = NEW.observation_sequence
        JOIN run_events AS terminal_event
          ON terminal_event.task_id = task.task_id
         AND terminal_event.sequence = NEW.terminal_event_sequence
        JOIN supervised_run_event_commits AS commit_record
          ON commit_record.task_id = terminal_event.task_id
         AND commit_record.sequence = terminal_event.sequence
        JOIN dag_node_terminal_facts AS terminal
          ON terminal.intent_id = admission.intent_id
         AND terminal.task_id = admission.task_id
        JOIN dag_node_execution_transition_facts AS transition
          ON transition.intent_id = admission.intent_id
         AND transition.node_revision = terminal.node_revision
        JOIN dag_node_state_events AS state
          ON state.task_id = admission.task_id
         AND state.plan_id = admission.plan_id
         AND state.plan_hash = admission.plan_hash
         AND state.node_id = admission.node_id
         AND state.revision = terminal.node_revision
         AND state.state = terminal.node_state
        WHERE outcome.intent_id = NEW.intent_id
          AND outcome.outcome = 'reconciliation_required'
          AND outcome.document_hash = NEW.source_outcome_hash
          AND intent.task_id = NEW.task_id
          AND intent.plan_id = admission.plan_id
          AND intent.plan_hash = admission.plan_hash
          AND intent.approval_id = NEW.approval_id
          AND intent.node_id = admission.node_id
          AND intent.adapter_id = 'fwi.deepwave_adapter'
          AND intent.adapter_version IN ('1.4.0', '1.5.0', '1.6.0')
          AND json_extract(intent.request_json, '$.request.algorithm.id')
              = 'deepwave.acoustic_fwi'
          AND json_extract(intent.request_json, '$.request.algorithm.version')
              = intent.adapter_version
          AND task.project_id = NEW.project_id
          AND task.principal_id = NEW.principal_id
          AND task.status = 'Queued'
          AND plan.plan_id = admission.plan_id
          AND plan.plan_hash = admission.plan_hash
          AND approval.approval_id = admission.approval_id
          AND approval.decision = 'approved'
          AND admission.project_id = NEW.project_id
          AND admission.principal_id = NEW.principal_id
          AND admission.max_node_attempts = 1
          AND observation.classification = 'exact_negative'
          AND observation.failure_code = 'DISPATCH_NOT_STARTED'
          AND observation.evidence_kind = NEW.evidence_kind
          AND observation.adapter_version = intent.adapter_version
          AND observation.source_outcome_hash = outcome.document_hash
          AND observation.fencing_token = NEW.fencing_token
          AND observation.observed_at_us <= NEW.resolved_at_us
          AND observation.observation_sequence = (
              SELECT MAX(latest.observation_sequence)
              FROM dispatch_reconciliation_observations AS latest
              WHERE latest.intent_id = intent.intent_id
          )
          AND budget.tasks_used = NEW.approval_tasks_used
          AND NEW.approval_budget_refunded = 0
          AND terminal_event.event_type = 'node_failed'
          AND terminal_event.task_status = 'Running'
          AND terminal_event.node_id = admission.node_id
          AND terminal_event.fingerprint_hash = intent.fingerprint_hash
          AND terminal_event.document_hash = NEW.terminal_event_hash
          AND terminal_event.sequence = (
              SELECT MAX(latest.sequence)
              FROM run_events AS latest
              WHERE latest.task_id = task.task_id
          )
          AND commit_record.project_id = task.project_id
          AND commit_record.principal_id = task.principal_id
          AND commit_record.fencing_token = NEW.fencing_token
          AND commit_record.recorded_at = NEW.resolved_at
          AND commit_record.recorded_at_us = NEW.resolved_at_us
          AND terminal.plan_id = admission.plan_id
          AND terminal.plan_hash = admission.plan_hash
          AND terminal.approval_id = admission.approval_id
          AND terminal.node_id = admission.node_id
          AND terminal.node_revision = admission.queued_revision + 1
          AND terminal.node_state = 'Failed'
          AND terminal.event_sequence = terminal_event.sequence
          AND terminal.event_hash = terminal_event.document_hash
          AND terminal.attempt_id IS NULL
          AND terminal.dispatch_outcome_document_hash = outcome.document_hash
          AND terminal.project_id = NEW.project_id
          AND terminal.principal_id = NEW.principal_id
          AND terminal.completion_fencing_token = NEW.fencing_token
          AND terminal.recorded_at = NEW.resolved_at
          AND terminal.recorded_at_us = NEW.resolved_at_us
          AND transition.previous_state = 'Queued'
          AND transition.state = 'Failed'
          AND transition.event_sequence = terminal_event.sequence
          AND transition.event_hash = terminal_event.document_hash
          AND transition.reason = 'dispatch_not_started'
          AND transition.project_id = NEW.project_id
          AND transition.principal_id = NEW.principal_id
          AND transition.fencing_token = NEW.fencing_token
          AND transition.recorded_at = NEW.resolved_at
          AND transition.recorded_at_us = NEW.resolved_at_us
          AND state.previous_state = 'Queued'
          AND state.recorded_at = NEW.resolved_at
          AND state.recorded_at_us = NEW.resolved_at_us
          AND NOT EXISTS (
              SELECT 1 FROM dispatch_reconciliation_resolutions AS positive
              WHERE positive.intent_id = outcome.intent_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM worker_retry_reservations AS retry
              WHERE retry.intent_id = outcome.intent_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM worker_exit_retry_reservations AS retry
              WHERE retry.intent_id = outcome.intent_id
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
)
BEGIN
    SELECT RAISE(ABORT, 'negative reconciliation requires exact no-start proof');
END;
