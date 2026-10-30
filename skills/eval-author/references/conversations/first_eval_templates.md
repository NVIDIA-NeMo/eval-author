<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# First_eval task table data

The first_eval proposal, first-task review, and suite review use one three-column
table. The fixed column copy lives in
[`first_eval_templates.py`](../../scripts/conversation_flows/first_eval_templates.py).
Render the full checkpoint with `conversation_renderer.py`. Don't paste a second
table definition into a skill or report.

Display the task ID in bold before the behavior in the Task cell, separated by
` - `. Display the verifier type in bold before the scoring rule in the Verifier
cell. Keep the five saved fields separate so the report and later checks retain
their exact values. This compact layout gives narrow chat panes fewer columns
to fit.

Each proposed task in `scopes.create.first_eval.plan.tasks[]` needs a stable
`id`, a plain-language `behavior`, an `expected_result`, a `verifier_type`,
and a `scoring_rule`. Use a prefixed slug such as
`INV-01-exact-sku-lookup` for the ID, never a row number. Use one of
`Custom code`, `LLMaJ`, or
`Custom code + LLMaJ` for `verifier_type`. The scoring rule is one short
sentence that states the score decision and what a pass means. For example:
`Pass at 1 when the requested SKU and count match exactly, otherwise score 0.`
Store the proposed rule before asking for design approval. Keep
`example_ids` and the actual input in the saved design even though the table
doesn't display them.

After authoring, refresh the report with task metadata keyed by the exact
saved task path. Set `display_id` to the proposed `id` and add
`scoring_rule` with the implemented score and pass condition. Keep
`grading_kind` and `grading_rule` as existing report schema keys. They are
internal compatibility fields. The first_eval conversation calls them
verifier type and scoring rule.
For NeMo Gym, also save the scaffold's exact environment manifest path in
`tasks[<task-id>].workload_path`. The report can list both that workload and
resource configuration under the task root. The authored-row reader joins the
manifest path and checks the saved task root's revision, regardless of which
nested README the report selects.

Read the authored rows from `.eval-author/.working/report/state.json` with
`first_eval_table_data.py --repo <repository> --stage review_first` or
`--stage review_suite`. Pass its `task_rows` unchanged in the renderer's
`facts`, alongside fresh `observations` from `first_eval_state.observe`.
The reader joins saved task workloads, checks the report's stable display IDs and
README revisions, and binds rows to current task digests. A missing or
ambiguous authored task blocks review until the report is corrected and
refreshed. Legacy rows without a documented score display
`Not documented`; don't infer a pass rule from observed rewards.
