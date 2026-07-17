-- Persist every bounded reconciliation classification and close only an
-- exact, lock-fenced proof that no managed Worker reached ready.  The
-- original reconciliation_required outcome remains immutable.  A negative
-- resolution is terminal for the Task, but does not refund the already-used
-- Task admission budget and does not authorize another Worker attempt.

CREATE TABLE dispatch_reconciliation_observations (
    intent_id TEXT NOT NULL,
    observation_sequence INTEGER NOT NULL CHECK (
        typeof(observation_sequence) = 'integer'
        AND observation_sequence >= 1
    ),
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    source_outcome_hash TEXT NOT NULL CHECK (
        length(source_outcome_hash) = 71
        AND substr(source_outcome_hash, 1, 7) = 'sha256:'
    ),
    classification TEXT NOT NULL CHECK (
        classification IN ('transient', 'uncertain', 'exact_negative')
    ),
    failure_code TEXT NOT NULL,
    evidence_kind TEXT,
    attempt_id TEXT,
    attempt_number INTEGER,
    adapter_version TEXT,
    private_schema_version TEXT,
    private_record_hash TEXT,
    private_proof_hash TEXT,
    evidence_json TEXT,
    evidence_hash TEXT,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    observed_at TEXT NOT NULL,
    observed_at_us INTEGER NOT NULL CHECK (
        typeof(observed_at_us) = 'integer' AND observed_at_us >= 0
    ),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL CHECK (
        length(document_hash) = 71
        AND substr(document_hash, 1, 7) = 'sha256:'
    ),
    PRIMARY KEY (intent_id, observation_sequence),
    UNIQUE (intent_id, document_hash),
    CHECK (
        (classification IN ('transient', 'uncertain')
         AND evidence_kind IS NULL
         AND attempt_id IS NULL
         AND attempt_number IS NULL
         AND adapter_version IS NULL
         AND private_schema_version IS NULL
         AND private_record_hash IS NULL
         AND private_proof_hash IS NULL
         AND evidence_json IS NULL
         AND evidence_hash IS NULL)
        OR
        (classification = 'exact_negative'
         AND failure_code = 'DISPATCH_NOT_STARTED'
         AND evidence_kind = 'managed_pre_running_failure'
         AND attempt_id IS NOT NULL
         AND attempt_number = 1
         AND ((adapter_version = '1.4.0'
               AND private_schema_version = '1.1.0')
              OR
              (adapter_version = '1.5.0'
               AND private_schema_version = '1.2.0'))
         AND length(private_record_hash) = 71
         AND substr(private_record_hash, 1, 7) = 'sha256:'
         AND length(private_proof_hash) = 71
         AND substr(private_proof_hash, 1, 7) = 'sha256:'
         AND evidence_json IS NOT NULL
         AND length(evidence_hash) = 71
         AND substr(evidence_hash, 1, 7) = 'sha256:')
    ),
    FOREIGN KEY (intent_id) REFERENCES dispatch_outcomes(intent_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE dispatch_reconciliation_negative_resolutions (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    source_outcome_hash TEXT NOT NULL CHECK (
        length(source_outcome_hash) = 71
        AND substr(source_outcome_hash, 1, 7) = 'sha256:'
    ),
    result TEXT NOT NULL CHECK (result = 'not_dispatched'),
    evidence_kind TEXT NOT NULL CHECK (
        evidence_kind = 'managed_pre_running_failure'
    ),
    observation_sequence INTEGER NOT NULL CHECK (
        typeof(observation_sequence) = 'integer'
        AND observation_sequence >= 1
    ),
    terminal_event_sequence INTEGER NOT NULL CHECK (
        typeof(terminal_event_sequence) = 'integer'
        AND terminal_event_sequence >= 1
    ),
    terminal_event_hash TEXT NOT NULL CHECK (
        length(terminal_event_hash) = 71
        AND substr(terminal_event_hash, 1, 7) = 'sha256:'
    ),
    approval_tasks_used INTEGER NOT NULL CHECK (
        typeof(approval_tasks_used) = 'integer'
        AND approval_tasks_used >= 1
    ),
    approval_budget_refunded INTEGER NOT NULL CHECK (
        approval_budget_refunded = 0
    ),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL CHECK (
        length(document_hash) = 71
        AND substr(document_hash, 1, 7) = 'sha256:'
    ),
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    resolved_at TEXT NOT NULL,
    resolved_at_us INTEGER NOT NULL CHECK (
        typeof(resolved_at_us) = 'integer' AND resolved_at_us >= 0
    ),
    FOREIGN KEY (intent_id, observation_sequence)
        REFERENCES dispatch_reconciliation_observations(
            intent_id, observation_sequence
        ),
    FOREIGN KEY (intent_id) REFERENCES dispatch_outcomes(intent_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approval_budgets(task_id, approval_id),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES supervised_run_event_commits(task_id, sequence),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_dispatch_reconciliation_observations_scope
    ON dispatch_reconciliation_observations(
        project_id, principal_id, task_id, intent_id,
        observation_sequence
    );

CREATE INDEX idx_dispatch_reconciliation_negative_scope
    ON dispatch_reconciliation_negative_resolutions(
        project_id, principal_id, task_id, intent_id
    );

CREATE TRIGGER dispatch_reconciliation_observation_requires_exact_case
BEFORE INSERT ON dispatch_reconciliation_observations
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_outcomes AS outcome
    JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE outcome.intent_id = NEW.intent_id
      AND outcome.outcome = 'reconciliation_required'
      AND outcome.document_hash = NEW.source_outcome_hash
      AND intent.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Queued'
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_reconciliation_resolutions AS positive
          WHERE positive.intent_id = outcome.intent_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM dispatch_reconciliation_negative_resolutions AS negative
          WHERE negative.intent_id = outcome.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'reconciliation observation requires an unresolved case');
END;

CREATE TRIGGER dispatch_reconciliation_observation_requires_active_term
BEFORE INSERT ON dispatch_reconciliation_observations
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND lease.heartbeat_at_us <= NEW.observed_at_us
      AND lease.expires_at_us > NEW.observed_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'reconciliation observation requires the active term');
END;

CREATE TRIGGER dispatch_reconciliation_observation_sequence_is_contiguous
BEFORE INSERT ON dispatch_reconciliation_observations
WHEN NEW.observation_sequence != (
    SELECT COALESCE(MAX(observation_sequence), 0) + 1
    FROM dispatch_reconciliation_observations
    WHERE intent_id = NEW.intent_id
)
BEGIN
    SELECT RAISE(ABORT, 'reconciliation observation sequence must advance once');
END;

CREATE TRIGGER dispatch_reconciliation_negative_requires_exact_case
BEFORE INSERT ON dispatch_reconciliation_negative_resolutions
WHEN NOT EXISTS (
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
      AND intent.adapter_version IN ('1.4.0', '1.5.0')
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
BEGIN
    SELECT RAISE(ABORT, 'negative reconciliation requires exact no-start proof');
END;

CREATE TRIGGER dispatch_reconciliation_negative_requires_active_term
BEFORE INSERT ON dispatch_reconciliation_negative_resolutions
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND lease.heartbeat_at_us <= NEW.resolved_at_us
      AND lease.expires_at_us > NEW.resolved_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'negative reconciliation requires the active term');
END;

CREATE TRIGGER dispatch_reconciliation_observations_are_immutable
BEFORE UPDATE ON dispatch_reconciliation_observations
BEGIN
    SELECT RAISE(ABORT, 'reconciliation observations are immutable');
END;

CREATE TRIGGER dispatch_reconciliation_observations_cannot_be_deleted
BEFORE DELETE ON dispatch_reconciliation_observations
BEGIN
    SELECT RAISE(ABORT, 'reconciliation observations are immutable');
END;

CREATE TRIGGER dispatch_reconciliation_negative_resolutions_are_immutable
BEFORE UPDATE ON dispatch_reconciliation_negative_resolutions
BEGIN
    SELECT RAISE(ABORT, 'negative reconciliation resolutions are immutable');
END;

CREATE TRIGGER dispatch_reconciliation_negative_resolutions_cannot_be_deleted
BEFORE DELETE ON dispatch_reconciliation_negative_resolutions
BEGIN
    SELECT RAISE(ABORT, 'negative reconciliation resolutions are immutable');
END;

-- The negative resolution is resolved terminal provenance for Trash.  It is
-- intentionally not admitted to effective_dispatched_intents and therefore
-- does not make status, artifact, cancel, timeout, retry, or purge consumers
-- treat the Task as having a Worker receipt.
DROP TRIGGER task_visibility_trash_requires_resolved_terminal;

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
              JOIN effective_dispatched_intents AS outcome
                ON outcome.intent_id = intent.intent_id
              WHERE intent.task_id = tasks.task_id
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN worker_retry_exhaustions AS exhaustion
                ON exhaustion.intent_id = intent.intent_id
               AND exhaustion.attempt_number = 2
              JOIN run_events AS terminal
                ON terminal.task_id = exhaustion.task_id
               AND terminal.sequence = exhaustion.terminal_event_sequence
              WHERE intent.task_id = tasks.task_id
                AND tasks.status = 'Failed'
                AND exhaustion.task_id = tasks.task_id
                AND exhaustion.project_id = tasks.project_id
                AND exhaustion.principal_id = tasks.principal_id
                AND terminal.event_type = 'node_failed'
                AND terminal.task_status = 'Failed'
                AND terminal.document_hash = exhaustion.terminal_event_hash
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN worker_exit_retry_exhaustions AS exhaustion
                ON exhaustion.intent_id = intent.intent_id
               AND exhaustion.attempt_number = 2
              JOIN run_events AS terminal
                ON terminal.task_id = exhaustion.task_id
               AND terminal.sequence = exhaustion.terminal_event_sequence
              WHERE intent.task_id = tasks.task_id
                AND tasks.status = 'Failed'
                AND exhaustion.task_id = tasks.task_id
                AND exhaustion.project_id = tasks.project_id
                AND exhaustion.principal_id = tasks.principal_id
                AND terminal.event_type = 'node_failed'
                AND terminal.task_status = 'Failed'
                AND terminal.document_hash = exhaustion.terminal_event_hash
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN dispatch_reconciliation_negative_resolutions AS negative
                ON negative.intent_id = intent.intent_id
              JOIN run_events AS terminal
                ON terminal.task_id = negative.task_id
               AND terminal.sequence = negative.terminal_event_sequence
              WHERE intent.task_id = tasks.task_id
                AND tasks.status = 'Failed'
                AND negative.task_id = tasks.task_id
                AND negative.project_id = tasks.project_id
                AND negative.principal_id = tasks.principal_id
                AND terminal.event_type = 'node_failed'
                AND terminal.task_status = 'Failed'
                AND terminal.document_hash = negative.terminal_event_hash
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a resolved terminal task can be moved to trash');
END;
