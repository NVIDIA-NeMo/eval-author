# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded verification-integrity scores; never canonical merit or an aggregate."""


def score_check_group(*, passed: int, failed: int, unknown: int = 0, excluded: int = 0) -> dict:
    """Score only fully assessed applicable checks; exclusions never dilute failures."""
    for name, value in (("passed", passed), ("failed", failed), ("unknown", unknown), ("excluded", excluded)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative exact integer")
    assessed = passed + failed
    applicable = assessed + unknown
    if unknown or not applicable:
        score = None
        action = "Resolve unknown evidence and freshness before scoring." if unknown else "Establish applicable checks."
    else:
        score = 0 if passed == 0 else 3 if failed == 0 else 1 if 2 * passed <= assessed else 2
        action = "Resolve failed checks and rerun verification." if failed else "Retain the assessed evidence."
    return {
        "schema": "verification-check-group-v1",
        "score": score,
        "outcome": "unassessed" if score is None else "assessed",
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "excluded": excluded,
        "applicable": applicable,
        "assessed": assessed,
        "coverage": assessed / applicable if applicable else None,
        "next_action": action,
    }


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    # Missing or malformed groups must not masquerade as known empty inventories.
    return value if isinstance(value, list) else [None]


def score_integrity_groups(native: dict) -> list[dict]:
    """Project revalidated ``project_native`` rows, not caller-supplied summary counts.

    Receipt consistency assesses historical passed claims independently of task
    freshness. Required verification admits only current pass/fail case states.
    Review candidates and nonclaims are excluded, not failed checks. This helper
    does not authenticate sources; rubric_report revalidates them before calling.
    """
    native = _mapping(native)
    claims = []
    for control in _rows(native.get("controls")):
        control = _mapping(control)
        integrity = _mapping(control.get("integrity"))
        state = integrity.get("status_claim")
        claims.append(
            {
                "consistent": "passed",
                "contradiction": "failed",
                "excluded": "excluded",
                "not_applicable": "excluded",
            }.get(state, "unknown")
            if isinstance(state, str)
            else "unknown"
        )
    required = []
    for case in _rows(_mapping(native.get("required_verification")).get("cases")):
        case = _mapping(case)
        state = case.get("state")
        if case.get("freshness") == "current" and state in ("pass", "fail"):
            required.append("passed" if state == "pass" else "failed")
        else:
            required.append("unknown")
    groups = []
    for ident, name, states, ref, boundary in (
        (
            "receipt_consistency",
            "Receipt consistency",
            claims,
            "controls",
            "Selected passed receipt claims only; not current task qualification or agent behavior. Review candidates and nonclaims are excluded.",
        ),
        (
            "required_verification",
            "Required verification",
            required,
            "required_verification/cases",
            "Selected required controls with current evidence only; missing, stale and unresolved evidence remains unknown. No task-quality or agent-behavior score.",
        ),
    ):
        groups.append(
            {
                **score_check_group(**{key: states.count(key) for key in ("passed", "failed", "unknown", "excluded")}),
                "id": ident,
                "name": name,
                "scope": "verification_integrity",
                "evidence_refs": ["#/measurements/native/" + ref],
                "evidence_boundary": boundary,
            }
        )
    return groups
