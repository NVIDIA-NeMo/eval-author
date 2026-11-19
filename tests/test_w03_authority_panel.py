# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author-rubric/scripts"))
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import run_rubric_measurements

    return run_rubric_measurements


@pytest.mark.parametrize(
    "mutation",
    [
        "checks_missing",
        "check_id",
        "check_pointer",
        "check_status",
        "question_missing",
        "question_id",
        "question_reference",
        "question_decision",
        "question_neighbor",
    ],
)
def test_authority_matcher_rejects_check_and_question_drift(panel, mutation):
    question = {
        "requirement_id": "limit",
        "reference": "Reviewed refund rule",
        "decision_needed": panel.AUTHORITY_DECISION_NEEDED["prose"],
    }
    checks = [{"id": "total", "pointer": "/checks/total", "status": "observed"}]
    case = {
        "measurement": "authority",
        "codes": ["authority.reference_prose"],
        "requirement_statuses": ["prose", "resolved"],
        "checks": checks,
        "questions": [question, None],
    }
    observation: dict[str, Any] = {
        "diagnostics": [{"code": "authority.reference_prose"}],
        "requirements": [{"status": "prose", "question": question}, {"status": "resolved"}],
        "checks": checks,
    }
    assert panel.ethos_or_authority_matches(observation, case)
    changed = copy.deepcopy(observation)
    if mutation == "checks_missing":
        changed.pop("checks")
    elif mutation.startswith("check_"):
        changed["checks"][0][mutation.removeprefix("check_")] = "wrong"
    elif mutation == "question_missing":
        changed["requirements"][0].pop("question")
    elif mutation == "question_neighbor":
        changed["requirements"][1]["question"] = changed["requirements"][0].pop("question")
    else:
        field = {
            "question_id": "requirement_id",
            "question_reference": "reference",
            "question_decision": "decision_needed",
        }[mutation]
        changed["requirements"][0]["question"][field] = "wrong"
    assert not panel.ethos_or_authority_matches(changed, case)


def test_resolved_panel_selects_explicit_synthetic_result(panel, tmp_path):
    selection = panel.evidence_fixture(tmp_path, "authority-resolved")
    artifacts = {row["id"]: row for row in selection["artifacts"]}
    assert "result_artifact_id" in selection["authority"]
    result = artifacts[selection["authority"]["result_artifact_id"]]
    assert result["role"] == "result"
    assert json.loads((tmp_path / result["path"]).read_text()) == {
        "checks": {"total": 1},
        "synthetic": True,
        "executed": False,
    }
    plan = json.loads((tmp_path / artifacts[selection["authority"]["plan_artifact_id"]]["path"]).read_text())
    assert plan["checks"][0]["success"] == {"pointer": "/checks/total", "equals": 1}
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    code, report, error = panel.package_cli(
        tmp_path, "skills/eval-author-rubric/scripts/measure.py", "--root", tmp_path, "--selection", request
    )
    assert code == 0 and not error
    observation = report["measurements"]["authority"]
    assert observation["status"] == "pass"
    assert observation["checks"] == [{"id": "total", "pointer": "/checks/total", "status": "observed"}]


def test_authority_panel_covers_observation_and_source_controls(panel):
    cases = json.loads((ROOT / "evals/rubric-measurements/cases.json").read_text())["evidence_cases"]
    authority = {row["id"]: row for row in cases if row["measurement"] == "authority"}
    assert set(authority) == {
        "authority-resolved",
        "authority-broken-reference",
        "authority-prose",
        "authority-check-unobserved",
        "authority-no-result",
        "authority-unresolved",
        "authority-unselected",
        "authority-owner-instruction",
        "authority-verifier-reference",
        "authority-refund",
    }
    for row in authority.values():
        assert row["checks"]
        assert len(row["questions"]) == len(row["requirement_statuses"])


@pytest.mark.parametrize(
    "mutation", ["authority-check-unobserved", "authority-no-result", "authority-unresolved", "authority-unselected"]
)
def test_authority_observation_and_question_controls(panel, tmp_path, mutation):
    selection = panel.evidence_fixture(tmp_path, mutation)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    before = panel.package_inputs(tmp_path)
    code, report, error = panel.package_cli(
        tmp_path, "skills/eval-author-rubric/scripts/measure.py", "--root", tmp_path, "--selection", request
    )
    assert code == 1 and not error
    assert panel.package_inputs(tmp_path) == before
    cases = json.loads((ROOT / "evals/rubric-measurements/cases.json").read_text())["evidence_cases"]
    case = next(row for row in cases if row["id"] == mutation)
    assert panel.evidence_matches(report, case, selection, tmp_path)
    authority = report["measurements"]["authority"]
    assert authority["requirements"][1]["status"] == "resolved"
    assert report["measurements"]["route"]["executed_scorer"] is None
    assert report["preflight_success"] is False


@pytest.mark.parametrize("mutation", ["authority-owner-instruction", "authority-verifier-reference"])
def test_reconstructed_authority_distinguishes_owner_and_verifier(panel, tmp_path, mutation):
    selection = panel.evidence_fixture(tmp_path, mutation)
    artifacts = {row["id"]: row for row in selection["artifacts"]}
    assert "owner-instruction" in artifacts and "reconstructed-verifier" in artifacts
    owner = artifacts["owner-instruction"]
    verifier = artifacts["reconstructed-verifier"]
    assert owner["role"] == "task" and verifier["role"] == "scorer"
    trace = ROOT / "evals/rubric-measurements/reconstruction.trace.jsonl"
    assert (tmp_path / artifacts["reconstruction-source"]["path"]).read_bytes() == trace.read_bytes()
    content = json.loads(trace.read_text())["root_spans"][0]["turns"][0]["content"]
    assert (tmp_path / owner["path"]).read_text() == content
    plan = json.loads((tmp_path / "construction.json").read_text())
    assert plan["requirements"][1]["authority"]["reference"] == owner["path"] + "#/currency"
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    code, report, error = panel.package_cli(
        tmp_path, "skills/eval-author-rubric/scripts/measure.py", "--root", tmp_path, "--selection", request
    )
    assert not error
    cases = json.loads((ROOT / "evals/rubric-measurements/cases.json").read_text())["evidence_cases"]
    case = next(row for row in cases if row["id"] == mutation)
    assert code == case["cli_exit_code"]
    assert panel.evidence_matches(report, case, selection, tmp_path)
    assert "handcrafted" in case["description"].lower()
    assert "reconstruction" not in selection


def test_refund_panel_copies_authority_and_contract_without_result(panel, tmp_path):
    selection = panel.evidence_fixture(tmp_path, "authority-refund")
    selected = {row["path"]: row for row in selection["artifacts"]}
    for name, role in (("authority", "construction"), ("contract", "task")):
        relative = f"evals/scorer-controls/{name}.json"
        assert relative in selected
        assert selected[relative]["role"] == role
        assert (tmp_path / relative).read_bytes() == (ROOT / relative).read_bytes()
    assert "result_artifact_id" not in selection["authority"]
    assert not any(row["role"] == "result" for row in selection["artifacts"])
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    before = panel.package_inputs(tmp_path)
    code, report, error = panel.package_cli(
        tmp_path, "skills/eval-author-rubric/scripts/measure.py", "--root", tmp_path, "--selection", request
    )
    assert code == 1 and not error
    assert panel.package_inputs(tmp_path) == before
    cases = json.loads((ROOT / "evals/rubric-measurements/cases.json").read_text())["evidence_cases"]
    case = next(row for row in cases if row["id"] == "authority-refund")
    assert panel.evidence_matches(report, case, selection, tmp_path)
    authority = report["measurements"]["authority"]
    assert authority["counts"]["requirements"] == authority["counts"]["checks"] == authority["counts"]["mapped"] == 9
    assert authority["counts"]["by_status"]["resolved"] == 9


def test_authority_cases_pass_actual_panel_with_nested_inputs(panel, monkeypatch):
    original = panel.loads

    def authority_cases_only(value):
        data = original(value)
        if isinstance(data, dict) and "raw_measurements" in data:
            data["raw_measurements"] = data["raw_measurements"][:1]
            data["assessments"] = data["assessments"][:1]
            data["evidence_cases"] = [row for row in data["evidence_cases"] if row["measurement"] == "authority"]
            data["package_acceptance"] = data["package_acceptance"][-1:]
        return data

    monkeypatch.setattr(panel, "loads", authority_cases_only)
    report = panel.run_panel()
    assert report["status"] == "passed", panel.summary(report)
    assert len(report["evidence_cases"]) == 10
    assert all(row["matches_expectations"] for row in report["evidence_cases"])
    assert report["native_execution"] is False
