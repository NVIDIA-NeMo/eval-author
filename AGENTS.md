<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agent instructions

## Documentation Rules

This repo authors agent skills (`skills/*/SKILL.md`, `references/`, bundled `scripts/`), not an API or database. The skill files are the instructions agents run; `docs/` and the README are for humans who install and use the skills. Keep the two separate.

### When to Write/Update Documentation

- **User-facing behavior changes:** A new, renamed, or removed workflow, trigger prompt, command, flag, output file, or prerequisite that a user sees or must act on. Update the existing page in `docs/` (or the README) that covers it instead of creating a new file.
- **Install or requirement changes:** New runtime, version pin, or setup step. Update `docs/getting-started.md` and, for release-worthy changes, `CHANGELOG.md`.
- **Report or artifact format changes:** If the shape of generated reports or `.eval-author/` artifacts changes, update the doc that describes them (e.g. `docs/results.md`).
- **Non-obvious decisions:** A design choice or constraint a future maintainer can't recover from the code or git history goes in `docs/dev-notes/`. Include a short inline comment next to workarounds for upstream bugs.

### When NOT to Write Documentation

- **Skill instructions are not docs:** Agent-facing prompts, procedures, and rules belong only in the relevant `SKILL.md` or `references/*.md`. Never copy them into `docs/`, the README, or a new markdown file.
- **No docs for internals:** Skip docs for refactors, test changes, helper scripts, and wording tweaks to skill prompts that don't change what users see or do.
- **Don't narrate code:** No docstrings or comments that restate self-explanatory code, and no changelog-style prose or summary files unless asked.
- **When unsure, don't add it:** Ask whether a user would be confused or blocked without it. If not, leave it out.

## Avoiding duplication

Review every change for repeated content: instructions, setup steps, workflow lists, requirements, code, and tests. Consider whether it needs to repeat at all. If not, remove it and link to the single source of truth. If it must repeat and runs longer than a single line, put it in one file and reference it from the other places:

- Skill instructions shared across workflows go in a file in `references/`, linked from each `SKILL.md`.
- User-facing details live in one `docs/` page; other docs link to it.
- Shared logic goes in one script or helper rather than being copied between skills.

## Copyright headers

Every new `.md`, `.py`, `.sh`, `.toml`, `.yaml`, and `.yml` file needs the NVIDIA Apache-2.0 header. After creating NVIDIA-authored files, run `make update-copyright-headers`, then `make check-copyright-headers` to verify. In a `SKILL.md`, the header goes after the front matter. Don't add headers to the exceptions listed in `tools/check_copyright_headers.py`, such as signed NVSkills reports and templates copied into users' repos.
