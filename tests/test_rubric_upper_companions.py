# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"))
import rubric_upper_method_review as method
from rubric_upper_companions import compact_identity_evidence
from upper_anticheat_contract import CRITERIA, GAPS


def fixture_report():
    return {"items": {key: {
        "score": 2, "check_results": [{"id": key + ".probe", "method": "probe",
            "outcome": "PARTIAL" if key == "task-contract.clear-contract" else "PASS",
            "remaining_gap": GAPS[key]}],
        "limitations": [GAPS[key]], "remaining_gaps": [GAPS[key]],
        "findings": [{"history": "retain structured dissent"}], "upper_anchor_companion": {},
    } for key in CRITERIA}, "measurements": {"upper_anticheat_contract": {"remaining_method_decisions": GAPS}}}


@pytest.mark.parametrize("variant", ["accepted", "missing", "revoked", "reject", "failed", "false-predicate"])
def test_scoped_method_does_not_promote_without_all_gates(variant):
    report = fixture_report()
    old = deepcopy(report)
    receipt = {"digest": "synthetic", "revoked": variant == "revoked", "event": {
        "request": {"binding": "synthetic"},
        "decisions": {k: {"decision": "reject" if variant == "reject" else "approve"} for k in method.CRITERIA},
    }}
    if variant == "missing":
        receipt = None
    if variant == "failed":
        for k in method.CRITERIA:
            report["items"][k]["check_results"][0]["outcome"] = "FAIL"
    method.apply(report, {"binding": "synthetic"}, receipt,
                 {k: variant != "false-predicate" for k in method.CRITERIA})
    assert all(report["items"][k]["score"] == (3 if variant == "accepted" else 2) for k in method.CRITERIA)
    assert all(report["items"][k] == old["items"][k] for k in CRITERIA if k not in method.CRITERIA)
    assert GAPS == old["measurements"]["upper_anticheat_contract"]["remaining_method_decisions"]
    assert all(r["findings"] == [{"history": "retain structured dissent"}] for r in report["items"].values())


def test_inventory_link_requires_exact_value_and_preserves_other_evidence():
    inventory = [{"id": "x", "digest": "test"}]
    report = {"measurements": {"identity": {"records": inventory}}, "items": {
        "test": {"check_results": [{"evidence": [
            {"helper": "identity", "value": deepcopy(inventory)},
            {"helper": "identity", "value": [{"id": "different"}]},
        ]}]}}}
    compact_identity_evidence(report)
    evidence = report["items"]["test"]["check_results"][0]["evidence"]
    assert evidence[0] == {"helper": "identity", "value_ref": "#/report/measurements/identity/records"}
    assert evidence[1]["value"] == [{"id": "different"}]
    assert report["measurements"]["identity"]["records"] == inventory
