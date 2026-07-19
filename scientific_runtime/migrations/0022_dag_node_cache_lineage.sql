-- P3 scope-bound DAG node cache, durable cache-hit facts, and same-live
-- checkpoint coexistence.
--
-- A cache entry is only an immutable index over one already executed,
-- durably Succeeded node.  A hit is a separate active-term fact which projects
-- one exact Pending target directly to Succeeded without creating a dispatch
-- intent or Worker attempt.  Neither fact is a Worker checkpoint and neither
-- changes the P2 same-live Worker/process/attempt resume boundary.

CREATE TABLE dag_node_cache_entries (
    cache_entry_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    cache_key_document_json TEXT NOT NULL CHECK (
        json_valid(cache_key_document_json)
        AND json_type(cache_key_document_json, '$') = 'object'
    ),
    cache_key_hash TEXT NOT NULL CHECK (
        length(cache_key_hash) = 71
        AND substr(cache_key_hash, 1, 7) = 'sha256:'
    ),
    source_intent_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    source_plan_id TEXT NOT NULL,
    source_plan_hash TEXT NOT NULL CHECK (
        length(source_plan_hash) = 71
        AND substr(source_plan_hash, 1, 7) = 'sha256:'
    ),
    source_approval_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    source_pending_revision INTEGER NOT NULL CHECK (
        typeof(source_pending_revision) = 'integer'
        AND source_pending_revision >= 1
    ),
    source_queued_revision INTEGER NOT NULL CHECK (
        typeof(source_queued_revision) = 'integer'
        AND source_queued_revision = source_pending_revision + 1
    ),
    source_succeeded_revision INTEGER NOT NULL CHECK (
        typeof(source_succeeded_revision) = 'integer'
        AND source_succeeded_revision = source_queued_revision + 2
    ),
    source_admission_document_hash TEXT NOT NULL CHECK (
        length(source_admission_document_hash) = 71
        AND substr(source_admission_document_hash, 1, 7) = 'sha256:'
    ),
    source_input_binding_document_hash TEXT NOT NULL CHECK (
        length(source_input_binding_document_hash) = 71
        AND substr(source_input_binding_document_hash, 1, 7) = 'sha256:'
    ),
    source_terminal_event_sequence INTEGER NOT NULL CHECK (
        typeof(source_terminal_event_sequence) = 'integer'
        AND source_terminal_event_sequence >= 1
    ),
    source_terminal_event_hash TEXT NOT NULL CHECK (
        length(source_terminal_event_hash) = 71
        AND substr(source_terminal_event_hash, 1, 7) = 'sha256:'
    ),
    source_receipt_document_hash TEXT NOT NULL CHECK (
        length(source_receipt_document_hash) = 71
        AND substr(source_receipt_document_hash, 1, 7) = 'sha256:'
    ),
    trusted_lineage_document_json TEXT NOT NULL CHECK (
        json_valid(trusted_lineage_document_json)
        AND json_type(trusted_lineage_document_json, '$') = 'object'
    ),
    trusted_lineage_document_hash TEXT NOT NULL CHECK (
        length(trusted_lineage_document_hash) = 71
        AND substr(trusted_lineage_document_hash, 1, 7) = 'sha256:'
    ),
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    UNIQUE (source_intent_id, cache_key_hash),
    FOREIGN KEY (source_intent_id)
        REFERENCES dag_node_terminal_facts(intent_id),
    FOREIGN KEY (source_task_id, source_plan_id, source_plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (source_task_id, source_approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (source_task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (source_task_id, source_terminal_event_sequence)
        REFERENCES run_events(task_id, sequence)
);

CREATE INDEX idx_dag_node_cache_entries_scope_key
    ON dag_node_cache_entries(
        project_id, principal_id, cache_key_hash,
        recorded_at_us DESC, cache_entry_id
    );

CREATE TRIGGER dag_node_cache_entry_requires_exact_succeeded_source
BEFORE INSERT ON dag_node_cache_entries
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_terminal_facts AS terminal
    JOIN dag_node_execution_admissions AS admission
      ON admission.intent_id = terminal.intent_id
    JOIN dispatch_intents AS intent
      ON intent.intent_id = admission.intent_id
    JOIN tasks AS task
      ON task.task_id = terminal.task_id
    JOIN plans AS plan
      ON plan.task_id = terminal.task_id
     AND plan.plan_id = terminal.plan_id
     AND plan.plan_hash = terminal.plan_hash
    JOIN approvals AS approval
      ON approval.task_id = terminal.task_id
     AND approval.approval_id = terminal.approval_id
     AND approval.plan_id = terminal.plan_id
     AND approval.plan_hash = terminal.plan_hash
    JOIN dag_node_input_binding_facts AS binding
      ON binding.task_id = terminal.task_id
     AND binding.plan_id = terminal.plan_id
     AND binding.approval_id = terminal.approval_id
     AND binding.target_node_id = terminal.node_id
     AND binding.target_node_revision
         = terminal.input_binding_node_revision
     AND binding.fencing_token = terminal.input_fencing_token
     AND binding.binding_document_hash
         = terminal.input_binding_document_hash
    JOIN dag_node_state_events AS succeeded
      ON succeeded.task_id = terminal.task_id
     AND succeeded.plan_id = terminal.plan_id
     AND succeeded.plan_hash = terminal.plan_hash
     AND succeeded.node_id = terminal.node_id
     AND succeeded.revision = terminal.node_revision
     AND succeeded.state = terminal.node_state
    JOIN run_events AS event
      ON event.task_id = terminal.task_id
     AND event.sequence = terminal.event_sequence
    JOIN json_each(plan.document_json, '$.nodes') AS plan_node
      ON json_extract(plan_node.value, '$.node_id') = terminal.node_id
    WHERE terminal.intent_id = NEW.source_intent_id
      AND terminal.task_id = NEW.source_task_id
      AND terminal.plan_id = NEW.source_plan_id
      AND terminal.plan_hash = NEW.source_plan_hash
      AND terminal.approval_id = NEW.source_approval_id
      AND terminal.node_id = NEW.source_node_id
      AND terminal.input_binding_node_revision
          = NEW.source_pending_revision
      AND terminal.input_binding_document_hash
          = NEW.source_input_binding_document_hash
      AND terminal.node_revision = NEW.source_succeeded_revision
      AND terminal.node_state = 'Succeeded'
      AND terminal.attempt_id IS NOT NULL
      AND terminal.attempt_number = 1
      AND terminal.event_sequence = NEW.source_terminal_event_sequence
      AND terminal.event_hash = NEW.source_terminal_event_hash
      AND terminal.receipt_document_json IS NOT NULL
      AND terminal.receipt_document_hash
          = NEW.source_receipt_document_hash
      AND admission.task_id = NEW.source_task_id
      AND admission.plan_id = NEW.source_plan_id
      AND admission.plan_hash = NEW.source_plan_hash
      AND admission.approval_id = NEW.source_approval_id
      AND admission.node_id = NEW.source_node_id
      AND admission.pending_revision = NEW.source_pending_revision
      AND admission.queued_revision = NEW.source_queued_revision
      AND admission.document_hash = NEW.source_admission_document_hash
      AND admission.input_binding_document_hash
          = NEW.source_input_binding_document_hash
      AND intent.task_id = NEW.source_task_id
      AND intent.plan_id = NEW.source_plan_id
      AND intent.plan_hash = NEW.source_plan_hash
      AND intent.approval_id = NEW.source_approval_id
      AND intent.node_id = NEW.source_node_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND approval.decision = 'approved'
      AND binding.project_id = NEW.project_id
      AND binding.principal_id = NEW.principal_id
      AND event.event_type = 'node_succeeded'
      AND event.node_id = NEW.source_node_id
      AND event.document_hash = NEW.source_terminal_event_hash
      AND succeeded.previous_state = 'Running'
      AND succeeded.recorded_at_us <= NEW.recorded_at_us
      AND terminal.recorded_at_us <= NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = succeeded.task_id
            AND later.plan_id = succeeded.plan_id
            AND later.node_id = succeeded.node_id
            AND later.revision > succeeded.revision
      )
      AND json_extract(
          NEW.cache_key_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.cache_key_document_json, '$.permission_scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.cache_key_document_json, '$.permission_scope.principal_id'
      ) = NEW.principal_id
      AND json_extract(
          NEW.cache_key_document_json, '$.node_contract.node_id'
      ) = NEW.source_node_id
      AND json_extract(
          NEW.cache_key_document_json, '$.node_contract.algorithm.id'
      ) = json_extract(plan_node.value, '$.algorithm.id')
      AND json_extract(
          NEW.cache_key_document_json, '$.node_contract.algorithm.version'
      ) = json_extract(plan_node.value, '$.algorithm.version')
      AND json_extract(
          NEW.cache_key_document_json, '$.adapter.id'
      ) = intent.adapter_id
      AND json_extract(
          NEW.cache_key_document_json, '$.adapter.version'
      ) = intent.adapter_version
      AND json_type(
          NEW.cache_key_document_json, '$.approval_scope'
      ) = 'object'
      AND json_type(
          NEW.cache_key_document_json, '$.inputs'
      ) = 'array'
      AND json_type(
          NEW.cache_key_document_json, '$.node_contract.outputs'
      ) = 'array'
      AND json_type(
          NEW.cache_key_document_json, '$.execution_fingerprint'
      ) = 'object'
      AND json_extract(
          NEW.trusted_lineage_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.trusted_lineage_document_json,
          '$.permission_scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.trusted_lineage_document_json,
          '$.permission_scope.principal_id'
      ) = NEW.principal_id
      AND json_extract(
          NEW.trusted_lineage_document_json, '$.source.task_id'
      ) = NEW.source_task_id
      AND json_extract(
          NEW.trusted_lineage_document_json, '$.source.plan_id'
      ) = NEW.source_plan_id
      AND json_extract(
          NEW.trusted_lineage_document_json, '$.source.plan_hash'
      ) = NEW.source_plan_hash
      AND json_extract(
          NEW.trusted_lineage_document_json, '$.source.approval_id'
      ) = NEW.source_approval_id
      AND json_extract(
          NEW.trusted_lineage_document_json, '$.source.node_id'
      ) = NEW.source_node_id
      AND json_extract(
          NEW.trusted_lineage_document_json,
          '$.source.succeeded_revision'
      ) = NEW.source_succeeded_revision
      AND json_extract(
          NEW.trusted_lineage_document_json,
          '$.source.input_binding_document_hash'
      ) = NEW.source_input_binding_document_hash
      AND json_extract(
          NEW.trusted_lineage_document_json,
          '$.source.receipt_document_hash'
      ) = NEW.source_receipt_document_hash
      AND json_extract(
          NEW.trusted_lineage_document_json,
          '$.source.semantic_cache_key_hash'
      ) = NEW.cache_key_hash
      AND json_type(
          NEW.trusted_lineage_document_json, '$.dataset_roots'
      ) = 'array'
      AND json_array_length(
          NEW.trusted_lineage_document_json, '$.dataset_roots'
      ) >= 1
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(
              NEW.trusted_lineage_document_json, '$.dataset_roots'
          ) AS root
          WHERE json_type(root.value, '$') != 'object'
             OR json_type(root.value, '$.id') != 'text'
             OR json_type(root.value, '$.version') != 'text'
             OR length(json_extract(root.value, '$.content_hash')) != 71
             OR substr(
                    json_extract(root.value, '$.content_hash'), 1, 7
                ) != 'sha256:'
             OR json_type(root.value, '$.data_type') != 'text'
             OR length(
                    json_extract(root.value, '$.catalog_document_hash')
                ) != 71
             OR substr(
                    json_extract(root.value, '$.catalog_document_hash'), 1, 7
                ) != 'sha256:'
      )
      AND json_type(
          NEW.trusted_lineage_document_json, '$.outputs'
      ) = 'array'
      AND json_array_length(
          NEW.trusted_lineage_document_json, '$.outputs'
      ) >= 1
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node cache entry requires an exact executed Succeeded source'
    );
END;

CREATE TRIGGER dag_node_cache_entries_are_append_only
BEFORE UPDATE ON dag_node_cache_entries
BEGIN
    SELECT RAISE(ABORT, 'DAG node cache entries are append-only');
END;

CREATE TRIGGER dag_node_cache_entries_cannot_be_deleted
BEFORE DELETE ON dag_node_cache_entries
BEGIN
    SELECT RAISE(ABORT, 'DAG node cache entries are append-only');
END;

CREATE TABLE dag_node_cache_hit_facts (
    cache_hit_id TEXT PRIMARY KEY,
    target_task_id TEXT NOT NULL,
    target_plan_id TEXT NOT NULL,
    target_plan_hash TEXT NOT NULL CHECK (
        length(target_plan_hash) = 71
        AND substr(target_plan_hash, 1, 7) = 'sha256:'
    ),
    target_approval_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    target_pending_revision INTEGER NOT NULL CHECK (
        typeof(target_pending_revision) = 'integer'
        AND target_pending_revision >= 1
    ),
    target_succeeded_revision INTEGER NOT NULL CHECK (
        typeof(target_succeeded_revision) = 'integer'
        AND target_succeeded_revision = target_pending_revision + 1
    ),
    target_input_binding_document_hash TEXT NOT NULL CHECK (
        length(target_input_binding_document_hash) = 71
        AND substr(target_input_binding_document_hash, 1, 7) = 'sha256:'
    ),
    target_input_fencing_token INTEGER NOT NULL CHECK (
        typeof(target_input_fencing_token) = 'integer'
        AND target_input_fencing_token >= 1
    ),
    target_input_owner_id TEXT NOT NULL,
    target_input_term_acquired_at TEXT NOT NULL,
    source_cache_entry_id TEXT NOT NULL,
    source_intent_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    source_plan_id TEXT NOT NULL,
    source_plan_hash TEXT NOT NULL CHECK (
        length(source_plan_hash) = 71
        AND substr(source_plan_hash, 1, 7) = 'sha256:'
    ),
    source_approval_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    source_succeeded_revision INTEGER NOT NULL CHECK (
        typeof(source_succeeded_revision) = 'integer'
        AND source_succeeded_revision >= 4
    ),
    source_admission_document_hash TEXT NOT NULL CHECK (
        length(source_admission_document_hash) = 71
        AND substr(source_admission_document_hash, 1, 7) = 'sha256:'
    ),
    source_receipt_document_hash TEXT NOT NULL CHECK (
        length(source_receipt_document_hash) = 71
        AND substr(source_receipt_document_hash, 1, 7) = 'sha256:'
    ),
    source_trusted_lineage_document_hash TEXT NOT NULL CHECK (
        length(source_trusted_lineage_document_hash) = 71
        AND substr(source_trusted_lineage_document_hash, 1, 7) = 'sha256:'
    ),
    cache_key_document_json TEXT NOT NULL CHECK (
        json_valid(cache_key_document_json)
        AND json_type(cache_key_document_json, '$') = 'object'
    ),
    cache_key_hash TEXT NOT NULL CHECK (
        length(cache_key_hash) = 71
        AND substr(cache_key_hash, 1, 7) = 'sha256:'
    ),
    artifact_verification_document_json TEXT NOT NULL CHECK (
        json_valid(artifact_verification_document_json)
        AND json_type(artifact_verification_document_json, '$') = 'object'
    ),
    artifact_verification_document_hash TEXT NOT NULL CHECK (
        length(artifact_verification_document_hash) = 71
        AND substr(artifact_verification_document_hash, 1, 7) = 'sha256:'
    ),
    output_receipt_document_json TEXT NOT NULL CHECK (
        json_valid(output_receipt_document_json)
        AND json_type(output_receipt_document_json, '$') = 'object'
    ),
    output_receipt_document_hash TEXT NOT NULL CHECK (
        length(output_receipt_document_hash) = 71
        AND substr(output_receipt_document_hash, 1, 7) = 'sha256:'
    ),
    event_sequence INTEGER NOT NULL CHECK (
        typeof(event_sequence) = 'integer' AND event_sequence >= 1
    ),
    event_hash TEXT NOT NULL CHECK (
        length(event_hash) = 71
        AND substr(event_hash, 1, 7) = 'sha256:'
    ),
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    completion_fencing_token INTEGER NOT NULL CHECK (
        typeof(completion_fencing_token) = 'integer'
        AND completion_fencing_token >= 1
    ),
    completion_owner_id TEXT NOT NULL,
    completion_term_acquired_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL CHECK (
        typeof(recorded_at_us) = 'integer' AND recorded_at_us >= 0
    ),
    UNIQUE (
        target_task_id, target_plan_id, target_node_id,
        target_succeeded_revision
    ),
    UNIQUE (target_task_id, event_sequence),
    FOREIGN KEY (source_cache_entry_id)
        REFERENCES dag_node_cache_entries(cache_entry_id),
    FOREIGN KEY (target_task_id, target_plan_id, target_plan_hash)
        REFERENCES plans(task_id, plan_id, plan_hash),
    FOREIGN KEY (target_task_id, target_approval_id)
        REFERENCES approvals(task_id, approval_id),
    FOREIGN KEY (
        target_task_id, target_plan_id, target_approval_id,
        target_node_id, target_pending_revision,
        target_input_fencing_token, target_input_binding_document_hash
    ) REFERENCES dag_node_input_binding_facts(
        task_id, plan_id, approval_id, target_node_id,
        target_node_revision, fencing_token, binding_document_hash
    ),
    FOREIGN KEY (target_task_id, project_id, principal_id)
        REFERENCES tasks(task_id, project_id, principal_id),
    FOREIGN KEY (target_task_id, event_sequence)
        REFERENCES run_events(task_id, sequence),
    FOREIGN KEY (target_task_id, event_sequence)
        REFERENCES supervised_run_event_commits(task_id, sequence),
    FOREIGN KEY (project_id, principal_id, target_input_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        ),
    FOREIGN KEY (project_id, principal_id, completion_fencing_token)
        REFERENCES runtime_supervisor_terms(
            project_id, principal_id, fencing_token
        )
);

CREATE INDEX idx_dag_node_cache_hit_facts_term
    ON dag_node_cache_hit_facts(
        project_id, principal_id, completion_fencing_token,
        target_task_id, target_node_id
    );

CREATE INDEX idx_dag_node_cache_hit_facts_source
    ON dag_node_cache_hit_facts(
        source_cache_entry_id, target_task_id, target_node_id
    );

CREATE TRIGGER dag_node_cache_hit_requires_active_term
BEFORE INSERT ON dag_node_cache_hit_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM runtime_supervisor_leases AS lease
    JOIN runtime_supervisor_terms AS term
      ON term.project_id = lease.project_id
     AND term.principal_id = lease.principal_id
     AND term.fencing_token = lease.fencing_token
    WHERE lease.project_id = NEW.project_id
      AND lease.principal_id = NEW.principal_id
      AND lease.fencing_token = NEW.completion_fencing_token
      AND term.owner_id = NEW.completion_owner_id
      AND term.acquired_at = NEW.completion_term_acquired_at
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
    SELECT RAISE(ABORT, 'DAG node cache hit requires the active term');
END;

CREATE TRIGGER dag_node_cache_hit_requires_exact_source_and_target
BEFORE INSERT ON dag_node_cache_hit_facts
WHEN NOT EXISTS (
    SELECT 1
    FROM dag_node_cache_entries AS cache_entry
    JOIN dag_node_terminal_facts AS source_terminal
      ON source_terminal.intent_id = cache_entry.source_intent_id
    JOIN dag_node_execution_admissions AS source_admission
      ON source_admission.intent_id = source_terminal.intent_id
    JOIN tasks AS target_task
      ON target_task.task_id = NEW.target_task_id
    JOIN plans AS target_plan
      ON target_plan.task_id = target_task.task_id
     AND target_plan.plan_id = target_task.current_plan_id
    JOIN approvals AS target_approval
      ON target_approval.task_id = target_task.task_id
     AND target_approval.approval_id = target_task.current_approval_id
     AND target_approval.plan_id = target_plan.plan_id
     AND target_approval.plan_hash = target_plan.plan_hash
    JOIN dag_node_input_binding_facts AS target_binding
      ON target_binding.task_id = target_task.task_id
     AND target_binding.plan_id = target_plan.plan_id
     AND target_binding.approval_id = target_approval.approval_id
     AND target_binding.target_node_id = NEW.target_node_id
     AND target_binding.target_node_revision
         = NEW.target_pending_revision
     AND target_binding.fencing_token = NEW.target_input_fencing_token
     AND target_binding.binding_document_hash
         = NEW.target_input_binding_document_hash
    JOIN dag_node_state_events AS target_pending
      ON target_pending.task_id = target_task.task_id
     AND target_pending.plan_id = target_plan.plan_id
     AND target_pending.plan_hash = target_plan.plan_hash
     AND target_pending.node_id = NEW.target_node_id
     AND target_pending.revision = NEW.target_pending_revision
     AND target_pending.state = 'Pending'
    JOIN run_events AS event
      ON event.task_id = target_task.task_id
     AND event.sequence = NEW.event_sequence
    JOIN supervised_run_event_commits AS event_commit
      ON event_commit.task_id = event.task_id
     AND event_commit.sequence = event.sequence
    WHERE cache_entry.cache_entry_id = NEW.source_cache_entry_id
      AND cache_entry.project_id = NEW.project_id
      AND cache_entry.principal_id = NEW.principal_id
      AND cache_entry.source_intent_id = NEW.source_intent_id
      AND cache_entry.source_task_id = NEW.source_task_id
      AND cache_entry.source_plan_id = NEW.source_plan_id
      AND cache_entry.source_plan_hash = NEW.source_plan_hash
      AND cache_entry.source_approval_id = NEW.source_approval_id
      AND cache_entry.source_node_id = NEW.source_node_id
      AND cache_entry.source_succeeded_revision
          = NEW.source_succeeded_revision
      AND cache_entry.source_admission_document_hash
          = NEW.source_admission_document_hash
      AND cache_entry.source_receipt_document_hash
          = NEW.source_receipt_document_hash
      AND cache_entry.trusted_lineage_document_hash
          = NEW.source_trusted_lineage_document_hash
      AND cache_entry.cache_key_document_json
          = NEW.cache_key_document_json
      AND cache_entry.cache_key_hash = NEW.cache_key_hash
      AND source_terminal.task_id = NEW.source_task_id
      AND source_terminal.plan_id = NEW.source_plan_id
      AND source_terminal.plan_hash = NEW.source_plan_hash
      AND source_terminal.approval_id = NEW.source_approval_id
      AND source_terminal.node_id = NEW.source_node_id
      AND source_terminal.node_revision = NEW.source_succeeded_revision
      AND source_terminal.node_state = 'Succeeded'
      AND source_terminal.receipt_document_hash
          = NEW.source_receipt_document_hash
      AND source_admission.document_hash
          = NEW.source_admission_document_hash
      AND target_task.project_id = NEW.project_id
      AND target_task.principal_id = NEW.principal_id
      AND target_task.status IN ('AwaitingApproval', 'Running')
      AND target_plan.plan_id = NEW.target_plan_id
      AND target_plan.plan_hash = NEW.target_plan_hash
      AND target_approval.approval_id = NEW.target_approval_id
      AND target_approval.decision = 'approved'
      AND target_binding.project_id = NEW.project_id
      AND target_binding.principal_id = NEW.principal_id
      AND target_binding.owner_id = NEW.target_input_owner_id
      AND target_binding.term_acquired_at
          = NEW.target_input_term_acquired_at
      AND NEW.target_input_fencing_token = NEW.completion_fencing_token
      AND NEW.target_input_owner_id = NEW.completion_owner_id
      AND NEW.target_input_term_acquired_at
          = NEW.completion_term_acquired_at
      AND target_binding.recorded_at_us <= NEW.recorded_at_us
      AND NEW.target_succeeded_revision = NEW.target_pending_revision + 1
      AND target_pending.recorded_at_us <= NEW.recorded_at_us
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = target_pending.task_id
            AND later.plan_id = target_pending.plan_id
            AND later.node_id = target_pending.node_id
            AND later.revision > target_pending.revision
      )
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_execution_admissions AS target_admission
          WHERE target_admission.task_id = NEW.target_task_id
            AND target_admission.plan_id = NEW.target_plan_id
            AND target_admission.node_id = NEW.target_node_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM dispatch_intents AS target_intent
          WHERE target_intent.task_id = NEW.target_task_id
            AND target_intent.plan_id = NEW.target_plan_id
            AND target_intent.node_id = NEW.target_node_id
      )
      AND event.event_type = 'node_succeeded'
      AND event.task_status IN ('Running', 'Succeeded')
      AND event.node_id = NEW.target_node_id
      AND event.document_hash = NEW.event_hash
      AND event.sequence = (
          SELECT MAX(latest.sequence)
          FROM run_events AS latest
          WHERE latest.task_id = NEW.target_task_id
      )
      AND event_commit.project_id = NEW.project_id
      AND event_commit.principal_id = NEW.principal_id
      AND event_commit.fencing_token = NEW.completion_fencing_token
      AND event_commit.recorded_at = NEW.recorded_at
      AND event_commit.recorded_at_us = NEW.recorded_at_us
      AND cache_entry.recorded_at_us <= NEW.recorded_at_us
      AND json_extract(
          NEW.artifact_verification_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.artifact_verification_document_json,
          '$.permission_scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.artifact_verification_document_json,
          '$.permission_scope.principal_id'
      ) = NEW.principal_id
      AND json_extract(
          NEW.artifact_verification_document_json, '$.cache_entry_id'
      ) = NEW.source_cache_entry_id
      AND json_extract(
          NEW.artifact_verification_document_json, '$.cache_key_hash'
      ) = NEW.cache_key_hash
      AND json_extract(
          NEW.artifact_verification_document_json, '$.source.intent_id'
      ) = NEW.source_intent_id
      AND json_extract(
          NEW.artifact_verification_document_json,
          '$.source.receipt_document_hash'
      ) = NEW.source_receipt_document_hash
      AND json_extract(
          NEW.artifact_verification_document_json,
          '$.source.trusted_lineage_document_hash'
      ) = NEW.source_trusted_lineage_document_hash
      AND json_type(
          NEW.artifact_verification_document_json, '$.artifacts'
      ) = 'array'
      AND json_array_length(
          NEW.artifact_verification_document_json, '$.artifacts'
      ) >= 1
      AND NOT EXISTS (
          SELECT 1
          FROM json_tree(
              NEW.artifact_verification_document_json
          ) AS component
          WHERE component.key IS NOT NULL
            AND (
                lower(CAST(component.key AS TEXT)) LIKE '%path%'
                OR lower(CAST(component.key AS TEXT)) IN (
                    'location', 'uri', 'url'
                )
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(
              NEW.artifact_verification_document_json, '$.artifacts'
          ) AS artifact
          WHERE json_type(artifact.value, '$') != 'object'
             OR json_type(artifact.value, '$.output_port') != 'text'
             OR json_type(artifact.value, '$.data_type') != 'text'
             OR json_type(artifact.value, '$.schema_version') != 'text'
             OR json_type(artifact.value, '$.media_type') != 'text'
             OR length(
                    json_extract(artifact.value, '$.content_hash')
                ) != 71
             OR substr(
                    json_extract(artifact.value, '$.content_hash'), 1, 7
                ) != 'sha256:'
             OR json_type(artifact.value, '$.size_bytes') != 'integer'
             OR json_extract(artifact.value, '$.size_bytes') < 0
             OR length(
                    json_extract(
                        artifact.value, '$.artifact_manifest_hash'
                    )
                ) != 71
             OR substr(
                    json_extract(
                        artifact.value, '$.artifact_manifest_hash'
                    ), 1, 7
                ) != 'sha256:'
             OR json_type(artifact.value, '$.symlink') != 'false'
      )
      AND json_extract(
          NEW.output_receipt_document_json, '$.schema_version'
      ) = '1.0.0'
      AND json_extract(
          NEW.output_receipt_document_json, '$.task_id'
      ) = NEW.target_task_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.plan.plan_id'
      ) = NEW.target_plan_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.plan.plan_hash'
      ) = NEW.target_plan_hash
      AND json_extract(
          NEW.output_receipt_document_json, '$.approval_id'
      ) = NEW.target_approval_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.node.node_id'
      ) = NEW.target_node_id
      AND json_extract(
          NEW.output_receipt_document_json,
          '$.node.input_binding_revision'
      ) = NEW.target_pending_revision
      AND json_extract(
          NEW.output_receipt_document_json, '$.node.succeeded_revision'
      ) = NEW.target_succeeded_revision
      AND json_extract(
          NEW.output_receipt_document_json, '$.node.state'
      ) = 'Succeeded'
      AND json_extract(
          NEW.output_receipt_document_json,
          '$.input_binding_document_hash'
      ) = NEW.target_input_binding_document_hash
      AND json_extract(
          NEW.output_receipt_document_json, '$.scope.project_id'
      ) = NEW.project_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.scope.principal_id'
      ) = NEW.principal_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.cache.cache_hit_id'
      ) = NEW.cache_hit_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.cache.cache_entry_id'
      ) = NEW.source_cache_entry_id
      AND json_extract(
          NEW.output_receipt_document_json, '$.cache.cache_key_hash'
      ) = NEW.cache_key_hash
      AND json_extract(
          NEW.output_receipt_document_json,
          '$.cache.source_receipt_document_hash'
      ) = NEW.source_receipt_document_hash
      AND json_extract(
          NEW.output_receipt_document_json,
          '$.cache.trusted_lineage_document_hash'
      ) = NEW.source_trusted_lineage_document_hash
      AND json_extract(
          NEW.output_receipt_document_json,
          '$.cache.artifact_verification_document_hash'
      ) = NEW.artifact_verification_document_hash
      AND json_type(
          NEW.output_receipt_document_json, '$.outputs'
      ) = 'array'
      AND json_array_length(
          NEW.output_receipt_document_json, '$.outputs'
      ) >= 1
      AND json_extract(
          NEW.output_receipt_document_json, '$.succeeded_at'
      ) = event.occurred_at
      AND NOT EXISTS (
          SELECT 1 FROM task_abandonments AS abandonment
          WHERE abandonment.task_id = target_task.task_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_purge_requests AS purge
          WHERE purge.task_id = target_task.task_id
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node cache hit requires exact source and target identities'
    );
END;

CREATE TRIGGER dag_node_cache_hit_facts_are_append_only
BEFORE UPDATE ON dag_node_cache_hit_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG node cache hit facts are append-only');
END;

CREATE TRIGGER dag_node_cache_hit_facts_cannot_be_deleted
BEFORE DELETE ON dag_node_cache_hit_facts
BEGIN
    SELECT RAISE(ABORT, 'DAG node cache hit facts are append-only');
END;

-- Preserve every v21 state cause and add only the exact no-dispatch cache-hit
-- Pending-to-Succeeded cause.
DROP TRIGGER dag_node_transition_state_requires_exact_active_fact;

CREATE TRIGGER dag_node_transition_state_requires_exact_active_fact
BEFORE INSERT ON dag_node_state_events
WHEN NEW.revision > 1 AND NOT (
    (NEW.previous_state = 'Pending' AND NEW.state = 'Queued'
     AND EXISTS (
         SELECT 1
         FROM dag_node_execution_admissions AS admission
         JOIN runtime_supervisor_leases AS lease
           ON lease.project_id = admission.project_id
          AND lease.principal_id = admission.principal_id
          AND lease.fencing_token = admission.admission_fencing_token
         JOIN runtime_supervisor_terms AS term
           ON term.project_id = lease.project_id
          AND term.principal_id = lease.principal_id
          AND term.fencing_token = lease.fencing_token
         WHERE admission.task_id = NEW.task_id
           AND admission.plan_id = NEW.plan_id
           AND admission.plan_hash = NEW.plan_hash
           AND admission.node_id = NEW.node_id
           AND admission.pending_revision = NEW.revision - 1
           AND admission.queued_revision = NEW.revision
           AND admission.admitted_at = NEW.recorded_at
           AND term.owner_id = admission.admission_owner_id
           AND term.acquired_at = admission.admission_term_acquired_at
           AND lease.heartbeat_at_us <= admission.admitted_at_us
           AND lease.expires_at_us > admission.admitted_at_us
     ))
    OR
    (NEW.previous_state = 'Pending' AND NEW.state = 'Blocked'
     AND EXISTS (
         SELECT 1 FROM dag_node_scheduler_transition_facts AS scheduler
         WHERE scheduler.task_id = NEW.task_id
           AND scheduler.plan_id = NEW.plan_id
           AND scheduler.plan_hash = NEW.plan_hash
           AND scheduler.node_id = NEW.node_id
           AND scheduler.previous_revision = NEW.revision - 1
           AND scheduler.node_revision = NEW.revision
           AND scheduler.state = NEW.state
           AND scheduler.recorded_at = NEW.recorded_at
           AND scheduler.recorded_at_us = NEW.recorded_at_us
     ))
    OR
    (NEW.previous_state = 'Pending' AND NEW.state = 'Succeeded'
     AND EXISTS (
         SELECT 1 FROM dag_node_cache_hit_facts AS cache_hit
         WHERE cache_hit.target_task_id = NEW.task_id
           AND cache_hit.target_plan_id = NEW.plan_id
           AND cache_hit.target_plan_hash = NEW.plan_hash
           AND cache_hit.target_node_id = NEW.node_id
           AND cache_hit.target_pending_revision = NEW.revision - 1
           AND cache_hit.target_succeeded_revision = NEW.revision
           AND cache_hit.recorded_at = NEW.recorded_at
           AND cache_hit.recorded_at_us = NEW.recorded_at_us
     ))
    OR
    (EXISTS (
         SELECT 1
         FROM dag_node_execution_admissions AS admission
         JOIN dag_node_execution_transition_facts AS transition
           ON transition.intent_id = admission.intent_id
          AND transition.node_revision = NEW.revision
         WHERE admission.task_id = NEW.task_id
           AND admission.plan_id = NEW.plan_id
           AND admission.plan_hash = NEW.plan_hash
           AND admission.node_id = NEW.node_id
           AND transition.previous_state = NEW.previous_state
           AND transition.state = NEW.state
           AND transition.recorded_at = NEW.recorded_at
           AND transition.recorded_at_us = NEW.recorded_at_us
           AND (NEW.state NOT IN ('Succeeded', 'Failed', 'Cancelled') OR EXISTS (
               SELECT 1 FROM dag_node_terminal_facts AS terminal
               WHERE terminal.intent_id = admission.intent_id
                 AND terminal.node_revision = NEW.revision
                 AND terminal.node_state = NEW.state
                 AND terminal.event_sequence = transition.event_sequence
                 AND terminal.event_hash = transition.event_hash
           ))
     ))
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node state transition requires an exact active fact');
END;

-- P2 still proves the same live Worker/process/attempt.  This additional DAG
-- gate merely requires that the admitted node is the exact current Running
-- projection before P2 may append a checkpoint wait.
DROP TRIGGER dag_node_execution_rejects_checkpoint_wait;

CREATE TRIGGER dag_node_execution_rejects_checkpoint_wait
BEFORE INSERT ON worker_checkpoint_waits
WHEN EXISTS (
    SELECT 1 FROM dag_node_execution_admissions AS admission
    WHERE admission.task_id = NEW.task_id
      AND admission.intent_id = NEW.intent_id
)
AND NOT EXISTS (
    SELECT 1
    FROM dag_node_execution_admissions AS admission
    JOIN tasks AS task
      ON task.task_id = admission.task_id
    JOIN plans AS plan
      ON plan.task_id = task.task_id
     AND plan.plan_id = task.current_plan_id
     AND plan.plan_hash = admission.plan_hash
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dag_node_state_events AS state
      ON state.task_id = admission.task_id
     AND state.plan_id = admission.plan_id
     AND state.plan_hash = admission.plan_hash
     AND state.node_id = admission.node_id
     AND state.revision = admission.queued_revision + 1
     AND state.state = 'Running'
    WHERE admission.task_id = NEW.task_id
      AND admission.intent_id = NEW.intent_id
      AND admission.node_id = NEW.node_id
      AND admission.project_id = NEW.project_id
      AND admission.principal_id = NEW.principal_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status = 'Running'
      AND approval.approval_id = admission.approval_id
      AND approval.decision = 'approved'
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = state.task_id
            AND later.plan_id = state.plan_id
            AND later.node_id = state.node_id
            AND later.revision > state.revision
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'DAG node checkpoint requires the exact current Running node'
    );
END;

-- Cancel and timeout ownership remain unchanged.  A completed same-attempt
-- checkpoint cycle no longer owns the Task after its exact resume outcome.
DROP TRIGGER dag_node_execution_admission_rejects_owned_control;

CREATE TRIGGER dag_node_execution_admission_rejects_owned_control
BEFORE INSERT ON dag_node_execution_admissions
WHEN EXISTS (
    SELECT 1 FROM task_cancel_requests AS cancel
    WHERE cancel.task_id = NEW.task_id
)
OR EXISTS (
    SELECT 1 FROM worker_attempt_timeout_windows AS timeout
    WHERE timeout.task_id = NEW.task_id
)
OR EXISTS (
    SELECT 1
    FROM worker_checkpoint_waits AS checkpoint
    WHERE checkpoint.task_id = NEW.task_id
      AND NOT EXISTS (
          SELECT 1
          FROM task_checkpoint_resume_outcomes AS outcome
          WHERE outcome.checkpoint_id = checkpoint.checkpoint_id
            AND outcome.task_id = checkpoint.task_id
            AND outcome.intent_id = checkpoint.intent_id
            AND outcome.attempt_id = checkpoint.attempt_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'DAG node execution cannot adopt a control-owned Task');
END;
