<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Native benchmark cohorts

TASTE cohorts require the [per-run integration flag](taste-integration.md).

Use this path when the user selects an existing benchmark's own runner and asks
for additional tasks, including an independent EA cohort or a TASTE cohort.
It does not convert the benchmark to Gym or Harbor. Unknown runtimes still need
an inspected execution adapter; a runner name or successful baseline is not
proof that generated task inputs, reset, grading and traces are supported.

## Reuse the agreed scope

Carry forward confirmed Ethos, selected source files, existing runtime evidence,
requested distinct-task counts, model roles and execution authorization. Record
`setup: native` and the inspected native runtime in `scopes.expand`; use the
runtime's actual Python and dependencies instead of the Gym/Harbor locator.
Preserve audit lifecycle and approvals when setting up expansion. Source and
content changes still require their applicable review; technical continuation
does not reopen unchanged decisions.

Use [coverage planning](coverage-planning.md) for meaningful examples and their
expected outcomes. Reuse an already agreed quota. Stable task IDs identify
distinct requests, fixtures or starting conditions; repeats do not add tasks.
An EA cohort may target reviewed tool, capability and failure-case gaps, including
variants involving already-covered tools. Explain each proposed improvement and
its uncertainty. Provisional baseline findings must remain labeled provisional.
For TASTE, keep the representatives and recovery rules chosen by the TASTE
protocol; do not replace them with EA's preferred gaps. Independent cohorts use
the same agreed baseline and do not learn from each other's generated cases.

## Prepare and generate

Inspect the selected runtime's task schema, public model input, fixture/reset
interface, tools, scorer, native result and trace format. Bind their exact source
versions. Verify primary trace conversion supports required task features,
including preloaded conversation history when present; retain copied context
without counting its actions as new execution. Keep benchmark-specific initialization and execution in EA integrations;
TASTE consumes prepared data and the shared generation protocol.

Follow the shared [construction contract](construction-plan.md), with provider
`native`, and [environment procedure](../../eval-author-environment/SKILL.md) for
state/reset and tool proof. Native tasks reuse the selected environment rather
than adopting another provider's file layout. Keep public requests separate from
reference actions, expected results, private hints and verifier-only state.
Materialize each immutable task package under `.eval-author/tasks/<stable-id>`;
retain private construction plans and execution evidence outside that package.
Register each new task as Draft through `journey_state.py record-task`.

Generate the agreed cases with the selected generation model. Retain prompts,
responses, source bindings, candidate/fixture revisions and all rejected attempts.
Run independent schema, argument, policy and scenario checks. Derive correctness
from the declared authority and actual reference execution, never from the
target agent's output. Preserve native reward separately from additional outcome
checks. If the native scorer does not grade requested answers or artifacts, add
and prove an independent checker; otherwise retain that limitation and do not
claim the unchecked requirement is qualified.

## Execute and qualify

Use the shared [validation controls](task-validation.md): known-correct,
realistic incorrect, empty/no-op, and applicable alternative and side-effect
controls. Every control runs the actual tools/scorer from equivalent fresh state.
An infrastructure error is not a successful negative control. A task allowing
correct inaction may correctly pass a no-op; its declared expectations must
match the public task. Preserve every substantive failure and repair.

Use the shared `task_evidence.py` recorder with `provider: native`. The maintained
native evidence adapter binds task/config/runtime/reset inputs, untouched native
results, runtime source hashes, and actual agent Compass Trace. It must reject changed
inputs, reused identities, private-hinted primary runs and mismatched conversion
bindings. The adapter normalizes evidence; the selected native grader remains
the grading authority. Keep native source artifacts, not only normalized scores.

The result contract uses `provider: native`, `native_run_id: /native_run_id`
and `native: {task, config, runtime, reset}` with task-relative input paths.
The runtime JSON contains `files: [{path, sha256}]` for the inspected executor,
adapter, scorer and runtime sources; reset records the fixture/reset selection.
The adapter must capture the actual executed inputs. Checks address untouched
result fields below `/native`, or bound execution fields below `/execution`.
`task_evidence.py run --native-source <descriptor.json>` takes a descriptor with
`execution` and an `artifacts` list of fresh absolute output paths. The versioned
execution envelope and conversion bindings are defined by the maintained
[evidence adapter](../scripts/eval_author_shared/native_evidence.py). Keep this
integration bookkeeping outside model-facing requests.

For TASTE, hinted feasibility is an optional diagnostic, not a qualification
gate. When performed, retain exact hints, reference and RNG settings plus actual
native interaction evidence. The complete hint receipt reaches neither the
solver nor the user simulator; only the permitted hint text reaches the solver.
Hinted runs remain separate from all primary unhinted runs.

Record all qualification controls and the agreed fresh unhinted target-agent
attempts with purpose `baseline`, including behavioral failures. Serialize
journey-state writes when qualifying independent tasks concurrently; a failed
state write resumes from verified retained evidence without rerunning execution.
Native task qualification requires passing current
controls and healthy, trace-backed execution; it does not require the target
agent to solve every task. Inspect native termination and provider errors, including
exhausted empty-response retries; do not treat an adapter's empty error field as
proof of a healthy execution. Use `task_evidence.py qualify-native` to verify the
complete attempt ledger before recording an accepted task. This qualifies the
task for evaluation, not a tool-gap closure or a coverage increase. The existing
automatic `task_pipeline.py verify` keeps its stricter repeated-success rules.

## Measure and hand off

Retain a per-task inventory with cohort, source/representative, meaningful
variation, requirement/check mapping, controls, hinted feasibility where relevant,
primary execution and native reward. Keep qualification, target performance and
coverage separate. Update existing suite/report artifacts and both successful
and unresolved cases; never fill a quota by renaming or copying a case.

Measure baseline and new primary Compass Trace against the same approved denominator.
For independent cohorts report baseline, baseline plus EA, baseline plus TASTE,
and their union. Keep the baseline source manifest immutable and create explicit
derived cohort selections. Do not claim an improvement before measurement.
