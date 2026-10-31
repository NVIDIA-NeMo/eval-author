<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# compute_mutant_scores.py

Compute a mutation-testing score for each predefined task subset.

## Usage

```bash
uv run python labs/metric-benchmark/compute_mutant_scores.py \
  --cases cases.yml --analysis analysis.yml --case case-id \
  --results-root experiment-results --output mutant-scores.json
```

Supply these required arguments:

- `--cases`: path to the case collection and its agent/task manifest references.
- `--analysis`: path to the analysis settings, including subset references and scoring policy.
- `--case`: ID of the case to score from the case collection.
- `--results-root`: whole execution output directory containing `execution.json`.
- `--output`: path for this case's mutant-score JSON artifact.

Optionally, add `--allow-unsigned` when input SHA pins are missing during iteration;
any supplied pins are still verified. Unpinned task and agent sources must remain
available to compute their content identity.

## Inputs and behavior

Each invocation scores one case. `--cases` provides the case collection, while
`--case` selects the case ID within it. `--analysis` supplies that case's subset
manifest and the scoring policy. Use a separate output path for each case;
the experiment coordinator calls the same library once per case.

The CLI derives selection from the configured case and `agent_run_id` values in
the selected case’s `results.json`, which must contain one run per agent. No selection file needs to be
authored. Pass the whole experiment directory as `--results-root`; `execution.json`
locates the case selected by `--case`, regardless of its directory name.
Only cases.yml and analysis.yml, referenced agent and task
manifests, the selected subset manifest, `execution.json`, and the selected
case’s `results.json` are required when case inputs are signed. Task directories
and agent bundles can then be absent; sign the bundle before removing those sources
for analysis elsewhere. See [stage boundaries and reuse](benchmark-format.md#stage-boundaries-and-reuse).
Case descriptions and native trial evidence are not needed for this arithmetic stage. The command
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

`compute_mutant_scores(raw, subsets, selection)` returns a `MutantScores` model;
the CLI serializes that model to the JSON file named by `--output`.

## Output example

For a two-task subset with higher-is-better rewards:

| Agent | Task A | Task B | Mean baseline-minus-mutant difference |
| --- | --- | --- | --- |
| Baseline | 0.5 | 1.0 | — |
| Mutant A | 0.0 | 0.5 | 0.5 |
| Mutant B | 1.0 | 1.0 | -0.25 |

The subset score is `(0.5 + -0.25) / 2 = 0.125`. This illustrative output excerpt
shows the score and its contributing effects. The full artifact also includes
`context` (run/case/baseline IDs and hashes of results, subsets, and selection)
and `configuration` (agent run selection, mutant IDs, and scoring policy), omitted
here for readability.

```json
{
  "artifact_type": "mutant_scores",
  "score_definition": "mean_signed_paired_degradation_v1",
  "scores": {
    "subset-example": {"value": 0.125, "missing_score_reason": null}
  },
  "mutant_effects": {
    "subset-example": {
      "mutant-a": {"value": 0.5, "missing_score_reason": null},
      "mutant-b": {"value": -0.25, "missing_score_reason": null}
    }
  },
  "diagnostics": [
    {
      "code": "negative_mutant_score",
      "eval_subset_id": "subset-example",
      "mutant_bundle_id": "mutant-b",
      "value": -0.25
    }
  ]
}
```

An unavailable score instead has `"value": null` and a nonempty `"missing_score_reason"`;
`mutant_effects` identifies the missing pairs. A numeric score must be finite
and have no unavailability reason.

The correlation stage reads this artifact alongside the independently computed
coverage artifact. It checks matching `context` values and joins their `scores`
by subset ID. Coverage does not consume the mutant scores or diagnostics.
