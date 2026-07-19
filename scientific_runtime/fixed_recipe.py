"""Immutable P3 product Recipe for the fixed Marmousi Guided workflow.

This is deliberately a closed product definition, not an Algorithm SDK or a
dynamic registration surface. Every accepted identity, stage, dependency,
Worker command, and output contract is enumerated here so callers can fail
closed before admitting a multi-node PlanGraph.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


RECIPE_ID = "forward_qc_fwi"
RECIPE_VERSION = "1.0.0"
RECIPE_ALGORITHM_ID = "deepwave.acoustic_fwi"
RECIPE_ALGORITHM_VERSION = "1.6.0"
RECIPE_ADAPTER_ID = "fwi.deepwave_adapter"
RECIPE_ADAPTER_VERSION = "1.6.0"
RECIPE_EXTENSION_KEY = "org.agent_rpc.recipe"

RECIPE_STAGES = (
    "data_check",
    "forward",
    "quality_check",
    "fwi",
    "result_check",
)

_FIXED_RECIPE_EXTENSION = {
    "id": RECIPE_ID,
    "version": RECIPE_VERSION,
}

# The four check/forward stages intentionally reuse the same real, bounded
# Worker ``forward`` computation. Their stage-specific artifact projections
# below state what each node actually checks or publishes; they are not
# represented as independently selectable Algorithms.
_PLAN_OUTPUTS = [
    {"port": "inverted_model", "data_type": "inverted_velocity_model_2d"},
    {"port": "loss", "data_type": "loss_curve"},
    {"port": "true_model_figure", "data_type": "figure"},
    {"port": "initial_model_figure", "data_type": "figure"},
    {"port": "inverted_model_figure", "data_type": "figure"},
    {"port": "model_error_figure", "data_type": "figure"},
    {"port": "shot_gathers_figure", "data_type": "figure"},
    {"port": "loss_curve_figure", "data_type": "figure"},
]

# These are evidence inputs owned by the packaged Recipe, not new public
# Algorithm ports.  Every stage still executes the immutable Algorithm 1.6
# dataset contract through the Adapter.  The additional typed bindings make
# the control-plane execution, cache identity, and trusted lineage consume the
# exact upstream artifacts that justify each fixed transition.
_UPSTREAM_INPUTS: dict[str, list[dict[str, str]]] = {
    "data_check": [],
    "forward": [
        {
            "port": "checked_model",
            "source_node_id": "data_check",
            "source_output_port": "inverted_model",
            "data_type": "inverted_velocity_model_2d",
        }
    ],
    "quality_check": [
        {
            "port": "dataset_quality",
            "source_node_id": "data_check",
            "source_output_port": "loss",
            "data_type": "loss_curve",
        }
    ],
    "fwi": [
        {
            "port": "forward_evidence",
            "source_node_id": "forward",
            "source_output_port": "shot_gathers_figure",
            "data_type": "figure",
        },
        {
            "port": "quality_evidence",
            "source_node_id": "quality_check",
            "source_output_port": "model_error_figure",
            "data_type": "figure",
        },
    ],
    "result_check": [
        {
            "port": "fwi_model",
            "source_node_id": "fwi",
            "source_output_port": "inverted_model",
            "data_type": "inverted_velocity_model_2d",
        },
        {
            "port": "fwi_loss",
            "source_node_id": "fwi",
            "source_output_port": "loss",
            "data_type": "loss_curve",
        },
    ],
}

_FIXED_RECIPE_MANIFEST: dict[str, Any] = {
    "schema_version": "1.0.0",
    "id": RECIPE_ID,
    "version": RECIPE_VERSION,
    "label": "Forward + quality checks + FWI",
    "description": (
        "Fixed five-node Marmousi workflow with explicit fan-out/fan-in; "
        "all stages reuse the immutable Deepwave FWI 1.6 Algorithm/Adapter bundle."
    ),
    "algorithm": {
        "id": RECIPE_ALGORITHM_ID,
        "version": RECIPE_ALGORITHM_VERSION,
    },
    "adapter": {
        "id": RECIPE_ADAPTER_ID,
        "version": RECIPE_ADAPTER_VERSION,
    },
    "task_type": "acoustic_fwi_2d",
    "dataset_input": {
        "port": "model",
        "data_type": "velocity_model_2d",
    },
    "plan_outputs": _PLAN_OUTPUTS,
    "nodes": [
        {
            "node_id": "data_check",
            "label": "Dataset check",
            "dependencies": [],
            "upstream_inputs": _UPSTREAM_INPUTS["data_check"],
            "command": "forward",
            "risk": {
                "code": "synthetic_dataset_scope",
                "severity": "low",
                "mitigation": "Verify the immutable Marmousi identity and finite Worker outputs.",
            },
            "outputs": [
                {
                    "port": "inverted_model",
                    "data_type": "inverted_velocity_model_2d",
                },
                {"port": "loss", "data_type": "loss_curve"},
            ],
        },
        {
            "node_id": "forward",
            "label": "Forward modelling",
            "dependencies": ["data_check"],
            "upstream_inputs": _UPSTREAM_INPUTS["forward"],
            "command": "forward",
            "risk": {
                "code": "synthetic_forward_only",
                "severity": "medium",
                "mitigation": "Label the result as a fixed synthetic comparison, not field-data validation.",
            },
            "outputs": [
                {"port": "shot_gathers_figure", "data_type": "figure"},
            ],
        },
        {
            "node_id": "quality_check",
            "label": "Quality check",
            "dependencies": ["data_check"],
            "upstream_inputs": _UPSTREAM_INPUTS["quality_check"],
            "command": "forward",
            "risk": {
                "code": "fixed_quality_metrics",
                "severity": "medium",
                "mitigation": "Use only finite, hash-verified outputs from the fixed Worker contract.",
            },
            "outputs": [
                {"port": "model_error_figure", "data_type": "figure"},
            ],
        },
        {
            "node_id": "fwi",
            "label": "FWI",
            "dependencies": ["forward", "quality_check"],
            "upstream_inputs": _UPSTREAM_INPUTS["fwi"],
            "command": "invert",
            "risk": {
                "code": "bounded_synthetic_inversion",
                "severity": "high",
                "mitigation": "Keep the approved iteration/resource bounds and verify finite model/loss artifacts.",
            },
            "outputs": [
                {
                    "port": "inverted_model",
                    "data_type": "inverted_velocity_model_2d",
                },
                {"port": "loss", "data_type": "loss_curve"},
            ],
        },
        {
            "node_id": "result_check",
            "label": "Result check",
            "dependencies": ["fwi"],
            "upstream_inputs": _UPSTREAM_INPUTS["result_check"],
            "command": "forward",
            "risk": {
                "code": "fixed_result_review",
                "severity": "medium",
                "mitigation": (
                    "Require the hash-verified FWI model/loss receipt, then publish "
                    "only the fixed forward diagnostic figures with its lineage."
                ),
            },
            "outputs": [
                {"port": "shot_gathers_figure", "data_type": "figure"},
                {"port": "model_error_figure", "data_type": "figure"},
            ],
        },
    ],
}


def load_fixed_recipe_manifest() -> dict[str, Any]:
    """Return an isolated copy of the one packaged P3 Recipe definition."""

    return copy.deepcopy(_FIXED_RECIPE_MANIFEST)


def fixed_recipe_extension() -> dict[str, str]:
    """Return the exact hash-bound PlanGraph/TaskDraft extension value."""

    return copy.deepcopy(_FIXED_RECIPE_EXTENSION)


def is_fixed_recipe_extension(value: Any) -> bool:
    """Recognize only the exact packaged Recipe identity, with no extra keys."""

    return (
        isinstance(value, Mapping)
        and set(value) == {"id", "version"}
        and value.get("id") == RECIPE_ID
        and value.get("version") == RECIPE_VERSION
    )


def fixed_recipe_stage(stage_id: Any) -> dict[str, Any] | None:
    """Return one exact stage contract, or ``None`` for an unknown node id."""

    if not isinstance(stage_id, str) or stage_id not in RECIPE_STAGES:
        return None
    for stage in _FIXED_RECIPE_MANIFEST["nodes"]:
        if stage["node_id"] == stage_id:
            return copy.deepcopy(stage)
    return None


def fixed_recipe_plan_inputs(
    stage_id: Any, dataset_identity: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    """Build the one allowed PlanGraph input list for a fixed Recipe stage."""

    stage = fixed_recipe_stage(stage_id)
    if stage is None or not isinstance(dataset_identity, Mapping):
        return None
    inputs: list[dict[str, Any]] = [
        {"port": "model", "dataset": copy.deepcopy(dict(dataset_identity))}
    ]
    for edge in stage["upstream_inputs"]:
        inputs.append(
            {
                "port": edge["port"],
                "source": {
                    "node_id": edge["source_node_id"],
                    "port": edge["source_output_port"],
                    "data_type": edge["data_type"],
                },
            }
        )
    return inputs
