#!/usr/bin/env python3
"""Production-composed Guided fixed-Recipe HTTP/SSE exit evidence.

The CPU case is the representative P3 E2E.  The dual-Workflow CUDA case is
explicitly opt-in because it owns a real CUDA device for the duration of two
complete five-node workflows.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np

from scientific_runtime import RuntimeSupervisor as ProductionRuntimeSupervisor
from scientific_runtime.fwi_adapter import DEFAULT_WORKER_PYTHON
from scientific_runtime.task_dispatcher import DeepwaveTaskDispatcher
from web import serve
from web.workbench_api import API_PREFIX, APIResponse, SSEEventStream


HOST = "127.0.0.1:8080"
ORIGIN = "http://127.0.0.1:8080"
NODES = ("data_check", "forward", "quality_check", "fwi", "result_check")
TERMINAL = {"Succeeded", "Failed", "Cancelled"}
CUDA_GATE = "SCIENTIFIC_RUNTIME_RUN_GUIDED_RECIPE_CUDA_E2E"


class _Recorder:
    """Shared path-free observations across pre/post-restart composition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.attempts: Counter[tuple[str, str]] = Counter()
        self.successes: Counter[tuple[str, str]] = Counter()
        self.max_cuda_active = 0
        self.status_observations: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def attempted(self, task_id: str, node_id: str) -> None:
        with self._lock:
            self.attempts[(task_id, node_id)] += 1

    def dispatched(self, task_id: str, node_id: str) -> None:
        with self._lock:
            self.successes[(task_id, node_id)] += 1

    def cuda_sample(self, active: int) -> None:
        with self._lock:
            self.max_cuda_active = max(self.max_cuda_active, active)

    def observed(self, task_id: str, node_id: str, status: str, updated_at: str) -> None:
        with self._lock:
            history = self.status_observations.setdefault((task_id, node_id), [])
            history.append((status, updated_at))
            del history[:-20]

    def observations(self, task_id: str) -> dict[str, list[tuple[str, str]]]:
        with self._lock:
            return {
                node_id: list(history)
                for (observed_task_id, node_id), history in self.status_observations.items()
                if observed_task_id == task_id
            }

    def count(self, task_id: str, node_id: str) -> tuple[int, int]:
        with self._lock:
            return self.attempts[(task_id, node_id)], self.successes[(task_id, node_id)]


class _RecordingDispatcher(DeepwaveTaskDispatcher):
    """Real Deepwave dispatcher with read-only dispatch/concurrency evidence."""

    def __init__(self, adapter, recorder: _Recorder) -> None:
        super().__init__(adapter)
        self._recorder = recorder
        self._cuda_intents = {}
        self._sample_lock = threading.Lock()

    @staticmethod
    def _is_cuda(intent) -> bool:
        resources = intent.request.get("resources", {})
        return resources.get("device") == "cuda" or resources.get("gpu_count") == 1

    def ensure_first_dispatch(self, intent):
        self._recorder.attempted(intent.task_id, intent.node_id)
        handle = super().ensure_first_dispatch(intent)
        self._recorder.dispatched(intent.task_id, intent.node_id)
        if self._is_cuda(intent):
            dispatched = replace(intent, state="dispatched", handle=handle)
            with self._sample_lock:
                self._cuda_intents[(intent.task_id, intent.node_id)] = dispatched
            self._sample_cuda()
        return handle

    def status(self, intent):
        result = super().status(intent)
        self._recorder.observed(
            intent.task_id,
            intent.node_id,
            result.get("status"),
            result.get("updated_at"),
        )
        if self._is_cuda(intent):
            with self._sample_lock:
                self._cuda_intents[(intent.task_id, intent.node_id)] = intent
            self._sample_cuda()
        return result

    def _sample_cuda(self) -> None:
        with self._sample_lock:
            tracked = list(self._cuda_intents.items())
        active = 0
        terminal_keys = []
        for key, intent in tracked:
            try:
                status = DeepwaveTaskDispatcher.status(self, intent)
            except Exception:
                continue
            # Queued includes a managed process blocked before acquiring the
            # inherited logical-device flock.  Count only states that hold the
            # real CUDA execution boundary; otherwise two serialized workers
            # would be misreported as two concurrent CUDA computations.
            if status.get("status") in {"Running", "Waiting", "Retrying"}:
                active += 1
            elif status.get("terminal") is True:
                terminal_keys.append(key)
        with self._sample_lock:
            for key in terminal_keys:
                self._cuda_intents.pop(key, None)
        self._recorder.cuda_sample(active)


class GuidedRecipeProductionE2E(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        if not DEFAULT_WORKER_PYTHON.is_file() or not os.access(DEFAULT_WORKER_PYTHON, os.X_OK):
            self.skipTest(f"fixed Worker Python is unavailable: {DEFAULT_WORKER_PYTHON}")

    def _clean_source_identity(self) -> tuple[str, str]:
        project_root = Path(__file__).resolve().parents[2]
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(status.stdout, "", "production E2E requires a clean Git tree")
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=project_root, text=True
        ).strip()
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertRegex(tree, r"^[0-9a-f]{40}$")
        return commit, tree

    @staticmethod
    def _supervisor(tasks, **kwargs):
        return ProductionRuntimeSupervisor(
            tasks,
            **kwargs,
            # Match the production lease window.  A complete five-node
            # cache-only Recipe verifies every physical artifact in one
            # bounded supervisor pass and can legitimately exceed the former
            # test-only five-second lease on a busy CPU host.
            lease_seconds=30,
            heartbeat_interval_seconds=1,
            poll_interval_seconds=0.05,
            worker_projection_interval_seconds=0.10,
            start_timeout_seconds=10,
            join_timeout_seconds=15,
        )

    def _compose(self, recorder: _Recorder):
        self._recorder = recorder
        with mock.patch.object(
            serve,
            "DeepwaveTaskDispatcher",
            side_effect=lambda adapter: _RecordingDispatcher(adapter, recorder),
        ), mock.patch.object(
            serve, "RuntimeSupervisor", side_effect=self._supervisor
        ):
            runtime = serve.create_workbench_runtime()
        session = runtime.api.dispatch("GET", f"{API_PREFIX}/session", {"Host": HOST}, b"")
        data = self._json(session, 200)
        return runtime, data["csrf_token"]

    @staticmethod
    def _json(response: APIResponse, status: int) -> dict:
        if response.status != status:
            raise AssertionError(f"HTTP {response.status}, expected {status}: {response.body[:500]!r}")
        payload = json.loads(response.body.decode("utf-8"))
        if payload.get("ok") is not True:
            raise AssertionError(payload)
        return payload["data"]

    @staticmethod
    def _get(api, csrf: str, path: str) -> APIResponse:
        return api.dispatch("GET", path, {"Host": HOST, "X-Workbench-CSRF": csrf}, b"")

    @staticmethod
    def _post(api, csrf: str, path: str, payload: dict, key: str) -> APIResponse:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return api.dispatch(
            "POST",
            path,
            {
                "Host": HOST,
                "X-Workbench-CSRF": csrf,
                "Origin": ORIGIN,
                "Idempotency-Key": key,
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            },
            body,
        )

    def _create_approved(
        self,
        runtime,
        csrf: str,
        *,
        device: str,
        seed: int,
        suffix: str,
        iterations: int = 1,
    ):
        form = {
            "goal": "Run the fixed forward, quality-check, and FWI Recipe",
            "dataset_id": "marmousi_94_288",
            "dataset_version": "1.0.0",
            "preset": "fwi_smoke",
            "device": device,
            "iterations": iterations,
            "seed": seed,
            "optimizer": "adam",
            "learning_rate": "10",
            "recipe_id": "forward_qc_fwi",
            "recipe_version": "1.0.0",
        }
        created = self._json(
            self._post(runtime.api, csrf, f"{API_PREFIX}/tasks", form, f"recipe-create-{suffix}"),
            201,
        )
        task_id = created["task_id"]
        self.assertEqual(
            [(n["node_id"], n["dependencies"]) for n in created["plan"]["nodes"]],
            [
                ("data_check", []),
                ("forward", ["data_check"]),
                ("quality_check", ["data_check"]),
                ("fwi", ["forward", "quality_check"]),
                ("result_check", ["fwi"]),
            ],
        )
        plan_hash = created["plan"]["plan_hash"]
        self._json(
            self._post(
                runtime.api,
                csrf,
                f"{API_PREFIX}/tasks/{task_id}/approve",
                {"plan_hash": plan_hash},
                f"recipe-approve-{suffix}",
            ),
            200,
        )
        return task_id, plan_hash

    def _task(self, runtime, csrf: str, task_id: str) -> dict:
        response = self._get(runtime.api, csrf, f"{API_PREFIX}/tasks/{task_id}")
        if response.status >= 500:
            # The public boundary must stay redacted.  Test code may call the
            # in-process application once so a regression reports the exact
            # chained Store invariant instead of only TASK_STORE_UNAVAILABLE.
            runtime.api._application.get_task(task_id, refresh=True)
        return self._json(response, 200)

    def _wait(self, runtime, csrf: str, task_id: str, predicate, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self._task(runtime, csrf, task_id)
            if predicate(last):
                return last
            if runtime.supervisor.failure_code:
                self.fail(f"RuntimeSupervisor failed: {runtime.supervisor.failure_code}")
            cycle = runtime.supervisor.last_cycle
            if cycle is not None and cycle.task_failures:
                self.fail(
                    "RuntimeSupervisor task failure: "
                    f"{cycle.task_failures}; nodes={last['runtime_nodes']}; "
                    f"adapter_status={self._recorder.observations(task_id)}"
                )
            time.sleep(0.10)
        self.fail(f"timed out waiting for {task_id}; last status={None if last is None else last['status']}")

    @staticmethod
    def _node(task: dict, node_id: str) -> dict:
        return next(node for node in task["runtime_nodes"] if node["node_id"] == node_id)

    @staticmethod
    def _timeline(database: Path, task_id: str) -> dict[str, dict[str, int]]:
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT node_id, state, recorded_at_us FROM dag_node_state_events "
                "WHERE task_id = ? ORDER BY recorded_at_us, revision",
                (task_id,),
            ).fetchall()
        result = {node: {} for node in NODES}
        for node, state, recorded_at_us in rows:
            result[node][state] = recorded_at_us
        return result

    @staticmethod
    def _durable_dispatch_count(database: Path, task_id: str, node_id: str) -> int:
        with sqlite3.connect(database) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM dispatch_intents WHERE task_id = ? AND node_id = ?",
                (task_id, node_id),
            ).fetchone()[0]

    @staticmethod
    def _worker_job_id(database: Path, task_id: str, node_id: str) -> str:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT dispatch_handle_json FROM dag_node_terminal_facts "
                "WHERE task_id = ? AND node_id = ? AND node_state = 'Succeeded'",
                (task_id, node_id),
            ).fetchone()
        if row is None:
            raise AssertionError("Succeeded node has no durable dispatch handle")
        handle = json.loads(row[0])
        job_id = handle.get("job_id")
        if not isinstance(job_id, str) or not job_id.startswith("fwi-"):
            raise AssertionError("durable dispatch handle has no fixed Worker job")
        return job_id

    @staticmethod
    def _node_fingerprint(database: Path, task_id: str, node_id: str) -> dict:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT dispatch_handle_json FROM dag_node_terminal_facts "
                "WHERE task_id = ? AND node_id = ? AND node_state = 'Succeeded'",
                (task_id, node_id),
            ).fetchone()
        if row is None:
            raise AssertionError("executed Recipe node has no durable dispatch handle")
        fingerprint = json.loads(row[0]).get("fingerprint")
        if not isinstance(fingerprint, dict):
            raise AssertionError("executed Recipe node has no runtime fingerprint")
        return fingerprint

    def _wait_for_task_failure(
        self, runtime, csrf: str, task_id: str, timeout: float
    ) -> tuple[dict, str]:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self._task(runtime, csrf, task_id)
            cycle = runtime.supervisor.last_cycle
            if cycle is not None:
                failure = next(
                    (code for failed_task, code in cycle.task_failures if failed_task == task_id),
                    None,
                )
                if failure is not None:
                    return last, failure
            if runtime.supervisor.failure_code:
                self.fail(f"RuntimeSupervisor failed: {runtime.supervisor.failure_code}")
            time.sleep(0.05)
        self.fail(f"timed out waiting for fail-closed cache verification: {last}")

    def _sse(self, runtime, csrf: str, task_id: str, after: int) -> tuple[list[dict], int, bool]:
        stream = runtime.api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{task_id}/events/stream?after_sequence={after}",
            {"Host": HOST, "X-Workbench-CSRF": csrf, "Accept": "text/event-stream"},
            b"",
        )
        self.assertIsInstance(stream, SSEEventStream)
        events = []
        while True:
            frames = stream.next_batch()
            for frame in frames:
                text = frame.decode("utf-8")
                sequence = int(next(line[4:] for line in text.splitlines() if line.startswith("id: ")))
                event = json.loads(next(line[6:] for line in text.splitlines() if line.startswith("data: ")))
                self.assertEqual(sequence, event["sequence"])
                events.append(event)
            if stream.terminal or not frames:
                break
        return events, stream.after_sequence, stream.terminal

    @staticmethod
    def _output_identity(node: dict) -> list[tuple[str, str, str]]:
        return sorted(
            (item["output_port"], item["content_hash"], item["manifest_hash"])
            for item in node["outputs"]
        )

    @staticmethod
    def _output_hash(node: dict, output_port: str) -> str:
        matches = [
            value["content_hash"]
            for value in node["outputs"]
            if value["output_port"] == output_port
        ]
        if len(matches) != 1:
            raise AssertionError(
                f"{node['node_id']} has {len(matches)} public {output_port!r} outputs"
            )
        return matches[0]

    def _artifact_evidence(self, runtime, csrf: str, task_id: str, plan_hash: str) -> list[dict]:
        listed = self._json(
            self._get(runtime.api, csrf, f"{API_PREFIX}/tasks/{task_id}/artifacts"), 200
        )["artifacts"]
        self.assertEqual(len(listed), 8)
        evidence = []
        for manifest in listed:
            artifact_id = manifest["artifact_id"]
            content = self._get(
                runtime.api, csrf, f"{API_PREFIX}/tasks/{task_id}/artifacts/{artifact_id}"
            )
            self.assertEqual(content.status, 200)
            self.assertEqual("sha256:" + hashlib.sha256(content.body).hexdigest(), manifest["content_hash"])
            self.assertEqual(len(content.body), manifest["size_bytes"])
            media_type = manifest["media_type"]
            if media_type == "application/x-npy":
                finite = bool(np.isfinite(np.load(io.BytesIO(content.body), allow_pickle=False)).all())
            elif media_type == "text/csv":
                rows = list(csv.DictReader(io.StringIO(content.body.decode("utf-8"))))
                finite = bool(rows) and all(
                    math.isfinite(float(value)) for row in rows for value in row.values()
                )
            elif media_type == "image/png":
                finite = content.body.startswith(b"\x89PNG\r\n\x1a\n")
            else:
                self.fail(f"unexpected artifact media type: {media_type}")
            self.assertTrue(finite)
            self.assertEqual(manifest["lineage"]["plan_hash"], plan_hash)
            self.assertEqual(manifest["lineage"]["algorithm"]["version"], "1.6.0")
            evidence.append(
                {
                    "artifact_id": artifact_id,
                    "content_hash": manifest["content_hash"],
                    "media_type": media_type,
                    "finite": finite,
                }
            )
        return evidence

    def test_cpu_recipe_restart_sse_artifact_hash_and_lineage(self) -> None:
        source_commit, source_tree = self._clean_source_identity()
        recorder = _Recorder()
        with tempfile.TemporaryDirectory(prefix="guided-recipe-e2e-") as private:
            root = Path(private)
            run_root, state_root = root / "runs", root / "state"
            run_root.mkdir(mode=0o700)
            state_root.mkdir(mode=0o700)
            database = state_root / "tasks.sqlite3"
            environment = {
                "FWI_RUN_ROOT": str(run_root),
                "SCIENTIFIC_RUNTIME_DB_PATH": str(database),
                "AGENT_CORS_ORIGIN": ORIGIN,
            }
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
                serve, "HOST", "127.0.0.1"
            ), mock.patch.object(serve, "PORT", 8080):
                first, csrf = self._compose(recorder)
                task_id, plan_hash = self._create_approved(
                    first, csrf, device="cpu", seed=17, suffix="cpu"
                )
                self.assertTrue(first.supervisor.start())
                try:
                    at_a = self._wait(
                        first,
                        csrf,
                        task_id,
                        lambda task: self._node(task, "data_check")["status"] == "Succeeded",
                        900,
                    )
                finally:
                    self.assertTrue(first.supervisor.stop())

                a_before = self._output_identity(self._node(at_a, "data_check"))
                dispatch_before = recorder.count(task_id, "data_check")
                durable_before = self._durable_dispatch_count(database, task_id, "data_check")
                prefix, prefix_last, prefix_terminal = self._sse(first, csrf, task_id, 0)
                self.assertFalse(prefix_terminal)
                frozen_timeline = self._timeline(database, task_id)
                self._task(first, csrf, task_id)
                self._task(first, csrf, task_id)
                self.assertEqual(self._timeline(database, task_id), frozen_timeline)

                second, csrf = self._compose(recorder)
                recovered = self._task(second, csrf, task_id)
                a_after = self._output_identity(self._node(recovered, "data_check"))
                self.assertEqual(a_after, a_before)
                self.assertEqual(recorder.count(task_id, "data_check"), dispatch_before)
                self.assertEqual(
                    self._durable_dispatch_count(database, task_id, "data_check"), durable_before
                )
                self.assertTrue(second.supervisor.start())
                try:
                    terminal = self._wait(
                        second, csrf, task_id, lambda task: task["status"] in TERMINAL, 1200
                    )
                finally:
                    self.assertTrue(second.supervisor.stop())

                self.assertEqual(terminal["status"], "Succeeded")
                self.assertTrue(all(self._node(terminal, node)["status"] == "Succeeded" for node in NODES))
                self.assertEqual(recorder.count(task_id, "data_check"), (1, 1))
                self.assertEqual(self._durable_dispatch_count(database, task_id, "data_check"), 1)
                production_fingerprint = self._node_fingerprint(
                    database, task_id, "data_check"
                )
                self.assertEqual(
                    production_fingerprint["provenance_mode"], "reproducible"
                )
                self.assertIs(
                    production_fingerprint["source"]["identity_complete"], True
                )
                self.assertIs(production_fingerprint["source"]["dirty"], False)
                self.assertEqual(
                    production_fingerprint["source"]["git_commit"], source_commit
                )
                self.assertEqual(
                    production_fingerprint["source"]["git_tree"], source_tree
                )
                self.assertRegex(
                    production_fingerprint["environment"]["environment_lock_hash"],
                    r"^sha256:[0-9a-f]{64}$",
                )
                suffix, final_sequence, suffix_terminal = self._sse(
                    second, csrf, task_id, prefix_last
                )
                events = prefix + suffix
                self.assertTrue(suffix_terminal)
                self.assertEqual([event["sequence"] for event in events], list(range(1, final_sequence + 1)))
                serialized_events = json.dumps(events, separators=(",", ":"))
                for private_value in ("intent_id", "attempt_id", "/root/"):
                    self.assertNotIn(private_value, serialized_events)

                timeline = self._timeline(database, task_id)
                self.assertLess(timeline["data_check"]["Succeeded"], timeline["forward"]["Running"])
                self.assertLess(timeline["data_check"]["Succeeded"], timeline["quality_check"]["Running"])
                self.assertLess(timeline["forward"]["Running"], timeline["quality_check"]["Succeeded"])
                self.assertLess(timeline["quality_check"]["Running"], timeline["forward"]["Succeeded"])
                self.assertLess(timeline["forward"]["Succeeded"], timeline["fwi"]["Running"])
                self.assertLess(timeline["quality_check"]["Succeeded"], timeline["fwi"]["Running"])
                self.assertLess(timeline["fwi"]["Succeeded"], timeline["result_check"]["Running"])

                artifacts = self._artifact_evidence(second, csrf, task_id, plan_hash)
                dataset_hash = self._node(terminal, "data_check")["lineage"][
                    "dataset_roots"
                ][0]["content_hash"]
                direct_lineage = {
                    "data_check": [dataset_hash],
                    "forward": [
                        dataset_hash,
                        self._output_hash(
                            self._node(terminal, "data_check"), "inverted_model"
                        ),
                    ],
                    "quality_check": [
                        dataset_hash,
                        self._output_hash(
                            self._node(terminal, "data_check"), "loss"
                        ),
                    ],
                    "fwi": [
                        dataset_hash,
                        self._output_hash(
                            self._node(terminal, "forward"),
                            "shot_gathers_figure",
                        ),
                        self._output_hash(
                            self._node(terminal, "quality_check"),
                            "model_error_figure",
                        ),
                    ],
                    "result_check": [
                        dataset_hash,
                        self._output_hash(
                            self._node(terminal, "fwi"), "inverted_model"
                        ),
                        self._output_hash(self._node(terminal, "fwi"), "loss"),
                    ],
                }
                lineage = []
                for node_id in NODES:
                    node = self._node(terminal, node_id)
                    self.assertRegex(node["lineage"]["document_hash"], r"^sha256:[0-9a-f]{64}$")
                    self.assertTrue(node["lineage"]["dataset_roots"])
                    self.assertEqual(
                        node["lineage"]["direct_artifact_hashes"],
                        direct_lineage[node_id],
                    )
                    self.assertTrue(node["outputs"])
                    lineage.append(
                        {
                            "node_id": node_id,
                            "document_hash": node["lineage"]["document_hash"],
                            "direct_artifact_hashes": node["lineage"][
                                "direct_artifact_hashes"
                            ],
                            "output_hashes": sorted(value["content_hash"] for value in node["outputs"]),
                        }
                    )

                # A second HTTP-created Workflow with the same semantic Plan
                # must reuse every clean, production-Worker result without a
                # dispatch intent or Adapter launch.  The plan instance/hash
                # remains distinct; cache identity intentionally excludes it.
                cache_task_id, cache_plan_hash = self._create_approved(
                    second,
                    csrf,
                    device="cpu",
                    seed=17,
                    suffix="cache-hit",
                )
                self.assertNotEqual(cache_plan_hash, plan_hash)
                self.assertTrue(second.supervisor.start())
                try:
                    cache_terminal = self._wait(
                        second,
                        csrf,
                        cache_task_id,
                        lambda task: task["status"] in TERMINAL,
                        300,
                    )
                finally:
                    self.assertTrue(second.supervisor.stop())
                self.assertEqual(cache_terminal["status"], "Succeeded")
                self.assertTrue(
                    all(
                        self._node(cache_terminal, node_id)["cache"]["state"] == "hit"
                        for node_id in NODES
                    )
                )
                self.assertTrue(
                    all(recorder.count(cache_task_id, node_id) == (0, 0) for node_id in NODES)
                )
                self.assertTrue(
                    all(
                        self._durable_dispatch_count(database, cache_task_id, node_id) == 0
                        for node_id in NODES
                    )
                )

                # Changing one approved parameter changes every fixed stage's
                # normalized contract.  It therefore misses and executes real
                # Workers instead of reusing the previous Workflow.
                miss_task_id, miss_plan_hash = self._create_approved(
                    second,
                    csrf,
                    device="cpu",
                    seed=17,
                    suffix="cache-miss",
                    iterations=2,
                )
                self.assertNotEqual(miss_plan_hash, plan_hash)
                self.assertTrue(second.supervisor.start())
                try:
                    miss_terminal = self._wait(
                        second,
                        csrf,
                        miss_task_id,
                        lambda task: task["status"] in TERMINAL,
                        1200,
                    )
                finally:
                    self.assertTrue(second.supervisor.stop())
                self.assertEqual(miss_terminal["status"], "Succeeded")
                self.assertTrue(
                    all(
                        self._node(miss_terminal, node_id)["cache"]["state"] == "miss"
                        for node_id in NODES
                    )
                )
                self.assertTrue(
                    all(recorder.count(miss_task_id, node_id) == (1, 1) for node_id in NODES)
                )

                # Corrupt one physical source artifact after the legitimate
                # hit.  A new exact Plan still resolves the semantic key, but
                # the Adapter's nofollow bytes/hash verification must reject
                # reuse before any target dispatch is created.
                source_job_id = self._worker_job_id(database, task_id, "data_check")
                tampered_path = run_root / source_job_id / "models" / "inverted.npy"
                tampered = bytearray(tampered_path.read_bytes())
                self.assertTrue(tampered)
                tampered[-1] ^= 0x01
                tampered_path.write_bytes(tampered)
                tamper_task_id, _ = self._create_approved(
                    second,
                    csrf,
                    device="cpu",
                    seed=17,
                    suffix="cache-tamper",
                )
                self.assertTrue(second.supervisor.start())
                try:
                    tamper_task, tamper_code = self._wait_for_task_failure(
                        second, csrf, tamper_task_id, 120
                    )
                finally:
                    self.assertTrue(second.supervisor.stop())
                self.assertEqual(tamper_code, "ADAPTER_ARTIFACT_INVALID")
                self.assertEqual(self._node(tamper_task, "data_check")["status"], "Pending")
                self.assertEqual(recorder.count(tamper_task_id, "data_check"), (0, 0))
                self.assertEqual(
                    self._durable_dispatch_count(database, tamper_task_id, "data_check"), 0
                )

                evidence = {
                    "schema_version": "1.0.0",
                    "mode": "cpu",
                    "task_id": task_id,
                    "plan_hash": plan_hash,
                    "node_timeline_us": timeline,
                    "restart": {
                        "after_node": "data_check",
                        "dispatch_before": dispatch_before,
                        "dispatch_after": recorder.count(task_id, "data_check"),
                        "durable_dispatch_before": durable_before,
                        "durable_dispatch_after": self._durable_dispatch_count(database, task_id, "data_check"),
                        "artifact_hashes_before": a_before,
                        "artifact_hashes_after": a_after,
                    },
                    "sse": {
                        "prefix_last_sequence": prefix_last,
                        "terminal_sequence": final_sequence,
                        "continuous": True,
                    },
                    "artifacts": artifacts,
                    "runtime_lineage": lineage,
                    "cache": {
                        "production_fingerprint": {
                            "provenance_mode": production_fingerprint[
                                "provenance_mode"
                            ],
                            "source_identity_complete": production_fingerprint[
                                "source"
                            ]["identity_complete"],
                            "source_dirty": production_fingerprint["source"][
                                "dirty"
                            ],
                            "source_git_commit": production_fingerprint["source"][
                                "git_commit"
                            ],
                            "source_git_tree": production_fingerprint["source"][
                                "git_tree"
                            ],
                            "environment_lock_hash": production_fingerprint[
                                "environment"
                            ]["environment_lock_hash"],
                        },
                        "hit": {
                            "task_id": cache_task_id,
                            "node_states": {
                                node_id: self._node(cache_terminal, node_id)["cache"]["state"]
                                for node_id in NODES
                            },
                            "successful_dispatches": {
                                node_id: recorder.count(cache_task_id, node_id)[1]
                                for node_id in NODES
                            },
                        },
                        "parameter_change_miss": {
                            "task_id": miss_task_id,
                            "iterations": 2,
                            "node_states": {
                                node_id: self._node(miss_terminal, node_id)["cache"]["state"]
                                for node_id in NODES
                            },
                        },
                        "tamper": {
                            "task_id": tamper_task_id,
                            "failure_code": tamper_code,
                            "target_dispatches": recorder.count(
                                tamper_task_id, "data_check"
                            )[1],
                        },
                    },
                    "gpu": {"max_active": 0},
                }
                print("GUIDED_RECIPE_E2E_EVIDENCE=" + json.dumps(evidence, sort_keys=True, separators=(",", ":")))

    @unittest.skipUnless(os.environ.get(CUDA_GATE) == "1", f"set {CUDA_GATE}=1 to own one real CUDA device")
    def test_cuda_two_workflows_have_one_real_active_worker(self) -> None:
        source_commit, source_tree = self._clean_source_identity()
        probe = subprocess.run(
            [
                str(DEFAULT_WORKER_PYTHON),
                "-c",
                "import torch,sys;sys.exit(0 if torch.cuda.is_available() and torch.cuda.device_count()==1 else 1)",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode:
            self.skipTest("the fixed Worker Python does not expose exactly one CUDA device")
        recorder = _Recorder()
        with tempfile.TemporaryDirectory(prefix="guided-recipe-cuda-e2e-") as private:
            root = Path(private)
            run_root, state_root = root / "runs", root / "state"
            run_root.mkdir(mode=0o700)
            state_root.mkdir(mode=0o700)
            database = state_root / "tasks.sqlite3"
            environment = {
                "FWI_RUN_ROOT": str(run_root),
                "SCIENTIFIC_RUNTIME_DB_PATH": str(database),
                "AGENT_CORS_ORIGIN": ORIGIN,
                "CUDA_VISIBLE_DEVICES": "0",
            }
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
                serve, "HOST", "127.0.0.1"
            ), mock.patch.object(serve, "PORT", 8080):
                runtime, csrf = self._compose(recorder)
                first = self._create_approved(runtime, csrf, device="cuda", seed=101, suffix="cuda-a")
                second = self._create_approved(runtime, csrf, device="cuda", seed=202, suffix="cuda-b")
                self.assertTrue(runtime.supervisor.start())
                try:
                    terminal = [
                        self._wait(runtime, csrf, task_id, lambda task: task["status"] in TERMINAL, 1800)
                        for task_id, _ in (first, second)
                    ]
                finally:
                    self.assertTrue(runtime.supervisor.stop())
                self.assertEqual([task["status"] for task in terminal], ["Succeeded", "Succeeded"])
                self.assertEqual(recorder.max_cuda_active, 1)
                for task_id, _ in (first, second):
                    for node_id in NODES:
                        fingerprint = self._node_fingerprint(database, task_id, node_id)
                        self.assertEqual(
                            fingerprint["source"]["git_commit"], source_commit
                        )
                        self.assertEqual(
                            fingerprint["source"]["git_tree"], source_tree
                        )
                        self.assertIs(fingerprint["source"]["dirty"], False)
                evidence = {
                    "schema_version": "1.0.0",
                    "mode": "cuda",
                    "task_ids": [first[0], second[0]],
                    "plan_hashes": [first[1], second[1]],
                    "terminal_statuses": [task["status"] for task in terminal],
                    "gpu": {"max_active": recorder.max_cuda_active},
                    "source": {
                        "git_commit": source_commit,
                        "git_tree": source_tree,
                        "dirty": False,
                    },
                    "successful_dispatches": {
                        task_id: {node: recorder.count(task_id, node)[1] for node in NODES}
                        for task_id, _ in (first, second)
                    },
                }
                print("GUIDED_RECIPE_E2E_EVIDENCE=" + json.dumps(evidence, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
