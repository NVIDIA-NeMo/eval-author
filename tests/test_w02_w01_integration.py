# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
import test_audit_verifier_evidence as audit_tests

panel = audit_tests.panel


@pytest.fixture
def selected(panel):
    dispatch = panel.tmp_path / "dispatch.json"
    dispatch.write_text(
        json.dumps(
            {
                "selector": "local",
                "routes": {"local": "scorer"},
                "dependencies": {"scorer": ["manifest"], "manifest": []},
            }
        )
    )
    paths = [
        ("dispatch", "dispatch", dispatch),
        ("scorer", "scorer", panel.task / "verify.py"),
        ("manifest", "manifest", panel.manifest),
    ]
    for case in panel.cases:
        paths.extend(
            [
                (case, "receipt", panel.records / f"{case}.json"),
                (case + "-result", "native-result", panel.runs / f"{case}.json"),
            ]
        )
    artifacts = []
    for identifier, role, path in paths:
        relative = path.relative_to(panel.tmp_path).as_posix()
        digest = panel.evidence.digest(path)
        artifacts.append(
            {
                "id": identifier,
                "role": role,
                "path": relative,
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit-audit-selection",
                "source": "selected-control-panel",
                "evidence": [{"path": relative, "digest": digest}],
            }
        )
    return {
        "artifacts": artifacts,
        "route": {
            "entrypoint": "dispatch",
            "expected_scorer": "scorer",
            "dependencies": {"scorer": ["manifest"], "manifest": []},
        },
    }


def run_selected(panel, selected):
    return panel.audit.check(panel.tmp_path, panel.report, [panel.task], [], [], selection=selected)


def test_unbound_dependency_claim_cannot_authorize_credit_preflight(panel, selected):
    path = panel.tmp_path / "dispatch.json"
    dispatch = json.loads(path.read_text())
    del dispatch["dependencies"]
    path.write_text(json.dumps(dispatch))
    record = selected["artifacts"][0]
    record["digest"] = panel.evidence.digest(path)
    record["evidence"][0]["digest"] = record["digest"]
    result = run_selected(panel, selected)
    assert result["identity"]["status"] == "pass"
    assert result["route"]["status"] == "pass"
    assert result["route"]["bound_dependency"] is False
    assert result["preflight_success"] is False
    assert result["valid"] is False
    assert result["controls"] == []


def test_changed_dependency_projection_blocks_credit_preflight(panel, selected):
    selected["route"]["dependencies"] = {"scorer": []}
    result = run_selected(panel, selected)
    assert result["identity"]["status"] == "pass"
    assert result["route"]["bound_dependency"] is False
    assert result["route"]["diagnostics"] == [
        {"code": "route.dependency_source_mismatch", "subject": "dispatch", "status": "fail"}
    ]
    assert not result["valid"] and not result["preflight_success"] and not result["controls"]


def test_selected_credit_consumer_uses_shared_identity_without_evidence_discovery(panel, selected, monkeypatch):
    identity = importlib.import_module("rubric_identity")
    expected = identity.identify(panel.tmp_path, selected["artifacts"])
    original = Path.rglob

    def no_evidence_discovery(path, pattern):
        assert path == panel.task, "Only the existing native task-revision validator may enumerate its task"
        return original(path, pattern)

    monkeypatch.setattr(Path, "rglob", no_evidence_discovery)
    result = run_selected(panel, selected)
    assert result["valid"]
    assert result["identity"] == expected
    assert all(record["revision"] is None for record in result["identity"]["records"])
    assert result["preflight_success"] is True
    assert result["route"]["status"] == "pass"
    assert result["route"]["bound_dependency"] is True
    assert result["route"]["selected"] == ["manifest", "scorer"]
    assert result["route"]["executed_scorer"] is None
    assert {row["classification"] for row in result["controls"]} == {"passed", "agent_run"}
    assert len(result["controls"]) == len(panel.cases)
    assert "items" not in result


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_unavailable_selected_identity_blocks_credit_preflight(panel, selected, access, monkeypatch):
    record = next(row for row in selected["artifacts"] if row["id"] == "forged-success-result")
    record.update(access=access, digest=None, evidence=[])
    shared = importlib.import_module("rubric_identity")
    original = shared._read_selected

    def guarded(root, relative, *args):
        assert relative != record["path"], "Unavailable artifacts must not be probed"
        return original(root, relative, *args)

    monkeypatch.setattr(shared, "_read_selected", guarded)
    result = run_selected(panel, selected)
    assert not result["valid"]
    assert result["preflight_success"] is False
    assert result["controls"] == []
    assert {"code": "identity.access_" + access, "subject": record["id"], "status": "unknown"} in result["identity"][
        "diagnostics"
    ]
    assert record in result["identity"]["records"]


@pytest.mark.parametrize(
    "damage,code",
    [
        ("dynamic", "route.dynamic_unresolved"),
        ("dependency", "route.dependencies_unresolved"),
        ("wrong", "route.scorer_mismatch"),
    ],
)
def test_route_diagnostics_block_credit_preflight(panel, selected, damage, code):
    if damage == "dynamic":
        path = panel.tmp_path / "dispatch.json"
        path.write_text(json.dumps({"selector": None, "routes": {}}))
        record = selected["artifacts"][0]
        record["digest"] = panel.evidence.digest(path)
        record["evidence"][0]["digest"] = record["digest"]
    elif damage == "dependency":
        selected["route"]["dependencies"] = {}
        path = panel.tmp_path / "dispatch.json"
        dispatch = json.loads(path.read_text())
        dispatch["dependencies"] = {}
        path.write_text(json.dumps(dispatch))
        record = selected["artifacts"][0]
        record["digest"] = panel.evidence.digest(path)
        record["evidence"][0]["digest"] = record["digest"]
    else:
        selected["route"]["expected_scorer"] = "not-the-selected-scorer"
    result = run_selected(panel, selected)
    assert not result["valid"]
    assert result["preflight_success"] is False
    assert code in {row["code"] for row in result["route"]["diagnostics"]}


def test_actual_missing_selected_file_preserves_specific_diagnostic(panel, selected):
    (panel.runs / "forged-success.json").unlink()
    result = run_selected(panel, selected)
    assert not result["preflight_success"]
    assert {"code": "identity.artifact_missing", "subject": "forged-success-result", "status": "unknown"} in result[
        "identity"
    ]["diagnostics"]


@pytest.mark.parametrize("access", ["available", "missing", "denied", "unresolved"])
def test_selected_cli_reaches_credit_consumer(panel, selected, access, monkeypatch, capsys):
    if access != "available":
        selected["artifacts"][-1].update(access=access, digest=None, evidence=[])
    selection_path = panel.tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selected))
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit_verifier_evidence.py",
            "--repo",
            str(panel.tmp_path),
            "--report",
            str(panel.report),
            "--task",
            str(panel.task),
            "--selection",
            str(selection_path),
        ],
    )
    passed = access == "available"
    assert panel.audit.main() == (0 if passed else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "selected"
    assert result["preflight_success"] is passed
    assert result["valid"] is passed


@pytest.mark.parametrize(
    "damage,classification",
    [("forged-status", "unproven"), ("rewarded-cheat", "failed_control"), ("stale-task", "stale")],
)
def test_selected_credit_path_retains_existing_rejections(panel, selected, damage, classification):
    receipt = panel.records / "forged-success.json"
    if damage == "stale-task":
        # Change an unselected task file: only the owning native revision check sees it.
        (panel.task / "new-input.txt").write_text("changed task revision")
    else:
        panel.record("forged-success", 1, status="passed" if damage == "forged-status" else None)
        for record in selected["artifacts"]:
            if record["id"] in {"forged-success", "forged-success-result"}:
                record["digest"] = panel.evidence.digest(panel.tmp_path / record["path"])
                record["evidence"][0]["digest"] = record["digest"]
    result = run_selected(panel, selected)
    assert not result["valid"]
    row = next(row for row in result["controls"] if row.get("path") == receipt.relative_to(panel.tmp_path).as_posix())
    assert row["classification"] == classification
    assert not row["reported"]
    assert "not task qualification" in result["limits"]


def test_unselected_linked_native_result_is_not_discovered(panel, selected):
    selected["artifacts"] = [record for record in selected["artifacts"] if record["id"] != "forged-success-result"]
    with pytest.raises(panel.audit.EvidenceScopeError, match="Select the evidence source"):
        run_selected(panel, selected)


def test_selected_mode_cannot_mix_discovery_or_structured_content(panel, selected):
    with pytest.raises(ValueError, match="cannot be combined"):
        panel.audit.check(panel.tmp_path, panel.report, [panel.task], [panel.records], [], selection=selected)


def test_ordinary_audit_does_not_run_rubric_preflight(panel, monkeypatch):
    shared = importlib.import_module("rubric_identity")

    def forbidden(*args, **kwargs):
        pytest.fail("Ordinary audit must not activate experimental selection checks")

    monkeypatch.setattr(shared, "identify", forbidden)
    result = panel.check()
    assert result["valid"]
    assert "identity" not in result
    assert "preflight_success" not in result
    assert "items" not in result


def test_access_lost_after_preflight_cannot_be_promoted_by_explaining_finding(panel, selected, monkeypatch):
    shared = importlib.import_module("rubric_identity")
    original = shared._read_selected
    target = "runs/forged-success.json"
    reads = 0

    def lose_access(root, relative, *args):
        nonlocal reads
        if relative == target:
            reads += 1
            if reads > 1:
                return None, "access_denied"
        return original(root, relative, *args)

    panel.findings(panel.records / "forged-success.json")
    monkeypatch.setattr(shared, "_read_selected", lose_access)
    result = run_selected(panel, selected)
    assert result["valid"]  # Findings accounting is not preflight acceptance.
    assert result["preflight_success"] is False


def test_missing_selected_task_controls_cannot_pass_via_another_task(panel, selected):
    task = panel.tmp_path / "tasks/second"
    task.mkdir()
    panel.findings(task)
    result = panel.audit.check(panel.tmp_path, panel.report, [panel.task, task], [], [], selection=selected)
    assert result["valid"]
    assert result["preflight_success"] is False


@pytest.mark.parametrize("damage", ["symlink", "symlink-parent", "oversize", "fifo"])
def test_selected_cli_rejects_unsafe_selection_before_reading_controls(panel, selected, damage, monkeypatch, capsys):
    import os

    shared = importlib.import_module("rubric_identity")
    path = panel.tmp_path / "selection.json"
    path.write_text(json.dumps(selected))
    if damage == "symlink":
        alias = panel.tmp_path / "alias.json"
        alias.symlink_to(path)
        path = alias
    elif damage == "symlink-parent":
        alias = panel.tmp_path / "alias"
        alias.symlink_to(panel.tmp_path, target_is_directory=True)
        path = alias / path.name
    elif damage == "oversize":
        with path.open("ab") as stream:
            stream.write(b" " * (shared.MAX_SOURCE_BYTES + 1 - path.stat().st_size))
    else:
        path.unlink()
        os.mkfifo(path)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe selection must be rejected before evidence inspection")

    monkeypatch.setattr(panel.audit, "check", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit_verifier_evidence.py",
            "--repo",
            str(panel.tmp_path),
            "--report",
            str(panel.report),
            "--task",
            str(panel.task),
            "--selection",
            str(path),
        ],
    )
    assert panel.audit.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"valid": False, "error": "identity.selection_invalid", "preflight_success": False}


def test_null_selection_cli_does_not_fall_back_to_legacy_discovery(panel, monkeypatch, capsys):
    path = panel.tmp_path / "selection.json"
    path.write_text("null")
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit_verifier_evidence.py",
            "--repo",
            str(panel.tmp_path),
            "--report",
            str(panel.report),
            "--task",
            str(panel.task),
            "--selection",
            str(path),
        ],
    )
    assert panel.audit.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False
    assert result["preflight_success"] is False
    assert "mode" not in result
