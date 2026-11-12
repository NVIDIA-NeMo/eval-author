<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Prepared inputs and EA execution

TASTE consumes prepared data for sequence learning and clustering. In the default
skill-directed Stage 3, Codex follows the [protocol](stage3-protocol.md) using
EA/native tools directly. A new benchmark needs real initialization, interaction
and grading support; it does not need a TASTE-specific Python adapter.
Benchmark code, native prompts and domain rules stay outside `vendor/taste/`.

## Prepared input contract

Use a fresh `prepared.json` with this structure:

```json
{
  "schema": "taste.prepared-inputs.v1",
  "domain": "MB1",
  "resources_dir": "/absolute/private/run/resources",
  "options": {
    "seed_path": "/absolute/private/run/resources/seeds.jsonl",
    "seed_split": "train"
  }
}
```

`domain` is a label, not a backend registry key. Stages 1 and 2 and skill-directed
Stage 3 work without `task_service`. Record the Stage 3 executor in the run
profile, not as a new prepared configuration field.

For sequence work, supply `policy.md`, `tool_spec.json` and normalized seeds.
Optional `context.json` supplies context; `domain.json` may declare
`action_group_map`. Tool metadata maps names to schemas and optional
READ/WRITE/GENERIC classifications. Shared TASTE prompts consume these data;
`options.prompts_dir` can provide reviewed overrides.

Stage 3 additionally needs task/fixture definitions and examples. Codex may use
existing native definitions; the scripted generator requires `task_schema.json`
and `fixture_schema.json`. Include any prepared schemas before training when
possible: adding bound resources later changes the input digest.

Each normalized seed has `id`, `split`, `actions` and an optional `native`
object containing a candidate example. Actions are ordered objects with `name`
and an `arguments` object. Preserve repeated IDs/calls, argument types and empty
references. EA preparation converts native task fields once. Retain source
selection and hashes; never silently substitute held-out tasks or generated
cohorts for the selected seed split.

## EA and native execution

Reuse the selected benchmark's runner, tools, state preparation and scorer
through EA. For MB1, identify how to materialize a task/fixture, replay a reference,
run the actual user-agent interaction, grade it and package the effective task.
Codex follows the Stage 3 protocol using those capabilities directly. Missing
native semantics remain unsupported rather than being replaced by a scripted
success trace.

Use the shared EA environment/construction/validation instructions linked from
the protocol. Source/provider eligibility for EA acceptance remains a separate
check; native execution alone does not establish a supported acceptance mode.

## Optional scripted task-service boundary

Read this section when using the automated `--stage generate` runner or extending
its service. The skill-directed path requires no such class. Configure `task_service` as a compatible `module:factory` with its native
root and options. Only the scripted Stage 3 runner calls
`factory(config=config)`; service options stay opaque to sequence stages.

The generator produces `{"task": {...}, "reference_actions": [...]}` and a
separate fixture according to `task_schema.json` and `fixture_schema.json`.
The service implements two calls from `src/backends/contracts.py`:

```python
validate(candidate, fixture, *, hints: str, settings: dict) -> ValidationResult
export(records, path) -> Path
```

EA validation provides native checks, applicable synthesis/alignment, reference
replay, actual interaction and grading. It consumes the supplied hints privately
and preserves reference actions, arguments and actors within the attempt.
Return the effective candidate/fixture, native task, failure details and
`repair_scope` (`fixture` or `candidate_and_fixture`). The scripted runner then
performs its existing repair loop. Unsupported required capabilities are terminal
incompatibility; runtime/provider failures are infrastructure errors.

Export receives records with `id`, `candidate`, `fixture`, `native_task` and
`generation` provenance. It preserves request/state/grading semantics and keeps
private provenance and evidence out of native model inputs. The runner's atomic
`tasks.json` is its resume authority; it reconciles progress against successful
payloads and rebuilds the service-produced `native-tasks.jsonl`. Restore a
complete checkpoint or start fresh if payloads/identities conflict. These files
are specific to the scripted executor, not requirements for a benchmark adapter
in skill mode.

## Evidence and reuse

Reuse current compatible environment/runtime evidence. Prove initialization,
real tool outputs/errors, reset/isolation, known-correct and incorrect controls,
optional hinted interaction and export/reload parity. Exercise generation, repair and
representative recovery through the selected executor. Retain actual failures,
hints and settings. Native services remain reusable EA automation.

Scripted services link evidence through `evidence_dir`; skill mode retains
EA/native receipts under its private run directory. Deterministic controls do
not establish live-model feasibility, generation quality or EA acceptance. Use
applicable current-runtime evidence before bulk generation and preserve already
granted execution limits without repeated authorization.

Checkpoints/clusters bind the prepared specification, resource/prompt hashes and
length settings. Scripted resume also binds cluster and generation settings;
skill continuation follows the [protocol's evidence rules](stage3-protocol.md#evidence-resume-and-handoff).
Old native-backend configurations/checkpoints require fresh preparation. Retain
recovery lineage and exact reference lengths. Duplicate empirical lengths keep
their weights; sampled lengths are not final-cohort quotas.

The sequence CLIs create private append-only `events.jsonl` journals: Stage 1
under `<save-dir>/evidence/`, Stage 2 under `<output>.evidence/`. Each destination
must be new. Retain the journals with checkpoints and cluster artifacts. They
record seed/skipped/draw decisions, complete validator text and effective
verdicts, each model transport attempt and reported usage, sampled pools,
cluster geometry, ordered replacement checks and every recovery round before
slots are replaced. Unknown model cost is `null`; exception records retain
classes and available status codes, excluding HTTP headers and bodies. A
started event without its completion is an interrupted attempt, not a verdict.

### Recover interrupted Stage 1 training

Use the training command's `--resume-checkpoint` and `--resume-journal` together,
with a fresh workspace and save directory. Keep the original inputs, total draw
target, validator model, length policy and learning settings. Resume checks the
checkpoint against recorded decisions, replays later completed decisions without
calling the model again, and links the new journal to its unchanged ancestors.
A sampled draw without a verdict is validated using its retained sequence;
the interrupted transport remains separate evidence with unknown cost where
the provider supplied none.

Current checkpoints and draw boundaries retain Python and NumPy random states.
They support continuation of the sampling state with compatible runtime versions;
they do not guarantee identical responses from another live model request.
Legacy checkpoints omit these states. Recovering their learned state requires
an explicit `--resume-rng-seed` and records a new stochastic boundary. Do not call
that an exact continuation of the original random stream. If exact continuation
is required, missing random state is a blocker.

Before launching recovery, establish that the original worker is terminal or
its execution handle is missing; a polling timeout alone is insufficient. Retain
source versions, runtime identity and parent artifact hashes with the new launch.
Follow [EA execution recovery](../../../eval-author/references/execution-recovery.md)
for evidence preservation and existing authorization. Sequence recovery changes
neither cohort selection nor the distinction between generation and evaluation.
