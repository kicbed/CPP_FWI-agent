#!/usr/bin/env python3
"""Transport and security tests for the Guided Workbench HTTP facade."""

from __future__ import annotations

import copy
import json
import unittest
from email.message import Message

from scientific_runtime.workbench_service import GuidedWorkbench
from web.workbench_api import (
    API_PREFIX,
    APIResponse,
    MAX_JSON_BYTES,
    SSEEventStream,
    WorkbenchAPI,
)


CSRF = "csrf-token-0123456789"
HOST = "127.0.0.1:8080"
ORIGIN = "http://127.0.0.1:8080"


class WorkbenchValidationError(RuntimeError):
    def __init__(self, code: str = "INVALID_FORM"):
        self.code = code
        super().__init__("/root/private/request.json: invalid")


class WorkbenchNotFound(RuntimeError):
    pass


class WorkbenchConflict(RuntimeError):
    def __init__(self, code: str = "IDEMPOTENCY_CONFLICT"):
        self.code = code
        super().__init__("conflict at /root/private/runtime.sqlite")


class WorkbenchRuntimeError(RuntimeError):
    def __init__(self, code: str = "ADAPTER_UNAVAILABLE"):
        self.code = code
        super().__init__("worker failed below /root/fwi-runs/private")


class FakeApplication:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: Exception | None = None
        self.artifact_media_type = "text/csv"
        self.artifact_content = b"iteration,loss\n0,1.0\n"

    def _call(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.error is not None:
            raise self.error
        return {"operation": name}

    def session_capabilities(self):
        return self._call("session_capabilities") | {
            "mode": "guided",
            "algorithm": {
                "id": "deepwave.acoustic_fwi",
                "version": "1.6.0",
            },
            "features": {"checkpoint_wait_resume": True},
            "capabilities": {
                "checkpoint_resume": {
                    "automatic": True,
                    "browser_mutation": False,
                    "same_attempt": True,
                    "capacity_released_while_waiting": False,
                }
            },
        }

    def list_catalog(self):
        return self._call("list_catalog") | {"datasets": [], "algorithms": []}

    def create_task(self, form, key):
        return self._call("create_task", form, key) | {"task_id": "task-1"}

    def list_tasks(self, *, cursor=None, limit=20, view="active"):
        self._call("list_tasks", cursor=cursor, limit=limit, view=view)
        return {"tasks": [], "next_cursor": None}

    def get_task(self, task_id, *, refresh=True):
        return self._call("get_task", task_id, refresh=refresh) | {"task_id": task_id}

    def revise_task(self, task_id, expected_revision, form, key):
        return self._call("revise_task", task_id, expected_revision, form, key)

    def approve_and_submit(self, task_id, plan_hash, key):
        return self._call("approve_and_submit", task_id, plan_hash, key)

    def abandon_task(self, task_id, key):
        return self._call("abandon_task", task_id, key)

    def cancel_task(self, task_id, key, reason):
        return self._call("cancel_task", task_id, key, reason)

    def trash_task(self, task_id, expected_visibility_revision, key):
        return self._call(
            "trash_task", task_id, expected_visibility_revision, key
        )

    def restore_task(self, task_id, expected_visibility_revision, key):
        return self._call(
            "restore_task", task_id, expected_visibility_revision, key
        )

    def purge_task(self, task_id, expected_visibility_revision, key):
        return self._call(
            "purge_task", task_id, expected_visibility_revision, key
        )

    def list_events(self, task_id, *, after_sequence=0, limit=100):
        self._call("list_events", task_id, after_sequence=after_sequence, limit=limit)
        return [{"sequence": after_sequence + 1}]

    def list_artifacts(self, task_id):
        self._call("list_artifacts", task_id)
        return [{"artifact_id": "loss"}]

    def read_artifact(self, task_id, artifact_id):
        self._call("read_artifact", task_id, artifact_id)
        content = self.artifact_content
        return (
            {
                "task_id": task_id,
                "artifact_id": artifact_id,
                "media_type": self.artifact_media_type,
                "size_bytes": len(content),
            },
            content,
        )


def guided_form() -> dict:
    return {
        "goal": "Run the bounded inversion smoke workflow",
        "dataset_id": "marmousi_94_288",
        "dataset_version": "1.0.0",
        "preset": "fwi_smoke",
        "device": "cpu",
        "iterations": 1,
        "seed": 7,
        "optimizer": "adam",
        "learning_rate": "10",
    }


def recipe_guided_form() -> dict:
    return guided_form() | {
        "recipe_id": "forward_qc_fwi",
        "recipe_version": "1.0.0",
    }


class WorkbenchAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.application = FakeApplication()
        self.api = WorkbenchAPI(
            self.application,
            CSRF,
            allowed_hosts={HOST, "localhost:8080"},
            allowed_origins={ORIGIN},
        )

    def decode(self, response):
        self.assertEqual(response.headers["Content-Length"], str(len(response.body)))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertFalse(
            any(name.lower().startswith("access-control-") for name in response.headers)
        )
        return json.loads(response.body.decode("utf-8"))

    def get_headers(self, *, csrf: bool = True):
        headers = {"Host": HOST}
        if csrf:
            headers["X-Workbench-CSRF"] = CSRF
        return headers

    def mutation_headers(self, body: bytes, **updates):
        headers = {
            "Host": HOST,
            "X-Workbench-CSRF": CSRF,
            "Origin": ORIGIN,
            "Idempotency-Key": "browser-mutation-0001",
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        headers.update(updates)
        return headers

    def mutation(self, method: str, path: str, payload: dict, **header_updates):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.api.dispatch(
            method,
            path,
            self.mutation_headers(body, **header_updates),
            body,
        )

    @staticmethod
    def run_event(task_id: str, sequence: int, status: str = "Running"):
        return {
            "schema_version": "1.0.0",
            "event_id": f"event-{sequence}",
            "sequence": sequence,
            "task_id": task_id,
            "node_id": "invert",
            "event_type": "node_succeeded" if status == "Succeeded" else "node_progress",
            "task_status": status,
            "occurred_at": "2026-07-17T12:00:00Z",
            "fingerprint": {},
            "extensions": {},
        }

    def test_event_stream_requires_transport_headers_and_replays_exact_sequence(self):
        task_id = "task-stream-1"
        events = [
            self.run_event(task_id, 8),
            self.run_event(task_id, 9, "Succeeded"),
        ]

        class StreamApplication(FakeApplication):
            def list_events(inner_self, requested_task_id, *, after_sequence=0, limit=100):
                inner_self._call(
                    "list_events",
                    requested_task_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
                return [
                    copy.deepcopy(event)
                    for event in events
                    if event["sequence"] > after_sequence
                ][:limit]

        application = StreamApplication()
        api = WorkbenchAPI(
            application,
            CSRF,
            allowed_hosts={HOST},
            allowed_origins={ORIGIN},
        )
        headers = self.get_headers() | {"Accept": "text/event-stream"}
        stream = api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{task_id}/events/stream?after_sequence=7",
            headers,
            b"",
        )
        self.assertIsInstance(stream, SSEEventStream)
        frames = stream.next_batch()
        self.assertEqual(len(frames), 2)
        self.assertTrue(frames[0].startswith(b"id: 8\nevent: run_event\ndata: "))
        self.assertTrue(frames[1].startswith(b"id: 9\nevent: run_event\ndata: "))
        first = json.loads(frames[0].split(b"data: ", 1)[1].strip())
        self.assertEqual(first["task_id"], task_id)
        self.assertEqual(first["sequence"], 8)
        self.assertTrue(stream.terminal)
        self.assertEqual(stream.after_sequence, 9)
        self.assertEqual(stream.next_batch(), ())
        self.assertEqual(
            application.calls[0],
            ("list_events", (task_id,), {"after_sequence": 7, "limit": 100}),
        )

        for path, request_headers, expected_status, expected_code in (
            (
                f"{API_PREFIX}/tasks/{task_id}/events/stream",
                {"Host": HOST, "Accept": "text/event-stream"},
                403,
                "CSRF_FORBIDDEN",
            ),
            (
                f"{API_PREFIX}/tasks/{task_id}/events/stream",
                self.get_headers(),
                406,
                "EVENT_STREAM_REQUIRED",
            ),
            (
                f"{API_PREFIX}/tasks/{task_id}/events/stream?limit=10",
                headers,
                400,
                "INVALID_QUERY",
            ),
        ):
            with self.subTest(path=path, expected_code=expected_code):
                response = api.open_event_stream("GET", path, request_headers, b"")
                self.assertIsInstance(response, APIResponse)
                self.assertEqual(response.status, expected_status)
                self.assertEqual(self.decode(response)["error"]["code"], expected_code)

    def test_event_stream_fails_before_headers_for_scope_and_sequence_errors(self):
        task_id = "task-stream-invalid"

        self.application.error = WorkbenchNotFound("private task path")
        response = self.api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{task_id}/events/stream",
            self.get_headers() | {"Accept": "text/event-stream"},
            b"",
        )
        self.assertIsInstance(response, APIResponse)
        self.assertEqual(response.status, 404)
        self.assertNotIn(b"private", response.body)

        self.application.error = None

        def wrong_sequence(_task_id, *, after_sequence=0, limit=100):
            return [self.run_event(task_id, after_sequence + 2)]

        self.application.list_events = wrong_sequence
        response = self.api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{task_id}/events/stream?after_sequence=3",
            self.get_headers() | {"Accept": "text/event-stream"},
            b"",
        )
        self.assertIsInstance(response, APIResponse)
        self.assertEqual(response.status, 500)
        self.assertEqual(self.decode(response)["error"]["code"], "INTERNAL_ERROR")

    def test_event_stream_pages_backlog_and_resumes_after_last_durable_id(self):
        task_id = "task-stream-backlog"
        events = [
            self.run_event(
                task_id,
                sequence,
                "Succeeded" if sequence == 105 else "Running",
            )
            for sequence in range(1, 106)
        ]

        class BacklogApplication(FakeApplication):
            def list_events(inner_self, requested_task_id, *, after_sequence=0, limit=100):
                inner_self._call(
                    "list_events",
                    requested_task_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
                return [
                    copy.deepcopy(event)
                    for event in events
                    if event["sequence"] > after_sequence
                ][:limit]

        application = BacklogApplication()
        api = WorkbenchAPI(
            application,
            CSRF,
            allowed_hosts={HOST},
            allowed_origins={ORIGIN},
        )
        headers = self.get_headers() | {"Accept": "text/event-stream"}
        path = f"{API_PREFIX}/tasks/{task_id}/events/stream"
        stream = api.open_event_stream("GET", path, headers, b"")
        self.assertIsInstance(stream, SSEEventStream)
        first = stream.next_batch()
        second = stream.next_batch()
        self.assertEqual((len(first), len(second)), (100, 5))
        self.assertFalse(any(b"id: 101\n" in frame for frame in first))
        self.assertTrue(second[0].startswith(b"id: 101\n"))
        self.assertTrue(second[-1].startswith(b"id: 105\n"))
        self.assertTrue(stream.terminal)
        self.assertEqual(
            [call[2]["after_sequence"] for call in application.calls],
            [0, 100],
        )

        resumed = api.open_event_stream(
            "GET", path + "?after_sequence=100", headers, b""
        )
        self.assertIsInstance(resumed, SSEEventStream)
        resumed_frames = resumed.next_batch()
        self.assertEqual(len(resumed_frames), 5)
        self.assertEqual(
            [int(frame.split(b"\n", 1)[0].split(b": ", 1)[1]) for frame in resumed_frames],
            [101, 102, 103, 104, 105],
        )

    def test_session_is_the_only_csrf_free_endpoint_and_returns_transport_token(self):
        response = self.api.dispatch(
            "GET", f"{API_PREFIX}/session", {"Host": HOST}, b""
        )
        self.assertEqual(response.status, 200)
        payload = self.decode(response)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["csrf_token"], CSRF)
        self.assertEqual(payload["data"]["mode"], "guided")
        self.assertEqual(
            payload["data"]["algorithm"],
            {"id": "deepwave.acoustic_fwi", "version": "1.6.0"},
        )
        self.assertTrue(
            payload["data"]["features"]["checkpoint_wait_resume"]
        )
        self.assertEqual(
            payload["data"]["capabilities"]["checkpoint_resume"],
            {
                "automatic": True,
                "browser_mutation": False,
                "same_attempt": True,
                "capacity_released_while_waiting": False,
            },
        )

        response = self.api.dispatch(
            "GET", f"{API_PREFIX}/catalog", {"Host": HOST}, b""
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(self.decode(response)["error"]["code"], "CSRF_FORBIDDEN")

        before_resume = len(self.application.calls)
        response = self.mutation(
            "POST",
            f"{API_PREFIX}/tasks/task-1/resume",
            {},
        )
        self.assertEqual(response.status, 404)
        self.assertEqual(self.decode(response)["error"]["code"], "NOT_FOUND")
        self.assertEqual(len(self.application.calls), before_resume)

        http_message = Message()
        http_message["Host"] = HOST
        response = self.api.dispatch(
            "GET", f"{API_PREFIX}/session", http_message, b""
        )
        self.assertEqual(response.status, 200)

    def test_host_allowlist_is_required_and_origin_configuration_is_loopback_only(self):
        for headers in ({}, {"Host": "attacker.example"}, {"Host": f"{HOST} "}):
            with self.subTest(headers=headers):
                response = self.api.dispatch("GET", f"{API_PREFIX}/session", headers, b"")
                self.assertEqual(response.status, 403)

        with self.assertRaises(ValueError):
            WorkbenchAPI(
                self.application,
                CSRF,
                allowed_hosts={HOST},
                allowed_origins={"https://example.com"},
            )
        with self.assertRaises(ValueError):
            WorkbenchAPI(
                self.application,
                CSRF,
                allowed_hosts={"0.0.0.0:8080"},
                allowed_origins={ORIGIN},
            )

    def test_preflight_rejects_unsafe_metadata_before_any_body_is_read(self):
        form = guided_form()
        encoded = json.dumps(form, separators=(",", ":")).encode("utf-8")
        valid = self.mutation_headers(encoded)

        response = self.api.preflight(
            "POST",
            f"{API_PREFIX}/tasks",
            valid | {"Content-Length": str(MAX_JSON_BYTES + 1)},
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 413)

        response = self.api.preflight(
            "POST", f"{API_PREFIX}/tasks", valid | {"Host": "attacker.example"}
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 403)

        response = self.api.preflight(
            "POST",
            f"{API_PREFIX}/tasks",
            valid | {"X-Workbench-CSRF": "wrong-token"},
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 403)

        response = self.api.preflight(
            "POST",
            f"{API_PREFIX}/tasks",
            valid | {"Origin": "http://localhost:8080"},
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 403)

        response = self.api.preflight(
            "POST",
            f"{API_PREFIX}/tasks",
            valid | {"Transfer-Encoding": "chunked"},
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.application.calls, [])

    def test_default_http_port_uses_browser_canonical_host_and_origin(self):
        api = WorkbenchAPI(
            self.application,
            CSRF,
            allowed_hosts={"127.0.0.1:80"},
            allowed_origins={"http://127.0.0.1:80"},
        )
        response = api.dispatch(
            "GET",
            f"{API_PREFIX}/session",
            {"Host": "127.0.0.1"},
            b"",
        )
        self.assertEqual(response.status, 200)

        body = json.dumps(guided_form(), separators=(",", ":")).encode("utf-8")
        response = api.dispatch(
            "POST",
            f"{API_PREFIX}/tasks",
            {
                "Host": "127.0.0.1",
                "X-Workbench-CSRF": CSRF,
                "Origin": "http://127.0.0.1",
                "Idempotency-Key": "default-port-create",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            body,
        )
        self.assertEqual(response.status, 201)

    def test_flat_create_and_revision_forms_are_forwarded_without_request_identity(self):
        form = guided_form()
        response = self.mutation("POST", f"{API_PREFIX}/tasks", form)
        self.assertEqual(response.status, 201)
        self.assertEqual(self.decode(response)["data"]["task_id"], "task-1")
        call = self.application.calls[-1]
        self.assertEqual(call[0], "create_task")
        self.assertEqual(call[1][0], form)
        self.assertEqual(call[1][1], "browser-mutation-0001")
        self.assertNotIn("project_id", call[1][0])
        self.assertNotIn("principal_id", call[1][0])

        revised = dict(form, expected_revision=1, iterations=2)
        response = self.mutation(
            "PUT", f"{API_PREFIX}/tasks/task-1/draft", revised
        )
        self.assertEqual(response.status, 200)
        call = self.application.calls[-1]
        self.assertEqual(call[0], "revise_task")
        self.assertEqual(call[1][0:2], ("task-1", 1))
        self.assertNotIn("expected_revision", call[1][2])
        self.assertEqual(call[1][2]["iterations"], 2)

        legacy = guided_form()
        legacy.pop("optimizer")
        legacy.pop("learning_rate")
        response = self.mutation("POST", f"{API_PREFIX}/tasks", legacy)
        self.assertEqual(response.status, 201)
        legacy_call = self.application.calls[-1]
        self.assertEqual(legacy_call[0], "create_task")
        self.assertEqual(legacy_call[1][0], legacy)

        legacy_revision = dict(legacy, expected_revision=1, iterations=3)
        response = self.mutation(
            "PUT", f"{API_PREFIX}/tasks/task-1/draft", legacy_revision
        )
        self.assertEqual(response.status, 200)
        legacy_call = self.application.calls[-1]
        self.assertEqual(legacy_call[0], "revise_task")
        self.assertEqual(legacy_call[1][0:2], ("task-1", 1))
        self.assertEqual(legacy_call[1][2], dict(legacy, iterations=3))

        recipe = recipe_guided_form()
        response = self.mutation("POST", f"{API_PREFIX}/tasks", recipe)
        self.assertEqual(response.status, 201)
        recipe_call = self.application.calls[-1]
        self.assertEqual(recipe_call[0], "create_task")
        self.assertEqual(recipe_call[1][0], recipe)

        recipe_revision = dict(recipe, expected_revision=1, iterations=2)
        response = self.mutation(
            "PUT", f"{API_PREFIX}/tasks/task-1/draft", recipe_revision
        )
        self.assertEqual(response.status, 200)
        recipe_call = self.application.calls[-1]
        self.assertEqual(recipe_call[0], "revise_task")
        self.assertEqual(recipe_call[1][2], dict(recipe, iterations=2))

        partial_optimizer = dict(legacy, optimizer="adam")
        response = self.mutation(
            "POST", f"{API_PREFIX}/tasks", partial_optimizer
        )
        self.assertEqual(response.status, 422)
        self.assertEqual(self.decode(response)["error"]["code"], "INVALID_FORM")

        partial_recipe = dict(form, recipe_id="forward_qc_fwi")
        response = self.mutation("POST", f"{API_PREFIX}/tasks", partial_recipe)
        self.assertEqual(response.status, 422)
        self.assertEqual(self.decode(response)["error"]["code"], "INVALID_FORM")

        smuggled = dict(form, project_id="other-project")
        response = self.mutation("POST", f"{API_PREFIX}/tasks", smuggled)
        self.assertEqual(response.status, 422)
        self.assertEqual(self.decode(response)["error"]["code"], "INVALID_FORM")

    def test_task_collection_get_is_csrf_scoped_paginated_and_body_free(self):
        cursor = "v1_dGFzay0x"
        response = self.api.dispatch(
            "GET",
            f"{API_PREFIX}/tasks?limit=2&cursor={cursor}",
            self.get_headers(),
            b"",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(self.decode(response)["data"], {"tasks": [], "next_cursor": None})
        self.assertEqual(
            self.application.calls[-1],
            (
                "list_tasks",
                (),
                {"cursor": cursor, "limit": 2, "view": "active"},
            ),
        )

        response = self.api.dispatch(
            "GET",
            f"{API_PREFIX}/tasks?limit=2&view=trash",
            self.get_headers(),
            b"",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            (
                "list_tasks",
                (),
                {"cursor": None, "limit": 2, "view": "trash"},
            ),
        )

        response = self.api.dispatch(
            "GET", f"{API_PREFIX}/tasks", {"Host": HOST}, b""
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(self.decode(response)["error"]["code"], "CSRF_FORBIDDEN")

        response = self.api.dispatch(
            "GET",
            f"{API_PREFIX}/tasks",
            self.get_headers() | {"Content-Length": "2"},
            b"{}",
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.decode(response)["error"]["code"], "BODY_FORBIDDEN")

        body = json.dumps(guided_form(), separators=(",", ":")).encode("utf-8")
        response = self.api.dispatch(
            "POST",
            f"{API_PREFIX}/tasks?limit=2",
            self.mutation_headers(body),
            body,
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.decode(response)["error"]["code"], "INVALID_QUERY")

    def test_task_collection_query_and_methods_fail_closed(self):
        invalid_targets = (
            f"{API_PREFIX}/tasks?cursor=bad",
            f"{API_PREFIX}/tasks?cursor=v1_!!",
            f"{API_PREFIX}/tasks?cursor=v1_dGFzay0x=",
            f"{API_PREFIX}/tasks?cursor=v1_dGFzay0x&cursor=v1_dGFzay0y",
            f"{API_PREFIX}/tasks?limit=0",
            f"{API_PREFIX}/tasks?limit=51",
            f"{API_PREFIX}/tasks?limit=01",
            f"{API_PREFIX}/tasks?after_sequence=1",
            f"{API_PREFIX}/tasks?project_id=other",
            f"{API_PREFIX}/tasks?view=deleted",
            f"{API_PREFIX}/tasks?view=active&view=trash",
        )
        for target in invalid_targets:
            with self.subTest(target=target):
                response = self.api.dispatch("GET", target, self.get_headers(), b"")
                self.assertEqual(response.status, 400)
                self.assertEqual(self.decode(response)["error"]["code"], "INVALID_QUERY")

        response = self.api.dispatch(
            "PUT", f"{API_PREFIX}/tasks", self.get_headers(), b""
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers["Allow"], "GET, POST")

    def test_task_approval_abandon_status_events_and_artifact_routes(self):
        task_path = f"{API_PREFIX}/tasks/task-1"
        response = self.api.dispatch("GET", task_path, self.get_headers(), b"")
        self.assertEqual(response.status, 200)
        self.assertEqual(self.application.calls[-1], ("get_task", ("task-1",), {"refresh": True}))

        plan_hash = "sha256:" + "a" * 64
        response = self.mutation("POST", f"{task_path}/approve", {"plan_hash": plan_hash})
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            ("approve_and_submit", ("task-1", plan_hash, "browser-mutation-0001"), {}),
        )

        response = self.mutation("POST", f"{task_path}/abandon", {})
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            ("abandon_task", ("task-1", "browser-mutation-0001"), {}),
        )

        response = self.mutation(
            "POST", f"{task_path}/cancel", {"reason": "user_requested"}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            (
                "cancel_task",
                ("task-1", "browser-mutation-0001", "user_requested"),
                {},
            ),
        )

        for payload in (
            {},
            {"reason": "wall_time_exceeded"},
            {"reason": True},
            {"reason": "user_requested", "task_id": "other"},
        ):
            with self.subTest(cancel_payload=payload):
                previous_calls = len(self.application.calls)
                response = self.mutation("POST", f"{task_path}/cancel", payload)
                self.assertEqual(response.status, 422)
                self.assertEqual(
                    self.decode(response)["error"]["code"], "INVALID_CANCEL"
                )
                self.assertEqual(len(self.application.calls), previous_calls)

        previous_calls = len(self.application.calls)
        response = self.api.dispatch(
            "GET", f"{task_path}/cancel", self.get_headers(), b""
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers["Allow"], "POST")
        self.assertEqual(len(self.application.calls), previous_calls)

        for absent_mutation in ("timeout", "reconcile", "reconciliation", "retry"):
            with self.subTest(absent_mutation=absent_mutation):
                previous_calls = len(self.application.calls)
                response = self.mutation(
                    "POST", f"{task_path}/{absent_mutation}", {}
                )
                self.assertEqual(response.status, 404)
                self.assertEqual(
                    self.decode(response)["error"]["code"], "NOT_FOUND"
                )
                self.assertEqual(len(self.application.calls), previous_calls)

        response = self.mutation(
            "POST", f"{task_path}/trash", {"expected_visibility_revision": 0}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            ("trash_task", ("task-1", 0, "browser-mutation-0001"), {}),
        )

        response = self.mutation(
            "POST", f"{task_path}/restore", {"expected_visibility_revision": 1}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            ("restore_task", ("task-1", 1, "browser-mutation-0001"), {}),
        )

        response = self.mutation(
            "POST",
            f"{task_path}/purge",
            {
                "expected_visibility_revision": 1,
                "confirmation_task_id": "task-1",
            },
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            ("purge_task", ("task-1", 1, "browser-mutation-0001"), {}),
        )

        for endpoint, payload in (
            ("trash", {}),
            ("trash", {"expected_visibility_revision": -1}),
            ("restore", {"expected_visibility_revision": True}),
            (
                "restore",
                {"expected_visibility_revision": 1, "project_id": "other"},
            ),
        ):
            with self.subTest(endpoint=endpoint, payload=payload):
                response = self.mutation(
                    "POST", f"{task_path}/{endpoint}", payload
                )
                self.assertEqual(response.status, 422)
                self.assertEqual(
                    self.decode(response)["error"]["code"],
                    "INVALID_VISIBILITY",
                )

        for payload in (
            {},
            {"expected_visibility_revision": 1},
            {
                "expected_visibility_revision": -1,
                "confirmation_task_id": "task-1",
            },
            {
                "expected_visibility_revision": True,
                "confirmation_task_id": "task-1",
            },
            {
                "expected_visibility_revision": 1,
                "confirmation_task_id": "other-task",
            },
            {
                "expected_visibility_revision": 1,
                "confirmation_task_id": "task-1",
                "project_id": "other",
            },
        ):
            with self.subTest(purge_payload=payload):
                previous_calls = len(self.application.calls)
                response = self.mutation("POST", f"{task_path}/purge", payload)
                self.assertEqual(response.status, 422)
                self.assertEqual(
                    self.decode(response)["error"]["code"], "INVALID_PURGE"
                )
                self.assertEqual(len(self.application.calls), previous_calls)

        response = self.api.dispatch(
            "GET",
            f"{task_path}/events?after_sequence=7&limit=25",
            self.get_headers(),
            b"",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.application.calls[-1],
            ("list_events", ("task-1",), {"after_sequence": 7, "limit": 25}),
        )
        self.assertEqual(self.decode(response)["data"], {"events": [{"sequence": 8}]})

        response = self.api.dispatch(
            "GET", f"{task_path}/artifacts", self.get_headers(), b""
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.decode(response)["data"],
            {"artifacts": [{"artifact_id": "loss"}]},
        )

    def test_http_events_do_not_serialize_retry_exhaustion_private_proof(self):
        private_extension = {
            "intent_id": "intent-http-private-exhaustion",
            "attempt_id": "attempt-" + "a" * 32,
            "attempt_number": 2,
            "observation_sequence": 4,
            "evidence_hash": "sha256:" + "b" * 64,
            "private_schema_version": "1.2.0",
            "private_proof_hash": "sha256:" + "c" * 64,
            "failure_kind": "pre_running_launch_failure",
            "max_attempts": 2,
            "private_path": "/root/private/http-retry",
        }
        worker_exit_extension = {
            "intent_id": "intent-http-private-worker-exit",
            "attempt_number": 2,
            "previous_attempt_id": "attempt-http-private-worker-exit",
            "previous_observation_sequence": 5,
            "evidence_hash": "sha256:" + "d" * 64,
            "private_schema_version": "1.1.0",
            "private_proof_hash": "sha256:" + "e" * 64,
            "failure_kind": "worker_exit",
            "max_attempts": 2,
            "source_outcome_document_hash": "sha256:" + "f" * 64,
            "source_handle_hash": "sha256:" + "0" * 64,
            "pid": 4242,
            "private_path": "/root/private/http-worker-exit",
        }
        canonical = {
            "schema_version": "1.0.0",
            "event_id": "event-http-retry-exhausted",
            "sequence": 2,
            "task_id": "task-http-retry-exhausted",
            "node_id": "invert",
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "retry_exhausted",
                "message": "FWI Worker exhausted its approved launch attempts",
                "retryable": False,
            },
            "occurred_at": "2026-07-17T08:00:00Z",
            "fingerprint": {},
            "extensions": {
                "org.agent_rpc.retry_exhaustion": private_extension,
                "org.agent_rpc.worker_exit_retry": worker_exit_extension,
            },
        }

        class ExactExhaustionEventView:
            def list_run_events(inner_self, *_args, **_kwargs):
                return [copy.deepcopy(canonical)]

        application = GuidedWorkbench(
            ExactExhaustionEventView(),
            object(),
            project_id="project-http-events",
            principal_id="user-http-events",
        )
        api = WorkbenchAPI(
            application,
            CSRF,
            allowed_hosts={HOST},
            allowed_origins={ORIGIN},
        )
        response = api.dispatch(
            "GET",
            f"{API_PREFIX}/tasks/{canonical['task_id']}/events",
            self.get_headers(),
            b"",
        )
        self.assertEqual(response.status, 200)
        payload = self.decode(response)
        event = payload["data"]["events"][0]
        self.assertEqual(event["error"]["code"], "retry_exhausted")
        self.assertNotIn("org.agent_rpc.retry_exhaustion", event["extensions"])
        self.assertNotIn("org.agent_rpc.worker_exit_retry", event["extensions"])
        serialized = response.body.decode("utf-8")
        stream = api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{canonical['task_id']}/events/stream?after_sequence=1",
            self.get_headers() | {"Accept": "text/event-stream"},
            b"",
        )
        self.assertIsInstance(stream, SSEEventStream)
        serialized += b"".join(stream.next_batch()).decode("utf-8")
        for private in (
            private_extension["intent_id"],
            private_extension["attempt_id"],
            private_extension["evidence_hash"],
            private_extension["private_proof_hash"],
            private_extension["private_schema_version"],
            private_extension["private_path"],
            worker_exit_extension["intent_id"],
            worker_exit_extension["previous_attempt_id"],
            worker_exit_extension["evidence_hash"],
            worker_exit_extension["private_proof_hash"],
            worker_exit_extension["source_outcome_document_hash"],
            worker_exit_extension["source_handle_hash"],
            worker_exit_extension["private_path"],
            "intent_id",
            "attempt_id",
            "previous_attempt_id",
            "evidence_hash",
            "private_proof_hash",
            "private_schema_version",
            "source_outcome_document_hash",
            "source_handle_hash",
            "4242",
            "/root/",
        ):
            self.assertNotIn(private, serialized)

    def test_http_and_sse_bound_dag_failure_and_cache_hit_extensions(self):
        dag_private = {
            "intent_id": "intent-http-private-dag-no-retry",
            "attempt_id": "attempt-http-private-dag-no-retry",
            "attempt_number": 1,
            "observation_sequence": 6,
            "evidence_hash": "sha256:" + "1" * 64,
            "private_schema_version": "1.2.0",
            "private_proof_hash": "sha256:" + "2" * 64,
            "failure_kind": "worker_exit",
            "max_node_attempts": 1,
        }
        cache_private = {
            "state": "hit",
            "cache_hit_id": "cache-hit-http-private",
            "cache_entry_id": "cache-entry-http-private",
            "cache_key_hash": "sha256:" + "3" * 64,
            "source_intent_id": "intent-http-private-cache-source",
            "source_receipt_document_hash": "sha256:" + "4" * 64,
            "worker_runtime_started": False,
            "private_path": "/root/private/http-cache-source",
        }
        canonical = {
            "schema_version": "1.0.0",
            "event_id": "event-http-public-cache-hit",
            "sequence": 10,
            "task_id": "task-http-public-cache-hit",
            "node_id": "data_check",
            "event_type": "node_succeeded",
            "task_status": "Running",
            "occurred_at": "2026-07-17T08:00:00Z",
            "fingerprint": {},
            "extensions": {
                "org.agent_rpc.dag_no_retry": dag_private,
                "org.agent_rpc.node_cache": cache_private,
            },
        }

        class ExactDagCacheEventView:
            def list_run_events(inner_self, *_args, **_kwargs):
                return [copy.deepcopy(canonical)]

        application = GuidedWorkbench(
            ExactDagCacheEventView(),
            object(),
            project_id="project-http-events",
            principal_id="user-http-events",
        )
        api = WorkbenchAPI(
            application,
            CSRF,
            allowed_hosts={HOST},
            allowed_origins={ORIGIN},
        )
        response = api.dispatch(
            "GET",
            f"{API_PREFIX}/tasks/{canonical['task_id']}/events",
            self.get_headers(),
            b"",
        )
        self.assertEqual(response.status, 200)
        event = self.decode(response)["data"]["events"][0]
        self.assertNotIn("org.agent_rpc.dag_no_retry", event["extensions"])
        self.assertEqual(
            event["extensions"]["org.agent_rpc.node_cache"],
            {
                "state": "hit",
                "cache_key_hash": cache_private["cache_key_hash"],
                "worker_runtime_started": False,
            },
        )
        serialized = response.body.decode("utf-8")
        stream = api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{canonical['task_id']}/events/stream?after_sequence=9",
            self.get_headers() | {"Accept": "text/event-stream"},
            b"",
        )
        self.assertIsInstance(stream, SSEEventStream)
        serialized += b"".join(stream.next_batch()).decode("utf-8")
        for private in (
            dag_private["intent_id"],
            dag_private["attempt_id"],
            dag_private["evidence_hash"],
            dag_private["private_proof_hash"],
            cache_private["cache_hit_id"],
            cache_private["cache_entry_id"],
            cache_private["source_intent_id"],
            cache_private["source_receipt_document_hash"],
            cache_private["private_path"],
            "source_intent_id",
            "source_receipt_document_hash",
            "/root/",
        ):
            self.assertNotIn(private, serialized)

    def test_http_events_do_not_serialize_checkpoint_resume_evidence(self):
        checkpoint_id = "checkpoint-" + "1" * 32
        resume_id = "checkpoint-resume-" + "2" * 32
        attempt_id = "attempt-" + "3" * 32
        checkpoint_path = "task-http-private/checkpoints/checkpoint.json"
        proof_hash = "sha256:" + "4" * 64
        canonical = [
            {
                "schema_version": "1.0.0",
                "event_id": "event-http-private-checkpoint",
                "sequence": 7,
                "task_id": "task-http-checkpoint-events",
                "node_id": "invert",
                "event_type": "checkpoint_created",
                "task_status": "Running",
                "checkpoint": {"relative_path": checkpoint_path},
                "occurred_at": "2026-07-17T08:00:00Z",
                "fingerprint": {},
                "extensions": {
                    "org.agent_rpc.checkpoint_wait": {
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_index": 1,
                        "completed_updates": 12,
                        "same_attempt": True,
                        "attempt_id": attempt_id,
                        "proof_hash": proof_hash,
                    },
                    "org.agent_rpc.public_progress": {"phase": "checkpoint"},
                },
            },
            {
                "schema_version": "1.0.0",
                "event_id": "event-http-private-waiting",
                "sequence": 8,
                "task_id": "task-http-checkpoint-events",
                "node_id": "invert",
                "event_type": "node_waiting",
                "task_status": "Waiting",
                "occurred_at": "2026-07-17T08:00:00Z",
                "fingerprint": {},
                "extensions": {
                    "org.agent_rpc.checkpoint_wait": {
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_index": 1,
                        "completed_updates": 12,
                        "same_attempt": True,
                        "attempt_id": attempt_id,
                        "proof_hash": proof_hash,
                    }
                },
            },
            {
                "schema_version": "1.0.0",
                "event_id": "event-http-private-resume",
                "sequence": 9,
                "task_id": "task-http-checkpoint-events",
                "node_id": "invert",
                "event_type": "node_started",
                "task_status": "Running",
                "occurred_at": "2026-07-17T08:00:01Z",
                "fingerprint": {},
                "extensions": {
                    "org.agent_rpc.checkpoint_resume": {
                        "checkpoint_id": checkpoint_id,
                        "resume_id": resume_id,
                        "same_attempt": True,
                        "attempt_id": attempt_id,
                        "proof_hash": proof_hash,
                        "private_path": "/root/private/http-checkpoint-resume",
                    }
                },
            },
        ]

        class ExactCheckpointEventView:
            def list_run_events(inner_self, *_args, **_kwargs):
                return copy.deepcopy(canonical)

        application = GuidedWorkbench(
            ExactCheckpointEventView(),
            object(),
            project_id="project-http-events",
            principal_id="user-http-events",
        )
        api = WorkbenchAPI(
            application,
            CSRF,
            allowed_hosts={HOST},
            allowed_origins={ORIGIN},
        )
        response = api.dispatch(
            "GET",
            f"{API_PREFIX}/tasks/{canonical[0]['task_id']}/events",
            self.get_headers(),
            b"",
        )
        self.assertEqual(response.status, 200)
        events = self.decode(response)["data"]["events"]
        self.assertEqual(
            [
                (event["sequence"], event["event_type"], event["task_status"])
                for event in events
            ],
            [
                (7, "checkpoint_created", "Running"),
                (8, "node_waiting", "Waiting"),
                (9, "node_started", "Running"),
            ],
        )
        self.assertEqual(
            events[0]["extensions"]["org.agent_rpc.public_progress"],
            {"phase": "checkpoint"},
        )
        self.assertNotIn("checkpoint", events[0])
        for event in events:
            self.assertNotIn(
                "org.agent_rpc.checkpoint_wait", event["extensions"]
            )
            self.assertNotIn(
                "org.agent_rpc.checkpoint_resume", event["extensions"]
            )

        serialized = response.body.decode("utf-8")
        stream = api.open_event_stream(
            "GET",
            f"{API_PREFIX}/tasks/{canonical[0]['task_id']}/events/stream?after_sequence=6",
            self.get_headers() | {"Accept": "text/event-stream"},
            b"",
        )
        self.assertIsInstance(stream, SSEEventStream)
        serialized += b"".join(stream.next_batch()).decode("utf-8")
        for private in (
            checkpoint_id,
            resume_id,
            attempt_id,
            checkpoint_path,
            proof_hash,
            "/root/private/http-checkpoint-resume",
            "checkpoint_id",
            "resume_id",
            "attempt_id",
            "proof_hash",
            "relative_path",
            "org.agent_rpc.checkpoint_wait",
            "org.agent_rpc.checkpoint_resume",
        ):
            self.assertNotIn(private, serialized)

    def test_mutations_require_exact_origin_csrf_and_idempotency_key(self):
        body = json.dumps(guided_form()).encode("utf-8")
        cases = (
            ({"X-Workbench-CSRF": "wrong"}, "CSRF_FORBIDDEN", 403),
            ({"Origin": "http://localhost:8080"}, "ORIGIN_FORBIDDEN", 403),
            ({"Idempotency-Key": ""}, "IDEMPOTENCY_KEY_REQUIRED", 400),
        )
        for updates, code, status in cases:
            with self.subTest(code=code):
                response = self.api.dispatch(
                    "POST",
                    f"{API_PREFIX}/tasks",
                    self.mutation_headers(body, **updates),
                    body,
                )
                self.assertEqual(response.status, status)
                self.assertEqual(self.decode(response)["error"]["code"], code)

    def test_json_requires_exact_length_utf8_media_type_and_no_transfer_encoding(self):
        form = guided_form()
        body = json.dumps(form).encode("utf-8")
        cases = (
            ({"Content-Length": str(len(body) + 1)}, body, 400),
            ({"Content-Length": None}, body, 411),
            ({"Content-Type": "text/plain"}, body, 415),
            ({"Content-Type": "application/json; charset=iso-8859-1"}, body, 415),
            ({"Transfer-Encoding": "chunked"}, body, 400),
            ({"Content-Encoding": "gzip"}, body, 415),
            ({"Content-Length": str(MAX_JSON_BYTES + 1)}, b"x" * (MAX_JSON_BYTES + 1), 413),
        )
        for updates, candidate, status in cases:
            with self.subTest(updates=updates):
                headers = self.mutation_headers(candidate)
                for name, value in updates.items():
                    if value is None:
                        headers.pop(name)
                    else:
                        headers[name] = value
                response = self.api.dispatch(
                    "POST", f"{API_PREFIX}/tasks", headers, candidate
                )
                self.assertEqual(response.status, status)

    def test_duplicate_keys_nonfinite_numbers_and_invalid_json_are_rejected(self):
        prefixes = (
            b'{"goal":"one","goal":"two",',
            b'{"goal":NaN,',
            b'{"goal":Infinity,',
            b'{"goal":1e9999,',
        )
        suffix = (
            b'"dataset_id":"marmousi_94_288","dataset_version":"1.0.0",'
            b'"preset":"fwi_smoke","device":"cpu","iterations":1,"seed":7}'
        )
        candidates = [prefix + suffix for prefix in prefixes]
        candidates.extend((b"[]", b'{"goal":"\xff"}'))
        for body in candidates:
            with self.subTest(body=body[:30]):
                response = self.api.dispatch(
                    "POST",
                    f"{API_PREFIX}/tasks",
                    self.mutation_headers(body),
                    body,
                )
                self.assertEqual(response.status, 400)
                self.assertEqual(self.decode(response)["error"]["code"], "INVALID_JSON")

    def test_ambiguous_paths_double_decoding_and_unknown_queries_are_rejected(self):
        task_path = f"{API_PREFIX}/tasks/task-1"
        targets = (
            f"{API_PREFIX}/tasks/task%2f1",
            f"{API_PREFIX}/tasks/task%5c1",
            f"{API_PREFIX}/tasks/task%001",
            f"{API_PREFIX}/tasks/task%2e1",
            f"{API_PREFIX}/tasks/task%252f1",
            f"{API_PREFIX}/tasks/../task-1",
            f"{task_path}?project_id=other",
            f"{task_path}/events?after_sequence=1&unknown=2",
            f"{task_path}/events?after_sequence=01",
        )
        for target in targets:
            with self.subTest(target=target):
                response = self.api.dispatch("GET", target, self.get_headers(), b"")
                self.assertEqual(response.status, 400)

    def test_methods_and_get_bodies_fail_closed(self):
        response = self.api.dispatch(
            "POST", f"{API_PREFIX}/catalog", self.get_headers(), b""
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers["Allow"], "GET")

        response = self.api.dispatch(
            "GET",
            f"{API_PREFIX}/catalog",
            self.get_headers() | {"Content-Length": "2"},
            b"{}",
        )
        self.assertEqual(response.status, 400)

        response = self.api.dispatch(
            "OPTIONS", f"{API_PREFIX}/catalog", self.get_headers(), b""
        )
        self.assertEqual(response.status, 405)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_stable_application_errors_map_without_paths_or_exception_text(self):
        cases = (
            (WorkbenchValidationError(), 422, "INVALID_FORM"),
            (WorkbenchNotFound("missing /root/task"), 404, "NOT_FOUND"),
            (WorkbenchConflict(), 409, "IDEMPOTENCY_CONFLICT"),
            (WorkbenchRuntimeError(), 503, "ADAPTER_UNAVAILABLE"),
            (RuntimeError("unexpected /root/private/.env"), 500, "INTERNAL_ERROR"),
        )
        for error, status, code in cases:
            with self.subTest(error=type(error).__name__):
                self.application.error = error
                response = self.api.dispatch(
                    "GET", f"{API_PREFIX}/catalog", self.get_headers(), b""
                )
                self.assertEqual(response.status, status)
                payload = self.decode(response)
                self.assertEqual(payload["error"]["code"], code)
                serialized = response.body.decode("utf-8")
                self.assertNotIn("/root", serialized)
                self.assertNotIn(".env", serialized)
        self.application.error = None

    def test_binary_artifact_is_scoped_allowlisted_and_forced_to_attachment(self):
        path = f"{API_PREFIX}/tasks/task-1/artifacts/loss"
        response = self.api.dispatch("GET", path, self.get_headers(), b"")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, self.application.artifact_content)
        self.assertEqual(response.headers["Content-Type"], "text/csv")
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="loss.csv"')
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertFalse(
            any(name.lower().startswith("access-control-") for name in response.headers)
        )

        self.application.artifact_media_type = "application/x-npy"
        self.application.artifact_content = b"\x93NUMPY"
        npy_path = f"{API_PREFIX}/tasks/task-1/artifacts/model"
        response = self.api.dispatch("GET", npy_path, self.get_headers(), b"")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/x-npy")
        self.assertEqual(
            response.headers["Content-Disposition"],
            'attachment; filename="model.npy"',
        )

        self.application.artifact_media_type = "image/png"
        self.application.artifact_content = b"\x89PNG\r\n\x1a\ncontrolled-image"
        png_path = f"{API_PREFIX}/tasks/task-1/artifacts/true-model"
        response = self.api.dispatch("GET", png_path, self.get_headers(), b"")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, self.application.artifact_content)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(
            response.headers["Content-Disposition"],
            'attachment; filename="true-model.png"',
        )

        self.application.artifact_media_type = "text/html"
        response = self.api.dispatch("GET", path, self.get_headers(), b"")
        self.assertEqual(response.status, 500)
        self.assertEqual(self.decode(response)["error"]["code"], "INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
