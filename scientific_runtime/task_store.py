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
from typing import Any, Mapping, Protocol, Sequence


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

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "Draft": frozenset({"Draft", "NeedsInput", "AwaitingApproval"}),
    "NeedsInput": frozenset({"NeedsInput", "Draft", "AwaitingApproval"}),
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

    def get_task(self, task_id: str) -> TaskSnapshot | None:
        ...

    def append_draft_revision(
        self,
        *,
        task_id: str,
        expected_revision: int,
        draft: Mapping[str, Any],
        now: str,
    ) -> TaskSnapshot:
        ...

    def store_plan(
        self, *, task_id: str, plan: Mapping[str, Any], now: str
    ) -> TaskSnapshot:
        ...

    def store_approval(
        self, *, task_id: str, approval: Mapping[str, Any], now: str
    ) -> TaskSnapshot:
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

    def get_task(self, task_id: str) -> TaskSnapshot | None:
        connection = self._connect()
        try:
            return self._load_snapshot(connection, task_id)
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
        runtime_status = row["status"] not in {
            "Draft",
            "NeedsInput",
            "AwaitingApproval",
        }
        if not runtime_status and event_summary["event_count"] != 0:
            raise TaskStoreCorruption("pre-runtime task unexpectedly has run events")
        if runtime_status:
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
        )

    def append_draft_revision(
        self,
        *,
        task_id: str,
        expected_revision: int,
        draft: Mapping[str, Any],
        now: str,
    ) -> TaskSnapshot:
        document_json, document_hash = encode_document(draft)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, current_draft_id, current_draft_revision
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskStoreConflict("task does not exist")
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
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("updated task cannot be read")
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
            raise TaskStoreConflict("draft revision conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def store_plan(
        self, *, task_id: str, plan: Mapping[str, Any], now: str
    ) -> TaskSnapshot:
        document_json, document_hash = encode_document(plan)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                """
                SELECT status, current_draft_id, current_draft_revision
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise TaskStoreConflict("task does not exist")
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
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("task with stored plan cannot be read")
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
            raise TaskStoreConflict("plan conflicts with durable state") from error
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def store_approval(
        self, *, task_id: str, approval: Mapping[str, Any], now: str
    ) -> TaskSnapshot:
        document_json, document_hash = encode_document(approval)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status, current_plan_id FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise TaskStoreConflict("task does not exist")
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
            snapshot = self._load_snapshot(connection, task_id)
            if snapshot is None:
                raise TaskStoreCorruption("task with stored approval cannot be read")
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
            raise TaskStoreConflict("approval conflicts with durable state") from error
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
        AwaitingApproval -> Queued must later be wrapped by the atomic registry,
        approval-budget, deterministic-gate, and submit-idempotency transaction.
        This P1.1 primitive therefore rejects every pre-runtime state.
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
