<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Running the determinism checks

Use these tools to compare workflow decisions, verify retained evidence, and
check reports against their sources. Run commands from the repository checkout.
Developer tools in `tools/` are not included in skill-only installations.

| Check | What it verifies |
| --- | --- |
| [Workflow scope](#workflow-scope) | Reviewed actions, writes and handoffs match a request contract. |
| [Discovery comparisons](#discovery-comparisons) | Repeated discovery reports agree under frozen inputs. |
| [Audit and task evidence](#audit-and-task-evidence) | Acceptance still matches its retained source and execution evidence. |
| [Factual and semantic reports](#factual-and-semantic-reports) | Reports preserve source facts and semantic comparisons use compatible evidence. |
| [Host capture and accounting](#host-capture-and-accounting) | Every planned host attempt retains its native output and review. |

Passing checker tests establishes checker behavior. Live workflow acceptance
requires actual executions and reviewed evidence for the relevant inputs,
hosts and environments. Exact prose and model trajectories need not be identical.

## Workflow scope

`tools/check_workflow_scope.py` checks supplied evidence against request contracts.
It does not launch assistants or select their routes.

The [contracts](../tests/fixtures/workflow-scope/contracts.json) pin their source
revision and policy references. Cases cover broad help, explicit audit, inventory,
readiness, narrow audit validation, shared Journey 0 entry, recommendations, task
drafting, trace inspection, and follow-ups that broaden or narrow scope.
Each turn specifies exact prompt alternatives, prior context, selected flow,
permitted and required actions, allowed writes, and terminal handoff.
These are fixture-specific scopes, not universal permission grants.

### Capture format

Save capture artifacts outside the observed disposable repository. Take snapshots
while the workspace is quiescent, before and after each turn:

```bash
python tools/check_workflow_scope.py snapshot --root /tmp/disposable-repo > /tmp/before.json
# Run the authorized workflow with a capture-capable harness.
python tools/check_workflow_scope.py snapshot --root /tmp/disposable-repo > /tmp/after.json
python tools/check_workflow_scope.py check --evidence /tmp/capture.json --raw-trace /tmp/tool-trace.json
```

Snapshots record regular-file SHA-256 hashes, permission modes, directories,
symlink targets, and errors. They include dotfiles and do not follow symlinks.
Unsupported file types, reparse points, read errors, and observed mutations make
a snapshot incomplete. This is a quiescent-fixture check, not a filesystem
sandbox or an atomic snapshot across concurrent writers.

The supported raw trace envelope is JSON with a `tool_calls` array. Every call
has a unique nonempty string `id`, a `tool` name, and a zero-based integer `turn` index.
Calls are in chronological order; extra native payload fields
are retained. An adapter must extract this array from its complete native trace
and retain that original trace separately. Include failed and unexpected calls.

A normalized capture has this shape:

```json
{
  "schema_version": 1,
  "case": "generic-help",
  "evidence_kind": "synthetic",
  "contracts_sha256": "<hash of sorted compact contracts JSON>",
  "raw_trace_sha256": "<hash of exact raw trace bytes>",
  "turns": [{
    "prompt": "Help me with evals.",
    "context": "Fresh session; no prior authorization.",
    "flow": "eval-author",
    "handoff": "ask_goal",
    "events": [],
    "before": {"complete": true, "entries": {}, "errors": []},
    "after": {"complete": true, "entries": {}, "errors": []}
  }]
}
```

Contract hashing uses Python's
`sha256(json.dumps(contracts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()`.
A tool event contains `call_id`, `tool`, `action`, and `write_paths`:

```json
{"call_id": "call-1", "tool": "exec_command", "action": "inventory", "write_paths": []}
```

The adapter classifies effects from the actual invocation and supplies every
attempted destination, including temporary writes, failed writes, deletions,
permission changes, and writes later undone. Each raw call ID must appear
exactly once in raw order, with matching tool names and turn indices. Unknown actions and missing coverage
make the evidence incomplete. Explicit empty events are valid for a welcome;
missing event lists are not.

Paths must be canonical repository-relative POSIX paths. Exact write rules
permit that entry; a trailing slash permits descendants by path component.
Only creation of required ancestor directories is implicit, never deletion or
permission changes. Both write attempts and snapshot differences are checked.
Follow-up snapshots must form an unbroken chain.

### Interpreting results

Exit 0 means supplied normalized evidence passes its contract; exit 1 means
an observed violation; exit 2 means malformed or incomplete evidence.
Known violations retain failure status even when other evidence is incomplete.
Reports preserve sorted findings and input digests; output goes only to stdout.

Every report declares `evidence_scope` as `supplied-normalized-evidence` and
`host_acceptance` as `not_measured`. Choosing `evidence_kind: captured` does not
promote the result to host acceptance. Flow, action, context, and handoff labels
are supplied by the adapter/reviewer. Raw-call coverage does not independently
prove those labels, native trace extraction, or conversational semantics.
Snapshots cover only the chosen root and can miss unrecorded transient or
outside-root activity. This is not enforcement of shell permissions.

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

## Audit and task evidence

Measurements bind the exact parsed ATIF bytes and the canonical audit
payload. Semantic judgments also bind their canonical JSON contents. Aggregation
records each measurement file's byte digest and rejects changed traces,
denominators, judgments and duplicate/copy measurements. Existing unbound v1
reports remain readable for review, but cannot establish task acceptance.

New aggregate reports retain a relative artifact index for the audit,
measurements, traces and judgment files. Preserve their relative layout when
moving a workspace or archiving evidence; verification uses the retained byte
digests without rewriting the original measurements. Legacy reports without an
artifact index still require their recorded locations. This portability applies
to coverage aggregates; execution manifests and saved task acceptance still
require the prepared task workspace and recorded receipt locations.

Coverage bindings describe retained inputs, not a re-execution or version pin of
the measurement implementation. Keep the measurement source revision with an
experiment when comparing versions; an update to the installed skills does not
invalidate historical measurements by itself.

```bash
uv run --locked python skills/eval-author-audit/scripts/audit_spec/report.py \
  --verify-existing /absolute/path/to/audit-coverage-report.json
```

`task_pipeline.py scaffold` records the source gap report and proposal digests in
`.eval-author-lineage.json` inside the draft. Finish the draft before preparing
its execution manifest. Changing the proposal, source report or task makes
existing proof stale. Preserve the lineage file through provider packaging.

A result contract requires separate `reference`, `incorrect`, `empty`, and
`agent` cases. The empty case tests no-op/empty-answer behavior; it cannot replace
a realistic wrong-answer control. All controls need native health evidence.
Synthetic receipt tests do not establish verifier quality or real-agent success.

`task_evidence.py run` registers each attempt before launching its command in a
manifest-specific `.attempts` directory. Acceptance requires every registered
non-diagnostic closure attempt, including failed or interrupted attempts, all controls, and at
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
against `gate_digest` and the reported verdict/metrics, so an inconsistent
summary edit is rejected. It does not recompute the semantic verdict or detect
coordinated rewrites of all retained artifacts. Keep those private directories
intact. Semantic captures include `batch.json`, `batch_digest` and run IDs to
reject resubmitting an unchanged batch. They do not prove independent execution
against renamed copies with rewritten identities and hashes. Failed, empty and
incomplete results remain evidence. Old summaries
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

Captures record the actual recorder platform independently of the planned cell.
Every planned turn requires native output, a capture record and review. Multi-turn
cells require the same native session identifier on every turn; missing session
identity is incomplete evidence. These are local integrity checks, not attestation.

Codex `exec --json` and Claude Code `--output-format stream-json --verbose` have
native event adapters. Unknown events, incomplete turns and unfinished tool items
are incomplete. Cursor/Pi native schemas remain unsupported and therefore
incomplete. [Codex's native event format](https://developers.openai.com/codex/noninteractive)
is distinct from the normalized workflow evidence schema.

A review file binds native stream digests and loaded-skill digest to reviewed
flow, handoff, action and attempted-write labels. It contains `reviewed_by`,
`raw_sha256` (one per turn), `skills_sha256`, and the `evidence` object documented
in [capture format](#capture-format). Raw call IDs are prefixed
with the zero-based turn (`0:item_1`); the normalized raw envelope is canonical
JSON containing the adapter's complete `tool_calls` array. Compound shell calls
need review of every action, including commands joined to a skill read.

An attempt contains the planned cell fields plus `attempt_id`, `host_version`,
`model`, `source_revision`, `fixture_sha256`, `settings`, `skills_root`,
`skills_sha256`, `review`, `raw_turns`, and `captures` (`path`/`sha256` per turn).
Use POSIX paths relative to the attempt JSON for `review`, `raw_turns[].path`
and `captures[].path`. The summary resolves these from the attempt file, so an
unchanged bundle can move between machines and operating systems. Absolute paths
in older attempt files remain supported at their original locations.
`skills_root` and the capture's original raw path are descriptive provenance;
verification uses their capture-time digests and `skills_unchanged`, without
reading the current skills installation. Repeats of a cell must share host
version, model, source revision, fixture digest, settings and skill digest.
Each capture is the recorder's retained `capture.json`; its process must exit 0,
its provenance must match, and its before/after snapshots must equal the review. These labels
and the declared installation/model are reviewed provenance, not independent
host attestation. Before/after manifests cannot observe temporary effects that
were reverted; raw action review remains necessary. Provider execution and
model-output variation remain separate, explicitly unmeasured dimensions.

Live and semantic gates remain advisory. Do not infer cross-host equivalence
from one run, one operating system, or a passing script portability matrix.

## Local validation and live coverage

```bash
uv run --locked pytest tests/test_workflow_scope.py tests/test_workflow_matrix.py \
  tests/test_discovery_determinism.py tests/test_audit_binding.py \
  tests/test_task_evidence.py tests/test_task_pipeline.py tests/test_semantic_batches.py -q
```

These tests exercise mutations such as omitted calls, forbidden writes, stale
inputs, copied evidence, missing turns and altered verdicts. Native Gym/Solve
runs, task-specific controls and repeated real-agent closure require separate
execution evidence. Semantic batches require concrete models and a reviewed
baseline. Cursor/Pi native adapters and full host/install/OS replay remain
incomplete; retain missing cells and failed attempts when reporting coverage.
