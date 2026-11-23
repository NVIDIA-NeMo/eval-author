# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import trace_ingest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-trace-environment/examples/workplace/prepare_trace.py"
SPEC = importlib.util.spec_from_file_location("workplace_prepare_trace", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
prepare_trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_trace)


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> dict:
    output = tmp_path_factory.mktemp("workplace-trace").resolve() / "prepared"
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), "--access-mode", "stateful_simulation"],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize("mode", ["mock", "simulation", "unknown", "", "REAL"])
def test_unknown_access_rejected(mode: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prepare_trace.prepare(tmp_path / "rejected", mode)
    assert not (tmp_path / "rejected").exists()


def test_access_modes_are_explicit(tmp_path: Path) -> None:
    modes = {mode.value for mode in prepare_trace.AccessMode}
    assert modes == {"real", "exact_replay", "stateful_simulation", "none"}
    contract = prepare_trace.access_contract("stateful_simulation")
    assert contract["stateful_simulation"]["opt_in"] is True
    assert contract["stateful_simulation"]["production_access"] is None
    assert prepare_trace.access_contract("exact_replay")["stateful_simulation"]["opt_in"] is False
    assert prepare_trace.access_contract("none")["none"]["production_access"] == "none"
    with pytest.raises(ValueError, match="No Workplace credentials supplied"):
        prepare_trace.prepare(tmp_path / "real", "real")
    assert not (tmp_path / "real").exists()


def test_cli_requires_access_mode(tmp_path: Path) -> None:
    output = tmp_path / "unselected"
    process = subprocess.run([sys.executable, str(SCRIPT), "--output", str(output)], capture_output=True, text=True)
    assert process.returncode == 2
    assert "--access-mode" in process.stderr
    assert not output.exists()


@pytest.mark.parametrize("mode,replay", [("none", False), ("exact_replay", True)])
def test_non_simulation_modes_never_build_task(tmp_path: Path, mode: str, replay: bool) -> None:
    output = tmp_path.resolve() / mode
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), "--access-mode", mode], capture_output=True, text=True
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["task"] is None
    assert result["task_mode"] == "none"
    assert result["access_mode"] == mode
    assert result["replay_baseline"] is replay
    assert result["construction_status"] == "not_attempted"
    assert not (output / "task").exists()
    assert not list(output.rglob("task.toml"))
    assert not list(output.rglob("service.py"))
    workspace = Path(result["workspace"])
    assert (workspace / "private/tool-call-generation.json").exists() is replay
    assert (workspace / "task/environment/tool-call-fixtures/mcp_replay.py").exists() is replay
    assert not (workspace / "private/construction.json").exists()
    decisions = json.loads((workspace / "private/tool-access.json").read_text())
    assert {row["access"] for row in decisions["decisions"]} == ({"mock"} if replay else {"none"})
    modes = json.loads((output / "private/access-mode.json").read_text())
    assert modes["simulation_task"] is None
    assert modes["task_mode"] == "none"
    assert modes["replay_baseline"] is replay


def test_compass_source_and_private_pipeline(prepared: dict) -> None:
    assert prepared["access_mode"] == prepared["task_mode"] == "stateful_simulation"
    assert prepared["replay_baseline"] is True
    output = Path(prepared["output"])
    workspace = Path(prepared["workspace"])
    source = trace_ingest.Trace.model_validate_json((output / "private/synthetic-source.trace.json").read_bytes())
    assert source.id == "synthetic-workplace-email"
    root = source.root_spans[0]
    assert isinstance(root.turns, list)
    assert len(root.turns) == 4
    assert [child.tool_name for child in root.children] == [
        "email_search_emails",
        "email_get_email_information_by_id",
        "email_reply_email",
    ]
    summary = json.loads((workspace / "summary.json").read_text())
    assert summary["source"]["kind"] == "trace"
    assert summary["source"]["loader"] == {"distribution": "nemo-compass", "version": "0.2.1"}
    assert summary["privacy"]["reviewer_kind"] == "agent"
    assert summary["environment"]["qualification"] == "not_run"
    assert summary["privacy"]["contextual_review_complete"] is True
    inventory = json.loads((workspace / "private/tool-call-inventory.json").read_text())
    assert inventory["tool_count"] == inventory["call_count"] == 3
    assert (workspace / "private/tool-call-plan.json").is_file()
    assert (workspace / "private/tool-call-generation.json").is_file()
    modes = json.loads((output / "private/access-mode.json").read_text())
    assert modes["agent_visible_trace"] is False
    assert modes["state_basis"] == "reconstructed"
    assert modes["observed_historical_state"] is False
    assert modes["product_task_acceptance"] == "unmeasured"
    assert modes["construction_assessment"]["execution"] == "unmeasured"
    assert Path(prepared["task"]) == output / "task"
    assert (output / "task/task.toml").is_file()
    for path in (output, workspace):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    for relative in ("safe/trace.json", "private/tool-call-inventory.json", "private/construction.json"):
        assert stat.S_IMODE((workspace / relative).stat().st_mode) == 0o600
    task = Path(prepared["task"])
    agent_files = [
        task / "instruction.md",
        task / "environment/Dockerfile",
        *list((task / "environment/main").rglob("*.py")),
    ]
    for path in agent_files:
        text = path.read_text()
        # The builder's explicit requested reply is public task specification.
        # Preparation must not copy the source demonstration or hidden grading data.
        if path.name != "instruction.md":
            assert "Confirmed for Tuesday at 10:00." not in text
        assert "synthetic-workplace-email" not in text
    assert "COPY main/" in (task / "environment/Dockerfile").read_text()
    assert not list(task.rglob("trace.json"))
    assert not list(task.rglob("mcp-scenario.json"))
    assert not (task / "environment/main/seed.json").exists()


def test_reconstructed_state_uses_validated_construction(prepared: dict) -> None:
    workspace = Path(prepared["workspace"])
    document = json.loads((workspace / "private/construction.json").read_text())
    assert document["schema"] == "nemo.eval_author.construction.v3"
    assert document["bindings"]["trace_sha256"]
    seed_path = workspace / "task/environment/workplace/seed.json"
    task = Path(prepared["task"])
    seed_bytes = seed_path.read_bytes()
    assert seed_bytes == (SCRIPT.parent / "task/seed.json").read_bytes()
    assert seed_bytes == (task / "environment/workplace/seed.json").read_bytes()
    assert seed_bytes == (task / "tests/seed.json").read_bytes()
    seed = json.loads(seed_bytes)
    assert [row["email_id"] for row in seed] == ["1", "2", "3"]
    bound = next(row for row in document["inputs"] if row["path"] == "environment/workplace/seed.json")
    assert bound["sha256"] == hashlib.sha256(seed_bytes).hexdigest()
    assert len(document["starting_state"]["external_inputs"]) == 2
    for row in document["starting_state"]["external_inputs"]:
        assert row["identity"] == "sha256:" + bound["sha256"]
    assert prepare_trace.validate_task_binding(workspace, task)["status"] == "structurally_valid"
    assert seed[0]["subject"] == "Schedule confirmation"
    assert seed[0]["body"] == "Please confirm Tuesday at 10:00."
    assert seed[0]["inbox/outbox"] == "inbox"
    assert all(row["basis"] == "authored" and row["source"] is None for row in document["inputs"])
    assessed = prepare_trace.construction.assess(document, mode="proposal", workspace=workspace)
    assert assessed["status"] == "structurally_valid"
    assert assessed["provenance"]["basis_counts"]["observed"] == 0
    assert assessed["provenance"]["basis_counts"]["authored"] == len(document["inputs"])
    tampered = copy.deepcopy(document)
    tampered["inputs"][0]["basis"] = "observed"
    with pytest.raises(prepare_trace.construction.ConstructionError):
        prepare_trace.construction.assess(tampered, mode="proposal", workspace=workspace)


@pytest.mark.parametrize("relative", ["environment/workplace/seed.json", "tests/seed.json"])
def test_task_seed_mutation_rejects(prepared: dict, tmp_path: Path, relative: str) -> None:
    task = tmp_path / "task"
    shutil.copytree(prepared["task"], task)
    (task / relative).write_text("[]\n")
    with pytest.raises(ValueError, match="construction-bound initial state"):
        prepare_trace.validate_task_binding(Path(prepared["workspace"]), task)


def test_source_projection_does_not_claim_authored_seed_fields(prepared: dict) -> None:
    workspace = Path(prepared["workspace"])
    inventory = json.loads((workspace / "private/tool-call-inventory.json").read_text())
    projection = json.loads(inventory["tools"][0]["calls"][0]["results"][0]["content"])["emails"]
    assert projection == [
        {
            "email_id": "1",
            "inbox/outbox": "inbox",
            "subject": "Schedule confirmation",
            "body": "Please confirm Tuesday at 10:00.",
        }
    ]
    candidate = json.loads((workspace / "candidate.json").read_text())
    assert "two additional rows are authored reconstruction" in " ".join(candidate["uncertainties"])


def test_production_replay_rejects_unseen_calls(prepared: dict) -> None:
    workspace = Path(prepared["workspace"])
    fixtures = workspace / "task/environment/tool-call-fixtures"
    scenario = json.loads((fixtures / "mcp-scenario.json").read_text())
    requests = []
    for index, tool in enumerate(scenario["tools"], 1):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "tools/call",
                "params": {"name": tool["definition"]["name"], "arguments": tool["cases"][0]["arguments"]},
            }
        )
    requests.extend(
        [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "email_search_emails", "arguments": {"query": "invoice"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "email_reply_email", "arguments": {"email_id": "1", "body": "Tuesday at 10 works."}},
            },
        ]
    )
    process = subprocess.run(
        [sys.executable, str(fixtures / "mcp_replay.py"), "--scenario", str(fixtures / "mcp-scenario.json")],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(responses) == 5
    assert [response["result"]["isError"] for response in responses] == [False, False, False, True, True]
    assert json.loads(responses[1]["result"]["content"][0]["text"]) == {"body": "Please confirm Tuesday at 10:00."}
    assert (fixtures / "mcp_replay.py").read_bytes() == (prepare_trace.ASSETS / "replay_mcp_server.py").read_bytes()


def test_simulation_not_accepted_by_production_contract(prepared: dict) -> None:
    from eval_author_shared import fixture_compiler

    workspace = Path(prepared["workspace"])
    inventory = json.loads((workspace / "private/tool-call-inventory.json").read_text())
    plan = json.loads((workspace / "private/tool-call-plan.json").read_text())
    decisions = json.loads((workspace / "private/requested-tool-access.json").read_text())
    decisions["decisions"][0].update(access="stateful_simulation", adapter=None)
    with pytest.raises(ValueError, match="real, mock, or none"):
        fixture_compiler.resolve_tool_access(inventory, plan, decisions, reviewer_kind="agent")
