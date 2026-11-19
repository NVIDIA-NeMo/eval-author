# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))


def test_separate_action_payload_has_no_expected_labels():
    spec = importlib.util.find_spec("rubric_refund_holdout_runtime")
    assert spec is not None, "held-out runtime is missing"
    import rubric_refund_holdout_runtime as runtime

    payload = runtime.payload("ledger-first-cent-split")
    assert set(payload) == {"case", "candidate", "invalid_requests"}
    events = payload["candidate"]["events"]
    assert [e["kind"] for e in events] == ["approve", "ledger", "refund", "refund"]
    assert [e["refund"]["amount_cents"] for e in events if e["kind"] == "refund"] == [1, 7999]
    assert "expected" not in json.dumps(payload)
    for case in ("ledger-cent-transfer", "zero-plus-full", "cross-order-one-cent"):
        assert [e["kind"] for e in runtime.payload(case)["candidate"]["events"]] == [
            "approve",
            "refund",
            "refund",
            "ledger",
        ]


def test_frozen_comparison_retains_per_check_disagreement():
    spec = importlib.util.find_spec("rubric_refund_holdout")
    assert spec is not None, "held-out owner is missing"
    import rubric_refund_holdout as owner

    expected = {"id": "case", "expected_total": 0, "expected_checks": [True, False]}
    actual = {"checks": {"a": 1, "b": 1}, "total_credit": 0}
    result = owner.compare(expected, ["a", "b"], actual)
    assert result["status"] == "disagreement"
    assert result["differences"] == [{"check": "b", "expected": 0, "actual": 1}]
    assert expected["expected_checks"] == [True, False]


def test_freeze_is_pinned_and_all_native_cases_are_declared():
    import rubric_refund_holdout as owner
    import rubric_refund_holdout_runtime as runtime

    assert hasattr(owner, "frozen"), "pinned freeze reader is missing"
    freeze = owner.frozen(_native_bundle() / "public-freeze.json")
    assert [c["id"] for c in freeze["cases"]] == list(runtime.CASES)
    assert freeze["author"]["independence_qualified"] is False
    assert hasattr(owner, "collect"), "native owner is missing"


def test_public_source_drift_is_rejected(tmp_path, monkeypatch):
    import pytest
    import rubric_refund_holdout as owner

    assert hasattr(owner, "validate_public_sources"), "public source binding is missing"
    (tmp_path / "instruction.md").write_text("changed public policy")
    monkeypatch.setattr(owner.collector, "BASE", tmp_path)
    with pytest.raises(ValueError):
        owner.validate_public_sources()


def test_frozen_bytes_tamper_rejected(tmp_path):
    import pytest
    import rubric_refund_holdout as owner

    path = tmp_path / "freeze.json"
    path.write_text('{"cases":[]}')
    with pytest.raises(ValueError):
        owner.frozen(path)


def _native_bundle():
    import os

    import pytest

    path = os.environ.get("EVAL_AUTHOR_HOLDOUT_TEST_BUNDLE")
    if not path:
        pytest.skip("Set EVAL_AUTHOR_HOLDOUT_TEST_BUNDLE to exercise admitted native evidence")
    return Path(path)


def test_actual_native_all_checks_match_after_ordering_decision():
    import rubric_refund_holdout as owner

    bundle = _native_bundle()
    result = owner.validate(bundle, owner.admission(bundle))
    assert result["counts"] == {
        "declared": 10,
        "completed": 10,
        "matched": 10,
        "disagreements": 0,
        "infrastructure_errors": 0,
    }
    assert result["rubric_score"] is None
    controls = {c["case"]: c for c in result["controls"]}
    assert controls["base-no-action"]["differences"] == []
    noaction = controls["base-no-action"]["actual"]
    assert noaction["checks"]["approval_before_effect"] == 1
    assert noaction["checks"]["approval_present"] == 0
    assert noaction["checks"]["amount"] == noaction["checks"]["fresh_effect"] == 0
    assert noaction["total_credit"] == 0
    assert controls["optional-false-cent-split"]["actual"]["total_credit"] == 8
    assert controls["declared-zero-inaction"]["companion"] is True
    for case in (
        "approval-off-by-one",
        "ledger-cent-transfer",
        "zero-plus-full",
        "approval-between-splits",
        "cross-order-one-cent",
    ):
        assert controls[case]["actual"]["total_credit"] == 0
        assert controls[case]["differences"] == []


def test_changed_producer_rejects_admitted_native(monkeypatch):
    import pytest
    import rubric_refund_holdout as owner

    bundle = _native_bundle()
    identity = owner.producer_identity()
    identity["holdout"][0]["sha256"] = "0" * 64
    monkeypatch.setattr(owner, "producer_identity", lambda: identity)
    with pytest.raises(ValueError):
        owner.admission(bundle)


def test_coherent_changed_actual_check_rejected(tmp_path):
    import shutil

    import pytest
    import rubric_refund_holdout as owner

    original = _native_bundle()
    document = owner.admission(original)
    bundle = tmp_path / "copy"
    bundle.mkdir()
    for name in owner.HoldoutPanel.directories:
        shutil.copytree(original / name, bundle / name)
    for name in owner.HoldoutPanel.documents:
        shutil.copyfile(original / name, bundle / name)
    assert owner.validate(bundle, document)["counts"]["completed"] == 10
    path = bundle / "trials/base-no-action/verifier/checks.json"
    checks = json.loads(path.read_text())
    checks["checks"]["approval_before_effect"] = 0
    path.write_text(json.dumps(checks))
    document["files"] = owner.indexed_files(bundle)
    with pytest.raises(ValueError):
        owner.validate(bundle, document)


def test_combined_selects_and_projects_actual_holdout_without_scoring():
    from copy import deepcopy

    import rubric_base_assessment as base
    import rubric_combined as combined
    from rubric_example_assessment import Sources

    bundle = _native_bundle()
    selection = base.select_panels(bundle.parent, bundle.name + "/task/task.toml", [bundle.name])
    assert [a["path"] for a in selection["artifacts"] if a["id"].startswith("native-panel:")] == [
        bundle.name + "/native-index.json"
    ]
    assert not any("freeze" in a["path"] for a in selection["artifacts"])
    sources = Sources(bundle.parent, selection["artifacts"])
    panels = combined.project_panels(sources, selection["example_assessment"], bundle.name + "/task")
    panel = panels[0]
    assert panel["counts"] == {
        "declared": 10,
        "completed": 10,
        "matched": 10,
        "disagreements": 0,
        "infrastructure_errors": 0,
    }
    assert panel["task_freshness"] == "current"
    assert panel["observation_status"] == "complete"
    assert panel["expected_agreement"] is True
    assert len(panel["controls"]) == 10
    noaction = next(c for c in panel["controls"] if c["case"] == "base-no-action")
    assert noaction["observation_status"] == "complete"
    assert noaction["expected_agreement"] is True
    assert noaction["differences"] == []
    assert noaction["actual"]["total_credit"] == noaction["expected"]["total_credit"] == 0
    assert panel["independence_qualified"] is False and panel["human_decisions"] == []
    rows = {"AC-02": {"score": 2, "check_results": []}, "task-contract.clear-contract": {"score": None}}
    before = deepcopy(rows)
    combined.apply_panels(rows, panels, lambda *_: (_ for _ in ()).throw(AssertionError("wrong scoring owner")))
    assert rows == before
    single = {"native_bundle_id": selection["example_assessment"]["native_bundle_ids"][0]}
    assert combined.project_panels(sources, single, bundle.name + "/task") == panels


def test_combined_rejects_unsupported_schema(tmp_path):
    import pytest
    import rubric_combined as combined

    (tmp_path / "native-index.json").write_text('{"schema":"eval-author.refund-heldout-panel.v99"}')
    with pytest.raises(ValueError):
        combined.select_bundle(tmp_path, ".")


def test_combined_rejects_changed_selected_task(tmp_path):
    import shutil

    import pytest
    import rubric_combined as combined
    from rubric_example_assessment import Sources
    from rubric_table import select_task

    bundle = _native_bundle()
    shutil.copytree(bundle / "task", tmp_path / "task")
    (tmp_path / "task/instruction.md").write_text("changed policy")
    import os

    root = Path(os.path.commonpath([bundle, tmp_path]))
    selection = select_task(root, (tmp_path / "task/task.toml").relative_to(root).as_posix())
    addition = combined.select_bundle(root, bundle.relative_to(root).as_posix())
    selection["artifacts"].extend(addition["artifacts"])
    with pytest.raises(ValueError):
        combined.project_panels(
            Sources(root, selection["artifacts"]),
            {"native_bundle_ids": [addition["native_bundle_id"]]},
            (tmp_path / "task").relative_to(root).as_posix(),
        )


def test_combined_rejects_tampered_actual_evidence(tmp_path):
    import shutil

    import pytest
    import rubric_base_assessment as base
    import rubric_combined as combined
    from rubric_example_assessment import Sources

    original = _native_bundle()
    bundle = tmp_path / "bundle"
    shutil.copytree(original, bundle)
    selection = base.select_panels(tmp_path, "bundle/task/task.toml", ["bundle"])
    path = bundle / "trials/base-no-action/verifier/checks.json"
    checks = json.loads(path.read_text())
    checks["checks"]["approval_before_effect"] = 0
    path.write_text(json.dumps(checks))
    with pytest.raises(ValueError):
        combined.project_panels(
            Sources(tmp_path, selection["artifacts"]), selection["example_assessment"], "bundle/task"
        )


def test_native_registry_does_not_accept_resealed_index(tmp_path):
    import pytest
    import rubric_refund_holdout as owner

    original = _native_bundle()
    document = owner.admission(original)
    document["bundle"] = str(tmp_path)
    (tmp_path / "native-index.json").write_text(json.dumps(document, sort_keys=True))
    with pytest.raises(ValueError):
        owner.admission(tmp_path)
