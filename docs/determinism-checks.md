<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Running the determinism checks

PR #75 includes #76's workflow contracts and the Phase 3–6 checking tools.
These commands run from the Eval Author checkout. They do not establish live
acceptance merely because their checker tests pass. The
[plan](determinism-plan.md) tracks the remaining measured acceptance work.

## Discovery comparisons

`tools/discovery_determinism.py` retains a digest-bound raw discovery report and
compares its full decision projection. Only `discovered_at` and the order of
explicitly listed set-like collections are normalized. Sample order, runtime
selection, missing/blocked/unproven reasons, severity, fingerprints, unknown
fields and duplicate entries remain significant. Temporary roots are not
silently rewritten; use the same fixture root for repeated discovery runs.

Supply `provenance.json` with nonempty identities for `repository`, `skills`,
`policy`, `fixture`, and `runtime`. Use revisions and content digests, including
dirty files, plus actual runtime versions and relevant backend identity. These
are caller-supplied identities; the comparator does not attest the environment.
The discovery fingerprint alone is insufficient: it excludes runtime, policy,
and other inputs outside the scanner's selected files.

```bash
uv run --locked python tools/discovery_determinism.py capture \
  --report /tmp/run-1/discovery.json --exit-status 1 \
  --provenance /tmp/provenance.json > /tmp/run-1/capture.json
uv run --locked python tools/discovery_determinism.py capture \
  --report /tmp/run-2/discovery.json --exit-status 1 \
  --provenance /tmp/provenance.json > /tmp/run-2/capture.json
uv run --locked python tools/discovery_determinism.py compare \
  /tmp/run-1/capture.json /tmp/run-2/capture.json
```

Use the discovery process's actual exit status. Keep reports outside the scanned
repository. Capturing or comparing does not execute discovery. Full discovery
may run provider tasks; file-only inventory remains `tools/search-tasks.py`.

Repeat comparison requires at least two distinct retained reports with identical
input identities, decisions and exit codes. `compare --mutation` requires both
changed input identities and changed evidence; the caller must additionally
assert the specific expected change. Unchanged happy paths alone do not prove
failure sensitivity. Exit codes are 0 for match/expected change, 1 for mismatch,
and 2 for incomplete or malformed evidence.

`tests/test_discovery_determinism.py` runs discovery repeatedly over disposable
Harbor, Gym-marked, mixed, empty, malformed, ignored, dropped-task and missing-env
fixtures, with different working directories. A controlled Gym fixture also reverses
creation and concurrent validation completion order, checks the exact sample and
unsampled entries, and mutates a config to require changed evidence.
Provider availability is recorded;
a Gym marker without Gym remains blocked. The existing runtime/scanner/skill
contract suites cover creation order, scan bounds, unreadable inputs, missing
backends, sampling and copied installations. Native Gym/live Solve acceptance is
separate from these checks.

## Audit and task evidence

New measurements bind the exact parsed ATIF bytes and the canonical audit
payload. Semantic judgments also bind their canonical JSON contents. Aggregation
records each measurement file's byte digest and rejects changed traces,
denominators, judgments and duplicate/copy measurements. Existing unbound v1
reports remain readable for review, but cannot establish task acceptance.

```bash
uv run --locked python skills/eval-author-audit/scripts/audit_spec/report.py \
  --verify-existing /absolute/path/to/audit-coverage-report.json
```

`task_pipeline.py scaffold` records the source gap report and proposal digests in
`.eval-author-lineage.json` inside the draft. Finish the draft before preparing
its execution manifest. Changing the proposal, source report or task makes
existing proof stale. Preserve the lineage file through provider packaging.

A result contract now requires separate `reference`, `incorrect`, `empty`, and
`agent` cases. The empty case tests no-op/empty-answer behavior; it cannot replace
a realistic wrong-answer control. All controls need native health evidence.
Synthetic receipt tests do not establish verifier quality or real-agent success.

`task_evidence.py run` registers each attempt before launching its command in a
manifest-specific `.attempts` directory. Acceptance requires every registered
closure attempt, including failed or interrupted attempts, all controls, and at
least two distinct agent runs. Copying a trace to another path cannot create a
fresh run. Repair through a new manifest, retaining the previous manifest,
ledger and results; do not delete failed entries to obtain acceptance. This is
local integrity/accounting, not a tamper-proof execution attestation.

`task_pipeline.py verify` rebuilds bound before/after aggregates and requires the
same denominator, matching trace/run identities, coverage in every repetition,
and unchanged draft lineage. Its saved verdict includes upstream artifact hashes.
Revalidate a saved verdict before relying on it:

```bash
uv run --locked python skills/eval-author-task-create/scripts/task_pipeline.py \
  check-acceptance --report /absolute/path/to/acceptance.json
```

For legacy evidence, regenerate measurements and aggregates, recreate a draft
from the bound report/proposal, then prepare a new manifest with all controls and
repeat execution. Never adopt old results as fresh runs. Read-only `select` and
review of legacy reports remain available.

## Factual and semantic reports

`tools/check_report_facts.py` emits a complete JSON-pointer factual ledger from a
structured source. Nulls and empty collections count as facts. A candidate must
retain every fact, its type, source digest, and evidence stage; omissions,
contradictions, extra success claims, and unreviewed prose fields fail.

```bash
uv run --locked python tools/check_report_facts.py \
  --source /tmp/discovery.json --stage validation > /tmp/facts.json
uv run --locked python tools/check_report_facts.py \
  --source /tmp/discovery.json --stage validation --candidate /tmp/facts.json
uv run --locked python tools/check_report_facts.py \
  --source /tmp/discovery.json --stage validation --render
```

The stage is supplied by the caller: `inventory`, `prerequisites`, `validation`,
`sampled_execution`, `coverage`, or `acceptance`. The checker compares explicit
claims; it does not infer the truth of an arbitrary narrative or repair a false
source. A sampled result cannot acquire an `all_tasks_passed` claim absent from
its source.

Use the existing [semantic pilot](semantic-regression.md) with its reviewed,
locked baseline. Run two independent `--mode check` batches without changing
thresholds, then account for both:

```bash
uv run --locked python tools/check_semantic_batches.py --suite /private/suite.json \
  /private/batch-1/semantic-regression-summary.json \
  /private/batch-2/semantic-regression-summary.json
```

The batch checker requires the same clean source, frozen suite/models/run count,
case denominator and reviewed comparison identity. It rereads the suite's locked
baseline and each batch's retained reports/claims rather than trusting digest
strings in the summaries. It also verifies retained `gate-result.json` bytes
against `gate_digest` and the reported verdict/metrics, so editing a summary
cannot turn a regression into a pass. Keep those private directories intact.
New semantic captures include `batch.json` and `batch_digest` so a copied result
cannot pass as an independent batch. Failed, empty and incomplete results remain evidence. Old summaries
without batch identity and gate input bindings must be rerun. Gate receipts bind
the batch, configuration, clustering inputs, baseline and measurement identity.
The checker validates baseline run counts and measurement compatibility. Existing
reviewed locks remain valid through the explicit legacy measurement-version
mapping; capture metadata changes alone do not require recalibration.
The checker consumes the semantic pilot's
verdicts; it does not independently recalculate model judgments. Neither report
stability nor typed-fact retention establishes task quality.

## Host capture and accounting

`tools/workflow_matrix.py` plans every contract and bounded paraphrase for the
four documented assistants (Codex, Claude Code, Cursor, Pi), both checkout and
copied installations, and macOS/Linux/Windows, with two repetitions. Missing
cells cannot be removed to improve the denominator.

```bash
uv run --locked python tools/workflow_matrix.py > /tmp/workflow-matrix.json
uv run --locked python tools/workflow_matrix.py \
  --matrix /tmp/workflow-matrix.json --attempt /private/attempt.json
```

The second command returns incomplete until every required cell has evidence.
Counts partition planned cells into pass/fail/incomplete/skipped and report the
number of attempted executions separately. Retries use distinct `attempt_id`
values; failures/incomplete attempts cannot be replaced with a successful retry.
Raw captures cannot be reused across repeat cells.

`tools/capture_workflow.py --help` documents a bounded native-process recorder.
Supply a disposable fixture, exact planned cell, loaded skills root, actual host
version/model/settings, and a trusted argv that accepts the exact prompt on stdin
and emits native JSONL. It records filesystem manifests, the command, return
code, raw stdout/stderr, skill digest and interruptions outside the observed tree.
For multi-turn cases, use the host's continuation command and record each turn;
do not concatenate prompts into a fresh session and call that continuation.
Capture never returns a passing acceptance verdict.

Codex `exec --json` and Claude Code `--output-format stream-json --verbose` have
native event adapters. Unknown events, incomplete turns and unfinished tool items
are incomplete. Cursor/Pi native schemas remain unsupported and therefore
incomplete. [Codex's native event format](https://developers.openai.com/codex/noninteractive)
is distinct from the normalized workflow evidence schema.

A review file binds native stream digests and loaded-skill digest to reviewed
flow, handoff, action and attempted-write labels. It contains `reviewed_by`,
`raw_sha256` (one per turn), `skills_sha256`, and the `evidence` object documented
in [workflow scope checks](workflow-scope-checks.md). Raw call IDs are prefixed
with the zero-based turn (`0:item_1`); the normalized raw envelope is canonical
JSON containing the adapter's complete `tool_calls` array. Compound shell calls
need review of every action, including commands joined to a skill read.

An attempt contains the planned cell fields plus `attempt_id`, `host_version`,
`model`, `source_revision`, `fixture_sha256`, `settings`, `skills_root`,
`skills_sha256`, `review`, `raw_turns`, and `captures` (`path`/`sha256` per turn).
Each capture is the recorder's retained `capture.json`; its process must exit 0,
its provenance must match, and its before/after snapshots must equal the review. These labels
and the declared installation/model are reviewed provenance, not independent
host attestation. Before/after manifests cannot observe temporary effects that
were reverted; raw action review remains necessary. Provider execution and
model-output variation remain separate, explicitly unmeasured dimensions.

Live and semantic gates remain advisory. Do not infer cross-host equivalence
from one run, one operating system, or a passing script portability matrix.

Captures record the actual recorder platform independently of the planned cell.
Every planned turn requires native output, a capture record and review. Multi-turn
cells require the same native session identifier on every turn; missing session
identity is incomplete evidence. These are local integrity checks, not attestation.
