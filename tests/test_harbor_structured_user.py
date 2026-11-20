# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from harbor.bridges.acp import build_acpx_config
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from litellm import ModelResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"))
from harbor_structured_user import StructuredACPUser, target_reply  # noqa: E402


class LocalEnvironment:
    def __init__(self, root: Path, acpx: str) -> None:
        self.root = root
        self.acpx = acpx
        self.commands: list[str] = []
        self.uploads: list[str] = []
        self.env = {**os.environ, "HOME": str(root / "home")}
        (root / "home").mkdir()

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads.append(Path(source).read_text())
        shutil.copyfile(source, target)

    async def exec(self, command: str, timeout_sec: float = 30) -> SimpleNamespace:
        self.commands.append(command)
        command = command.replace("acpx", self.acpx, 1)
        process = await asyncio.create_subprocess_shell(
            command, cwd=self.root, env=self.env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
        return SimpleNamespace(return_code=process.returncode, stdout=stdout.decode(), stderr=stderr.decode())


def customer(tmp_path: Path, **kwargs: Any) -> StructuredACPUser:
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "task_id": "quote-check",
                "goal": "Check exact delivery",
                "start_prompt": '  Customer says: "don\'t" $HOME `echo nope` \\n\nsecond line ☕  ',
                "user_facts": {"private": "never-copy-this"},
            }
        )
    )
    logs = tmp_path / "user-agent"
    logs.mkdir()
    return StructuredACPUser(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(str(logs)),
        model_name="selected/customer-model",
        config=scenario,
        **kwargs,
    )


def test_native_structured_customer_preserves_text_and_session(tmp_path: Path, monkeypatch) -> None:
    acpx = os.environ.get("EVAL_AUTHOR_ACPX") or shutil.which("acpx")
    if acpx is None:
        pytest.skip("Requires ACPX 0.11.2")
    environment = LocalEnvironment(tmp_path, acpx)
    fixture = Path(__file__).parent / "fixtures/acp/counter.py"
    state = tmp_path / "state"
    config = build_acpx_config([sys.executable, str(fixture), str(state)], {"ttl": 1, "timeout": 10})
    (tmp_path / ".acpxrc.json").write_text(json.dumps(config))
    user = customer(tmp_path)
    replies = iter([{"action": "message", "message": "Yes, keep this exact.\n"}, {"action": "stop", "message": ""}])
    prompts = []

    async def choose(messages: list[dict[str, str]]) -> ModelResponse:
        prompts.append(list(messages))
        return ModelResponse(
            choices=[{"message": {"content": json.dumps(next(replies))}}],
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        )

    monkeypatch.setattr(user, "_choose", choose)
    context = AgentContext()

    async def run() -> None:
        assert (await environment.exec("acpx sessions ensure")).return_code == 0
        try:
            await user.run("Ignored terminal prompt", cast(BaseEnvironment, environment), context)
        finally:
            await environment.exec("acpx sessions close")

    asyncio.run(run())
    assert len(list(state.iterdir())) == 1
    assert next(state.iterdir()).read_text() == "2"
    first = json.loads(environment.uploads[0])[0]["text"]
    assert prompts[0][-1]["content"] == f"turn=1 received={first}"
    assert prompts[1][-1]["content"] == "turn=2 received=Yes, keep this exact.\n"
    assert all("never-copy-this" not in text for text in environment.uploads)
    assert all("echo nope" not in command for command in environment.commands)
    assert context.n_input_tokens == 14 and context.n_output_tokens == 6
    metadata = context.metadata or {}
    assert metadata["customer_messages"] == metadata["model_responses"] == 2
    assert metadata["empty_polling_commands"] == 0
    assert metadata["termination"] == "customer_stopped"


@pytest.mark.parametrize("failure", ["invalid-action", "transport", "limit", "timeout"])
def test_failed_customer_retains_status_and_never_replays(tmp_path: Path, failure: str, monkeypatch) -> None:
    user = customer(tmp_path, max_messages=1)
    deliveries: list[str] = []

    async def deliver(text: str, environment: object, index: int) -> str:
        deliveries.append(text)
        if failure == "transport":
            raise RuntimeError("failed")
        if failure == "timeout":
            raise asyncio.TimeoutError()
        return "Need anything else?"

    async def choose(messages: list[dict[str, str]]) -> ModelResponse:
        content = '{"action":"message","message":"continue"}' if failure == "limit" else '{"action":"shell"}'
        return ModelResponse(choices=[{"message": {"content": content}}])

    monkeypatch.setattr(user, "_deliver", deliver)
    monkeypatch.setattr(user, "_choose", choose)
    with pytest.raises((ValueError, RuntimeError, asyncio.TimeoutError)):
        asyncio.run(user.run("", cast(BaseEnvironment, None), AgentContext()))
    assert len(deliveries) == 1
    status = json.loads((user.logs_dir / "simulation.json").read_text())
    assert status["termination"] == ("max_messages" if failure == "limit" else "incomplete")


@pytest.mark.parametrize(
    "output",
    [
        '{"result":{"stopReason":"cancelled"}}',
        '{"error":{"code":-1}}',
        '{"result":{}}',
    ],
)
def test_target_reply_requires_completed_turn(output: str) -> None:
    with pytest.raises(RuntimeError):
        target_reply(output)


def test_delivery_timeout_cancels_without_replaying(tmp_path: Path) -> None:
    user = customer(tmp_path)
    commands: list[str] = []

    class Environment:
        async def upload_file(self, source: Path, target: str) -> None:
            pass

        async def exec(self, command: str, timeout_sec: float) -> SimpleNamespace:
            commands.append(command)
            if command != "acpx cancel":
                raise asyncio.TimeoutError()
            return SimpleNamespace(return_code=0)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(user.run("", cast(BaseEnvironment, Environment()), AgentContext()))
    assert len(commands) == 2
    assert commands[-1] == "acpx cancel"
    status = json.loads((user.logs_dir / "simulation.json").read_text())
    assert status["termination"] == "incomplete"
    assert status["customer_messages"] == status["model_responses"] == 0
