<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Stage 3: Skill-directed task generation

Read this after Stage 2 produces usable representatives. Codex executes this
procedure using EA and the benchmark's native tools. No Python task-service
implementation is required. The optional scripted runner has a different
hinted-feasibility gate and runs through a service; its model transport,
prompts and resume files are separate.

## Inputs and source authority

Use the selected Stage 2 artifact, its bound prepared data/checkpoint, the
[run profile](run-profile.md), native task and fixture definitions, and current
execution/grading evidence. Read the shared EA
[construction](../../../eval-author/references/construction-plan.md) and
[validation](../../../eval-author/references/task-validation.md) contracts for
checks within their supported provider scope. Reuse native equivalents where
needed and report any pending EA provider/acceptance support.

The maintained prompts are the source of generation instructions:

| Operation | Prompt |
| --- | --- |
| Candidate and reference arguments | [create_user_task.txt](../vendor/taste/artifacts/prompts/stage3/create_user_task.txt) |
| Separate starting fixture | [generate_db_initialization.txt](../vendor/taste/artifacts/prompts/stage3/generate_db_initialization.txt) |
| Candidate repair | [patch_task.txt](../vendor/taste/artifacts/prompts/stage3/patch_task.txt) |
| Fixture repair | [patch_db.txt](../vendor/taste/artifacts/prompts/stage3/patch_db.txt) |

Supply their policy, tool definitions, task/fixture definitions, prepared examples
and context. Apply context during repair too. In skill mode Codex performs the
authoring or delegates to the selected authoring model outside TASTE. Record that
executor and any prompt/model changes; retaining the method does not establish
identical experimental behavior to the release.

For gap-targeted runs, carry each selected audit objective and its observable
trigger and expected response into task and fixture generation, repairs and
validation as specified in [gap-targeted selection](gap-targeted.md).
The exact learned sequence remains binding. Retain intended objectives separately
from observed audit coverage.

## Generate for one representative

1. Take a usable representative in the selected artifact's order. Preserve its cluster
   identity and exact ordered tool names, including repetitions.
2. Generate the candidate: public task plus a private reference with concrete
   arguments. Keep it as `{"task": {...}, "reference_actions": [...]}` for the
   hint helper. The public task follows the benchmark's real format; private
   actions contain `name`, `arguments` and optional `requestor`/`action_id`.
   Check that its ordered action names equal the selected sequence.
3. Generate the starting fixture separately, conditioned on that candidate and
   reference. Use the benchmark's state/baseline/patch rules. A stateless task
   may have an empty fixture. Save both before starting validation.

A reference is a proposed solution, not independent correctness authority.
Preserve sequence names/order through repairs and full regeneration. Concrete
arguments can change between validation attempts.

## Validate through EA and native tools

Freeze the current reference, including arguments and actors, for each attempt.
Use EA/native tools to perform these operations, returning early on failure:

1. Check native task/state schemas, tool arguments and applicable policy rules.
   Initialize the fixture and replay the proposed reference against real tools.
2. Review the observed outputs/state against the intended task. Use EA's
   synthesis/alignment mechanisms where needed to construct meaningful checks
   and a coherent scenario. Preserve the frozen reference; an argument change
   requires a new attempt. Retain the effective task and fixture after any
   allowed refinement.
3. Recheck the refined task/reference, package it, and apply the same EA
   qualification controls and unhinted execution used for other tasks. Do not
   treat the target agent's failure to solve a healthy task as a task defect.

Record the effective candidate/fixture, success or failed step, evidence paths
and whether repair concerns the fixture or both candidate and fixture.

### Optional hinted diagnostic

The skill-directed flow modifies TASTE by making hinted execution optional and
not a qualification gate. When selected, construct hints using the existing implementation:

```bash
"$TASTE_PYTHON" "$TASTE_SKILL/scripts/taste_protocol.py" hints \
  --candidate "$CANDIDATE" --policy "$POLICY" --coverage-p "$HINT_P" \
  --seed "$ATTEMPT_SEED" --output "$HINT_RECEIPT"
```

Shuffling is enabled unless `--no-shuffle` is selected. The output records the
hint text, reference and RNG settings. Give the solver only its `hints` text as
private assistant guidance: the complete receipt contains the unredacted
reference and must not become solver or user-simulator input. Retain the exact
receipt for this attempt; do not generate hints a second time for logging.

Use the actual benchmark interaction model from a fresh equivalent state;
do not substitute invented dialogue or reference replay. Retain actual trace,
scores and errors. Do not silently retry until success or use a hinted trace as
qualification or primary evaluation evidence. A failed diagnostic does not
itself invalidate the task; investigate any concrete task or grader defect it
reveals under the shared validation and repair rules.

## Repair, regenerate or stop

On a repairable candidate failure, use the corresponding prompt: patch only the
fixture for a fixture error, or patch the candidate and then its fixture for a
candidate/argument/coherence error. Use the effective versions returned by EA.
Recheck sequence preservation and rerun the applicable validation after every
patch. If another hinted diagnostic is explicitly selected, use fresh hints.

Allow at most `max_patch_attempts` patches for each generated candidate. If they
fail, regenerate candidate then fixture for the same selected sequence, up to
`max_task_retries` full regenerations. These limits count **extra** attempts:
one initial generation plus the regeneration limit, each with one initial
validation plus its patch limit. Generation/patch-format failures also consume
the applicable attempt budget; do not silently retry malformed outputs forever.
The total run ceiling bounds these loops and any model transport retries.
Track these generation counters separately from EA's later qualification and
task-revision limits; entering qualification does not reset consumed generation
budget.

Unsupported required execution/grading capabilities stop dependent work.
Infrastructure or provider errors retain a resumable interruption; they do not
become negative candidate evidence or reasons to select easier representatives.
Follow EA's [execution recovery](../../../eval-author/references/execution-recovery.md)
for the recovery declaration and required fresh execution, including failures
in post-execution assessment. This protocol introduces no assessment-only retry
exception.
After ordinary candidate retries are exhausted, record that representative as
failed and continue the initial batch within budget.

## Recover representatives after the initial batch

Invoke the retained selection algorithm once, after the initial batch, when
failures remain and the run budget permits it. Write `outcomes.json` with one
entry per usable original cluster: `cluster_idx`, `success` and
`action_sequence`. For successes, use the exact sequence of the retained task.
Never infer success from an intended reference or a progress message.

```bash
"$TASTE_PYTHON" "$TASTE_SKILL/scripts/taste_protocol.py" recover \
  --workspace "$RUN_DIR" --clusters "$CLUSTERS" --outcomes "$OUTCOMES" \
  --model-name "$SEQUENCE_MODEL" --output "$RECOVERY" \
  --pool-size "$RECOVERY_POOL_SIZE" --temperature "$RECOVERY_TEMPERATURE"
```

Use the original Stage 1/2 workspace to resolve relative checkpoint paths.
This helper reuses sampling, frozen-center clustering and sequence plausibility
checks. It performs no task generation or native execution. It preserves the
saved length, distance and write-filter settings and freezes successful
sequences. Without a checkpoint or a successful center, it reports a skipped
recovery. The selected implementation tries recovery members for plausibility
and falls back to the medoid if none passes; such a replacement is explicitly
unvalidated. It still requires the full task-generation and EA validation path.

For each returned replacement, retain its parent sequence and cluster lineage,
then repeat generation and validation above. Do not recursively start another
recovery pass or fill shortfalls with hand-picked tasks. Report any unfilled
clusters after the pass or budget cutoff.

## Evidence, resume and handoff

Keep private per-attempt candidates, fixtures, hints, failures, native receipts
and costs in the run's working directory. Record consumed generation/patch/
recovery attempts and bind them to input fingerprints, selected sequences and
model/hint settings. Persist a successful payload and its receipts before
marking that representative complete. For atomic JSON/text replacement reuse
`atomic_write_text` in `src/stage3_tasks/task_set.py`; it performs no export or
native calls. Do not use `save_task_rows` in skill mode: that function belongs to
the scripted runner and invokes the configured export service.

On continuation, reconcile retained payloads, actual jobs and receipts before
skipping or rerunning an attempt. Missing evidence is not success. Keep consumed
budget after interruption and verify input/settings identity before reuse.
Skill-managed records are not automatically interchangeable with the scripted
runner's `tasks.json`/`--resume` contract; do not switch executors mid-run without
an explicit, checked conversion.

Package effective tasks through EA/native tooling and prove reload parity.
Return task paths, retained evidence, representative lineage and shortfall to
the skill's EA qualification/evaluation steps. Independent controls and genuine
unhinted runs establish later acceptance and coverage; the Stage 3 receipt does
not confer either.
