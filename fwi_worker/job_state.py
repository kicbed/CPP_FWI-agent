"""Atomic status files and append-only progress/log output."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
VALID_STATUSES = {"queued", "running", *TERMINAL_STATUSES}
VALID_TRANSITIONS = {
    None: {"queued", "running", "cancelled"},
    "queued": {"queued", "running", "failed", "cancelled"},
    "running": {"running", "succeeded", "failed", "cancelled"},
    "succeeded": {"succeeded"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class JobState:
    def __init__(self, run_dir: Path, job_id: str) -> None:
        self.run_dir = run_dir
        self.job_id = job_id
        self.status_path = run_dir / "status.json"
        self.progress_path = run_dir / "progress.jsonl"
        self.log_path = run_dir / "run.log"

    def read(self) -> dict[str, Any] | None:
        if not self.status_path.exists():
            return None
        value = json.loads(self.status_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status.json root must be an object")
        return value

    def update(
        self,
        status: str,
        stage: str,
        iteration: int,
        total_iterations: int,
        message: str,
        **details: Any,
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid job status: {status}")
        previous = self.read()
        previous_status = previous.get("status") if previous else None
        early_managed_timeout = (
            previous_status is None
            and status == "failed"
            and details.get("failure_code") == "WALL_TIME_EXCEEDED"
        )
        if (
            status not in VALID_TRANSITIONS.get(previous_status, set())
            and not early_managed_timeout
        ):
            raise ValueError(f"invalid status transition: {previous_status} -> {status}")
        value: dict[str, Any] = {
            "job_id": self.job_id,
            "status": status,
            "stage": stage,
            "iteration": int(iteration),
            "total_iterations": int(total_iterations),
            "message": message,
            "updated_at": utc_now(),
        }
        value.update(details)
        atomic_write_json(self.status_path, value)
        event = dict(value)
        event["event"] = "status"
        self.append_progress(event)
        self.append_log(f"STATUS {status}")
        return value

    def append_iteration(self, iteration: int, frequency_hz: float, loss: float) -> None:
        event = {
            "event": "iteration",
            "job_id": self.job_id,
            "iteration": int(iteration),
            "frequency_hz": float(frequency_hz),
            "loss": float(loss),
            "updated_at": utc_now(),
        }
        self.append_progress(event)
        self.append_log(f"ITER {iteration} FREQ {frequency_hz:.8g} LOSS {loss:.12g}")

    def append_progress(self, value: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.progress_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
            stream.write("\n")

    def append_log(self, line: str) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{utc_now()} {line}\n")
