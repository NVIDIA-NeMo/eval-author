# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check synthetic action permissions, not rendered copy or observed host routing."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workflow_scope", ROOT / "tools/check_workflow_scope.py")
assert SPEC and SPEC.loader
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)
CONTRACTS = json.loads(scope.CONTRACTS.read_text())


def capture(case):
    if case.startswith("audit-retained-runs-"):
        return retained_capture(case)
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


def retained_trial(repo, relative, *, agent="src-agent", task="SRC-01", finished=True):
    trial = repo / ".eval-author/jobs" / relative
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "agent_info": {"name": agent},
                "task_name": task,
                "finished_at": "2026-11-10T12:00:00+00:00" if finished else None,
            }
        )
    )
    (trial / "agent/trajectory.json").write_text("TRAJECTORY_CONTENT_MUST_NOT_BE_READ")
    return trial


def retained_capture(case):
    scripts = ROOT / "skills/eval-author/scripts"
    state_path = ".eval-author/.working/journey-state.json"
    with tempfile.TemporaryDirectory(prefix="retained-runs-") as folder:
        repo = Path(folder)
        if case.endswith("found"):
            for job in range(86):
                for trial in range(7):
                    retained_trial(repo, f"job-{job:03}/{trial}", agent="oracle")
            for trial in range(11):
                retained_trial(repo, f"SRC-01-retained/{trial}", agent="src-agent" if trial == 10 else "nop")
            logs = repo / ".eval-author/jobs/job-000/0/logs"
            logs.mkdir()
            for index in range(3000):
                (logs / str(index)).touch()
            os.utime(repo / ".eval-author/jobs/SRC-01-retained", ns=(1, 1))
        elif case.endswith("controls-only"):
            retained_trial(repo, "controls/oracle", agent="oracle")
            retained_trial(repo, "controls/nop", agent="nop")
        else:
            retained_trial(repo, "new/control", agent="oracle")
            retained_trial(repo, "old/agent")
            os.utime(repo / ".eval-author/jobs/old", ns=(1, 1))
        state_cli = [sys.executable, str(scripts / "journey_state.py"), "--repo", str(repo), "--state-only"]
        subprocess.run(
            [*state_cli, "init", "--agent", "src-agent", "--journey", "audit"], capture_output=True, check=True
        )
        initial = {
            "prerequisites": {"data": {"status": "unknown", "source": "No lookup yet"}},
            "scopes": {"audit": {"mode": {"kind": "measured", "source": "Fixture user chose measured audit"}}},
        }
        subprocess.run(
            [*state_cli, "update", "--input", "-", "--expected-revision", "1"],
            input=json.dumps(initial),
            text=True,
            capture_output=True,
            check=True,
        )
        turns, calls, offset = [], [], 0
        for index, contract in enumerate(CONTRACTS["cases"][case]):
            before = scope.snapshot(repo)
            subprocess.run([*state_cli, "show"], capture_output=True, check=True)
            args = [
                sys.executable,
                "-I",
                "-S",
                str(scripts / "retained_runs.py"),
                "--repo",
                str(repo),
                "--agent",
                "src-agent",
                "--task",
                "SRC-01",
                "--offset",
                str(offset),
            ]
            if case.endswith("batched"):
                args += ["--max-jobs", "1"]
            observed = subprocess.run(args, text=True, capture_output=True, check=True)
            result = json.loads(observed.stdout)
            if case.endswith("found"):
                assert result["jobs_total"] == len(result["jobs_checked"]) == 87
                assert result["jobs_checked"][0] == ".eval-author/jobs/SRC-01-retained"
                assert result["complete"] and result["next_offset"] is None
            assert scope.snapshot(repo) == before
            patch = result["state_update"] | {"next_handoff": "eval-author-audit"}
            if not result["candidates"] and result["next_offset"] is None:
                patch.update(
                    next_handoff="eval-author",
                    blockers=[
                        {"id": "data", "phase": "setup", "message": "Retained jobs have no qualifying agent runs"}
                    ],
                )
            subprocess.run(
                [
                    *state_cli,
                    "update",
                    "--input",
                    "-",
                    "--expected-revision",
                    str(json.loads((repo / state_path).read_text())["revision"]),
                ],
                input=json.dumps(patch),
                text=True,
                capture_output=True,
                check=True,
            )
            contents = (repo / state_path).read_text()
            saved = json.loads(contents)
            assert "lifecycle" not in saved["scopes"]["audit"]
            assert saved["prerequisites"]["data"]["status"] == ("verified" if result["candidates"] else "unknown")
            if result["candidates"]:
                assert len(result["candidates"]) == 1
                candidate = result["candidates"][0]
                assert candidate["agent"] == "src-agent" and candidate["task"] == "SRC-01"
                expected = "SRC-01-retained/10" if case.endswith("found") else "old/agent"
                assert candidate["trace"] == f".eval-author/jobs/{expected}/agent/trajectory.json"
                assert candidate["trace"] in saved["prerequisites"]["data"]["source"]
                handoff = "select_audit_trace_source"
            else:
                handoff = (
                    "continue_retained_run_lookup"
                    if result["next_offset"] is not None
                    else "audit_prerequisite_blocker"
                )
            events = []
            for name, action in (
                ("state", "read_saved_state"),
                ("lookup", "read_repository"),
                ("save", "write_artifact"),
            ):
                call_id = f"{index}-{name}"
                calls.append(
                    {
                        "id": call_id,
                        "tool": name,
                        "turn": index,
                        **({"argv": args, "stdout": observed.stdout} if name == "lookup" else {}),
                    }
                )
                events.append(
                    {
                        "call_id": call_id,
                        "tool": name,
                        "action": action,
                        "write_paths": [state_path] if name == "save" else [],
                    }
                )
            turns.append(
                {
                    "prompt": contract["prompts"][0],
                    "context": contract["context"],
                    "flow": "eval-author-audit",
                    "handoff": handoff,
                    "events": events,
                    "before": before,
                    "after": scope.snapshot(repo),
                    "artifact_contents": {state_path: contents},
                }
            )
            offset = result["next_offset"]
        raw = json.dumps({"tool_calls": calls}).encode()
        return {
            "schema_version": 1,
            "case": case,
            "evidence_kind": "synthetic",
            "contracts_sha256": scope.digest(json.dumps(CONTRACTS, sort_keys=True, separators=(",", ":")).encode()),
            "raw_trace_sha256": scope.digest(raw),
            "turns": turns,
        }, raw


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
    "mutation",
    ["no_lookup", "wrong_status", "missing_contents", "unbound_contents", "wrong_handoff", "wrong_candidate"],
)
def test_retained_lookup_contract_rejects_missing_evidence_or_wrong_state(mutation):
    evidence, raw = capture("audit-retained-runs-found")
    turn = evidence["turns"][0]
    path = ".eval-author/.working/journey-state.json"
    if mutation == "no_lookup":
        turn["events"] = [event for event in turn["events"] if event["action"] != "read_repository"]
    elif mutation == "missing_contents":
        del turn["artifact_contents"][path]
    else:
        state = json.loads(turn["artifact_contents"][path])
        if mutation == "wrong_handoff":
            state["next_handoff"] = "eval-author-first-eval"
        elif mutation == "wrong_candidate":
            state["prerequisites"]["data"]["source"] = ".eval-author/jobs/unrelated/control/agent/trajectory.json"
        else:
            state["prerequisites"]["data"]["status"] = "confirmed"
        body = json.dumps(state)
        turn["artifact_contents"][path] = body
        if mutation != "unbound_contents":
            turn["after"]["entries"][path]["sha256"] = scope.digest(body.encode())
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["status"] != "pass"
    if mutation == "no_lookup":
        assert "turn[0]: required action missing read_repository" in report["violations"]


def retained_module():
    spec = importlib.util.spec_from_file_location("retained_runs", ROOT / "skills/eval-author/scripts/retained_runs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lookup_reads_only_metadata_and_prioritizes_known_tasks(tmp_path, monkeypatch):
    module = retained_module()
    retained_trial(tmp_path, "newest/1")
    retained_trial(tmp_path, "SRC-01-oldest/1")
    os.utime(tmp_path / ".eval-author/jobs/SRC-01-oldest", ns=(1, 1))
    original = Path.open
    original_os_open = os.open

    def metadata_only(path, *args, **kwargs):
        assert path.name == "result.json", f"Unexpected file read: {path}"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", metadata_only)

    def metadata_fd_only(path, *args, **kwargs):
        assert Path(path).name == "result.json", f"Unexpected file read: {path}"
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", metadata_fd_only)
    result = module.lookup(tmp_path, agent="src-agent", tasks=("SRC-01",), max_jobs=1)
    assert result["jobs_checked"] == [".eval-author/jobs/SRC-01-oldest"]
    assert result["state_update"]["prerequisites"]["data"]["status"] == "verified"
    assert result["next_offset"] == 1 and not result["complete"]


def test_unknown_agent_inventory_cannot_verify_availability(tmp_path):
    module = retained_module()
    retained_trial(tmp_path, "agents/run")
    retained_trial(tmp_path, "controls/run", agent="oracle")
    result = module.lookup(tmp_path)
    assert [item["agent"] for item in result["candidates"]] == ["src-agent"]
    assert result["state_update"]["prerequisites"]["data"]["status"] == "unknown"


def test_default_job_budget_continues_past_200_controls(tmp_path):
    module = retained_module()
    for index in range(201):
        retained_trial(tmp_path, f"job-{index:03}/trial", agent="src-agent" if index == 200 else "oracle")
        os.utime(tmp_path / f".eval-author/jobs/job-{index:03}", ns=(1, 1))
    first = module.lookup(tmp_path, agent="src-agent")
    assert len(first["jobs_checked"]) == 200
    assert first["next_offset"] == 200 and not first["candidates"]
    assert first["state_update"]["prerequisites"]["data"]["status"] == "unknown"
    second = module.lookup(tmp_path, agent="src-agent", offset=first["next_offset"])
    assert second["complete"] and second["next_offset"] is None
    assert second["candidates"][0]["trace"] == ".eval-author/jobs/job-200/trial/agent/trajectory.json"
    assert second["state_update"]["prerequisites"]["data"]["status"] == "verified"


@pytest.mark.parametrize(
    "problem",
    [
        "incomplete",
        "other_agent",
        "other_task",
        "bad_json",
        "oversize",
        "missing_trace",
        "metadata_link",
        "trace_link",
        "agent_link",
        "job_link",
        "root_link",
    ],
)
def test_lookup_does_not_verify_invalid_or_unrelated_runs(tmp_path, problem):
    module = retained_module()
    repo = tmp_path / "repo"
    trial = retained_trial(
        repo,
        "job/trial",
        agent="other" if problem == "other_agent" else "src-agent",
        task="OTHER" if problem == "other_task" else "SRC-01",
        finished=problem != "incomplete",
    )
    metadata, trace = trial / "result.json", trial / "agent/trajectory.json"
    outside = tmp_path / "outside.json"
    outside.write_text(metadata.read_text())
    if problem == "bad_json":
        metadata.write_text("{broken")
    elif problem == "oversize":
        metadata.write_bytes(b" " * (module.MAX_METADATA_BYTES + 1))
    elif problem == "missing_trace":
        trace.unlink()
    elif problem in {"metadata_link", "trace_link"}:
        target = metadata if problem == "metadata_link" else trace
        target.unlink()
        target.symlink_to(outside)
    elif problem in {"agent_link", "job_link", "root_link"}:
        target = (
            trial / "agent"
            if problem == "agent_link"
            else repo / ".eval-author/jobs/job"
            if problem == "job_link"
            else repo / ".eval-author/jobs"
        )
        moved = tmp_path / "outside-directory"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
    result = module.lookup(repo, agent="src-agent", tasks=("SRC-01",))
    assert result["candidates"] == []
    assert result["state_update"]["prerequisites"]["data"]["status"] == "unknown"


def test_missing_or_unreadable_jobs_do_not_claim_absence(tmp_path, monkeypatch):
    module = retained_module()
    assert module.lookup(tmp_path, agent="src-agent")["state_update"]["prerequisites"]["data"]["status"] == "unknown"

    def denied(_path):
        raise PermissionError("fixture denied")

    monkeypatch.setattr(module, "directories", denied)
    result = module.lookup(tmp_path, agent="src-agent")
    assert not result["complete"] and result["errors"]
    assert result["state_update"]["prerequisites"]["data"]["status"] == "unknown"


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


@pytest.mark.parametrize("case", ["audit-known-repo-no-state", "audit-invalid-saved-state"])
@pytest.mark.parametrize(
    "action", ["read_repository", "read_trace", "inventory", "runtime_probe", "validate", "delegate", "write_artifact"]
)
def test_audit_route_classification_allows_only_saved_metadata(case, action):
    evidence, raw = capture(case)
    writes = [".eval-author/.working/journey-state.json"] if action == "write_artifact" else []
    raw = add_call(evidence, raw, action=action, writes=writes)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[0]: forbidden action {action}" in report["violations"]
    if writes:
        assert f"turn[0]: forbidden write attempt {writes[0]}" in report["violations"]


@pytest.mark.parametrize("case", ["generic-help", "explicit-audit", "first-eval-opening", "explicit-expansion"])
def test_saved_metadata_exception_needs_a_known_repository_and_audit_request(case):
    evidence, raw = capture(case)
    raw = add_call(evidence, raw, action="read_saved_state")
    report = scope.check(CONTRACTS, evidence, raw)
    assert "turn[0]: forbidden action read_saved_state" in report["violations"]


def test_saved_state_lookup_cannot_write_even_when_later_audit_work_may_write():
    evidence, raw = capture("audit-returning-new-chat")
    raw = add_call(evidence, raw, action="read_saved_state", writes=[".eval-author/.working/journey-state.json"])
    report = scope.check(CONTRACTS, evidence, raw)
    assert "turn[0]: saved-state lookup must not write" in report["violations"]


def test_returning_audit_scope_reaches_checks_with_selected_evidence_context():
    evidence, raw = capture("audit-returning-new-chat")
    for action in ("read_repository", "validate", "read_trace", "propose"):
        raw = add_call(evidence, raw, action=action)
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/audit-coverage-report.md"])
    report = scope.check(CONTRACTS, evidence, raw)
    assert report["status"] == "pass"
    assert report["host_acceptance"] == "not_measured"
    evidence["turns"][0]["handoff"] = "shared_opening"
    assert "turn[0]: wrong handoff" in scope.check(CONTRACTS, evidence, raw)["violations"]


@pytest.mark.parametrize("action", ["read_saved_state", "render_conversation"])
def test_separate_audit_request_requires_metadata_and_rendering_actions(action):
    evidence, raw = capture("audit-returning-new-chat")
    events = evidence["turns"][0]["events"]
    omitted = next(event for event in events if event["action"] == action)
    events.remove(omitted)
    parsed = json.loads(raw)
    parsed["tool_calls"] = [call for call in parsed["tool_calls"] if call["id"] != omitted["call_id"]]
    raw = json.dumps(parsed).encode()
    evidence["raw_trace_sha256"] = scope.digest(raw)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[0]: required action missing {action}" in report["violations"]
    assert report["host_acceptance"] == "not_measured"


@pytest.mark.parametrize(
    "case,action",
    [
        ("audit-returning-new-chat", "read_repository"),
        ("audit-returning-new-chat", "read_trace"),
        ("audit-returning-new-chat", "validate"),
        ("audit-returning-new-chat", "propose"),
        ("audit-returning-new-chat", "write_artifact"),
        ("audit-returning-new-chat", "render_conversation"),
        ("audit-known-repo-no-state", "render_conversation"),
        ("audit-invalid-saved-state", "render_conversation"),
    ],
)
def test_audit_cannot_render_or_inspect_before_route_classification(case, action):
    evidence, raw = capture(case)
    writes = [".eval-author/.working/journey-state.json"] if action == "write_artifact" else []
    raw = add_call(evidence, raw, action=action, writes=writes)
    events = evidence["turns"][0]["events"]
    events.insert(0, events.pop())
    parsed = json.loads(raw)
    parsed["tool_calls"].insert(0, parsed["tool_calls"].pop())
    raw = json.dumps(parsed).encode()
    evidence["raw_trace_sha256"] = scope.digest(raw)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[0]: saved-state lookup must precede {action}" in report["violations"]


@pytest.mark.parametrize("case", ["audit-known-repo-no-state", "audit-invalid-saved-state"])
def test_audit_can_render_after_route_classification(case):
    evidence, raw = capture(case)
    raw = add_call(evidence, raw, action="render_conversation")
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"


@pytest.mark.parametrize(
    "case",
    [
        "audit-returning-traces-unresolved",
        "audit-returning-source-unselected",
        "audit-returning-ethos-changed",
    ],
)
@pytest.mark.parametrize("action", ["read_trace", "propose", "execute", "scaffold", "install", "delegate"])
def test_returning_audit_rendering_does_not_bypass_unresolved_inputs(case, action):
    evidence, raw = capture(case)
    raw = add_call(evidence, raw, action="render_conversation")
    writes = [".eval-author/tasks/new-case/task.toml"] if action == "scaffold" else []
    raw = add_call(evidence, raw, action=action, writes=writes)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[0]: forbidden action {action}" in report["violations"]


@pytest.mark.parametrize("action", ["read_repository", "inventory", "validate", "runtime_probe"])
def test_unresolved_audit_traces_cannot_trigger_artifact_or_execution_checks(action):
    evidence, raw = capture("audit-returning-traces-unresolved")
    raw = add_call(evidence, raw, action=action)
    assert f"turn[0]: forbidden action {action}" in scope.check(CONTRACTS, evidence, raw)["violations"]


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


def test_ethos_completion_source_answer_allows_design_proposal():
    evidence, raw = capture("ethos-completion-pause")
    raw = add_call(evidence, raw, action="delegate", turn=1)
    raw = add_call(evidence, raw, action="propose", turn=1)
    assert scope.check(CONTRACTS, evidence, raw)["status"] == "pass"


@pytest.mark.parametrize("action", ["scaffold", "execute"])
def test_ethos_source_answer_does_not_authorize_building_or_execution(action):
    evidence, raw = capture("ethos-completion-pause")
    raw = add_call(evidence, raw, action=action, turn=1)
    report = scope.check(CONTRACTS, evidence, raw)
    assert f"turn[1]: forbidden action {action}" in report["violations"]


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
    raw = add_call(evidence, raw, action="write_artifact", writes=[".eval-author/audit-coverage-report.md"])
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
