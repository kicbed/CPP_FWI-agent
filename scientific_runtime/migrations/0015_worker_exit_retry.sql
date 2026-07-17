-- One finite post-ready Worker-exit retry for the current managed FWI
-- Adapter.  Every state transition is append-only: the original dispatch
-- receipt and timeout window remain immutable, while this migration projects
-- which receipt/window is currently effective.

CREATE TABLE worker_exit_retry_reservations (
    intent_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number = 2),
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    previous_attempt_id TEXT NOT NULL,
    previous_observation_sequence INTEGER NOT NULL CHECK (
        typeof(previous_observation_sequence) = 'integer'
        AND previous_observation_sequence >= 1
    ),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 71
        AND substr(evidence_hash, 1, 7) = 'sha256:'
    ),
    private_schema_version TEXT NOT NULL CHECK (
        private_schema_version IN ('1.1.0', '1.2.0')
    ),
    private_proof_hash TEXT NOT NULL CHECK (
        length(private_proof_hash) = 71
        AND substr(private_proof_hash, 1, 7) = 'sha256:'
    ),
    failure_kind TEXT NOT NULL CHECK (failure_kind = 'worker_exit'),
    source_outcome_document_hash TEXT NOT NULL CHECK (
        length(source_outcome_document_hash) = 71
        AND substr(source_outcome_document_hash, 1, 7) = 'sha256:'
    ),
    source_handle_hash TEXT NOT NULL CHECK (
        length(source_handle_hash) = 71
        AND substr(source_handle_hash, 1, 7) = 'sha256:'
    ),
    retry_event_sequence INTEGER NOT NULL CHECK (
        typeof(retry_event_sequence) = 'integer'
        AND retry_event_sequence >= 1
    ),
    retry_event_hash TEXT NOT NULL CHECK (
        length(retry_event_hash) = 71
        AND substr(retry_event_hash, 1, 7) = 'sha256:'
    ),
    first_fencing_token INTEGER NOT NULL CHECK (
        typeof(first_fencing_token) = 'integer'
        AND first_fencing_token >= 1
    ),
    reserved_at TEXT NOT NULL,
    reserved_at_us INTEGER NOT NULL CHECK (
        typeof(reserved_at_us) = 'integer' AND reserved_at_us >= 0
    ),
    PRIMARY KEY (intent_id, attempt_number),
    UNIQUE (previous_attempt_id, previous_observation_sequence),
    UNIQUE (task_id, retry_event_sequence),
    FOREIGN KEY (intent_id) REFERENCES dispatch_attempts(intent_id),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approval_retry_budgets(task_id, approval_id),
    FOREIGN KEY (previous_attempt_id, previous_observation_sequence)
        REFERENCES worker_attempt_observations(
            attempt_id, observation_sequence
        ),
    FOREIGN KEY (task_id, retry_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, retry_event_sequence)
        REFERENCES supervised_run_event_commits(task_id, sequence),
    FOREIGN KEY (project_id, principal_id, first_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE supervised_worker_exit_retry_attempts (
    intent_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number = 2),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    authorized_at TEXT NOT NULL,
    authorized_at_us INTEGER NOT NULL CHECK (
        typeof(authorized_at_us) = 'integer' AND authorized_at_us >= 0
    ),
    PRIMARY KEY (intent_id, attempt_number, fencing_token),
    FOREIGN KEY (intent_id, attempt_number)
        REFERENCES worker_exit_retry_reservations(intent_id, attempt_number),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE worker_exit_retry_timeout_retirements (
    timeout_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number = 2),
    attempt_id TEXT NOT NULL UNIQUE,
    timeout_window_hash TEXT NOT NULL CHECK (
        length(timeout_window_hash) = 71
        AND substr(timeout_window_hash, 1, 7) = 'sha256:'
    ),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    retired_at TEXT NOT NULL,
    retired_at_us INTEGER NOT NULL CHECK (
        typeof(retired_at_us) = 'integer' AND retired_at_us >= 0
    ),
    UNIQUE (intent_id, attempt_number),
    FOREIGN KEY (intent_id, attempt_number)
        REFERENCES worker_exit_retry_reservations(intent_id, attempt_number),
    FOREIGN KEY (timeout_id, intent_id, attempt_id)
        REFERENCES worker_attempt_timeout_windows(
            timeout_id, intent_id, attempt_id
        ),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE worker_exit_retry_dispatch_replacements (
    intent_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number = 2),
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    source_outcome_document_hash TEXT NOT NULL CHECK (
        length(source_outcome_document_hash) = 71
        AND substr(source_outcome_document_hash, 1, 7) = 'sha256:'
    ),
    source_handle_hash TEXT NOT NULL CHECK (
        length(source_handle_hash) = 71
        AND substr(source_handle_hash, 1, 7) = 'sha256:'
    ),
    attempt_id TEXT NOT NULL,
    observation_sequence INTEGER NOT NULL CHECK (
        typeof(observation_sequence) = 'integer'
        AND observation_sequence >= 1
    ),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 71
        AND substr(evidence_hash, 1, 7) = 'sha256:'
    ),
    handle_json TEXT NOT NULL,
    handle_hash TEXT NOT NULL CHECK (
        length(handle_hash) = 71
        AND substr(handle_hash, 1, 7) = 'sha256:'
    ),
    effective_outcome_json TEXT NOT NULL,
    effective_outcome_hash TEXT NOT NULL CHECK (
        length(effective_outcome_hash) = 71
        AND substr(effective_outcome_hash, 1, 7) = 'sha256:'
    ),
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    replaced_at TEXT NOT NULL,
    replaced_at_us INTEGER NOT NULL CHECK (
        typeof(replaced_at_us) = 'integer' AND replaced_at_us >= 0
    ),
    PRIMARY KEY (intent_id, attempt_number),
    UNIQUE (attempt_id, observation_sequence),
    FOREIGN KEY (intent_id, attempt_number)
        REFERENCES worker_exit_retry_reservations(intent_id, attempt_number),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approval_retry_budgets(task_id, approval_id),
    FOREIGN KEY (attempt_id, observation_sequence)
        REFERENCES worker_attempt_observations(
            attempt_id, observation_sequence
        ),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE worker_exit_retry_exhaustions (
    intent_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number = 2),
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    observation_sequence INTEGER NOT NULL CHECK (
        typeof(observation_sequence) = 'integer'
        AND observation_sequence >= 1
    ),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 71
        AND substr(evidence_hash, 1, 7) = 'sha256:'
    ),
    private_schema_version TEXT NOT NULL CHECK (
        private_schema_version = '1.3.0'
    ),
    private_proof_hash TEXT NOT NULL CHECK (
        length(private_proof_hash) = 71
        AND substr(private_proof_hash, 1, 7) = 'sha256:'
    ),
    failure_kind TEXT NOT NULL CHECK (
        failure_kind IN ('pre_running_launch_failure', 'worker_exit')
    ),
    max_attempts INTEGER NOT NULL CHECK (max_attempts = 2),
    terminal_event_sequence INTEGER NOT NULL CHECK (
        typeof(terminal_event_sequence) = 'integer'
        AND terminal_event_sequence >= 1
    ),
    terminal_event_hash TEXT NOT NULL CHECK (
        length(terminal_event_hash) = 71
        AND substr(terminal_event_hash, 1, 7) = 'sha256:'
    ),
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    exhausted_at TEXT NOT NULL,
    exhausted_at_us INTEGER NOT NULL CHECK (
        typeof(exhausted_at_us) = 'integer' AND exhausted_at_us >= 0
    ),
    PRIMARY KEY (intent_id, attempt_number),
    UNIQUE (attempt_id, observation_sequence),
    UNIQUE (task_id, terminal_event_sequence),
    FOREIGN KEY (intent_id, attempt_number)
        REFERENCES worker_exit_retry_reservations(intent_id, attempt_number),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approval_retry_budgets(task_id, approval_id),
    FOREIGN KEY (attempt_id, observation_sequence)
        REFERENCES worker_attempt_observations(
            attempt_id, observation_sequence
        ),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES supervised_run_event_commits(task_id, sequence),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_worker_exit_retry_reservations_scope
    ON worker_exit_retry_reservations(
        project_id, principal_id, task_id, intent_id
    );

CREATE INDEX idx_supervised_worker_exit_retry_attempts_term
    ON supervised_worker_exit_retry_attempts(
        project_id, principal_id, fencing_token, intent_id
    );

CREATE INDEX idx_worker_exit_retry_replacements_scope
    ON worker_exit_retry_dispatch_replacements(
        project_id, principal_id, task_id, intent_id
    );

CREATE INDEX idx_worker_exit_retry_exhaustions_scope
    ON worker_exit_retry_exhaustions(
        project_id, principal_id, task_id, intent_id
    );

CREATE TRIGGER worker_exit_retry_reservation_requires_exact_case
BEFORE INSERT ON worker_exit_retry_reservations
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = intent.approval_id
    JOIN approval_retry_budgets AS budget
      ON budget.task_id = task.task_id
     AND budget.approval_id = intent.approval_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.attempt_id = NEW.previous_attempt_id
     AND attempt.intent_id = intent.intent_id
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
     AND observation.observation_sequence
         = NEW.previous_observation_sequence
    JOIN effective_dispatched_intents AS source
      ON source.intent_id = intent.intent_id
    JOIN run_events AS event
      ON event.task_id = task.task_id
     AND event.sequence = NEW.retry_event_sequence
    JOIN supervised_run_event_commits AS commit_record
      ON commit_record.task_id = event.task_id
     AND commit_record.sequence = event.sequence
    WHERE intent.intent_id = NEW.intent_id
      AND intent.task_id = NEW.task_id
      AND intent.approval_id = NEW.approval_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version = '1.5.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.5.0'
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Retrying'
      AND approval.decision = 'approved'
      AND json_extract(approval.document_json, '$.schema_version') = '1.1.0'
      AND budget.max_attempts = 2
      AND budget.max_concurrent_attempts = 1
      AND budget.retryable_failure_classes_json
          = '["pre_running_launch_failure","worker_exit"]'
      AND attempt.attempt_number = 1
      AND attempt.attempt_number = (
          SELECT MAX(latest_attempt.attempt_number)
          FROM worker_launch_attempts AS latest_attempt
          WHERE latest_attempt.intent_id = intent.intent_id
      )
      AND observation.document_hash = NEW.evidence_hash
      AND observation.ticket_state = 'spawned'
      AND observation.ticket_worker_pid IS NOT NULL
      AND observation.ready_record_hash IS NOT NULL
      AND observation.heartbeat_state = 'running'
      AND observation.heartbeat_record_hash IS NOT NULL
      AND observation.observation_sequence = (
          SELECT MAX(latest_observation.observation_sequence)
          FROM worker_attempt_observations AS latest_observation
          WHERE latest_observation.attempt_id = attempt.attempt_id
      )
      AND source.outcome_document_hash = NEW.source_outcome_document_hash
      AND json_extract(
          source.outcome_document_json, '$.status'
      ) = 'dispatched'
      AND json_extract(
          source.outcome_document_json, '$.handle.submission_id'
      ) = attempt.submission_id
      AND json_extract(
          source.outcome_document_json, '$.handle.job_id'
      ) = attempt.job_id
      AND json_extract(
          source.outcome_document_json, '$.handle.request_hash'
      ) = attempt.adapter_request_hash
      AND event.event_type = 'node_retrying'
      AND event.task_status = 'Retrying'
      AND event.node_id = intent.node_id
      AND event.fingerprint_hash = intent.fingerprint_hash
      AND event.document_hash = NEW.retry_event_hash
      AND event.sequence = (
          SELECT MAX(latest_event.sequence)
          FROM run_events AS latest_event
          WHERE latest_event.task_id = task.task_id
      )
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".intent_id'
      ) = NEW.intent_id
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".attempt_number'
      ) = NEW.attempt_number
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".previous_attempt_id'
      ) = NEW.previous_attempt_id
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".previous_observation_sequence'
      ) = NEW.previous_observation_sequence
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".evidence_hash'
      ) = NEW.evidence_hash
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".private_schema_version'
      ) = NEW.private_schema_version
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".private_proof_hash'
      ) = NEW.private_proof_hash
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".failure_kind'
      ) = NEW.failure_kind
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".max_attempts'
      ) = 2
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".source_outcome_document_hash'
      ) = NEW.source_outcome_document_hash
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.worker_exit_retry".source_handle_hash'
      ) = NEW.source_handle_hash
      AND event.recorded_at = NEW.reserved_at
      AND commit_record.project_id = NEW.project_id
      AND commit_record.principal_id = NEW.principal_id
      AND commit_record.fencing_token = NEW.first_fencing_token
      AND commit_record.recorded_at = NEW.reserved_at
      AND commit_record.recorded_at_us = NEW.reserved_at_us
      AND observation.observed_at_us <= NEW.reserved_at_us
      AND NOT EXISTS (
          SELECT 1 FROM worker_launch_attempts AS successor
          WHERE successor.intent_id = intent.intent_id
            AND successor.attempt_number >= 2
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_retry_reservations AS pre_ready_retry
          WHERE pre_ready_retry.intent_id = intent.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS timeout_window
          JOIN supervised_timeout_attempts AS delivery
            ON delivery.timeout_id = timeout_window.timeout_id
          WHERE timeout_window.attempt_id = attempt.attempt_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS timeout_outcome
          WHERE timeout_outcome.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS elapsed_window
          WHERE elapsed_window.attempt_id = attempt.attempt_id
            AND elapsed_window.deadline_at_us <= NEW.reserved_at_us
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'worker-exit retry requires exact stopped attempt 1 evidence'
    );
END;

CREATE TRIGGER worker_exit_retry_reservation_requires_active_term
BEFORE INSERT ON worker_exit_retry_reservations
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.first_fencing_token
      AND lease.heartbeat_at_us <= NEW.reserved_at_us
      AND lease.expires_at_us > NEW.reserved_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'worker-exit retry requires the active term');
END;

CREATE TRIGGER worker_exit_retry_timeout_retirement_requires_exact_window
BEFORE INSERT ON worker_exit_retry_timeout_retirements
WHEN NOT EXISTS (
    SELECT 1
    FROM worker_exit_retry_reservations AS retry
    JOIN worker_attempt_timeout_windows AS timeout_window
      ON timeout_window.timeout_id = NEW.timeout_id
     AND timeout_window.intent_id = retry.intent_id
     AND timeout_window.attempt_id = retry.previous_attempt_id
    WHERE retry.intent_id = NEW.intent_id
      AND retry.attempt_number = NEW.attempt_number
      AND retry.project_id = NEW.project_id
      AND retry.principal_id = NEW.principal_id
      AND retry.first_fencing_token = NEW.fencing_token
      AND retry.previous_attempt_id = NEW.attempt_id
      AND timeout_window.document_hash = NEW.timeout_window_hash
      AND timeout_window.deadline_at_us > retry.reserved_at_us
      AND NEW.retired_at = retry.reserved_at
      AND NEW.retired_at_us = retry.reserved_at_us
      AND NOT EXISTS (
          SELECT 1 FROM supervised_timeout_attempts AS delivery
          WHERE delivery.timeout_id = timeout_window.timeout_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS outcome
          WHERE outcome.timeout_id = timeout_window.timeout_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'timeout retirement requires an exact live window');
END;

CREATE TRIGGER worker_exit_retry_reservation_retires_timeout
AFTER INSERT ON worker_exit_retry_reservations
BEGIN
    INSERT INTO worker_exit_retry_timeout_retirements(
        timeout_id, intent_id, attempt_number, attempt_id,
        timeout_window_hash, project_id, principal_id, fencing_token,
        retired_at, retired_at_us
    )
    SELECT timeout_window.timeout_id,
           NEW.intent_id,
           NEW.attempt_number,
           NEW.previous_attempt_id,
           timeout_window.document_hash,
           NEW.project_id,
           NEW.principal_id,
           NEW.first_fencing_token,
           NEW.reserved_at,
           NEW.reserved_at_us
    FROM worker_attempt_timeout_windows AS timeout_window
    WHERE timeout_window.intent_id = NEW.intent_id
      AND timeout_window.attempt_id = NEW.previous_attempt_id
      AND timeout_window.deadline_at_us > NEW.reserved_at_us
      AND NOT EXISTS (
          SELECT 1 FROM supervised_timeout_attempts AS delivery
          WHERE delivery.timeout_id = timeout_window.timeout_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS outcome
          WHERE outcome.timeout_id = timeout_window.timeout_id
      );
END;

CREATE TRIGGER supervised_worker_exit_retry_attempt_requires_active_term
BEFORE INSERT ON supervised_worker_exit_retry_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN worker_exit_retry_reservations AS retry
      ON retry.intent_id = NEW.intent_id
     AND retry.attempt_number = NEW.attempt_number
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND retry.project_id = NEW.project_id
      AND retry.principal_id = NEW.principal_id
      AND NEW.authorized_at_us >= retry.reserved_at_us
      AND lease.heartbeat_at_us <= NEW.authorized_at_us
      AND lease.expires_at_us > NEW.authorized_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = retry.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS timeout_outcome
          WHERE timeout_outcome.task_id = retry.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = retry.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'worker-exit retry delivery requires the active term');
END;

-- Attempt 2 may now be authorized by either of the two accepted, mutually
-- exclusive retry reservations.  The existing attempt-3 hard stop remains in
-- force and is intentionally not replaced here.
DROP TRIGGER worker_launch_attempt_requires_retry_reservation;

CREATE TRIGGER worker_launch_attempt_requires_retry_reservation
BEFORE INSERT ON worker_launch_attempts
WHEN NEW.attempt_number > 1
 AND NOT EXISTS (
     SELECT 1
     FROM worker_retry_reservations AS retry
     JOIN worker_launch_attempts AS prior
       ON prior.attempt_id = retry.previous_attempt_id
      AND prior.intent_id = retry.intent_id
     WHERE retry.intent_id = NEW.intent_id
       AND retry.attempt_number = NEW.attempt_number
       AND retry.task_id = NEW.task_id
       AND retry.project_id = NEW.project_id
       AND retry.principal_id = NEW.principal_id
       AND prior.attempt_number = 1
       AND prior.submission_id = NEW.submission_id
       AND prior.adapter_request_hash = NEW.adapter_request_hash
       AND NEW.created_at = retry.reserved_at
       AND NEW.job_id != prior.job_id
       AND EXISTS (
           SELECT 1 FROM supervised_retry_attempts AS delivery
           WHERE delivery.intent_id = NEW.intent_id
             AND delivery.attempt_number = NEW.attempt_number
             AND delivery.project_id = NEW.project_id
             AND delivery.principal_id = NEW.principal_id
             AND delivery.fencing_token = retry.first_fencing_token
             AND delivery.authorized_at_us <= NEW.first_observed_at_us
       )
 )
 AND NOT EXISTS (
     SELECT 1
     FROM worker_exit_retry_reservations AS retry
     JOIN worker_launch_attempts AS prior
       ON prior.attempt_id = retry.previous_attempt_id
      AND prior.intent_id = retry.intent_id
     WHERE retry.intent_id = NEW.intent_id
       AND retry.attempt_number = NEW.attempt_number
       AND retry.task_id = NEW.task_id
       AND retry.project_id = NEW.project_id
       AND retry.principal_id = NEW.principal_id
       AND prior.attempt_number = 1
       AND prior.submission_id = NEW.submission_id
       AND prior.adapter_request_hash = NEW.adapter_request_hash
       AND NEW.created_at = retry.reserved_at
       AND NEW.job_id != prior.job_id
       AND EXISTS (
           SELECT 1
           FROM supervised_worker_exit_retry_attempts AS delivery
           WHERE delivery.intent_id = NEW.intent_id
             AND delivery.attempt_number = NEW.attempt_number
             AND delivery.project_id = NEW.project_id
             AND delivery.principal_id = NEW.principal_id
             AND delivery.fencing_token = retry.first_fencing_token
             AND delivery.authorized_at_us <= NEW.first_observed_at_us
       )
 )
BEGIN
    SELECT RAISE(ABORT, 'retry attempt requires its durable reservation');
END;

CREATE TRIGGER worker_exit_retry_replacement_requires_exact_case
BEFORE INSERT ON worker_exit_retry_dispatch_replacements
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    JOIN approval_retry_budgets AS budget
      ON budget.task_id = task.task_id
     AND budget.approval_id = intent.approval_id
    JOIN worker_exit_retry_reservations AS retry
      ON retry.intent_id = intent.intent_id
     AND retry.attempt_number = NEW.attempt_number
    JOIN worker_launch_attempts AS prior_attempt
      ON prior_attempt.attempt_id = retry.previous_attempt_id
     AND prior_attempt.intent_id = intent.intent_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.attempt_id = NEW.attempt_id
     AND attempt.intent_id = intent.intent_id
     AND attempt.attempt_number = NEW.attempt_number
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
     AND observation.observation_sequence = NEW.observation_sequence
    WHERE intent.intent_id = NEW.intent_id
      AND intent.task_id = NEW.task_id
      AND intent.approval_id = NEW.approval_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version = '1.5.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.5.0'
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Retrying'
      AND budget.max_attempts = 2
      AND budget.max_concurrent_attempts = 1
      AND budget.retryable_failure_classes_json
          = '["pre_running_launch_failure","worker_exit"]'
      AND retry.task_id = NEW.task_id
      AND retry.project_id = NEW.project_id
      AND retry.principal_id = NEW.principal_id
      AND retry.approval_id = NEW.approval_id
      AND retry.source_outcome_document_hash
          = NEW.source_outcome_document_hash
      AND retry.source_handle_hash = NEW.source_handle_hash
      AND prior_attempt.attempt_number = 1
      AND prior_attempt.submission_id = attempt.submission_id
      AND prior_attempt.adapter_request_hash = attempt.adapter_request_hash
      AND prior_attempt.job_id <> attempt.job_id
      AND attempt.created_at = retry.reserved_at
      AND attempt.attempt_number = (
          SELECT MAX(latest_attempt.attempt_number)
          FROM worker_launch_attempts AS latest_attempt
          WHERE latest_attempt.intent_id = intent.intent_id
      )
      AND observation.document_hash = NEW.evidence_hash
      AND observation.observation_sequence = (
          SELECT MAX(latest_observation.observation_sequence)
          FROM worker_attempt_observations AS latest_observation
          WHERE latest_observation.attempt_id = attempt.attempt_id
      )
      AND observation.ticket_state = 'spawned'
      AND observation.ticket_worker_pid IS NOT NULL
      AND observation.ready_record_hash IS NOT NULL
      AND observation.heartbeat_state IN ('running', 'succeeded', 'failed')
      AND observation.heartbeat_record_hash IS NOT NULL
      AND json_valid(NEW.handle_json)
      AND json_type(NEW.handle_json, '$') = 'object'
      AND json_extract(NEW.handle_json, '$.submission_id')
          = attempt.submission_id
      AND json_extract(NEW.handle_json, '$.job_id') = attempt.job_id
      AND json_extract(NEW.handle_json, '$.request_hash')
          = attempt.adapter_request_hash
      AND json_extract(NEW.handle_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.handle_json, '$.node_id') = intent.node_id
      AND json_extract(NEW.handle_json, '$.adapter_version') = '1.5.0'
      AND json_valid(NEW.effective_outcome_json)
      AND json_type(NEW.effective_outcome_json, '$') = 'object'
      AND json_extract(NEW.effective_outcome_json, '$.status') = 'dispatched'
      AND json_extract(NEW.effective_outcome_json, '$.recorded_at')
          = NEW.replaced_at
      AND json(json_extract(NEW.effective_outcome_json, '$.handle'))
          = json(NEW.handle_json)
      AND (
          SELECT COUNT(*) FROM json_each(NEW.effective_outcome_json)
      ) = 3
      AND EXISTS (
          SELECT 1
          FROM supervised_worker_exit_retry_attempts AS delivery
          WHERE delivery.intent_id = retry.intent_id
            AND delivery.attempt_number = retry.attempt_number
            AND delivery.authorized_at_us <= attempt.first_observed_at_us
      )
      AND observation.observed_at_us <= NEW.replaced_at_us
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS timeout_outcome
          WHERE timeout_outcome.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_exit_retry_exhaustions AS exhaustion
          WHERE exhaustion.intent_id = intent.intent_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'dispatch replacement requires exact attempt 2 proof');
END;

CREATE TRIGGER worker_exit_retry_replacement_requires_active_term
BEFORE INSERT ON worker_exit_retry_dispatch_replacements
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND lease.heartbeat_at_us <= NEW.replaced_at_us
      AND lease.expires_at_us > NEW.replaced_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'dispatch replacement requires the active term');
END;

-- Reservation immediately retires the old effective handle.  Only a proved
-- attempt-2 replacement becomes visible again; downstream cancel, timeout,
-- trash, and purge readers therefore cannot act on attempt 1 in the gap.
DROP VIEW effective_dispatched_intents;

CREATE VIEW effective_dispatched_intents AS
SELECT outcome.intent_id,
       outcome.document_json AS outcome_document_json,
       outcome.document_hash AS outcome_document_hash,
       outcome.recorded_at,
       'direct' AS source
FROM dispatch_outcomes AS outcome
WHERE outcome.outcome = 'dispatched'
  AND NOT EXISTS (
      SELECT 1 FROM worker_exit_retry_reservations AS retry
      WHERE retry.intent_id = outcome.intent_id
  )
UNION ALL
SELECT resolution.intent_id,
       resolution.effective_outcome_json AS outcome_document_json,
       resolution.effective_outcome_hash AS outcome_document_hash,
       resolution.resolved_at AS recorded_at,
       'reconciliation' AS source
FROM dispatch_reconciliation_resolutions AS resolution
WHERE resolution.result = 'dispatched'
  AND NOT EXISTS (
      SELECT 1 FROM worker_exit_retry_reservations AS retry
      WHERE retry.intent_id = resolution.intent_id
  )
UNION ALL
SELECT replacement.intent_id,
       replacement.effective_outcome_json AS outcome_document_json,
       replacement.effective_outcome_hash AS outcome_document_hash,
       replacement.replaced_at AS recorded_at,
       'worker_exit_retry_replacement' AS source
FROM worker_exit_retry_dispatch_replacements AS replacement;

CREATE TRIGGER worker_exit_retry_exhaustion_requires_exact_case
BEFORE INSERT ON worker_exit_retry_exhaustions
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    JOIN approval_retry_budgets AS budget
      ON budget.task_id = task.task_id
     AND budget.approval_id = intent.approval_id
    JOIN worker_exit_retry_reservations AS retry
      ON retry.intent_id = intent.intent_id
     AND retry.attempt_number = NEW.attempt_number
    JOIN worker_launch_attempts AS prior_attempt
      ON prior_attempt.attempt_id = retry.previous_attempt_id
     AND prior_attempt.intent_id = intent.intent_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.attempt_id = NEW.attempt_id
     AND attempt.intent_id = intent.intent_id
     AND attempt.attempt_number = NEW.attempt_number
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
     AND observation.observation_sequence = NEW.observation_sequence
    JOIN run_events AS event
      ON event.task_id = task.task_id
     AND event.sequence = NEW.terminal_event_sequence
    JOIN supervised_run_event_commits AS commit_record
      ON commit_record.task_id = event.task_id
     AND commit_record.sequence = event.sequence
    JOIN runtime_supervisor_leases AS lease
      ON lease.project_id = NEW.project_id
     AND lease.principal_id = NEW.principal_id
     AND lease.fencing_token = NEW.fencing_token
    WHERE intent.intent_id = NEW.intent_id
      AND intent.task_id = NEW.task_id
      AND intent.approval_id = NEW.approval_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version = '1.5.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.5.0'
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Failed'
      AND budget.max_attempts = NEW.max_attempts
      AND budget.max_attempts = 2
      AND budget.max_concurrent_attempts = 1
      AND budget.retryable_failure_classes_json
          = '["pre_running_launch_failure","worker_exit"]'
      AND retry.task_id = NEW.task_id
      AND retry.project_id = NEW.project_id
      AND retry.principal_id = NEW.principal_id
      AND retry.approval_id = NEW.approval_id
      AND prior_attempt.attempt_number = 1
      AND prior_attempt.submission_id = attempt.submission_id
      AND prior_attempt.adapter_request_hash = attempt.adapter_request_hash
      AND prior_attempt.job_id <> attempt.job_id
      AND attempt.created_at = retry.reserved_at
      AND attempt.attempt_number = (
          SELECT MAX(latest_attempt.attempt_number)
          FROM worker_launch_attempts AS latest_attempt
          WHERE latest_attempt.intent_id = intent.intent_id
      )
      AND observation.document_hash = NEW.evidence_hash
      AND observation.observation_sequence = (
          SELECT MAX(latest_observation.observation_sequence)
          FROM worker_attempt_observations AS latest_observation
          WHERE latest_observation.attempt_id = attempt.attempt_id
      )
      AND NEW.private_schema_version = '1.3.0'
      AND (
          (
              NEW.failure_kind = 'pre_running_launch_failure'
              AND observation.ticket_state = 'failed'
              AND observation.ticket_worker_pid IS NULL
              AND observation.ready_record_hash IS NULL
              AND observation.heartbeat_record_hash IS NULL
              AND event.occurred_at = json_extract(
                  observation.document_json, '$.ticket.updated_at'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM worker_exit_retry_dispatch_replacements AS replacement
                  WHERE replacement.intent_id = intent.intent_id
              )
          )
          OR
          (
              NEW.failure_kind = 'worker_exit'
              AND observation.ticket_state = 'spawned'
              AND observation.ticket_worker_pid IS NOT NULL
              AND observation.ready_record_hash IS NOT NULL
              AND observation.heartbeat_state = 'running'
              AND observation.heartbeat_record_hash IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM worker_exit_retry_dispatch_replacements AS replacement
                  WHERE replacement.intent_id = intent.intent_id
                    AND replacement.attempt_number = NEW.attempt_number
                    AND replacement.attempt_id = NEW.attempt_id
              )
          )
      )
      AND event.event_type = 'node_failed'
      AND event.task_status = 'Failed'
      AND event.node_id = intent.node_id
      AND event.fingerprint_hash = intent.fingerprint_hash
      AND event.document_hash = NEW.terminal_event_hash
      AND json_extract(event.document_json, '$.error.code')
          = 'retry_exhausted'
      AND json_type(event.document_json, '$.error.retryable') = 'false'
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".intent_id'
      ) = NEW.intent_id
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".attempt_id'
      ) = NEW.attempt_id
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".attempt_number'
      ) = NEW.attempt_number
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".observation_sequence'
      ) = NEW.observation_sequence
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".evidence_hash'
      ) = NEW.evidence_hash
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".private_schema_version'
      ) = NEW.private_schema_version
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".private_proof_hash'
      ) = NEW.private_proof_hash
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".failure_kind'
      ) = NEW.failure_kind
      AND json_extract(
          event.document_json,
          '$.extensions."org.agent_rpc.retry_exhaustion".max_attempts'
      ) = NEW.max_attempts
      AND event.sequence = (
          SELECT MAX(latest_event.sequence)
          FROM run_events AS latest_event
          WHERE latest_event.task_id = task.task_id
      )
      AND event.recorded_at = NEW.exhausted_at
      AND commit_record.project_id = NEW.project_id
      AND commit_record.principal_id = NEW.principal_id
      AND commit_record.fencing_token = NEW.fencing_token
      AND commit_record.recorded_at = NEW.exhausted_at
      AND commit_record.recorded_at_us = NEW.exhausted_at_us
      AND observation.observed_at_us <= NEW.exhausted_at_us
      AND lease.heartbeat_at_us <= NEW.exhausted_at_us
      AND lease.expires_at_us > NEW.exhausted_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS timeout_window
          JOIN supervised_timeout_attempts AS delivery
            ON delivery.timeout_id = timeout_window.timeout_id
          WHERE timeout_window.attempt_id = attempt.attempt_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS timeout_outcome
          WHERE timeout_outcome.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'retry exhaustion requires exact attempt 2 proof');
END;

CREATE TRIGGER worker_exit_retry_reservations_are_immutable
BEFORE UPDATE ON worker_exit_retry_reservations
BEGIN SELECT RAISE(ABORT, 'worker-exit retry reservations are immutable'); END;

CREATE TRIGGER worker_exit_retry_reservations_cannot_be_deleted
BEFORE DELETE ON worker_exit_retry_reservations
BEGIN SELECT RAISE(ABORT, 'worker-exit retry reservations are immutable'); END;

CREATE TRIGGER supervised_worker_exit_retry_attempts_are_immutable
BEFORE UPDATE ON supervised_worker_exit_retry_attempts
BEGIN SELECT RAISE(ABORT, 'worker-exit retry attempts are immutable'); END;

CREATE TRIGGER supervised_worker_exit_retry_attempts_cannot_be_deleted
BEFORE DELETE ON supervised_worker_exit_retry_attempts
BEGIN SELECT RAISE(ABORT, 'worker-exit retry attempts are immutable'); END;

CREATE TRIGGER worker_exit_retry_timeout_retirements_are_immutable
BEFORE UPDATE ON worker_exit_retry_timeout_retirements
BEGIN SELECT RAISE(ABORT, 'timeout retirements are immutable'); END;

CREATE TRIGGER worker_exit_retry_timeout_retirements_cannot_be_deleted
BEFORE DELETE ON worker_exit_retry_timeout_retirements
BEGIN SELECT RAISE(ABORT, 'timeout retirements are immutable'); END;

CREATE TRIGGER worker_exit_retry_dispatch_replacements_are_immutable
BEFORE UPDATE ON worker_exit_retry_dispatch_replacements
BEGIN SELECT RAISE(ABORT, 'dispatch replacements are immutable'); END;

CREATE TRIGGER worker_exit_retry_dispatch_replacements_cannot_be_deleted
BEFORE DELETE ON worker_exit_retry_dispatch_replacements
BEGIN SELECT RAISE(ABORT, 'dispatch replacements are immutable'); END;

CREATE TRIGGER worker_exit_retry_exhaustions_are_immutable
BEFORE UPDATE ON worker_exit_retry_exhaustions
BEGIN SELECT RAISE(ABORT, 'worker-exit retry exhaustions are immutable'); END;

CREATE TRIGGER worker_exit_retry_exhaustions_cannot_be_deleted
BEFORE DELETE ON worker_exit_retry_exhaustions
BEGIN SELECT RAISE(ABORT, 'worker-exit retry exhaustions are immutable'); END;

-- A B2 attempt-2 pre-ready exhaustion has no effective dispatch receipt, so
-- explicitly admit its exact terminal record to the existing trash lifecycle.
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
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a resolved terminal task can be moved to trash');
END;

-- For B2, the effective receipt must identify the same latest attempt that a
-- cancel or new timeout window targets.  Historical and B1 paths retain their
-- pre-v15 behavior because they have no B2 reservation.
DROP TRIGGER task_cancel_request_requires_exact_running_attempt;

CREATE TRIGGER task_cancel_request_requires_exact_running_attempt
BEFORE INSERT ON task_cancel_requests
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN dispatch_intents AS intent ON intent.task_id = task.task_id
    JOIN effective_dispatched_intents AS dispatch
      ON dispatch.intent_id = intent.intent_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.intent_id = intent.intent_id
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
    JOIN run_events AS event
      ON event.task_id = task.task_id
     AND event.sequence = NEW.request_event_sequence
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('Queued', 'Running')
      AND intent.intent_id = NEW.intent_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version IN ('1.4.0', '1.5.0')
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = intent.adapter_version
      AND attempt.attempt_id = NEW.attempt_id
      AND attempt.attempt_number = (
          SELECT MAX(latest_attempt.attempt_number)
          FROM worker_launch_attempts AS latest_attempt
          WHERE latest_attempt.intent_id = intent.intent_id
      )
      AND observation.observation_sequence = (
          SELECT MAX(latest.observation_sequence)
          FROM worker_attempt_observations AS latest
          WHERE latest.attempt_id = attempt.attempt_id
      )
      AND observation.ticket_state = 'spawned'
      AND observation.ready_record_hash IS NOT NULL
      AND observation.heartbeat_state = 'running'
      AND observation.heartbeat_record_hash IS NOT NULL
      AND event.sequence = (
          SELECT MAX(latest_event.sequence)
          FROM run_events AS latest_event
          WHERE latest_event.task_id = task.task_id
      )
      AND event.event_type = 'cancel_requested'
      AND event.task_status = task.status
      AND event.node_id = intent.node_id
      AND (
          NOT EXISTS (
              SELECT 1 FROM worker_exit_retry_reservations AS retry
              WHERE retry.intent_id = intent.intent_id
          )
          OR (
              json_extract(
                  dispatch.outcome_document_json, '$.handle.submission_id'
              ) = attempt.submission_id
              AND json_extract(
                  dispatch.outcome_document_json, '$.handle.job_id'
              ) = attempt.job_id
              AND json_extract(
                  dispatch.outcome_document_json, '$.handle.request_hash'
              ) = attempt.adapter_request_hash
          )
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'cancel request requires an exact running attempt');
END;

DROP TRIGGER worker_attempt_timeout_window_requires_exact_start;

CREATE TRIGGER worker_attempt_timeout_window_requires_exact_start
BEFORE INSERT ON worker_attempt_timeout_windows
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN dispatch_intents AS intent ON intent.task_id = task.task_id
    JOIN effective_dispatched_intents AS dispatch
      ON dispatch.intent_id = intent.intent_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.intent_id = intent.intent_id
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
     AND observation.observation_sequence = NEW.start_observation_sequence
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('Queued', 'Running', 'Retrying')
      AND intent.intent_id = NEW.intent_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version IN ('1.4.0', '1.5.0')
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = intent.adapter_version
      AND json_type(
          intent.request_json, '$.request.resources.wall_time_seconds'
      ) = 'integer'
      AND json_extract(
          intent.request_json, '$.request.resources.wall_time_seconds'
      ) = NEW.wall_time_seconds
      AND attempt.attempt_id = NEW.attempt_id
      AND attempt.attempt_number = (
          SELECT MAX(latest_attempt.attempt_number)
          FROM worker_launch_attempts AS latest_attempt
          WHERE latest_attempt.intent_id = intent.intent_id
      )
      AND observation.observation_sequence = (
          SELECT MIN(first_running.observation_sequence)
          FROM worker_attempt_observations AS first_running
          WHERE first_running.attempt_id = attempt.attempt_id
            AND first_running.ticket_state = 'spawned'
            AND first_running.ready_record_hash IS NOT NULL
            AND first_running.heartbeat_state = 'running'
            AND first_running.heartbeat_record_hash IS NOT NULL
      )
      AND observation.ticket_state = 'spawned'
      AND observation.ready_record_hash = NEW.ready_record_hash
      AND observation.heartbeat_state = 'running'
      AND observation.heartbeat_record_hash
          = NEW.running_heartbeat_record_hash
      AND observation.observed_at = NEW.started_at
      AND observation.observed_at_us = NEW.started_at_us
      AND NEW.recorded_at_us >= observation.observed_at_us
      AND NEW.deadline_at_us
          = observation.observed_at_us + NEW.wall_time_seconds * 1000000
      AND json_valid(NEW.capability_proof_json)
      AND json_type(NEW.capability_proof_json, '$') = 'object'
      AND json_extract(NEW.capability_proof_json, '$.schema_version')
          = '2.0.0'
      AND (
          (
              attempt.attempt_number = 1
              AND json_extract(
                  NEW.capability_proof_json, '$.private_schema_version'
              ) = '1.1.0'
          )
          OR (
              attempt.attempt_number = 2
              AND intent.adapter_version = '1.5.0'
              AND (
                  (
                      EXISTS (
                          SELECT 1 FROM worker_retry_reservations AS retry
                          WHERE retry.intent_id = intent.intent_id
                            AND retry.attempt_number = 2
                      )
                      AND json_extract(
                          NEW.capability_proof_json,
                          '$.private_schema_version'
                      ) = '1.2.0'
                  )
                  OR (
                      EXISTS (
                          SELECT 1
                          FROM worker_exit_retry_dispatch_replacements AS replacement
                          WHERE replacement.intent_id = intent.intent_id
                            AND replacement.attempt_number = 2
                            AND replacement.attempt_id = attempt.attempt_id
                      )
                      AND json_extract(
                          NEW.capability_proof_json,
                          '$.private_schema_version'
                      ) = '1.3.0'
                  )
              )
          )
      )
      AND json_extract(NEW.capability_proof_json, '$.attempt_id')
          = NEW.attempt_id
      AND json_extract(NEW.capability_proof_json, '$.binding_hash')
          = attempt.binding_hash
      AND json_extract(
          NEW.capability_proof_json, '$.capability_record_hash'
      ) = NEW.capability_record_hash
      AND json_extract(
          NEW.capability_proof_json, '$.supported_reasons[0]'
      ) = 'user_requested'
      AND json_extract(
          NEW.capability_proof_json, '$.supported_reasons[1]'
      ) = 'wall_time_exceeded'
      AND json_array_length(
          NEW.capability_proof_json, '$.supported_reasons'
      ) = 2
      AND (
          NOT EXISTS (
              SELECT 1 FROM worker_exit_retry_reservations AS retry
              WHERE retry.intent_id = intent.intent_id
          )
          OR (
              json_extract(
                  dispatch.outcome_document_json, '$.handle.submission_id'
              ) = attempt.submission_id
              AND json_extract(
                  dispatch.outcome_document_json, '$.handle.job_id'
              ) = attempt.job_id
              AND json_extract(
                  dispatch.outcome_document_json, '$.handle.request_hash'
              ) = attempt.adapter_request_hash
          )
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'timeout window requires exact first running evidence');
END;

-- A retired attempt-1 window can never regain delivery authority even if a
-- stale caller retained its timeout_id across the reservation transaction.
DROP TRIGGER supervised_timeout_attempt_requires_due_window;

CREATE TRIGGER supervised_timeout_attempt_requires_due_window
BEFORE INSERT ON supervised_timeout_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM worker_attempt_timeout_windows AS timeout
    JOIN tasks AS task ON task.task_id = timeout.task_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.attempt_id = timeout.attempt_id
    WHERE timeout.timeout_id = NEW.timeout_id
      AND timeout.project_id = NEW.project_id
      AND timeout.principal_id = NEW.principal_id
      AND timeout.intent_id = NEW.intent_id
      AND timeout.attempt_id = NEW.attempt_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('Queued', 'Running')
      AND attempt.attempt_number = (
          SELECT MAX(latest_attempt.attempt_number)
          FROM worker_launch_attempts AS latest_attempt
          WHERE latest_attempt.intent_id = timeout.intent_id
      )
      AND NEW.authorized_at_us >= timeout.deadline_at_us
      AND NOT EXISTS (
          SELECT 1 FROM worker_exit_retry_timeout_retirements AS retirement
          WHERE retirement.timeout_id = timeout.timeout_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_timeout_outcomes AS outcome
          WHERE outcome.timeout_id = timeout.timeout_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = timeout.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'supervised timeout requires a due pending window');
END;
