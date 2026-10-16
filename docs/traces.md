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

## Convert local traces

ATIF (Agent Trajectory Interchange Format) records an agent's interaction in a
structured trajectory. Convert an exported MLflow trace before using it in an
ATIF coverage audit or deriving a task:

```text
Use Eval Author to convert /absolute/path/mlflow-export.json to ATIF.
The agent name is support-agent and its version is 1.0.
Write the converted traces to a new private directory at
/absolute/path/private-atif and report any conversion losses.
```

The [MLflow conversion skill](../skills/mlflow-to-atif/SKILL.md) accepts a
`Trace.to_dict()` export, an array of exports, or a `{"traces": [...]}` object.
It writes one owner-private `.atif.json` file per trace. Conversion preserves
recorded evidence and reports losses; it cannot recover missing instructions
or results.

For Gym traces, use the standalone
[Gym conversion skill](../skills/gym-to-atif/SKILL.md) with one
rollout JSON record or one explicit JSONL line. Prefer the original Harbor ATIF
when retained. The experimental trace-to-environment
[source guide](../skills/eval-author-trace-environment/references/sources.md)
describes the supported ATIF, Gym, MLflow, Intake, and bounded JSON
OpenTelemetry inputs and their limits.

## Load shared normalized evidence

The Gym and MLflow skills can also use NeMo Compass's pinned `insight-agent`
distribution to retain normalized provider evidence with its bundled
`trace_ingest` loaders. See the
[Gym loader guide](../skills/gym-to-atif/references/trace-intel-ingest.md) for
native `ng_trajectory` captures and the
[MLflow loader guide](../skills/mlflow-to-atif/references/trace-intel-ingest.md)
for complete bounded exports. These optional commands need Python 3.12 or 3.13
and the documented dependencies; loading makes no model calls or live queries.

Their output is a NeMo Compass model, not an ATIF trajectory. Keep using the
ATIF converters for coverage measurement or experimental environment derivation.
A capture that loads successfully can still lack sufficient interaction evidence
for either workflow.

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

The source can be ATIF, Gym, MLflow, Intake, or supported OpenTelemetry data;
source format does not select the output provider. No audit coverage report is
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
