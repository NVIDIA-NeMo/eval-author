# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def showcase_output(tmp_path_factory, worker_id):
    # Build the read-only showcase once per session and share it across xdist workers;
    # tests that tamper with it copy it first.
    base = tmp_path_factory.getbasetemp()
    shared = (base if worker_id == "master" else base.parent) / "shared-showcase"
    output = shared / "output"
    with FileLock(str(shared) + ".lock"):
        if not (shared / "complete").is_file():
            shutil.rmtree(shared, ignore_errors=True)
            shared.mkdir()
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/run_rubric_showcase.py"), "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=1800,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            assert result.returncode == 0, result.stderr
            (shared / "complete").touch()
    return output


def test_showcase_includes_current_assessments_and_historical_rule_demo(showcase_output):
    output = showcase_output
    module = showcase_module()
    summary = module.read(output / "showcase.json")
    sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
    from rubric_leakage_current import canonical_definitions, level

    definitions = canonical_definitions()
    inventory = summary["criterion_inventory"]
    assert [row["id"] for row in inventory] == list(definitions)
    assert len(inventory) == 23
    assert sum(row["level"] == "task" for row in inventory) == 19
    assert sum(row["level"] == "suite" for row in inventory) == 4
    for row in inventory:
        assert row["title"] == definitions[row["id"]]["title"]
        assert row["level"] == level(definitions[row["id"]])
    assessments = summary["current_assessments"]
    import rubric_leakage_recipes as leakage
    import rubric_mutation_runner as runner

    source_cases = {path.stem for path in (runner.fixtures.EXAMPLE / "mutations").glob("*.json")}
    source_cases.update(runner.EXTRA_CASES)
    expected_reports = {
        f"source-controls/{case}/{arm}/report/assessment.json"
        for case in source_cases
        for arm in ("baseline", "changed", "repair")
    } | {f"current-leakage/{case}/assessment/ordinary/report/assessment.json" for case in (*leakage.CASES, "repair")}
    assert len(assessments) == len(expected_reports)
    assert {case["evidence"] for case in assessments} == expected_reports
    assert len(summary["current_numeric_criteria"]) == 13
    assert summary["pending_owner_requests"]["exact_request_count"] == 24
    numeric = set()
    from rubric_leakage_current import observed_score

    for case in assessments:
        numeric.update(ident for ident, row in case["report"]["items"].items() if observed_score(row) is not None)
    assert set(summary["current_numeric_criteria"]) == numeric | {"task-contract.discoverable-requirements"}
    assert {"AUDIT-05-B", "AUDIT-05-S03"} <= numeric
    assert "AUDIT-04-B-SUITE" not in numeric and "CREATE-02-SUITE" not in numeric
    assert not any(ident.startswith("AC-") for ident in summary["current_numeric_criteria"])
    for case in assessments:
        saved = module.read(output / case["evidence"])
        assert case["report"] == saved["report"]
        assert set(case["report"]["items"]) == set(definitions)
        for ident, row in case["report"]["items"].items():
            assert row["level"] == level(definitions[ident])
            if row["level"] == "suite":
                assert row["score"] is None and row["status"] == "NOT_APPLICABLE"
            if row["score"] is None:
                assert row["remaining_gaps"]
        if any(
            f"source-controls/{name}/changed/" in case["id"]
            for name in ("completion-only", "approval-after-effect", "ledger-omission")
        ):
            assert case["report"]["items"]["AUDIT-03"]["score"] == 0
            assert case["report"]["items"]["AUDIT-03"]["evidence_strength"] == "E1"
        if "source-controls/declared-scripted/changed/" in case["id"]:
            assert case["report"]["items"]["provenance.declared-configuration"]["score"] == 2
    demo = summary["criterion_demonstration"]
    assert demo["criterion_count"] == len(summary["historical_criterion_inventory"]) == 18
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
    assert len(summary["production_gaps"]) == 10
    page = (output / "index.html").read_text()
    assert "Historical synthetic scoring-rule coverage" in page
    assert "synthetic numbers do not qualify" in page
    assert "criterion-scoring/run-a/missing/measure-report.json" in page


def test_showcase_displays_anticheat_without_renaming_evidence(showcase_output):
    from html.parser import HTMLParser

    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    module = showcase_module()
    summary = module.read(showcase_output / "showcase.json")
    panel = module.read(showcase_output / "scorer/panel.json")
    before = json.dumps([summary, panel], sort_keys=True)
    page = module.render(summary, panel)
    parser = VisibleText()
    parser.feed(page)
    text = " ".join(parser.parts)
    for number in range(1, 5):
        assert f"Anticheat-0{number}" in text
        assert f"AC-0{number}" not in text
        assert f"AC-0{number}" in summary["criterion_demonstration"]["results"]["missing"]
    assert "scorer/rubric-baseline.json" in page
    assert json.dumps([summary, panel], sort_keys=True) == before


def test_showcase_repeats_every_area_in_fresh_root(showcase_output):
    output = showcase_output
    summary = json.loads((output / "showcase.json").read_text())
    repeat = output / "fresh-replay"
    primary = json.loads((output / "semantic-result.json").read_text())
    repeated = json.loads((repeat / "semantic-result.json").read_text())
    assert primary == repeated
    assert set(primary) == {
        "identity",
        "provenance",
        "task_contract",
        "scorer",
        "integrity",
        "criterion_demonstration",
        "current_assessments",
    }
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


# Pairwise, not the full product: every value and validator branch is covered once each (CI time).
@pytest.mark.parametrize(
    "relative,criterion,mutation",
    [
        ("scorer/rubric-baseline.json", "AC-03", "remove-row"),
        ("scorer/rubric-baseline.json", "AC-03", "alter-score"),
        (
            "task-contract/planted-summary/measure-report.json",
            "task-contract.discoverable-requirements",
            "remove-score",
        ),
        ("task-contract/clean-neighbor/measure-report.json", "task-contract.discoverable-requirements", "alter-score"),
    ],
)
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


# Pairwise, not the full product: every value and validator branch is covered once each (CI time).
@pytest.mark.parametrize(
    "source,mutation",
    [
        ("test.sh", "delete"),
        ("instruction.md", "change"),
        ("orders.csv", "delete"),
        ("Dockerfile", "change"),
        ("task.toml", "delete"),
        ("contract.json", "change"),
    ],
)
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
    assert "Current canonical criteria" in page
    assert "receipt_consistency" in page
    assert "scorer/rubric-reviewed.json" in page
    assert "integrity/anchor-3/rubric.stdout.json" in page
    assert summary["identity"]["status"] == "passed"
    assert summary["provenance"]["matched"] == summary["provenance"]["cases"] == 16
    assert {
        "AUDIT-03",
        "AUDIT-04-B",
        "TRACE-04-A",
        "ENV-03-S01",
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
        display_id = row["id"].replace("AC-", "Anticheat-") if row["id"].startswith("AC-") else row["id"]
        assert display_id in page
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


@pytest.mark.parametrize("invalid_root", [None, "primary", "fresh", "current-primary", "current-fresh"])
def test_main_validates_both_collections_once_before_success(tmp_path, monkeypatch, capsys, invalid_root):
    module = showcase_module()
    output = tmp_path / "output"
    fresh = output / "fresh-replay"
    invalid = {"primary": output, "fresh": fresh}.get(invalid_root)
    events = []

    def summarize(root: Path) -> dict:
        panel = module.read(root / "scorer/panel.json")
        return {key: panel[key] for key in ("scorer", "integrity")}

    def collect(root: Path) -> dict:
        events.append(("collect", root))
        (root / "scorer").mkdir()
        (root / "scorer/panel.json").write_text(
            json.dumps(
                {
                    "valid": root != invalid,
                    "scorer": {"replay_equal": True},
                    "integrity": {"fresh_replay_equal": True},
                }
            )
        )
        return summarize(root)

    def validate(root: Path) -> None:
        events.append(("validate", root))
        if not module.read(root / "scorer/panel.json")["valid"]:
            raise ValueError("injected invalid report")

    def validate_current(root: Path) -> None:
        events.append(("validate-current", root))
        if root == {"current-primary": output, "current-fresh": fresh}.get(invalid_root):
            raise ValueError("injected invalid report")

    # Keep main, compare_runs, hashes and verify_existing real; fake panel boundaries.
    monkeypatch.setattr(module, "collect", collect)
    monkeypatch.setattr(module, "validate_reports", validate)
    monkeypatch.setattr(module, "validate_current", validate_current)
    monkeypatch.setattr(module, "summarize_reports", summarize)
    monkeypatch.setattr(module, "semantic_projection", summarize)
    monkeypatch.setattr(module, "render", lambda summary, panel: "<html>showcase</html>")

    if invalid_root is None:
        assert module.main(["--output", str(output)]) == 0
        captured = capsys.readouterr()
        assert captured.out == f"{output / 'index.html'}\n"
        assert captured.err == ""
    else:
        with pytest.raises(SystemExit) as exc:
            module.main(["--output", str(output)])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "injected invalid report" in captured.err
        assert f"partial evidence retained at {output}" in captured.err

    expected = [("collect", output), ("collect", fresh), ("validate", output)]
    if invalid_root != "primary":
        expected.append(("validate", fresh))
    if invalid_root not in {"primary", "fresh"}:
        expected.append(("validate-current", output))
        if invalid_root != "current-primary":
            expected.append(("validate-current", fresh))
    assert events == expected
    for root in (output, fresh):
        assert (root / "scorer/panel.json").is_file()
        assert module.read(root / "semantic-result.json") == {
            "scorer": {"replay_equal": True},
            "integrity": {"fresh_replay_equal": True},
        }
    assert (output / "showcase-hashes.json").is_file()
    assert (output / "showcase.json").is_file()
    assert (output / "index.html").read_text() == "<html>showcase</html>"


# Pairwise, not the full product: every value and validator branch is covered once each (CI time).
@pytest.mark.parametrize(
    "case,mutation",
    [
        ("missing", "required-verification"),
        ("stale", "findings"),
        ("acceptance-contradiction", "projection"),
        ("acceptance-consistent-claim", "expected"),
        ("missing", "default"),
    ],
)
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
        timeout=600,
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
    assert len(rows) == summary["identity"]["counts"]["evidence_cases"] == 20
    retained = module.read(showcase_output / "identity/panel.json")
    authority = {row["id"]: row for row in rows.values() if row["measurement"] == "authority"}
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
    page = (showcase_output / "index.html").read_text()
    assert '<h3 id="authority">W03 authority observations</h3>' in page
    for case in retained["evidence_cases"]:
        if case["id"] not in authority:
            continue
        observation = case["observed"]["measurements"]["authority"]
        assert authority[case["id"]]["observation"] == observation
        for requirement in observation["requirements"]:
            assert requirement["reference"] in page
            if "question" in requirement:
                assert requirement["question"]["decision_needed"] in page
        for check in observation["checks"]:
            assert check["pointer"] in page
    assert "Check observations establish emission only" in page
    assert "Unresolved authority questions" in page
    assert "authority.check_unobserved" in page
    assert "authority.reference_implementation" in page
    assert 'href="scorer/inputs/contract.json"' in page
    assert 'href="scorer/rubric-reviewed.json"' in page
    assert "No result artifact selected" in page
    assert "does not establish authenticated owner approval" in page
    refund = authority["authority-refund"]["observation"]
    assert refund["counts"]["requirements"] == refund["counts"]["checks"] == 9
    assert all(row["status"] == "resolved" for row in refund["requirements"])
    assert all(row["status"] == "unknown" for row in refund["checks"])
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
    assert len(summary["production_gaps"]) == 10
    for row in summary["task_contract"]["ethos"]:
        assert row["usable_authority"] is False
        assert row["owner_confirmation"]["status"] == "NOT_RUN"


# Pairwise, not the full product: every value and validator branch is covered once each (CI time).
# observation and resealed-expectation branch on the measurement, so each runs for authority and ethos.
@pytest.mark.parametrize(
    "ident,mutation",
    [
        ("authority-resolved", "observation"),
        ("ethos-valid", "observation"),
        ("authority-broken-reference", "resealed-expectation"),
        ("ethos-malformed", "resealed-expectation"),
        ("authority-prose", "diagnostics"),
        ("ethos-valid", "status"),
        ("authority-resolved", "exit-code"),
        ("authority-check-unobserved", "check"),
        ("authority-prose", "question"),
    ],
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
    elif mutation == "check":
        observation["checks"][0]["status"] = "observed"
    elif mutation == "question":
        del observation["requirements"][0]["question"]
    elif measurement == "authority":
        observation["requirements"][0]["status"] = "invented"
        if mutation == "resealed-expectation":
            row["expected"]["requirement_statuses"][0] = "invented"
    else:
        observation["valid"] = not observation["valid"]
        if mutation == "resealed-expectation":
            row["expected"]["valid"] = observation["valid"]
    path.write_text(json.dumps(panel))
    if mutation in {"check", "question"}:
        (output / "identity.stdout").write_text(json.dumps(panel))
    with pytest.raises(ValueError, match="identity"):
        module.validate_reports(output)
    (output / "showcase-hashes.json").write_text(json.dumps(module.retained_hashes(output)))
    with pytest.raises(ValueError, match="identity"):
        module.verify_existing(output)


# Pairwise, not the full product: every value and validator branch is covered once each (CI time).
@pytest.mark.parametrize(
    "section,ident,mutation",
    [
        ("assessments", "valid", "schema"),
        ("evidence_cases", "sourced-equal", "version"),
        ("assessments", "valid", "criterion-order"),
        ("evidence_cases", "sourced-equal", "check-order"),
        ("assessments", "valid", "source"),
        ("evidence_cases", "sourced-equal", "check-source"),
        ("assessments", "valid", "expected-score"),
        ("evidence_cases", "sourced-equal", "expected-check"),
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


@pytest.fixture
def current_validation_bundle(tmp_path, monkeypatch, request):
    tmp_path = tmp_path / getattr(request, "param", "")
    module = showcase_module()
    current, table = module.current_modules()
    import rubric_leakage_recipes as leakage
    import rubric_mutation_runner as runner

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    report = {
        "items": {
            ident: {
                "score": None,
                "status": "UNASSESSED",
                "check_results": [{"check_id": check["id"], "outcome": "NOT_RUN"} for check in definition["checks"]],
            }
            for ident, definition in current.canonical_definitions().items()
        }
    }

    def assessment(root, relative):
        path = root / relative
        write(
            path,
            {
                "root": str(root),
                "report": report,
                "selection": {},
                "task": None,
                "definitions_sha256": current.definitions_digest(),
            },
        )
        (path.parent / "results.md").write_text("markdown")
        (path.parent / "results.html").write_text("html")
        return {"state": "assessed", "report": str(path), "exit": 1}

    source = []
    cases = sorted(
        {path.stem for path in (runner.fixtures.EXAMPLE / "mutations").glob("*.json")} | runner.EXTRA_CASES.keys()
    )
    for case in cases:
        records = {
            arm: assessment(tmp_path / "source-controls" / case / arm, "report/assessment.json")
            for arm in ("changed", "baseline", "repair")
        }
        catalog = runner.expectation(case)
        source.append(
            {
                "case": case,
                "assessment": records["changed"],
                "baseline_assessment": records["baseline"],
                "repair": {"assessment": records["repair"]},
                "expected": catalog.get(
                    "expected_scores", dict.fromkeys(catalog["target_criteria"], catalog["intended_score"])
                ),
                "observed": runner.observed_rows(records["changed"]),
            }
        )
    runs = [
        {
            "case": "leakage-" + case,
            "assessment": assessment(tmp_path / "current-leakage" / case, "assessment/ordinary/report/assessment.json"),
        }
        for case in leakage.CASES
    ]
    indexed = {row["case"]: row["assessment"] for row in runs}
    repairs = {
        "baseline": indexed["leakage-inventory"],
        "repair": assessment(tmp_path / "current-leakage/repair", "assessment/ordinary/report/assessment.json"),
    }
    for row in runs:
        case = row["case"].removeprefix("leakage-")
        ident = leakage.CONTROL_CASES[case][0] if case in leakage.CONTROL_CASES else None
        row["criterion_baseline"] = indexed[f"leakage-{ident}.base"] if ident else repairs["baseline"]
        row["criterion_repair"] = indexed[f"leakage-{ident}.repair"] if ident else repairs["repair"]
    monkeypatch.setattr(runner, "validate_coverage", lambda value: None)
    monkeypatch.setattr(runner, "verify_case_input", lambda root, case: None)
    monkeypatch.setattr(leakage, "verify_input", lambda root, case, selection: None)
    monkeypatch.setattr(leakage, "verify_observation", lambda report, case: None)
    monkeypatch.setattr(table, "assess_table", lambda root, selection, task: report)
    monkeypatch.setattr(table, "render", lambda report, title: "markdown")
    monkeypatch.setattr(table, "render_html", lambda markdown: "html")
    pending = leakage.pending_requests(runs)
    result = {
        "runs": runs,
        "source_repairs": repairs,
        "pending_requests": pending,
        "verification": "ordinary-replay-verified",
    }
    write(tmp_path / "source-controls/runs.json", source)
    write(tmp_path / "source-controls/coverage.json", runner.reconcile(source))
    write(tmp_path / "current-leakage/leakage-results.json", result)
    write(tmp_path / "current-leakage/owner-requests.json", pending)
    return module, tmp_path


@pytest.mark.parametrize(
    "current_validation_bundle",
    ["source-controls/demo", "current-leakage/demo", "source-controls/current-leakage/demo"],
    indirect=True,
)
def test_current_validation_accepts_producer_names_in_ancestors(current_validation_bundle):
    module, output = current_validation_bundle
    module.validate_current(output)


@pytest.mark.parametrize("producer", ["source-controls", "current-leakage"])
@pytest.mark.parametrize("mutation", ["outside-root", "parent-traversal"])
def test_current_validation_confines_producer_reports(current_validation_bundle, producer, mutation):
    module, output = current_validation_bundle
    path = output / producer / ("runs.json" if producer == "source-controls" else "leakage-results.json")
    value = module.read(path)
    runs = value if producer == "source-controls" else value["runs"]
    record = runs[0]["assessment"]
    relative = Path(record["report"]).relative_to(output)
    root = output.parent / (output.name + "-outside") if mutation == "outside-root" else output / "child/.."
    record["report"] = str(root / relative)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        module.validate_current(output)


@pytest.mark.parametrize("producer", ["source-controls", "current-leakage"])
@pytest.mark.parametrize(
    "mutation", ["empty", "omitted", "duplicate", "duplicate-report", "missing-baseline", "wrong-repair"]
)
def test_current_validation_requires_complete_producer_inventory(current_validation_bundle, producer, mutation):
    module, output = current_validation_bundle
    module.validate_current(output)
    path = output / producer / ("runs.json" if producer == "source-controls" else "leakage-results.json")
    value = module.read(path)
    runs = value if producer == "source-controls" else value["runs"]
    if mutation == "empty":
        runs.clear()
        if producer == "current-leakage":
            value["source_repairs"].clear()
    elif mutation == "omitted":
        runs.pop()
    elif mutation == "duplicate":
        runs[-1] = runs[0]
    elif mutation == "duplicate-report":
        runs[-1]["assessment"] = runs[0]["assessment"]
    elif mutation == "missing-baseline":
        del runs[0]["baseline_assessment" if producer == "source-controls" else "criterion_baseline"]
    elif producer == "source-controls":
        runs[0]["repair"]["assessment"] = runs[0]["baseline_assessment"]
    else:
        runs[0]["criterion_repair"] = runs[0]["criterion_baseline"]
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, KeyError)):
        module.validate_current(output)


@pytest.mark.parametrize("mutation", ["omitted-attempt", "duplicate-attempt", "omitted-target"])
def test_current_validation_reconciles_coverage_with_runs(current_validation_bundle, mutation):
    module, output = current_validation_bundle
    path = output / "source-controls/coverage.json"
    coverage = module.read(path)
    cell = next(row for row in coverage["cells"] if row["attempts"])
    if mutation == "omitted-attempt":
        cell["attempts"].pop()
    elif mutation == "duplicate-attempt":
        cell["attempts"].append(cell["attempts"][0])
    else:
        path = output / "source-controls/runs.json"
        runs = module.read(path)
        runs[0]["expected"] = {}
        path.write_text(json.dumps(runs))
    if mutation != "omitted-target":
        path.write_text(json.dumps(coverage))
    with pytest.raises(ValueError, match="coverage|expectation"):
        module.validate_current(output)


@pytest.mark.parametrize(
    "mutation", ["missing-repair", "duplicate-repair", "wrong-control-base", "wrong-control-repair", "wrong-root"]
)
def test_current_validation_checks_repair_and_root_bindings(current_validation_bundle, mutation):
    module, output = current_validation_bundle
    path = output / "current-leakage/leakage-results.json"
    result = module.read(path)
    if mutation == "missing-repair":
        del result["source_repairs"]["repair"]
    elif mutation == "duplicate-repair":
        result["source_repairs"]["repair"] = result["source_repairs"]["baseline"]
    elif mutation.startswith("wrong-control"):
        row = next(row for row in result["runs"] if row["case"] == "leakage-AUDIT-03.0")
        field = "criterion_baseline" if mutation == "wrong-control-base" else "criterion_repair"
        row[field] = result["source_repairs"]["baseline"]
    else:
        assessment = Path(result["source_repairs"]["repair"]["report"])
        saved = module.read(assessment)
        saved["root"] = str(output / "current-leakage/inventory")
        assessment.write_text(json.dumps(saved))
    path.write_text(json.dumps(result))
    with pytest.raises((ValueError, KeyError)):
        module.validate_current(output)


def test_current_semantic_comparison_omits_only_path_bound_requests():
    module = showcase_module()
    from copy import deepcopy

    report: dict[str, Any] = {
        "measurements": {
            "scoped_reviews": {
                "contract": {
                    "request": {
                        "binding": "request-a",
                        "crosswalk": {"intake_binding": "intake-a"},
                        "question": "Keep this decision",
                    }
                }
            }
        },
        "items": {
            "task-contract.scoring-authority": {
                "score": 0,
                "evidence_refs": [{"binding": "request-a", "measurement": "contract"}],
                "check_results": [{"outcome": "FAIL", "evidence_refs": [{"binding": "request-a"}]}],
            }
        },
    }
    other = deepcopy(report)
    request = other["measurements"]["scoped_reviews"]["contract"]["request"]
    request["binding"] = "request-b"
    request["crosswalk"]["intake_binding"] = "intake-b"
    row = other["items"]["task-contract.scoring-authority"]
    row["evidence_refs"][0]["binding"] = "request-b"
    row["check_results"][0]["evidence_refs"][0]["binding"] = "request-b"
    before = deepcopy(report)
    assert module.current_semantics(report) == module.current_semantics(other)
    assert report == before
    request["question"] = "Changed decision"
    assert module.current_semantics(report) != module.current_semantics(other)


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

    def test_payload_row():
        return {
            "score": None,
            "status": payload,
            "remaining_gaps": [payload],
            "level": "task",
            "findings": [payload],
            "check_results": [{"method": "human_review", "outcome": "NOT_RUN", "next_action": payload}],
        }

    summary["historical_criterion_inventory"] = summary["criterion_inventory"]
    summary["criterion_inventory"] = [{"id": ident, "title": payload, "level": "task"} for ident in ids]
    summary["current_assessments"] = [
        {"id": payload, "evidence": payload, "report": {"items": {ident: test_payload_row() for ident in ids}}}
    ]
    summary["current_numeric_criteria"] = []
    summary["pending_owner_requests"] = {"exact_request_count": payload}
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
