# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    audit = importlib.import_module("audit_verifier_evidence")
    evidence = audit.task_evidence
    task = tmp_path / "tasks/example"
    task.mkdir(parents=True)
    (task / "verify.py").write_text("# A synthetic task revision, never executed by the audit.\n")
    records = tmp_path / "measurements/example"
    records.mkdir(parents=True)
    runs = tmp_path / "runs"
    runs.mkdir()
    report = tmp_path / "audit.md"
    report.write_text("## Key findings\n\nNo defects observed.\n")
    cases: dict[str, dict] = {}
    for name, kind, reward in [
        ("reference", "reference", 1),
        ("alternative", "alternative", 1),
        ("nop", "empty", 0),
        ("agent", "agent", 1),
        ("early-exit", "incorrect", 0),
        ("comments", "incorrect", 0),
        ("forged-success", "incorrect", 0),
        ("all-failure", "incorrect", 0),
    ]:
        cases[name] = {
            "kind": kind,
            "requirement": f"{name} must earn {reward}",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
        }
    contract = {
        "task_id": "example",
        "provider": "harbor",
        "native_run_id": "/id",
        "cases": cases,
        "side_effect_not_applicable": "This synthetic task only returns a value.",
    }
    manifest = records / "revision.json"

    def write(path, value):
        path.write_text(json.dumps(value))

    write(
        manifest,
        {
            "schema": evidence.MANIFEST_SCHEMA,
            "task_dir": str(task),
            "task_sha256": evidence.task_digest(task),
            "harbor_task_checksum": "synthetic",
            "contract": contract,
            "construction": {"status": "unmeasured"},
            "repair_count": 0,
        },
    )

    def record(name, reward, *, suffix="", error=None, status=None):
        result = runs / f"{name}{suffix}.json"
        write(
            result,
            {
                "id": name + suffix,
                "task_checksum": "synthetic",
                "exception_info": error,
                "verifier_result": {"rewards": {"reward": reward}},
            },
        )
        receipt = records / f"{name}{suffix}.json"
        expected = cases[name]["checks"][0]["min"]
        write(
            receipt,
            {
                "schema": evidence.SCHEMA,
                "manifest": str(manifest),
                "manifest_sha256": evidence.digest(manifest),
                "case": name,
                "purpose": "closure",
                "returncode": 0,
                "result": str(result),
                "result_sha256": evidence.digest(result),
                "native_run_id": name + suffix,
                "status": status or ("passed" if reward == expected else "behavior_failed"),
            },
        )
        return receipt, result

    for name, case in cases.items():
        record(name, case["checks"][0]["min"])

    def check():
        return audit.check(tmp_path, report, [task], [records], [runs])

    def inspect(case, *, verify_historical=False):
        path = records / f"{case}.json"
        review = audit.Review(tmp_path, [records, runs])
        return audit.inspect_receipt(
            review,
            path,
            json.loads(path.read_text()),
            {task},
            include_integrity=True,
            verify_historical=verify_historical,
        )

    def findings(*paths):
        report.write_text(
            "## Key findings\n\n"
            + "\n\n".join(
                f"The recorded control needs review: [evidence]({p.relative_to(tmp_path).as_posix()})." for p in paths
            )
        )

    return SimpleNamespace(**locals())


@pytest.mark.parametrize("case", ["early-exit", "comments", "forged-success", "all-failure"])
def test_each_rewarded_cheat_requires_a_finding_even_without_agent_trace(panel, case):
    receipt, _ = panel.record(case, 1)
    (panel.records / "agent.json").unlink()
    result = panel.check()
    assert not result["valid"]
    assert result["missing_findings"] == [str(receipt.relative_to(panel.tmp_path))]
    panel.findings(receipt)
    assert panel.check()["valid"]


def test_receipt_integrity_records_a_validated_positive_control(panel):
    row = panel.inspect("reference")
    assert row["integrity"] == {
        "status": "consistent",
        "execution": "healthy",
        "behavior": "passed",
        "status_claim": "consistent",
        "code": "validated",
        "freshness": "current",
    }


@pytest.mark.parametrize("status", ["passed", "behavior_failed"])
def test_receipt_integrity_separates_failed_behavior_from_false_pass_claim(panel, status):
    panel.record("early-exit", 1, status=status)
    row = panel.inspect("early-exit")
    contradiction = status == "passed"
    assert row["classification"] == ("unproven" if contradiction else "failed_control")
    assert row["integrity"] == {
        "status": "mismatched" if contradiction else "consistent",
        "execution": "healthy",
        "behavior": "failed",
        "status_claim": "contradiction" if contradiction else "consistent",
        "code": "status_contradiction" if contradiction else "validated",
        "freshness": "current",
    }


@pytest.mark.parametrize("damage", ["manifest_digest", "result_digest", "run_id", "malformed", "missing_result"])
def test_receipt_integrity_requires_bound_readable_native_sources(panel, damage):
    path, native = panel.record("early-exit", 1, status="passed")
    receipt = json.loads(path.read_text())
    if damage == "manifest_digest":
        receipt["manifest_sha256"] = "0" * 64
    elif damage == "result_digest":
        receipt["result_sha256"] = "0" * 64
    elif damage == "run_id":
        receipt["native_run_id"] = "another-run"
    elif damage == "malformed":
        native.write_text("{")
        receipt["result_sha256"] = panel.evidence.digest(native)
    else:
        native.unlink()
    panel.write(path, receipt)
    row = panel.inspect("early-exit")
    assert row["classification"] == "unproven"
    assert row["integrity"]["status"] == ("unknown" if damage == "missing_result" else "mismatched")
    assert row["integrity"]["code"] == ("source_missing" if damage == "missing_result" else "invalid_evidence")
    assert row["integrity"]["behavior"] == row["integrity"]["status_claim"] == "unknown"


@pytest.mark.parametrize("kind", ["agent", "diagnostic", "baseline"])
def test_receipt_integrity_excludes_nonacceptance_evidence(panel, kind):
    case = "agent" if kind == "agent" else "early-exit"
    if kind == "diagnostic":
        manifest = json.loads(panel.manifest.read_text())
        manifest["contract"]["cases"][case]["kind"] = "diagnostic"
        panel.write(panel.manifest, manifest)
    path, _ = panel.record(case, 1, status="passed")
    receipt = json.loads(path.read_text())
    if kind == "baseline":
        receipt["purpose"] = "baseline"
    review = panel.audit.Review(panel.tmp_path, [panel.records, panel.runs])
    row = panel.audit.inspect_receipt(review, path, receipt, {panel.task}, include_integrity=True)
    assert row["integrity"]["status_claim"] == "excluded"
    assert row["integrity"]["code"] == "excluded"
    assert row["classification"] == ("agent_run" if kind == "agent" else "unproven")


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unsupported"),
        ("purpose", "unsupported"),
        ("purpose", None),
        ("status", "unsupported"),
        ("returncode", False),
        ("provider", "unsupported"),
        ("status", "behavior_failed"),
    ],
)
def test_receipt_integrity_does_not_infer_false_pass_without_supported_claim(panel, field, value):
    path, _ = panel.record("early-exit", 0 if value == "behavior_failed" else 1, status="passed")
    receipt = json.loads(path.read_text())
    if field == "provider":
        manifest = json.loads(panel.manifest.read_text())
        manifest["contract"][field] = value
        panel.write(panel.manifest, manifest)
        receipt["manifest_sha256"] = panel.evidence.digest(panel.manifest)
    else:
        receipt[field] = value
    review = panel.audit.Review(panel.tmp_path, [panel.records, panel.runs])
    row = panel.audit.inspect_receipt(review, path, receipt, {panel.task}, include_integrity=True)
    assert row["integrity"]["status_claim"] == "unknown"
    assert row["integrity"]["code"] != "status_contradiction"


@pytest.mark.parametrize(
    "status,returncode", [("infrastructure_error", 0), ("infrastructure_error", None), ("passed", 1)]
)
def test_receipt_integrity_records_structured_execution_error_without_result(panel, status, returncode):
    path, native = panel.record("early-exit", 1, status=status)
    native.unlink()
    receipt = json.loads(path.read_text())
    if returncode is None:
        receipt.pop("returncode")
    else:
        receipt["returncode"] = returncode
    panel.write(path, receipt)
    row = panel.inspect("early-exit")
    assert row["integrity"]["execution"] == "execution_error"
    assert row["integrity"]["code"] == "execution_error"
    assert row["integrity"]["status"] == row["integrity"]["behavior"] == row["integrity"]["status_claim"] == "unknown"


def test_receipt_integrity_is_opt_in_without_changing_legacy_decisions(panel):
    path, _ = panel.record("early-exit", 1, status="passed")
    receipt = json.loads(path.read_text())
    review = panel.audit.Review(panel.tmp_path, [panel.records, panel.runs])
    legacy = panel.audit.inspect_receipt(review, path, receipt, {panel.task})
    assert "integrity" not in legacy
    assessed = panel.audit.inspect_receipt(review, path, receipt, {panel.task}, include_integrity=True)
    assert {key: value for key, value in assessed.items() if key != "integrity"} == legacy
    assert all("integrity" not in row for row in panel.check()["controls"])


@pytest.mark.parametrize("verify_historical", [False, True])
def test_receipt_integrity_keeps_historical_freshness_separate(panel, verify_historical):
    panel.record("early-exit", 1, status="passed")
    (panel.task / "verify.py").write_text("# repaired revision\n")
    row = panel.inspect("early-exit", verify_historical=verify_historical)
    assert row["integrity"]["freshness"] == "stale"
    assert row["integrity"]["status_claim"] == ("contradiction" if verify_historical else "unknown")
    assert row["classification"] == ("unproven" if verify_historical else "stale")


@pytest.mark.parametrize("damage", ["health", "missing_check", "exception_text"])
def test_receipt_integrity_never_parses_failure_text_as_execution_or_behavior(panel, damage):
    path, native_path = panel.record("early-exit", 1, status="passed")
    native = json.loads(native_path.read_text())
    receipt = json.loads(path.read_text())
    if damage == "health":
        native["exception_info"] = {"exception_type": "Receipt status disagrees with native assertions"}
    elif damage == "missing_check":
        native.pop("verifier_result")
    else:
        native["id"] = "Control has no healthy completed execution"
        receipt["error"] = "infrastructure_error"
    panel.write(native_path, native)
    receipt["result_sha256"] = panel.evidence.digest(native_path)
    panel.write(path, receipt)
    integrity = panel.inspect("early-exit")["integrity"]
    assert integrity["status"] == "mismatched"
    assert integrity["execution"] == integrity["behavior"] == integrity["status_claim"] == "unknown"


def test_receipt_integrity_recovery_does_not_erase_earlier_contradiction(panel):
    path, _ = panel.record("early-exit", 1, status="passed")
    panel.record("early-exit", 0, suffix="-recovered")
    assert panel.inspect("early-exit")["integrity"]["status_claim"] == "contradiction"
    assert panel.inspect("early-exit-recovered")["integrity"]["status_claim"] == "consistent"
    assert path.relative_to(panel.tmp_path).as_posix() in panel.check()["missing_findings"]


def test_clean_neighbors_do_not_need_failure_findings(panel):
    result = panel.check()
    assert result["valid"]
    assert {row["classification"] for row in result["controls"]} == {"passed", "agent_run"}


def test_blanket_rejection_exposes_the_rejected_valid_neighbor(panel):
    reference, _ = panel.record("reference", 0)
    alternative, _ = panel.record("alternative", 0)
    result = panel.check()
    assert not result["valid"]
    assert sum(row["classification"] == "false_rejection" for row in result["controls"]) == 2
    panel.findings(reference, alternative)
    assert panel.check()["valid"]


@pytest.mark.parametrize("placement", ["bare", "next_actions", "code", "comment", "reference_link"])
def test_link_without_visible_finding_cannot_clear_a_failure(panel, placement):
    receipt, _ = panel.record("early-exit", 1)
    link = f"[control]({receipt.relative_to(panel.tmp_path)})"
    snippets = {
        "bare": link,
        "next_actions": "## Next actions and open questions\n\nRepair this defect: " + link,
        "code": "```\nThis cheat earned credit: " + link + "\n```",
        "comment": "<!-- This cheat earned credit: " + link + " -->",
        "reference_link": "This cheat earned credit: [control][c]\n\n[c]: " + str(receipt.relative_to(panel.tmp_path)),
    }
    panel.report.write_text("## Key findings\n\n" + snippets[placement])
    assert not panel.check()["valid"]


def test_later_pass_and_owner_acceptance_do_not_hide_earlier_failure(panel):
    receipt, _ = panel.record("forged-success", 1)
    panel.record("forged-success", 0, suffix="-later")
    panel.report.write_text("## Key findings\n\nThe owner accepted this task despite its known defects.\n")
    assert not panel.check()["valid"]
    panel.findings(receipt)
    assert panel.check()["valid"]


def test_task_change_requires_historical_evidence_limit_instead_of_current_failure(panel):
    panel.record("early-exit", 1)
    (panel.task / "verify.py").write_text("# repaired revision\n")
    result = panel.check()
    assert not result["valid"]
    assert "failed_control" not in {row["classification"] for row in result["controls"]}
    panel.findings(panel.manifest)
    assert panel.check()["valid"]


@pytest.mark.parametrize("damage", ["native_bytes", "manifest_bytes", "health", "missing_result", "lying_status"])
def test_invalid_evidence_stays_unproven_until_reported(panel, damage):
    receipt, native = panel.record("early-exit", 1)
    if damage == "native_bytes":
        native.write_text(native.read_text() + "\n")
    elif damage == "manifest_bytes":
        panel.manifest.write_text(panel.manifest.read_text() + "\n")
    elif damage == "health":
        panel.record("early-exit", 1, error={"exception_type": "VerifierError"})
    elif damage == "missing_result":
        native.unlink()
    else:
        panel.record("early-exit", 1, status="passed")
    result = panel.check()
    assert not result["valid"]
    assert any(row["classification"] == "unproven" for row in result["controls"])
    panel.findings(panel.manifest, receipt)
    assert panel.check()["valid"]


def test_declared_but_unexecuted_controls_need_a_limitation(panel):
    (panel.records / "comments.json").unlink()
    result = panel.check()
    assert not result["valid"]
    assert any(row.get("cases") == ["comments"] for row in result["controls"])
    panel.findings(panel.manifest)
    assert panel.check()["valid"]


def test_unsupported_or_absent_receipts_do_not_qualify_a_task(panel):
    for path in panel.records.glob("*.json"):
        path.unlink()
    (panel.records / "unsupported.json").write_text('{"schema": "some.other.format", "passed": true}')
    assert not panel.check()["valid"]
    panel.findings(panel.task)
    result = panel.check()
    assert result["valid"]
    assert result["controls"][0]["classification"] == "unproven"


def test_unselected_native_source_is_not_read(panel, monkeypatch):
    original = Path.read_bytes

    def guarded(path):
        assert not path.is_relative_to(panel.runs), "read an unselected run"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(panel.audit.EvidenceScopeError, match="Select the evidence source"):
        panel.audit.check(panel.tmp_path, panel.report, [panel.task], [panel.records], [])


def test_result_symlink_is_rejected_before_reading(panel):
    _, native = panel.record("early-exit", 1)
    target = panel.tmp_path / "private.json"
    native.rename(target)
    native.symlink_to(target)
    with pytest.raises(panel.audit.EvidenceScopeError, match="symlinks"):
        panel.check()


def test_report_links_do_not_authorize_reading_arbitrary_sources(panel):
    panel.report.write_text("## Key findings\n\nUnrelated [private source](elsewhere/private.json).\n")
    result = panel.check()
    assert result["valid"]
    assert not any(ref["path"].startswith("elsewhere") for ref in result["evidence"])


def test_evidence_references_bind_receipts_manifests_and_native_results(panel):
    result = panel.check()
    refs = {ref["path"]: ref["digest"] for ref in result["evidence"]}
    for path in [panel.manifest, panel.records / "early-exit.json", panel.runs / "early-exit.json"]:
        assert refs[str(path.relative_to(panel.tmp_path))] == panel.evidence.digest(path)


def test_gym_native_assertions_use_the_same_finding_requirement(panel):
    manifest = json.loads(panel.manifest.read_text())
    manifest["contract"]["provider"] = "gym"
    manifest["contract"]["native_run_id"] = "/native_run_id"
    panel.write(panel.manifest, manifest)
    failed, _ = panel.record("forged-success", 1)
    for path in panel.records.glob("*.json"):
        receipt = json.loads(path.read_text())
        if "result" not in receipt:
            continue
        native_path = Path(receipt["result"])
        native = json.loads(native_path.read_text())
        native["native_run_id"] = native["id"]
        panel.write(native_path, native)
        receipt["manifest_sha256"] = panel.evidence.digest(panel.manifest)
        receipt["result_sha256"] = panel.evidence.digest(native_path)
        panel.write(path, receipt)
    result = panel.check()
    assert result["missing_findings"] == [str(failed.relative_to(panel.tmp_path))]
    panel.findings(failed)
    assert panel.check()["valid"]


def test_malformed_receipt_is_not_hidden_by_other_passed_controls(panel):
    receipt = panel.records / "early-exit.json"
    panel.write(receipt, {"schema": panel.evidence.SCHEMA, "case": "early-exit"})
    assert not panel.check()["valid"]
    panel.findings(receipt, panel.manifest)
    assert panel.check()["valid"]


def test_cli_exits_nonzero_for_missing_findings(panel):
    import subprocess
    import sys

    panel.record("early-exit", 1)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "audit_verifier_evidence.py"),
            "--repo",
            str(panel.tmp_path),
            "--report",
            str(panel.report),
            "--task",
            str(panel.task),
            "--evidence-dir",
            str(panel.records),
            "--source-root",
            str(panel.runs),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["missing_findings"]


def test_zero_aggregate_reward_does_not_hide_a_failed_control_assertion(panel):
    manifest = json.loads(panel.manifest.read_text())
    manifest["contract"]["cases"]["early-exit"]["checks"].append({"pointer": "/cheat_credit", "equals": 0})
    panel.write(panel.manifest, manifest)
    for path in panel.records.glob("*.json"):
        receipt = json.loads(path.read_text())
        if "result" not in receipt:
            continue
        receipt["manifest_sha256"] = panel.evidence.digest(panel.manifest)
        if receipt["case"] == "early-exit":
            result = Path(receipt["result"])
            native = json.loads(result.read_text())
            native["cheat_credit"] = 1
            panel.write(result, native)
            receipt["result_sha256"] = panel.evidence.digest(result)
            receipt["status"] = "behavior_failed"
        panel.write(path, receipt)
    result = panel.check()
    assert not result["valid"]
    assert any(row["classification"] == "failed_control" for row in result["controls"])


@pytest.mark.parametrize("heading", ["Key findings", "Findings and limitations"])
def test_findings_links_read_the_current_and_legacy_findings_heading(panel, heading):
    audit = importlib.import_module("audit_verifier_evidence")
    text = f"## {heading}\n\nThis cheat earned credit: [control](records/control.json)\n\n## Next section\n\n"
    links = audit.findings_links(panel.report, text)
    assert links == {(panel.report.parent / "records/control.json").resolve()}
    assert audit.findings_links(panel.report, text.replace(heading, "Other section", 1)) == set()


@pytest.mark.parametrize("keep_original", [True, False])
def test_control_evidence_survives_a_copied_or_moved_repository(panel, tmp_path_factory, keep_original):
    before = panel.check()
    assert before["valid"]
    destination = tmp_path_factory.mktemp("relocated") / "repo"
    if keep_original:
        shutil.copytree(panel.tmp_path, destination)
    else:
        shutil.move(panel.tmp_path, destination)
    result = audit_check = panel.audit.check(
        destination,
        destination / "audit.md",
        [destination / "tasks/example"],
        [destination / "measurements/example"],
        [destination / "runs"],
    )
    assert audit_check["valid"]
    assert {row["classification"] for row in result["controls"]} == {"passed", "agent_run"}
    assert not any(row["classification"] == "unproven" for row in result["controls"])


def test_relocated_evidence_still_fails_when_a_digest_differs(panel, tmp_path_factory):
    destination = tmp_path_factory.mktemp("relocated") / "repo"
    shutil.move(panel.tmp_path, destination)
    (destination / "runs/early-exit.json").write_text("{}")
    result = panel.audit.check(
        destination,
        destination / "audit.md",
        [destination / "tasks/example"],
        [destination / "measurements/example"],
        [destination / "runs"],
    )
    assert any(row["classification"] == "unproven" for row in result["controls"])


def test_absolute_evidence_outside_the_repository_without_a_relocated_match_is_rejected(panel, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "result.json"
    outside.write_text("{}")
    review = panel.audit.Review(panel.tmp_path, [panel.records])
    with pytest.raises(panel.audit.EvidenceScopeError):
        review.path(outside)
