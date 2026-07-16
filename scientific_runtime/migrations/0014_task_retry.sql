-- Finite automatic retry budget and active-term authorization.
--
-- max_tasks remains a Task admission budget.  This separate immutable row
-- binds the number of Worker attempts approved for that Task.  Historical
-- ApprovalDecision 1.0 rows are deliberately backfilled as single-attempt.
CREATE TABLE approval_retry_budgets (
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    max_attempts INTEGER NOT NULL CHECK (max_attempts IN (1, 2)),
    max_concurrent_attempts INTEGER NOT NULL CHECK (max_concurrent_attempts = 1),
    max_cumulative_attempt_wall_time_seconds INTEGER NOT NULL CHECK (
        typeof(max_cumulative_attempt_wall_time_seconds) = 'integer'
        AND max_cumulative_attempt_wall_time_seconds >= 1
        AND max_cumulative_attempt_wall_time_seconds <= 172800
    ),
    retryable_failure_classes_json TEXT NOT NULL CHECK (
        retryable_failure_classes_json IN (
            '[]',
            '["pre_running_launch_failure","worker_exit"]'
        )
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, approval_id),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id)
);

INSERT INTO approval_retry_budgets(
    task_id, approval_id, max_attempts, max_concurrent_attempts,
    max_cumulative_attempt_wall_time_seconds,
    retryable_failure_classes_json, created_at
)
SELECT task_id,
       approval_id,
       CASE json_extract(document_json, '$.schema_version')
           WHEN '1.1.0' THEN 2 ELSE 1 END,
       1,
       CASE json_extract(document_json, '$.schema_version')
           WHEN '1.1.0' THEN
               json_extract(
                   document_json,
                   '$.scope.retry_policy.max_cumulative_attempt_wall_time_seconds'
               )
           ELSE json_extract(document_json, '$.scope.resource_limits.wall_time_seconds')
       END,
       CASE json_extract(document_json, '$.schema_version')
           WHEN '1.1.0' THEN '["pre_running_launch_failure","worker_exit"]'
           ELSE '[]'
       END,
       recorded_at
FROM approvals;

CREATE TRIGGER approvals_initialize_retry_budget
AFTER INSERT ON approvals
BEGIN
    INSERT INTO approval_retry_budgets(
        task_id, approval_id, max_attempts, max_concurrent_attempts,
        max_cumulative_attempt_wall_time_seconds,
        retryable_failure_classes_json, created_at
    ) VALUES (
        NEW.task_id,
        NEW.approval_id,
        CASE json_extract(NEW.document_json, '$.schema_version')
            WHEN '1.1.0' THEN 2
            WHEN '1.0.0' THEN 1
            ELSE NULL
        END,
        1,
        CASE json_extract(NEW.document_json, '$.schema_version')
            WHEN '1.1.0' THEN
                CASE
                    WHEN json_extract(
                        NEW.document_json,
                        '$.scope.retry_policy.max_attempts'
                    ) = 2
                    AND json_extract(
                        NEW.document_json,
                        '$.scope.retry_policy.max_concurrent_attempts'
                    ) = 1
                    AND json_extract(
                        NEW.document_json,
                        '$.scope.retry_policy.max_cumulative_attempt_wall_time_seconds'
                    ) = 2 * json_extract(
                        NEW.document_json,
                        '$.scope.resource_limits.wall_time_seconds'
                    )
                    AND json_array_length(
                        NEW.document_json,
                        '$.scope.retry_policy.retryable_failure_classes'
                    ) = 2
                    AND EXISTS (
                        SELECT 1 FROM json_each(
                            NEW.document_json,
                            '$.scope.retry_policy.retryable_failure_classes'
                        ) WHERE value = 'pre_running_launch_failure'
                    )
                    AND EXISTS (
                        SELECT 1 FROM json_each(
                            NEW.document_json,
                            '$.scope.retry_policy.retryable_failure_classes'
                        ) WHERE value = 'worker_exit'
                    )
                    THEN json_extract(
                        NEW.document_json,
                        '$.scope.retry_policy.max_cumulative_attempt_wall_time_seconds'
                    )
                    ELSE NULL
                END
            WHEN '1.0.0' THEN json_extract(
                NEW.document_json,
                '$.scope.resource_limits.wall_time_seconds'
            )
            ELSE NULL
        END,
        CASE json_extract(NEW.document_json, '$.schema_version')
            WHEN '1.1.0' THEN '["pre_running_launch_failure","worker_exit"]'
            WHEN '1.0.0' THEN '[]'
            ELSE NULL
        END,
        NEW.recorded_at
    );
END;

CREATE TRIGGER approval_retry_budgets_are_immutable
BEFORE UPDATE ON approval_retry_budgets
BEGIN
    SELECT RAISE(ABORT, 'approval retry budgets are immutable');
END;

CREATE TRIGGER approval_retry_budgets_cannot_be_deleted
BEFORE DELETE ON approval_retry_budgets
BEGIN
    SELECT RAISE(ABORT, 'approval retry budgets are immutable');
END;

-- One reservation consumes attempt 2 permanently.  New Supervisor terms may
-- append delivery attempts for the same reservation after a crash, but no row
-- can refund it or authorize attempt 3.
CREATE TABLE worker_retry_reservations (
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
        length(evidence_hash) = 71 AND substr(evidence_hash, 1, 7) = 'sha256:'
    ),
    private_proof_hash TEXT NOT NULL CHECK (
        length(private_proof_hash) = 71
        AND substr(private_proof_hash, 1, 7) = 'sha256:'
    ),
    failure_kind TEXT NOT NULL CHECK (failure_kind = 'pre_running_launch_failure'),
    first_fencing_token INTEGER NOT NULL CHECK (
        typeof(first_fencing_token) = 'integer' AND first_fencing_token >= 1
    ),
    reserved_at TEXT NOT NULL,
    reserved_at_us INTEGER NOT NULL CHECK (
        typeof(reserved_at_us) = 'integer' AND reserved_at_us >= 0
    ),
    PRIMARY KEY (intent_id, attempt_number),
    UNIQUE (previous_attempt_id, previous_observation_sequence),
    FOREIGN KEY (intent_id) REFERENCES dispatch_attempts(intent_id),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approval_retry_budgets(task_id, approval_id),
    FOREIGN KEY (previous_attempt_id, previous_observation_sequence)
        REFERENCES worker_attempt_observations(attempt_id, observation_sequence),
    FOREIGN KEY (project_id, principal_id, first_fencing_token)
        REFERENCES runtime_supervisor_terms(project_id, principal_id, fencing_token)
);

CREATE TABLE supervised_retry_attempts (
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
        REFERENCES worker_retry_reservations(intent_id, attempt_number),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(project_id, principal_id, fencing_token)
);

CREATE TRIGGER worker_retry_reservation_requires_exact_case
BEFORE INSERT ON worker_retry_reservations
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    JOIN approval_retry_budgets AS budget
      ON budget.task_id = task.task_id
     AND budget.approval_id = intent.approval_id
    JOIN worker_launch_attempts AS attempt
      ON attempt.attempt_id = NEW.previous_attempt_id
     AND attempt.intent_id = intent.intent_id
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
     AND observation.observation_sequence = NEW.previous_observation_sequence
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
      AND task.status = 'Queued'
      AND budget.max_attempts = 2
      AND budget.max_concurrent_attempts = 1
      AND budget.retryable_failure_classes_json
          = '["pre_running_launch_failure","worker_exit"]'
      AND attempt.attempt_number = 1
      AND observation.document_hash = NEW.evidence_hash
      AND observation.ticket_state = 'failed'
      AND observation.ticket_worker_pid IS NULL
      AND observation.ready_record_hash IS NULL
      AND observation.heartbeat_record_hash IS NULL
      AND observation.observation_sequence = (
          SELECT MAX(latest.observation_sequence)
          FROM worker_attempt_observations AS latest
          WHERE latest.attempt_id = attempt.attempt_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_launch_attempts AS retry
          WHERE retry.intent_id = intent.intent_id
            AND retry.attempt_number >= 2
      )
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_outcomes AS outcome
          WHERE outcome.intent_id = intent.intent_id
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
    SELECT RAISE(ABORT, 'retry reservation requires exact stopped attempt 1');
END;

CREATE TRIGGER worker_retry_reservation_requires_active_term
BEFORE INSERT ON worker_retry_reservations
WHEN NOT EXISTS (
    SELECT 1 FROM runtime_supervisor_leases AS lease
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
    SELECT RAISE(ABORT, 'retry reservation requires the active term');
END;

CREATE TRIGGER supervised_retry_attempt_requires_active_term
BEFORE INSERT ON supervised_retry_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN worker_retry_reservations AS retry
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
)
BEGIN
    SELECT RAISE(ABORT, 'retry delivery requires the active term');
END;

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
             -- A successor term may be the first observer after the original
             -- term durably authorized attempt 2 and then crashed after the
             -- Adapter append.  Bind the append to the reservation's first
             -- delivery; the normal observation trigger separately fences
             -- NEW.first_fencing_token as the current observing term.
             AND delivery.fencing_token = retry.first_fencing_token
             AND delivery.authorized_at_us <= NEW.first_observed_at_us
       )
 )
BEGIN
    SELECT RAISE(ABORT, 'retry attempt requires its durable reservation');
END;

CREATE TRIGGER worker_launch_attempt_rejects_attempt_three
BEFORE INSERT ON worker_launch_attempts
WHEN NEW.attempt_number > 2
BEGIN
    SELECT RAISE(ABORT, 'finite retry budget permits at most two attempts');
END;

CREATE TRIGGER worker_retry_reservations_are_immutable
BEFORE UPDATE ON worker_retry_reservations
BEGIN SELECT RAISE(ABORT, 'retry reservations are immutable'); END;

CREATE TRIGGER worker_retry_reservations_cannot_be_deleted
BEFORE DELETE ON worker_retry_reservations
BEGIN SELECT RAISE(ABORT, 'retry reservations are immutable'); END;

CREATE TRIGGER supervised_retry_attempts_are_immutable
BEFORE UPDATE ON supervised_retry_attempts
BEGIN SELECT RAISE(ABORT, 'retry delivery attempts are immutable'); END;

CREATE TRIGGER supervised_retry_attempts_cannot_be_deleted
BEFORE DELETE ON supervised_retry_attempts
BEGIN SELECT RAISE(ABORT, 'retry delivery attempts are immutable'); END;

-- One terminal record closes the finite budget after exact attempt 2 also
-- stops before ready.  It binds the latest SQLite observation, the Adapter's
-- private proof, the terminal RunEvent, and the active Supervisor term.
CREATE TABLE worker_retry_exhaustions (
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
        private_schema_version = '1.2.0'
    ),
    private_proof_hash TEXT NOT NULL CHECK (
        length(private_proof_hash) = 71
        AND substr(private_proof_hash, 1, 7) = 'sha256:'
    ),
    failure_kind TEXT NOT NULL CHECK (
        failure_kind = 'pre_running_launch_failure'
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
        REFERENCES worker_retry_reservations(intent_id, attempt_number),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approval_retry_budgets(task_id, approval_id),
    FOREIGN KEY (attempt_id, observation_sequence)
        REFERENCES worker_attempt_observations(attempt_id, observation_sequence),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES supervised_run_event_commits(task_id, sequence),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(project_id, principal_id, fencing_token)
);

CREATE TRIGGER worker_retry_exhaustion_requires_exact_case
BEFORE INSERT ON worker_retry_exhaustions
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN dispatch_attempts AS dispatch
      ON dispatch.intent_id = intent.intent_id
    JOIN tasks AS task ON task.task_id = intent.task_id
    JOIN approval_retry_budgets AS budget
      ON budget.task_id = task.task_id
     AND budget.approval_id = intent.approval_id
    JOIN worker_retry_reservations AS retry
      ON retry.intent_id = intent.intent_id
     AND retry.attempt_number = 2
    JOIN worker_launch_attempts AS attempt
      ON attempt.intent_id = intent.intent_id
     AND attempt.attempt_number = 2
    JOIN worker_launch_attempts AS prior_attempt
      ON prior_attempt.intent_id = intent.intent_id
     AND prior_attempt.attempt_number = 1
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
      ON lease.project_id = task.project_id
     AND lease.principal_id = task.principal_id
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
      AND task.status = 'Queued'
      AND budget.max_attempts = NEW.max_attempts
      AND budget.max_attempts = 2
      AND budget.max_concurrent_attempts = 1
      AND budget.retryable_failure_classes_json
          = '["pre_running_launch_failure","worker_exit"]'
      AND retry.task_id = task.task_id
      AND retry.project_id = task.project_id
      AND retry.principal_id = task.principal_id
      AND retry.approval_id = intent.approval_id
      AND attempt.attempt_id = NEW.attempt_id
      AND attempt.attempt_number = NEW.attempt_number
      AND prior_attempt.attempt_id = retry.previous_attempt_id
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
      AND observation.ticket_state = 'failed'
      AND observation.ticket_worker_pid IS NULL
      AND observation.ready_record_hash IS NULL
      AND observation.heartbeat_record_hash IS NULL
      AND observation.observation_sequence = (
          SELECT MAX(latest_observation.observation_sequence)
          FROM worker_attempt_observations AS latest_observation
          WHERE latest_observation.attempt_id = attempt.attempt_id
      )
      AND NEW.private_schema_version = '1.2.0'
      AND NEW.failure_kind = 'pre_running_launch_failure'
      AND event.event_type = 'node_failed'
      AND event.task_status = 'Failed'
      AND event.node_id = intent.node_id
      AND event.fingerprint_hash = intent.fingerprint_hash
      AND event.document_hash = NEW.terminal_event_hash
      AND event.occurred_at = json_extract(
          observation.document_json, '$.ticket.updated_at'
      )
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
      AND commit_record.project_id = task.project_id
      AND commit_record.principal_id = task.principal_id
      AND commit_record.fencing_token = NEW.fencing_token
      AND commit_record.recorded_at = NEW.exhausted_at
      AND commit_record.recorded_at_us = NEW.exhausted_at_us
      AND event.recorded_at = NEW.exhausted_at
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
          SELECT 1 FROM dispatch_outcomes AS outcome
          WHERE outcome.intent_id = intent.intent_id
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
    SELECT RAISE(ABORT, 'retry exhaustion requires exact stopped attempt 2');
END;

CREATE TRIGGER worker_retry_exhaustions_are_immutable
BEFORE UPDATE ON worker_retry_exhaustions
BEGIN SELECT RAISE(ABORT, 'retry exhaustions are immutable'); END;

CREATE TRIGGER worker_retry_exhaustions_cannot_be_deleted
BEFORE DELETE ON worker_retry_exhaustions
BEGIN SELECT RAISE(ABORT, 'retry exhaustions are immutable'); END;

-- A pre-ready exhausted Task has no positive dispatch receipt by design, but
-- its exact terminal exhaustion is a resolved runtime outcome and therefore
-- remains eligible for the existing trash lifecycle.
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
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a resolved terminal task can be moved to trash');
END;

-- Adapter 1.5 introduces only the immutable retry-capable receipt binding.
-- Rebuild the current downstream guards without weakening historical 1.4
-- support or permitting cross-version Algorithm/Adapter pairs.
DROP TRIGGER supervised_dispatch_reconciliation_requires_exact_case;

CREATE TRIGGER supervised_dispatch_reconciliation_requires_exact_case
BEFORE INSERT ON supervised_dispatch_reconciliation_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_outcomes AS outcome
    JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE outcome.intent_id = NEW.intent_id
      AND outcome.outcome = 'reconciliation_required'
      AND outcome.document_hash = NEW.source_outcome_hash
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version IN ('1.4.0', '1.5.0')
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = intent.adapter_version
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Queued'
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_reconciliation_resolutions AS resolution
          WHERE resolution.intent_id = outcome.intent_id
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
    SELECT RAISE(ABORT, 'dispatch reconciliation requires an exact unresolved case');
END;

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
      AND task.status IN ('Queued', 'Running')
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
          (attempt.attempt_number = 1
           AND json_extract(
               NEW.capability_proof_json, '$.private_schema_version'
           ) = '1.1.0')
          OR
          (attempt.attempt_number = 2
           AND intent.adapter_version = '1.5.0'
           AND json_extract(
               NEW.capability_proof_json, '$.private_schema_version'
           ) = '1.2.0')
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
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'timeout window requires exact first running evidence');
END;

DROP TRIGGER dispatch_reconciliation_resolution_requires_exact_proof;

CREATE TRIGGER dispatch_reconciliation_resolution_requires_exact_proof
BEFORE INSERT ON dispatch_reconciliation_resolutions
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_outcomes AS outcome
    JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
    JOIN tasks AS task ON task.task_id = intent.task_id
    JOIN supervised_dispatch_reconciliation_attempts AS authorization
      ON authorization.intent_id = outcome.intent_id
     AND authorization.fencing_token = NEW.fencing_token
     AND authorization.evidence_kind = NEW.evidence_kind
    WHERE outcome.intent_id = NEW.intent_id
      AND outcome.outcome = 'reconciliation_required'
      AND outcome.document_hash = NEW.source_outcome_hash
      AND authorization.source_outcome_hash = outcome.document_hash
      AND authorization.project_id = NEW.project_id
      AND authorization.principal_id = NEW.principal_id
      AND authorization.authorized_at_us <= NEW.resolved_at_us
      AND intent.task_id = NEW.task_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version IN ('1.4.0', '1.5.0')
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = intent.adapter_version
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Queued'
      AND json_valid(NEW.handle_json)
      AND json_type(NEW.handle_json, '$') = 'object'
      AND json_valid(NEW.effective_outcome_json)
      AND json_type(NEW.effective_outcome_json, '$') = 'object'
      AND json_extract(NEW.effective_outcome_json, '$.status') = 'dispatched'
      AND json_extract(NEW.effective_outcome_json, '$.recorded_at')
          = NEW.resolved_at
      AND json(json_extract(NEW.effective_outcome_json, '$.handle'))
          = json(NEW.handle_json)
      AND (
          SELECT COUNT(*) FROM json_each(NEW.effective_outcome_json)
      ) = 3
      AND json_valid(NEW.document_json)
      AND json_type(NEW.document_json, '$') = 'object'
      AND json_extract(NEW.document_json, '$.schema_version') = '1.0.0'
      AND json_extract(NEW.document_json, '$.intent_id') = NEW.intent_id
      AND json_extract(NEW.document_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.document_json, '$.source_outcome_hash')
          = NEW.source_outcome_hash
      AND json_extract(NEW.document_json, '$.result') = NEW.result
      AND json_extract(NEW.document_json, '$.evidence.kind')
          = NEW.evidence_kind
      AND json_extract(NEW.document_json, '$.resolved_at') = NEW.resolved_at
      AND json(json_extract(NEW.document_json, '$.effective_outcome'))
          = json(NEW.effective_outcome_json)
      AND json_type(NEW.document_json, '$.extensions') = 'object'
      AND (
          SELECT COUNT(*)
          FROM json_each(NEW.document_json, '$.extensions')
      ) = 0
      AND (
          SELECT COUNT(*) FROM json_each(NEW.document_json)
      ) = 9
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND (
          (NEW.evidence_kind = 'managed_worker_receipt'
           AND EXISTS (
               SELECT 1
               FROM worker_launch_attempts AS attempt
               JOIN worker_attempt_observations AS observation
                 ON observation.attempt_id = attempt.attempt_id
               JOIN supervised_dispatch_adoptions AS adoption
                 ON adoption.intent_id = attempt.intent_id
                AND adoption.attempt_id = attempt.attempt_id
               WHERE attempt.intent_id = NEW.intent_id
                 AND attempt.attempt_id = NEW.attempt_id
                 AND observation.observation_sequence
                     = NEW.observation_sequence
                 AND observation.document_hash = NEW.evidence_record_hash
                 AND observation.ticket_state = 'spawned'
                 AND observation.ready_record_hash IS NOT NULL
                 AND observation.heartbeat_state
                     IN ('running', 'succeeded', 'failed')
                 AND observation.heartbeat_record_hash IS NOT NULL
                 AND json_extract(NEW.handle_json, '$.submission_id')
                     = attempt.submission_id
                 AND json_extract(NEW.handle_json, '$.job_id')
                     = attempt.job_id
                 AND json_extract(NEW.handle_json, '$.request_hash')
                     = attempt.adapter_request_hash
                 AND json_extract(
                     NEW.document_json, '$.evidence.attempt_id'
                 ) = NEW.attempt_id
                 AND json_type(
                     NEW.document_json, '$.evidence.observation_sequence'
                 ) = 'integer'
                 AND json_extract(
                     NEW.document_json, '$.evidence.observation_sequence'
                 ) = NEW.observation_sequence
                 AND json_extract(
                     NEW.document_json,
                     '$.evidence.observation_document_hash'
                 ) = NEW.evidence_record_hash
                 AND (
                     SELECT COUNT(*)
                     FROM json_each(NEW.document_json, '$.evidence')
                 ) = 4
                 AND adoption.project_id = NEW.project_id
                 AND adoption.principal_id = NEW.principal_id
                 AND adoption.fencing_token = NEW.fencing_token
                 AND adoption.recorded_at = NEW.resolved_at
                 AND adoption.recorded_at_us = NEW.resolved_at_us
           ))
          OR
          (NEW.evidence_kind = 'private_receipt'
           AND EXISTS (
               SELECT 1
               FROM supervised_private_receipt_adoptions AS adoption
               WHERE adoption.intent_id = NEW.intent_id
                 AND adoption.project_id = NEW.project_id
                 AND adoption.principal_id = NEW.principal_id
                 AND adoption.fencing_token = NEW.fencing_token
                 AND adoption.private_schema_version
                     = NEW.private_schema_version
                 AND adoption.receipt_record_hash = NEW.receipt_record_hash
                 AND adoption.outcome_document_hash
                     = NEW.effective_outcome_hash
                 AND json_extract(
                     NEW.document_json, '$.evidence.private_schema_version'
                 ) = NEW.private_schema_version
                 AND json_extract(
                     NEW.document_json, '$.evidence.receipt_record_hash'
                 ) = NEW.receipt_record_hash
                 AND (
                     SELECT COUNT(*)
                     FROM json_each(NEW.document_json, '$.evidence')
                 ) = 3
                 AND adoption.recorded_at = NEW.resolved_at
                 AND adoption.recorded_at_us = NEW.resolved_at_us
           ))
      )
)
BEGIN
    SELECT RAISE(ABORT, 'dispatch reconciliation resolution requires exact positive proof');
END;
