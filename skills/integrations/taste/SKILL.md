---
name: eval-author-taste
description: "Follow the three-stage TASTE method: learn and cluster tool sequences with bundled algorithms, then generate and validate sequence-conditioned tasks through Codex and Eval Author. Use when TASTE generation from an EA audit is requested; ordinary EA authoring stays with eval-author-task-create."
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->


# Generate tasks with EA and TASTE

Use this skill as the procedure for the three TASTE stages.
Codex coordinates the run. Bundled TASTE code performs sequence learning and
clustering; Codex follows TASTE's task-generation protocol using EA and the
benchmark's native tools for Stage 3. A new benchmark needs usable data,
execution and grading, but no TASTE-specific Python adapter for this path.

Use [eval-author](../../eval-author/SKILL.md) for shared evidence, authorization,
runtime and task lifecycle. Reuse confirmed ETHOS, the selected audit and
execution authorization. Preparing or reviewing the workflow launches no model
calls; an already authorized run needs no repeated readiness pause.

## Prepare the run

Read the approved audit specification and available coverage report/measurement
JSON. Explicitly requested sequence work may proceed while baseline measurement
is pending; retain that status and the approved source selection. Full coverage
comparisons still require actual baseline measurement.
Check agent, corpus and denominator identity. Map audit item IDs to evidence
limitations, triggers, expected outcomes, relevant tools/policy and observable
checks using [EA coverage planning](../../eval-author/references/coverage-planning.md).
Ask the user to choose **gap-agnostic** or **gap-targeted** selection for each
new run unless the request or an earlier decision for this run already selects
a mode. Gap-agnostic retains
the upstream representative selection; gap-targeted uses audit objectives to
select among learned sequences. Explain that selection does not establish
observed coverage. Record the choice and the fixed audit baseline.

Record the [run profile](references/run-profile.md): source split, distinct-example
target, length policy, models by role, training/clustering settings, hint contract,
patch/regeneration limits and total budget. Use a fresh private run directory.
Preserve original seed action order, duplicates, empty references and missing
reads. Never silently train on held-out tasks, generated cohorts or target results.

Read [prepared inputs and EA execution](references/backend-adapters.md) to prepare
normalized trajectories, tools and policy. Stage 3 additionally needs the native
task/fixture format and a real execution/grading path. Reuse compatible EA
preparation and environment evidence. A native capability gap does not block
independent authorized sequence work.

The default Stage 3 executor is **Codex following this skill**. The optional
**scripted runner** currently requires hinted feasibility; use it only when the
user explicitly selects that different method and a compatible EA
`validate`/`export` service already exists or its implementation is in scope.
The [reuse map](references/upstream-reuse.md) gives source paths and commands.
Record the executor and model/prompt differences; a skill-directed run is not an
exact reproduction of the original scripted model setup.

## TASTE Stage 1: Learn sequence patterns

Run bundled Stage 1 with `ActionSequenceNGramModelSampler`, `BayesianSignedNGram`
and `ActionSequenceValidator`. Preserve seed validation, positive/negative
feedback, problematic indices, annealing, duplicate weights, attempted draws
and checkpoints. Policy and tool schemas inform the LLM plausibility check;
this stage does not execute the benchmark. Do not replace training with an LLM
request to list workflows.

Use `LengthDistributionConfig` for the selected policy. Distinguish empirical
weighted draws from fitting/KDE and skew-normal behavior, and record excluded
zero-call mass. Checkpoint reuse requires matching inputs and settings. Compare
drawn, pooled, selected and accepted lengths because later filtering changes
the distribution. Generation judgments remain separate from unhinted results.

## TASTE Stage 2: Select diverse sequences

Run bundled Stage 2 with weighted distance and `ActionSequenceClusterer`.
Retain pool, groups, medoids, members, plausibility decisions, distance-ordered
replacement and frozen-center recovery lineage. The native runtime and Stage 3
task service are unnecessary. In gap-agnostic mode, map representatives to audit
objectives without filtering or re-ranking them against gaps or target outcomes.
In gap-targeted mode, follow [gap-targeted selection](references/gap-targeted.md)
after clustering; do not change or fabricate the learned sequence pool. Never
use target-agent results to select representatives.

A request for `k` clusters does not establish `k` valid tasks. Keep successful
representatives fixed during recovery, respect the run ceiling and report any
shortfall. Intended audit relevance and reference-sequence diversity remain
separate measurements.

## TASTE Stage 3: Build executable tasks

Read and follow the [Stage 3 protocol](references/stage3-protocol.md) and, for
gap-targeted runs, the objective handoff in the gap-targeted reference. It is the
single procedure for generation, EA handoffs, optional hinted diagnostics, bounded
repair/regeneration, recovery and private checkpoints. Use its linked TASTE
prompts for task/reference generation and the separate fixture step. Codex can
perform these authoring steps directly; EA/native tools perform the checks,
synthesis/alignment, interaction and scoring outside TASTE.

Keep ordered reference tool names, repetitions and representative lineage.
Reuse the executable hint and recovery helpers when applicable instead of
improvising their algorithms. Preserve actual errors, effective task/fixture
changes, individual attempts and costs. Hinted diagnostics are optional and do
not gate EA qualification; they never replace an unhinted target run.

Do not invoke ordinary EA task-create to choose a different task or invent trace
admission/tool-gap eligibility. Reuse the shared construction and validation
contracts at the points identified by the protocol. Required unsupported native
semantics remain explicit capability gaps.

## Qualify and evaluate through EA

Package native tasks at stable `.eval-author/tasks/<task-slug>/` paths, or the
existing suite location allowed by the selected EA flow. Never overwrite an
existing task. Record the TASTE run, source representative and distinct-example
mapping; repeated trials are not new tasks. Keep private generation work under
`.eval-author/.working/taste/<run-id>/` and native evidence in the selected EA
job/measurement locations.

Prove request, initialized state, reference and grading parity after packaging.
Reuse [eval-author-environment](../../eval-author-environment/SKILL.md) when
preparation is needed, then the shared
[construction](../../eval-author/references/construction-plan.md) and
[task-validation](../../eval-author/references/task-validation.md) contracts.
Native state reward may need separately named answer/artifact checks; preserve
native reward, valid alternative solutions and disclosed limits.

Register tasks as Draft through EA's supported lifecycle. For native benchmark
runners, retain qualification receipts and report unsupported EA acceptance modes
explicitly. Automatic tool-gap
acceptance remains restricted to eligible measured tools through
[eval-author-task-create](../../eval-author-task-create/SKILL.md). TASTE-generated
representatives do not automatically satisfy that mode or trace admission.
Keep unsupported source/provider acceptance pending; do not fabricate eligibility.

Run the actual agent without hints under the selected model, simulator, budget,
reset and grading contract. Retain every requested attempt, error and cutoff;
larger-budget diagnostics cannot replace primary failures. Follow
[execution recovery](../../eval-author/references/execution-recovery.md). Keep
private hinted traces outside the primary corpus and use genuine provider
outputs and the shared [trace loader](../../eval-author/references/trace-loading.md).

## Measure and publish

Use [eval-author-audit](../../eval-author-audit/SKILL.md) on primary traces loaded
through NeMo Compass with the approved audit. Publish additions-only and
baseline-plus-additions coverage by witness union. Separate native/supplemental reward,
tool/capability/failure coverage and reference diversity. Do not select on target
outcomes and then call those results held out.

Report requested/authored/validated/executed distinct counts, losses, costs,
method deltas, shortfalls and uncertainty. Update the existing report, rerun guide
and benchmark UI through [suite publication](../../eval-author/references/suite-readme.md).
EA journey state remains workflow authority; TASTE artifacts retain generation
evidence rather than creating a second acceptance procedure.
