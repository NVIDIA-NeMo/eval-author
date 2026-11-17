# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import shlex
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/agents/tau2_retail"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("retail_example_" + name, EXAMPLE / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_original_reference_files_match_provenance() -> None:
    provenance = json.loads((EXAMPLE / "upstream/provenance.json").read_text())
    assert provenance["revision"] in (EXAMPLE / "requirements.txt").read_text()
    for item in [*provenance["files"], provenance["baseline_dependencies"]]:
        assert hashlib.sha256((EXAMPLE / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert not list(EXAMPLE.glob("*.env"))


class FakeSession:
    def __init__(self, directory: Path, *, restore: bool = False) -> None:
        self.directory = directory
        self.messages = json.loads((directory / "session.json").read_text()) if restore else []

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "session.json").write_text(json.dumps(self.messages))

    def respond(self, text: str) -> str:
        self.messages.append(text)
        self.save()
        return f"Turn {len(self.messages)}"


def test_acp_restores_the_same_session_and_refuses_extra_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLE))
    module = load("acp_server")
    monkeypatch.setattr(module, "RetailSession", FakeSession)
    notifications: list[dict[str, Any]] = []
    monkeypatch.setattr(module, "emit", notifications.append)
    server = module.Server(tmp_path)
    assert server.dispatch("initialize", {})["agentCapabilities"]["loadSession"] is True
    session_id = server.dispatch("session/new", {"mcpServers": []})["sessionId"]
    server.dispatch("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "hello"}]})
    restored = module.Server(tmp_path)
    restored.dispatch("session/load", {"sessionId": session_id})
    restored.dispatch("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "yes"}]})
    assert [item["params"]["sessionId"] for item in notifications] == [session_id, session_id]
    assert notifications[-1]["params"]["update"]["content"]["text"] == "Turn 2"
    with pytest.raises(ValueError, match="fresh task"):
        restored.dispatch("session/new", {})
    with pytest.raises(ValueError, match="text messages"):
        restored.dispatch("session/prompt", {"sessionId": session_id, "prompt": [{"type": "image"}]})
    with pytest.raises(ValueError):
        restored.dispatch("session/load", {"sessionId": "../escape"})
    with pytest.raises(ValueError, match="fixed retail toolkit"):
        module.Server(tmp_path / "other").dispatch("session/new", {"mcpServers": [{"name": "extra"}]})


@pytest.fixture
def evidence() -> tuple[ModuleType, dict, dict, dict]:
    verifier = load("verify")
    order_id = verifier.ORDER_ID
    seed: dict[str, Any] = {
        "orders": {
            order_id: {
                "status": "pending",
                "payment_history": [{"transaction_type": "payment", "amount": 17.5, "payment_method_id": "card1"}],
            }
        },
        "users": {"customer": {"email": "customer@example.com"}},
        "products": {},
    }
    final = copy.deepcopy(seed)
    final["orders"][order_id].update(status="cancelled", cancel_reason=verifier.REASON)
    final["orders"][order_id]["payment_history"].append(
        {"transaction_type": "refund", "amount": 17.5, "payment_method_id": "card1"}
    )
    transcript = {
        "messages": [
            {"role": "user", "content": "Please cancel my order."},
            {"role": "assistant", "content": "What is your email?"},
            {"role": "user", "content": "customer@example.com"},
            {"role": "assistant", "content": f"Please confirm cancellation of {order_id} for {verifier.REASON}."},
            {"role": "user", "content": "yes"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "write",
                        "name": "cancel_pending_order",
                        "arguments": {"order_id": order_id, "reason": verifier.REASON},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "write", "error": False, "content": "cancelled"},
            {"role": "assistant", "content": "Your order was cancelled."},
        ]
    }
    return verifier, transcript, final, seed


def test_verifier_accepts_matching_write_confirmation_and_database(evidence: tuple) -> None:
    verifier, transcript, final, seed = evidence
    assert all(verifier.check(transcript, final, seed).values())
    final["orders"][verifier.ORDER_ID]["exchange_items"] = None
    assert all(verifier.check(transcript, final, seed).values())


@pytest.mark.parametrize(
    "mutation",
    [
        "no_confirmation",
        "late_confirmation",
        "wrong_order",
        "failed_tool",
        "extra_write",
        "no_mutation",
        "unrelated_mutation",
        "final_answer_only",
    ],
)
def test_verifier_rejects_false_success(evidence: tuple, mutation: str) -> None:
    verifier, transcript, final, seed = evidence
    messages = transcript["messages"]
    if mutation == "no_confirmation":
        messages[4]["content"] = "no"
    elif mutation == "late_confirmation":
        messages.append(messages.pop(4))
    elif mutation == "wrong_order":
        messages[5]["tool_calls"][0]["arguments"]["order_id"] = "#OTHER"
    elif mutation == "failed_tool":
        messages[6]["error"] = True
    elif mutation == "extra_write":
        messages.insert(0, copy.deepcopy(messages[5]))
    elif mutation == "no_mutation":
        final = seed
    elif mutation == "unrelated_mutation":
        final["users"]["customer"]["email"] = "changed@example.com"
    elif mutation == "final_answer_only":
        transcript["messages"] = [messages[0], messages[-1]]
    assert not all(verifier.check(transcript, final, seed).values())


def test_simulator_sends_exact_opening_and_quotes_followup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from harbor.models.agent.context import AgentContext

    monkeypatch.syspath_prepend(str(EXAMPLE))
    module = load("retail_harbor")
    opening = "I need help with O'Reilly's order. $(touch /tmp/should-not-exist)"
    followup = "yes; echo no"
    completion = AsyncMock(
        side_effect=[
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps({"message": followup, "done": False})))
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"message": "", "done": True})))],
                usage=None,
            ),
        ]
    )

    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=completion))

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(module, "AsyncOpenAI", lambda **kwargs: Client())
    environment = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(stdout="Please confirm.", return_code=0)))
    runner = module.NeutralUserHarbor(
        logs_dir=tmp_path,
        model_name="openai/test",
        extra_env={"OPENAI_API_KEY": "unit-test", "OPENAI_BASE_URL": "https://test.invalid"},
    )
    instruction = "## Private scenario\n\n" + json.dumps({"schema_version": 1, "start_prompt": opening})
    context = AgentContext()
    asyncio.run(runner.run(instruction, cast(Any, environment), context))
    commands = [call.args[0] for call in environment.exec.call_args_list]
    assert [shlex.split(command) for command in commands] == [["acpx", "prompt", opening], ["acpx", "prompt", followup]]
    saved = json.loads((tmp_path / "conversation.json").read_text())
    assert saved["terminated"] is True
    assert saved["exchanges"][0]["user"] == opening
    assert context.metadata is not None
    assert context.metadata["token_accounting"] == "simulator_only"
    with pytest.raises(ValueError, match="translation"):
        module.private_scenario("Missing scenario")


def test_prepare_is_repeat_safe_and_keeps_private_inputs_out_of_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import yaml

    module = load("prepare")
    source = tmp_path / "example"
    shutil.copytree(EXAMPLE, source, ignore=shutil.ignore_patterns(".eval-author", "__pycache__"))
    monkeypatch.setattr(module, "ROOT", source)
    module.main()
    task = source / ".eval-author/tasks/cancel-order"
    assert not (task / "environment/user-sim.yaml").exists()
    assert not (task / "environment/verify.py").exists()
    config = yaml.safe_load((source / ".eval-author/base-run.yaml").read_text())
    assert Path(config["jobs_dir"]).is_absolute()
    assert config["agents"][0]["env"]["OPENAI_API_KEY"] == "${OPENAI_API_KEY}"
    with pytest.raises(FileExistsError):
        module.main()
