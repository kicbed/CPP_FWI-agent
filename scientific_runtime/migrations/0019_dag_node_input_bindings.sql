-- Dormant P3 all-input binding facts and a reserved producer-success receipt.
--
-- Neither table admits a Task, changes a DAG node state, authorizes Adapter
-- dispatch, consumes Approval budget, or writes a RunEvent.  In particular,
-- v18's initial-Pending-only state trigger remains unchanged.  Consequently
-- dag_node_succeeded_outputs has no production-reachable writer in v19; it is
-- reserved so a later executable-transition migration can establish producer
-- evidence without weakening this binding boundary.

CREATE TABLE dag_node_input_binding_facts (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71
        AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    approval_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    target_node_revision INTEGER NOT NULL CHECK (
        typeof(target_node_revision) = 'integer'
        AND target_node_revision >= 1
    ),
    target_node_state TEXT NOT NULL CHECK (target_node_state = 'Pending'),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    term_acquired_at TEXT NOT NULL,
    claim_readiness_document_hash TEXT NOT NULL CHECK (
        length(claim_readiness_document_hash) = 71
        AND substr(claim_readiness_document_hash, 1, 7) = 'sha256:'
    ),
    binding_document_json TEXT NOT NULL,
    binding_document_hash TEXT NOT NULL CHECK (
        length(binding_document_hash) = 71
        AND substr(binding_document_hash, 1, 7) = 'sha256:'
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token
    ),
    -- A future producer-success receipt uses this exact term-bound binding as
    -- its parent.  Including the document hash prevents a receipt from naming
    -- only a mutable-looking logical node identity.
    UNIQUE (
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token, binding_document_hash
    ),
    FOREIGN KEY (
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token
    ) REFERENCES dag_node_claim_candidates(
        task_id, plan_id, approval_id, node_id,
        node_revision, fencing_token
    ),
    FOREIGN KEY (
        task_id, plan_id, plan_hash, target_node_id,
        target_node_revision, target_node_state
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

CREATE INDEX idx_dag_node_input_binding_facts_term
    ON dag_node_input_binding_facts(
        project_id, principal_id, fencing_token,
        task_id, approval_id, target_node_id
    );

CREATE TRIGGER dag_node_input_binding_requires_current_claim
BEFORE INSERT ON dag_node_input_binding_facts
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
    JOIN dag_node_claim_candidates AS claim
      ON claim.task_id = task.task_id
     AND claim.plan_id = plan.plan_id
     AND claim.approval_id = approval.approval_id
     AND claim.node_id = NEW.target_node_id
     AND claim.node_revision = NEW.target_node_revision
     AND claim.fencing_token = NEW.fencing_token
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'AwaitingApproval'
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND claim.plan_hash = NEW.plan_hash
      AND claim.node_state = NEW.target_node_state
      AND claim.project_id = NEW.project_id
      AND claim.principal_id = NEW.principal_id
      AND claim.owner_id = NEW.owner_id
      AND claim.term_acquired_at = NEW.term_acquired_at
      AND claim.readiness_document_hash
          = NEW.claim_readiness_document_hash
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND (
          SELECT COUNT(*)
          FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id')
                = NEW.target_node_id
      ) = 1
      AND json_valid(NEW.binding_document_json)
      AND json_type(NEW.binding_document_json, '$') = 'object'
      AND json_extract(
          NEW.binding_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.binding_document_json, '$.task_id'
      ) = NEW.task_id
      AND json_extract(
          NEW.binding_document_json, '$.plan.plan_id'
      ) = NEW.plan_id
      AND json_extract(
          NEW.binding_document_json, '$.plan.plan_hash'
      ) = NEW.plan_hash
      AND json_extract(
          NEW.binding_document_json, '$.approval_id'
      ) = NEW.approval_id
      AND json_extract(
          NEW.binding_document_json, '$.target.node_id'
      ) = NEW.target_node_id
      AND json_type(
          NEW.binding_document_json, '$.target.revision'
      ) = 'integer'
      AND json_extract(
          NEW.binding_document_json, '$.target.revision'
      ) = NEW.target_node_revision
      AND json_extract(
          NEW.binding_document_json, '$.target.state'
      ) = NEW.target_node_state
      AND json_extract(
          NEW.binding_document_json, '$.scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.binding_document_json, '$.scope.principal_id'
      ) = NEW.principal_id
      AND json_type(
          NEW.binding_document_json, '$.supervisor_term.fencing_token'
      ) = 'integer'
      AND json_extract(
          NEW.binding_document_json, '$.supervisor_term.fencing_token'
      ) = NEW.fencing_token
      AND json_extract(
          NEW.binding_document_json, '$.supervisor_term.owner_id'
      ) = NEW.owner_id
      AND json_extract(
          NEW.binding_document_json, '$.supervisor_term.acquired_at'
      ) = NEW.term_acquired_at
      AND json_extract(
          NEW.binding_document_json, '$.claim_readiness_document_hash'
      ) = NEW.claim_readiness_document_hash
      AND json_type(NEW.binding_document_json, '$.inputs') = 'array'
      AND json_array_length(NEW.binding_document_json, '$.inputs') = (
          SELECT json_array_length(plan_node.value, '$.inputs')
          FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id')
                = NEW.target_node_id
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
    SELECT RAISE(
        ABORT,
        'DAG input binding requires the current approved claim'
    );
END;

CREATE TRIGGER dag_node_input_binding_requires_latest_pending
BEFORE INSERT ON dag_node_input_binding_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_state_events AS state
    WHERE state.task_id = NEW.task_id
      AND state.plan_id = NEW.plan_id
      AND state.plan_hash = NEW.plan_hash
      AND state.node_id = NEW.target_node_id
      AND state.revision = NEW.target_node_revision
      AND state.state = NEW.target_node_state
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
    SELECT RAISE(
        ABORT,
        'DAG input binding requires the latest Pending revision'
    );
END;

CREATE TRIGGER dag_node_input_binding_requires_active_term
BEFORE INSERT ON dag_node_input_binding_facts
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
    SELECT RAISE(ABORT, 'DAG input binding requires the active term');
END;

CREATE TRIGGER dag_node_input_binding_facts_are_append_only
BEFORE UPDATE ON dag_node_input_binding_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG input binding facts are append-only');
END;

CREATE TRIGGER dag_node_input_binding_facts_cannot_be_deleted
BEFORE DELETE ON dag_node_input_binding_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG input binding facts are append-only');
END;

-- One canonical row will eventually describe the complete output inventory
-- for one exact Succeeded producer revision.  It is intentionally aggregate:
-- independently committable per-port rows could otherwise expose a partial
-- output set as a completed producer receipt.
CREATE TABLE dag_node_succeeded_outputs (
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 71
        AND substr(plan_hash, 1, 7) = 'sha256:'
    ),
    approval_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    input_binding_node_revision INTEGER NOT NULL CHECK (
        typeof(input_binding_node_revision) = 'integer'
        AND input_binding_node_revision >= 1
    ),
    input_binding_document_hash TEXT NOT NULL CHECK (
        length(input_binding_document_hash) = 71
        AND substr(input_binding_document_hash, 1, 7) = 'sha256:'
    ),
    node_revision INTEGER NOT NULL CHECK (
        typeof(node_revision) = 'integer'
        AND node_revision > input_binding_node_revision
    ),
    node_state TEXT NOT NULL CHECK (node_state = 'Succeeded'),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (
        typeof(fencing_token) = 'integer' AND fencing_token >= 1
    ),
    owner_id TEXT NOT NULL,
    term_acquired_at TEXT NOT NULL,
    receipt_document_json TEXT NOT NULL,
    receipt_document_hash TEXT NOT NULL CHECK (
        length(receipt_document_hash) = 71
        AND substr(receipt_document_hash, 1, 7) = 'sha256:'
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    PRIMARY KEY (
        task_id, plan_id, approval_id, node_id, node_revision
    ),
    -- A Succeeded revision has exactly one original Approval provenance.  A
    -- later reapproval may not relabel the same completed revision.
    UNIQUE (task_id, plan_id, node_id, node_revision),
    FOREIGN KEY (
        task_id, plan_id, plan_hash, node_id, node_revision, node_state
    ) REFERENCES dag_node_state_events(
        task_id, plan_id, plan_hash, node_id, revision, state
    ),
    FOREIGN KEY (
        task_id, plan_id, approval_id, node_id,
        input_binding_node_revision, fencing_token,
        input_binding_document_hash
    ) REFERENCES dag_node_input_binding_facts(
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token, binding_document_hash
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

CREATE INDEX idx_dag_node_succeeded_outputs_node
    ON dag_node_succeeded_outputs(
        task_id, plan_id, approval_id, node_id, node_revision DESC
    );

CREATE TRIGGER dag_node_succeeded_output_requires_exact_binding
BEFORE INSERT ON dag_node_succeeded_outputs
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = NEW.plan_id
     AND plan.plan_hash = NEW.plan_hash
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = NEW.approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dag_node_input_binding_facts AS binding
      ON binding.task_id = task.task_id
     AND binding.plan_id = plan.plan_id
     AND binding.approval_id = approval.approval_id
     AND binding.target_node_id = NEW.node_id
     AND binding.target_node_revision = NEW.input_binding_node_revision
     AND binding.fencing_token = NEW.fencing_token
     AND binding.binding_document_hash
         = NEW.input_binding_document_hash
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND approval.decision = 'approved'
      AND binding.plan_hash = NEW.plan_hash
      AND binding.target_node_state = 'Pending'
      AND binding.project_id = NEW.project_id
      AND binding.principal_id = NEW.principal_id
      AND binding.owner_id = NEW.owner_id
      AND binding.term_acquired_at = NEW.term_acquired_at
      AND binding.recorded_at_us <= NEW.recorded_at_us
      AND json_valid(plan.document_json)
      AND json_type(plan.document_json, '$.nodes') = 'array'
      AND json_array_length(plan.document_json, '$.nodes') BETWEEN 2 AND 32
      AND (
          SELECT COUNT(*)
          FROM json_each(plan.document_json, '$.nodes') AS plan_node
          WHERE json_extract(plan_node.value, '$.node_id') = NEW.node_id
      ) = 1
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
    SELECT RAISE(
        ABORT,
        'DAG Succeeded outputs require an exact prior input binding'
    );
END;

CREATE TRIGGER dag_node_succeeded_output_requires_latest_success
BEFORE INSERT ON dag_node_succeeded_outputs
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_state_events AS state
    WHERE state.task_id = NEW.task_id
      AND state.plan_id = NEW.plan_id
      AND state.plan_hash = NEW.plan_hash
      AND state.node_id = NEW.node_id
      AND state.revision = NEW.node_revision
      AND state.state = NEW.node_state
      AND state.recorded_at_us <= NEW.recorded_at_us
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
    SELECT RAISE(
        ABORT,
        'DAG Succeeded outputs require the latest Succeeded revision'
    );
END;

CREATE TRIGGER dag_node_succeeded_output_requires_active_term
BEFORE INSERT ON dag_node_succeeded_outputs
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
    SELECT RAISE(ABORT, 'DAG Succeeded outputs require the active term');
END;

CREATE TRIGGER dag_node_succeeded_output_requires_complete_receipt
BEFORE INSERT ON dag_node_succeeded_outputs
WHEN NOT EXISTS (
    SELECT 1
    FROM plans AS plan
    JOIN approvals AS approval
      ON approval.task_id = plan.task_id
     AND approval.approval_id = NEW.approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN json_each(plan.document_json, '$.nodes') AS plan_node
      ON json_extract(plan_node.value, '$.node_id') = NEW.node_id
    WHERE plan.task_id = NEW.task_id
      AND plan.plan_id = NEW.plan_id
      AND plan.plan_hash = NEW.plan_hash
      AND approval.decision = 'approved'
      AND json_valid(plan.document_json)
      AND json_type(plan_node.value, '$.outputs') = 'array'
      AND json_array_length(plan_node.value, '$.outputs') >= 1
      AND json_valid(NEW.receipt_document_json)
      AND json_type(NEW.receipt_document_json, '$') = 'object'
      AND json_extract(
          NEW.receipt_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.receipt_document_json, '$.task_id'
      ) = NEW.task_id
      AND json_extract(
          NEW.receipt_document_json, '$.plan.plan_id'
      ) = NEW.plan_id
      AND json_extract(
          NEW.receipt_document_json, '$.plan.plan_hash'
      ) = NEW.plan_hash
      AND json_extract(
          NEW.receipt_document_json, '$.approval_id'
      ) = NEW.approval_id
      AND json_extract(
          NEW.receipt_document_json, '$.node.node_id'
      ) = NEW.node_id
      AND json_type(
          NEW.receipt_document_json, '$.node.input_binding_revision'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json, '$.node.input_binding_revision'
      ) = NEW.input_binding_node_revision
      AND json_extract(
          NEW.receipt_document_json, '$.input_binding_document_hash'
      ) = NEW.input_binding_document_hash
      AND json_type(
          NEW.receipt_document_json, '$.node.succeeded_revision'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json, '$.node.succeeded_revision'
      ) = NEW.node_revision
      AND json_extract(
          NEW.receipt_document_json, '$.node.state'
      ) = NEW.node_state
      AND json_extract(
          NEW.receipt_document_json, '$.scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.receipt_document_json, '$.scope.principal_id'
      ) = NEW.principal_id
      AND json_type(
          NEW.receipt_document_json, '$.supervisor_term.fencing_token'
      ) = 'integer'
      AND json_extract(
          NEW.receipt_document_json, '$.supervisor_term.fencing_token'
      ) = NEW.fencing_token
      AND json_extract(
          NEW.receipt_document_json, '$.supervisor_term.owner_id'
      ) = NEW.owner_id
      AND json_extract(
          NEW.receipt_document_json, '$.supervisor_term.acquired_at'
      ) = NEW.term_acquired_at
      AND length(
          json_extract(NEW.receipt_document_json, '$.receipt_record_hash')
      ) = 71
      AND substr(
          json_extract(NEW.receipt_document_json, '$.receipt_record_hash'),
          1,
          7
      ) = 'sha256:'
      AND json_extract(
          NEW.receipt_document_json, '$.succeeded_at'
      ) = NEW.recorded_at
      AND json_type(NEW.receipt_document_json, '$.outputs') = 'array'
      AND json_array_length(NEW.receipt_document_json, '$.outputs')
          = json_array_length(plan_node.value, '$.outputs')
      AND (
          SELECT COUNT(DISTINCT json_extract(output.value, '$.port'))
          FROM json_each(plan_node.value, '$.outputs') AS output
      ) = json_array_length(plan_node.value, '$.outputs')
      AND (
          SELECT COUNT(
              DISTINCT json_extract(receipt_output.value, '$.output_port')
          )
          FROM json_each(
              NEW.receipt_document_json, '$.outputs'
          ) AS receipt_output
      ) = json_array_length(NEW.receipt_document_json, '$.outputs')
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(
              NEW.receipt_document_json, '$.outputs'
          ) AS receipt_output
          WHERE json_type(receipt_output.value, '$') != 'object'
             OR json_type(receipt_output.value, '$.output_port') != 'text'
             OR json_type(receipt_output.value, '$.data_type') != 'text'
             OR NOT EXISTS (
                 SELECT 1
                 FROM json_each(
                     plan_node.value, '$.outputs'
                 ) AS planned_output
                 WHERE json_extract(
                           planned_output.value, '$.port'
                       ) = json_extract(
                           receipt_output.value, '$.output_port'
                       )
                   AND json_extract(
                           planned_output.value, '$.data_type'
                       ) = json_extract(
                           receipt_output.value, '$.data_type'
                       )
             )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(plan_node.value, '$.outputs') AS planned_output
          WHERE NOT EXISTS (
              SELECT 1
              FROM json_each(
                  NEW.receipt_document_json, '$.outputs'
              ) AS receipt_output
              WHERE json_extract(
                        receipt_output.value, '$.output_port'
                    ) = json_extract(planned_output.value, '$.port')
                AND json_extract(
                        receipt_output.value, '$.data_type'
                    ) = json_extract(planned_output.value, '$.data_type')
          )
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG Succeeded outputs require one complete canonical receipt'
    );
END;

CREATE TRIGGER dag_node_succeeded_outputs_are_append_only
BEFORE UPDATE ON dag_node_succeeded_outputs
BEGIN
    SELECT RAISE(ABORT, 'DAG Succeeded outputs are append-only');
END;

CREATE TRIGGER dag_node_succeeded_outputs_cannot_be_deleted
BEFORE DELETE ON dag_node_succeeded_outputs
BEGIN
    SELECT RAISE(ABORT, 'DAG Succeeded outputs are append-only');
END;
