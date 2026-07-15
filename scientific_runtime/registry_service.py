"""Validated provisioning and scoped reads for P1 registry snapshots.

This module has no HTTP or LLM-facing registration route.  Mutation methods
are an internal bootstrap/admin boundary; tasks and future Workbench reads only
consume the immutable SQLite records they create.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from scientific_runtime_contracts import schema_errors

from .task_store import (
    RegistryWriteRecord,
    TaskStore,
    TaskStoreConflict,
    TaskStoreCorruption,
)


OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
SEMANTIC_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$"
)


class RegistryServiceError(RuntimeError):
    """Base class for stable Catalog/Registry failures."""


class RegistryValidationError(RegistryServiceError):
    """A registry contract or request is invalid."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class RegistryConflict(RegistryServiceError):
    """An immutable id/version is already bound to different content."""


class RegistryNotFound(RegistryServiceError):
    """A visible registry snapshot does not exist in the requested scope."""


class RegistryCorruption(RegistryServiceError):
    """A persisted snapshot no longer satisfies its validated contract."""


@dataclass(frozen=True)
class RegistryResult:
    document: dict[str, Any]
    replayed: bool


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _validate_identity(value: str, *, field: str) -> None:
    if not isinstance(value, str) or OPAQUE_ID.fullmatch(value) is None:
        raise RegistryValidationError(
            "INVALID_IDENTITY", [f"{field} must be a v1 opaque identifier"]
        )


def _validate_registry_key(value: str, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or IDENTIFIER.fullmatch(value) is None
    ):
        raise RegistryValidationError(
            "INVALID_REGISTRY_KEY", [f"{field} must be a v1 identifier"]
        )


def _validate_version(value: str) -> None:
    if not isinstance(value, str) or SEMANTIC_VERSION.fullmatch(value) is None:
        raise RegistryValidationError(
            "INVALID_VERSION", ["version must be a v1 semantic version"]
        )


class RegistryService:
    """Persist immutable registry records and return permission-scoped views."""

    def __init__(
        self, store: TaskStore, *, clock: Callable[[], str] = _utc_now
    ) -> None:
        self._store = store
        self._clock = clock

    @staticmethod
    def _validate_schema(name: str, value: Mapping[str, Any]) -> None:
        errors = schema_errors(name, value)
        if errors:
            raise RegistryValidationError("SCHEMA_INVALID", errors)

    @staticmethod
    def _validate_manifest_semantics(manifest: Mapping[str, Any]) -> None:
        errors: list[str] = []
        try:
            Draft7Validator.check_schema(manifest["parameter_schema"])
        except SchemaError as error:
            errors.append(f"parameter_schema is not valid Draft-07: {error.message}")
        for field in ("inputs", "outputs"):
            ports = [item["port"] for item in manifest[field]]
            if len(ports) != len(set(ports)):
                errors.append(f"{field} port names must be unique")
        if errors:
            raise RegistryValidationError("MANIFEST_INVALID", errors)

    @staticmethod
    def _validated_dataset(value: dict[str, Any]) -> dict[str, Any]:
        errors = schema_errors("dataset-ref.schema.json", value)
        if errors:
            raise RegistryCorruption(
                "persisted DatasetRef is invalid: " + "; ".join(errors)
            )
        return value

    @classmethod
    def _validated_manifest(cls, value: dict[str, Any]) -> dict[str, Any]:
        errors = schema_errors("algorithm-manifest.schema.json", value)
        if errors:
            raise RegistryCorruption(
                "persisted AlgorithmManifest is invalid: " + "; ".join(errors)
            )
        try:
            cls._validate_manifest_semantics(value)
        except RegistryValidationError as error:
            raise RegistryCorruption(str(error)) from error
        return value

    def register_dataset(
        self, *, dataset: Mapping[str, Any]
    ) -> RegistryResult:
        self._validate_schema("dataset-ref.schema.json", dataset)
        try:
            record: RegistryWriteRecord = self._store.register_dataset(
                dataset=dataset, now=self._clock()
            )
        except TaskStoreCorruption as error:
            raise RegistryCorruption(str(error)) from error
        except TaskStoreConflict as error:
            raise RegistryConflict(str(error)) from error
        return RegistryResult(document=record.document, replayed=record.replayed)

    def register_algorithm(
        self, *, manifest: Mapping[str, Any]
    ) -> RegistryResult:
        self._validate_schema("algorithm-manifest.schema.json", manifest)
        self._validate_manifest_semantics(manifest)
        try:
            record: RegistryWriteRecord = self._store.register_algorithm(
                manifest=manifest, now=self._clock()
            )
        except TaskStoreCorruption as error:
            raise RegistryCorruption(str(error)) from error
        except TaskStoreConflict as error:
            raise RegistryConflict(str(error)) from error
        return RegistryResult(document=record.document, replayed=record.replayed)

    def get_dataset(
        self,
        *,
        project_id: str,
        principal_id: str,
        dataset_id: str,
        version: str,
        permission: str = "read",
    ) -> dict[str, Any]:
        _validate_identity(project_id, field="project_id")
        _validate_identity(principal_id, field="principal_id")
        _validate_registry_key(dataset_id, field="dataset_id")
        _validate_version(version)
        if permission not in {"read", "execute"}:
            raise RegistryValidationError(
                "INVALID_PERMISSION", ["permission must be read or execute"]
            )
        try:
            dataset = self._store.get_dataset(
                project_id=project_id, dataset_id=dataset_id, version=version
            )
        except TaskStoreCorruption as error:
            raise RegistryCorruption(str(error)) from error
        if dataset is None:
            raise RegistryNotFound("dataset does not exist in the requested scope")
        dataset = self._validated_dataset(dataset)
        scope = dataset["access_scope"]
        if (
            scope["project_id"] != project_id
            or principal_id not in scope["principals"]
            or permission not in scope["permissions"]
        ):
            raise RegistryNotFound("dataset does not exist in the requested scope")
        return dataset

    def list_datasets(
        self,
        *,
        project_id: str,
        principal_id: str,
        permission: str = "read",
    ) -> list[dict[str, Any]]:
        _validate_identity(project_id, field="project_id")
        _validate_identity(principal_id, field="principal_id")
        if permission not in {"read", "execute"}:
            raise RegistryValidationError(
                "INVALID_PERMISSION", ["permission must be read or execute"]
            )
        try:
            datasets = self._store.list_datasets(project_id=project_id)
        except TaskStoreCorruption as error:
            raise RegistryCorruption(str(error)) from error
        visible: list[dict[str, Any]] = []
        for dataset in datasets:
            dataset = self._validated_dataset(dataset)
            scope = dataset["access_scope"]
            if (
                principal_id in scope["principals"]
                and permission in scope["permissions"]
            ):
                visible.append(dataset)
        return visible

    def get_algorithm(
        self,
        *,
        algorithm_id: str,
        version: str,
        require_allowlisted: bool = True,
    ) -> dict[str, Any]:
        _validate_registry_key(algorithm_id, field="algorithm_id")
        _validate_version(version)
        if type(require_allowlisted) is not bool:
            raise RegistryValidationError(
                "INVALID_ALLOWLIST_FILTER",
                ["require_allowlisted must be a boolean"],
            )
        try:
            manifest = self._store.get_algorithm(
                algorithm_id=algorithm_id, version=version
            )
        except TaskStoreCorruption as error:
            raise RegistryCorruption(str(error)) from error
        if manifest is None:
            raise RegistryNotFound("algorithm version is not registered")
        manifest = self._validated_manifest(manifest)
        if require_allowlisted and not manifest["security"]["allowlisted"]:
            raise RegistryNotFound("algorithm version is not registered")
        return manifest

    def list_algorithms(
        self, *, allowlisted_only: bool = True
    ) -> list[dict[str, Any]]:
        if type(allowlisted_only) is not bool:
            raise RegistryValidationError(
                "INVALID_ALLOWLIST_FILTER",
                ["allowlisted_only must be a boolean"],
            )
        try:
            manifests = self._store.list_algorithms()
        except TaskStoreCorruption as error:
            raise RegistryCorruption(str(error)) from error
        values = [self._validated_manifest(manifest) for manifest in manifests]
        if allowlisted_only:
            values = [
                manifest
                for manifest in values
                if manifest["security"]["allowlisted"]
            ]
        return values
