# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/run_task_contract_demo.py"
CRITERIA = ["CREATE-03", "CREATE-04", "CREATE-05-A"]


def run_demo(output):
    return subprocess.run(
        [sys.executable, str(CLI), "--output", str(output)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
    )


def test_cli_retains_bounded_source_scores_separate_from_static_crosswalk(tmp_path):
    output = tmp_path / "demo"
    result = run_demo(output)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary == json.loads((output / "panel.json").read_text())
    assert summary["schema"] == "task-contract-demo-v1"
    assert summary["native_execution"] is False
    assert summary["model_execution"] is False
    assert summary["human_adjudication"] is False
    cases = {row["id"]: row for row in summary["cases"] if row["kind"] == "task_contract"}
    assert set(cases) == {"planted-summary", "clean-neighbor", "agent-only-review-pending"}
    assert cases["planted-summary"]["observed"]["summary_required"] is True
    assert cases["clean-neighbor"]["observed"]["summary_required"] is False
    assert all(row["matched"] for row in cases.values())
    assert cases["agent-only-review-pending"]["assessment_state"] == "human-review-pending"
    for case in cases.values():
        scores = dict.fromkeys(CRITERIA)
        strengths = dict.fromkeys(CRITERIA)
        if case["id"] == "planted-summary":
            scores["CREATE-04"] = 0
            strengths["CREATE-04"] = "E1"
        assert case["rubric_scores"] == scores
        assert case["emitted_criterion_ids"] == CRITERIA
        assert case["reviewer_kind"] == "agent"
        assert case["measurement_kind"] == "emitted-bounded-source-inspection"
        report = json.loads((output / case["report"]).read_text())
        emitted = {criterion: report["items"][criterion]["score"] for criterion in CRITERIA}
        assert case["observed"]["rubric_scores"] == case["expected"]["rubric_scores"] == emitted == scores
        assert case["observed"]["evidence_strengths"] == case["expected"]["evidence_strengths"] == strengths
        assert case["measure_exit_code"] == 1  # Null criteria are not a successful full assessment.
        selection = json.loads((output / case["id"] / "selection.json").read_text())
        manifest_id = selection["contract_controls"]["artifact_id"]
        manifest_record = next(row for row in selection["artifacts"] if row["id"] == manifest_id)
        manifest_path = output / case["id"] / manifest_record["path"]
        manifest = json.loads(manifest_path.read_text())
        assert manifest["schema"] == "rubric-contract-controls-v1"
        assert set(manifest) == {
            "schema",
            "instruction_id",
            "dockerfile_id",
            "scorer_id",
            "test_data_id",
            "task_config_id",
        }
        assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == manifest_record["digest"]
        for criterion in CRITERIA:
            row = report["items"][criterion]
            assert row["evidence_strength"] == strengths[criterion]
            assert row["status"] == ("ASSESSED" if scores[criterion] is not None else "NOT_ASSESSED")
            assert row["assessment_scope"] == "bounded-source-inspection"
        assert "no score is emitted" not in case["next_action"]
        crosswalk = json.loads((output / case["crosswalk"]).read_text())
        assert crosswalk["not_emitted_rubric_score"] is True
        assert len(crosswalk["conditions"]) == case["observed"]["condition_count"]
        for condition in crosswalk["conditions"]:
            for evidence in condition["evidence"]:
                data = (output / evidence["path"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == evidence["digest"]
                assert evidence["quote"] == "\n".join(
                    data.decode().splitlines()[evidence["start_line"] - 1 : evidence["end_line"]]
                )
    planted = json.loads((output / cases["planted-summary"]["crosswalk"]).read_text())
    assert any(row["id"] == "summary-value" and row["status"] == "unsupported" for row in planted["conditions"])
    assert (output / "report.md").is_file()
    repeated = run_demo(output)
    assert repeated.returncode == 2
    assert json.loads((output / "panel.json").read_text()) == summary
    hashes = json.loads((output / "hashes.json").read_text())
    for path, digest in hashes.items():
        assert hashlib.sha256((output / path).read_bytes()).hexdigest() == digest


def test_ethos_variants_consume_real_measure_reports_and_bound_inputs(tmp_path):
    output = tmp_path / "ethos-demo"
    result = run_demo(output)
    assert result.returncode == 0, result.stderr
    panel = json.loads(result.stdout)
    cases = {row["id"]: row for row in panel["cases"] if row["kind"] == "ethos"}
    expected = {
        "ethos-valid": ("pass", True, []),
        "ethos-invalid": ("fail", False, ["ethos.invalid"]),
        "ethos-stale": ("fail", True, ["ethos.digest_mismatch"]),
        "ethos-missing-validator": ("unknown", None, ["ethos.validator_unavailable"]),
    }
    assert set(cases) == set(expected)
    assert panel["case_count"] == panel["matched_count"] == 7
    for ident, (status, valid, codes) in expected.items():
        case = cases[ident]
        report = json.loads((output / case["report"]).read_text())
        observed = report["measurements"]["ethos"]
        assert case["observed"] == observed
        assert (observed["status"], observed["valid"]) == (status, valid)
        assert [row["code"] for row in observed["diagnostics"]] == codes
        assert case["matched"] is True
        assert case["usable_authority"] is (status == "pass")
        assert observed["digest"] == hashlib.sha256((output / ident / "ETHOS.md").read_bytes()).hexdigest()
        if ident != "ethos-missing-validator":
            assert (
                observed["validator_digest"]
                == hashlib.sha256(
                    (ROOT / "team-skills/ethos/ethos-write/scripts/validate_ethos.py").read_bytes()
                ).hexdigest()
            )
        assert case["rubric_scores"] == dict.fromkeys(CRITERIA)
        assert case["emitted_criterion_ids"] == []
    assert cases["ethos-invalid"]["observed"]["error"] == "missing section: ## Constraints"
    assert cases["ethos-stale"]["selected_digest"] != cases["ethos-stale"]["observed"]["digest"]


def test_structural_inspection_reads_summary_values_instead_of_echoing_expectations():
    demo = runpy.run_path(str(CLI))
    task = ROOT / "skills/eval-author-audit/evals/files/tasks/order-total"
    scorer = (
        (task / "tests/test.sh").read_text().replace("'USD'", "'EUR'").replace("'order_count': 3", "'order_count': 9")
    )
    observed = demo["inspect"](scorer, (task / "environment/orders.csv").read_text())
    assert observed["summary_expected"] == {"currency": "EUR", "order_count": 9}


def test_markdown_distinguishes_emitted_scores_from_authored_crosswalk(tmp_path):
    output = tmp_path / "markdown"
    result = run_demo(output)
    assert result.returncode == 0, result.stderr
    panel = json.loads(result.stdout)
    assert "frozen supported source shapes only" in panel["scope"]
    markdown = (output / "report.md").read_text()
    assert "Static crosswalks are authored, not emitted rubric scores" in markdown
    assert "CREATE-04=0/E1" in markdown
    assert "CREATE-03=null; CREATE-04=null; CREATE-05-A=null" in markdown
    assert "expected-vs-observed match=True" in markdown
    assert "not human-approved" in markdown
    assert "or emitted task-contract rubric score" not in markdown
    assert "No human adjudication, model, native or container execution" in markdown


@pytest.mark.parametrize("source", ["Dockerfile", "task.toml"])
def test_resealed_source_mutation_is_invalid_not_a_false_score(tmp_path, source, capsys):
    demo = runpy.run_path(str(CLI))
    original_contract_case = demo["contract_case"]

    def mutated_case(output, ident, scorer, expected, sources):
        changed_sources = {**sources, source: sources[source] + b"\n# unsupported source mutation\n"}
        return original_contract_case(output, ident, scorer, expected, changed_sources)

    # Mutate before artifact selection so the real CLI receives freshly bound,
    # unsupported bytes, not merely a stale digest. Do not mock its assessment.
    demo["collect"].__globals__["contract_case"] = mutated_case
    output = tmp_path / "mutated"
    assert demo["main"](["--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"
    retained = json.loads((output / "planted-summary/measure.stdout.json").read_text())
    assert retained["status"] == "invalid"
    assert not any(row.get("score") is not None for row in retained.get("items", {}).values())
    assert not (output / "panel.json").exists()


def test_authored_citation_rejects_changed_source(tmp_path):
    demo = runpy.run_path(str(CLI))
    (tmp_path / "source").write_text("changed\n")
    with pytest.raises(ValueError, match="evidence span"):
        demo["cite"](tmp_path, "source", "original")
