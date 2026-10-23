---
name: eval-author-trace-environment
version: 1.7.0
description: >-
  Experimental compatibility workflow for an explicit trace-environment request
  or continuation of an existing trace-environment workspace. New requests to
  derive tasks from recorded interactions go to task-create ATIF source mode or
  first-eval. Supports NeMo Gym, MLflow, Intake, OpenTelemetry and ATIF evidence.
triggers:
  - explicitly use trace-environment
  - resume the trace-environment workflow by name
  - continue an existing trace-environment workspace
not-for:
  - eval-author (use for the shared standard and routing)
  - mlflow-to-atif (use only to normalize MLflow traces into ATIF)
  - gym-to-atif (use only to normalize NeMo Gym Responses traces into ATIF)
  - eval-author-task-create (use for new ATIF-source tasks or an actionable audit coverage gap)
  - eval-author-inspect-trace (use to explain an Intake trace without creating an environment)
  - eval-author-environment (use when first-eval or task-create needs a reusable environment for its agreed cases)
compatibility: >-
  Python 3.11+, jsonschema 4.23+, referencing 0.28.4+; mlflow-to-atif for MLflow; gym-to-atif for NeMo Gym; nemo CLI for Intake; Harbor and
  Docker for Harbor proof. NeMo Gym output uses the sibling eval-author-task-create
  scaffolder. Verify authoring runtimes using eval-author’s shared locator
  and runtime-policy.json.
  Native-agent checks use the agent's configured provider.
metadata:
  author: Andrew Suter-Morris <asutermorris@nvidia.com>
  tags: [evaluation, harbor, gym, traces]
maturity: alpha
license: Apache-2.0
user-invocable: true
allowed-tools: Bash Read Write Grep Glob
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Eval Author: trace to environment

**Experimental.** All workflows and outputs from this skill are experimental,
including source normalization, task and fixture generation, validation, and
publication review/export. Identify them as experimental when presenting them
to the user.

## Purpose

Read `eval-author` for the shared evidence standard and boundaries. Turn one
recorded interaction into a reproducible Harbor or native NeMo Gym task with privacy
scrubbing and provider-native validation.

This scoped trace workflow keeps its selected source and existing proof boundary.
Reuse applicable shared state when supplied; do not add a full prerequisite or
Ethos interview to trace-only work. All new private working artifacts live below
`.eval-author/.working/trace-environments/`; keep historical evidence-bound paths
readable and preserve their receipts unchanged.

## Instructions

Follow the core's [provider-selection rule](../eval-author/SKILL.md#select-the-evaluation-provider),
choosing the output provider independently of the trace source. Both NeMo Gym and
Harbor are supported. A NeMo Gym source does not imply NeMo Gym output. Follow shared
steps 1-5, then the selected provider's construction path in Step 6, one task workspace per trace. Commands are in
`## Available Scripts`; an end-to-end run is in `## Examples`. Trace payloads,
credentials, and task workspaces never go into Git.

Preserve failed evidence: never rewrite or downgrade `summary.json`, delete a failed
`validation.json`, or change `construction.json`/`candidate.json` to pass a failed
qualification without first recording a repair. Never substitute the oracle for
an alternative control. A reviewed correct alternative rejected by the verifier
is a verifier defect to investigate, not a reason to weaken that control.

## Artifact contract

Use one workspace per task. The tree below describes Harbor output; the
[NeMo Gym extension](references/gym-output.md) adds a native `gym/` draft and its own
report in the same private workspace. Paths are generated workspace artifacts, not
bundled skill files; `extra.*` names elsewhere are ATIF fields, not file paths.

```text
.eval-author/.working/trace-environments/
  .gitignore
  <task-id>/
    private/source.atif.json
    private/canonical.atif.json
    private/privacy-audit.json
    private/{tool-call-inventory,tool-call-plan,tool-access,tool-call-generation}.json
    private/publications/<digest>/
    private/publication-review.json
    private/ground-truth/
    private/construction.json
    safe/trace.atif.json
    safe/privacy.json
    candidate.json
    task/
      environment/tool-call-fixtures/
    reproducibility.json
    validation.json
    summary.json
    summary.md
```

Source, evidence, ground truth, and jobs stay ignored. Harbor's whitelist-only
`export` command publishes reviewed Harbor products; NeMo Gym handoff stays private.
`init` writes the parent `.gitignore`, makes directories owner-only, refuses to replace an existing
workspace, and prints content-free JSON summaries.

## Step 1: create the private task workspace

Choose a stable lowercase kebab-case task ID from a caller-supplied case name
or trace ID; never put a person's name, email, account number, or other private
value in it. Run `init --task-id <task-id>`, then keep every source export and
intermediate conversion under the returned task directory, using `umask 077`
before redirecting provider output there.

## Step 2: normalize exactly one source to ATIF

Read and follow `references/sources.md`. One task per trace; never merge
unrelated traces, and never invent an instruction, tool result, file, or final
answer. Existing ATIF is used as-is; MLflow goes through `mlflow-to-atif`;
Intake reads one exact trace plus every detailed span; bounded JSON OTLP
exports map spans directly. For NeMo Gym, read and follow the `gym-to-atif` skill:
prefer a retained Harbor ATIF, otherwise use its offline adapter.
Record every missing or lossy field under
`extra.normalization.uncertainties` or `extra.normalization.losses`. If no
complete human instruction exists, record no_candidate instead of guessing.

## Step 3: make a text-only safe copy

```bash
python <skill_dir>/scripts/trace_environment.py prepare \
  --task-dir <task-dir> \
  --atif <canonical-atif-file> \
  --source-kind <atif|mlflow|intake|otel|gym>
```

The helper retains an exact owner-private original, writes the bounded canonical
ATIF and a scrubbed safe copy, replaces image parts with omission markers, and
records only redaction counts in the safe report. It recognizes common secret
fields, bearer tokens, private keys, and personal data classes (emails, phones,
SSNs, IP addresses, cluster-local hostnames, home paths). It also writes
`private/privacy-audit.json`: a complete string-field and character denominator,
URL hosts, and candidate name, organization, and street-address findings —
contextual leads, not automatic claims. Review every text field in
`safe/trace.atif.json`, every audit finding and host, then record who performed
this trace review with `review-privacy`. Generated task files do not exist yet;
review the complete publication separately after finalization.

Use the audit's field and character denominator to plan bounded reads; a
truncated display does not establish missing trace evidence. If review cannot
finish, retain that limitation without issuing a complete review attestation.

Never copy a redacted value into a verifier. An image-only user instruction
blocks candidacy. The scanner cannot establish that proprietary code is safe;
the contextual reviewer owns that judgment.

Run `audit-evidence --task-dir <task-dir>` before deciding candidacy for either
provider. Read [references/evidence-audit.md](references/evidence-audit.md) and
inspect the private report's source locations for missing, ambiguous, or
conflicting observations. Carry material gaps into candidate uncertainties;
the report measures recorded evidence coverage, not task correctness or readiness.

### Resolve tool-call access

For traces with tool calls, read `references/trace-derived-fixtures.md`, then
run `inventory-tool-calls`, `plan-tool-call-access`, and
`resolve-tool-call-access --decisions <decisions.json> --reviewer-kind <agent|human>`.
After privacy review, when at least one decision is mock, run
`generate-mock-tool-calls`.

Select `real`, `mock`, or `none` for every scoped tool; candidate finalization
requires complete decisions. Never substitute silently. The fixture reference
defines reviewed schema overrides for traces that record calls without schemas.
For NeMo Gym output, follow the native tool wiring in
[references/gym-output.md](references/gym-output.md); the generated MCP adapter
and `integration.toml` are Harbor-specific. For Harbor, copy generated fixtures
into the task image and merge `integration.toml` into
`task.toml`; finalization checks the wiring. Preserve the user's harness and model;
check registration and prove discovery/calls as the fixture reference specifies. The MCP adapter performs
exact-match replay. Fixtures are agent-visible and cannot hold verifier truth.

## Step 4: inventory ground truth and software requirements

Before deciding candidacy, look for ground truth that is distinct from the
agent's observed answer:

- a separate reference or successful trajectory;
- expected output, golden files, fixtures, or labeled data;
- verifier inputs or reference solutions; and
- explicit human or evaluator feedback that establishes correctness.

An agent answer is not ground truth merely because the trace succeeded. Record
it as `available`, `partial`, `absent`, or `unknown`; retain artifacts under
`private/ground-truth/` (owner-only, SHA-256 digests, `atif_step` or `external`
provenance) and its use as `comparison_only`, `verification`, or `none`. Never
copy private ground-truth values into the generated task; generalize only what
the verifier needs. `references/candidate-record.md` defines the provenance and
reason-code contracts.

Also inventory software needed to reproduce the work or verify the outcome:
libraries, CLIs, desktop applications such as CAD tools, services, hardware,
and proprietary or commercially licensed software. For each item record whether
it is actually required, its version when known, license class, local
availability, redistributability, evidence steps, and a short note, with the
same explicit ATIF-or-external provenance object. Inventory the complete build,
runtime, solution, and verifier dependency closure, pinning images, packages,
repositories, and tools where reproducibility depends on them. The grading path
must not download dependencies or contact a live service. A dependency needed
only by the verifier still belongs in the inventory; common misses include
fonts, compiler headers, package indexes, and language registries.

Do not silently replace required software with a different application or mock
when the requested behavior depends on the real product. Required unavailable
software prevents candidacy; unknown availability keeps the environment
`unproven` until resolved. Proprietary or non-redistributable software is
usable only with a legitimate, reproducible runtime path; never copy licensed
binaries into the task.

## Step 5: decide candidate or no_candidate

Use `safe/trace.atif.json` as the trace evidence for this decision. Proposal or
audit conclusions are context, not additional facts or automatic vetoes. Later
user corrections outrank earlier turns.
Every decision must cite real ATIF `step_id` values. Distinguish human intent
from harness-authored turns; a transport role alone does not settle authorship.
Read
[references/candidate-record.md](references/candidate-record.md) for the
`candidate.json` shape before writing the decision.
Run its read-only `check-candidate` command before construction; it checks
metadata, not execution, privacy review or readiness.

Read [references/construction-boundary.md](references/construction-boundary.md)
when a proposal precedes construction or missing state, ground truth, or runtime
access could change the decision. Apply the same construction criteria with and
without proposals. Explain any disagreement with a recommendation in the
candidate's existing `uncertainties` field, citing the relevant trace steps and
specific blocker or permitted reconstruction; do not optimize for candidate yield.

Most traces do not record the agent's starting workspace. When the capability,
instruction, and expected outcome are fully evidenced but the world state is
not, you may still build a task as a coherent synthetic world around the
recorded facts. Declare `"state_basis": "reconstructed"` in the candidate and
follow the reconstruction rules in `references/candidate-record.md`; the
default `"recorded"` basis claims the environment reproduces recorded state.
Never present a reconstruction as a replay of the trace.

Use `candidate` only when the request and expected outcome are complete,
reproducible without private or live external state, and objectively testable.
This basic flow supports execution verification only. Do not add a model judge
or convert a subjective, visual, or prose-quality outcome into a brittle string
check.

For no candidate, use the reference's null and empty fields and stable
`reason_codes`; retain the `ground_truth` and `software_requirements`
inventories even when empty. Ground truth may be absent without blocking a task,
but its absence must be explicit. Use
`required_software_unavailable` or `proprietary_runtime_unavailable` when the
software inventory blocks reproducibility.

For batch failures, use the reference's specific source or construction
`reason_codes` instead of `insufficient_trace_evidence`. Record failed commands
or contract checks in the NeMo Gym report or Step 7's `finalize --did-not-work`,
according to the selected output path.

## Step 6: author and prove a candidate environment

For `no_candidate`, skip construction and use Step 7's no-candidate finalization
for either provider. For every candidate, read
[references/task-fidelity.md](references/task-fidelity.md) for starting-state,
runtime setup, assertion, and alternative-solution review. These authoring
checks supplement provider proof; the helper does not machine-check all of them.
For NeMo Gym output, read and follow
[references/gym-output.md](references/gym-output.md) for construction, native
validation, and handoff. It replaces the Harbor-specific instructions below,
Step 7's candidate finalization, and Harbor batch/publication reporting.

For proof execution failures and result recaps, follow
[Execution recovery](../eval-author/references/execution-recovery.md) alongside
this workflow's integrity protocol. Diagnostic probes never complete proof.

For Harbor output, read and follow the complete
[Harbor construction, proof, and publication procedure](references/harbor-workflow.md)
before writing the task or executing controls. It covers the construction contract,
independent alternatives, per-check evidence, probes, repeated controls, reporting,
and bounded repairs. Do not weaken the verifier to make Oracle pass. Missing Harbor
or Docker leaves technical status `not_run` and the environment `unproven`.

## Step 7: finalize Harbor or no_candidate and verify the summary

Refresh and link `.eval-author/report.html` and `.eval-author/README.md` as the
private case/run catalog.
Preserve the candidate suite's operational `README.md` with its export-required inventory,
verified setup, rerun commands, result paths, and any remaining execution blockers
before handoff. Keep source traces, control evidence, and actual-agent evidence
distinct in the inventory.
No-candidate outcomes do not create a generated evaluation suite.
For a requested export, make the guide usable from the exported product root:
use its `task/` paths and include required setup without depending on private
reports or unexported configs. The guide is included in the publication preview
and requires the same exact-content review as the other exported files.

Record concise facts about the conversion and construction with `finalize`
(`--status candidate --human-reviewed --worked-well ... --did-not-work ...`, or
`--status no_candidate --reason ...`), then verify digests and required
artifacts with `check`. Do not include raw payloads or redacted values in these
flags.

Derived environment status:
failed technical proof becomes `failed`; passed proof with the required separate
no-network verification, `--human-reviewed`, and no required software whose
availability is `unknown` becomes `ready`; every other
candidate is `unproven`. A shared verifier is a contract error rather than an
unproven candidate. The human-review flag means a human supplied or reviewed
Relevant experience and the generalized task. It is distinct from the earlier
contextual privacy review by an agent or human.

## Harbor batch and publication

Before preparing a batch or exporting any workspace, including `no_candidate`,
read and follow [Harbor batch and publication](references/harbor-workflow.md#harbor-batch-and-publication).
Keep every source in the denominator and require exact-preview publication review;
privacy review and a valid workspace check do not authorize export.

## Examples

Steps 1–7 cover Harbor authoring; the [NeMo Gym extension](references/gym-output.md)
contains the native scaffold and validation example. For retained Harbor proof commands, use
[the repeat-run example](references/environment-integrity.md#repeat-and-negative-control-proof);
for transcription controls, use
[the copy-probe example](references/check-grammar.md#executed-copy-probes).

## Available Scripts

Invoke the helper as `python <skill_dir>/scripts/trace_environment.py
<command>`; there is no `run_script()` wrapper. Every command prints one JSON
object and exits nonzero on contract errors. NeMo Gym's standalone adapter lives in
the sibling `gym-to-atif` skill; put its owner-private output under
`<task-dir>/private/gym` before `prepare`.

See the [command reference](references/commands.md) for workspace preparation,
privacy and candidate review, proof and repair recording, and publication commands.

## Prerequisites

Python 3.11+ with jsonschema 4.23+ and referencing 0.28.4+ for the helper; the
sibling `mlflow-to-atif` skill for MLflow sources; the sibling `gym-to-atif`
skill for NeMo Gym sources; the `nemo` CLI for Intake
sources; an existing Harbor installation plus Docker for Harbor probes and proof.
For NeMo Gym output, use the existing separate NeMo Gym runtime and sibling
`eval-author-task-create` resources described in the NeMo Gym extension; Harbor and
Docker are not required unless the selected NeMo Gym components need them.

## Limitations

- Text-only evidence; image-only instructions block candidacy.
- Execution verification with binary rewards only: no model judge, rubric, or graded scoring.
- One bounded trace per task; no merging, deduplication, or corpus triage.
- Cannot prove unrecorded side effects, subjective outcomes, unavailable software, or private and live external state.
- Harbor probes and proof run on the local Harbor+Docker runtime; NeMo Gym uses its native local runtime.
- NeMo Gym output has native validation and a private report; Harbor's automated proof, batch readiness, and publication/export commands do not cover NeMo Gym products.
- Reconstructed-state tasks generalize the recorded capability; they never recover unrecorded state.

## Troubleshooting

- On a contract failure, preserve the artifacts and record the exact failed
  command in the NeMo Gym report or Harbor's `finalize --did-not-work`; never bypass
  privacy, isolation, or proof gates.
- `validate-task` exits 1: fix each listed issue; hints name the contract. Rerun after every edit.
- `probe` reports `not_run`: Harbor is missing; probing is optional, proof is not.
- `record-validation` fails with `missing_check_evidence`: apply
  `references/check-grammar.md` and rerun the jobs (`--allow-aggregate-only` only for historical proof).
- `record-validation` fails on NOP PASS rows: the untouched environment passes a scored check; tighten it and repair.
- `check` reports stale or missing proof after a repair: re-record `record-reproducibility` and re-prove.
