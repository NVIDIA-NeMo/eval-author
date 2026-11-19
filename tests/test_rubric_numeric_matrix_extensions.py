# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"))


def test_extension_expectation_not_selected_score():
    import rubric_mutation_runner as runner
    from rubric_numeric_matrix_extensions import extension_specs

    assert sum(len(v) for v in extension_specs("native").values()) == 7
    # Recipe-matched semantic cells were removed; that kind is no longer an accepted extension.
    with pytest.raises(ValueError):
        extension_specs("semantic")
    with pytest.raises(ValueError):
        runner.validate_matrix_expectation(
            {"extension_kind": "semantic", "case": "advantaged-comparison", "expected": {"x": 3}}
        )


def test_current_ordinary_pairs_reach_matrix_and_extension_without_replay(tmp_path, monkeypatch):
    from copy import deepcopy

    import rubric_leakage_recipes as recipes
    import rubric_mutation_runner as runner
    import rubric_numeric_matrix_extensions as extension

    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", raising=False)
    monkeypatch.setattr(runner, "matrix_replay", lambda *a, **k: pytest.fail("generation must defer replay"))
    base_root = tmp_path / "full-base"
    base = runner.matrix_assess(base_root, recipes.materialize(base_root, "inventory"), base_root / "assessment", {})
    ident = "AUDIT-03"
    runs: list[Any] = []
    for arm in ("base", "0", "missing", "operational", "repair"):
        case = ident + "." + arm
        root = tmp_path / case
        record = runner.matrix_assess(root, recipes.materialize(root, case), root / "assessment", {})
        assert record["state"] == "assessed"
        runs.append(
            {
                "case": "leakage-" + case,
                "expected": recipes.expectations(case),
                "assessment": record,
                "extension_kind": "leakage",
                "evidence_control_kind": recipes.evidence_kind(case),
                "reason": "test observation",
                "repair_description": "fresh paired repair",
            }
        )
    for run in runs:
        run.update(criterion_baseline=runs[0]["assessment"], criterion_repair=runs[-1]["assessment"])
    result = runner.matrix_summary(base, runs, base)
    assert result["base_scores"][ident] is None
    defect = next(r for r in result["rows"] if r["case"] == "leakage-" + ident + ".0")
    assert (defect["base"], defect["actual"], defect["repair"]) == (1, 0, 1)
    assert defect["numeric_drop"] is True
    missing = next(c for c in result["missing_evidence_cells"] if c["criterion"] == ident)
    assert missing["demonstrated_drop"] is True
    assert [r["case"] for r in missing["attempts"]] == ["leakage-" + ident + ".missing"]
    fault = result["operational_failure_controls"][0]
    assert fault["actual"] is None and fault["matched"] is False and fault["numeric_drop"] is False
    assert all(fault["case"] != a["case"] for c in result["cells"] for a in c["attempts"])
    assert len(result["cells"]) == 84 and len(result["missing_evidence_cells"]) == 21
    assert "METHOD-J1" not in result["base_scores"]
    extension.enrich(result)
    enriched = next(r for r in result["rows"] if r["case"] == defect["case"])
    assert enriched["repair_report"] == runs[-1]["assessment"]["report"]
    assert enriched["repair_reports"]["criterion_paired"] == enriched["repair_report"]
    assert enriched["repair_reports"]["full_base"] == base["report"]
    assert result["runs"][1]["full_emitted_items"][ident]["score"] == 0
    projection = json.loads(Path(runs[1]["assessment"]["report"]).read_text())["report"]["measurements"][
        "leakage_bounded"
    ]
    probe = projection["observations"]["probes"]["no-work"]
    assert probe["actual"]["max_credit"] == 1
    assert probe["intended"]["max_credit"] != probe["actual"]["max_credit"]
    result.update(options={}, verification={"all_replayed": False, "state": "deferred"})
    text = extension.render(result, tmp_path)
    assert "operational_failure" in text
    assert "AUDIT-03.repair/assessment/ordinary/report/results.md" in text
    forged = deepcopy(runs)
    forged[1]["criterion_repair"] = base
    with pytest.raises(ValueError, match="criterion pair"):
        runner.matrix_summary(base, forged, base)
    forged = deepcopy(runs)
    forged[3]["evidence_control_kind"] = "missing_evidence"
    with pytest.raises(ValueError, match="control kind"):
        runner.matrix_summary(base, forged, base)


def test_leakage_cli_defers_replay_and_batches_exact_requests(tmp_path, monkeypatch):
    import rubric_leakage_recipes as recipes
    import rubric_mutation_runner as runner

    monkeypatch.setattr(runner, "matrix_replay", lambda *a, **k: pytest.fail("unexpected replay"))
    # Use the real collector and ordinary CLI for base/repair duplicate bindings.
    monkeypatch.setattr(recipes, "CASES", ("inventory", "AUDIT-03.base", "AUDIT-03.repair", "AUDIT-03.operational"))
    assert recipes.main(["--output", str(tmp_path / "batch"), "--defer-replay"]) == 1
    saved = json.loads((tmp_path / "batch/leakage-results.json").read_text())
    requests = json.loads((tmp_path / "batch/owner-requests.json").read_text())
    assert saved["verification"] == "deferred"
    assert len(requests["owner_questions"]) == 7
    assert requests["exact_request_count"] == 1
    entry = requests["owner_questions"]["AUDIT-03"][0]
    assert entry["cases"] == ["leakage-AUDIT-03.base", "leakage-AUDIT-03.repair"]
    assert entry["request"]["observations"] is not None
    assert "decisions" not in entry["request"]
    assert not list((tmp_path / "batch").rglob("replay.command.json"))


def test_check_id_only_report_normalization(tmp_path):
    import rubric_mutation_runner as runner

    items = {
        ident: {
            "score": None,
            "check_results": [{"check_id": c["id"], "outcome": "NOT_RUN"} for c in definition["checks"]],
        }
        for ident, definition in runner.canonical_definitions().items()
    }
    path = tmp_path / "synthetic-report.json"
    path.write_text(json.dumps({"report": {"items": items}}))
    rows = runner.observed_rows({"state": "assessed", "report": str(path)})
    assert len(rows) == 21 and sum(len(r["checks"]) for r in rows.values()) == 70


@pytest.mark.parametrize("deferred", [False, True])
def test_matrix_cli_forwards_replay_policy(tmp_path, monkeypatch, deferred):
    import rubric_mutation_runner as runner

    called = []

    def generate(source, output, options, *, defer_replay=False):
        called.append(defer_replay)
        return {"counts": {}, "verification": {"all_replayed": not defer_replay}}

    monkeypatch.setattr(runner, "run_matrix", generate)
    args = ["--base-report", str(tmp_path / "report.json"), "--output", str(tmp_path / "matrix")]
    assert runner.main(args + (["--defer-replay"] if deferred else [])) == 0
    assert called == [deferred]


def test_leakage_default_replays_distinct_reports(tmp_path, monkeypatch):
    import rubric_leakage_recipes as recipes
    import rubric_mutation_runner as runner

    monkeypatch.setattr(recipes, "CASES", ("inventory",))
    replayed = []
    monkeypatch.setattr(runner, "matrix_replay", lambda record: replayed.append(record["report"]) or True)
    assert recipes.main(["--output", str(tmp_path / "default")]) == 0
    assert len(replayed) == len(set(replayed)) == 2
    saved = json.loads((tmp_path / "default/leakage-results.json").read_text())
    assert saved["verification"] == "ordinary-replay-verified"
