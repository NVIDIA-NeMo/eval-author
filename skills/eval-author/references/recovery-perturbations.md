<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Recovery perturbations

- Compare one environmental demand at a time against an unchanged task objective and verifier.
- Qualify both arms with a reference strategy before collecting target-agent observations.
- Keep synthetic controls, model trials, and rubric scores separate.

## Select a scenario

Use this procedure from [coverage planning](coverage-planning.md#plan-breadth-per-behavior) when recovery, information quality, or workflow demands are part of the agreed scope. The environment workflow owns the stateful backend in `../../eval-author-environment/scripts/recovery_scenarios.py` and the catalog in `../../eval-author-environment/scenarios/recovery.json`.

Each catalog entry declares its identifier, category, severity, seed, deterministic call schedule, trigger, expected successful behavior, and machine-readable failure signature. Synthetic entries are authored conditions, not recovered production incidents. A trace-observed entry requires source references and review of whether its trigger and response match the retained trace. A citation alone does not establish that fidelity.

Choose one scenario per comparison. Preserve the task request, authoritative truth, verifier, agent configuration, and budgets across arms. An explicit call schedule describes reproducible service behavior. It does not seed model generation. Record provider revision and sampling seed when available and leave them unknown otherwise.

The initial backend supplies invoice-total and stock-total tasks. Both require selecting eligible records for a requested account and submitting their authoritative total. The information and workflow perturbations change how the agent obtains that result, not which result is correct. The timeout and rate-limit scenarios return synthetic service errors. They do not reproduce network latency, HTTP transport failures, or provider outages. Rate-limit waiting advances a logical service clock.

## Qualify the comparison

1. Run the reference strategy through the same public operations and budgets as the target. It must enumerate continuations, read authoritative records, select the requested account and eligibility, satisfy preparation requirements, and submit the total.
2. Verify the unchanged final-state checker accepts both arms. Keep missing reference success as a setup blocker, not an agent failure.
3. Run a deliberately incomplete scripted strategy to establish whether each planted mechanism is reachable. Label it a scripted control, not a model-performance demonstration.
4. Run repeated target-agent trials with fresh state. Alternate arm order. Preserve failures and budget exhaustion without automatic replacement trials.
5. Retain each trial's inputs, tool responses, final state, verdict, execution status, budget use, and scenario identity. Record injection reach, response delivery, detected failure signature, and task success separately.

The external adapter acknowledges delivery by returning a response to the next request containing the tool result. A failed adapter round trip leaves delivery unconfirmed. This does not prove the model attended to the result. An ordinary failure following exposure does not by itself identify the intended mechanism. Preserve a missing signature as unconfirmed. Execution errors and budget exhaustion do not establish explicit abandonment.

## Read difficulty results

The repository command `labs/metric-benchmark/recovery_trials.py` produces `difficulty-matrix.json`. Each row joins a task and scenario with severity, provenance, reference controls, repeated paired results, pass-rate difference, and intended-failure observations. These rows support the existing breadth and difficulty discussion. They are not an additional rubric scorer and contain no numeric rubric score.

Use the existing native task-evidence owner when exporting these backends into Harbor or NeMo Gym. Local state-machine trials are not native runtime receipts. Metric-benchmark's agent-mutant analysis compares different agents on the same task and must not be used unchanged for these task/environment comparisons.

## Add another scenario

1. Define a service condition with a finite trigger schedule and an available recovery route. Do not make the backend branch on the correct answer or the agent's instruction text.
2. Add a catalog entry with a unique stable ID, declared provenance, severity, seed, trigger, schedule, successful behavior, and failure signature. Reject unknown configuration fields and unsupported mechanisms rather than silently ignoring them.
3. Implement the service condition under the environment owner. Keep source truth and final-state grading outside the response mutation.
4. Exercise the trigger, a correct recovery, an intended-failure control, a non-triggered call, reset, and deterministic replay through public operations. Include the maximum supported severity under the selected budget.
5. Run isolated paired comparisons and inspect retained evidence before proposing combinations. The initial runner rejects combination requests by accepting only a single scenario per trial.

For repository command usage and external adapter boundaries, see `labs/metric-benchmark/docs/run-evals.md` in the development checkout.
