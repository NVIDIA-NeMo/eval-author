<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Experiment stages

This draft starts with the prepared inputs defined in the
[architecture](architecture.md#supplied-inputs). Agent bundling, mutant generation
and qualification, and benchmark construction and publication are release
preparation rather than stages of a benchmark run. The system under evaluation
is a separate input. Coverage computation, including rubric generation when the
system uses it, is part of the experiment.

Before starting, validate the declared input identities, task inventory, baseline
and mutant relationships, case description, system version, and configuration.
Check that the chosen backend can invoke the bundles and retain the evidence
needed by the system under evaluation. Validation does not execute agents or
establish benchmark quality.

## Stage contract

Each stage has declared inputs, fixed parameters, outputs, and completion
criteria. Outputs become available downstream only after validation.
The program controls transitions and handles failures according to configured
rules. An execution error must remain distinct from a legitimate low reward.

Resume at completed stage boundaries. An incomplete stage can reconcile its
recorded attempts when invoked again, but must not blindly duplicate executions
or pick the most favorable attempt. Changing an input invalidates dependent
outputs; earlier observations remain in the record under their original identities.

The following descriptions specify behavior, not implemented APIs.

## Running Evals

### 1. Run Eval Matrix

**Purpose:** execute every task against the baseline and every supplied mutant
selected in the configuration.

**Inputs:** prepared agent bundles, benchmark tasks, and execution settings.

**Operation:** enumerate the agent-by-task matrix, including configured
repetitions. Execute it through the selected backend. Use the same task and
verifier versions and comparable execution settings for baseline and mutants;
record intentional differences that define a mutation. Apply a fixed retry
policy to execution failures without retrying legitimate agent failures to
improve their scores.

**Outputs:** an execution manifest identifying each requested pairing and its
attempts, status, and evidence locations. These are portable experiment
identities; backend-specific metadata can accompany them.

**Completion:** every requested pairing and repetition is accounted for, with no
pending or unresolved attempts. A completed execution can have a failing task
reward. Missing execution evidence is not a zero reward or a completed result.
The treatment of exhausted execution failures must be fixed in the run policy.

### 2. Collect Eval Results

**Purpose:** make execution evidence available in a consistent form for analysis.

**Inputs:** the execution manifest and the native outputs of the backend.

**Operation:** collect results and traces from their recorded locations. Preserve
native evidence and produce an index keyed by case, agent, task, repetition,
and attempt. Record correctness and any available resource measurements, their
units, and evidence locations. Verify the recorded task inventory against the
supplied benchmark.

**Outputs:** Raw Execution Results and Task References. The shared index locates
native evidence rather than replacing it.

**Completion:** all requested executions are represented, evidence can be read
and its identity checked, and measurements or traces absent from the source are
explicitly marked. Collection failures leave the stage incomplete. The collector
does not manufacture measurements or turn missing data into agent failures.

## Statistical Analysis

### 3. Eval Subset Construction

**Purpose:** construct simulated evaluation sets from the benchmark task inventory.

**Inputs:** Task References and the configured subset count, sizes, sampling
algorithm, seed, and constraints, or explicit supplied memberships.

**Operation:** select task identities without consulting outcomes, resource
measurements, or coverage scores. Preserve exact memberships so subsequent
analysis need not resample.

**Outputs:** Eval Subset Definitions with stable subset IDs, source benchmark
identity, membership, and construction parameters.

**Completion:** all memberships match the requested count, sizes, and constraints,
and every task belongs to the supplied benchmark. An impossible sampling request
fails explicitly rather than silently relaxing its constraints.

### 4. Compute Mutant Scores

**Purpose:** measure each subset's sensitivity to the supplied mutations.

**Inputs:** Eval Subset Definitions, Raw Execution Results for the baseline and
mutants, the fixed mutant cohort, and the scoring policy.

**Operation:** compare baseline and mutant outcomes on identical task memberships.
Compute one score per subset and scoring variation. Correctness and efficiency
remain separate variations; their units and score direction must be explicit.

A candidate correctness score inherited from the original experiment is the mean
signed reward loss:

```text
S(agent, subset) = mean task reward for that agent on that subset
M(subset) = mean over selected mutants of
            [S(baseline, subset) - S(mutant, subset)]
```

This candidate weights mutants equally and preserves negative differences when
a mutant outperforms the baseline. It is an example policy, not a finalized
default. Repetition aggregation, incomplete evidence, and any alternative
weighting must be specified before use.

**Outputs:** Mutant Scores, keyed by subset ID, with the case, baseline, mutant
cohort, aggregation, and scoring variation identified. Retain per-mutant
comparisons as supporting evidence.

**Completion:** every requested subset has a score or an explicit unavailable
value and reason. Never silently change task membership or mutant composition
to obtain a computable score.

### 5. Compute Coverage

**Purpose:** obtain the system under evaluation's coverage scores for the same
subsets.

**Inputs:** the case description, Eval Subset Definitions, permitted baseline
evidence from Raw Execution Results, and the versioned system under evaluation
with its settings. Additional inputs such as agent source or task contents must
be explicitly allowed by the configured evidence policy.

**Operation:** invoke Eval Author or another coverage system through its declared
interface to produce scores. If that system needs a rubric, let it generate the
rubric within this stage using only permitted inputs, and retain the resulting
artifact. Rubric generation and measurement are parts of the system being tested,
not assumed benchmark answers.

For a method that uses one case-level rubric, freeze its generated contents and
denominator before scoring subsets and reuse them within that comparison.
A method that derives its coverage definition separately for each subset must
declare that behavior and retain each definition; it must not be silently treated
as a shared-rubric measurement. The allowed generation scope is an open protocol
decision.

Reuse per-task measurements across overlapping subsets only when the system's
method makes them independent of the other tasks in the subset. Systems that
score whole subsets can instead produce scores directly. Mutant outcomes,
mutant scores, and correlation results are never inputs to coverage computation.

**Outputs:** Coverage Scores, keyed by subset ID, with system and configuration
identities and supporting measurement evidence. Retain any generated rubrics,
per-task measurements, and judgments with their exact inputs and versions.

**Completion:** every requested subset and system configuration has a score or
an explicit unavailable value and reason. Generated coverage artifacts are
accounted for. If computation generates rubrics or judgments with a model,
their reproducibility must satisfy the chosen
[determinism contract](architecture.md#determinism); deterministic aggregation
alone does not make those outputs deterministic.

### 6. Spearman Correlation

**Purpose:** determine whether coverage predicts mutation sensitivity across
evaluation subsets.

**Inputs:** Coverage Scores and Mutant Scores with matching subset definitions.

**Operation:** verify matching case, baseline, benchmark, and subset identities.
Join scores by subset ID and compute Spearman rank correlation separately for
each coverage-system configuration and mutant-score variation, using average
ranks for ties. Retain each paired score and every exclusion with its reason.

**Outputs:** Correlation Results containing the coefficient, number of included
subsets, paired scores, configuration identities, and exclusions.

**Completion:** each comparison has a result or an explicit undefined result.
Constant scores or too few usable pairs do not become zero correlation.
Unexpected identity or membership mismatches are errors, not silently dropped
observations. Any allowed exclusion policy must be specified in advance.

Overlapping subsets share observations. Treating them as independent samples
would overstate the strength of evidence. The uncertainty method remains an
open decision; an observed coefficient alone is not a general validity claim.

### 7. Record Experiment

**Purpose:** preserve enough evidence to inspect and reproduce the analysis.

**Inputs:** the frozen configuration, supplied input and system identities, and
all stage outputs, including generated coverage artifacts, paired scores, and
correlation results.

**Operation:** retain an inventory of inputs, execution attempts, native evidence,
subset memberships, generated rubrics and judgments when present, scores,
exclusions, implementation versions, and completion status. A rerun creates a
distinct record linked to its reused inputs.

**Outputs:** an Experiment Record containing the inventory and analysis report,
with evidence stored or referenced according to the eventual distribution format.

**Completion:** every input and output is accounted for and accessible to the
intended reader. Unavailable values and failed attempts retain their explanations.
Partial runs should retain diagnostic records, but must not be reported as
completed benchmark results.

Mutant scoring and coverage measurement can run independently after subset
construction. Their results meet at correlation. None of the statistical
analysis stages reruns the benchmark agents.
