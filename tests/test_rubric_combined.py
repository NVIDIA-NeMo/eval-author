# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest


def test_real_combined_attack_and_boundary_selection(monkeypatch):
    configured = os.environ.get("EVAL_AUTHOR_COMBINED_TEST_ROOT")
    if not configured:
        pytest.skip("set EVAL_AUTHOR_COMBINED_TEST_ROOT to current combined collection")
    scratch = Path(configured).parent
    directory = Path(configured).name
    bundles = [directory + "/attacks", directory + "/boundaries"]
    module = importlib.import_module("rubric_base_assessment")
    selection = module.select_panels(scratch, bundles[0] + "/task/task.toml", bundles)
    report = importlib.import_module("skills.eval-author-rubric.scripts.measure").measure(scratch, selection)
    assert len(report["items"]) == 18
    assert report["items"]["AC-02"]["score"] == 2
    assert report["items"]["AC-03"]["score"] == 2
    assert report["items"]["AC-04"]["score"] == 2
    panels = report["measurements"]["example_assessment"]["native"]["panels"]
    assert len(panels) == 2
    assert sum(panel["counts"]["attempts"] for panel in panels) == 31


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
importlib.import_module("skills.eval-author-rubric.scripts.measure")


def test_combined_projection_keeps_all_scopes_and_current_defect():
    combined = importlib.import_module("rubric_combined")
    rows = {
        "AC-02": {
            "score": None,
            "check_results": [
                {"id": "c", "outcome": "NOT_RUN", "evidence": [], "evidence_refs": [], "remaining_gap": "missing"}
            ],
            "findings": [],
            "remaining_gaps": [],
            "limitations": [],
            "evidence_refs": [],
        }
    }
    original = deepcopy(rows)

    def apply(items, panel):
        row = items["AC-02"]
        row.update(score=panel["score"], status="ASSESSED")
        row["findings"].append({"code": panel["scope"]})
        row["check_results"][0]["outcome"] = "FAIL" if panel["score"] == 0 else "PASS"

    panels = [
        {"scope": "failed", "index_digest": "a", "score": 0},
        {"scope": "passed", "index_digest": "b", "score": 2},
    ]
    combined.apply_panels(rows, panels, apply)
    assert rows["AC-02"]["score"] == 0
    assert rows["AC-02"]["check_results"][0]["outcome"] == "FAIL"
    assert rows["AC-02"]["findings"] == [{"code": "failed"}, {"code": "passed"}]
    assert len(rows["AC-02"]["native_panel_assessments"]) == 2
    reverse = deepcopy(original)
    combined.apply_panels(reverse, panels[::-1], apply)
    assert reverse == rows


def test_existing_failed_check_cannot_be_overwritten():
    combined = importlib.import_module("rubric_combined")
    rows = {
        "x": {
            "score": 0,
            "findings": [{"code": "existing"}],
            "check_results": [{"id": "c", "outcome": "FAIL", "remaining_gap": "current defect"}],
        }
    }

    def apply(items, panel):
        items["x"]["score"] = 2
        items["x"]["check_results"][0].update(outcome="PASS", remaining_gap=None)

    combined.apply_panels(rows, [{"scope": "new", "index_digest": "a"}], apply)
    assert rows["x"]["score"] == 0
    assert rows["x"]["check_results"][0]["outcome"] == "FAIL"
    assert rows["x"]["check_results"][0]["remaining_gap"] == "current defect"


@pytest.mark.parametrize("ident", ["AC-02", "AC-03", "AC-04", "provenance.runtime-route"])
def test_owning_projection_replaces_only_baseline_absence(ident):
    from rubric_scoring import ITEM_LIMITATIONS

    combined = importlib.import_module("rubric_combined")
    absent = ITEM_LIMITATIONS.get(
        ident,
        "No owning Anticheat control evidence is selected for this task. "
        "Source inspection cannot establish solver boundaries, credit, consumer integrity or reset behavior.",
    )
    missing_check = "No qualified evidence consumed for: Versioned validation result and errors."
    old_gap = missing_check if ident.startswith("provenance.") else absent
    independent = "Independent review remains absent."
    contradiction = {"message": "The recorded owner decision contradicts the public contract."}
    rows = {
        ident: {
            "score": None,
            "limitations": [absent, independent],
            "remaining_gaps": [absent, old_gap, independent],
            "findings": [contradiction],
            "check_results": [
                {
                    "id": ident + ":check",
                    "outcome": "NOT_RUN",
                    "expected_evidence": "Versioned validation result and errors.",
                    "remaining_gap": old_gap,
                    "evidence_refs": [],
                }
            ],
        }
    }

    def apply(items, panel):
        row = items[ident]
        row.update(score=2, remaining_gaps=[independent])
        row["limitations"].append(independent)
        row["check_results"][0].update(
            outcome="PASS",
            remaining_gap=independent,
            evidence_refs=[
                "selected-source-reference",
                {"measurement": "example_assessment.native", "digest": panel["index_digest"]},
            ],
        )

    combined.apply_panels(rows, [{"scope": "owning", "index_digest": "a"}], apply)
    row = rows[ident]
    assert row["limitations"] == [independent]
    assert row["remaining_gaps"] == [independent]
    assert row["check_results"][0]["remaining_gap"] == independent
    assert row["score"] == 2
    assert row["findings"] == [contradiction]


def test_table_displays_panel_scopes_and_separate_credit():

    table = importlib.import_module("rubric_table")
    panels = [
        {
            "scope": scope,
            "index_digest": digest,
            "native_execution": True,
            "counts": {"attempts": 1, "completed": 1, "infrastructure_errors": 0},
            "controls": [{"case": scope, "status": "passed", "checks": {"total_credit": credit, "max_credit": 4}}],
        }
        for scope, digest, credit in [("attack", "a", 0), ("boundary", "b", 4)]
    ]
    report = {"measurements": {"example_assessment": {"native": {"panels": panels}}}}
    row = {"evidence_refs": [{"measurement": "example_assessment.native", "digest": p["index_digest"]} for p in panels]}
    label, details = table.task_credit(report, row, 1)
    assert "Not measured" not in label
    assert "attack" in "\n".join(details) and "boundary" in "\n".join(details)
    assert "0/4" in "\n".join(details) and "4/4" in "\n".join(details)
    assert 'id="task-credit-1"' in "\n".join(details)


@pytest.mark.parametrize("suffix", ["deterministic-1", "deterministic-2"])
def test_native_observation_replaces_source_only_check_gap(suffix):
    from rubric_report import PARTIAL_HELPERS

    combined = importlib.import_module("rubric_combined")
    ident = "provenance.observed-configuration"
    check_id = ident + "." + suffix
    gap = PARTIAL_HELPERS[check_id][1] + " Still required: Native observation."
    rows = {
        ident: {
            "score": None,
            "limitations": [],
            "remaining_gaps": [gap],
            "check_results": [
                {
                    "id": check_id,
                    "outcome": "PARTIAL",
                    "remaining_gap": gap,
                    "expected_evidence": "Native observation.",
                    "evidence_refs": [],
                }
            ],
        }
    }

    def apply(items, panel):
        row = items[ident]
        row.update(score=2, remaining_gaps=["Independent repeat remains absent."])
        row["check_results"][0].update(
            outcome="PASS",
            remaining_gap=None,
            evidence_refs=[{"measurement": "example_assessment.native.environment", "digest": panel["index_digest"]}],
        )

    combined.apply_panels(rows, [{"scope": "runtime", "index_digest": "a"}], apply)
    assert rows[ident]["remaining_gaps"] == ["Independent repeat remains absent."]


@pytest.mark.parametrize("outcome", ["NOT_RUN", "FAIL", "ERROR"])
def test_absence_is_retained_without_replacing_its_check(outcome):
    from rubric_scoring import ITEM_LIMITATIONS

    combined = importlib.import_module("rubric_combined")
    gap = ITEM_LIMITATIONS["provenance.runtime-route"]
    rows = {
        "provenance.runtime-route": {
            "score": None,
            "limitations": [gap],
            "remaining_gaps": [gap],
            "check_results": [{"id": "c", "outcome": outcome, "remaining_gap": gap, "evidence_refs": []}],
        }
    }

    def apply(items, panel):
        items["provenance.runtime-route"].update(score=2, remaining_gaps=[])

    combined.apply_panels(rows, [{"scope": "new", "index_digest": "a"}], apply)
    assert rows["provenance.runtime-route"]["limitations"] == [gap]
    assert rows["provenance.runtime-route"]["remaining_gaps"] == [gap]
    assert rows["provenance.runtime-route"]["check_results"][0]["outcome"] == outcome


@pytest.mark.parametrize(
    "selector",
    [
        {"native_bundle_ids": []},
        {"native_bundle_ids": ["a", "a"]},
        {"native_bundle_ids": ["a"], "native_bundle_id": "b"},
        {"native_bundle_ids": "a"},
    ],
)
def test_invalid_panel_selection_is_rejected(selector):
    combined = importlib.import_module("rubric_combined")
    with pytest.raises(ValueError, match="invalid native panel selection"):
        combined.project_panels(None, selector, "task")


def test_registry_list_does_not_admit_unregistered_bytes(tmp_path, monkeypatch):
    combined = importlib.import_module("rubric_combined")
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRIES", str(tmp_path))
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRY", "original")
    with pytest.raises(ValueError, match="admission missing"):
        with combined.registry_for(b"not a registered index"):
            pytest.fail("unregistered panel admitted")
    assert os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"] == "original"


def test_owning_native_evidence_can_complete_partial_source_row():
    combined = importlib.import_module("rubric_combined")
    rows = {"x": {"score": 1, "check_results": []}}

    def apply(items, panel):
        items["x"]["score"] = 2

    combined.apply_panels(rows, [{"scope": "runtime", "index_digest": "a"}], apply)
    assert rows["x"]["score"] == 2


def test_combined_report_replays_with_bounded_reader():
    configured = os.environ.get("EVAL_AUTHOR_COMBINED_TEST_ROOT")
    if not configured:
        pytest.skip("requires current combined native run")
    import json

    table = importlib.import_module("rubric_table")
    report = json.loads((Path(configured) / "report/assessment.json").read_text())["report"]
    markdown = table.render(report, "Selected package")
    assert len(markdown.encode()) <= 1048576
    assert len(table.render_html(markdown).encode()) <= 1048576


@pytest.mark.parametrize("mutation", ["score", "drop-panel", "counts"])
def test_combined_replay_rejects_changed_claims(tmp_path, monkeypatch, mutation):
    import json
    import shutil

    configured = os.environ.get("EVAL_AUTHOR_COMBINED_REPORT")
    if not configured:
        pytest.skip("requires current combined report")
    table = importlib.import_module("rubric_table")
    source = Path(configured)
    destination = tmp_path / "report"
    shutil.copytree(source, destination)
    path = destination / "assessment.json"
    saved = json.loads(path.read_text())
    if mutation == "score":
        saved["report"]["items"]["AC-02"]["score"] = 3
    elif mutation == "drop-panel":
        saved["selection"]["example_assessment"]["native_bundle_ids"].pop()
    else:
        saved["report"]["measurements"]["example_assessment"]["native"]["counts"]["infrastructure_errors"] = 0
    path.write_text(json.dumps(saved))
    assert table.main(["--root", saved["root"], "--verify-existing", str(destination)]) == 2


def test_base_reuses_selection_with_explicit_operator_trust(tmp_path, monkeypatch):
    import json

    base = importlib.import_module("rubric_base_assessment")
    table = importlib.import_module("rubric_table")
    store = tmp_path / "operator"
    store.mkdir()
    selected = tmp_path / "selection.json"
    selected.write_text("{}")
    seen = []

    def observed(args):
        seen.append(
            (
                args,
                os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE"),
                os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST"),
            )
        )
        return 1

    monkeypatch.setattr(table, "main", observed)
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", raising=False)
    result = base.main(
        [
            "--root",
            str(tmp_path),
            "--selection",
            str(selected),
            "--output",
            str(tmp_path / "out"),
            "--source-review-operator-store",
            str(store),
            "--source-review-operator-trust",
            "localhost-assessment-operator-v1",
        ]
    )
    assert result == 1
    assert len(seen) == 2
    assert all(entry[1:] == (str(store), "localhost-assessment-operator-v1") for entry in seen)
    assert "--selection" in seen[0][0] and "--verify-existing" in seen[1][0]
    assert os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE") is None
    assert json.loads(selected.read_text()) == {}


def test_base_rejects_operator_store_without_explicit_trust(tmp_path, monkeypatch):
    base = importlib.import_module("rubric_base_assessment")
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", "localhost-assessment-operator-v1")
    assert (
        base.main(
            [
                "--root",
                str(tmp_path),
                "--selection",
                str(tmp_path / "selection.json"),
                "--output",
                str(tmp_path / "out"),
                "--source-review-operator-store",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert not (tmp_path / "out").exists()


def test_collection_rejects_changed_base_before_docker(tmp_path):
    import shutil

    from rubric_native_collect import BASE

    module = importlib.import_module("rubric_base_assessment")
    shutil.copytree(BASE, tmp_path / "task")
    (tmp_path / "task/instruction.md").write_text("changed contract")
    with pytest.raises(ValueError, match="unchanged base"):
        module.validate_base(tmp_path, "task/task.toml")
