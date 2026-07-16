-- Durable exact-attempt wall-time enforcement for the current managed FWI
-- Adapter.  A window is armed only after the control plane has independently
-- proved the exact Worker's v2 stop capability.  Delivery and terminal
-- resolution remain fenced by the active runtime Supervisor term.

CREATE TABLE worker_attempt_timeout_windows (
    timeout_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    start_observation_sequence INTEGER NOT NULL CHECK (
        typeof(start_observation_sequence) = 'integer'
        AND start_observation_sequence >= 1
    ),
    wall_time_seconds INTEGER NOT NULL CHECK (
        typeof(wall_time_seconds) = 'integer'
        AND wall_time_seconds BETWEEN 1 AND 86400
    ),
    started_at TEXT NOT NULL,
    started_at_us INTEGER NOT NULL CHECK (
        typeof(started_at_us) = 'integer' AND started_at_us >= 0
    ),
    deadline_at TEXT NOT NULL,
    deadline_at_us INTEGER NOT NULL CHECK (
        typeof(deadline_at_us) = 'integer'
        AND deadline_at_us = started_at_us + wall_time_seconds * 1000000
    ),
    ready_record_hash TEXT NOT NULL CHECK (
        length(ready_record_hash) = 71
        AND substr(ready_record_hash, 1, 7) = 'sha256:'
    ),
    running_heartbeat_record_hash TEXT NOT NULL CHECK (
        length(running_heartbeat_record_hash) = 71
        AND substr(running_heartbeat_record_hash, 1, 7) = 'sha256:'
    ),
    capability_record_hash TEXT NOT NULL CHECK (
        length(capability_record_hash) = 71
        AND substr(capability_record_hash, 1, 7) = 'sha256:'
    ),
    capability_proof_json TEXT NOT NULL,
    capability_proof_hash TEXT NOT NULL CHECK (
        length(capability_proof_hash) = 71
        AND substr(capability_proof_hash, 1, 7) = 'sha256:'
    ),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL CHECK (
        length(document_hash) = 71
        AND substr(document_hash, 1, 7) = 'sha256:'
    ),
    recorded_fencing_token INTEGER NOT NULL CHECK (
        typeof(recorded_fencing_token) = 'integer'
        AND recorded_fencing_token >= 1
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    UNIQUE (timeout_id, intent_id, attempt_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (intent_id, attempt_id)
        REFERENCES worker_launch_attempts(intent_id, attempt_id),
    FOREIGN KEY (attempt_id, start_observation_sequence)
        REFERENCES worker_attempt_observations(
            attempt_id, observation_sequence
        ),
    FOREIGN KEY (project_id, principal_id, recorded_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE supervised_timeout_attempts (
    timeout_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    action TEXT NOT NULL CHECK (action = 'deliver_exact_attempt_timeout'),
    authorized_at TEXT NOT NULL,
    authorized_at_us INTEGER NOT NULL CHECK (
        typeof(authorized_at_us) = 'integer' AND authorized_at_us >= 0
    ),
    PRIMARY KEY (timeout_id, fencing_token),
    UNIQUE (timeout_id, intent_id, attempt_id, fencing_token),
    FOREIGN KEY (timeout_id, intent_id, attempt_id)
        REFERENCES worker_attempt_timeout_windows(
            timeout_id, intent_id, attempt_id
        ),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE task_timeout_outcomes (
    timeout_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    result TEXT NOT NULL CHECK (
        result IN ('timeout_confirmed', 'terminal_preempted')
    ),
    terminal_status TEXT NOT NULL CHECK (
        terminal_status IN ('Succeeded', 'Failed')
    ),
    failure_code TEXT CHECK (
        (result = 'timeout_confirmed'
         AND terminal_status = 'Failed'
         AND failure_code = 'WALL_TIME_EXCEEDED')
        OR
        (result = 'terminal_preempted' AND failure_code IS NULL)
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
    FOREIGN KEY (timeout_id, intent_id, attempt_id)
        REFERENCES worker_attempt_timeout_windows(
            timeout_id, intent_id, attempt_id
        ),
    FOREIGN KEY (timeout_id, fencing_token)
        REFERENCES supervised_timeout_attempts(timeout_id, fencing_token),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (task_id, terminal_event_sequence)
        REFERENCES run_events(task_id, sequence)
);

CREATE INDEX idx_worker_attempt_timeout_windows_scope_deadline
    ON worker_attempt_timeout_windows(
        project_id, principal_id, deadline_at_us, task_id
    );

CREATE INDEX idx_worker_attempt_timeout_windows_task
    ON worker_attempt_timeout_windows(task_id, attempt_id);

CREATE INDEX idx_supervised_timeout_attempts_term
    ON supervised_timeout_attempts(
        project_id, principal_id, fencing_token, timeout_id
    );

CREATE TRIGGER worker_attempt_timeout_window_requires_exact_start
BEFORE INSERT ON worker_attempt_timeout_windows
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
     AND observation.observation_sequence = NEW.start_observation_sequence
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
      AND json_extract(
          NEW.capability_proof_json, '$.private_schema_version'
      ) = '1.1.0'
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

CREATE TRIGGER worker_attempt_timeout_window_requires_active_term
BEFORE INSERT ON worker_attempt_timeout_windows
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.recorded_fencing_token
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
    SELECT RAISE(ABORT, 'timeout window requires the active term');
END;

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

CREATE TRIGGER supervised_timeout_attempt_requires_active_term
BEFORE INSERT ON supervised_timeout_attempts
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
    SELECT RAISE(ABORT, 'supervised timeout requires the active term');
END;

-- The durable first delivery authorization owns the exact Worker's one v2
-- stop slot.  An armed-but-not-due window deliberately does not block a user
-- cancellation request.
CREATE TRIGGER task_cancel_request_rejects_authorized_timeout
BEFORE INSERT ON task_cancel_requests
WHEN EXISTS (
    SELECT 1
    FROM worker_attempt_timeout_windows AS timeout
    JOIN supervised_timeout_attempts AS delivery
      ON delivery.timeout_id = timeout.timeout_id
    WHERE timeout.task_id = NEW.task_id
)
BEGIN
    SELECT RAISE(ABORT, 'authorized timeout already owns the exact stop slot');
END;

CREATE TRIGGER task_timeout_outcome_requires_terminal_event
BEFORE INSERT ON task_timeout_outcomes
WHEN NOT EXISTS (
    SELECT 1
    FROM worker_attempt_timeout_windows AS timeout
    JOIN supervised_timeout_attempts AS delivery
      ON delivery.timeout_id = timeout.timeout_id
     AND delivery.fencing_token = NEW.fencing_token
    JOIN tasks AS task ON task.task_id = timeout.task_id
    JOIN run_events AS event
      ON event.task_id = task.task_id
     AND event.sequence = NEW.terminal_event_sequence
    WHERE timeout.timeout_id = NEW.timeout_id
      AND timeout.task_id = NEW.task_id
      AND timeout.project_id = NEW.project_id
      AND timeout.principal_id = NEW.principal_id
      AND timeout.intent_id = NEW.intent_id
      AND timeout.attempt_id = NEW.attempt_id
      AND delivery.project_id = NEW.project_id
      AND delivery.principal_id = NEW.principal_id
      AND delivery.intent_id = NEW.intent_id
      AND delivery.attempt_id = NEW.attempt_id
      AND NEW.resolved_at_us >= timeout.deadline_at_us
      AND NEW.resolved_at_us >= delivery.authorized_at_us
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = NEW.terminal_status
      AND json_valid(NEW.adapter_proof_json)
      AND json_type(NEW.adapter_proof_json, '$') = 'object'
      AND json_extract(NEW.adapter_proof_json, '$.schema_version') = '1.0.0'
      AND json_extract(NEW.adapter_proof_json, '$.task_id') = NEW.task_id
      AND json_extract(NEW.adapter_proof_json, '$.request_id') = NEW.timeout_id
      AND json_extract(NEW.adapter_proof_json, '$.reason')
          = 'wall_time_exceeded'
      AND json_extract(NEW.adapter_proof_json, '$.attempt_id')
          = NEW.attempt_id
      AND json_extract(NEW.adapter_proof_json, '$.wall_time_seconds')
          = timeout.wall_time_seconds
      AND json_extract(NEW.adapter_proof_json, '$.started_at')
          = timeout.started_at
      AND json_extract(NEW.adapter_proof_json, '$.deadline_at')
          = timeout.deadline_at
      AND json_extract(NEW.adapter_proof_json, '$.ready_record_hash')
          = timeout.ready_record_hash
      AND json_extract(
          NEW.adapter_proof_json, '$.capability_record_hash'
      ) = timeout.capability_record_hash
      AND json_extract(NEW.adapter_proof_json, '$.terminal_status')
          = NEW.terminal_status
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
          SELECT MAX(latest.sequence)
          FROM run_events AS latest
          WHERE latest.task_id = task.task_id
      )
      AND event.task_status = NEW.terminal_status
      AND (
          (NEW.result = 'timeout_confirmed'
           AND NEW.terminal_status = 'Failed'
           AND NEW.failure_code = 'WALL_TIME_EXCEEDED'
           AND event.event_type = 'node_failed'
           AND json_extract(event.document_json, '$.error.code')
               = 'wall_time_exceeded'
           AND json_extract(NEW.adapter_proof_json, '$.state') = 'timed_out'
           AND json_extract(NEW.adapter_proof_json, '$.code')
               = 'TIMEOUT_COMPLETED'
           AND json_extract(
               NEW.adapter_proof_json, '$.terminal_failure_code'
           ) = 'WALL_TIME_EXCEEDED'
           AND json_type(
               NEW.adapter_proof_json, '$.request_record_hash'
           ) = 'text'
           AND json_type(
               NEW.adapter_proof_json, '$.acknowledgement_record_hash'
           ) = 'text')
          OR
          (NEW.result = 'terminal_preempted'
           AND NEW.failure_code IS NULL
           AND NEW.terminal_status IN ('Succeeded', 'Failed')
           AND json_extract(NEW.adapter_proof_json, '$.state')
               = 'terminal_won'
           AND json_extract(NEW.adapter_proof_json, '$.code')
               = 'TIMEOUT_TERMINAL_WON'
           AND json_type(
               NEW.adapter_proof_json, '$.terminal_failure_code'
           ) = 'null'
           AND ((NEW.terminal_status = 'Succeeded'
                 AND event.event_type = 'node_succeeded')
                OR
                (NEW.terminal_status = 'Failed'
                 AND event.event_type = 'node_failed'
                 AND json_extract(event.document_json, '$.error.code')
                     != 'wall_time_exceeded')))
      )
)
BEGIN
    SELECT RAISE(ABORT, 'timeout outcome requires its exact terminal event');
END;

CREATE TRIGGER task_timeout_outcome_requires_active_term
BEFORE INSERT ON task_timeout_outcomes
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
    SELECT RAISE(ABORT, 'timeout outcome requires the active term');
END;

CREATE TRIGGER worker_attempt_timeout_windows_are_immutable
BEFORE UPDATE ON worker_attempt_timeout_windows
BEGIN
    SELECT RAISE(ABORT, 'timeout windows are immutable');
END;

CREATE TRIGGER worker_attempt_timeout_windows_cannot_be_deleted
BEFORE DELETE ON worker_attempt_timeout_windows
BEGIN
    SELECT RAISE(ABORT, 'timeout windows are immutable');
END;

CREATE TRIGGER supervised_timeout_attempts_are_immutable
BEFORE UPDATE ON supervised_timeout_attempts
BEGIN
    SELECT RAISE(ABORT, 'supervised timeout attempts are immutable');
END;

CREATE TRIGGER supervised_timeout_attempts_cannot_be_deleted
BEFORE DELETE ON supervised_timeout_attempts
BEGIN
    SELECT RAISE(ABORT, 'supervised timeout attempts are immutable');
END;

CREATE TRIGGER task_timeout_outcomes_are_immutable
BEFORE UPDATE ON task_timeout_outcomes
BEGIN
    SELECT RAISE(ABORT, 'timeout outcomes are immutable');
END;

CREATE TRIGGER task_timeout_outcomes_cannot_be_deleted
BEFORE DELETE ON task_timeout_outcomes
BEGIN
    SELECT RAISE(ABORT, 'timeout outcomes are immutable');
END;
