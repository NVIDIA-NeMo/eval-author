# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical labels and section copy shared by conversation flows and saved state."""

JOURNEYS = {
    "create": "Build, run, and review your first evaluations",
    "audit": "Review evaluation coverage, identify gaps, and recommend improvements",
    "expand": "Improve, run, and review your existing evaluations",
}
CHECKLIST_LABELS = (
    "Check how your agent runs and how you’ll test it",
    "Agree on how your agent behaves using "
    "[ETHOS.md](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/README.md)",
)
STATUS_LABELS = {
    "verified": "Verified",
    "confirmed": "Confirmed by you",
    "missing": "Missing",
    "unknown": "Unknown",
    "not_required": "Not needed now",
}
RECOMMENDATIONS = "### Recommendations\n\nThese suggestions are optional and are not required to continue."
BLOCKERS = "### Blockers\n\nThese items need to be resolved before we can continue."
HANDOFF_BLOCKERS = "### Blockers\n\nThese items prevent us from continuing with your evaluations."
BLOCKER_PAUSE = "The {skill} requires “{quote}”. This prerequisite must be resolved before we continue."
NEXT_STEPS = "### Next Steps"
REPORTS = "Review your evaluations in {report} or {readme}."
