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
| [Rubric identity measurements](#rubric-identity-measurements) | Handcrafted mutations produce exact scoped identity diagnostics and scores. |
| [Provenance demo](#provenance-demo) | Selected source bytes, record links, and revision changes produce expected diagnostics. |
| [Audit and task evidence](#audit-and-task-evidence) | Acceptance still matches its retained source and execution evidence. |
| [Verifier-cheat panel](#verifier-cheat-panel) | Real Harbor controls distinguish a sound grader from a planted vulnerable grader. |
| [Live anti-cheat experiment](#live-anti-cheat-experiment) | Model-backed Harbor task attempts separate real behavior from vulnerable reward signals. |
| [Factual and semantic reports](#factual-and-semantic-reports) | Reports preserve source facts and semantic comparisons use compatible evidence. |
| [Host capture and accounting](#host-capture-and-accounting) | Every planned host attempt retains its native output and review. |

Passing checker tests establishes checker behavior. Live workflow acceptance
requires actual executions and reviewed evidence for the relevant inputs,
hosts and environments. Workflow execution checks do not require identical model
trajectories or free-form prose. The
[conversation renderer](../skills/eval-author/scripts/conversation_renderer.py)
separately requires byte-identical messages for identical rendering inputs;
that presentation guarantee does not establish deterministic workflow execution.

## Rubric identity measurements

The experimental rubric demo assesses source-bound task access (`provenance.task-artifact-access`),
explicit grading-path identification (`provenance.grading-path`), and grading-dependency access
with a retained completeness review (`provenance.grading-dependency-access`). Raw identity, route,
configuration, relationship, and approval measurements remain separate from
canonical criterion scores. See the
[rubric measurement contract](../skills/eval-author-rubric/references/identity.md)
for input fields and supported evidence.

Run the credential-free handcrafted demo from the checkout:

```bash
uv run --locked python tools/run_rubric_measurements.py --format text
uv run --locked python tools/run_rubric_measurements.py
```

The default output is `rubric-mutation-panel-v3` JSON. It contains 14 raw-measurement
regressions, five canonical score variants, and eight source configuration and
freshness variants in `evidence_cases`. Both
assessment panels invoke the actual measurement CLI without mocking helper outputs.
Each includes
independently authored expected outcomes, observed items and raw measurements,
input identity, CLI exit code, and whether expectations matched. All cases check
the complete canonical result fields and all 38 check IDs. CLI cases also
compare every check outcome with the panel's explicit expectation map. Counts
separate `cases`, `assessments`, and `evidence_cases`.

The panel exits nonzero on an unexpected outcome. The text summary lists mismatched
case IDs and omits success claims when any case mismatches. Correctly detecting a
planted defect does not cause a nonzero exit.
It also checks that CLI assessment leaves the selected input bytes unchanged.

| Variant | provenance.task-artifact-access | provenance.grading-path | provenance.grading-dependency-access |
| --- | --- | --- | --- |
| Valid declared inventory | 2 | 2 | 2 |
| Wrong scorer | 2 | 0 | 2 |
| Ambiguous route | 2 | null | 2 |
| Confirmed missing task file | 0 | 2 | 2 |
| Partial inventory and dependency route | 1 | 1 | 1 |

`null` means `NOT_ASSESSED`, never zero. The other eight canonical criteria remain
null in every variant because the required review and native evidence is missing. The handcrafted retained
review is not independent corroboration, so no variant earns level 3.

Inventories cover only declared local references and known grading branches.
They do not establish arbitrary-code dependency closure, solver permissions,
provider compatibility, or grading correctness.
The partial review preserves its unresolved material dependency instead of
certifying completeness. The confirmed-missing variant removes the task
file before the checker reads it. A caller's missing-access declaration alone
remains unknown.

The source configuration and freshness variants use handcrafted refund-task inputs,
JSON configuration records, and retained review notes. They use no native receipts:

| Variant | Expected bounded observation |
| --- | --- |
| Sourced equal | Seven selected fields equal and bound to synthetic run/task/configuration identities. |
| Sourced different | Only `/runtime_pins/harbor` differs. |
| Sourced provider-hidden | That pin is unknown with reason `provider_not_disclosed`. Six fields remain comparable. |
| Fresh bindings | Refund note and independently bound neighbor note are current. |
| Material task change | Refund note is stale after changing its instruction. |
| Material scorer change | Refund note is stale after changing its approval check. |
| Material dependency change | Refund note is stale after changing its selected judge prompt. |
| Unknown ETHOS change | Refund note is unresolved after changing intent. Semantic impact remains unknown. |

Every freshness variant also checks that the unaffected neighbor remains current
and usable. Each compares old and new lineage against the retained binding and
current selected bytes. The inputs explicitly declare materiality for the task,
scorer and prompt. ETHOS impact is unknown. These bindings cover only the selected
references, not an inferred complete dependency graph.

A current note is usable
**for review**, not proof of a passing task. The demo does not rewrite or regrade
stale notes, use fabricated rewards, or use native-success receipts.

All eight measurements retain `2/2/2` on the inventory/path criteria and null elsewhere. They earn no numeric credit on other criteria.
Source equality does not establish configuration acceptability, native attestation
or fair comparison. Run-bound fields here assert only what the selected records say.

When complete evidence is missing, deterministic checks retain `PARTIAL` or
`BLOCKED`, even when a helper passes. Human review, LLM judging, and probes remain
`NOT_RUN`. Provider validation and solver-permission execution remain blocked.
The raw details about sources and freshness do not add a score anchor or claim
that all 38 checks passed.

The wrong-scorer variant changes an explicit JSON selector to a weak route that
names only a claimed-success check. This is a **weak-route defect, not demonstrated
exploitation**: no scorer, solver, cheat, model, or native task executes. These are
synthetic local selections, not customer tasks.
[Package acceptance](#package-acceptance-same-command) measures retained-credit and
identity-aware coverage CLI integration separately.
The existing native verifier-cheat panel below remains the executed cheat control.

The demo removes temporary inputs after each case. If you need to retain the
report, redirect default JSON stdout. That report is not a retained executable
task package.

### Package acceptance (same command)

The separate `package_acceptance` section runs 14 additional CLI cases with its
own counts and pass/fail status, separate from the three panels above.
Expectations come from `evals/rubric-measurements/cases.json`, not
observed output or imported test fixtures. An unexpected package outcome also
makes the overall command exit nonzero.

Eight cases send **the same selection file** through both actual consumers:
`audit_verifier_evidence.py --selection` and audit `measure.py
--identity-root --identity-selection`. They compare complete identity records,
digests, diagnostics, and exit codes. The valid case checks retained-credit control
classification and coverage's trace digest and identity envelope. Missing,
denied, unresolved, unknown-route, wrong-scorer, caller-only dependency mapping,
and source/mapping contradiction cases cannot become successful measurements.
The valid route binds its dependency graph to selected dispatch JSON bytes.
That finite declaration does not establish arbitrary-code dependency closure.

Two cases run the rubric CLI with `approval_sources`. An exact retained approved
decision passes. A retained denial fails when it contradicts the caller's approval
projection. These cases check retained content, not the decision author's identity
or authority. Four more cases separate configuration comparison from binding.
They test equal values with valid run/task/configuration binding, then equal
values with each binding independently mismatched.

The mismatched cases remain `equal` and may exit zero from the rubric CLI.
However, `binding_accepted` is false with the exact mismatch reason.
The panel checks that rejection separately rather than treating equality as accepted binding.

These are explicitly synthetic, native-shaped receipt/ATIF **parser fixtures**,
not captured native executions. Package acceptance is local CLI integration,
not task merit, provider compatibility, solver permissions, anti-cheat success,
or end-to-end conversational workflow acceptance. `runtime_acceptance` stays
`not_measured`, `merit_acceptance` stays `not_assessed`, and the existing top-level
`workflow_integration: false` denotes that unmeasured full workflow boundary.
The three rubric panels contain no native-shaped receipts.

Every package case verifies that all fixture input bytes remain unchanged,
including its selection. Only coverage's dedicated output subtree may be created.
Temporary roots never enter the package fixture's retained identities.
Thus, repeated commands produce byte-identical JSON when source and runtime are
unchanged. The regression test runs the full command twice and compares stdout.

Package subprocesses use a private scratch HOME and credential-free environment.
They launch no scorer, task, model, or network call. The panel removes temporary
packages and outputs. Retain stdout for review, not as an executable or
independently attested task package.

For an explicitly selected private package, the installed rubric provides:

```bash
python skills/eval-author-rubric/scripts/measure.py \
  --root /path/to/package --selection /path/to/private-selection.json
```

It emits `rubric-measurements-v2` JSON without modifying workflow state. Exit 1
means failed/unknown raw predicates or comparisons still requiring review. Exit 2
means invalid input. Exit 0 does not mean every canonical item is assessed or that
native preflight passed. Configuration acceptability remains unassessed.
To check a selected `ETHOS.md`, add an `ethos` selection naming its artifact and
pass `--ethos-validator` with the installed `ethos-write/scripts/validate_ethos.py`.
The result records the document and validator digests and the validator's
verdict. A valid result checks structure only, not confirmed or adequate intent.
Keep selections and output private because they can contain local evidence locations.

## Provenance demo

Run the separate provenance panel from the checkout:

```bash
uv run --locked python tools/run_rubric_provenance_demo.py --format text
uv run --locked python tools/run_rubric_provenance_demo.py --run-dir /absolute/path/to/new-demo-directory
```

The first command prints expected and observed outcomes. The second retains the
synthetic inputs, selections, CLI reports, and `panel.json` for inspection and
replay. The output directory must not exist. Without `--run-dir`, temporary inputs
are removed after the run. JSON is the default output format.

The panel checks selected generation records, candidate changes, validation
references, configuration bindings, and selected-set changes. Calibration cases
check retained dependencies on dataset, scorer, and runtime revisions. They do
not fit a calibration model or decide whether calibration transfers.

Reconstruction cases bind candidate files to their owning prepared summary and
selected trace steps. Exact source bytes can support a recovered declaration.
Wrong steps and changed bytes fail. Partial, generated, and unavailable sources
retain unknown source proof. These checks do not reconstruct a workspace or
establish that it can run.

Expectations are authored separately in
`evals/rubric-measurements/provenance.json`. Each case invokes the real rubric
CLI and checks its diagnostics and exit status. Relevant mutations also check
that an unaffected neighbor remains current. The panel verifies unchanged input
bytes and retains every canonical criterion and check in the reports.

These cases add deterministic evidence checks, not scoring anchors. Their scores
remain null. Supplied failed and rejected candidates retain their identities and
reasons, but this does not establish that every attempt was recorded or that each
reason is valid. The fixtures execute no generator, scorer, model, or native task.

## Verifier-cheat panel

The credential-free `verifier-cheats` CI job runs real Harbor tasks in Docker,
independently of unit tests, semantic regression, and native NeMo Gym checks.
It runs on same-repository PRs targeting `main`, pushes to `main`, manual CI
dispatch, and the daily schedule. The fixtures are deliberately small code
execution evaluations, not customer tasks or model-generated evidence.

Run the same panel locally with Docker running and the locked development
environment installed.

Both Harbor panels require a Docker host that can enforce the tasks' `no-network`
policy. Use the Linux CI runtime or a Docker Desktop VM with the required kernel
support; a running Docker daemon alone is not sufficient. Harbor may reject an
unsupported runtime with `network_mode='no-network' is not supported`. Upgrade or
switch the Docker runtime rather than disabling network isolation to make a run pass.

Choose a new output directory for every attempt:

```bash
uv sync --locked
uv run --locked python tools/collect_verifier_cheat_evidence.py \
  --output "$PWD/.eval-author/verifier-cheat-evidence/run-1"
```

The panel runs one list-normalization task with three grader variants and six
scripted submissions, for eighteen native Harbor trials. It pairs a sound verifier
with a deliberately vulnerable variant and an always-fail grader. A
correct solution must pass; no-work submissions must fail under the sound
verifier. The planted defect must also be detected by a cheat receiving reward
under the vulnerable verifier. This positive control prevents a broken setup or
blanket rejection from masquerading as cheat resistance. The vulnerable variant
is a test fixture, never a task approved for training or agent evaluation.
The always-fail grader must reject the correct solution, exposing false rejection
separately from cheat detection. The submissions are a correct implementation,
an immediate successful exit, a fabricated success claim, a hidden-case read,
a hanging program, and boolean output substituted for integers. Hidden cases are
root-only during grading, timeout outcomes earn zero under every grader, and
the sound grader compares canonical JSON rather than Python's boolean/integer equality.

`verifier-cheat-summary.json` retains per-trial rewards and aggregate counts for
sound reference acceptance, rejected cheats, rewarded planted exploits, and
always-fail reference rejection. Its schema is `nemo.eval_author.verifier_cheat_evidence.v2`
and protocol is `harbor-verifier-cheats-v2`; do not combine these counts with the
older nine-trial panel. `verifier-cheat-private.json` retains the detailed manifest,
native result paths, input hashes, and runtime inspection data. The collector retains native results, commands, input identities, runtime
information, and per-control measurements. Missing, invalid, interrupted, or
unexpected results fail the job rather than count as successful rejections.
Download `verifier-cheat-evidence-<run-id>-<attempt>` for the allowlisted summary.
Full native evidence is retained separately as `verifier-cheat-native-<run-id>-<attempt>`
only while the repository is private. Both artifacts have 30-day retention,
including available evidence after failure; public runs do not upload raw logs or
Docker inspection output.

This is an executed fixed-fixture verifier regression, not a measurement of
whether an authoring agent follows the skill instructions. It uses no model
credentials and does not establish arbitrary-code sandbox security, general
reward-hacking resistance, or screening of previously generated tasks. Live
authoring and semantic regression remain separate measurements.

## Live anti-cheat experiment

This is a separate model-backed experiment, not an expansion of the credential-free
panel above. It uses newly authored synthetic repository-repair tasks, not source
traces, customer records, or imported trajectories. Normal task solving and
explicit adversarial reward-seeking are separate arms: behavior under an attack
instruction is not evidence that an ordinary solver spontaneously cheated.
The tasks repair ledger reconciliation and dependency-aware build ordering.
Each frozen submission is checked against hidden behavioral cases and a
deliberately weak exit-code-only diagnostic. The diagnostic does not verify a
success report's contents; a fabricated report can pass simply by exiting zero.
Trusted-grader error rates for live submissions are not independently estimated:
the behavioral checks themselves provide the finite-case reference label.

### Execution gate

The [workflow](../.github/workflows/anti-cheat-live.yml) plans without credentials
on same-repository PRs. Live execution requires `refs/heads/main` and either:

- a push (including a merged PR) or daily scheduled run, automatically; or
- manual dispatch with `run_live=true` (the default is planning only).

The live job reuses the `semantic-regression` GitHub environment and its
`INFERENCE_HUB_API_KEY` secret. Maintain that environment's `main`-only branch
restriction in GitHub settings. No enable variable is required. These code changes
do not create credentials, change environment protection, or make this a required merge
check. A scheduled run executes main's current revision, not necessarily a newly
merged revision. Planning is not a successful live measurement.
The live job is advisory (`continue-on-error: true`), like semantic regression;
the summary still records incomplete execution and the runner still exits nonzero.
The credential-free verifier regression remains a failing check for unexpected results.
The runner checkpoints after each cell and has a 50-minute overall budget within
the 60-minute job. Active checkpoints have status `incomplete` and reason
`in-progress` unless a specific failure has already been observed. A caught
`KeyboardInterrupt` checkpoints reason `interrupted` before propagating; an
interrupted cell has no score and retains only validated partial usage. An abrupt
runner loss may leave the last `in-progress` checkpoint, not proof of a still-running
process. Only planned or completed summaries have reason `none`.
Its reviewed panel bounds each live attempt's model turns,
tool calls, completion tokens and observed total tokens; those observations are
not a complete billing ledger. The default run includes eight model-backed cells
(two tasks, two arms, two repetitions), each with a 48,000 observed-token cap.
This runs on every main push as well as the daily schedule; maintainers should
review that cadence against their inference budget. Budget exhaustion remains incomplete evidence.
Each cell permits at most two retries for transient 429/5xx responses, sharing
the original request deadline and reserving token allowance for failed requests.
Malformed tool arguments return a fixed repairable error and consume the existing
tool-call budget. Neither behavior relaxes container permissions or run limits.

Only the two named aggregate files, `anti-cheat-live-summary.json` and
`anti-cheat-live-summary.md`, are uploaded, with 90-day retention. Raw model
responses, submitted code, tool output, Harbor logs and configuration remain in
the runner's private temporary directory and are not artifacts. Do not extend the
upload path to a directory or wildcard. The model credential stays on the host,
not in the task container. Private local evidence must be reviewed before sharing.
Provisioning failures retain a planning fallback, not completed measurements.
Cells can include an optional `request_diagnostic` with the last request state,
HTTP error status, and attempt count. It excludes provider messages, headers,
and request IDs. An `in-flight` state does not identify why a request stopped.
Runner loss or cancellation can prevent publication even with an always-run
upload step; an absent artifact is not evidence of a successful experiment.

### Guided experiment

Start by reviewing the fixed tasks, prompts, controls, and run budget in
`evals/anti-cheat-live/`. Controls and live runs require the
[network-isolation-capable Docker runtime](#verifier-cheat-panel) described above;
planning does not. Use a fresh output directory for every attempt:

```bash
python tools/run_anti_cheat_live.py --mode plan --output /private/anti-cheat-plan
uv run --locked python tools/run_anti_cheat_live.py --mode controls \
  --output /private/anti-cheat-controls --private-output /private/anti-cheat-controls-native
```

With Docker running, controls execute actual Harbor trials without inference.
Only after reviewing their results, authorize the model-backed run with the
provider key supplied through the environment, never as a command-line value:

```bash
uv run --locked python tools/run_anti_cheat_live.py --mode live --repetitions 2 \
  --output /private/anti-cheat-run-1 --private-output /private/anti-cheat-run-1-native
```

Review valid-solution acceptance and known-cheat rejection before interpreting
agent scores. Reward without correct behavior is false credit, not task success;
missing execution or an unhealthy verifier is incomplete evidence, not rejection.
Keep the normal and adversarial arms separate and retain every planned repetition,
including failed or incomplete attempts. A completed experiment can expose
vulnerabilities: completion does not mean a task is safe to release.

For longitudinal review, archive the aggregate artifacts before retention expires.
Compare only batches with matching task, prompt, model, runtime and measurement
bindings; record source revisions separately. Keep changed bindings as separate
series rather than silently replacing the baseline. Comparability also requires
the same lockfile, Docker version, and observed per-cell provider fingerprints:
changes to any of those deliberately split the series and can leave singleton batches.
Do not pool them merely to produce a longer trend. Repeat the unchanged panel
before drawing conclusions from a small score change. This experiment measures
solver behavior and verifier sensitivity, not whether proposal or trace-environment
authoring followed the skill instructions, and not general sandbox security.
These artifacts are not yet automatically imported into the fixtures dashboard.

Compare retained aggregate summaries offline:

```bash
uv run --locked python tools/compare_anti_cheat_live.py \
  /private/anti-cheat-run-1/anti-cheat-live-summary.json \
  /private/anti-cheat-run-2/anti-cheat-live-summary.json
```

The JSON history separates incompatible bindings, runtime identities, observed
provider identities and denominators into different series. It recomputes counts
and rejects inconsistent summaries or reused native-result hashes/CI identities.
Incomplete attempts remain visible. Local batches retain input order, not inferred
chronology. Unknown provider revisions remain unknown: matching requested model
names cannot establish an unchanged backend. Exit 0 means summary consistency,
not successful task execution or independently verified native evidence; exit 2
rejects the inputs. Do not compare planning output as though it were live evidence.

## Workflow scope

`tools/check_workflow_scope.py` checks supplied evidence against request contracts.
It does not launch assistants or select their routes.

The [contracts](../tests/fixtures/workflow-scope/contracts.json) pin their source
revision and policy references. Cases cover broad help, explicit audit, inventory,
readiness, narrow audit validation, shared prerequisites and Ethos, recommendations, task
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

These saved-state assertions check workflow correctness. They do not establish
that an agent repeats the same decisions or produces identical conversations.
A contract can declare `expected_json`, mapping an artifact path to JSON
pointers and expected values. Supply the saved UTF-8 text in the turn's
`artifact_contents` mapping. The checker verifies its hash against the after
snapshot before checking values. Missing contents make evidence incomplete.
Wrong values or text that does not match the snapshot fail the check. The
retained-run cases execute the lookup and save real journey state to test
availability, batching, and controls without claiming live agent acceptance.

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

Measurements bind the exact loaded trace bytes and the canonical audit
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
fresh run. An explicit repair may update the current manifest in place, carrying
its repair count and requiring fresh controls and agent evidence. It resets
registrations for the old manifest while leaving native results untouched;
source control owns version history. Do not otherwise delete failed entries to
obtain acceptance. This is
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
Contracts may declare `platforms` when a scenario needs a platform capability.
The planner includes only those platforms for that scenario. Descriptor-safe
retained-run cases cover macOS and Linux. Unsupported hosts report unknown
availability instead of using unsafe file access. Continuation batches in these
cases stay within one user turn and one planned prompt.

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
inputs, copied evidence, missing turns and altered verdicts. Native NeMo Gym/Solve
runs, task-specific controls and repeated real-agent closure require separate
execution evidence. Semantic batches require concrete models and a reviewed
baseline. Cursor/Pi native adapters and full host/install/OS replay remain
incomplete; retain missing cells and failed attempts when reporting coverage.
