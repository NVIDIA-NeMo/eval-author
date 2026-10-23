# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retained reference copies may differ only by reviewed owner-specific changes."""

import argparse
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


def owner_delta(legacy: str, current: str) -> list[str]:
    diff = list(difflib.unified_diff(legacy.splitlines(keepends=True), current.splitlines(keepends=True), n=0))
    return [line for line in diff[2:] if not line.startswith("@@")]


def deltas():
    result = {}
    for name, owner in OWNERS.items():
        legacy = ROOT / "skills/eval-author-trace-environment/references" / name
        current = ROOT / "skills" / owner / "references" / name
        result[name] = owner_delta(legacy.read_text(), current.read_text())
    return result


def test_retained_references_have_only_reviewed_owner_differences():
    expected = json.loads((ROOT / "tests/fixtures/trace-reference-owner-deltas.json").read_text())
    assert deltas() == expected, (
        "Reference copies drifted; reconcile shared rules and review owner-specific differences"
    )


def test_owner_delta_ignores_synchronized_context_and_position_changes():
    legacy, current = "header\nshared\nold owner\ntail\n", "header\nshared\nnew owner\ntail\n"
    expected = owner_delta(legacy, current)
    assert owner_delta("prefix\n" + legacy, "prefix\n" + current) == expected
    assert owner_delta(legacy.replace("shared", "updated"), current.replace("shared", "updated")) == expected
    assert owner_delta(legacy, current.replace("shared", "drifted")) != expected
    assert owner_delta("---\n", "") == ["----\n"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", required=True)
    parser.parse_args()
    (ROOT / "tests/fixtures/trace-reference-owner-deltas.json").write_text(json.dumps(deltas(), indent=2) + "\n")
