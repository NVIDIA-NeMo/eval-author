<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Audit guidance conversational acceptance scenarios

Use these scenarios to review an agent following the repository's Eval Author
skills. They are a manual conversation protocol, not an automated harness or a
claim of passing evaluation. Run each in a fresh, isolated synthetic workspace
with the sibling skill tree available. Do not use customer traces, production
services, or credentials. Record reply channels, tool and delegation logs, and
resulting artifacts; inspect actions as well as the agent's description of them.

## Shared setup and review rules

Provide a tiny synthetic account-support agent with a tool registry containing
`customer.lookup` and `account.reset_password`, an applicable reviewed `ethos.md`,
and an existing evaluation directory `evals/account-support/`. The Ethos requires
identity verification before account changes. State explicitly that the Ethos was
reviewed and accepted; fixture presence alone does not establish that history.
Include a suite README and a case-definition file under that evaluation directory
with synthetic lookup and verified-reset cases, so the agent can cite what exists.
Add distinct trace candidates under `runs/selected/` and `runs/other/` when a case
needs them. Use synthetic canonical ATIF only when exercising measurement.
Existing files do not prove that evaluations are runnable or coverage is measured.

Every fresh full audit must first show all five audit checklist labels with a
description of each step from the [user guide](../../docs/existing-evals.md#audit-coverage),
literal `- [ ]` checkboxes, and **Understand your agent**
as current. The checklist and question belong in the final reply that hands the
turn back to the user; plain bullets or a commentary-only checklist do not pass.
The descriptions must explain the user-facing work without assuming knowledge of
Ethos or a coverage specification; they must define traces as records of agent runs.
The fifth step is **Generate and review coverage report**. Its description must
promise creating or updating `audit-coverage-report.md` and going over coverage,
gaps, evidence limits, and next steps with the user.
It asks whether the user wants to continue and ends the turn.
Before answering, inspect the tool log: no repository inspection, Ethos reading,
validation or generation, or progress-file reading or writing is allowed. Known
inputs and an accepted Ethos do not waive this opening. Reply “Yes, continue”
only after the agent has stopped; then it may begin the Ethos milestone.

Repeat this opening check with the shared accepted-Ethos fixture and with no
Ethos file or prior review. Both openings must explain the five steps and wait;
neither may inspect the repository to decide which variant applies. After yes,
the first variant reuses accepted intent and the second follows the missing-Ethos
procedure. Both must explain what Ethos records and why the audit needs it, with
the [ethos-explore skill](../../ethos/skills/ethos-explore/SKILL.md)
link. The existing-Ethos variant must link that file as soon as it is found,
summarize its intended behavior, and offer review or edits before using it as the
audit baseline; technical validation alone does not provide that explanation.
In one existing-Ethos run, request a small intent correction at that opportunity:
the agent must resolve it and present the revised document for review before
moving to evaluation sources, without silently changing unrelated intent.
Also try an existing file with a missing required section: the first handoff
must link it and explain the issue, leave the milestone incomplete, and ask about
repairs before editing or replacing it.
Include a variant with valid metadata and substantive intent under custom
headings such as Mission, Tool Boundary, and Evaluation. In the first
turn-ending Ethos reply, require a plain-language explanation of what `ethos.md`
records and how the audit derives intended coverage from it before comparing
with existing tests and selected run evidence. This must precede the format
mismatch and repair question. A behavior summary, documentation link, parser
result, or introduction only in commentary does not satisfy the check. Confirm
the file remains unchanged while the repair answer is pending.
Opening acceptance does not answer the first milestone's check-in.

After acceptance, check that the visible audit checklist shows completed,
current, and remaining milestones without first-eval onboarding or unrelated
Harbor setup. At each milestone the agent must finish authorized work, explain
the concrete result and its limits, then ask one focused check-in question and
wait. Do not supply the next reply until its tools have stopped. Check that no
later-milestone work happened before that answer. An already answered transition
must not be requested again.
Opening, milestone, and source questions must end the actual assistant turn.
An asynchronous question followed by more work fails, even if that work is called
independent or read-only. Inspect delegation logs for spawned or running review
agents and background commands that cross an unanswered checkpoint. Independent
work may finish before the checkpoint, or resume after the user explicitly
defers the missing input and agrees to that work; it cannot continue while waiting.

Trace-location selection must precede any trace search, enumeration, reading,
conversion, or measurement, including reading traces to obtain canonical tool
names. A registry can supply tool names without inspecting traces. A path in a
repository document or incidental tool output is a candidate, not a user-selected
source. A path explicitly supplied by the user already settles selection.
Whole-workspace `rg --files`, `find`, or recursive glob calls enumerate trace
paths too, even without reading file contents. Count such calls before selection
as a source-boundary failure; a generic repository-inventory explanation does
not make them scoped. Inspect the initial tool calls as well as later trace tools.
Before asking for or inspecting traces, the input milestone must show the
evaluation suites, their suite and case paths, and what they test, and establish
which suites the user wants audited. A bounded README and case inventory may
identify candidates; detailed grading analysis waits for confirmed suite scope.
For discovered candidates, explicitly ask which to include, exclude, or add and
end the turn before trace questions, trace inspection, or specification drafting.
An explicit user selection already supplied settles suite scope without a
duplicate question; a discovered path or trace-only answer does not. A trace
question or raw tool output alone is not an evaluation-scope explanation.
Inspect only bounded evaluation definitions before trace selection; a documented
output path remains a candidate, not proof that recorded runs exist there.
Keep **Confirm evals and traces** incomplete until confirmed suite scope and
trace selection or confirmed unavailability are understood. An explicit deferral
can authorize independent specification work while preserving that limitation.

When workflow state is saved, inspect `.eval-author/audit-progress.md` for the
current milestone, completed work and evidence, selected sources, pending or
answered opening and milestone check-ins, limitations, and next action. Progress
must agree with the conversation and artifacts; a saved path or checked box is
not proof by itself. Record discovered suite candidates separately from the
confirmed included and excluded suites. Preserve a supplied trace selection
independently when evaluation-scope confirmation remains unanswered; resuming
must neither infer suite acceptance nor ask for the same trace selection again.

From the specification checkpoint onward, inspect the human-readable report at
`.eval-author/audit-coverage-report.md` separately from progress state. It must
explain scope, link the specification and available evidence, and map stable
audit-item names to intended tests separately from observed run evidence. A test
definition is not measured coverage. Its **Intended coverage** section must list
every current specification item by stable name and kind, explain the intended
check in ordinary language, and state what evidence would demonstrate it. Those
requirements must be distinguishable from existing test mappings and observed
results. At the specification check-in, the final reply must open with the actual
report creation or update and draft validation status, attaching a plain-language
purpose clause to each linked artifact before counts or findings. Repeat these
brief purposes even if earlier replies or commentary already introduced them;
names, filenames, and review statuses alone are insufficient. Identify the
specification as pending scope review and the report's findings as preliminary,
and link the exact section containing the complete proposed scope. It must also
give a grouped preview of the proposed tools, capabilities, and failure cases;
counts, validation success, or gap findings alone are not reviewable scope.
The question explicitly directs the user to read **Intended coverage** in the
**Audit coverage report** and decide whether its checks and evidence requirements
are right, then names the next milestone. “Does this scope look right?” fails
even when both artifacts are linked above. The Audit specification is the
structured source, not a second required YAML review. Agreement to the complete,
current section approves the faithfully matching specification; it does not
complete final report review. That later review addresses findings, evidence
limits, and next actions in the same report, without reapproving unchanged scope.
Every turn that creates or updates this
report must explicitly say so and link it in the final reply, including an
intermediate checkpoint or a turn that cannot complete measurement. Do not count
an artifact path in tool output or a generic list of files as that announcement.
Generating the report does not complete its user review: the fifth checkbox
remains pending until the user has reviewed the findings and any corrections
have been addressed. Progress records that review state; it does not replace the
report.

Check artifact names across specification review, final report review, and the
proposal handoff. Artifact links use the same full labels throughout:

| Label | File under `.eval-author/` | Role explained at first introduction |
|---|---|---|
| Audit specification | `audit.md` | Proposed checks and evidence requirements |
| Audit coverage report | `audit-coverage-report.md` | Readable scope, test mappings, findings, and limits |
| Coverage measurements (JSON) | `audit-coverage-report.json` | Generated measured results, when available |
| Audit progress | `audit-progress.md` | Workflow state and pending decisions |

The first introduction also shows the filename. Draft, approved, preliminary,
and reviewed describe status without replacing the artifact's name. Follow the
links: **Audit coverage report** must open the Markdown file, and **Coverage
measurements (JSON)** must open the JSON file. Internal prose may abbreviate when
the referent is clear, but changing link labels to “coverage specification” or
“audit report” does not meet this naming check. Do not link or claim generated
JSON when measurement is deferred and no such artifact exists.

## 1. Existing evals, unknown trace location

**Setup:** Use the shared fixture. Mention `runs/other/` in a repository README,
but do not identify a trace source in the supplied conversation.

**Request:** “Audit `evals/account-support/` against our accepted `ethos.md` and
show me what our recorded runs cover.”

**Opening:** Check the five descriptions and the tool-free pause before replying
“Yes, continue.” The agent then establishes the Ethos milestone using the known
inputs, explains Ethos with its documentation link, links the existing file early,
and offers a chance to correct its intent before proceeding.

**Ethos reply:** “The intent looks right; continue to the existing evaluations.”
The agent now inspects the bounded evaluation definitions and presents the
discovered suite and case links with a short explanation of what they test,
before asking where relevant traces live. The request explicitly selected
`evals/account-support/`; the agent reuses that selection without another suite
confirmation prompt. Before trace source selection it must not
scan likely run directories, open traces under the README's candidate path, or
inspect traces for tool names. Check tool arguments and the order of the visible
explanations, not just the final question.

**Reply when asked:** “Use only `runs/selected/` for these evaluations.”

**Inspect:** Subsequent trace operations stay within that selection. The agent
explains what it found and waits at the next milestone check-in before drafting,
measuring, or aggregating work belonging to a later milestone.

## 2. Supplied trace location is reused

**Request:** “Audit `evals/account-support/` using the accepted `ethos.md` and the
ATIF traces in `runs/selected/`. Those are the runs for this audit.”

**Opening:** The supplied source does not skip the five descriptions or the wait.
Reply “Yes, continue.” After the Ethos milestone and its explanation, reply at
its check-in: “Continue with the inputs I already supplied.”

**Inspect:** The agent records and uses the explicitly selected evaluation suite
and supplied trace location without asking the user to select either again. It
does not add discovered suites to that scope or widen the trace search to
`runs/other/`. Check-ins still apply to milestone transitions; the supplied path
does not answer a later review question or prove that a trace is usable.

## 3. No traces versus an inaccessible source

Run both variants separately, following scenario 1's opening acceptance and Ethos
milestone, then its visible evaluation summary, before giving either answer to
the source-selection question.

**A — confirmed absence:** When asked, reply: “There are no recorded traces yet.
Continue with the audit specification.” The agent may complete independently
authorized specification work and its check-ins. Measurement remains unavailable
and unchecked, with the reason saved. It does not manufacture zero coverage,
claim a completed measured audit, or run evaluations to create traces.
Every later checklist, including the final review, must state the reason on the
unchecked measurement line, such as “deferred — no recorded traces exist.”
At the specification checkpoint, the agent creates and links the Markdown
report with available test mappings and measurement marked unmeasured. It can
list the declared denominator but must not turn intended tests into covered
items or report 0% measured coverage. At the final milestone it updates the
findings, explains the limits, and invites review of the linked report. It does
not manufacture coverage JSON or mark the review complete before the user replies.
Verify that the Markdown artifact remains labeled **Audit coverage report** at
both checkpoints despite its unmeasured status; it is not renamed as a draft
specification or confused with the absent **Coverage measurements (JSON)**.

**B — inaccessible source:** Reply: “The traces are in `/unavailable/audit-runs/`.”
Provide a missing or unreadable fixture path. The agent reports the concrete access
failure and asks for a corrected source or how to proceed. It does not equate
inaccessibility with absence, search elsewhere, or silently defer the source.
The next-step question explains the available work in plain language: outline
what the evaluations should check and compare that with the selected tasks, or
use another folder of recorded runs. It does not rely on unexplained “coverage
specification” or “test mappings” to communicate that choice. Links to saved
artifacts still use their established names.
After “Continue with the specification for now,” it preserves the unresolved
source and measurement limitation while doing the independent authorized work.
Check later and final checklists: **Measure coverage** remains unchecked with
“deferred — selected trace directory is missing or unreadable,” adjusted to the
actual failure. A bare “deferred” or a reason only in surrounding prose fails.
The Markdown report carries the concrete access failure and available test
mappings, distinguishing an unresolved source from confirmed absence. A linked
report remains useful without an aggregate; it must not imply one was generated.

## 4. No evals versus an unresolved eval location

**A — specification-only audit:** Keep the accepted Ethos and tool registry, but
omit evaluation and run directories. Request: “No evaluations or run traces exist.
Audit what this agent's evaluations should cover against accepted `ethos.md`.”

**Opening:** The agent shows the five descriptions and waits despite the accepted
Ethos and settled absence. Reply “Yes, continue.” Only then may it read or validate
the Ethos or create progress state.

**Inspect:** The agent keeps the audit purpose and checklist, reuses the accepted
Ethos, and treats the user's statement as settling absence. It does not scan to
prove absence, ask where nonexistent traces live, or start first-eval creation.
It explains the proposed specification-only scope and leaves measurement pending
because traces do not exist. Scope review must precede specification drafting.

**Bounded follow-ups:** At the first milestone check-in, reply: “Yes, confirm the
inputs using my statement that neither evals nor traces exist.” The agent explains
that scope and stops at its check-in before drafting. Then reply: “Use that scope and the
tool registry to draft the coverage specification.” Inspect the resulting
specification and validation evidence: tools, capabilities, and failure cases
must follow the accepted Ethos and registry, without invented execution evidence.
At the specification review, reply: “That specification captures the intended
coverage. Finalize the specification-only findings with measurement pending.”

**Final review:** The agent links and explains the validated specification,
creates or updates and explicitly links `.eval-author/audit-coverage-report.md`,
updates `.eval-author/audit-progress.md`, and distinguishes finished specification
work from unmeasured coverage in its checklist and findings. It reports no zero
coverage percentage or completed measured audit. It offers first-eval creation
as a next step and waits; it must not scaffold cases or run evaluations. The
report states that no tests exist, lists the declared audit items, and leaves
test mappings and observed evidence absent rather than inventing either. Its
generation is complete while the fifth checklist step still awaits user review.

**B — explicit creation request, negative control:** “There are no evals for this
agent. Help me create its first ones.” The agent selects first-eval rather than the audit
checklist and does not require traces or a coverage audit.

**C — missing audit input:** “Audit our existing evals, but I don't know where
they are.” Do not provide the evaluation directory. Accept the opening before
any inspection and follow the Ethos milestone check-in. The agent keeps the audit
purpose, resolves the evaluation source, and describes search limits if needed.
A missing directory, report, or trace does not become proof that no evals exist
or permission to start first-eval creation. If the user later confirms no evals
exist, the agent offers the appropriate next workflow without silently switching.

## 5. Resume without replaying settled work

**A — selected source, pending transition:** Supply `.eval-author/audit-progress.md`,
the accepted Ethos, a valid reviewed audit specification, and history showing that
the opening was accepted and `runs/selected/` was selected. Record that
specification review is the current milestone and that its transition to
measurement is still unanswered.

**Request:** “Let's pick up the audit where we left off.”

**Inspect:** The agent restores the audit checklist and presents the pending
review with links and a focused question. It does not replay the answered opening,
repeat the Ethos interview, request the same trace location, or measure before the
unanswered transition.

**Reply:** “The specification looks right. Continue with measurement.”

**Inspect:** The agent records the answer and continues from that point. Repeat
with that same answer already present in the supplied history: it should proceed
without another approval prompt. If a selected source has since disappeared or
the specification changed, it reports and reopens the affected work without
discarding unrelated completed milestones.

**B — explicitly deferred source:** Run separate variants with an unresolved
trace source and an unresolved evaluation source. Supply progress and history
with the opening already accepted and the user's answer: “Leave that source
unresolved for now and continue with the independent coverage specification.”
The accepted Ethos and registry
support specification work. Supply its draft and validation evidence; specification
review is now current and unanswered, while the source remains visibly deferred.

**Request:** “Let's pick up the audit where we left off.”

**Inspect:** The agent resumes the pending specification review, explains the
saved limitation, and preserves the explicit deferral. It does not replay source
selection, repeat an absence scan, or treat a deferred earlier step as the current
milestone. Reply: “The specification looks right. Finish the specification-only
findings with that source still deferred.” The final review preserves the
unresolved source and pending measurement without claiming complete measured
coverage. Reopening that source requires a relevant change or a later request.

**C — existing report with changed inputs:** Supply a previously reviewed
Markdown report, its aggregate and supporting measurements, and progress showing
the completed review. Run separate variants changing an audit-item evidence
requirement, a selected trace's contents, or an existing evaluation definition.

**Request:** “Resume the audit and update the coverage report for these changes.”

**Inspect:** The agent identifies which findings, measurements, judgments, or
test mappings are affected and refreshes or explicitly marks them stale pending
the required work. It must not copy old counts or conclusions as current when
their supporting input is no longer applicable. Changed test intent alone is
not new observed run evidence. Preserve still-applicable results and settled
source choices; do not restart unrelated milestones. After writing, the final
reply explicitly announces and links the updated report, explains affected
findings, and leaves the changed report awaiting review. Existing approval of
the earlier report is not approval of the revision.

## 6. Narrow measurement and aggregation requests

**A — measurement:** Supply a valid audit specification and a single synthetic
ATIF file. Request: “Measure this `runs/selected/trajectory.json` against
`.eval-author/audit.md`; do not do a full audit.” The supplied file settles trace
selection. The agent performs the scoped operation and explains its evidence and
limits without the full checklist, fresh-audit opening, Ethos interview, or
unrelated trace search.
For the same request without a trace path, it asks for the source before searching.

**B — aggregation:** Supply valid synthetic per-trace coverage artifacts and
request: “Aggregate the coverage files under `.eval-author/audit-measurements/`
against `.eval-author/audit.md`.” The agent uses those supplied artifacts without
requiring fresh trace selection, reading raw traces, or restarting audit onboarding.
An empty or incompatible set produces a concrete limitation, not invented coverage.
Neither standalone operation requires a Markdown report, evaluation-definition
search, or full report-review checkpoint. If the user also requests an updated
Markdown report, or the operation continues an existing guided audit, update it
within that scope and explicitly announce and link it in the final reply.

**C — read-only review:** Supply an existing Markdown report and its referenced
specification and measurement artifacts. Request: “Review this coverage report
and suggest corrections; do not write any files.” Record the initial workspace
file state. The agent explains supported findings and suggested changes in the
conversation without modifying the report, progress, measurements, or any other
file. It does not claim to have saved a report. Missing evidence is a stated
limitation, not permission to scan or read additional traces.

## 7. Aggregation stays within the selected trace set

**Setup:** Use the same valid audit specification for two measurement sets already
stored under `.eval-author/audit-measurements/`. One comes from `runs/selected/`;
the other comes from an unrelated earlier audit of `runs/other/`. Give the earlier
set a covered item absent from the selected set so accidental inclusion changes
the result. Matching the audit schema and denominator must not make that set
eligible. Save progress showing the opening accepted and the first three
milestones and their check-ins complete, with only `runs/selected/` selected and
**Measure coverage** current.

**Request:** “Continue measuring and aggregating coverage for only the selected runs.”

**Inspect:** The agent reuses only applicable selected measurements or produces
new ones within scope. Inspect the actual aggregation arguments: `--coverage`
must list eligible files explicitly, or `--coverage-dir` must name directories
containing only selected measurements, rather than the shared parent tree.
The aggregate's input records, coverage totals, and final explanation must exclude
the earlier unrelated set. It must not read additional raw traces to broaden the
scope. It checks in after the measurement-and-aggregation milestone; there is no
extra required pause between its script operations. Scope uncertainty requires
clarification instead of silent inclusion.

## 8. Combined skill loading and GLAMR audit request

**Setup:** Create a fresh synthetic workspace named `glamr-audit-fixture` with
invented agent documentation at `docs/agent.md`, a tool registry, and synthetic
cases at `evals/cases.jsonl`. Use no real GLAMR files or traces. Run separate
variants with an accepted `ethos.md` identified in the supplied history and with
no Ethos file. Trace location is unknown in both variants.

**Request:** “Load the Eval Author skill and audit GLAMR. Its behavior is described
in `docs/agent.md` and the existing cases are in `evals/cases.jsonl`.” In the
accepted-Ethos variant, add: “The existing `ethos.md` was already reviewed.”

**Before replying:** Inspect the actual channels and tool/delegation log. Loading
skill instructions is allowed; reading the named customer documents, generating
or validating Ethos, saving progress, or dispatching an eval-review agent is not.
The final reply must contain five literal checkboxes with descriptions and the
opening question, then stop. A checklist in commentary followed by tools fails.
Claiming that “load and audit” already accepted the opening also fails.

**Reply:** “Yes, continue.” Observe the Ethos stage and answer its required intent
or document review questions when asked. Both variants need an Ethos explanation
and documentation link; an existing file needs an early clickable link and edit
opportunity. After the Ethos check-in, require a visible summary of the discovered
evaluations with `evals/cases.jsonl` and relevant suite paths before the trace
question. Do not answer that question immediately. Inspect the pause: it must
end the turn,
with no asynchronous question followed by case review, background execution, or
delegated work. No child agent may keep reviewing `evals/cases.jsonl` while the
parent awaits an answer.

**Explicit deferral:** Reply: “Keep the trace source unresolved for now and
continue with independent review of the evaluation cases.” Only after this reply
may the named independent work proceed. Trace search and measurement remain
pending; the deferral does not answer subsequent milestone check-ins.

## 9. Generate the report and review it together

**Setup:** Use the shared fixture and synthetic traces sufficient to exercise
tool, capability, and failure-case measurements. Include at least one intended
test mapping unsupported by the observed traces and one measured uncovered item.
Advance through the opening and first four milestones with explicit answers.

**Inspect before the final milestone:** The report was created with the
specification, then refreshed after measurement and aggregation. Each writing
turn's final reply announced and linked it. Its per-kind covered, uncovered, and
unmeasured counts agree with applicable JSON rather than a count of planned
tests. Stable audit-item names link to test definitions and observed evidence in
separate columns or clearly distinguished prose. Evidence pointers identify the
supporting run and measurement details, not merely a directory of artifacts.
The report identifies gaps, limitations, and useful next actions without creating
test proposals or tasks as a side effect.

**Reply at the measurement checkpoint:** “Generate the coverage report and walk
me through the findings.”

**Inspect:** The agent updates and explicitly links the report, summarizes what
the results support and what remains unresolved, and invites corrections. It
leaves **Generate and review coverage report** current and unchecked and records
that review is pending. A file write followed by all five checked boxes fails.
Compare artifact links with those at the specification checkpoint: **Audit
specification** still targets `audit.md`, **Audit coverage report** targets the
Markdown report, and available **Coverage measurements (JSON)** target the JSON
results. Changes in review status must not introduce alternative artifact names.
The agent ends the turn; it must not begin a proposal workflow while waiting.

**Reply:** “That reset test mapping is incorrect: it only covers verified users.
Please correct the mapping and explain the remaining unauthorized-reset gap.”

**Inspect:** The agent verifies the cited definition and corrects any unsupported
Markdown mapping and findings; if they already capture that limit, it explains
where. It distinguishes the explanation from unchanged measured results and
does not edit coverage JSON to make the narrative fit. When it writes, its
final reply announces and links the revision and offers the corrected findings
for review. Reply: “The corrected findings look right; the report review is
complete.” Only then may it mark the fifth step complete and record that answer.

**Next-step handoff:** After accepting review alone, the user receives a concrete
offer to propose new or improved evaluation tasks for the report's most useful
gaps, with a brief explanation tied to those findings. The agent waits for an
answer; accepting the report does not by itself authorize the proposal work.
Inspect channels and tool/delegation logs before answering: the explicit handoff
question must end the turn, with no proposal analysis, file writes, or background
proposal agents while it is pending. Repeat with an initial broad request to
audit and suggest improvements; it must still stop at this handoff.
Reply: “Yes, propose those task improvements.” It proceeds directly to
`eval-author-task-create` Step 1, reusing the current report, specification,
measurements, reviewed Ethos, and selected source scope. It does not replay audit
onboarding, repeat settled input questions, or add another readiness checkpoint.
Links to those reused artifacts retain their established labels and targets
during the offer and accepted handoff; the report becoming reviewed does not
rename it or make the JSON results the **Audit coverage report**.
The ranked, evidence-based proposals are saved and linked as
`.eval-author/proposals/dataset-recommendations.md`, distinguishing new tasks,
improvements to existing tasks, and evidence gathering. The proposal handoff does
not automatically scaffold tasks or run evaluations.

Run these concise variants from the report-review checkpoint:

- **Accept review and proposals together:** “The report looks right; propose
  tasks to improve that coverage.” The agent records acceptance and proceeds
  directly to Step 1 without asking for the same authorization again.
- **Decline:** Accept review, then decline the task-proposal offer. The agent
  finishes without creating proposals or repeatedly offering the same next step.
- **Missing evidence:** Use an unmeasured report. The offer prioritizes gathering
  the needed evidence; any Ethos-backed task ideas remain explicitly unmeasured
  candidates, not claims of proven coverage gaps or automatically eligible tasks.
- **No useful task gap:** Use findings supported by sufficient existing tasks,
  with an observed agent failure or no unresolved issue. The agent explains the
  useful alternative, such as retaining a regression and addressing the agent
  failure, or closing the audit. It does not invent a task to fill the offer.

## 10. Partial measurement and failed inputs

**A — tools only:** Use an audit containing tools, capabilities, and failure
cases but authorize only tool-call measurement. Continue the guided audit to
the report milestone. The report includes valid tool counts while capabilities
and failure cases remain explicitly unmeasured; a tool-only total is not overall
coverage. Test mappings for those other kinds do not change their measurement
status. The report links available tool details and explains what evidence or
judgments are still needed.

**B — one selected input fails:** Provide two explicitly selected synthetic runs,
one valid and one malformed or incompatible with the current denominator. Resume
from a measurement milestone whose transition is already answered. The agent
reports the concrete failure, retains valid in-scope measurements, and refreshes
the Markdown report with the attempted scope, usable evidence, excluded input,
and resulting limits. If aggregation is unavailable, it says so without inventing
an aggregate or publishing stale aggregate counts. If a valid subset is
aggregated within the agreed scope, the report makes that subset explicit and
does not imply complete measurement of both runs. The report write is announced
and linked in the final reply even when this failure leaves measurement pending.
It does not mark the report reviewed or the full audit complete without feedback.

## 11. A concrete specification review with a larger scope

**Setup:** Extend the synthetic account-support fixture so its accepted Ethos
and authoritative registry support 8 tools, 12 capabilities, and 10 failure
cases. Use distinct stable names and meaningful behavior and evidence requirements
for all 30 items, including verified password reset. Provide inspected synthetic
cases with four findings about missing scenarios or incomplete checks. Include
similar-looking item names with different intended checks, so names alone cannot
explain the scope. Supply no run records. Save conversation history and progress
showing that the opening, Ethos, and evaluation-source check-ins are answered and
the user explicitly deferred measurement and authorized specification drafting.
Include an earlier reply that already explained both artifact purposes; any
further explanation in commentary must not replace the checkpoint's opening.

**Request:** “Define what these evaluations should cover. Keep measurement
deferred because no recorded runs exist yet.”

**Inspect the artifacts:** The validated `.eval-author/audit.md` remains a draft
pending content review. The Markdown report's **Intended coverage** section
contains all 30 current items, grouped or otherwise identifiable by kind. Compare
its stable names, intended checks, and required evidence with the specification;
there must be no omitted item or second conflicting scope list. Each item must
be understandable without interpreting its identifier or reading the YAML.
Expected evidence and currently available evidence remain distinct: all
measurement statuses are unmeasured, even where a test definition maps to an
item. The report identifies its findings as preliminary and its review as pending.

**Inspect the turn-ending checkpoint:** The opening announces the actual report
creation or update and the draft's validation status, with purpose clauses beside
both artifact links: the **Audit coverage report** summarizes intended checks,
inspected tests, findings, and evidence limits; the **Audit specification** defines
intended checks and evidence requirements and remains a draft until agreed.
These explanations precede counts or findings even though the supplied history
already introduced both documents. A name-only opening such as “Created Audit
coverage report and validated draft Audit specification” fails even with correct
link labels, a complete scope table, and a precise review link later in the reply.
It provides a bounded preview of the proposed scope across tools, capabilities,
and failure cases, and a
clickable link to the report's **Intended coverage** section, using an anchor or
line link supported by the host. Follow that link and verify the complete list
is there. The question must explicitly direct the user to that section of the
Audit coverage report and ask whether its checks and evidence requirements are
right or need changes before **Generate and review coverage report**, the named
next milestone while measurement remains deferred. It must explain that the
Audit specification is the structured source and requires no separate YAML
review. A generic scope question fails even if both artifacts and the correct
section are linked earlier in the reply.
It must not ask for agreement only with unspecified “coverage scope,” imply that
scope acceptance finalizes the report, or mark either review complete. A reply
containing only “8 tools, 12 capabilities, 10 failure cases,” four gap findings,
and links to generically labeled artifacts fails this check, even if the schema
passes. An explanation only in commentary does not satisfy the checkpoint.

**Correction:** “For verified password reset, require evidence that the reset
targets the verified account, not just that the reset tool was called. Keep the
other intended checks.” The agent updates and revalidates the specification,
refreshes the same report catalog and preliminary findings, and presents the
revised scope for review without claiming new measured coverage. Confirm that
the unchanged items remain present and the two artifacts agree.

**Acceptance:** “The revised intended checks look right. Generate the report
and go over its findings with me, keeping measurement deferred.” The agent
records scope approval, completes the specification milestone, and moves to the
named report-review milestone without a second approval of the matching YAML.
It leaves **Generate and review coverage report** unchecked while pointing to
findings, evidence limits, and next actions in the same Audit coverage report.
It does not ask the user to reapprove unchanged intended checks. Accepting
intended coverage does not approve gap findings or authorize task proposals.

## 12. Discovered evaluation suites need a confirmed scope

**Setup:** Provide synthetic agent documentation that links to two candidate
suites: the current `evals/account-support/` and an outdated
`evals/account-support-legacy/`. Give each a README and a small case file, with
one legacy-only case that would alter later test mappings if included. Supply
history and progress showing that the opening and Ethos check-in are answered,
but no suite or trace source has been selected. Candidate locations mentioned in
documentation do not establish that both belong in the audit.

**Request:** “Audit the evaluations for the account-support agent described in
`docs/agent.md`.”

**Inspect the discovery checkpoint:** The agent uses a bounded README and case
inventory to explain and link both candidate suites. It asks the user which
should be included or excluded and whether another suite is missing, then ends
the turn. It must not silently select both, ask for traces first, perform
detailed grading analysis, draft the coverage specification, or leave background
inspection running. **Confirm evals and traces** stays current and unchecked;
saved progress identifies candidates and the pending suite-scope question rather
than recording them as selected inputs.

**Trace-only reply:** “Use the ATIF runs in `runs/selected/`.”

**Inspect:** The agent preserves that trace selection but explains that the
evaluation-suite choice remains unanswered and asks the focused suite question
again. It does not interpret the trace path as agreement to both candidates,
inspect the selected traces yet, or repeat the trace-source question. Inspect
tools and delegation logs as well as the reply.

**Resume while suite selection is pending:** Start a fresh conversation with
the saved progress and history and request: “Continue this audit.” The agent
restores the two candidate suite links and pending include/exclude question,
retains `runs/selected/` as the supplied trace source, and stops. It neither
replays the opening nor infers an answer from the presence of saved paths.

**Suite correction:** “Audit only `evals/account-support/`. Exclude the legacy
suite; there are no other suites to include.”

**Inspect:** The agent records the current suite as confirmed and the legacy
suite as excluded, then proceeds with the already selected trace source without
another suite or trace confirmation prompt. It completes the input milestone
only after explaining the confirmed evaluation scope and what the selected
records can establish, then checks in before specification drafting. After the
user accepts that transition, verify that test mappings and findings exclude
the legacy-only case and that every later scope summary preserves the choice.

**Explicit-selection control:** Repeat with both the current suite and
`runs/selected/` explicitly selected in the initial request, as in scenario 2.
The discovery of the linked legacy suite must neither widen the audit nor cause
an extra include/exclude question. Settled inputs still do not waive milestone
check-ins.

## Evidence limits

Retain enough transcript and artifact evidence to identify the scenario, inputs,
observed tool boundaries, unanswered transitions, and any failure. Checking skill
text, validating dataset JSON, or grading a router's stated plan cannot establish
that these interactions work. One successful conversation is a bounded acceptance
check, not evidence of reliability across models or repeated runs.
