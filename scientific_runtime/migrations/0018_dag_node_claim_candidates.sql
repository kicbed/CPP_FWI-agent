-- Dormant P3 DAG lifecycle facts and active-term claim candidates.
--
-- These records do not admit a Task, authorize Adapter dispatch, consume an
-- Approval budget, or change Task/RunEvent state.  They only bind one exact
-- approved PlanGraph node-state snapshot to the active control-plane term so
-- later P3 work can add a separate execution admission boundary safely.
CREATE TABLE dag_node_state_events (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71
        AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    node_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (
        typeof(revision) = 'integer' AND revision >= 1
    ),
    previous_state TEXT CHECK (
        previous_state IS NULL OR previous_state IN (
            'Pending', 'Queued', 'Running', 'Waiting', 'Retrying',
            'Succeeded', 'Failed', 'Cancelled', 'Blocked'
        )
    ),
    state TEXT NOT NULL CHECK (state IN (
        'Pending', 'Queued', 'Running', 'Waiting', 'Retrying',
        'Succeeded', 'Failed', 'Cancelled', 'Blocked'
    )),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (task_id, plan_id, node_id, revision),
    UNIQUE (
        task_id, plan_id, plan_hash, node_id, revision, state
    ),
    FOREIGN KEY (task_id, plan_id, plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (task_id, plan_id, node_id)
        REFERENCES plan_node_idempotency(task_id, plan_id, node_id)
);

CREATE INDEX idx_dag_node_state_events_current
    ON dag_node_state_events(task_id, plan_id, node_id, revision DESC);

-- P3 has not yet defined executable node transitions.  Keep the durable state
-- contract fail closed at the initial Pending fact; a later migration must
-- replace this trigger when the dispatch/transition boundary is implemented.
CREATE TRIGGER dag_node_state_events_are_initial_pending_only
BEFORE INSERT ON dag_node_state_events
WHEN NEW.revision != 1
  OR NEW.previous_state IS NOT NULL
  OR NEW.state != 'Pending'
BEGIN
    SELECT RAISE(ABORT, 'DAG node transitions are not enabled');
END;

CREATE TRIGGER dag_node_initial_state_requires_current_approved_plan
BEFORE INSERT ON dag_node_state_events
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    WHERE task.task_id = NEW.task_id
      AND task.status = 'AwaitingApproval'
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.decision = 'approved'
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND EXISTS (
          SELECT 1 FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id') = NEW.node_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node state requires the current approved plan');
END;

CREATE TRIGGER dag_node_state_events_are_append_only
BEFORE UPDATE ON dag_node_state_events
BEGIN
    SELECT RAISE(ABORT, 'DAG node state events are append-only');
END;

CREATE TRIGGER dag_node_state_events_cannot_be_deleted
BEFORE DELETE ON dag_node_state_events
BEGIN
    SELECT RAISE(ABORT, 'DAG node state events are append-only');
END;

CREATE TABLE dag_node_claim_candidates (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71
        AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    approval_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer' AND node_revision >= 1
    ),
    node_state TEXT NOT NULL CHECK (node_state = 'Pending'),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    term_acquired_at TEXT NOT NULL,
    readiness_document_json TEXT NOT NULL,
    readiness_document_hash TEXT NOT NULL CHECK (
        length(readiness_document_hash) = 71
        AND substr(readiness_document_hash, 1, 7) = 'sha256:'
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (
        task_id, plan_id, approval_id, node_id, node_revision, fencing_token
    ),
    FOREIGN KEY (
        task_id, plan_id, plan_hash, node_id, node_revision, node_state
    ) REFERENCES dag_node_state_events(
        task_id, plan_id, plan_hash, node_id, revision, state
    ),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (project_id, principal_id, fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_dag_node_claim_candidates_term
    ON dag_node_claim_candidates(
        project_id, principal_id, fencing_token,
        task_id, approval_id, node_id
    );

CREATE TRIGGER dag_node_claim_requires_current_approved_plan
BEFORE INSERT ON dag_node_claim_candidates
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'AwaitingApproval'
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND EXISTS (
          SELECT 1 FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id') = NEW.node_id
      )
      AND json_valid(NEW.readiness_document_json)
      AND json_type(NEW.readiness_document_json, '$') = 'object'
      AND json_extract(
          NEW.readiness_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.readiness_document_json, '$.task_id'
      ) = NEW.task_id
      AND json_extract(
          NEW.readiness_document_json, '$.plan_id'
      ) = NEW.plan_id
      AND json_extract(
          NEW.readiness_document_json, '$.plan_hash'
      ) = NEW.plan_hash
      AND json_extract(
          NEW.readiness_document_json, '$.approval_id'
      ) = NEW.approval_id
      AND json_extract(
          NEW.readiness_document_json, '$.selected_node_id'
      ) = NEW.node_id
      AND json_type(
          NEW.readiness_document_json, '$.node_states'
      ) = 'array'
      AND json_array_length(
          NEW.readiness_document_json, '$.node_states'
      ) = json_array_length(plan.document_json, '$.nodes')
      AND json_type(
          NEW.readiness_document_json, '$.runnable_node_ids'
      ) = 'array'
      AND EXISTS (
          SELECT 1
          FROM json_each(
              NEW.readiness_document_json, '$.runnable_node_ids'
          ) AS runnable
          WHERE runnable.value = NEW.node_id
      )
      AND EXISTS (
          SELECT 1
          FROM json_each(
              NEW.readiness_document_json, '$.node_states'
          ) AS selected_state
          WHERE json_extract(
                    selected_state.value, '$.node_id'
                ) = NEW.node_id
            AND json_extract(
                    selected_state.value, '$.revision'
                ) = NEW.node_revision
            AND json_extract(
                    selected_state.value, '$.state'
                ) = NEW.node_state
      )
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(
              NEW.readiness_document_json, '$.node_states'
          ) AS claimed_state
          WHERE json_type(claimed_state.value, '$') != 'object'
             OR NOT EXISTS (
                 SELECT 1
                 FROM dag_node_state_events AS durable_state
                 WHERE durable_state.task_id = NEW.task_id
                   AND durable_state.plan_id = NEW.plan_id
                   AND durable_state.plan_hash = NEW.plan_hash
                   AND durable_state.node_id = json_extract(
                       claimed_state.value, '$.node_id'
                   )
                   AND durable_state.revision = json_extract(
                       claimed_state.value, '$.revision'
                   )
                   AND durable_state.state = json_extract(
                       claimed_state.value, '$.state'
                   )
                   AND NOT EXISTS (
                       SELECT 1
                       FROM dag_node_state_events AS later
                       WHERE later.task_id = durable_state.task_id
                         AND later.plan_id = durable_state.plan_id
                         AND later.node_id = durable_state.node_id
                         AND later.revision > durable_state.revision
                   )
             )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM dag_node_state_events AS durable_state
          WHERE durable_state.task_id = NEW.task_id
            AND durable_state.plan_id = NEW.plan_id
            AND durable_state.plan_hash = NEW.plan_hash
            AND NOT EXISTS (
                SELECT 1
                FROM dag_node_state_events AS later
                WHERE later.task_id = durable_state.task_id
                  AND later.plan_id = durable_state.plan_id
                  AND later.node_id = durable_state.node_id
                  AND later.revision > durable_state.revision
            )
            AND NOT EXISTS (
                SELECT 1
                FROM json_each(
                    NEW.readiness_document_json, '$.node_states'
                ) AS claimed_state
                WHERE json_extract(
                          claimed_state.value, '$.node_id'
                      ) = durable_state.node_id
                  AND json_extract(
                          claimed_state.value, '$.revision'
                      ) = durable_state.revision
                  AND json_extract(
                          claimed_state.value, '$.state'
                      ) = durable_state.state
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = task.task_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node claim requires the current approved plan');
END;

CREATE TRIGGER dag_node_claim_requires_latest_pending_revision
BEFORE INSERT ON dag_node_claim_candidates
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_state_events AS state
    WHERE state.task_id = NEW.task_id
      AND state.plan_id = NEW.plan_id
      AND state.plan_hash = NEW.plan_hash
      AND state.node_id = NEW.node_id
      AND state.revision = NEW.node_revision
      AND state.state = NEW.node_state
      AND NOT EXISTS (
          SELECT 1
          FROM dag_node_state_events AS later
          WHERE later.task_id = state.task_id
            AND later.plan_id = state.plan_id
            AND later.node_id = state.node_id
            AND later.revision > state.revision
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node claim requires the latest Pending revision');
END;

CREATE TRIGGER dag_node_claim_requires_active_term
BEFORE INSERT ON dag_node_claim_candidates
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.fencing_token
      AND term.owner_id = NEW.owner_id
      AND term.acquired_at = NEW.term_acquired_at
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
    SELECT RAISE(ABORT, 'DAG node claim requires the active term');
END;

CREATE TRIGGER dag_node_claim_candidates_are_append_only
BEFORE UPDATE ON dag_node_claim_candidates
BEGIN
    SELECT RAISE(ABORT, 'DAG node claim candidates are append-only');
END;

CREATE TRIGGER dag_node_claim_candidates_cannot_be_deleted
BEFORE DELETE ON dag_node_claim_candidates
BEGIN
    SELECT RAISE(ABORT, 'DAG node claim candidates are append-only');
END;
