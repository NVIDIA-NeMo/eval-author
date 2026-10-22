# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import workflow_matrix as matrix
from check_workflow_scope import snapshot
from discovery_determinism import encoded, sha
from test_workflow_scope import CONTRACTS, capture


def test_all_documented_hosts_forms_platforms_and_paraphrases_are_planned():
    plan = matrix.plan(CONTRACTS)
    cells = plan["cells"]
    assert {cell["host"] for cell in cells} == {"codex", "claude-code", "cursor", "pi"}
    assert {cell["platform"] for cell in cells} == {"darwin", "linux", "win32"}
    assert {cell["installation"] for cell in cells} == {"checkout", "copied"}
    assert len({cell["id"] for cell in cells}) == len(cells)
    result = matrix.summarize(plan, CONTRACTS, [])
    assert result["planned"] == result["counts"]["incomplete"]
    assert not result["passed"] and result["attempted"] == 0
    skipped = {**cells[0], "skip_reason": "host unavailable"}
    result = matrix.summarize(plan, CONTRACTS, [skipped])
    assert result["counts"]["skipped"] == 1
    assert sum(result["counts"].values()) == len(cells)
    with pytest.raises(ValueError, match="duplicate"):
        matrix.summarize(plan, CONTRACTS, [skipped, skipped])


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_native_tools_are_retained_and_unknown_events_are_incomplete(host):
    if host == "codex":
        events = [
            {"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": "touch x"}},
            {"type": "turn.completed"},
        ]
    else:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "touch x"}}]
                },
            },
            {"type": "result", "is_error": False},
        ]
    raw = b"\n".join(encoded(event) for event in events)
    calls, errors = matrix.native_calls(host, raw, 1)
    assert not errors and calls[0]["id"] == "1:1" and calls[0]["turn"] == 1
    assert "touch x" in json.dumps(calls)
    assert matrix.native_calls(host, raw + b'\n{"type":"new_tool"}', 1)[1]
    assert matrix.native_calls(host, b"", 0)[1]


def attempt(tmp_path):
    cell = matrix.plan(CONTRACTS)["cells"][0]
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("Test-only skill installation.")
    skill_sha = sha(encoded(snapshot(skills)))
    native = tmp_path / "native.jsonl"
    native.write_text('{"type":"turn.completed"}\n')
    evidence, _ = capture(cell["case"])
    evidence["evidence_kind"] = "captured"
    evidence["raw_trace_sha256"] = sha(encoded({"tool_calls": []}))
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "raw_sha256": [sha(native.read_bytes())],
                "skills_sha256": skill_sha,
                "reviewed_by": "synthetic-test-reviewer",
                "evidence": evidence,
            }
        )
    )
    captured = {
        "cell": cell,
        "platform": cell["platform"],
        "turn": 0,
        "prompt": cell["prompts"][0],
        "returncode": 0,
        "reason": "awaiting_native_review",
        "skills_unchanged": True,
        "host_version": "fixture",
        "model": "fixture",
        "settings": {"sandbox": "fixture"},
        "skills_root": str(skills),
        "skills_sha256": skill_sha,
        "raw": {"path": str(native), "sha256": sha(native.read_bytes())},
        "before": evidence["turns"][0]["before"],
        "after": evidence["turns"][0]["after"],
    }
    capture_file = tmp_path / "capture.json"
    capture_file.write_text(json.dumps(captured))
    return {
        "captures": [{"path": str(capture_file), "sha256": sha(capture_file.read_bytes())}],
        **cell,
        "host_version": "fixture",
        "model": "fixture",
        "source_revision": "fixture",
        "fixture_sha256": sha(encoded(evidence["turns"][0]["before"])),
        "settings": {"sandbox": "fixture"},
        "skills_root": str(skills),
        "skills_sha256": skill_sha,
        "review": str(review),
        "raw_turns": [{"path": str(native), "sha256": sha(native.read_bytes())}],
    }


def test_review_mutations_fail_but_later_skill_updates_preserve_evidence(tmp_path):
    recorded = attempt(tmp_path)
    assert matrix.evaluate(CONTRACTS, recorded)["status"] == "pass"  # adapter unit test, not live evidence
    changed = copy.deepcopy(recorded)
    changed["model"] = ""
    assert matrix.evaluate(CONTRACTS, changed)["status"] == "incomplete"
    review = Path(recorded["review"])
    payload = json.loads(review.read_text())
    payload["evidence"]["turns"][0]["flow"] = "eval-author-first-eval"
    review.write_text(json.dumps(payload))
    assert matrix.evaluate(CONTRACTS, recorded)["status"] == "fail"
    (Path(recorded["skills_root"]) / "SKILL.md").write_text("changed")
    payload["evidence"]["turns"][0]["flow"] = "eval-author"
    review.write_text(json.dumps(payload))
    assert matrix.evaluate(CONTRACTS, recorded)["status"] == "pass"


@pytest.mark.parametrize("platform", matrix.PLATFORMS)
def test_saved_attempt_is_portable_and_does_not_read_original_skills(tmp_path, platform):
    root = tmp_path / "original"
    root.mkdir()
    recorded = attempt(root)
    cell = next(
        c
        for c in matrix.plan(CONTRACTS)["cells"]
        if c["host"] == "codex" and c["case"] == recorded["case"] and c["platform"] == platform and c["repeat"] == 0
    )
    recorded.update(cell)
    record_path = Path(recorded["captures"][0]["path"])
    captured = json.loads(record_path.read_text())
    captured.update(cell=cell, platform=platform, skills_root=r"C:\old\skills")
    captured["raw"]["path"] = r"C:\old\capture\native.jsonl"
    record_path.write_text(json.dumps(captured))
    recorded["captures"][0]["sha256"] = sha(record_path.read_bytes())
    recorded["skills_root"] = r"C:\old\skills"
    recorded["review"] = "review.json"
    for entry in recorded["captures"] + recorded["raw_turns"]:
        entry["path"] = Path(entry["path"]).name
    (root / "attempt.json").write_text(json.dumps(recorded))
    destination = tmp_path / "archive"
    root.rename(destination)
    loaded = matrix.load_attempt(destination / "attempt.json")
    assert matrix.evaluate(CONTRACTS, loaded)["status"] == "pass"
    (destination / "native.jsonl").write_text("{}")
    assert matrix.evaluate(CONTRACTS, loaded)["status"] == "incomplete"


@pytest.mark.parametrize("scalar", [1, True, "warning", None])
def test_scalar_native_events_mark_only_the_attempt_incomplete(tmp_path, scalar):
    recorded = attempt(tmp_path)
    recorded["prompts"].append("Follow up")
    Path(recorded["raw_turns"][0]["path"]).write_text(json.dumps(scalar))
    with pytest.raises(ValueError, match="object"):
        matrix.native_session("codex", encoded(scalar))
    # Exercise the summary's normal error handling with a valid single-turn cell.
    recorded["prompts"].pop()
    result = matrix.summarize(matrix.plan(CONTRACTS), CONTRACTS, [recorded])
    assert result["observations"][0]["status"] == "incomplete"
    assert "object" in result["observations"][0]["reason"]


@pytest.mark.parametrize("field", matrix.PROVENANCE)
def test_repeats_require_consistent_provenance(tmp_path, monkeypatch, field):
    recorded = attempt(tmp_path)
    second = copy.deepcopy(recorded)
    second.update(matrix.plan(CONTRACTS)["cells"][1])
    raw = tmp_path / "second.jsonl"
    raw.write_text('{"type":"turn.completed"}\n\n')
    second["raw_turns"] = [{"path": str(raw), "sha256": sha(raw.read_bytes())}]
    # Isolate cross-repeat accounting: each capture can independently validate.
    monkeypatch.setattr(matrix, "evaluate", lambda *args: {"status": "pass"})
    assert matrix.summarize(matrix.plan(CONTRACTS), CONTRACTS, [recorded, second])["counts"]["pass"] == 2
    second[field] = "upgraded"
    result = matrix.summarize(matrix.plan(CONTRACTS), CONTRACTS, [recorded, second])
    assert result["counts"]["pass"] == 0
    assert all("repeat provenance differs within cell" in row["incomplete"] for row in result["observations"][:2])


def test_matrix_cannot_drop_unmeasured_cells():
    plan = matrix.plan(CONTRACTS)
    plan["cells"] = plan["cells"][:1]
    with pytest.raises(ValueError, match="edited"):
        matrix.summarize(plan, CONTRACTS, [])


def test_reused_native_capture_does_not_establish_repeats(tmp_path):
    recorded = attempt(tmp_path)
    plan = matrix.plan(CONTRACTS)
    second = {**recorded, **plan["cells"][1]}
    summary = matrix.summarize(plan, CONTRACTS, [recorded, second])
    assert summary["counts"]["pass"] == 1
    assert summary["observations"][1]["status"] == "incomplete"
    assert "reused" in summary["observations"][1]["reason"]


def test_unfinished_native_tool_cannot_disappear():
    raw = b'{"type":"item.started","item":{"id":"1","type":"command_execution"}}\n{"type":"turn.completed"}'
    assert "native items did not complete" in matrix.native_calls("codex", raw, 0)[1]


def test_recorder_retains_failed_host_and_filesystem_effects(tmp_path):
    from capture_workflow import capture as record

    repo, skills = tmp_path / "repo", tmp_path / "skills"
    repo.mkdir()
    skills.mkdir()
    (skills / "SKILL.md").write_text("synthetic host test")
    cell = matrix.plan(CONTRACTS)["cells"][0]
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('unexpected').write_text('x'); raise SystemExit(1)",
    ]
    result = record(
        cell,
        repo,
        skills,
        tmp_path / "capture",
        command,
        host_version="synthetic",
        model="synthetic",
        settings={"sandbox": "test"},
    )
    assert result["returncode"] == 1 and result["reason"] == "host_failed"
    assert result["status"] == "incomplete"
    assert result["platform"] == sys.platform
    assert "unexpected" not in result["before"]["entries"] and "unexpected" in result["after"]["entries"]
    with pytest.raises(FileExistsError):
        record(
            cell, repo, skills, tmp_path / "capture", command, host_version="synthetic", model="synthetic", settings={}
        )


def test_successful_retry_cannot_hide_an_incomplete_attempt(tmp_path):
    recorded = attempt(tmp_path)
    missing = {**recorded, "attempt_id": "failed-first", "raw_turns": [{"path": str(tmp_path / "absent")}]}
    summary = matrix.summarize(matrix.plan(CONTRACTS), CONTRACTS, [missing, {**recorded, "attempt_id": "retry"}])
    assert summary["attempted"] == 2
    assert summary["observations"][0]["status"] == "incomplete"
    assert len(summary["observations"][0]["attempts"]) == 2


def test_host_exit_failure_cannot_be_promoted_by_review(tmp_path):
    recorded = attempt(tmp_path)
    entry = recorded["captures"][0]
    path = Path(entry["path"])
    captured = json.loads(path.read_text())
    captured["returncode"] = 1
    path.write_text(json.dumps(captured))
    entry["sha256"] = sha(path.read_bytes())
    result = matrix.evaluate(CONTRACTS, recorded)
    assert result["status"] == "incomplete"
    assert "native host process did not succeed" in result["incomplete"]


def test_review_cannot_replace_captured_snapshots(tmp_path):
    recorded = attempt(tmp_path)
    path = Path(recorded["review"])
    reviewed = json.loads(path.read_text())
    reviewed["evidence"]["turns"][0]["before"]["complete"] = False
    path.write_text(json.dumps(reviewed))
    result = matrix.evaluate(CONTRACTS, recorded)
    assert result["status"] == "incomplete"
    assert "reviewed snapshots differ from capture" in result["incomplete"]


def test_capture_platform_must_match_plan(tmp_path):
    recorded = attempt(tmp_path)
    entry = recorded["captures"][0]
    path = Path(entry["path"])
    captured = json.loads(path.read_text())
    captured["platform"] = "wrong-platform"
    path.write_text(json.dumps(captured))
    entry["sha256"] = sha(path.read_bytes())
    assert matrix.evaluate(CONTRACTS, recorded)["status"] == "incomplete"


def test_missing_planned_followup_is_incomplete(tmp_path):
    recorded = attempt(tmp_path)
    cell = next(c for c in matrix.plan(CONTRACTS)["cells"] if c["case"] == "broaden-after-inventory")
    recorded.update(cell)
    evidence, _ = capture(cell["case"])
    evidence["evidence_kind"] = "captured"
    events = [{"type": "thread.started", "thread_id": "session"}]
    for index, event in enumerate(evidence["turns"][0]["events"]):
        event.update(call_id=f"0:{index}", tool="command_execution")
        events.append(
            {"type": "item.completed", "item": {"id": str(index), "type": "command_execution", "command": "inventory"}}
        )
    events.append({"type": "turn.completed"})
    native = Path(recorded["raw_turns"][0]["path"])
    native.write_bytes(b"\n".join(encoded(event) for event in events))
    recorded["raw_turns"][0]["sha256"] = sha(native.read_bytes())
    calls, errors = matrix.native_calls(cell["host"], native.read_bytes(), 0)
    assert not errors
    evidence["raw_trace_sha256"] = sha(encoded({"tool_calls": calls}))
    review_path = Path(recorded["review"])
    review = json.loads(review_path.read_text())
    review.update(evidence=evidence, raw_sha256=[sha(native.read_bytes())])
    review_path.write_text(json.dumps(review))
    entry = recorded["captures"][0]
    path = Path(entry["path"])
    captured = json.loads(path.read_text())
    captured.update(
        cell=cell,
        prompt=cell["prompts"][0],
        raw=recorded["raw_turns"][0],
        before=evidence["turns"][0]["before"],
        after=evidence["turns"][0]["after"],
    )
    path.write_text(json.dumps(captured))
    entry["sha256"] = sha(path.read_bytes())
    recorded["fixture_sha256"] = sha(encoded(captured["before"]))
    result = matrix.evaluate(CONTRACTS, recorded)
    assert result["workflow"]["status"] == "pass"
    assert result["status"] == "incomplete"
    assert result["incomplete"] == ["every planned turn requires native output, a capture record, and review"]


@pytest.mark.parametrize(
    "host,events",
    [
        ("codex", [{"type": "thread.started", "thread_id": "same"}]),
        ("claude-code", [{"type": "system", "session_id": "same"}, {"type": "result", "session_id": "same"}]),
    ],
)
def test_native_continuation_requires_unambiguous_session(host, events):
    assert matrix.native_session(host, b"\n".join(encoded(e) for e in events)) == "same"
    with pytest.raises(ValueError, match="session"):
        matrix.native_session(host, b'{"type":"turn.completed"}')
    other = {key: "different" if value == "same" else value for key, value in events[0].items()}
    with pytest.raises(ValueError, match="session"):
        matrix.native_session(host, b"\n".join(encoded(e) for e in [*events, other]))
