# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Detect whether Harbor can judge this repository.

Standard library only, and safe to import when Harbor is absent. The shared
locator checks imports in isolated child interpreters. In-process Harbor imports
live in ``_judge.py``, loaded only after this module reports Harbor available.

This is the provider gate. A second provider adds its own probe next to this one
rather than changing ``discover.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from _checks import ADVISORY, FAIL, PASS, WARN, CheckResult, check
from locate_runtime import locate

PROVIDER = "harbor"


def probe(repo: Path | None = None) -> dict:
    """Locate candidates while verifying discovery's own interpreter explicitly."""
    evidence = locate(repo or Path.cwd(), explicit={"harbor": {"python": sys.executable}}, providers=("harbor",))
    selection = evidence["selection"]["harbor"]
    observed = selection["selected"]
    if observed is not None and importlib.util.find_spec("harbor") is None:
        # The parent may have disabled site-packages (python -S); a normal child
        # probe cannot establish that this process can import the validator.
        observed = None
        selection = {
            **selection,
            "selected": None,
            "status": "unresolved",
            "reason": "Harbor is unavailable in discovery's current Python process (check interpreter flags)",
        }
        evidence["selection"]["harbor"] = selection
    return {
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "harbor_importable": observed is not None,
        "harbor_version": observed["version"] if observed else None,
        "harbor_cli": observed["cli"] if observed else None,
        "harbor_probe": evidence,
        "harbor_reason": selection["reason"],
    }


def probe_checks(runtime: dict) -> list[CheckResult]:
    """Turn the probe into the checks that gate the validation ladder."""
    checks: list[CheckResult] = []
    if runtime["harbor_importable"]:
        checks.append(
            check(
                "harbor",
                "runtime",
                PASS,
                "Harbor {} is importable, so Harbor judges this report.".format(runtime["harbor_version"] or "?"),
            )
        )
    else:
        checks.append(
            check(
                "harbor",
                "runtime",
                FAIL,
                "Harbor cannot run discovery: " + runtime.get("harbor_reason", "not importable"),
                hint="Run locate_runtime.py --provider harbor --flow discovery and use its selected Python.",
            )
        )
    if runtime["harbor_cli"] is None:
        checks.append(
            check(
                "harbor-cli",
                "runtime",
                WARN,
                "No compatible Harbor CLI was selected, so the CLI round trip cannot run.",
                severity=ADVISORY,
                proven=False,
            )
        )
    return checks


def is_available(runtime: dict) -> bool:
    """Return whether the ladder can run against this runtime."""
    return bool(runtime["harbor_importable"])
