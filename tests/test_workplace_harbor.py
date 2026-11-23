# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1] / "skills/eval-author-trace-environment/examples/workplace"
spec = importlib.util.spec_from_file_location("workplace_harbor", ROOT / "run_harbor.py")
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


REPLY = {
    "email_id": "4",
    "inbox/outbox": "outbox",
    "sender/recipient": "alex@example.test",
    "subject": "RE: Schedule confirmation",
    "sent_datetime": "2023-11-30 23:59:00",
    "body": "Confirmed for Tuesday at 10:00.",
}
NEGATIVE_ADDITIONS = {
    "wrong-body": [dict(REPLY, body="Confirmed for Wednesday at 11:00.")],
    "wrong-id": [dict(REPLY, **{"sender/recipient": "billing@example.test", "subject": "RE: November invoice"})],
    "extra-reply": [
        REPLY,
        dict(
            REPLY,
            email_id="5",
            body="Received the invoice.",
            **{"sender/recipient": "billing@example.test", "subject": "RE: November invoice"},
        ),
    ],
    "duplicate-reply": [REPLY, dict(REPLY, email_id="5")],
}


def write_state(path, rows):
    state_path = path.parent / "artifacts/export/state.json"
    snapshot = json.loads(state_path.read_text())
    snapshot["state"] = rows
    state_path.write_text(json.dumps(snapshot))


@pytest.fixture
def native(tmp_path):
    seed = json.loads((ROOT / "task/seed.json").read_text())
    trial = tmp_path / "trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "agent/oracle.txt").write_text("")
    (trial / "artifacts/export").mkdir(parents=True)
    snapshot = {
        "access_mode": "stateful_simulation",
        "initial_state_basis": "reconstructed",
        "state_basis": "observed",
        "initial_state": seed,
        "state": seed + NEGATIVE_ADDITIONS["wrong-body"],
        "closed": True,
    }
    (trial / "artifacts/export/state.json").write_text(json.dumps(snapshot))
    result = {
        "id": str(uuid4()),
        "trial_name": "trial",
        "config": {"job_id": str(uuid4()), "agent": {"name": "oracle"}},
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": 0.0}},
    }
    path = trial / "result.json"
    path.write_text(json.dumps(result))
    return path, result, seed


@pytest.mark.parametrize("control", list(NEGATIVE_ADDITIONS))
@pytest.mark.parametrize(
    "mutation", ["unchanged", "other-negative", "arbitrary-body", "original-changed", "original-missing"]
)
def test_negative_control_requires_exact_mutation(native, control, mutation):
    path, _, seed = native
    rows = json.loads(json.dumps(seed + NEGATIVE_ADDITIONS[control]))
    if mutation == "unchanged":
        rows = seed
    elif mutation == "other-negative":
        other = "wrong-id" if control == "wrong-body" else "wrong-body"
        rows = seed + NEGATIVE_ADDITIONS[other]
    elif mutation == "arbitrary-body":
        rows[-1]["body"] = "Some other incorrect body."
    elif mutation == "original-changed":
        rows[0]["body"] = "Changed original."
    else:
        rows.pop(0)
    write_state(path, rows)
    record = collector.summarize_result([path], control, 0, seed)
    assert record["passed"] is False
    assert "Control final state" in " ".join(record["errors"])


@pytest.mark.parametrize("control", list(NEGATIVE_ADDITIONS))
def test_negative_control_accepts_intended_mutation(native, control):
    path, _, seed = native
    write_state(path, seed + NEGATIVE_ADDITIONS[control])
    assert collector.summarize_result([path], control, 0, seed)["passed"] is True


@pytest.mark.parametrize("payload", ["{", "null", "[]", "{}", '{"verifier_result":{"rewards":null}}'])
def test_result_summary_rejects_malformed_result(native, payload):
    path, _, seed = native
    path.write_text(payload)
    record = collector.summarize_result([path], "wrong-body", 0, seed)
    assert record["passed"] is False
    assert record["errors"]


@pytest.mark.parametrize("reward", [None, False, "0", float("nan")])
def test_result_summary_rejects_invalid_reward(native, reward):
    path, result, seed = native
    result["verifier_result"]["rewards"]["reward"] = reward
    path.write_text(json.dumps(result))
    assert collector.summarize_result([path], "wrong-body", 0, seed)["passed"] is False


def test_result_summary_missing_or_multiple_results(native):
    path, _, seed = native
    for paths in ([], [path, path]):
        record = collector.summarize_result(paths, "wrong-body", 0, seed)
        assert record["passed"] is False
        assert record["native_result_count"] == len(paths)


def test_oracle_failure_cannot_pass_negative_control(native):
    path, _, seed = native
    assert collector.summarize_result([path], "wrong-body", 0, seed)["passed"] is True
    for exit_code in ("17", "0", "", "invalid"):
        (path.parent / "agent/exit-code.txt").write_text(exit_code)
        assert collector.summarize_result([path], "wrong-body", 0, seed)["passed"] is False


@pytest.mark.parametrize("payload", ["{", "null", "[]", '{"closed":true}', '{"state":null}'])
def test_malformed_state_is_not_a_negative_control_pass(native, payload):
    path, _, seed = native
    (path.parent / "artifacts/export/state.json").write_text(payload)
    assert collector.summarize_result([path], "wrong-body", 0, seed)["passed"] is False


def test_noop_requires_exact_seed(native):
    path, result, seed = native
    result["config"]["agent"]["name"] = "nop"
    path.write_text(json.dumps(result))
    write_state(path, seed)
    assert collector.summarize_result([path], "noop", 0, seed)["passed"] is True
    state_path = path.parent / "artifacts/export/state.json"
    snapshot = json.loads(state_path.read_text())
    snapshot["state"] = list(reversed(seed))
    state_path.write_text(json.dumps(snapshot))
    assert collector.summarize_result([path], "noop", 0, seed)["passed"] is False


@pytest.mark.parametrize("identity", ["trial_id", "job_id", "trial_name"])
def test_panel_rejects_identity_collision(native, identity):
    path, _, seed = native
    first = collector.summarize_result([path], "wrong-body", 0, seed)
    second = dict(first, control="wrong-id", trial_id=str(uuid4()), job_id=str(uuid4()), trial_name="other")
    second[identity] = first[identity]
    report = collector.summarize_controls([first, second], ["wrong-body", "wrong-id"])
    assert report["complete"] is True
    assert report["passed"] is False
    assert report["errors"]


def test_panel_requires_reference_repeat_state_equality(native):
    path, _, seed = native
    base = collector.summarize_result([path], "wrong-body", 0, seed)
    first = dict(base, control="reference", expected=1, reward=1)
    second = dict(first, control="reference-repeat", trial_id=str(uuid4()), job_id=str(uuid4()), trial_name="repeat")
    assert collector.summarize_controls([first, second], ["reference", "reference-repeat"])["passed"] is True
    second["state_sha256"] = "different"
    assert collector.summarize_controls([first, second], ["reference", "reference-repeat"])["passed"] is False
    assert collector.summarize_controls([second], ["reference-repeat"])["passed"] is False


@pytest.mark.parametrize("selection", [[], ["unknown"], ["noop", "noop"]])
def test_run_rejects_invalid_selection_before_writes(tmp_path, selection):
    output = tmp_path / "output"
    with pytest.raises(ValueError):
        collector.run(output, tmp_path, selection)
    assert not output.exists()


@pytest.mark.parametrize("failure", [OSError("build failed"), collector.subprocess.TimeoutExpired("build", 600)])
def test_run_persists_failure_summary(tmp_path, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(collector.subprocess, "run", fail)
    output = tmp_path / "output"
    report = collector.run(output, tmp_path, ["noop"])
    assert report["passed"] is False
    assert report["controls"][0]["errors"]
    assert json.loads((output / "summary.json").read_text()) == report


def test_collector_change_after_build_prevents_native_launch(tmp_path, monkeypatch):
    import shutil

    source = tmp_path / "source"
    source.mkdir()
    for name in ("task", "runtime"):
        shutil.copytree(ROOT / name, source / name)
    shutil.copy2(ROOT / "run_harbor.py", source / "run_harbor.py")
    monkeypatch.setattr(collector, "ROOT", source)
    calls = []

    def build_only(command, **kwargs):
        calls.append(command)
        frozen = Path(command[1]).parents[1]
        pending = json.loads((tmp_path / "output/summary.json").read_text())
        assert pending["controls"][0]["source_hashes_before"] == collector.source_hashes(source)
        assert pending["passed"] is False
        assert frozen != source
        assert (frozen / "run_harbor.py").read_bytes() == (source / "run_harbor.py").read_bytes()
        with (source / "run_harbor.py").open("a") as stream:
            stream.write("\n# changed\n")
        return collector.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(collector.subprocess, "run", build_only)
    report = collector.run(tmp_path / "output", tmp_path, ["noop"])
    assert len(calls) == 1
    assert report["passed"] is False
    record = report["controls"][0]
    assert record["source_hashes_before"] != collector.source_hashes(source)
    assert record["source_stable"] is False
    assert "Source changed" in " ".join(record["errors"])
