<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Work on existing evaluations

Follow [the setup guide](getting-started.md) to load Eval Author in your coding
agent. Choose the request below that matches your goal. If you are unsure where
the evaluations live, start with the setup guide's inventory request.
Review the [runtime requirements](getting-started.md#requirements) for the
operation you choose.

## Find local tasks without running them

The shared [task search](../skills/eval-author/references/task-search.md) finds
Harbor task directories and NeMo Gym workload candidates from provider layouts
and Eval Author's existing extension markers.
It uses Python's standard library and reports stable paths, marker files, and
search limits without importing providers or executing repository code. Matches
are inventory candidates, including incomplete scaffolds and legacy configs;
use readiness checks only when you want to validate or execute them.

## Check readiness

```text
Check whether the NeMo Gym or Harbor evaluations in this repository are ready to run.
Explain any blockers and show the command for each suite you check.
```

Eval Author uses provider-native checks: Harbor validates its configs and tasks,
and NeMo Gym validates native manifests and runs verifier fixtures. Discovery also
runs a bounded sample of Harbor tasks with Oracle. It saves findings and evidence
in `.eval-author/discovery.md`.

Verified checks require the corresponding runtime and execution dependencies.
The report explains what passed, failed, or remains unverified for each suite.
These checks do not measure the actual agent's performance or evaluation quality.

Discovery can also describe other evaluation formats and their documented run
commands. Eval Author does not convert those suites; use the
[conversion handoff](#convert-a-suite-for-expansion) when expansion requires a
supported format.

## Convert a suite for expansion

If expansion pauses because your suite uses an unsupported format, Eval Author
provides the [canonical conversion prompt](../skills/eval-author/scripts/conversation_flows/onboarding_templates.py).
Paste it into a separate coding agent chat in the same repository. Fill in the
suite path, current framework, and target format: Harbor or NeMo Gym.

Return to Eval Author with the converted suite paths and validation results.
It checks the actual format and any remaining prerequisites before resuming
expansion.

## Audit coverage

```text
Audit these evaluations against my agent's intended behavior using the
available traces. Explain what is covered, what is missing, and what the
evidence cannot establish.
```

A full audit quietly checks for saved progress before choosing its introduction.
For a new audit, choose one of two approaches before setup checks:

- **Review tasks only.** Review scenarios, fixtures, and verifiers against
  the agent's intended behavior (`ETHOS.md`). This needs no saved runs.
- **Full review with measured evaluation runs.** Review tasks, then analyze
  saved agent runs to validate that intended behaviors and tools are covered.
  Uses additional tokens.

Both approaches hold each scored check to the standard Eval Author applies to
tasks it creates: it must trace to the task instruction and a justified expected
value. Checks for requirements the instruction never states, or expected values
that contradict their source, are reported as false rejections. Expected values
with no identifiable source are reported as evidence limits.

An approach already named in your request carries forward. The choice replaces
the initial readiness question. Resuming an audit keeps its saved approach and
pending decision. Saved setup or Ethos from creating evaluations can be reused,
but a new audit still needs an approach.

Fresh audits show the welcome, checklist, and approach question without a
separate opening acknowledgment, then follow the
[shared setup and Ethos confirmation](getting-started.md).
If you have already completed those steps while creating or improving evaluations,
ask to audit the suite directly, including in a new chat in the same repository.
When saved progress exists, separate audit requests acknowledge it, followed by
the next result or unresolved decision without another readiness question. Routine
setup checks run without step-by-step narration until the first result or decision.
Longer measurement or aggregation work can include useful progress updates.
Ask for execution details when
you want them. A direct handoff from creation or improvement in the current
conversation continues without repeating the welcome, as do replies within an active audit.
Eval Author reuses your saved confirmations, checks only missing or changed
audit requirements, and continues at the first unfinished audit step. It reviews
Ethos again when the agent, intended behavior, or document has changed.
Previously selected suites and runs do not need another selection; when those
inputs are settled, the next review is the proposed audit checks. Measured audits
require traces. Missing traces pause measurement while preserving earlier work.
Task review finishes once its findings are recorded, with run coverage labeled
unmeasured. A saved audit without an approach defaults to task review.
Both approaches save the Audit coverage report.
If saved selections refer to older work, Eval Author explains their source and
how they differ from current recorded tasks or runs, then asks which set you
intend to audit. Missing-path findings identify the particular selection that
was checked, keeping other current work distinct.

A repository with no saved progress starts normal onboarding. If saved progress
is damaged, unreadable, or belongs to another repository, Eval Author pauses
audit checks to protect earlier decisions. It explains the error and offers a
specific recovery step, such as restoring access, restoring a known valid copy,
or confirming an intentional repository move. After recovery, it reloads saved
progress and resumes the audit.

For measured audits, Eval Author checks `.eval-author/jobs/` for completed agent
runs when trace availability is unresolved. It checks run metadata and trace-file
presence, then offers relevant candidates when you select audit sources.
It checks known task matches first, then newer jobs, and continues in batches
when needed. Logs and other trial files do not consume the job search budget.
It reads trace contents only after you select the source. An empty or incomplete
lookup leaves availability unresolved rather than proving no traces exist.

If saved trace availability is unresolved or traces are absent, the prerequisite
response explains the required action and links the audit skill instruction
that requires the pause. Unknown availability and confirmed absence are shown
separately; neither is a claim about measured coverage.

Milestone and source-selection questions hand the conversation back
to you. Work stops while the answer is pending, including background work and
delegated reviews. Independent work can finish before a checkpoint or continue
after you explicitly defer the missing input and agree to that work; asking a
question while continuing it in the background is not a check-in.

After Ethos is confirmed or reused, Eval Author shows the existing evaluations it found,
links their suite and case locations, and explains what they test. It asks you
to confirm which suites belong in the audit, including any additions or
exclusions, and waits before moving to traces. Suites you already explicitly
selected are reused without another confirmation. Discovery alone does not
select a suite, and giving a trace location does not confirm the suite list.

Once the evaluation scope is confirmed, it explains that traces record the
agent's actual actions during a run, letting the audit check what was exercised
beyond the test definitions and pass/fail scores. It then asks you to select
the trace source and location before searching beyond the retained-job metadata
or reading trace contents.
This also applies to using traces to identify runtime tool names. A source you
already explicitly selected is reused. If the location is unknown, it helps you
choose a bounded place to look before searching.

At **Define what the evals should cover**, review the proposed checks in the
[Audit coverage report](results.md#coverage-audits). Approval allows measurement
of the agreed traces without another readiness question. After measurement,
read the findings in that same report. You don't need to approve them.

**Shared progress** (`.eval-author/.working/journey-state.json`) saves suite and
trace selections separately, review decisions, measurement status, pending
questions, and the next action. Returning to an audit, including after another
workflow, resumes its pending decision or first unfinished step. Requests only
to validate a specification or measure or aggregate coverage stay within that scope.
Audit evidence paths are relative to the repository. Each case or run must fall
within its selected suite or trace location.

Changes to the agent, Ethos, selected scope, specification, cases, verifiers, or
traces reopen check review and dependent measurement and findings. Changes to
report content or its supporting evidence also make recorded findings stale until they are regenerated. Saved
progress therefore reflects the current inputs when you resume.

Coverage measurement compares that specification with recorded traces
(normalized NeMo Compass traces, or ATIF, MLflow, or NeMo Gym traces), each a structured record
of an agent's interaction. Measurements are saved
under `.eval-author/audit-measurements/`. **Coverage measurements (JSON)**
(`.eval-author/audit-coverage-report.json`) is the file of generated results
aggregated over the selected runs, when available.

Eval Author generates the Audit coverage report and links available evidence.

The Audit coverage report can be prepared before measurement and updated as
evidence becomes available. Without usable traces, you can establish the audit
proposed checks and report, but coverage is **unmeasured**, not 0%. Measurement
is deferred only after you choose independent report work. The unchecked
measurement step includes the reason, such as “deferred — selected trace
directory is missing,” including in later
check-ins and the final review. If you have no evaluations yet, Eval Author can define
the specification and then offer the [first-eval workflow](first-evals.md).
An uncovered item can reflect a missing scenario, an agent failure, or
insufficient evidence. Coverage measurements (JSON) alone does not identify
which explanation applies or establish overall agent quality.

The final checklist step includes generating the Audit coverage report and
presenting its current findings; it does not wait for an acceptance reply.
Whenever a session creates or updates the report, its final response says so and
links it, including at intermediate checkpoints. On resume, Eval Author reconciles the report with changed inputs
before presenting its findings as current.

The review includes recommended changes, their expected behavior, supporting
evidence, and which next actions Eval Author can perform. The findings need no
approval, so the audit ends with a concrete next-step offer and no question.
Recording current findings can complete a task review or fully measured
audit while you leave the next action undecided; it does not start implementation.
A deferred or partial measurement remains unfinished after findings review.
Selecting a recommendation continues to the supported authoring path, reusing
the audit's recommendation and settled decisions. Eval Author explains any
implementation limit without offering to prepare the same recommendation again.

The offer follows the evidence. With no evaluations, it points to the
[first-eval workflow](first-evals.md). With insufficient evidence, it recommends
the needed measurement first; any candidate task proposals are explicitly
unmeasured. If the findings do not support more tasks, it explains the useful
alternative, such as addressing an observed agent failure or gathering specific
evidence.

See [audit results](results.md#coverage-audits) for report editing, saved artifacts,
and coverage limits.

### Check explicitly selected retained controls

For an existing Markdown audit report, the read-only verifier evidence checker
can read an explicit artifact selection instead of searching evidence directories:

```bash
python <skills>/eval-author/scripts/audit_verifier_evidence.py \
  --repo <repository> --report <audit-report.md> --task <task-directory> \
  --selection <private-selection.json>
```

The selection has `artifacts` and `route` fields that follow the
[shared identity and declared-route contract](../skills/eval-author-rubric/references/identity.md).
Select receipts with role `receipt`, manifests with role `manifest`, and every
linked native result explicitly. Do not combine this mode with `--content`,
`--evidence-dir`, or `--source-root`. Existing structured-content and directory
modes are unchanged. None of these checks applies experimental rubric scores.

The JSON result preserves shared `identity` and `route` diagnostics alongside
retained-control classifications. `valid` still means findings are accounted for.
`preflight_success` also requires resolved identity/access/route checks and
current passing retained controls for every selected task. Missing, denied,
unresolved, stale, or unproven evidence cannot pass merely because a report explains
it. The selected-mode command exits nonzero when either condition fails.

Add `--integrity` to report receipt contradictions, required-control states,
and superseded manifests. See
[verification-integrity checks](determinism-checks.md#verification-integrity-checks).

This is a bounded retained-credit check, not full workflow readiness or task
qualification. It does not execute controls, prove solver-role isolation, or
attest which scorer ran. The existing native task-revision check still hashes the
selected task tree. The checker does not rediscover evidence files.

### Measure coverage with an explicit identity selection

For an explicitly selected audit trace, opt in to identity and declared-route
checks before writing coverage:

```bash
python <skills>/eval-author-audit/scripts/audit_spec/measure.py \
  --audit <audit.md> --trace <selected-trace.json> \
  --task-id <task-id> --run-id <run-id> --evidence-class agent_under_test \
  --out-dir <private-measurements-directory> \
  --identity-root <repository> --identity-selection <private-selection.json>
```

Supply `--identity-root` and `--identity-selection` together. The
selection uses the same [artifact and route contract](../skills/eval-author-rubric/references/identity.md)
as the retained-control checker. Include the measured trace with role `trace`.
Its selected path and digest must match the trace actually measured. With
`--trial-dir` instead of `--trace`, select that trial's `agent/trajectory.json`.
Existing task/run identity and evidence-class options remain in effect.

Both selected consumers require source-bound declared dependency closure, not
just a dependency map asserted in the selection. Select the dependency source
and follow the shared contract. Unknown or mismatched routes cannot authorize
measurement. This verifies the selected declaration, not arbitrary code imports
or which scorer executed.

Selection files must be regular local files, at most 1 MiB, with no symlink
traversal. Selected trace files can contain at most 2,000,000 bytes. Other selected
artifacts retain the 1 MiB limit, within a 16 MiB identity-read budget. Root aliases
are resolved at entry; symlinks below that root remain invalid.
If selected evidence is missing, denied, unresolved, or invalid, the
command exits nonzero without writing new coverage. It does not produce zero
coverage. Without either identity flag, ordinary coverage measurement is unchanged and does not
activate rubric assessment. See [identity-aware coverage results](results.md#identity-aware-coverage-results)
for the retained envelope and its limits.

## Propose new evaluations

```text
Use this Audit coverage report to propose the next evaluations to add or improve.
Rank the recommendations and explain the evidence behind each one.
```

Eval Author considers the Audit coverage report, underlying measurements,
traces, and existing cases. Recommendations may include a new scenario, a
stronger check, retaining a failing case as a regression test, or gathering more
evidence. Each recommendation explains its priority, expected behavior, how a
verifier would check it, and supporting evidence.
Recommendations distinguish agent failures from verifier defects and ungraded
runs. Applicable controls must be executed before a task is described as screened.
Missing evidence leaves screening unproven. Proposals expand the existing
next actions in the Audit coverage report. Request this directly when you want
additional planning; it is not a required follow-up to an audit.

See [audit results](results.md#coverage-audits) for where protections and later
findings belong.
An explicit planning request produces recommendations. Selecting an existing
audit recommendation reuses it. Construction design, validation, and execution
keep their applicable scope and authorization requirements. The supported
authoring paths are summarized below.

For supported additions, Eval Author saves the task plan and your answer at the
existing scope review. Returning to the conversation keeps that plan and any
unanswered scope question. Once scope is agreed, the selected authoring workflow
continues without another approval step. Permission to run the agent remains separate.

During creation, progress shows every agreed task, which drafts exist, and which
verifier checks passed, failed, or remain incomplete. It preserves partial work
and blockers when you return. Changes to task files or evidence reopen affected
validation; passing verifier checks does not measure your agent's performance.
Once all agreed tasks have current passing checks, the authored tasks are ready
for review. Running the agent requires its own authorization.

### What Eval Author can create or improve

| Work | Current support |
|---|---|
| Build a first NeMo Gym or Harbor suite | Supported, including task instructions, fixtures, environments, graders, and validation |
| Add tasks to a starter suite created with Eval Author | Supported; no audit or measured tool gap is required |
| Create a task from an audit finding | The audit-gap creation workflow supports one eligible, measured tool gap at a time |
| Automatically create tasks for capability or failure-case audit gaps | Not supported by the audit-gap generator; the audit can recommend these changes |
| Create a task candidate from a recorded agent interaction | Experimental; requires privacy review, candidate admission, and native qualification |
| Revise or repair tasks and graders within Eval Author's existing authoring workflow | Supported; affected validation and acceptance evidence must be renewed |
| Recommend stronger cases, grader repairs, regressions, or additional evidence | Included in the audit |
| Apply arbitrary audit-selected revisions to existing suite cases or graders | A general implementation workflow is not yet supported |

Creating a task includes authoring and validating its grader. The repair workflow
also supports fixing verified defects in Eval Author tasks and revalidating them.
That support does not establish a general editor for every existing evaluation
suite. For a selected audit recommendation, Eval Author identifies the applicable
authoring path or explains that applying the change is not yet supported.

For the complete workflows, see the [discovery](../skills/eval-author-discover/SKILL.md),
[audit](../skills/eval-author-audit/SKILL.md), and
[task-creation](../skills/eval-author-task-create/SKILL.md) skills.
