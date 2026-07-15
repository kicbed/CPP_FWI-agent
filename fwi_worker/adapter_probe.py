"""Read-only evidence probe used by the Scientific Runtime FWI Adapter.

The control plane can run without PyTorch, Deepwave, or Pydantic.  This module
is therefore invoked only through the fixed Worker virtual-environment Python.
It emits a small path-free JSON document and never creates a run directory or
starts numerical work.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from typing import Any


def _environment_snapshot_hash() -> str:
    distributions: list[tuple[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = str(distribution.metadata.get("Name") or "").strip().lower()
        if name:
            distributions.append((name, str(distribution.version)))
    material = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "distributions": sorted(set(distributions)),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def dataset_evidence() -> dict[str, Any]:
    from scientific_runtime.fwi_registry import verified_marmousi_dataset_ref

    return {
        "dataset": verified_marmousi_dataset_ref(
            project_id="adapter-validation",
            principals=["adapter-validation"],
        )
    }


def runtime_evidence(device: str) -> dict[str, Any]:
    import torch

    from .config import resolve_config
    from .deepwave_2d import validate_device
    from .metrics import environment_info

    validate_device(device)
    config = resolve_config(
        {
            "preset": "fwi_smoke",
            "device": device,
            "iterations": 1,
            "seed": 2026,
        }
    )
    environment = environment_info(config)
    compute_capability = None
    if device == "cuda":
        capability = torch.cuda.get_device_capability(0)
        compute_capability = f"{capability[0]}.{capability[1]}"
    return {
        "device_details": {
            "device": device,
            "device_name": environment["device_name"],
            "compute_capability": compute_capability,
            "development_environment_snapshot_hash": _environment_snapshot_hash(),
            "runtime": {
                "python": platform.python_version(),
                "pytorch": str(torch.__version__),
                "deepwave": str(importlib.metadata.version("deepwave")),
                "cuda": (
                    str(torch.version.cuda) if torch.version.cuda is not None else None
                ),
            },
            "determinism": {
                "torch_deterministic_algorithms": bool(
                    torch.are_deterministic_algorithms_enabled()
                ),
                "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
            },
        }
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fwi-adapter-probe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("dataset")
    runtime = subparsers.add_parser("runtime")
    runtime.add_argument("--device", required=True, choices=("cpu", "cuda"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    value = dataset_evidence() if args.command == "dataset" else runtime_evidence(args.device)
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
