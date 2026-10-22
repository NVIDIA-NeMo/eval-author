# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retained reference copies may differ only by reviewed owner-specific changes."""

import difflib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNERS = {
    "sources.md": "eval-author-inspect-trace",
    "evidence-audit.md": "eval-author-inspect-trace",
    "candidate-record.md": "eval-author-task-create",
    "construction-boundary.md": "eval-author-task-create",
    "construction-qualification.md": "eval-author",
    "publication-review.md": "eval-author",
    **dict.fromkeys(
        [
            "check-grammar.md",
            "environment-integrity.md",
            "gym-output.md",
            "solution-review.md",
            "task-contract.md",
            "task-fidelity.md",
            "trace-derived-fixtures.md",
        ],
        "eval-author-environment",
    ),
}


def deltas():
    result = {}
    for name, owner in OWNERS.items():
        legacy = ROOT / "skills/eval-author-trace-environment/references" / name
        current = ROOT / "skills" / owner / "references" / name
        result[name] = list(
            difflib.unified_diff(
                legacy.read_text().splitlines(keepends=True),
                current.read_text().splitlines(keepends=True),
                fromfile="retained/" + name,
                tofile=owner + "/" + name,
            )
        )
    return result


def test_retained_references_have_only_reviewed_owner_differences():
    expected = json.loads((ROOT / "tests/fixtures/trace-reference-owner-deltas.json").read_text())
    assert deltas() == expected, (
        "Reference copies drifted; reconcile shared rules and review owner-specific differences"
    )
