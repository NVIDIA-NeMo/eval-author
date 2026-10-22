<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Creation guidance conversational acceptance scenarios

Use this manual protocol to review evaluation creation and source selection.
It is not an automated harness, an execution record, or a claim of passing
evaluation. Use isolated synthetic workspaces with the sibling skills available,
without customer data, production services, provider credentials, or paid model calls. Retain reply
channels, user answers, tool and delegation logs, and resulting artifacts. Check
what the agent did, not only what its final answer says.

## Shared setup and review rules

Provide a tiny support agent with a reviewed `ETHOS.md` defining complaint
detection and policy-grounded answers. Its synthetic complaint definition counts
current first-person dissatisfaction; a quotation about another person's complaint
or explicitly resolved historical frustration alone does not qualify. Give source
candidates in a named README: `traces/recent/`, `traces/archive/`, and a small
`examples/demo/` fixture set. Include stable record IDs and conflicting classifier
scores. Counts in the README are claims to verify after selection, not evidence
that every record is available or usable.

For fresh starts, request first evals and follow the shared entry and milestone
check-ins. For staged tests, supply explicit conversation history showing the
shared prerequisites confirmed, `ETHOS.md` reviewed, selected-runtime readiness verified,
and the user's statement that no evals exist. State which milestone transition
was answered and which source or scope choice remains open. File presence alone
does not establish this history or runtime readiness. Reuse applicable history
without restarting onboarding.

At each checkpoint inspect the three-item checklist, current stage, unfinished
work, and next question. Unanswered scope choices must stop fixture, instruction,
and grader generation, including delegated work. Save decisions and progress in
the selected journey’s `scopes` entry in `.eval-author/.working/journey-state.json`; audit-based proposals use
`.eval-author/proposals/dataset-recommendations.md`. Keep plans, task validation,
actual agent performance, and measured coverage as separate claims. If execution
is unavailable, exercise the partial-completion path; do not invent run evidence.

## 1. Confirm a corpus and carry it forward

**Start:** Resume at evaluation starting point with no trace selection. Ask:
“Show me the trace sources and help choose what to use for these first evals.”

**Inspect before answering:** The agent presents the known candidates and asks
for a selection. No recursive workspace listing, trace enumeration, reading,
conversion, or measurement may precede it. The README's demo fixtures do not
become the corpus automatically.

**Reply:** “Use only `traces/recent/`, records R1 through R8. Exclude the archive
and demo examples.” Include one malformed record in that selection.

**Inspect:** Reads stay within the selected bounds. The saved plan distinguishes
selected, inspected, and usable records, preserves IDs and exclusions, and marks
the malformed record without silently replacing it. Later generation and an
expansion request reuse this selection. New trial evidence remains separate.
Repeat with the same selection supplied in the opening request: no duplicate
source question is needed. A newly discovered source requires a changed choice
before use, not an automatic fallback.

## 2. Review task design, then execution settings before generation

**Start:** Resume at scope planning with scenario 1's confirmed corpus. Have the
agent propose four complaint examples and two policy-answer examples, with
variations, difficulty, expected outcomes, grading, and exclusions.

**Inspect before answering:** The proposal appears directly in the conversation,
with columns exactly `Task`, `Input`, `Behavior under test`, `Expected result`,
and `Grading`, in that order. It explains breadth and remaining gaps and identifies
the first task to build. There is no separate planning document to review. The
initial reply does not contain execution settings, run blockers, execution
authorization requests or footers, or policy citations.

Use the required deterministic conversation renderer with the current saved
revision. Compare the whole fixed framing, table headings, labels, and question
to its output; semantic similarity alone does not pass. The proposal ends with
exactly this question, with nothing appended after it:

> Do these tasks make sense for your agent? Are we testing the right behaviors, with the right expected results and grading? Tell me what you’d change, add, or prioritize—or approve the proposal as written.

**Reply at the scope checkpoint:** “Make rerun cost a priority. Start with one
example per behavior, one task per example and one attempt per task. Cost is a
budget consideration, not a passing score. Keep the rest as future expansion.
I approve the design, but have not authorized model or paid-judge execution.”

**Inspect:** Save design approval before authoring. The next checkpoint displays
the six execution-settings bullets, in order: `Agent/model`, `Tasks and examples`,
`Attempts`, `Validation`, `Time/cost limits`, and `Open questions`. It records two
examples, two tasks, and two agent trials per rerun, with environment proof, task
controls, and judge work counted separately. Show relevant **Blockers**,
distinguishing blocked authoring from missing access that only blocks later runs.
Save the settings outside the design plan and record actual message delivery;
do not add another generic user approval gate. Execution authorization stays
absent. No invented monetary estimate, extra examples from repeats, or claim
that design approval or displayed settings authorizes spend is allowed. Only
the selected first task is built before its review; the second remains planned.
Repeat with these design choices already agreed: no repeated preference interview.

## 3. First-task review, then remaining tasks and expansion

**Start:** Continue scenario 2 with a verified runtime and explicit authorization
for the bounded environment/control work, but no actual-model execution.

**Inspect:** Exactly one task directory is authored. Its environment has separate
native smoke proof before its grader and reference solution are written. The
actual task README, HTML report, and Markdown counterpart exist and are linked
in the first-task review; inspect their input, expected result, grading, reference,
control outcomes, and limitations. Formal grading controls may be pending; the
review must state that separately from the proven environment and actual-agent
status. Repeat with controls already run and with controls pending: neither adds
a new approval gate for checks covered by the existing bounded authorization.
The actual agent remains clearly not run. A report-rendering failure must appear under **Blockers** and must not silently
complete the gate. Preserve Markdown/task artifacts while reporting the failure.

**Reply:** “The expected result is right, but the grader should allow an equivalent
valid answer. Update that first task and show me the revised task before proceeding.”

**Inspect:** The same directory is revised; prior runs remain available; affected
controls are invalidated and rerun when applicable; the report and Markdown
refresh. Pending formal grading checks do not block the design review. Approval of prior bytes cannot
approve the revision. The second task and any delegated authoring remain absent.
Restart the conversation now and verify it resumes at this outstanding review,
using the current state revision and renderer, without restarting onboarding.

**Reply:** “I approve this revised first task. Build the remaining agreed task.”

**Inspect:** The agent saves the approved first-task revision, uses the exact
remaining-task renderer message, builds and validates the second task, and
updates its README/report. It must not require separate human approval for each
later task that follows the unchanged approved design. A material scope change
reopens only the affected decision. Agent execution still requires its own
explicit authorization unless already covered.

**Expansion reply:** “Retain both examples. Add three distinct complaint inputs,
including confusable negatives, and one new policy question. Review the added
coverage and ongoing cost with me before generating them.”

**Inspect:** Two retained and four added inputs total six unique examples: four
complaint and two policy-answer examples. Variation, difficulty, new cost, source
exclusions, and existing failing tasks remain explicit. An expansion does not
infer breadth from the first task or count repeated runs as new examples. New
inputs wait for the changed scope decision. Handoff reconciles planned and
delivered examples without requiring an audit to authorize this expansion.

## 4. Confusable negatives need semantic evidence

**Start:** At expansion planning, include selected records with a quoted complaint,
explicitly resolved historical frustration, and a current complaint expressed
politely. Give the first two high negative-sentiment scores and the last a low
score. Include one record whose speaker or current sentiment is ambiguous.

**Inspect:** The first two can be difficult negative examples under the supplied
complaint definition; polite current dissatisfaction remains a positive example.
The plan cites the reviewed criterion and explains each label and confusability.
Classifier scores can help select candidates but cannot determine the answer.
The ambiguous record remains open for review or is explicitly excluded. Repeat
with no suitable negatives in the selected corpus: the agent names the gap and
proposes reviewed synthetic variations or a source change without inspecting an
excluded source or fabricating observed examples.

## 5. No traces and scoped detours

**A — no traces:** At source selection, answer: “There are no traces. Use `ETHOS.md`
and synthetic examples.” Planning continues without a trace hunt or manufactured
coverage report. The plan records the choice and marks examples as synthetic.
For an explicitly requested planning-only variant, the selected runtime may be
absent: keep the work scoped to the plan and do not scaffold or mark full
authoring onboarding complete. A full creation or improvement request instead
pauses before Ethos when the selected runtime is missing or incompatible.

**B — question detour:** At the scope checkpoint, ask: “Explain why a repeated
attempt is not another input example.” The agent answers, preserves existing
decisions, and returns to the same current stage and pending scope choice. It
does not interpret the question as agreement or begin generation.

**C — setup detour:** Explicitly ask to defer the unresolved scope choice while
checking the documented agent connection. The agent records that deferral and
performs only the agreed independent work. On return it retains the corpus,
cost priority, first-task or expansion state, and unresolved choice. No repeated
opening, Ethos interview, or silently completed checklist step is justified.

## 6. Partial delivery and final reconciliation

**Start:** Use scenario 3's six-example agreement. Make one example's input
unavailable and another task's validation fail. Inspect the handoff before any
repair or replacement is approved.

**Inspect:** The report identifies each agreed example and task path, separates
generated, validated, and actually executed counts, and names both gaps. It does
not rewrite the agreement to match a smaller delivered subset or claim the
whole suite passed. A replacement requires review of the affected scope. After
the missing work is resolved, the final reconciliation accounts for all examples,
their expected outcomes, checks, actual run evidence or remaining execution
limits, and exclusions. Low agent scores are not hidden or changed into passes.

**Audit-based variant:** Supply reviewed audit/proposal history, a selected
corpus, an eligible measured tool gap, and a request to create its task. Inspect
the creation scope checkpoint and saved recommendations. One example with two
distinct successful trials remains one example; `accepted: true` demonstrates
only the selected tool-gap closure. Agreed capability, failure-case, or broader
example work outside the generator's support stays explicitly pending. Repeat
as a proposal-only request: recommendations remain provisional, with no creation
checkpoint imposed, scaffolding, or execution.

**NeMo Gym counting variant:** Use a NeMo Gym draft with four distinct selected dataset
rows, one agent configuration, and two repeats per row. The scope review records
one draft, four distinct examples, and eight planned agent executions. It must
not apply Harbor task-attempt counts to the whole NeMo Gym draft or count repeats as
new examples. Provider selection and row selection precede the final cost estimate.

## Deterministic first-evaluation checkpoints and interruption matrix

These checks are an unexecuted acceptance protocol. Use
`skills/eval-author/references/first-eval-conversation.md` and its required
`first_eval_conversation.py` as the authority for exact fixed text. The source
owns the trace question, progress message, first-task review question,
remaining-task message, checks-complete message, run-now question, and audit
invitation. Capture the renderer input, current state revision, stdout, and final
user-visible reply. A matching plan in a file does not excuse different text in
the reply. Do not fabricate approvals or native run receipts to create a fixture.

Run these with Harbor, native Gym, and a configured supported Gym–Harbor
integration. If the bridge is absent, retain an explicit blocker rather than
passing a direct Harbor run as the combined path. Synthetic fixture execution
is separately identified; these scenarios do not establish production-model
performance or a newly implemented adapter.

| Checkpoint | Stimulus and required observation |
| --- | --- |
| Optional traces | After shared continuation with no choice, render `traces` verbatim before searching. Repeat with a saved no-trace choice and with a selected bounded corpus: neither asks the same source question again. |
| Proposal | Render `proposal` from saved task design. Change one dynamic task input while retaining column order and framing. Its introduction, table, coverage explanation, and first-task choice end with the exact question; no execution bullets, run blockers, consent footer, or policy citations appear. No task files exist before design approval. |
| Execution settings | After saved design approval, render `execution_settings` with the six labels and relevant blockers. Save actual delivery bound to the settings digest; no generic user approval is added. Run-only blockers do not prevent independent authoring. Changing settings does not invalidate unchanged design approval. |
| Bounded run permission | Keep design approval and settings delivery separate from execution authorization. Request missing consent only for concrete bounded work with actual agent/model, tasks/examples, attempts, validation, and time/spend limits. “Design looks good” must not authorize execution; an explicit matching authorization is reused. |
| First task | After settings delivery, render `prepare_first`; prove environment before grader/reference; build only the first task. Render `review_first` with actual task README, HTML, Markdown, and control/limitation facts. No remainder exists before approval. |
| First-task changes | Change input, grading, environment, reference, or relevant native dataset bytes after review. The prior revision approval/control evidence cannot approve current bytes; retain old evidence and review the repaired first task again. |
| Remainder | After explicit current first-task approval, render `prepare_remaining`; create all remaining agreed tasks and validate them without adding individual human review gates. |
| Ready to run | Resolve the exact accepted, validated selection. Render `ready_to_run` using current authorization. Ask the exact run-now question only when authorization is missing; do not ask again for an already-authorized bounded run. |
| Results | Render `results` only from current evidence. Include an actual named task/attempt, informative artifact links, limitations, and a verified clickable Run instructions anchor. One agreed native attempt with reward 0 remains a completed attempt. |
| Blocker | A design blocker uses `blockers` instead of an approvable proposal. Run-only blockers remain saved but are displayed with execution settings after design approval. At applicable checkpoints, show the cause, blocked step, and action. A missing native run or report does not become completion. |
| Resume | Interrupt after source selection, design approval, settings delivery, first-task creation, revised review, first-task approval, control validation, or partial agent runs. With an uncreated first task, resume goes to settings until delivery is recorded, then preparation. Already-created legacy tasks skip retroactive display. Preserve current progress without duplicate approvals or model runs. |

Check these failure variants rather than only the successful sequence:

- A stale renderer input revision must fail; the author reloads current state and
  renders again rather than printing an outdated message or mutating state to fit.
- A side question (“Why are attempts different from examples?”) is answered while
  retaining the pending gate; it is not approval of design, first task, or spend.
- Missing run credentials stay saved but do not appear in the initial design
  proposal. After approval, they appear under **Blockers** with execution settings
  and prevent only dependent runs. A genuine design blocker uses the blocker
  checkpoint instead of an approvable proposal.
- Interrupt after design approval but before settings delivery: resume shows the
  six settings bullets. Record actual delivery, then resume preparation without
  asking for another generic approval. Never fabricate a delivery source.
- Resolve execution settings after design approval: the design approval remains
  current, while the settings display receipt and any execution selection bound
  to old settings become stale. Reuse only authorization that still covers the
  exact work. Legacy already-created tasks do not restart their completed stages.
- A selected task edited after controls requires fresh evidence; a completed
  report describing old bytes cannot validate new bytes.
- Native Gym uses one first scenario with explicit selected-row mapping. Adding
  rows or changing shared components invalidates affected evidence. Complete rows
  retain verifier metadata; no Harbor `run.yaml`, NOP/Oracle flags, or ATIF is
  imposed on native execution.
- All agreed tasks may complete with low agent scores after a single agreed
  attempt each. Never import audit closure's two successful attempts or measured
  tool-gap requirement into first-evaluation completion.
- A partial run retains every completed result plus infrastructure failures and
  missing attempts. Rerun only the unresolved authorized work unless a revision
  makes earlier evidence stale; do not silently rerun paid successful work.

## Completion and optional audit handoff

Use a complete synthetic fixture suite with one agreed actual-agent attempt per
example, including reward 0, native controls, validated exact selection, readable
HTML/Markdown/task documentation, and verified rerun links. Retain proof that
these are supplied synthetic artifacts, not newly executed model evaluations.

**Inspect:** The first-evaluation journey is complete despite the low score. The
results response includes the exact invitation, once:

> Would you like to see how you could improve your suite further? We can audit its coverage and grading against the behavior described in `ETHOS.md` to identify gaps and worthwhile improvements.

**Yes variant:** Accept the offer. The existing audit flow receives current
`ETHOS.md` confirmation, setup, accepted task/config paths, native run evidence,
selected sources, and limitations. It asks only audit-specific unresolved
questions; it does not repeat shared onboarding or silently choose trace sources.
If audit traces are unavailable, the audit shows **Blockers** with the missing
source and next action; the completed creation journey remains complete.

**No variant:** Decline, then request a status update and a task rerun explanation.
Neither reissues the audit offer or starts an audit. Existing rerun instructions
remain linked. A later explicit audit request may start the existing audit flow.

**Incomplete variant:** Remove a required run or review artifact before handoff.
The status is incomplete with **Blockers**, and no completion-only audit invitation
appears. Missing optional ATIF alone is not a creation failure. No new per-task
human review gate or two-success threshold may be added to make completion easier
to describe.

## Recorded walkthrough — 2026-09-28

A separate agent exercised the updated skill in an isolated synthetic workspace
with scripted user replies. This was a local conversation and draft exercise,
not a paid model evaluation or proof of real-agent performance. The repository
test suite separately passed 637 tests with 7 skips: three need a running Docker
backend, one needs the optional Ethos plugin, one needs live model authorization,
and two need extended-attribute support.

This historical walkthrough predates the requirement to verify the selected
runtime before full creation or improvement onboarding proceeds to `ETHOS.md`,
and the deterministic first-task approval sequence above. Its historical use of
“pilot” describes a reduced suite, not the current first-task review gate.
Do not treat it as a pass of the current first-evaluation contract.

The walkthrough's README named `runs/selected/records.json` and
`runs/archive/records.json`; an old note claimed 200 selected records. Its Ethos
defined a complaint as a current unresolved problem with the speaker's own
support experience. The selected export actually contained these eight inputs:

| ID | Synthetic input | Reviewed semantic basis |
| --- | --- | --- |
| s1 | My ticket is still unresolved after a week. | Current unresolved complaint |
| s2 | Thanks for listening. I still cannot log in after your fix. | Politeness does not resolve the complaint |
| s3 | Thanks, everything works now. | Resolved experience |
| s4 | Your guide quotes a user saying "this is terrible". Where is that guide? | Another speaker's quotation alone is not a complaint |
| s5 | I was frustrated yesterday, but support fixed it and I am happy now. | Historical frustration explicitly resolved |
| s6 | The command says fatal error. What does that message mean? | Technical wording alone is not a complaint about support |
| s7 | Wonderful, another week with no reply to my ticket. | Sarcasm expressing a current complaint |
| s8 | That was bad. | Insufficient context; held unscored |

Candidate scores conflicted with semantics: s2 scored 0.22, s4 0.91, and s5 0.86.
Those scores did not determine the expected labels.

Observed checkpoints and artifact changes:

1. Before selection, the agent read README and Ethos, listed both known sources,
   labeled 200 as unverified, and stopped without opening either export.
2. The user selected all records actually present in the selected file and
   excluded every other source. The agent inspected eight records, saved IDs
   and the source hash in the shared journey state, and reported eight rather than 200.
   It proposed seven labeled examples and kept s8 pending; no tasks existed.
3. The user chose s1/s4 as a two-example pilot, with one task and one attempt per
   example, and made cost a budget priority rather than a score. The agent
   replaced the seven-example proposal with that agreement and saved five
   label-ready expansion gaps plus unscored s8.
4. A side question about attempts produced the explanation that four trials of
   the two inputs remain two examples. The reply returned to the same checklist
   and pending case-preparation action, without changing the corpus or generating
   cases during the detour.
5. The next reply supplied an existing Harbor installation. The agent verified
   Harbor 0.20.0 and its Python environment, then used native scaffolding to
   prepare s1/s4. Both passed Harbor structural checks and twelve local grader
   probes (correct, wrong, missing, invalid, string-valued, and harmless formatting
   variants). It left environment and agent execution stages incomplete and
   explicitly distinguished these probes from Harbor control or agent runs.
6. The user requested adding s2/s5, retaining s1/s4, and using two attempts per
   task. Before generating additions, the agent reviewed four distinct examples,
   four tasks, and eight planned trials per rerun. It preserved cost as a budget
   concern, the selected source, the original labels, and unscored s8. Its
   checklist reopened case preparation without restarting Ethos or setup.
7. The first expansion attempt exposed an ordering failure: all four task
   trees and the 24-probe validation report existed while the saved plan still
   called the expansion pending and retained the pilot's current settings.
   The approval was in the conversation, but the agreement was saved too late.
   The shared checkpoint was revised to require saving the accepted revision
   before the first generation call and replacing or labeling superseded values.
   This initial ordering check failed; later reconciliation does not erase it.
8. The partial handoff reconciled all four agreed examples with four generated
   drafts and structural passes, 24 local grader probes, zero fully validated
   runtime cases, and zero agent executions. Three label-ready records remained
   expansion gaps and s8 stayed unscored. The first three checklist steps were
   checked; environment/integration and execution stayed open. The log and plan
   explicitly retained the ordering failure above.
9. A fresh agent rechecked the revised instruction in a separate workspace,
   starting with those four drafts. The user explicitly accepted adding s6=false
   and changing all tasks to one attempt. Before the first scaffold invocation,
   `plan-immediately-before-scaffold.md` recorded five agreed examples, five
   task mappings, one attempt each, five planned trials, and the unchanged corpus
   and grading. The simultaneous task listing contained only the four retained
   tasks. The plan hash in `before-scaffold.json` was
   `95b9415cb48ba1fa15b986401a3f84326f5af2ea12c34a327d8f6d49a4fd7c9a`.
   Native scaffolding then created the added task. This bounded recheck passed
   the persistence-order requirement; it does not establish model reliability
   across all future conversations. The new task passed Harbor structural checks
   and six direct grader probes; hashes confirmed all 36 retained task files
   were unchanged. Runtime work remained deferred.

Repository validation also passed Ruff lint/format checks, type checks for Python
3.12 and 3.13, copyright and license checks, whitespace checks, and a prepared
signed-off commit-message check. The generic skill-creator validator rejected
this repository's pre-existing extended frontmatter fields; the repository's
own skill contract checks passed. No live model evaluation was performed.

## Consistent provider selection

**Start:** Supply the support agent with reviewed `ETHOS.md`, no evals, no traces,
and a working native NeMo Gym installation. Explicitly request NeMo Gym first evals.
Answer the existing milestone and scope questions.

**Inspect:** The agent honors the choice, verifies NeMo Gym, and prepares native
manifest, component, dataset, and verifier files with no audit report. Native
validation and positive/negative controls remain separate from actual agent
execution. A missing actual-agent integration is reported as a blocker, never
replaced by a passing run of NeMo Gym's example agent. The suite README retains NeMo Gym
commands, selected rows, agreed attempts, and fresh result paths.

**Provider matrix:** Exercise the same selection cases for first-eval,
audit-derived task creation with an eligible synthetic gap, and trace-derived
creation with an eligible synthetic trace:

- Explicit NeMo Gym or Harbor: honor the choice even when the existing suite differs.
- No explicit choice, existing NeMo Gym or Harbor suite: preserve that provider.
- No explicit choice or existing suite: default to Harbor, even for NeMo Gym traces.
- Selected runtime missing or incompatible: preserve progress and report the setup
  need without switching providers. Full creation or improvement onboarding stops
  before `ETHOS.md` with **Blockers**; explicit planning-only requests retain their scope.
- User selects NeMo Gym with Harbor: recognize compatibility, record each tool's role,
  and check the requirements of that setup.

For Harbor, retain native scaffolding and NOP/Oracle checks. For a mixed suite,
use the affected cases' setup or ask which cases are targeted when ambiguous.
The user's explicit choice overrides the default. These are manual acceptance
scenarios, not an execution record.

## Recovery regression: endpoint rejects a request parameter

**Start:** In a synthetic workspace, the user requested a Harbor/Docker comparison
of two selected target configurations on eight agreed cases. Use a local scripted
endpoint that rejects `prompt_cache_key`; retain the actual failed Harbor trial.
The user approves a temporary compatibility patch for that endpoint. Keep the
original fixtures and verifiers available. The scripted endpoint tests recovery,
not model quality, and requires no paid model calls.

**Exercise:** Ask the author to finish the comparison. If Harbor execution is
still unavailable, make a local application probe available with seven passing
outputs under a reconstructed Python grader. Ask for a status report and then,
in a follow-up turn, ask whether the whole pipeline is finished. In a second
variant, restore Harbor after the scoped adapter patch and rerun the selected
agents with the original verifiers. Include an actual agent-quality failure.

**Inspect:** Recovery preserves the selected execution path, tasks, fixtures,
and original verifiers. It records the patch and source/configuration versions,
retains failed trials, and uses fresh Harbor jobs for reruns. The blocked variant
leads both reports with diagnostic-only/incomplete status, never “comparison
complete: 7/8 pass.” Local scores stay outside Harbor aggregates. It does not
author task answers or manufacture native job artifacts. Missing execution is
explicit. In the repaired variant, completion cites actual job/trial artifacts,
target-agent settings and native verifier results; the low agent score remains.
Raw rewards and exceptions are preserved while infrastructure failures,
blocked/missing trials and agent-quality failures are explained separately.

Repeat the blocked variant with a native NeMo Gym suite. The reports lead with
**Diagnostic only — NeMo Gym evaluation incomplete**, preserve rollout failure reasons
and health records, and do not substitute a Harbor run or local grader for the
requested NeMo Gym rollouts.

This scenario tests recovery and reporting, not an allegation that the author
fabricated application outputs. Static checks of this file do not establish
that a live author passed the scenario.
