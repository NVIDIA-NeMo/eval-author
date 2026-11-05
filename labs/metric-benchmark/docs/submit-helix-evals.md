<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# submit_helix_evals.py — execute published inputs

Submit one native Helix taskset run per case and agent version. This prototype
uses an existing authenticated Helix client context and already-published inputs.
Use the [publication stage](tools.md#publish-inputs-to-helix) to upload prepared bundles and tasks and request image builds first.

It is based on the current [case format](benchmark-format.md). The case and agent/task
manifests remain unchanged; a companion file maps their identities to remote inputs.
The publisher produces the bindings used by this stage.

## Usage

```bash
python labs/metric-benchmark/submit_helix_evals.py \
  --cases cases.yml --execution helix-execution.yml --bindings publication/bindings.json \
  --run-id experiment-1 --output submitted-run --context evaluation
```

- `--cases`: the prepared case manifests; supplied content pins are checked.
- `--execution`: connection and scheduling settings, shown below.
- `--bindings`: publisher-generated mapping tied to case and publication identities.
- `--publication`: publication record; defaults to `publication.json` beside the bindings.
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
backend: helix
base_url: https://helix.example.com
workspace: benchmark
dispatcher_profile: workers
max_concurrent_task_jobs: 2
sandbox:
  profile: evaluation-sandbox
```

Use the publisher's complete `bindings.json` and `publication.json`; do not hand-author
Harbor targets. The planner derives each target from the prepared agent and checks
its packaging and bundle identity against the publication record before any submission.
The publication digest covers the derivation separately from unchanged `cases_sha256`.
Unsupported registered-agent mappings are rejected by publication.

Omitted endpoint/workspace values are taken from the resolved publication record.
The selected context must match it; changing context cannot redirect a replay.

Before the first submission, the library checks every case's uploaded taskset
membership, native task identities and ready default-platform image builds.
Pending image builds return exit 2 before any run is submitted. It forces one attempt and zero trial
retries. Concurrency is per remote run, subject to Helix's operator limits.

`submission.json` records the verified publication, requests and stable idempotency keys before network
submission. `provenance.json` records the submitting client/server versions.
Accepted responses live under `runs/`. Repeat the same command under
the same Helix caller after interruption: saved acceptances are reused and ambiguous
requests replay their original keys. Changed inputs need a new output directory.
A terminal failed evaluation is not restarted by replaying its submission.

## Python library and runtime

`plan_submission(loaded_cases, settings, bindings, run_id, publication=publication_plan)` constructs the requests.
`submit_helix_evals(plan, output, client)` submits them. The CLI calls those same
functions. `HelixClient.from_context(workspace, context=...)` uses the official
`nemo-helix-plugin` client, installed separately for this exploration; planning
and the normal local runner do not require it. No dependency lock changes are
included yet. The prototype was checked against Helix source commit
`df376600748eb576146dfb2d1b8770414545a487`.

Next use the [download tool](download-helix-results.md). Its current output is
an evidence collection, **not yet the benchmark's validated results directory**.

## Live-test handoff

Start with one prepared task with a deterministic, non-model verifier and two
built-in agent versions (Oracle and NOP), so the first live check uses two task
jobs and no model calls. These are transport
controls, not meaningful mutants. The task must already be published with its
images and have a solution/verifier suitable for the Oracle/NOP comparison.
Do not use the synthetic repository test bindings as uploaded inputs.

Obtain these from the instance owner or the preparation step:

- An authenticated client context and an authorized workspace on an instance with
  native taskset runs enabled; record its deployed version separately from the client version.
- A dispatcher profile and sandbox configuration valid for that deployment.
- The prepared `cases.yml` and its referenced agent/task metadata, with real content pins.
- Published taskset/task revision references and native task IDs for those cases,
  plus the publisher-generated bindings and publication record that derive their targets.

Keep deployment URLs, credentials, native evidence and private logs outside the
source checkout and public issue/PR comments.

### Reproduce the client environment

The benchmark and Helix environments use different Harbor versions. Keep their
virtual environments separate. From a fresh benchmark checkout:

```bash
git clone https://github.com/NVIDIA-NeMo/eval-author.git eval-author-helix
cd eval-author-helix
gh pr checkout 162
uv sync --locked --python 3.13
uv run --locked pytest -q tests/test_metric_benchmark_helix.py
export EVAL_AUTHOR_REPO="$PWD"

git clone https://github.com/NVIDIA-NeMo/nemo-helix.git ../nemo-helix-client
cd ../nemo-helix-client
git checkout --detach df376600748eb576146dfb2d1b8770414545a487
uv tool run --from uv==0.13.4 uv sync --python 3.13 --frozen --no-default-groups --package nemo-helix-ext
uv tool run --from uv==0.13.4 uv sync --frozen --no-default-groups --package nemo-evals-plugin --inexact
export HELIX_PYTHON="$PWD/.venv/bin/python"
uv tool run --from uv==0.13.4 uv run --no-sync _nhx --no-telemetry evals runs --help
```

The benchmark test run uses mocked HTTP; its optional native-schema check skips
without the Helix package. Run it again in the client environment to exercise the
native schema too (still no live requests):

```bash
uv tool run --from uv==0.13.4 uv run --no-sync --with pytest \
  pytest -q "$EVAL_AUTHOR_REPO/tests/test_metric_benchmark_helix.py"
```

The pinned client is the version used for the prototype, not a claim about the
server version. If the deployment requires a different client, record that version
and rerun the schema check before adapting requests. Follow that client's documented
login flow; do not substitute unsigned development tokens for normal authentication.

### Verify access, plan, then submit

Set `HELIX_CONTEXT` to an existing authenticated context, `HELIX_WORKSPACE` to its
workspace, and `HELIX_TEST_DIR` to an absolute directory outside both checkouts
containing `cases.yml`, `helix-execution.yml` and `helix-inputs.yml` plus their metadata.
If login is needed, set `HELIX_BASE_URL` privately to the instance's URL and use the
normal browser/device login from the Helix client checkout:

```bash
uv tool run --from uv==0.13.4 uv run --no-sync _nhx --no-telemetry auth login  \
  --context "$HELIX_CONTEXT" --base-url "$HELIX_BASE_URL"
```

Use the same endpoint in `helix-execution.yml`. First make a read-only request:

```bash
uv tool run --from uv==0.13.4 uv run --no-sync _nhx --no-telemetry \
  --context "$HELIX_CONTEXT" evals runs list --workspace "$HELIX_WORKSPACE" --json
```

An HTTP 401/403 is an access/setup problem, not evidence that job submission works.
Resolve it with the instance owner before proceeding. If a token expires, renew it
through the Helix CLI before invoking the prototype again.

Compute the binding's `cases_sha256` from the actual prepared metadata, rather than
hashing the YAML file's bytes:

```bash
PYTHONPATH="$EVAL_AUTHOR_REPO/labs/metric-benchmark" "$HELIX_PYTHON" - "$HELIX_TEST_DIR/cases.yml" <<'PYCODE'
import sys
from pathlib import Path
from metric_benchmark.config import load_cases
print(load_cases(Path(sys.argv[1])).identity)
PYCODE
```

Set that value in `helix-inputs.yml`. Set a new `HELIX_RUN_ID` for the smoke run;
retain the same value and submission directory when retrying that run:

```bash
"$HELIX_PYTHON" "$EVAL_AUTHOR_REPO/labs/metric-benchmark/submit_helix_evals.py" \
  --cases "$HELIX_TEST_DIR/cases.yml" \
  --execution "$HELIX_TEST_DIR/helix-execution.yml" \
  --bindings "$HELIX_TEST_DIR/helix-inputs.yml" \
  --run-id "$HELIX_RUN_ID" --output "$HELIX_TEST_DIR/submitted" \
  --context "$HELIX_CONTEXT" --plan-only
```

Inspect `submitted/submission.json`: the two planned runs must cover the intended
case, baseline and control agent, use the pinned taskset, and request one attempt
with no trial retries. Repeat the command **without `--plan-only`** to submit.
Repeat that same submission command once more and confirm that it returns the same
remote run IDs. Keep the submission directory for all subsequent checks and download.
Continue with the [live collection checks](download-helix-results.md#live-collection-checks).
