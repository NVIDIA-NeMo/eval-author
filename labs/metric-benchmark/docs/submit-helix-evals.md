<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# submit_helix_evals.py — exploratory prototype

Submit one native Helix taskset run per case and agent version. This prototype
uses an existing authenticated Helix client context and already-published inputs.
It does not upload tasks, build images, or register agents.

It is based on the current [case format](benchmark-format.md). The case and agent/task
manifests remain unchanged; a companion file maps their identities to remote inputs.
Separating executable locations out of the case metadata is a subsequent design change.

## Usage

```bash
python labs/metric-benchmark/submit_helix_evals.py \
  --cases cases.yml --execution helix-execution.yml --bindings helix-inputs.yml \
  --run-id experiment-1 --output submitted-run --context evaluation
```

- `--cases`: the prepared case manifests; supplied content pins are checked.
- `--execution`: connection and scheduling settings, shown below.
- `--bindings`: prep-generated mapping tied to the resolved case identity.
- `--run-id`: identifier for this experiment execution.
- `--output`: local submission directory. Keep this for recovery and download.
- `--context`: an existing Helix CLI context; otherwise use the active context.
- `--allow-unsigned`: permit missing pins while still checking supplied pins.
- `--plan-only`: write `submission.json` without connecting or submitting jobs.

The client context must match the configured endpoint. Authentication comes from
Helix's client configuration, including its supported environment overrides.
Do not put credentials in experiment files.

```yaml
# helix-execution.yml
base_url: https://helix.example.com
workspace: benchmark
dispatcher_profile: workers
max_concurrent_task_jobs: 2
sandbox:
  profile: evaluation-sandbox
```

An upload mapping for a small case could have this shape. Digest placeholders
must be replaced by prep; Helix reference fragments use 64 bare hex characters,
whereas the benchmark's case digest includes the `sha256:` prefix.

```yaml
# helix-inputs.yml
cases_sha256: "sha256:<resolved-case-digest>"
cases:
  example-case:
    taskset: "benchmark/suite#<taskset-content-digest>"
    tasks:
      example-task:
        ref: "benchmark/uploaded-task#<task-content-digest>"
        native_task_id: example-task
    targets:
      baseline:
        source: {name: oracle}
      mutant:
        source: {name: nop}
```

These built-ins demonstrate execution, not a scientific mutation experiment.
Targets use the native Helix Harbor target schema. Registered agent sources are
resolved by Helix at acceptance; proving their resolved identity against prepared
mutant bundles remains part of the validation work before release.

Before the first submission, the library checks every case's uploaded taskset
membership and native task identities. It forces one attempt and zero trial
retries. Concurrency is per remote run, subject to Helix's operator limits.

`submission.json` records requests and stable idempotency keys before network
submission. Accepted responses live under `runs/`. Repeat the same command under
the same Helix caller after interruption: saved acceptances are reused and ambiguous
requests replay their original keys. Changed inputs need a new output directory.
A terminal failed evaluation is not restarted by replaying its submission.

## Python library and runtime

`plan_submission(loaded_cases, settings, bindings, run_id)` constructs the requests.
`submit_helix_evals(plan, output, client)` submits them. The CLI calls those same
functions. `HelixClient.from_context(workspace, context=...)` uses the official
`nemo-helix-plugin` client, installed separately for this exploration; planning
and the normal local runner do not require it. No dependency lock changes are
included yet. The prototype was checked against Helix source commit
`1cb0b0c4701e9757b57db2c4c27955febba7aafb`.

Next use the [download tool](download-helix-results.md). Its current output is
an evidence collection, **not yet the benchmark's validated results directory**.
