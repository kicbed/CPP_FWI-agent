#!/usr/bin/env python3
"""
Lab Agent Workbench HTTP Server.

Serves the frontend and provides a simple HTTP server.
Usage: python3 web/serve.py [port]
Default port: 8080
"""

import http.server
import socketserver
import os
import re
import sys
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
HOST = os.environ.get("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
ALLOW_ORIGIN = os.environ.get("WEB_ALLOW_ORIGIN", "").strip()
WEB_DIR = Path(__file__).parent.resolve()
DEFAULT_FWI_RUN_ROOT = Path("/root/fwi-runs")
FWI_ARTIFACT_PREFIX = "/fwi-artifacts/"
FWI_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
FWI_CONTENT_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".png": "image/png",
}


def fwi_run_root():
    """Return the configured artifact root without exposing other filesystem roots."""
    configured = os.environ.get("FWI_RUN_ROOT", str(DEFAULT_FWI_RUN_ROOT))
    return Path(configured).expanduser().resolve()


def is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # Same-origin is sufficient for the Web UI and FWI artifacts. An
        # explicit origin can be enabled for a trusted development client.
        if ALLOW_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/fwi-artifacts" or request_path.startswith(FWI_ARTIFACT_PREFIX):
            self._serve_fwi_artifact(request_path, send_body=True)
            return
        super().do_GET()

    def do_HEAD(self):
        request_path = urlsplit(self.path).path
        if request_path == "/fwi-artifacts" or request_path.startswith(FWI_ARTIFACT_PREFIX):
            self._serve_fwi_artifact(request_path, send_body=False)
            return
        super().do_HEAD()

    def _serve_fwi_artifact(self, request_path, send_body):
        """Serve a single allow-listed file below FWI_RUN_ROOT/<job_id>."""
        if request_path == "/fwi-artifacts":
            self.send_error(http.HTTPStatus.NOT_FOUND)
            return

        try:
            decoded_path = unquote(request_path, errors="strict")
        except (UnicodeDecodeError, ValueError):
            self.send_error(http.HTTPStatus.BAD_REQUEST, "Invalid artifact URL encoding")
            return

        if "\x00" in decoded_path or "\\" in decoded_path:
            self.send_error(http.HTTPStatus.FORBIDDEN, "Invalid artifact path")
            return

        remainder = decoded_path[len(FWI_ARTIFACT_PREFIX):]
        parts = remainder.split("/")
        if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
            self.send_error(http.HTTPStatus.FORBIDDEN, "Invalid artifact path")
            return

        job_id, relative_parts = parts[0], parts[1:]
        if not FWI_JOB_ID_PATTERN.fullmatch(job_id):
            self.send_error(http.HTTPStatus.FORBIDDEN, "Invalid FWI job id")
            return

        suffix = Path(relative_parts[-1]).suffix.lower()
        content_type = FWI_CONTENT_TYPES.get(suffix)
        if content_type is None:
            self.send_error(http.HTTPStatus.FORBIDDEN, "Artifact type is not allowed")
            return

        root = fwi_run_root()
        unresolved_job_root = root / job_id
        if unresolved_job_root.is_symlink():
            self.send_error(http.HTTPStatus.FORBIDDEN, "FWI job directory cannot be a symlink")
            return
        job_root = unresolved_job_root.resolve()
        if not is_within(job_root, root):
            self.send_error(http.HTTPStatus.FORBIDDEN, "Artifact path escapes run root")
            return

        candidate = job_root.joinpath(*relative_parts)
        try:
            target = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            self.send_error(http.HTTPStatus.NOT_FOUND, "Artifact not found")
            return

        # Resolving before opening rejects symlink traversal outside this job. A
        # directory is never served, so SimpleHTTPRequestHandler cannot list it.
        if not is_within(target, job_root):
            self.send_error(http.HTTPStatus.FORBIDDEN, "Artifact path escapes job directory")
            return
        if target.suffix.lower() != suffix:
            self.send_error(http.HTTPStatus.FORBIDDEN, "Artifact symlink changes file type")
            return
        if not target.is_file():
            self.send_error(http.HTTPStatus.NOT_FOUND, "Artifact not found")
            return

        try:
            artifact = target.open("rb")
        except OSError:
            self.send_error(http.HTTPStatus.NOT_FOUND, "Artifact not readable")
            return

        try:
            size = os.fstat(artifact.fileno()).st_size
            self.send_response(http.HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if send_body:
                self.copyfile(artifact, self.wfile)
        finally:
            artifact.close()

    def log_message(self, format, *args):
        # Quiet logging
        pass

class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    """Allow a clean stop/start cycle without waiting for TCP TIME_WAIT."""

    allow_reuse_address = True
    daemon_threads = True


def main():
    # Bind to loopback by default. Failing on a busy port is intentional: the
    # one-click launcher can then roll back instead of reporting the wrong URL.
    with ReusableThreadingTCPServer((HOST, PORT), Handler) as httpd:
        display_host = "localhost" if HOST in {"0.0.0.0", "::", "127.0.0.1", "::1"} else HOST
        url = f"http://{display_host}:{PORT}"
        print(f"\033[1;36m┌─────────────────────────────────────────┐\033[0m")
        print(f"\033[1;36m│\033[0m  🌐 Lab Agent Workbench 已启动          \033[1;36m│\033[0m")
        print(f"\033[1;36m│\033[0m  📍 {url:<33} \033[1;36m│\033[0m")
        print(f"\033[1;36m│\033[0m  按 Ctrl+C 停止                         \033[1;36m│\033[0m")
        print(f"\033[1;36m└─────────────────────────────────────────┘\033[0m")

        try:
            webbrowser.open(url)
        except Exception:
            pass

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\033[1;33mWeb UI 服务器已停止\033[0m")
            httpd.shutdown()

if __name__ == '__main__':
    main()
