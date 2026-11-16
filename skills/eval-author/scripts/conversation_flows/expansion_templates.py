# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixed copy for the supported expansion entry checkpoint."""

OPTIONS_INTRO = "Let’s work on improving your existing evaluations. I can help you:"
OPTIONS = (
    "Expand a starter suite created with Eval Author",
    "Create a task candidate from a recorded agent interaction (experimental)",
    "Audit your suite to identify improvements",
)
OPTIONS_QUESTION = "What would you like to work on?"


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))
