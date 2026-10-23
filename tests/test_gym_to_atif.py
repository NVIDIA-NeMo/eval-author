# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic NeMo Gym records shaped from upstream 676cf1f; no live agent or customer data."""

import copy
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.models.trajectories import Trajectory

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/gym-to-atif/scripts"
TRACE_ENVIRONMENT_SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-trace-environment/scripts"
IMAGE = "data:image/png;base64,iVBORw0KGgo="  # schema fixture, not a decoded/validated image


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


def normalize(adapter, source, **kwargs):
    encoded, receipt = adapter.normalize(json.dumps(source).encode(), **kwargs)
    canonical = json.loads(encoded)
    Trajectory.model_validate(canonical)  # provider-owned ATIF validation, not a handwritten schema
    return canonical, receipt


def cli(*args):
    completed = subprocess.run([sys.executable, str(SCRIPTS / "gym_to_atif.py"), *args], capture_output=True, text=True)
    assert completed.stdout, completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def test_native_projection_retains_pairing_and_does_not_invent_agent_or_proof(adapter):
    source = rollout()
    canonical, receipt = normalize(adapter, source)
    assert [step["source"] for step in canonical["steps"]] == ["user", "agent", "agent"]
    call = canonical["steps"][1]
    assert call["tool_calls"][0]["arguments"] == {"path": "fixture.json"}
    assert call["observation"]["results"][0]["source_call_id"] == "call-1"
    assert canonical["agent"]["tool_definitions"] == source["responses_create_params"]["tools"]
    assert canonical["agent"]["name"] == "unknown-gym-agent"
    assert "final_metrics" not in canonical
    assert canonical["extra"]["gym_source"]["reported_reward"] == 0.5
    assert all("timestamp" not in step and "metrics" not in step for step in canonical["steps"])
    assert receipt["basis"] == "gym_projection" and receipt["source_kind"] == "gym"
    assert receipt["lossless_round_trip_claimed"] is False
    again, again_receipt = normalize(adapter, source)
    assert (again, again_receipt) == (canonical, receipt)


def test_developer_role_and_request_instructions_are_explicit(adapter):
    source = rollout()
    source["responses_create_params"]["instructions"] = "Recorded system instructions"
    source["responses_create_params"]["input"].insert(0, {"role": "developer", "content": "Recorded developer rules"})
    canonical, receipt = normalize(adapter, source)
    assert canonical["steps"][0]["message"] == "Recorded system instructions"
    assert canonical["steps"][1]["source"] == "system"
    assert canonical["steps"][1]["extra"]["gym"]["role"] == "developer"
    assert any("Developer role" in loss for loss in receipt["losses"])


def test_adjacent_calls_keep_observation_arrival_order(adapter):
    source = rollout()
    output = source["response"]["output"]
    output.insert(1, {"type": "function_call", "call_id": "call-2", "name": "read", "arguments": "{}"})
    output.insert(2, {"type": "function_call_output", "call_id": "call-2", "output": "Second result arrived first"})
    canonical, _ = normalize(adapter, source)
    step = canonical["steps"][1]
    assert [c["tool_call_id"] for c in step["tool_calls"]] == ["call-1", "call-2"]
    assert [r["source_call_id"] for r in step["observation"]["results"]] == ["call-2", "call-1"]


def test_full_output_exact_prefix_is_not_duplicated(adapter):
    source = rollout()
    prompt = {**source["responses_create_params"]["input"][0], "type": "message", "phase": None}
    source["response"]["output"].insert(0, prompt)
    canonical, _ = normalize(adapter, source)
    assert sum(step["source"] == "user" for step in canonical["steps"]) == 1
    assert "not duplicated" in str(canonical["extra"]["normalization"]["gym_operations"])


def test_ambiguous_history_requires_explicit_scope(adapter):
    source = rollout()
    source["response"]["output"].insert(0, {"type": "message", "role": "user", "content": "Different recorded prompt"})
    with pytest.raises(adapter.ConversionError, match="overlap"):
        normalize(adapter, source)
    canonical, _ = normalize(adapter, source, output_scope="full")
    assert canonical["steps"][0]["message"] == "Different recorded prompt"
    assert sum(step["source"] == "user" for step in canonical["steps"]) == 1


def test_standalone_full_response_and_missing_prompt(adapter):
    source = rollout()
    with pytest.raises(adapter.ConversionError, match="root user"):
        normalize(adapter, source["response"])
    source["response"]["output"].insert(0, {"type": "message", "role": "user", "content": "Recorded request"})
    canonical, _ = normalize(adapter, source["response"])
    assert canonical["steps"][0]["source"] == "user"


def test_harbor_source_atif_wins_byte_for_byte_and_never_follows_embedded_paths(adapter):
    source = rollout()
    source["atif_conversion"] = {
        "source_trajectory_paths": ["/not-authorized/do-not-read.json"],
        "trajectories": [{"session_id": "source-session"}],
    }
    with pytest.raises(adapter.ConversionError, match="--source-atif"):
        normalize(adapter, source)
    atif = {
        "schema_version": "ATIF-v1.6",
        "session_id": "source-session",
        "agent": {"name": "real-agent", "version": "1.0"},
        "steps": [{"step_id": 1, "source": "user", "message": "Original request"}],
    }
    exact = json.dumps(atif, indent=4).encode() + b"\n\n"
    result, receipt = adapter.normalize(json.dumps(source).encode(), exact)
    assert result == exact
    assert json.loads(result)["schema_version"] == "ATIF-v1.6"
    assert receipt["atif_bytes_preserved"] is True and receipt["source_kind"] == "atif"
    wrong = copy.deepcopy(atif)
    wrong["session_id"] = "wrong-session"
    with pytest.raises(adapter.ConversionError, match="does not match"):
        adapter.normalize(json.dumps(source).encode(), json.dumps(wrong).encode())


def test_multistep_bridge_requires_explicit_original_not_first_output_last_reward(adapter):
    source = rollout()
    source["atif_conversion"] = {"trajectories": [{"session_id": "one"}, {"session_id": "two"}]}
    with pytest.raises(adapter.ConversionError, match="multi-step"):
        normalize(adapter, source, allow_projection=True)


def test_structured_images_preserved_in_user_and_tool_content(adapter):
    source = rollout()
    source["responses_create_params"]["input"][0]["content"] = [{"type": "input_image", "image_url": IMAGE}]
    source["response"]["output"][1]["output"] = [
        {"type": "input_image", "image_url": "https://example.org/fixture.png"}
    ]
    canonical, _ = normalize(adapter, source)
    assert canonical["steps"][0]["message"][0]["source"]["path"] == IMAGE
    assert canonical["steps"][1]["observation"]["results"][0]["content"][0]["type"] == "image"
    assert "MIME inferred" in str(canonical["extra"]["normalization"]["uncertainties"])


def test_bridge_serialized_images_recovered_not_treated_as_plain_instruction(adapter):
    source = rollout()
    image = {"type": "image", "source": {"media_type": "image/png", "path": "/unread/local.png"}}
    source["atif_conversion"] = {"source_trajectory_paths": ["/unread/source.json"], "warnings": ["serialized images"]}
    source["responses_create_params"]["input"][0]["content"] = json.dumps([image])
    source["response"]["output"][2]["content"][0]["text"] = json.dumps([image])
    canonical, receipt = normalize(adapter, source, allow_projection=True)
    assert canonical["steps"][0]["message"] == [image]
    assert canonical["steps"][-1]["message"] == [image]
    assert receipt["losses"]
    assert receipt["media_fetched"] is False


def test_reasoning_summary_does_not_claim_verbatim_chain_of_thought(adapter):
    source = rollout()
    source["response"]["output"].insert(
        0,
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Recorded summary"}],
            "encrypted_content": "OPAQUE-CONTENT",
        },
    )
    canonical, _ = normalize(adapter, source)
    step = canonical["steps"][1]
    assert step["message"] == "" and "reasoning_content" not in step
    assert step["extra"]["gym"]["reasoning_summary"][0]["text"] == "Recorded summary"
    assert "OPAQUE-CONTENT" not in json.dumps(canonical)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-call",
        "duplicate-call",
        "non-object-arguments",
        "unknown-type",
        "opaque-image",
        "remote-context",
        "namespace",
    ],
)
def test_unsupported_or_incomplete_mappings_fail_closed(adapter, mutation):
    source = rollout()
    output = source["response"]["output"]
    if mutation == "unknown-call":
        output[1]["call_id"] = "missing"
    elif mutation == "duplicate-call":
        output.insert(1, copy.deepcopy(output[0]))
    elif mutation == "non-object-arguments":
        output[0]["arguments"] = "[]"
    elif mutation == "unknown-type":
        output[0]["type"] = "computer_call"
    elif mutation == "opaque-image":
        source["responses_create_params"]["input"][0]["content"] = [
            {"type": "input_image", "image_url": "https://example.org/opaque"}
        ]
    elif mutation == "remote-context":
        source["responses_create_params"]["previous_response_id"] = "not-fetched"
    else:
        output[0]["namespace"] = "scope"
    with pytest.raises(adapter.ConversionError):
        normalize(adapter, source)


def test_no_result_is_invented_for_failed_rollout(adapter):
    source = rollout()
    source["response"]["output"] = source["response"]["output"][:1]
    source["response"]["status"] = "failed"
    canonical, _ = normalize(adapter, source)
    assert "observation" not in canonical["steps"][1]
    assert canonical["extra"]["gym_source"]["response_status"] == "failed"
    assert "no observations were invented" in str(canonical["extra"]["normalization"]["uncertainties"])


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
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in out.iterdir())
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    code, _ = cli("--input", str(source), "--row", "1", "--output-dir", str(out))
    assert code == 2
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


@pytest.mark.parametrize("serialized", [False, True])
def test_prepare_accepts_gym_provenance_and_blocks_image_only_instruction(tmp_path, serialized):
    source = tmp_path / "rollout.json"
    record = rollout()
    record["responses_create_params"]["input"][0]["content"] = [{"type": "input_image", "image_url": IMAGE}]
    flags = []
    if serialized:
        record["atif_conversion"] = {"source_trajectory_paths": ["/never-opened/original.json"]}
        record["responses_create_params"]["input"][0]["content"] = json.dumps(
            [{"type": "image", "source": {"media_type": "image/png", "path": IMAGE}}]
        )
        flags = ["--allow-projection"]
    source.write_text(json.dumps(record))
    out = tmp_path / "converted"
    assert cli("--input", str(source), "--output-dir", str(out), *flags)[0] == 0
    helper = TRACE_ENVIRONMENT_SCRIPTS / "trace_environment.py"
    init = subprocess.run(
        [
            sys.executable,
            str(helper),
            "init",
            "--root",
            str(tmp_path / ".eval-author" / ".working" / "trace-environments"),
            "--task-id",
            "gym-image",
        ],
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
    assert json.loads(prepared.stdout)["blocking_reason_count"] == 1
    summary = json.loads((task / "summary.json").read_text())
    assert summary["source"]["kind"] == "gym"
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
    root = tmp_path / ".eval-author" / ".working" / "batch"
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


def test_direct_conversion_enforces_raw_byte_limit(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "MAX_SOURCE_BYTES", 8)
    with pytest.raises(adapter.ConversionError, match="raw-byte"):
        adapter.normalize(json.dumps(rollout()).encode())


def test_errors_do_not_echo_source_payload_and_duplicate_keys_are_rejected(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text('{"secret":"DO-NOT-PRINT", "secret":"DO-NOT-PRINT"}')
    code, result = cli("--input", str(source), "--output-dir", str(tmp_path / "out"))
    assert code == 2 and "DO-NOT-PRINT" not in json.dumps(result)
    assert not (tmp_path / "out").exists()
