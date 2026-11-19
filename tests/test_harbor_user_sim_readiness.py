# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import builtins
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


@pytest.mark.parametrize("field", ["import_path", "name"])
def test_explicit_builtin_import_keeps_harbor_class_identity(judge: Any, field: str) -> None:
    value = config(agents=[{field: "harbor.agents.installed.claude_code:ClaudeCode"}])
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    assert not failures(outcome)
    assert judge._agent_class(value.agents[0]) is ClaudeCode


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


@pytest.mark.parametrize("target_version", [None, "1"])
def test_target_replay_and_conflicting_installed_versions_are_blocked(judge: Any, target_version: str | None) -> None:
    value = config(
        agents=[{"name": "claude-code", "load_trajectory": "old.json", "kwargs": {"version": target_version}}],
        user_agent={"name": "claude-code", "kwargs": {"version": "2"}, "bridge": {"kind": "acp"}},
    )
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    assert {"user-agent-session", "user-agent-version"} <= set(failures(outcome))


@pytest.mark.parametrize("target_identity", ["name", "import_path", "name-import"])
@pytest.mark.parametrize("user_identity", ["name", "import_path", "name-import"])
@pytest.mark.parametrize("target_version", [None, "2.1.74", "2.1.75"])
def test_version_checks_use_resolved_role_names(
    judge: Any, target_identity: str, user_identity: str, target_version: str | None
) -> None:
    def role(identity: str, version: str | None) -> dict[str, Any]:
        key = "import_path" if identity == "import_path" else "name"
        name = "claude-code" if identity == "name" else "harbor.agents.installed.claude_code:ClaudeCode"
        return {key: name, "kwargs": {"version": version}}

    value = config(
        agents=[role(target_identity, target_version)],
        user_agent={**role(user_identity, "2.1.75"), "bridge": {"kind": "acp"}},
    )
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    assert ("user-agent-version" in failures(outcome)) == (target_version != "2.1.75")


@pytest.mark.parametrize("user_sim", [True, False])
@pytest.mark.parametrize(
    "setting,value",
    [
        ("prompt_template_path", "PRIVATE_CANARY"),
        ("append_system_prompt", "PRIVATE_CANARY"),
        ("allowed_tools", "PRIVATE_CANARY"),
        ("disallowed_tools", "PRIVATE_CANARY"),
        ("config", {"policy": "PRIVATE_CANARY"}),
        ("memory_dir", "PRIVATE_CANARY"),
        ("disable_web_search", True),
        ("max_turns", 3),
        ("max_budget_usd", "1"),
        ("fallback_model", "PRIVATE_CANARY"),
        ("thinking_display", "omitted"),
        ("permission_mode", "plan"),
    ],
)
def test_ignored_claude_options_only_block_interactive_targets(
    judge: Any, setting: str, value: object, user_sim: bool
) -> None:
    selected = config(agents=[{"name": "claude-code", "kwargs": {setting: value}}])
    if not user_sim:
        selected.user_agent = None
    outcome = judge.ValidationOutcome()
    judge._check_agent(selected, outcome)
    assert ("user-agent-settings" in failures(outcome)) == user_sim
    if user_sim:
        assert setting in str(outcome)
    assert "PRIVATE_CANARY" not in str(outcome)


@pytest.mark.parametrize(
    "name,kwargs,blocked",
    [
        ("claude-code", {"reasoning_effort": "high", "max_thinking_tokens": 1000}, False),
        ("codex", {"config": {"instructions": "policy"}, "web_search": "disabled"}, False),
        ("gemini-cli", {"reasoning_effort": "high", "disable_web_search": True}, False),
        ("gemini-cli", {"sandbox": True}, True),
        ("opencode", {"opencode_config": {"instructions": ["policy"]}}, False),
        ("opencode", {"variant": "high"}, True),
        ("codex", {"prompt_template_path": "prompt.md"}, True),
        ("gemini-cli", {"prompt_template_path": "prompt.md"}, True),
        ("opencode", {"prompt_template_path": "prompt.md"}, True),
    ],
)
def test_stock_acp_settings_follow_each_adapters_support(
    judge: Any, name: str, kwargs: dict[str, Any], blocked: bool
) -> None:
    outcome = judge.ValidationOutcome()
    judge._check_agent(config(agents=[{"name": name, "kwargs": kwargs}]), outcome)
    assert failures(outcome) == (["user-agent-settings"] if blocked else [])


def test_simulator_cli_settings_remain_supported(judge: Any) -> None:
    value = config(
        agents=[{"name": "codex"}],
        user_agent={"name": "claude-code", "kwargs": {"append_system_prompt": "policy"}, "bridge": {"kind": "acp"}},
    )
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    assert not failures(outcome)


@pytest.mark.parametrize("custom_acp", [None, "command", "env"])
def test_custom_acp_implementation_is_not_treated_as_stock(
    judge: Any, monkeypatch: pytest.MonkeyPatch, custom_acp: str | None
) -> None:
    class InheritedClaude(ClaudeCode):
        pass

    class CustomClaude(ClaudeCode):
        def acp_command(self) -> list[str]:
            return ["custom-acp-with-policy"]

    class CustomEnvClaude(ClaudeCode):
        def acp_env(self) -> dict[str, str]:
            return {"CUSTOM_ADAPTER_POLICY": "policy"}

    target = {None: InheritedClaude, "command": CustomClaude, "env": CustomEnvClaude}[custom_acp]
    monkeypatch.setattr(judge, "_agent_class", lambda _: target)
    outcome = judge.ValidationOutcome()
    judge._check_agent(config(agents=[{"name": "claude-code", "kwargs": {"append_system_prompt": "policy"}}]), outcome)
    assert ("user-agent-settings" in failures(outcome)) == (custom_acp is None)


@pytest.mark.parametrize("source", ["agent", "task"])
@pytest.mark.parametrize("user_sim", [True, False])
def test_claude_mcp_registration_is_not_silently_lost(judge: Any, tmp_path: Path, source: str, user_sim: bool) -> None:
    task = tmp_path / "task"
    _write_task(task)
    value = config()
    if source == "task":
        with (task / "task.toml").open("a") as stream:
            stream.write(
                '\n[[environment.mcp_servers]]\nname = "orders"\ntransport = "sse"\nurl = "http://orders/mcp"\n'
            )
    else:
        value = config(
            agents=[
                {
                    "name": "claude-code",
                    "mcp_servers": [{"name": "orders", "url": "http://orders/mcp", "transport": "sse"}],
                }
            ]
        )
    if not user_sim:
        value.user_agent = None
    outcome = judge.ValidationOutcome()
    judge._check_agent(value, outcome)
    judge._check_task_interactions(value, [task], outcome)
    assert ("user-agent-settings" in failures(outcome)) == user_sim


def test_preflight_rejects_old_runtime_before_importing_judge(
    judge: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = importlib.import_module("check_harbor_config")
    monkeypatch.setattr(helper, "version", lambda _: "0.23.0")
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        assert name != "providers.harbor._judge", "Unsupported runtime imported native validators"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(ValueError, match=r"harbor >=0\.24\.0"):
        asyncio.run(helper.preflight(tmp_path, tmp_path / "run.yaml"))


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


@pytest.mark.parametrize("steps", [1, 2])
@pytest.mark.parametrize("user_sim", [True, False])
def test_multistep_bridge_or_unsupported_session_resume_is_blocked(
    judge: Any, tmp_path: Path, user_sim: bool, steps: int
) -> None:
    task = tmp_path / "task"
    _write_task(task)
    path = task / "task.toml"
    path.write_text(path.read_text() + "".join(f'\n[[steps]]\nname = "step-{i}"\n' for i in range(steps)))
    value = config() if user_sim else config(agents=[{"name": "nop", "resume_trajectory": True}], user_agent=None)
    outcome = judge.ValidationOutcome()
    judge._check_task_interactions(value, [task], outcome)
    assert failures(outcome) == (["user-agent-task"] if user_sim else ["agent-resume"] if steps > 1 else [])


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
    assert result.preflight_passed is True
    assert result.runtime_verified is False
    assert result.config_path == str(path)


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


@pytest.mark.parametrize("source", ["config", "host", "interpolated"])
@pytest.mark.parametrize("override", [False, True])
def test_effective_target_options_include_native_environment_fallbacks(
    judge: Any, monkeypatch: pytest.MonkeyPatch, source: str, override: bool
) -> None:
    target: dict[str, Any] = {"name": "claude-code"}
    if source == "host":
        monkeypatch.setenv("CLAUDE_CODE_MAX_TURNS", "2")
    elif source == "interpolated":
        monkeypatch.setenv("PRIVATE_LIMIT", "2")
        target["env"] = {"CLAUDE_CODE_MAX_TURNS": "${PRIVATE_LIMIT}"}
    else:
        target["env"] = {"CLAUDE_CODE_MAX_TURNS": "2"}
    if override:
        target["kwargs"] = {"max_turns": None}
    outcome = judge.ValidationOutcome()
    judge._check_agent(config(agents=[target]), outcome)
    assert ("user-agent-settings" in failures(outcome)) is (not override)
    assert "PRIVATE_LIMIT" not in str(outcome.checks)


def test_preflight_result_serializes_existing_check_records(judge: Any) -> None:
    helper = importlib.import_module("check_harbor_config")
    entry = judge._check("agent", "pass", "Ready")
    result = helper.PreflightResult(
        preflight_passed=True,
        working_directory="/repo",
        config_path="/repo/run.yaml",
        checks=[entry],
        required_env_vars=[],
    )
    assert json.loads(result.model_dump_json())["checks"] == [entry.as_dict()]
    assert result.runtime_verified is False
