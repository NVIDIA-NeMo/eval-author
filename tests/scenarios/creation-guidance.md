<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Creation guidance conversational acceptance scenarios

Use this manual protocol to review ASE-1027 and ASE-1031 behavior. It is not an automated
harness, an execution record, or a claim of passing evaluation. Use isolated
synthetic workspaces with the sibling skills available, without customer data,
production services, provider credentials, or paid model calls. Retain reply
channels, user answers, tool and delegation logs, and resulting artifacts. Check
what the agent did, not only what its final answer says.

## Shared setup and review rules

Provide a tiny support agent with a reviewed `ethos.md` defining complaint
detection and policy-grounded answers. Its synthetic complaint definition counts
current first-person dissatisfaction; a quotation about another person's complaint
or explicitly resolved historical frustration alone does not qualify. Give source
candidates in a named README: `traces/recent/`, `traces/archive/`, and a small
`examples/demo/` fixture set. Include stable record IDs and conflicting classifier
scores. Counts in the README are claims to verify after selection, not evidence
that every record is available or usable.

For fresh starts, request first evals and follow the normal opening and milestone
check-ins. For staged tests, supply explicit conversation history showing the
opening accepted, Ethos reviewed, selected-runtime readiness checked or explicitly deferred,
and the user's statement that no evals exist. State which milestone transition
was answered and which source or scope choice remains open. File presence alone
does not establish this history or runtime readiness. Reuse applicable history
without restarting onboarding.

At each checkpoint inspect the five-step checklist, current stage, unfinished
work, and next question. Unanswered scope choices must stop fixture, instruction,
and grader generation, including delegated work. Save decisions and progress in
`.eval-author/first-eval.md`; audit-based proposals use
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

## 2. Review priorities, breadth, and cost before generation

**Start:** Resume at scope planning with scenario 1's confirmed corpus. Have the
agent propose four complaint examples and two policy-answer examples, with
variations, difficulty, expected outcomes, grading, and exclusions.

**Inspect before answering:** In the plan-review reply itself, the agent explains
that a pilot is a limited first set of eval cases, what it is intended to help
learn, what passing would and would not establish, and why the proposed breadth
fits that purpose. Linking to a saved explanation is insufficient. The reply
explicitly invites actionable feedback on priorities such as lower ongoing cost,
broader coverage, difficult cases, or another user concern; a generic “does this
look right?” alone is insufficient. Judge the meaning, not exact wording.

**Reply at the scope checkpoint:** “Make rerun cost a priority. Start with one
example per behavior, one task per example and one attempt per task. Cost is a
budget consideration, not a passing score. Keep the rest as future expansion.”

**Inspect:** The agreement changes to two distinct examples, two tasks, and two
agent trials per rerun, with controls and any judge work accounted for separately.
The agent explains what the pilot omits and asks only about unresolved choices.
The saved plan retains the user's priorities and feedback before generation.
Inspect the saved file immediately before the first fixture/scaffold write:
accepted counts and attempt settings must already be current, not left as
"pending" until handoff or recorded only in a conversation log.
No monetary estimate is invented when pricing or comparable measurements are
missing. Increasing attempts does not increase distinct-example counts; neither
renaming an example nor reusing it under another behavior increases the unique
total. A broad plan is not silently reduced to the pilot without this answer.
Repeat with the cost preference and two-example scope already agreed in the
conversation: the agent applies them and continues authorized preparation,
without repeating the priorities question or adding another approval loop.

## 3. Pilot, then meaningful expansion

**Start:** Continue scenario 2 through the stages that can actually be exercised.
Record the resulting tasks and available validation or execution evidence. A
failed agent response remains a useful baseline when its task works.

**Reply:** “Retain both pilot examples. Add three distinct complaint inputs,
including confusable negatives, and one new policy question. Review the added
coverage and ongoing cost with me before generating them.”

**Inspect:** The new plan maps two retained and four added examples to six unique
inputs: four complaint and two policy-answer examples. It explains the new
variation, difficulty, expected outcomes, and cost rather than copying the pilot
size or counting repeats as expansion. Source exclusions and existing failing
cases remain intact. Generation waits for the affected scope choices, then the
handoff reconciles each agreed example against what was actually delivered.

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

**A — no traces:** At source selection, answer: “There are no traces. Use Ethos
and synthetic examples.” Planning continues without a trace hunt or manufactured
coverage report. The plan records the choice and marks examples as synthetic.
Missing the selected runtime can leave a useful plan complete while scaffolding
is blocked. Report prerequisites for the user's selected setup.

**B — question detour:** At the scope checkpoint, ask: “Explain why a repeated
attempt is not another input example.” The agent answers, preserves existing
decisions, and returns to the same current stage and pending scope choice. It
does not interpret the question as agreement or begin generation.

**C — setup detour:** Explicitly ask to defer the unresolved scope choice while
checking the documented agent connection. The agent records that deferral and
performs only the agreed independent work. On return it retains the corpus,
cost priority, pilot or expansion state, and unresolved choice. No repeated
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

**Gym counting variant:** Use a Gym draft with four distinct selected dataset
rows, one agent configuration, and two repeats per row. The scope review records
one draft, four distinct examples, and eight planned agent executions. It must
not apply Harbor task-attempt counts to the whole Gym draft or count repeats as
new examples. Provider selection and row selection precede the final cost estimate.

## Recorded walkthrough — 2026-09-28

A separate agent exercised the updated skill in an isolated synthetic workspace
with scripted user replies. This was a local conversation and draft exercise,
not a paid model evaluation or proof of real-agent performance. The repository
test suite separately passed 637 tests with 7 skips: three need a running Docker
backend, one needs the optional Ethos plugin, one needs live model authorization,
and two need extended-attribute support.

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
   and the source hash in `first-eval.md`, and reported eight rather than 200.
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

**Start:** Supply the support agent with reviewed Ethos, no evals, no traces,
and a working native Gym installation. Explicitly request Gym first evals.
Answer the existing milestone and scope questions.

**Inspect:** The agent honors the choice, verifies Gym, and prepares native
manifest, component, dataset, and verifier files with no audit report. Native
validation and positive/negative controls remain separate from actual agent
execution. A missing actual-agent integration is reported as a blocker, never
replaced by a passing run of Gym's example agent. The suite README retains Gym
commands, selected rows, agreed attempts, and fresh result paths.

**Provider matrix:** Exercise the same selection cases for first-eval,
audit-derived task creation with an eligible synthetic gap, and trace-derived
creation with an eligible synthetic trace:

- Explicit Gym or Harbor: honor the choice even when the existing suite differs.
- No explicit choice, existing Gym or Harbor suite: preserve that provider.
- No explicit choice or existing suite: default to Harbor, even for Gym traces.
- Selected runtime missing: retain useful planning and report the setup need;
  do not silently switch providers because another runtime is installed.
- User selects Gym with Harbor: recognize compatibility, record each tool's role,
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

Repeat the blocked variant with a native Gym suite. The reports lead with
**Diagnostic only — Gym evaluation incomplete**, preserve rollout failure reasons
and health records, and do not substitute a Harbor run or local grader for the
requested Gym rollouts.

This scenario tests recovery and reporting, not an allegation that the author
fabricated application outputs. Static checks of this file do not establish
that a live author passed the scenario.
