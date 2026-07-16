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
import secrets
import signal
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

WEB_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = WEB_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    # ``python web/serve.py`` otherwise exposes only ``web/`` on sys.path.
    # Resolve the trusted repository root from this file, never from cwd or a
    # request value.
    sys.path.insert(0, str(PROJECT_ROOT))

from scientific_runtime import (  # noqa: E402
    DeepwaveAdapter,
    DeepwaveTaskDispatcher,
    RegistryService,
    RuntimeRecoveryResult,
    RuntimeSupervisor,
    SQLiteTaskStore,
    TaskService,
    register_verified_fwi_baseline,
)
from scientific_runtime.workbench_service import GuidedWorkbench  # noqa: E402
from web.workbench_api import (  # noqa: E402
    API_PREFIX as WORKBENCH_API_PREFIX,
    APIResponse,
    MAX_JSON_BYTES as WORKBENCH_MAX_JSON_BYTES,
    WorkbenchAPI,
)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
HOST = os.environ.get("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
ALLOW_ORIGIN = os.environ.get("WEB_ALLOW_ORIGIN", "").strip()
DEFAULT_FWI_RUN_ROOT = Path("/root/fwi-runs")
FWI_ARTIFACT_PREFIX = "/fwi-artifacts/"
EMBEDDING_HEALTH_PATH = "/api/embedding-health"
MAX_EMBEDDING_HEALTH_BYTES = 32 * 1024
HTTP_REQUEST_TIMEOUT_SECONDS = 10.0
WORKBENCH_BODY_TIMEOUT_SECONDS = 5.0
HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS = 10.0
FWI_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
STABLE_RUNTIME_FAILURE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
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
        "img-src 'self' data: blob:",
        "connect-src 'self' http://127.0.0.1:5000 http://127.0.0.1:50052 "
        "http://localhost:5000 http://localhost:50052 http://localhost:8500 "
        "http://localhost:5010 http://localhost:5011",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )
)

# Set exactly once by ``main``. Importing this module for route tests remains
# read-only and does not create a database or probe the numerical runtime.
WORKBENCH_API = None


@dataclass(frozen=True)
class WorkbenchRuntime:
    """Composed HTTP facade and its side-effect-free supervisor handle."""

    api: WorkbenchAPI
    supervisor: RuntimeSupervisor


class _TerminationRequested(BaseException):
    """Unwind the main server loop without being caught as an app failure."""


@dataclass
class _ShutdownSignalState:
    """Previous handlers plus a latch that makes cleanup non-interruptible."""

    previous: dict
    cleaning: bool = False


def _workbench_error_response(status, code, message):
    payload = json.dumps(
        {"ok": False, "error": {"code": code, "message": message}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return APIResponse(
        status=int(status),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        body=payload,
    )


def scientific_runtime_database_path():
    """Return the server-owned durable task database path.

    ``SQLiteTaskStore`` performs the authoritative no-symlink/private-parent
    validation.  This server also rejects unsafe deployment overlap and
    ownership before composing the store.  Browser requests can never select
    the path.
    """

    default_state_home = Path.home() / ".local" / "state"
    state_home = Path(os.environ.get("XDG_STATE_HOME", str(default_state_home))).expanduser()
    default_path = state_home / "cpp-fwi-agent" / "scientific-runtime" / "tasks.sqlite3"
    return Path(os.environ.get("SCIENTIFIC_RUNTIME_DB_PATH", str(default_path))).expanduser()


def _paths_overlap(left, right):
    left = Path(left)
    right = Path(right)
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def validated_scientific_runtime_database_path(run_root):
    """Fail closed before TaskStore creation for direct-server launches."""

    database_path = scientific_runtime_database_path()
    if not database_path.is_absolute():
        raise ValueError("SCIENTIFIC_RUNTIME_DB_PATH must be absolute")
    canonical_database = database_path.resolve(strict=False)
    canonical_run_root = Path(run_root).resolve(strict=True)
    critical_roots = tuple(
        Path(value)
        for value in (
            "/etc",
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib32",
            "/lib64",
            "/boot",
            "/proc",
            "/sys",
            "/dev",
            "/run",
        )
    )
    if any(
        canonical_database == root or root in canonical_database.parents
        for root in critical_roots
    ):
        raise ValueError("SCIENTIFIC_RUNTIME_DB_PATH is inside a sensitive directory")
    home = Path.home().resolve()
    if (
        _paths_overlap(canonical_database, PROJECT_ROOT)
        or canonical_database == home
        or canonical_database in home.parents
        or canonical_database.parent == Path("/")
        or canonical_database == Path("/var")
        or canonical_database.parent == Path("/var")
    ):
        raise ValueError("SCIENTIFIC_RUNTIME_DB_PATH is not a dedicated state path")
    if _paths_overlap(canonical_database, canonical_run_root):
        raise ValueError("SCIENTIFIC_RUNTIME_DB_PATH cannot overlap FWI_RUN_ROOT")

    owner_probe = canonical_database.parent
    while not owner_probe.exists():
        parent = owner_probe.parent
        if parent == owner_probe:
            break
        owner_probe = parent
    if owner_probe.exists():
        owner_status = os.stat(owner_probe, follow_symlinks=False)
        if owner_status.st_uid != os.geteuid() or owner_status.st_mode & 0o022:
            raise ValueError(
                "SCIENTIFIC_RUNTIME_DB_PATH must have a process-owned private parent"
            )
    return database_path


def create_workbench_runtime():
    """Compose the local Guided API and an unstarted fenced supervisor."""

    if HOST not in {"127.0.0.1", "localhost"}:
        raise ValueError("P1 Guided Workbench requires a loopback WEB_HOST")

    run_root = fwi_run_root()
    store = SQLiteTaskStore(validated_scientific_runtime_database_path(run_root))
    registry = RegistryService(store)
    project_id = "local-workbench"
    principal_id = "local-user"
    register_verified_fwi_baseline(
        registry,
        project_id=project_id,
        principals=[principal_id],
    )

    def registry_snapshot_provider(
        *, project_id, principal_id, dataset_id, dataset_version
    ):
        return registry.get_dataset(
            project_id=project_id,
            principal_id=principal_id,
            dataset_id=dataset_id,
            version=dataset_version,
            permission="execute",
        )

    adapter = DeepwaveAdapter(
        run_root=run_root,
        registry_snapshot_provider=registry_snapshot_provider,
    )
    tasks = TaskService(store, dispatcher=DeepwaveTaskDispatcher(adapter))
    application = GuidedWorkbench(
        tasks,
        registry,
        project_id=project_id,
        principal_id=principal_id,
    )
    browser_origin = os.environ.get(
        "AGENT_CORS_ORIGIN", f"http://{HOST}:{PORT}"
    ).strip()
    try:
        browser_host = urlsplit(browser_origin).netloc
    except ValueError as error:
        raise ValueError("AGENT_CORS_ORIGIN is not a valid Workbench origin") from error
    api = WorkbenchAPI(
        application,
        csrf_token=secrets.token_urlsafe(32),
        allowed_hosts={browser_host},
        allowed_origins={browser_origin},
    )
    supervisor = RuntimeSupervisor(
        tasks,
        project_id=project_id,
        principal_id=principal_id,
        owner_id=f"supervisor-{secrets.token_hex(16)}",
    )
    # Validate the complete HTTP boundary before the bounded pre-lease
    # inventory.  It is read-only; all runtime mutations start only after the
    # composed RuntimeSupervisor acquires its fenced term.
    recovery = application.recover_runtime_on_startup(max_tasks=10000)
    report_runtime_recovery(recovery)
    return WorkbenchRuntime(api=api, supervisor=supervisor)


def create_workbench_api():
    """Backward-compatible composition helper used by route-level tests."""

    return create_workbench_runtime().api


def report_runtime_recovery(recovery):
    """Write one path-free startup summary to the supervised server log."""

    if not isinstance(recovery, RuntimeRecoveryResult):
        return

    def code_counts(values):
        counts = {}
        for _, code in values:
            counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    summary = {
        "scanned": len(recovery.scanned_task_ids),
        "receipt_recovery_attempted": len(
            recovery.receipt_recovery_attempted_task_ids
        ),
        "receipt_recovered": len(recovery.receipt_recovered_task_ids),
        "pending_deferred": len(recovery.pending_deferred_task_ids),
        "dispatching_deferred": code_counts(recovery.dispatching_deferred),
        "status_refreshed": len(recovery.status_refreshed_task_ids),
        "status_refresh_failures": code_counts(
            recovery.status_refresh_failures
        ),
        "reconciliation_required": len(
            recovery.reconciliation_required_task_ids
        ),
    }
    try:
        print(
            "scientific runtime startup recovery: "
            + json.dumps(summary, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
        )
    except (OSError, ValueError):
        # Runtime state remains in SQLite; a closed supervisor stderr must not
        # turn a successful, side-effect-free summary into a startup failure.
        pass


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
    def setup(self):
        super().setup()
        # Bound slow request lines/headers as well as the stricter Guided body
        # read below.  BaseHTTPRequestHandler closes cleanly on TimeoutError.
        self.connection.settimeout(HTTP_REQUEST_TIMEOUT_SECONDS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # Same-origin is sufficient for the Web UI and FWI artifacts. An
        # explicit origin can be enabled for a trusted development client.
        workbench_response = self._is_workbench_target()
        if ALLOW_ORIGIN and not workbench_response:
            self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if not workbench_response:
            self.send_header('Cache-Control', 'no-cache')
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def do_OPTIONS(self):
        if self._is_workbench_target():
            self._serve_workbench("OPTIONS")
            return
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self._is_workbench_target():
            self._serve_workbench("GET")
            return
        request_path = urlsplit(self.path).path
        if request_path == EMBEDDING_HEALTH_PATH:
            self._serve_embedding_health(send_body=True)
            return
        if request_path == "/fwi-artifacts" or request_path.startswith(FWI_ARTIFACT_PREFIX):
            self._serve_fwi_artifact(request_path, send_body=True)
            return
        super().do_GET()

    def do_HEAD(self):
        if self._is_workbench_target():
            self._serve_workbench("HEAD", send_body=False)
            return
        request_path = urlsplit(self.path).path
        if request_path == EMBEDDING_HEALTH_PATH:
            self._serve_embedding_health(send_body=False)
            return
        if request_path == "/fwi-artifacts" or request_path.startswith(FWI_ARTIFACT_PREFIX):
            self._serve_fwi_artifact(request_path, send_body=False)
            return
        super().do_HEAD()

    def do_POST(self):
        if self._is_workbench_target():
            self._serve_workbench("POST")
            return
        self.send_error(http.HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        if self._is_workbench_target():
            self._serve_workbench("PUT")
            return
        self.send_error(http.HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        if self._is_workbench_target():
            self._serve_workbench("DELETE")
            return
        self.send_error(http.HTTPStatus.NOT_IMPLEMENTED)

    def do_PATCH(self):
        if self._is_workbench_target():
            self._serve_workbench("PATCH")
            return
        self.send_error(http.HTTPStatus.NOT_IMPLEMENTED)

    def _is_workbench_target(self):
        raw_path = getattr(self, "path", "").partition("?")[0]
        return (
            raw_path == WORKBENCH_API_PREFIX
            or raw_path.startswith(WORKBENCH_API_PREFIX + "/")
        )

    def _workbench_body(self):
        values = self.headers.get_all("Content-Length", failobj=[])
        if len(values) != 1 or not values[0].isdigit():
            return b""
        length = int(values[0])
        if length <= 0:
            return b""
        previous_timeout = self.connection.gettimeout()
        self.connection.settimeout(WORKBENCH_BODY_TIMEOUT_SECONDS)
        try:
            # WorkbenchAPI.preflight has already rejected a declared overrun;
            # keep the cap as defense in depth if this helper is reused.
            return self.rfile.read(min(length, WORKBENCH_MAX_JSON_BYTES + 1))
        finally:
            self.connection.settimeout(previous_timeout)

    def _send_workbench_response(self, response, send_body):
        self.send_response(response.status)
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(response.body)

    def _serve_workbench(self, method, send_body=True):
        # One handler owns one stable facade even while the main thread drains
        # requests during shutdown.  Never read the publication global twice.
        api = WORKBENCH_API
        if api is None:
            self.close_connection = True
            self._send_workbench_response(
                _workbench_error_response(
                    http.HTTPStatus.SERVICE_UNAVAILABLE,
                    "RUNTIME_UNAVAILABLE",
                    "scientific runtime is temporarily unavailable",
                ),
                send_body,
            )
            return

        early_response = api.preflight(
            method, self.path, self.headers.items()
        )
        if early_response is not None:
            # The body was deliberately not consumed.  Closing prevents any
            # remaining bytes from being interpreted as a second request if
            # the server protocol is upgraded from HTTP/1.0 in the future.
            self.close_connection = True
            self._send_workbench_response(early_response, send_body)
            return
        try:
            body = self._workbench_body()
        except socket.timeout:
            self.close_connection = True
            self._send_workbench_response(
                _workbench_error_response(
                    http.HTTPStatus.REQUEST_TIMEOUT,
                    "BODY_TIMEOUT",
                    "request body timed out",
                ),
                send_body,
            )
            return
        response = api.dispatch(method, self.path, self.headers.items(), body)
        self._send_workbench_response(response, send_body)

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
        # Hidden files are private implementation/control state, never Web
        # artifacts.  This is checked after decoding so encoded leading dots
        # cannot expose present or future Worker sidecars.
        if any(part.startswith(".") for part in relative_parts):
            self.send_error(http.HTTPStatus.FORBIDDEN, "Private artifact path")
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
    """Track request threads, close listening separately, and fail closed."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        self.runtime_supervisor = None
        super().__init__(*args, **kwargs)

    def service_actions(self):
        super().service_actions()
        threads = vars(self).get("_threads")
        if threads is not None:
            threads.reap()
        supervisor = self.runtime_supervisor
        if supervisor is not None and not supervisor.healthy:
            raise RuntimeError(
                supervisor.failure_code or "RUNTIME_SUPERVISOR_STOPPED"
            )

    def close_listener(self):
        """Close the listening socket without waiting on application handlers."""

        socketserver.TCPServer.server_close(self)

    def drain_request_threads(self, timeout=HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS):
        """Bound the cooperative drain while retaining non-daemon safety."""

        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("request drain timeout must be non-negative")
        deadline = time.monotonic() + float(timeout)
        # ThreadingMixIn exposes a non-iterable class-level _NoThreads sentinel
        # until its first request. Only an instance collection contains work.
        threads = tuple(vars(self).get("_threads", ()))
        for thread in threads:
            if thread is threading.current_thread():
                return False
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads)

    def server_close(self):
        """Compatibility close with the same bounded drain policy."""

        self.close_listener()
        return self.drain_request_threads()


def _install_shutdown_signal_handlers():
    """Translate TERM/INT into one cleanup-safe main-thread unwind."""

    if threading.current_thread() is not threading.main_thread():
        return _ShutdownSignalState(previous={})
    state = _ShutdownSignalState(previous={})

    def request_shutdown(signum, _frame):
        # Once either signal starts the unwind, both must be inert so a
        # different second signal cannot interrupt lease cleanup.
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            signal.signal(shutdown_signal, signal.SIG_IGN)
        if state.cleaning:
            return
        raise _TerminationRequested()

    for signum in (signal.SIGINT, signal.SIGTERM):
        state.previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)
    return state


def _begin_shutdown_cleanup(state):
    """Latch cleanup before making both shutdown signals inert."""

    state.cleaning = True
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, signal.SIG_IGN)
        except (OSError, ValueError):
            # The latch still makes an already-entered handler return normally.
            pass


def _restore_shutdown_signal_handlers(state):
    if threading.current_thread() is not threading.main_thread():
        return
    for signum, handler in state.previous.items():
        signal.signal(signum, handler)


def serve_workbench():
    """Bind, recover, fence, publish, then cooperatively stop with hard fallback."""

    global WORKBENCH_API
    # Never retain a previously composed API across a failed restart attempt.
    WORKBENCH_API = None
    fwi_run_root()
    # Resolve the accepted localhost spelling ourselves so a resolver/NSS
    # misconfiguration cannot turn the Guided bind into a non-loopback socket.
    bind_host = "127.0.0.1" if HOST == "localhost" else HOST
    shutdown_signals = _install_shutdown_signal_handlers()
    httpd = None
    supervisor = None
    primary_error = None
    graceful_stop = False
    try:
        httpd = ReusableThreadingTCPServer(
            (bind_host, PORT), Handler, bind_and_activate=False
        )
        # Bind first to prove that the configured port is available, but do
        # not listen yet: clients cannot connect while recovery is running.
        httpd.server_bind()
        # A busy-port failure therefore occurs before even the read-only
        # startup inventory. No handler can run until activation and
        # serve_forever.
        # Wildcard binds retain legacy/static Compose behavior but never
        # compose the unauthenticated Guided runtime on that socket.
        runtime = (
            create_workbench_runtime()
            if HOST in {"127.0.0.1", "localhost"}
            else None
        )
        supervisor = None if runtime is None else runtime.supervisor
        if supervisor is not None:
            if not supervisor.start():
                raise RuntimeError(
                    supervisor.failure_code
                    or "RUNTIME_SUPERVISOR_START_FAILED"
                )
            httpd.runtime_supervisor = supervisor
        # Recovery has completed. Only now activate the listening socket and
        # publish the API that handlers will see. If either step raises, the
        # context closes the socket and this global remains fail-closed.
        httpd.server_activate()
        WORKBENCH_API = None if runtime is None else runtime.api
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

        httpd.serve_forever()
    except (KeyboardInterrupt, _TerminationRequested):
        graceful_stop = True
    except BaseException as error:
        primary_error = error
    finally:
        _begin_shutdown_cleanup(shutdown_signals)
        cleanup_codes = []
        cleanup_causes = []
        if httpd is not None:
            # Stop accepting first, but never let an application handler block
            # the supervisor's cooperative stop and lease release.
            try:
                httpd.close_listener()
            except BaseException as error:
                cleanup_codes.append("HTTP_LISTENER_CLOSE_FAILED")
                cleanup_causes.append(error)
        if supervisor is not None:
            try:
                if not supervisor.stop():
                    failure_code = supervisor.failure_code
                    cleanup_codes.append(
                        failure_code
                        if isinstance(failure_code, str)
                        and STABLE_RUNTIME_FAILURE_CODE.fullmatch(failure_code)
                        else "RUNTIME_SUPERVISOR_STOP_FAILED"
                    )
            except BaseException as error:
                failure_code = getattr(error, "code", None)
                cleanup_codes.append(
                    failure_code
                    if isinstance(failure_code, str)
                    and STABLE_RUNTIME_FAILURE_CODE.fullmatch(failure_code)
                    else "RUNTIME_SUPERVISOR_STOP_FAILED"
                )
                cleanup_causes.append(error)
        if httpd is not None:
            # The API remains published during this bounded drain. A handler
            # that outlives it is non-daemon and therefore still cannot be
            # silently abandoned; stop_system.sh retains a forced-KILL bound.
            try:
                drained = httpd.drain_request_threads(
                    HTTP_HANDLER_DRAIN_TIMEOUT_SECONDS
                )
                if not drained:
                    cleanup_codes.append("HTTP_HANDLER_DRAIN_TIMEOUT")
            except BaseException as error:
                cleanup_codes.append("HTTP_HANDLER_DRAIN_FAILED")
                cleanup_causes.append(error)
        WORKBENCH_API = None
        try:
            _restore_shutdown_signal_handlers(shutdown_signals)
        except BaseException as error:
            cleanup_codes.append("SHUTDOWN_SIGNAL_RESTORE_FAILED")
            cleanup_causes.append(error)
        if graceful_stop:
            print("\n\033[1;33mWeb UI 服务器已停止\033[0m")
        if primary_error is not None:
            if cleanup_codes:
                try:
                    primary_error.workbench_cleanup_codes = tuple(cleanup_codes)
                except (AttributeError, TypeError):
                    pass
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(
                        "workbench cleanup: " + ",".join(cleanup_codes)
                    )
                try:
                    print(
                        "scientific runtime cleanup "
                        + json.dumps(
                            {"failure_codes": cleanup_codes},
                            ensure_ascii=True,
                            separators=(",", ":"),
                        ),
                        file=sys.stderr,
                    )
                except Exception:
                    pass
            raise primary_error
        if cleanup_codes:
            cleanup_error = RuntimeError(
                "WORKBENCH_CLEANUP_FAILED:" + ",".join(cleanup_codes)
            )
            if cleanup_causes:
                raise cleanup_error from cleanup_causes[0]
            raise cleanup_error


def main():
    # A busy port aborts before startup runtime recovery has any side effect.
    serve_workbench()

if __name__ == '__main__':
    main()
