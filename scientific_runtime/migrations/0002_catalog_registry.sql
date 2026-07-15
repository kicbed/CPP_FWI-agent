CREATE TABLE dataset_versions (
    dataset_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    data_type TEXT NOT NULL,
    core_hash TEXT NOT NULL,
    first_registered_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, version),
    UNIQUE (dataset_id, version, content_hash, data_type)
);

CREATE TABLE dataset_catalog (
    project_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    data_type TEXT NOT NULL,
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    PRIMARY KEY (project_id, dataset_id, version),
    FOREIGN KEY (dataset_id, version, content_hash, data_type)
        REFERENCES dataset_versions(dataset_id, version, content_hash, data_type)
);

CREATE TABLE algorithm_registry (
    algorithm_id TEXT NOT NULL,
    version TEXT NOT NULL,
    allowlisted INTEGER NOT NULL CHECK (allowlisted IN (0, 1)),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    PRIMARY KEY (algorithm_id, version)
);

CREATE TABLE approval_budgets (
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    max_tasks INTEGER NOT NULL CHECK (
        typeof(max_tasks) = 'integer' AND max_tasks >= 1
    ),
    tasks_used INTEGER NOT NULL DEFAULT 0 CHECK (
        typeof(tasks_used) = 'integer'
        AND tasks_used >= 0
        AND tasks_used <= max_tasks
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, approval_id),
    FOREIGN KEY (task_id, approval_id)
        REFERENCES approvals(task_id, approval_id)
);

INSERT INTO approval_budgets(
    task_id, approval_id, max_tasks, tasks_used, created_at, updated_at
)
SELECT
    task_id,
    approval_id,
    CASE
        WHEN json_type(document_json, '$.scope.max_tasks') = 'integer'
        THEN json_extract(document_json, '$.scope.max_tasks')
        ELSE NULL
    END,
    0,
    recorded_at,
    recorded_at
FROM approvals;

CREATE INDEX idx_dataset_catalog_project
    ON dataset_catalog(project_id, dataset_id, version);
CREATE INDEX idx_algorithm_registry_allowlisted
    ON algorithm_registry(allowlisted, algorithm_id, version);

CREATE TRIGGER dataset_versions_are_immutable
BEFORE UPDATE ON dataset_versions
BEGIN
    SELECT RAISE(ABORT, 'dataset versions are immutable');
END;

CREATE TRIGGER dataset_versions_cannot_be_deleted
BEFORE DELETE ON dataset_versions
BEGIN
    SELECT RAISE(ABORT, 'dataset versions are immutable');
END;

CREATE TRIGGER dataset_catalog_is_immutable
BEFORE UPDATE ON dataset_catalog
BEGIN
    SELECT RAISE(ABORT, 'dataset catalog entries are immutable');
END;

CREATE TRIGGER dataset_catalog_cannot_be_deleted
BEFORE DELETE ON dataset_catalog
BEGIN
    SELECT RAISE(ABORT, 'dataset catalog entries are immutable');
END;

CREATE TRIGGER algorithm_registry_is_immutable
BEFORE UPDATE ON algorithm_registry
BEGIN
    SELECT RAISE(ABORT, 'algorithm registry entries are immutable');
END;

CREATE TRIGGER algorithm_registry_cannot_be_deleted
BEFORE DELETE ON algorithm_registry
BEGIN
    SELECT RAISE(ABORT, 'algorithm registry entries are immutable');
END;

CREATE TRIGGER approvals_initialize_budget
AFTER INSERT ON approvals
BEGIN
    INSERT INTO approval_budgets(
        task_id, approval_id, max_tasks, tasks_used, created_at, updated_at
    ) VALUES (
        NEW.task_id,
        NEW.approval_id,
        CASE
            WHEN json_type(NEW.document_json, '$.scope.max_tasks') = 'integer'
            THEN json_extract(NEW.document_json, '$.scope.max_tasks')
            ELSE NULL
        END,
        0,
        NEW.recorded_at,
        NEW.recorded_at
    );
END;

CREATE TRIGGER approval_budget_identity_is_immutable
BEFORE UPDATE OF task_id, approval_id, max_tasks, created_at ON approval_budgets
BEGIN
    SELECT RAISE(ABORT, 'approval budget identity is immutable');
END;

CREATE TRIGGER approval_budget_usage_is_monotonic
BEFORE UPDATE OF tasks_used ON approval_budgets
WHEN NEW.tasks_used < OLD.tasks_used
BEGIN
    SELECT RAISE(ABORT, 'approval budget usage cannot decrease');
END;

CREATE TRIGGER approval_budgets_cannot_be_deleted
BEFORE DELETE ON approval_budgets
BEGIN
    SELECT RAISE(ABORT, 'approval budgets cannot be deleted');
END;
