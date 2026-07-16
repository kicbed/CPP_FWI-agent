CREATE TABLE worker_launch_attempts (
    attempt_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (
        typeof(attempt_number) = 'integer' AND attempt_number >= 1
    ),
    submission_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    adapter_request_hash TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    first_fencing_token INTEGER NOT NULL CHECK (
        typeof(first_fencing_token) = 'integer' AND first_fencing_token >= 1
    ),
    first_observed_at TEXT NOT NULL,
    first_observed_at_us INTEGER NOT NULL CHECK (
        typeof(first_observed_at_us) = 'integer' AND first_observed_at_us >= 0
    ),
    UNIQUE (intent_id, attempt_number),
    UNIQUE (intent_id, attempt_id),
    FOREIGN KEY (intent_id) REFERENCES dispatch_attempts(intent_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, first_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

-- Append one exact row per Supervisor sample.  The Supervisor uses an
-- independent, lower-frequency cadence for already-dispatched attempts so
-- this audit does not inherit the Worker's one-second heartbeat frequency.
CREATE TABLE worker_attempt_observations (
    attempt_id TEXT NOT NULL,
    observation_sequence INTEGER NOT NULL CHECK (
        typeof(observation_sequence) = 'integer'
        AND observation_sequence >= 1
    ),
    ticket_state TEXT NOT NULL CHECK (
        ticket_state IN ('staged', 'leased', 'spawned', 'failed')
    ),
    capacity_slot INTEGER,
    capacity_generation INTEGER,
    ticket_worker_pid INTEGER,
    ticket_updated_at TEXT NOT NULL,
    ticket_record_hash TEXT NOT NULL,
    ready_worker_pid INTEGER,
    ready_started_at TEXT,
    ready_record_hash TEXT,
    heartbeat_sequence INTEGER,
    heartbeat_state TEXT,
    heartbeat_updated_at TEXT,
    heartbeat_record_hash TEXT,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    observed_at TEXT NOT NULL,
    observed_at_us INTEGER NOT NULL CHECK (
        typeof(observed_at_us) = 'integer' AND observed_at_us >= 0
    ),
    PRIMARY KEY (attempt_id, observation_sequence),
    UNIQUE (attempt_id, document_hash),
    CHECK (
        (ticket_state = 'staged'
         AND capacity_slot IS NULL
         AND capacity_generation IS NULL
         AND ticket_worker_pid IS NULL)
        OR
        (ticket_state = 'leased'
         AND typeof(capacity_slot) = 'integer' AND capacity_slot >= 0
         AND typeof(capacity_generation) = 'integer'
         AND capacity_generation >= 1
         AND ticket_worker_pid IS NULL)
        OR
        (ticket_state = 'spawned'
         AND typeof(capacity_slot) = 'integer' AND capacity_slot >= 0
         AND typeof(capacity_generation) = 'integer'
         AND capacity_generation >= 1
         AND typeof(ticket_worker_pid) = 'integer' AND ticket_worker_pid >= 1)
        OR
        (ticket_state = 'failed'
         AND ticket_worker_pid IS NULL
         AND ((capacity_slot IS NULL AND capacity_generation IS NULL)
              OR (typeof(capacity_slot) = 'integer' AND capacity_slot >= 0
                  AND typeof(capacity_generation) = 'integer'
                  AND capacity_generation >= 1)))
    ),
    CHECK (
        (ready_worker_pid IS NULL
         AND ready_started_at IS NULL
         AND ready_record_hash IS NULL)
        OR
        (ticket_state = 'spawned'
         AND typeof(ready_worker_pid) = 'integer' AND ready_worker_pid >= 1
         AND ready_worker_pid = ticket_worker_pid
         AND ready_started_at IS NOT NULL
         AND ready_record_hash IS NOT NULL)
    ),
    CHECK (
        (heartbeat_sequence IS NULL
         AND heartbeat_state IS NULL
         AND heartbeat_updated_at IS NULL
         AND heartbeat_record_hash IS NULL)
        OR
        (ready_record_hash IS NOT NULL
         AND typeof(heartbeat_sequence) = 'integer'
         AND heartbeat_sequence >= 1
         AND heartbeat_state IS NOT NULL
         AND heartbeat_state IN ('running', 'succeeded', 'failed', 'stopped')
         AND heartbeat_updated_at IS NOT NULL
         AND heartbeat_record_hash IS NOT NULL)
    ),
    CHECK (
        ready_record_hash IS NULL OR heartbeat_record_hash IS NOT NULL
    ),
    FOREIGN KEY (attempt_id) REFERENCES worker_launch_attempts(attempt_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE supervised_dispatch_adoptions (
    intent_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    FOREIGN KEY (intent_id) REFERENCES dispatch_outcomes(intent_id),
    FOREIGN KEY (attempt_id) REFERENCES worker_launch_attempts(attempt_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_worker_launch_attempts_intent
    ON worker_launch_attempts(intent_id, attempt_number);

CREATE INDEX idx_worker_attempt_observations_term
    ON worker_attempt_observations(
        project_id, principal_id, fencing_token, attempt_id,
        observation_sequence
    );

CREATE TRIGGER worker_launch_attempt_requires_matching_intent
BEFORE INSERT ON worker_launch_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM dispatch_intents AS intent
    JOIN tasks AS task ON task.task_id = intent.task_id
    WHERE intent.intent_id = NEW.intent_id
      AND intent.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
)
BEGIN
    SELECT RAISE(ABORT, 'worker attempt must match its durable intent scope');
END;

CREATE TRIGGER worker_launch_attempt_requires_active_term
BEFORE INSERT ON worker_launch_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.first_fencing_token
      AND lease.heartbeat_at_us <= NEW.first_observed_at_us
      AND lease.expires_at_us > NEW.first_observed_at_us
      AND NOT EXISTS (
          SELECT 1 FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'worker attempt requires the active term');
END;

CREATE TRIGGER worker_attempt_observation_requires_matching_attempt
BEFORE INSERT ON worker_attempt_observations
WHEN NOT EXISTS (
    SELECT 1 FROM worker_launch_attempts AS attempt
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.project_id = NEW.project_id
      AND attempt.principal_id = NEW.principal_id
)
BEGIN
    SELECT RAISE(ABORT, 'worker observation must match its attempt scope');
END;

CREATE TRIGGER worker_attempt_observation_requires_active_term
BEFORE INSERT ON worker_attempt_observations
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
    SELECT RAISE(ABORT, 'worker observation requires the active term');
END;

CREATE TRIGGER worker_attempt_observation_sequence_is_contiguous
BEFORE INSERT ON worker_attempt_observations
WHEN NEW.observation_sequence != (
    SELECT COALESCE(MAX(observation_sequence), 0) + 1
    FROM worker_attempt_observations
    WHERE attempt_id = NEW.attempt_id
)
BEGIN
    SELECT RAISE(ABORT, 'worker observation sequence must advance once');
END;

CREATE TRIGGER worker_attempt_observation_cannot_regress
BEFORE INSERT ON worker_attempt_observations
WHEN EXISTS (
    SELECT 1
    FROM worker_attempt_observations AS prior
    WHERE prior.attempt_id = NEW.attempt_id
      AND prior.observation_sequence = (
          SELECT MAX(latest.observation_sequence)
          FROM worker_attempt_observations AS latest
          WHERE latest.attempt_id = NEW.attempt_id
      )
      AND (
          (prior.ready_record_hash IS NOT NULL
           AND NEW.ready_record_hash IS NULL)
          OR
          (prior.ready_record_hash IS NOT NULL
           AND (prior.ready_worker_pid != NEW.ready_worker_pid
                OR prior.ready_started_at != NEW.ready_started_at
                OR prior.ready_record_hash != NEW.ready_record_hash))
          OR
          (prior.heartbeat_sequence IS NOT NULL
           AND NEW.heartbeat_sequence IS NULL)
          OR
          (prior.heartbeat_sequence IS NOT NULL
           AND NEW.heartbeat_sequence < prior.heartbeat_sequence)
          OR
          (prior.heartbeat_sequence = NEW.heartbeat_sequence
           AND prior.heartbeat_record_hash != NEW.heartbeat_record_hash)
          OR
          (prior.heartbeat_state IN ('succeeded', 'failed', 'stopped'))
          OR
          (prior.ticket_state = NEW.ticket_state
           AND prior.ticket_record_hash != NEW.ticket_record_hash)
          OR
          (prior.ticket_state = 'failed')
          OR
          (prior.ticket_state = 'leased' AND NEW.ticket_state = 'staged')
          OR
          (prior.ticket_state = 'spawned'
           AND NEW.ticket_state NOT IN ('spawned', 'failed'))
          OR
          (prior.capacity_slot IS NOT NULL
           AND (NEW.capacity_slot IS NULL
                OR NEW.capacity_slot != prior.capacity_slot
                OR NEW.capacity_generation != prior.capacity_generation))
          OR
          (prior.ticket_worker_pid IS NOT NULL
           AND NEW.ticket_state != 'failed'
           AND NEW.ticket_worker_pid != prior.ticket_worker_pid)
      )
)
BEGIN
    SELECT RAISE(ABORT, 'worker observation cannot regress or follow terminal evidence');
END;

CREATE TRIGGER supervised_dispatch_adoption_requires_matching_attempt
BEFORE INSERT ON supervised_dispatch_adoptions
WHEN NOT EXISTS (
    SELECT 1
    FROM worker_launch_attempts AS attempt
    JOIN dispatch_outcomes AS outcome ON outcome.intent_id = attempt.intent_id
    JOIN worker_attempt_observations AS observation
      ON observation.attempt_id = attempt.attempt_id
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.intent_id = NEW.intent_id
      AND attempt.project_id = NEW.project_id
      AND attempt.principal_id = NEW.principal_id
      AND outcome.outcome = 'dispatched'
      AND observation.ready_record_hash IS NOT NULL
      AND observation.heartbeat_record_hash IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'dispatch adoption must match exact Worker evidence');
END;

CREATE TRIGGER supervised_dispatch_adoption_requires_active_term
BEFORE INSERT ON supervised_dispatch_adoptions
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
    SELECT RAISE(ABORT, 'dispatch adoption requires the active term');
END;

CREATE TRIGGER worker_launch_attempts_are_append_only
BEFORE UPDATE ON worker_launch_attempts
BEGIN
    SELECT RAISE(ABORT, 'worker launch attempts are append-only');
END;

CREATE TRIGGER worker_launch_attempts_cannot_be_deleted
BEFORE DELETE ON worker_launch_attempts
BEGIN
    SELECT RAISE(ABORT, 'worker launch attempts are append-only');
END;

CREATE TRIGGER worker_attempt_observations_are_append_only
BEFORE UPDATE ON worker_attempt_observations
BEGIN
    SELECT RAISE(ABORT, 'worker attempt observations are append-only');
END;

CREATE TRIGGER worker_attempt_observations_cannot_be_deleted
BEFORE DELETE ON worker_attempt_observations
BEGIN
    SELECT RAISE(ABORT, 'worker attempt observations are append-only');
END;

CREATE TRIGGER supervised_dispatch_adoptions_are_append_only
BEFORE UPDATE ON supervised_dispatch_adoptions
BEGIN
    SELECT RAISE(ABORT, 'supervised dispatch adoptions are append-only');
END;

CREATE TRIGGER supervised_dispatch_adoptions_cannot_be_deleted
BEFORE DELETE ON supervised_dispatch_adoptions
BEGIN
    SELECT RAISE(ABORT, 'supervised dispatch adoptions are append-only');
END;
