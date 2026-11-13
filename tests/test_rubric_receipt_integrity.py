# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Measure receipt consistency without attributing failure to an agent."""

import json

import pytest
from test_rubric_native import adapter, run_rubric_cli
from test_rubric_native import panel as native_panel


@pytest.fixture
def panel(tmp_path, monkeypatch):
    return getattr(native_panel, "__wrapped__")(tmp_path, monkeypatch)


def test_unavailable_receipt_is_not_a_passing_opportunity(panel):
    panel.identity["records"] = [row for row in panel.identity["records"] if row["id"] != "receipt"]
    output = adapter().project_native(panel.tmp_path, panel.identity, **panel.selection)
    measurement = output["receipt_integrity"]
    assert measurement["detector_version"] == "receipt-integrity-v1"
    assert measurement["selected_receipts"] == 1
    assert measurement["eligible_opportunities"] == 0
    assert measurement["contradictions"] == 0
    assert measurement["unknown"] == 1
    assert measurement["violation_rate"] is None
    assert measurement["context_rot_score"] is None
    assert output["controls"][0]["integrity"]["status"] == "unknown"


@pytest.mark.parametrize(
    "claimed,reward,expected",
    [("passed", 0, "contradiction"), ("passed", 1, "consistent"), ("behavior_failed", 0, "consistent")],
)
def test_cli_scores_bound_status_claim_without_context_attribution(panel, claimed, reward, expected):
    panel.receipt.update(status=claimed, purpose="closure")
    panel.result["verifier_result"]["rewards"]["reward"] = reward
    records = panel.seal()["records"]
    completed = run_rubric_cli(panel, {"artifacts": records, "native": panel.selection})
    assert completed.returncode == 1  # Native receipts still do not establish current task freshness.
    report = json.loads(completed.stdout)
    native = report["measurements"]["native"]
    integrity = native["controls"][0]["integrity"]
    assert integrity["status_claim"] == expected
    assert integrity["freshness"] == "unknown"
    assert native["receipt_integrity"]["eligible_opportunities"] == 1
    assert native["receipt_integrity"]["contradictions"] == int(expected == "contradiction")
    assert native["receipt_integrity"]["unknown"] == 0
    assert native["receipt_integrity"]["violation_rate"] == (1.0 if expected == "contradiction" else 0.0)
    assert report["items"]["CREATE-07"]["score"] is None
    assert "PRIVATE-" not in completed.stdout
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    predicate = next(row for row in check["predicate_results"] if row["id"] == "selected_receipt_status_consistent")
    assert predicate["outcome"] == ("FAIL" if expected == "contradiction" else "PASS")
    assert check["outcome"] == "PARTIAL"
    counts = next(row for row in check["measurements"] if row["unit"] == "selected receipt claims")
    assert counts["denominator"] == 1


@pytest.mark.parametrize("kind", ["alternative", "empty", "diagnostic", "agent", "baseline"])
def test_valid_exceptions_and_no_action_are_not_contradictions(panel, kind):
    panel.receipt.update(status="passed", purpose="baseline" if kind == "baseline" else "closure")
    if kind in {"alternative", "diagnostic"}:
        panel.manifest["contract"]["cases"][kind] = dict(panel.manifest["contract"]["cases"]["reference"], kind=kind)
    panel.receipt["case"] = "reference" if kind == "baseline" else kind
    panel.manifest["contract"]["cases"][panel.receipt["case"]]["checks"] = [
        {"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 0.5}
    ]
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    summary = output["receipt_integrity"]
    assert summary["contradictions"] == 0
    assert summary["excluded"] == int(kind in {"diagnostic", "agent", "baseline"})
    assert summary["eligible_opportunities"] == int(kind in {"alternative", "empty"})


def test_execution_error_without_native_output_is_unknown_not_contradiction(panel):
    panel.receipt.update(status="infrastructure_error", returncode=1, purpose="closure")
    records = panel.seal()["records"]
    panel.paths["result"].unlink()
    completed = run_rubric_cli(panel, {"artifacts": records, "native": panel.selection})
    assert completed.returncode == 1
    measurement = json.loads(completed.stdout)["measurements"]["native"]
    assert measurement["controls"][0]["integrity"]["execution"] == "execution_error"
    assert measurement["receipt_integrity"]["execution_errors"] == 1
    assert measurement["receipt_integrity"]["contradictions"] == 0
    assert measurement["receipt_integrity"]["eligible_opportunities"] == 0


def test_real_recorder_to_cli_retains_contradiction_and_recovery(panel, monkeypatch):
    import sys
    from types import SimpleNamespace

    import harbor.models.task.task
    from eval_author_shared import task_evidence
    from rubric_identity import identify

    monkeypatch.setattr(harbor.models.task.task, "Task", lambda path: SimpleNamespace(checksum="synthetic-checksum"))
    root = panel.tmp_path
    contract = root / "contract.json"
    contract.write_text(json.dumps(panel.manifest["contract"]))
    manifest = root / "prepared.json"
    task_evidence.prepare(panel.task, contract, manifest)
    records = []
    selected = {"manifest_ids": ["prepared"], "receipt_ids": [], "result_ids": [], "trace_ids": []}

    def select(path, ident, role):
        digest = task_evidence.digest(path)
        records.append(
            {
                "id": ident,
                "role": role,
                "path": path.relative_to(root).as_posix(),
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "scripted producer test, not live Harbor",
                "evidence": [{"path": path.relative_to(root).as_posix(), "digest": digest}],
            }
        )

    select(manifest, "prepared", "manifest")
    for name, reward in (("first", 0), ("recovery", 1)):
        result = root / (name + "-result.json")
        receipt = root / (name + "-receipt.json")
        native = dict(panel.result, id=name, trial_name=name, verifier_result={"rewards": {"reward": reward}})
        command = [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(result)!r}).write_text({json.dumps(native)!r})",
        ]
        recorded = task_evidence.run(manifest, "reference", name, receipt, result, None, command)
        assert recorded["status"] == ("behavior_failed" if reward == 0 else "passed")
        if name == "first":
            recorded["status"] = "passed"  # Plant corruption after the real recorder writes the receipt.
            receipt.write_text(json.dumps(recorded))
        select(receipt, name, "receipt")
        select(result, name + "-result", "result")
        selected["receipt_ids"].append(name)
        selected["result_ids"].append(name + "-result")
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    completed = run_rubric_cli(panel, {"artifacts": records, "native": selected})
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    summary = report["measurements"]["native"]["receipt_integrity"]
    assert summary["eligible_opportunities"] == 2
    assert summary["contradictions"] == 1
    assert summary["violation_rate"] == 0.5
    assert summary["unknown"] == 0
    assert [row["integrity"]["status_claim"] for row in report["measurements"]["native"]["controls"]] == [
        "contradiction",
        "consistent",
    ]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert completed.stdout == run_rubric_cli(panel, {"artifacts": records, "native": selected}).stdout
    (root / "observed-report.json").write_text(completed.stdout)
    assert identify(root, records)["status"] == "pass"


@pytest.mark.parametrize("reward", [0, 1], ids=["failed-native", "passed-native"])
def test_wrong_task_result_cannot_supply_status_claim(panel, reward):
    panel.receipt.update(status="passed", purpose="closure")
    panel.result["task_name"] = "other-task"
    panel.result["verifier_result"]["rewards"]["reward"] = reward
    records = panel.seal()["records"]
    completed = run_rubric_cli(panel, {"artifacts": records, "native": panel.selection})
    assert completed.returncode == 1
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    native = report["measurements"]["native"]
    assert "native.reference_invalid" in {row["code"] for row in native["diagnostics"]}
    assert native["controls"][0]["integrity"]["status_claim"] == "unknown"
    assert native["receipt_integrity"]["eligible_opportunities"] == 0
    assert native["receipt_integrity"]["contradictions"] == 0
    assert native["receipt_integrity"]["unknown"] == 1
    assert native["receipt_integrity"]["violation_rate"] is None
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    predicate = next(row for row in check["predicate_results"] if row["id"] == "selected_receipt_status_consistent")
    assert predicate["outcome"] == "BLOCKED"


@pytest.mark.parametrize("config", [{}, []], ids=["valid-config", "malformed-config"])
@pytest.mark.parametrize("reward", [0, 1], ids=["failed-native", "passed-native"])
def test_wrong_role_result_is_rejected_before_native_consumption(panel, monkeypatch, config, reward):
    panel.receipt.update(status="passed", purpose="closure", case="incorrect")
    panel.result["config"] = config
    panel.result["verifier_result"]["rewards"]["reward"] = reward
    selection = {**panel.selection, "result_ids": [], "trace_ids": ["trace", "result"]}
    records = panel.seal()["records"]
    completed = run_rubric_cli(panel, {"artifacts": records, "native": selection})
    assert completed.returncode == 1
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    native = report["measurements"]["native"]
    assert "native.reference_invalid" in {row["code"] for row in native["diagnostics"]}
    assert native["controls"][0]["classification"] == "unproven"
    assert native["controls"][0]["integrity"]["status_claim"] == "unknown"
    assert native["receipt_integrity"]["eligible_opportunities"] == 0
    assert native["receipt_integrity"]["contradictions"] == 0
    assert native["receipt_integrity"]["unknown"] == 1
    assert native["receipt_integrity"]["violation_rate"] is None
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    predicate = next(row for row in check["predicate_results"] if row["id"] == "selected_receipt_status_consistent")
    assert predicate["outcome"] == "BLOCKED"

    module = adapter()
    original = module._SelectedReview.object
    consumed = []

    def observe(review, path):
        consumed.append(path)
        return original(review, path)

    monkeypatch.setattr(module._SelectedReview, "object", observe)
    module.project_native(panel.tmp_path, {"records": records}, **selection)
    assert panel.paths["result"] not in consumed


def test_contradiction_denominator_excludes_baseline_neighbor(panel):
    from eval_author_shared import task_evidence

    panel.receipt.update(status="passed", purpose="closure")
    records = panel.seal()["records"]
    baseline_path = panel.paths["receipt"].with_name("baseline.json")
    baseline_path.write_text(json.dumps(dict(panel.receipt, purpose="baseline")))
    digest = task_evidence.digest(baseline_path)
    path = baseline_path.relative_to(panel.tmp_path).as_posix()
    records.append(
        dict(
            next(row for row in records if row["id"] == "receipt"),
            id="baseline",
            path=path,
            digest=digest,
            evidence=[{"path": path, "digest": digest}],
        )
    )
    selection = {**panel.selection, "receipt_ids": ["receipt", "baseline"]}
    completed = run_rubric_cli(panel, {"artifacts": records, "native": selection})
    assert completed.returncode == 1
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    summary = report["measurements"]["native"]["receipt_integrity"]
    assert summary["selected_receipts"] == 2
    assert summary["eligible_opportunities"] == summary["contradictions"] == summary["excluded"] == 1
    assert summary["violation_rate"] == 1.0
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    counts = {row["name"]: row for row in check["measurements"] if row["unit"] == "selected receipt claims"}
    assert counts["contradictions"]["value"] == 1
    assert counts["contradictions"]["denominator"] == 1
    for name in ("eligible_opportunities", "unknown", "excluded", "execution_errors", "mismatched"):
        assert counts[name]["denominator"] == 2


def test_report_revalidates_integrity_instead_of_trusting_saved_counts(panel, monkeypatch):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"))
    module = importlib.import_module("skills.eval-author-rubric.scripts.measure")
    from rubric_report import build_report
    from rubric_scoring import assess

    panel.receipt.update(status="passed", purpose="closure")
    selection = {"artifacts": panel.seal()["records"], "native": panel.selection}
    report = module.measure(panel.tmp_path, selection)
    report["measurements"]["native"]["receipt_integrity"]["contradictions"] = 0
    rebuilt = build_report(
        assess(report["measurements"], selection, root=panel.tmp_path),
        report["measurements"],
        selection,
        panel.tmp_path,
    )
    check = next(row for row in rebuilt["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    assert not any(row["id"] == "selected_receipt_status_consistent" for row in check.get("predicate_results", []))
    assert "revalidation" in check["remaining_gap"]
