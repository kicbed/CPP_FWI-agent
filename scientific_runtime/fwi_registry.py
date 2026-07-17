"""Trusted P1 bootstrap material for the fixed Marmousi/Deepwave baseline.

The DatasetRef is built only after the existing Worker verifier checks both
fixed model hashes and the sidecar.  File paths stay inside that trusted
bootstrap boundary and are never copied into the public registry document.
Registration alone does not make the future Algorithm Adapter executable and
does not submit or queue a task.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from scientific_runtime_contracts import schema_errors

from .registry_service import RegistryResult, RegistryService, RegistryValidationError


DEEPWAVE_ALGORITHM_ID = "deepwave.acoustic_fwi"
DEEPWAVE_ALGORITHM_VERSION = "1.6.0"
DEEPWAVE_LEGACY_ALGORITHM_VERSION = "1.0.0"
DEEPWAVE_MANIFEST_PATHS = {
    DEEPWAVE_LEGACY_ALGORITHM_VERSION: (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1.json"
    ),
    "1.1.0": (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1_1.json"
    ),
    "1.2.0": (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1_2.json"
    ),
    "1.3.0": (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1_3.json"
    ),
    "1.4.0": (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1_4.json"
    ),
    "1.5.0": (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1_5.json"
    ),
    DEEPWAVE_ALGORITHM_VERSION: (
        Path(__file__).with_name("registrations") / "deepwave_acoustic_fwi_v1_6.json"
    ),
}


@dataclass(frozen=True)
class FWIBaselineRegistration:
    dataset: RegistryResult
    algorithm: RegistryResult


def load_deepwave_manifest(
    version: str = DEEPWAVE_ALGORITHM_VERSION,
) -> dict[str, Any]:
    if not isinstance(version, str):
        raise RegistryValidationError(
            "PACKAGED_MANIFEST_UNAVAILABLE",
            ["packaged Deepwave manifest version must be a string"],
        )
    path = DEEPWAVE_MANIFEST_PATHS.get(version)
    if path is None:
        raise RegistryValidationError(
            "PACKAGED_MANIFEST_UNAVAILABLE",
            [f"no packaged Deepwave manifest exists for version {version!r}"],
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RegistryValidationError(
            "PACKAGED_MANIFEST_INVALID", ["packaged manifest must be an object"]
        )
    errors = schema_errors("algorithm-manifest.schema.json", value)
    if errors:
        raise RegistryValidationError("PACKAGED_MANIFEST_INVALID", errors)
    if value.get("id") != DEEPWAVE_ALGORITHM_ID or value.get("version") != version:
        raise RegistryValidationError(
            "PACKAGED_MANIFEST_INVALID",
            ["packaged manifest identity does not match its versioned path"],
        )
    return value


def _dataset_ref_from_validated_metadata(
    metadata: dict[str, Any],
    *,
    project_id: str,
    principals: Sequence[str],
) -> dict[str, Any]:
    dataset = {
        "schema_version": "1.0.0",
        "id": metadata["id"],
        "version": "1.0.0",
        "content_hash": "sha256:" + str(metadata["sha256"]).lower(),
        "data_type": "velocity_model_2d",
        "immutable": True,
        "metadata": {
            "shape": list(metadata["shape"]),
            "dtype": metadata["compute_dtype"],
            "axis_order": list(metadata["axis_order"]),
            "units": metadata["velocity_unit"],
            "physics": metadata["physics"],
            "parameter": metadata["parameter"],
            "grid_spacing_m": {
                "dx": metadata["dx_m"],
                "dz": metadata["dz_m"],
            },
            "value_range": {
                "minimum": metadata["velocity_min_mps"],
                "maximum": metadata["velocity_max_mps"],
            },
        },
        "lineage": [],
        "access_scope": {
            "project_id": project_id,
            "principals": list(principals),
            "permissions": ["read", "execute"],
        },
        "extensions": {},
    }
    errors = schema_errors("dataset-ref.schema.json", dataset)
    if errors:
        raise RegistryValidationError("DATASET_BOOTSTRAP_INVALID", errors)
    return dataset


def verified_marmousi_dataset_ref(
    *, project_id: str, principals: Sequence[str]
) -> dict[str, Any]:
    """Verify the fixed local Marmousi files and return a path-free DatasetRef."""

    from fwi_worker.config import resolve_config
    from fwi_worker.model_io import read_and_validate_sidecar

    config = resolve_config({"preset": "forward", "device": "cpu"})
    metadata = read_and_validate_sidecar(config)
    return _dataset_ref_from_validated_metadata(
        metadata, project_id=project_id, principals=principals
    )


def register_verified_fwi_baseline(
    registry: RegistryService,
    *,
    project_id: str,
    principals: Sequence[str],
) -> FWIBaselineRegistration:
    """Provision the verified baseline without exposing an execution entrypoint."""

    dataset = registry.register_dataset(
        dataset=verified_marmousi_dataset_ref(
            project_id=project_id, principals=principals
        )
    )
    algorithm = registry.register_algorithm(manifest=load_deepwave_manifest())
    return FWIBaselineRegistration(dataset=dataset, algorithm=algorithm)
