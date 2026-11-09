<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helix benchmark architecture

This design separates **publishing resources**, **running an agent against a benchmark**,
and **recovering results and sources**. Four tools expose these operations. This PR
contains documentation and examples only: the tool names and commands below are the
proposed interface to implement downstream, not commands available in this base.

## Resource flow

![Helix resource flow showing YAML definitions, four proposed tools, registered resources and standard results.](assets/helix-resource-flow.svg)

[Open the resource-flow SVG](assets/helix-resource-flow.svg).

The diagram groups the workflow into definitions, publication, execution and collection.
Yellow boxes are YAML definitions, blue boxes are tools, and green boxes name output
files. The hierarchy shows `experiment.yml` referencing cases, execution and analysis
settings, with agent and task manifests beneath `cases.yml`. `publication.yml` supplies
Helix publication settings separately. Output filenames match the command examples;
run receipt names and output directories are caller-selected.

Repeat execution for the baseline and each selected mutant. Each invocation runs
one agent against every task in the selected benchmark. Collection combines those
runs into the experiment's existing results layout. Source recovery is needed only
when a consumer requires files that are not already available and verified locally.

## Four tools

| Proposed tool | Purpose and inputs | Output |
| --- | --- | --- |
| `helix_publish.py` | Upload agents, tasks, or both from signed prepared inputs; optionally verify and reuse existing resources. | `references.json`, mapping case/agent/task identities to exact registered references, plus a pinned taskset for each published benchmark. |
| `helix_execute.py` | Run one selected registered agent against one pinned benchmark taskset, using execution settings and context. | `runs/baseline.json` or `runs/mutant.json`: run/job references, selected experiment identities and resolved resource versions. |
| `helix_collect.py` | Download runs, verify their inputs and trial evidence, and import them into the standard benchmark format. Recover required sources when absent. | `execution.json`, per-case `results.json`, and per-trial `result.json` plus native files in the existing results layout, ready for validation and analysis. |
| `helix_sources.py` | Download the exact agent bundles and/or task sources named by publication references or a run receipt. | Verified local sources in the prepared input layout. Optional standalone access to the same recovery operation used by collection. |

Preparation of the experiment remains outside these tools. Collection and import
are one user-facing operation; they may use separate internal helpers. Publication
receipts and local caches can aid inspection, but later execution and recovery must
not depend on retaining the publisher's working directory.

## Decisions

1. **Agents are independent resources.** Register each baseline and mutant from its
   own signed bundle, or verify and reuse an existing registered version. Upload
   associated files through the agent's FileSet. Do not copy agent bundles into task images.
2. **Tasks stay unchanged.** Upload prepared task directories without editing their
   Dockerfiles. Existing pinned task revisions may be reused only after verification
   against local prepared task sources.
3. **Publication only makes resources available.** It accepts agents only, tasks only,
   or both, and returns references. Grouping tasks into a pinned taskset defines the
   published benchmark. Publication does not select compute, build task images or
   submit evaluation runs; execution arranges any required image builds.
4. **The signed experiment remains authoritative.** It selects cases, baseline,
   mutants and tasks. Signed agent definitions and manifests own behavior, model,
   files and runtime options. Publication and execution cannot substitute another
   agent or change experiment membership.
5. **Execution takes references.** One invocation selects one agent and one benchmark
   taskset, with exact registered IDs and revisions/content hashes. It does not require
   the original local bundles. Agent changes never require rebuilding task images.
6. **Recovery is part of the contract.** Published resources must retain the complete
   agent definition and bundled files, and complete task sources. Downloaded bytes
   must match their recorded hashes before downstream code treats them as local inputs.
   Missing or changed sources are errors, not permission to use the latest version.
7. **Names alone are not immutable evidence.** Record resolved agent configuration,
   file hashes and task revisions for each run. Exact recovery requires retained,
   immutable source snapshots. Helix download support alone does not establish historical
   retention of agent FileSets: downstream implementation must verify that guarantee
   or store a recoverable immutable snapshot in Helix before claiming it. A hash
   detects changes but cannot recover overwritten bytes.
8. **The results contract stays the same.** Preserve experiment identities, selected
   agent/task membership, trials, attempts, statuses and native evidence. Collection
   produces the existing [results format](results-format.md) for unchanged validation,
   scoring and analysis. Failures remain visible; they are not silently omitted.
9. **Registered-agent support is initially Helix-only.** Downloading a bundle does not
   make it locally executable. The local executor must reject unsupported registered
   agents explicitly. Native Harbor configurations and Oracle/NOP controls remain
   supported through their existing path.

Running the baseline and mutants means multiple execution invocations. Repeating
an agent/benchmark pair is a separate choice: keep distinct run identities and
explicit analysis selection. Do not silently aggregate repetitions or change the
existing [trial and scoring rules](stages.md#1-run-mutants-x-eval-tasks).

## Configuration ownership

| Input | Owns |
| --- | --- |
| Experiment, cases and task manifests | Selection, case membership, task IDs and signed source identities; references to analysis settings. |
| Agent manifest and bundle | Baseline/mutant IDs, agent definition, models, instructions, files and runner options. |
| Helix context | Saved server connection, authentication and default workspace. |
| Publication settings | Resource destination, secret references and optional existing pinned resource references. No model, runtime or compute overrides. |
| Execution settings | Dispatcher, concurrency and sandbox resources, with an optional destination assertion matching published references. |
| Publication references and run receipts | Exact remote IDs, revisions, hashes and their mapping to experiment identities; resolved inputs used by each run. |

Actual credentials stay in the Helix context or secret store. Recovery covers
published inputs, not secret values, Git history or a promise of identical model
outputs. Keep the signed experiment metadata and analysis inputs alongside the
references; these tools do not regenerate experiment selection or analysis settings.

## Example workflow

The following commands define the intended interface. Run them from
`labs/metric-benchmark` once implemented. `prepared/cases.yml` represents an already
prepared, signed experiment with working bundles and tasks. Context `my-helix` supplies
connection/authentication; example case and agent IDs are `example`, `baseline` and `mutant`.

### 1. Upload resources and save references

```bash
python helix_publish.py --cases prepared/cases.yml \
  --resources agents,tasks --publication examples/helix/publication.yml \
  --context my-helix --output references.json
```

`--resources agents` uploads only agents; `--resources tasks` uploads only tasks.
They can also be published independently and their mappings combined:

```bash
python helix_publish.py --cases prepared/cases.yml \
  --resources agents --publication examples/helix/publication.yml \
  --context my-helix --output agent-references.json
python helix_publish.py --cases prepared/cases.yml \
  --resources tasks --publication examples/helix/publication-existing-task.yml \
  --context my-helix --references agent-references.json --output references.json
```

The second call preserves agent references and adds task/taskset references; conflicting
identities are errors. Resources outside the selected scope need not exist locally.
The [existing-task settings](../examples/helix/publication-existing-task.yml) illustrate
verified reuse; omitted task IDs are uploaded. The output records remote-issued IDs
and exact versions, not invented IDs or mutable names alone.

### 2. Run one agent against all benchmark tasks

```bash
python helix_execute.py --references references.json --case example --agent baseline \
  --execution examples/helix/execution.yml --context my-helix \
  --output runs/baseline.json
python helix_execute.py --references references.json --case example --agent mutant \
  --execution examples/helix/execution.yml --context my-helix \
  --output runs/mutant.json
```

`--case` and `--agent` select exact remote references from the publication mapping;
execution does not look for local agent or task directories. A taskset is the pinned
list of tasks constituting this case's benchmark. Each run receipt retains enough
resource identity and source-location information to recover what ran independently
of later changes to the publication mapping.

### 3. Collect and format results

```bash
python helix_collect.py --references references.json \
  --run runs/baseline.json --run runs/mutant.json \
  --context my-helix --sources-output recovered \
  --output results/experiment-001
```

Collection verifies the full selected agent-by-task matrix and reports incomplete
runs or missing evidence explicitly. It downloads required sources to `recovered`
when needed and writes the standard `execution.json`, case `results.json` indexes,
and agent/run/trial directories described in the [results format](results-format.md).
Existing validation and analysis consume those outputs and the retained experiment
metadata. No separate user-facing import command is required.

### 4. Recover sources independently, when useful

```bash
python helix_sources.py --run runs/baseline.json \
  --resources agents,tasks --context my-helix --output recovered-baseline
```

Use `--references references.json` instead of `--run` to recover published inputs
before execution. `--resources agents` or `--resources tasks` limits recovery.
A run receipt selects the versions actually used. Recovery preserves the declared
relative paths and verifies identities and hashes; it rejects conflicting local files.

### Configuration templates

The [example directory](../examples/helix) supplies:

- [Agent manifest](../examples/helix/agents.yml): separate registered baseline and mutant bundles.
- [Agent definition](../examples/helix/agent.yaml): a minimal managed agent configuration.
- [Execution](../examples/helix/execution.yml): compute settings.
- [Publication](../examples/helix/publication.yml): per-agent secret bindings.
- [Existing task](../examples/helix/publication-existing-task.yml): a pinned task revision to verify and reuse.

Supply working bundles and task sources, choose a model available in the destination
workspace, then sign the experiment. These templates are not a runnable benchmark.

## Runtime boundary

Helix currently implements registered Harbor agents through its Fabric adapter.
That is a worker implementation detail: benchmark users register an agent bundle and
select tasks. They do not configure bundle mounts or alter task Dockerfiles.

The example gives DeepAgents file tools the task container’s filesystem rooted
at `/`, so absolute paths in task instructions keep their meaning. This is the
container filesystem, not the machine submitting the run.

Supporting separate resources does not make every task/agent pairing valid. Worker
dependencies, task setup permissions, network access and model availability still
need to support the chosen agent. Fail unsupported combinations explicitly and
retain setup failures as execution evidence.

## Stack

1. #185: architecture and examples (this base).
2. #173: publication and exact resource references, including the source recovery contract.
3. #162: execution and collection using those references.
4. #174: verification and standard result formatting behind the collection tool.

These PR boundaries organize implementation; they do not require separate user-facing
commands for collection and import. Downstream implementations must be reconciled
with this proposed interface before claiming the workflow is available.
