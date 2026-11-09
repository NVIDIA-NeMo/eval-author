<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Collect Helix results

`helix_collect.py` downloads selected runs, verifies their identities and writes the
standard benchmark results directory. There is no separate import command.
See the [architecture command examples](helix-architecture.md) for the full workflow.

## Inputs and outputs

Supply `--references`, one `--run` receipt per prepared baseline/mutant, the shared
`--client` configuration, `--sources-output` for verified source recovery, and
`--output` for the final results directory. Receipts must cover the exact matrix,
with one explicitly selected run per agent and common execution settings. Duplicate
selections, another experiment's references and unsubmitted receipts are errors.

The tool downloads any needed agent/task sources using `helix_sources.py`'s shared
operation. Sources already present and matching their pins are reused. The original
upload directory and local publication cache are not required. Keep the experiment
and analysis files; source recovery does not reconstruct them.

Collection needs the qualified Helix client environment, including the agents/evals
plugins, Harbor 0.24.0 and `tomlkit==0.15.0`. Later registered-agent validation and
analysis use that same environment offline because identity checking uses the
platform's pure agent translator. Oracle/NOP results also validate in the normal
benchmark environment.

The output contains `execution.json`, per-case `results.json`, original native trial
results/traces/logs, resolved job specifications, and task/agent identity evidence.
The importer reconstructs image binding on a separate task copy; it never rewrites
native checksums or prepared sources. It verifies the original task archive,
registered-agent configuration, captured source hashes, resolved images, complete
matrix and native measurements before publishing output.

## Pending runs, failures and retries

- Exit **2** means runs or evidence are still pending. No final output directory is
  published. Repeat the identical command later.
- Exit **0** means the selected matrix was collected and passed identity validation.
  A failed trial can still have an unavailable score; exit 0 does not mean every
  trial succeeded or every verifier passed.
- Exit **1** means invalid selections, unavailable/changed source evidence, an API
  failure, or an integrity failure. Investigate before changing inputs.

Failed task jobs remain explicit unavailable observations. Successful task jobs
with native execution errors preserve those errors and their available artifacts.
One failed task does not discard the other trials.

The hidden sibling `.OUTPUT.helix` directory retains collection state and native
archives for interrupted-transfer recovery. The final directory is published
atomically. Repeating collection validates and reuses identical evidence; existing
outputs with different contents are rejected. Preserve receipts and private evidence
outside public checkouts. Remote resources are not automatically deleted.

## Qualification limits

Supported agents are separately registered bundles or native Oracle/NOP controls.
Supported tasks have one shared agent/verifier environment. Separate verifier images
and compose image mappings fail explicitly. A zero reward does not prove that a
model ran: inspect native errors and trajectories alongside verifier rewards.
