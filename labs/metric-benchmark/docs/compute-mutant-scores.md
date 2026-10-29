<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# compute_mutant_scores.py

Compute a mutation-testing score for each predefined task subset.

## Usage

```bash
uv run python labs/metric-benchmark/compute_mutant_scores.py \
  --benchmark experiment.yml --case case-id \
  --results-root experiment-results --output mutant-scores.json
```

## Inputs and behavior

The CLI derives selection from the configured case and `agent_run_id` values in
the selected case’s `results.json`, which must contain one run per agent. No selection file needs to be
authored. Pass the whole experiment directory as `--results-root`; `execution.json`
locates the case selected by `--case`, regardless of its directory name.
Only the experiment YAML with its inline cases, referenced agent and task
manifests, the selected subset manifest, `execution.json`, and the selected
case’s `results.json` are required. Task directories, agent bundles, case descriptions,
and native trial evidence are not needed for this arithmetic stage. The command
checks the index and subset consistency; use `validate_results.py` separately for
native-evidence validation. Programmatic callers pass models to the library.
The score is the equally weighted mean over mutants of the signed mean paired
task degradation. Improvements remain negative. The output includes `diagnostics`
with `code: negative_mutant_score`, subset ID, and negative value.
`mutant_bundle_id` identifies an individual effect; null identifies the subset
aggregate. These flags are for inspection only: they neither fail scoring nor
clip values or exclude correlation pairs. Incomplete required pairs make
the subset unavailable. `attempt_policy: latest` selects by attempt number,
never by reward. Repetitions require a future explicit aggregation policy.
The score context carries the execution’s `run_id` and hashes of the results,
subsets and scoring selection. Coverage uses the same context so correlation can
check that the pairs refer to the same observations. The prepared subset manifest
is unchanged when the execution run ID changes.

## Python library

`compute_mutant_scores(raw, subsets, selection)`.
