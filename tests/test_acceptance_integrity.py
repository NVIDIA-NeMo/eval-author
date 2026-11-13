# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check typed acceptance-claim integrity with real recorder receipts and attempt ledgers."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.parametrize("entrypoint", ["function", "cli"])
def test_empty_evidence_leaves_a_passed_claim_unknown(tmp_path, entrypoint):
    path = saved(tmp_path, [])
    if entrypoint == "cli":
        measured = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "task_pipeline.py"),
                "check-acceptance",
                "--report",
                str(path),
                "--integrity",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        code, result = measured.returncode, json.loads(measured.stdout)["acceptance_integrity"]
    else:
        code, result = integrity(path)
    assert code == 1
    assert result["status"] == "unknown"
    assert result["eligible_claims"] == 1
    assert result["violations"] == 0
    assert result["required_verification"]["required_cases"] == 0
    assert result["required_verification"]["verified"] is False
    assert codes(result) == [("missing_required", "unknown")]


def test_cited_failed_control_contradicts_the_passed_claim(tmp_path, prepared):
    receipts = complete(tmp_path, prepared[2])
    failed = execute(tmp_path, prepared[2], "reference", "reference-failed", reward=0)
    code, result = integrity(saved(tmp_path, [failed, *receipts]))
    assert code == 1
    assert result["status"] == "violation"
    assert ("cited_failure", "violation") in codes(result)


@pytest.mark.parametrize("citation", ["duplicate", "distinct"])
def test_failure_findings_count_separately_from_the_single_claim(tmp_path, prepared, capsys, citation):
    receipts = complete(tmp_path, prepared[2])
    first = execute(tmp_path, prepared[2], "reference", "first-failure", reward=0)
    second = (
        first if citation == "duplicate" else execute(tmp_path, prepared[2], "reference", "second-failure", reward=0)
    )
    path = saved(tmp_path, [first, second, *receipts])
    code = pipeline.main(["check-acceptance", "--report", str(path), "--integrity"])
    result = json.loads(capsys.readouterr().out)["acceptance_integrity"]
    assert code == 1
    assert result["status"] == "violation"
    assert result["eligible_claims"] == result["violations"] == 1
    expected = 1 if citation == "duplicate" else 2
    assert result["violation_findings"] == expected
    assert codes(result) == [("cited_failure", "violation")] * expected
    reference = next(row for row in result["required_verification"]["cases"] if row["case"] == "reference")
    assert reference["receipts"] == expected + 1


def test_duplicate_passing_citation_does_not_inflate_control_receipts(tmp_path, prepared):
    receipts = complete(tmp_path, prepared[2])
    code, result = integrity(saved(tmp_path, [receipts[0], *receipts]))
    assert code == 0
    assert result["status"] == "consistent"
    assert result["violations"] == 0
    assert all(row["receipts"] == 1 for row in result["required_verification"]["cases"])
    assert result["violation_findings"] == 0
    assert len(result["findings"]) == 3


def test_registered_failure_omitted_from_the_claim_is_a_violation(tmp_path, prepared):
    execute(tmp_path, prepared[2], "incorrect", "incorrect-accepted", reward=1)
    code, result = integrity(saved(tmp_path, complete(tmp_path, prepared[2])))
    assert code == 1
    assert codes(result) == [("omitted_failure", "violation")]


@pytest.mark.parametrize("failure", ["returncode", "native_health"])
def test_omitted_execution_failure_cannot_be_hidden_by_a_passed_status(tmp_path, prepared, capsys, failure):
    failed = execute(
        tmp_path,
        prepared[2],
        "reference",
        "execution-failed",
        command=[sys.executable, "-c", "raise SystemExit(7)"] if failure == "returncode" else None,
        error="failed" if failure == "native_health" else None,
    )
    receipt = json.loads(failed.read_text())
    assert receipt["status"] == "infrastructure_error"
    write(failed, {**receipt, "status": "passed"})
    path = saved(tmp_path, complete(tmp_path, prepared[2]))
    code = pipeline.main(["check-acceptance", "--report", str(path), "--integrity"])
    result = json.loads(capsys.readouterr().out)["acceptance_integrity"]
    assert code == 1
    assert result["status"] == "violation"
    assert result["required_verification"]["verified"] is True
    assert result["eligible_claims"] == result["violations"] == 1
    assert codes(result) == [("omitted_failure", "violation")]


@pytest.fixture
def cli_ledger(tmp_path, prepared):
    manifest = prepared[2]
    receipts = []
    # Synthetic native payloads satisfy the real subprocess reader, without Harbor execution.
    for case, reward in (("reference", 1), ("incorrect", 0), ("empty", 0)):
        native = tmp_path / f"cli-{case}-native.json"
        receipt = tmp_path / f"cli-{case}-receipt.json"
        payload = {
            "rollout_id": case,
            "reward": reward,
            "error": None,
            "task_checksum": "synthetic",
            "exception_info": None,
        }
        command = [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(native)!r}).write_text({json.dumps(payload)!r})",
        ]
        evidence.run(manifest, case, case, receipt, native, None, command)
        receipts.append(receipt)
    failed = execute(
        tmp_path, manifest, "reference", "omitted-failed", command=[sys.executable, "-c", "raise SystemExit(7)"]
    )
    ledger = Path(evidence.read(manifest)["attempts"])
    entry = ledger / (hashlib.sha256(b"omitted-failed").hexdigest() + ".json")
    assert evidence.read(entry)["receipt"] == str(failed)
    return saved(tmp_path, receipts), entry


def cli_integrity(path):
    measured = subprocess.run(
        [sys.executable, str(SCRIPTS / "task_pipeline.py"), "check-acceptance", "--report", str(path), "--integrity"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return measured.returncode, json.loads(measured.stdout)["acceptance_integrity"]


@pytest.mark.parametrize("scope", ["current", "foreign_revision", "baseline"])
@pytest.mark.parametrize(
    "mutation",
    [
        "empty_object",
        "invalid_json",
        *[f"missing_{field}" for field in ("run_id", "receipt", "purpose", "case", "manifest_sha256")],
        *[f"wrong_type_{field}" for field in ("run_id", "receipt", "purpose", "case", "manifest_sha256", "kind")],
        "empty_run_id",
        "wrong_run_hash",
        "invalid_manifest_hash",
        "invalid_purpose",
        "invalid_kind",
        "unexpected_schema",
        "empty_receipt",
        "escaping_receipt",
        "nul_receipt",
    ],
)
def test_malformed_registration_cannot_hide_an_omitted_failure(cli_ledger, scope, mutation):
    path, entry = cli_ledger
    code, result = cli_integrity(path)
    assert code == 1 and result["status"] == "violation"
    assert codes(result) == [("omitted_failure", "violation")]
    attempt = evidence.read(entry)
    if scope == "foreign_revision":
        attempt["manifest_sha256"] = "a" * 64
    elif scope == "baseline":
        attempt["purpose"] = "baseline"
    if mutation == "empty_object":
        attempt = {}
    elif mutation.startswith("missing_"):
        del attempt[mutation.removeprefix("missing_")]
    elif mutation.startswith("wrong_type_"):
        attempt[mutation.removeprefix("wrong_type_")] = []
    else:
        changes = {
            "empty_run_id": {"run_id": ""},
            "wrong_run_hash": {"run_id": "another-run"},
            "invalid_manifest_hash": {"manifest_sha256": "not-a-sha256"},
            "invalid_purpose": {"purpose": "unknown"},
            "invalid_kind": {"kind": "unknown"},
            "unexpected_schema": {"schema": evidence.SCHEMA},
            "empty_receipt": {"receipt": ""},
            "escaping_receipt": {"receipt": "../outside-receipt.json"},
            "nul_receipt": {"receipt": "bad\u0000path"},
        }
        attempt.update(changes.get(mutation, {}))
    write(entry, attempt)
    if mutation == "invalid_json":
        entry.write_text("{")
    before = {item: item.read_bytes() for item in path.parent.rglob("*.json")}
    code, result = cli_integrity(path)
    assert code == 1
    assert result["status"] == "unknown"
    assert result["required_verification"]["verified"] is True
    assert result["violations"] == result["violation_findings"] == 0
    assert codes(result) == [("ledger_unavailable", "unknown")]
    assert all(item.read_bytes() == raw for item, raw in before.items())


@pytest.mark.parametrize("purpose", ["closure", "baseline"])
@pytest.mark.parametrize("change", [{"case": "undeclared"}, {"kind": "agent"}])
def test_current_registration_must_match_its_declared_case(cli_ledger, purpose, change):
    path, entry = cli_ledger
    write(entry, {**evidence.read(entry), "purpose": purpose, **change})
    code, result = cli_integrity(path)
    assert code == 1
    assert result["status"] == "unknown"
    assert codes(result) == [("ledger_unavailable", "unknown")]


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("scope", ["current", "foreign_revision", "baseline", "agent", "diagnostic"])
def test_valid_registration_filtering_and_historical_kind_support(cli_ledger, historical, scope):
    path, entry = cli_ledger
    attempt = evidence.read(entry)
    if scope == "foreign_revision":
        attempt.update(manifest_sha256="a" * 64, case="case-from-older-contract")
    elif scope == "baseline":
        attempt["purpose"] = "baseline"
    elif scope in ("agent", "diagnostic"):
        manifest_path = Path(evidence.read(Path(attempt["receipt"]))["manifest"])
        manifest = evidence.read(manifest_path)
        if scope == "diagnostic":
            manifest["contract"]["cases"]["diagnostic"] = {
                **manifest["contract"]["cases"]["reference"],
                "kind": "diagnostic",
            }
            write(manifest_path, manifest)
            digest = evidence.digest(manifest_path)
            for receipt in path.parent.glob("*-receipt.json"):
                write(receipt, {**evidence.read(receipt), "manifest_sha256": digest})
            for registration in entry.parent.glob("*.json"):
                write(registration, {**evidence.read(registration), "manifest_sha256": digest})
            attempt["manifest_sha256"] = digest
        attempt.update(case=scope, kind=scope)
    if historical:
        del attempt["kind"]
    write(entry, attempt)
    code, result = cli_integrity(path)
    if scope == "current":
        assert code == 1
        assert result["status"] == "violation"
        assert codes(result) == [("omitted_failure", "violation")]
    else:
        assert code == 0
        assert result["status"] == "consistent"
        assert codes(result) == []
    assert result["required_verification"]["verified"] is True


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


def test_absent_evidence_field_is_not_assessed(tmp_path):
    path = write(
        tmp_path / "acceptance.json",
        {"schema": "nemo.eval_author.task_gap_verification.v2", "task_validation": "passed"},
    )
    code, result = integrity(path)
    assert code == 1
    assert (result["status"], result["eligible_claims"], result["violations"]) == ("not_applicable", 0, 0)
    assert result["violation_findings"] == 0
    assert result["findings"] == []


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
