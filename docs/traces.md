<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Work with traces

Use a recorded interaction to understand what happened, prepare evidence for a
coverage audit, or build an evaluation task. First follow
[Getting started](getting-started.md) to load Eval Author. Check the
[requirements](getting-started.md#requirements) for your workflow, including
platform access, execution dependencies, and model provider setup.

The trace-to-environment workflow is **experimental**, including task creation,
validation, and publication/export steps.

## Inspect an Intake trace

Provide a trace ID and an explicit NeMo Helix workspace:

```text
Use Eval Author to explain Intake trace TRACE_ID in workspace WORKSPACE.
Identify the outcome, important moments, issues, recoveries, and uncertainty.
Save the report locally.
```

The agent reads the selected trace and relevant evaluator evidence, then writes
`.eval-author/traces/intake-TRACE_ID.md`. Each important finding cites its
supporting evidence. An outcome can be `success`, `failure`, or `unknown`;
an error span alone does not establish that the overall task failed.

This workflow requires an existing, working `nemo` CLI and read access to Intake.
It does not set up the platform or ingest new traces. See the
[Intake inspection skill](../skills/eval-author-inspect-trace/SKILL.md).
Keep the report uncommitted and treat its contents as private trace data.

## Load local traces

Eval Author loads provider traces through one shared module,
`skills/eval-author/scripts/trace_loading.py`, backed by NeMo Compass's pinned
`nemo-compass` distribution. It normalizes ATIF (Agent Trajectory Interchange
Format), MLflow, Gym, and Intake evidence into Compass `Trace` objects; that
`Trace` is Eval Author's only trace interface, and Eval Author never converts
traces into ATIF. See the
[trace loading guide](../skills/eval-author/references/trace-loading.md).
Loading needs Python 3.12 or 3.13 and the skill's `requirements.txt`; it makes
no model calls or live queries.

Coverage audits measure ATIF, MLflow, Gym, and normalized traces directly. The
experimental trace-to-environment workflow's `prepare` step reads ATIF only
until it reads Compass Traces; its
[source guide](../skills/eval-author-trace-environment/references/sources.md)
describes this temporary limit. A capture that loads successfully can still
lack sufficient interaction evidence for coverage measurement or experimental
environment derivation.

## Build an evaluation task

**Experimental:** This workflow uses the trace-to-environment skill.

```text
Use Eval Author to derive a private Harbor task from
/absolute/path/trace.atif.json. Use the task ID support-case.
Review the trace for private data, explain what ground truth it supports,
and validate the candidate when the required runtime is available.
```

The [trace environment skill](../skills/eval-author-trace-environment/SKILL.md)
creates one private, gitignored workspace per task under
`.eval-author/trace-environments/`. It reviews trace evidence, builds a candidate,
and records validation results. A trace can yield `no_candidate` when it lacks
usable evidence, or a candidate whose environment remains unproven. A recorded
successful answer alone is insufficient ground truth.

Harbor's `nop` baseline and `oracle` reference solution check whether the task
is solvable and its verifier distinguishes outcomes. These checks do not
establish your actual agent's performance. That requires running your agent
with its configured provider and dependencies. See [Understanding results](results.md).

### Produce a Gym task

Choose Gym explicitly or use a repository whose existing suite uses Gym:

```text
Use eval-author-trace-environment to produce a native Gym task from
/absolute/path/trace.atif.json, with task ID ledger-total.
Review privacy and ground truth, complete the native task, and validate it
with the installed Gym runtime. Retain controls and exact rerun instructions.
```

The source must be ATIF, including a Gym run's retained Harbor ATIF, until the
workflow reads NeMo Compass Traces; source format does not select the output
provider. No audit coverage report is
required. The [Gym extension](../skills/eval-author-trace-environment/references/gym-output.md)
shares trace preparation and candidacy, then reuses Gym's native scaffolder,
resources server, datasets, verifier fixtures, and repeated execution controls.
It requires the sibling `eval-author-task-create` skill and an existing Gym
v0.6.0+ runtime in Python 3.13.14+.

The native task lives under `<task-dir>/gym/`; `gym-report.md` records validation
and remaining blockers. The shared `summary.json` remains a pending preparation
record. Gym controls do not measure model performance. Harbor's automated
readiness, batch reporting, and publication/export do not cover Gym products.

## Sharing generated tasks

**Experimental:** Publication review and export currently cover Harbor products.
Native Gym products from this extension remain private.

Keep source traces, intermediate files, and validation jobs private. Before
sharing a generated Harbor product, complete the separate
[publication review](../skills/eval-author-trace-environment/references/publication-review.md)
of the exact files to export. The export command includes only the reviewed
task product; the earlier trace review does not authorize publication.
Any change to that product requires a new review before export.
