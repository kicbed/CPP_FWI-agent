from __future__ import annotations

import copy
import unittest

from scientific_runtime.dag_scheduler import (
    BLOCKED,
    CANCELLED,
    FAILED,
    PENDING,
    QUEUED,
    RETRYING,
    RUNNING,
    SUCCEEDED,
    WAITING,
    BlockedDagNode,
    DagScheduleError,
    evaluate_dag_readiness,
)
from scientific_runtime_contracts import compute_plan_hash
from tests.test_scientific_runtime_contracts import plan_graph


def dag_plan(*nodes: tuple[str, tuple[str, ...]]) -> dict:
    plan = plan_graph()
    template = plan["nodes"][0]
    plan["nodes"] = []
    for node_id, dependencies in nodes:
        node = copy.deepcopy(template)
        node["node_id"] = node_id
        node["dependencies"] = list(dependencies)
        node["idempotency_key"] = f"task-001:{node_id}:0001"
        plan["nodes"].append(node)
    plan["plan_hash"] = compute_plan_hash(plan)
    return plan


class ScientificRuntimeDagSchedulerTest(unittest.TestCase):
    def assert_error(self, code: str, plan: dict, states: dict[str, str]) -> None:
        with self.assertRaises(DagScheduleError) as raised:
            evaluate_dag_readiness(plan, node_states=states)
        self.assertEqual(raised.exception.code, code)

    def test_chain_unlocks_once_and_never_reschedules_succeeded_nodes(self) -> None:
        plan = dag_plan(
            ("prepare", ()),
            ("invert", ("prepare",)),
            ("quality", ("invert",)),
        )
        initial = evaluate_dag_readiness(
            plan,
            node_states={
                "prepare": PENDING,
                "invert": PENDING,
                "quality": PENDING,
            },
        )
        self.assertEqual(
            initial.topological_layers,
            (("prepare",), ("invert",), ("quality",)),
        )
        self.assertEqual(initial.runnable_node_ids, ("prepare",))
        self.assertEqual(initial.waiting_node_ids, ("invert", "quality"))

        after_prepare = evaluate_dag_readiness(
            plan,
            node_states={
                "quality": PENDING,
                "invert": PENDING,
                "prepare": SUCCEEDED,
            },
        )
        self.assertEqual(after_prepare.runnable_node_ids, ("invert",))
        self.assertEqual(after_prepare.succeeded_node_ids, ("prepare",))

        complete = evaluate_dag_readiness(
            plan,
            node_states={node_id: SUCCEEDED for node_id in ("quality", "prepare", "invert")},
        )
        self.assertEqual(complete.runnable_node_ids, ())
        self.assertEqual(
            complete.succeeded_node_ids, ("prepare", "invert", "quality")
        )
        self.assertTrue(complete.all_nodes_succeeded)

    def test_fan_out_and_fan_in_are_stable_across_input_order(self) -> None:
        plan = dag_plan(
            ("join", ("right", "left")),
            ("right", ("root",)),
            ("root", ()),
            ("left", ("root",)),
        )
        decision = evaluate_dag_readiness(
            plan,
            node_states={
                "join": PENDING,
                "left": PENDING,
                "right": PENDING,
                "root": SUCCEEDED,
            },
        )
        self.assertEqual(
            decision.topological_layers,
            (("root",), ("left", "right"), ("join",)),
        )
        self.assertEqual(decision.runnable_node_ids, ("left", "right"))
        self.assertEqual(decision.waiting_node_ids, ("join",))

        reordered_plan = dag_plan(
            ("left", ("root",)),
            ("root", ()),
            ("join", ("left", "right")),
            ("right", ("root",)),
        )
        reordered = evaluate_dag_readiness(
            reordered_plan,
            node_states={
                "right": PENDING,
                "root": SUCCEEDED,
                "join": PENDING,
                "left": PENDING,
            },
        )
        self.assertEqual(reordered.topological_layers, decision.topological_layers)
        self.assertEqual(reordered.runnable_node_ids, decision.runnable_node_ids)
        self.assertEqual(reordered.waiting_node_ids, decision.waiting_node_ids)

        joined = evaluate_dag_readiness(
            plan,
            node_states={
                "root": SUCCEEDED,
                "left": SUCCEEDED,
                "right": SUCCEEDED,
                "join": PENDING,
            },
        )
        self.assertEqual(joined.runnable_node_ids, ("join",))

    def test_dependency_failure_blocks_only_its_transitive_branch(self) -> None:
        plan = dag_plan(
            ("root", ()),
            ("child", ("root",)),
            ("join", ("child",)),
            ("independent", ()),
        )
        decision = evaluate_dag_readiness(
            plan,
            node_states={
                "root": FAILED,
                "child": PENDING,
                "join": PENDING,
                "independent": PENDING,
            },
        )
        self.assertEqual(decision.failed_node_ids, ("root",))
        self.assertEqual(decision.runnable_node_ids, ("independent",))
        self.assertEqual(
            decision.blocked_nodes,
            (
                BlockedDagNode("child", ("root",)),
                BlockedDagNode("join", ("root",)),
            ),
        )

    def test_cancelled_and_persisted_blocked_states_propagate(self) -> None:
        plan = dag_plan(
            ("root", ()),
            ("child", ("root",)),
            ("grandchild", ("child",)),
        )
        decision = evaluate_dag_readiness(
            plan,
            node_states={
                "root": CANCELLED,
                "child": BLOCKED,
                "grandchild": PENDING,
            },
        )
        self.assertEqual(decision.cancelled_node_ids, ("root",))
        self.assertEqual(
            decision.blocked_nodes,
            (
                BlockedDagNode("child", ("root",)),
                BlockedDagNode("grandchild", ("root",)),
            ),
        )

        invalid = dag_plan(("root", ()))
        self.assert_error("DAG_STATE_INVALID", invalid, {"root": BLOCKED})

    def test_active_nodes_are_classified_but_never_runnable(self) -> None:
        plan = dag_plan(
            ("queued", ()),
            ("running", ()),
            ("waiting", ()),
            ("retrying", ()),
            ("done", ()),
        )
        decision = evaluate_dag_readiness(
            plan,
            node_states={
                "waiting": WAITING,
                "done": SUCCEEDED,
                "retrying": RETRYING,
                "running": RUNNING,
                "queued": QUEUED,
            },
        )
        self.assertEqual(decision.runnable_node_ids, ())
        self.assertEqual(
            decision.active_node_ids,
            ("queued", "retrying", "running", "waiting"),
        )
        self.assertEqual(decision.succeeded_node_ids, ("done",))

    def test_state_snapshot_must_be_exact_and_dependency_consistent(self) -> None:
        plan = dag_plan(("root", ()), ("child", ("root",)))
        self.assert_error("DAG_STATE_INVALID", plan, {"root": PENDING})
        self.assert_error(
            "DAG_STATE_INVALID",
            plan,
            {"root": PENDING, "child": PENDING, "extra": PENDING},
        )
        self.assert_error(
            "DAG_STATE_INVALID",
            plan,
            {"root": "Ready", "child": PENDING},
        )
        self.assert_error(
            "DAG_STATE_INVALID",
            plan,
            {"root": [PENDING], "child": PENDING},
        )
        self.assert_error(
            "DAG_STATE_INVALID",
            plan,
            {"root": PENDING, "child": PENDING, 1: PENDING},
        )
        self.assert_error(
            "DAG_STATE_INVALID",
            plan,
            {"root": PENDING, "child": RUNNING},
        )

    def test_duplicate_unknown_cyclic_and_hash_drift_graphs_fail_closed(self) -> None:
        duplicate = dag_plan(("root", ()), ("root", ()))
        self.assert_error("DAG_GRAPH_INVALID", duplicate, {"root": PENDING})

        unknown = dag_plan(("root", ("missing",)))
        self.assert_error("DAG_GRAPH_INVALID", unknown, {"root": PENDING})

        cyclic = dag_plan(("left", ("right",)), ("right", ("left",)))
        self.assert_error(
            "DAG_GRAPH_INVALID",
            cyclic,
            {"left": PENDING, "right": PENDING},
        )

        drifted = dag_plan(("root", ()))
        drifted["nodes"][0]["dependencies"] = ["root"]
        self.assert_error("DAG_PLAN_HASH_INVALID", drifted, {"root": PENDING})


if __name__ == "__main__":
    unittest.main()
