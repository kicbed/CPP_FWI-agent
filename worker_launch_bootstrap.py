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
    WorkerCancellationRequested,
    WorkerHeartbeat,
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
        )
        heartbeat.start()
        heartbeat.raise_if_cancel_requested()
        run_worker = _load_run_worker()
        result = run_worker(
            args.command,
            args.config,
            args.run_dir,
            managed_launch=True,
            cancel_check=heartbeat.raise_if_cancel_requested,
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
