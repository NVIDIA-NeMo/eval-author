# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_external import agent_network, check_external  # noqa: E402

CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"
ORIGIN = '[metadata]\nrepo = "https://github.com/acme/parser"\nbase_commit = "abc1234"\n'


def write_task(root: Path, toml: str) -> None:
    task = root / "task"
    task.mkdir()
    (task / "task.toml").write_text(toml, encoding="utf-8")
    (task / "instruction.md").write_text("Fix the parser.\n", encoding="utf-8")


def codes(result: dict) -> list[str]:
    return [row["code"] for row in result["diagnostics"]]


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ("", {"mode": "public", "allowed_hosts": [], "source": "default"}),
        (
            '[environment]\nnetwork_mode = "no-network"\n',
            {"mode": "no-network", "allowed_hosts": [], "source": "environment"},
        ),
        (
            "[environment]\nallow_internet = false\n",
            {"mode": "no-network", "allowed_hosts": [], "source": "environment"},
        ),
        (
            '[environment]\nnetwork_mode = "no-network"\n[agent]\nnetwork_mode = "public"\n',
            {"mode": "public", "allowed_hosts": [], "source": "agent"},
        ),
    ],
)
def test_agent_network_follows_harbor_precedence(toml: str, expected: dict) -> None:
    assert agent_network(tomllib.loads(toml)) == expected


def test_public_repository_with_open_network_fails(tmp_path: Path) -> None:
    write_task(tmp_path, ORIGIN)

    result = check_external(tmp_path, {"task": "task"})

    assert result["status"] == "fail"
    assert result["origin_host"] == "github.com"
    assert result["source_reachable"] is True
    assert codes(result) == ["external.source_reachable"]


def test_no_network_passes(tmp_path: Path) -> None:
    write_task(tmp_path, ORIGIN + '[agent]\nnetwork_mode = "no-network"\n')

    result = check_external(tmp_path, {"task": "task"})

    assert result["status"] == "pass"
    assert result["source_reachable"] is False


@pytest.mark.parametrize(
    ("hosts", "reachable"), [('["pypi.org"]', False), ('["*.com"]', True), ('["github.com"]', True)]
)
def test_allowlist_reaches_the_source_only_when_it_matches(tmp_path: Path, hosts: str, reachable: bool) -> None:
    write_task(tmp_path, ORIGIN + f'[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = {hosts}\n')

    result = check_external(tmp_path, {"task": "task"})

    assert result["source_reachable"] is reachable
    assert result["status"] == ("fail" if reachable else "pass")


def test_open_network_without_a_known_source_is_unknown(tmp_path: Path) -> None:
    write_task(tmp_path, "")

    result = check_external(tmp_path, {"task": "task"})

    assert result["status"] == "unknown"
    assert codes(result) == ["external.network_open"]


def test_private_host_is_not_assumed_published(tmp_path: Path) -> None:
    write_task(tmp_path, ORIGIN.replace("github.com", "git.internal.example") + '[agent]\nnetwork_mode = "public"\n')

    result = check_external(tmp_path, {"task": "task"})

    assert result["source_reachable"] is None
    assert codes(result) == ["external.network_open"]


def test_non_harbor_directory_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "evals").mkdir()

    assert codes(check_external(tmp_path, {"task": "evals"})) == ["external.task_not_harbor"]


def test_invalid_task_config_is_unknown(tmp_path: Path) -> None:
    write_task(tmp_path, "[[not toml")

    assert codes(check_external(tmp_path, {"task": "task"})) == ["external.task_config_invalid"]


def test_measure_reports_external(tmp_path: Path) -> None:
    write_task(tmp_path, ORIGIN)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"external": {"task": "task"}}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(selection)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert json.loads(completed.stdout)["measurements"]["external"]["status"] == "fail"
