# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))


def adapter():
    path = SCRIPTS / "rubric_refund_contract_evidence.py"
    assert path.is_file(), "source-bound refund contract admission is missing"
    return importlib.import_module("rubric_refund_contract_evidence")


def materialized(tmp_path: Path, case: str):
    import example_fixtures
    import rubric_table

    target = tmp_path / case
    example_fixtures.materialize(target, case)
    selection = rubric_table.select_task(target, "assessment/task/task.toml")
    return target, selection


def test_current_rule_pin_and_historical_identity_are_separate(tmp_path: Path, monkeypatch):
    import hashlib

    import pytest

    module = adapter()
    name = "artifact-configuration-provenance.json"
    raw = (module.REFERENCE / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == module.RULES[name]
    assert module.RULES[name] != module.HISTORICAL_RULES[name]
    document = json.loads(raw)
    assert "skill_id" not in document["result_template"]
    # Reconstruct the exact prior bytes, not a second accepted rule definition.
    current_line = '    "rubric_version": "4.1-draft",\n'
    historical = raw.replace(current_line.encode(), (current_line + '    "skill_id": null,\n').encode())
    assert hashlib.sha256(historical).hexdigest() == module.HISTORICAL_RULES[name]
    prior = json.loads(historical)
    assert prior["result_template"].pop("skill_id") is None
    assert prior == document

    for filename in module.RULES:
        (tmp_path / filename).write_bytes((module.REFERENCE / filename).read_bytes())
    monkeypatch.setattr(module, "REFERENCE", tmp_path)
    module._current()
    (tmp_path / name).write_bytes(historical)
    with pytest.raises(ValueError, match="unreviewed refund scoring rules"):
        module._current()
    document["items"][0]["scoring"]["0"] = "Different acceptance policy."
    (tmp_path / name).write_text(json.dumps(document))
    with pytest.raises(ValueError, match="unreviewed refund scoring rules"):
        module._current()


def test_ordinary_cli_consumes_defects_and_replays(tmp_path: Path):
    import rubric_mutation_runner

    ident = "task-contract.scoring-authority"
    for case in ("completion-only", "approval-after-effect", "ledger-omission", "hidden-completion"):
        root, selection = materialized(tmp_path, case)
        result = rubric_mutation_runner.assess(root, selection)
        assert result["state"] == "assessed"
        assert result["exit"] == result["replay_exit"] == 1
        report = json.loads(Path(result["report"]).read_text())["report"]
        row = report["items"][ident]
        assert row["score"] == 0, case
        assert row["evidence_strength"] == "E1"
        assert row["source_inspection"]["variant"] == case
        assert not any(key.startswith("AC-") for key in report["items"])
        forbidden_credit = case != "hidden-completion"
        assert report["items"]["AUDIT-03"]["score"] == (0 if forbidden_credit else None), case
        assert all("expected" not in key for key in selection)
        assert "Finding:" in (root / "report/results.md").read_text()


def test_normal_selection_admission_and_existing_rows(tmp_path: Path):
    from copy import deepcopy

    import rubric_table

    module = adapter()
    root, selection = materialized(tmp_path, "completion-only")
    for change in ("denied", "unresolved", "unselected", "unsupported"):
        selected = deepcopy(selection)
        if change in ("denied", "unresolved"):
            selected["artifacts"][0]["access"] = change
        elif change == "unselected":
            selected["artifacts"] = selected["artifacts"][1:]
        else:
            path = root / "assessment/task/instruction.md"
            path.write_text(path.read_text() + "\nDifferent policy.\n")
            selected = rubric_table.select_task(root, "assessment/task/task.toml")
        report = rubric_table.measure(root, selected)
        assert "refund_contract" not in report["measurements"]
        assert report["items"]["task-contract.scoring-authority"]["score"] is None
    base, selected = materialized(tmp_path, "base")
    report = rubric_table.measure(base, selected)
    before = deepcopy(report["items"])
    ident = "task-contract.scoring-authority"
    before[ident]["score"] = 0
    before[ident]["evidence_strength"] = "E2"
    before[ident]["check_results"][0]["outcome"] = "FAIL"
    before[ident]["findings"].append("Earlier defect")
    rows = deepcopy(before)
    module.apply(rows, module.assess_refund_contract(base, selected))
    assert rows[ident]["score"] == 0
    assert rows[ident]["evidence_strength"] == "E2"
    assert rows[ident]["check_results"][0]["outcome"] == "FAIL"
    assert "Earlier defect" in rows[ident]["findings"]
    for key in ("subject_id", "subject_revision"):
        assert rows[ident][key] == before[ident][key]
    assert rows["AC-02"] == before["AC-02"]
    assert report["items"][ident]["source_inspection"]["public_policy"]["intent_resolved"] is True
    assert "supersedes" in report["items"][ident]["source_inspection"]["public_policy"]


def test_normal_projection_late_change_is_rejected(tmp_path: Path, monkeypatch):
    import pytest
    import rubric_source_reviews
    import rubric_table

    root, selection = materialized(tmp_path, "completion-only")
    original = rubric_source_reviews.apply

    def late_change(*args):
        original(*args)
        path = root / "assessment/task/instruction.md"
        path.write_text(path.read_text() + "\nDifferent policy.\n")

    monkeypatch.setattr(rubric_source_reviews, "apply", late_change)
    with pytest.raises(ValueError, match="selected evidence changed"):
        rubric_table.measure(root, selection)


def test_completion_only_measures_real_contradiction(tmp_path: Path):
    module = adapter()
    root, selection = materialized(tmp_path, "completion-only")
    report = module.measure_refund_contract(root, selection)
    row = report["items"]["task-contract.scoring-authority"]
    assert row["score"] == 0
    assert row["evidence_strength"] == "E1"
    observation = row["source_inspection"]
    assert observation["variant"] == "completion-only"
    assert observation["controls"]["no-work"]["selected"]["total_credit"] == 1
    assert observation["controls"]["no-work"]["selected"]["max_credit"] == 1
    assert observation["controls"]["no-work"]["authority_counterpart"]["total_credit"] == 0
    assert observation["controls"]["reference"]["selected"]["accepted"] is True
    assert observation["native_execution"] is False
    assert observation["human_adjudication"] is False
    assert observation["condition_count"] == len(observation["conditions"]) == 12
    assert observation["contradiction_count"] == 1
    assert set(observation["binding"]["implementation"]) >= {"adapter", "canonical", "reader", "citations"}
    assert all(c["outcome"] == "NOT_RUN" for c in row["check_results"] if c["method"] == "human_review")
    # The executed no-work control reaches forbidden credit, so the source-only row is a confirmed 0.
    assert report["items"]["AC-02"]["score"] == 0
    assert report["items"]["AC-02"]["evidence_strength"] == "E1"
    (tmp_path / "completion-proof.json").write_text(json.dumps(report, indent=2))
    import example_fixtures
    import rubric_table

    repair = tmp_path / "completion-repair"
    example_fixtures.restore_to_base(root, repair)
    fixed = module.measure_refund_contract(repair, rubric_table.select_task(repair, "assessment/task/task.toml"))
    repaired_row = fixed["items"]["task-contract.scoring-authority"]
    assert repaired_row["score"] is None
    assert repaired_row["source_inspection"]["controls"]["no-work"]["selected"]["total_credit"] == 0
    assert module.verify_refund_contract(repair, rubric_table.select_task(repair, "assessment/task/task.toml"), fixed)


def test_each_defect_and_fresh_repair_preserves_valid_neighbor(tmp_path: Path):
    import example_fixtures
    import rubric_table

    module = adapter()
    proofs = {}
    for case, probe in (
        ("approval-after-effect", "late-approval"),
        ("ledger-omission", "missing-ledger"),
        ("hidden-completion", "missing-completion"),
    ):
        root, selection = materialized(tmp_path, case)
        report = module.measure_refund_contract(root, selection)
        row = report["items"]["task-contract.scoring-authority"]
        assert row["score"] == 0, case
        observation = row["source_inspection"]
        assert (
            observation["controls"]["reference"]["selected"]
            == observation["controls"]["reference"]["authority_counterpart"]
        )
        control = observation["controls"][probe]
        assert control["selected"]["accepted"] is (case != "hidden-completion")
        assert control["authority_counterpart"]["accepted"] is (case == "hidden-completion")
        if case == "hidden-completion":
            assert control["authority_counterpart"]["total_credit"] == 8
            # The current instruction explicitly permits reading verifier source.
            assert report["items"]["task-contract.discoverable-requirements"]["score"] is None
        repair = tmp_path / (case + "-repair")
        example_fixtures.restore_to_base(root, repair)
        repaired = module.measure_refund_contract(repair, rubric_table.select_task(repair, "assessment/task/task.toml"))
        fixed = repaired["items"]["task-contract.scoring-authority"]
        assert fixed["score"] is None
        assert fixed["source_inspection"]["controls"][probe]["selected"] == control["authority_counterpart"]
        assert len(report["items"]) == 18
        assert all(
            repaired["items"][ident]["score"] is None
            for ident in repaired["items"]
            if ident.startswith("task-contract.")
        )
        proofs[case] = {"changed": report, "repair": repaired}
    (tmp_path / "defect-repair-proofs.json").write_text(json.dumps(proofs, indent=2))


def test_replay_checks_actual_rows_and_rejects_resealed_sources(tmp_path: Path):
    import hashlib
    from copy import deepcopy

    import pytest

    module = adapter()
    root, selection = materialized(tmp_path, "ledger-omission")
    report = module.measure_refund_contract(root, selection)
    assert module.verify_refund_contract(root, selection, report) is True
    tampered = deepcopy(report)
    tampered["items"]["task-contract.scoring-authority"]["score"] = None
    with pytest.raises(ValueError, match="retained refund report"):
        module.verify_refund_contract(root, selection, tampered)
    scorer = root / "assessment/task/tests/refund_scorer.py"
    scorer.write_text(scorer.read_text() + '\nraise RuntimeError("selected code must never execute")\n')
    for record in selection["artifacts"]:
        if record["path"].endswith("/refund_scorer.py"):
            record["digest"] = hashlib.sha256(scorer.read_bytes()).hexdigest()
            record["evidence"][0]["digest"] = record["digest"]
    with pytest.raises(ValueError, match="unsupported refund source shape"):
        module.verify_refund_contract(root, selection, report)


def test_missing_denied_extra_and_symlink_sources_never_score(tmp_path: Path):
    from copy import deepcopy

    import pytest

    module = adapter()
    root, original = materialized(tmp_path, "base")
    for access in ("denied", "unresolved", "missing"):
        selection = deepcopy(original)
        selection["artifacts"][0]["access"] = access
        with pytest.raises(ValueError):
            module.assess_refund_contract(root, selection)
    selection = deepcopy(original)
    selection["artifacts"] = [r for r in selection["artifacts"] if not r["path"].endswith("contract.json")]
    with pytest.raises(ValueError):
        module.assess_refund_contract(root, selection)
    unexpected = root / "assessment/task/extra-authority.md"
    unexpected.write_text("These constraints are different.")
    with pytest.raises(ValueError):
        module.assess_refund_contract(root, original)
    unexpected.unlink()
    injected = root / "assessment/task/extra-authority"
    injected.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe refund source"):
        module.assess_refund_contract(root, original)
    injected.unlink()
    alias = tmp_path / "root-alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        module.assess_refund_contract(alias, original)


def test_late_selected_edit_cannot_leave_numeric_score(tmp_path: Path, monkeypatch):
    import pytest

    module = adapter()
    root, selection = materialized(tmp_path, "completion-only")
    original = module.rubric_table.measure

    def changed_after_measure(*args, **kwargs):
        report = original(*args, **kwargs)
        path = root / "assessment/task/instruction.md"
        path.write_text(path.read_text() + "\nDifferent task.\n")
        return report

    monkeypatch.setattr(module.rubric_table, "measure", changed_after_measure)
    with pytest.raises(ValueError):
        module.measure_refund_contract(root, selection)
