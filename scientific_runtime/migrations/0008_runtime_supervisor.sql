CREATE TABLE runtime_supervisor_terms (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    acquired_at_us INTEGER NOT NULL CHECK (
        typeof(acquired_at_us) = 'integer' AND acquired_at_us >= 0
    ),
    initial_expires_at TEXT NOT NULL,
    initial_expires_at_us INTEGER NOT NULL CHECK (
        typeof(initial_expires_at_us) = 'integer'
        AND initial_expires_at_us > acquired_at_us
    ),
    PRIMARY KEY (project_id, principal_id, fencing_token),
    UNIQUE (
        project_id, principal_id, fencing_token,
        owner_id, acquired_at, acquired_at_us
    )
);

CREATE TABLE runtime_supervisor_leases (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    acquired_at_us INTEGER NOT NULL CHECK (
        typeof(acquired_at_us) = 'integer' AND acquired_at_us >= 0
    ),
    heartbeat_at TEXT NOT NULL,
    heartbeat_at_us INTEGER NOT NULL CHECK (
        typeof(heartbeat_at_us) = 'integer' AND heartbeat_at_us >= acquired_at_us
    ),
    expires_at TEXT NOT NULL,
    expires_at_us INTEGER NOT NULL CHECK (
        typeof(expires_at_us) = 'integer' AND expires_at_us > heartbeat_at_us
    ),
    PRIMARY KEY (project_id, principal_id),
    FOREIGN KEY (
        project_id, principal_id, fencing_token,
        owner_id, acquired_at, acquired_at_us
    ) REFERENCES runtime_supervisor_terms(
        project_id, principal_id, fencing_token,
        owner_id, acquired_at, acquired_at_us
    )
);

CREATE TABLE runtime_supervisor_term_closures (
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    reason TEXT NOT NULL CHECK (reason IN ('released', 'expired_takeover')),
    final_heartbeat_at TEXT NOT NULL,
    final_heartbeat_at_us INTEGER NOT NULL CHECK (
        typeof(final_heartbeat_at_us) = 'integer'
        AND final_heartbeat_at_us >= 0
    ),
    final_expires_at TEXT NOT NULL,
    final_expires_at_us INTEGER NOT NULL CHECK (
        typeof(final_expires_at_us) = 'integer'
        AND final_expires_at_us > final_heartbeat_at_us
    ),
    closed_at TEXT NOT NULL,
    closed_at_us INTEGER NOT NULL CHECK (
        typeof(closed_at_us) = 'integer'
        AND closed_at_us >= final_heartbeat_at_us
    ),
    PRIMARY KEY (project_id, principal_id, fencing_token),
    CHECK (reason != 'expired_takeover' OR closed_at_us >= final_expires_at_us),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE TABLE supervised_run_event_commits (
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (task_id, sequence),
    FOREIGN KEY (task_id, sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_runtime_supervisor_leases_expiry
    ON runtime_supervisor_leases(expires_at_us);

CREATE INDEX idx_supervised_run_event_commits_term
    ON supervised_run_event_commits(
        project_id, principal_id, fencing_token, task_id, sequence
    );

CREATE TRIGGER runtime_supervisor_terms_are_append_only
BEFORE UPDATE ON runtime_supervisor_terms
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor terms are append-only');
END;

CREATE TRIGGER runtime_supervisor_terms_cannot_be_deleted
BEFORE DELETE ON runtime_supervisor_terms
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor terms are append-only');
END;

CREATE TRIGGER runtime_supervisor_term_closures_are_append_only
BEFORE UPDATE ON runtime_supervisor_term_closures
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor term closures are append-only');
END;

CREATE TRIGGER runtime_supervisor_term_closures_cannot_be_deleted
BEFORE DELETE ON runtime_supervisor_term_closures
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor term closures are append-only');
END;

CREATE TRIGGER runtime_supervisor_lease_scope_is_immutable
BEFORE UPDATE OF project_id, principal_id ON runtime_supervisor_leases
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor lease scope is immutable');
END;

CREATE TRIGGER runtime_supervisor_lease_fence_is_contiguous
BEFORE UPDATE OF fencing_token ON runtime_supervisor_leases
WHEN NEW.fencing_token != OLD.fencing_token + 1
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor fencing token must advance once');
END;

CREATE TRIGGER runtime_supervisor_lease_term_is_immutable
BEFORE UPDATE ON runtime_supervisor_leases
WHEN NEW.fencing_token = OLD.fencing_token
 AND (
    NEW.owner_id != OLD.owner_id
    OR NEW.acquired_at != OLD.acquired_at
    OR NEW.acquired_at_us != OLD.acquired_at_us
 )
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor lease term is immutable');
END;

CREATE TRIGGER runtime_supervisor_heartbeat_is_monotonic
BEFORE UPDATE ON runtime_supervisor_leases
WHEN NEW.fencing_token = OLD.fencing_token
 AND (
    NEW.heartbeat_at_us < OLD.heartbeat_at_us
    OR NEW.expires_at_us < OLD.expires_at_us
 )
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor lease cannot regress');
END;

CREATE TRIGGER runtime_supervisor_leases_cannot_be_deleted
BEFORE DELETE ON runtime_supervisor_leases
BEGIN
    SELECT RAISE(ABORT, 'runtime supervisor leases cannot be deleted');
END;

CREATE TRIGGER supervised_run_event_commit_requires_active_term
BEFORE INSERT ON supervised_run_event_commits
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND lease.heartbeat_at_us <= NEW.recorded_at_us
      AND lease.expires_at_us > NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1
          FROM runtime_supervisor_term_closures AS closure
          WHERE closure.project_id = lease.project_id
            AND closure.principal_id = lease.principal_id
            AND closure.fencing_token = lease.fencing_token
      )
)
BEGIN
    SELECT RAISE(ABORT, 'supervised run event requires the active term');
END;

CREATE TRIGGER supervised_run_event_commits_are_append_only
BEFORE UPDATE ON supervised_run_event_commits
BEGIN
    SELECT RAISE(ABORT, 'supervised run event commits are append-only');
END;

CREATE TRIGGER supervised_run_event_commits_cannot_be_deleted
BEFORE DELETE ON supervised_run_event_commits
BEGIN
    SELECT RAISE(ABORT, 'supervised run event commits are append-only');
END;
