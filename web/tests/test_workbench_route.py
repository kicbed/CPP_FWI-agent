#!/usr/bin/env python3
"""HTTP integration checks for the Guided Workbench server wiring."""

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from web import serve
from web.workbench_api import WorkbenchAPI


class _Application:
    def __init__(self):
        self.created = []
        self.listed = []
        self.cancelled = []
        self.purged = []
        self.event_reads = []

    def session_capabilities(self):
        return {
            "mode": "guided",
            "scope": {"project_id": "local-workbench", "principal_id": "local-user"},
            "capabilities": {"cancel": True, "retry": False, "sse": False},
        }

    def list_catalog(self):
        return {"datasets": [], "algorithms": []}

    def create_task(self, form, key):
        self.created.append((form, key))
        return {"task_id": "task-route-test", "status": "AwaitingApproval"}

    def list_tasks(self, *, cursor=None, limit=20, view="active"):
        self.listed.append((cursor, limit, view))
        return {
            "tasks": [
                {
                    "task_id": "task-route-test",
                    "status": "Running",
                    "goal": "route integration",
                }
            ],
            "next_cursor": None,
        }

    def purge_task(self, task_id, expected_visibility_revision, key):
        self.purged.append((task_id, expected_visibility_revision, key))
        return {
            "task_id": task_id,
            "purge_state": "purged",
            "purged_at": "2026-07-15T12:00:00Z",
            "local_run_state": "deleted",
            "audit_retained": True,
            "replayed": False,
        }

    def cancel_task(self, task_id, key, reason):
        self.cancelled.append((task_id, key, reason))
        return {
            "task_id": task_id,
            "status": "Running",
            "can_cancel": False,
            "cancellation": {
                "state": "requested",
                "reason": reason,
                "requested_at": "2026-07-16T12:00:00Z",
                "resolved_at": None,
                "failure_code": None,
            },
            "replayed": False,
        }

    def list_events(self, task_id, *, after_sequence=0, limit=100):
        self.event_reads.append((task_id, after_sequence, limit))
        events = [
            {
                "schema_version": "1.0.0",
                "event_id": "event-route-1",
                "sequence": 1,
                "task_id": task_id,
                "node_id": "invert",
                "event_type": "node_started",
                "task_status": "Running",
                "occurred_at": "2026-07-17T12:00:00Z",
                "fingerprint": {},
                "extensions": {},
            },
            {
                "schema_version": "1.0.0",
                "event_id": "event-route-2",
                "sequence": 2,
                "task_id": task_id,
                "node_id": "invert",
                "event_type": "node_succeeded",
                "task_status": "Succeeded",
                "occurred_at": "2026-07-17T12:00:01Z",
                "fingerprint": {},
                "extensions": {},
            },
        ]
        return [event for event in events if event["sequence"] > after_sequence][:limit]


class WorkbenchRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_api = serve.WORKBENCH_API
        cls.previous_allow_origin = serve.ALLOW_ORIGIN
        serve.ALLOW_ORIGIN = "http://127.0.0.1:9090"
        cls.application = _Application()
        cls.server = serve.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), serve.Handler
        )
        cls.port = cls.server.server_address[1]
        cls.csrf = "route-test-csrf-token"
        serve.WORKBENCH_API = WorkbenchAPI(
            cls.application,
            csrf_token=cls.csrf,
            allowed_hosts={f"127.0.0.1:{cls.port}"},
            allowed_origins={f"http://127.0.0.1:{cls.port}"},
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        serve.WORKBENCH_API = cls.previous_api
        serve.ALLOW_ORIGIN = cls.previous_allow_origin

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, content

    def raw_request(self, request, *, timeout=2):
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as client:
            client.settimeout(timeout)
            client.sendall(request)
            chunks = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def test_session_and_mutation_reach_api_without_legacy_cors(self):
        status, headers, body = self.request(
            "GET", "/api/scientific-runtime/v1/session"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        session = json.loads(body)
        self.assertEqual(session["data"]["csrf_token"], self.csrf)

        status, headers, body = self.request(
            "GET",
            "/api/scientific-runtime/v1/tasks?limit=7",
            headers={"X-Workbench-CSRF": self.csrf},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)
        task_page = json.loads(body)["data"]
        self.assertEqual(task_page["tasks"][0]["task_id"], "task-route-test")
        self.assertEqual(self.application.listed[-1], (None, 7, "active"))

        form = {
            "goal": "route integration",
            "dataset_id": "marmousi_94_288",
            "dataset_version": "1.0.0",
            "preset": "fwi_smoke",
            "device": "cpu",
            "iterations": 1,
            "seed": 2026,
            "optimizer": "adam",
            "learning_rate": "10",
        }
        encoded = json.dumps(form, separators=(",", ":")).encode("utf-8")
        status, headers, body = self.request(
            "POST",
            "/api/scientific-runtime/v1/tasks",
            body=encoded,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
                "X-Workbench-CSRF": self.csrf,
                "Idempotency-Key": "route-create-1",
            },
        )
        self.assertEqual(status, 201)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertEqual(json.loads(body)["data"]["task_id"], "task-route-test")
        self.assertEqual(self.application.created, [(form, "route-create-1")])

        cancel = {"reason": "user_requested"}
        encoded = json.dumps(cancel, separators=(",", ":")).encode("utf-8")
        status, headers, body = self.request(
            "POST",
            "/api/scientific-runtime/v1/tasks/task-route-test/cancel",
            body=encoded,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
                "X-Workbench-CSRF": self.csrf,
                "Idempotency-Key": "route-cancel-1",
            },
        )
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)
        cancellation = json.loads(body)["data"]["cancellation"]
        self.assertEqual(cancellation["state"], "requested")
        self.assertEqual(cancellation["reason"], "user_requested")
        self.assertEqual(
            self.application.cancelled,
            [("task-route-test", "route-cancel-1", "user_requested")],
        )

        purge = {
            "expected_visibility_revision": 1,
            "confirmation_task_id": "task-route-test",
        }
        encoded = json.dumps(purge, separators=(",", ":")).encode("utf-8")
        status, headers, body = self.request(
            "POST",
            "/api/scientific-runtime/v1/tasks/task-route-test/purge",
            body=encoded,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
                "X-Workbench-CSRF": self.csrf,
                "Idempotency-Key": "route-purge-1",
            },
        )
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertEqual(json.loads(body)["data"]["purge_state"], "purged")
        self.assertEqual(
            self.application.purged,
            [("task-route-test", 1, "route-purge-1")],
        )

    def test_preflight_and_unsupported_methods_fail_closed(self):
        for method in ("OPTIONS", "DELETE", "PATCH"):
            with self.subTest(method=method):
                status, headers, body = self.request(
                    method, "/api/scientific-runtime/v1/tasks/task-route-test"
                )
                self.assertEqual(status, 405)
                self.assertNotIn("access-control-allow-origin", headers)
                self.assertEqual(json.loads(body)["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_task_event_stream_is_finite_replayable_and_scope_authenticated(self):
        path = (
            "/api/scientific-runtime/v1/tasks/task-route-test/events/stream"
            "?after_sequence=1"
        )
        status, headers, body = self.request(
            "GET",
            path,
            headers={
                "Accept": "text/event-stream",
                "X-Workbench-CSRF": self.csrf,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["connection"], "close")
        self.assertEqual(headers["x-accel-buffering"], "no")
        self.assertNotIn("content-length", headers)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertTrue(body.startswith(b"retry: 1000\n\nid: 2\n"))
        self.assertIn(b"event: run_event\n", body)
        payload = json.loads(body.split(b"data: ", 1)[1].strip())
        self.assertEqual(payload["task_id"], "task-route-test")
        self.assertEqual(payload["sequence"], 2)
        self.assertEqual(payload["task_status"], "Succeeded")
        self.assertEqual(
            self.application.event_reads[-1],
            ("task-route-test", 1, 100),
        )

        status, headers, body = self.request(
            "GET",
            path,
            headers={"Accept": "text/event-stream"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "CSRF_FORBIDDEN")
        self.assertIn("content-length", headers)

    def test_framing_is_rejected_before_a_caller_controlled_body_read(self):
        prefix = (
            b"POST /api/scientific-runtime/v1/tasks HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{self.port}\r\n".encode("ascii")
            + f"Origin: http://127.0.0.1:{self.port}\r\n".encode("ascii")
            + f"X-Workbench-CSRF: {self.csrf}\r\n".encode("ascii")
            + b"Idempotency-Key: route-framing-test\r\n"
            + b"Content-Type: application/json\r\n"
        )

        response = self.raw_request(
            prefix + b"Content-Length: 65537\r\nConnection: close\r\n\r\n"
        )
        self.assertIn(b" 413 ", response.split(b"\r\n", 1)[0])

        response = self.raw_request(
            b"POST /api/scientific-runtime/v1/tasks HTTP/1.1\r\n"
            + b"Host: attacker.example\r\n"
            + b"Content-Length: 10\r\nConnection: close\r\n\r\n"
        )
        self.assertIn(b" 403 ", response.split(b"\r\n", 1)[0])

        response = self.raw_request(
            prefix + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        )
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])

    def test_partial_body_times_out_and_closes_the_connection(self):
        previous = serve.WORKBENCH_BODY_TIMEOUT_SECONDS
        serve.WORKBENCH_BODY_TIMEOUT_SECONDS = 0.1
        try:
            request = (
                b"POST /api/scientific-runtime/v1/tasks HTTP/1.1\r\n"
                + f"Host: 127.0.0.1:{self.port}\r\n".encode("ascii")
                + f"Origin: http://127.0.0.1:{self.port}\r\n".encode("ascii")
                + f"X-Workbench-CSRF: {self.csrf}\r\n".encode("ascii")
                + b"Idempotency-Key: route-timeout-test\r\n"
                + b"Content-Type: application/json\r\n"
                + b"Content-Length: 2\r\nConnection: close\r\n\r\n{"
            )
            response = self.raw_request(request)
            self.assertIn(b" 408 ", response.split(b"\r\n", 1)[0])
            payload = json.loads(response.split(b"\r\n\r\n", 1)[1])
            self.assertEqual(payload["error"]["code"], "BODY_TIMEOUT")
        finally:
            serve.WORKBENCH_BODY_TIMEOUT_SECONDS = previous

    def test_oversized_request_line_gets_414_without_handler_exception(self):
        request = b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\n\r\n"
        response = self.raw_request(request)
        self.assertIn(b" 414 ", response.split(b"\r\n", 1)[0])

    def test_wildcard_bind_never_composes_the_guided_runtime(self):
        previous_host = serve.HOST
        serve.HOST = "0.0.0.0"
        try:
            with self.assertRaises(ValueError):
                serve.create_workbench_api()
        finally:
            serve.HOST = previous_host

        previous_api = serve.WORKBENCH_API
        serve.WORKBENCH_API = None
        try:
            status, headers, body = self.request(
                "GET", "/api/scientific-runtime/v1/session"
            )
            self.assertEqual(status, 503)
            self.assertNotIn("access-control-allow-origin", headers)
            self.assertEqual(json.loads(body)["error"]["code"], "RUNTIME_UNAVAILABLE")
        finally:
            serve.WORKBENCH_API = previous_api

    def test_composition_recovers_runtime_before_exposing_workbench_api(self):
        order = []
        application = mock.Mock()

        def recover_runtime_on_startup(*, max_tasks):
            self.assertEqual(order, [("api",), ("supervisor",)])
            order.append(("recover", max_tasks))

        application.recover_runtime_on_startup.side_effect = (
            recover_runtime_on_startup
        )
        composed_api = mock.sentinel.composed_api
        composed_supervisor = mock.sentinel.composed_supervisor

        def compose_api(
            composed_application,
            *,
            csrf_token,
            allowed_hosts,
            allowed_origins,
        ):
            self.assertIs(composed_application, application)
            self.assertEqual(order, [])
            self.assertEqual(csrf_token, "composition-csrf-token")
            self.assertEqual(allowed_hosts, {"127.0.0.1:8080"})
            self.assertEqual(allowed_origins, {"http://127.0.0.1:8080"})
            order.append(("api",))
            return composed_api

        def compose_supervisor(tasks, **kwargs):
            self.assertIs(tasks, mock.sentinel.tasks)
            self.assertEqual(kwargs["project_id"], "local-workbench")
            self.assertEqual(kwargs["principal_id"], "local-user")
            self.assertRegex(kwargs["owner_id"], r"^supervisor-[0-9a-f]{32}$")
            self.assertEqual(order, [("api",)])
            order.append(("supervisor",))
            return composed_supervisor

        with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
            serve, "PORT", 8080
        ), mock.patch.dict(
            os.environ,
            {"AGENT_CORS_ORIGIN": "http://127.0.0.1:8080"},
        ), mock.patch.object(
            serve, "fwi_run_root", return_value=mock.sentinel.run_root
        ), mock.patch.object(
            serve,
            "validated_scientific_runtime_database_path",
            return_value=mock.sentinel.database_path,
        ), mock.patch.object(
            serve, "SQLiteTaskStore", return_value=mock.sentinel.store
        ), mock.patch.object(
            serve, "RegistryService", return_value=mock.sentinel.registry
        ), mock.patch.object(
            serve, "register_verified_fwi_baseline"
        ), mock.patch.object(
            serve, "DeepwaveAdapter", return_value=mock.sentinel.adapter
        ), mock.patch.object(
            serve,
            "DeepwaveTaskDispatcher",
            return_value=mock.sentinel.dispatcher,
        ), mock.patch.object(
            serve, "TaskService", return_value=mock.sentinel.tasks
        ), mock.patch.object(
            serve, "GuidedWorkbench", return_value=application
        ) as guided_workbench, mock.patch.object(
            serve.secrets, "token_urlsafe", return_value="composition-csrf-token"
        ), mock.patch.object(
            serve, "WorkbenchAPI", side_effect=compose_api
        ), mock.patch.object(
            serve, "RuntimeSupervisor", side_effect=compose_supervisor
        ):
            result = serve.create_workbench_api()

        self.assertIs(result, composed_api)
        guided_workbench.assert_called_once_with(
            mock.sentinel.tasks,
            mock.sentinel.registry,
            project_id="local-workbench",
            principal_id="local-user",
            enable_fixed_recipe_dag=True,
        )
        application.recover_runtime_on_startup.assert_called_once_with(
            max_tasks=10000
        )
        self.assertEqual(
            order,
            [("api",), ("supervisor",), ("recover", 10000)],
        )

    def _create_workbench_api_with_mocked_runtime(
        self, application, *, api_side_effect=None
    ):
        with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
            serve, "PORT", 8080
        ), mock.patch.dict(
            os.environ,
            {"AGENT_CORS_ORIGIN": "http://127.0.0.1:8080"},
        ), mock.patch.object(
            serve, "fwi_run_root", return_value=mock.sentinel.run_root
        ), mock.patch.object(
            serve,
            "validated_scientific_runtime_database_path",
            return_value=mock.sentinel.database_path,
        ), mock.patch.object(
            serve, "SQLiteTaskStore", return_value=mock.sentinel.store
        ), mock.patch.object(
            serve, "RegistryService", return_value=mock.sentinel.registry
        ), mock.patch.object(
            serve, "register_verified_fwi_baseline"
        ), mock.patch.object(
            serve, "DeepwaveAdapter", return_value=mock.sentinel.adapter
        ), mock.patch.object(
            serve,
            "DeepwaveTaskDispatcher",
            return_value=mock.sentinel.dispatcher,
        ), mock.patch.object(
            serve, "TaskService", return_value=mock.sentinel.tasks
        ), mock.patch.object(
            serve, "GuidedWorkbench", return_value=application
        ), mock.patch.object(
            serve.secrets, "token_urlsafe", return_value="composition-csrf-token"
        ), mock.patch.object(
            serve,
            "WorkbenchAPI",
            return_value=mock.sentinel.composed_api,
            side_effect=api_side_effect,
        ):
            return serve.create_workbench_api()

    def test_composition_skips_recovery_when_http_boundary_is_invalid(self):
        application = mock.Mock()

        with self.assertRaisesRegex(ValueError, "invalid HTTP boundary"):
            self._create_workbench_api_with_mocked_runtime(
                application,
                api_side_effect=ValueError("invalid HTTP boundary"),
            )

        application.recover_runtime_on_startup.assert_not_called()

    def test_composition_does_not_expose_api_when_recovery_fails(self):
        application = mock.Mock()
        application.recover_runtime_on_startup.side_effect = RuntimeError(
            "startup recovery failed"
        )
        previous_api = serve.WORKBENCH_API

        with self.assertRaisesRegex(RuntimeError, "startup recovery failed"):
            self._create_workbench_api_with_mocked_runtime(application)

        self.assertIs(serve.WORKBENCH_API, previous_api)
        application.recover_runtime_on_startup.assert_called_once_with(
            max_tasks=10000
        )

    def test_runtime_recovery_summary_is_path_free_and_counted(self):
        recovery = serve.RuntimeRecoveryResult(
            project_id="project-secret",
            principal_id="principal-secret",
            scanned_task_ids=("task-secret-1", "task-secret-2"),
            receipt_recovery_attempted_task_ids=("task-secret-1",),
            receipt_recovered_task_ids=(),
            pending_deferred_task_ids=("task-secret-2",),
            dispatching_deferred=(
                ("task-secret-1", "ADAPTER_SUBMISSION_NOT_FOUND"),
            ),
            status_refreshed_task_ids=(),
            status_refresh_failures=(
                ("task-secret-1", "ADAPTER_STATUS_UNAVAILABLE"),
            ),
            reconciliation_required_task_ids=(),
        )

        with mock.patch("builtins.print") as output:
            serve.report_runtime_recovery(recovery)

        message = output.call_args.args[0]
        self.assertIn('"scanned":2', message)
        self.assertIn('"pending_deferred":1', message)
        self.assertIn('"ADAPTER_SUBMISSION_NOT_FOUND":1', message)
        self.assertNotIn("task-secret", message)
        self.assertNotIn("project-secret", message)
        self.assertIs(output.call_args.kwargs["file"], serve.sys.stderr)

        with mock.patch("builtins.print", side_effect=ValueError("closed stream")):
            serve.report_runtime_recovery(recovery)

    def test_server_binds_then_recovers_before_publishing_and_serving(self):
        order = []
        composed_api = mock.sentinel.bound_composed_api

        class Supervisor:
            healthy = True
            failure_code = None

            def start(self):
                self.assert_private()
                order.append(("supervisor_started",))
                return True

            def stop(self):
                order.append(("supervisor_stopped", serve.WORKBENCH_API))
                return True

            @staticmethod
            def assert_private():
                if serve.WORKBENCH_API is not None:
                    raise AssertionError("API published before supervisor readiness")

        supervisor = Supervisor()

        class BoundServer:
            def __init__(self, address, handler, *, bind_and_activate):
                order.append(("constructed", address, handler, bind_and_activate))
                self.runtime_supervisor = None

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activated",))

            def serve_forever(self):
                order.append(("served", serve.WORKBENCH_API))

            def close_listener(self):
                order.append(("listener_closed", serve.WORKBENCH_API))

            def drain_request_threads(self, timeout):
                order.append(("handlers_drained", serve.WORKBENCH_API, timeout))
                return True

        def compose_after_bind():
            self.assertEqual(
                order[0][:2], ("constructed", ("127.0.0.1", 8080))
            )
            self.assertFalse(order[0][3])
            self.assertEqual(order[1], ("bound",))
            self.assertIsNone(serve.WORKBENCH_API)
            order.append(("recovered",))
            return serve.WorkbenchRuntime(composed_api, supervisor)

        previous_api = serve.WORKBENCH_API
        serve.WORKBENCH_API = mock.sentinel.stale_api
        try:
            with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                serve, "PORT", 8080
            ), mock.patch.object(
                serve, "fwi_run_root"
            ), mock.patch.object(
                serve, "ReusableThreadingTCPServer", BoundServer
            ), mock.patch.object(
                serve, "create_workbench_runtime", side_effect=compose_after_bind
            ), mock.patch.object(
                serve.webbrowser, "open", return_value=False
            ), mock.patch("builtins.print"):
                serve.serve_workbench()
        finally:
            self.assertIsNone(serve.WORKBENCH_API)
            serve.WORKBENCH_API = previous_api

        self.assertEqual(
            [item[0] for item in order],
            [
                "constructed",
                "bound",
                "recovered",
                "supervisor_started",
                "activated",
                "served",
                "listener_closed",
                "supervisor_stopped",
                "handlers_drained",
            ],
        )
        self.assertIs(order[5][1], composed_api)
        self.assertIs(order[6][1], composed_api)
        self.assertIs(order[7][1], composed_api)
        self.assertIs(order[8][1], composed_api)
        self.assertEqual(order[8][2], serve.HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS)

    def test_supervisor_start_failure_closes_socket_without_publishing(self):
        order = []

        class Supervisor:
            failure_code = "RUNTIME_SUPERVISOR_LEASE_HELD"

            def start(self):
                order.append(("supervisor_start_failed", serve.WORKBENCH_API))
                return False

            def stop(self):
                order.append(("supervisor_stopped", serve.WORKBENCH_API))
                return True

        class BoundServer:
            def __init__(self, address, handler, *, bind_and_activate):
                order.append(("constructed", address, bind_and_activate))
                self.runtime_supervisor = None

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activated",))

            def serve_forever(self):
                order.append(("served",))

            def close_listener(self):
                order.append(("listener_closed", serve.WORKBENCH_API))

            def drain_request_threads(self, timeout):
                order.append(("handlers_drained", serve.WORKBENCH_API, timeout))
                return True

        runtime = serve.WorkbenchRuntime(mock.sentinel.api, Supervisor())
        previous_api = serve.WORKBENCH_API
        serve.WORKBENCH_API = mock.sentinel.stale_api
        try:
            with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                serve, "PORT", 8080
            ), mock.patch.object(serve, "fwi_run_root"), mock.patch.object(
                serve, "ReusableThreadingTCPServer", BoundServer
            ), mock.patch.object(
                serve, "create_workbench_runtime", return_value=runtime
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "RUNTIME_SUPERVISOR_LEASE_HELD"
                ):
                    serve.serve_workbench()
        finally:
            self.assertIsNone(serve.WORKBENCH_API)
            serve.WORKBENCH_API = previous_api

        self.assertEqual(
            order,
            [
                ("constructed", ("127.0.0.1", 8080), False),
                ("bound",),
                ("supervisor_start_failed", None),
                ("listener_closed", None),
                ("supervisor_stopped", None),
                ("handlers_drained", None, serve.HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS),
            ],
        )

    def test_activation_failure_stops_started_supervisor_before_publication(self):
        order = []

        class Supervisor:
            healthy = True
            failure_code = None

            def start(self):
                order.append(("supervisor_started", serve.WORKBENCH_API))
                return True

            def stop(self):
                order.append(("supervisor_stopped", serve.WORKBENCH_API))
                return True

        class BoundServer:
            def __init__(self, address, handler, *, bind_and_activate):
                order.append(("constructed", address, bind_and_activate))
                self.runtime_supervisor = None

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activation_failed", serve.WORKBENCH_API))
                raise OSError("activation failed")

            def serve_forever(self):
                order.append(("served",))

            def close_listener(self):
                order.append(("listener_closed", serve.WORKBENCH_API))

            def drain_request_threads(self, timeout):
                order.append(("handlers_drained", serve.WORKBENCH_API, timeout))
                return True

        runtime = serve.WorkbenchRuntime(mock.sentinel.api, Supervisor())
        previous_api = serve.WORKBENCH_API
        try:
            with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                serve, "PORT", 8080
            ), mock.patch.object(serve, "fwi_run_root"), mock.patch.object(
                serve, "ReusableThreadingTCPServer", BoundServer
            ), mock.patch.object(
                serve, "create_workbench_runtime", return_value=runtime
            ):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    serve.serve_workbench()
        finally:
            self.assertIsNone(serve.WORKBENCH_API)
            serve.WORKBENCH_API = previous_api

        self.assertEqual(
            [item[0] for item in order],
            [
                "constructed",
                "bound",
                "supervisor_started",
                "activation_failed",
                "listener_closed",
                "supervisor_stopped",
                "handlers_drained",
            ],
        )
        self.assertTrue(all(item[1] is None for item in order[2:]))

    def test_serve_error_remains_primary_when_close_fails_and_supervisor_stops(self):
        order = []
        composed_api = mock.sentinel.composed_api

        class Supervisor:
            healthy = True
            failure_code = None

            def start(self):
                order.append(("supervisor_started", serve.WORKBENCH_API))
                return True

            def stop(self):
                order.append(("supervisor_stopped", serve.WORKBENCH_API))
                return True

        class BoundServer:
            def __init__(self, address, handler, *, bind_and_activate):
                order.append(("constructed",))
                self.runtime_supervisor = None

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activated", serve.WORKBENCH_API))

            def serve_forever(self):
                order.append(("serve_failed", serve.WORKBENCH_API))
                raise RuntimeError("serve loop failed")

            def close_listener(self):
                order.append(("close_failed", serve.WORKBENCH_API))
                raise OSError("close failed")

            def drain_request_threads(self, timeout):
                order.append(("handlers_drained", serve.WORKBENCH_API, timeout))
                return True

        runtime = serve.WorkbenchRuntime(composed_api, Supervisor())
        previous_api = serve.WORKBENCH_API
        try:
            with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                serve, "PORT", 8080
            ), mock.patch.object(serve, "fwi_run_root"), mock.patch.object(
                serve, "ReusableThreadingTCPServer", BoundServer
            ), mock.patch.object(
                serve, "create_workbench_runtime", return_value=runtime
            ), mock.patch.object(
                serve.webbrowser, "open", return_value=False
            ), mock.patch("builtins.print"):
                with self.assertRaisesRegex(
                    RuntimeError, "serve loop failed"
                ) as raised:
                    serve.serve_workbench()
        finally:
            self.assertIsNone(serve.WORKBENCH_API)
            serve.WORKBENCH_API = previous_api

        self.assertEqual(
            [item[0] for item in order],
            [
                "constructed",
                "bound",
                "supervisor_started",
                "activated",
                "serve_failed",
                "close_failed",
                "supervisor_stopped",
                "handlers_drained",
            ],
        )
        self.assertIsNone(order[2][1])
        self.assertIsNone(order[3][1])
        for item in order[4:]:
            self.assertIs(item[1], composed_api)
        self.assertEqual(
            getattr(raised.exception, "workbench_cleanup_codes", ()),
            ("HTTP_LISTENER_CLOSE_FAILED",),
        )

    def test_handler_keeps_one_api_facade_during_shutdown_publication_race(self):
        handler = object.__new__(serve.Handler)
        handler.path = "/api/scientific-runtime/v1/session"
        handler.headers = mock.Mock()
        handler.headers.items.return_value = (("Host", "127.0.0.1"),)
        handler._workbench_body = mock.Mock(return_value=b"")
        handler._send_workbench_response = mock.Mock()
        first_api = mock.Mock()
        second_api = mock.Mock()
        response = mock.sentinel.response

        def replace_publication(*_args):
            serve.WORKBENCH_API = second_api
            return None

        first_api.preflight.side_effect = replace_publication
        first_api.dispatch.return_value = response
        previous_api = serve.WORKBENCH_API
        serve.WORKBENCH_API = first_api
        try:
            handler._serve_workbench("GET")
        finally:
            serve.WORKBENCH_API = previous_api

        first_api.dispatch.assert_called_once()
        second_api.dispatch.assert_not_called()
        handler._send_workbench_response.assert_called_once_with(response, True)

    def test_server_service_actions_fails_when_supervisor_self_fences(self):
        server = object.__new__(serve.ReusableThreadingTCPServer)
        finished = threading.Thread(target=lambda: None)
        finished.start()
        finished.join()
        server._threads = serve.socketserver._Threads()
        server._threads.append(finished)
        self.assertEqual(len(server._threads), 1)
        server.runtime_supervisor = mock.Mock(
            healthy=False, failure_code="RUNTIME_SUPERVISOR_LEASE_LOST"
        )
        with self.assertRaisesRegex(
            RuntimeError, "RUNTIME_SUPERVISOR_LEASE_LOST"
        ):
            server.service_actions()
        self.assertEqual(len(server._threads), 0)

        server.runtime_supervisor = mock.Mock(healthy=True, failure_code=None)
        server.service_actions()

    def test_supervisor_stop_failure_still_drains_and_unpublishes(self):
        order = []
        composed_api = mock.sentinel.stop_failure_api
        supervisor = mock.Mock(
            healthy=True,
            failure_code="RUNTIME_SUPERVISOR_STOP_TIMEOUT",
        )
        supervisor.start.return_value = True
        supervisor.stop.side_effect = lambda: order.append(
            ("supervisor_stop_failed", serve.WORKBENCH_API)
        ) or False
        server = mock.Mock()
        server.close_listener.side_effect = lambda: order.append(
            ("listener_closed", serve.WORKBENCH_API)
        )
        server.drain_request_threads.side_effect = lambda timeout: order.append(
            ("handlers_drained", serve.WORKBENCH_API, timeout)
        ) or True

        previous_api = serve.WORKBENCH_API
        try:
            with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                serve, "PORT", 8080
            ), mock.patch.object(serve, "fwi_run_root"), mock.patch.object(
                serve, "ReusableThreadingTCPServer", return_value=server
            ), mock.patch.object(
                serve,
                "create_workbench_runtime",
                return_value=serve.WorkbenchRuntime(composed_api, supervisor),
            ), mock.patch.object(
                serve.webbrowser, "open", return_value=False
            ), mock.patch("builtins.print"):
                with self.assertRaisesRegex(
                    RuntimeError, "RUNTIME_SUPERVISOR_STOP_TIMEOUT"
                ):
                    serve.serve_workbench()
        finally:
            self.assertIsNone(serve.WORKBENCH_API)
            serve.WORKBENCH_API = previous_api

        self.assertEqual(
            order,
            [
                ("listener_closed", composed_api),
                ("supervisor_stop_failed", composed_api),
                (
                    "handlers_drained",
                    composed_api,
                    serve.HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS,
                ),
            ],
        )

    def test_server_listener_close_and_request_drain_are_separate_and_bounded(self):
        server = object.__new__(serve.ReusableThreadingTCPServer)
        server.socket = mock.Mock()
        request_thread = mock.Mock()
        request_thread.is_alive.return_value = True
        server._threads = [request_thread]

        server.close_listener()
        self.assertFalse(server.drain_request_threads(0.001))

        server.socket.close.assert_called_once_with()
        request_thread.join.assert_called_once()
        self.assertLessEqual(request_thread.join.call_args.args[0], 0.001)
        self.assertFalse(server.daemon_threads)
        self.assertTrue(server.block_on_close)

    def test_real_server_with_no_requests_closes_without_thread_sentinel_error(self):
        server = serve.ReusableThreadingTCPServer(
            ("127.0.0.1", 0), serve.Handler
        )
        try:
            self.assertNotIn("_threads", vars(server))
            server.close_listener()
            self.assertEqual(server.socket.fileno(), -1)
            self.assertTrue(server.sse_stop_event.is_set())
            self.assertTrue(server.drain_request_threads(0.01))
        finally:
            if server.socket.fileno() >= 0:
                server.server_close()

    def test_active_nonterminal_event_stream_stops_before_handler_drain_bound(self):
        class IdleApplication(_Application):
            def list_events(self, task_id, *, after_sequence=0, limit=100):
                self.event_reads.append((task_id, after_sequence, limit))
                return []

        previous_api = serve.WORKBENCH_API
        server = serve.ReusableThreadingTCPServer(
            ("127.0.0.1", 0), serve.Handler
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        connection = None
        try:
            port = server.server_address[1]
            csrf = "active-stream-shutdown-csrf"
            serve.WORKBENCH_API = WorkbenchAPI(
                IdleApplication(),
                csrf_token=csrf,
                allowed_hosts={f"127.0.0.1:{port}"},
                allowed_origins={f"http://127.0.0.1:{port}"},
            )
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(
                "GET",
                "/api/scientific-runtime/v1/tasks/task-idle/events/stream",
                headers={
                    "Accept": "text/event-stream",
                    "X-Workbench-CSRF": csrf,
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(len(b"retry: 1000\n\n")), b"retry: 1000\n\n")
            self.assertLess(
                serve.SSE_STREAM_WRITE_TIMEOUT_SECONDS,
                serve.HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS,
            )

            server.shutdown()
            started = time.monotonic()
            server.close_listener()
            self.assertTrue(server.drain_request_threads(2.0))
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertTrue(server.sse_stop_event.is_set())
        finally:
            if connection is not None:
                connection.close()
            if thread.is_alive():
                server.shutdown()
            if server.socket.fileno() >= 0:
                server.close_listener()
            server.drain_request_threads(2.0)
            thread.join(timeout=2)
            serve.WORKBENCH_API = previous_api

    def test_planned_finite_event_stream_close_has_explicit_marker(self):
        class IdleApplication(_Application):
            def list_events(self, task_id, *, after_sequence=0, limit=100):
                self.event_reads.append((task_id, after_sequence, limit))
                return []

        previous_api = serve.WORKBENCH_API
        server = serve.ReusableThreadingTCPServer(
            ("127.0.0.1", 0), serve.Handler
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        connection = None
        try:
            port = server.server_address[1]
            csrf = "finite-stream-marker-csrf"
            serve.WORKBENCH_API = WorkbenchAPI(
                IdleApplication(),
                csrf_token=csrf,
                allowed_hosts={f"127.0.0.1:{port}"},
                allowed_origins={f"http://127.0.0.1:{port}"},
            )
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            with mock.patch.object(serve, "SSE_STREAM_MAX_SECONDS", 0.05), mock.patch.object(
                serve, "SSE_STREAM_POLL_INTERVAL_SECONDS", 0.01
            ):
                connection.request(
                    "GET",
                    "/api/scientific-runtime/v1/tasks/task-idle/events/stream",
                    headers={
                        "Accept": "text/event-stream",
                        "X-Workbench-CSRF": csrf,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                body = response.read()
            self.assertTrue(body.startswith(b"retry: 1000\n\n"))
            self.assertTrue(body.endswith(b": stream-end\n\n"))
        finally:
            if connection is not None:
                connection.close()
            server.shutdown()
            server.close_listener()
            server.drain_request_threads(2.0)
            thread.join(timeout=2)
            serve.WORKBENCH_API = previous_api

    def test_shutdown_signal_ignores_both_signals_before_unwinding(self):
        installed = {}
        previous = {
            serve.signal.SIGINT: mock.sentinel.previous_int,
            serve.signal.SIGTERM: mock.sentinel.previous_term,
        }

        def install(signum, handler):
            installed[signum] = handler

        with mock.patch.object(
            serve.signal, "getsignal", side_effect=lambda signum: previous[signum]
        ), mock.patch.object(serve.signal, "signal", side_effect=install):
            captured = serve._install_shutdown_signal_handlers()
            term_handler = installed[serve.signal.SIGTERM]
            with self.assertRaises(serve._TerminationRequested):
                term_handler(serve.signal.SIGTERM, None)
            self.assertIs(installed[serve.signal.SIGINT], serve.signal.SIG_IGN)
            self.assertIs(installed[serve.signal.SIGTERM], serve.signal.SIG_IGN)
            serve._begin_shutdown_cleanup(captured)
            term_handler(serve.signal.SIGTERM, None)
            serve._restore_shutdown_signal_handlers(captured)

        self.assertEqual(captured.previous, previous)
        self.assertTrue(captured.cleaning)
        self.assertIs(installed[serve.signal.SIGINT], previous[serve.signal.SIGINT])
        self.assertIs(installed[serve.signal.SIGTERM], previous[serve.signal.SIGTERM])

    def test_busy_port_fails_before_runtime_recovery(self):
        previous_api = serve.WORKBENCH_API
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            port = occupied.getsockname()[1]
            recovery = mock.Mock()
            serve.WORKBENCH_API = mock.sentinel.stale_api
            try:
                with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                    serve, "PORT", port
                ), mock.patch.object(
                    serve, "fwi_run_root"
                ), mock.patch.object(
                    serve, "create_workbench_runtime", recovery
                ):
                    with self.assertRaises(OSError):
                        serve.serve_workbench()
            finally:
                self.assertIsNone(serve.WORKBENCH_API)
                serve.WORKBENCH_API = previous_api
            recovery.assert_not_called()

    def test_recovery_failure_closes_bound_server_and_keeps_api_private(self):
        order = []

        class BoundServer:
            def __init__(self, address, handler, *, bind_and_activate):
                order.append(("constructed", address, bind_and_activate))
                self.runtime_supervisor = None

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activated",))

            def serve_forever(self):
                order.append(("served",))

            def close_listener(self):
                order.append(("listener_closed",))

            def drain_request_threads(self, timeout):
                order.append(("handlers_drained", timeout))
                return True

        def fail_recovery():
            self.assertIsNone(serve.WORKBENCH_API)
            order.append(("recovery_failed",))
            raise RuntimeError("bounded recovery failed")

        previous_api = serve.WORKBENCH_API
        serve.WORKBENCH_API = mock.sentinel.stale_api
        try:
            with mock.patch.object(serve, "HOST", "127.0.0.1"), mock.patch.object(
                serve, "PORT", 8080
            ), mock.patch.object(
                serve, "fwi_run_root"
            ), mock.patch.object(
                serve, "ReusableThreadingTCPServer", BoundServer
            ), mock.patch.object(
                serve, "create_workbench_runtime", side_effect=fail_recovery
            ):
                with self.assertRaisesRegex(RuntimeError, "bounded recovery failed"):
                    serve.serve_workbench()
        finally:
            self.assertIsNone(serve.WORKBENCH_API)
            serve.WORKBENCH_API = previous_api

        self.assertEqual(
            order,
            [
                ("constructed", ("127.0.0.1", 8080), False),
                ("bound",),
                ("recovery_failed",),
                ("listener_closed",),
                ("handlers_drained", serve.HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS),
            ],
        )

    def test_database_path_must_not_overlap_run_root_and_parent_must_be_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "runs"
            run_root.mkdir(mode=0o700)
            overlapping = run_root / "job" / "tasks.csv"
            with mock.patch.dict(
                os.environ,
                {"SCIENTIFIC_RUNTIME_DB_PATH": str(overlapping)},
            ):
                with self.assertRaises(ValueError):
                    serve.validated_scientific_runtime_database_path(run_root)

            state = root / "state"
            state.mkdir(mode=0o700)
            database = state / "tasks.sqlite3"
            with mock.patch.dict(
                os.environ,
                {"SCIENTIFIC_RUNTIME_DB_PATH": str(database)},
            ):
                self.assertEqual(
                    serve.validated_scientific_runtime_database_path(run_root),
                    database,
                )
            with mock.patch.dict(
                os.environ,
                {"SCIENTIFIC_RUNTIME_DB_PATH": str(database)},
            ), mock.patch.object(
                serve.os, "geteuid", return_value=os.geteuid() + 1
            ):
                with self.assertRaises(ValueError):
                    serve.validated_scientific_runtime_database_path(run_root)


if __name__ == "__main__":
    unittest.main()
