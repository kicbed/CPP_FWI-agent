-- Resolve only positively proved existing dispatches.  The original
-- reconciliation_required dispatch_outcomes row remains immutable; this
-- migration adds an append-only effective receipt projected from exact
-- managed Worker evidence or one exact legacy-private receipt.

CREATE TABLE supervised_dispatch_reconciliation_attempts (
    intent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    evidence_kind TEXT NOT NULL CHECK (
        evidence_kind IN ('managed_worker_receipt', 'private_receipt')
    ),
    source_outcome_hash TEXT NOT NULL CHECK (
        length(source_outcome_hash) = 71
        AND substr(source_outcome_hash, 1, 7) = 'sha256:'
    ),
    authorized_at TEXT NOT NULL,
    authorized_at_us INTEGER NOT NULL CHECK (
        typeof(authorized_at_us) = 'integer' AND authorized_at_us >= 0
    ),
    PRIMARY KEY (intent_id, fencing_token, evidence_kind),
    FOREIGN KEY (intent_id) REFERENCES dispatch_outcomes(intent_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE dispatch_reconciliation_resolutions (
    intent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    source_outcome_hash TEXT NOT NULL CHECK (
        length(source_outcome_hash) = 71
        AND substr(source_outcome_hash, 1, 7) = 'sha256:'
    ),
    result TEXT NOT NULL CHECK (result = 'dispatched'),
    evidence_kind TEXT NOT NULL CHECK (
        evidence_kind IN ('managed_worker_receipt', 'private_receipt')
    ),
    handle_json TEXT NOT NULL,
    handle_hash TEXT NOT NULL CHECK (
        length(handle_hash) = 71
        AND substr(handle_hash, 1, 7) = 'sha256:'
    ),
    evidence_record_hash TEXT NOT NULL CHECK (
        length(evidence_record_hash) = 71
        AND substr(evidence_record_hash, 1, 7) = 'sha256:'
    ),
    attempt_id TEXT,
    observation_sequence INTEGER,
    private_schema_version TEXT,
    receipt_record_hash TEXT,
    effective_outcome_json TEXT NOT NULL,
    effective_outcome_hash TEXT NOT NULL CHECK (
        length(effective_outcome_hash) = 71
        AND substr(effective_outcome_hash, 1, 7) = 'sha256:'
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
    CHECK (
        (evidence_kind = 'managed_worker_receipt'
         AND attempt_id IS NOT NULL
         AND typeof(observation_sequence) = 'integer'
         AND observation_sequence >= 1
         AND private_schema_version IS NULL
         AND receipt_record_hash IS NULL)
        OR
        (evidence_kind = 'private_receipt'
         AND attempt_id IS NULL
         AND observation_sequence IS NULL
         AND private_schema_version = '1.0.0'
         AND receipt_record_hash = evidence_record_hash)
    ),
    FOREIGN KEY (intent_id) REFERENCES dispatch_outcomes(intent_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (intent_id, fencing_token, evidence_kind)
        REFERENCES supervised_dispatch_reconciliation_attempts(
            intent_id, fencing_token, evidence_kind
        ),
    FOREIGN KEY (attempt_id, observation_sequence)
        REFERENCES worker_attempt_observations(
            attempt_id, observation_sequence
        ),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_supervised_dispatch_reconciliation_attempts_term
    ON supervised_dispatch_reconciliation_attempts(
        project_id, principal_id, fencing_token, intent_id
    );

CREATE INDEX idx_dispatch_reconciliation_resolutions_scope
    ON dispatch_reconciliation_resolutions(
        project_id, principal_id, task_id, intent_id
    );

CREATE VIEW effective_dispatched_intents AS
SELECT intent_id,
       document_json AS outcome_document_json,
       document_hash AS outcome_document_hash,
       recorded_at,
       'direct' AS source
FROM dispatch_outcomes
WHERE outcome = 'dispatched'
UNION ALL
SELECT intent_id,
       effective_outcome_json AS outcome_document_json,
       effective_outcome_hash AS outcome_document_hash,
       resolved_at AS recorded_at,
       'reconciliation' AS source
FROM dispatch_reconciliation_resolutions
WHERE result = 'dispatched';

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
      AND intent.adapter_version = '1.4.0'
      AND json_extract(intent.request_json, '$.request.algorithm.id')
          = 'deepwave.acoustic_fwi'
      AND json_extract(intent.request_json, '$.request.algorithm.version')
          = '1.4.0'
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

CREATE TRIGGER supervised_dispatch_reconciliation_requires_active_term
BEFORE INSERT ON supervised_dispatch_reconciliation_attempts
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
    SELECT RAISE(ABORT, 'dispatch reconciliation requires the active term');
END;

-- Existing adoption tables remain the proof-specific audit.  During a
-- reconciliation transaction the adoption is inserted before the resolution;
-- the matching authorization closes that temporary ordering window inside the
-- same BEGIN IMMEDIATE transaction.
DROP TRIGGER supervised_dispatch_adoption_requires_matching_attempt;

CREATE TRIGGER supervised_dispatch_adoption_requires_matching_attempt
BEFORE INSERT ON supervised_dispatch_adoptions
WHEN NOT EXISTS (
    SELECT 1
    FROM worker_launch_attempts AS attempt
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.intent_id = NEW.intent_id
      AND attempt.project_id = NEW.project_id
      AND attempt.principal_id = NEW.principal_id
      AND observation.observation_sequence = (
          SELECT MAX(latest.observation_sequence)
          FROM worker_attempt_observations AS latest
          WHERE latest.attempt_id = attempt.attempt_id
      )
      AND observation.ready_record_hash IS NOT NULL
      AND observation.heartbeat_record_hash IS NOT NULL
      AND (
          EXISTS (
              SELECT 1 FROM dispatch_outcomes AS outcome
              WHERE outcome.intent_id = attempt.intent_id
                AND outcome.outcome = 'dispatched'
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_outcomes AS outcome
              JOIN supervised_dispatch_reconciliation_attempts AS authorization
                ON authorization.intent_id = outcome.intent_id
               AND authorization.fencing_token = NEW.fencing_token
               AND authorization.evidence_kind = 'managed_worker_receipt'
               AND authorization.source_outcome_hash = outcome.document_hash
              WHERE outcome.intent_id = attempt.intent_id
                AND outcome.outcome = 'reconciliation_required'
                AND NOT EXISTS (
                    SELECT 1
                    FROM dispatch_reconciliation_resolutions AS resolution
                    WHERE resolution.intent_id = outcome.intent_id
                )
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'dispatch adoption must match exact Worker evidence');
END;

DROP TRIGGER supervised_private_receipt_requires_exact_outcome;

CREATE TRIGGER supervised_private_receipt_requires_exact_outcome
BEFORE INSERT ON supervised_private_receipt_adoptions
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_outcomes AS outcome
    JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE outcome.intent_id = NEW.intent_id
      AND intent.adapter_id = 'fwi.deepwave_adapter'
      AND intent.adapter_version = '1.4.0'
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Queued'
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM worker_launch_attempts AS attempt
          WHERE attempt.intent_id = intent.intent_id
      )
      AND (
          (outcome.outcome = 'dispatched'
           AND outcome.document_hash = NEW.outcome_document_hash)
          OR
          (outcome.outcome = 'reconciliation_required'
           AND EXISTS (
               SELECT 1
               FROM supervised_dispatch_reconciliation_attempts AS authorization
               WHERE authorization.intent_id = outcome.intent_id
                 AND authorization.fencing_token = NEW.fencing_token
                 AND authorization.evidence_kind = 'private_receipt'
                 AND authorization.source_outcome_hash = outcome.document_hash
           )
           AND NOT EXISTS (
               SELECT 1
               FROM dispatch_reconciliation_resolutions AS resolution
               WHERE resolution.intent_id = outcome.intent_id
           ))
      )
)
BEGIN
    SELECT RAISE(ABORT, 'private receipt adoption must match its exact outcome');
END;

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
      AND intent.adapter_version = '1.4.0'
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

CREATE TRIGGER dispatch_reconciliation_resolution_requires_active_term
BEFORE INSERT ON dispatch_reconciliation_resolutions
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
    SELECT RAISE(ABORT, 'dispatch reconciliation resolution requires the active term');
END;

CREATE TRIGGER supervised_dispatch_reconciliation_attempts_are_immutable
BEFORE UPDATE ON supervised_dispatch_reconciliation_attempts
BEGIN
    SELECT RAISE(ABORT, 'dispatch reconciliation attempts are immutable');
END;

CREATE TRIGGER supervised_dispatch_reconciliation_attempts_cannot_be_deleted
BEFORE DELETE ON supervised_dispatch_reconciliation_attempts
BEGIN
    SELECT RAISE(ABORT, 'dispatch reconciliation attempts are immutable');
END;

CREATE TRIGGER dispatch_reconciliation_resolutions_are_immutable
BEFORE UPDATE ON dispatch_reconciliation_resolutions
BEGIN
    SELECT RAISE(ABORT, 'dispatch reconciliation resolutions are immutable');
END;

CREATE TRIGGER dispatch_reconciliation_resolutions_cannot_be_deleted
BEFORE DELETE ON dispatch_reconciliation_resolutions
BEGIN
    SELECT RAISE(ABORT, 'dispatch reconciliation resolutions are immutable');
END;

-- Every downstream SQL boundary consumes the same effective-dispatch view.
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
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a resolved terminal task can be moved to trash');
END;
