# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed acceptance-claim integrity over real recorder receipts and attempt ledgers."""

import json
import subprocess
import sys

import pytest
from test_task_evidence import SCRIPTS, complete, evidence, execute, pipeline, write
from test_task_evidence import prepared as task_prepared


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    return getattr(task_prepared, "__wrapped__")(tmp_path, monkeypatch)


def saved(tmp_path, receipts):
    payload = {
        "schema": "nemo.eval_author.task_gap_verification.v2",
        "task_validation": "passed",
        "evidence": [str(path) for path in receipts],
    }
    return write(tmp_path / "acceptance.json", payload)


def integrity(path):
    code, payload = pipeline._acceptance_integrity(path)
    return code, payload["acceptance_integrity"]


def codes(result):
    return sorted((row["code"], row["outcome"]) for row in result["findings"] if row["outcome"] != "consistent")


def test_complete_current_controls_support_the_claim(tmp_path, prepared):
    code, result = integrity(saved(tmp_path, complete(tmp_path, prepared[2])))
    assert code == 0
    assert result["status"] == "consistent"
    assert result["violations"] == 0
    assert result["required_verification"]["verified"] is True
    assert result["agent_runs"] == "excluded"


def test_cited_failed_control_contradicts_the_passed_claim(tmp_path, prepared):
    receipts = complete(tmp_path, prepared[2])
    failed = execute(tmp_path, prepared[2], "reference", "reference-failed", reward=0)
    code, result = integrity(saved(tmp_path, [failed, *receipts]))
    assert code == 1
    assert result["status"] == "violation"
    assert ("cited_failure", "violation") in codes(result)


def test_registered_failure_omitted_from_the_claim_is_a_violation(tmp_path, prepared):
    execute(tmp_path, prepared[2], "incorrect", "incorrect-accepted", reward=1)
    code, result = integrity(saved(tmp_path, complete(tmp_path, prepared[2])))
    assert code == 1
    assert codes(result) == [("omitted_failure", "violation")]


def test_unreadable_registered_attempt_is_an_evidence_gap(tmp_path, prepared):
    interrupted = execute(tmp_path, prepared[2], "reference", "interrupted")
    receipts = complete(tmp_path, prepared[2])
    interrupted.unlink()
    code, result = integrity(saved(tmp_path, receipts))
    assert code == 1
    assert result["status"] == "unknown"
    assert codes(result) == [("omitted_attempt", "unknown")]


def test_missing_required_control_leaves_the_claim_unsupported_not_false(tmp_path, prepared):
    receipts = [path for path in complete(tmp_path, prepared[2]) if "empty" not in path.name]
    receipts[0].with_name("empty-receipt.json").unlink()
    _, result = integrity(saved(tmp_path, receipts))
    assert result["status"] == "unknown"
    assert ("missing_required", "unknown") in codes(result)
    assert result["violations"] == 0


def test_task_change_makes_the_claim_stale_without_a_contradiction(tmp_path, prepared):
    task, _, manifest = prepared
    path = saved(tmp_path, complete(tmp_path, manifest))
    (task / "instruction.md").write_text("Changed requirement.")
    code, result = integrity(path)
    assert code == 1
    assert result["status"] == "stale"
    assert result["violations"] == 0


def test_claim_citing_receipts_from_before_an_in_place_repair_relies_on_superseded_results(tmp_path, prepared):
    task, contract, manifest = prepared
    path = saved(tmp_path, complete(tmp_path, manifest))
    evidence.prepare(task, contract, manifest, manifest, "Correct a grading requirement")
    code, result = integrity(path)
    assert code == 1
    assert result["status"] == "violation"
    assert {row["code"] for row in result["findings"]} >= {"superseded_reliance"}
    assert all(row["outcome"] != "consistent" for row in result["findings"] if row["code"] == "superseded_reliance")


@pytest.mark.parametrize("change", [{"task_validation": "failed"}, {"schema": "legacy"}, {"evidence": None}])
def test_unsupported_or_negative_claims_are_not_assessed(tmp_path, prepared, change):
    path = saved(tmp_path, complete(tmp_path, prepared[2]))
    write(path, {**json.loads(path.read_text()), **change})
    code, result = integrity(path)
    assert code == 1
    assert (result["status"], result["eligible_claims"], result["violations"]) == ("not_applicable", 0, 0)


def test_cli_integrity_is_opt_in_and_preserves_records(tmp_path, prepared):
    receipts = complete(tmp_path, prepared[2])
    execute(tmp_path, prepared[2], "incorrect", "incorrect-accepted", reward=1)
    path = saved(tmp_path, receipts)
    before = {item: item.read_bytes() for item in tmp_path.rglob("*.json")}
    command = [sys.executable, str(SCRIPTS / "task_pipeline.py"), "check-acceptance", "--report", str(path)]
    measured = subprocess.run([*command, "--integrity"], capture_output=True, text=True, timeout=60)
    assert measured.returncode == 1
    result = json.loads(measured.stdout)["acceptance_integrity"]
    # The subprocess has no synthetic native identity, so unverifiable receipts must not pass.
    assert result["claim"] == "passed" and result["status"] != "consistent"
    assert all(item.read_bytes() == raw for item, raw in before.items())
    plain = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert "acceptance_integrity" not in plain.stdout
