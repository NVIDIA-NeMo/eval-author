# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
from pathlib import Path

import pytest
from metric_benchmark.atif_recovery import recover_embedded_children
from metric_benchmark.io import digest_file, write_json


def recording(root: Path) -> dict:
    stub = {"schema_version": "ATIF-v1.7", "trajectory_id": "child", "agent": {"name": "Atom"}, "steps": []}
    write_json(
        root / "agent/trajectory.json",
        {"trajectory_id": "parent", "steps": [{"step_id": 1}], "subagent_trajectories": [stub]},
    )
    write_json(root / "agent/trajectory-index.json", {"sessions": [{"root": False, "path": "children/one.json"}]})
    child = {**stub, "steps": [{"step_id": 1, "message": "Recorded child event"}]}
    write_json(root / "agent/children/one.json", child)
    return child


def test_recovery_preserves_originals_and_binds_exact_recorded_child(tmp_path):
    source, output = tmp_path / "source", tmp_path / "derived"
    child = recording(source)
    (source / "result.json").write_text('{"reward": 1}')
    (source / "agent/tool-log.txt").write_text("Recorded tool output")
    originals = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert recover_embedded_children(source, output) == output
    value = json.loads((output / "agent/trajectory.json").read_text())
    assert value["subagent_trajectories"] == [child]
    assert all(p.read_bytes() == data for p, data in originals.items())
    assert all(
        (output / p.relative_to(source)).read_bytes() == data
        for p, data in originals.items()
        if p != source / "agent/trajectory.json"
    )
    proof = json.loads((output / "recovery.json").read_text())
    assert proof["inputs"] == {str(p.relative_to(source)): digest_file(p) for p in originals}
    assert proof["output_sha256"] == digest_file(output / "agent/trajectory.json")
    assert proof["source_root"] == "../source"
    repeat = tmp_path / "repeat"
    recover_embedded_children(source, repeat)
    assert (repeat / "recovery.json").read_bytes() == (output / "recovery.json").read_bytes()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("id", "child.*no exact-ID"),
        ("metadata", "child.*conflicts"),
        ("empty", "child.*empty"),
        ("escape", "without traversal"),
        ("duplicate", "ambiguous trajectory IDs"),
    ],
)
def test_rejects_unproven_child_replacements(tmp_path, change, message):
    source, output = tmp_path / "source", tmp_path / "derived"
    child = recording(source)
    if change == "id":
        child["trajectory_id"] = "other"
    if change == "metadata":
        child["agent"] = {"name": "other"}
    if change == "empty":
        child["steps"] = []
    (source / "agent/children/one.json").write_text(json.dumps(child))
    if change in ("escape", "duplicate"):
        sessions = [{"root": False, "path": "../outside.json" if change == "escape" else "children/one.json"}]
        if change == "duplicate":
            sessions += copy.deepcopy(sessions)
        (source / "agent/trajectory-index.json").write_text(json.dumps({"sessions": sessions}))
    with pytest.raises(ValueError, match=message):
        recover_embedded_children(source, output)
    assert not output.exists()


def test_recovers_nested_children(tmp_path):
    source, output = tmp_path / "source", tmp_path / "derived"
    child = recording(source)
    grandchild = {**child, "trajectory_id": "grandchild"}
    child["subagent_trajectories"] = [{**grandchild, "steps": []}]
    (source / "agent/children/one.json").write_text(json.dumps(child))
    (source / "agent/children/two.json").write_text(json.dumps(grandchild))
    (source / "agent/trajectory-index.json").write_text(
        json.dumps(
            {
                "sessions": [
                    {"root": False, "path": "children/one.json"},
                    {"root": False, "path": "children/two.json"},
                ]
            }
        )
    )
    recover_embedded_children(source, output)
    derived = json.loads((output / "agent/trajectory.json").read_text())
    assert derived["subagent_trajectories"][0]["subagent_trajectories"] == [grandchild]


def test_rejects_cycles(tmp_path):
    source = tmp_path / "source"
    child = recording(source)
    child["subagent_trajectories"] = [{**child, "steps": []}]
    (source / "agent/children/one.json").write_text(json.dumps(child))
    with pytest.raises(ValueError, match="cyclic"):
        recover_embedded_children(source, tmp_path / "cycle")
    assert not (tmp_path / "cycle").exists()


def test_native_validation_rejects_recovered_duplicate_siblings(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"))
    from trace_loading import load_traces

    source, output = tmp_path / "source", tmp_path / "derived"
    child = recording(source)
    child["agent"]["version"] = "1"
    child["steps"] = [{"step_id": 1, "source": "user", "message": "Recorded request"}]
    (source / "agent/children/one.json").write_text(json.dumps(child))
    parent = {**child, "trajectory_id": "parent", "subagent_trajectories": [{**child, "steps": []}] * 2}
    (source / "agent/trajectory.json").write_text(json.dumps(parent))
    recover_embedded_children(source, output)
    with pytest.raises(ValueError, match="embedded subagents require unique trajectory_id"):
        load_traces(output / "agent/trajectory.json", "atif")


@pytest.mark.parametrize("children", [None, []])
def test_complete_trace_needs_no_inventory_or_output(tmp_path, children):
    source = tmp_path / "source"
    write_json(source / "agent/trajectory.json", {"steps": [{"step_id": 1}], "subagent_trajectories": children})
    output = tmp_path / "derived"
    assert recover_embedded_children(source, output) is None
    assert not output.exists()


@pytest.mark.parametrize(
    "inventory",
    [
        {},
        {"sessions": {}},
        {"sessions": [None]},
        {"sessions": [{"root": "false", "path": "children/one.json"}]},
        {"sessions": [{"root": False}]},
    ],
)
def test_rejects_malformed_inventory_before_writing(tmp_path, inventory):
    source, output = tmp_path / "source", tmp_path / "derived"
    recording(source)
    (source / "agent/trajectory-index.json").write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match="inventory"):
        recover_embedded_children(source, output)
    assert not output.exists()


@pytest.mark.parametrize("location", ["source", "source/derived", "existing"])
def test_recovery_cannot_overwrite_evidence_or_existing_outputs(tmp_path, location):
    source = tmp_path / "source"
    recording(source)
    output = tmp_path / location
    if location == "existing":
        output.mkdir()
    original = (source / "agent/trajectory.json").read_bytes()
    with pytest.raises(ValueError, match="new output directory"):
        recover_embedded_children(source, output)
    assert (source / "agent/trajectory.json").read_bytes() == original
