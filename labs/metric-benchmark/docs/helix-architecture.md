<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helix benchmark architecture

The workflow has four tools: **upload agents and tasks**, **run an agent against a
benchmark**, **download results**, and **download sources**. This PR defines how those
tools should work. It contains only documentation and examples; later PRs will
implement the proposed commands.

## Resource flow

![Helix resource flow showing YAML definitions, four proposed tools, registered resources and standard results.](assets/helix-resource-flow.svg)

[Open the resource-flow SVG](assets/helix-resource-flow.svg).

Each execution command runs one agent against every task in the selected benchmark.
Collection downloads those runs into the experiment's standard results directory.
Sources only need downloading when a later step needs files that are not already
present and verified locally.

## Four tools

| Proposed tool | Purpose and inputs | Output |
| --- | --- | --- |
| `helix_publish.py` | Upload prepared agents, tasks, or both. Check existing resources before reusing them. | `references.json`: each case's agent IDs, content hashes and retained source locations, plus pinned task and taskset references. |
| `helix_execute.py` | Read run settings from `execution.yml` and agent/taskset IDs from `references.json`. Run one selected agent against one case's benchmark. | `runs/baseline.json` or `runs/mutant.json`: submission retry key, run/job IDs and the actual agent configuration, source hashes and pinned task references, linked to the case. |
| `helix_collect.py` | Download results, check what ran and whether results are complete, and write the standard format. Download missing sources when needed. | `execution.json`, a `results.json` for each case, and a `result.json` plus original output files for each trial, ready for validation and analysis. |
| `helix_sources.py` | Download the retained agent sources and/or pinned tasks listed in `references.json` or a run receipt. | Verified source files at their original relative paths. Collection uses the same download operation when needed. |

These tools consume an already prepared experiment. Collection downloads results
and writes the standard results format in one step. The intended contract is that
saved references suffice to run on Helix and download
published sources without the original upload directory. For agents, this requires
the retained remote copy described in decision 7; registration alone does not provide it.

`references.json` maps uploaded or reused agents, tasks and benchmark tasksets to
the Helix IDs, task revisions, agent content hashes and source locations needed for
execution and download. It retains the experiment's case, agent and task IDs so each remote resource can be matched to its
prepared input.

The saved references are used to execute the experiment, download its sources and
results, and verify that results account for every selected agent and task. Task
revisions identify pinned remote tasks. Agent IDs identify registrations;
hashes identify their expected contents, and retained source locations allow exact
downloads without local bundles. Execution records the references it used alongside
the run IDs,
so collected results remain linked to what ran.

## Decisions

1. **Register agents separately.** Each baseline and mutant comes from its own signed
   bundle. Register it and upload its files to the agent's FileSet (Helix file storage),
   or check and reuse an existing registration with matching contents. Derive new agent
   names from the signed definition and bundle contents, so changed content gets a
   different name. This avoids accidental replacement by our publisher; Helix still
   allows names to be overwritten. Never put agent bundles in task images.
2. **Keep tasks unchanged.** Upload the prepared task directories without editing their
   Dockerfiles. To reuse a task already in Helix, select an exact revision and check
   that it matches the local prepared source.
3. **Publication uploads or reuses resources.** Publish agents, tasks, or both and return
   their references. A *taskset* is the benchmark's list of tasks, each fixed to an exact
   version. Publication does not choose compute, build task images or start evaluations.
   Execution requests any required image builds and waits for them to succeed before
   submitting: Helix submission against a stored taskset rejects unready images.
4. **The prepared experiment decides what runs.** It selects the cases, baseline,
   mutants and tasks. Signed agent definitions and manifests specify behavior, model,
   files and runtime options. Publishing or running the experiment must not change
   those choices. Here, *signed* means the input files have recorded content hashes;
   see [input signing](benchmark-format.md#references-and-content-pins).
5. **Execution uses saved references.** Run one agent against one benchmark taskset,
   using agent IDs and content hashes plus pinned taskset references. Check that the
   agent configuration and files captured for the run match the published hashes;
   report a mismatch instead of silently running changed content. The original local
   bundles are not needed when the retained remote copies are available. Changing an
   agent never requires rebuilding task images.
6. **Published sources must be downloadable.** Keep the complete agent definition,
   bundled files and task sources. Check downloaded files against their recorded
   hashes before using them. If files are missing or have changed, report an error
   rather than downloading a newer version.
7. **Agent registration is not a source archive.** Helix agents have names, not pinned
   revisions. Re-registering an agent replaces its files, and the platform's temporary
   [run snapshots](https://github.com/NVIDIA-NeMo/nemo-platform/blob/76b48f43cd9626860341c0ae30bfd9803cdd4f3a/plugins/nemo-evals/src/nemo_evals/jobs/agent_files_snapshot.py)
   do not guarantee later downloads. Publication must retain a separate
   remote copy of the complete signed agent definition and bundled files, record its
   location and hashes, and reuse it without overwriting it. Run receipts keep those
   references along with the actual configuration and hashes used. The storage and
   retention mechanism still needs verification before implementation can promise
   exact agent downloads; it must survive run cleanup and agent re-registration.
   A hash detects changed files but cannot restore missing contents.
8. **Keep the same results format.** Preserve experiment IDs, selected agents and tasks,
   trials, attempts, statuses and original run evidence. Collection writes the existing
   [results format](results-format.md), so validation, scoring and analysis stay the
   same. Failed runs remain in the results.
9. **Registered agents initially run only on Helix.** Downloading an agent does not
   make it runnable locally. Local execution must report an error for unsupported
   registered agents. Existing native Harbor configurations and Oracle/NOP controls
   continue to work through their current path.

Run the execution command once for the baseline and once for each selected mutant.
To repeat the same agent against the same benchmark, save a separate run and choose
explicitly which run analysis should use. Do not automatically combine repeated runs
or change the existing [trial and scoring rules](stages.md#1-run-mutants-x-eval-tasks).

Execution generates or accepts an idempotency key: a submission retry key that prevents
starting a duplicate run after an interrupted request. Save the key and the selected
request in the run receipt before submitting, then add the returned run IDs. Retrying
that submission reuses the saved key and request; an intentional new run uses a new
key. Do not overwrite an existing run receipt to start a different run.

## What each configuration controls

| Input | What it controls or records |
| --- | --- |
| Experiment, cases and task manifests (`experiment.yml`, `cases.yml`, `tasks.yml`) | Which cases, agents and tasks are selected; task IDs and signed source hashes; links to analysis settings. |
| Agent manifest and bundle (`agents.yml`, `agent.yaml` and bundled files) | Baseline/mutant IDs, agent definition, models, instructions, files and runner options. |
| Helix client settings (`helix-client.example.yml`, supplied with `--client`) | Server address, authentication source and default workspace, shared by all four tools. |
| Publication settings (`publication.yml` or `publication-existing-task.yml`) | Where to publish, which secrets to use, and any existing resources to verify and reuse. Cannot override models, runtime options or compute settings. |
| Execution settings (`execution.yml`) | Where jobs run (dispatcher), how many run at once, and sandbox resources. Can also check that the destination matches the published references. |
| Analysis settings (`analysis.yml`, referencing description and subset files) | Settings for analyzing results: case descriptions, fixed task subsets, coverage, scoring and missing-result handling. |
| Publication references and run receipts (`references.json`, `runs/baseline.json`, `runs/mutant.json`) | Agent IDs, source locations and hashes, plus pinned task/taskset references, linked to experiment IDs. A run receipt records the inputs actually used by that run. |

Client credentials are read from the configured environment variable; agent secrets
stay in the secret store. Source downloads retrieve published files; they do not include secret values or Git history, or guarantee that
rerunning a model produces the same answer. Keep the signed experiment files and
analysis inputs with the references. These tools do not recreate them.

## Example workflow

These examples show the proposed commands, to be implemented in later PRs. They run
from `labs/metric-benchmark` and use the YAML layout in `examples/helix/cases.yml`.
The referenced sources are not included; a real run requires supplying them,
configuring settings and signing the inputs. Every tool takes the same `--client`
file for its Helix server, authentication source and workspace. The example case is
`example`; its agents are `baseline` and `mutant`.

`--client` and the client YAML are proposed benchmark-tool interfaces. The implementation
must translate these settings into Helix SDK configuration. The example names an
environment variable for the access token; it contains no token value.

### 1. Upload resources and save references

```bash
python helix_publish.py --cases examples/helix/cases.yml \
  --resources agents,tasks --publication examples/helix/publication.yml \
  --client examples/helix/helix-client.example.yml --output references.json
```

`--resources agents` uploads only agents; `--resources tasks` uploads only tasks.
You can publish them separately and combine their references:

```bash
python helix_publish.py --cases examples/helix/cases.yml \
  --resources agents --publication examples/helix/publication.yml \
  --client examples/helix/helix-client.example.yml --output agent-references.json
python helix_publish.py --cases examples/helix/cases.yml \
  --resources tasks --publication examples/helix/publication-existing-task.yml \
  --client examples/helix/helix-client.example.yml --references agent-references.json --output references.json
```

The second command keeps the agent references and adds the task and taskset references.
It reports an error if the mappings conflict. Only the sources being published need
to be available locally. The [existing-task settings](../examples/helix/publication-existing-task.yml)
show how to reuse a verified task version; tasks not listed there are uploaded.
The output records registered agent IDs, source hashes and retained source locations,
plus task and taskset references pinned by digest.

### 2. Run one agent against all benchmark tasks

```bash
python helix_execute.py --references references.json --case example --agent baseline \
  --execution examples/helix/execution.yml --client examples/helix/helix-client.example.yml \
  --output runs/baseline.json
python helix_execute.py --references references.json --case example --agent mutant \
  --execution examples/helix/execution.yml --client examples/helix/helix-client.example.yml \
  --output runs/mutant.json
```

`--case` and `--agent` select the agent and benchmark taskset from `references.json`.
Execution uses the saved IDs and verifies the expected content without reading local
source directories. Each output JSON file is a *run receipt*: it records the run IDs,
the actual agent configuration and hashes, pinned tasks and where to download sources.
Later changes to `references.json`
do not change that record of what ran.

### 3. Collect and format results

```bash
python helix_collect.py --references references.json \
  --run runs/baseline.json --run runs/mutant.json \
  --client examples/helix/helix-client.example.yml --sources-output downloaded-sources \
  --output results/experiment-001
```

Collection checks that every selected agent has results for every task and reports
incomplete runs or missing evidence. It downloads any needed sources to `downloaded-sources`
and writes `execution.json`, each case's `results.json`, and the agent/run/trial
directories described in the [results format](results-format.md). Existing validation
and analysis use those files and the saved experiment definition. There is no separate
import command to run.

### 4. Download Helix sources

Download retained agent sources and pinned task sources using their recorded locations
and hashes.
Use a run receipt to download the versions that ran, or `references.json` to download
the published versions.

```bash
python helix_sources.py --run runs/baseline.json \
  --resources agents,tasks --client examples/helix/helix-client.example.yml --output downloaded-baseline
```

Use `--references references.json` instead of `--run` to download sources before execution. Use `--resources agents` or `--resources tasks` to download
only one kind. A run receipt selects the sources actually used by that run. Downloads
keep the original relative paths, check IDs and hashes, and report an error if existing
local files conflict.

### Configuration templates

The [example README](../examples/helix/README.md) explains the complete YAML layout,
how case/agent/task names map to Helix IDs, and what differs from local experiments.
Referenced agent bundles, task sources and analysis data are intentionally absent.

## Running agents on Helix

Users register an agent bundle and select tasks. Helix handles making the agent
files available; users do not configure mounts or edit task Dockerfiles. Task paths
must keep their meaning inside the task container.

An agent still needs compatible tasks and a suitable worker: its dependencies,
setup permissions, network access and model must be available. Unsupported
combinations must produce clear errors, and setup failures must be kept with the
run's evidence.
