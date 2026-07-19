-- P3 fixed Recipe fan-out admission.
--
-- Historical PlanGraphs retain the v21 single-active-node rule.  Only the
-- exact server-owned forward_qc_fwi Recipe may hold two active admissions so
-- its forward and quality branches can overlap.  Per-node intent/admission
-- uniqueness and the inherited CPU/GPU kernel locks remain unchanged.

DROP TRIGGER dag_node_execution_admission_requires_exact_current_case;

CREATE TRIGGER dag_node_execution_admission_requires_exact_current_case
BEFORE INSERT ON dag_node_execution_admissions
WHEN NOT EXISTS (
    SELECT 1
    FROM tasks AS task
    JOIN plans AS plan
      ON plan.task_id = task.task_id AND plan.plan_id = task.current_plan_id
    JOIN approvals AS approval
      ON approval.task_id = task.task_id
     AND approval.approval_id = task.current_approval_id
     AND approval.plan_id = plan.plan_id
     AND approval.plan_hash = plan.plan_hash
    JOIN dispatch_intents AS intent ON intent.intent_id = NEW.intent_id
    JOIN dag_node_input_binding_facts AS binding
      ON binding.task_id = task.task_id AND binding.plan_id = plan.plan_id
     AND binding.approval_id = approval.approval_id
     AND binding.target_node_id = NEW.node_id
     AND binding.target_node_revision = NEW.pending_revision
     AND binding.fencing_token = NEW.input_fencing_token
     AND binding.binding_document_hash = NEW.input_binding_document_hash
    JOIN dag_node_state_events AS pending
      ON pending.task_id = task.task_id AND pending.plan_id = plan.plan_id
     AND pending.plan_hash = plan.plan_hash AND pending.node_id = NEW.node_id
     AND pending.revision = NEW.pending_revision AND pending.state = 'Pending'
    WHERE task.task_id = NEW.task_id
      AND task.project_id = NEW.project_id
      AND task.principal_id = NEW.principal_id
      AND task.status IN ('AwaitingApproval', 'Running')
      AND plan.plan_id = NEW.plan_id AND plan.plan_hash = NEW.plan_hash
      AND approval.approval_id = NEW.approval_id
      AND approval.decision = 'approved'
      AND intent.task_id = NEW.task_id AND intent.plan_id = NEW.plan_id
      AND intent.plan_hash = NEW.plan_hash
      AND intent.approval_id = NEW.approval_id AND intent.node_id = NEW.node_id
      AND intent.node_idempotency_key = NEW.node_idempotency_key
      AND binding.plan_hash = NEW.plan_hash
      AND binding.target_node_state = 'Pending'
      AND binding.project_id = NEW.project_id
      AND binding.principal_id = NEW.principal_id
      AND binding.owner_id = NEW.input_owner_id
      AND binding.term_acquired_at = NEW.input_term_acquired_at
      AND NEW.input_fencing_token = NEW.admission_fencing_token
      AND NEW.input_owner_id = NEW.admission_owner_id
      AND NEW.input_term_acquired_at = NEW.admission_term_acquired_at
      AND NEW.queued_revision = NEW.pending_revision + 1
      AND NEW.max_node_attempts = 1
      AND NOT EXISTS (
          SELECT 1 FROM dag_node_state_events AS later
          WHERE later.task_id = pending.task_id AND later.plan_id = pending.plan_id
            AND later.node_id = pending.node_id
            AND later.revision > pending.revision
      )
      AND NOT EXISTS (
          SELECT 1 FROM task_cancel_requests AS cancel
          WHERE cancel.task_id = task.task_id
      )
      AND (
          NOT EXISTS (
              SELECT 1
              FROM dag_node_execution_admissions AS active_admission
              JOIN dag_node_state_events AS active_state
                ON active_state.task_id = active_admission.task_id
               AND active_state.plan_id = active_admission.plan_id
               AND active_state.node_id = active_admission.node_id
              WHERE active_admission.task_id = NEW.task_id
                AND active_state.state IN (
                    'Queued', 'Running', 'Waiting', 'Retrying'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM dag_node_state_events AS newer
                    WHERE newer.task_id = active_state.task_id
                      AND newer.plan_id = active_state.plan_id
                      AND newer.node_id = active_state.node_id
                      AND newer.revision > active_state.revision
                )
          )
          OR (
              json_valid(plan.document_json)
              AND json_type(plan.document_json, '$.extensions') = 'object'
              AND (
                  SELECT COUNT(*)
                  FROM json_each(plan.document_json, '$.extensions')
              ) = 1
              AND json_type(
                  plan.document_json,
                  '$.extensions."org.agent_rpc.recipe"'
              ) = 'object'
              AND (
                  SELECT COUNT(*)
                  FROM json_each(
                      plan.document_json,
                      '$.extensions."org.agent_rpc.recipe"'
                  )
              ) = 2
              AND json_extract(
                  plan.document_json,
                  '$.extensions."org.agent_rpc.recipe".id'
              ) = 'forward_qc_fwi'
              AND json_extract(
                  plan.document_json,
                  '$.extensions."org.agent_rpc.recipe".version'
              ) = '1.0.0'
              -- Keep this database-side capability predicate equivalent to
              -- ``is_fixed_recipe_parallel_plan``.  The extension is only an
              -- identity claim; it cannot by itself relax the historical
              -- single-active-node invariant.
              AND json_extract(
                  plan.document_json, '$.schema_version'
              ) = '1.2.0'
              AND json_extract(
                  plan.document_json, '$.task_type'
              ) = 'acoustic_fwi_2d'
              AND json_type(plan.document_json, '$.nodes') = 'array'
              AND json_array_length(
                  json_extract(plan.document_json, '$.nodes')
              ) = 5
              AND json_type(
                  plan.document_json, '$.nodes[0]'
              ) = 'object'
              AND json_extract(
                  plan.document_json, '$.nodes[0].node_id'
              ) = 'data_check'
              AND json_type(
                  plan.document_json, '$.nodes[0].dependencies'
              ) = 'array'
              AND json_array_length(
                  json_extract(
                      plan.document_json, '$.nodes[0].dependencies'
                  )
              ) = 0
              AND json_type(
                  plan.document_json, '$.nodes[1]'
              ) = 'object'
              AND json_extract(
                  plan.document_json, '$.nodes[1].node_id'
              ) = 'forward'
              AND json_type(
                  plan.document_json, '$.nodes[1].dependencies'
              ) = 'array'
              AND json_array_length(
                  json_extract(
                      plan.document_json, '$.nodes[1].dependencies'
                  )
              ) = 1
              AND json_extract(
                  plan.document_json, '$.nodes[1].dependencies[0]'
              ) = 'data_check'
              AND json_type(
                  plan.document_json, '$.nodes[2]'
              ) = 'object'
              AND json_extract(
                  plan.document_json, '$.nodes[2].node_id'
              ) = 'quality_check'
              AND json_type(
                  plan.document_json, '$.nodes[2].dependencies'
              ) = 'array'
              AND json_array_length(
                  json_extract(
                      plan.document_json, '$.nodes[2].dependencies'
                  )
              ) = 1
              AND json_extract(
                  plan.document_json, '$.nodes[2].dependencies[0]'
              ) = 'data_check'
              AND json_type(
                  plan.document_json, '$.nodes[3]'
              ) = 'object'
              AND json_extract(
                  plan.document_json, '$.nodes[3].node_id'
              ) = 'fwi'
              AND json_type(
                  plan.document_json, '$.nodes[3].dependencies'
              ) = 'array'
              AND json_array_length(
                  json_extract(
                      plan.document_json, '$.nodes[3].dependencies'
                  )
              ) = 2
              AND json_extract(
                  plan.document_json, '$.nodes[3].dependencies[0]'
              ) = 'forward'
              AND json_extract(
                  plan.document_json, '$.nodes[3].dependencies[1]'
              ) = 'quality_check'
              AND json_type(
                  plan.document_json, '$.nodes[4]'
              ) = 'object'
              AND json_extract(
                  plan.document_json, '$.nodes[4].node_id'
              ) = 'result_check'
              AND json_type(
                  plan.document_json, '$.nodes[4].dependencies'
              ) = 'array'
              AND json_array_length(
                  json_extract(
                      plan.document_json, '$.nodes[4].dependencies'
                  )
              ) = 1
              AND json_extract(
                  plan.document_json, '$.nodes[4].dependencies[0]'
              ) = 'fwi'
              AND NOT EXISTS (
                  SELECT 1
                  FROM json_each(plan.document_json, '$.nodes') AS recipe_node
                  WHERE json_type(recipe_node.value, '$.inputs') != 'array'
                     OR json_array_length(
                         json_extract(recipe_node.value, '$.inputs')
                     ) != CASE CAST(recipe_node.key AS INTEGER)
                         WHEN 0 THEN 1
                         WHEN 1 THEN 2
                         WHEN 2 THEN 2
                         WHEN 3 THEN 3
                         WHEN 4 THEN 3
                         ELSE -1
                     END
                     OR json_type(
                         recipe_node.value, '$.inputs[0]'
                     ) != 'object'
                     OR (
                         SELECT COUNT(*)
                         FROM json_each(recipe_node.value, '$.inputs[0]')
                     ) != 2
                     OR json_extract(
                         recipe_node.value, '$.inputs[0].port'
                     ) != 'model'
                     OR json_type(
                         recipe_node.value, '$.inputs[0].dataset'
                     ) != 'object'
                     OR (
                         SELECT COUNT(*)
                         FROM json_each(
                             recipe_node.value, '$.inputs[0].dataset'
                         )
                     ) != 4
                     OR json_extract(
                         recipe_node.value, '$.inputs[0].dataset.id'
                     ) != json_extract(
                         plan.document_json,
                         '$.nodes[0].inputs[0].dataset.id'
                     )
                     OR json_type(
                         recipe_node.value, '$.inputs[0].dataset.id'
                     ) != 'text'
                     OR json_extract(
                         recipe_node.value, '$.inputs[0].dataset.version'
                     ) != json_extract(
                         plan.document_json,
                         '$.nodes[0].inputs[0].dataset.version'
                     )
                     OR json_type(
                         recipe_node.value, '$.inputs[0].dataset.version'
                     ) != 'text'
                     OR json_extract(
                         recipe_node.value,
                         '$.inputs[0].dataset.content_hash'
                     ) != json_extract(
                         plan.document_json,
                         '$.nodes[0].inputs[0].dataset.content_hash'
                     )
                     OR json_type(
                         recipe_node.value,
                         '$.inputs[0].dataset.content_hash'
                     ) != 'text'
                     OR json_extract(
                         recipe_node.value, '$.inputs[0].dataset.data_type'
                     ) != 'velocity_model_2d'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM (
                      SELECT 1 AS node_index, 1 AS input_index,
                             'checked_model' AS target_port,
                             'data_check' AS source_node,
                             'inverted_model' AS source_port,
                             'inverted_velocity_model_2d' AS data_type
                      UNION ALL
                      SELECT 2, 1, 'dataset_quality', 'data_check',
                             'loss', 'loss_curve'
                      UNION ALL
                      SELECT 3, 1, 'forward_evidence', 'forward',
                             'shot_gathers_figure', 'figure'
                      UNION ALL
                      SELECT 3, 2, 'quality_evidence', 'quality_check',
                             'model_error_figure', 'figure'
                      UNION ALL
                      SELECT 4, 1, 'fwi_model', 'fwi',
                             'inverted_model', 'inverted_velocity_model_2d'
                      UNION ALL
                      SELECT 4, 2, 'fwi_loss', 'fwi',
                             'loss', 'loss_curve'
                  ) AS expected_input
                  WHERE json_type(
                            plan.document_json,
                            printf(
                                '$.nodes[%d].inputs[%d]',
                                expected_input.node_index,
                                expected_input.input_index
                            )
                        ) != 'object'
                     OR (
                         SELECT COUNT(*)
                         FROM json_each(
                             plan.document_json,
                             printf(
                                 '$.nodes[%d].inputs[%d]',
                                 expected_input.node_index,
                                 expected_input.input_index
                             )
                         )
                     ) != 2
                     OR json_extract(
                         plan.document_json,
                         printf(
                             '$.nodes[%d].inputs[%d].port',
                             expected_input.node_index,
                             expected_input.input_index
                         )
                     ) != expected_input.target_port
                     OR json_type(
                         plan.document_json,
                         printf(
                             '$.nodes[%d].inputs[%d].source',
                             expected_input.node_index,
                             expected_input.input_index
                         )
                     ) != 'object'
                     OR (
                         SELECT COUNT(*)
                         FROM json_each(
                             plan.document_json,
                             printf(
                                 '$.nodes[%d].inputs[%d].source',
                                 expected_input.node_index,
                                 expected_input.input_index
                             )
                         )
                     ) != 3
                     OR json_extract(
                         plan.document_json,
                         printf(
                             '$.nodes[%d].inputs[%d].source.node_id',
                             expected_input.node_index,
                             expected_input.input_index
                         )
                     ) != expected_input.source_node
                     OR json_extract(
                         plan.document_json,
                         printf(
                             '$.nodes[%d].inputs[%d].source.port',
                             expected_input.node_index,
                             expected_input.input_index
                         )
                     ) != expected_input.source_port
                     OR json_extract(
                         plan.document_json,
                         printf(
                             '$.nodes[%d].inputs[%d].source.data_type',
                             expected_input.node_index,
                             expected_input.input_index
                         )
                     ) != expected_input.data_type
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM json_each(plan.document_json, '$.nodes') AS recipe_node
                  WHERE json_type(
                            recipe_node.value, '$.parameters'
                        ) != 'object'
                     OR json_extract(
                            recipe_node.value, '$.parameters'
                        ) != json_extract(
                            plan.document_json, '$.nodes[0].parameters'
                        )
                     OR json_type(
                            recipe_node.value, '$.resources'
                        ) != 'object'
                     OR json_extract(
                            recipe_node.value, '$.resources'
                        ) != json_extract(
                            plan.document_json, '$.nodes[0].resources'
                        )
                     OR json_type(
                            recipe_node.value, '$.outputs'
                        ) != 'array'
                     OR json_array_length(
                            json_extract(recipe_node.value, '$.outputs')
                        ) != 8
                     OR EXISTS (
                         SELECT 1
                         FROM (
                             SELECT 0 AS output_index,
                                    'inverted_model' AS port,
                                    'inverted_velocity_model_2d' AS data_type
                             UNION ALL
                             SELECT 1, 'loss', 'loss_curve'
                             UNION ALL
                             SELECT 2, 'true_model_figure', 'figure'
                             UNION ALL
                             SELECT 3, 'initial_model_figure', 'figure'
                             UNION ALL
                             SELECT 4, 'inverted_model_figure', 'figure'
                             UNION ALL
                             SELECT 5, 'model_error_figure', 'figure'
                             UNION ALL
                             SELECT 6, 'shot_gathers_figure', 'figure'
                             UNION ALL
                             SELECT 7, 'loss_curve_figure', 'figure'
                         ) AS expected_output
                         WHERE json_type(
                                   recipe_node.value,
                                   printf(
                                       '$.outputs[%d]',
                                       expected_output.output_index
                                   )
                               ) != 'object'
                            OR (
                                SELECT COUNT(*)
                                FROM json_each(
                                    recipe_node.value,
                                    printf(
                                        '$.outputs[%d]',
                                        expected_output.output_index
                                    )
                                )
                            ) != 2
                            OR json_extract(
                                recipe_node.value,
                                printf(
                                    '$.outputs[%d].port',
                                    expected_output.output_index
                                )
                            ) != expected_output.port
                            OR json_extract(
                                recipe_node.value,
                                printf(
                                    '$.outputs[%d].data_type',
                                    expected_output.output_index
                                )
                            ) != expected_output.data_type
                     )
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM json_each(plan.document_json, '$.nodes') AS recipe_node
                  WHERE json_type(recipe_node.value, '$.algorithm') != 'object'
                     OR (
                         SELECT COUNT(*)
                         FROM json_each(recipe_node.value, '$.algorithm')
                     ) != 2
                     OR json_extract(
                         recipe_node.value, '$.algorithm.id'
                     ) != 'deepwave.acoustic_fwi'
                     OR json_extract(
                         recipe_node.value, '$.algorithm.version'
                     ) != '1.6.0'
              )
              AND (
                  SELECT COUNT(*)
                  FROM dag_node_execution_admissions AS active_admission
                  JOIN dag_node_state_events AS active_state
                    ON active_state.task_id = active_admission.task_id
                   AND active_state.plan_id = active_admission.plan_id
                   AND active_state.node_id = active_admission.node_id
                  WHERE active_admission.task_id = NEW.task_id
                    AND active_state.state IN (
                        'Queued', 'Running', 'Waiting', 'Retrying'
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM dag_node_state_events AS newer
                        WHERE newer.task_id = active_state.task_id
                          AND newer.plan_id = active_state.plan_id
                          AND newer.node_id = active_state.node_id
                          AND newer.revision > active_state.revision
                    )
              ) < 2
          )
      )
)
BEGIN
    SELECT RAISE(
        ABORT, 'DAG node admission requires the exact current ready case'
    );
END;

-- A successful DAG whose every current node was satisfied by an exact cache
-- hit has no dispatch intent by design.  Admit only that complete, current
-- approval-bound evidence to the existing Trash lifecycle; partial hits,
-- historical approvals/plans, and any task with a dispatch intent retain the
-- inherited fail-closed behavior.
DROP TRIGGER task_visibility_trash_requires_resolved_terminal;

CREATE TRIGGER task_visibility_trash_requires_resolved_terminal
BEFORE INSERT ON task_visibility_events
WHEN NEW.action = 'trashed' AND NOT EXISTS (
    SELECT 1
    FROM tasks
    WHERE tasks.task_id = NEW.task_id
      AND tasks.project_id = NEW.project_id
      AND tasks.principal_id = NEW.principal_id
      AND tasks.status IN ('Succeeded', 'Failed', 'Cancelled')
      AND (
          (
              tasks.status = 'Cancelled'
              AND EXISTS (
                  SELECT 1 FROM task_abandonments
                  WHERE task_abandonments.task_id = tasks.task_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM dispatch_intents
                  WHERE dispatch_intents.task_id = tasks.task_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM approvals
                  WHERE approvals.task_id = tasks.task_id
                    AND approvals.decision = 'approved'
              )
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN effective_dispatched_intents AS outcome
                ON outcome.intent_id = intent.intent_id
              WHERE intent.task_id = tasks.task_id
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN worker_retry_exhaustions AS exhaustion
                ON exhaustion.intent_id = intent.intent_id
               AND exhaustion.attempt_number = 2
              JOIN run_events AS terminal
                ON terminal.task_id = exhaustion.task_id
               AND terminal.sequence = exhaustion.terminal_event_sequence
              WHERE intent.task_id = tasks.task_id
                AND tasks.status = 'Failed'
                AND exhaustion.task_id = tasks.task_id
                AND exhaustion.project_id = tasks.project_id
                AND exhaustion.principal_id = tasks.principal_id
                AND terminal.event_type = 'node_failed'
                AND terminal.task_status = 'Failed'
                AND terminal.document_hash = exhaustion.terminal_event_hash
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN worker_exit_retry_exhaustions AS exhaustion
                ON exhaustion.intent_id = intent.intent_id
               AND exhaustion.attempt_number = 2
              JOIN run_events AS terminal
                ON terminal.task_id = exhaustion.task_id
               AND terminal.sequence = exhaustion.terminal_event_sequence
              WHERE intent.task_id = tasks.task_id
                AND tasks.status = 'Failed'
                AND exhaustion.task_id = tasks.task_id
                AND exhaustion.project_id = tasks.project_id
                AND exhaustion.principal_id = tasks.principal_id
                AND terminal.event_type = 'node_failed'
                AND terminal.task_status = 'Failed'
                AND terminal.document_hash = exhaustion.terminal_event_hash
          )
          OR EXISTS (
              SELECT 1
              FROM dispatch_intents AS intent
              JOIN dispatch_reconciliation_negative_resolutions AS negative
                ON negative.intent_id = intent.intent_id
              JOIN run_events AS terminal
                ON terminal.task_id = negative.task_id
               AND terminal.sequence = negative.terminal_event_sequence
              WHERE intent.task_id = tasks.task_id
                AND tasks.status = 'Failed'
                AND negative.task_id = tasks.task_id
                AND negative.project_id = tasks.project_id
                AND negative.principal_id = tasks.principal_id
                AND terminal.event_type = 'node_failed'
                AND terminal.task_status = 'Failed'
                AND terminal.document_hash = negative.terminal_event_hash
          )
          OR (
              tasks.status = 'Succeeded'
              AND tasks.current_plan_id IS NOT NULL
              AND tasks.current_approval_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM dispatch_intents AS cache_intent
                  WHERE cache_intent.task_id = tasks.task_id
              )
              AND EXISTS (
                  SELECT 1
                  FROM plans AS current_plan
                  JOIN approvals AS current_approval
                    ON current_approval.task_id = current_plan.task_id
                   AND current_approval.approval_id =
                       tasks.current_approval_id
                   AND current_approval.plan_id = current_plan.plan_id
                   AND current_approval.plan_hash = current_plan.plan_hash
                  WHERE current_plan.task_id = tasks.task_id
                    AND current_plan.plan_id = tasks.current_plan_id
                    AND current_approval.decision = 'approved'
                    AND json_valid(current_plan.document_json)
                    AND json_type(
                        current_plan.document_json, '$.nodes'
                    ) = 'array'
                    AND json_array_length(
                        json_extract(current_plan.document_json, '$.nodes')
                    ) > 1
                    AND (
                        SELECT COUNT(*)
                        FROM dag_node_cache_hit_facts AS current_hit
                        WHERE current_hit.target_task_id = tasks.task_id
                          AND current_hit.target_plan_id = current_plan.plan_id
                          AND current_hit.target_plan_hash =
                              current_plan.plan_hash
                          AND current_hit.target_approval_id =
                              current_approval.approval_id
                    ) = json_array_length(
                        json_extract(current_plan.document_json, '$.nodes')
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM json_each(
                            current_plan.document_json, '$.nodes'
                        ) AS current_node
                        WHERE json_type(
                                  current_node.value, '$.node_id'
                              ) != 'text'
                           OR (
                               SELECT COUNT(*)
                               FROM dag_node_cache_hit_facts AS node_hit
                               WHERE node_hit.target_task_id = tasks.task_id
                                 AND node_hit.target_plan_id =
                                     current_plan.plan_id
                                 AND node_hit.target_plan_hash =
                                     current_plan.plan_hash
                                 AND node_hit.target_approval_id =
                                     current_approval.approval_id
                                 AND node_hit.target_node_id = json_extract(
                                     current_node.value, '$.node_id'
                                 )
                           ) != 1
                    )
              )
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'only a resolved terminal task can be moved to trash');
END;

-- Migration 0021 intentionally retained P2's weak terminal-won cancellation
-- compatibility when it rebuilt the DAG terminal trigger.  The exact P3
-- fixed Recipe has a stronger contract: a reusable Succeeded node receipt
-- must bind the latest canonical Worker heartbeat in state ``succeeded``.
-- Keep this as a Recipe-only guard so historical/P2 terminal projections are
-- unchanged.
CREATE TRIGGER fixed_recipe_terminal_success_requires_succeeded_worker
BEFORE INSERT ON dag_node_terminal_facts
WHEN NEW.node_state = 'Succeeded'
  AND EXISTS (
      SELECT 1
      FROM plans AS plan
      WHERE plan.task_id = NEW.task_id
        AND plan.plan_id = NEW.plan_id
        AND plan.plan_hash = NEW.plan_hash
        AND json_valid(plan.document_json)
        AND json_extract(
            plan.document_json,
            '$.extensions."org.agent_rpc.recipe".id'
        ) = 'forward_qc_fwi'
        AND json_extract(
            plan.document_json,
            '$.extensions."org.agent_rpc.recipe".version'
        ) = '1.0.0'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM worker_attempt_observations AS observation
      WHERE observation.attempt_id = NEW.attempt_id
        AND observation.observation_sequence = NEW.worker_observation_sequence
        AND observation.document_hash = NEW.worker_observation_hash
        AND observation.heartbeat_state = 'succeeded'
  )
BEGIN
    SELECT RAISE(
        ABORT,
        'fixed Recipe success requires exact succeeded Worker evidence'
    );
END;
