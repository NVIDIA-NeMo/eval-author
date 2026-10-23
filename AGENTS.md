# Agent instructions

## Documentation

Do not add or edit documentation unless the change needs it. Add docs only when a user must act differently: a new or changed user-facing command, flag, config key, or workflow, or a decision future maintainers can't recover from the code (use `docs/dev-notes/`). Update the existing doc that covers the topic instead of creating a new file.

Do not write docs for internal refactors, test changes, or agent-facing behavior. Prompt and skill instructions belong in the relevant `SKILL.md` or `references/` file, not in `docs/` or the README. Don't narrate what the code already says, and don't add changelog-style prose unless asked.
