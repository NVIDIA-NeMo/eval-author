# Agent instructions

## Documentation Rules

This repo authors agent skills (`skills/*/SKILL.md`, `references/`, bundled `scripts/`), not an API or database. The skill files are the instructions agents run; `docs/` and the README are for humans who install and use the skills. Keep the two separate.

### When to Write/Update Documentation

- **User-facing behavior changes:** A new, renamed, or removed workflow, trigger prompt, command, flag, output file, or prerequisite that a user sees or must act on. Update the existing page in `docs/` (or the README) that covers it.
- **Install or requirement changes:** New runtime, version pin, or setup step. Update `docs/getting-started.md` and, for release-worthy changes, `CHANGELOG.md`.
- **Report or artifact format changes:** If the shape of generated reports or `.eval-author/` artifacts changes, update the doc that describes them (e.g. `docs/results.md`).
- **Non-obvious decisions:** A design choice or constraint a future maintainer can't recover from the code or git history goes in `docs/dev-notes/`. Include a short inline comment next to workarounds for upstream bugs.

### When NOT to Write Documentation

- **Skill instructions are not docs:** Agent-facing prompts, procedures, and rules belong only in the relevant `SKILL.md` or `references/*.md`. Never copy them into `docs/`, the README, or a new markdown file.
- **No docs for internals:** Skip docs for refactors, test changes, helper scripts, and wording tweaks to skill prompts that don't change what users see or do.
- **Don't duplicate:** Link to the existing page instead of restating setup steps, workflow lists, or overviews. Don't create a new file when an existing one covers the topic.
- **Don't narrate code:** No docstrings or comments that restate self-explanatory code, and no changelog-style prose or summary files unless asked.
- **When unsure, don't add it:** Ask whether a user would be confused or blocked without it. If not, leave it out.
