-- P2 checkpoint / Waiting / same-attempt resume authority.
-- Every row is append-only; mutable task status is only the bounded projection.

-- SQLite cannot widen a CHECK constraint in place.  This exact catalog
-- rewrite changes only the accepted non-terminal heartbeat enum.  The
-- initializer verifies the complete schema manifest, quick_check, and
-- foreign_key_check before commit, then every runtime connection reparses
-- the updated table definition with foreign keys enabled.
PRAGMA writable_schema = ON;

UPDATE sqlite_master
SET sql = replace(
    sql,
    "heartbeat_state IN ('running', 'succeeded', 'failed', 'stopped')",
    "heartbeat_state IN ('running', 'waiting', 'succeeded', 'failed', 'stopped')"
)
WHERE type = 'table'
  AND name = 'worker_attempt_observations'
  AND sql LIKE "%heartbeat_state IN ('running', 'succeeded', 'failed', 'stopped')%";

UPDATE sqlite_master
SET sql = replace(
    sql,
    "(adapter_version = '1.5.0'
               AND private_schema_version = '1.2.0'))",
    "(adapter_version = '1.5.0'
               AND private_schema_version = '1.2.0')
              OR
              (adapter_version = '1.6.0'
               AND private_schema_version = '1.2.0'))"
)
WHERE type = 'table'
  AND name = 'dispatch_reconciliation_observations'
  AND sql LIKE "%adapter_version = '1.5.0'%";

PRAGMA writable_schema = OFF;

CREATE TRIGGER worker_attempt_waiting_requires_checkpoint_capable_intent
BEFORE INSERT ON worker_attempt_observations
WHEN NEW.heartbeat_state = 'waiting'
 AND NOT EXISTS (
    SELECT 1
    FROM worker_launch_attempts AS attempt
    JOIN dispatch_intents AS intent ON intent.intent_id = attempt.intent_id
    WHERE attempt.attempt_id = NEW.attempt_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version = '1.6.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.6.0'
)
BEGIN
    SELECT RAISE(
        ABORT,
        'checkpoint Waiting requires the exact 1.6 managed dispatch'
    );
END;

CREATE TABLE worker_checkpoint_waits (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (
        typeof(attempt_number) = 'integer' AND attempt_number >= 1
    ),
    checkpoint_index INTEGER NOT NULL CHECK (
        typeof(checkpoint_index) = 'integer' AND checkpoint_index >= 1
    ),
    completed_updates INTEGER NOT NULL CHECK (
        typeof(completed_updates) = 'integer' AND completed_updates >= 1
    ),
    checkpoint_manifest_relative_path TEXT NOT NULL,
    checkpoint_manifest_size_bytes INTEGER NOT NULL CHECK (
        typeof(checkpoint_manifest_size_bytes) = 'integer'
        AND checkpoint_manifest_size_bytes >= 1
        AND checkpoint_manifest_size_bytes <= 16777216
    ),
    binding_hash TEXT NOT NULL,
    submission_receipt_record_hash TEXT NOT NULL,
    ready_record_hash TEXT NOT NULL,
    checkpoint_manifest_hash TEXT NOT NULL,
    checkpoint_receipt_record_hash TEXT NOT NULL,
    checkpoint_created_at TEXT NOT NULL,
    checkpoint_created_at_us INTEGER NOT NULL CHECK (
        typeof(checkpoint_created_at_us) = 'integer'
        AND checkpoint_created_at_us >= 0
    ),
    source_outcome_document_hash TEXT NOT NULL,
    source_handle_hash TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    proof_hash TEXT NOT NULL,
    checkpoint_event_sequence INTEGER NOT NULL CHECK (
        typeof(checkpoint_event_sequence) = 'integer'
        AND checkpoint_event_sequence >= 1
    ),
    waiting_event_sequence INTEGER NOT NULL CHECK (
        typeof(waiting_event_sequence) = 'integer'
        AND waiting_event_sequence = checkpoint_event_sequence + 1
    ),
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    UNIQUE (task_id, attempt_id, checkpoint_index),
    UNIQUE (task_id, checkpoint_event_sequence),
    UNIQUE (task_id, waiting_event_sequence),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id),
    FOREIGN KEY (task_id, checkpoint_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, waiting_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE task_checkpoint_resume_requests (
    resume_id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    requested_at_us INTEGER NOT NULL CHECK (
        typeof(requested_at_us) = 'integer' AND requested_at_us >= 0
    ),
    UNIQUE (project_id, principal_id, idempotency_key),
    FOREIGN KEY (checkpoint_id) REFERENCES worker_checkpoint_waits(checkpoint_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id)
);

CREATE TABLE supervised_checkpoint_resume_authorizations (
    resume_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    resume_request_json TEXT NOT NULL,
    resume_request_record_hash TEXT NOT NULL,
    authorization_json TEXT NOT NULL,
    authorization_hash TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    authorized_at TEXT NOT NULL,
    authorized_at_us INTEGER NOT NULL CHECK (
        typeof(authorized_at_us) = 'integer' AND authorized_at_us >= 0
    ),
    PRIMARY KEY (resume_id, fencing_token),
    FOREIGN KEY (resume_id) REFERENCES task_checkpoint_resume_requests(resume_id),
    FOREIGN KEY (checkpoint_id) REFERENCES worker_checkpoint_waits(checkpoint_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE task_checkpoint_resume_outcomes (
    resume_id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    authorization_hash TEXT NOT NULL,
    adapter_proof_json TEXT NOT NULL,
    adapter_proof_hash TEXT NOT NULL,
    resume_acknowledged_at TEXT NOT NULL,
    resume_acknowledged_at_us INTEGER NOT NULL CHECK (
        typeof(resume_acknowledged_at_us) = 'integer'
        AND resume_acknowledged_at_us >= 0
    ),
    running_event_sequence INTEGER NOT NULL CHECK (
        typeof(running_event_sequence) = 'integer'
        AND running_event_sequence >= 1
    ),
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    resumed_at TEXT NOT NULL,
    resumed_at_us INTEGER NOT NULL CHECK (
        typeof(resumed_at_us) = 'integer' AND resumed_at_us >= 0
    ),
    FOREIGN KEY (resume_id, fencing_token)
        REFERENCES supervised_checkpoint_resume_authorizations(
            resume_id, fencing_token
        ),
    FOREIGN KEY (checkpoint_id) REFERENCES worker_checkpoint_waits(checkpoint_id),
    FOREIGN KEY (task_id, running_event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id)
);

CREATE INDEX idx_worker_checkpoint_waits_task
    ON worker_checkpoint_waits(
        task_id, recorded_at_us, attempt_id, checkpoint_index
    );
CREATE INDEX idx_checkpoint_resume_requests_task
    ON task_checkpoint_resume_requests(task_id, requested_at_us);
CREATE INDEX idx_checkpoint_resume_authorizations_term
    ON supervised_checkpoint_resume_authorizations(
        project_id, principal_id, fencing_token, resume_id
    );

CREATE TRIGGER worker_checkpoint_wait_requires_active_term
BEFORE INSERT ON worker_checkpoint_waits
WHEN NOT EXISTS (
    SELECT 1 FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
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
    SELECT RAISE(ABORT, 'checkpoint wait requires the active term');
END;

CREATE TRIGGER worker_checkpoint_wait_requires_exact_live_attempt
BEFORE INSERT ON worker_checkpoint_waits
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
    JOIN run_events AS checkpoint_event
      ON checkpoint_event.task_id = task.task_id
     AND checkpoint_event.sequence = NEW.checkpoint_event_sequence
    JOIN run_events AS waiting_event
      ON waiting_event.task_id = task.task_id
     AND waiting_event.sequence = NEW.waiting_event_sequence
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Running'
      AND intent.intent_id = NEW.intent_id
      AND intent.node_id = NEW.node_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version = '1.6.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.6.0'
      AND attempt.attempt_id = NEW.attempt_id
      AND attempt.attempt_number = NEW.attempt_number
      AND attempt.attempt_number = (
          SELECT MAX(latest.attempt_number)
          FROM worker_launch_attempts AS latest
          WHERE latest.intent_id = intent.intent_id
      )
      AND attempt.submission_id = NEW.submission_id
      AND attempt.binding_hash = NEW.binding_hash
      AND observation.observation_sequence = (
          SELECT MAX(latest.observation_sequence)
          FROM worker_attempt_observations AS latest
          WHERE latest.attempt_id = attempt.attempt_id
      )
      AND observation.ticket_state = 'spawned'
      AND observation.ready_record_hash = NEW.ready_record_hash
      AND (
          observation.heartbeat_state = 'running'
          OR (
              intent.adapter_version = '1.6.0'
              AND json_extract(
                  intent.request_json, '$.request.algorithm.version'
              ) = '1.6.0'
              AND observation.heartbeat_state = 'waiting'
          )
      )
      AND observation.heartbeat_record_hash IS NOT NULL
      AND dispatch.outcome_document_hash = NEW.source_outcome_document_hash
      AND json_extract(dispatch.outcome_document_json, '$.handle.submission_id')
          = attempt.submission_id
      AND json_extract(dispatch.outcome_document_json, '$.handle.job_id')
          = attempt.job_id
      AND checkpoint_event.event_type = 'checkpoint_created'
      AND checkpoint_event.task_status = 'Running'
      AND checkpoint_event.node_id = intent.node_id
      AND waiting_event.event_type = 'node_waiting'
      AND waiting_event.task_status = 'Waiting'
      AND waiting_event.node_id = intent.node_id
      AND NOT EXISTS (
          SELECT 1 FROM worker_checkpoint_waits AS prior
          LEFT JOIN task_checkpoint_resume_outcomes AS outcome
            ON outcome.checkpoint_id = prior.checkpoint_id
          WHERE prior.task_id = task.task_id AND outcome.resume_id IS NULL
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS timeout
          WHERE timeout.task_id = task.task_id
            AND NOT EXISTS (
                SELECT 1
                FROM worker_exit_retry_timeout_retirements AS retirement
                WHERE retirement.timeout_id = timeout.timeout_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM task_timeout_outcomes AS outcome
                WHERE outcome.timeout_id = timeout.timeout_id
            )
            AND (
                timeout.deadline_at_us <= NEW.recorded_at_us
                OR EXISTS (
                    SELECT 1 FROM supervised_timeout_attempts AS delivery
                    WHERE delivery.timeout_id = timeout.timeout_id
                )
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint wait requires an exact live attempt');
END;

CREATE TRIGGER checkpoint_resume_request_requires_current_wait
BEFORE INSERT ON task_checkpoint_resume_requests
WHEN NOT EXISTS (
    SELECT 1
    FROM worker_checkpoint_waits AS checkpoint
    JOIN tasks AS task ON task.task_id = checkpoint.task_id
    WHERE checkpoint.checkpoint_id = NEW.checkpoint_id
      AND checkpoint.task_id = NEW.task_id
      AND checkpoint.project_id = NEW.project_id
      AND checkpoint.principal_id = NEW.principal_id
      AND checkpoint.intent_id = NEW.intent_id
      AND checkpoint.attempt_id = NEW.attempt_id
      AND task.status = 'Waiting'
      AND NOT EXISTS (
          SELECT 1 FROM task_checkpoint_resume_outcomes AS outcome
          WHERE outcome.checkpoint_id = checkpoint.checkpoint_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS timeout
          WHERE timeout.task_id = task.task_id
            AND NOT EXISTS (
                SELECT 1
                FROM worker_exit_retry_timeout_retirements AS retirement
                WHERE retirement.timeout_id = timeout.timeout_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM task_timeout_outcomes AS outcome
                WHERE outcome.timeout_id = timeout.timeout_id
            )
            AND (
                timeout.deadline_at_us <= NEW.requested_at_us
                OR EXISTS (
                    SELECT 1 FROM supervised_timeout_attempts AS delivery
                    WHERE delivery.timeout_id = timeout.timeout_id
                )
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'resume request requires the current checkpoint wait');
END;

CREATE TRIGGER checkpoint_resume_authorization_requires_active_term
BEFORE INSERT ON supervised_checkpoint_resume_authorizations
WHEN NOT EXISTS (
    SELECT 1 FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
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
    SELECT RAISE(ABORT, 'checkpoint resume authorization requires the active term');
END;

CREATE TRIGGER checkpoint_resume_authorization_requires_current_wait
BEFORE INSERT ON supervised_checkpoint_resume_authorizations
WHEN NOT EXISTS (
    SELECT 1
    FROM task_checkpoint_resume_requests AS request
    JOIN worker_checkpoint_waits AS checkpoint
      ON checkpoint.checkpoint_id = request.checkpoint_id
    JOIN tasks AS task ON task.task_id = request.task_id
    WHERE request.resume_id = NEW.resume_id
      AND request.checkpoint_id = NEW.checkpoint_id
      AND request.task_id = NEW.task_id
      AND request.project_id = NEW.project_id
      AND request.principal_id = NEW.principal_id
      AND request.intent_id = NEW.intent_id
      AND request.attempt_id = NEW.attempt_id
      AND task.status = 'Waiting'
      AND NOT EXISTS (
          SELECT 1 FROM task_checkpoint_resume_outcomes AS outcome
          WHERE outcome.resume_id = request.resume_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS timeout
          WHERE timeout.task_id = task.task_id
            AND NOT EXISTS (
                SELECT 1
                FROM worker_exit_retry_timeout_retirements AS retirement
                WHERE retirement.timeout_id = timeout.timeout_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM task_timeout_outcomes AS outcome
                WHERE outcome.timeout_id = timeout.timeout_id
            )
            AND (
                timeout.deadline_at_us <= NEW.authorized_at_us
                OR EXISTS (
                    SELECT 1 FROM supervised_timeout_attempts AS delivery
                    WHERE delivery.timeout_id = timeout.timeout_id
                )
            )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint resume requires the current Waiting task');
END;

CREATE TRIGGER checkpoint_resume_authorization_reuses_worker_request
BEFORE INSERT ON supervised_checkpoint_resume_authorizations
WHEN EXISTS (
    SELECT 1
    FROM supervised_checkpoint_resume_authorizations AS prior
    WHERE prior.resume_id = NEW.resume_id
      AND (
          prior.resume_request_json <> NEW.resume_request_json
          OR prior.resume_request_record_hash
             <> NEW.resume_request_record_hash
          OR prior.authorization_json <> NEW.authorization_json
          OR prior.authorization_hash <> NEW.authorization_hash
      )
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint resume Worker request is immutable');
END;

CREATE TRIGGER checkpoint_resume_outcome_requires_active_term
BEFORE INSERT ON task_checkpoint_resume_outcomes
WHEN NOT EXISTS (
    SELECT 1 FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND lease.heartbeat_at_us <= NEW.resumed_at_us
      AND lease.expires_at_us > NEW.resumed_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint resume outcome requires the active term');
END;

CREATE TRIGGER checkpoint_resume_outcome_requires_exact_ack
BEFORE INSERT ON task_checkpoint_resume_outcomes
WHEN NOT EXISTS (
    SELECT 1
    FROM supervised_checkpoint_resume_authorizations AS authorization
    JOIN task_checkpoint_resume_requests AS request
      ON request.resume_id = authorization.resume_id
    JOIN worker_checkpoint_waits AS checkpoint
      ON checkpoint.checkpoint_id = request.checkpoint_id
    JOIN tasks AS task ON task.task_id = request.task_id
    JOIN run_events AS event
      ON event.task_id = task.task_id
     AND event.sequence = NEW.running_event_sequence
    WHERE authorization.resume_id = NEW.resume_id
      AND authorization.fencing_token = NEW.fencing_token
      AND authorization.authorization_hash = NEW.authorization_hash
      AND request.checkpoint_id = NEW.checkpoint_id
      AND request.task_id = NEW.task_id
      AND request.project_id = NEW.project_id
      AND request.principal_id = NEW.principal_id
      AND request.intent_id = NEW.intent_id
      AND request.attempt_id = NEW.attempt_id
      AND task.status = 'Waiting'
      AND event.event_type = 'node_started'
      AND event.task_status = 'Running'
      AND event.node_id = checkpoint.node_id
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM worker_attempt_timeout_windows AS timeout
          WHERE timeout.task_id = task.task_id
            AND NOT EXISTS (
                SELECT 1
                FROM worker_exit_retry_timeout_retirements AS retirement
                WHERE retirement.timeout_id = timeout.timeout_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM task_timeout_outcomes AS outcome
                WHERE outcome.timeout_id = timeout.timeout_id
            )
            AND (
                timeout.deadline_at_us <= NEW.resumed_at_us
                OR EXISTS (
                    SELECT 1 FROM supervised_timeout_attempts AS delivery
                    WHERE delivery.timeout_id = timeout.timeout_id
                )
            )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'checkpoint resume outcome requires an exact ack');
END;

CREATE TRIGGER worker_checkpoint_waits_are_append_only
BEFORE UPDATE ON worker_checkpoint_waits
BEGIN SELECT RAISE(ABORT, 'checkpoint waits are append-only'); END;
CREATE TRIGGER worker_checkpoint_waits_cannot_be_deleted
BEFORE DELETE ON worker_checkpoint_waits
BEGIN SELECT RAISE(ABORT, 'checkpoint waits are append-only'); END;
CREATE TRIGGER checkpoint_resume_requests_are_append_only
BEFORE UPDATE ON task_checkpoint_resume_requests
BEGIN SELECT RAISE(ABORT, 'checkpoint resume requests are append-only'); END;
CREATE TRIGGER checkpoint_resume_requests_cannot_be_deleted
BEFORE DELETE ON task_checkpoint_resume_requests
BEGIN SELECT RAISE(ABORT, 'checkpoint resume requests are append-only'); END;
CREATE TRIGGER checkpoint_resume_authorizations_are_append_only
BEFORE UPDATE ON supervised_checkpoint_resume_authorizations
BEGIN SELECT RAISE(ABORT, 'checkpoint resume authorizations are append-only'); END;
CREATE TRIGGER checkpoint_resume_authorizations_cannot_be_deleted
BEFORE DELETE ON supervised_checkpoint_resume_authorizations
BEGIN SELECT RAISE(ABORT, 'checkpoint resume authorizations are append-only'); END;
CREATE TRIGGER checkpoint_resume_outcomes_are_append_only
BEFORE UPDATE ON task_checkpoint_resume_outcomes
BEGIN SELECT RAISE(ABORT, 'checkpoint resume outcomes are append-only'); END;
CREATE TRIGGER checkpoint_resume_outcomes_cannot_be_deleted
BEFORE DELETE ON task_checkpoint_resume_outcomes
BEGIN SELECT RAISE(ABORT, 'checkpoint resume outcomes are append-only'); END;

-- Waiting keeps the same live attempt, so cancellation and an already-armed
-- wall-time window remain valid.  The exact-attempt checks remain unchanged.
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
      AND task.status IN ('Queued', 'Running', 'Waiting')
      AND intent.intent_id = NEW.intent_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version IN ('1.4.0', '1.5.0', '1.6.0')
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
      AND observation.heartbeat_state IN ('running', 'waiting')
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
              json_extract(dispatch.outcome_document_json, '$.handle.submission_id')
                  = attempt.submission_id
              AND json_extract(dispatch.outcome_document_json, '$.handle.job_id')
                  = attempt.job_id
              AND json_extract(dispatch.outcome_document_json, '$.handle.request_hash')
                  = attempt.adapter_request_hash
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
      AND task.status IN ('Queued', 'Running', 'Waiting')
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

-- The current immutable 1.6 pair retains D-012's finite two-attempt policy.
-- Rewrite the exact managed-version predicates and add Waiting only to the
-- checkpoint-capable 1.6 branches of the already-proven reconciliation,
-- replacement, and timeout triggers.  Attempt, budget, proof, and fencing
-- clauses remain unchanged.
PRAGMA writable_schema = ON;

UPDATE sqlite_master
SET sql = replace(
    sql,
    "intent.adapter_version IN ('1.4.0', '1.5.0')",
    "intent.adapter_version IN ('1.4.0', '1.5.0', '1.6.0')"
)
WHERE type = 'trigger'
  AND name IN (
      'dispatch_reconciliation_negative_requires_exact_case',
      'dispatch_reconciliation_resolution_requires_exact_proof',
      'supervised_dispatch_reconciliation_requires_exact_case',
      'task_cancel_request_requires_exact_running_attempt',
      'worker_attempt_timeout_window_requires_exact_start'
  );

UPDATE sqlite_master
SET sql = replace(
    sql,
    "intent.adapter_version = '1.5.0'",
    "intent.adapter_version IN ('1.5.0', '1.6.0')"
)
WHERE type = 'trigger'
  AND name IN (
      'worker_attempt_timeout_window_requires_exact_start',
      'worker_exit_retry_exhaustion_requires_exact_case',
      'worker_exit_retry_replacement_requires_exact_case',
      'worker_exit_retry_reservation_requires_exact_case',
      'worker_retry_exhaustion_requires_exact_case',
      'worker_retry_reservation_requires_exact_case'
  );

UPDATE sqlite_master
SET sql = replace(
    sql,
    "json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.5.0'",
    "json_extract(intent.request_json, '$.request.algorithm.version')
          = intent.adapter_version"
)
WHERE type = 'trigger'
  AND name IN (
      'worker_exit_retry_exhaustion_requires_exact_case',
      'worker_exit_retry_replacement_requires_exact_case',
      'worker_exit_retry_reservation_requires_exact_case',
      'worker_retry_exhaustion_requires_exact_case',
      'worker_retry_reservation_requires_exact_case'
  );

UPDATE sqlite_master
SET sql = replace(
    sql,
    "json_extract(NEW.handle_json, '$.adapter_version') = '1.5.0'",
    "json_extract(NEW.handle_json, '$.adapter_version') = intent.adapter_version"
)
WHERE type = 'trigger'
  AND name = 'worker_exit_retry_replacement_requires_exact_case';

UPDATE sqlite_master
SET sql = replace(
    sql,
    "AND observation.heartbeat_state
                     IN ('running', 'succeeded', 'failed')",
    "AND (
                     observation.heartbeat_state
                         IN ('running', 'succeeded', 'failed')
                     OR (
                         intent.adapter_version = '1.6.0'
                         AND json_extract(
                             intent.request_json,
                             '$.request.algorithm.version'
                         ) = '1.6.0'
                         AND observation.heartbeat_state = 'waiting'
                     )
                 )"
)
WHERE type = 'trigger'
  AND name = 'dispatch_reconciliation_resolution_requires_exact_proof';

UPDATE sqlite_master
SET sql = replace(
    sql,
    "AND observation.heartbeat_state IN ('running', 'succeeded', 'failed')",
    "AND (
          observation.heartbeat_state IN ('running', 'succeeded', 'failed')
          OR (
              intent.adapter_version = '1.6.0'
              AND json_extract(
                  intent.request_json, '$.request.algorithm.version'
              ) = '1.6.0'
              AND observation.heartbeat_state = 'waiting'
          )
      )"
)
WHERE type = 'trigger'
  AND name = 'worker_exit_retry_replacement_requires_exact_case';

UPDATE sqlite_master
SET sql = replace(
    sql,
    "AND observation.observation_sequence = (
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
          = observation.observed_at_us + NEW.wall_time_seconds * 1000000",
    "AND observation.observation_sequence = (
          SELECT MIN(first_live.observation_sequence)
          FROM worker_attempt_observations AS first_live
          WHERE first_live.attempt_id = attempt.attempt_id
            AND first_live.ticket_state = 'spawned'
            AND first_live.ready_record_hash IS NOT NULL
            AND first_live.heartbeat_record_hash IS NOT NULL
            AND (
                first_live.heartbeat_state = 'running'
                OR (
                    intent.adapter_version = '1.6.0'
                    AND first_live.heartbeat_state = 'waiting'
                )
            )
      )
      AND observation.ticket_state = 'spawned'
      AND observation.ready_record_hash = NEW.ready_record_hash
      AND observation.heartbeat_record_hash
          = NEW.running_heartbeat_record_hash
      AND (
          (
              observation.heartbeat_state = 'running'
              AND observation.observed_at = NEW.started_at
              AND observation.observed_at_us = NEW.started_at_us
              AND NEW.recorded_at_us >= observation.observed_at_us
              AND NEW.deadline_at_us = observation.observed_at_us
                  + NEW.wall_time_seconds * 1000000
          )
          OR (
              intent.adapter_version = '1.6.0'
              AND json_extract(
                  intent.request_json, '$.request.algorithm.version'
              ) = '1.6.0'
              AND observation.heartbeat_state = 'waiting'
              AND (
                  (
                      length(observation.ready_started_at) = 20
                      AND NEW.started_at = substr(
                          observation.ready_started_at, 1, 19
                      ) || '.000000Z'
                  )
                  OR (
                      length(observation.ready_started_at) = 24
                      AND NEW.started_at = substr(
                          observation.ready_started_at, 1, 23
                      ) || '000Z'
                  )
                  OR (
                      length(observation.ready_started_at) = 27
                      AND NEW.started_at = observation.ready_started_at
                  )
              )
              AND length(NEW.started_at) = 27
              AND substr(NEW.started_at, 20, 1) = '.'
              AND substr(NEW.started_at, 27, 1) = 'Z'
              AND substr(NEW.started_at, 21, 6) NOT GLOB '*[^0-9]*'
              AND NEW.started_at_us =
                  CAST(strftime('%s', NEW.started_at) AS INTEGER) * 1000000
                  + CAST(substr(NEW.started_at, 21, 6) AS INTEGER)
              AND observation.observed_at_us >= NEW.started_at_us
              AND NEW.recorded_at_us >= observation.observed_at_us
              AND NEW.deadline_at_us = NEW.started_at_us
                  + NEW.wall_time_seconds * 1000000
          )
      )"
)
WHERE type = 'trigger'
  AND name = 'worker_attempt_timeout_window_requires_exact_start';

PRAGMA writable_schema = OFF;
