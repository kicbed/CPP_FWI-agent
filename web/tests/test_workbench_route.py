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
