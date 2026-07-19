from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scientific_runtime.task_store import (
    APPLICATION_ID,
    SCHEMA_MIGRATIONS_SQL,
    IdempotencyConflict,
    SQLiteTaskStore,
    TaskStoreConflict,
    _migration_statements,
)


PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"
NOW = "2026-07-15T12:00:00Z"
TRASHED_AT = "2026-07-15T12:01:00Z"
PURGE_REQUESTED_AT = "2026-07-15T12:02:00Z"
PURGED_AT = "2026-07-15T12:03:00Z"


def request_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


class ScientificRuntimeTaskPurgeStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "tasks.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_trashed_task(self, task_id: str) -> None:
        draft = {
            "draft_id": f"draft-{task_id}",
            "revision": 1,
            "status": "Draft",
        }
        self.store.create_task(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=draft,
            idempotency_key=f"create-{task_id}",
            request_hash=request_hash(f"create-{task_id}"),
            now=NOW,
        )
        abandonment = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "previous_status": "Draft",
            "status": "Cancelled",
            "reason": "user_discarded_draft",
            "actor": {"type": "user", "id": PRINCIPAL_ID},
            "abandoned_at": NOW,
            "extensions": {},
        }
        self.store.abandon_task(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            abandonment=abandonment,
            idempotency_key=f"abandon-{task_id}",
            request_hash=request_hash(f"abandon-{task_id}"),
            now=NOW,
        )
        result = self.store.change_task_visibility(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            operation="trash_task",
            expected_visibility_revision=0,
            idempotency_key=f"trash-{task_id}",
            request_hash=request_hash(f"trash-{task_id}"),
            now=TRASHED_AT,
        )
        self.assertEqual(result.snapshot.visibility_revision, 1)
        self.assertEqual(result.snapshot.trashed_at, TRASHED_AT)

    def reserve(self, task_id: str, key: str = "purge-key"):
        return self.store.reserve_task_purge(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            expected_visibility_revision=1,
            idempotency_key=key,
            request_hash=request_hash(f"purge-{task_id}"),
            now=PURGE_REQUESTED_AT,
        )

    def test_two_phase_alias_recovery_blocks_restore_and_hides_only_completed(self) -> None:
        task_id = "task-purge-two-phase"
        self.create_trashed_task(task_id)

        reserved = self.reserve(task_id)
        self.assertFalse(reserved.replayed)
        self.assertIsNone(reserved.outcome)
        self.assertEqual(reserved.snapshot.purge_id, reserved.purge_id)
        self.assertEqual(
            reserved.snapshot.purge_requested_at, PURGE_REQUESTED_AT
        )
        self.assertIsNone(reserved.snapshot.purged_at)
        self.assertEqual(
            [item.task_id for item in self.store.list_tasks(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                view="trash",
            ).snapshots],
            [task_id],
        )

        exact = self.reserve(task_id)
        alias = self.reserve(task_id, key="purge-key-after-restart")
        self.assertTrue(exact.replayed)
        self.assertTrue(alias.replayed)
        self.assertEqual(exact.purge_id, reserved.purge_id)
        self.assertEqual(alias.purge_id, reserved.purge_id)

        with self.assertRaisesRegex(TaskStoreConflict, "cannot be restored"):
            self.store.change_task_visibility(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                operation="restore_task",
                expected_visibility_revision=1,
                idempotency_key="restore-after-purge-request",
                request_hash=request_hash("restore-after-purge-request"),
                now=PURGED_AT,
            )

        completed = self.store.complete_task_purge(
            purge_id=reserved.purge_id,
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            local_run_state="not_created",
            now=PURGED_AT,
        )
        self.assertFalse(completed.replayed)
        self.assertEqual(
            completed.outcome,
            {
                "task_id": task_id,
                "purge_id": reserved.purge_id,
                "purge_state": "purged",
                "purged_at": PURGED_AT,
                "local_run_state": "not_created",
                "audit_retained": True,
            },
        )
        self.assertEqual(completed.snapshot.purged_at, PURGED_AT)
        self.assertEqual(
            completed.snapshot.purge_local_run_state, "not_created"
        )
        self.assertEqual(
            self.store.list_tasks(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                view="trash",
            ).snapshots,
            (),
        )
        self.assertIsNotNone(self.store.get_task(task_id))

        replayed_completion = self.store.complete_task_purge(
            purge_id=reserved.purge_id,
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            local_run_state="not_created",
            now="2026-07-15T12:04:00Z",
        )
        self.assertTrue(replayed_completion.replayed)
        self.assertEqual(replayed_completion.outcome, completed.outcome)
        self.assertTrue(self.reserve(task_id).replayed)
        with self.assertRaisesRegex(TaskStoreConflict, "already purged"):
            self.reserve(task_id, key="new-key-after-completion")

    def test_scope_cas_and_idempotency_conflicts_fail_closed(self) -> None:
        first = "task-purge-conflict-a"
        second = "task-purge-conflict-b"
        self.create_trashed_task(first)
        self.create_trashed_task(second)

        with self.assertRaisesRegex(TaskStoreConflict, "precondition"):
            self.store.reserve_task_purge(
                task_id=first,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                expected_visibility_revision=2,
                idempotency_key="stale-purge",
                request_hash=request_hash(f"purge-{first}"),
                now=PURGE_REQUESTED_AT,
            )
        with self.assertRaisesRegex(TaskStoreConflict, "scope"):
            self.store.reserve_task_purge(
                task_id=first,
                project_id="other-project",
                principal_id=PRINCIPAL_ID,
                expected_visibility_revision=1,
                idempotency_key="foreign-purge",
                request_hash=request_hash(f"purge-{first}"),
                now=PURGE_REQUESTED_AT,
            )

        self.reserve(first, key="shared-purge-key")
        with self.assertRaises(IdempotencyConflict):
            self.store.reserve_task_purge(
                task_id=second,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                expected_visibility_revision=1,
                idempotency_key="shared-purge-key",
                request_hash=request_hash(f"purge-{second}"),
                now=PURGE_REQUESTED_AT,
            )

    def test_pending_purge_blocks_replay_of_an_older_restore_mutation(self) -> None:
        task_id = "task-purge-old-restore"
        self.create_trashed_task(task_id)
        restore_hash = request_hash("old-restore")
        restored = self.store.change_task_visibility(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            operation="restore_task",
            expected_visibility_revision=1,
            idempotency_key="old-restore-key",
            request_hash=restore_hash,
            now="2026-07-15T12:01:30Z",
        )
        self.assertEqual(restored.snapshot.visibility_revision, 2)
        trashed_again = self.store.change_task_visibility(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            operation="trash_task",
            expected_visibility_revision=2,
            idempotency_key="trash-again-key",
            request_hash=request_hash("trash-again"),
            now="2026-07-15T12:01:45Z",
        )
        self.assertEqual(trashed_again.snapshot.visibility_revision, 3)
        self.store.reserve_task_purge(
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            expected_visibility_revision=3,
            idempotency_key="purge-after-old-restore",
            request_hash=request_hash("purge-after-old-restore"),
            now=PURGE_REQUESTED_AT,
        )

        with self.assertRaisesRegex(TaskStoreConflict, "cannot be restored"):
            self.store.change_task_visibility(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                operation="restore_task",
                expected_visibility_revision=1,
                idempotency_key="old-restore-key",
                request_hash=restore_hash,
                now=PURGED_AT,
            )

    def test_purge_tables_and_restore_guard_are_immutable(self) -> None:
        task_id = "task-purge-immutable"
        self.create_trashed_task(task_id)
        reserved = self.reserve(task_id)
        self.store.complete_task_purge(
            purge_id=reserved.purge_id,
            task_id=task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            local_run_state="not_created",
            now=PURGED_AT,
        )

        connection = sqlite3.connect(self.database_path)
        try:
            for statement in (
                "UPDATE task_purge_requests SET requested_at = requested_at",
                "DELETE FROM task_purge_idempotency",
                "UPDATE task_purge_outcomes SET purged_at = purged_at",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement)
                    connection.rollback()
            connection.execute("PRAGMA foreign_keys = ON")
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
        finally:
            connection.close()


class ScientificRuntimeTaskPurgeMigrationTest(unittest.TestCase):
    def test_v6_database_upgrades_in_place_to_v20(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "v6.sqlite3"
            migrations = Path(__file__).parents[1] / "scientific_runtime" / "migrations"
            connection = sqlite3.connect(database_path, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(SCHEMA_MIGRATIONS_SQL)
                for version in range(1, 7):
                    path = next(migrations.glob(f"{version:04d}_*.sql"))
                    text = path.read_text(encoding="utf-8")
                    for statement in _migration_statements(text):
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(
                            version, name, checksum, applied_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            version,
                            path.name,
                            hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            NOW,
                        ),
                    )
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            finally:
                connection.close()

            store = SQLiteTaskStore(database_path)
            self.assertEqual(store.migration_version(), 22)
            connection = sqlite3.connect(database_path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "task_purge_requests",
                        "task_purge_idempotency",
                        "task_purge_outcomes",
                        "task_cancel_requests",
                        "supervised_cancel_attempts",
                        "task_cancel_outcomes",
                        "worker_attempt_timeout_windows",
                        "supervised_timeout_attempts",
                        "task_timeout_outcomes",
                    }.issubset(tables)
                )
                connection.execute("PRAGMA foreign_keys = ON")
                self.assertIsNone(
                    connection.execute("PRAGMA foreign_key_check").fetchone()
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
