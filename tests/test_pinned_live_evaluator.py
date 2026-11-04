# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Guard the version-only adapter without provider or container execution."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = "skillevaluator.tier3.harbor.runner"


@pytest.fixture
def wrapper(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    path = ROOT / "tools/run_pinned_live_evaluator.py"
    assert path.is_file(), "the guarded live adapter is missing"
    spec = importlib.util.spec_from_file_location("live_wrapper", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path), "tier3", "evaluate", "fixture"])
    return module


@pytest.fixture
def upstream(wrapper, monkeypatch):
    origin = {"vcs_info": {"vcs": "git", "commit_id": wrapper.EVALUATOR_REVISION}}
    packages = {
        "skillevaluator": SimpleNamespace(version="0.7.0", read_text=lambda _: json.dumps(origin)),
        "harbor": SimpleNamespace(version="0.24.0"),
    }
    table = MappingProxyType(
        {
            "codex": MappingProxyType({"reasoning_effort": "high", "version": "0.161.0"}),
            "claude-code": MappingProxyType({"version": "2.1.292"}),
        }
    )
    modules = {
        RUNNER: SimpleNamespace(_HARBOR_AGENT_KWARGS=table),
        "skillevaluator.cli": SimpleNamespace(cli=Mock()),
    }
    monkeypatch.setattr(wrapper, "distribution", packages.__getitem__)
    monkeypatch.setattr(wrapper, "import_module", modules.__getitem__)
    return SimpleNamespace(packages=packages, modules=modules, origin=origin, table=table)


def test_pin_preserves_existing_entries_before_cli_dispatch(wrapper, upstream):
    def cli():
        table = upstream.modules[RUNNER]._HARBOR_AGENT_KWARGS
        assert isinstance(table, MappingProxyType)
        assert table["opencode"] == {"version": wrapper.OPENCODE_VERSION}
        assert isinstance(table["opencode"], MappingProxyType)
        assert wrapper.OPENCODE_VERSION == "1.19.5"
        assert set(table) == {*upstream.table, "opencode"}
        for agent, options in upstream.table.items():
            assert table[agent] is options

    upstream.modules["skillevaluator.cli"].cli.side_effect = cli
    before = sys.argv.copy()
    wrapper.main()
    assert sys.argv == before
    upstream.modules["skillevaluator.cli"].cli.assert_called_once_with()


@pytest.mark.parametrize("args", [[], ["tier3"], ["tier3", "validate"], ["evaluate"], ["similarity-check"]])
def test_non_evaluate_commands_fail_before_upstream_imports(wrapper, upstream, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["wrapper", *args])
    imports = Mock(side_effect=AssertionError("unexpected upstream import"))
    monkeypatch.setattr(wrapper, "import_module", imports)
    with pytest.raises(ValueError, match="restricted to tier3 evaluate"):
        wrapper.main()
    imports.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    ["revision", "missing_origin", "malformed_origin", "list_origin", "vcs", "editable", "evaluator", "harbor"],
)
def test_runtime_drift_fails_before_upstream_imports(wrapper, upstream, monkeypatch, mutation):
    if mutation == "revision":
        upstream.origin["vcs_info"]["commit_id"] = "unreviewed"
    elif mutation == "vcs":
        upstream.origin["vcs_info"]["vcs"] = "other"
    elif mutation == "editable":
        upstream.origin["dir_info"] = {"editable": True}
    elif mutation in ("missing_origin", "malformed_origin", "list_origin"):
        raw = {"missing_origin": None, "malformed_origin": "{", "list_origin": "[]"}[mutation]
        upstream.packages["skillevaluator"].read_text = lambda _: raw
    else:
        package = "skillevaluator" if mutation == "evaluator" else "harbor"
        upstream.packages[package].version = "unreviewed"
    imports = Mock(side_effect=AssertionError("unexpected upstream import"))
    monkeypatch.setattr(wrapper, "import_module", imports)
    with pytest.raises(ValueError, match="requires pinned"):
        wrapper.main()
    imports.assert_not_called()


@pytest.mark.parametrize(
    "mutation", ["missing", "mutable", "nested_mutable", "opencode", "version", "reasoning", "extra", "missing_agent"]
)
def test_table_contract_drift_fails_without_mutation(wrapper, upstream, mutation):
    table = dict(upstream.table)
    if mutation == "missing":
        del upstream.modules[RUNNER]._HARBOR_AGENT_KWARGS
    else:
        if mutation == "nested_mutable":
            table["codex"] = dict(table["codex"])
        elif mutation == "opencode":
            table["opencode"] = MappingProxyType({"version": wrapper.OPENCODE_VERSION})
        elif mutation in ("version", "reasoning"):
            table["codex"] = MappingProxyType(
                {**table["codex"], "version" if mutation == "version" else "reasoning_effort": "changed"}
            )
        elif mutation == "extra":
            table["other-agent"] = MappingProxyType({})
        elif mutation == "missing_agent":
            del table["claude-code"]
        upstream.modules[RUNNER]._HARBOR_AGENT_KWARGS = table if mutation == "mutable" else MappingProxyType(table)
    before = vars(upstream.modules[RUNNER]).copy()
    with pytest.raises(ValueError, match="upstream agent-kwargs contract changed"):
        wrapper.main()
    assert vars(upstream.modules[RUNNER]) == before
    upstream.modules["skillevaluator.cli"].cli.assert_not_called()


def test_native_preflight_and_both_trial_arms_launch_with_pin(wrapper, monkeypatch, tmp_path):
    # Real pinned producers and subprocess runners; only the Harbor executable
    # is replaced by a network-free argv recorder, which deliberately fails.
    monkeypatch.setattr(
        os,
        "environ",
        {"HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1", "HARBOR_TELEMETRY": "0"},
    )
    pytest.importorskip("skillevaluator", reason="requires the isolated pinned SkillEvaluator runtime")
    runner = wrapper.import_module(RUNNER)
    preflight = wrapper.import_module("skillevaluator.tier3.harbor.runtime_preflight")
    harbor_cli = wrapper.import_module("harbor.cli.utils")
    cli = wrapper.import_module("skillevaluator.cli")
    dataset = tmp_path / "dataset"
    task = dataset / "case-one"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[task]\nname = "fixture"\n')
    common = dict(
        dataset=dataset,
        agent="opencode",
        model="openai/fixture",
        env_mode="docker",
        jobs_dir=tmp_path / "jobs",
        run_env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    baseline = runner.build_harbor_run_command(
        dataset_path=dataset, agent="opencode", job_name="fixture", env_mode="docker"
    )
    assert not any(value.startswith("version=") for value in baseline)
    original = runner._HARBOR_AGENT_KWARGS
    monkeypatch.setattr(runner, "_HARBOR_AGENT_KWARGS", original)
    callback = Mock()
    monkeypatch.setattr(cli, "cli", callback)
    wrapper.main()  # Real direct_url metadata and table contract, no metadata mocks.
    callback.assert_called_once_with()

    capture = tmp_path / "harbor-argv.jsonl"
    recorder = tmp_path / "harbor-recorder"
    recorder.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(capture)!r}, 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(7)\n"
    )
    recorder.chmod(0o700)
    monkeypatch.setattr(runner, "_harbor_bin", lambda: str(recorder))
    assert not preflight.run_agent_runtime_preflight(**common).ok
    for arm in ("with", "without"):
        ok, detail = runner._run_harbor(
            **common,
            job_name=f"opencode-{arm}",
            n_attempts=1,
            n_concurrent=1,
            timeout_multiplier=1.0,
            override_cpus=None,
            override_memory_mb=None,
            override_storage_mb=None,
        )
        assert not ok and "exited 7" in detail
    commands = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(commands) == 3
    assert [cmd[cmd.index("--job-name") + 1] for cmd in commands] == [
        "runtime-preflight-opencode",
        "opencode-with",
        "opencode-without",
    ]
    for command in commands:
        kwargs = [command[i + 1] for i, arg in enumerate(command) if arg == "--ak"]
        assert len(kwargs) == 1
        assert harbor_cli.parse_kwargs(kwargs) == {"version": wrapper.OPENCODE_VERSION}
        assert command[command.index("--env") + 1] == runner.SECURE_DOCKER_ENV_IMPORT_PATH
        assert command[command.index("--agent") + 1] == baseline[baseline.index("--agent") + 1]
        assert command[command.index("--n-attempts") + 1] == "1"
        assert command[command.index("--n-concurrent") + 1] == "1"
    assert "--disable-verification" in commands[0]
    assert all("--disable-verification" not in command for command in commands[1:])
    assert dict(runner._HARBOR_AGENT_KWARGS) == {**original, "opencode": {"version": wrapper.OPENCODE_VERSION}}
