-- Append-only authorization audit for Supervisor-owned first dispatch.
--
-- This table is deliberately not a Worker lease or a capacity authority.
-- The fixed Adapter's inherited kernel locks remain authoritative for both
-- execution and capacity.  A row only proves that one active control-plane
-- term was allowed to enter the exact Adapter submission state machine.
CREATE TABLE supervised_dispatch_attempts (
    intent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    reason TEXT NOT NULL CHECK (
        reason IN (
            'pending_first_dispatch',
            'dispatching_no_record_takeover',
            'staged_attempt_resume'
        )
    ),
    authorized_at TEXT NOT NULL,
    authorized_at_us INTEGER NOT NULL CHECK (
        typeof(authorized_at_us) = 'integer' AND authorized_at_us >= 0
    ),
    PRIMARY KEY (intent_id, fencing_token),
    FOREIGN KEY (intent_id) REFERENCES dispatch_attempts(intent_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_supervised_dispatch_attempts_term
    ON supervised_dispatch_attempts(
        project_id, principal_id, fencing_token, intent_id
    );

-- Compatibility adoption for current Adapter 1.4 receipts created before the
-- managed launch-control schema.  These rows never authorize a launch and are
-- deliberately disjoint from v9 Worker-evidence adoptions.
CREATE TABLE supervised_private_receipt_adoptions (
    intent_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    private_schema_version TEXT NOT NULL CHECK (
        private_schema_version = '1.0.0'
    ),
    receipt_record_hash TEXT NOT NULL CHECK (
        length(receipt_record_hash) = 71
        AND substr(receipt_record_hash, 1, 7) = 'sha256:'
    ),
    outcome_document_hash TEXT NOT NULL CHECK (
        length(outcome_document_hash) = 71
        AND substr(outcome_document_hash, 1, 7) = 'sha256:'
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    FOREIGN KEY (intent_id) REFERENCES dispatch_outcomes(intent_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_supervised_private_receipt_adoptions_term
    ON supervised_private_receipt_adoptions(
        project_id, principal_id, fencing_token, intent_id
    );

CREATE TRIGGER supervised_private_receipt_requires_exact_outcome
BEFORE INSERT ON supervised_private_receipt_adoptions
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_outcomes AS outcome
    JOIN dispatch_intents AS intent ON intent.intent_id = outcome.intent_id
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE outcome.intent_id = NEW.intent_id
      AND outcome.outcome = 'dispatched'
      AND outcome.document_hash = NEW.outcome_document_hash
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
)
BEGIN
    SELECT RAISE(ABORT, 'private receipt adoption must match its exact outcome');
END;

CREATE TRIGGER supervised_private_receipt_requires_active_term
BEFORE INSERT ON supervised_private_receipt_adoptions
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
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
    SELECT RAISE(ABORT, 'private receipt adoption requires the active term');
END;

CREATE TRIGGER supervised_private_receipt_adoptions_are_immutable
BEFORE UPDATE ON supervised_private_receipt_adoptions
BEGIN
    SELECT RAISE(ABORT, 'private receipt adoptions are immutable');
END;

CREATE TRIGGER supervised_private_receipt_adoptions_cannot_be_deleted
BEFORE DELETE ON supervised_private_receipt_adoptions
BEGIN
    SELECT RAISE(ABORT, 'private receipt adoptions are immutable');
END;

CREATE TRIGGER supervised_dispatch_attempt_requires_matching_intent
BEFORE INSERT ON supervised_dispatch_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE intent.intent_id = NEW.intent_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Queued'
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_outcomes AS outcome
          WHERE outcome.intent_id = intent.intent_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'supervised dispatch must match an active queued intent');
END;

-- A first-dispatch authorization is created in the same transaction and with
-- the same timestamp as the immutable pending claim.  Older claims must use a
-- takeover/resume reason, so direct SQL cannot mislabel their audit history.
CREATE TRIGGER supervised_pending_dispatch_requires_atomic_claim
BEFORE INSERT ON supervised_dispatch_attempts
WHEN NEW.reason = 'pending_first_dispatch'
 AND NOT EXISTS (
     SELECT 1
     FROM dispatch_attempts AS claim
     WHERE claim.intent_id = NEW.intent_id
       AND claim.claimed_at = NEW.authorized_at
       AND NOT EXISTS (
           SELECT 1 FROM worker_launch_attempts AS attempt
           WHERE attempt.intent_id = NEW.intent_id
       )
 )
BEGIN
    SELECT RAISE(ABORT, 'pending dispatch requires its atomic claim');
END;

CREATE TRIGGER supervised_no_record_takeover_requires_no_worker_projection
BEFORE INSERT ON supervised_dispatch_attempts
WHEN NEW.reason = 'dispatching_no_record_takeover'
 AND EXISTS (
     SELECT 1 FROM worker_launch_attempts AS attempt
     WHERE attempt.intent_id = NEW.intent_id
 )
BEGIN
    SELECT RAISE(ABORT, 'no-record takeover conflicts with Worker evidence');
END;

CREATE TRIGGER supervised_staged_resume_requires_exact_projection
BEFORE INSERT ON supervised_dispatch_attempts
WHEN NEW.reason = 'staged_attempt_resume'
 AND NOT EXISTS (
     SELECT 1
     FROM worker_launch_attempts AS attempt
     JOIN worker_attempt_observations AS observation
       ON observation.attempt_id = attempt.attempt_id
     WHERE attempt.intent_id = NEW.intent_id
       AND observation.observation_sequence = (
           SELECT MAX(latest.observation_sequence)
           FROM worker_attempt_observations AS latest
           WHERE latest.attempt_id = attempt.attempt_id
       )
       AND observation.ticket_state = 'staged'
       AND observation.capacity_slot IS NULL
       AND observation.capacity_generation IS NULL
       AND observation.ticket_worker_pid IS NULL
       AND observation.ready_record_hash IS NULL
       AND observation.heartbeat_record_hash IS NULL
 )
BEGIN
    SELECT RAISE(ABORT, 'staged resume requires exact pre-Popen evidence');
END;

CREATE TRIGGER supervised_dispatch_attempt_requires_active_term
BEFORE INSERT ON supervised_dispatch_attempts
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
    SELECT RAISE(ABORT, 'supervised dispatch requires the active term');
END;

CREATE TRIGGER supervised_dispatch_attempts_are_immutable
BEFORE UPDATE ON supervised_dispatch_attempts
BEGIN
    SELECT RAISE(ABORT, 'supervised dispatch attempts are immutable');
END;

CREATE TRIGGER supervised_dispatch_attempts_cannot_be_deleted
BEFORE DELETE ON supervised_dispatch_attempts
BEGIN
    SELECT RAISE(ABORT, 'supervised dispatch attempts are immutable');
END;
