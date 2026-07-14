#!/usr/bin/env python3
"""
Lab Agent Workbench HTTP Server.

Serves the frontend and provides a simple HTTP server.
Usage: python3 web/serve.py [port]
Default port: 8080
"""

import http.server
import http.client
import socketserver
import json
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
EMBEDDING_HEALTH_PATH = "/api/embedding-health"
MAX_EMBEDDING_HEALTH_BYTES = 32 * 1024
FWI_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
FWI_CONTENT_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".png": "image/png",
}
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net",
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:",
        "img-src 'self' data:",
        "connect-src 'self' http://127.0.0.1:5000 http://127.0.0.1:50052 "
        "http://localhost:5000 http://localhost:50052 http://localhost:8500 "
        "http://localhost:5010 http://localhost:5011",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )
)


def local_embedding_enabled():
    """Mirror the launcher's opt-in/auto semantics without reading any secret."""
    configured = os.environ.get("ENABLE_LOCAL_EMBEDDING", "auto").strip().lower()
    if configured == "true":
        return True
    if configured == "false":
        return False
    if configured != "auto":
        return False
    return (os.environ.get("ROUTING_MODE", "fixed").strip().lower() == "agent-rag" and
            os.environ.get("EMBEDDING_PROVIDER", "local").strip().lower() == "local")


def parse_local_embedding_url(value):
    """Return a fixed loopback host/port; never proxy an arbitrary URL."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or
            parsed.username is not None or parsed.password is not None or port is None or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        return None
    if not 1 <= port <= 65535:
        return None
    # Resolve localhost ambiguity and ensure the health proxy can never leave
    # the IPv4 loopback interface even when host resolver settings differ.
    return "127.0.0.1", port


def embedding_health():
    """Return a small, sanitized view of the optional local service health."""
    enabled = local_embedding_enabled()
    result = {
        "type": "embedding_health",
        "provider": "local",
        "enabled": enabled,
        "status": "disabled" if not enabled else "unavailable",
        "model_loaded": False,
        "dimension": 0,
    }
    if not enabled:
        return result

    target = parse_local_embedding_url(
        os.environ.get("LOCAL_EMBEDDING_URL", "http://127.0.0.1:6000").strip()
    )
    if target is None:
        result["status"] = "misconfigured"
        return result

    connection = http.client.HTTPConnection(target[0], target[1], timeout=1.5)
    try:
        connection.request("GET", "/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_EMBEDDING_HEALTH_BYTES + 1)
        if response.status != http.HTTPStatus.OK or len(body) > MAX_EMBEDDING_HEALTH_BYTES:
            return result
        upstream = json.loads(body.decode("utf-8"))
        if not isinstance(upstream, dict):
            return result
        dimension = upstream.get("dimension", 0)
        model_loaded = upstream.get("model_loaded") is True
        if (upstream.get("status") != "ok" or not model_loaded or
                not isinstance(dimension, int) or isinstance(dimension, bool) or
                not 1 <= dimension <= 65536):
            return result
        result.update({
            "status": "ok",
            "model_loaded": True,
            "dimension": dimension,
        })
        model = upstream.get("model")
        if isinstance(model, str) and model and len(model) <= 200:
            result["model"] = model
        device = upstream.get("device")
        if device in ("cpu", "cuda"):
            result["device"] = device
        return result
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError,
            http.client.HTTPException):
        return result
    finally:
        connection.close()


def fwi_run_root():
    """Return the configured artifact root without exposing other filesystem roots."""
    configured = os.environ.get("FWI_RUN_ROOT", str(DEFAULT_FWI_RUN_ROOT))
    unresolved = Path(configured).expanduser()
    if not unresolved.is_absolute() or unresolved.is_symlink():
        raise ValueError("FWI_RUN_ROOT must be an absolute non-symlink directory")
    root = unresolved.resolve(strict=True)
    if not root.is_dir() or root.parent == Path("/"):
        raise ValueError("FWI_RUN_ROOT must be a dedicated directory")

    project_root = WEB_DIR.parent
    home = Path.home().resolve()
    forbidden = tuple(Path(value) for value in (
        "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32",
        "/lib64", "/boot", "/proc", "/sys", "/dev", "/run",
    ))
    if any(root == value or is_within(root, value) for value in forbidden):
        raise ValueError("FWI_RUN_ROOT is inside a sensitive system directory")
    if (root == Path("/") or root == Path("/var") or root == project_root or
            is_within(root, project_root) or is_within(project_root, root) or
            root == home or is_within(home, root)):
        raise ValueError("FWI_RUN_ROOT overlaps a protected directory")
    return root


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
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == EMBEDDING_HEALTH_PATH:
            self._serve_embedding_health(send_body=True)
            return
        if request_path == "/fwi-artifacts" or request_path.startswith(FWI_ARTIFACT_PREFIX):
            self._serve_fwi_artifact(request_path, send_body=True)
            return
        super().do_GET()

    def do_HEAD(self):
        request_path = urlsplit(self.path).path
        if request_path == EMBEDDING_HEALTH_PATH:
            self._serve_embedding_health(send_body=False)
            return
        if request_path == "/fwi-artifacts" or request_path.startswith(FWI_ARTIFACT_PREFIX):
            self._serve_fwi_artifact(request_path, send_body=False)
            return
        super().do_HEAD()

    def _serve_embedding_health(self, send_body):
        payload = json.dumps(embedding_health(), ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if send_body:
            self.wfile.write(payload)

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

        try:
            root = fwi_run_root()
        except (OSError, RuntimeError, ValueError):
            self.send_error(http.HTTPStatus.INTERNAL_SERVER_ERROR,
                            "FWI artifact root is not safely configured")
            return
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
    fwi_run_root()
    with ReusableThreadingTCPServer((HOST, PORT), Handler) as httpd:
        # Keep the browser Origin aligned with AGENT_CORS_ORIGIN. In
        # particular, do not rewrite 127.0.0.1 to localhost after startup.
        display_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
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
