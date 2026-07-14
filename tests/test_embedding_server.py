#!/usr/bin/env python3
"""Contract and input-boundary tests for the private embedding HTTP API."""

import importlib.util
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "embedding_server.py"
)
SPEC = importlib.util.spec_from_file_location("embedding_server_for_test", MODULE_PATH)
embedding_server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(embedding_server)


class FakeModel:
    def get_sentence_embedding_dimension(self):
        return 3

    def encode(self, value, normalize_embeddings=True):
        assert normalize_embeddings is True
        if isinstance(value, list):
            return np.asarray([[1.0, 0.0, 0.5] for _ in value], dtype=np.float32)
        return np.asarray([1.0, 0.0, 0.5], dtype=np.float32)


class InvalidModel(FakeModel):
    def encode(self, value, normalize_embeddings=True):
        return np.asarray([np.nan, 0.0, 0.5], dtype=np.float32)


class EmbeddingServerTest(unittest.TestCase):
    def setUp(self):
        embedding_server.app.config.update(TESTING=True)
        embedding_server.model = FakeModel()
        embedding_server.model_public_id = "test/model"
        embedding_server.model_device = "cpu"
        embedding_server.model_local_files_only = True
        embedding_server.configure_cors(None)
        self.client = embedding_server.app.test_client()

    def tearDown(self):
        embedding_server.configure_cors(None)

    def test_health_has_bounded_operational_metadata(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["model"], "test/model")
        self.assertEqual(payload["device"], "cpu")
        self.assertEqual(payload["dimension"], 3)
        self.assertTrue(payload["local_files_only"])

    def test_embed_requires_exact_json_object_and_bounded_text(self):
        self.assertEqual(
            self.client.post("/embed", data="x", content_type="text/plain").status_code,
            415,
        )
        self.assertEqual(self.client.post("/embed", json=[]).status_code, 400)
        self.assertEqual(
            self.client.post("/embed", json={"text": "x", "extra": 1}).status_code,
            400,
        )
        self.assertEqual(self.client.post("/embed", json={"text": " "}).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/embed", json={"text": "x" * (embedding_server.MAX_TEXT_BYTES + 1)}
            ).status_code,
            400,
        )

        response = self.client.post("/embed", json={"text": "FWI"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["dimension"], 3)

    def test_batch_count_and_total_input_are_bounded(self):
        too_many = ["x"] * (embedding_server.MAX_BATCH_SIZE + 1)
        self.assertEqual(
            self.client.post("/embed_batch", json={"texts": too_many}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/embed_batch", json={"texts": ["x", 1]}).status_code,
            400,
        )
        response = self.client.post("/embed_batch", json={"texts": ["FWI", "波动方程"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 2)

    def test_oversized_http_body_is_rejected(self):
        response = self.client.post(
            "/embed",
            data=b"{" + b" " * embedding_server.MAX_REQUEST_BYTES + b"}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)

    def test_cors_is_off_by_default_and_exact_when_enabled(self):
        self.assertEqual(
            self.client.get(
                "/health", headers={"Origin": "http://127.0.0.1:8080"}
            ).status_code,
            403,
        )
        embedding_server.configure_cors("http://127.0.0.1:8080")
        response = self.client.get(
            "/health", headers={"Origin": "http://127.0.0.1:8080"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:8080",
        )
        self.assertEqual(
            self.client.get(
                "/health", headers={"Origin": "http://localhost:8080"}
            ).status_code,
            403,
        )
        with self.assertRaises(ValueError):
            embedding_server.configure_cors("https://example.com")

    def test_non_finite_model_output_is_not_returned(self):
        embedding_server.model = InvalidModel()
        response = self.client.post("/embed", json={"text": "FWI"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "embedding failed"})


if __name__ == "__main__":
    unittest.main()
