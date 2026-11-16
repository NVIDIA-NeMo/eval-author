# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixed copy for the supported expansion entry checkpoint."""

INTRO = "Let’s agree which supported improvements to make to your existing evaluations."
OPTIONS_INTRO = "Let’s work on improving your existing evaluations. I can help you:"
OPTIONS = (
    "Expand a starter suite created with Eval Author",
    "Create a task candidate from a recorded agent interaction (experimental)",
    "Audit your suite to identify improvements",
)
OPTIONS_QUESTION = "What would you like to work on?"
LIMIT = (
    "I can also explain and plan other audit recommendations. "
    "Implementing revisions to existing cases or graders inside Eval Author is not yet implemented. "
    "We still need to agree scope before creating tasks or running your agent."
)


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))
