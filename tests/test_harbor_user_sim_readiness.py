# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.nop import NopAgent
from harbor.models.bridge import BridgeKind
from harbor.models.job.config import JobConfig
from test_skill_contract import _import_discover, _write_task


@pytest.fixture
def judge(monkeypatch: pytest.MonkeyPatch) -> Any:
    _import_discover(monkeypatch)
    return importlib.import_module("providers.harbor._judge")


def config(**updates: Any) -> JobConfig:
    return JobConfig.model_validate(
        {
            "agents": [{"name": "claude-code", "model_name": "target-model"}],
            "user_agent": {"name": "codex", "model_name": "simulator-model", "bridge": {"kind": "acp"}},
            **updates,
        }
    )


def failures(outcome: Any) -> list[str]:
    return [entry.name for entry in outcome.checks if entry.status == "fail"]


def test_both_roles_resolve_without_constructing_or_running_agents(judge: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_construct(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Static readiness must not construct either agent")

    monkeypatch.setattr(judge.AgentFactory, "create_agent_from_config", no_construct)
    outcome = judge.ValidationOutcome()
    judge._check_agent(config(), outcome)
    assert not failures(outcome)
    assert {entry.name for entry in outcome.checks} == {"agent", "user-agent", "user-agent-bridge"}


@pytest.mark.parametrize("role", ["agent", "user-agent"])
def test_missing_either_role_blocks_readiness(judge: Any, role: str) -> None:
    value = config()
    agent = value.agents[0] if role == "agent" else value.user_agent
    assert agent is not None
    agent.name = "not-a-harbor-agent"
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    assert role in failures(outcome)


@pytest.mark.parametrize("capability", ["advertisement-only", "implementation-only", "neither"])
def test_bridge_requires_advertised_and_implemented_interface(
    judge: Any, monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    class AdvertisedOnly(NopAgent):
        capabilities = AgentCapabilities(bridges=frozenset({BridgeKind.ACP}))

    class ImplementedOnly(ClaudeCode):
        capabilities = AgentCapabilities()

    target = {"advertisement-only": AdvertisedOnly, "implementation-only": ImplementedOnly, "neither": NopAgent}[
        capability
    ]
    monkeypatch.setattr(judge, "_agent_class", lambda agent: target)
    outcome = judge.ValidationOutcome()
    judge._check_agent(config(), outcome)
    assert "user-agent-bridge" in failures(outcome)


def test_target_replay_and_conflicting_installed_versions_are_blocked(judge: Any) -> None:
    value = config(
        agents=[{"name": "claude-code", "load_trajectory": "old.json", "kwargs": {"version": "1"}}],
        user_agent={"name": "claude-code", "kwargs": {"version": "2"}, "bridge": {"kind": "acp"}},
    )
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    assert {"user-agent-session", "user-agent-version"} <= set(failures(outcome))


@pytest.mark.parametrize(
    "case", ["missing-persona", "missing-template", "invalid-template", "missing-bridge", "reserved-acpx", "bad-kwargs"]
)
def test_invalid_simulator_inputs_fail_without_disclosing_contents(judge: Any, tmp_path: Path, case: str) -> None:
    value = config()
    user = value.user_agent
    assert user is not None
    path = tmp_path / "input"
    if case == "missing-persona":
        user.user_persona_path = path
    elif case == "missing-template":
        user.user_prompt_template_path = path
    elif case == "invalid-template":
        path.write_text("PRIVATE_CANARY {{ not_an_allowed_field }}")
        user.user_prompt_template_path = path
    elif case == "missing-bridge":
        user.bridge.prompt_path = path
    elif case == "reserved-acpx":
        path.write_text(json.dumps({"agents": {"target": {"command": "PRIVATE_CANARY"}}}))
        user.bridge.kwargs = {"acpx_config_path": str(path)}
    else:
        user.bridge.kwargs = {"unsupported": "PRIVATE_CANARY"}
    outcome = judge.ValidationOutcome()
    judge._check_user_inputs(value, outcome)
    assert "user-agent-inputs" in failures(outcome)
    assert "PRIVATE_CANARY" not in str(outcome)


def test_default_prompts_are_valid_but_do_not_prove_a_conversation(judge: Any) -> None:
    outcome = judge.ValidationOutcome()
    judge._check_user_inputs(config(), outcome)
    assert not failures(outcome)
    proof = next(entry for entry in outcome.checks if entry.name == "user-agent-proof")
    assert proof.status == "warn" and proof.proven is False


def test_simulator_host_variables_are_included(judge: Any) -> None:
    value = config(user_agent={"name": "codex", "env": {"API_KEY": "${SIMULATOR_KEY}"}, "bridge": {"kind": "acp"}})
    outcome = judge.ValidationOutcome()
    judge._check_required_env_vars(value, [], outcome)
    assert [(item.name, item.default) for item in outcome.required_env_vars] == [("SIMULATOR_KEY", None)]


def test_optional_target_variable_does_not_hide_required_simulator_variable(judge: Any) -> None:
    value = config(
        agents=[{"name": "claude-code", "env": {"API_KEY": "${SHARED_KEY:-fallback}"}}],
        user_agent={"name": "codex", "env": {"API_KEY": "${SHARED_KEY}"}, "bridge": {"kind": "acp"}},
    )
    outcome = judge.ValidationOutcome()
    judge._check_required_env_vars(value, [], outcome)
    assert [(item.name, item.default) for item in outcome.required_env_vars] == [("SHARED_KEY", None)]


@pytest.mark.parametrize("user_sim", [True, False])
def test_multistep_bridge_or_unsupported_session_resume_is_blocked(judge: Any, tmp_path: Path, user_sim: bool) -> None:
    task = tmp_path / "task"
    _write_task(task)
    path = task / "task.toml"
    path.write_text(path.read_text() + '\n[[steps]]\nname = "first"\n')
    value = config() if user_sim else config(agents=[{"name": "nop", "resume_trajectory": True}], user_agent=None)
    outcome = judge.ValidationOutcome()
    judge._check_task_interactions(value, [task], outcome)
    assert failures(outcome) == ["user-agent-task" if user_sim else "agent-resume"]


def test_job_preflight_checks_simulator_even_if_resolution_fails(
    judge: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = judge.ConfigCandidate(
        path=tmp_path / "run.yaml",
        parsed=True,
        data={
            "agents": [{"name": "claude-code"}],
            "user_agent": {"name": "not-a-simulator", "bridge": {"kind": "acp"}},
        },
    )

    async def unresolved(*args: Any) -> None:
        return None

    monkeypatch.setattr(judge, "_resolve", unresolved)
    monkeypatch.setattr(judge, "_check_backend", lambda *args: None)
    monkeypatch.setattr(judge, "_harbor_executable", lambda: None)
    outcome = asyncio.run(judge.judge_config(candidate, tmp_path))
    assert "user-agent" in failures(outcome)


def test_explicit_private_config_preflight_does_not_scan_other_tasks(
    judge: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = importlib.import_module("check_harbor_config")
    explore = importlib.import_module("providers.harbor._explore")
    task = tmp_path / "tasks/one"
    _write_task(task)
    path = tmp_path / ".eval-author/.working/user-sim/one/run.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "tasks": [{"path": "tasks/one"}],
                "agents": [{"name": "claude-code"}],
                "user_agent": {"name": "codex", "bridge": {"kind": "acp"}},
            }
        )
    )

    def no_scan(*args: Any, **kwargs: Any) -> None:
        pytest.fail("The explicit preflight must not scan unrelated tasks")

    monkeypatch.setattr(explore, "scan_repository", no_scan)
    monkeypatch.setattr(judge, "_check_backend", lambda *args: None)
    monkeypatch.setattr(judge, "_harbor_executable", lambda: None)
    result = asyncio.run(helper.preflight(tmp_path, path))
    assert result["preflight_passed"] is True
    assert result["runtime_verified"] is False
    assert result["config_path"] == str(path)


def test_preflight_cli_sanitizes_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("agents: [PRIVATE_CANARY\n")
    script = Path(__file__).resolve().parents[1] / "skills/eval-author-discover/scripts/check_harbor_config.py"
    result = subprocess.run(
        [sys.executable, str(script), "--repo", str(tmp_path), "--config", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["preflight_passed"] is False
    assert "PRIVATE_CANARY" not in result.stdout + result.stderr
