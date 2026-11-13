# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_authority import check_authority  # noqa: E402

ETHOS = b"---\nname: x\n---\n\n## Purpose & Outcomes\n\nTotals use two decimals.\n\n## Constraints\n\nNo writes.\n"
INSTRUCTION = b"Compute the total.\nWrite it to total.txt.\n"
FIXTURE = b'{"orders": [{"amount": 120.25}]}'


def record(root, ident, role, path, content, digest=None):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(content)
    actual = hashlib.sha256(content).hexdigest()
    return {
        "id": ident,
        "role": role,
        "path": path,
        "digest": digest or actual,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "fixture",
        "evidence": [{"path": path, "digest": actual}],
    }


def measure(root, references, *, checks=None, sources=None):
    requirements = [
        {"id": f"r{index}", "statement": "s", "authority": {"basis": basis, "reference": reference}}
        for index, (basis, reference) in enumerate(references)
    ]
    checks = checks if checks is not None else [{"id": "c", "requirements": [row["id"] for row in requirements]}]
    plan = json.dumps({"requirements": requirements, "checks": checks}).encode()
    artifacts = sources or [
        record(root, "ethos", "ethos", "ETHOS.md", ETHOS),
        record(root, "instruction", "task", "tasks/t/instruction.md", INSTRUCTION),
        record(root, "fixture", "task", "tasks/t/orders.json", FIXTURE),
    ]
    artifacts = artifacts + [record(root, "plan", "construction", ".eval-author/.working/construction.json", plan)]
    return check_authority(root, artifacts, {"plan_artifact_id": "plan"})


def statuses(result):
    return [row["status"] for row in result["requirements"]]


def codes(result):
    return sorted((row["code"], row["subject"], row["status"]) for row in result["diagnostics"])


@pytest.mark.parametrize(
    "reference",
    [
        "ETHOS.md#Purpose & Outcomes",
        "ETHOS.md#purpose--outcomes",
        "tasks/t/instruction.md#L1-L2",
        "tasks/t/orders.json#/orders/0/amount",
        "tasks/t/instruction.md",
    ],
)
def test_locators_naming_selected_content_resolve(tmp_path, reference):
    result = measure(tmp_path, [("specification", reference)])
    assert (result["status"], statuses(result)) == ("pass", ["resolved"])


@pytest.mark.parametrize(
    "reference", ["ETHOS.md#Scope", "tasks/t/instruction.md#L2-L3", "tasks/t/orders.json#/orders/1"]
)
def test_missing_targets_are_broken_without_affecting_neighbors(tmp_path, reference):
    result = measure(tmp_path, [("specification", reference), ("specification", "ETHOS.md#Constraints")])
    assert statuses(result) == ["broken", "resolved"]
    assert codes(result) == [("authority.reference_broken", "r0", "fail")]


@pytest.mark.parametrize(
    ("basis", "reference", "status", "code"),
    [
        ("specification", "Reviewed total rule", "prose", "authority.reference_prose"),
        ("user_decision", "docs/decisions.md#Totals", "unselected", "authority.reference_unselected"),
        ("unresolved", "ETHOS.md#Constraints", "unresolved", "authority.unresolved"),
    ],
)
def test_uncheckable_references_stay_unknown(tmp_path, basis, reference, status, code):
    result = measure(tmp_path, [(basis, reference)])
    assert (result["status"], statuses(result)) == ("unknown", [status])
    assert codes(result) == [(code, "r0", "unknown")]


def test_changed_source_bytes_fail_the_reference(tmp_path):
    sources = [record(tmp_path, "ethos", "ethos", "ETHOS.md", ETHOS, digest="0" * 64)]
    result = measure(tmp_path, [("specification", "ETHOS.md#Constraints")], sources=sources)
    assert codes(result) == [("authority.reference_changed", "r0", "fail")]


def test_mapping_gaps_and_counts(tmp_path):
    references = [("specification", "ETHOS.md#Constraints"), ("specification", "Input rule")]
    result = measure(tmp_path, references, checks=[{"id": "c", "requirements": ["r0", "missing"]}])
    assert codes(result) == [
        ("authority.check_dangling", "c", "fail"),
        ("authority.reference_prose", "r1", "unknown"),
        ("authority.requirement_unmapped", "r1", "unknown"),
    ]
    counts = result["counts"]
    assert (counts["requirements"], counts["checks"], counts["mapped"]) == (2, 1, 1)
    assert counts["by_status"]["resolved"] == counts["by_status"]["prose"] == 1


def test_malformed_plan_fails_and_wrong_role_is_rejected(tmp_path):
    plan = record(tmp_path, "plan", "construction", "plan.json", b'{"requirements": [{"id": "r0"}]}')
    result = check_authority(tmp_path, [plan], {"plan_artifact_id": "plan"})
    assert codes(result) == [("authority.plan_invalid", "plan", "fail")]
    with pytest.raises(ValueError):
        check_authority(tmp_path, [{**plan, "role": "task"}], {"plan_artifact_id": "plan"})
