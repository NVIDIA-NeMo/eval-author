# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
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
    report.write_text("## Findings and limitations\n\nNo defects observed.\n")
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

    def findings(*paths):
        report.write_text(
            "## Findings and limitations\n\n"
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
    panel.report.write_text("## Findings and limitations\n\n" + snippets[placement])
    assert not panel.check()["valid"]


def test_later_pass_and_owner_acceptance_do_not_hide_earlier_failure(panel):
    receipt, _ = panel.record("forged-success", 1)
    panel.record("forged-success", 0, suffix="-later")
    panel.report.write_text("## Findings and limitations\n\nThe owner accepted this task despite its known defects.\n")
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
    panel.report.write_text("## Findings and limitations\n\nUnrelated [private source](elsewhere/private.json).\n")
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
