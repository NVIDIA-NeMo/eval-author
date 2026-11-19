<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agent instructions

## Documentation Rules

This repo authors agent skills (`skills/*/SKILL.md`, `references/`, bundled `scripts/`), not an API or database. The skill files are the instructions agents run; `docs/` and the README are for humans who install and use the skills. Keep the two separate.

### When to Write/Update Documentation

- **User-facing behavior changes:** A new, renamed, or removed workflow, trigger prompt, command, flag, output file, or prerequisite that a user sees or must act on. Update the existing page in `docs/` (or the README) that covers it instead of creating a new file.
- **Install or requirement changes:** New runtime, version pin, or setup step. Update `docs/getting-started.md` and, for release-worthy changes, `CHANGELOG.md`.
- **Report or artifact format changes:** If the shape of generated reports or `.eval-author/` artifacts changes, update the doc that describes them (e.g. `docs/results.md`).
- **Published Dev Notes:** `docs/dev-notes/` is the published Dev Notes series, not a general-purpose design or development documentation directory. Add or update content there only when explicitly asked to author or revise a published developer note. Do not put routine design decisions, implementation notes, plans, or working documentation there.

### When NOT to Write Documentation

- **Skill instructions are not docs:** Agent-facing prompts, procedures, and rules belong only in the relevant `SKILL.md` or `references/*.md`. Never copy them into `docs/`, the README, or a new markdown file.
- **No docs for internals:** Skip docs for refactors, test changes, helper scripts, and wording tweaks to skill prompts that don't change what users see or do.
- **Don't narrate code:** No docstrings or comments that restate self-explanatory code, and no changelog-style prose or summary files unless asked. Include a short inline comment next to workarounds for upstream bugs.
- **When unsure, don't add it:** Ask whether a user would be confused or blocked without it. If not, leave it out.

## Avoiding duplication

Review every change for repeated content: instructions, setup steps, workflow lists, requirements, code, and tests. Consider whether it needs to repeat at all. If not, remove it and link to the single source of truth. If it must repeat and runs longer than a single line, put it in one file and reference it from the other places:

- Skill instructions shared across workflows go in a file in `references/`, linked from each `SKILL.md`.
- User-facing details live in one `docs/` page; other docs link to it.
- Before implementing common behavior, search for an existing standard-library function, supported dependency API, or repository utility. Reuse it, or extend the owning helper when appropriate, rather than adding a parallel implementation.
- Shared logic goes in one script or helper rather than being copied between skills. Consolidate duplicate code touched by the change when practical; keep shared runtime utilities inside the installed skill trees.
- Extract helpers around genuinely shared behavior, not superficial similarity. Keep abstractions small and avoid unrelated refactors.

## Unit tests

- Add or update unit tests for new or changed behavior, including regression tests for bug fixes. Run the affected tests; deduplication is not a reason to reduce coverage.
- Search for existing coverage, fixtures, factories, and test helpers before adding another test or setup path. Extend or parameterize an existing test when the behavior is the same and only inputs or expected results differ.
- Consolidate duplicate unit tests and repeated setup or assertions where practical. Reuse fixtures, factories, and helpers wherever they fit the behavior, at the narrowest useful scope; keep test intent and failures easy to understand.
- A test may contain multiple related assertions; there is no one-assertion-per-test rule. For example, check two related fields of the same returned object in one test when they describe the same scenario, rather than repeating setup and execution in separate tests. Keep unrelated behaviors or scenarios separate, and make each assertion's failure clear.
- Preserve distinct behavioral cases, edge cases, failure paths, and integration boundaries when deduplicating. Keep expected results independent of the production logic under test; do not compute expectations with the same helper being checked.
- Keep unit tests focused: use the smallest valid fixture that exercises the behavior, rather than rebuilding a full demo or invoking a CLI for each field assertion. Retain dedicated integration and end-to-end tests for the real producer, CLI, and replay paths.
- Reuse expensive read-only setup and generated artifacts through appropriately scoped fixtures where isolation and artifact identity allow it. Parameterization alone does not eliminate repeated setup. Keep mutable cases isolated and retain independent executions when testing determinism, fresh-process behavior, or replay; copied output is not proof of a second run.
- Measure setup and call durations before optimizing, then compare affected-test and full-suite wall time on comparable runs. Preserve behavioral coverage and report skips and unavailable runtimes separately from passes; do not claim a speedup by omitting cases, adding skips, or weakening assertions.

## Research artifact compatibility

This is a research project. Do not add speculative format versions, migration code,
or backward-compatibility branches for reports and artifacts that can be regenerated.
Validate the current format and change it directly when needed. Keep actual runtime
requirements and evidence-integrity checks.

## Types and validation

- Prefer explicit parameter and return types when writing or changing scripts and code.
- Prefer existing Pydantic models over plain dictionaries for structured records when the supported runtime permits it, especially for external inputs and persisted records that need validation. Propose a reusable Pydantic model when no suitable model exists; do not create a competing schema for the same data.
- Use `TypedDict` or dataclasses when runtime validation is unnecessary or Pydantic is not suitable. Plain dictionaries remain appropriate for genuinely dynamic key-value mappings. Type annotations alone do not validate data.
- Reuse existing models and owning validators. Preserve supported formats, strictness, and unknown-evidence handling rather than introducing competing schemas or silent coercion.
- Keep standalone scripts dependency-light. Use standard-library types and explicit validation when Pydantic is not part of the supported runtime; propose any dependency change explicitly rather than adding it solely to replace a dictionary.
- Run the affected tests and configured type checks after changing a data contract.

## Conversation rendering

Use the existing `skills/eval-author/scripts/conversation_renderer.py` entry point.
Keep shared formatting and escaping in `conversation_formatting.py` and shared
labels and headings in `conversation_templates.py`. Flow-specific rendering and
copy belong in `conversation_flows/<flow>.py` and `<flow>_templates.py`, with
the flow's instructions in `references/conversations/<flow>.md`.

Fixed conversation copy has one source of truth in its template module; link to
it instead of duplicating it in skill instructions. When copy changes
intentionally, update the affected fixture expectations in the same change.
Follow the [shared rendering contract](skills/eval-author/references/conversation-rendering.md)
for input authority, workflow boundaries, and extension guidance.

## Copyright headers

Every new NVIDIA-authored source or markdown file needs the NVIDIA Apache-2.0 header, and CI fails without it. Follow [DEVELOPMENT.md](DEVELOPMENT.md) to add headers and [CONTRIBUTING.md](CONTRIBUTING.md) for the checks to run before opening a PR. Don't hand-write headers or copy them from this file.
