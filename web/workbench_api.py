"""Transport boundary for the P1 Scientific Runtime Guided Workbench.

This module deliberately does not own an HTTP server.  ``WorkbenchAPI`` turns
one already-bounded HTTP request into an ``APIResponse`` so the existing Web
server can integrate it without giving request data access to project or
principal identity.  The application object is the trusted identity boundary.
"""

from __future__ import annotations

import hmac
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import unquote_to_bytes, urlsplit


API_PREFIX = "/api/scientific-runtime/v1"
MAX_JSON_BYTES = 64 * 1024
MAX_SSE_EVENT_BYTES = 128 * 1024
SSE_BATCH_LIMIT = 100

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CONTENT_LENGTH = re.compile(r"^(?:0|[1-9][0-9]*)$")
_BAD_ENCODED_PATH = re.compile(r"%(?:00|25|2e|2f|5c)", re.IGNORECASE)
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_QUERY = re.compile(r"^[A-Za-z0-9_=&-]+$")
_TASK_CURSOR = re.compile(r"^v1_[A-Za-z0-9_-]{4,175}$")

_MUTATION_ENDPOINTS = frozenset(
    {
        "create_task",
        "revise_task",
        "approve",
        "abandon",
        "cancel",
        "trash",
        "restore",
        "purge",
    }
)
_FORM_FIELDS = frozenset(
    {
        "goal",
        "dataset_id",
        "dataset_version",
        "preset",
        "device",
        "iterations",
        "seed",
        "optimizer",
        "learning_rate",
    }
)
_RECIPE_SELECTOR_FIELDS = frozenset({"recipe_id", "recipe_version"})
_RECIPE_FORM_FIELDS = _FORM_FIELDS | _RECIPE_SELECTOR_FIELDS
_ALLOWED_ARTIFACT_TYPES = {
    "application/x-npy": ".npy",
    "text/csv": ".csv",
    "image/png": ".png",
}


@dataclass(frozen=True)
class APIResponse:
    """A complete HTTP response produced by :class:`WorkbenchAPI`."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class SSEEventStream:
    """Bounded, read-only projection of one task's durable RunEvents."""

    def __init__(self, application: Any, task_id: str, after_sequence: int) -> None:
        self._application = application
        self._task_id = task_id
        self._after_sequence = after_sequence
        self._buffered: tuple[bytes, ...] | None = None
        self._terminal = False

    @property
    def after_sequence(self) -> int:
        return self._after_sequence

    @property
    def terminal(self) -> bool:
        return self._terminal

    def prime(self) -> None:
        """Authorize scope and validate the first page before HTTP headers."""

        if self._buffered is not None:
            raise _ApplicationInvariantError("event stream was already primed")
        self._buffered = self._read_batch()

    def next_batch(self) -> tuple[bytes, ...]:
        if self._buffered is not None:
            frames = self._buffered
            self._buffered = None
            return frames
        if self._terminal:
            return ()
        return self._read_batch()

    def _read_batch(self) -> tuple[bytes, ...]:
        events = self._application.list_events(
            self._task_id,
            after_sequence=self._after_sequence,
            limit=SSE_BATCH_LIMIT,
        )
        if not isinstance(events, list) or len(events) > SSE_BATCH_LIMIT:
            raise _ApplicationInvariantError("invalid event stream page")
        frames: list[bytes] = []
        terminal_seen = False
        for event in events:
            if not isinstance(event, Mapping):
                raise _ApplicationInvariantError("invalid event stream item")
            sequence = event.get("sequence")
            if (
                type(sequence) is not int
                or sequence != self._after_sequence + 1
                or sequence > 2**63 - 1
                or event.get("task_id") != self._task_id
                or terminal_seen
            ):
                raise _ApplicationInvariantError("invalid event stream sequence")
            encoded = json.dumps(
                dict(event),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_SSE_EVENT_BYTES:
                raise _ApplicationInvariantError("event stream item is too large")
            frames.append(
                b"id: "
                + str(sequence).encode("ascii")
                + b"\nevent: run_event\ndata: "
                + encoded
                + b"\n\n"
            )
            self._after_sequence = sequence
            terminal_seen = event.get("task_status") in {
                "Succeeded",
                "Failed",
                "Cancelled",
            }
        self._terminal = terminal_seen
        return tuple(frames)


class _RequestError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.extra_headers = dict(extra_headers or {})
        super().__init__(code)


class _DuplicateJSONKey(ValueError):
    pass


class _ApplicationInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Route:
    endpoint: str
    allowed_methods: tuple[str, ...]
    task_id: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True)
class _RequestContext:
    headers: dict[str, str]
    route: _Route
    query: dict[str, int | str]
    idempotency_key: str


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJSONKey(key)
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _has_surrogate(value: Any) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, Mapping):
        return any(_has_surrogate(key) or _has_surrogate(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_surrogate(item) for item in value)
    return False


def _json_headers(length: int) -> dict[str, str]:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(length),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _json_response(
    status: int,
    payload: Mapping[str, Any],
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> APIResponse:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = _json_headers(len(encoded))
    if extra_headers:
        headers.update(extra_headers)
    return APIResponse(status=status, headers=headers, body=encoded)


def _error_response(
    status: int,
    code: str,
    message: str,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> APIResponse:
    return _json_response(
        status,
        {"ok": False, "error": {"code": code, "message": message}},
        extra_headers=extra_headers,
    )


def _success_response(data: Any, *, status: int = 200) -> APIResponse:
    return _json_response(status, {"ok": True, "data": data})


def _normalized_headers(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
    try:
        items_method = getattr(headers, "items", None)
        items = items_method() if callable(items_method) else iter(headers)
    except TypeError as error:
        raise _RequestError(400, "INVALID_HEADERS", "request headers are invalid") from error

    normalized: dict[str, str] = {}
    try:
        for name, value in items:
            if not isinstance(name, str) or not isinstance(value, str):
                raise _RequestError(400, "INVALID_HEADERS", "request headers are invalid")
            lowered = name.lower()
            if (
                not lowered
                or lowered in normalized
                or any(ord(character) <= 0x20 or ord(character) >= 0x7F for character in lowered)
                or "\r" in value
                or "\n" in value
            ):
                raise _RequestError(400, "INVALID_HEADERS", "request headers are invalid")
            normalized[lowered] = value
    except (TypeError, ValueError) as error:
        if isinstance(error, _RequestError):
            raise
        raise _RequestError(400, "INVALID_HEADERS", "request headers are invalid") from error
    return normalized


def _validate_origin_configuration(origins: Iterable[str]) -> frozenset[str]:
    if isinstance(origins, str):
        origins = (origins,)
    validated: set[str] = set()
    for origin in origins:
        if not isinstance(origin, str) or not origin or origin != origin.strip():
            raise ValueError("allowed_origins must contain exact loopback HTTP origins")
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError as error:
            raise ValueError("allowed_origins must contain exact loopback HTTP origins") from error
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("allowed_origins must contain exact loopback HTTP origins")
        validated.add(origin)
        # Browsers lowercase the authority and omit HTTP's default port in
        # Origin.  Treat those spellings as the same exact origin tuple.
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        authority = host if port in {None, 80} else f"{host}:{port}"
        validated.add(f"http://{authority}")
    if not validated:
        raise ValueError("at least one loopback allowed_origin is required")
    return frozenset(validated)


def _validate_host_configuration(hosts: Iterable[str]) -> frozenset[str]:
    if isinstance(hosts, str):
        hosts = (hosts,)
    validated: set[str] = set()
    for host in hosts:
        if (
            not isinstance(host, str)
            or not host
            or host != host.strip()
            or any(character in host for character in "/?#@,\r\n\t ")
        ):
            raise ValueError("allowed_hosts contains an invalid host")
        lowered = host.lower()
        validated.add(lowered)
        try:
            parsed = urlsplit("//" + lowered)
            port = parsed.port
        except ValueError as error:
            raise ValueError("allowed_hosts contains an invalid host") from error
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.hostname is None
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("allowed_hosts contains an invalid host")
        if port in {None, 80}:
            canonical = (
                f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            )
            validated.add(canonical)
            validated.add(f"{canonical}:80")
    if not validated:
        raise ValueError("at least one allowed_host is required")
    return frozenset(validated)


def _decode_request_path(raw_target: str) -> tuple[str, str | None]:
    if (
        not isinstance(raw_target, str)
        or not raw_target
        or not raw_target.startswith("/")
        or raw_target.startswith("//")
        or "#" in raw_target
        or "\\" in raw_target
        or "\x00" in raw_target
        or any(ord(character) < 0x20 or ord(character) >= 0x7F for character in raw_target)
    ):
        raise _RequestError(400, "INVALID_TARGET", "request target is invalid")

    path_part, separator, query = raw_target.partition("?")
    if separator and not query:
        raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
    if "?" in query:
        raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
    if _BAD_ENCODED_PATH.search(path_part):
        raise _RequestError(400, "INVALID_TARGET", "request target is invalid")

    # ``unquote_to_bytes`` leaves malformed percent escapes unchanged.  Count
    # every percent before decoding so a second decoder can never reinterpret
    # an accepted target.
    without_escapes = _PERCENT_ESCAPE.sub("", path_part)
    if "%" in without_escapes:
        raise _RequestError(400, "INVALID_TARGET", "request target is invalid")
    try:
        path = unquote_to_bytes(path_part).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as error:
        raise _RequestError(400, "INVALID_TARGET", "request target is invalid") from error
    if "%" in path or "\\" in path or "\x00" in path or "//" in path:
        raise _RequestError(400, "INVALID_TARGET", "request target is invalid")
    if any(part in {"", ".", ".."} for part in path.split("/")[1:]):
        raise _RequestError(400, "INVALID_TARGET", "request target is invalid")
    return path, query if separator else None


def _route(path: str) -> _Route:
    if path == f"{API_PREFIX}/session":
        return _Route("session", ("GET",))
    if path == f"{API_PREFIX}/catalog":
        return _Route("catalog", ("GET",))
    if path == f"{API_PREFIX}/tasks":
        return _Route("task_collection", ("GET", "POST"))

    prefix = f"{API_PREFIX}/tasks/"
    if not path.startswith(prefix):
        raise _RequestError(404, "NOT_FOUND", "requested resource was not found")
    parts = path[len(prefix) :].split("/")
    task_id = parts[0]
    if _OPAQUE_ID.fullmatch(task_id) is None:
        raise _RequestError(400, "INVALID_IDENTITY", "request identity is invalid")
    if len(parts) == 1:
        return _Route("get_task", ("GET",), task_id=task_id)
    if len(parts) == 2:
        endpoints = {
            "draft": ("revise_task", ("PUT",)),
            "approve": ("approve", ("POST",)),
            "abandon": ("abandon", ("POST",)),
            "cancel": ("cancel", ("POST",)),
            "trash": ("trash", ("POST",)),
            "restore": ("restore", ("POST",)),
            "purge": ("purge", ("POST",)),
            "events": ("events", ("GET",)),
            "artifacts": ("artifacts", ("GET",)),
        }
        matched = endpoints.get(parts[1])
        if matched is not None:
            return _Route(matched[0], matched[1], task_id=task_id)
    if len(parts) == 3 and parts[1:] == ["events", "stream"]:
        return _Route("event_stream", ("GET",), task_id=task_id)
    if len(parts) == 3 and parts[1] == "artifacts":
        artifact_id = parts[2]
        if _OPAQUE_ID.fullmatch(artifact_id) is None:
            raise _RequestError(400, "INVALID_IDENTITY", "request identity is invalid")
        return _Route(
            "artifact_content",
            ("GET",),
            task_id=task_id,
            artifact_id=artifact_id,
        )
    raise _RequestError(404, "NOT_FOUND", "requested resource was not found")


def _query_values(route: _Route, raw_query: str | None) -> dict[str, int | str]:
    if raw_query is None:
        return {}
    if (
        route.endpoint not in {"events", "event_stream", "task_collection"}
        or _QUERY.fullmatch(raw_query) is None
    ):
        raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
    values: dict[str, int | str] = {}
    allowed = (
        {"after_sequence", "limit"}
        if route.endpoint == "events"
        else {"after_sequence"}
        if route.endpoint == "event_stream"
        else {"cursor", "limit", "view"}
    )
    for item in raw_query.split("&"):
        if item.count("=") != 1:
            raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
        key, raw_value = item.split("=", 1)
        if key not in allowed or key in values or not raw_value.isascii():
            raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
        if key == "cursor":
            if _TASK_CURSOR.fullmatch(raw_value) is None:
                raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
            values[key] = raw_value
            continue
        if key == "view":
            if raw_value not in {"active", "trash"}:
                raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
            values[key] = raw_value
            continue
        if not raw_value.isdigit() or (len(raw_value) > 1 and raw_value.startswith("0")):
            raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
        number = int(raw_value)
        if key == "after_sequence" and not 0 <= number <= 2**63 - 1:
            raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
        maximum_limit = 50 if route.endpoint == "task_collection" else 100
        if key == "limit" and not 1 <= number <= maximum_limit:
            raise _RequestError(400, "INVALID_QUERY", "request query is invalid")
        values[key] = number
    return values


def _validate_content_type(value: str | None) -> None:
    if value is None:
        raise _RequestError(415, "UNSUPPORTED_MEDIA_TYPE", "application/json is required")
    parts = [part.strip() for part in value.split(";")]
    if not parts or parts[0].lower() != "application/json" or len(parts) > 2:
        raise _RequestError(415, "UNSUPPORTED_MEDIA_TYPE", "application/json is required")
    if len(parts) == 2:
        parameter = parts[1].split("=", 1)
        if len(parameter) != 2 or parameter[0].strip().lower() != "charset":
            raise _RequestError(415, "UNSUPPORTED_MEDIA_TYPE", "UTF-8 JSON is required")
        charset = parameter[1].strip().lower()
        if charset not in {"utf-8", '"utf-8"'}:
            raise _RequestError(415, "UNSUPPORTED_MEDIA_TYPE", "UTF-8 JSON is required")


def _parse_json_body(headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
    _validate_content_type(headers.get("content-type"))
    try:
        decoded = body.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _RequestError(400, "INVALID_JSON", "request JSON is invalid") from error
    if not isinstance(value, dict) or _has_surrogate(value):
        raise _RequestError(400, "INVALID_JSON", "request JSON is invalid")
    return value


def _safe_application_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and _SAFE_ERROR_CODE.fullmatch(code) is not None:
        return code
    return fallback


def _application_error(error: Exception) -> APIResponse:
    names = {base.__name__.lower() for base in type(error).__mro__}
    joined = " ".join(names)
    if "notfound" in joined:
        return _error_response(
            404,
            _safe_application_code(error, "NOT_FOUND"),
            "requested resource was not found",
        )
    if "conflict" in joined or "idempotency" in joined:
        return _error_response(
            409,
            _safe_application_code(error, "CONFLICT"),
            "request conflicts with current state",
        )
    if "validation" in joined or "handleerror" in joined:
        return _error_response(
            422,
            _safe_application_code(error, "VALIDATION_FAILED"),
            "request validation failed",
        )
    if (
        "workbenchruntimeerror" in names
        or "unavailable" in joined
        or "taskdispatcherror" in names
        or "taskstoreerror" in names
        or "adaptererror" in names
        or "dispatcherror" in names
    ):
        return _error_response(
            503,
            _safe_application_code(error, "RUNTIME_UNAVAILABLE"),
            "scientific runtime is temporarily unavailable",
        )
    return _error_response(500, "INTERNAL_ERROR", "internal server error")


def _validated_form(
    payload: Mapping[str, Any], *, revision: bool
) -> tuple[dict[str, Any], int | None]:
    revision_fields = {"expected_revision"} if revision else set()
    supplied_form_fields = set(payload) - revision_fields
    legacy_form_fields = _FORM_FIELDS - {"optimizer", "learning_rate"}
    if (
        supplied_form_fields
        not in (_FORM_FIELDS, legacy_form_fields, _RECIPE_FORM_FIELDS)
        or (revision and "expected_revision" not in payload)
        or (not revision and "expected_revision" in payload)
    ):
        raise _RequestError(422, "INVALID_FORM", "request validation failed")
    form = {key: payload[key] for key in supplied_form_fields}
    # Preserve the seven-field wire shape until the Workbench can perform an
    # exact durable replay check.  It applies Adam/LR=10 only after that check;
    # partial optimizer input remains invalid.
    if not revision:
        return form, None
    expected_revision = payload["expected_revision"]
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 1
        or expected_revision > 2**63 - 1
    ):
        raise _RequestError(422, "INVALID_FORM", "request validation failed")
    return form, expected_revision


class WorkbenchAPI:
    """Strict, same-origin HTTP facade over a trusted ``GuidedWorkbench``.

    ``project_id`` and ``principal_id`` are intentionally absent from this
    constructor and from every route.  They remain encapsulated by
    ``application`` and can therefore never be selected by a browser request.
    """

    def __init__(
        self,
        application: Any,
        csrf_token: str,
        allowed_hosts: Iterable[str],
        allowed_origins: Iterable[str],
    ) -> None:
        if (
            not isinstance(csrf_token, str)
            or not csrf_token
            or len(csrf_token) > 512
            or any(ord(character) < 0x21 or ord(character) >= 0x7F for character in csrf_token)
        ):
            raise ValueError("csrf_token must be a non-empty printable ASCII secret")
        self._application = application
        self._csrf_token = csrf_token
        self._allowed_hosts = _validate_host_configuration(allowed_hosts)
        self._allowed_origins = _validate_origin_configuration(allowed_origins)

    def dispatch(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        body: bytes,
    ) -> APIResponse:
        """Validate and dispatch one HTTP request without leaking exceptions."""

        try:
            return self._dispatch(method, raw_target, headers, body)
        except _RequestError as error:
            return _error_response(
                error.status,
                error.code,
                error.message,
                extra_headers=error.extra_headers,
            )
        except Exception:
            # Serialization and application invariant failures are also kept
            # path-free.  No exception text is returned to the browser.
            return _error_response(500, "INTERNAL_ERROR", "internal server error")

    def preflight(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
    ) -> APIResponse | None:
        """Validate request metadata before an HTTP server reads a body.

        A ``None`` result authorizes a bounded body read.  Any response result
        must be sent immediately without reading caller-controlled bytes, and
        the server should close that connection because a body may remain.
        ``dispatch`` repeats the checks and validates the exact bytes so this
        method is an optimization and availability boundary, not an auth
        shortcut.
        """

        try:
            self._prepare_request(
                method,
                raw_target,
                headers,
                actual_body_length=None,
            )
            return None
        except _RequestError as error:
            return _error_response(
                error.status,
                error.code,
                error.message,
                extra_headers=error.extra_headers,
            )
        except Exception:
            return _error_response(500, "INTERNAL_ERROR", "internal server error")

    def open_event_stream(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        body: bytes,
    ) -> SSEEventStream | APIResponse:
        """Authorize and prime one task-scoped SSE stream.

        The first bounded Store read happens before the HTTP server commits a
        streaming response, so missing/cross-scope tasks and corrupt pages
        retain normal JSON error semantics. Later failures close the stream;
        they never synthesize task state or mutate the runtime.
        """

        try:
            if not isinstance(body, bytes):
                raise _RequestError(400, "INVALID_BODY", "request body is invalid")
            context = self._prepare_request(
                method,
                raw_target,
                headers,
                actual_body_length=len(body),
            )
            if context.route.endpoint != "event_stream":
                raise _RequestError(404, "NOT_FOUND", "requested resource was not found")
            if context.headers.get("accept") != "text/event-stream":
                raise _RequestError(
                    406,
                    "EVENT_STREAM_REQUIRED",
                    "text/event-stream is required",
                )
            stream = SSEEventStream(
                self._application,
                context.route.task_id,
                int(context.query.get("after_sequence", 0)),
            )
            try:
                stream.prime()
            except Exception as error:
                return _application_error(error)
            return stream
        except _RequestError as error:
            return _error_response(
                error.status,
                error.code,
                error.message,
                extra_headers=error.extra_headers,
            )
        except Exception:
            return _error_response(500, "INTERNAL_ERROR", "internal server error")

    def _prepare_request(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        *,
        actual_body_length: int | None,
    ) -> _RequestContext:
        if not isinstance(method, str) or method not in {"GET", "POST", "PUT"}:
            raise _RequestError(405, "METHOD_NOT_ALLOWED", "request method is not allowed")
        normalized = _normalized_headers(headers)

        host = normalized.get("host")
        if host is None or host != host.strip() or host.lower() not in self._allowed_hosts:
            raise _RequestError(403, "HOST_FORBIDDEN", "request host is not allowed")
        if "transfer-encoding" in normalized:
            raise _RequestError(
                400,
                "TRANSFER_ENCODING_FORBIDDEN",
                "transfer encoding is not allowed",
            )
        if "content-encoding" in normalized:
            raise _RequestError(
                415,
                "CONTENT_ENCODING_FORBIDDEN",
                "content encoding is not allowed",
            )

        declared_text = normalized.get("content-length")
        declared_length: int | None = None
        if declared_text is not None:
            if _CONTENT_LENGTH.fullmatch(declared_text) is None:
                raise _RequestError(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "content length is invalid",
                )
            declared_length = int(declared_text)
            if declared_length > MAX_JSON_BYTES:
                raise _RequestError(413, "BODY_TOO_LARGE", "request body is too large")
        if actual_body_length is not None:
            if actual_body_length > MAX_JSON_BYTES:
                raise _RequestError(413, "BODY_TOO_LARGE", "request body is too large")
            if declared_length is not None and declared_length != actual_body_length:
                raise _RequestError(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "content length is invalid",
                )
            if declared_length is None and actual_body_length:
                raise _RequestError(
                    411,
                    "CONTENT_LENGTH_REQUIRED",
                    "content length is required",
                )

        path, raw_query = _decode_request_path(raw_target)
        route = _route(path)
        query = _query_values(route, raw_query)
        if method not in route.allowed_methods:
            raise _RequestError(
                405,
                "METHOD_NOT_ALLOWED",
                "request method is not allowed",
                extra_headers={"Allow": ", ".join(route.allowed_methods)},
            )

        if route.endpoint != "session":
            supplied_csrf = normalized.get("x-workbench-csrf")
            if supplied_csrf is None or not hmac.compare_digest(
                supplied_csrf, self._csrf_token
            ):
                raise _RequestError(403, "CSRF_FORBIDDEN", "CSRF token is invalid")

        key = ""
        endpoint = (
            "list_tasks"
            if route.endpoint == "task_collection" and method == "GET"
            else "create_task"
            if route.endpoint == "task_collection"
            else route.endpoint
        )
        route = _Route(endpoint, route.allowed_methods, route.task_id, route.artifact_id)
        if route.endpoint == "create_task" and query:
            raise _RequestError(400, "INVALID_QUERY", "request query is invalid")

        if route.endpoint in _MUTATION_ENDPOINTS:
            origin = normalized.get("origin")
            if origin is None or origin not in self._allowed_origins:
                raise _RequestError(403, "ORIGIN_FORBIDDEN", "request origin is not allowed")
            key = normalized.get("idempotency-key", "")
            if (
                not key
                or len(key) > 255
                or key != key.strip()
                or any(ord(character) < 0x21 or ord(character) >= 0x7F for character in key)
            ):
                raise _RequestError(
                    400,
                    "IDEMPOTENCY_KEY_REQUIRED",
                    "a valid Idempotency-Key is required",
                )
            if declared_length is None:
                raise _RequestError(
                    411,
                    "CONTENT_LENGTH_REQUIRED",
                    "content length is required",
                )
            _validate_content_type(normalized.get("content-type"))
        elif declared_length not in {None, 0} or (
            actual_body_length is not None and actual_body_length
        ):
            raise _RequestError(400, "BODY_FORBIDDEN", "request body is not allowed")

        return _RequestContext(
            headers=normalized,
            route=route,
            query=query,
            idempotency_key=key,
        )

    def _dispatch(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        body: bytes,
    ) -> APIResponse:
        if not isinstance(body, bytes):
            raise _RequestError(400, "INVALID_BODY", "request body is invalid")
        context = self._prepare_request(
            method,
            raw_target,
            headers,
            actual_body_length=len(body),
        )
        route = context.route
        query = context.query
        key = context.idempotency_key
        if route.endpoint in _MUTATION_ENDPOINTS:
            payload = _parse_json_body(context.headers, body)
        else:
            payload = {}

        try:
            if route.endpoint == "session":
                capabilities = self._application.session_capabilities()
                if not isinstance(capabilities, Mapping):
                    raise _ApplicationInvariantError("invalid session result")
                data = dict(capabilities)
                data["csrf_token"] = self._csrf_token
                return _success_response(data)
            if route.endpoint == "catalog":
                return _success_response(self._application.list_catalog())
            if route.endpoint == "list_tasks":
                result = self._application.list_tasks(
                    cursor=query.get("cursor"),
                    limit=query.get("limit", 20),
                    view=query.get("view", "active"),
                )
                if not isinstance(result, Mapping):
                    raise _ApplicationInvariantError("invalid task list result")
                return _success_response(result)
            if route.endpoint == "create_task":
                form, _ = _validated_form(payload, revision=False)
                return _success_response(self._application.create_task(form, key), status=201)
            if route.endpoint == "get_task":
                return _success_response(self._application.get_task(route.task_id, refresh=True))
            if route.endpoint == "revise_task":
                form, expected_revision = _validated_form(payload, revision=True)
                return _success_response(
                    self._application.revise_task(
                        route.task_id,
                        expected_revision,
                        form,
                        key,
                    )
                )
            if route.endpoint == "approve":
                if set(payload) != {"plan_hash"} or not isinstance(payload["plan_hash"], str):
                    raise _RequestError(422, "INVALID_APPROVAL", "request validation failed")
                return _success_response(
                    self._application.approve_and_submit(route.task_id, payload["plan_hash"], key)
                )
            if route.endpoint == "abandon":
                if payload:
                    raise _RequestError(422, "INVALID_ABANDON", "request validation failed")
                return _success_response(self._application.abandon_task(route.task_id, key))
            if route.endpoint == "cancel":
                if (
                    set(payload) != {"reason"}
                    or payload["reason"] != "user_requested"
                ):
                    raise _RequestError(422, "INVALID_CANCEL", "request validation failed")
                return _success_response(
                    self._application.cancel_task(
                        route.task_id,
                        key,
                        "user_requested",
                    )
                )
            if route.endpoint in {"trash", "restore"}:
                if set(payload) != {"expected_visibility_revision"}:
                    raise _RequestError(
                        422, "INVALID_VISIBILITY", "request validation failed"
                    )
                revision = payload["expected_visibility_revision"]
                if (
                    type(revision) is not int
                    or not 0 <= revision <= 2**63 - 1
                ):
                    raise _RequestError(
                        422, "INVALID_VISIBILITY", "request validation failed"
                    )
                function = (
                    self._application.trash_task
                    if route.endpoint == "trash"
                    else self._application.restore_task
                )
                return _success_response(function(route.task_id, revision, key))
            if route.endpoint == "purge":
                if set(payload) != {
                    "expected_visibility_revision",
                    "confirmation_task_id",
                }:
                    raise _RequestError(
                        422, "INVALID_PURGE", "request validation failed"
                    )
                revision = payload["expected_visibility_revision"]
                confirmation_task_id = payload["confirmation_task_id"]
                if (
                    type(revision) is not int
                    or not 0 <= revision <= 2**63 - 1
                    or not isinstance(confirmation_task_id, str)
                    or confirmation_task_id != route.task_id
                ):
                    raise _RequestError(
                        422, "INVALID_PURGE", "request validation failed"
                    )
                return _success_response(
                    self._application.purge_task(route.task_id, revision, key)
                )
            if route.endpoint == "events":
                events = self._application.list_events(
                    route.task_id,
                    after_sequence=query.get("after_sequence", 0),
                    limit=query.get("limit", 100),
                )
                if not isinstance(events, list):
                    raise _ApplicationInvariantError("invalid event list")
                return _success_response({"events": events})
            if route.endpoint == "event_stream":
                raise _RequestError(
                    406,
                    "EVENT_STREAM_REQUIRED",
                    "streaming transport is required",
                )
            if route.endpoint == "artifacts":
                artifacts = self._application.list_artifacts(route.task_id)
                if not isinstance(artifacts, list):
                    raise _ApplicationInvariantError("invalid artifact list")
                return _success_response({"artifacts": artifacts})
            if route.endpoint == "artifact_content":
                return self._artifact_response(route.task_id, route.artifact_id)
        except _RequestError:
            raise
        except Exception as error:
            return _application_error(error)
        raise _ApplicationInvariantError("unhandled API route")

    def _artifact_response(self, task_id: str, artifact_id: str) -> APIResponse:
        result = self._application.read_artifact(task_id, artifact_id)
        if not isinstance(result, tuple) or len(result) != 2:
            raise _ApplicationInvariantError("invalid artifact result")
        manifest, content = result
        if not isinstance(manifest, Mapping) or not isinstance(content, bytes):
            raise _ApplicationInvariantError("invalid artifact result")
        media_type = manifest.get("media_type")
        extension = _ALLOWED_ARTIFACT_TYPES.get(media_type)
        if (
            extension is None
            or manifest.get("task_id") != task_id
            or manifest.get("artifact_id") != artifact_id
            or manifest.get("size_bytes") != len(content)
        ):
            raise _ApplicationInvariantError("artifact manifest does not match content")
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", artifact_id)[:128]
        headers = {
            "Content-Type": media_type,
            "Content-Length": str(len(content)),
            "Content-Disposition": f'attachment; filename="{safe_id}{extension}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        return APIResponse(status=200, headers=headers, body=content)


__all__ = [
    "API_PREFIX",
    "APIResponse",
    "MAX_JSON_BYTES",
    "MAX_SSE_EVENT_BYTES",
    "SSEEventStream",
    "WorkbenchAPI",
]
