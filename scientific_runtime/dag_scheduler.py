"""Pure deterministic readiness decisions for a hash-bound PlanGraph.

This P3 kernel deliberately has no storage, clock, lease, dispatcher, or
Adapter access.  It classifies an exact node-state snapshot; callers remain
responsible for proving approval and state from durable storage and for
fencing any later execution side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from scientific_runtime_contracts import (
    PlanDataEdgeError,
    compute_plan_hash,
    extract_plan_data_edges,
    schema_errors,
)


PENDING = "Pending"
QUEUED = "Queued"
RUNNING = "Running"
WAITING = "Waiting"
RETRYING = "Retrying"
SUCCEEDED = "Succeeded"
FAILED = "Failed"
CANCELLED = "Cancelled"
BLOCKED = "Blocked"

_ACTIVE_STATES = frozenset({QUEUED, RUNNING, WAITING, RETRYING})
_KNOWN_STATES = frozenset(
    {
        PENDING,
        *_ACTIVE_STATES,
        SUCCEEDED,
        FAILED,
        CANCELLED,
        BLOCKED,
    }
)


class DagScheduleError(ValueError):
    """A stable fail-closed rejection from the pure DAG kernel."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(sorted(set(errors)))
        super().__init__(f"{code}: {'; '.join(self.errors)}")


@dataclass(frozen=True)
class BlockedDagNode:
    """One node transitively blocked by terminal dependency failures."""

    node_id: str
    blocked_by_node_ids: tuple[str, ...]


@dataclass(frozen=True)
class DagReadiness:
    """Deterministic classification of one exact PlanGraph state snapshot."""

    plan_id: str
    plan_hash: str
    topological_layers: tuple[tuple[str, ...], ...]
    runnable_node_ids: tuple[str, ...]
    waiting_node_ids: tuple[str, ...]
    active_node_ids: tuple[str, ...]
    succeeded_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    cancelled_node_ids: tuple[str, ...]
    blocked_nodes: tuple[BlockedDagNode, ...]
    all_nodes_succeeded: bool


def _validated_graph(
    plan: Mapping[str, Any],
) -> tuple[
    tuple[tuple[str, ...], ...],
    dict[str, tuple[str, ...]],
]:
    if not isinstance(plan, Mapping):
        raise DagScheduleError("DAG_PLAN_INVALID", ["plan must be an object"])

    contract_errors = schema_errors("plan-graph.schema.json", plan)
    if contract_errors:
        raise DagScheduleError("DAG_PLAN_INVALID", contract_errors)
    try:
        expected_hash = compute_plan_hash(plan)
    except (TypeError, ValueError) as error:
        raise DagScheduleError("DAG_PLAN_INVALID", [str(error)]) from error
    if plan["plan_hash"] != expected_hash:
        raise DagScheduleError(
            "DAG_PLAN_HASH_INVALID",
            ["plan_hash does not match canonical plan content"],
        )

    node_ids = [node["node_id"] for node in plan["nodes"]]
    graph_errors: list[str] = []
    if len(node_ids) != len(set(node_ids)):
        graph_errors.append("node_id values must be unique")
    known_node_ids = set(node_ids)
    dependencies: dict[str, tuple[str, ...]] = {}
    for node in plan["nodes"]:
        node_id = node["node_id"]
        node_dependencies = tuple(node["dependencies"])
        dependencies[node_id] = node_dependencies
        unknown = sorted(set(node_dependencies) - known_node_ids)
        if unknown:
            graph_errors.append(
                f"node {node_id!r} has unknown dependencies: {', '.join(unknown)}"
            )
    if graph_errors:
        raise DagScheduleError("DAG_GRAPH_INVALID", graph_errors)

    remaining = {
        node_id: set(node_dependencies)
        for node_id, node_dependencies in dependencies.items()
    }
    layers: list[tuple[str, ...]] = []
    while remaining:
        layer = tuple(
            sorted(
                node_id
                for node_id, node_dependencies in remaining.items()
                if not node_dependencies
            )
        )
        if not layer:
            raise DagScheduleError(
                "DAG_GRAPH_INVALID", ["plan dependencies must be acyclic"]
            )
        layers.append(layer)
        for node_id in layer:
            del remaining[node_id]
        completed_layer = set(layer)
        for node_dependencies in remaining.values():
            node_dependencies.difference_update(completed_layer)
    try:
        extract_plan_data_edges(plan)
    except PlanDataEdgeError as error:
        raise DagScheduleError("DAG_DATA_EDGE_INVALID", error.errors) from error
    return tuple(layers), dependencies


def _validated_states(
    node_states: Mapping[str, str], known_node_ids: set[str]
) -> dict[str, str]:
    if not isinstance(node_states, Mapping):
        raise DagScheduleError(
            "DAG_STATE_INVALID", ["node_states must be an object"]
        )
    errors: list[str] = []
    if any(not isinstance(node_id, str) for node_id in node_states):
        errors.append("node state keys must be strings")
        supplied_node_ids = {
            node_id for node_id in node_states if isinstance(node_id, str)
        }
    else:
        supplied_node_ids = set(node_states)
    missing = sorted(known_node_ids - supplied_node_ids)
    extra = sorted(supplied_node_ids - known_node_ids)
    if missing:
        errors.append("missing node states: " + ", ".join(missing))
    if extra:
        errors.append("unknown node states: " + ", ".join(extra))
    for node_id in sorted(known_node_ids & supplied_node_ids):
        state = node_states[node_id]
        if not isinstance(state, str) or state not in _KNOWN_STATES:
            errors.append(f"node {node_id!r} has unknown state {state!r}")
    if errors:
        raise DagScheduleError("DAG_STATE_INVALID", errors)
    return {node_id: node_states[node_id] for node_id in known_node_ids}


def evaluate_dag_readiness(
    plan: Mapping[str, Any], *, node_states: Mapping[str, str]
) -> DagReadiness:
    """Classify runnable, waiting, active, completed, and blocked nodes.

    A pending node is runnable only when every direct dependency is exactly
    ``Succeeded``.  ``Failed`` and ``Cancelled`` nodes block their descendants;
    an already persisted ``Blocked`` state must be justified by the same
    transitive dependency proof.  Independent branches remain classifiable,
    but this function intentionally makes no task-wide fail-fast decision.
    """

    layers, dependencies = _validated_graph(plan)
    ordered_node_ids = tuple(node_id for layer in layers for node_id in layer)
    states = _validated_states(node_states, set(ordered_node_ids))

    blocked_roots: dict[str, tuple[str, ...]] = {}
    state_errors: list[str] = []
    runnable: list[str] = []
    waiting: list[str] = []
    active: list[str] = []
    succeeded: list[str] = []
    failed: list[str] = []
    cancelled: list[str] = []
    blocked: list[BlockedDagNode] = []

    for node_id in ordered_node_ids:
        state = states[node_id]
        node_dependencies = dependencies[node_id]
        dependency_blockers: set[str] = set()
        for dependency in node_dependencies:
            dependency_state = states[dependency]
            if dependency_state in {FAILED, CANCELLED}:
                dependency_blockers.add(dependency)
            dependency_blockers.update(blocked_roots.get(dependency, ()))
        blocker_ids = tuple(sorted(dependency_blockers))

        if state == BLOCKED:
            if not blocker_ids:
                state_errors.append(
                    f"node {node_id!r} is Blocked without a failed, cancelled, "
                    "or blocked dependency"
                )
            else:
                blocked_roots[node_id] = blocker_ids
                blocked.append(BlockedDagNode(node_id, blocker_ids))
            continue

        if state == PENDING:
            if blocker_ids:
                blocked_roots[node_id] = blocker_ids
                blocked.append(BlockedDagNode(node_id, blocker_ids))
            elif all(states[dependency] == SUCCEEDED for dependency in node_dependencies):
                runnable.append(node_id)
            else:
                waiting.append(node_id)
            continue

        if state == CANCELLED:
            cancelled.append(node_id)
            continue

        if blocker_ids:
            state_errors.append(
                f"node {node_id!r} in state {state!r} has a failed, cancelled, "
                "or blocked dependency"
            )
            continue

        incomplete_dependencies = tuple(
            dependency
            for dependency in node_dependencies
            if states[dependency] != SUCCEEDED
        )
        if incomplete_dependencies:
            state_errors.append(
                f"node {node_id!r} in state {state!r} has incomplete "
                "dependencies: " + ", ".join(incomplete_dependencies)
            )
            continue
        if state in _ACTIVE_STATES:
            active.append(node_id)
        elif state == SUCCEEDED:
            succeeded.append(node_id)
        elif state == FAILED:
            failed.append(node_id)

    if state_errors:
        raise DagScheduleError("DAG_STATE_INVALID", state_errors)

    return DagReadiness(
        plan_id=plan["plan_id"],
        plan_hash=plan["plan_hash"],
        topological_layers=layers,
        runnable_node_ids=tuple(runnable),
        waiting_node_ids=tuple(waiting),
        active_node_ids=tuple(active),
        succeeded_node_ids=tuple(succeeded),
        failed_node_ids=tuple(failed),
        cancelled_node_ids=tuple(cancelled),
        blocked_nodes=tuple(blocked),
        all_nodes_succeeded=len(succeeded) == len(ordered_node_ids),
    )
