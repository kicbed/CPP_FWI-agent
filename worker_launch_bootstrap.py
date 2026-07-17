"""Lightweight fenced bootstrap for Adapter-managed FWI Workers.

This module intentionally imports no numerical package before it validates the
two inherited kernel leases, starts the independent heartbeat, and publishes
the immutable ready receipt.  The standalone/MCP ``python -m fwi_worker`` path
is unchanged and does not claim this Adapter-managed capacity boundary.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import Any

from worker_launch_control import (
    CANCELLED_WORKER_EXIT_CODE,
    WALL_TIME_EXCEEDED_WORKER_EXIT_CODE,
    WorkerCancellationRequested,
    WorkerHeartbeat,
    WorkerWallTimeExceeded,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m worker_launch_bootstrap")
    parser.add_argument("--command", choices=("invert",), required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--launch-attempt-id", required=True)
    parser.add_argument("--launch-attempt-fd", required=True, type=int)
    parser.add_argument("--capacity-lease-fd", required=True, type=int)
    parser.add_argument("--wall-time-seconds", type=int, default=86_400)
    parser.add_argument(
        "--checkpoint-after-first-update",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _load_run_worker() -> Any:
    module = importlib.import_module("fwi_worker.__main__")
    run_worker = getattr(module, "run_worker", None)
    if not callable(run_worker):
        raise RuntimeError("fixed FWI Worker entry point is unavailable")
    return run_worker


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    heartbeat: WorkerHeartbeat | None = None
    try:
        heartbeat = WorkerHeartbeat(
            run_root=args.run_root,
            run_dir=args.run_dir,
            attempt_id=args.launch_attempt_id,
            attempt_fd=args.launch_attempt_fd,
            capacity_fd=args.capacity_lease_fd,
            wall_time_seconds=args.wall_time_seconds,
        )
        heartbeat.start()
        heartbeat.raise_if_cancel_requested()
        run_worker = _load_run_worker()

        def checkpoint_barrier(run_dir, config, state, checkpoint) -> None:
            assert heartbeat is not None
            from fwi_worker.checkpoint import save_checkpoint_payload

            manifest = save_checkpoint_payload(
                run_dir=run_dir,
                binding=heartbeat.checkpoint_binding,
                config=config,
                checkpoint=checkpoint,
            )

            def on_waiting(receipt) -> None:
                state.update(
                    "waiting",
                    "checkpoint_wait",
                    checkpoint.completed_updates,
                    config.iterations,
                    "Checkpoint is durable; waiting for exact resume authorization",
                    checkpoint_id=receipt["checkpoint_id"],
                    checkpoint_record_hash=receipt["record_hash"],
                    checkpoint_manifest_relative_path=(
                        receipt["manifest_relative_path"]
                    ),
                    checkpoint_manifest_size_bytes=receipt["manifest_size_bytes"],
                    checkpoint_manifest_hash=receipt["manifest_hash"],
                    completed_updates=checkpoint.completed_updates,
                )

            def on_resumed(receipt, request) -> None:
                state.update(
                    "running",
                    "invert",
                    checkpoint.completed_updates,
                    config.iterations,
                    "Exact live Worker resumed after checkpoint authorization",
                    checkpoint_id=receipt["checkpoint_id"],
                    checkpoint_record_hash=receipt["record_hash"],
                    resume_id=request["resume_id"],
                    resume_request_record_hash=request["record_hash"],
                    completed_updates=checkpoint.completed_updates,
                )

            heartbeat.wait_for_checkpoint_resume(
                manifest.as_dict(),
                on_waiting=on_waiting,
                on_resumed=on_resumed,
            )

        run_arguments = {
            "managed_launch": True,
            "cancel_check": heartbeat.raise_if_cancel_requested,
        }
        if args.checkpoint_after_first_update:
            run_arguments["checkpoint_barrier"] = checkpoint_barrier
        result = run_worker(
            args.command,
            args.config,
            args.run_dir,
            **run_arguments,
        )
        heartbeat.stop("succeeded")
        heartbeat = None
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except WorkerCancellationRequested:
        if heartbeat is not None:
            heartbeat.stop("stopped")
            heartbeat = None
        return CANCELLED_WORKER_EXIT_CODE
    except WorkerWallTimeExceeded:
        if heartbeat is not None:
            heartbeat.stop("stopped")
            heartbeat = None
        return WALL_TIME_EXCEEDED_WORKER_EXIT_CODE
    except BaseException as error:
        if heartbeat is not None:
            try:
                heartbeat.stop("failed")
            except Exception:
                pass
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        print(
            f"worker_launch_bootstrap: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
