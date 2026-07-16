-- Durable, exact-attempt user cancellation for the current managed FWI
-- Adapter.  These rows are control-plane audit evidence only: the Worker's
-- inherited kernel locks remain the execution and capacity authority.

CREATE TABLE task_cancel_requests (
    request_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL CHECK (reason = 'user_requested'),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (
        length(request_hash) = 71
        AND substr(request_hash, 1, 7) = 'sha256:'
    ),
    request_event_sequence INTEGER NOT NULL CHECK (
        typeof(request_event_sequence) = 'integer'
        AND request_event_sequence >= 1
    ),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL CHECK (
        length(document_hash) = 71
        AND substr(document_hash, 1, 7) = 'sha256:'
    ),
    requested_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (project_id, principal_id, idempotency_key),
    UNIQUE (request_id, intent_id, attempt_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id),
    FOREIGN KEY (task_id, request_event_sequence)
        REFERENCES run_events(task_id, sequence)
);

CREATE TABLE supervised_cancel_attempts (
    request_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    action TEXT NOT NULL CHECK (action = 'deliver_exact_attempt_cancel'),
    authorized_at TEXT NOT NULL,
    authorized_at_us INTEGER NOT NULL CHECK (
        typeof(authorized_at_us) = 'integer' AND authorized_at_us >= 0
    ),
    PRIMARY KEY (request_id, fencing_token),
    UNIQUE (request_id, intent_id, attempt_id, fencing_token),
    FOREIGN KEY (request_id, intent_id, attempt_id)
        REFERENCES task_cancel_requests(request_id, intent_id, attempt_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE task_cancel_outcomes (
    request_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    result TEXT NOT NULL CHECK (
        result IN ('cancel_confirmed', 'terminal_preempted')
    ),
    terminal_status TEXT NOT NULL CHECK (
        terminal_status IN ('Cancelled', 'Succeeded', 'Failed')
    ),
    terminal_event_sequence INTEGER NOT NULL CHECK (
        typeof(terminal_event_sequence) = 'integer'
        AND terminal_event_sequence >= 1
    ),
    adapter_proof_json TEXT NOT NULL,
    adapter_proof_hash TEXT NOT NULL CHECK (
        length(adapter_proof_hash) = 71
        AND substr(adapter_proof_hash, 1, 7) = 'sha256:'
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
    FOREIGN KEY (request_id, intent_id, attempt_id)
        REFERENCES task_cancel_requests(request_id, intent_id, attempt_id),
    FOREIGN KEY (request_id, fencing_token)
        REFERENCES supervised_cancel_attempts(request_id, fencing_token),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES run_events(task_id, sequence)
);

CREATE INDEX idx_task_cancel_requests_scope
    ON task_cancel_requests(project_id, principal_id, requested_at, task_id);

CREATE INDEX idx_supervised_cancel_attempts_term
    ON supervised_cancel_attempts(
        project_id, principal_id, fencing_token, request_id
    );

CREATE TRIGGER task_cancel_request_requires_exact_running_attempt
BEFORE INSERT ON task_cancel_requests
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN dispatch_intents AS intent ON intent.task_id = task.task_id
    JOIN dispatch_outcomes AS dispatch
      ON dispatch.intent_id = intent.intent_id
     AND dispatch.outcome = 'dispatched'
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
      AND intent.adapter_version = '1.4.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.4.0'
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

CREATE TRIGGER task_cancel_request_blocks_supervised_dispatch
BEFORE INSERT ON supervised_dispatch_attempts
WHEN EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN task_cancel_requests AS request ON request.task_id = intent.task_id
    WHERE intent.intent_id = NEW.intent_id
)
BEGIN
    SELECT RAISE(ABORT, 'cancelled intent cannot receive supervised dispatch');
END;

CREATE TRIGGER supervised_cancel_attempt_requires_pending_request
BEFORE INSERT ON supervised_cancel_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM task_cancel_requests AS request
    JOIN tasks AS task ON task.task_id = request.task_id
    WHERE request.request_id = NEW.request_id
      AND request.project_id = NEW.project_id
      AND request.principal_id = NEW.principal_id
      AND request.intent_id = NEW.intent_id
      AND request.attempt_id = NEW.attempt_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_outcomes AS outcome
          WHERE outcome.request_id = request.request_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'supervised cancel must match a pending request');
END;

CREATE TRIGGER supervised_cancel_attempt_requires_active_term
BEFORE INSERT ON supervised_cancel_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
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
    SELECT RAISE(ABORT, 'supervised cancel requires the active term');
END;

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
      AND json_extract(NEW.adapter_proof_json, '$.terminal_status')
          = NEW.terminal_status
      AND json_extract(NEW.adapter_proof_json, '$.local_run_state') = 'retained'
      AND json_type(NEW.adapter_proof_json, '$.replayed') IN ('true', 'false')
      AND length(json_extract(NEW.adapter_proof_json, '$.receipt_record_hash')) = 71
      AND substr(
          json_extract(NEW.adapter_proof_json, '$.receipt_record_hash'), 1, 7
      ) = 'sha256:'
      AND length(json_extract(NEW.adapter_proof_json, '$.proof_hash')) = 71
      AND substr(json_extract(NEW.adapter_proof_json, '$.proof_hash'), 1, 7)
          = 'sha256:'
      AND (
          (NEW.result = 'cancel_confirmed'
           AND json_extract(NEW.adapter_proof_json, '$.state') = 'cancelled'
           AND json_extract(NEW.adapter_proof_json, '$.code') = 'CANCEL_COMPLETED'
           AND json_type(
               NEW.adapter_proof_json, '$.capability_record_hash'
           ) = 'text'
           AND json_type(NEW.adapter_proof_json, '$.request_record_hash') = 'text'
           AND json_type(
               NEW.adapter_proof_json, '$.acknowledgement_record_hash'
           ) = 'text')
          OR
          (NEW.result = 'terminal_preempted'
           AND json_extract(NEW.adapter_proof_json, '$.state') = 'terminal_won'
           AND json_extract(NEW.adapter_proof_json, '$.code') = 'CANCEL_TERMINAL_WON')
      )
      AND event.sequence = (
          SELECT MAX(latest.sequence)
          FROM run_events AS latest
          WHERE latest.task_id = task.task_id
      )
      AND event.task_status = NEW.terminal_status
      AND (
          (NEW.result = 'cancel_confirmed'
           AND NEW.terminal_status = 'Cancelled'
           AND event.event_type = 'task_cancelled')
          OR
          (NEW.result = 'terminal_preempted'
           AND NEW.terminal_status = 'Succeeded'
           AND event.event_type = 'node_succeeded')
          OR
          (NEW.result = 'terminal_preempted'
           AND NEW.terminal_status = 'Failed'
           AND event.event_type = 'node_failed')
      )
)
BEGIN
    SELECT RAISE(ABORT, 'cancel outcome requires its exact terminal event');
END;

CREATE TRIGGER task_cancel_outcome_requires_active_term
BEFORE INSERT ON task_cancel_outcomes
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
    SELECT RAISE(ABORT, 'cancel outcome requires the active term');
END;

CREATE TRIGGER task_cancel_requests_are_immutable
BEFORE UPDATE ON task_cancel_requests
BEGIN
    SELECT RAISE(ABORT, 'cancel requests are immutable');
END;

CREATE TRIGGER task_cancel_requests_cannot_be_deleted
BEFORE DELETE ON task_cancel_requests
BEGIN
    SELECT RAISE(ABORT, 'cancel requests are immutable');
END;

CREATE TRIGGER supervised_cancel_attempts_are_immutable
BEFORE UPDATE ON supervised_cancel_attempts
BEGIN
    SELECT RAISE(ABORT, 'supervised cancel attempts are immutable');
END;

CREATE TRIGGER supervised_cancel_attempts_cannot_be_deleted
BEFORE DELETE ON supervised_cancel_attempts
BEGIN
    SELECT RAISE(ABORT, 'supervised cancel attempts are immutable');
END;

CREATE TRIGGER task_cancel_outcomes_are_immutable
BEFORE UPDATE ON task_cancel_outcomes
BEGIN
    SELECT RAISE(ABORT, 'cancel outcomes are immutable');
END;

CREATE TRIGGER task_cancel_outcomes_cannot_be_deleted
BEFORE DELETE ON task_cancel_outcomes
BEGIN
    SELECT RAISE(ABORT, 'cancel outcomes are immutable');
END;
