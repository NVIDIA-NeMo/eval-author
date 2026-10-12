# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Detect whether a working NeMo Gym CLI is installed.

Standard library only. Discovery never imports Gym: Judge and Solve run the
``gym`` command, which brings its own interpreter. So the Probe asks that command
for its version, and Gym can live in its own environment, apart from Harbor.

The Probe passes when Harbor or Gym is installed. Gym alone is enough to explore,
and to judge and solve Gym manifests. Harbor still judges every task directory,
including the Gym extension tasks that discovery converts.
"""

from __future__ import annotations

from pathlib import Path

from _checks import ADVISORY, PASS, WARN, CheckResult, check
from locate_runtime import locate
from runtime_policy import install_spec, load, satisfies

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
    """Return whether a NeMo Gym CLI new enough for Judge and Solve is installed."""
    return bool(runtime["gym_available"])


def unavailable_reason(runtime: dict) -> str:
    """Say why Gym cannot judge manifests: missing, or too old."""
    if runtime["gym_version"]:
        constraint = load()["providers"]["gym"]["supported"]["discovery"]
        if not satisfies(runtime["gym_version"], constraint):
            return f"NeMo Gym {runtime['gym_version']} does not meet {constraint}."
    return runtime.get("gym_reason", "NeMo Gym is not installed.")


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
            unavailable_reason(runtime),
            severity=ADVISORY,
            hint=INSTALL_HINT,
        )
    ]
