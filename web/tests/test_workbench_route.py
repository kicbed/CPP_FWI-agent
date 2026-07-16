#!/usr/bin/env python3
"""HTTP integration checks for the Guided Workbench server wiring."""

import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from web import serve
from web.workbench_api import WorkbenchAPI


class _Application:
    def __init__(self):
        self.created = []
        self.listed = []
        self.purged = []

    def session_capabilities(self):
        return {
            "mode": "guided",
            "scope": {"project_id": "local-workbench", "principal_id": "local-user"},
            "capabilities": {"cancel": False, "retry": False, "sse": False},
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
            self.assertEqual(order, [("api",)])
            order.append(("recover", max_tasks))

        application.recover_runtime_on_startup.side_effect = (
            recover_runtime_on_startup
        )
        composed_api = mock.sentinel.composed_api

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
            serve, "WorkbenchAPI", side_effect=compose_api
        ):
            result = serve.create_workbench_api()

        self.assertIs(result, composed_api)
        application.recover_runtime_on_startup.assert_called_once_with(
            max_tasks=10000
        )
        self.assertEqual(order, [("api",), ("recover", 10000)])

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

        class BoundServer:
            def __init__(self, address, handler, *, bind_and_activate):
                order.append(("constructed", address, handler, bind_and_activate))

            def __enter__(self):
                order.append(("entered",))
                return self

            def __exit__(self, exc_type, exc, traceback):
                order.append(("closed", exc_type))
                return False

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activated",))

            def serve_forever(self):
                order.append(("served", serve.WORKBENCH_API))

        def compose_after_bind():
            self.assertEqual(
                order[0][:2], ("constructed", ("127.0.0.1", 8080))
            )
            self.assertFalse(order[0][3])
            self.assertEqual(order[1], ("entered",))
            self.assertEqual(order[2], ("bound",))
            self.assertIsNone(serve.WORKBENCH_API)
            order.append(("recovered",))
            return composed_api

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
                serve, "create_workbench_api", side_effect=compose_after_bind
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
                "entered",
                "bound",
                "recovered",
                "activated",
                "served",
                "closed",
            ],
        )
        self.assertIs(order[5][1], composed_api)
        self.assertIsNone(order[6][1])

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
                    serve, "create_workbench_api", recovery
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

            def __enter__(self):
                order.append(("entered",))
                return self

            def __exit__(self, exc_type, exc, traceback):
                order.append(("closed", exc_type))
                return False

            def server_bind(self):
                order.append(("bound",))

            def server_activate(self):
                order.append(("activated",))

            def serve_forever(self):
                order.append(("served",))

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
                serve, "create_workbench_api", side_effect=fail_recovery
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
                ("entered",),
                ("bound",),
                ("recovery_failed",),
                ("closed", RuntimeError),
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
