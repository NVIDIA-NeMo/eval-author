<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# download_helix_results.py — proposal

Check previously submitted Helix runs once. If the complete matrix and its
artifacts are ready, download the evidence and publish a benchmark results
directory. **This command is proposed, not implemented.** It does not submit or
retry evaluations.

## Proposed usage

```bash
uv run python labs/metric-benchmark/download_helix_results.py \
  --submission helix-run/submission.json \
  --output experiment-results
```

- `--submission`: the record produced by
  [`submit_helix_evals.py`](submit-helix-evals.md), including the saved input mapping
  and remote run IDs. It supplies the Helix instance and workspace.
- `--output`: destination for the complete, locally readable results directory.

Use Helix authentication with permission to read the runs and their artifacts.
Original executable task and agent directories should not be needed for download;
collection uses the metadata frozen in the submission record.

## Completion check

First require a recorded submission for every expected case/agent; if submission
was interrupted, report that it needs to be resumed. Inspect every recorded run
and all its task members before downloading evidence.
Compare them with the expected baseline/mutant × task matrix, including task and
agent identities. A top-level `completed` status or a downloadable summary alone
does not prove that all expected evidence exists.

| Condition | Proposed behavior |
| --- | --- |
| Runs are queued/running, or completed runs are still packaging artifacts | Print progress and “not done running” or “results still being prepared”; exit 2. Run this command again later. |
| All expected trials are finished and their evidence is ready | Download, validate, publish the results directory; exit 0. |
| A run is failed/cancelled/expired, or terminal output is missing required trials/evidence | Name the case, agent, and affected tasks; exit 1 without publishing a complete results directory. |
| Authentication, API, transfer, or validation fails | Report the actionable error; exit 1. Preserve the submission record for retry. |

Report known terminal failures even if other runs are still active. A finished
Harbor trial with an agent/verifier error is still an observation: retain its
native error and unavailable measurements. That differs from a missing trial or
unfinished job. The initial tool does not import partial runs or silently replace
missing observations with zero. It does not wait indefinitely or cancel other runs.

## Download and output

Fetch each task's native evidence as well as the combined summary. Helix's
[bundle merger](https://github.com/NVIDIA-NeMo/nemo-helix/blob/04e38c56dcbf1044c0574405c307f7937881bac9/plugins/nemo-evals/src/nemo_evals/taskset_runs/bundle_writer.py)
links to evidence in per-task bundles; the
[existing run download](https://github.com/NVIDIA-NeMo/nemo-helix/blob/04e38c56dcbf1044c0574405c307f7937881bac9/plugins/nemo-evals/src/nemo_evals/runs_cli.py)
downloads the merged bundle only. Copying that bundle alone would leave the
benchmark dependent on remote evidence.

Map trials using the saved case/task/agent identities and Helix membership records,
not directory-name guesses. Preserve native trial records, verifier outputs, and
agent traces, including baseline traces used by coverage. Extract every native
reward metric and retain error/unavailable outcomes. Do not rewrite native JSON
to make it match a local run.

The target is the shared [results contract](results-format.md):

```text
experiment-results/
  execution-inputs.json
  execution.json
  cases/case-0000/execution-results/
    results.json
    agents/agent-0000/run-0001/
      ... intact native trial evidence from the task bundles ...
```

Storage labels are illustrative; the indexes carry identities and relative
evidence paths. Preserve the `cases_sha256` binding introduced with the separate
case definitions, and record Helix execution settings in `execution_sha256`.
Remote run/job IDs remain available as execution provenance. There is no analysis
configuration in the downloaded execution identity.

Download into staging and validate before publishing `execution.json` as the
completion marker. An interrupted transfer must not leave a directory that claims
to be complete. Repeating collection should reuse a matching complete destination
or finish the interrupted transfer; it must not merge different submissions.
Scoring, coverage, and correlation then consume the saved directory as usual.
A `download_helix_results(...)` library function would provide the same behavior
as the thin CLI.

## Compatibility before implementation

The flow above is the intended interface, not a claim that Helix bundles currently
pass our validator. The current reader targets Harbor 0.20.0; the inspected
[Helix lockfile](https://github.com/NVIDIA-NeMo/nemo-helix/blob/04e38c56dcbf1044c0574405c307f7937881bac9/uv.lock)
pins 0.24.0. Helix also binds prepared images into task configuration and
resolves registered agents into runtime configurations. These transformations
affect checksums, paths, and agent identity checks.

Before implementing collection, obtain a small completed native Helix example:
one case, a baseline, one mutant, and two tasks, including both runs' frozen plans
and all task bundles.
Use it to define a narrow validation of prepared inputs against effective runtime
inputs and to test native result compatibility. Preserve original evidence and
the existing local-run checks. Avoid requiring conflicting Harbor versions in the
same Python environment merely to call the Helix API.

Also test an active run, a terminal partial run, a lost submit response, and an
interrupted download. These are the acceptance checks for the two-tool interface;
no Helix jobs or deployment checks have been performed for this proposal.
