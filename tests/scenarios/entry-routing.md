<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Entry routing conversational acceptance scenarios

Use these scenarios to review Eval Author's first response to onboarding and
expansion requests. This is a manual protocol, not an execution record or a
claim of passing model evaluation. Use fresh, isolated synthetic workspaces
with the sibling skills available and no customer data or provider credentials.
Retain replies, channels, tool calls, and delegation logs. Judge the selected
workflow and actions, not exact phrasing.

The [offline scope checker](../../docs/workflow-scope-checks.md) makes a subset
of these route and effect expectations executable against normalized captures.
Its synthetic tests are not a record of passing conversational replay.

## 1. Fresh onboarding to expand existing evals

**Request:** "help me onboard to expand my evals"

**Inspect:** The agent selects the guided audit and explains that coverage
findings will guide proposed additions. Its first turn ends with the audit's
five unchecked steps, descriptions, **Understand your agent** marked current,
and the opening question. Use the opening checks in
[audit guidance](audit-guidance.md#shared-setup-and-review-rules), including no
repository inspection, Ethos work, runtime probes, or delegation before the
user answers. Reading the core and audit skill is sufficient for the opening.
No preliminary inventory, generic advice, workflow menu, demand for an existing
report, or question asking whether evals exist replaces that guided response.

**Variants:** Repeat with "Walk me through improving the evaluations I already
have" and "I'm new to Eval Author; help me get started expanding our test
coverage." Repeat with an existing suite path supplied. The path settles an
input; it does not change the opening or permit inspecting it early.

**Continue:** Accept the opening and exercise the audit milestones. Preserve
expansion as the requested outcome through review of the coverage report and
the existing proposal handoff. Missing run evidence must remain an explicit
measurement limitation; it does not turn the request into first-eval creation.
After the proposal handoff is accepted, reuse the reviewed findings and agreed
sources to propose additions without restarting the audit or creating tasks.

## 2. Broad help starts guided onboarding

**Request:** "help me with evals"

**Inspect:** Give a short welcome and ask one focused question about building
first evals, improving an existing suite, or finding out what exists. End the
turn and wait before repository inspection, runtime probes, or delegation.
Do not start inventory automatically, choose an audit without a known goal,
or replace the welcome with documentation links or internal skill names.

**Variants:** Repeat with "help me with my evals", "how do I use Eval Author?",
"Help me onboard to Eval Author", and "get me started" in an established
Eval Author context. The word "onboard" is not required. Repeat with a
repository path supplied but no goal: a path alone must not select inventory.

**Reply variants:** "I have no evals yet" selects the first-eval opening;
"I have evals and want to expand them" selects the audit opening;
"I don't know whether this repo has evals" selects discovery inventory.
Do not ask the starting-point question again once answered.

**Discovery continuation:** Present the findings and documented run guidance,
then offer the applicable audit or first-eval path. An unresolved location calls
for the next source question. Wait for the next choice unless it was already
requested; inventory alone does not authorize authoring or evaluation runs.

## 3. Narrow operations keep their scope

**Request variants:** "Only find the evals and their run commands"; "Only check
whether my Harbor evals run"; "Only validate this supplied audit.md."

**Inspect:** Select discovery inventory, discovery readiness, or audit-spec
validation respectively. None starts a welcome or full guided checklist merely
because the user is new or the request concerns evals.
Discovery findings may offer a guided next step without claiming it was chosen.

**Explicit audit variant:** "Audit my existing evals" still opens the guided
audit directly, with no new routing question or preliminary inventory.

**Explanation-only variant:** "Explain how Eval Author works; don't start a
workflow." Answer the explanation without opening onboarding or scanning files.

## 4. Expansion with established progress reuses it

**Starter-suite history:** Supply conversation history establishing first-eval's
completed opening, reviewed Ethos, verified runtime, confirmed corpus, and
accepted starter cases. Request: "Help me expand the starter suite we built
with more negative cases."

**Inspect:** Resume first-eval scope planning for affected behaviors. Reuse
the plan, source selection, and unchanged prerequisites. Do not restart
onboarding or introduce a coverage audit the user did not request.
Repeat with "Help me onboard to expand the starter suite we built" and the
same established history; onboarding wording must not override resumption.

**Existing-audit history:** Supply reviewed audit findings, their paths, and
agreed evidence scope. Request: "Use this audit to propose additions to my
evals; proposals only."

**Inspect:** Enter dataset proposals using those findings. Do not repeat the
audit opening, require a new audit solely because this is an expansion request,
or create or execute tasks.
Repeat with "Help me onboard to expand my evals using this reviewed audit;
proposals only"; onboarding wording must not override the supplied findings.

**Narrow-addition variant:** "Help me add two negative cases to the starter
suite; don't audit coverage."

**Inspect:** Preserve the selected additions and first-eval scope check-in.
The new broad onboarding route must not override that explicit scope.

## 5. Broad help reuses known intent and progress

**Known-goal variants:** In an initial request or prior conversation, establish
"I have no evals and want to build them" or "I have evals and want to improve
their coverage", then say "help me with evals".

**Inspect:** Open guided authoring or the guided audit directly. Do not ask the
starting-point question when the goal already selects the route. Knowing only
that an eval directory exists is different: ask about the desired outcome,
without asking again whether evals exist or inferring an audit request.

**Return history:** Supply a guided audit with its opening and Ethos milestone
accepted, and its eval/trace-source question still pending. Request: "help me
with evals".

**Inspect:** Resume that source-selection milestone and pending question with
existing progress. Do not restart the welcome, replay the audit opening, scan
unselected traces, or jump to proposals.
