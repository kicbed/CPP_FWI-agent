from __future__ import annotations

import contextlib
import hashlib
import io
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import worker_launch_bootstrap
import worker_launch_control as worker_control
from worker_launch_control import (
    CANCELLED_WORKER_EXIT_CODE,
    CONTROL_DIRECTORY,
    WORKER_HEARTBEAT_NAME,
    WORKER_READY_NAME,
    LaunchAttemptBinding,
    ParentLaunchLease,
    WorkerCancellationRequested,
    WorkerControlError,
    WorkerHeartbeat,
    execution_fence_is_held,
    read_worker_cancel_evidence,
    read_worker_attempt_evidence,
    request_worker_cancel,
    stage_launch_attempt,
    worker_attempt_started,
)


NOW = "2026-07-16T08:00:00Z"


def _hold_fenced_worker(
    run_root: str,
    run_dir: str,
    attempt_id: str,
    attempt_fd: int,
    capacity_fd: int,
    ready: multiprocessing.Queue,
    stop: multiprocessing.Event,
) -> None:
    heartbeat: WorkerHeartbeat | None = None
    try:
        heartbeat = WorkerHeartbeat(
            run_root=run_root,
            run_dir=run_dir,
            attempt_id=attempt_id,
            attempt_fd=attempt_fd,
            capacity_fd=capacity_fd,
            interval_seconds=0.02,
        )
        heartbeat.start()
        ready.put((True, os.getpid()))
        stop.wait(10.0)
        heartbeat.stop("succeeded")
    except BaseException as error:
        ready.put((False, f"{type(error).__name__}:{error}"))
        if heartbeat is not None:
            try:
                heartbeat.stop("failed")
            except Exception:
                pass


def _hard_exit_on_cancel(
    run_root: str,
    run_dir: str,
    attempt_id: str,
    attempt_fd: int,
    capacity_fd: int,
    ready: multiprocessing.Queue,
) -> None:
    heartbeat = WorkerHeartbeat(
        run_root=run_root,
        run_dir=run_dir,
        attempt_id=attempt_id,
        attempt_fd=attempt_fd,
        capacity_fd=capacity_fd,
        interval_seconds=0.02,
        cancel_grace_seconds=0.1,
    )
    heartbeat.start()
    ready.put(os.getpid())
    # No numerical checkpoint cooperates.  The heartbeat's bounded grace path
    # must terminate this exact process while the inherited fences are held.
    time.sleep(10.0)


class WorkerLaunchControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "runs"
        self.root.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def binding(
        self,
        index: int,
        *,
        submission_digit: str | None = None,
        attempt_digit: str | None = None,
    ) -> tuple[LaunchAttemptBinding, Path]:
        submission_digit = submission_digit or str(index % 10)
        attempt_digit = attempt_digit or format(index % 16, "x")
        job_id = f"fwi-20260716T0800{index:02d}Z-{index:012x}"
        run_dir = self.root / job_id
        run_dir.mkdir(mode=0o700)
        binding = LaunchAttemptBinding(
            submission_id="submission-" + submission_digit * 64,
            attempt_id="attempt-" + attempt_digit * 32,
            attempt_number=index,
            job_id=job_id,
            request_hash="sha256:" + format((index + 8) % 16, "x") * 64,
            created_at=NOW,
        )
        stage_launch_attempt(self.root, run_dir, binding)
        return binding, run_dir

    def test_worker_inherits_cross_process_submission_and_capacity_fences(self) -> None:
        binding, run_dir = self.binding(1)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        stop = context.Event()
        process = context.Process(
            target=_hold_fenced_worker,
            args=(
                str(self.root),
                str(run_dir),
                binding.attempt_id,
                lease.attempt_fd,
                lease.capacity_fd,
                ready,
                stop,
            ),
        )
        process.start()
        try:
            lease.mark_spawned(process.pid)
            lease.close_parent()
            started, detail = ready.get(timeout=5.0)
            self.assertTrue(started, detail)
            self.assertEqual(detail, process.pid)
            self.assertTrue(
                worker_attempt_started(self.root, run_dir, binding)
            )

            first = json.loads(
                (run_dir / WORKER_HEARTBEAT_NAME).read_text(encoding="utf-8")
            )
            for _ in range(100):
                time.sleep(0.01)
                second = json.loads(
                    (run_dir / WORKER_HEARTBEAT_NAME).read_text(
                        encoding="utf-8"
                    )
                )
                if second["sequence"] > first["sequence"]:
                    break
            self.assertGreater(second["sequence"], first["sequence"])

            same_submission, same_dir = self.binding(
                2, submission_digit="1", attempt_digit="2"
            )
            with self.assertRaisesRegex(WorkerControlError, "WORKER_ATTEMPT_BUSY"):
                ParentLaunchLease.acquire(self.root, same_dir, max_active=1)

            other_submission, other_dir = self.binding(
                3, submission_digit="3", attempt_digit="3"
            )
            with self.assertRaisesRegex(
                WorkerControlError, "ADAPTER_CONCURRENCY_LIMIT"
            ):
                ParentLaunchLease.acquire(self.root, other_dir, max_active=1)
        finally:
            stop.set()
            process.join(5.0)
            if process.is_alive():
                process.kill()
                process.join(5.0)
        self.assertEqual(process.exitcode, 0)

        released = ParentLaunchLease.acquire(
            self.root, other_dir, max_active=1
        )
        released.abort()

    def test_ready_and_heartbeat_are_exact_attempt_evidence(self) -> None:
        binding, run_dir = self.binding(4)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
        )
        lease.close_parent()
        heartbeat.start()
        try:
            self.assertTrue(worker_attempt_started(self.root, run_dir, binding))
            evidence = read_worker_attempt_evidence(
                self.root, run_dir, binding
            )
            self.assertIsNotNone(evidence)
            assert evidence is not None
            self.assertTrue(evidence.ready)
            self.assertTrue(evidence.started)
            self.assertEqual(evidence.ticket_state, "spawned")
            self.assertEqual(evidence.ready_worker_pid, os.getpid())
            self.assertGreaterEqual(evidence.heartbeat_sequence, 1)
            self.assertNotIn(str(self.root), json.dumps(evidence.as_dict()))
            ready_path = run_dir / WORKER_READY_NAME
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            ready["attempt_id"] = "attempt-" + "f" * 32
            ready_path.write_text(json.dumps(ready), encoding="utf-8")
            ready_path.chmod(0o600)
            with self.assertRaisesRegex(
                WorkerControlError, "private control integrity check failed"
            ):
                worker_attempt_started(self.root, run_dir, binding)
        finally:
            heartbeat.stop("succeeded")

    def test_exact_cancel_is_acknowledged_and_cooperatively_releases_fences(
        self,
    ) -> None:
        binding, run_dir = self.binding(2)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        hard_exits: list[int] = []
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=1.0,
            hard_exit=hard_exits.append,
        )
        lease.close_parent()
        heartbeat.start()
        evidence, replayed = request_worker_cancel(
            self.root,
            binding,
            cancel_id="cancel-cooperative-1",
            reason="user_requested",
            requested_at=NOW,
        )
        self.assertFalse(replayed)
        self.assertTrue(evidence.requested)
        self.assertTrue(execution_fence_is_held(self.root, binding))

        for _ in range(200):
            evidence = read_worker_cancel_evidence(self.root, binding)
            if evidence.acknowledged:
                break
            time.sleep(0.01)
        self.assertTrue(evidence.acknowledged)
        with self.assertRaises(WorkerCancellationRequested) as raised:
            heartbeat.raise_if_cancel_requested()
        self.assertEqual(raised.exception.cancel_id, "cancel-cooperative-1")
        self.assertEqual(raised.exception.reason, "user_requested")
        self.assertTrue(execution_fence_is_held(self.root, binding))

        heartbeat.stop("stopped")
        self.assertEqual(hard_exits, [])
        self.assertFalse(execution_fence_is_held(self.root, binding))
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.heartbeat_state, "stopped")
        replay, was_replayed = request_worker_cancel(
            self.root,
            binding,
            cancel_id="cancel-cooperative-1",
            reason="user_requested",
            requested_at="2026-07-16T08:01:00Z",
        )
        self.assertTrue(was_replayed)
        self.assertEqual(replay.request_record_hash, evidence.request_record_hash)
        with self.assertRaisesRegex(WorkerControlError, "WORKER_CANCEL_CONFLICT"):
            request_worker_cancel(
                self.root,
                binding,
                cancel_id="cancel-conflict-2",
                reason="user_requested",
                requested_at=NOW,
            )
        self.assertFalse(
            any("cancel" in path.name for path in run_dir.iterdir())
        )

    def test_uncooperative_worker_self_exits_after_bounded_cancel_grace(self) -> None:
        binding, run_dir = self.binding(3)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        process = context.Process(
            target=_hard_exit_on_cancel,
            args=(
                str(self.root),
                str(run_dir),
                binding.attempt_id,
                lease.attempt_fd,
                lease.capacity_fd,
                ready,
            ),
        )
        process.start()
        try:
            lease.mark_spawned(process.pid)
            lease.close_parent()
            self.assertEqual(ready.get(timeout=5.0), process.pid)
            request_worker_cancel(
                self.root,
                binding,
                cancel_id="cancel-hard-exit-1",
                reason="user_requested",
                requested_at=NOW,
            )
            process.join(5.0)
            self.assertFalse(process.is_alive())
        finally:
            if process.is_alive():
                process.kill()
                process.join(5.0)
        self.assertEqual(process.exitcode, CANCELLED_WORKER_EXIT_CODE)
        evidence = read_worker_cancel_evidence(self.root, binding)
        self.assertTrue(evidence.acknowledged)
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.heartbeat_state, "stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_hard_exit_is_called_even_if_stopped_heartbeat_write_fails(self) -> None:
        binding, run_dir = self.binding(6)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        hard_exits: list[int] = []
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=0.1,
            hard_exit=hard_exits.append,
        )
        lease.close_parent()
        heartbeat.start()
        original_write = heartbeat._write_heartbeat

        def fail_stopped(state: str) -> None:
            if state == "stopped":
                raise WorkerControlError(
                    "WORKER_HEARTBEAT_FAILED: synthetic stopped write failure"
                )
            original_write(state)

        with patch.object(
            heartbeat, "_write_heartbeat", side_effect=fail_stopped
        ):
            request_worker_cancel(
                self.root,
                binding,
                cancel_id="cancel-hard-write-failure-1",
                reason="user_requested",
                requested_at=NOW,
            )
            for _ in range(200):
                if hard_exits:
                    break
                time.sleep(0.01)
            self.assertEqual(hard_exits, [CANCELLED_WORKER_EXIT_CODE])
            # The injected callback returned instead of terminating this test
            # process, so stop() reports the heartbeat failure but still closes
            # both inherited descriptors in its finally block.
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_HEARTBEAT_FAILED"
            ):
                heartbeat.stop("stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_append_only_request_name_never_exposes_partial_json(self) -> None:
        binding, run_dir = self.binding(5)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=300.0,
            hard_exit=lambda _code: None,
        )
        lease.close_parent()
        heartbeat.start()
        first_write = threading.Event()
        release_write = threading.Event()
        real_write = worker_control.os.write
        result: list[object] = []

        def delayed_write(descriptor: int, data: bytes) -> int:
            if not first_write.is_set():
                size = max(1, len(data) // 2)
                written = real_write(descriptor, data[:size])
                first_write.set()
                release_write.wait(2.0)
                return written
            return real_write(descriptor, data)

        def publish() -> None:
            try:
                result.append(
                    request_worker_cancel(
                        self.root,
                        binding,
                        cancel_id="cancel-atomic-publication-1",
                        reason="user_requested",
                        requested_at=NOW,
                    )
                )
            except BaseException as error:  # pragma: no cover - asserted below
                result.append(error)

        thread = threading.Thread(target=publish)
        try:
            with patch.object(
                worker_control.os, "write", side_effect=delayed_write
            ):
                thread.start()
                self.assertTrue(first_write.wait(2.0))
                while_the_writer_is_blocked = read_worker_cancel_evidence(
                    self.root, binding
                )
                self.assertFalse(while_the_writer_is_blocked.requested)
                release_write.set()
                thread.join(2.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(result), 1)
            self.assertIsInstance(result[0], tuple)
            for _ in range(200):
                evidence = read_worker_cancel_evidence(self.root, binding)
                if evidence.acknowledged:
                    break
                time.sleep(0.01)
            self.assertTrue(evidence.acknowledged)
        finally:
            release_write.set()
            if thread.is_alive():
                thread.join(2.0)
            heartbeat.stop("stopped")

    def test_staged_attempt_evidence_is_not_a_liveness_decision(self) -> None:
        binding, run_dir = self.binding(8)
        evidence = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.ticket_state, "staged")
        self.assertFalse(evidence.ready)
        self.assertFalse(evidence.started)
        self.assertFalse(worker_attempt_started(self.root, run_dir, binding))

    def test_ready_without_heartbeat_fails_closed(self) -> None:
        binding, run_dir = self.binding(14)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=10.0,
        )
        lease.close_parent()
        heartbeat.start()
        try:
            (run_dir / WORKER_HEARTBEAT_NAME).unlink()
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_HEARTBEAT_INVALID"
            ):
                read_worker_attempt_evidence(self.root, run_dir, binding)
        finally:
            heartbeat.stop("failed")

    def test_failed_ticket_rejects_partial_capacity_identity(self) -> None:
        binding, run_dir = self.binding(15)
        ticket_path = run_dir / ".worker-launch.json"
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        ticket.update(
            {
                "state": "failed",
                "capacity_slot": 0,
                "capacity_generation": None,
                "worker_pid": None,
            }
        )
        ticket.pop("record_hash")
        payload = json.dumps(
            ticket,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        ticket["record_hash"] = "sha256:" + hashlib.sha256(payload).hexdigest()
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        ticket_path.chmod(0o600)

        with self.assertRaisesRegex(
            WorkerControlError, "launch ticket projection is invalid"
        ):
            read_worker_attempt_evidence(self.root, run_dir, binding)

    def test_capacity_policy_and_permanent_lock_inode_fail_closed(self) -> None:
        first, first_dir = self.binding(5)
        lease = ParentLaunchLease.acquire(self.root, first_dir, max_active=1)
        lease.abort()

        mismatch, mismatch_dir = self.binding(6)
        with self.assertRaisesRegex(
            WorkerControlError, "WORKER_CAPACITY_POLICY_MISMATCH"
        ):
            ParentLaunchLease.acquire(self.root, mismatch_dir, max_active=2)

        slot = (
            self.root
            / CONTROL_DIRECTORY
            / "worker-capacity"
            / "slots"
            / "slot-000.lock"
        )
        slot.rename(slot.with_name("slot-000.replaced.lock"))
        slot.touch(mode=0o600)
        replacement, replacement_dir = self.binding(7)
        with self.assertRaisesRegex(
            WorkerControlError, "permanent lock inode changed"
        ):
            ParentLaunchLease.acquire(self.root, replacement_dir, max_active=1)

    def test_owned_nonwritable_0755_run_root_matches_deployment_policy(self) -> None:
        self.root.chmod(0o755)
        binding, run_dir = self.binding(13)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        self.assertEqual(lease.binding, binding)
        lease.abort()

    def test_lightweight_bootstrap_crosses_ready_before_loading_worker(self) -> None:
        binding, run_dir = self.binding(8)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        attempt_fd = os.dup(lease.attempt_fd)
        capacity_fd = os.dup(lease.capacity_fd)
        lease.close_parent()
        observed_ready: list[bool] = []

        def synthetic_run_worker(
            command,
            config,
            requested_run_dir,
            *,
            managed_launch=False,
            cancel_check=None,
        ):
            observed_ready.append(
                (run_dir / WORKER_READY_NAME).is_file()
                and managed_launch
                and callable(cancel_check)
            )
            return {"status": "synthetic", "command": command}

        output = io.StringIO()
        with patch.object(
            worker_launch_bootstrap,
            "_load_run_worker",
            return_value=synthetic_run_worker,
        ), contextlib.redirect_stdout(output):
            result = worker_launch_bootstrap.main(
                [
                    "--command",
                    "invert",
                    "--config",
                    str(run_dir / "config.original.json"),
                    "--run-dir",
                    str(run_dir),
                    "--run-root",
                    str(self.root),
                    "--launch-attempt-id",
                    binding.attempt_id,
                    "--launch-attempt-fd",
                    str(attempt_fd),
                    "--capacity-lease-fd",
                    str(capacity_fd),
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(observed_ready, [True])
        printed = json.loads(output.getvalue())
        self.assertEqual(printed["status"], "synthetic")
        final_heartbeat = json.loads(
            (run_dir / WORKER_HEARTBEAT_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(final_heartbeat["state"], "succeeded")
        self.assertEqual(
            sorted(path.name for path in run_dir.iterdir()),
            [
                ".worker-heartbeat.json",
                ".worker-launch.json",
                ".worker-ready.json",
            ],
        )

    def test_staged_attempt_cannot_receive_cancel_before_worker_capability(
        self,
    ) -> None:
        binding, run_dir = self.binding(4)
        with self.assertRaisesRegex(
            WorkerControlError, "WORKER_CANCEL_UNSUPPORTED"
        ):
            read_worker_cancel_evidence(self.root, binding)
        with self.assertRaisesRegex(
            WorkerControlError, "WORKER_CANCEL_UNSUPPORTED"
        ):
            request_worker_cancel(
                self.root,
                binding,
                cancel_id="cancel-before-capability-1",
                reason="user_requested",
                requested_at=NOW,
            )
        self.assertFalse(
            (self.root / CONTROL_DIRECTORY / "worker-cancel").exists()
        )

        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
        )
        lease.close_parent()
        heartbeat.start()
        try:
            evidence = read_worker_cancel_evidence(self.root, binding)
            self.assertFalse(evidence.requested)
        finally:
            heartbeat.stop("succeeded")

    def test_bootstrap_releases_fences_only_after_worker_unwind_finishes(
        self,
    ) -> None:
        binding, run_dir = self.binding(7)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        attempt_fd = os.dup(lease.attempt_fd)
        capacity_fd = os.dup(lease.capacity_fd)
        lease.close_parent()
        entered = threading.Event()
        unwinding = threading.Event()
        finish_unwind = threading.Event()
        results: list[int] = []

        def synthetic_run_worker(
            _command,
            _config,
            _requested_run_dir,
            *,
            managed_launch=False,
            cancel_check=None,
        ):
            self.assertTrue(managed_launch)
            self.assertTrue(callable(cancel_check))
            entered.set()
            try:
                while True:
                    cancel_check()
                    time.sleep(0.005)
            finally:
                unwinding.set()
                finish_unwind.wait(2.0)

        arguments = [
            "--command",
            "invert",
            "--config",
            str(run_dir / "config.original.json"),
            "--run-dir",
            str(run_dir),
            "--run-root",
            str(self.root),
            "--launch-attempt-id",
            binding.attempt_id,
            "--launch-attempt-fd",
            str(attempt_fd),
            "--capacity-lease-fd",
            str(capacity_fd),
        ]
        with patch.object(
            worker_launch_bootstrap,
            "_load_run_worker",
            return_value=synthetic_run_worker,
        ):
            thread = threading.Thread(
                target=lambda: results.append(
                    worker_launch_bootstrap.main(arguments)
                )
            )
            thread.start()
            try:
                self.assertTrue(entered.wait(2.0))
                request_worker_cancel(
                    self.root,
                    binding,
                    cancel_id="cancel-bootstrap-unwind-1",
                    reason="user_requested",
                    requested_at=NOW,
                )
                self.assertTrue(unwinding.wait(2.0))
                self.assertTrue(execution_fence_is_held(self.root, binding))
                finish_unwind.set()
                thread.join(2.0)
            finally:
                finish_unwind.set()
                if thread.is_alive():
                    thread.join(2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [CANCELLED_WORKER_EXIT_CODE])
        self.assertFalse(execution_fence_is_held(self.root, binding))
        evidence = read_worker_cancel_evidence(self.root, binding)
        self.assertTrue(evidence.acknowledged)
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.heartbeat_state, "stopped")

    def test_real_exec_inherits_fds_and_releases_capacity_after_failure(self) -> None:
        binding, run_dir = self.binding(9)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        environment = os.environ.copy()
        environment["FWI_RUN_ROOT"] = str(self.root)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "worker_launch_bootstrap",
                "--command",
                "invert",
                "--config",
                str(run_dir / "missing-config.json"),
                "--run-dir",
                str(run_dir),
                "--run-root",
                str(self.root),
                *lease.child_arguments,
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=lease.pass_fds,
        )
        lease.mark_spawned(process.pid)
        lease.close_parent()
        self.assertEqual(process.wait(timeout=30.0), 1)
        self.assertTrue(worker_attempt_started(self.root, run_dir, binding))
        heartbeat = json.loads(
            (run_dir / WORKER_HEARTBEAT_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(heartbeat["worker_pid"], process.pid)
        self.assertEqual(heartbeat["state"], "failed")
        self.assertFalse(execution_fence_is_held(self.root, binding))

        replacement, replacement_dir = self.binding(10)
        replacement_lease = ParentLaunchLease.acquire(
            self.root, replacement_dir, max_active=1
        )
        replacement_lease.abort()

    def test_missing_run_directory_is_never_recreated_by_evidence_reads(self) -> None:
        binding, run_dir = self.binding(11)
        shutil.rmtree(run_dir)
        with self.assertRaisesRegex(
            WorkerControlError, "run directory is unavailable"
        ):
            worker_attempt_started(self.root, run_dir, binding)
        self.assertFalse(run_dir.exists())

    def test_exec_child_self_promotes_if_controller_misses_spawn_mark(self) -> None:
        binding, run_dir = self.binding(12)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        environment = os.environ.copy()
        environment["FWI_RUN_ROOT"] = str(self.root)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "worker_launch_bootstrap",
                "--command",
                "invert",
                "--config",
                str(run_dir / "missing-config.json"),
                "--run-dir",
                str(run_dir),
                "--run-root",
                str(self.root),
                *lease.child_arguments,
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=lease.pass_fds,
        )
        # Simulate the controller disappearing after Popen returned but before
        # it could persist mark_spawned().  The exec child owns both leases.
        lease.close_parent()
        self.assertEqual(process.wait(timeout=30.0), 1)
        ticket = json.loads(
            (run_dir / ".worker-launch.json").read_text(encoding="utf-8")
        )
        self.assertEqual(ticket["state"], "spawned")
        self.assertEqual(ticket["worker_pid"], process.pid)
        self.assertTrue(worker_attempt_started(self.root, run_dir, binding))
        self.assertFalse(execution_fence_is_held(self.root, binding))


if __name__ == "__main__":
    unittest.main()
