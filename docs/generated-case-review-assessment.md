<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generated case review capability assessment

Harbor's existing local viewer can support review of generated Harbor tasks and
their recorded trials. Better logging and README links can make those views
discoverable, but do not provide readable review of native Gym JSONL or standalone
source traces. Reuse Harbor and scope additional rendering to those gaps.

This assessment supports [ASE-1029](https://linear.app/nvidia/issue/ASE-1029/improve-visibility-and-review-of-generated-test-cases).
The native-runtime findings below describe the assessment before the additional
reader. The bundled [local reader](../skills/eval-author/references/local-review.md)
now covers selected Gym JSONL and ATIF, MLflow, or Gym traces without a server or model
calls. Neither native nor bundled rendering establishes evaluation quality.
The broader suite README guidance is a separate change in
[PR #43](https://github.com/NVIDIA-NeMo/eval-author/pull/43).

## Versions and evidence

Assessed on September 30, 2026 against the repository's pinned versions:

- **Harbor 0.20.0:** installed CLI, packaged frontend, source inspection, and
  29 synthetic API and directory-detection checks. Native task scaffolding and
  Harbor's models produced the inspection fixtures. All run results, rewards,
  exceptions, and traces were invented fixtures, not evaluation outcomes.
  Chromium 145 browser inspection confirmed rendered task instructions and
  README, verifier source, separate pass/fail/error/config-only trial rows, and
  ATIF messages with expandable tool arguments and observations. Missing,
  malformed, and config-only trajectory states were also inspected in-browser.
- **Gym v0.6.0**, commit `3045a793346a31291d7ea4ae6af3f94a35036ce5`:
  pinned source and versioned documentation, plus six dependency-free source CLI
  help probes. A configured Gym runtime was unavailable; dataset preparation,
  profiling, and health checks were not executed.
- **Gym rollout loading:** NeMo Compass's Gym loader, through Eval Author's
  `trace_loading.py`, requires an explicit physical-line selection for JSONL
  and retains the exact source row. Loading supplies structured evidence, not
  a human review interface.

No model calls, live evaluations, customer traces, or container jobs were used.
Current online documentation can describe features newer than these pins.

## Native capabilities and gaps found

| Review need | Existing support | Remaining gap |
| --- | --- | --- |
| Read a Harbor case before execution | Task mode exposes instructions, configuration, README, fixtures, verifier, and reference solution files. | A case still needs its expected outcome and grading explained. A task listing does not prove validation or execution. |
| Inspect Harbor attempts | Job mode lists trials with individual rewards, errors, verifier output, and retained trajectories. Multiple attempts remain distinct. | Task and job modes are separate; authoring provenance and generated-case links need the suite handoff. |
| Find nested generated tasks | The task scanner accepts tasks directly inside the supplied directory. | It does not recursively find tasks under a mixed trace-workspace collection. Supply each actual task parent. |
| Review missing or invalid traces | A missing Harbor trajectory returns no data; malformed trajectory JSON returns an API error. Both showed the same "No trajectory" browser state. | The empty view alone cannot distinguish absence from parse failure. Report the recorded reason and preserve failed attempts. |
| Open a standalone source ATIF | The tested Harbor viewer reads trajectories within its job/trial layout. | A source-only directory produces no jobs; passing the ATIF file itself fails. Source evidence needs a separate readable view. |
| Review Gym dataset cases | `gym dataset render` materializes prompts into JSONL. Task manifests, verifier code, and dataset fields remain inspectable. | Rendering here produces another JSONL file, not a case browser. Inputs and verifier-only expectations need a readable presentation. |
| Review Gym rollouts | `gym eval profile` produces metrics; `gym eval health-check` reports evidence quality; BLADE provides agent-assisted analysis. | None of these establishes a built-in human transcript viewer in the inspected pin. |

Harbor observations come from the installed `harbor.cli.view`,
`harbor.viewer.task_scanner`, `harbor.viewer.scanner`, `harbor.viewer.server`,
and `harbor.viewer.trial_utils` modules. Its
[official viewer guide](https://docs.harborframework.com/core-concepts/results/view-job-results)
describes the supported local job-review workflow; installed-version checks
establish the limits above.

For Gym, the pinned [command registry][gym-cli] and
[dataset-render contract][gym-render] establish command availability and output
format. [Profiling][gym-profile] uses paired materialized inputs and rollout
indices; [health checking][gym-health] marks checks requiring missing capture as
unobserved, separately from answer correctness. [BLADE][gym-blade] requires an
agent to analyze retained artifacts. The legacy `ng_viewer` was
[deprecated in v0.2.0](https://github.com/NVIDIA-NeMo/Gym/releases/tag/v0.2.0);
no replacement viewer command exists in the inspected v0.6.0 registry.

The v0.6.0 registry also has no `gym eval export` command. Newer live
[ATIF export documentation](https://docs.nvidia.com/nemo/gym/reference/trajectory-capabilities/#atif-export)
must not be presented as a command available at this pin.

## What improved handoff and logging can solve

Use the README as the entry point for case purpose, input, grading, source
evidence, and run evidence. Include the exact artifact roots and viewer commands
verified against the installed version. For Harbor 0.20.0, the command shapes are:

```bash
# Run from the evaluated repository; substitute its actual artifact paths.
harbor view .eval-author/task-drafts --tasks --port 8080
harbor view .eval-author/jobs --jobs --port 8081
```

For a trace-derived task at `<workspace>/task/`, use `<workspace>` as the task
parent and the directory containing that workspace's jobs for the run view.
Keep each viewer command running while using its local URL. Reading existing
files does not require starting an evaluation.

Log and link each retained attempt, including failures, and distinguish source
traces, task controls, and actual-agent results. Configuration alone cannot
establish execution: Harbor can display a config-only trial and derive its
detail timestamp from the config file's modification time. A missing trajectory
also does not establish whether capture was unsupported, failed, or never run.
Report only the status supported by retained evidence.

## Acceptance criteria assessment

| ASE-1029 criterion | Assessment |
| --- | --- |
| Cases and associated traces are discoverable without a follow-up question | The README handoff can address this. Its behavior still needs authoring-workflow validation; native viewers do not create the handoff automatically. |
| Cases and traces are readable without manually formatting JSONL | Harbor supports its native artifacts. The bundled reader presents selected Gym records and ATIF, MLflow, or Gym traces, with missing, malformed, and unsupported evidence identified. |
| Review uses a supported workflow without building a custom UI | Harbor has a reusable native workflow. The bundled reader supplies a reusable offline HTML report for the formats its viewer does not accept; users do not need to build a UI. |
| Logging sufficiency and any viewer scope are documented | Logging and links address discovery and context. The bounded reader below addresses the formats that still needed presentation. |

## Implemented local reader

The inspected native tools do not provide the remaining views. The bundled
`render_review.py` addresses them with a static local HTML report, using Python's
standard library and the browser's existing links and expandable details:

- It reads explicitly selected Gym dataset rows and retained rollout records,
  keeping the request and grading fields distinct, and ATIF, MLflow, Gym, or
  normalized trace files loaded through the pinned NeMo Compass trace loader.
- It presents recorded messages, tool calls and their results, final responses,
  and available rewards/errors. It preserves loader uncertainty, missing
  capture, unsupported content, and malformed-record errors explicitly.
- It requires a caller-supplied evidence role, links the selected original,
  retains raw records, and never follows embedded paths. A role label does not
  verify provenance or identify an attempt's corresponding generated case.
- It works locally without model calls or uploads and creates private outputs.
  The report contains source evidence and remains subject to the existing
  workspace and publication boundaries.

The [usage guide](../skills/eval-author/references/local-review.md) defines
selection, limits, output permissions, error behavior, and the README handoff.
Trace rendering shows Compass spans depth-first with tool results and their
error flags, without claiming provider schema validation. Images and external
references are not loaded. Native Gym observability attachments do not become
canonical ATIF merely because their raw data appears in the report.

Reliable case-to-attempt association remains separate work: use recorded provider
identities and retained input versions, and leave ambiguous associations
unresolved. Rendering cannot infer this relationship from row order or similar
prompts. Editing cases, recording approvals, deploying a shared service, and
upgrading Gym are outside this minimum scope.

The [reader regression tests](../tests/test_render_review.py) exercise selected
physical lines, unexecuted cases, retained errors and rewards, missing/malformed
records, unsupported content, conversion caveats, private outputs, and bounded
rendering. They also run a copied script without site packages and check that
recorded HTML and embedded paths remain inert.

Chromium 145 inspection of synthetic reports confirmed readable cases, separate
attempts and setup errors, ATIF tool calls and observations, row navigation,
expandable raw evidence, and desktop/mobile wrapping. These reports made no
network requests and executed no embedded payloads. The native Harbor checks
above cover nested task roots and its missing/malformed trajectory states.
Authoring-workflow validation of automatic README handoff and reliable
case-to-attempt associations remains separate from these reader checks.

[gym-cli]: https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/nemo_gym/cli/main.py#L972
[gym-render]: https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/fern/versions/v0.6.0/pages/reference/cli-commands.mdx#L363
[gym-profile]: https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/fern/versions/v0.6.0/pages/reference/cli-commands.mdx#L752
[gym-health]: https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/fern/versions/v0.6.0/pages/evaluation/rollout-health.mdx#L55
[gym-blade]: https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/fern/versions/v0.6.0/pages/evaluation/diagnose-results.mdx#L7
