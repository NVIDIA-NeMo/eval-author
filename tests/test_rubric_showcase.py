# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def showcase_output(tmp_path_factory):
    output = tmp_path_factory.mktemp("showcase") / "output"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_rubric_showcase.py"), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    return output


def test_showcase_includes_separate_all18_rule_demo(showcase_output):
    output = showcase_output
    summary = json.loads((output / "showcase.json").read_text())
    demo = summary["criterion_demonstration"]
    assert demo["criterion_count"] == len(summary["criterion_inventory"]) == 18
    assert demo["vector_count_per_root"] == 90
    assert demo["task_qualification"] is False
    assert demo["assessment_scope"] == "synthetic-scoring-demonstration"
    for variant, expected in (("score-0", 0), ("score-1", 1), ("score-2", 2), ("score-3", 3), ("missing", None)):
        report = json.loads((output / f"criterion-scoring/run-a/{variant}/measure-report.json").read_text())
        assert demo["results"][variant] == {
            ident: row["score"] for ident, row in report["criterion_demonstration"].items()
        }
        assert set(demo["results"][variant].values()) == {expected}
        baseline = json.loads((output / f"criterion-scoring/run-a/{variant}/baseline-report.json").read_text())
        assert report["items"] == baseline["items"]
    assert len(summary["production_gaps"]) == 12
    page = (output / "index.html").read_text()
    assert "Synthetic scoring-rule coverage" in page
    assert "synthetic numbers do not qualify" in page
    assert "criterion-scoring/run-a/missing/measure-report.json" in page


def test_showcase_repeats_every_area_in_fresh_root(showcase_output):
    output = showcase_output
    summary = json.loads((output / "showcase.json").read_text())
    repeat = output / "fresh-replay"
    primary = json.loads((output / "semantic-result.json").read_text())
    repeated = json.loads((repeat / "semantic-result.json").read_text())
    assert primary == repeated
    assert set(primary) == {"identity", "provenance", "task_contract", "scorer", "integrity", "criterion_demonstration"}
    assert summary["determinism"]["fresh_run_equal"] is True
    assert set(summary["determinism"]["areas"]) == set(primary)
    assert all(summary["determinism"]["areas"].values())
    assert (output / "integrity/panel.json").read_bytes() != (repeat / "integrity/panel.json").read_bytes()
    assert len(primary["identity"]["package_acceptance"]["cases"]) > 0
    assert len(primary["provenance"]["cases"]) == 16
    assert len(primary["task_contract"]["cases"]) == 6
    assert len(primary["scorer"]["panel"]["cases"]) == 21
    assert len(primary["integrity"]["cases"]) == 8
    assert primary["integrity"]["retry"]["score"] is None
    assert "Fresh-root semantic repeat" in (output / "index.html").read_text()


def test_source_bound_contract_rows_are_validated(showcase_output):
    module = showcase_module()
    module.validate_source_bound_scores(showcase_output)
    summary = module.read(showcase_output / "showcase.json")
    for arm, expected in (("baseline", 0), ("reviewed", None)):
        actual = module.read(showcase_output / f"scorer/rubric-{arm}.json")["items"]["AC-03"]
        assert summary["receipt_authenticity"][arm]["criterion"] == actual
        assert actual["score"] == expected
    for row in summary["task_contract"]["assessments"]:
        report = module.read(showcase_output / row["evidence"])
        assert row["items"] == {
            key: report["items"][key]
            for key in (
                "task-contract.clear-contract",
                "task-contract.discoverable-requirements",
                "task-contract.scoring-authority",
            )
        }
        assert row["items"]["task-contract.discoverable-requirements"]["score"] == (
            0 if row["id"] == "planted-summary" else None
        )


@pytest.mark.parametrize(
    "relative,criterion",
    [
        ("scorer/rubric-baseline.json", "AC-03"),
        ("task-contract/planted-summary/measure-report.json", "task-contract.discoverable-requirements"),
        ("task-contract/clean-neighbor/measure-report.json", "task-contract.discoverable-requirements"),
    ],
)
@pytest.mark.parametrize("mutation", ["remove-row", "remove-score", "alter-score"])
def test_source_bound_validation_rejects_new_row_mutations(showcase_output, tmp_path, relative, criterion, mutation):
    import shutil

    output = tmp_path / "source-bound"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / relative
    report = module.read(path)
    if mutation == "remove-row":
        del report["items"][criterion]
    elif mutation == "remove-score":
        del report["items"][criterion]["score"]
    else:
        report["items"][criterion]["score"] = 3
    path.write_text(json.dumps(report))
    with pytest.raises((ValueError, KeyError)):
        module.validate_source_bound_scores(output)
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises((ValueError, KeyError)):
        module.verify_existing(output)


@pytest.mark.parametrize("mutation", ["remove-row", "remove-score", "alter-score"])
def test_demo_validation_rejects_resealed_mutations(showcase_output, tmp_path, mutation):
    import shutil

    output = tmp_path / "synthetic"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "criterion-scoring/run-a/score-3/measure-report.json"
    report = module.read(path)
    if mutation == "remove-row":
        del report["criterion_demonstration"]["AC-03"]
    elif mutation == "remove-score":
        del report["criterion_demonstration"]["AC-03"]["score"]
    else:
        report["criterion_demonstration"]["AC-03"]["score"] = 2
    path.write_text(json.dumps(report))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises((ValueError, KeyError)):
        module.verify_existing(output)


@pytest.mark.parametrize(
    "source", ["test.sh", "instruction.md", "orders.csv", "Dockerfile", "task.toml", "contract.json"]
)
@pytest.mark.parametrize("mutation", ["delete", "change"])
def test_source_bound_validation_requires_retained_sources(showcase_output, tmp_path, source, mutation):
    import shutil

    output = tmp_path / "lost-source"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "task-contract/planted-summary" / source
    if mutation == "delete":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n")
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="contract"):
        module.validate_source_bound_scores(output)
    with pytest.raises(ValueError, match="contract"):
        module.verify_existing(output)


def test_semantic_projection_retains_full_demo_rows(showcase_output, tmp_path):
    import shutil

    output = tmp_path / "semantic-demo"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "fresh-replay/criterion-scoring/run-b/score-1/measure-report.json"
    report = module.read(path)
    report["criterion_demonstration"]["AC-03"]["remaining_gaps"] = ["changed diagnostic"]
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="criterion_demonstration"):
        module.compare_runs(output)


def test_source_bound_validation_rejects_lost_shared_finding(showcase_output, tmp_path):
    import shutil

    output = tmp_path / "finding"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "scorer/rubric-baseline.json"
    report = module.read(path)
    report["items"]["AC-03"]["finding_ids"] = []
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="shared finding"):
        module.validate_source_bound_scores(output)


def test_showcase_cli_reads_emitted_scores_and_keeps_boundaries(showcase_output):
    output = showcase_output
    summary = json.loads((output / "showcase.json").read_text())
    for arm, expected in (("baseline", 0), ("reviewed", 1)):
        report = json.loads((output / f"scorer/rubric-{arm}.json").read_text())
        assert summary["scorer"][arm]["score"] == report["items"]["AC-02"]["score"] == expected
        assert summary["scorer"][arm]["evidence_strength"] == "E2"
    assert summary["scorer"]["matched_controls"] == summary["scorer"]["attempted_controls"] == 21
    assert [row["observed"] for row in summary["integrity"]["anchors"]] == [0, 1, 2, 3]
    assert all(row["canonical_score"] is None for row in summary["integrity"]["anchors"])
    assert summary["aggregate_score"] is None
    assert summary["native_execution"] is False
    assert summary["workflow_acceptance"] is False
    assert summary["scorer"]["replay_equal"] is True
    assert summary["scorer"]["reviewed"]["alternatives_accepted"] == 1
    assert summary["scorer"]["reviewed"]["alternatives_total"] == 1
    assert summary["scorer"]["reviewed"]["health_errors"] == 3
    assert summary["scorer"]["baseline"]["false_accepts"] == 13
    assert summary["scorer"]["baseline"]["invalid_denominator"] == 13
    assert summary["scorer"]["baseline"]["false_rejects"] == 1
    assert summary["scorer"]["baseline"]["valid_denominator"] == 5
    assert summary["scorer"]["reviewed"]["false_accepts"] == 0
    assert summary["scorer"]["reviewed"]["false_rejects"] == 0
    assert summary["integrity"]["fresh_replay_equal"] is True
    page = (output / "index.html").read_text()
    assert "Unassessed" in page
    assert "Independent held-out" in page
    assert "The fixture demo runs" in page
    assert "Canonical rubric" in page
    assert "receipt_consistency" in page
    assert "scorer/rubric-reviewed.json" in page
    assert "integrity/anchor-3/rubric.stdout.json" in page
    assert summary["identity"]["status"] == "passed"
    assert summary["provenance"]["matched"] == summary["provenance"]["cases"] == 16
    assert {
        "AC-01",
        "AC-02",
        "AC-03",
        "AC-04",
        "task-contract.clear-contract",
        "task-contract.discoverable-requirements",
        "task-contract.scoring-authority",
    } <= {row["id"] for row in summary["criterion_inventory"]}
    assert summary["schema"] == "rubric-showcase-v2"
    assert all("score" not in row for row in summary["criterion_inventory"])
    canonical = {}
    for name in ("artifact-configuration-provenance", "task-contract-authority"):
        document = json.loads((ROOT / f"skills/eval-author-rubric/references/{name}.json").read_text())
        canonical.update({row["id"]: row for row in document["items"]})
    for row in summary["criterion_inventory"]:
        assert row["title"] in page
        assert row["id"] in page
        if row["id"] in canonical:
            assert row["title"] == canonical[row["id"]]["title"]
            assert row["source"] == canonical[row["id"]]["source"]
    assert "<th>Task artifact access</th>" in page
    assert "<th>Discoverable grading requirements</th>" in page
    assert "identity/panel.json" in page
    assert "provenance/panel.json" in page
    assert summary["task_contract"]["matched"] == summary["task_contract"]["cases"] == 6
    assert summary["task_contract"]["human_adjudication"] is False
    assert "task-contract/report.md" in page
    assert "<table" in page


def showcase_module():
    import importlib.util

    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("showcase_under_test", ROOT / "tools/run_rubric_showcase.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", ["missing", "stale", "acceptance-contradiction", "acceptance-consistent-claim"])
@pytest.mark.parametrize("mutation", ["required-verification", "findings", "projection", "expected", "default"])
def test_partial_acceptance_requires_full_projection(showcase_output, tmp_path, case, mutation):
    import shutil

    output = tmp_path / "partial-acceptance"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    panel_path = output / "integrity/panel.json"
    panel = module.read(panel_path)
    row = next(row for row in panel["cases"] if row["id"] == case)
    path = output / "integrity" / row["evidence"]["report"]
    report = module.read(path)
    if mutation == "required-verification":
        del report["acceptance_integrity"]["required_verification"]
    elif mutation == "findings":
        report["acceptance_integrity"]["findings"].append({"code": "invented"})
    elif mutation == "projection":
        row["observed"]["score"] = 2
    elif mutation == "expected":
        row["expected"]["score"] = 2
    else:
        row["default_acceptance"]["report"] = {"valid": True}
        (output / "integrity" / row["evidence"]["default_report"]).write_text(json.dumps({"valid": True}))
    path.write_text(json.dumps(report))
    panel_path.write_text(json.dumps(panel))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises((ValueError, KeyError)):
        module.validate_reports(output)
    with pytest.raises((ValueError, KeyError)):
        module.verify_existing(output)


def test_validation_rejects_missing_integrity_group(showcase_output, tmp_path):
    import shutil

    output = tmp_path / "mutated"
    shutil.copytree(showcase_output, output)
    path = output / "integrity/anchor-3/rubric.stdout.json"
    report = json.loads(path.read_text())
    check = next(
        row
        for row in report["items"]["provenance.evidence-revision-consistency"]["check_results"]
        if row["id"] == "provenance.evidence-revision-consistency.deterministic-1"
    )
    check["group_scores"] = []
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="integrity"):
        showcase_module().validate_reports(output)


@pytest.mark.parametrize(
    "relative,change",
    [
        ("identity/panel.json", "count"),
        ("provenance/panel.json", "count"),
        ("scorer/rubric-reviewed.json", "score"),
        ("scorer/panel.json", "count"),
        ("task-contract/panel.json", "count"),
        ("integrity/anchor-3/rubric.stdout.json", "freshness"),
        ("integrity/missing/acceptance.stdout.json", "delete"),
        ("integrity/retry/retry.stdout.json", "delete"),
        ("fresh-replay/provenance/panel.json", "delete"),
        ("index.html", "delete"),
    ],
)
def test_verify_existing_rejects_tampering(showcase_output, tmp_path, relative, change):
    import shutil

    output = tmp_path / "tampered"
    shutil.copytree(showcase_output, output)
    path = output / relative
    if change == "delete":
        path.unlink()
    else:
        report = json.loads(path.read_text())
        if change == "count":
            if "counts" in report:
                report["counts"]["cases"] = 0
            elif "control_count" in report:
                report["control_count"] = 0
            else:
                report["case_count"] = 0
        elif change == "score":
            report["items"]["AC-02"]["score"] = 3
        else:
            report["measurements"]["native"]["required_verification"]["cases"][0]["freshness"] = "current"
        path.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_rubric_showcase.py"), "--verify-existing", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" not in result.stderr
    assert "one of the arguments" not in result.stderr


def test_verify_existing_is_read_only(showcase_output):
    from hashlib import sha256

    def hashes():
        return {
            p.relative_to(showcase_output).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in showcase_output.rglob("*")
            if p.is_file()
        }

    before = hashes()
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_rubric_showcase.py"), "--verify-existing", str(showcase_output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert hashes() == before


def test_verify_existing_rejects_resealed_summary(showcase_output, tmp_path):
    import shutil

    output = tmp_path / "resealed"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    summary = json.loads((output / "showcase.json").read_text())
    summary["scorer"]["reviewed"]["false_accepts"] = 9
    (output / "showcase.json").write_text(json.dumps(summary))
    (output / "index.html").write_text(module.render(summary, module.read(output / "scorer/panel.json")))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="summary"):
        module.verify_existing(output)


@pytest.mark.parametrize(
    "relative,field,value",
    [
        ("identity/panel.json", "counts", {"cases": 0}),
        ("provenance/panel.json", "counts", {"cases": 0}),
        ("scorer/panel.json", "control_count", 0),
        ("task-contract/panel.json", "case_count", 0),
    ],
)
def test_report_validation_does_not_trust_success_counts(showcase_output, tmp_path, relative, field, value):
    import shutil

    output = tmp_path / "mutated-count"
    shutil.copytree(showcase_output, output)
    path = output / relative
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        showcase_module().validate_reports(output)


def test_semantic_repeat_rejects_changed_case(showcase_output, tmp_path):
    import shutil

    output = tmp_path / "changed-case"
    shutil.copytree(showcase_output, output)
    path = output / "fresh-replay/identity/panel.json"
    report = json.loads(path.read_text())
    report["assessments"][0]["observed"]["items"]["provenance.task-artifact-access"]["score"] = 3
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="fresh-root semantic mismatch: identity"):
        showcase_module().compare_runs(output)


def test_showcase_surfaces_authority_and_ethos_without_new_scores(showcase_output):
    module = showcase_module()
    summary = module.read(showcase_output / "showcase.json")
    rows = {row["id"]: row for row in summary["identity"]["evidence_cases"]}
    assert len(rows) == summary["identity"]["counts"]["evidence_cases"] == 13
    for ident, statuses in (
        ("authority-resolved", ["resolved", "resolved"]),
        ("authority-broken-reference", ["broken", "resolved"]),
        ("authority-prose", ["prose", "resolved"]),
    ):
        assert [row["status"] for row in rows[ident]["observation"]["requirements"]] == statuses
    assert rows["ethos-valid"]["observation"]["valid"] is True
    assert rows["ethos-malformed"]["observation"]["valid"] is False
    page = (showcase_output / "index.html").read_text()
    for ident in rows:
        assert ident in page
    assert "authority.reference_broken" in page
    assert "authority.reference_prose" in page
    assert "Owner confirmation: NOT_RUN" in page
    assert len(summary["production_gaps"]) == 12
    for row in summary["task_contract"]["ethos"]:
        assert row["usable_authority"] is False
        assert row["owner_confirmation"]["status"] == "NOT_RUN"


@pytest.mark.parametrize("mutation", ["observation", "resealed-expectation", "diagnostics", "status", "exit-code"])
@pytest.mark.parametrize(
    "ident", ["authority-resolved", "authority-broken-reference", "authority-prose", "ethos-valid", "ethos-malformed"]
)
def test_identity_validation_rejects_resealed_evidence_outcomes(showcase_output, tmp_path, ident, mutation):
    import shutil

    output = tmp_path / "identity-outcome"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "identity/panel.json"
    panel = module.read(path)
    row = next(row for row in panel["evidence_cases"] if row["id"] == ident)
    measurement = row["expected"]["measurement"]
    observation = row["observed"]["measurements"][measurement]
    if mutation == "diagnostics":
        observation["diagnostics"] = [{"code": "invented"}]
    elif mutation == "status":
        observation["status"] = "invented"
    elif mutation == "exit-code":
        row["cli_exit_code"] = 99
    elif measurement == "authority":
        observation["requirements"][0]["status"] = "invented"
        if mutation == "resealed-expectation":
            row["expected"]["requirement_statuses"][0] = "invented"
    else:
        observation["valid"] = not observation["valid"]
        if mutation == "resealed-expectation":
            row["expected"]["valid"] = observation["valid"]
    path.write_text(json.dumps(panel))
    with pytest.raises(ValueError, match="identity"):
        module.validate_reports(output)
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="identity"):
        module.verify_existing(output)


@pytest.mark.parametrize("section,ident", [("assessments", "valid"), ("evidence_cases", "sourced-equal")])
@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "version",
        "criterion-order",
        "check-order",
        "source",
        "check-source",
        "expected-score",
        "expected-check",
    ],
)
def test_identity_canonical_reports_reject_coherent_resealing(showcase_output, tmp_path, section, ident, mutation):
    import shutil

    output = tmp_path / "identity-canonical"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    # Coherently alter both roots and every derived summary, so outer comparisons
    # cannot hide a missing check against the checked-in canonical definitions.
    for root in (output, output / "fresh-replay"):
        path = root / "identity/panel.json"
        panel = module.read(path)
        row = next(row for row in panel[section] if row["id"] == ident)
        report = row["observed"]
        item_id = "provenance.task-artifact-access"
        item = report["items"][item_id]
        if mutation == "schema":
            report["schema"] = "invented"
        elif mutation == "version":
            item["rubric_version"] = "invented"
        elif mutation == "criterion-order":
            report["items"] = dict(reversed(list(report["items"].items())))
        elif mutation == "check-order":
            item["check_results"].reverse()
        elif mutation == "source":
            item["source"]["id"] = "invented"
        elif mutation == "check-source":
            item["check_results"][0]["source_id"] = "invented"
        elif mutation == "expected-score":
            row["expected_items"][item_id]["score"] = item["score"] = 3
        else:
            check = item["check_results"][0]
            row["expected_checks"][check["id"]] = check["outcome"] = "PARTIAL"
            check["remaining_gap"] = "invented gap"
        path.write_text(json.dumps(panel))
    summary = module.summarize_reports(output)
    summary["determinism"] = module.compare_runs(output)
    (output / "showcase.json").write_text(json.dumps(summary))
    (output / "index.html").write_text(module.render(summary, module.read(output / "scorer/panel.json")))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="identity"):
        module.validate_reports(output)
    with pytest.raises(ValueError, match="identity"):
        module.verify_existing(output)


@pytest.mark.parametrize("mutation", ["counts", "differences", "native-attestation", "basis", "binding", "fields"])
def test_identity_sourced_metadata_is_validated(showcase_output, tmp_path, mutation):
    import shutil

    output = tmp_path / "identity-metadata"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "identity/panel.json"
    panel = module.read(path)
    row = next(row for row in panel["evidence_cases"] if row["id"] == "sourced-equal")
    observation = row["observed"]["measurements"]["sourced_config"]
    if mutation == "counts":
        observation["counts"]["run_bound"] = 0
    elif mutation == "differences":
        observation["differences"] = ["/agent"]
    elif mutation == "native-attestation":
        observation["native_attestation"] = True
    elif mutation == "fields":
        del observation["fields"]["/agent"]
    elif mutation == "basis":
        observation["fields"]["/agent"]["observed"]["basis"] = "native"
    else:
        observation["fields"]["/agent"]["observed"]["binding"]["status"] = "unbound"
    path.write_text(json.dumps(panel))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="identity"):
        module.validate_reports(output)
    with pytest.raises(ValueError, match="identity"):
        module.verify_existing(output)


@pytest.mark.parametrize("ident", ["authority-resolved", "ethos-valid"])
def test_fresh_comparison_retains_authority_and_ethos_diagnostics(showcase_output, tmp_path, ident):
    import shutil

    output = tmp_path / "changed-evidence"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "fresh-replay/identity/panel.json"
    panel = module.read(path)
    row = next(row for row in panel["evidence_cases"] if row["id"] == ident)
    row["observed"]["measurements"][row["expected"]["measurement"]]["diagnostics"] = [{"code": "changed"}]
    path.write_text(json.dumps(panel))
    with pytest.raises(ValueError, match="identity"):
        module.compare_runs(output)


@pytest.mark.parametrize("mutation", ["authority", "confirmation"])
def test_source_bound_validation_rejects_fabricated_ethos_confirmation(showcase_output, tmp_path, mutation):
    import shutil

    output = tmp_path / "false-owner"
    shutil.copytree(showcase_output, output)
    module = showcase_module()
    path = output / "task-contract/panel.json"
    panel = module.read(path)
    row = next(row for row in panel["cases"] if row["id"] == "ethos-valid")
    if mutation == "authority":
        row["usable_authority"] = True
    else:
        row["owner_confirmation"]["status"] = "confirmed"
    path.write_text(json.dumps(panel))
    with pytest.raises(ValueError, match="ethos"):
        module.validate_source_bound_scores(output)


def test_showcase_refuses_existing_output(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_rubric_showcase.py"), "--output", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert not (tmp_path / "index.html").exists()


def test_scoring_replay_resolves_trusted_temporary_root(tmp_path, monkeypatch):
    import contextlib

    module = showcase_module()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    source = tmp_path / "bundle/criterion-scoring"
    source.mkdir(parents=True)
    (source / "sentinel").write_text("retained")

    @contextlib.contextmanager
    def temporary_directory(**kwargs):
        yield str(alias)

    seen = []

    def verify(copied, *, verify):
        assert verify is True
        assert copied == real / "criterion-scoring"
        assert not any(path.is_symlink() for path in copied.parents)
        seen.append(copied)

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", temporary_directory)
    module.verify_private_copy(source, verify)
    assert seen
    assert (source / "sentinel").read_text() == "retained"


@pytest.mark.parametrize("failure", ["symlink", "existing", "partial"])
def test_output_failure_diagnostics_preserve_evidence(tmp_path, monkeypatch, capsys, failure):
    module = showcase_module()
    output = tmp_path / "output"
    if failure == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        output = alias / "output"
    elif failure == "existing":
        output.mkdir()
        (output / "sentinel").write_text("keep")
    else:

        def fail(directory):
            (directory / "sentinel").write_text("keep")
            raise ValueError("producer failed")

        monkeypatch.setattr(module, "collect", fail)
    with pytest.raises(SystemExit):
        module.main(["--output", str(output)])
    error = capsys.readouterr().err
    if failure == "symlink":
        assert str(tmp_path / "alias") in error
    else:
        assert "new output directory" in error
        assert str(output) in error
        assert (output / "sentinel").read_text() == "keep"


@pytest.mark.parametrize("mutation", ["expected-score", "expected-count", "fresh-equality"])
def test_integrity_rejects_coherent_expectation_tampering(tmp_path, mutation):
    module = showcase_module()
    sys.path.insert(0, str(ROOT / "tools"))
    from run_verification_integrity_demo import anchor

    integrity = tmp_path / "integrity"
    integrity.mkdir()
    cases = [anchor(integrity / f"anchor-{score}", passed, score) for score, passed in enumerate((0, 1, 3, 4))]
    cases.extend(
        {"id": ident} for ident in ("missing", "stale", "acceptance-contradiction", "acceptance-consistent-claim")
    )
    panel: dict = {"status": "passed", "cases": cases, "determinism": {"normalized_fresh_run_equal": True}}
    if mutation == "fresh-equality":
        cast(Any, panel["determinism"])["normalized_fresh_run_equal"] = "<img src=x onerror=alert(1)>"
    else:
        cases[0]["expected"]["score" if mutation == "expected-score" else "passed"] = 99
    (integrity / "panel.json").write_text(json.dumps(panel))
    # Only this prefix is needed: the independent expectation gate must reject
    # before reaching absent later panels, not fail on an unrelated file read.
    with pytest.raises(ValueError, match="integrity (anchor expectation|replay)"):
        module.validate_reports(tmp_path)


def test_renderer_escapes_all_untrusted_scalar_fields():
    import html

    module = showcase_module()
    payload = '<img src=x onerror="alert(1)">'
    ids = [
        "provenance.task-artifact-access",
        "provenance.grading-path",
        "provenance.grading-dependency-access",
        "task-contract.clear-contract",
        "task-contract.discoverable-requirements",
        "task-contract.scoring-authority",
    ]
    arm: dict = dict.fromkeys(
        (
            "score",
            "evidence_strength",
            "false_accepts",
            "invalid_denominator",
            "false_rejects",
            "valid_denominator",
            "health_errors",
            "alternatives_accepted",
            "alternatives_total",
        ),
        payload,
    )
    arm["remaining_gaps"] = [payload]
    summary = {
        "criterion_inventory": [
            {
                "id": ident,
                "title": payload,
                "area": payload,
                "demonstration_status": payload,
                "scoring_rule_status": payload,
            }
            for ident in ids
        ],
        "scorer": {
            "baseline": arm,
            "reviewed": arm,
            "matched_controls": payload,
            "attempted_controls": payload,
            "replay_equal": payload,
        },
        "integrity": {
            "fresh_replay_equal": payload,
            "anchors": [
                dict.fromkeys(
                    (
                        "id",
                        "expected",
                        "observed",
                        "passed",
                        "failed",
                        "unknown",
                        "excluded",
                        "canonical_score",
                        "evidence",
                    ),
                    payload,
                )
            ],
        },
        "task_contract": {"ethos": [], "assessments": [], "matched": payload, "cases": payload},
        "identity": {
            "counts": {"evidence_cases": payload},
            "assessments": [{"id": payload, "scores": dict.fromkeys(ids, payload)}],
            "evidence_cases": [
                {"id": payload, "measurement": "ethos", "observation": {"status": payload, "valid": payload}}
            ],
        },
        "provenance": {"matched": payload, "cases": payload},
        "receipt_authenticity": {},
        "production_gaps": [],
        "criterion_demonstration": {
            "criterion_count": payload,
            "vector_count_per_root": payload,
            "results": {
                name: dict.fromkeys(ids, payload) for name in ("score-0", "score-1", "score-2", "score-3", "missing")
            },
        },
        "determinism": {"fresh_run_equal": payload},
    }
    page = module.render(summary, {"cases": []})
    assert payload not in page
    assert html.escape(payload, quote=True) in page


def test_scorer_replay_rejects_coordinated_panel_tampering(tmp_path, capsys):
    module = showcase_module()
    import run_scorer_controls as owner

    output = tmp_path / "scorer"
    assert owner.main(["--output", str(output), "--rubric"]) == 0
    capsys.readouterr()
    path = output / "panel.json"
    panel = module.read(path)
    row = next(row for row in panel["cases"] if row["id"] == "no-work")
    row["observed"]["baseline"]["accepted"] = False
    row["expected"]["baseline"]["accepted"] = False
    path.write_text(json.dumps(panel))
    before = module.retained_hashes(tmp_path)
    with pytest.raises(ValueError, match="scorer replay"):
        module.validate_scorer_reports(output)
    assert module.retained_hashes(tmp_path) == before


@pytest.mark.parametrize("area", ["integrity", "task-contract"])
def test_linked_markdown_rejects_resealed_append(showcase_output, tmp_path, area):
    import shutil

    module = showcase_module()
    output = tmp_path / "linked"
    shutil.copytree(showcase_output, output)
    path = output / area / "report.md"
    path.write_text(path.read_text() + "\nInvented verified approval.\n")
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="Markdown"):
        module.validate_linked_reports(output)
    with pytest.raises(ValueError, match="Markdown"):
        module.verify_existing(output)


@pytest.mark.parametrize("capture", ["identity.stdout", "scorer.stdout", "integrity.stderr"])
def test_invocation_captures_are_consistency_checked(showcase_output, tmp_path, capture):
    import shutil

    module = showcase_module()
    output = tmp_path / "capture"
    shutil.copytree(showcase_output, output)
    (output / capture).write_text("invented invocation capture")
    with pytest.raises(ValueError, match="invocation capture"):
        module.validate_invocation_captures(output)


@pytest.mark.parametrize("mutation", ["expected", "fresh-equality", "scorer"])
def test_verify_rejects_fully_resealed_forgery(showcase_output, tmp_path, mutation):
    import shutil

    module = showcase_module()
    output = tmp_path / "forged"
    shutil.copytree(showcase_output, output)
    for root in (output, output / "fresh-replay"):
        name = "scorer" if mutation == "scorer" else "integrity"
        path = root / name / "panel.json"
        panel = module.read(path)
        if mutation == "expected":
            panel["cases"][0]["expected"]["score"] = '<img src=x onerror="alert(1)">'
        elif mutation == "fresh-equality":
            panel["determinism"]["normalized_fresh_run_equal"] = False
        else:
            row = next(row for row in panel["cases"] if row["id"] == "no-work")
            row["observed"]["baseline"]["accepted"] = row["expected"]["baseline"]["accepted"] = False
        path.write_text(json.dumps(panel))
        for capture in (f"{name}.stdout", "scorer-replay.stdout") if name == "scorer" else (f"{name}.stdout",):
            captured = module.read(root / capture)
            captured.update(panel)
            (root / capture).write_text(json.dumps(captured))
    summary = module.summarize_reports(output)
    summary["determinism"] = module.compare_runs(output)
    (output / "showcase.json").write_text(json.dumps(summary))
    (output / "index.html").write_text(module.render(summary, module.read(output / "scorer/panel.json")))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="integrity (anchor expectation|replay)|scorer replay"):
        module.verify_existing(output)


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_required", False),
        ("summary_required", False),
        ("condition_count", 99),
        ("expected_total", "999.99"),
        ("fixture_total", "999.99"),
        ("fixture_order_count", 99),
        ("summary_expected", {"currency": "EUR", "order_count": 99}),
        ("reward_rule", "invented"),
        (
            "evidence_strengths",
            dict.fromkeys(
                (
                    "task-contract.clear-contract",
                    "task-contract.discoverable-requirements",
                    "task-contract.scoring-authority",
                ),
                "E3",
            ),
        ),
    ],
)
def test_contract_inspection_rejects_coherent_resealing(showcase_output, tmp_path, field, value):
    import hashlib
    import shutil

    module = showcase_module()
    output = tmp_path / "contract-forgery"
    shutil.copytree(showcase_output, output)
    for root in (output, output / "fresh-replay"):
        path = root / "task-contract/panel.json"
        panel = module.read(path)
        row = next(row for row in panel["cases"] if row["id"] == "planted-summary")
        row["observed"][field] = row["expected"][field] = value
        path.write_text(json.dumps(panel))
        (root / "task-contract.stdout").write_text(json.dumps(panel))
        hashes_path = root / "task-contract/hashes.json"
        hashes = module.read(hashes_path)
        hashes["panel.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes_path.write_text(json.dumps(hashes))
    summary = module.summarize_reports(output)
    summary["determinism"] = module.compare_runs(output)
    (output / "showcase.json").write_text(json.dumps(summary))
    (output / "index.html").write_text(module.render(summary, module.read(output / "scorer/panel.json")))
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="task contract inspection"):
        module.verify_existing(output)


def test_showcase_labels_logs_as_unauthenticated_invocation_captures(showcase_output):
    page = (showcase_output / "index.html").read_text()
    assert "invocation captures, not authenticated execution evidence" in page
