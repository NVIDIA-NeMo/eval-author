# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))


def companion():
    return importlib.import_module("rubric_audit_companion")


def test_explicit_audit_selection_contract():
    import pytest

    module = companion()
    assert module.selection_mode({"mode": "inspect"}) == "inspect"
    assert module.selection_mode({"mode": "exercise", "path": "private/audit"}) == "exercise"
    for value in (
        None,
        {},
        {"mode": True},
        {"mode": "inspect", "score": 3},
        {"mode": "exercise"},
        {"mode": "exercise", "path": "../audit"},
    ):
        with pytest.raises(ValueError):
            module.selection_mode(value)


def test_typed_diff_preserves_boolean_number_and_missing_null():
    module = companion()
    actual = module.typed_diff({"budget": True, "absent": None}, {"budget": 1, "new": None})
    assert [(row["pointer"], row["kind"]) for row in actual] == [
        ("/absent", "removed"),
        ("/budget", "type_changed"),
        ("/new", "added"),
    ]
    assert actual[1]["left"] is True
    assert type(actual[1]["right"]) is int
    assert module.typed_diff({"a/b~": [1, 2]}, {"a/b~": [1, 3]})[0]["pointer"] == "/a~1b~0/1"
    assert module.typed_diff({"b": 1, "a": 2}, {"a": 2, "b": 1}) == []


def test_projection_preserves_current_failure():
    module = companion()
    rows = {
        key: {
            "score": 0,
            "check_results": [{"id": "x", "outcome": "FAIL"}],
            "evidence_refs": [],
            "findings": ["confirmed"],
        }
        for key in (module.REVISION, module.FAIRNESS)
    }
    report = {"items": rows, "measurements": {}}
    audit = {"revision_score": 3}
    projected = module.project_report(report, audit)
    for key in rows:
        assert projected["items"][key]["score"] == 0
        assert projected["items"][key]["findings"] == ["confirmed"]
        assert projected["items"][key]["check_results"] == rows[key]["check_results"]


def test_current_native_manifest_and_scripted_diff():
    import json
    import os

    import pytest

    configured = os.environ.get("EVAL_AUTHOR_AUDIT_REPORT")
    if not configured:
        pytest.skip("requires current admitted combined report")
    saved = json.loads(Path(configured).read_text())
    result = companion().inspect(Path(saved["root"]), saved["selection"])
    assert result["counts"] == {"panels": 3, "results": 34, "infrastructure_errors": 1}
    assert len(result["result_revisions"]) == 34
    assert len({row["id"] for row in result["result_revisions"]}) == 34
    assert all(row["disposition"] == "current" for row in result["result_revisions"])
    assert result["comparison"]["model_study"] is False
    assert result["comparison"]["fairness_score"] is None
    assert [row["pointer"] for row in result["comparison"]["setup_differences"]] == [
        "/agent/kwargs/case",
        "/trial_name",
    ]
    assert result["comparison"]["owner_question"]["difference_refs"] == ["/agent/kwargs/case"]
    assert result["comparison"]["arms"][0]["checks"] == result["comparison"]["arms"][1]["checks"]
    assert result["checks"]["provenance.comparison-fairness.deterministic"] == "PASS"
    assert result["checks"]["provenance.comparison-fairness.human-review"] == "NOT_RUN"
    assert result["checks"]["provenance.evidence-revision-consistency.deterministic-1"] == "PASS"
    assert result["revision_score"] == 2


def test_one_skill_audit_exercise_and_coherent_tampering(tmp_path, capsys):
    import json
    import os

    import pytest

    configured = os.environ.get("EVAL_AUTHOR_AUDIT_REPORT")
    if not configured:
        pytest.skip("requires current admitted combined report")
    saved = json.loads(Path(configured).read_text())
    module = companion()
    base = importlib.import_module("rubric_base_assessment")
    table = importlib.import_module("rubric_table")
    selected = saved["selection"]
    task = next(
        row["path"] for row in selected["artifacts"] if row["id"] == selected["example_assessment"]["task_config_id"]
    )
    bundles = [
        str(Path(row["path"]).parent)
        for row in selected["artifacts"]
        if row["id"] in selected["example_assessment"]["native_bundle_ids"]
    ]
    destination = tmp_path / "integrated"
    args = ["--root", saved["root"], "--task", task, "--output", str(destination), "--approve-audit-exercise"]
    for bundle in bundles:
        args.extend(["--bundle", bundle])
    assert base.main(args) == 1
    capsys.readouterr()
    report_path = destination / "report"
    current = json.loads((report_path / "assessment.json").read_text())
    from rubric_leakage_current import canonical_definitions
    assert set(current["report"]["items"]) == set(canonical_definitions())
    assert len(current["report"]["items"]) == 21
    assert sum(len(row["check_results"]) for row in current["report"]["items"].values()) == 70
    assert set(current["report"]["legacy_evidence_items"]) == {"AC-01", "AC-02", "AC-03", "AC-04"}
    assert "METHOD-J1" in current["report"]["undefined_criteria"]
    assert current["report"]["items"][module.REVISION]["score"] == 3
    assert current["report"]["items"][module.FAIRNESS]["score"] is None
    without_audit = {key: value for key, value in current["selection"].items() if key != "audit_companion"}
    baseline = table.assess_table(Path(saved["root"]), without_audit, None)
    for ident, row in baseline["items"].items():
        if ident not in (module.REVISION, module.FAIRNESS):
            assert current["report"]["items"][ident] == row
    for name, measurement in baseline["measurements"].items():
        assert current["report"]["measurements"][name] == measurement
    assert table.main(["--root", saved["root"], "--verify-existing", str(report_path)]) == 1
    assert capsys.readouterr().out == (report_path / "results.md").read_text()
    assert (report_path / "results.md").read_text().count('href="audit.json"') == 1
    assert len((report_path / "results.md").read_bytes()) <= 1048576
    audit_path = destination / "audit" / "audit.json"
    audit = json.loads(audit_path.read_text())
    audit["invalidation_tests"][0]["invalidated_results"] = 19
    audit_path.write_text(json.dumps(audit, sort_keys=True))
    (report_path / "audit.json").write_text(json.dumps(audit, sort_keys=True))
    # Reseal all report projections so rejection must come from the owning replay.
    old = current["report"]["measurements"]["audit_companion"]["inventory_ref"]["digest"]
    current = json.loads(json.dumps(current).replace(old, module.digest(audit)))
    (report_path / "assessment.json").write_text(table.encoded(current) + "\n")
    markdown = table.render(current["report"], "Selected package")
    (report_path / "results.md").write_text(markdown)
    (report_path / "results.html").write_text(table.render_html(markdown))
    assert table.main(["--root", saved["root"], "--verify-existing", str(report_path)]) == 2
    assert "could not complete" in capsys.readouterr().out


def test_actual_source_mutations_invalidate_and_fresh_replay_restores(tmp_path):
    import json
    import os

    import pytest

    configured = os.environ.get("EVAL_AUTHOR_AUDIT_REPORT")
    if not configured:
        pytest.skip("requires current admitted combined report")
    saved = json.loads(Path(configured).read_text())
    module = companion()
    result = module.exercise(Path(saved["root"]), saved["selection"], tmp_path / "exercise")
    assert result["revision_score"] == 3
    assert module.verify(Path(saved["root"]), saved["selection"], tmp_path / "exercise") == result
    assert result["checks"][module.REVISION + ".deterministic-3"] == "PASS"
    assert len(result["invalidation_tests"]) == 3
    probe = result["invalidation_tests"][0]
    retained = tmp_path / "exercise"
    mutation = retained / probe["id"] / probe["mutation_path"]
    original = (Path(saved["root"]) / probe["mutation_path"]).read_bytes()
    changed = mutation.read_bytes()
    mutation.write_bytes(original)
    with pytest.raises(ValueError, match="retained audit probes"):
        module.verify(Path(saved["root"]), saved["selection"], retained)
    mutation.write_bytes(changed)
    counterpart = retained / (probe["id"] + "-fresh-counterpart") / probe["mutation_path"]
    counterpart.write_bytes(changed)
    with pytest.raises(ValueError, match="retained audit probes"):
        module.verify(Path(saved["root"]), saved["selection"], retained)
    counterpart.write_bytes(original)
    report_module = importlib.import_module("skills.eval-author-rubric.scripts.measure")
    report = report_module.measure(Path(saved["root"]), saved["selection"])
    projected = module.project_report(report, result)
    assert projected["items"][module.REVISION]["score"] == 3
    assert projected["items"][module.FAIRNESS]["score"] is None
    assert projected["items"][module.REVISION]["evidence_strength"] == "E2"
    for ident, row in report["items"].items():
        if ident not in (module.REVISION, module.FAIRNESS):
            assert projected["items"][ident] == row
    table = importlib.import_module("rubric_table")
    rendered = table.render(projected, "Audit companion")
    assert "9/9" in rendered
    assert "scripted scorer comparison" in rendered
    assert len(rendered.encode()) <= 1048576
    for probe in result["invalidation_tests"]:
        assert probe["stale_reuse_rejected"] is True
        assert probe["invalidated_results"] == 20
        assert probe["unchanged_results"] == 14
        assert probe["fresh_source_observation"]["native_score"] is None
        assert probe["fresh_source_observation"]["changed"] is True
        assert probe["restored_replay_equal"] is True
        assert probe["original_preserved"] is True
