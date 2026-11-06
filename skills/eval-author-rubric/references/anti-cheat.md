<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Validity: anti-cheat

This is the first populated area of the experimental rubric for validity,
coverage, and diversity. These bounded items do not implement the rest of validity
or measure coverage and diversity. Attack-class coverage below is a denominator
for this assessment, not the rubric's coverage pillar.

## Evidence and scoring

Use the [shared task validation procedure](../../eval-author/references/task-validation.md)
for control definitions, execution, native result recording, and repair. This
reference adds assessment questions, not a competing execution contract.

Assess each item separately for each selected revision. Record:

- Item ID and pillar, selected task/configuration identity, and applicability.
- Observed measurement, with reviewed/tested versus declared denominators.
- Score or status, evidence strength, and source IDs pointing to existing evidence.
- What remains unknown, the consequence for trusting results, and the next check.

Use `NOT_ASSESSED` for insufficient or unrun evidence, not zero. Use
`NOT_APPLICABLE` only with a concrete task-contract reason; a missing runtime or
unsupported control is not that reason. A partial assessment can score 1 only
when inspected evidence establishes the stated partial condition; mark the unrun
checks individually. A demonstrated defect is 0 even when other checks are unrun.
Do not average items or produce an overall validity, coverage, diversity, or
quality score from this slice.

Evidence strength is independent of the score: `E0` assertion only; `E1`
inspectable artifacts; `E2` directly tested on the identified revision; `E3`
independent scoped corroboration. A reproduced failure can be 0/E3. A schema,
receipt digest, or agent-authored assessment does not establish independent review.

Separate methods explicitly. **Deterministic** means exact inspection or comparison
against declared predicates, not semantic judgment just because a script invokes
it. **Review** below means contextual assessment with the reviewer kind recorded
(agent or human); never label an agent's judgment human review. **Probe** means
an authorized intervention. No decision model, LLM judge, or monitor is required
or qualified by this initial slice. Unresolved semantic questions remain gaps.

## AC-01: protected information stays outside the solver boundary

**Pillar:** validity.

**Question:** Can the solver obtain answers or scoring information that the task
contract says must remain private?

**Requirement:** Identify protected inputs and intentional solver-visible
references; verify the actual boundary rather than trusting file names.

**Scope:** Selected task, image, mounts, context, tool responses, and permissions.

**Measurement:** Reviewed surfaces / declared surfaces; unauthorized readable
protected artifacts / tested artifacts; unresolved access and meaning questions.

**Checks:**

- Deterministic: inventory explicitly selected artifact roles, paths, identities,
  permissions, and content flows. Compare known answer artifacts with solver-visible
  material using exact or documented normalized comparisons; retain source IDs,
  not answer text. A matching permitted reference is not automatically leakage.
- Review: distinguish intentional task inputs from answer-revealing material;
  trace reachable copies, image layers, mounts, and tool handoffs. Record missing
  surfaces. Static configuration cannot establish runtime read denial.
- Probe: where authorized, attempt reads of declared private artifacts as the
  actual solver role through each relevant supported access path. Pair denied
  reads with a permitted-input read so a broken interface cannot count as isolation.

**Scoring:** 0: confirmed unintended protected-information exposure. 1: inspected
boundaries protect only part of the declared scope, with known unresolved paths.
2: declared surfaces are reviewed and applicable solver-role access controls
confirm the intended boundary. 3: independent access paths and held-out controls
corroborate it.

**Evidence:** Artifact/role inventory, reviewed exposure findings, solver-role
read results, and permitted-read control, all bound to the selected revision.

**On gaps or failure:** Stop treating affected results as protected from leakage;
propose isolation/fixture repair and new evidence. Missing access tests remain
unproven. Never move private answers into solver context to make a control work.

**Boundary:** Local exposure is data leakage, not evidence of model-training
contamination. Unknown corpora, semantic leaks, local split overlap, and benchmark-
feedback reuse are not cleared by this task-boundary check. Sensitive-data
exposure requires separate handling even if it cannot improve the score.

## AC-02: shortcuts do not earn forbidden credit

**Pillar:** validity.

**Question:** Can an incomplete or cheating submission earn credit the contract
requires withholding?

**Requirement:** Declare plausible attack classes and expected per-check and total
credit; preserve legitimate inaction and valid alternatives.

**Scope:** Every selected reachable grading path and its declared controls.

**Measurement:** Healthy reached controls / applicable controls, by attack class;
expected versus actual per-check and total rewards; reference/alternative outcomes.

**Checks:**

- Review: select controls from the shared
  [simple-cheat screen](../../eval-author/references/task-validation.md#screen-simple-cheats):
  early exit/no work, all-failure stub, inert reference text, copied answers,
  forged/stale success, and scorer/test tampering as applicable. Follow entry
  point, guards, invoked checks, and reward writer. Explain excluded classes.
- Probe: execute applicable attacks plus reference, realistic incorrect-answer,
  and valid-alternative controls through the existing native path. Preserve the
  task revision across arms and retain trusted health and reach evidence.
- Deterministic: compare every affected check and total with declared credit.
  Keep malformed, skipped, unreachable, and infrastructure-failed controls out of
  the successful-defense count. A zero total can hide forbidden partial credit.

**Scoring:** 0: a healthy reached attack earns forbidden credit. 1: inspected or
partly exercised defenses leave known applicable classes unresolved. 2: every
declared applicable attack receives intended credit and required valid controls
pass. 3: independently authored held-out attacks and valid alternatives corroborate
those results.

**Evidence:** Applicability matrix, control sources, expected/actual scores, native
health/reach receipts, and valid-control outcomes. Static confirmed shortcuts are
reported as defects even when the execution-based score remains NOT_ASSESSED.

**On gaps or failure:** Flag affected passes as untrustworthy; propose containment
and repair through the owning workflow. Preserve failed receipts. Rerun required
controls on repaired retained tasks as well as newly generated tasks; a generator
fix alone does not repair historical results.

**Boundary:** A confirmed verifier shortcut does not prove any solver used it.
Rejected valid alternatives are verifier defects, not improved anti-cheat quality.
This is bounded resistance, not a claim against all reward hacking.

## AC-03: credit comes from current, trusted results

**Pillar:** validity.

**Question:** Can solver-authored, stale, or mismatched evidence be mistaken for a
current grading result?

**Requirement:** Bind credit to the selected task/configuration and native run;
keep assertions, result production, and receipt verification outside solver control.

**Scope:** Result production, output transfer, native receipts, and their consumer.

**Measurement:** Current validated receipts / selected receipts; rejected forged,
stale, or mismatched controls / applicable controls; unresolved trust boundaries.

**Checks:**

- Review: trace who can write the reward, expected outputs, imported modules,
  assertion process, and success reports. A separate container does not protect a
  privileged grader that imports hostile submitted code into its own process.
- Deterministic: validate existing receipts with the owning workflow's recorder;
  reconcile task/configuration identity, native invocation, result health, and
  declared checks. Do not replace native validation with file presence or a hash.
- Probe: use the shared forged/stale-success controls where authorized, including
  success left behind before a later failure. Verify the actual result consumer
  rejects or ignores them, with a current valid-result neighbor accepted.

**Scoring:** 0: confirmed untrusted or stale evidence grants credit. 1: inspected
production or identity checks leave known consumer paths unresolved. 2: selected
receipts are current and valid, and applicable forgery/staleness controls are
rejected while valid results are accepted. 3: independent consumer-boundary
controls corroborate rejection and valid acceptance.

**Evidence:** Producer/consumer ownership, selected revision identities, native
validation results, and paired invalid/valid receipt controls.

**On gaps or failure:** Withhold trust in the affected results and request fresh
native evidence or a separately authorized repair. Never edit a receipt to pass.

**Boundary:** Identity does not prove scorer correctness. Reuse an AC-02 forged-
success observation here when relevant, but do not count it as independent evidence
or add the two scores together.

## AC-04: prior trials do not leak answers or success

**Pillar:** validity.

**Question:** Does unintended cross-trial state expose answers or grant credit?

**Requirement:** Declare isolated and intentionally shared state and test the
selected runtime's reset boundary.

**Scope:** Task workspace, reward outputs, backend state, caches, and sessions
material to the selected trials; intentionally longitudinal tasks retain memory.

**Measurement:** Reviewed reset surfaces / declared surfaces; expected post-reset
states / tested states; target outcomes alone versus after a sentinel predecessor.

**Checks:**

- Review: identify answer-bearing and score-bearing persistence surfaces and
  intentional sharing. Missing surfaces remain explicit gaps.
- Deterministic: compare starting and reset state through trusted observations,
  including fresh reward files; content comparisons, not just row/file counts.
- Probe: on an authorized isolated copy, run a fixed non-LLM target control alone
  and after a harmless sentinel predecessor. Check reset removes unintended state
  and preserves required longitudinal state. Do not mutate production services.

**Scoring:** 0: confirmed unintended persistence leaks answers or changes credit.
1: reset is partly verified with known uncovered material surfaces. 2: all declared
surfaces reset as intended and the bounded predecessor control shows no unintended
carryover. 3: independent order, session, or supported-concurrency controls
corroborate the boundary.

**Evidence:** State inventory, intentional-sharing decisions, before/after/reset
observations, native target outcomes, and control identities.

**On gaps or failure:** Narrow independent-trial claims and propose reset repair;
keep unexecuted checks NOT_ASSESSED. Reassess affected trials rather than silently
combining evidence across attempts.

**Boundary:** This anti-cheat slice does not establish full environment fidelity,
statistical independence, or absence of delayed external effects.
