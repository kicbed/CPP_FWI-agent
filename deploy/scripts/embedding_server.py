#!/usr/bin/env python3
"""Loopback-only sentence-transformers embedding service.

The service is intentionally small and private: it only binds to loopback,
accepts bounded JSON requests, and does not enable CORS unless one exact local
web origin is supplied explicitly.
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from sentence_transformers import SentenceTransformer


MAX_REQUEST_BYTES = 256 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_BATCH_SIZE = 32
MAX_BATCH_TEXT_BYTES = 128 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOOPBACK_ORIGIN_RE = re.compile(
    r"^http://(?:127\.0\.0\.1|localhost|\[::1\]):(?:[1-9][0-9]{0,4})$"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
model: Optional[SentenceTransformer] = None
model_public_id = "unloaded"
model_device = "cpu"
model_local_files_only = True
allowed_cors_origin: Optional[str] = None


def _public_model_id(model_name: str) -> str:
    """Return a useful model ID without exposing an absolute host path."""
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model_name):
        return model_name[:128]
    name = Path(model_name).name
    if re.fullmatch(r"[A-Za-z0-9_.-]+", name or ""):
        return name[:128]
    return "local-model"


def _validate_cors_origin(origin: Optional[str]) -> Optional[str]:
    if origin is None:
        return None
    if not LOOPBACK_ORIGIN_RE.fullmatch(origin):
        raise ValueError(
            "CORS origin must be loopback HTTP with an explicit port"
        )
    port = int(origin.rsplit(":", 1)[1])
    if port > 65535:
        raise ValueError("CORS origin port is out of range")
    return origin


def configure_cors(origin: Optional[str]) -> None:
    global allowed_cors_origin
    allowed_cors_origin = _validate_cors_origin(origin)


def _model_dimension(value: SentenceTransformer) -> int:
    getter = getattr(value, "get_embedding_dimension", None)
    if callable(getter):
        return int(getter() or 0)
    return int(value.get_sentence_embedding_dimension() or 0)


def load_model(
    model_name: str,
    *,
    device: str = "cpu",
    local_files_only: bool = True,
) -> None:
    """Load one trusted sentence-transformers model."""
    global model, model_public_id, model_device, model_local_files_only
    logger.info(
        "Loading embedding model id=%s device=%s local_files_only=%s",
        _public_model_id(model_name),
        device,
        local_files_only,
    )
    loaded = SentenceTransformer(
        model_name,
        device=device,
        local_files_only=local_files_only,
        trust_remote_code=False,
    )
    dimension = _model_dimension(loaded)
    if dimension <= 0 or dimension > 65536:
        raise RuntimeError("Embedding model reported an invalid dimension")
    model = loaded
    model_public_id = _public_model_id(model_name)
    model_device = device
    model_local_files_only = local_files_only
    logger.info("Embedding model ready dimension=%d", dimension)


def _error(message: str, status: int):
    return jsonify({"error": message}), status


@app.before_request
def enforce_private_http_api():
    origin = request.headers.get("Origin")
    if origin and (allowed_cors_origin is None or origin != allowed_cors_origin):
        return _error("origin is not allowed", 403)


@app.after_request
def add_exact_cors_headers(response):
    origin = request.headers.get("Origin")
    if allowed_cors_origin is not None and origin == allowed_cors_origin:
        response.headers["Access-Control-Allow-Origin"] = allowed_cors_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers.add("Vary", "Origin")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error_value):
    return _error("request body is too large", 413)


def _read_json_object(allowed_fields: set[str]):
    if request.mimetype != "application/json":
        return None, _error("Content-Type must be application/json", 415)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _error("JSON body must be an object", 400)
    unexpected = set(data) - allowed_fields
    if unexpected:
        return None, _error("JSON body contains unsupported fields", 400)
    return data, None


def _validate_text(value: Any):
    if not isinstance(value, str):
        return None, "text must be a string"
    if not value.strip():
        return None, "text must not be empty"
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        return None, "text is too large"
    return value, None


def _finite_array(value: Any) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.ndim == 0 or not np.isfinite(array).all():
        raise RuntimeError("Embedding model returned invalid values")
    return array


@app.route("/embed", methods=["POST"])
def embed():
    if model is None:
        return _error("embedding model is not ready", 503)
    data, error = _read_json_object({"text"})
    if error is not None:
        return error
    text, message = _validate_text(data.get("text"))
    if message is not None:
        return _error(message, 400)
    try:
        embedding = _finite_array(model.encode(text, normalize_embeddings=True))
        if embedding.ndim != 1:
            raise RuntimeError("Embedding model returned an invalid shape")
        return jsonify(
            {"embedding": embedding.tolist(), "dimension": int(embedding.shape[0])}
        )
    except Exception:
        # Do not include model exception text: some backends may echo input.
        logger.error("Single embedding request failed")
        return _error("embedding failed", 500)


@app.route("/embed_batch", methods=["POST"])
def embed_batch():
    if model is None:
        return _error("embedding model is not ready", 503)
    data, error = _read_json_object({"texts"})
    if error is not None:
        return error
    texts = data.get("texts")
    if not isinstance(texts, list) or not texts:
        return _error("texts must be a non-empty array", 400)
    if len(texts) > MAX_BATCH_SIZE:
        return _error("batch contains too many texts", 400)

    validated = []
    total_bytes = 0
    for value in texts:
        text, message = _validate_text(value)
        if message is not None:
            return _error(message, 400)
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_BATCH_TEXT_BYTES:
            return _error("batch text is too large", 400)
        validated.append(text)

    try:
        embeddings = _finite_array(
            model.encode(validated, normalize_embeddings=True)
        )
        if embeddings.ndim != 2 or embeddings.shape[0] != len(validated):
            raise RuntimeError("Embedding model returned an invalid batch shape")
        return jsonify(
            {
                "embeddings": embeddings.tolist(),
                "count": int(embeddings.shape[0]),
                "dimension": int(embeddings.shape[1]),
            }
        )
    except Exception:
        logger.error("Batch embedding request failed")
        return _error("embedding failed", 500)


@app.route("/health", methods=["GET"])
def health():
    ready = model is not None
    dimension = _model_dimension(model) if ready else 0
    payload = {
        "schema_version": 1,
        "status": "ok" if ready else "unavailable",
        "model_loaded": ready,
        "model": model_public_id,
        "device": model_device,
        "dimension": dimension,
        "normalize_embeddings": True,
        "local_files_only": model_local_files_only,
    }
    return jsonify(payload), 200 if ready else 503


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 Embedding 服务")
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-Embedding-0.6B",
        help="模型名称或本地路径",
    )
    parser.add_argument("--port", type=int, default=6000, help="服务端口")
    parser.add_argument("--host", default="127.0.0.1", help="仅允许 loopback")
    parser.add_argument(
        "--device", choices=("cpu", "cuda"), default="cpu", help="推理设备"
    )
    parser.add_argument(
        "--local-files-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="默认仅使用本地模型缓存；--no-local-files-only 才允许下载",
    )
    parser.add_argument(
        "--cors-origin",
        default=None,
        help="可选的精确 Web Origin，例如 http://127.0.0.1:8080",
    )
    args = parser.parse_args()

    if args.host not in LOOPBACK_HOSTS:
        parser.error("--host must be 127.0.0.1, localhost, or ::1")
    if args.port <= 0 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        configure_cors(args.cors_origin)
    except ValueError as exc:
        parser.error(str(exc))

    load_model(
        args.model,
        device=args.device,
        local_files_only=args.local_files_only,
    )
    logger.info("Starting private embedding service on %s:%d", args.host, args.port)
    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
