# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Adapt shared runtime selection evidence into discovery's Gym report fields.

The locator owns runtime detection, compatibility, and selection. This module
only formats that evidence for discovery. Judge and Solve use the selected CLI
in its own environment; discovery never imports Gym.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Shared runtime helpers ship with the sibling eval-author skill.
sys.path.append(str(Path(__file__).resolve().parents[4] / "eval-author/scripts"))

from _checks import ADVISORY, PASS, WARN, CheckResult, check  # noqa: E402

try:
    from locate_runtime import locate
except ModuleNotFoundError as exc:
    if exc.name not in {"locate_runtime", "runtime_policy"}:
        raise
    raise SystemExit(
        "Incomplete skill installation: install the sibling eval-author skill alongside eval-author-discover."
    ) from None
from runtime_policy import install_spec  # noqa: E402

INSTALL_HINT = f"Locate an existing Gym runtime first; when setup is authorized, install {install_spec('gym')} in a separate environment meeting runtime-policy.json."


def probe(repo: Path | None = None, cli: str | None = None) -> dict:
    """Use the shared locator, or reverify an explicitly selected CLI."""
    evidence = locate(repo or Path.cwd(), explicit={"gym": {"cli": cli}} if cli else None, providers=("gym",))
    selection = evidence["selection"]["gym"]
    selected = selection["selected"]
    return {
        "gym_available": selected is not None,
        "gym_version": selected["version"] if selected else None,
        "gym_cli": selected["cli"] if selected else None,
        "gym_probe": evidence,
        "gym_reason": selection["reason"],
    }


def is_available(runtime: dict) -> bool:
    """Return whether the shared locator selected a usable Gym runtime."""
    return bool(runtime["gym_available"])


def probe_checks(runtime: dict, *, report_missing: bool) -> list[CheckResult]:
    """Report Gym availability; report its absence only when it matters."""
    if runtime["gym_available"]:
        return [
            check(
                "gym",
                "runtime",
                PASS,
                "NeMo Gym {} is installed at {}.".format(runtime["gym_version"], runtime["gym_cli"]),
                severity=ADVISORY,
            )
        ]
    if not report_missing:
        return []
    return [
        check(
            "gym",
            "runtime",
            WARN,
            runtime["gym_reason"],
            severity=ADVISORY,
            hint=INSTALL_HINT,
        )
    ]
