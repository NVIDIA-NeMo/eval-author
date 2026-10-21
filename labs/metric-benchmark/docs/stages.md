<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Experiment stages

The experiment runs the four stages below using the supplied YAML configuration.
Each stage has a Python tool and an importable API; the orchestrator validates
inputs, calls the tools in dependency order, and passes their outputs onward.

**Prep:** case construction and subset preparation happen outside this repository.
Runs consume the [prepared inputs](architecture.md#supplied-inputs), including a
fixed subset manifest.

| Stage | Tool |
| --- | --- |
| 1. Run mutants x eval tasks | Planned local Harbor loop; future NeMo Helix implementation |
| 2. Compute mutant scores | `compute_mutant_scores.py` |
| 3. Compute coverage | `compute_coverage.py` |
| 4. Correlation | `correlate.py` |

The analysis tools are provided in the implementation PRs above this design.
Their [tool guide](https://github.com/NVIDIA-NeMo/eval-author/blob/feat/metric-benchmark-record/labs/metric-benchmark/docs/tools.md)
describes available commands and implementation status.

## Stage contract

Each stage has declared inputs, parameters, outputs, and completion criteria.
Outputs become available downstream only after validation. The runner stops on a
stage error. Scoring accepts only configured execution statuses, so a failed
execution's numeric reward is not automatically treated as a valid observation.

The analysis pipeline can reuse verified coverage results when their inputs and
saved files match. Changed inputs require a new output directory. Task-subset
membership stays fixed; analysis never resamples subsets or reruns benchmark tasks.

## 1. Run mutants x eval tasks

**Purpose:** execute the benchmark tasks against the baseline and supplied
mutants and produce the results directory consumed by analysis.

**Inputs:** prepared agent bundles, benchmark tasks, and experiment execution
settings.

**Implementations:** the first execution script will run Harbor tasks in a
basic local loop: baseline first, then the configured mutants, with one Harbor job per agent
covering the full benchmark. Agent jobs run sequentially. Task concurrency within
a Harbor job is an explicit execution setting; a fully sequential run uses one
concurrent trial. The initial analysis supports one trial per agent/task within
the selected run; repetition aggregation remains future work.

This phase is expected to be expensive: with `M` mutants and `T` tasks, even a
single execution per pair requires `(M + 1) × T` task executions before retries.
The baseline is included because mutant scoring compares its outcomes with each
mutant's outcomes. The repository currently supplies the analysis tools; the
local Harbor loop is the first execution implementation to add here.

A future NeMo Helix implementation will use an orchestrator to run evaluations at
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

The NeMo Helix implementation will export the same agent/run layout and index with
task-level evidence in a supported format. Its scheduling and storage can differ
internally. Analysis reads the common index and evidence without a NeMo Helix-specific
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
implementation. A NeMo Helix implementation will produce that same contract rather
than making analysis interpret NeMo Helix-specific result metadata.

## 2. Compute mutant scores

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

## 3. Compute coverage

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

## 4. Correlation

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
