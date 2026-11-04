<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# download_helix_results.py — collect native evidence

Check every submitted Helix run and collect the complete set of task bundles.
The current output is an evidence collection with `analysis_ready: false`.
It deliberately does not publish `execution.json` or claim compatibility with
the benchmark's native-evidence validator.

## Usage

```bash
python labs/metric-benchmark/download_helix_results.py \
  --submission submitted-run --output collected-evidence --context evaluation
```

- `--submission`: directory produced by the [submit tool](submit-helix-evals.md).
- `--output`: destination for the complete native evidence collection.
- `--context`: existing authenticated Helix context; otherwise use the active context.

The command checks once. It exits 2 while jobs are queued/running or results are
being packaged, and exits 1 for invalid identities, incomplete terminal matrices,
or API/transfer failures. Terminal infrastructure failures do not hide other active
runs: collection waits until every configured member has settled. An interrupted submission
must first be resumed with the submit tool.

Once all runs are complete, it reads every page of task membership and checks that
every expected task is accounted for. Succeeded jobs must have an artifact. It downloads each
`agent-eval-results` task bundle, including native Harbor files and agent traces.
It retains the original compressed bundle, task membership, artifact metadata and full
Jobs API response, including the resolved `job.spec`. Each job must match its
expected run and task index. Evidence is matched through native task IDs.
The combined summary alone is insufficient for offline analysis.

Failed, cancelled or expired task jobs produce explicit `status: missing` collection
observations with unavailable-evidence reasons, retained membership and any existing
Jobs API specification. Successful members remain fully collected. This is distinct
from a succeeded job whose native trial reports an execution error; its native evidence
is retained unchanged. Collection never invents a reward or reruns evaluations.
`provenance.json` captures the collecting client/server versions; job specifications
preserve the actual worker configuration. Resolved taskset ordering is retained too.

```text
collected-evidence/
  collection.json
  submission.json
  provenance.json
  runs/run-0000/status.json
  runs/run-0000/taskset.json
  runs/run-0000/task-0000/
    job.json
    member.json
    artifact.json
    bundle.tar.gz
    bundle/agent-eval-results/
      trials.jsonl
      ... complete original bundle, including native Harbor evidence ...
```

An incomplete transfer never publishes the destination. Verified individual archives
are retained in a private sibling `.<output-name>.helix-downloads` cache, bound to the
submission identity. Retrying verifies and reuses those archives rather than transferring
them again. The cache is removed after the complete output is published. A matching completed collection is
reused after checking its files. Archives with traversal, links, special entries,
duplicate paths, or members outside `agent-eval-results/` are rejected.
Extraction is isolated from collector-owned metadata.
Use one writer per destination.

## Python library and remaining boundary

`check_helix_runs(submission, client)` checks completion without downloading.
`download_helix_results(submission, output, client)` implements the CLI operation.
Neither requires the original executable task or agent directories.

Pass a completed collection and its publication records to the separate
[import stage](tools.md#execute-collect-and-import-helix-results). That stage
validates the resolved configuration and produces the shared results contract.
Collection itself remains an immutable native-evidence artifact.

## Live collection checks

After following the [submit handoff](submit-helix-evals.md#live-test-handoff), use
its client interpreter, context and external test directory:

```bash
"$HELIX_PYTHON" "$EVAL_AUTHOR_REPO/labs/metric-benchmark/download_helix_results.py" \
  --submission "$HELIX_TEST_DIR/submitted" --output "$HELIX_TEST_DIR/collected" \
  --context "$HELIX_CONTEXT"
```

Run once while jobs are active if possible: expect exit 2 and no published output
directory. Run again after completion: expect exit 0, one observation per task and
agent, and intact native result files and traces. A very short run may complete
before the first check; record that rather than claiming the live pending path was tested.
Repeat the completed download and confirm that the collection is unchanged.
An incomplete terminal matrix must not produce a successful collection. Terminal
failed members must be explicitly accounted for as missing native evidence.
The mocked suite exercises these failure cases; a successful live smoke alone does not.

For the one-task Oracle/NOP control, inspect the native verifier rewards and compare
them with the prepared task's expected results. Keep full bundles, submission records,
run/job IDs and client/server versions privately for diagnosis. Report only sanitized
outcomes publicly. The expected collection still has `analysis_ready: false` and no
`execution.json`; this is a smoke-test success, not completed analysis integration.

The full Jobs API response is preserved as `job.json` for each task. Its `spec`
records the resolved task, target, prepared image and sandbox policy used by the
importer. Do not change recorded checksums or native files to satisfy validation.
