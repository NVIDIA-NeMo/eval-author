# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
import sys
from pathlib import Path

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
    assert len(primary["task_contract"]["cases"]) == 7
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
        assert row["items"] == {key: report["items"][key] for key in ("CREATE-03", "CREATE-04", "CREATE-05-A")}
        assert row["items"]["CREATE-04"]["score"] == (0 if row["id"] == "planted-summary" else None)


@pytest.mark.parametrize(
    "relative,criterion",
    [
        ("scorer/rubric-baseline.json", "AC-03"),
        ("task-contract/planted-summary/measure-report.json", "CREATE-04"),
        ("task-contract/clean-neighbor/measure-report.json", "CREATE-04"),
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
    assert "Fixture execution" in page
    assert "Canonical rubric" in page
    assert "receipt_consistency" in page
    assert "scorer/rubric-reviewed.json" in page
    assert "integrity/anchor-3/rubric.stdout.json" in page
    assert summary["identity"]["status"] == "passed"
    assert summary["provenance"]["matched"] == summary["provenance"]["cases"] == 16
    assert {"AC-01", "AC-02", "AC-03", "AC-04", "CREATE-03", "CREATE-04", "CREATE-05-A"} <= {
        row["id"] for row in summary["criterion_inventory"]
    }
    assert all("score" not in row for row in summary["criterion_inventory"])
    assert "identity/panel.json" in page
    assert "provenance/panel.json" in page
    assert summary["task_contract"]["matched"] == summary["task_contract"]["cases"] == 7
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


def test_validation_rejects_missing_integrity_group(showcase_output, tmp_path):
    import shutil

    output = tmp_path / "mutated"
    shutil.copytree(showcase_output, output)
    path = output / "integrity/anchor-3/rubric.stdout.json"
    report = json.loads(path.read_text())
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
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
    report["assessments"][0]["observed"]["items"]["DISC-01-A"]["score"] = 3
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="fresh-root semantic mismatch: identity"):
        showcase_module().compare_runs(output)


def test_showcase_refuses_existing_output(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_rubric_showcase.py"), "--output", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert not (tmp_path / "index.html").exists()
