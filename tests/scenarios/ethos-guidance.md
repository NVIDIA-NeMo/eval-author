<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos creation conversational acceptance scenarios

Use this manual protocol to review ASE-1058. It is not an automated harness,
an execution record, or a claim of passing model evaluation. Use isolated
synthetic workspaces without customer data, production services, or provider
credentials. Retain reply channels, user answers, tool and delegation logs,
and resulting artifacts. Judge the meaning of questions and actions, not
exact wording or the final answer alone.

## Shared setup and review rules

Provide a small account-support agent whose README and source identify its
prompt, configured model, tool registry, workflow, and current validation
checks. It can look up synthetic account records and request a password
reset. Its intended behavior requires verifying identity before any reset,
refusing to reveal account secrets, and escalating ambiguous ownership.
Its confirmed external constraints require the internal inference gateway,
customer data staying in the approved region, and security review before
shipping a new tool with write access. These are durable safety and operating
boundaries, not a request to change the application.

Use these interview answers when the skill asks about intent:

- Purpose and outcome: help users regain account access safely; successful
  verified recovery and appropriate escalation are the outcomes.
- Principles: when account ownership is uncertain, protect the account and
  escalate instead of guessing.
- Vision: no additional use cases are planned.
- Trade-offs: identity verification and correctness are hard gates; favor
  successful recovery over lower latency or cost.

Across every creation path below, inspect all questions, options, draft
sections, saved content, and handoffs. Fail the scenario if creation asks
whether prompts may be tuned, models changed, tools added or removed,
validation changed, or workflow logic refactored. Renaming that interview
to optimization scope, editable parts, or another permission list still
fails. Questions about intended tool behavior, actual validation, safety,
or confirmed external policy remain appropriate.

New files must contain the template's 14 required sections, including Scope,
Behavior, Constraints, and Open Questions, with no catalog of modification
permissions. Unknown intent stays explicit. Neither creating the document nor
omitting a permission section grants authority to
modify the application. Compare application files before and after each run;
prompt, model, tool, validation, and workflow files must remain unchanged.
Run the bundled validator when available and retain its actual result.

## 1. Standalone Ethos creation

**Request:** "Create an ETHOS.md for this account-support agent from its source
and my answers."

**Inspect:** The exploration reads the source, collects unresolved intent,
and presents a complete draft for review. Answer intent questions one at a
time using the shared setup. The interview can confirm the gateway, data,
and security-review boundaries; it does not ask for permission to optimize
or edit the prompt, model, tools, validation, or workflow. The draft and
saved file preserve the identity-verification rule, refusal and escalation
behavior, external constraints, and quality priorities.

**Continue:** Review the draft and accept its content. Inspect the handoff
to ethos-write, its saved file, and its validation result. A new document
passes with all 14 sections and no permission catalog. The final handoff identifies
the document and any unresolved intent without implying implementation work
was approved or performed.

**Unknown-constraints variant:** Remove the supplied external-policy answers
and answer "I don't know" when asked. The file records that uncertainty and
its effect in Open Questions, without treating missing constraints as blanket
permission to change providers, costs, or implementation. The known behavior
and safety rules still appear.

## 2. Eval Author delegates Ethos creation

**Start:** Supply explicit history showing a first-eval opening was accepted,
the account-support agent was selected, and no Ethos file exists. Resume:
"Continue with the Ethos for this agent."

**Inspect:** Eval Author introduces Ethos and hands the selected agent,
available answers, local path, and review state to the Ethos skills. Follow
scenario 1's interview and review. The delegation does not add a permission
questionnaire, and the returned document preserves the shared behavior and
constraints. The saved content is reviewed before Eval Author uses it.

**Continue:** Accept the saved content and inspect the return to the next
unfinished Eval Author milestone. The handoff reports the checked path,
remaining gaps, and review state. It does not restart the intent interview,
introduce optimization permissions, or alter application files.

**Audit variant:** Repeat with history showing a guided audit opening was
accepted and Ethos is its current milestone. Confirm that creation behaves
the same and returns to the audit's next unfinished milestone.

## 3. Eval Author fallback with Ethos skills unavailable

**Start:** Use scenario 2's first-eval history, but make ethos-explore and
ethos-write unavailable while keeping Eval Author's Local Ethos procedure
and the template readable. Resume the Ethos milestone.

**Inspect:** The fallback explains how to install the unavailable skills and
continues local capture through the documented procedure. It reuses supplied
answers and asks only for missing intent. Neither a missing skill nor its
fallback causes an optimization-permission interview. The saved file meets
the same 14-section contract and preserves all confirmed safety, scope, and
external constraints before review and handoff.

**Validation variants:** Run once with the bundled validator available and
once without it. The former retains an actual parser result; the latter
reports structural inspection honestly. Both check the full document and
do not require a permission section or infer authorization from its absence.

## 4. Preserve a legacy permission section during a targeted update

**Start:** Provide a valid reviewed Ethos with all 14 current sections plus
a legacy permissions section and a custom Local Notes section. Give the
permissions section distinct existing values, such as model changes forbidden
and prompt changes requiring the service owner's approval. Preserve a copy of
the file.

**Request:** "Update the role sentence to say this agent supports contractors
as well as employees. Preserve the other content."

**Inspect:** The targeted update preserves the legacy permissions and Local
Notes content, creation metadata, and confirmed safety constraints. Validation
accepts the extra sections. The skill does not delete the legacy section to
match the new template, reinterpret its contents as a new grant, or reopen
permission questions for unrelated fields. The revised Role and update
metadata are reviewable, and application files remain unchanged.
