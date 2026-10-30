# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical copy for orientation, prerequisites, and Ethos onboarding."""

WELCOME = (
    "Welcome to Eval Author! This skill helps you build, audit, and improve evaluations "
    "against your agent’s intended behavior."
)
WELCOME_WITH_CHECKLIST = WELCOME + " Here’s what we’ll work through:"
AUDIT_REUSE_NOTICE = "I’ll reuse your saved answers and check only what’s missing or changed."
AUDIT_ETHOS_REUSE_NOTICE = (
    "Saved ETHOS.md approval will carry forward if the agent, intent, and document are unchanged."
)
GOAL_QUESTION = "What would you like to do?"
GOAL_CHOICES = (
    "Build your first evaluations",
    "Audit coverage, identify gaps, and recommend improvements",
    "Improve your evaluation suite",
)
GOAL_INTROS = {
    "create": "We’ll build your first evaluations. Here’s what we’ll work through:",
    "audit": (
        "We’ll review evaluation coverage, identify gaps, and recommend improvements. Here’s what we’ll work through:"
    ),
    "expand": "We’ll improve your existing evaluations. Here’s what we’ll work through:",
}
READY = "Ready to get started?"
CONTINUATION = {
    "create": "We’re ready to build your evaluation suite. Ready to continue?",
    "audit": "We’re ready to review evaluation coverage, identify gaps, and recommend improvements. Ready to continue?",
    "expand": "We’re ready to improve your existing evaluations. Ready to continue?",
}
# Existing saved questions retain their meaning when presentation copy changes.
CONTINUATION_JOURNEYS = {
    **{question: journey for journey, question in CONTINUATION.items()},
    "We’re ready to check what your existing evaluations cover. Ready to continue?": "audit",
}
PREREQUISITES_COMPLETE = "The prerequisites for reviewing ETHOS.md are complete."
PREREQUISITE_PROGRESS_INTRO = "Here’s what I already know about your setup and what I’m checking before we continue."
PREREQUISITE_RESULTS_INTRO = "Here’s what the prerequisite checks found, including anything that still needs attention."
AUDIT_ENTRY_STATE_FAILURE = (
    "- **Saved progress:** {error} "
    "I’m pausing audit checks because I can’t safely reuse or update saved progress. "
    "**Next step:** {recovery} "
    "Once that’s resolved, I’ll reload saved progress and continue the audit."
)
PREREQUISITE_STATE_FAILURE = (
    "- **Save prerequisite findings:** {error} "
    "**Needed:** Resolve the state helper error, then retry the prerequisite checks."
)
PREREQUISITE_UNSAVED_NOTICE = (
    "These findings are retained only in this conversation. A fresh chat cannot resume them until they are saved."
)
TABLE_HEADER = "| Prerequisite | Status | Findings |\n|---|---|---|"
AUTHORING_ROWS = (
    ("agent_repository", "Agent and repository"),
    ("evaluation_framework", "Evaluation framework"),
    ("skills", "Required skills"),
    ("access", "File and command access"),
    ("runtime", "Python and evaluation runtime"),
    ("environment", "Evaluation environment"),
    ("services_data", "Required services and data"),
    ("agent_access", "Agent connection and credentials"),
)
AUDIT_ROWS = (
    *AUTHORING_ROWS[:2],
    ("traces", "Traces from completed evaluation runs"),
    *AUTHORING_ROWS[2:4],
    ("tooling", "Audit tooling"),
)
TRACE_FINDING = (
    "This audit requires saved traces from evaluations that have already run. "
    "Their availability must be confirmed before we continue."
)
AUDIT_TRACE_BLOCKER = {
    "step": "Saved evaluation traces",
    "reason": (
        "A full audit needs saved traces from completed evaluation runs. "
        "Evaluation definitions and pass/fail scores alone are insufficient."
    ),
    "skill_label": "Eval Author audit skill",
    "quote": "Confirm that traces from completed evaluations exist before continuing to Ethos",
}
BLOCKER_TEMPLATES = {
    ("audit", "setup", "data"): {
        "unknown": {
            **AUDIT_TRACE_BLOCKER,
            "finding": "Availability of saved traces from completed evaluation runs has not been confirmed.",
            "needed": "Do you have saved traces from evaluations that have already run for this agent?",
        },
        "missing": {
            **AUDIT_TRACE_BLOCKER,
            "finding": "No saved traces from completed evaluation runs are available.",
            "needed": "Provide saved traces from completed evaluation runs or choose a different scope.",
        },
    },
    ("expand", "setup", "evaluation_format"): {
        "*": {
            "step": "Improve existing evaluations",
            "reason": (
                "The supplied suite uses an unsupported format. "
                "Eval Author supports NeMo Gym or Harbor for improving existing evaluations."
            ),
            "needed": "Convert the existing evaluations to NeMo Gym or Harbor before we continue.",
            "skill_label": "Eval Author skill",
            "quote": "Keep the blocker until the converted suite's actual format is checked",
        },
    },
}
CONVERSION_HANDOFF = """Start a separate chat with your coding agent in the same repository to convert the suite. Replace the three placeholders below with your suite path, current framework, and chosen target: NeMo Gym or Harbor.

```text
Convert the existing evaluation suite at <suite path> from <current framework>
to <NeMo Gym or Harbor>. Handle this as repository engineering work outside
the Eval Author workflow.

Inspect the existing cases, fixtures, tool interfaces, setup/reset logic, and
graders. Use the target framework's current official documentation and the
runtime version supported by Eval Author.

Write the converted suite to a new directory, preserving the original suite
and agent implementation. Carry over scenario identifiers, inputs, expected
behavior, scoring rules and thresholds, and failure cases. Reuse existing
fixtures and mock or in-memory boundaries where applicable.

Map each original case and check to its converted counterpart. Explain any
behavior that cannot be represented faithfully and ask about unresolved
semantic choices before changing what a test measures.

Validate the target format and run offline verifier checks with known passing
and failing fixtures where available. Keep live agent/model runs separate;
report their commands and requirements without claiming they passed.

Report the converted suite path, mapping, changed files, setup/run commands,
checks actually performed, and any remaining gaps or dependencies.
```

Return here with the converted suite path and validation results so Eval Author can recheck prerequisites."""
ETHOS_ABSENT = "ETHOS.md records what the agent should do, what it should avoid, and what a good result looks like."
ETHOS_UNKNOWN = "ETHOS.md has not been checked yet."
ETHOS_FOUND = "I found {document}."
ETHOS_CONFIRMED = "{document} is confirmed and validated."
ETHOS_DRAFT = "Here is the proposed {document}."
ETHOS_REVIEW = (
    "Is this the right ETHOS.md for {agent}, and does it accurately describe how the agent should behave today?"
)
