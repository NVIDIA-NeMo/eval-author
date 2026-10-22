<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos creation conversational acceptance scenarios

Use this manual protocol to review Ethos creation. It is not an automated harness,
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

## 2. Eval Author delegates to the canonical orchestrator

**Start:** Supply selected agent context, prior answers with provenance, and
shared prerequisite progress. Request continuation for first-eval, audit, and expansion.

**Inspect:** Eval Author maps its state into ordinary Ethos conversation context:
repository, agent, selected document path, findings, prior answers with provenance,
exact draft and confirmation, and unfinished Ethos work. Journey names, checklist
rules, hashes, and the shared state layout remain inside Eval Author. Only
unresolved intent is asked; the canonical workflow owns exploration and writing.
Confirm draft content before writing. Eval Author maps the reported path,
validation, outstanding work, and user confirmations back to shared journey state
and computes the saved content digest itself.
An existing document is linked and summarized for the user's confirmation that
it is correct and current on shared entry, even when an earlier flow confirmed unchanged
content. After that reply, continuations reuse it without another interview.
The three-item shared prerequisite orientation is not repeated inside Ethos.

## 3. Unusable canonical Ethos skills and recovery

Test a missing `ethos-orchestrate/SKILL.md`, a listed skill whose symlink target
was removed, and a readable `ethos-write/SKILL.md` with its required validator
missing. The prerequisite pass detects each before Ethos confirmation, finishes
other accessible checks, and shows **Blockers** with the actual cause. Denied
access is Unknown rather than proof of a missing file. Keep setup incomplete and
omit Next Steps and Ethos guidance. Provide the canonical NVIDIA skills link and
installation command for all three Ethos skills, with the actual assistant and
installation scope. Do not install during checks or use a fallback interview or writer.

**Recovery variant:** Resume a current flow where the user already confirmed the
exact document or draft and destination before skill access failed. Repair the
skill installation without changing that content. Recheck its files and resources,
retain the confirmation reply and reviewed content binding, and resume canonical
validation without asking for the same approval again. A listing alone is not
enough to clear the blocker. Missing validation still prevents completion. If the
document, agent, destination, or material validation findings change during repair,
reopen the affected review. A runtime probe or provider installation does not
repair missing skill access.

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
