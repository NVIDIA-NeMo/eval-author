<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Read your results

Eval Author saves reports and evaluation artifacts in the repository you are
working on. Start with the report for your requested workflow, and read its
limitations before deciding what to change.

## Experimental rubric artifacts

Rubric selections use `rubric_scope` for source-backed inventories. Package demo
observations use `retained_credit`, `retained_credit_controls`, and `coverage` to
identify their consumers. The provenance panel uses `rubric-provenance-demo-v2`
with `rubric-provenance-expectations-v1` expectations.
Criterion IDs name their area, such as `provenance.grading-path` or
`task-contract.scoring-authority`. Each check ID adds its method, such as
`.deterministic-1` or `.llm-judge`. Result rows and check results keep the source
rubric's IDs in `source` and `source_id`. The `skill_id` field is no longer filled
from the source rubric.
Rubric definitions use `4.1-draft`, measurement reports use
`rubric-measurements-v3`, and identity mutation panels use
`rubric-mutation-panel-v4`. The overall showcase, criterion scoring demo and task
contract demo use `rubric-showcase-v2`, `criterion-scoring-demo-v2` and
`task-contract-demo-v2`. Reports preserve canonical criterion and check order.
Update integrations to these current names and rebuild older demo outputs rather
than relying on former field or schema names; no compatibility aliases are kept.
Keep old source-bound receipts under their original identifiers instead of
renaming or resealing them as evidence for the new source bytes.
See the [rubric measurements](determinism-checks.md#rubric-identity-measurements)
and [provenance demo](determinism-checks.md#provenance-demo) for commands.

### Rubric table results

The [one-skill Harbor assessment](determinism-checks.md#one-skill-harbor-assessment)
prints a Markdown table. With `--output`, it saves three private files:

| File | Content |
| --- | --- |
| `results.md` | Criterion rows, check counts, evidence links, gaps, and next actions. |
| `results.html` | Self-contained browser view with the same results and expandable evidence. Open this file for desktop or mobile review. |
| `assessment.json` | `rubric-table-v1` bundle with the exact selection, root, definition digest, and emitted measurement report. |

`--task` selects one task directory and includes every populated criterion.
It declares `reference_scope: task_directory` for bounded checks of missing
required references. An absent required COPY source is reported as missing.
Partial selections without this scope keep unselected references unresolved.
`--selection` uses an existing supported measurement selection and retains its rows.
Neither mode creates an aggregate quality score. The table keeps these fields separate:

- Expected score: `Not supplied`. The assessment never reads mutation expectations.
- Observed rubric score: the emitted criterion score, or `Unassessed` for null.
- Task credit: `Not measured here`. A rubric score is not a task reward.
- Controls run: completed check records divided by retained check records, followed by each outcome count.
- Evidence: the reported strength and a link to full evidence details.
- Human action: the review actions required by the emitted checks, or an explicit statement that none is specified. A required action is not a completed approval.

Only `PASS` and `FAIL` count as completed checks. `PARTIAL`, `ERROR`, `BLOCKED`,
`NOT_RUN`, `UNKNOWN`, and other unassessed states remain separate. These counts
are not native trial totals or proof that every required control ran.
`Not applicable` requires a recorded task-contract reason. It is distinct from
missing evidence. Case labels are short directory names, not source identities.
Full source IDs, exact quotations, check records, findings, approval references,
method qualifications, and gaps remain in expandable structured details.
Report-level measurements and unknowns have a separate details section.

Recorded source reviews appear in both the main table and the details, including
contradicted decisions. A recorded name or selected JSON cannot establish authority.
A separately accepted source-review method can qualify the four supported Discover
source checks. The table names its trust basis and keeps the agent identity.
Accepted checks do not ask for the same owner decision again. Pre-acceptance gaps
and the original packet remain labeled historical evidence. Other missing review
or native evidence remains Unassessed.

The HTML view uses five columns on desktop and stacked criterion rows on narrow
screens. Scores, blockers, and human actions stay visible without horizontal
scrolling. Evidence links open their matching details section. This browser view
is the supported responsive renderer. Markdown hosts may render tables or raw
HTML differently. No external scripts, fonts, or styles are loaded.

Use `--verify-existing` with the original root to compare all three files against a
fresh measurement. Verification rereads selected sources and rejects changed
scores, rows, observations, definitions, recorded reviews, Markdown, and HTML. In task mode, it also
rebuilds the directory inventory, so added or removed files invalidate the result.
Verification does not execute retained task code or change the saved bundle.
Regenerate older two-file bundles in a new output directory. Do not overwrite
historical evidence to make it match the current renderer.

Keep the original source location and frozen evidence together. The current format
is root-bound, not a portable archive or authenticated attestation. Coordinated
rewrites of sources, selections, and reports do not prove historical execution.
The verifier checks correspondence with current sources, not reviewer identity,
independence, solver permissions, or the truth of recorded human claims.

The ordinary table also runs bounded local scoring controls for the exact admitted
refund source family through `rubric_refund_contract_evidence.py`. The
`measurements.refund_contract` records bind current source bytes, public conditions,
and control results. Completion-only grading, late approval, omitted ledger entries,
and mandatory completion each establish scoring-authority 0/E1. Clean base and
repaired controls receive no positive score without the required review evidence.
Unsupported or denied source selections keep their ordinary measurements and receive
no inferred semantic zero. Retained replay repeats source admission and local controls.
These checks neither execute selected Python nor supply native boundary evidence.

The source-only path cannot establish an all-3 base. Unassessed rows identify
missing evidence or unsupported consumers. They do not count as passed controls.
In direct task mode, the table does not collect new human decisions or run native
controls. It also has no automatic expectation comparison or with/without-rubric
study. Keep those acceptance gaps explicit.

### Combined refund base assessment

One entrypoint joins the attack panel, boundary panel, runtime-origin observation,
read-boundary panel, and a fresh NOP configuration declaration. It emits the full 18-criterion table
and replays it before returning. From the repository root, use the same exact
Harbor 0.24.0 Python executable throughout:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  skills/eval-author-rubric/scripts/rubric_base_assessment.py \
  --root /absolute/private --task selected/task/task.toml \
  --bundle current-attacks --bundle current-boundaries \
  --registry /absolute/private/attack-registry \
  --registry /absolute/private/boundary-registry \
  --output /absolute/private/new-combined --approve-local-native \
  --docker-plugins /Applications/Docker.app/Contents/Resources/cli-plugins
```

Repeat `--bundle` for an already collected runtime observation or read-boundary
panel. With approval, the helper collects missing scopes through the bundled
credential-free Docker collectors. It does not run a model. The first explicit
registry receives new admissions. Collection includes image resolution and at
most 36 native attempts when no panel is reused. Existing per-attempt recorder
deadlines remain unchanged.

The read-boundary panel tests UID 65534 before upload, after upload, and after
verification. Direct, `/proc/self/root`, and symlink reads share a permitted-input
control. It scans every saved image layer for exact reference-source copies.
Public scoring rules and socket state remain intentional inputs. Readable
verifier source is reported separately because its confidentiality is not
explicit in the task contract. Independent held-out controls remain required for
AC-01 score 3. Omitting this panel keeps AC-01 Unassessed and permits replay of
earlier selections.

Use `--audit-inspect` to check admitted result-to-source bindings at revision
score 2. Use `--approve-audit-exercise` instead to authorize three fixed changes
in fresh private copies: scorer branch, task timeout, and recorded runtime timeout
multiplier. This approval launches no Docker jobs or model runs. Keep
`--approve-local-native` omitted when reusing all three panels.

The exercise checks each copy before mutation, rejects stale native credit,
preserves unaffected panels, and validates a fresh copy of the original source.
Its revision score 3 covers the canonical change-injection and lineage anchor.
Recovery proves reuse after an exact rollback. It does not qualify changed-source
regrading: changed observations have no native score. A changed evaluation still
needs fresh affected native checks before it can receive their scores.

The explicit selection is `"audit_companion": {"mode": "inspect"}` or
`"audit_companion": {"mode": "exercise", "path": "new-combined/audit"}`.
The orchestrator creates it. The latter path is root-relative retained data, never
an executable or an authorization to launch new native work. Replay rebuilds the
fixed probes with current trusted code in temporary private copies. It compares
every retained file and executable bit, including the exact mutations, rejected
reuse, invalidation records, unchanged neighbors and fresh counterparts. It also
revalidates the current source and producer bindings. Saved PASS values and
self-resealed hashes cannot substitute for those checks.

The report links the full `report/audit.json` manifest once. Exercise evidence
stays under `audit/`. Fairness remains Unassessed until the required human review
and matched sensitivity checks exist. The report retains its scoped owner question.
Set `TMPDIR` to an existing private scratch directory for replay copies.

The selected task must match the unchanged base. Each owning validator checks
its own task and producer bytes. The attack panel's explicit zero-balance inaction
companion keeps its distinct task contract and credit scale. Changed or stale
panels fail closed. Preserve them and collect fresh evidence instead of resealing.

The output contains `selection.json`, `configuration/`, any newly collected
panels, and `report/assessment.json`, `report/results.md`, `report/results.html`.
Each panel retains its source identity, attempt counts, operational errors and
scope. Overlapping projections retain failed checks and the lower current score.
They never add rubric scores or silently supersede a defect.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  skills/eval-author-rubric/scripts/rubric_base_assessment.py \
  --root /absolute/private --registry /absolute/private/attack-registry \
  --registry /absolute/private/boundary-registry \
  --verify-existing /absolute/private/new-combined/report
```

Exit 1 can accompany `replay: verified`: the retained assessment is reproducible
but incomplete. Exit 2 means selection, execution or replay failed. Registry
roots are trusted local inputs supplied to the command, never imported from a
report. The ordinary table command can use the same roots through the
path-separated `EVAL_AUTHOR_NATIVE_REGISTRIES` variable.

### Reuse an accepted source review

Ask the same skill to reuse the accepted decision and current evidence. The agent
prepares the selection and runs the command. You do not assemble review JSON or
repeat the human review. Local operator trust needs no SSH key or external service.

For first use, choose an existing private operator store outside task and evidence
inputs. The operator records only your actual packet-scoped answer and explicit
retention consent. Local trust does not authenticate your identity independently.
A process with arbitrary code under your host account can change this store.
File permissions do not isolate it from such a process. Keep it outside solver
mounts, solver tools and selected evidence.

The command takes trust configuration explicitly. With a previously recorded
acceptance, run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  skills/eval-author-rubric/scripts/rubric_base_assessment.py \
  --root /absolute/private --selection /absolute/private/current-selection.json \
  --registry /absolute/private/native-registry \
  --source-review-operator-store /absolute/private/operator-store \
  --source-review-operator-trust localhost-assessment-operator-v1 \
  --output /absolute/private/new-accepted-review
```

Open `new-accepted-review/report/results.html` or `results.md`. The command replays
both views and the complete `assessment.json`. JSON uses compact serialization;
large source inventories are linked rather than repeated in HTML. Evidence and
reader limits are unchanged. This reuse path does not collect native controls or
call a model. Current source bindings, lower anchors, dissent and revocation still
control admission. Accepted source alternatives do not adjudicate fairness, task
semantics or independent native controls.

To replay later, use the same command and trust flags, replacing `--selection`
and `--output` with `--verify-existing /absolute/private/new-accepted-review/report`.
Keep the exact Python executable and set `TMPDIR` to a private scratch directory.
No hidden source-review environment variable is required.

To revoke, ask the assessment operator to revoke the exact packet's retained
qualification. It calls `revoke_operator_decision` in `rubric_source_reviews.py`
and reads back the revocation marker. The original decision stays available for
audit. Replay must reject the old qualified report after revocation. Generate a
new report to see the remaining evidence without that qualification.

Hosts that require authenticated session provenance or signatures can instead
supply `--source-review-session-store` or `--source-review-signed-store`. These
optional routes retain their existing trust checks. Neither a packet-provided
key nor a transcript copied into selected JSON grants authority.

To retain the refund example's already-given Environment intent, ask the operator
to save the exact solver-action and external-state answers for those two criteria.
This needs separate, explicit retention consent: source-review-only consent does
not cover permissions or state interpretation. The same local trust flags admit
these separate records; no SSH key, service, invented identity or repeat semantic
approval is needed. Current source and rubric bindings, native action/error/reset
checks, and component/state observations remain mandatory. Such scoped intent can
support level 2, never independent level 3 or blanket runtime approval. The report
retains the original answers and rationale. To revoke this record, the operator uses
`revoke` in `rubric_general_review.py`; old qualified reports must fail replay and
a new report keeps the historical decision without current authority.

The same `prepare`/`record`/`read`/`revoke` operator API accepts `kind="contract"`
for one adjudication covering all three task-contract criteria and `kind="fairness"`
with the current owning Audit comparison. These records stay outside selected evidence.
The contract request preserves the exact intake binding and reconstructs the approved
public/enforcement spans. Current full coverage, exact fixture computations and owning
scorer controls gate positive scores; discoverability additionally consumes native
solver-UID readable-source observations. Owner approval alone earns no score.

Fairness is scoped to reference versus split-refund scripted policies, not model
performance. The full versioned setup diff must still match; unreviewed differences
cannot inherit approval. A resolved narrow justification supports level 2 without
claiming matched sensitivity tests. Contract blind-discovery and independent-boundary
checks remain unrun where those stronger prerequisites are absent. All six records
are read again before final projection and on ordinary retained replay. Existing
failed checks and operational errors remain visible. A numeric table may still exit
1 because unrelated helper measurements remain unknown; this is not a replay failure.

A complete 18-row table is not an all-3 base or proof of review benefit.

## Case and run catalog

The case and run catalog can be found in `.eval-author/report.html` and the
`.eval-author/README.md` which is a lighter weight version for agents and humans
who prefer markdown. These catalogs provide provide a high level overview of tasks
and runs, and link to the corresponding assets on disc, as well as provide links
to the Harbor viewer for Harbor generated tasks.

To refresh the catalog, ask: “Use Eval Author to refresh the HTML report for evaluations in
this repository.” The report is saved at `.eval-author/report.html`, with Tasks,
Runs, and viewer Settings. The same command maintains the task and run tables inside a m
arked section of `.eval-author/README.md`.

See the [Report generation](../skills/eval-author/references/suite-readme.md#report-generation)
for selected roots, metadata, and CLI usage. The report reads existing artifacts;
it does not run evaluations. Missing task descriptions show `Not documented`.
The Runs table has one row per native Harbor job, using its exact directory name
and path. The reward graph combines scored trial rewards, including partial
credit, using the documented reward range. Execution completion and scored trial
counts appear separately; an incomplete job's score is labeled partial. Missing
rewards do not become zeroes. Without a documented reward range, the numeric
total appears without a graph. Native scores without a documented pass rule
are not labeled passed or failed. Standalone controls and other individual trials
remain accessible in the separate Legacy trials section and are not combined
into inferred jobs.

## Discovery and readiness

Open `.eval-author/discovery.md`. An inventory describes the evaluations found,
what they test, and documented or source-derived run commands. It does not prove
that the suite can run. A report that found no evaluations describes the scope
inspected; it is not a claim about material outside that scope.

A readiness report adds Harbor validation evidence and any blockers. Check which
validation steps completed, which could not run, and what needs attention. A
missing backend or credential is a readiness problem, not a failed agent test.
See [Check readiness](existing-evals.md#check-readiness) for the next step.

## Review generated cases

Use the **Tasks** tab in `.eval-author/report.html` to review tasks at a high
level throughout their lifecycle. Once a task has been created, open its README
or use the Harbor viewer, where available, for a closer look.

**Proposed** tasks have not been created yet.

Standalone drafts use `.eval-author/tasks/<task-slug>/README.md`. Trace candidates use
`<task-dir>/README.md`, and generated batches also have a collection-root guide.
The guide is refreshed after runs and linked in the handoff, including when
execution is blocked.

Task README files allow users to inspect its Input, Behavior under test,
Expected result, and Verifier sections. Input links the instruction and fixtures.
Verifier links the actual verifier code and criteria or judge files. The report's
Expected outcome column shows the opening summary paragraph from Expected result;
open the task README for its detailed assertions and expected values. Existing
sections that begin with a list or table continue to display their full text.

At the accepted-task handoff, create a flat `.eval-author/tasks/` catalog for new
environments; follow the existing final task-directory convention for existing
environments. Future suite commands point to the accepted tasks in that catalog.

Its case inventory explains what each case tests, its input, expected outcome,
and grading criteria, with links to task and verifier details. Readable summaries
and links let you review the cases without reconstructing the conversation or
formatting raw JSONL. Source evidence used to design a case is labeled separately
from task validation controls and actual-agent attempts. A source trace does not
prove that the generated case passed.

Follow the guide's result links and verified viewer commands where available to
inspect saved evidence. Unexecuted cases remain visible as **not run**; missing
traces or other evidence are called out explicitly. Controls that passed do not
stand in for agent results. The same guide saves setup, full-suite and coverage
item rerun commands, and known execution blockers.

## First evaluations

The HTML report and Markdown catalog show proposed, authored, validated, and
executed task totals for your full saved plan. Use the
[generated case review guide](#review-generated-cases) to inspect cases and their
available evidence.

Task controls check the task and its verifier: NeMo Gym's known-correct and negative
verifier fixtures, or Harbor's no-op baseline and reference solution. Results
from running your actual agent are separate evidence of its performance.
A valid task, a working environment, and a successful agent run are distinct
outcomes. See [Build your first evaluations](first-evals.md).

## Open readable cases and traces

Start with the html report or the README.md for artifact paths and commands. Harbor
tasks and recorded jobs use its native local viewer. Report links target the
Harbor 0.24.0 viewer; refresh the report after upgrading to regenerate trial links.
For NeMo Gym JSONL and ATIF, MLflow, or NeMo Gym traces, Eval Author includes an
offline reader that creates a private `report.html`
you can open directly in a browser; see [Local review](../skills/eval-author/references/local-review.md)
for commands and supported formats.

NeMo Gym reports keep model input separate from verifier and other row fields. The
reader shows recorded messages, tool calls and results, and available rewards
and errors, while preserving raw records and identifying missing or unsupported
content. Select NeMo Gym records by their physical line numbers. Keep generated
cases, source evidence, task controls, and actual-agent attempts in reports
with their respective evidence roles; these are caller-supplied labels, not
verified relationships.

Read attention notices before drawing conclusions. A readable case is not a
validated task, and rendering a trace does not establish a successful run. The
HTML contains the selected source data and belongs in the same private,
gitignored workspace as the evidence. Do not publish it merely because it is
readable in a browser.

## Coverage audits

Each audit records its approach. A task review checks existing tasks, fixtures,
and verifiers against intended behavior. A measured audit includes that review
and adds coverage analysis from selected recorded runs. See
[Audit coverage](existing-evals.md#audit-coverage) for the initial choice and
token tradeoff.

The Audit coverage report is the only document you review; other outputs support
internal validation, measurement, and saved progress:

| Artifact | File | Role |
| --- | --- | --- |
| **Audit specification (internal)** | `.eval-author/audit.md` | Intended checks and evidence requirements |
| **Audit coverage report** | `.eval-author/audit-coverage-report.md` | Readable scope, test mappings, findings, limits, and next actions |
| **Coverage measurements (internal JSON)** | `.eval-author/audit-coverage-report.json` | Generated measured results aggregated over the selected runs, when available |
| **Report content (internal)** | `.eval-author/.working/audit/report-content.json` | Saved report text and evidence references |
| **Journey state (internal)** | `.eval-author/.working/journey-state.json` | Saved scope and review decisions, partial progress, pending questions, and next action |

When you switch from creating or improving evaluations into an audit, the
originating journey's `scopes` entry keeps its last question and handoff in
`audit_entry`, with the agent and state revision. This context survives an
interrupted turn. The top-level pending question still belongs to the active
conversation.

Start with the Audit coverage report. It contains the complete proposed checks
and links available supporting evidence. A mapping to an existing test describes what that test is
designed to address; it does not establish that a recorded run satisfied the
audit's coverage criteria.

The report opens with a Summary (including an ADVISORY when verifier cheating is
confirmed and the Evaluation coverage table), then Key findings. Recommended
next actions is a single list, highest priority first, that names each finding or
advisory, the capabilities and tools it involves, and the proposed task.
The Coverage report maps each audit item to its tasks and run evidence, followed by the task
catalog, verifier cheat screening, scope and execution evidence, and
definitions.

The audit report is generated. Ask Eval Author to revise and regenerate it when
you need changes. Direct Markdown edits block refresh.

Older manually written reports remain readable. Ask for conversion before
regenerating one. Conversion preserves the original report and historical
recommendation files.

Reports do not yet compare coverage across audits.

Measurement details live under `.eval-author/audit-measurements/`;
the Coverage measurements (JSON) file combines only the current selected inputs.
The Audit coverage report explains what those results mean. Journey state saves
a measurement summary and references its evidence; failed inputs and missing
methods keep measurement partial.

Saved audit evidence links original traces and selected suite runs to loaded
traces, their `loading.json` receipts, and measurements. Journey state references the saved
selection manifest. New manifests use
`.eval-author/.working/audit/selection-manifest.json`, outside the source
locations. Resumed audits keep their saved manifest path. Changing the original
source or its selection reopens check review even if the prepared trace is
unchanged. Changed prepared evidence reopens measurement.

Without usable traces, coverage is **unmeasured**, not 0%; the specification can
still be completed and the report prepared while measurement remains
pending. Deferral is recorded only after your explicit choice of independent work.
The unchecked **Measure coverage** step states the reason inline,
for example “deferred — selected trace
directory is missing,” so the limitation remains visible in later reviews.

A task review deliberately leaves run coverage unmeasured. It can complete after
its findings are recorded, without a measurement JSON file or a deferred step.
The incomplete-measurement rules above apply to measured audits. Resuming either
approach preserves its choice and reviews changed inputs before reusing results.

Once an audit's findings are recorded, Eval Author saves a snapshot of it under
`.eval-author/.working/audit-history/`, which a new audit keeps. The next report for the same agent compares
capability coverage with the previous audit in the Evaluation coverage table:
percentage points for the coverage ratio and relative percentages for counts. A
changed declared scope or measurement method is explained and its coverage
change is not compared, so a larger scope never reads as better performance.
The report has one Evaluation coverage table, with Current audit, Previous audit,
and Change columns. The first audit shows `No comparable previous audit` above the
same table, with `-` and `Not compared` in its Previous audit and
Change columns. History stays in your checkout.

A covered item has supporting evidence under the audit's declared criteria.
An uncovered item may lack evidence in the measured traces, or its evidence type
may not have been measured at all. Review that distinction before proposing a
new evaluation. Coverage does not establish overall agent quality or prove that
an unobserved behavior never occurs.

The final checklist step, **Generate and review coverage report**, creates or
updates the Audit coverage report and presents its findings. Findings need no
approval. Whenever a session creates or
updates the report, its final response explicitly says so and links it, even if the
audit stops at an intermediate checkpoint. When an audit resumes, changed
inputs are reconciled with the report before its findings are treated as current.

The audit also inspects selected tasks' grading paths and reconciles retained
cheat-control results before recording findings. Findings distinguish verifier
defects, agent failures, rejected valid solutions, and ungraded execution.
Failed controls remain relevant even when a task has no usable agent trajectory.
A later passing control doesn't erase a failure on the same task revision.
Missing or stale evidence leaves screening unproven. These verifier checks do
not establish agent coverage. Planned control definitions and expected rejection
stay in the task README's **Verifier** section. Legacy **Grading** and **Grader**
headings remain supported.

Journey state saves audit decisions and the pending checkpoint across chats
and workflow switches. Recording findings doesn't rewrite the report. See [audit resumption](existing-evals.md#audit-coverage)
for how changed inputs reopen review and measurement.

For the findings and optional next-action handoff, see
[Audit coverage](existing-evals.md#audit-coverage).

### Identity-aware coverage results

When measurement uses the paired
[`--identity-root` and `--identity-selection` flags](existing-evals.md#measure-coverage-with-an-explicit-identity-selection),
the CLI summary and each per-method `coverage.json` include `identity_preflight`:

- `identity` retains selected artifact records, access states, digests, nullable
  revisions, and stable diagnostics.
- `route` retains the declared scorer and dependency checks. `executed_scorer`
  stays null: resolving a declaration is not evidence that this scorer ran.
- `route_request` retains the selection used to resolve that route. Aggregation
  and retained-report verification recompute the route from the selected files
  and reject an envelope that disagrees with the result.
- `subject` keeps the measured `task_id` and `run_id`, including IDs from
  native trial metadata when you supply no override.
- `measurement_allowed` means the selected identity and source-bound declared
  route passed the gate. The measured trace must also match its selected path
  and digest before the CLI writes coverage.
- `preflight_success` remains false even for a successful coverage measurement.
  This envelope does not certify workflow readiness, task quality, or rubric
  criteria.

The aggregate Coverage measurements (JSON) preserves the envelope in each
`input_reports` entry alongside that input's subject and coverage accounting.
Aggregation rejects unresolved or inconsistent envelopes rather than upgrading
them into passing evidence. It validates retained measurement data without
attesting to a fresh native execution. Ordinary reports without the opt-in
fields remain supported.

Identity-aware reports without `route_request` must be measured again before
aggregation. A route envelope alone cannot establish its source binding.

Identity failures leave coverage unmeasured, not 0%. They do not erase earlier
output files. Do not present an older result as the failed attempt's measurement.

## Trace workflows

Intake inspection writes a report under `.eval-author/traces/` with observed
behavior, issues, recoveries, and uncertainty. Review the cited trace evidence
before treating an observation as a general pattern.

The **experimental** trace-to-environment workflow writes a workspace under
`.eval-author/.working/trace-environments/` containing a summary and the evidence for its
candidate and validation decisions. A `no_candidate` result means the available
evidence did not support a task.
A generated candidate can still have an unproven environment or unresolved
software requirements. Read those limits before attempting execution or
[sharing a task](traces.md#sharing-generated-tasks).

Loading a trace into a NeMo Compass `Trace` produces a structured record for
downstream tools. Loading alone does not measure coverage or validate a
runnable evaluation task.

## Saved output

Shared internal progress and agreed scope are stored in
`.eval-author/.working/journey-state.json`; they are not a second report or plan.
When an audit reopens authoring review, legacy scope values that are not JSON
objects are retained under `legacy_context` alongside the scope's review flags.

`.eval-author/.working/` holds temporary construction files and the current state
needed to recover unfinished work. Final task files and READMEs stay at their
documented paths; source control manages task and verifier history. Eval Author
does not create revision archives. You decide how long to keep evaluation
results; they are not automatically expired or deleted.
Routine update payloads, state-response copies, renderer inputs, and polling
responses are temporary. Managed scratch is removed on ordinary completion or
errors; that does not clear existing user files or results.

These are typical paths relative to the repository being evaluated. The shared
prerequisite flow keeps the selected `ETHOS.md` at its existing agent-local or
repository-root path; see [document selection](getting-started.md#load-the-skills).
Changing that selection reopens affected approvals in the current state.
Workflows may stop before producing every artifact when prerequisites are missing.

| Workflow | Main outputs |
| --- | --- |
| First evaluations | `ETHOS.md`, `.eval-author/README.md`, `.eval-author/tasks/`, and run results under `.eval-author/runs/` for NeMo Gym or `.eval-author/jobs/` for Harbor. Harbor suites also get `.eval-author/run.yaml` when the agent integration supports it. |
| Discovery and readiness | `.eval-author/discovery.md` |
| Coverage audit | The four artifacts listed under [Coverage audits](#coverage-audits), plus supporting evidence in `.eval-author/audit-measurements/` |
| Intake trace inspection | `.eval-author/traces/` |
| Trace-derived environments (**experimental**) | A private, gitignored workspace per task under `.eval-author/.working/trace-environments/`, with a root `README.md` for generated-case review and reruns; generated batches also have a collection-root guide. No-candidate outcomes do not create a suite guide. |

[Choose another workflow](../README.md#choose-your-journey) or review
[runtime requirements](getting-started.md#requirements).
