#!/usr/bin/env python3
"""Integration tests for the allow-listed FWI artifact HTTP route."""

import base64
import http.client
import http.server
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fwi_web_serve", WEB_DIR / "serve.py")
serve = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serve)


class ArtifactRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.previous_root = os.environ.get("FWI_RUN_ROOT")
        cls.previous_allow_origin = serve.ALLOW_ORIGIN
        serve.ALLOW_ORIGIN = ""
        cls.run_root = Path(cls.temp_dir.name) / "runs"
        cls.job_id = "fwi-test-20260714"
        cls.job_dir = cls.run_root / cls.job_id
        figures = cls.job_dir / "figures"
        figures.mkdir(parents=True)

        cls.png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        (figures / "result.png").write_bytes(cls.png_bytes)
        (cls.job_dir / "status.json").write_text(
            json.dumps({"job_id": cls.job_id, "status": "succeeded"}), encoding="utf-8"
        )
        (cls.job_dir / "loss.csv").write_text("iteration,loss\n0,1.0\n", encoding="utf-8")
        (cls.job_dir / "forbidden.txt").write_text("not served", encoding="utf-8")

        cls.outside_dir = Path(cls.temp_dir.name) / "outside"
        cls.outside_dir.mkdir()
        (cls.outside_dir / "secret.png").write_bytes(cls.png_bytes)
        (figures / "escape.png").symlink_to(cls.outside_dir / "secret.png")
        (figures / "type-confusion.png").symlink_to(cls.job_dir / "forbidden.txt")

        cls.other_job = cls.run_root / "other-job"
        cls.other_job.mkdir()
        (cls.other_job / "status.json").write_text('{"secret": true}', encoding="utf-8")
        (cls.run_root / "linked-job").symlink_to(cls.other_job, target_is_directory=True)

        os.environ["FWI_RUN_ROOT"] = str(cls.run_root)
        cls.server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        if cls.previous_root is None:
            os.environ.pop("FWI_RUN_ROOT", None)
        else:
            os.environ["FWI_RUN_ROOT"] = cls.previous_root
        serve.ALLOW_ORIGIN = cls.previous_allow_origin
        cls.temp_dir.cleanup()

    def request(self, path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, headers, body

    def test_png_json_csv_and_head_are_served_with_exact_types(self):
        status, headers, body = self.request(
            f"/fwi-artifacts/{self.job_id}/figures/result.png"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        policy = headers["content-security-policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("script-src 'self' 'unsafe-inline'", policy)
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)
        self.assertIn("https://cdn.jsdelivr.net", policy)
        self.assertNotIn("localhost:6000", policy)
        self.assertNotIn("127.0.0.1:6000", policy)
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(body, self.png_bytes)

        status, headers, body = self.request(
            f"/fwi-artifacts/{self.job_id}/%73tatus.json"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body), {"job_id": self.job_id, "status": "succeeded"})

        status, headers, body = self.request(f"/fwi-artifacts/{self.job_id}/loss.csv")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn(b"iteration,loss", body)

        status, headers, body = self.request(
            f"/fwi-artifacts/{self.job_id}/figures/result.png", method="HEAD"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertEqual(body, b"")

    def test_traversal_absolute_paths_and_env_are_rejected(self):
        rejected = [
            f"/fwi-artifacts/{self.job_id}/%2e%2e/other-job/status.json",
            f"/fwi-artifacts/{self.job_id}/%2Froot/.env",
            f"/fwi-artifacts/{self.job_id}/..%2F..%2Froot%2Ffwi-data%2Fmodels%2Fmarmousi_94_288.json",
            f"/fwi-artifacts/{self.job_id}/.env",
            f"/fwi-artifacts/{self.job_id}/forbidden.txt",
        ]
        for path in rejected:
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertIn(status, (403, 404))

        status, _, _ = self.request("/root/.env")
        self.assertEqual(status, 404)

    def test_symlink_escape_and_symlink_job_are_rejected(self):
        status, _, _ = self.request(
            f"/fwi-artifacts/{self.job_id}/figures/escape.png"
        )
        self.assertEqual(status, 403)

        status, _, _ = self.request("/fwi-artifacts/linked-job/status.json")
        self.assertEqual(status, 403)

        status, _, _ = self.request(
            f"/fwi-artifacts/{self.job_id}/figures/type-confusion.png"
        )
        self.assertEqual(status, 403)

    def test_missing_job_and_directory_listing_are_not_served(self):
        status, _, _ = self.request("/fwi-artifacts/missing-job/status.json")
        self.assertEqual(status, 404)

        status, _, _ = self.request(f"/fwi-artifacts/{self.job_id}/figures")
        self.assertIn(status, (403, 404))

        status, _, _ = self.request("/fwi-artifacts")
        self.assertEqual(status, 404)

    def test_cross_origin_access_is_opt_in(self):
        path = f"/fwi-artifacts/{self.job_id}/status.json"
        status, headers, _ = self.request(path)
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)

        serve.ALLOW_ORIGIN = "http://127.0.0.1:9090"
        try:
            status, headers, _ = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(
                headers.get("access-control-allow-origin"),
                "http://127.0.0.1:9090",
            )
            self.assertEqual(headers.get("vary"), "Origin")
        finally:
            serve.ALLOW_ORIGIN = ""

    def test_embedding_health_is_same_origin_sanitized_and_loopback_only(self):
        class UpstreamHealth(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/health":
                    self.send_error(404)
                    return
                payload = json.dumps({
                    "status": "ok",
                    "model_loaded": True,
                    "dimension": 1024,
                    "model": "Qwen/Qwen3-Embedding-0.6B",
                    "device": "cpu",
                    "ignored": "must-not-be-proxied",
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHealth)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        keys = ("ENABLE_LOCAL_EMBEDDING", "ROUTING_MODE", "EMBEDDING_PROVIDER",
                "LOCAL_EMBEDDING_URL")
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["ENABLE_LOCAL_EMBEDDING"] = "true"
            os.environ["LOCAL_EMBEDDING_URL"] = (
                f"http://127.0.0.1:{upstream.server_address[1]}"
            )
            status, headers, body = self.request(serve.EMBEDDING_HEALTH_PATH)
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
            health = json.loads(body)
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["dimension"], 1024)
            self.assertEqual(health["device"], "cpu")
            self.assertNotIn("ignored", health)

            status, _, body = self.request(serve.EMBEDDING_HEALTH_PATH, method="HEAD")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"")

            # A deployment value can never turn this endpoint into an SSRF
            # proxy. Non-loopback targets fail closed without being contacted.
            os.environ["LOCAL_EMBEDDING_URL"] = "http://example.com:6000"
            _, _, body = self.request(serve.EMBEDDING_HEALTH_PATH)
            self.assertEqual(json.loads(body)["status"], "misconfigured")

            os.environ["ENABLE_LOCAL_EMBEDDING"] = "false"
            _, _, body = self.request(serve.EMBEDDING_HEALTH_PATH)
            disabled = json.loads(body)
            self.assertFalse(disabled["enabled"])
            self.assertEqual(disabled["status"], "disabled")
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=5)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_unsafe_run_roots_are_rejected(self):
        original = os.environ.get("FWI_RUN_ROOT")
        try:
            for unsafe in ("/", "/etc", str(WEB_DIR.parent)):
                with self.subTest(root=unsafe):
                    os.environ["FWI_RUN_ROOT"] = unsafe
                    with self.assertRaises(ValueError):
                        serve.fwi_run_root()
        finally:
            if original is None:
                os.environ.pop("FWI_RUN_ROOT", None)
            else:
                os.environ["FWI_RUN_ROOT"] = original


if __name__ == "__main__":
    unittest.main()
