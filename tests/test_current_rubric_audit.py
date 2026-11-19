# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric"))


def adapter():
    path = ROOT / "skills/eval-author-rubric/current_rubric_audit.py"
    assert path.is_file(), "accepted-selection Audit adapter is missing"
    return importlib.import_module("current_rubric_audit")


def test_attack_lookup_uses_admitted_comparison_identity_not_path_fragment():
    module = adapter()
    for path in ("attacks/native-index.json", "renamed/native-index.json", "nested/attacks/native-index.json"):
        manifest = {"index": path, "id": "revision"}
        baseline = {
            "revision_manifests": [manifest, {"index": "other/native-index.json", "id": "other"}],
            "result_revisions": [
                {"id": "reference-id", "lineage": path, "recorded_revision": "revision"},
                {"id": "split-id", "lineage": path, "recorded_revision": "revision"},
            ],
            "comparison": {"arms": [{"native_id": "reference-id"}, {"native_id": "split-id"}]},
        }
        assert module.attack_manifest(baseline) == manifest
        baseline["result_revisions"][1]["lineage"] = "other/native-index.json"
        with pytest.raises(ValueError, match="comparison lineage"):
            module.attack_manifest(baseline)


def test_derived_revision_gap_is_deterministic_not_independent(monkeypatch):
    module = adapter()
    assert "change-injection" in module.remaining_gate().lower()
    assert "independent" not in module.remaining_gate().lower()


def test_isolated_copy_retains_directory_modes(tmp_path):
    module = adapter()
    root = tmp_path / "original"
    (root / "task/nested").mkdir(parents=True)
    (root / "task").chmod(0o755)
    (root / "task/nested").chmod(0o750)
    source = root / "task/nested/input.txt"
    source.write_text("unchanged")
    source.chmod(0o640)
    selection = {"artifacts": [{"path": "task/nested/input.txt"}]}
    target = tmp_path / "copy"
    module.copy_selection(root, selection, target)
    assert (target / "task").stat().st_mode & 0o777 == 0o755
    assert (target / "task/nested").stat().st_mode & 0o777 == 0o750
    assert (target / "task/nested/input.txt").stat().st_mode & 0o777 == 0o640
    assert (target / "task/nested/input.txt").read_bytes() == source.read_bytes()


def test_retained_identity_tampering_rejected_before_exercises(tmp_path):
    import json

    module = adapter()
    original = tmp_path / "original.json"
    original.write_text(json.dumps({"selection": {}}))
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"base": {"report": str(original)}}))
    (tmp_path / "report").mkdir()
    (tmp_path / "report/assessment.json").write_text(json.dumps({"audit_closure_adapter": {}}))
    with pytest.raises(ValueError, match="adapter identity changed"):
        module.verify_existing(tmp_path, matrix, tmp_path / "unused-freeze")


def test_exercise_rejects_changed_neighbor_projection(tmp_path, monkeypatch):
    """Synthetic admission seam: current freshness alone is not equality."""
    module = adapter()
    root = tmp_path / "original"
    files = {
        "attacks/task/tests/refund_scorer.py": b'grader == "baseline"',
        "attacks/task/task.toml": b"timeout_sec = 120",
        "attacks/trials/reference/config.json": b"{}",
        "neighbor/native-index.json": b"{}",
    }
    for relative, raw in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    selection = {"artifacts": [{"id": key, "path": key} for key in files]}
    baseline = {
        "comparison": {"arms": [{"native_id": "reference"}, {"native_id": "alternative"}]},
        "result_revisions": [
            {"id": name, "lineage": "attacks/native-index.json", "recorded_revision": "attack"}
            for name in ("reference", "alternative")
        ]
        + [{"id": "neighbor", "lineage": "neighbor/native-index.json"}],
        "revision_manifests": [
            {"id": "attack", "index": "attacks/native-index.json"},
            {"id": "neighbor", "index": "neighbor/native-index.json"},
        ],
        "checks": {module.REVISION + ".deterministic-1": "PASS"},
        "revision_score": 2,
        "limitations": [],
    }

    def inspect(path, _selection):
        if any((path / key).read_bytes() != value for key, value in files.items()):
            raise ValueError("synthetic stale rejection")
        return baseline

    monkeypatch.setattr(module.audit, "inspect", inspect)
    monkeypatch.setattr(module.audit, "Sources", lambda path, records: path)
    monkeypatch.setattr(module.table, "select_task", lambda *args: {"artifacts": []})
    monkeypatch.setattr(
        module.audit,
        "project_panels",
        lambda path, *args: [{"task_freshness": "current", "observation": "original" if path == root else "changed"}],
    )
    # The fixture is not a native bundle; retain the real copy implementation below its index-expansion seam.
    monkeypatch.setattr(module.audit, "read_json", lambda *args: {"files": []})
    with pytest.raises(ValueError, match="unchanged neighbor projection differs"):
        module.exercise(root, selection, tmp_path / "exercise")


@pytest.mark.parametrize("change", ["mutation-removed", "invalidated-missing", "file-mode", "directory-mode"])
def test_verify_rejects_altered_retained_control_tree(tmp_path, monkeypatch, change):
    module = adapter()

    def exercise(root, selection, output):
        (output / "scoring-source").mkdir(parents=True)
        (output / "scoring-source/input").write_bytes(b"changed source")
        (output / "scoring-source-invalidated.json").write_text("[]")
        return {"synthetic": True}

    monkeypatch.setattr(module, "exercise", exercise)
    retained = tmp_path / "retained"
    exercise(tmp_path, {}, retained)
    assert module.verify(tmp_path, {}, retained) == {"synthetic": True}
    if change == "mutation-removed":
        (retained / "scoring-source/input").write_bytes(b"original source")
    elif change == "invalidated-missing":
        (retained / "scoring-source-invalidated.json").unlink()
    elif change == "file-mode":
        (retained / "scoring-source/input").chmod(0o700)
    else:
        directory = retained / "scoring-source"
        directory.chmod((directory.stat().st_mode & 0o777) ^ 0o040)
    with pytest.raises(ValueError, match="retained Audit controls disagree"):
        module.verify(tmp_path, {}, retained)


def test_projection_keeps_neighbors_and_existing_measurements():
    import json

    module = adapter()
    report_path = ROOT / "tmp/current-audit-closure-03/base/ordinary/assessment.json"
    if not report_path.exists():
        pytest.skip("requires retained authorized local Audit closure")
    report = json.loads(report_path.read_bytes())["report"]
    result = json.loads((ROOT / "tmp/current-audit-closure-03/exercise/audit.json").read_bytes())
    projected = module.project(report, result)
    reversed_report = json.loads(json.dumps(report))
    reversed_report["items"] = dict(reversed(list(reversed_report["items"].items())))
    assert module.table.render(module.project(reversed_report, result)) == module.table.render(projected)
    assert projected["items"][module.REVISION]["score"] == result["revision_score"]
    for key, row in report["items"].items():
        if key != module.REVISION:
            assert projected["items"][key] == row
    for key, value in report["measurements"].items():
        assert projected["measurements"][key] == value
    report["items"][module.REVISION]["check_results"][0]["outcome"] = "FAIL"
    assert module.project(report, result)["items"][module.REVISION]["score"] == 2
