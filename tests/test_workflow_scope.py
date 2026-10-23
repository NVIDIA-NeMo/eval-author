# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mutation tests for the checker; synthetic evidence is not a host replay."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workflow_scope", ROOT / "tools/check_workflow_scope.py")
assert SPEC and SPEC.loader
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)
CONTRACTS = json.loads(scope.CONTRACTS.read_text())


def capture(case):
    turns, calls = [], []
    empty = {"complete": True, "entries": {}, "errors": []}
    for index, contract in enumerate(CONTRACTS["cases"][case]):
        events = []
        for offset, action in enumerate(contract["required_actions"]):
            call_id = f"{index}-{offset}"
            calls.append({"id": call_id, "tool": "fixture_" + action, "turn": index})
            writes = [".eval-author/tasks/cover-read/task.toml"] if action == "scaffold" else []
            events.append({"call_id": call_id, "action": action, "tool": "fixture_" + action, "write_paths": writes})
        turns.append(
            {
                "prompt": contract["prompts"][0],
                "context": contract["context"],
                "flow": contract["flow"],
                "handoff": contract["handoff"],
                "events": events,
                "before": copy.deepcopy(empty),
                "after": copy.deepcopy(empty),
            }
        )
    raw = json.dumps({"tool_calls": calls}).encode()
    evidence = {
        "schema_version": 1,
        "case": case,
        "evidence_kind": "synthetic",
        "contracts_sha256": scope.digest(json.dumps(CONTRACTS, sort_keys=True, separators=(",", ":")).encode()),
        "raw_trace_sha256": scope.digest(raw),
        "turns": turns,
    }
    return evidence, raw


def add_call(evidence, raw, *, action, writes=(), turn=0):
    parsed = json.loads(raw)
    call_id = "extra-" + str(len(parsed["tool_calls"]))
    parsed["tool_calls"].append({"id": call_id, "tool": "fixture", "turn": turn})
    evidence["turns"][turn]["events"].append(
        {"call_id": call_id, "tool": "fixture", "action": action, "write_paths": list(writes)}
    )
    parsed["tool_calls"].sort(key=lambda call: call["turn"])
    raw = json.dumps(parsed).encode()
    evidence["raw_trace_sha256"] = scope.digest(raw)
    return raw


@pytest.mark.parametrize("case", CONTRACTS["cases"])
def test_matrix_and_paraphrases_are_checkable(case):
    evidence, raw = capture(case)
    for index, contract in enumerate(CONTRACTS["cases"][case]):
        for prompt in contract["prompts"]:
            evidence["turns"][index]["prompt"] = prompt
            report = scope.check(CONTRACTS, evidence, raw)
            assert report["status"] == "pass"
            assert report["host_acceptance"] == "not_measured"
            assert scope.check(CONTRACTS, evidence, raw) == report


@pytest.mark.parametrize(
    "case,action",
    [
        ("generic-help", "read_repository"),
        ("explicit-audit", "inventory"),
        ("inventory-only", "runtime_probe"),
        ("inventory-only", "execute"),
        ("recommendations-only", "scaffold"),
        ("task-draft", "execute"),
        ("trace-inspection", "install"),
        ("audit-validation", "read_trace"),
    ],
)
def test_scope_broadening_is_rejected(case, action):
    evidence, raw = capture(case)
    raw = add_call(evidence, raw, action=action, writes=["task.py"] if action == "scaffold" else [])
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


@pytest.mark.parametrize(
    "case,turn",
    [
        ("generic-help", 0),
        ("explicit-audit", 0),
        ("first-eval-opening", 0),
        ("explicit-expansion", 0),
        ("narrow-to-recommendations", 0),
        ("broaden-after-inventory", 1),
        ("continue-shared-entry", 0),
        ("clarify-goal-before-setup", 0),
        ("clarify-goal-before-setup", 1),
    ],
)
@pytest.mark.parametrize("action", ["read_repository", "runtime_probe", "validate", "delegate", "write_artifact"])
def test_shared_opening_rejects_prerequisite_work(case, turn, action):
    evidence, raw = capture(case)
    writes = [".eval-author/.working/journey-state.json"] if action == "write_artifact" else []
    raw = add_call(evidence, raw, action=action, writes=writes, turn=turn)
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["status"] == "fail"
    assert f"turn[{turn}]: forbidden action {action}" in report["violations"]
    if writes:
        assert f"turn[{turn}]: forbidden write attempt {writes[0]}" in report["violations"]


@pytest.mark.parametrize(
    "case,turn",
    [
        (name, index)
        for name, turns in CONTRACTS["cases"].items()
        for index, turn in enumerate(turns)
        if turn["handoff"] in {"ask_goal", "shared_opening", "shared_prerequisites", "ethos_complete_pause"}
    ],
)
def test_pure_conversation_rendering_does_not_authorize_other_work(case, turn):
    evidence, raw = capture(case)
    raw = add_call(evidence, raw, action="render_conversation", turn=turn)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"
    # Rendering a prompt does not grant continuation, even if the rendered text
    # asks the user to continue. Action checks retain the preexisting boundary.
    raw = add_call(evidence, raw, action="scaffold", turn=turn)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[{turn}]: forbidden action scaffold" in report["violations"]


def test_orientation_rendering_cannot_write_even_transient_input_files():
    evidence, raw = capture("first-eval-opening")
    raw = add_call(evidence, raw, action="render_conversation", writes=[".eval-author/.working/conversation.json"])
    report = scope.check(CONTRACTS, evidence, raw)
    assert "turn[0]: forbidden write attempt .eval-author/.working/conversation.json" in report["violations"]


@pytest.mark.parametrize("case,turn", [("continue-shared-entry", 1), ("clarify-goal-before-setup", 2)])
def test_shared_setup_work_is_allowed_after_opening(case, turn):
    evidence, raw = capture(case)
    for action in ("read_repository", "runtime_probe", "validate", "write_artifact"):
        writes = [".eval-author/.working/journey-state.json"] if action == "write_artifact" else []
        raw = add_call(evidence, raw, action=action, writes=writes, turn=turn)
    evidence["turns"][turn]["after"]["entries"] = {
        ".eval-author": {"kind": "directory", "mode": 0o755},
        ".eval-author/.working": {"kind": "directory", "mode": 0o755},
        ".eval-author/.working/journey-state.json": {
            "kind": "file",
            "mode": 0o644,
            "sha256": scope.digest(b"{}"),
        },
    }
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["status"] == "pass"
    assert report["host_acceptance"] == "not_measured"


@pytest.mark.parametrize("action", ["install", "execute", "read_trace", "scaffold"])
def test_prerequisite_inspection_does_not_authorize_downstream_work(action):
    evidence, raw = capture("continue-shared-entry")
    raw = add_call(evidence, raw, action=action, turn=1)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[1]: forbidden action {action}" in report["violations"]


@pytest.mark.parametrize(
    "action", ["inventory", "runtime_probe", "read_trace", "propose", "scaffold", "execute", "delegate"]
)
def test_ethos_completion_waits_before_downstream_work(action):
    evidence, raw = capture("ethos-completion-pause")
    raw = add_call(evidence, raw, action=action)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[0]: forbidden action {action}" in report["violations"]


def test_ethos_completion_can_save_state_but_cannot_create_evaluations():
    evidence, raw = capture("ethos-completion-pause")
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/.working/journey-state.json"])
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/tasks/new-case/task.toml"])
    report = scope.check(CONTRACTS, evidence, raw)
    assert "turn[0]: forbidden write attempt .eval-author/tasks/new-case/task.toml" in report["violations"]


def test_ethos_completion_continuation_allows_source_scope_handoff():
    evidence, raw = capture("ethos-completion-pause")
    raw = add_call(evidence, raw, action="delegate", turn=1)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"


def test_transient_write_is_rejected_even_if_snapshots_match():
    evidence, raw = capture("recommendations-only")
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/tasks/unauthorized/task.toml"])
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["status"] == "fail"
    assert any("write attempt" in v for v in report["violations"])


@pytest.mark.parametrize(
    "path", ["../source.py", "/tmp/source.py", "a/../../source.py", "a//b", "a/./b", "C:/outside", "a\\b"]
)
def test_noncanonical_destinations_are_rejected(path):
    with pytest.raises(ValueError):
        scope.permitted(path, [".eval-author/"])


def test_prefix_does_not_allow_similar_directory():
    assert not scope.permitted(".eval-author/tasks-other/a", [".eval-author/tasks/"])


@pytest.mark.parametrize("mutation", ["flow", "handoff", "context", "prompt"])
def test_request_and_route_evidence_cannot_be_swapped(mutation):
    evidence, raw = capture("explicit-audit")
    evidence["turns"][0][mutation] = "wrong"
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_followup_scope_does_not_inherit_earlier_permissions():
    evidence, raw = capture("broaden-after-inventory")
    raw = add_call(evidence, raw, action="inventory", turn=1)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_incomplete_and_empty_evidence_are_distinct():
    evidence, raw = capture("generic-help")
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"
    del evidence["turns"][0]["events"]
    with pytest.raises(KeyError):
        scope.check(CONTRACTS, evidence, raw)


@pytest.mark.parametrize("mutation", ["omitted", "duplicate", "wrong_tool", "unknown_action"])
def test_raw_coverage_cannot_silently_drop_tool_calls(mutation):
    evidence, raw = capture("inventory-only")
    events = evidence["turns"][0]["events"]
    if mutation == "omitted":
        events.clear()
    elif mutation == "duplicate":
        events.append(copy.deepcopy(events[0]))
    elif mutation == "wrong_tool":
        events[0]["tool"] = "different"
    else:
        events[0]["action"] = "unrecognized"
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["incomplete"]
    assert report["status"] != "pass"


@pytest.mark.parametrize("field", ["contracts_sha256", "raw_trace_sha256"])
def test_changed_inputs_invalidate_capture(field):
    evidence, raw = capture("generic-help")
    evidence[field] = "0" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        scope.check(CONTRACTS, evidence, raw)


def test_snapshot_detects_changes_and_symlinks(tmp_path):
    (tmp_path / ".hidden").write_text("before")
    (tmp_path / "source.py").write_text("source")
    (tmp_path / "link").symlink_to(tmp_path / "source.py")
    before = scope.snapshot(tmp_path)
    (tmp_path / ".hidden").write_text("after")
    (tmp_path / "source.py").chmod(0o700)
    after = scope.snapshot(tmp_path)
    assert before["entries"]["link"]["kind"] == "symlink"
    assert before["entries"][".hidden"] != after["entries"][".hidden"]
    assert before["entries"]["source.py"] != after["entries"]["source.py"]
    evidence, raw = capture("inventory-only")
    evidence["turns"][0].update(before=before, after=after)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_unreadable_snapshot_is_incomplete(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(scope.os, "scandir", denied)
    assert scope.snapshot(tmp_path)["complete"] is False


def test_noop_cannot_pass_inventory():
    evidence, raw = capture("inventory-only")
    evidence["turns"][0]["events"] = []
    raw = b'{"tool_calls":[]}'
    evidence["raw_trace_sha256"] = scope.digest(raw)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_captured_label_does_not_claim_live_acceptance():
    evidence, raw = capture("generic-help")
    evidence["evidence_kind"] = "captured"
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["host_acceptance"] == "not_measured"


def test_cli_missing_evidence_is_incomplete(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text("{}")
    raw = tmp_path / "raw.json"
    raw.write_text('{"tool_calls":[]}')
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/check_workflow_scope.py"),
            "check",
            "--evidence",
            str(path),
            "--raw-trace",
            str(raw),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "incomplete"


def test_artifact_parent_symlink_cannot_escape_scope():
    evidence, raw = capture("recommendations-only")
    entry = {"kind": "symlink", "mode": 0o777, "target": "/outside"}
    for state in ("before", "after"):
        evidence["turns"][0][state]["entries"][".eval-author"] = entry
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/proposals/dataset-recommendations.md"])
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_followup_snapshot_chain_must_match():
    evidence, raw = capture("broaden-after-inventory")
    evidence["turns"][1]["before"]["entries"]["extra"] = {"kind": "directory", "mode": 0o755}
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_real_inventory_preserves_source_and_allows_only_report(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    task = repo / "tasks/example"
    task.mkdir(parents=True)
    (task / "task.toml").write_text("intentionally not parsed")
    (repo / "agent.py").write_text("original source")
    evidence, _ = capture("inventory-only")
    before = scope.snapshot(repo)
    script = ROOT / "skills/eval-author/scripts/search_tasks.py"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "--repo", str(repo)], capture_output=True, text=True, check=True
    )
    assert scope.snapshot(repo) == before
    report_path = repo / ".eval-author/discovery.json"
    report_path.parent.mkdir()
    report_path.write_text(result.stdout)
    after = scope.snapshot(repo)
    assert len(json.loads(result.stdout)["candidates"]) == 1
    raw = json.dumps(
        {
            "tool_calls": [
                {"id": "scan", "turn": 0, "tool": "subprocess.run", "argv": [str(script), "--repo", str(repo)]},
                {"id": "save", "turn": 0, "tool": "Path.write_text", "path": ".eval-author/discovery.json"},
            ]
        }
    ).encode()
    evidence["raw_trace_sha256"] = scope.digest(raw)
    evidence["turns"][0].update(
        before=before,
        after=after,
        events=[
            {"call_id": "scan", "tool": "subprocess.run", "action": "inventory", "write_paths": []},
            {
                "call_id": "save",
                "tool": "Path.write_text",
                "action": "write_artifact",
                "write_paths": [".eval-author/discovery.json"],
            },
        ],
    )
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"
    (repo / "agent.py").write_text("unexpected change")
    evidence["turns"][0]["after"] = scope.snapshot(repo)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


@pytest.mark.parametrize("change", ["delete", "chmod", "symlink"])
def test_artifact_parent_does_not_allow_destructive_change(change):
    evidence, raw = capture("recommendations-only")
    before = evidence["turns"][0]["before"]["entries"]
    after = evidence["turns"][0]["after"]["entries"]
    before[".eval-author"] = {"kind": "directory", "mode": 0o755}
    if change == "chmod":
        after[".eval-author"] = {"kind": "directory", "mode": 0o777}
    elif change == "symlink":
        after[".eval-author"] = {"kind": "symlink", "mode": 0o777, "target": "/outside"}
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "fail"


def test_snapshot_errors_cannot_produce_pass():
    evidence, raw = capture("generic-help")
    evidence["turns"][0]["after"] = {"complete": False, "entries": {}, "errors": [{"path": ".", "errno": 13}]}
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "incomplete"


def test_unchanged_symlink_destination_is_forbidden():
    evidence, raw = capture("inventory-only")
    entry = {"kind": "symlink", "mode": 0o777, "target": "/outside/source"}
    for state in ("before", "after"):
        evidence["turns"][0][state]["entries"][".eval-author/discovery.md"] = entry
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/discovery.md"])
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["status"] == "fail"
    assert any("write through symlink" in item for item in report["violations"])


@pytest.mark.parametrize("field", ["events", "turns", "tool_calls", "write_paths", "errors", "entries"])
def test_wrong_container_shapes_are_rejected(field):
    evidence, raw = capture("generic-help")
    if field == "events":
        evidence["turns"][0]["events"] = {}
    elif field == "turns":
        evidence["turns"] = {}
    elif field == "tool_calls":
        raw = b'{"tool_calls":{}}'
        evidence["raw_trace_sha256"] = scope.digest(raw)
    elif field == "write_paths":
        raw = add_call(evidence, raw, action="read_skill")
        evidence["turns"][0]["events"][0]["write_paths"] = ""
    else:
        evidence["turns"][0]["before"][field] = {} if field == "errors" else []
    with pytest.raises(ValueError):
        scope.check(CONTRACTS, evidence, raw)


@pytest.mark.parametrize("mutation", ["reorder", "turn"])
def test_raw_call_order_and_turn_binding(mutation):
    evidence, raw = capture("inventory-only")
    raw = add_call(evidence, raw, action="read_repository")
    if mutation == "reorder":
        evidence["turns"][0]["events"].reverse()
    else:
        parsed = json.loads(raw)
        parsed["tool_calls"][0]["turn"] = 1
        raw = json.dumps(parsed).encode()
        evidence["raw_trace_sha256"] = scope.digest(raw)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "incomplete"
