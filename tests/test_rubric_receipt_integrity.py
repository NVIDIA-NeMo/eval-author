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
    assert measurement["eligible_claims"] == 0
    assert measurement["contradictions"] == 0
    assert measurement["unknown"] == 1
    assert measurement["contradiction_rate"] is None
    assert measurement["context_rot_score"] is None
    assert output["controls"][0]["integrity"]["status"] == "unknown"


@pytest.mark.parametrize(
    "claimed,reward,expected",
    [("passed", 0, "contradiction"), ("passed", 1, "consistent"), ("behavior_failed", 0, "not_applicable")],
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
    eligible = int(claimed == "passed")
    assert native["receipt_integrity"]["eligible_claims"] == eligible
    assert native["receipt_integrity"]["not_applicable"] == 1 - eligible
    assert native["receipt_integrity"]["contradictions"] == int(expected == "contradiction")
    assert native["receipt_integrity"]["unknown"] == 0
    rate = native["receipt_integrity"]["contradiction_rate"]
    assert rate == ((1.0 if expected == "contradiction" else 0.0) if eligible else None)
    assert report["items"]["CREATE-07"]["score"] is None
    assert "PRIVATE-" not in completed.stdout
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    predicate = next(row for row in check["predicate_results"] if row["id"] == "selected_receipt_status_consistent")
    assert predicate["outcome"] == {"contradiction": "FAIL", "consistent": "PASS"}.get(expected, "BLOCKED")
    assert check["outcome"] == "PARTIAL"
    groups = {row["id"]: row for row in check["group_scores"]}
    assert groups["receipt_consistency"]["score"] == {"contradiction": 0, "consistent": 3}.get(expected)
    assert groups["required_verification"]["score"] is None
    assert groups["required_verification"]["unknown"] == 3
    assert groups["required_verification"]["outcome"] == "unassessed"
    assert all(row["scope"] == "verification_integrity" for row in groups.values())
    assert "aggregate_score" not in report
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
    assert summary["eligible_claims"] == int(kind in {"alternative", "empty"})
    assert summary["unknown"] == 0


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
    assert measurement["receipt_integrity"]["eligible_claims"] == 0


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
    assert summary["eligible_claims"] == 2
    assert summary["contradictions"] == 1
    assert summary["contradiction_rate"] == 0.5
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    groups = {row["id"]: row for row in check["group_scores"]}
    assert groups["receipt_consistency"]["score"] == 1
    assert groups["receipt_consistency"]["passed"] == groups["receipt_consistency"]["failed"] == 1
    assert groups["required_verification"]["score"] is None
    assert report["items"]["CREATE-07"]["score"] is None
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
    assert native["receipt_integrity"]["eligible_claims"] == 0
    assert native["receipt_integrity"]["contradictions"] == 0
    assert native["receipt_integrity"]["unknown"] == 1
    assert native["receipt_integrity"]["contradiction_rate"] is None
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
    assert native["receipt_integrity"]["eligible_claims"] == 0
    assert native["receipt_integrity"]["contradictions"] == 0
    assert native["receipt_integrity"]["unknown"] == 1
    assert native["receipt_integrity"]["contradiction_rate"] is None
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
    assert summary["eligible_claims"] == summary["contradictions"] == summary["excluded"] == 1
    assert summary["contradiction_rate"] == 1.0
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    counts = {row["name"]: row for row in check["measurements"] if row["unit"] == "selected receipt claims"}
    assert counts["contradictions"]["value"] == 1
    assert counts["contradictions"]["denominator"] == 1
    for name in ("eligible_claims", "unknown", "excluded", "execution_errors", "mismatched"):
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
    assert not check.get("group_scores")


def select(panel, records, ident, path, role):
    from eval_author_shared import task_evidence

    digest = task_evidence.digest(path)
    relative = path.relative_to(panel.tmp_path).as_posix()
    template = records[0]
    records.append(
        dict(
            template, id=ident, role=role, path=relative, digest=digest, evidence=[{"path": relative, "digest": digest}]
        )
    )
    return records


@pytest.mark.parametrize("field", ["trial_name", "trace"])
def test_later_reference_failure_withdraws_an_inspected_claim(panel, field):
    panel.receipt.update(status="passed", purpose="closure")
    if field == "trial_name":
        panel.result["trial_name"] = ""
    records = panel.seal()["records"]
    if field == "trace":
        panel.paths["trace"].write_text(json.dumps(dict(panel.trace, session_id="changed")))
        records = [row for row in records if row["id"] != "trace"]
        records = select(panel, records, "trace", panel.paths["trace"], "trace")
    output = adapter().project_native(panel.tmp_path, {"records": records}, **panel.selection)
    assert "native.reference_invalid" in {row["code"] for row in output["diagnostics"]}
    assert output["controls"][0]["integrity"]["code"] == "reference_invalid"
    summary = output["receipt_integrity"]
    assert summary["contradictions"] == summary["eligible_claims"] == 0
    assert summary["unknown"] == 1


def test_receipt_return_code_contradiction_needs_no_native_result(panel):
    panel.receipt.update(status="passed", returncode=1, purpose="closure")
    records = panel.seal()["records"]
    panel.paths["result"].unlink()
    output = adapter().project_native(panel.tmp_path, {"records": records}, **panel.selection)
    integrity = output["controls"][0]["integrity"]
    assert (integrity["basis"], integrity["status_claim"]) == ("receipt", "contradiction")
    assert output["receipt_integrity"]["contradictions"] == output["receipt_integrity"]["eligible_claims"] == 1


def test_required_verification_separates_failed_missing_and_unknown_freshness(panel):
    panel.receipt["purpose"] = "closure"
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    verification = output["required_verification"]
    assert verification["required_cases"] == 3
    assert verification["states"]["fail"] == 1
    assert verification["states"]["missing"] == 2
    assert verification["current_pass"] == verification["current_fail"] == 0
    assert verification["verified"] is False
    assert verification["capture"] == "unknown"
    rows = {row["case"]: row for row in verification["cases"]}
    assert rows["reference"] == {
        "manifest": "manifest",
        "case": "reference",
        "kind": "reference",
        "state": "fail",
        "freshness": "unknown",
        "receipts": 1,
        "superseded_receipts": 0,
    }
    assert rows["incorrect"]["state"] == rows["empty"]["state"] == "missing"
    assert "agent" not in rows


def test_out_of_place_repair_marks_receipts_on_the_old_manifest_for_review(panel):
    from eval_author_shared import task_evidence

    panel.receipt["purpose"] = "closure"
    records = panel.seal()["records"]
    repaired = panel.paths["manifest"].with_name("repair.json")
    repaired.write_text(
        json.dumps(
            dict(
                panel.manifest,
                repair_count=1,
                supersedes={"manifest_sha256": task_evidence.digest(panel.paths["manifest"])},
            )
        )
    )
    records = select(panel, records, "repair", repaired, "manifest")
    selection = {**panel.selection, "manifest_ids": ["manifest", "repair"]}
    lineage = adapter().project_native(panel.tmp_path, {"records": records}, **selection)["superseded_references"]
    assert lineage["superseded_manifests"] == lineage["references"] == lineage["enacted"] == 1
    assert lineage["confirmed"] == 0
    assert lineage["findings"] == [
        {
            "receipt": "receipt",
            "case": "reference",
            "superseded_manifest_sha256": task_evidence.digest(panel.paths["manifest"]),
            "superseding_manifest": "repair",
            "effect": "enacted",
            "status": "review_candidate",
        }
    ]


def test_in_place_repair_excludes_the_old_receipt_from_current_verification(panel):
    from eval_author_shared import task_evidence

    panel.receipt.update(status="passed", purpose="closure")
    records = panel.seal()["records"]
    old = task_evidence.digest(panel.paths["manifest"])
    panel.paths["manifest"].write_text(
        json.dumps(dict(panel.manifest, repair_count=1, supersedes={"manifest_sha256": old}))
    )
    records = [row for row in records if row["id"] != "manifest"]
    records = select(panel, records, "manifest", panel.paths["manifest"], "manifest")
    output = adapter().project_native(panel.tmp_path, {"records": records}, **panel.selection)
    assert {"code": "native.receipt_superseded", "subject": "receipt", "status": "unknown"} in output["diagnostics"]
    assert output["controls"][0]["integrity"]["code"] == "superseded"
    assert output["receipt_integrity"]["excluded"] == 1
    assert output["receipt_integrity"]["contradictions"] == 0
    rows = {row["case"]: row for row in output["required_verification"]["cases"]}
    assert rows["reference"]["state"] == "missing"
    assert rows["reference"]["superseded_receipts"] == 1
    assert output["superseded_references"]["references"] == 1


@pytest.mark.parametrize("kind", ["empty", "missing", "malformed", "baseline"])
def test_cli_unassessed_group_scores_do_not_become_zero(panel, kind):
    panel.receipt.update(status="passed", purpose="baseline" if kind == "baseline" else "closure")
    records = panel.seal()["records"]
    selected = panel.selection
    if kind == "empty":
        selected = {key: [] for key in selected}
    elif kind == "missing":
        panel.paths["receipt"].unlink()
    elif kind == "malformed":
        panel.paths["receipt"].write_text("[]")
        records = [row for row in records if row["id"] != "receipt"]
        select(panel, records, "receipt", panel.paths["receipt"], "receipt")
    completed = run_rubric_cli(panel, {"artifacts": records, "native": selected})
    assert completed.returncode == 1
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    receipt, required = check["group_scores"]
    assert receipt["score"] is required["score"] is None
    assert receipt["outcome"] == required["outcome"] == "unassessed"
    assert receipt["failed"] == required["failed"] == 0
    assert receipt["unknown"] == int(kind in {"missing", "malformed"})
    assert receipt["excluded"] == int(kind == "baseline")
    assert report["items"]["CREATE-07"]["score"] is None
