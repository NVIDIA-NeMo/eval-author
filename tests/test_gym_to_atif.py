# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic Gym records shaped from upstream 676cf1f; no live agent or customer data."""

import copy
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.models.trajectories import Trajectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/gym-to-atif/scripts"
EVAL_FILES = ROOT / "skills/gym-to-atif/evals/files"
TRACE_ENVIRONMENT_SCRIPTS = ROOT / "skills/eval-author-trace-environment/scripts"
IMAGE = "data:image/png;base64,iVBORw0KGgo="  # schema fixture, not a decoded/validated image
RECEIPT_FIELDS = {
    "schema",
    "basis",
    "source_kind",
    "gym_sha256",
    "atif_sha256",
    "source_atif_sha256",
    "atif_bytes_preserved",
    "lossless_round_trip_claimed",
    "losses",
    "media_fetched",
    "selected_line",
    "loader_version",
}


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import gym_to_atif

    return gym_to_atif


def rollout():
    return {
        "responses_create_params": {
            "input": [{"role": "user", "content": "Read the public fixture."}],
            "tools": [{"type": "function", "name": "read", "parameters": {"type": "object"}}],
        },
        "response": {
            "object": "response",
            "id": "response-fixture",
            "model": "example-model",
            "status": "completed",
            "output": [
                {"type": "function_call", "call_id": "call-1", "name": "read", "arguments": '{"path":"fixture.json"}'},
                {"type": "function_call_output", "call_id": "call-1", "output": "recorded result"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]},
            ],
            "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
        },
        "reward": 0.5,
    }


def convert(adapter, tmp_path, source, source_atif=None, **kwargs):
    """Run the real write path: retain the record, convert that file, write ATIF and receipt."""
    output = tmp_path / f"converted-{len(list(tmp_path.iterdir()))}"
    receipt = adapter.write_output(
        output, json.dumps(source).encode(), lambda path: adapter.normalize(path, source_atif, **kwargs)
    )
    encoded = (output / "trace.atif.json").read_bytes()
    canonical = json.loads(encoded)
    if source_atif is None:
        Trajectory.model_validate(canonical)  # provider-owned ATIF validation, not a handwritten schema
    return canonical, receipt, encoded


def cli(*args):
    completed = subprocess.run([sys.executable, str(SCRIPTS / "gym_to_atif.py"), *args], capture_output=True, text=True)
    assert completed.stdout, completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def tool_steps(canonical):
    return [step for step in canonical["steps"] if step.get("tool_calls")]


def test_compass_projection_retains_pairing_and_does_not_invent_agent_or_proof(adapter, tmp_path):
    source = rollout()
    canonical, receipt, encoded = convert(adapter, tmp_path, source)
    assert canonical["steps"][0] == {
        "step_id": 1,
        "source": "user",
        "message": "Read the public fixture.",
        "is_copied_context": False,
    }
    [call] = tool_steps(canonical)
    assert call["tool_calls"] == [
        {"tool_call_id": "call-1", "function_name": "read", "arguments": {"path": "fixture.json"}}
    ]
    assert call["observation"]["results"] == [{"source_call_id": "call-1", "content": "recorded result"}]
    assert canonical["steps"][-1]["message"] == "Done."
    assert canonical["agent"]["name"] == "unknown-gym-agent" and canonical["agent"]["version"] == "unknown"
    assert canonical["agent"]["tool_definitions"] == source["responses_create_params"]["tools"]
    assert canonical["extra"]["compass"]["exporter"] == "trace_ingest.exporters.atif@1"
    assert "final_metrics" not in canonical
    assert all("timestamp" not in step for step in canonical["steps"])
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["schema"] == "nemo.eval_author.gym_to_atif.v2"
    assert receipt["basis"] == "gym_projection" and receipt["source_kind"] == "gym"
    assert receipt["losses"] == canonical["extra"]["compass"]["loss_codes"]
    assert receipt["loader_version"] == "0.2.0"
    assert receipt["lossless_round_trip_claimed"] is False and receipt["media_fetched"] is False
    assert receipt["atif_bytes_preserved"] is False and receipt["source_atif_sha256"] is None
    assert receipt["selected_line"] is None
    assert receipt["atif_sha256"] == adapter.digest(encoded)
    assert receipt["gym_sha256"] == adapter.digest(json.dumps(source).encode())
    again, again_receipt, _ = convert(adapter, tmp_path, source)
    assert (again, again_receipt) == (canonical, receipt)


def test_request_instructions_and_developer_role_become_system_steps(adapter, tmp_path):
    source = rollout()
    source["responses_create_params"]["instructions"] = "Recorded system instructions"
    source["responses_create_params"]["input"].insert(0, {"role": "developer", "content": "Recorded developer rules"})
    canonical, _, _ = convert(adapter, tmp_path, source)
    assert [(step["source"], step["message"]) for step in canonical["steps"][:3]] == [
        ("system", "Recorded system instructions"),
        ("system", "Recorded developer rules"),
        ("user", "Read the public fixture."),
    ]


def test_each_call_keeps_its_own_result(adapter, tmp_path):
    source = rollout()
    output = source["response"]["output"]
    output.insert(1, {"type": "function_call", "call_id": "call-2", "name": "read", "arguments": "{}"})
    output.insert(2, {"type": "function_call_output", "call_id": "call-2", "output": "Second result arrived first"})
    canonical, _, _ = convert(adapter, tmp_path, source)
    pairs = [
        (step["tool_calls"][0]["tool_call_id"], step["observation"]["results"][0]["source_call_id"])
        for step in tool_steps(canonical)
    ]
    assert pairs == [("call-1", "call-1"), ("call-2", "call-2")]


def test_full_output_exact_prefix_is_not_duplicated(adapter, tmp_path):
    source = rollout()
    prompt = {**source["responses_create_params"]["input"][0], "type": "message", "phase": None}
    source["response"]["output"].insert(0, prompt)
    canonical, _, _ = convert(adapter, tmp_path, source)
    assert sum(step["source"] == "user" for step in canonical["steps"]) == 1


def test_ambiguous_history_is_rejected(adapter, tmp_path):
    source = rollout()
    source["response"]["output"].insert(0, {"type": "message", "role": "user", "content": "Different recorded prompt"})
    with pytest.raises(adapter.ConversionError, match="prompt history"):
        convert(adapter, tmp_path, source)
    assert list(tmp_path.iterdir()) == []


def test_standalone_response_without_request_input_is_rejected(adapter, tmp_path):
    source = rollout()
    with pytest.raises(adapter.ConversionError, match="no recoverable user instruction"):
        convert(adapter, tmp_path, source["response"])
    source["response"]["output"].insert(0, {"type": "message", "role": "user", "content": "Recorded request"})
    with pytest.raises(adapter.ConversionError, match="no recoverable user instruction"):
        convert(adapter, tmp_path, source["response"])
    assert list(tmp_path.iterdir()) == []


def test_typed_images_become_atif_image_parts_and_untyped_images_are_omitted(adapter, tmp_path):
    source = rollout()
    source["responses_create_params"]["input"][0]["content"] = [
        {"type": "input_text", "text": "Describe this."},
        {"type": "input_image", "image_url": IMAGE},
    ]
    canonical, receipt, _ = convert(adapter, tmp_path, source)
    assert canonical["steps"][0]["message"] == [
        {"type": "text", "text": "Describe this."},
        {"type": "image", "source": {"media_type": "image/png", "path": IMAGE}},
    ]
    assert receipt["media_fetched"] is False
    source["responses_create_params"]["input"][0]["content"] = [
        {"type": "input_image", "image_url": "https://example.org/opaque"}
    ]
    canonical, receipt, _ = convert(adapter, tmp_path, source)
    assert canonical["steps"][0]["message"] == "[image omitted: media type not recorded]"
    assert "image_without_media_type_omitted" in receipt["losses"]
    assert "example.org" not in json.dumps(canonical["steps"])


def test_agent_steps_keep_assistant_text_and_drop_reasoning(adapter, tmp_path):
    source = rollout()
    source["response"]["output"].insert(
        0,
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Recorded summary"}],
            "encrypted_content": "OPAQUE-CONTENT",
        },
    )
    canonical, receipt, _ = convert(adapter, tmp_path, source)
    assert "reasoning_not_exported" in receipt["losses"]
    assert "OPAQUE-CONTENT" not in json.dumps(canonical)
    assert all("reasoning_content" not in step for step in canonical["steps"])
    llm = next(step for step in canonical["steps"] if step.get("llm_call_count") == 1)
    assert llm["message"] == "Done." and llm["model_name"] == "example-model"
    assert "recorded result" not in llm["message"]


def test_harbor_source_atif_wins_byte_for_byte_and_never_follows_embedded_paths(adapter, tmp_path):
    source = rollout()
    source["atif_conversion"] = {
        "source_trajectory_paths": ["/not-authorized/do-not-read.json"],
        "trajectories": [{"session_id": "source-session"}],
    }
    with pytest.raises(adapter.ConversionError, match="--source-atif"):
        convert(adapter, tmp_path, source)
    atif = {
        "schema_version": "ATIF-v1.6",
        "session_id": "source-session",
        "agent": {"name": "real-agent", "version": "1.0"},
        "steps": [{"step_id": 1, "source": "user", "message": "Original request"}],
    }
    exact = json.dumps(atif, indent=4).encode() + b"\n\n"
    _, receipt, result = convert(adapter, tmp_path, source, exact)
    assert result == exact
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["basis"] == "provided_atif" and receipt["source_kind"] == "atif"
    assert receipt["atif_bytes_preserved"] is True and receipt["source_atif_sha256"] == adapter.digest(exact)
    assert receipt["losses"] == [] and receipt["loader_version"] is None
    wrong = copy.deepcopy(atif)
    wrong["session_id"] = "wrong-session"
    with pytest.raises(adapter.ConversionError, match="does not match"):
        convert(adapter, tmp_path, source, json.dumps(wrong).encode())


def test_bridge_projection_requires_explicit_opt_in(adapter, tmp_path):
    source = rollout()
    source["atif_conversion"] = {"source_trajectory_paths": ["/unread/source.json"]}
    with pytest.raises(adapter.ConversionError, match="--allow-projection"):
        convert(adapter, tmp_path, source)
    _, receipt, _ = convert(adapter, tmp_path, source, allow_projection=True)
    assert receipt["basis"] == "gym_projection"


def test_multistep_bridge_requires_explicit_original_not_first_output_last_reward(adapter, tmp_path):
    source = rollout()
    source["atif_conversion"] = {"trajectories": [{"session_id": "one"}, {"session_id": "two"}]}
    with pytest.raises(adapter.ConversionError, match="multi-step"):
        convert(adapter, tmp_path, source, allow_projection=True)


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("unknown-call", "invalid Gym rollout"),
        ("duplicate-call", "invalid Gym rollout"),
        ("unknown-type", "unsupported Gym conversation item"),
        ("remote-context", "remote conversation state"),
        ("namespace", "namespaced Gym functions"),
        ("not-gym", "rollout envelope or standalone Responses object"),
    ],
)
def test_unsupported_or_incomplete_mappings_fail_closed(adapter, tmp_path, mutation, message):
    source = rollout()
    output = source["response"]["output"]
    if mutation == "unknown-call":
        output[1]["call_id"] = "missing"
    elif mutation == "duplicate-call":
        output.insert(1, copy.deepcopy(output[0]))
    elif mutation == "unknown-type":
        output[0]["type"] = "computer_call"
    elif mutation == "remote-context":
        source["responses_create_params"]["previous_response_id"] = "not-fetched"
    elif mutation == "namespace":
        output[0]["namespace"] = "scope"
    else:
        source = {"ng_trajectory": {"schema_version": "1.0"}}
    with pytest.raises(adapter.ConversionError, match=message) as error:
        convert(adapter, tmp_path, source)
    assert str(tmp_path) not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_no_result_is_invented_for_failed_rollout(adapter, tmp_path):
    source = rollout()
    source["response"]["output"] = source["response"]["output"][:1]
    source["response"]["status"] = "failed"
    canonical, receipt, _ = convert(adapter, tmp_path, source)
    [call] = tool_steps(canonical)
    assert "observation" not in call
    assert "tool_result_missing" in receipt["losses"]


@pytest.mark.parametrize(("name", "row"), [("source.json", None), ("ambiguous.jsonl", 1)])
def test_eval_fixtures_convert_to_prepare_compatible_atif(tmp_path, monkeypatch, name, row):
    out = tmp_path / "converted"
    code, result = cli(
        "--input", str(EVAL_FILES / name), *(["--row", str(row)] if row else []), "--output-dir", str(out)
    )
    assert code == 0 and result["basis"] == "gym_projection"
    receipt = json.loads((out / "conversion.json").read_text())
    assert receipt["selected_line"] == row
    monkeypatch.syspath_prepend(str(TRACE_ENVIRONMENT_SCRIPTS))
    import trace_environment

    trace_environment._validate_trajectory(json.loads((out / "trace.atif.json").read_text()))


def test_jsonl_selection_private_output_and_no_overwrite(tmp_path):
    first, second = rollout(), rollout()
    second["responses_create_params"]["input"] = "Second independent episode"
    lines = [json.dumps(first).encode() + b"\n", json.dumps(second).encode() + b"\n"]
    source = tmp_path / "source.jsonl"
    source.write_bytes(b"".join(lines))
    out = tmp_path / "private-normalized"
    code, _ = cli("--input", str(source), "--output-dir", str(out))
    assert code == 2 and not out.exists()
    code, result = cli("--input", str(source), "--row", "2", "--output-dir", str(out))
    assert code == 0 and result["source_kind"] == "gym"
    assert (out / "source.gym.json").read_bytes() == lines[1]
    assert json.loads((out / "trace.atif.json").read_text())["steps"][0]["message"] == "Second independent episode"
    assert json.loads((out / "conversion.json").read_text())["selected_line"] == 2
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in out.iterdir())
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    code, _ = cli("--input", str(source), "--row", "1", "--output-dir", str(out))
    assert code == 2
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


@pytest.mark.parametrize("image_only", [False, True])
def test_prepare_accepts_gym_provenance_and_blocks_image_only_instruction(tmp_path, image_only):
    record = rollout()
    if image_only:
        record["responses_create_params"]["input"][0]["content"] = [{"type": "input_image", "image_url": IMAGE}]
    source = tmp_path / "rollout.json"
    source.write_text(json.dumps(record))
    out = tmp_path / "converted"
    assert cli("--input", str(source), "--output-dir", str(out))[0] == 0
    helper = TRACE_ENVIRONMENT_SCRIPTS / "trace_environment.py"
    init = subprocess.run(
        [sys.executable, str(helper), "init", "--root", str(tmp_path / ".eval-author" / "tasks"), "--task-id", "gym"],
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stdout
    task = Path(json.loads(init.stdout)["task_dir"])
    prepared = subprocess.run(
        [
            sys.executable,
            str(helper),
            "prepare",
            "--task-dir",
            str(task),
            "--atif",
            str(out / "trace.atif.json"),
            "--source-kind",
            "gym",
        ],
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0, prepared.stdout
    assert json.loads(prepared.stdout)["blocking_reason_count"] == int(image_only)
    summary = json.loads((task / "summary.json").read_text())
    assert summary["source"]["kind"] == "gym"
    if image_only:
        assert "image_only_user_instruction:step-1" in summary["privacy"]["blocking_reasons"]
        assert IMAGE not in (task / "safe/trace.atif.json").read_text()
    checked = subprocess.run(
        [sys.executable, str(helper), "check", "--task-dir", str(task)], capture_output=True, text=True
    )
    assert checked.returncode == 0, checked.stdout
    assert json.loads(checked.stdout)["valid"] is True


def test_batch_gym_provenance_and_resume(tmp_path):
    raw = tmp_path / "rollout.json"
    raw.write_text(json.dumps(rollout()))
    converted = tmp_path / "converted"
    assert cli("--input", str(raw), "--output-dir", str(converted))[0] == 0
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "nemo.eval_author.trace_environment_batch.v1",
                "members": [
                    {"task_id": "gym-record", "atif": str(converted / "trace.atif.json"), "source_kind": "gym"}
                ],
            }
        )
    )
    root = tmp_path / ".eval-author" / "batch"
    command = [
        sys.executable,
        str(TRACE_ENVIRONMENT_SCRIPTS / "trace_environment.py"),
        "batch-prepare",
        "--manifest",
        str(manifest),
        "--root",
        str(root),
    ]
    for expected in ("prepared", "existing"):
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
        assert json.loads(result.stdout)["counts"] == {expected: 1}
    summary = json.loads((root / "gym-record/summary.json").read_text())
    assert summary["source"]["kind"] == "gym"


def test_direct_conversion_enforces_raw_byte_limit(adapter, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "MAX_SOURCE_BYTES", 8)
    with pytest.raises(adapter.ConversionError, match="raw-byte"):
        convert(adapter, tmp_path, rollout())


def test_errors_do_not_echo_source_payload_and_duplicate_keys_are_rejected(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text('{"secret":"DO-NOT-PRINT", "secret":"DO-NOT-PRINT"}')
    code, result = cli("--input", str(source), "--output-dir", str(tmp_path / "out"))
    assert code == 2 and "DO-NOT-PRINT" not in json.dumps(result)
    assert not (tmp_path / "out").exists()


def test_missing_sibling_loader_names_the_incomplete_installation(tmp_path):
    lone = tmp_path / "skills/gym-to-atif/scripts"
    lone.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "gym_to_atif.py", lone)
    source = tmp_path / "rollout.json"
    source.write_text(json.dumps(rollout()))
    completed = subprocess.run(
        [sys.executable, str(lone / "gym_to_atif.py"), "--input", str(source), "--output-dir", str(tmp_path / "out")],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "install the sibling eval-author skill alongside gym-to-atif" in completed.stderr
    assert not (tmp_path / "out").exists()
