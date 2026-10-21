<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Experiment stages

This draft starts with the prepared inputs defined in the
[architecture](architecture.md#supplied-inputs). Agent bundling, mutant generation
and qualification, subset construction, and benchmark construction and publication are release
preparation rather than stages of a benchmark run. The system under evaluation
is a separate input. Coverage computation, including rubric generation when the
system uses it, is part of the experiment. The [three-stage overview](architecture.md#experiment-flow)
shows preparation of case examples as stage 1. Run mutants x eval tasks is stage 2;
score analysis is stage 3, with its individual components numbered 3.1–3.3.

Each stage has explicit inputs and outputs. Execution produces a results
directory through one of its supported implementations. The Python analysis
runner consumes that directory and calls its operations in a fixed order. An
agent or a person can invoke the runner, but neither supplies missing control
flow or decides whether a stage has completed.

## Stage contract

The benchmark's YAML definition selects the inputs, stage settings, and execution
implementation. Each executable stage provides a Python API and a command-line
tool. The orchestrator validates that definition and invokes the APIs in
dependency order. Prep produces the definition and its input artifacts when
building the benchmark; experiment runs consume them.

Each stage has declared inputs, fixed parameters, outputs, and completion
criteria. Outputs become available downstream only after validation.
The program controls transitions and stops on a stage error. Scoring accepts
only the configured execution statuses, so an unsuccessful execution's numeric
reward is not automatically treated as a valid observation.

On a repeated invocation, the analysis pipeline recomputes inexpensive
deterministic stages and reuses completed coverage requests after verifying
their inputs and saved files. Changed inputs cause an error in the existing
output directory. A coverage request without a completion receipt runs again;
the adapter owns any external side effects from an interrupted request.

The implementation stack provides standalone Python tools for analysis
components 3.1–3.3; see its [tool guide](https://github.com/NVIDIA-NeMo/eval-author/blob/feat/metric-benchmark-record/labs/metric-benchmark/README.md). Stage 2 defines the execution boundary. The current
tools consume the fixtures v1 saved-results index and native evidence, and
never rerun benchmark agents.

| Component | Command in the implementation stack |
| --- | --- |
| Prep: subset construction | `construct_subsets.py` |
| 3.1. Mutant scoring | `compute_mutant_scores.py` |
| 3.2. Coverage | `compute_coverage.py` |
| 3.3. Correlation | `correlate.py` |
| Analysis runner (manifest input pending) | `run_analysis.py` |

## 1. Prep

Prep prepares case examples for the experiment. The
[prepared-input contract](architecture.md#supplied-inputs) describes
the baseline, mutants, case description, tasks with their subset manifest, and configuration supplied to
the experiment. The experiment does not execute this preparation process.

### Subset construction

**Purpose:** prepare fixed evaluation subsets shared by every run and metric tool.

**Inputs:** the pinned benchmark task inventory and fixed subset count, size,
and seed. Execution results and coverage scores are not inputs to prep.

**Operation:** the sampler sorts task identities and applies its versioned,
seeded algorithm to select distinct subsets of the configured size. The seed is
fixed for the benchmark version and recorded as construction provenance.
Subsets are frozen before inspecting outcomes or coverage scores.

**Output:** a versioned `subsets.json` manifest with the source benchmark identity,
task inventory and revisions, stable subset IDs, explicit membership, algorithm,
count, size, and seed. The manifest's memberships are authoritative; analysis
reads them rather than regenerating them from the seed. Changing the inventory,
seed, or memberships creates a new benchmark version.

**Completion:** all subset IDs and memberships are unique, every member belongs
to the pinned inventory, and the requested count and size are satisfied.
Impossible requests fail. Analysis validates the manifest against the execution
inventory; missing observations never cause it to alter the prepared subsets.

**Current implementation:** the existing `eval_subset_definitions` artifact
contains the memberships and sampling parameters. The Python sampler accepts a
task inventory directly, but its CLI currently obtains that inventory from saved
results. A prep CLI accepting the inventory without results is still needed.
The combined `run_analysis.py` also still generates subsets; it needs to accept
and pin a supplied manifest instead. Standalone mutant scoring and coverage
already accept `--subsets`. The current artifact includes an `experiment_id`;
its binding must be separated from benchmark identity to reuse one unchanged
manifest across independently identified runs.

## 2. Run mutants x eval tasks

**Purpose:** execute the benchmark tasks against the baseline and supplied
mutants and produce the results directory consumed by analysis.

**Inputs:** prepared agent bundles, benchmark tasks, and experiment execution
settings.

**Implementations:** the initial local implementation uses a simple deterministic
loop: baseline first, then the configured mutants, with one Harbor job per agent
covering the full benchmark. Agent jobs run sequentially. Task concurrency within
a Harbor job is an explicit execution setting; a fully sequential run uses one
concurrent trial. The initial analysis supports one trial per agent/task within
the selected run; repetition aggregation remains future work.

This phase is expected to be expensive: with `M` mutants and `T` tasks, even a
single execution per pair requires `(M + 1) × T` task executions before retries.
The baseline is included because mutant scoring compares its outcomes with each
mutant's outcomes. The repository currently supplies the analysis tools; the
local Harbor loop is the first execution implementation to add here.

A future Helix implementation will use an orchestrator to run evaluations at
scale. Each implementation owns invocation, scheduling, retries, and collection
and produces the same output contract. Analysis can rerun against saved results
without repeating this execution phase.

### Results-directory contract

**Proposed layout:** agent first, then benchmark run, with native trial outputs
inside each run. One results root represents one case and benchmark revision.

```text
execution-results/
  results.json                         # Shared analysis index and task inventory
  agents/
    baseline/
      run-0001/                        # One intact Harbor job directory
        config.json
        lock.json
        result.json                    # Harbor's job-level result
        job.log
        task-a__<trial-suffix>/
          config.json
          lock.json
          result.json                  # Harbor's per-trial result
          agent/                       # Agent execution evidence
          verifier/                    # Verifier outputs
          ...                          # Other native Harbor artifacts
        task-b__<trial-suffix>/
          ...
    mutant-001/
      run-0001/
        ...                            # Same benchmark, this mutant's results
    mutant-002/
      run-0001/
        ...
```

The native portion shown is illustrative of Harbor's single-step trial layout;
its actual files depend on the pinned Harbor version and task type. The local
implementation chooses the agent/run parent directory and preserves the native
job contents, including native trial names. Explicit run names avoid using a
timestamp as the benchmark's logical run identity.

Agent-first matches the proposed execution loop and keeps each Harbor job's
configuration, aggregate results, and task evidence together. A task-first tree
would split that job across multiple directories. A second physical copy is
unnecessary: the shared index supports either agent-wise or task-wise views.
Separate run directories allow later executions without overwriting earlier
ones; the current scorer selects one run per agent.

**Index:** `results.json` uses the existing v1 `raw_execution_results` schema.
It includes the complete benchmark/task inventory and each execution's agent,
run, task revision, attempt, status, measurements, and native-evidence location.
This shared index is distinct from Harbor's own job and trial `result.json`
files. All evidence paths are relative to `execution-results/`. For example:

```json
{
  "native_results_path": "agents/mutant-001/run-0001/task-a__x7k2p9m",
  "native_results_format": "directory"
}
```

This is an excerpt of an execution entry, not a complete results index. It points
to one trial, not the whole job directory. Folder labels are filesystem-safe
aliases; the index carries the actual agent and task identities. A run identity
is unique across the results index even when agents reuse the folder label
`run-0001`. Native trial names can contain generated suffixes, and analysis never
parses those names to recover task identities, repetitions, or retry order.

The current reader already supports these relative paths and native directories
or archives. Existing commands accept explicit index and evidence-root paths;
the new producers will adopt this layout, but the reader does not yet enforce
these folder names. The local Harbor implementation still needs the code that
builds the shared index from native job/trial metadata.

The Helix implementation will export the same agent/run layout and index with
task-level evidence in a supported format. Its scheduling and storage can differ
internally. Analysis reads the common index and evidence without a Helix-specific
service API. An archive can replace a trial directory when the index explicitly
declares its path and format; the native evidence contents still need to be
readable by the coverage implementation.

**Completion contract:** the output accounts for the configured agent-by-task
matrix and identifies failed attempts and unavailable measurements explicitly.
A completed task with a zero reward remains distinct from an execution error.
The output index and referenced evidence agree on identities and formats.
A full execution-completeness validator is still to be implemented; the existing
analysis tools validate the index and handle unavailable observations according
to their scoring policy.

The shared contract covers directory contents as well as paths: measurements
have consistent meanings, and the evidence formats are readable by the coverage
implementation. A Helix implementation will produce that same contract rather
than making analysis interpret Helix-specific result metadata.

## 3. Score analysis

### 3.1. Compute Mutant Scores

**Purpose:** measure each subset's sensitivity to the supplied mutations.

**Inputs:** the prepared subset manifest, Raw Execution Results for the baseline and
mutants, the fixed mutant cohort, and the scoring policy.

**Operation:** the scoring function selects the configured runs and attempts,
pairs baseline and mutant outcomes by task, and computes one score per subset
for the configured measurement. It rejects ambiguous repetitions. The metric
name and higher-is-better or lower-is-better direction are configuration fields.

For reward, the implemented score is mean signed reward loss:

```text
S(agent, subset) = mean task reward for that agent on that subset
M(subset) = mean over selected mutants of
            [S(baseline, subset) - S(mutant, subset)]
```

The scorer uses this equally weighted signed score and preserves
negative differences when a mutant outperforms the baseline. Missing required
pairs make a subset unavailable; no tasks or mutants are silently dropped.
One run per agent and an explicit attempt-selection rule are required.
This analysis does not pool repetitions or apply alternative weighting.

**Outputs:** Mutant Scores, keyed by subset ID, with the case, baseline, mutant
cohort, aggregation, and scoring variation identified. The output also contains
the individual mutant effects used in the aggregate.

**Completion:** every requested subset has a score or an explicit unavailable
value and reason. Missing required pairs produce unavailable scores; the
function does not remove tasks or mutants to obtain a numeric result.

### 3.2. Compute Coverage

**Purpose:** obtain the system under evaluation's coverage scores for the same
subsets.

**Inputs:** the case description, the prepared subset manifest, permitted baseline
evidence from Raw Execution Results, and the configured system under evaluation
with its tool-specific adapter and settings. The current request supplies task
identities and native baseline evidence for the subset; additional source or
task-content interfaces are not implemented.

**Operation:** the coverage stage copies the subset's selected baseline evidence
into a work directory, writes a JSON request, and invokes the configured adapter
command. The adapter translates the request into its system's native inputs,
executes that system through its native interface, validates its outputs, and
converts the result to the common response. The coverage stage validates the
adapter's JSON response as a numeric score or an unavailable value with a reason.
Missing baseline evidence, invalid responses,
input modification, timeouts, and nonzero exits fail the stage.

The system may generate a rubric as part of its method. Its adapter places the
supporting outputs in the work directory. The runner does not infer which
rubrics or judgments the tool should have produced; required adapter-specific
outputs need an adapter-specific validator. The current implementation provides
the common invocation protocol and a synthetic adapter; it does not yet supply
production adapters for Eval Author or other systems.

Each subset is evaluated independently. The runner has no shared-rubric
generation pass or cross-subset measurement cache. Those methods would require
explicit additional interfaces and validation. Mutant outcomes, mutant scores,
and correlation results are absent from the coverage request. Staging inputs
does not sandbox the tool's access to the host.

**Outputs:** Coverage Scores, keyed by subset ID, with system and configuration
identities, plus the saved requests, responses, and files produced in each work
directory. The stage writes a completion receipt with file digests for each
successful request.

**Completion:** every subset has a validated response and completion receipt.
The stage writes the aggregate Coverage Scores artifact only after all subset
requests complete. If the tool generates rubrics or judgments with a model,
those files are observations. The [benchmark orchestration](architecture.md#benchmark-orchestration)
is deterministic; fresh model outputs are not assumed identical.

### 3.3. Correlation

**Purpose:** determine whether coverage predicts mutation sensitivity across
evaluation subsets.

**Inputs:** Coverage Scores and Mutant Scores with matching subset definitions.

**Operation:** the correlation function verifies matching source identities and
subset IDs, joins scores by subset ID, and computes Spearman rank correlation
using average ranks for ties. It rejects unavailable pairs by default. With the
explicit exclusion policy, it omits those pairs and writes their reasons into
the result alongside the included pairs.

**Outputs:** Correlation Results containing the coefficient, included pairs,
configuration identities, and exclusions. The included count is the length of
the pair list.

**Completion:** each comparison has a result or an explicit undefined result.
Constant scores or too few usable pairs do not become zero correlation.
Unexpected identity or membership mismatches are errors, not silently dropped
observations. The missing-value policy is an explicit function input.

Overlapping subsets share observations. Treating them as independent samples
would overstate the strength of evidence. The reported coefficient is
descriptive; the analysis does not report independent-sample significance or
claim general validity from that coefficient alone.

Mutant scoring and coverage measurement depend on the subset definitions, and
their results meet at correlation. The current runner executes them sequentially
in the order shown above. None of the analysis stages reruns benchmark agents.
