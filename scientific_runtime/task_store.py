"""SQLite authority for D-003 task identity and immutable runtime records.

The store is deliberately unaware of HTTP, MCP, workers, and dataset paths.
Business validation belongs to :mod:`scientific_runtime.task_service`; this
module provides short atomic transactions and durable read models.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
APPLICATION_ID = 0x53525431  # ASCII "SRT1"
SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

TASK_STATUSES = frozenset(
    {
        "Draft",
        "NeedsInput",
        "AwaitingApproval",
        "Queued",
        "Running",
        "Waiting",
        "Retrying",
        "Succeeded",
        "Failed",
        "Cancelled",
    }
)

WORKBENCH_MUTATION_OPERATIONS = frozenset(
    {"revise_draft", "persist_plan", "persist_approval", "abandon_task"}
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "Draft": frozenset({"Draft", "NeedsInput", "AwaitingApproval", "Cancelled"}),
    "NeedsInput": frozenset(
        {"NeedsInput", "Draft", "AwaitingApproval", "Cancelled"}
    ),
    "AwaitingApproval": frozenset({"AwaitingApproval", "Queued", "Cancelled"}),
    "Queued": frozenset({"Running", "Failed", "Cancelled"}),
    "Running": frozenset(
        {"Running", "Waiting", "Retrying", "Succeeded", "Failed", "Cancelled"}
    ),
    "Waiting": frozenset({"Running", "Failed", "Cancelled"}),
    "Retrying": frozenset({"Running", "Failed", "Cancelled"}),
    # Terminal-state retries are reads, not new transitions/events.
    "Succeeded": frozenset(),
    "Failed": frozenset(),
    "Cancelled": frozenset(),
}


class TaskStoreError(RuntimeError):
    """Base class for stable persistence failures."""


class TaskStoreConflict(TaskStoreError):
    """A uniqueness, revision, relationship, or state precondition failed."""


class IdempotencyConflict(TaskStoreConflict):
    """An idempotency key was replayed with a different request."""


class TaskStoreCorruption(TaskStoreError):
    """Persisted data is inconsistent with the migration contract."""


class TaskStoreUnavailable(TaskStoreError):
    """The durable store could not acquire its bounded SQLite lock."""


@dataclass(frozen=True)
class TaskSnapshot:
    """Current durable view of one task aggregate."""

    task_id: str
    project_id: str
    principal_id: str
    status: str
    draft: dict[str, Any]
    plan: dict[str, Any] | None
    approval: dict[str, Any] | None
    created_at: str
    updated_at: str
    abandonment: dict[str, Any] | None = None


@dataclass(frozen=True)
class CreateTaskRecord:
    """Store result indicating whether an idempotent create was replayed."""

    snapshot: TaskSnapshot
    replayed: bool


@dataclass(frozen=True)
class RegistryWriteRecord:
    """Immutable registry document and whether the write was a replay."""

    document: dict[str, Any]
    replayed: bool


@dataclass(frozen=True)
class RegistrySnapshots:
    """Server-owned registry documents read from one SQLite snapshot."""

    datasets: dict[tuple[str, str], dict[str, Any]]
    algorithms: dict[tuple[str, str], dict[str, Any]]


@dataclass(frozen=True)
class ApprovalBudget:
    """Durable task-count budget bound to one immutable approval."""

    task_id: str
    approval_id: str
    max_tasks: int
    tasks_used: int


@dataclass(frozen=True)
class SubmitGateContext:
    """Current server-owned submit inputs pinned by one write transaction."""

    snapshot: TaskSnapshot
    registry: RegistrySnapshots
    budget: ApprovalBudget


@dataclass(frozen=True)
class DispatchIntentSnapshot:
    """Durable P1 dispatch state; pending intents require P2 reconciliation."""

    intent_id: str
    task_id: str
    plan_id: str
    plan_hash: str
    approval_id: str
    node_id: str
    node_idempotency_key: str
    adapter_id: str
    adapter_version: str
    request: dict[str, Any]
    request_hash: str
    queue_fingerprint: dict[str, Any]
    state: str
    handle: dict[str, Any] | None
    failure_code: str | None
    created_at: str
    dispatch_claimed_at: str | None
    outcome_recorded_at: str | None


@dataclass(frozen=True)
class SubmitTaskRecord:
    """Atomic submit result and whether the operation was an exact replay."""

    snapshot: TaskSnapshot
    intent: DispatchIntentSnapshot
    replayed: bool


@dataclass(frozen=True)
class WorkbenchMutationRecord:
    """A durable non-submit Workbench mutation and its stable outcome."""

    task_id: str
    operation: str
    outcome: dict[str, Any]


@dataclass(frozen=True)
class _Migration:
    version: int
    path: Path
    text: str
    checksum: str


class TaskStore(Protocol):
    """Storage boundary used by the P1 TaskService."""

    def register_dataset(
        self, *, dataset: Mapping[str, Any], now: str
    ) -> RegistryWriteRecord:
        ...

    def register_algorithm(
        self, *, manifest: Mapping[str, Any], now: str
    ) -> RegistryWriteRecord:
        ...

    def get_dataset(
        self, *, project_id: str, dataset_id: str, version: str
    ) -> dict[str, Any] | None:
        ...

    def list_datasets(self, *, project_id: str) -> list[dict[str, Any]]:
        ...

    def get_algorithm(
        self, *, algorithm_id: str, version: str
    ) -> dict[str, Any] | None:
        ...

    def list_algorithms(self) -> list[dict[str, Any]]:
        ...

    def get_approval_budget(
        self, *, task_id: str, approval_id: str
    ) -> ApprovalBudget | None:
        ...

    def load_registry_snapshots(
        self,
        *,
        project_id: str,
        dataset_keys: Sequence[tuple[str, str]],
        algorithm_keys: Sequence[tuple[str, str]],
    ) -> RegistrySnapshots:
        ...

    def create_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        draft: Mapping[str, Any],
        idempotency_key: str,
        request_hash: str,
        now: str,
    ) -> CreateTaskRecord:
        ...

    def lookup_create_task(
        self,
        *,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CreateTaskRecord | None:
        ...

    def lookup_submit_task(
        self,
        *,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> SubmitTaskRecord | None:
        ...

    def submit_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        approval_id: str,
        idempotency_key: str,
        request_hash: str,
        admit: Callable[
            [SubmitGateContext, str],
            tuple[Mapping[str, Any], Mapping[str, Any]],
        ],
        clock: Callable[[], str],
    ) -> SubmitTaskRecord:
        ...

    def get_dispatch_intent(self, task_id: str) -> DispatchIntentSnapshot | None:
        ...

    def record_dispatch_success(
        self,
        *,
        intent_id: str,
        handle: Mapping[str, Any],
        now: str,
    ) -> DispatchIntentSnapshot:
        ...

    def claim_dispatch(
        self, *, intent_id: str, now: str
    ) -> tuple[DispatchIntentSnapshot, bool]:
        ...

    def record_dispatch_reconciliation(
        self,
        *,
        intent_id: str,
        failure_code: str,
        now: str,
    ) -> DispatchIntentSnapshot:
        ...

    def get_task(self, task_id: str) -> TaskSnapshot | None:
        ...

    def lookup_workbench_mutation(
        self,
        *,
        project_id: str,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> WorkbenchMutationRecord | None:
        ...

    def append_draft_revision(
        self,
        *,
        task_id: str,
        expected_revision: int,
        draft: Mapping[str, Any],
        now: str,
        project_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> TaskSnapshot:
        ...

    def store_plan(
        self,
        *,
        task_id: str,
        plan: Mapping[str, Any],
        now: str,
        project_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> TaskSnapshot:
        ...

    def store_approval(
        self,
        *,
        task_id: str,
        approval: Mapping[str, Any],
        now: str,
        project_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> TaskSnapshot:
        ...

    def abandon_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        abandonment: Mapping[str, Any],
        idempotency_key: str,
        request_hash: str,
        now: str,
    ) -> tuple[TaskSnapshot, bool]:
        ...

    def commit_runtime_transition(
        self,
        *,
        task_id: str,
        expected_status: str,
        event: Mapping[str, Any],
        now: str,
    ) -> TaskSnapshot:
        ...

    def list_run_events(
        self, task_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        ...


def encode_document(value: Mapping[str, Any]) -> tuple[str, str]:
    """Return deterministic JSON text and its SHA-256 identity.

    TaskDraft legitimately contains floating-point confidence values, so this
    is intentionally distinct from the integer-only PlanGraph hash profile.
    """

    try:
        text = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise TaskStoreConflict(f"document is not finite JSON: {error}") from error
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, f"sha256:{digest}"


def _decode_document(text: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise TaskStoreCorruption(f"invalid persisted JSON for {label}") from error
    if not isinstance(value, dict):
        raise TaskStoreCorruption(f"persisted {label} must be a JSON object")
    return value


def _raise_operational_error(error: sqlite3.OperationalError) -> None:
    error_code = getattr(error, "sqlite_errorcode", None)
    message = str(error).lower()
    # SQLITE_BUSY=5 and SQLITE_LOCKED=6. Python 3.10 does not expose the
    # symbolic constants, and extended result codes retain the low byte.
    if (
        (isinstance(error_code, int) and error_code & 0xFF in {5, 6})
        or "database is locked" in message
        or "database is busy" in message
    ):
        raise TaskStoreUnavailable("task store is busy") from error
    raise TaskStoreError("SQLite task-store operation failed") from error


def _decode_hashed_document(
    row: Mapping[str, Any], *, label: str
) -> dict[str, Any]:
    value = _decode_document(row["document_json"], label=label)
    try:
        _, actual_hash = encode_document(value)
    except TaskStoreConflict as error:
        raise TaskStoreCorruption(f"persisted {label} is not finite JSON") from error
    if actual_hash != row["document_hash"]:
        raise TaskStoreCorruption(f"persisted {label} hash does not match its content")
    return value


def _load_migrations() -> tuple[_Migration, ...]:
    migrations: list[_Migration] = []
    for path in sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        prefix = path.name.split("_", 1)[0]
        version = int(prefix)
        text = path.read_text(encoding="utf-8")
        migrations.append(
            _Migration(
                version=version,
                path=path,
                text=text,
                checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    if not migrations:
        raise TaskStoreCorruption("no task-store migrations are available")
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise TaskStoreCorruption(
            "task-store migration versions must be contiguous from one"
        )
    return tuple(migrations)


def _migration_statements(text: str) -> Sequence[str]:
    """Split SQL with sqlite's parser, preserving trigger bodies atomically."""

    statements: list[str] = []
    pending = ""
    for line in text.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise TaskStoreCorruption("incomplete SQLite migration statement")
    return statements


def _schema_manifest(connection: sqlite3.Connection) -> tuple[tuple[str, ...], ...]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return tuple(
        (
            str(row["type"]),
            str(row["name"]),
            str(row["tbl_name"]),
            " ".join(str(row["sql"]).split()) if row["sql"] is not None else "",
        )
        for row in rows
    )


def _expected_schema_manifest(
    migrations: Sequence[_Migration],
) -> tuple[tuple[str, ...], ...]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(SCHEMA_MIGRATIONS_SQL)
        for migration in migrations:
            for statement in _migration_statements(migration.text):
                connection.execute(statement)
        return _schema_manifest(connection)
    finally:
        connection.close()


class SQLiteTaskStore:
    """File-backed SQLite implementation with WAL and per-operation connections."""

    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5000):
        raw_path = Path(database_path).expanduser()
        if str(database_path) == ":memory:":
            raise ValueError("the durable task store cannot use an in-memory database")
        if not raw_path.is_absolute():
            raise ValueError("the durable task database path must be absolute")
        raw_path = Path(os.path.abspath(raw_path))
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        if any(part.is_symlink() for part in (raw_path, *raw_path.parents)):
            raise ValueError("the task database path cannot traverse a symbolic link")
        raw_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not raw_path.parent.is_dir():
            raise ValueError("the task database parent must be a directory")
        if os.stat(raw_path.parent).st_mode & 0o077:
            raise ValueError("the task database parent must be a dedicated private directory")
        self.database_path = raw_path
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._initialize()

    def _initialize(self) -> None:
        """Serialize first-open inspection without changing an unrelated DB."""

        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(self.database_path, flags | os.O_EXCL, 0o600)
        except FileExistsError:
            lock_fd = os.open(
                self.database_path,
                os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
        locked = False
        try:
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise TaskStoreError("task database is not a regular private file")
            deadline = time.monotonic() + self.busy_timeout_ms / 1000
            while not locked:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError as error:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TaskStoreUnavailable(
                            "task store initialization is busy"
                        ) from error
                    time.sleep(min(0.01, remaining))
            self._initialize_locked()
        finally:
            if locked:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _connect(self, *, require_wal: bool = True) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise TaskStoreError("SQLite foreign key enforcement is unavailable")
            if require_wal:
                journal_mode = str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower()
                if journal_mode != "wal":
                    raise TaskStoreError(
                        "SQLite WAL mode is required for the task store"
                    )
        except sqlite3.OperationalError as error:
            connection.close()
            _raise_operational_error(error)
        except sqlite3.DatabaseError as error:
            connection.close()
            raise TaskStoreCorruption("task database is not valid SQLite") from error
        except Exception:
            connection.close()
            raise
        return connection

    def _initialize_locked(self) -> None:
        migrations = _load_migrations()
        latest_version = migrations[-1].version
        expected_manifest = _expected_schema_manifest(migrations)
        connection = self._connect(require_wal=False)
        try:
            preflight_user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            preflight_application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            preflight_objects = connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
            if preflight_objects and preflight_application_id != APPLICATION_ID:
                raise TaskStoreError(
                    "refusing to claim an existing non-task SQLite database"
                )
            if not preflight_objects and (
                preflight_user_version != 0 or preflight_application_id != 0
            ):
                raise TaskStoreError(
                    "task database migration metadata is inconsistent"
                )

            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise TaskStoreError("SQLite WAL mode is required for the task store")

            connection.execute("BEGIN IMMEDIATE")
            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            existing_objects = connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
            first_install = not existing_objects
            if first_install:
                if user_version != 0 or application_id != 0:
                    raise TaskStoreError(
                        "task database migration metadata is inconsistent"
                    )
                connection.execute(SCHEMA_MIGRATIONS_SQL)
            else:
                if application_id != APPLICATION_ID:
                    raise TaskStoreError(
                        "refusing to claim an existing non-task SQLite database"
                    )
                if not any(
                    row["type"] == "table" and row["name"] == "schema_migrations"
                    for row in existing_objects
                ):
                    raise TaskStoreError(
                        "task database migration metadata is inconsistent"
                    )
            applied_rows = connection.execute(
                """
                SELECT version, name, checksum
                FROM schema_migrations ORDER BY version ASC
                """
            ).fetchall()
            applied_versions = [int(row["version"]) for row in applied_rows]
            if (
                any(version > latest_version for version in applied_versions)
                or user_version > latest_version
            ):
                raise TaskStoreError(
                    "task database was created by a newer migration version"
                )
            expected_applied_versions = list(range(1, user_version + 1))
            if applied_versions != expected_applied_versions:
                raise TaskStoreError(
                    "task database migration metadata is inconsistent"
                )
            migrations_by_version = {
                migration.version: migration for migration in migrations
            }
            for row in applied_rows:
                migration = migrations_by_version[int(row["version"])]
                if row["name"] != migration.path.name:
                    raise TaskStoreError(
                        "task database migration metadata is inconsistent"
                    )
                if row["checksum"] != migration.checksum:
                    raise TaskStoreError(
                        "applied task-store migration checksum changed"
                    )

            for migration in migrations[user_version:]:
                for statement in _migration_statements(migration.text):
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, name, checksum, applied_at)
                    VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (migration.version, migration.path.name, migration.checksum),
                )
                connection.execute(f"PRAGMA user_version = {migration.version}")
            if first_install:
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) != latest_version:
                raise TaskStoreError(
                    "task database migration metadata is inconsistent"
                )
            if _schema_manifest(connection) != expected_manifest:
                raise TaskStoreCorruption(
                    "task database schema does not match the applied migration"
                )
            quick_check = [
                str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
            if quick_check != ["ok"]:
                raise TaskStoreCorruption("SQLite quick_check failed for the task store")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise TaskStoreCorruption(
                    "SQLite foreign_key_check failed for the task store"
                )
            connection.commit()
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.DatabaseError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreCorruption(
                "task database schema or integrity check failed"
            ) from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        if self.database_path.is_symlink() or not self.database_path.is_file():
            raise TaskStoreError("task database is not a regular private file")
        os.chmod(self.database_path, 0o600)

    def journal_mode(self) -> str:
        connection = self._connect()
        try:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            connection.close()

    def migration_version(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()

    @staticmethod
    def _dataset_core_hash(dataset: Mapping[str, Any]) -> str:
        core = dict(dataset)
        core.pop("access_scope", None)
        _, core_hash = encode_document(core)
        return core_hash

    def _load_dataset_registration(
        self, connection: sqlite3.Connection, row: Mapping[str, Any]
    ) -> dict[str, Any]:
        dataset = _decode_hashed_document(row, label="dataset registration")
        indexed = {
            "id": row["dataset_id"],
            "version": row["version"],
            "content_hash": row["content_hash"],
            "data_type": row["data_type"],
        }
        if any(dataset.get(key) != value for key, value in indexed.items()):
            raise TaskStoreCorruption(
                "persisted dataset identity does not match its index"
            )
        access_scope = dataset.get("access_scope")
        if (
            not isinstance(access_scope, dict)
            or access_scope.get("project_id") != row["project_id"]
        ):
            raise TaskStoreCorruption(
                "persisted dataset project does not match its index"
            )
        version_row = connection.execute(
            """
            SELECT content_hash, data_type, core_hash
            FROM dataset_versions
            WHERE dataset_id = ? AND version = ?
            """,
            (row["dataset_id"], row["version"]),
        ).fetchone()
        if version_row is None:
            raise TaskStoreCorruption(
                "dataset catalog entry references a missing version"
            )
        if (
            version_row["content_hash"] != row["content_hash"]
            or version_row["data_type"] != row["data_type"]
            or version_row["core_hash"] != self._dataset_core_hash(dataset)
        ):
            raise TaskStoreCorruption(
                "persisted dataset version does not match its catalog entry"
            )
        return dataset

    def register_dataset(
        self, *, dataset: Mapping[str, Any], now: str
    ) -> RegistryWriteRecord:
        document_json, document_hash = encode_document(dataset)
        core_hash = self._dataset_core_hash(dataset)
        project_id = dataset["access_scope"]["project_id"]
        identity = (
            dataset["id"],
            dataset["version"],
            dataset["content_hash"],
            dataset["data_type"],
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version_row = connection.execute(
                """
                SELECT content_hash, data_type, core_hash
                FROM dataset_versions
                WHERE dataset_id = ? AND version = ?
                """,
                identity[:2],
            ).fetchone()
            if version_row is None:
                connection.execute(
                    """
                    INSERT INTO dataset_versions(
                        dataset_id, version, content_hash, data_type,
                        core_hash, first_registered_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (*identity, core_hash, now),
                )
            elif (
                version_row["content_hash"] != identity[2]
                or version_row["data_type"] != identity[3]
                or version_row["core_hash"] != core_hash
            ):
                raise TaskStoreConflict(
                    "dataset id/version already identifies different immutable content"
                )

            existing = connection.execute(
                """
                SELECT project_id, dataset_id, version, content_hash, data_type,
                       document_json, document_hash
                FROM dataset_catalog
                WHERE project_id = ? AND dataset_id = ? AND version = ?
                """,
                (project_id, *identity[:2]),
            ).fetchone()
            if existing is not None:
                if existing["document_hash"] != document_hash:
                    raise TaskStoreConflict(
                        "dataset project/version already has a different access snapshot"
                    )
                document = self._load_dataset_registration(connection, existing)
                connection.commit()
                return RegistryWriteRecord(document=document, replayed=True)

            connection.execute(
                """
                INSERT INTO dataset_catalog(
                    project_id, dataset_id, version, content_hash, data_type,
                    document_json, document_hash, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, *identity, document_json, document_hash, now),
            )
            inserted = connection.execute(
                """
                SELECT project_id, dataset_id, version, content_hash, data_type,
                       document_json, document_hash
                FROM dataset_catalog
                WHERE project_id = ? AND dataset_id = ? AND version = ?
                """,
                (project_id, *identity[:2]),
            ).fetchone()
            if inserted is None:
                raise TaskStoreCorruption(
                    "newly registered dataset cannot be read"
                )
            document = self._load_dataset_registration(connection, inserted)
            connection.commit()
            return RegistryWriteRecord(document=document, replayed=False)
        except (TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict(
                "dataset registration conflicts with durable state"
            ) from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def register_algorithm(
        self, *, manifest: Mapping[str, Any], now: str
    ) -> RegistryWriteRecord:
        document_json, document_hash = encode_document(manifest)
        identity = (manifest["id"], manifest["version"])
        allowlisted = int(bool(manifest["security"]["allowlisted"]))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT algorithm_id, version, allowlisted,
                       document_json, document_hash
                FROM algorithm_registry
                WHERE algorithm_id = ? AND version = ?
                """,
                identity,
            ).fetchone()
            if existing is not None:
                if existing["document_hash"] != document_hash:
                    raise TaskStoreConflict(
                        "algorithm id/version already identifies another manifest"
                    )
                document = self._load_algorithm_registration(existing)
                connection.commit()
                return RegistryWriteRecord(document=document, replayed=True)
            connection.execute(
                """
                INSERT INTO algorithm_registry(
                    algorithm_id, version, allowlisted,
                    document_json, document_hash, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (*identity, allowlisted, document_json, document_hash, now),
            )
            inserted = connection.execute(
                """
                SELECT algorithm_id, version, allowlisted,
                       document_json, document_hash
                FROM algorithm_registry
                WHERE algorithm_id = ? AND version = ?
                """,
                identity,
            ).fetchone()
            if inserted is None:
                raise TaskStoreCorruption(
                    "newly registered algorithm cannot be read"
                )
            document = self._load_algorithm_registration(inserted)
            connection.commit()
            return RegistryWriteRecord(document=document, replayed=False)
        except (TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict(
                "algorithm registration conflicts with durable state"
            ) from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _load_algorithm_registration(row: Mapping[str, Any]) -> dict[str, Any]:
        manifest = _decode_hashed_document(row, label="algorithm registration")
        security = manifest.get("security")
        if (
            manifest.get("id") != row["algorithm_id"]
            or manifest.get("version") != row["version"]
            or not isinstance(security, dict)
            or int(bool(security.get("allowlisted"))) != row["allowlisted"]
        ):
            raise TaskStoreCorruption(
                "persisted algorithm identity does not match its index"
            )
        return manifest

    def get_dataset(
        self, *, project_id: str, dataset_id: str, version: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT project_id, dataset_id, version, content_hash, data_type,
                       document_json, document_hash
                FROM dataset_catalog
                WHERE project_id = ? AND dataset_id = ? AND version = ?
                """,
                (project_id, dataset_id, version),
            ).fetchone()
            if row is None:
                return None
            return self._load_dataset_registration(connection, row)
        finally:
            connection.close()

    def list_datasets(self, *, project_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT project_id, dataset_id, version, content_hash, data_type,
                       document_json, document_hash
                FROM dataset_catalog
                WHERE project_id = ?
                ORDER BY dataset_id ASC, version ASC
                """,
                (project_id,),
            ).fetchall()
            return [
                self._load_dataset_registration(connection, row) for row in rows
            ]
        finally:
            connection.close()

    def get_algorithm(
        self, *, algorithm_id: str, version: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT algorithm_id, version, allowlisted,
                       document_json, document_hash
                FROM algorithm_registry
                WHERE algorithm_id = ? AND version = ?
                """,
                (algorithm_id, version),
            ).fetchone()
            if row is None:
                return None
            return self._load_algorithm_registration(row)
        finally:
            connection.close()

    def list_algorithms(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT algorithm_id, version, allowlisted,
                       document_json, document_hash
                FROM algorithm_registry
                ORDER BY algorithm_id ASC, version ASC
                """
            ).fetchall()
            return [self._load_algorithm_registration(row) for row in rows]
        finally:
            connection.close()

    def _load_registry_snapshots(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        dataset_keys: Sequence[tuple[str, str]],
        algorithm_keys: Sequence[tuple[str, str]],
    ) -> RegistrySnapshots:
        datasets: dict[tuple[str, str], dict[str, Any]] = {}
        for key in dict.fromkeys(dataset_keys):
            row = connection.execute(
                """
                SELECT project_id, dataset_id, version, content_hash, data_type,
                       document_json, document_hash
                FROM dataset_catalog
                WHERE project_id = ? AND dataset_id = ? AND version = ?
                """,
                (project_id, *key),
            ).fetchone()
            if row is not None:
                datasets[key] = self._load_dataset_registration(connection, row)
        algorithms: dict[tuple[str, str], dict[str, Any]] = {}
        for key in dict.fromkeys(algorithm_keys):
            row = connection.execute(
                """
                SELECT algorithm_id, version, allowlisted,
                       document_json, document_hash
                FROM algorithm_registry
                WHERE algorithm_id = ? AND version = ?
                """,
                key,
            ).fetchone()
            if row is not None:
                algorithms[key] = self._load_algorithm_registration(row)
        return RegistrySnapshots(datasets=datasets, algorithms=algorithms)

    def load_registry_snapshots(
        self,
        *,
        project_id: str,
        dataset_keys: Sequence[tuple[str, str]],
        algorithm_keys: Sequence[tuple[str, str]],
    ) -> RegistrySnapshots:
        connection = self._connect()
        try:
            # A deferred read transaction pins one consistent WAL snapshot for
            # all requested records.  Future submit code can call the internal
            # helper from its BEGIN IMMEDIATE gate/queue transaction.
            connection.execute("BEGIN")
            snapshots = self._load_registry_snapshots(
                connection,
                project_id=project_id,
                dataset_keys=dataset_keys,
                algorithm_keys=algorithm_keys,
            )
            connection.commit()
            return snapshots
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get_approval_budget(
        self, *, task_id: str, approval_id: str
    ) -> ApprovalBudget | None:
        connection = self._connect()
        try:
            return self._load_approval_budget(
                connection, task_id=task_id, approval_id=approval_id
            )
        finally:
            connection.close()

    @staticmethod
    def _load_approval_budget(
        connection: sqlite3.Connection, *, task_id: str, approval_id: str
    ) -> ApprovalBudget | None:
        row = connection.execute(
            """
            SELECT budget.task_id, budget.approval_id,
                   budget.max_tasks, budget.tasks_used,
                   approval.document_json, approval.document_hash
            FROM approval_budgets AS budget
            JOIN approvals AS approval
              ON approval.task_id = budget.task_id
             AND approval.approval_id = budget.approval_id
            WHERE budget.task_id = ? AND budget.approval_id = ?
            """,
            (task_id, approval_id),
        ).fetchone()
        if row is None:
            return None
        approval = _decode_hashed_document(row, label="approval")
        scope = approval.get("scope")
        if (
            approval.get("approval_id") != row["approval_id"]
            or not isinstance(scope, dict)
            or type(row["max_tasks"]) is not int
            or type(row["tasks_used"]) is not int
            or scope.get("max_tasks") != row["max_tasks"]
            or row["tasks_used"] < 0
            or row["tasks_used"] > row["max_tasks"]
        ):
            raise TaskStoreCorruption(
                "approval budget does not match its decision"
            )
        return ApprovalBudget(
            task_id=row["task_id"],
            approval_id=row["approval_id"],
            max_tasks=row["max_tasks"],
            tasks_used=row["tasks_used"],
        )

    @staticmethod
    def _load_workbench_mutation(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> WorkbenchMutationRecord | None:
        if operation not in WORKBENCH_MUTATION_OPERATIONS:
            raise TaskStoreConflict("workbench mutation operation is invalid")
        row = connection.execute(
            """
            SELECT task_id, request_hash,
                   outcome_json AS document_json,
                   outcome_hash AS document_hash
            FROM workbench_mutations
            WHERE project_id = ? AND principal_id = ?
              AND operation = ? AND idempotency_key = ?
            """,
            (project_id, principal_id, operation, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyConflict(
                "idempotency key was already used for another workbench request"
            )
        outcome = _decode_hashed_document(row, label="workbench mutation outcome")
        valid_outcome = False
        if operation == "revise_draft":
            valid_outcome = (
                set(outcome) == {"task_id", "draft_id", "draft_revision"}
                and isinstance(outcome.get("draft_id"), str)
                and bool(outcome["draft_id"])
                and type(outcome.get("draft_revision")) is int
                and outcome["draft_revision"] >= 1
            )
        elif operation == "persist_plan":
            valid_outcome = (
                set(outcome) == {"task_id", "plan_id", "plan_hash"}
                and isinstance(outcome.get("plan_id"), str)
                and bool(outcome["plan_id"])
                and isinstance(outcome.get("plan_hash"), str)
                and len(outcome["plan_hash"]) == 71
                and outcome["plan_hash"].startswith("sha256:")
                and all(character in "0123456789abcdef" for character in outcome["plan_hash"][7:])
            )
        elif operation == "persist_approval":
            valid_outcome = (
                set(outcome) == {"task_id", "approval_id", "decision"}
                and isinstance(outcome.get("approval_id"), str)
                and bool(outcome["approval_id"])
                and outcome.get("decision") in {"approved", "rejected"}
            )
        elif operation == "abandon_task":
            valid_outcome = (
                set(outcome) == {"task_id", "status"}
                and outcome.get("status") == "Cancelled"
            )
        if not valid_outcome or outcome.get("task_id") != row["task_id"]:
            raise TaskStoreCorruption(
                "workbench mutation outcome is invalid"
            )
        return WorkbenchMutationRecord(
            task_id=row["task_id"], operation=operation, outcome=outcome
        )

    @staticmethod
    def _record_workbench_mutation(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        task_id: str,
        outcome: Mapping[str, Any],
        now: str,
    ) -> None:
        if operation not in WORKBENCH_MUTATION_OPERATIONS:
            raise TaskStoreConflict("workbench mutation operation is invalid")
        outcome_json, outcome_hash = encode_document(outcome)
        connection.execute(
            """
            INSERT INTO workbench_mutations(
                project_id, principal_id, operation, idempotency_key,
                request_hash, task_id, outcome_json, outcome_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                principal_id,
                operation,
                idempotency_key,
                request_hash,
                task_id,
                outcome_json,
                outcome_hash,
                now,
            ),
        )

    @staticmethod
    def _mutation_arguments(
        *,
        project_id: str | None,
        principal_id: str | None,
        idempotency_key: str | None,
        request_hash: str | None,
    ) -> tuple[str, str, str, str] | None:
        values = (project_id, principal_id, idempotency_key, request_hash)
        if all(value is None for value in values):
            return None
        if not all(isinstance(value, str) and value for value in values):
            raise TaskStoreConflict(
                "workbench mutation identity must be provided as one complete set"
            )
        return values  # type: ignore[return-value]

    def lookup_workbench_mutation(
        self,
        *,
        project_id: str,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> WorkbenchMutationRecord | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            record = self._load_workbench_mutation(
                connection,
                project_id=project_id,
                principal_id=principal_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if record is None:
                connection.commit()
                return None
            snapshot = self._load_snapshot(connection, record.task_id)
            if (
                snapshot is None
                or snapshot.project_id != project_id
                or snapshot.principal_id != principal_id
            ):
                raise TaskStoreCorruption(
                    "workbench mutation crosses its task scope"
                )
            connection.commit()
            return record
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        draft: Mapping[str, Any],
        idempotency_key: str,
        request_hash: str,
        now: str,
    ) -> CreateTaskRecord:
        if draft.get("status") not in {"Draft", "NeedsInput", "AwaitingApproval"}:
            raise TaskStoreConflict(
                "task must be created in a pre-runtime draft status"
            )
        document_json, document_hash = encode_document(draft)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                """
                SELECT request_hash, task_id
                FROM idempotency_records
                WHERE project_id = ? AND principal_id = ?
                  AND operation = 'create_task' AND idempotency_key = ?
                """,
                (project_id, principal_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if replay["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "idempotency key was already used for another create request"
                    )
                snapshot = self._load_snapshot(connection, replay["task_id"])
                if snapshot is None:
                    raise TaskStoreCorruption(
                        "idempotency record references a missing task"
                    )
                if (
                    snapshot.project_id != project_id
                    or snapshot.principal_id != principal_id
                ):
                    raise TaskStoreCorruption(
                        "idempotency record crosses its project or principal scope"
                    )
                connection.commit()
                return CreateTaskRecord(snapshot=snapshot, replayed=True)

            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, project_id, principal_id, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    project_id,
                    principal_id,
                    draft["status"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO draft_revisions(
                    task_id, draft_id, revision, document_json,
                    document_hash, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    draft["draft_id"],
                    draft["revision"],
                    document_json,
                    document_hash,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tasks
                SET current_draft_id = ?, current_draft_revision = ?
                WHERE task_id = ?
                """,
                (draft["draft_id"], draft["revision"], task_id),
            )
            response_json, _ = encode_document({"task_id": task_id})
            connection.execute(
                """
                INSERT INTO idempotency_records(
                    project_id, principal_id, operation, idempotency_key,
                    request_hash, task_id, response_json, created_at
                ) VALUES (?, ?, 'create_task', ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    principal_id,
                    idempotency_key,
                    request_hash,
                    task_id,
                    response_json,
                    now,
                ),
            )
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("newly inserted task cannot be read")
            connection.commit()
            return CreateTaskRecord(snapshot=snapshot, replayed=False)
        except (IdempotencyConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("task creation conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def lookup_create_task(
        self,
        *,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CreateTaskRecord | None:
        """Read an existing create mapping before allocating a new task ID.

        ``create_task`` repeats the lookup in its write transaction, so this
        optimization does not weaken concurrent idempotency.
        """

        connection = self._connect()
        try:
            replay = connection.execute(
                """
                SELECT request_hash, task_id
                FROM idempotency_records
                WHERE project_id = ? AND principal_id = ?
                  AND operation = 'create_task' AND idempotency_key = ?
                """,
                (project_id, principal_id, idempotency_key),
            ).fetchone()
            if replay is None:
                return None
            if replay["request_hash"] != request_hash:
                raise IdempotencyConflict(
                    "idempotency key was already used for another create request"
                )
            snapshot = self._load_snapshot(connection, replay["task_id"])
            if snapshot is None:
                raise TaskStoreCorruption(
                    "idempotency record references a missing task"
                )
            if (
                snapshot.project_id != project_id
                or snapshot.principal_id != principal_id
            ):
                raise TaskStoreCorruption(
                    "idempotency record crosses its project or principal scope"
                )
            return CreateTaskRecord(snapshot=snapshot, replayed=True)
        finally:
            connection.close()

    def _load_submit_replay(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> SubmitTaskRecord | None:
        row = connection.execute(
            """
            SELECT record.request_hash, record.task_id, record.response_json,
                   record.created_at AS record_created_at,
                   link.request_hash AS link_request_hash,
                   link.task_id AS link_task_id, link.intent_id,
                   link.created_at AS link_created_at
            FROM idempotency_records AS record
            LEFT JOIN submit_idempotency_links AS link
              ON link.project_id = record.project_id
             AND link.principal_id = record.principal_id
             AND link.operation = record.operation
             AND link.idempotency_key = record.idempotency_key
            WHERE record.project_id = ? AND record.principal_id = ?
              AND record.operation = 'submit_task'
              AND record.idempotency_key = ?
            """,
            (project_id, principal_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyConflict(
                "idempotency key was already used for another submit request"
            )
        if (
            row["intent_id"] is None
            or row["link_request_hash"] != row["request_hash"]
            or row["link_task_id"] != row["task_id"]
            or row["link_created_at"] != row["record_created_at"]
        ):
            raise TaskStoreCorruption(
                "submit idempotency record is not bound to its dispatch intent"
            )
        response = _decode_document(
            row["response_json"], label="submit idempotency response"
        )
        if response != {
            "intent_id": row["intent_id"],
            "task_id": row["task_id"],
        }:
            raise TaskStoreCorruption(
                "submit idempotency response differs from its typed link"
            )
        snapshot = self._load_snapshot(connection, row["task_id"])
        intent = self._load_dispatch_intent(connection, task_id=row["task_id"])
        if snapshot is None or intent is None:
            raise TaskStoreCorruption(
                "submit idempotency record references missing durable state"
            )
        if intent.intent_id != row["intent_id"]:
            raise TaskStoreCorruption(
                "submit idempotency record references another dispatch intent"
            )
        if intent.created_at != row["record_created_at"]:
            raise TaskStoreCorruption(
                "submit idempotency record time differs from its dispatch intent"
            )
        return SubmitTaskRecord(snapshot=snapshot, intent=intent, replayed=True)

    def lookup_submit_task(
        self,
        *,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> SubmitTaskRecord | None:
        connection = self._connect()
        try:
            return self._load_submit_replay(
                connection,
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        finally:
            connection.close()

    def submit_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        approval_id: str,
        idempotency_key: str,
        request_hash: str,
        admit: Callable[
            [SubmitGateContext, str],
            tuple[Mapping[str, Any], Mapping[str, Any]],
        ],
        clock: Callable[[], str],
    ) -> SubmitTaskRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._load_submit_replay(
                connection,
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                connection.commit()
                return replay

            snapshot = self._load_snapshot(connection, task_id)
            if (
                snapshot is None
                or snapshot.project_id != project_id
                or snapshot.principal_id != principal_id
            ):
                raise TaskStoreConflict("task does not exist in the requested scope")
            if snapshot.status != "AwaitingApproval":
                raise TaskStoreConflict("task is not awaiting approval")
            if snapshot.plan is None or snapshot.approval is None:
                raise TaskStoreConflict("task has no current plan and approval")
            if snapshot.approval.get("approval_id") != approval_id:
                raise TaskStoreConflict("approval is not current for this task")
            budget = self._load_approval_budget(
                connection, task_id=task_id, approval_id=approval_id
            )
            if budget is None:
                raise TaskStoreCorruption("current approval has no durable budget")
            try:
                dataset_keys = [
                    (dataset["id"], dataset["version"])
                    for dataset in snapshot.draft["datasets"]
                ]
                algorithm_keys = [
                    (
                        snapshot.draft["algorithm"]["id"],
                        snapshot.draft["algorithm"]["version"],
                    )
                ]
                for node in snapshot.plan["nodes"]:
                    algorithm_keys.append(
                        (node["algorithm"]["id"], node["algorithm"]["version"])
                    )
                    dataset_keys.extend(
                        (binding["dataset"]["id"], binding["dataset"]["version"])
                        for binding in node["inputs"]
                    )
            except (KeyError, TypeError) as error:
                raise TaskStoreCorruption(
                    "current submit documents cannot identify registry records"
                ) from error
            registry = self._load_registry_snapshots(
                connection,
                project_id=project_id,
                dataset_keys=dataset_keys,
                algorithm_keys=algorithm_keys,
            )
            now = clock()
            intent, queued_event = admit(
                SubmitGateContext(
                    snapshot=snapshot,
                    registry=registry,
                    budget=budget,
                ),
                now,
            )
            intent_json, intent_hash = encode_document(intent)
            queue_fingerprint = intent.get("queue_fingerprint")
            if not isinstance(queue_fingerprint, Mapping):
                raise TaskStoreConflict("dispatch intent has no queue fingerprint")
            _, fingerprint_hash = encode_document(queue_fingerprint)
            event_json, event_hash = encode_document(queued_event)
            _, event_fingerprint_hash = encode_document(
                queued_event.get("fingerprint", {})
            )

            if len(snapshot.plan.get("nodes", [])) != 1:
                raise TaskStoreConflict(
                    "P1 submission requires exactly one plan node"
                )
            node = snapshot.plan["nodes"][0]
            indexed_intent = {
                "task_id": task_id,
                "plan_id": snapshot.plan["plan_id"],
                "plan_hash": snapshot.plan["plan_hash"],
                "approval_id": approval_id,
                "node_id": node["node_id"],
                "node_idempotency_key": node["idempotency_key"],
                "created_at": now,
            }
            adapter = intent.get("adapter")
            if (
                intent.get("schema_version") != "1.0.0"
                or any(intent.get(key) != value for key, value in indexed_intent.items())
                or not isinstance(intent.get("intent_id"), str)
                or not isinstance(adapter, Mapping)
                or set(adapter) != {"id", "version"}
                or not isinstance(intent.get("request"), Mapping)
                or queued_event.get("schema_version") != "1.0.0"
                or queued_event.get("sequence") != 1
                or queued_event.get("task_id") != task_id
                or queued_event.get("event_type") != "task_queued"
                or queued_event.get("task_status") != "Queued"
                or queued_event.get("occurred_at") != now
                or queued_event.get("node_id") is not None
                or queued_event.get("fingerprint") != queue_fingerprint
                or event_fingerprint_hash != fingerprint_hash
            ):
                raise TaskStoreConflict(
                    "dispatch intent or queued event differs from current state"
                )

            consumed = connection.execute(
                """
                UPDATE approval_budgets
                SET tasks_used = tasks_used + 1, updated_at = ?
                WHERE task_id = ? AND approval_id = ?
                  AND tasks_used < max_tasks
                """,
                (now, task_id, approval_id),
            )
            if consumed.rowcount != 1:
                raise TaskStoreConflict("approval task budget is exhausted")
            connection.execute(
                """
                INSERT INTO dispatch_intents(
                    intent_id, task_id, plan_id, plan_hash, approval_id,
                    node_id, node_idempotency_key, adapter_id,
                    adapter_version, request_json, request_hash,
                    fingerprint_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent["intent_id"],
                    task_id,
                    snapshot.plan["plan_id"],
                    snapshot.plan["plan_hash"],
                    approval_id,
                    node["node_id"],
                    node["idempotency_key"],
                    adapter["id"],
                    adapter["version"],
                    intent_json,
                    intent_hash,
                    fingerprint_hash,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_events(
                    task_id, sequence, event_id, event_type, task_status,
                    node_id, fingerprint_hash, document_json, document_hash,
                    occurred_at, recorded_at
                ) VALUES (?, 1, ?, 'task_queued', 'Queued', NULL, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    queued_event["event_id"],
                    event_fingerprint_hash,
                    event_json,
                    event_hash,
                    queued_event["occurred_at"],
                    now,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = 'Queued', updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            response_json, _ = encode_document(
                {"intent_id": intent["intent_id"], "task_id": task_id}
            )
            connection.execute(
                """
                INSERT INTO idempotency_records(
                    project_id, principal_id, operation, idempotency_key,
                    request_hash, task_id, response_json, created_at
                ) VALUES (?, ?, 'submit_task', ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    principal_id,
                    idempotency_key,
                    request_hash,
                    task_id,
                    response_json,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO submit_idempotency_links(
                    project_id, principal_id, operation, idempotency_key,
                    request_hash, task_id, intent_id, created_at
                ) VALUES (?, ?, 'submit_task', ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    principal_id,
                    idempotency_key,
                    request_hash,
                    task_id,
                    intent["intent_id"],
                    now,
                ),
            )
            queued_snapshot = self._load_snapshot(connection, task_id)
            stored_intent = self._load_dispatch_intent(
                connection, task_id=task_id
            )
            if queued_snapshot is None or stored_intent is None:
                raise TaskStoreCorruption("submitted task cannot be read")
            connection.commit()
            return SubmitTaskRecord(
                snapshot=queued_snapshot,
                intent=stored_intent,
                replayed=False,
            )
        except (IdempotencyConflict, TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("task submission conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get_task(self, task_id: str) -> TaskSnapshot | None:
        connection = self._connect()
        try:
            # Pin every query used to assemble the aggregate to one WAL read
            # snapshot. Without this transaction a concurrent RunEvent commit
            # could make the task row and latest-event row come from different
            # SQLite snapshots and look corrupt for one read.
            connection.execute("BEGIN")
            snapshot = self._load_snapshot(connection, task_id)
            connection.commit()
            return snapshot
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _load_snapshot(
        self, connection: sqlite3.Connection, task_id: str
    ) -> TaskSnapshot | None:
        row = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        abandonment_row = connection.execute(
            """
            SELECT task_id, project_id, principal_id,
                   document_json, document_hash, abandoned_at
            FROM task_abandonments WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        abandonment = None
        if abandonment_row is not None:
            abandonment = _decode_hashed_document(
                abandonment_row, label="task abandonment"
            )
            actor = abandonment.get("actor")
            if (
                set(abandonment)
                != {
                    "schema_version",
                    "task_id",
                    "previous_status",
                    "status",
                    "reason",
                    "actor",
                    "abandoned_at",
                    "extensions",
                }
                or abandonment.get("schema_version") != "1.0.0"
                or abandonment.get("task_id") != row["task_id"]
                or abandonment.get("previous_status")
                not in {"Draft", "NeedsInput", "AwaitingApproval"}
                or abandonment.get("status") != "Cancelled"
                or abandonment.get("reason") != "user_discarded_draft"
                or actor
                != {"type": "user", "id": abandonment_row["principal_id"]}
                or abandonment.get("abandoned_at")
                != abandonment_row["abandoned_at"]
                or abandonment.get("extensions") != {}
                or abandonment_row["project_id"] != row["project_id"]
                or abandonment_row["principal_id"] != row["principal_id"]
            ):
                raise TaskStoreCorruption(
                    "task abandonment differs from its immutable index"
                )
        pre_runtime_abandoned = row["status"] == "Cancelled" and abandonment is not None
        if (abandonment is None) != (not pre_runtime_abandoned):
            raise TaskStoreCorruption(
                "task abandonment does not match the Cancelled state"
            )
        if row["current_draft_id"] is None or row["current_draft_revision"] is None:
            raise TaskStoreCorruption("task has no current draft revision")
        draft_row = connection.execute(
            """
            SELECT draft_id, revision, document_json, document_hash
            FROM draft_revisions
            WHERE task_id = ? AND draft_id = ? AND revision = ?
            """,
            (
                task_id,
                row["current_draft_id"],
                row["current_draft_revision"],
            ),
        ).fetchone()
        if draft_row is None:
            raise TaskStoreCorruption("current draft revision is missing")
        draft = _decode_hashed_document(draft_row, label="draft")
        if (
            draft.get("draft_id") != draft_row["draft_id"]
            or draft.get("revision") != draft_row["revision"]
        ):
            raise TaskStoreCorruption("persisted draft identity does not match its index")
        if row["status"] in {"Draft", "NeedsInput", "AwaitingApproval"}:
            if draft.get("status") != row["status"]:
                raise TaskStoreCorruption(
                    "task status does not match its current pre-runtime draft"
                )
        elif pre_runtime_abandoned:
            if draft.get("status") != abandonment.get("previous_status"):
                raise TaskStoreCorruption(
                    "abandoned task does not retain its final pre-runtime draft"
                )
        elif draft.get("status") != "AwaitingApproval":
            raise TaskStoreCorruption(
                "runtime task does not retain an AwaitingApproval draft"
            )
        plan = None
        if row["current_plan_id"] is not None:
            plan_row = connection.execute(
                """
                SELECT plan_id, draft_id, draft_revision, plan_hash,
                       document_json, document_hash
                FROM plans WHERE task_id = ? AND plan_id = ?
                """,
                (task_id, row["current_plan_id"]),
            ).fetchone()
            if plan_row is None:
                raise TaskStoreCorruption("current plan is missing")
            plan = _decode_hashed_document(plan_row, label="plan")
            if (
                plan.get("plan_id") != plan_row["plan_id"]
                or plan.get("plan_hash") != plan_row["plan_hash"]
                or plan.get("draft")
                != {
                    "draft_id": plan_row["draft_id"],
                    "revision": plan_row["draft_revision"],
                }
            ):
                raise TaskStoreCorruption(
                    "persisted plan identity does not match its index"
                )
            if plan.get("draft") != {
                "draft_id": row["current_draft_id"],
                "revision": row["current_draft_revision"],
            }:
                raise TaskStoreCorruption(
                    "current plan does not bind the current draft revision"
                )
        approval = None
        if row["current_approval_id"] is not None:
            approval_row = connection.execute(
                """
                SELECT approval_id, plan_id, plan_hash, decision,
                       document_json, document_hash
                FROM approvals
                WHERE task_id = ? AND approval_id = ?
                """,
                (task_id, row["current_approval_id"]),
            ).fetchone()
            if approval_row is None:
                raise TaskStoreCorruption("current approval is missing")
            approval = _decode_hashed_document(approval_row, label="approval")
            if (
                approval.get("approval_id") != approval_row["approval_id"]
                or approval.get("plan_id") != approval_row["plan_id"]
                or approval.get("plan_hash") != approval_row["plan_hash"]
                or approval.get("decision") != approval_row["decision"]
            ):
                raise TaskStoreCorruption(
                    "persisted approval identity does not match its index"
                )
            if (
                plan is None
                or approval.get("plan_id") != plan.get("plan_id")
                or approval.get("plan_hash") != plan.get("plan_hash")
            ):
                raise TaskStoreCorruption(
                    "current approval does not bind the current plan"
                )
            budget = connection.execute(
                """
                SELECT max_tasks, tasks_used
                FROM approval_budgets
                WHERE task_id = ? AND approval_id = ?
                """,
                (task_id, approval_row["approval_id"]),
            ).fetchone()
            approval_scope = approval.get("scope")
            if (
                budget is None
                or not isinstance(approval_scope, dict)
                or type(budget["max_tasks"]) is not int
                or type(budget["tasks_used"]) is not int
                or budget["max_tasks"] != approval_scope.get("max_tasks")
                or budget["tasks_used"] < 0
                or budget["tasks_used"] > budget["max_tasks"]
            ):
                raise TaskStoreCorruption(
                    "current approval budget does not match its decision"
                )
        if row["status"] in {
            "Queued",
            "Running",
            "Waiting",
            "Retrying",
            "Succeeded",
            "Failed",
        } and (plan is None or approval is None or approval.get("decision") != "approved"):
            raise TaskStoreCorruption(
                "submitted task lacks its approved current plan"
            )
        event_summary = connection.execute(
            """
            SELECT COUNT(*) AS event_count,
                   COALESCE(MIN(sequence), 0) AS first_sequence,
                   COALESCE(MAX(sequence), 0) AS last_sequence
            FROM run_events WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        runtime_status = (
            row["status"] not in {"Draft", "NeedsInput", "AwaitingApproval"}
            and not pre_runtime_abandoned
        )
        if not runtime_status and event_summary["event_count"] != 0:
            raise TaskStoreCorruption("pre-runtime task unexpectedly has run events")
        intent_binding = connection.execute(
            """
            SELECT plan_id, plan_hash, approval_id
            FROM dispatch_intents WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if not runtime_status and intent_binding is not None:
            raise TaskStoreCorruption(
                "pre-runtime task unexpectedly has a dispatch intent"
            )
        if runtime_status:
            if (
                intent_binding is None
                or plan is None
                or approval is None
                or intent_binding["plan_id"] != plan.get("plan_id")
                or intent_binding["plan_hash"] != plan.get("plan_hash")
                or intent_binding["approval_id"] != approval.get("approval_id")
            ):
                raise TaskStoreCorruption(
                    "runtime task lacks its current dispatch intent"
                )
            if (
                event_summary["event_count"] == 0
                or event_summary["first_sequence"] != 1
                or event_summary["event_count"] != event_summary["last_sequence"]
            ):
                raise TaskStoreCorruption(
                    "runtime task does not have a contiguous event history"
                )
            first_event = connection.execute(
                """
                SELECT event_type, task_status FROM run_events
                WHERE task_id = ? ORDER BY sequence ASC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            latest_event = connection.execute(
                """
                SELECT task_status FROM run_events
                WHERE task_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if (
                first_event["event_type"] != "task_queued"
                or first_event["task_status"] != "Queued"
            ):
                raise TaskStoreCorruption(
                    "runtime event history does not start with task_queued"
                )
            if latest_event["task_status"] != row["status"]:
                raise TaskStoreCorruption(
                    "task status does not match its latest run event"
                )
            if self._load_dispatch_intent(connection, task_id=task_id) is None:
                raise TaskStoreCorruption(
                    "runtime task dispatch intent cannot be decoded"
                )
        return TaskSnapshot(
            task_id=row["task_id"],
            project_id=row["project_id"],
            principal_id=row["principal_id"],
            status=row["status"],
            draft=draft,
            plan=plan,
            approval=approval,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            abandonment=abandonment,
        )

    def _load_dispatch_intent(
        self, connection: sqlite3.Connection, *, task_id: str
    ) -> DispatchIntentSnapshot | None:
        row = connection.execute(
            """
            SELECT intent.intent_id, intent.task_id, intent.plan_id,
                   intent.plan_hash, intent.approval_id, intent.node_id,
                   intent.node_idempotency_key, intent.adapter_id,
                   intent.adapter_version, intent.request_json AS document_json,
                   intent.request_hash AS document_hash,
                   intent.fingerprint_hash, intent.created_at,
                   attempt.claimed_at AS dispatch_claimed_at,
                   outcome.outcome, outcome.document_json AS outcome_json,
                   outcome.document_hash AS outcome_hash,
                   outcome.recorded_at AS outcome_recorded_at
            FROM dispatch_intents AS intent
            LEFT JOIN dispatch_attempts AS attempt
              ON attempt.intent_id = intent.intent_id
            LEFT JOIN dispatch_outcomes AS outcome
              ON outcome.intent_id = intent.intent_id
            WHERE intent.task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        document = _decode_hashed_document(row, label="dispatch intent")
        required = {
            "schema_version",
            "intent_id",
            "task_id",
            "plan_id",
            "plan_hash",
            "approval_id",
            "node_id",
            "node_idempotency_key",
            "adapter",
            "request",
            "queue_fingerprint",
            "created_at",
        }
        if set(document) != required:
            raise TaskStoreCorruption("dispatch intent fields are inconsistent")
        adapter = document.get("adapter")
        request = document.get("request")
        fingerprint = document.get("queue_fingerprint")
        indexed = {
            "intent_id": row["intent_id"],
            "task_id": row["task_id"],
            "plan_id": row["plan_id"],
            "plan_hash": row["plan_hash"],
            "approval_id": row["approval_id"],
            "node_id": row["node_id"],
            "node_idempotency_key": row["node_idempotency_key"],
            "created_at": row["created_at"],
        }
        if (
            document.get("schema_version") != "1.0.0"
            or any(document.get(key) != value for key, value in indexed.items())
            or not isinstance(adapter, dict)
            or set(adapter) != {"id", "version"}
            or adapter.get("id") != row["adapter_id"]
            or adapter.get("version") != row["adapter_version"]
            or not isinstance(request, dict)
            or not isinstance(fingerprint, dict)
        ):
            raise TaskStoreCorruption("dispatch intent identity is inconsistent")
        request_fields = {
            "task_id",
            "node_id",
            "plan_hash",
            "idempotency_key",
            "project_id",
            "principal_id",
            "algorithm",
            "dataset",
            "task_type",
            "parameters",
            "resources",
            "normalized_config_hash",
        }
        task_identity = connection.execute(
            """
            SELECT task.project_id, task.principal_id,
                   task.current_plan_id, task.current_approval_id,
                   plan.plan_hash AS durable_plan_hash,
                   node.idempotency_key AS durable_node_key
            FROM tasks AS task
            JOIN plans AS plan
              ON plan.task_id = task.task_id AND plan.plan_id = ?
            JOIN plan_node_idempotency AS node
              ON node.task_id = task.task_id
             AND node.plan_id = plan.plan_id AND node.node_id = ?
            WHERE task.task_id = ?
            """,
            (row["plan_id"], row["node_id"], row["task_id"]),
        ).fetchone()
        if (
            set(request) != request_fields
            or task_identity is None
            or task_identity["current_plan_id"] != row["plan_id"]
            or task_identity["current_approval_id"] != row["approval_id"]
            or task_identity["durable_plan_hash"] != row["plan_hash"]
            or task_identity["durable_node_key"] != row["node_idempotency_key"]
            or request.get("task_id") != row["task_id"]
            or request.get("node_id") != row["node_id"]
            or request.get("plan_hash") != row["plan_hash"]
            or request.get("idempotency_key") != row["node_idempotency_key"]
            or request.get("project_id") != task_identity["project_id"]
            or request.get("principal_id") != task_identity["principal_id"]
        ):
            raise TaskStoreCorruption(
                "dispatch intent request differs from durable task state"
            )
        documents = connection.execute(
            """
            SELECT draft.document_json AS draft_json,
                   draft.document_hash AS draft_hash,
                   plan.document_json AS plan_json,
                   plan.document_hash AS plan_document_hash,
                   queued.document_json AS queued_json,
                   queued.document_hash AS queued_hash,
                   queued.fingerprint_hash AS queued_fingerprint_hash,
                   queued.event_type AS queued_type,
                   queued.task_status AS queued_status,
                   queued.node_id AS queued_node_id
            FROM tasks AS task
            JOIN draft_revisions AS draft
              ON draft.task_id = task.task_id
             AND draft.draft_id = task.current_draft_id
             AND draft.revision = task.current_draft_revision
            JOIN plans AS plan
              ON plan.task_id = task.task_id
             AND plan.plan_id = task.current_plan_id
            JOIN run_events AS queued
              ON queued.task_id = task.task_id AND queued.sequence = 1
            WHERE task.task_id = ?
            """,
            (row["task_id"],),
        ).fetchone()
        if documents is None:
            raise TaskStoreCorruption(
                "dispatch intent lacks its durable draft, plan, or queued event"
            )
        draft = _decode_hashed_document(
            {
                "document_json": documents["draft_json"],
                "document_hash": documents["draft_hash"],
            },
            label="dispatch draft",
        )
        plan = _decode_hashed_document(
            {
                "document_json": documents["plan_json"],
                "document_hash": documents["plan_document_hash"],
            },
            label="dispatch plan",
        )
        queued_event = _decode_hashed_document(
            {
                "document_json": documents["queued_json"],
                "document_hash": documents["queued_hash"],
            },
            label="queued event",
        )
        try:
            if len(plan["nodes"]) != 1:
                raise TaskStoreCorruption(
                    "dispatch plan no longer has exactly one node"
                )
            durable_node = plan["nodes"][0]
            if len(durable_node["inputs"]) != 1:
                raise TaskStoreCorruption(
                    "dispatch plan no longer has exactly one input"
                )
            input_identity = durable_node["inputs"][0]["dataset"]
            durable_dataset = next(
                value
                for value in draft["datasets"]
                if all(
                    value[key] == input_identity[key]
                    for key in ("id", "version", "content_hash", "data_type")
                )
            )
            expected_request = {
                "task_id": row["task_id"],
                "node_id": durable_node["node_id"],
                "plan_hash": plan["plan_hash"],
                "idempotency_key": durable_node["idempotency_key"],
                "project_id": task_identity["project_id"],
                "principal_id": task_identity["principal_id"],
                "algorithm": durable_node["algorithm"],
                "dataset": durable_dataset,
                "task_type": plan["task_type"],
                "parameters": durable_node["parameters"],
                "resources": durable_node["resources"],
            }
        except (KeyError, StopIteration, TypeError) as error:
            raise TaskStoreCorruption(
                "dispatch intent cannot be reconstructed from durable state"
            ) from error
        request_without_config_hash = {
            key: value
            for key, value in request.items()
            if key != "normalized_config_hash"
        }
        if request_without_config_hash != expected_request:
            raise TaskStoreCorruption(
                "dispatch intent request payload differs from current plan"
            )
        try:
            _, fingerprint_hash = encode_document(fingerprint)
        except TaskStoreConflict as error:
            raise TaskStoreCorruption(
                "dispatch intent fingerprint is not finite JSON"
            ) from error
        if fingerprint_hash != row["fingerprint_hash"]:
            raise TaskStoreCorruption(
                "dispatch intent fingerprint hash does not match"
            )
        if (
            fingerprint.get("algorithm") != request.get("algorithm")
            or fingerprint.get("seed") != request.get("parameters", {}).get("seed")
            or fingerprint.get("hardware", {}).get("device")
            != request.get("resources", {}).get("device")
            or fingerprint.get("normalized_config_hash")
            != request.get("normalized_config_hash")
            or fingerprint.get("input_hashes")
            != [request.get("dataset", {}).get("content_hash")]
            or documents["queued_type"] != "task_queued"
            or documents["queued_status"] != "Queued"
            or documents["queued_node_id"] is not None
            or queued_event.get("sequence") != 1
            or queued_event.get("task_id") != row["task_id"]
            or queued_event.get("fingerprint") != fingerprint
            or documents["queued_fingerprint_hash"] != fingerprint_hash
        ):
            raise TaskStoreCorruption(
                "dispatch intent fingerprint differs from its request or queued event"
            )

        state = "pending"
        handle: dict[str, Any] | None = None
        failure_code: str | None = None
        dispatch_claimed_at: str | None = None
        outcome_recorded_at: str | None = None
        if row["dispatch_claimed_at"] is not None:
            state = "dispatching"
            dispatch_claimed_at = str(row["dispatch_claimed_at"])
        if row["outcome"] is not None:
            outcome_row = {
                "document_json": row["outcome_json"],
                "document_hash": row["outcome_hash"],
            }
            outcome_document = _decode_hashed_document(
                outcome_row, label="dispatch outcome"
            )
            state = str(row["outcome"])
            outcome_recorded_at = str(row["outcome_recorded_at"])
            if (
                outcome_document.get("status") != state
                or outcome_document.get("recorded_at") != outcome_recorded_at
            ):
                raise TaskStoreCorruption("dispatch outcome identity is inconsistent")
            if state == "dispatched":
                if set(outcome_document) != {"status", "handle", "recorded_at"}:
                    raise TaskStoreCorruption(
                        "successful dispatch outcome fields are inconsistent"
                    )
                value = outcome_document.get("handle")
                if not isinstance(value, dict):
                    raise TaskStoreCorruption("dispatch handle must be an object")
                handle = value
            elif state == "reconciliation_required":
                if set(outcome_document) != {
                    "status",
                    "failure_code",
                    "recorded_at",
                }:
                    raise TaskStoreCorruption(
                        "failed dispatch outcome fields are inconsistent"
                    )
                value = outcome_document.get("failure_code")
                if not isinstance(value, str) or not value:
                    raise TaskStoreCorruption("dispatch failure code is invalid")
                failure_code = value
            else:
                raise TaskStoreCorruption("dispatch outcome state is invalid")
        result = DispatchIntentSnapshot(
            intent_id=row["intent_id"],
            task_id=row["task_id"],
            plan_id=row["plan_id"],
            plan_hash=row["plan_hash"],
            approval_id=row["approval_id"],
            node_id=row["node_id"],
            node_idempotency_key=row["node_idempotency_key"],
            adapter_id=row["adapter_id"],
            adapter_version=row["adapter_version"],
            request=request,
            request_hash=row["document_hash"],
            queue_fingerprint=fingerprint,
            state=state,
            handle=handle,
            failure_code=failure_code,
            created_at=row["created_at"],
            dispatch_claimed_at=dispatch_claimed_at,
            outcome_recorded_at=outcome_recorded_at,
        )
        if handle is not None:
            try:
                self._validate_dispatch_handle(result, handle)
            except TaskStoreConflict as error:
                raise TaskStoreCorruption(
                    "dispatch handle differs from its immutable intent"
                ) from error
        return result

    def get_dispatch_intent(self, task_id: str) -> DispatchIntentSnapshot | None:
        connection = self._connect()
        try:
            return self._load_dispatch_intent(connection, task_id=task_id)
        finally:
            connection.close()

    @staticmethod
    def _task_id_for_intent(
        connection: sqlite3.Connection, intent_id: str
    ) -> str | None:
        row = connection.execute(
            "SELECT task_id FROM dispatch_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        return None if row is None else str(row["task_id"])

    def claim_dispatch(
        self, *, intent_id: str, now: str
    ) -> tuple[DispatchIntentSnapshot, bool]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task_id = self._task_id_for_intent(connection, intent_id)
            if task_id is None:
                raise TaskStoreConflict("dispatch intent does not exist")
            intent = self._load_dispatch_intent(connection, task_id=task_id)
            if intent is None:
                raise TaskStoreCorruption("dispatch intent cannot be read")
            if intent.state != "pending":
                connection.commit()
                return intent, False
            connection.execute(
                "INSERT INTO dispatch_attempts(intent_id, claimed_at) VALUES (?, ?)",
                (intent_id, now),
            )
            claimed = self._load_dispatch_intent(connection, task_id=task_id)
            if claimed is None or claimed.state != "dispatching":
                raise TaskStoreCorruption("claimed dispatch intent cannot be read")
            connection.commit()
            return claimed, True
        except (TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("dispatch claim conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_dispatch_handle(
        intent: DispatchIntentSnapshot, handle: Mapping[str, Any]
    ) -> None:
        required = {
            "submission_id",
            "task_id",
            "node_id",
            "job_id",
            "idempotency_key",
            "plan_hash",
            "request_hash",
            "algorithm",
            "adapter_version",
            "fingerprint",
        }
        algorithm = handle.get("algorithm")
        fingerprint = handle.get("fingerprint")
        request = intent.request
        if (
            set(handle) != required
            or handle.get("task_id") != intent.task_id
            or handle.get("node_id") != intent.node_id
            or handle.get("idempotency_key") != intent.node_idempotency_key
            or handle.get("plan_hash") != intent.plan_hash
            or handle.get("adapter_version") != intent.adapter_version
            or algorithm != request.get("algorithm")
            or not isinstance(fingerprint, Mapping)
            or fingerprint.get("algorithm") != request.get("algorithm")
            or fingerprint.get("seed") != request.get("parameters", {}).get("seed")
            or fingerprint.get("hardware", {}).get("device")
            != request.get("resources", {}).get("device")
            or fingerprint.get("normalized_config_hash")
            != request.get("normalized_config_hash")
            or fingerprint.get("input_hashes")
            != [request.get("dataset", {}).get("content_hash")]
            or not isinstance(handle.get("submission_id"), str)
            or not isinstance(handle.get("job_id"), str)
            or not isinstance(handle.get("request_hash"), str)
        ):
            raise TaskStoreConflict(
                "dispatch handle differs from its immutable intent"
            )

    def record_dispatch_success(
        self,
        *,
        intent_id: str,
        handle: Mapping[str, Any],
        now: str,
    ) -> DispatchIntentSnapshot:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task_id = self._task_id_for_intent(connection, intent_id)
            if task_id is None:
                raise TaskStoreConflict("dispatch intent does not exist")
            intent = self._load_dispatch_intent(connection, task_id=task_id)
            if intent is None:
                raise TaskStoreCorruption("dispatch intent cannot be read")
            self._validate_dispatch_handle(intent, handle)
            if intent.state == "dispatched":
                if intent.handle != dict(handle):
                    raise TaskStoreConflict(
                        "dispatch success differs from the recorded handle"
                    )
                connection.commit()
                return intent
            if intent.state != "dispatching":
                raise TaskStoreConflict("dispatch intent is not claimed")
            document = {
                "status": "dispatched",
                "handle": dict(handle),
                "recorded_at": now,
            }
            document_json, document_hash = encode_document(document)
            connection.execute(
                """
                INSERT INTO dispatch_outcomes(
                    intent_id, outcome, document_json, document_hash, recorded_at
                ) VALUES (?, 'dispatched', ?, ?, ?)
                """,
                (intent_id, document_json, document_hash, now),
            )
            stored = self._load_dispatch_intent(connection, task_id=task_id)
            if stored is None or stored.state != "dispatched":
                raise TaskStoreCorruption("dispatch success cannot be read")
            connection.commit()
            return stored
        except (TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("dispatch outcome conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def record_dispatch_reconciliation(
        self,
        *,
        intent_id: str,
        failure_code: str,
        now: str,
    ) -> DispatchIntentSnapshot:
        if (
            not isinstance(failure_code, str)
            or not failure_code
            or len(failure_code) > 128
            or not failure_code.replace("_", "").isalnum()
            or failure_code.upper() != failure_code
        ):
            raise TaskStoreConflict("dispatch failure code is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task_id = self._task_id_for_intent(connection, intent_id)
            if task_id is None:
                raise TaskStoreConflict("dispatch intent does not exist")
            intent = self._load_dispatch_intent(connection, task_id=task_id)
            if intent is None:
                raise TaskStoreCorruption("dispatch intent cannot be read")
            if intent.state == "reconciliation_required":
                if intent.failure_code != failure_code:
                    raise TaskStoreConflict(
                        "dispatch reconciliation outcome already differs"
                    )
                connection.commit()
                return intent
            if intent.state != "dispatching":
                raise TaskStoreConflict("dispatch intent is not claimed")
            document = {
                "status": "reconciliation_required",
                "failure_code": failure_code,
                "recorded_at": now,
            }
            document_json, document_hash = encode_document(document)
            connection.execute(
                """
                INSERT INTO dispatch_outcomes(
                    intent_id, outcome, document_json, document_hash, recorded_at
                ) VALUES (?, 'reconciliation_required', ?, ?, ?)
                """,
                (intent_id, document_json, document_hash, now),
            )
            stored = self._load_dispatch_intent(connection, task_id=task_id)
            if stored is None or stored.state != "reconciliation_required":
                raise TaskStoreCorruption(
                    "dispatch reconciliation outcome cannot be read"
                )
            connection.commit()
            return stored
        except (TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("dispatch outcome conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def append_draft_revision(
        self,
        *,
        task_id: str,
        expected_revision: int,
        draft: Mapping[str, Any],
        now: str,
        project_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> TaskSnapshot:
        document_json, document_hash = encode_document(draft)
        mutation = self._mutation_arguments(
            project_id=project_id,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, project_id, principal_id,
                       current_draft_id, current_draft_revision
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskStoreConflict("task does not exist")
            if mutation is not None:
                mutation_project, mutation_principal, key, mutation_hash = mutation
                if (
                    row["project_id"] != mutation_project
                    or row["principal_id"] != mutation_principal
                ):
                    raise TaskStoreConflict("workbench mutation crosses task scope")
                replay = self._load_workbench_mutation(
                    connection,
                    project_id=mutation_project,
                    principal_id=mutation_principal,
                    operation="revise_draft",
                    idempotency_key=key,
                    request_hash=mutation_hash,
                )
                if replay is not None:
                    if replay.task_id != task_id:
                        raise IdempotencyConflict(
                            "idempotency key identifies another task"
                        )
                    snapshot = self._load_snapshot(connection, task_id)
                    if snapshot is None:
                        raise TaskStoreCorruption(
                            "workbench replay references a missing task"
                        )
                    connection.commit()
                    return snapshot
            if row["current_draft_revision"] != expected_revision:
                raise TaskStoreConflict("draft revision precondition failed")
            if draft["draft_id"] != row["current_draft_id"]:
                raise TaskStoreConflict("draft_id is immutable within a task")
            if draft["revision"] != expected_revision + 1:
                raise TaskStoreConflict("draft revision must increase by exactly one")
            if row["status"] not in {"Draft", "NeedsInput", "AwaitingApproval"}:
                raise TaskStoreConflict("draft cannot be revised after runtime entry")
            if draft["status"] not in ALLOWED_TRANSITIONS[row["status"]]:
                raise TaskStoreConflict("invalid draft status transition")
            connection.execute(
                """
                INSERT INTO draft_revisions(
                    task_id, draft_id, revision, document_json,
                    document_hash, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    draft["draft_id"],
                    draft["revision"],
                    document_json,
                    document_hash,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, current_draft_revision = ?,
                    current_plan_id = NULL, current_approval_id = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (draft["status"], draft["revision"], now, task_id),
            )
            if mutation is not None:
                self._record_workbench_mutation(
                    connection,
                    project_id=mutation[0],
                    principal_id=mutation[1],
                    operation="revise_draft",
                    idempotency_key=mutation[2],
                    request_hash=mutation[3],
                    task_id=task_id,
                    outcome={
                        "task_id": task_id,
                        "draft_id": draft["draft_id"],
                        "draft_revision": draft["revision"],
                    },
                    now=now,
                )
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("updated task cannot be read")
            connection.commit()
            return snapshot
        except (IdempotencyConflict, TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("draft revision conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def store_plan(
        self,
        *,
        task_id: str,
        plan: Mapping[str, Any],
        now: str,
        project_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> TaskSnapshot:
        document_json, document_hash = encode_document(plan)
        mutation = self._mutation_arguments(
            project_id=project_id,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                """
                SELECT status, project_id, principal_id,
                       current_draft_id, current_draft_revision
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise TaskStoreConflict("task does not exist")
            if mutation is not None:
                mutation_project, mutation_principal, key, mutation_hash = mutation
                if (
                    task["project_id"] != mutation_project
                    or task["principal_id"] != mutation_principal
                ):
                    raise TaskStoreConflict("workbench mutation crosses task scope")
                replay = self._load_workbench_mutation(
                    connection,
                    project_id=mutation_project,
                    principal_id=mutation_principal,
                    operation="persist_plan",
                    idempotency_key=key,
                    request_hash=mutation_hash,
                )
                if replay is not None:
                    if replay.task_id != task_id:
                        raise IdempotencyConflict(
                            "idempotency key identifies another task"
                        )
                    snapshot = self._load_snapshot(connection, task_id)
                    if snapshot is None:
                        raise TaskStoreCorruption(
                            "workbench replay references a missing task"
                        )
                    connection.commit()
                    return snapshot
            if task["status"] != "AwaitingApproval":
                raise TaskStoreConflict(
                    "plans can only target an AwaitingApproval draft"
                )
            expected_draft = {
                "draft_id": task["current_draft_id"],
                "revision": task["current_draft_revision"],
            }
            if plan["draft"] != expected_draft:
                raise TaskStoreConflict("plan does not target the current draft revision")
            existing = connection.execute(
                "SELECT task_id, document_hash FROM plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
            if existing is not None:
                if (
                    existing["task_id"] != task_id
                    or existing["document_hash"] != document_hash
                ):
                    raise TaskStoreConflict("plan_id already identifies another plan")
                if mutation is not None:
                    self._record_workbench_mutation(
                        connection,
                        project_id=mutation[0],
                        principal_id=mutation[1],
                        operation="persist_plan",
                        idempotency_key=mutation[2],
                        request_hash=mutation[3],
                        task_id=task_id,
                        outcome={
                            "task_id": task_id,
                            "plan_id": plan["plan_id"],
                            "plan_hash": plan["plan_hash"],
                        },
                        now=now,
                    )
                snapshot = self._load_snapshot(connection, task_id)
                if snapshot is None:
                    raise TaskStoreCorruption("task with stored plan cannot be read")
                connection.commit()
                return snapshot
            else:
                connection.execute(
                    """
                    INSERT INTO plans(
                        task_id, plan_id, draft_id, draft_revision, plan_hash,
                        document_json, document_hash, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        plan["plan_id"],
                        plan["draft"]["draft_id"],
                        plan["draft"]["revision"],
                        plan["plan_hash"],
                        document_json,
                        document_hash,
                        now,
                    ),
                )
                for node in plan["nodes"]:
                    connection.execute(
                        """
                        INSERT INTO plan_node_idempotency(
                            task_id, plan_id, node_id, idempotency_key
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            plan["plan_id"],
                            node["node_id"],
                            node["idempotency_key"],
                        ),
                    )
            connection.execute(
                """
                UPDATE tasks
                SET current_plan_id = ?, current_approval_id = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (plan["plan_id"], now, task_id),
            )
            if mutation is not None:
                self._record_workbench_mutation(
                    connection,
                    project_id=mutation[0],
                    principal_id=mutation[1],
                    operation="persist_plan",
                    idempotency_key=mutation[2],
                    request_hash=mutation[3],
                    task_id=task_id,
                    outcome={
                        "task_id": task_id,
                        "plan_id": plan["plan_id"],
                        "plan_hash": plan["plan_hash"],
                    },
                    now=now,
                )
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("task with stored plan cannot be read")
            connection.commit()
            return snapshot
        except (IdempotencyConflict, TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("plan conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def store_approval(
        self,
        *,
        task_id: str,
        approval: Mapping[str, Any],
        now: str,
        project_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> TaskSnapshot:
        document_json, document_hash = encode_document(approval)
        mutation = self._mutation_arguments(
            project_id=project_id,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                """
                SELECT status, project_id, principal_id, current_plan_id
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise TaskStoreConflict("task does not exist")
            if mutation is not None:
                mutation_project, mutation_principal, key, mutation_hash = mutation
                if (
                    task["project_id"] != mutation_project
                    or task["principal_id"] != mutation_principal
                ):
                    raise TaskStoreConflict("workbench mutation crosses task scope")
                replay = self._load_workbench_mutation(
                    connection,
                    project_id=mutation_project,
                    principal_id=mutation_principal,
                    operation="persist_approval",
                    idempotency_key=key,
                    request_hash=mutation_hash,
                )
                if replay is not None:
                    if replay.task_id != task_id:
                        raise IdempotencyConflict(
                            "idempotency key identifies another task"
                        )
                    snapshot = self._load_snapshot(connection, task_id)
                    if snapshot is None:
                        raise TaskStoreCorruption(
                            "workbench replay references a missing task"
                        )
                    connection.commit()
                    return snapshot
            if task["status"] != "AwaitingApproval":
                raise TaskStoreConflict(
                    "decisions can only target an AwaitingApproval task"
                )
            if task["current_plan_id"] is None:
                raise TaskStoreConflict("task has no current plan")
            plan = connection.execute(
                "SELECT plan_hash FROM plans WHERE task_id = ? AND plan_id = ?",
                (task_id, task["current_plan_id"]),
            ).fetchone()
            if plan is None:
                raise TaskStoreCorruption("task current plan is missing")
            if (
                approval["plan_id"] != task["current_plan_id"]
                or approval["plan_hash"] != plan["plan_hash"]
            ):
                raise TaskStoreConflict("approval does not bind the current plan hash")
            existing = connection.execute(
                "SELECT task_id, document_hash FROM approvals WHERE approval_id = ?",
                (approval["approval_id"],),
            ).fetchone()
            if existing is not None:
                if (
                    existing["task_id"] != task_id
                    or existing["document_hash"] != document_hash
                ):
                    raise TaskStoreConflict(
                        "approval_id already identifies another decision"
                    )
                if mutation is not None:
                    self._record_workbench_mutation(
                        connection,
                        project_id=mutation[0],
                        principal_id=mutation[1],
                        operation="persist_approval",
                        idempotency_key=mutation[2],
                        request_hash=mutation[3],
                        task_id=task_id,
                        outcome={
                            "task_id": task_id,
                            "approval_id": approval["approval_id"],
                            "decision": approval["decision"],
                        },
                        now=now,
                    )
                snapshot = self._load_snapshot(connection, task_id)
                if snapshot is None:
                    raise TaskStoreCorruption(
                        "task with stored approval cannot be read"
                    )
                connection.commit()
                return snapshot
            else:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        task_id, approval_id, plan_id, plan_hash, decision,
                        document_json, document_hash, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        approval["approval_id"],
                        approval["plan_id"],
                        approval["plan_hash"],
                        approval["decision"],
                        document_json,
                        document_hash,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE tasks
                SET current_approval_id = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (approval["approval_id"], now, task_id),
            )
            if mutation is not None:
                self._record_workbench_mutation(
                    connection,
                    project_id=mutation[0],
                    principal_id=mutation[1],
                    operation="persist_approval",
                    idempotency_key=mutation[2],
                    request_hash=mutation[3],
                    task_id=task_id,
                    outcome={
                        "task_id": task_id,
                        "approval_id": approval["approval_id"],
                        "decision": approval["decision"],
                    },
                    now=now,
                )
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("task with stored approval cannot be read")
            connection.commit()
            return snapshot
        except (IdempotencyConflict, TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("approval conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def abandon_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        abandonment: Mapping[str, Any],
        idempotency_key: str,
        request_hash: str,
        now: str,
    ) -> tuple[TaskSnapshot, bool]:
        """Atomically audit a user discard and terminate only pre-runtime work."""

        document_json, document_hash = encode_document(abandonment)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._load_workbench_mutation(
                connection,
                project_id=project_id,
                principal_id=principal_id,
                operation="abandon_task",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                if replay.task_id != task_id:
                    raise IdempotencyConflict(
                        "idempotency key identifies another task"
                    )
                snapshot = self._load_snapshot(connection, task_id)
                if snapshot is None:
                    raise TaskStoreCorruption(
                        "workbench replay references a missing task"
                    )
                connection.commit()
                return snapshot, True

            task = connection.execute(
                """
                SELECT project_id, principal_id, status
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise TaskStoreConflict("task does not exist")
            if (
                task["project_id"] != project_id
                or task["principal_id"] != principal_id
            ):
                raise TaskStoreConflict("workbench mutation crosses task scope")
            if task["status"] not in {"Draft", "NeedsInput", "AwaitingApproval"}:
                raise TaskStoreConflict(
                    "only a pre-runtime task can be abandoned"
                )
            if "Cancelled" not in ALLOWED_TRANSITIONS[task["status"]]:
                raise TaskStoreConflict("pre-runtime abandonment transition is invalid")
            if connection.execute(
                "SELECT 1 FROM task_abandonments WHERE task_id = ?", (task_id,)
            ).fetchone() is not None:
                raise TaskStoreCorruption(
                    "pre-runtime task already has an abandonment record"
                )
            connection.execute(
                """
                INSERT INTO task_abandonments(
                    task_id, project_id, principal_id, document_json,
                    document_hash, abandoned_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    project_id,
                    principal_id,
                    document_json,
                    document_hash,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tasks SET status = 'Cancelled', updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )
            self._record_workbench_mutation(
                connection,
                project_id=project_id,
                principal_id=principal_id,
                operation="abandon_task",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                task_id=task_id,
                outcome={"task_id": task_id, "status": "Cancelled"},
                now=now,
            )
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None or snapshot.status != "Cancelled":
                raise TaskStoreCorruption("abandoned task cannot be read")
            connection.commit()
            return snapshot, False
        except (IdempotencyConflict, TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict(
                "task abandonment conflicts with durable state"
            ) from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def commit_runtime_transition(
        self,
        *,
        task_id: str,
        expected_status: str,
        event: Mapping[str, Any],
        now: str,
    ) -> TaskSnapshot:
        """Atomically update status and append an already validated RunEvent.

        This is an internal persistence primitive, not a submission API.
        AwaitingApproval -> Queued is owned by ``submit_task`` and its atomic
        registry, approval-budget, Gate, intent, and idempotency transaction.
        This primitive therefore rejects every pre-runtime state.
        """

        document_json, document_hash = encode_document(event)
        _, fingerprint_hash = encode_document(event["fingerprint"])
        node_id = event.get("node_id")
        new_status = event["task_status"]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise TaskStoreConflict("task does not exist")
            if task["status"] != expected_status:
                raise TaskStoreConflict("task status precondition failed")
            if expected_status in {"Draft", "NeedsInput", "AwaitingApproval"}:
                raise TaskStoreConflict(
                    "runtime transition is unavailable before validated submission"
                )
            if event["task_id"] != task_id:
                raise TaskStoreConflict("event task_id does not match the task")
            if new_status not in ALLOWED_TRANSITIONS.get(expected_status, frozenset()):
                raise TaskStoreConflict("invalid task status transition")
            next_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            if event["sequence"] != next_sequence:
                raise TaskStoreConflict("run event sequence is not the next value")
            if node_id is not None:
                baseline = connection.execute(
                    """
                    SELECT fingerprint_hash FROM run_events
                    WHERE task_id = ? AND node_id = ?
                    ORDER BY sequence ASC LIMIT 1
                    """,
                    (task_id, node_id),
                ).fetchone()
                if (
                    baseline is not None
                    and baseline["fingerprint_hash"] != fingerprint_hash
                ):
                    raise TaskStoreConflict(
                        "run fingerprint changed within a plan node"
                    )
            connection.execute(
                """
                INSERT INTO run_events(
                    task_id, sequence, event_id, event_type, task_status,
                    node_id, fingerprint_hash, document_json, document_hash,
                    occurred_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event["sequence"],
                    event["event_id"],
                    event["event_type"],
                    new_status,
                    node_id,
                    fingerprint_hash,
                    document_json,
                    document_hash,
                    event["occurred_at"],
                    now,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (new_status, now, task_id),
            )
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("transitioned task cannot be read")
            connection.commit()
            return snapshot
        except (TaskStoreConflict, TaskStoreCorruption):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            _raise_operational_error(error)
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise TaskStoreConflict("run event conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def list_run_events(
        self, task_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        connection = self._connect()
        try:
            if connection.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone() is None:
                raise TaskStoreConflict("task does not exist")
            rows = connection.execute(
                """
                SELECT task_id, sequence, event_id, event_type, task_status,
                       node_id, fingerprint_hash, occurred_at,
                       document_json, document_hash
                FROM run_events
                WHERE task_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (task_id, after_sequence, limit),
            ).fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                event = _decode_hashed_document(row, label="run event")
                indexed = {
                    "task_id": row["task_id"],
                    "sequence": row["sequence"],
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "task_status": row["task_status"],
                    "node_id": row["node_id"],
                    "occurred_at": row["occurred_at"],
                }
                _, fingerprint_hash = encode_document(event["fingerprint"])
                if (
                    any(event.get(key) != value for key, value in indexed.items())
                    or fingerprint_hash != row["fingerprint_hash"]
                ):
                    raise TaskStoreCorruption(
                        "persisted run event identity does not match its index"
                    )
                events.append(event)
            return events
        finally:
            connection.close()

    def draft_history(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT draft_id, revision, document_json, document_hash
                FROM draft_revisions
                WHERE task_id = ? ORDER BY revision ASC
                """,
                (task_id,),
            ).fetchall()
            values: list[dict[str, Any]] = []
            for row in rows:
                draft = _decode_hashed_document(row, label="draft")
                if (
                    draft.get("draft_id") != row["draft_id"]
                    or draft.get("revision") != row["revision"]
                ):
                    raise TaskStoreCorruption(
                        "persisted draft identity does not match its index"
                    )
                values.append(draft)
            return values
        finally:
            connection.close()

    def plan_history(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT plan_id, draft_id, draft_revision, plan_hash,
                       document_json, document_hash
                FROM plans
                WHERE task_id = ? ORDER BY recorded_at ASC, plan_id ASC
                """,
                (task_id,),
            ).fetchall()
            values: list[dict[str, Any]] = []
            for row in rows:
                plan = _decode_hashed_document(row, label="plan")
                if (
                    plan.get("plan_id") != row["plan_id"]
                    or plan.get("plan_hash") != row["plan_hash"]
                    or plan.get("draft")
                    != {
                        "draft_id": row["draft_id"],
                        "revision": row["draft_revision"],
                    }
                ):
                    raise TaskStoreCorruption(
                        "persisted plan identity does not match its index"
                    )
                values.append(plan)
            return values
        finally:
            connection.close()

    def approval_history(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT approval_id, plan_id, plan_hash, decision,
                       document_json, document_hash
                FROM approvals
                WHERE task_id = ? ORDER BY recorded_at ASC, approval_id ASC
                """,
                (task_id,),
            ).fetchall()
            values: list[dict[str, Any]] = []
            for row in rows:
                approval = _decode_hashed_document(row, label="approval")
                if (
                    approval.get("approval_id") != row["approval_id"]
                    or approval.get("plan_id") != row["plan_id"]
                    or approval.get("plan_hash") != row["plan_hash"]
                    or approval.get("decision") != row["decision"]
                ):
                    raise TaskStoreCorruption(
                        "persisted approval identity does not match its index"
                    )
                budget = connection.execute(
                    """
                    SELECT max_tasks, tasks_used FROM approval_budgets
                    WHERE task_id = ? AND approval_id = ?
                    """,
                    (task_id, row["approval_id"]),
                ).fetchone()
                scope = approval.get("scope")
                if (
                    budget is None
                    or not isinstance(scope, dict)
                    or type(budget["max_tasks"]) is not int
                    or type(budget["tasks_used"]) is not int
                    or budget["max_tasks"] != scope.get("max_tasks")
                    or budget["tasks_used"] < 0
                    or budget["tasks_used"] > budget["max_tasks"]
                ):
                    raise TaskStoreCorruption(
                        "persisted approval budget does not match its decision"
                    )
                values.append(approval)
            return values
        finally:
            connection.close()
