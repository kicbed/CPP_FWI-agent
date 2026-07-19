from __future__ import annotations

import contextlib
import fcntl
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import worker_launch_bootstrap
import worker_launch_control as worker_control
from worker_launch_control import (
    CANCELLED_WORKER_EXIT_CODE,
    CONTROL_DIRECTORY,
    CUDA_LOGICAL_DEVICE_0_SLOT,
    WALL_TIME_EXCEEDED_WORKER_EXIT_CODE,
    WORKER_EXIT_NAME,
    WORKER_HEARTBEAT_NAME,
    WORKER_READY_NAME,
    WORKER_CONFIG_NAME,
    LaunchAttemptBinding,
    ParentLaunchLease,
    WorkerCancellationRequested,
    WorkerCheckpointEvidence,
    WorkerControlError,
    WorkerExitEvidence,
    WorkerHeartbeat,
    WorkerWallTimeExceeded,
    execution_fence_is_held,
    mark_launch_failed,
    read_worker_exit_evidence,
    read_pre_running_attempt_evidence,
    read_worker_cancel_evidence,
    read_worker_checkpoint_evidence,
    read_worker_stop_evidence,
    read_worker_attempt_evidence,
    request_worker_cancel,
    request_worker_checkpoint_resume,
    request_worker_stop,
    record_worker_exit,
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


def _hold_fenced_cuda_worker(
    run_root: str,
    run_dir: str,
    attempt_id: str,
    attempt_fd: int,
    capacity_fd: int,
    gpu_capacity_fd: int,
    gpu_capacity_slot: int,
    gpu_capacity_generation: int,
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
            resource_device="cuda",
            gpu_capacity_fd=gpu_capacity_fd,
            gpu_capacity_slot=gpu_capacity_slot,
            gpu_capacity_generation=gpu_capacity_generation,
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


def _hard_exit_on_timeout(
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
        wall_time_seconds=1,
    )
    heartbeat.start()
    assert heartbeat._started_monotonic is not None
    heartbeat._started_monotonic -= 1.0
    ready.put(os.getpid())
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
        device: str = "cpu",
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
        config_path = run_dir / WORKER_CONFIG_NAME
        config_path.write_text(
            json.dumps({"job_id": job_id, "device": device}),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        return binding, run_dir

    def abrupt_running_attempt(
        self, index: int
    ) -> tuple[LaunchAttemptBinding, Path]:
        """Leave exact ready/running sidecars while releasing both leases."""

        binding, run_dir = self.binding(index)
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
        heartbeat._stop.set()
        assert heartbeat._thread is not None
        heartbeat._thread.join(2.0)
        self.assertFalse(heartbeat._thread.is_alive())
        self.assertIsNone(heartbeat._failure)
        # Model an abrupt process exit: kernel leases close without a terminal
        # heartbeat write, leaving the last exact state at ``running``.
        heartbeat._close_descriptors()
        self.assertFalse(execution_fence_is_held(self.root, binding))
        return binding, run_dir

    def checkpoint_worker(self, index: int):
        import torch

        from fwi_worker.checkpoint import save_checkpoint_payload
        from fwi_worker.config import resolve_config
        from fwi_worker.inversion import InversionCheckpointState

        binding, run_dir = self.binding(index)
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
        config = resolve_config(
            {"preset": "fwi_smoke", "device": "cpu", "iterations": 2}
        )
        velocity = torch.nn.Parameter(
            torch.full((2, 3), 2000.0, dtype=torch.float32)
        )
        optimizer = torch.optim.Adam([velocity], lr=config.learning_rate)
        optimizer.zero_grad(set_to_none=True)
        velocity.square().sum().backward()
        optimizer.step()
        checkpoint = InversionCheckpointState(
            completed_updates=1,
            next_state_index=1,
            velocity=velocity,
            optimizer=optimizer,
            losses=(1.0,),
            gradient_clip_values=(0.5,),
        )
        manifest = save_checkpoint_payload(
            run_dir=run_dir,
            binding=binding,
            config=config,
            checkpoint=checkpoint,
            clock=lambda: "2026-07-16T08:00:01Z",
        )
        return binding, run_dir, heartbeat, manifest

    @staticmethod
    def rewrite_checkpoint_manifest(run_dir: Path, evidence, mutate):
        manifest_path = run_dir / evidence.manifest_relative_path
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(document)
        data = worker_control._stable_json_bytes(document) + b"\n"
        manifest_path.write_bytes(data)
        return {
            **evidence.as_dict(),
            "manifest_size_bytes": len(data),
            "manifest_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        }

    @staticmethod
    def checkpoint_resume_request(
        binding: LaunchAttemptBinding,
        waiting: WorkerCheckpointEvidence,
        *,
        resume_digit: str,
    ) -> dict[str, object]:
        return worker_control._record_with_hash(
            {
                "schema_version": "1.0.0",
                "resume_id": "resume-" + resume_digit * 32,
                "submission_id": binding.submission_id,
                "attempt_id": binding.attempt_id,
                "attempt_number": binding.attempt_number,
                "checkpoint_id": waiting.checkpoint_id,
                "checkpoint_manifest_hash": waiting.manifest_hash,
                "checkpoint_receipt_record_hash": waiting.checkpoint_record_hash,
                "checkpoint_proof_hash": "sha256:" + "e" * 64,
                "authorized_at": "2026-07-16T08:00:01Z",
            }
        )

    def worker_exit_statuses(
        self, binding: LaunchAttemptBinding
    ) -> tuple[dict[str, object], dict[str, object]]:
        pre: dict[str, object] = {
            "job_id": binding.job_id,
            "status": "running",
            "stage": "invert",
            "iteration": 1,
            "total_iterations": 2,
            "message": "FWI inversion running",
            "updated_at": NOW,
        }
        post = {
            **pre,
            "status": "failed",
            "stage": "worker_exit",
            "message": "FWI worker exited with code -9",
            "updated_at": "2026-07-16T08:00:01Z",
        }
        return pre, post

    @staticmethod
    def write_status(run_dir: Path, value: dict[str, object]) -> None:
        path = run_dir / "status.json"
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def test_worker_exit_receipt_is_append_only_and_revalidated(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(40)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)

        evidence = record_worker_exit(
            self.root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre,
            post_status=post,
            observed_at=post["updated_at"],
        )
        self.assertIsInstance(evidence, WorkerExitEvidence)
        self.assertEqual(evidence.return_code, -9)
        self.assertEqual(evidence.heartbeat_state, "running")
        self.assertEqual(
            json.loads((run_dir / "status.json").read_text(encoding="utf-8")),
            post,
        )
        self.assertEqual(
            read_worker_exit_evidence(self.root, run_dir, binding), evidence
        )
        receipt_path = run_dir / WORKER_EXIT_NAME
        inode = receipt_path.stat().st_ino

        replay = record_worker_exit(
            self.root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre,
            post_status=post,
        )
        self.assertEqual(replay, evidence)
        self.assertEqual(receipt_path.stat().st_ino, inode)

        # Readers continue to re-prove the immutable attempt sidecars and the
        # exact finalized status document.
        self.assertEqual(
            read_worker_exit_evidence(self.root, run_dir, binding), evidence
        )
        self.assertEqual(
            record_worker_exit(
                self.root,
                run_dir,
                binding,
                return_code=-9,
                pre_status=pre,
                post_status=post,
            ),
            evidence,
        )
        conflicting_post = {
            **post,
            "message": "FWI worker exited with code -6",
        }
        with self.assertRaisesRegex(WorkerControlError, "WORKER_EXIT_CONFLICT"):
            record_worker_exit(
                self.root,
                run_dir,
                binding,
                return_code=-6,
                pre_status=pre,
                post_status=conflicting_post,
            )

    def test_worker_exit_reader_recovers_receipt_first_status_gap(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(56)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        original_write = worker_control._atomic_write_private_json

        def interrupt_after_receipt(
            path: Path, value: dict[str, object]
        ) -> None:
            if path.name == "status.json" and (run_dir / WORKER_EXIT_NAME).exists():
                raise WorkerControlError(
                    "WORKER_CONTROL_UNAVAILABLE: injected reaper crash"
                )
            original_write(path, value)

        with patch.object(
            worker_control,
            "_atomic_write_private_json",
            side_effect=interrupt_after_receipt,
        ), self.assertRaisesRegex(WorkerControlError, "injected reaper crash"):
            record_worker_exit(
                self.root,
                run_dir,
                binding,
                return_code=-9,
                pre_status=pre,
                post_status=post,
            )
        self.assertTrue((run_dir / WORKER_EXIT_NAME).is_file())
        self.assertEqual(
            json.loads((run_dir / "status.json").read_text(encoding="utf-8")),
            pre,
        )

        # A later Supervisor process needs only durable files.  The reader
        # reconstructs one candidate and writes it only after its hash exactly
        # matches the receipt's bound post hash.
        evidence = read_worker_exit_evidence(self.root, run_dir, binding)
        self.assertEqual(evidence.return_code, -9)
        self.assertEqual(
            json.loads((run_dir / "status.json").read_text(encoding="utf-8")),
            post,
        )

    def test_worker_exit_and_stop_request_share_terminal_arbitration(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(57)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        writer_inside = threading.Event()
        release_writer = threading.Event()
        writer_result: list[object] = []
        stop_result: list[object] = []
        original_create = worker_control._create_private_json

        def pause_receipt_create(path: Path, value: dict[str, object]) -> None:
            if path.name == WORKER_EXIT_NAME:
                writer_inside.set()
                release_writer.wait(2.0)
            original_create(path, value)

        def write_exit() -> None:
            try:
                writer_result.append(
                    record_worker_exit(
                        self.root,
                        run_dir,
                        binding,
                        return_code=-9,
                        pre_status=pre,
                        post_status=post,
                    )
                )
            except BaseException as error:
                writer_result.append(error)

        def request_stop() -> None:
            try:
                stop_result.append(
                    request_worker_stop(
                        self.root,
                        binding,
                        request_id="racing-stop-57",
                        reason="user_requested",
                        requested_at=NOW,
                    )
                )
            except BaseException as error:
                stop_result.append(error)

        with patch.object(
            worker_control,
            "_create_private_json",
            side_effect=pause_receipt_create,
        ):
            writer = threading.Thread(target=write_exit)
            requester = threading.Thread(target=request_stop)
            try:
                writer.start()
                self.assertTrue(writer_inside.wait(2.0))
                requester.start()
                time.sleep(0.05)
                self.assertTrue(requester.is_alive())
            finally:
                release_writer.set()
                writer.join(2.0)
                if requester.ident is not None:
                    requester.join(2.0)

        self.assertFalse(writer.is_alive())
        self.assertFalse(requester.is_alive())
        self.assertEqual(len(writer_result), 1)
        self.assertIsInstance(writer_result[0], WorkerExitEvidence)
        self.assertEqual(len(stop_result), 1)
        self.assertIsInstance(stop_result[0], WorkerControlError)
        assert isinstance(stop_result[0], WorkerControlError)
        self.assertEqual(stop_result[0].code, "WORKER_STOP_CONFLICT")
        self.assertFalse(
            read_worker_stop_evidence(self.root, binding).requested
        )

    def test_worker_exit_rejects_reserved_and_success_return_codes(self) -> None:
        for index, return_code in enumerate(
            (
                0,
                CANCELLED_WORKER_EXIT_CODE,
                WALL_TIME_EXCEEDED_WORKER_EXIT_CODE,
            ),
            start=41,
        ):
            with self.subTest(return_code=return_code):
                binding, run_dir = self.abrupt_running_attempt(index)
                pre, post = self.worker_exit_statuses(binding)
                self.write_status(run_dir, pre)
                with self.assertRaisesRegex(
                    WorkerControlError, "WORKER_EXIT_INVALID"
                ):
                    record_worker_exit(
                        self.root,
                        run_dir,
                        binding,
                        return_code=return_code,
                        pre_status=pre,
                        post_status=post,
                    )
                self.assertFalse((run_dir / WORKER_EXIT_NAME).exists())

    def test_worker_exit_rejects_terminal_heartbeat_states(self) -> None:
        for index, state in enumerate(
            ("succeeded", "failed", "stopped"), start=44
        ):
            with self.subTest(state=state):
                binding, run_dir = self.binding(index)
                lease = ParentLaunchLease.acquire(
                    self.root, run_dir, max_active=1
                )
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
                heartbeat.stop(state)
                pre, post = self.worker_exit_statuses(binding)
                self.write_status(run_dir, pre)
                with self.assertRaisesRegex(
                    WorkerControlError, "WORKER_EXIT_UNSAFE"
                ):
                    record_worker_exit(
                        self.root,
                        run_dir,
                        binding,
                        return_code=-9,
                        pre_status=pre,
                        post_status=post,
                    )

    def test_worker_exit_never_relabels_terminal_status(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(58)
        base, _ = self.worker_exit_statuses(binding)
        for terminal_status, stage in (
            ("succeeded", "complete"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
        ):
            with self.subTest(status=terminal_status):
                terminal = {
                    **base,
                    "status": terminal_status,
                    "stage": stage,
                }
                post = {
                    **terminal,
                    "status": "failed",
                    "stage": "worker_exit",
                    "message": "FWI worker exited with code -9",
                    "updated_at": "2026-07-16T08:00:01Z",
                }
                self.write_status(run_dir, terminal)
                with self.assertRaisesRegex(
                    WorkerControlError, "terminal Worker status"
                ):
                    record_worker_exit(
                        self.root,
                        run_dir,
                        binding,
                        return_code=-9,
                        pre_status=terminal,
                        post_status=post,
                    )
        self.assertFalse((run_dir / WORKER_EXIT_NAME).exists())

    def test_worker_exit_rejects_active_execution_fence(self) -> None:
        binding, run_dir = self.binding(47)
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
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        try:
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_ATTEMPT_BUSY"
            ):
                record_worker_exit(
                    self.root,
                    run_dir,
                    binding,
                    return_code=-9,
                    pre_status=pre,
                    post_status=post,
                )
            self.assertFalse((run_dir / WORKER_EXIT_NAME).exists())
        finally:
            heartbeat.stop("failed")

    def test_worker_exit_rejects_v2_user_and_timeout_requests(self) -> None:
        for index, reason in ((48, "user_requested"), (49, "wall_time_exceeded")):
            with self.subTest(reason=reason):
                binding, run_dir = self.abrupt_running_attempt(index)
                attempt = read_worker_attempt_evidence(
                    self.root, run_dir, binding
                )
                assert attempt is not None
                if reason == "user_requested":
                    request_worker_stop(
                        self.root,
                        binding,
                        request_id=f"stop-{index}",
                        reason=reason,
                        requested_at=NOW,
                    )
                else:
                    assert attempt.ready_started_at is not None
                    started = datetime.fromisoformat(
                        attempt.ready_started_at[:-1] + "+00:00"
                    )
                    deadline = started + timedelta(seconds=86_400)
                    deadline_at = deadline.astimezone(timezone.utc).isoformat(
                        timespec="microseconds"
                    ).replace("+00:00", "Z")
                    request_worker_stop(
                        self.root,
                        binding,
                        request_id=f"stop-{index}",
                        reason=reason,
                        requested_at=deadline_at,
                        wall_time_seconds=86_400,
                        started_at=attempt.ready_started_at,
                        deadline_at=deadline_at,
                        ready_record_hash=attempt.ready_record_hash,
                    )
                pre, post = self.worker_exit_statuses(binding)
                self.write_status(run_dir, pre)
                with self.assertRaisesRegex(
                    WorkerControlError, "WORKER_EXIT_UNSAFE.*stop request"
                ):
                    record_worker_exit(
                        self.root,
                        run_dir,
                        binding,
                        return_code=-9,
                        pre_status=pre,
                        post_status=post,
                    )

    def test_worker_exit_rejects_legacy_cancel_request(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(50)
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        assert attempt is not None
        assert attempt.ticket_worker_pid is not None
        assert attempt.capacity_slot is not None
        assert attempt.capacity_generation is not None
        worker_control.ensure_worker_cancel_capability(
            self.root,
            binding,
            worker_pid=attempt.ticket_worker_pid,
            capacity_slot=attempt.capacity_slot,
            capacity_generation=attempt.capacity_generation,
        )
        worker_control._request_legacy_worker_cancel(
            self.root,
            binding,
            cancel_id="legacy-stop-50",
            reason="user_requested",
            requested_at=NOW,
        )
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        with self.assertRaisesRegex(
            WorkerControlError, "WORKER_EXIT_UNSAFE.*legacy"
        ):
            record_worker_exit(
                self.root,
                run_dir,
                binding,
                return_code=-9,
                pre_status=pre,
                post_status=post,
            )

    def test_worker_exit_reader_rejects_a_late_stop_request(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(55)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        record_worker_exit(
            self.root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre,
            post_status=post,
        )
        with self.assertRaisesRegex(
            WorkerControlError, "WORKER_STOP_CONFLICT.*terminal arbitration"
        ):
            request_worker_stop(
                self.root,
                binding,
                request_id="late-stop-55",
                reason="user_requested",
                requested_at=NOW,
            )
        self.assertFalse(
            read_worker_stop_evidence(self.root, binding).requested
        )
        self.assertEqual(
            read_worker_exit_evidence(self.root, run_dir, binding).return_code,
            -9,
        )

    def test_worker_exit_reader_rejects_tamper_status_drift_and_busy_fence(
        self,
    ) -> None:
        binding, run_dir = self.abrupt_running_attempt(51)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        record_worker_exit(
            self.root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre,
            post_status=post,
        )

        lock_path = (
            self.root
            / CONTROL_DIRECTORY
            / "worker-capacity"
            / "attempts"
            / f"{binding.submission_id}.lock"
        )
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_ATTEMPT_BUSY"
            ):
                read_worker_exit_evidence(self.root, run_dir, binding)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        drifted = {**pre, "message": "unbound status mutation"}
        self.write_status(run_dir, drifted)
        with self.assertRaisesRegex(
            WorkerControlError, "outside the bound transition"
        ):
            read_worker_exit_evidence(self.root, run_dir, binding)

    def test_worker_exit_reader_rejects_later_heartbeat_sidecar(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(59)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        record_worker_exit(
            self.root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre,
            post_status=post,
        )
        heartbeat_path = run_dir / WORKER_HEARTBEAT_NAME
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        heartbeat["sequence"] += 1
        heartbeat["updated_at"] = "2026-07-16T08:00:02Z"
        heartbeat.pop("record_hash")
        heartbeat = worker_control._record_with_hash(heartbeat)
        worker_control._atomic_write_private_json(heartbeat_path, heartbeat)
        with self.assertRaisesRegex(
            WorkerControlError, "sidecar evidence changed"
        ):
            read_worker_exit_evidence(self.root, run_dir, binding)

        self.write_status(run_dir, pre)
        receipt_path = run_dir / WORKER_EXIT_NAME
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["return_code"] = -6
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        receipt_path.chmod(0o600)
        with self.assertRaisesRegex(
            WorkerControlError, "integrity check failed"
        ):
            read_worker_exit_evidence(self.root, run_dir, binding)

    def test_worker_exit_requires_complete_exact_evidence_and_status(self) -> None:
        binding, run_dir = self.abrupt_running_attempt(52)
        pre, post = self.worker_exit_statuses(binding)
        self.write_status(run_dir, pre)
        with self.assertRaisesRegex(WorkerControlError, "WORKER_EXIT_MISSING"):
            read_worker_exit_evidence(self.root, run_dir, binding)
        (run_dir / WORKER_READY_NAME).unlink()
        with self.assertRaisesRegex(WorkerControlError, "WORKER_EXIT_UNSAFE"):
            record_worker_exit(
                self.root,
                run_dir,
                binding,
                return_code=-9,
                pre_status=pre,
                post_status=post,
            )

        other_binding, other_dir = self.abrupt_running_attempt(53)
        other_pre, other_post = self.worker_exit_statuses(other_binding)
        # Missing status.json is never invented by the receipt writer.
        with self.assertRaisesRegex(
            WorkerControlError, "status evidence is missing"
        ):
            record_worker_exit(
                self.root,
                other_dir,
                other_binding,
                return_code=-9,
                pre_status=other_pre,
                post_status=other_post,
            )
        self.assertFalse((other_dir / WORKER_EXIT_NAME).exists())

        capability_binding, capability_dir = self.abrupt_running_attempt(54)
        capability_pre, capability_post = self.worker_exit_statuses(
            capability_binding
        )
        self.write_status(capability_dir, capability_pre)
        (
            self.root
            / CONTROL_DIRECTORY
            / "worker-stop"
            / f"{capability_binding.attempt_id}.capability.json"
        ).unlink()
        with self.assertRaisesRegex(
            WorkerControlError, "has no v2 stop capability"
        ):
            record_worker_exit(
                self.root,
                capability_dir,
                capability_binding,
                return_code=-9,
                pre_status=capability_pre,
                post_status=capability_post,
            )
        self.assertFalse((capability_dir / WORKER_EXIT_NAME).exists())

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

    def test_cpu_capacity_has_an_explicit_two_worker_upper_bound(self) -> None:
        first, first_dir = self.binding(14)
        second, second_dir = self.binding(15)
        third, third_dir = self.binding(16)
        first_lease = ParentLaunchLease.acquire(
            self.root, first_dir, max_active=2
        )
        second_lease = ParentLaunchLease.acquire(
            self.root, second_dir, max_active=2
        )
        try:
            self.assertEqual(
                {first_lease.capacity_slot, second_lease.capacity_slot},
                {0, 1},
            )
            self.assertEqual(len(first_lease.pass_fds), 2)
            self.assertEqual(len(second_lease.pass_fds), 2)
            with self.assertRaisesRegex(
                WorkerControlError, "ADAPTER_CONCURRENCY_LIMIT"
            ):
                ParentLaunchLease.acquire(
                    self.root, third_dir, max_active=2
                )
        finally:
            first_lease.abort()
            second_lease.abort()
        released = ParentLaunchLease.acquire(
            self.root, third_dir, max_active=2
        )
        released.abort()
        self.assertEqual(first.attempt_number, 14)
        self.assertEqual(second.attempt_number, 15)
        self.assertEqual(third.attempt_number, 16)

    def test_cuda_device_zero_stays_locked_after_parent_close(self) -> None:
        binding, run_dir = self.binding(17, device="cuda")
        waiting, waiting_dir = self.binding(18, device="cuda")
        cpu_binding, cpu_dir = self.binding(19)
        lease = ParentLaunchLease.acquire(
            self.root,
            run_dir,
            max_active=2,
            resource_device="cuda",
        )
        self.assertEqual(lease.gpu_capacity_slot, CUDA_LOGICAL_DEVICE_0_SLOT)
        self.assertEqual(len(lease.pass_fds), 3)
        assert lease.gpu_capacity_fd is not None
        assert lease.gpu_capacity_generation is not None
        first_gpu_generation = lease.gpu_capacity_generation
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        stop = context.Event()
        process = context.Process(
            target=_hold_fenced_cuda_worker,
            args=(
                str(self.root),
                str(run_dir),
                binding.attempt_id,
                lease.attempt_fd,
                lease.capacity_fd,
                lease.gpu_capacity_fd,
                lease.gpu_capacity_slot,
                lease.gpu_capacity_generation,
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
            with self.assertRaisesRegex(
                WorkerControlError, "ADAPTER_CONCURRENCY_LIMIT"
            ):
                ParentLaunchLease.acquire(
                    self.root,
                    waiting_dir,
                    max_active=2,
                    resource_device="cuda",
                )

            # The failed CUDA admission released its generic slot instead of
            # occupying CPU capacity while device 0 was busy.
            cpu_lease = ParentLaunchLease.acquire(
                self.root, cpu_dir, max_active=2
            )
            cpu_lease.abort()
        finally:
            stop.set()
            process.join(5.0)
            if process.is_alive():
                process.kill()
                process.join(5.0)
        self.assertEqual(process.exitcode, 0)

        admitted = ParentLaunchLease.acquire(
            self.root,
            waiting_dir,
            max_active=2,
            resource_device="cuda",
        )
        try:
            self.assertEqual(
                admitted.gpu_capacity_generation,
                first_gpu_generation + 1,
            )
        finally:
            admitted.abort()
        self.assertEqual(waiting.attempt_number, 18)
        self.assertEqual(cpu_binding.attempt_number, 19)

    def test_cuda_projection_generation_drift_fails_closed(self) -> None:
        binding, run_dir = self.binding(20, device="cuda")
        waiting, waiting_dir = self.binding(21, device="cuda")
        lease = ParentLaunchLease.acquire(
            self.root,
            run_dir,
            max_active=2,
            resource_device="cuda",
        )
        lease.mark_spawned(os.getpid())
        assert lease.gpu_capacity_fd is not None
        assert lease.gpu_capacity_slot is not None
        assert lease.gpu_capacity_generation is not None
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            resource_device="cuda",
            gpu_capacity_fd=os.dup(lease.gpu_capacity_fd),
            gpu_capacity_slot=lease.gpu_capacity_slot,
            gpu_capacity_generation=lease.gpu_capacity_generation,
            interval_seconds=10.0,
        )
        lease.close_parent()
        heartbeat.start()
        gpu_projection_path = (
            self.root
            / CONTROL_DIRECTORY
            / "worker-capacity"
            / "slots"
            / f"slot-{CUDA_LOGICAL_DEVICE_0_SLOT:03d}.json"
        )
        projection = json.loads(
            gpu_projection_path.read_text(encoding="utf-8")
        )
        projection["generation"] += 1
        projection.pop("record_hash")
        worker_control._atomic_write_private_json(
            gpu_projection_path,
            worker_control._record_with_hash(projection),
        )
        try:
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_GPU_FENCE_LOST"
            ):
                heartbeat._write_active_heartbeat()
            with self.assertRaisesRegex(
                WorkerControlError, "ADAPTER_CONCURRENCY_LIMIT"
            ):
                ParentLaunchLease.acquire(
                    self.root,
                    waiting_dir,
                    max_active=2,
                    resource_device="cuda",
                )
        finally:
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_GPU_FENCE_LOST"
            ):
                heartbeat.stop("failed")

        recovered = ParentLaunchLease.acquire(
            self.root,
            waiting_dir,
            max_active=2,
            resource_device="cuda",
        )
        recovered.abort()
        self.assertEqual(waiting.attempt_number, 21)

    def test_cuda_permanent_lock_inode_replacement_fails_closed(self) -> None:
        _first, first_dir = self.binding(22, device="cuda")
        lease = ParentLaunchLease.acquire(
            self.root,
            first_dir,
            max_active=2,
            resource_device="cuda",
        )
        lease.abort()
        gpu_lock = (
            self.root
            / CONTROL_DIRECTORY
            / "worker-capacity"
            / "slots"
            / f"slot-{CUDA_LOGICAL_DEVICE_0_SLOT:03d}.lock"
        )
        gpu_lock.rename(gpu_lock.with_name("slot-064.replaced.lock"))
        gpu_lock.touch(mode=0o600)
        _replacement, replacement_dir = self.binding(23, device="cuda")
        with self.assertRaisesRegex(
            WorkerControlError, "permanent lock inode changed"
        ):
            ParentLaunchLease.acquire(
                self.root,
                replacement_dir,
                max_active=2,
                resource_device="cuda",
            )

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

    def test_pre_running_evidence_rejects_ready_and_heartbeat_sidecars(
        self,
    ) -> None:
        def stopped_attempt(index: int) -> tuple[LaunchAttemptBinding, Path]:
            binding, run_dir = self.binding(index)
            lease = ParentLaunchLease.acquire(
                self.root, run_dir, max_active=1
            )
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
            heartbeat.stop("stopped")
            self.assertFalse(execution_fence_is_held(self.root, binding))
            mark_launch_failed(run_dir, binding)
            return binding, run_dir

        ready_binding, ready_dir = stopped_attempt(16)
        self.assertTrue((ready_dir / WORKER_READY_NAME).is_file())
        with self.assertRaisesRegex(
            WorkerControlError, "WORKER_RETRY_UNSAFE.*sidecar"
        ):
            read_pre_running_attempt_evidence(
                self.root, ready_dir, ready_binding
            )

        heartbeat_binding, heartbeat_dir = stopped_attempt(17)
        (heartbeat_dir / WORKER_READY_NAME).unlink()
        heartbeat_document = json.loads(
            (heartbeat_dir / WORKER_HEARTBEAT_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(heartbeat_document["state"], "stopped")
        with self.assertRaisesRegex(WorkerControlError, "WORKER_RETRY_UNSAFE"):
            read_pre_running_attempt_evidence(
                self.root, heartbeat_dir, heartbeat_binding
            )

        stopped_binding, stopped_dir = self.binding(18)
        stopped_lease = ParentLaunchLease.acquire(
            self.root, stopped_dir, max_active=1
        )
        stopped_lease.abort()
        self.assertFalse((stopped_dir / WORKER_READY_NAME).exists())
        self.assertFalse((stopped_dir / WORKER_HEARTBEAT_NAME).exists())
        evidence = read_pre_running_attempt_evidence(
            self.root, stopped_dir, stopped_binding
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.ticket_state, "failed")
        self.assertFalse(evidence.ready)

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
        with self.assertRaisesRegex(WorkerControlError, "WORKER_STOP_CONFLICT"):
            request_worker_stop(
                self.root,
                binding,
                request_id="timeout-after-cancel-1",
                reason="wall_time_exceeded",
                requested_at="2026-07-16T08:30:00Z",
                wall_time_seconds=1800,
                started_at=NOW,
                deadline_at="2026-07-16T08:30:00Z",
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

    def test_timeout_window_keeps_microseconds_and_waits_for_monotonic_budget(
        self,
    ) -> None:
        binding, run_dir = self.binding(14)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=1.0,
            wall_time_seconds=1,
            hard_exit=lambda _code: None,
        )
        lease.close_parent()
        heartbeat.start()
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        assert attempt.ready_record_hash is not None
        try:
            evidence, replayed = request_worker_stop(
                self.root,
                binding,
                request_id="timeout-microseconds-1",
                reason="wall_time_exceeded",
                requested_at="2026-07-16T08:00:01.123456Z",
                wall_time_seconds=1,
                started_at="2026-07-16T08:00:00.123456Z",
                deadline_at="2026-07-16T08:00:01.123456Z",
                ready_record_hash=attempt.ready_record_hash,
            )
            self.assertFalse(replayed)
            self.assertTrue(evidence.requested)
            time.sleep(0.08)
            self.assertFalse(
                read_worker_stop_evidence(self.root, binding).acknowledged
            )

            assert heartbeat._started_monotonic is not None
            heartbeat._started_monotonic -= 1.0
            for _ in range(200):
                evidence = read_worker_stop_evidence(self.root, binding)
                if evidence.acknowledged:
                    break
                time.sleep(0.01)
            self.assertTrue(evidence.acknowledged)
            self.assertEqual(
                evidence.started_at, "2026-07-16T08:00:00.123456Z"
            )
            self.assertEqual(
                evidence.deadline_at, "2026-07-16T08:00:01.123456Z"
            )
            self.assertEqual(
                evidence.ready_record_hash, attempt.ready_record_hash
            )
            with self.assertRaises(WorkerWallTimeExceeded):
                heartbeat.raise_if_cancel_requested()
        finally:
            heartbeat.stop("stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_uncooperative_timeout_self_exits_with_distinct_code(self) -> None:
        binding, run_dir = self.binding(15)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        process = context.Process(
            target=_hard_exit_on_timeout,
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
            attempt = read_worker_attempt_evidence(
                self.root, run_dir, binding
            )
            self.assertIsNotNone(attempt)
            assert attempt is not None
            assert attempt.ready_record_hash is not None
            request_worker_stop(
                self.root,
                binding,
                request_id="timeout-hard-exit-1",
                reason="wall_time_exceeded",
                requested_at="2026-07-16T08:00:01.123456Z",
                wall_time_seconds=1,
                started_at="2026-07-16T08:00:00.123456Z",
                deadline_at="2026-07-16T08:00:01.123456Z",
                ready_record_hash=attempt.ready_record_hash,
            )
            process.join(5.0)
            self.assertFalse(process.is_alive())
        finally:
            if process.is_alive():
                process.kill()
                process.join(5.0)
        self.assertEqual(
            process.exitcode, WALL_TIME_EXCEEDED_WORKER_EXIT_CODE
        )
        evidence = read_worker_stop_evidence(self.root, binding)
        self.assertTrue(evidence.acknowledged)
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.heartbeat_state, "stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_timeout_ready_hash_mismatch_is_never_acknowledged(self) -> None:
        binding, run_dir = self.binding(16)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=1.0,
            wall_time_seconds=1,
            hard_exit=lambda _code: None,
        )
        lease.close_parent()
        heartbeat.start()
        assert heartbeat._started_monotonic is not None
        heartbeat._started_monotonic -= 1.0
        try:
            evidence, _ = request_worker_stop(
                self.root,
                binding,
                request_id="timeout-ready-mismatch-1",
                reason="wall_time_exceeded",
                requested_at="2026-07-16T08:00:01Z",
                wall_time_seconds=1,
                started_at=NOW,
                deadline_at="2026-07-16T08:00:01Z",
                ready_record_hash="sha256:" + "f" * 64,
            )
            self.assertTrue(evidence.requested)
            with self.assertRaisesRegex(
                WorkerControlError, "timeout ready receipt changed"
            ):
                heartbeat.raise_if_cancel_requested()
            self.assertFalse(
                read_worker_stop_evidence(self.root, binding).acknowledged
            )
        finally:
            try:
                heartbeat.stop("failed")
            except WorkerControlError:
                pass
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_tampered_timeout_ready_hash_fails_integrity_before_ack(self) -> None:
        binding, run_dir = self.binding(17)
        lease = ParentLaunchLease.acquire(self.root, run_dir, max_active=1)
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=1.0,
            wall_time_seconds=1,
            hard_exit=lambda _code: None,
        )
        lease.close_parent()
        heartbeat.start()
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        assert attempt.ready_record_hash is not None
        try:
            request_worker_stop(
                self.root,
                binding,
                request_id="timeout-ready-tamper-1",
                reason="wall_time_exceeded",
                requested_at="2026-07-16T08:00:01Z",
                wall_time_seconds=1,
                started_at=NOW,
                deadline_at="2026-07-16T08:00:01Z",
                ready_record_hash=attempt.ready_record_hash,
            )
            request_path = (
                self.root
                / CONTROL_DIRECTORY
                / "worker-stop"
                / f"{binding.attempt_id}.request.json"
            )
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request["ready_record_hash"] = "sha256:" + "e" * 64
            request_path.write_text(
                json.dumps(request), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                WorkerControlError, "integrity check failed"
            ):
                heartbeat.raise_if_cancel_requested()
            with self.assertRaisesRegex(
                WorkerControlError, "integrity check failed"
            ):
                read_worker_stop_evidence(self.root, binding)
        finally:
            try:
                heartbeat.stop("failed")
            except WorkerControlError:
                pass
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

    def test_timeout_hard_exit_76_survives_stopped_write_failure(self) -> None:
        binding, run_dir = self.binding(18)
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
            wall_time_seconds=1,
            hard_exit=hard_exits.append,
        )
        lease.close_parent()
        heartbeat.start()
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        assert attempt.ready_record_hash is not None
        assert heartbeat._started_monotonic is not None
        heartbeat._started_monotonic -= 1.0
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
            request_worker_stop(
                self.root,
                binding,
                request_id="timeout-hard-write-failure-1",
                reason="wall_time_exceeded",
                requested_at="2026-07-16T08:00:01Z",
                wall_time_seconds=1,
                started_at=NOW,
                deadline_at="2026-07-16T08:00:01Z",
                ready_record_hash=attempt.ready_record_hash,
            )
            for _ in range(200):
                if hard_exits:
                    break
                time.sleep(0.01)
            self.assertEqual(
                hard_exits, [WALL_TIME_EXCEEDED_WORKER_EXIT_CODE]
            )
            with self.assertRaisesRegex(
                WorkerControlError, "WORKER_HEARTBEAT_FAILED"
            ):
                heartbeat.stop("stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_acknowledged_stop_running_write_failure_keeps_force_deadline(
        self,
    ) -> None:
        cases = (
            (19, "user_requested", CANCELLED_WORKER_EXIT_CODE),
            (20, "wall_time_exceeded", WALL_TIME_EXCEEDED_WORKER_EXIT_CODE),
        )
        for index, reason, expected_exit_code in cases:
            with self.subTest(reason=reason):
                binding, run_dir = self.binding(index)
                lease = ParentLaunchLease.acquire(
                    self.root, run_dir, max_active=1
                )
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
                    wall_time_seconds=1,
                    hard_exit=hard_exits.append,
                )
                lease.close_parent()
                heartbeat.start()
                attempt = read_worker_attempt_evidence(
                    self.root, run_dir, binding
                )
                self.assertIsNotNone(attempt)
                assert attempt is not None
                assert attempt.ready_record_hash is not None
                if reason == "wall_time_exceeded":
                    assert heartbeat._started_monotonic is not None
                    heartbeat._started_monotonic -= 1.0
                running_write_failed = threading.Event()
                original_write = heartbeat._write_heartbeat

                def fail_acknowledged_running(state: str) -> None:
                    if (
                        state == "running"
                        and heartbeat.stop_evidence is not None
                    ):
                        running_write_failed.set()
                        raise WorkerControlError(
                            "WORKER_HEARTBEAT_FAILED: synthetic grace write failure"
                        )
                    original_write(state)

                with patch.object(
                    heartbeat,
                    "_write_heartbeat",
                    side_effect=fail_acknowledged_running,
                ):
                    if reason == "user_requested":
                        request_worker_cancel(
                            self.root,
                            binding,
                            cancel_id="cancel-grace-running-write-1",
                            reason=reason,
                            requested_at=NOW,
                        )
                    else:
                        request_worker_stop(
                            self.root,
                            binding,
                            request_id="timeout-grace-running-write-1",
                            reason=reason,
                            requested_at="2026-07-16T08:00:01Z",
                            wall_time_seconds=1,
                            started_at=NOW,
                            deadline_at="2026-07-16T08:00:01Z",
                            ready_record_hash=attempt.ready_record_hash,
                        )
                    self.assertTrue(running_write_failed.wait(2.0))
                    for _ in range(200):
                        if hard_exits:
                            break
                        time.sleep(0.01)
                    self.assertEqual(hard_exits, [expected_exit_code])
                    forced_attempt = read_worker_attempt_evidence(
                        self.root, run_dir, binding
                    )
                    self.assertIsNotNone(forced_attempt)
                    assert forced_attempt is not None
                    self.assertEqual(
                        forced_attempt.heartbeat_state, "stopped"
                    )
                    with self.assertRaisesRegex(
                        WorkerControlError, "WORKER_HEARTBEAT_FAILED"
                    ):
                        heartbeat.stop("stopped")
                self.assertFalse(
                    execution_fence_is_held(self.root, binding)
                )

    def test_cooperative_stop_wins_after_grace_running_write_failure(self) -> None:
        binding, run_dir = self.binding(21)
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
        running_write_failed = threading.Event()
        original_write = heartbeat._write_heartbeat

        def fail_acknowledged_running(state: str) -> None:
            if state == "running" and heartbeat.stop_evidence is not None:
                running_write_failed.set()
                raise WorkerControlError(
                    "WORKER_HEARTBEAT_FAILED: synthetic grace write failure"
                )
            original_write(state)

        with patch.object(
            heartbeat,
            "_write_heartbeat",
            side_effect=fail_acknowledged_running,
        ):
            request_worker_cancel(
                self.root,
                binding,
                cancel_id="cancel-cooperative-after-write-failure-1",
                reason="user_requested",
                requested_at=NOW,
            )
            self.assertTrue(running_write_failed.wait(2.0))
            with self.assertRaises(WorkerCancellationRequested):
                heartbeat.raise_if_cancel_requested()
            heartbeat.stop("stopped")
        self.assertEqual(hard_exits, [])
        attempt = read_worker_attempt_evidence(self.root, run_dir, binding)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.heartbeat_state, "stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_post_ack_clock_failure_cannot_escape_force_deadline(self) -> None:
        binding, run_dir = self.binding(22)
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
        original_monotonic = heartbeat._monotonic
        monotonic_calls = 0

        def fail_after_ack() -> float:
            nonlocal monotonic_calls
            monotonic_calls += 1
            if monotonic_calls > 1:
                raise RuntimeError("synthetic post-ack clock failure")
            return original_monotonic()

        heartbeat._monotonic = fail_after_ack
        request_worker_cancel(
            self.root,
            binding,
            cancel_id="cancel-post-ack-clock-failure-1",
            reason="user_requested",
            requested_at=NOW,
        )
        for _ in range(200):
            if hard_exits:
                break
            time.sleep(0.01)
        self.assertEqual(hard_exits, [CANCELLED_WORKER_EXIT_CODE])
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
                "config.original.json",
            ],
        )

    def test_bootstrap_rejects_config_and_lease_device_drift_before_ready(
        self,
    ) -> None:
        binding, run_dir = self.binding(24, device="cuda")
        lease = ParentLaunchLease.acquire(
            self.root,
            run_dir,
            max_active=1,
            resource_device="cpu",
        )
        lease.mark_spawned(os.getpid())
        attempt_fd = os.dup(lease.attempt_fd)
        capacity_fd = os.dup(lease.capacity_fd)
        lease.close_parent()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                result = worker_launch_bootstrap.main(
                    [
                        "--command",
                        "invert",
                        "--config",
                        str(run_dir / WORKER_CONFIG_NAME),
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
                        "--resource-device",
                        "cpu",
                    ]
                )
        finally:
            os.close(attempt_fd)
            os.close(capacity_fd)
        self.assertEqual(result, 1)
        self.assertIn("WORKER_RESOURCE_INVALID", stderr.getvalue())
        self.assertFalse((run_dir / WORKER_READY_NAME).exists())
        self.assertFalse((run_dir / WORKER_HEARTBEAT_NAME).exists())

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
                str(run_dir / WORKER_CONFIG_NAME),
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

    def test_checkpoint_payload_tamper_fails_closed_before_waiting(self) -> None:
        binding, run_dir, heartbeat, manifest = self.checkpoint_worker(1)
        model_path = (
            run_dir
            / "checkpoints"
            / manifest.checkpoint_id
            / "model.npy"
        )
        data = bytearray(model_path.read_bytes())
        data[-1] ^= 0x01
        model_path.write_bytes(data)
        try:
            with self.assertRaises(WorkerControlError) as tampered:
                heartbeat.wait_for_checkpoint_resume(
                    manifest.as_dict(),
                    on_waiting=lambda _receipt: None,
                    on_resumed=lambda _receipt, _request: None,
                )
            self.assertEqual(tampered.exception.code, "WORKER_CHECKPOINT_INVALID")
            self.assertTrue(execution_fence_is_held(self.root, binding))
            self.assertFalse(
                (run_dir / worker_control.WORKER_CHECKPOINT_NAME).exists()
            )
            self.assertFalse(
                (run_dir / worker_control.WORKER_RESUME_ACK_NAME).exists()
            )
        finally:
            heartbeat.stop("failed")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_checkpoint_consumer_rejects_file_over_producer_bound(self) -> None:
        binding, run_dir, heartbeat, evidence = self.checkpoint_worker(1)
        try:
            receipt = self.rewrite_checkpoint_manifest(
                run_dir,
                evidence,
                lambda document: document["model"].__setitem__(
                    "size_bytes", worker_control.MAX_CHECKPOINT_FILE_BYTES + 1
                ),
            )
            with self.assertRaisesRegex(
                WorkerControlError,
                "checkpoint file descriptor is invalid",
            ):
                worker_control._validate_checkpoint_manifest(
                    run_dir, binding, receipt
                )
        finally:
            heartbeat.stop("failed")

    def test_checkpoint_consumer_rejects_declared_aggregate_before_reads(
        self,
    ) -> None:
        binding, run_dir, heartbeat, evidence = self.checkpoint_worker(2)

        def inflate_descriptors(document) -> None:
            descriptors = [
                document["model"],
                document["history"]["losses"],
                document["history"]["gradient_clip_values"],
                *document["optimizer"]["state"].values(),
            ]
            for descriptor in descriptors:
                descriptor["size_bytes"] = 1_900_000

        try:
            receipt = self.rewrite_checkpoint_manifest(
                run_dir, evidence, inflate_descriptors
            )
            with self.assertRaisesRegex(
                WorkerControlError,
                "checkpoint payload exceeds the aggregate bound",
            ):
                worker_control._validate_checkpoint_manifest(
                    run_dir, binding, receipt
                )
        finally:
            heartbeat.stop("failed")

    def test_checkpoint_stop_wins_terminal_arbitration_without_resume_ack(
        self,
    ) -> None:
        binding, run_dir, heartbeat, manifest = self.checkpoint_worker(2)
        gate_entered = threading.Event()
        gate_release = threading.Event()
        stopped = threading.Event()
        resumed_callbacks: list[bool] = []
        errors: list[BaseException] = []
        original_arbitration = worker_control._hold_worker_terminal_arbitration

        @contextlib.contextmanager
        def gated_arbitration(run_root, selected_binding):
            if threading.current_thread().name == "checkpoint-stop-first":
                gate_entered.set()
                if not gate_release.wait(3.0):
                    raise RuntimeError("checkpoint arbitration gate timed out")
            with original_arbitration(run_root, selected_binding) as root:
                yield root

        def on_waiting(receipt) -> None:
            self.write_status(
                run_dir,
                {
                    "job_id": binding.job_id,
                    "status": "waiting",
                    "stage": "checkpoint_wait",
                    "iteration": 1,
                    "total_iterations": 2,
                    "message": "waiting",
                    "updated_at": "2026-07-16T08:00:01Z",
                    "checkpoint_id": receipt["checkpoint_id"],
                    "checkpoint_record_hash": receipt["record_hash"],
                },
            )

        def wait_at_barrier() -> None:
            try:
                heartbeat.wait_for_checkpoint_resume(
                    manifest.as_dict(),
                    on_waiting=on_waiting,
                    on_resumed=lambda _receipt, _request: resumed_callbacks.append(
                        True
                    ),
                )
            except WorkerCancellationRequested:
                heartbeat.stop("stopped")
                stopped.set()
            except BaseException as error:
                errors.append(error)

        waiter = threading.Thread(
            target=wait_at_barrier, name="checkpoint-stop-first"
        )
        try:
            with patch.object(
                worker_control,
                "_hold_worker_terminal_arbitration",
                gated_arbitration,
            ):
                waiter.start()
                waiting: WorkerCheckpointEvidence | None = None
                deadline = time.monotonic() + 3.0
                while waiting is None and time.monotonic() < deadline:
                    try:
                        waiting = read_worker_checkpoint_evidence(
                            self.root, run_dir, binding
                        )
                    except WorkerControlError as error:
                        if error.code != "WORKER_CHECKPOINT_PENDING":
                            raise
                    time.sleep(0.01)
                self.assertIsNotNone(waiting)
                assert waiting is not None
                request_worker_checkpoint_resume(
                    self.root,
                    run_dir,
                    binding,
                    request_document=self.checkpoint_resume_request(
                        binding, waiting, resume_digit="a"
                    ),
                )
                self.assertTrue(gate_entered.wait(3.0))
                stop_evidence, replayed = request_worker_cancel(
                    self.root,
                    binding,
                    cancel_id="cancel-checkpoint-stop-first",
                    reason="user_requested",
                    requested_at=NOW,
                )
                self.assertFalse(replayed)
                self.assertTrue(stop_evidence.requested)
                gate_release.set()
                waiter.join(3.0)
        finally:
            gate_release.set()
            if waiter.is_alive():
                waiter.join(2.0)
            if not heartbeat._closed:
                heartbeat.stop("failed")
        self.assertFalse(waiter.is_alive())
        self.assertTrue(stopped.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(resumed_callbacks, [])
        self.assertTrue(read_worker_cancel_evidence(self.root, binding).acknowledged)
        self.assertFalse(
            (run_dir / worker_control.WORKER_RESUME_ACK_NAME).exists()
        )
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_checkpoint_ack_commits_before_running_then_cancel_is_normal(
        self,
    ) -> None:
        binding, run_dir, heartbeat, manifest = self.checkpoint_worker(3)
        errors: list[BaseException] = []
        ack_visible_to_running_projection: list[bool] = []

        def on_waiting(receipt) -> None:
            self.write_status(
                run_dir,
                {
                    "job_id": binding.job_id,
                    "status": "waiting",
                    "stage": "checkpoint_wait",
                    "iteration": 1,
                    "total_iterations": 2,
                    "message": "waiting",
                    "updated_at": "2026-07-16T08:00:01Z",
                    "checkpoint_id": receipt["checkpoint_id"],
                    "checkpoint_record_hash": receipt["record_hash"],
                },
            )

        def on_resumed(receipt, request) -> None:
            ack_visible_to_running_projection.append(
                (run_dir / worker_control.WORKER_RESUME_ACK_NAME).is_file()
            )
            self.write_status(
                run_dir,
                {
                    "job_id": binding.job_id,
                    "status": "running",
                    "stage": "invert",
                    "iteration": 1,
                    "total_iterations": 2,
                    "message": "resumed",
                    "updated_at": "2026-07-16T08:00:02Z",
                    "checkpoint_id": receipt["checkpoint_id"],
                    "checkpoint_record_hash": receipt["record_hash"],
                    "resume_id": request["resume_id"],
                    "resume_request_record_hash": request["record_hash"],
                },
            )

        def wait_at_barrier() -> None:
            try:
                heartbeat.wait_for_checkpoint_resume(
                    manifest.as_dict(),
                    on_waiting=on_waiting,
                    on_resumed=on_resumed,
                )
            except BaseException as error:
                errors.append(error)

        waiter = threading.Thread(target=wait_at_barrier)
        waiter.start()
        try:
            waiting: WorkerCheckpointEvidence | None = None
            deadline = time.monotonic() + 3.0
            while waiting is None and time.monotonic() < deadline:
                try:
                    waiting = read_worker_checkpoint_evidence(
                        self.root, run_dir, binding
                    )
                except WorkerControlError as error:
                    if error.code != "WORKER_CHECKPOINT_PENDING":
                        raise
                time.sleep(0.01)
            self.assertIsNotNone(waiting)
            assert waiting is not None
            request_worker_checkpoint_resume(
                self.root,
                run_dir,
                binding,
                request_document=self.checkpoint_resume_request(
                    binding, waiting, resume_digit="b"
                ),
            )
            waiter.join(3.0)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(ack_visible_to_running_projection, [True])
            resumed = read_worker_checkpoint_evidence(
                self.root, run_dir, binding
            )
            assert resumed is not None
            self.assertEqual(resumed.state, "resumed")

            cancellation, replayed = request_worker_cancel(
                self.root,
                binding,
                cancel_id="cancel-checkpoint-ack-first",
                reason="user_requested",
                requested_at="2026-07-16T08:00:03Z",
            )
            self.assertFalse(replayed)
            self.assertTrue(cancellation.requested)
            deadline = time.monotonic() + 3.0
            while not cancellation.acknowledged and time.monotonic() < deadline:
                cancellation = read_worker_cancel_evidence(self.root, binding)
                time.sleep(0.01)
            self.assertTrue(cancellation.acknowledged)
            with self.assertRaises(WorkerCancellationRequested):
                heartbeat.raise_if_cancel_requested()
        finally:
            heartbeat.stop("stopped")
        self.assertFalse(execution_fence_is_held(self.root, binding))

    def test_checkpoint_wait_resume_keeps_same_fenced_attempt(self) -> None:
        import torch

        from fwi_worker.checkpoint import save_checkpoint_payload
        from fwi_worker.config import resolve_config
        from fwi_worker.inversion import InversionCheckpointState

        binding, run_dir = self.binding(1)
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
        config = resolve_config(
            {"preset": "fwi_smoke", "device": "cpu", "iterations": 2}
        )
        velocity = torch.nn.Parameter(
            torch.full((2, 3), 2000.0, dtype=torch.float32)
        )
        optimizer = torch.optim.Adam([velocity], lr=config.learning_rate)
        optimizer.zero_grad(set_to_none=True)
        velocity.square().sum().backward()
        optimizer.step()
        checkpoint = InversionCheckpointState(
            completed_updates=1,
            next_state_index=1,
            velocity=velocity,
            optimizer=optimizer,
            losses=(1.0,),
            gradient_clip_values=(0.5,),
        )
        manifest = save_checkpoint_payload(
            run_dir=run_dir,
            binding=binding,
            config=config,
            checkpoint=checkpoint,
            clock=lambda: "2026-07-16T08:00:01Z",
        )
        errors: list[BaseException] = []

        def on_waiting(receipt) -> None:
            self.write_status(
                run_dir,
                {
                    "job_id": binding.job_id,
                    "status": "waiting",
                    "stage": "checkpoint_wait",
                    "iteration": 1,
                    "total_iterations": 2,
                    "message": "waiting",
                    "updated_at": "2026-07-16T08:00:01Z",
                    "checkpoint_id": receipt["checkpoint_id"],
                    "checkpoint_record_hash": receipt["record_hash"],
                },
            )

        def on_resumed(receipt, request) -> None:
            self.write_status(
                run_dir,
                {
                    "job_id": binding.job_id,
                    "status": "running",
                    "stage": "invert",
                    "iteration": 1,
                    "total_iterations": 2,
                    "message": "resumed",
                    "updated_at": "2026-07-16T08:00:02Z",
                    "checkpoint_id": receipt["checkpoint_id"],
                    "checkpoint_record_hash": receipt["record_hash"],
                    "resume_id": request["resume_id"],
                    "resume_request_record_hash": request["record_hash"],
                },
            )

        def wait_at_barrier() -> None:
            try:
                heartbeat.wait_for_checkpoint_resume(
                    manifest.as_dict(),
                    on_waiting=on_waiting,
                    on_resumed=on_resumed,
                )
            except BaseException as error:
                errors.append(error)

        waiter = threading.Thread(target=wait_at_barrier)
        waiter.start()
        waiting: WorkerCheckpointEvidence | None = None
        deadline = time.monotonic() + 3.0
        while waiting is None and time.monotonic() < deadline:
            try:
                waiting = read_worker_checkpoint_evidence(
                    self.root, run_dir, binding
                )
            except WorkerControlError as error:
                if error.code != "WORKER_CHECKPOINT_PENDING":
                    raise
            time.sleep(0.01)
        self.assertIsNotNone(waiting)
        assert waiting is not None
        self.assertEqual(waiting.state, "waiting")
        self.assertTrue(execution_fence_is_held(self.root, binding))
        request = worker_control._record_with_hash(
            {
                "schema_version": "1.0.0",
                "resume_id": "resume-" + "d" * 32,
                "submission_id": binding.submission_id,
                "attempt_id": binding.attempt_id,
                "attempt_number": binding.attempt_number,
                "checkpoint_id": waiting.checkpoint_id,
                "checkpoint_manifest_hash": waiting.manifest_hash,
                "checkpoint_receipt_record_hash": waiting.checkpoint_record_hash,
                "checkpoint_proof_hash": "sha256:" + "e" * 64,
                "authorized_at": "2026-07-16T08:00:01Z",
            }
        )
        requested = request_worker_checkpoint_resume(
            self.root,
            run_dir,
            binding,
            request_document=request,
        )
        self.assertIn(requested.state, {"requested", "resumed"})
        waiter.join(3.0)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(errors, [])
        resumed = read_worker_checkpoint_evidence(self.root, run_dir, binding)
        assert resumed is not None
        self.assertEqual(resumed.state, "resumed")
        self.assertEqual(resumed.attempt_id, binding.attempt_id)
        self.assertEqual(resumed.resume_request_record_hash, request["record_hash"])
        self.assertTrue(execution_fence_is_held(self.root, binding))

        # Model a later abrupt running exit.  The completed resume preserves
        # the existing D-012 exact worker-exit proof; no checkpoint restore is
        # inferred and no second process is launched by the resume path.
        heartbeat._stop.set()
        assert heartbeat._thread is not None
        heartbeat._thread.join(2.0)
        heartbeat._close_descriptors()
        self.assertFalse(execution_fence_is_held(self.root, binding))
        pre = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        post = {
            **pre,
            "status": "failed",
            "stage": "worker_exit",
            "message": "FWI worker exited with code -9",
            "updated_at": "2026-07-16T08:00:03Z",
        }
        exit_evidence = record_worker_exit(
            self.root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre,
            post_status=post,
        )
        self.assertEqual(exit_evidence.attempt_id, binding.attempt_id)

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
                str(run_dir / WORKER_CONFIG_NAME),
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
