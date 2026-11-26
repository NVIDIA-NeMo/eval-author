# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import nvidia_recovery_adapter as adapter
import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*adapter.OVERRIDE_VARIABLES, "NVIDIA_RECOVERY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key-must-not-be-used")
    monkeypatch.setenv("NVIDIA_RECOVERY_API_KEY", "synthetic-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")


@pytest.fixture
def request_payload() -> dict[str, Any]:
    return {
        "instruction": "Submit the correct total.",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "submit",
                    "description": "Submit a total.",
                    "parameters": {
                        "type": "object",
                        "properties": {"total": {"type": "integer"}},
                        "required": ["total"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "history": [],
        "remaining_tool_calls": 3,
        "response_contract": {"tool": "name", "arguments": {}, "or": {"finish": True}},
    }


@pytest.fixture
def completion_payload() -> dict[str, Any]:
    return {
        "id": "synthetic-response",
        "object": "chat.completion",
        "created": 1,
        "model": "observed-model",
        "system_fingerprint": "synthetic-fingerprint",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "synthetic-call",
                            "type": "function",
                            "function": {"name": "submit", "arguments": '{"total": 42}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
    }


@pytest.fixture
def mock_provider(monkeypatch: pytest.MonkeyPatch, completion_payload: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Any] = {"requests": [], "response": completion_payload, "status": 200}
    original_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        observed["requests"].append(request)
        if "exception" in observed:
            raise observed["exception"]
        return httpx.Response(
            observed["status"],
            json=observed["response"],
            headers={"location": "https://forbidden.invalid/stolen"},
        )

    class MockClient(original_client):
        def __init__(self, **kwargs: Any) -> None:
            observed["client_options"] = kwargs
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(adapter.httpx, "Client", MockClient)
    return observed


def test_messages_reconstruct_history(request_payload: dict[str, Any]) -> None:
    request_payload["history"] = [
        {"tool": "submit", "arguments": {"total": 12}, "result": {"error": {"code": "timeout"}}},
        {"tool": "submit", "arguments": {"total": 42}, "result": {"ok": True}},
    ]
    messages, _ = adapter.build_messages(request_payload)
    assert messages[0]["role"] == "system"
    assert messages[1:] == [
        {"role": "user", "content": "Submit the correct total."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "history_0",
                    "type": "function",
                    "function": {
                        "name": "submit",
                        "arguments": '{"total": 12}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "history_0", "content": '{"error": {"code": "timeout"}}'},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "history_1",
                    "type": "function",
                    "function": {
                        "name": "submit",
                        "arguments": '{"total": 42}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "history_1", "content": '{"ok": true}'},
        {"role": "user", "content": "Remaining tool calls: 3. Select the next action or finish."},
    ]


@pytest.mark.parametrize("indexed_call", [False, True])
def test_nvidia_request_and_receipt(
    request_payload: dict[str, Any], mock_provider: dict[str, Any], indexed_call: bool
) -> None:
    request_payload["history"] = [
        {"tool": "submit", "arguments": {"total": 12}, "result": {"error": {"code": "timeout"}}}
    ]
    if indexed_call:
        mock_provider["response"]["choices"][0]["message"]["tool_calls"][0]["index"] = 0
    result = adapter.decide(request_payload)
    assert result["action"] == {"tool": "submit", "arguments": {"total": 42}}
    assert len(mock_provider["requests"]) == 1
    sent = mock_provider["requests"][0]
    assert str(sent.url) == "https://inference-api.nvidia.com/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer synthetic-key"
    body = json.loads(sent.content)
    assert body == {
        "model": "azure/openai/gpt-6.1-sol",
        "messages": adapter.build_messages(request_payload)[0],
        "tools": request_payload["tools"],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "n": 1,
        "max_completion_tokens": 2048,
    }
    client_options = mock_provider["client_options"]
    assert {key: client_options[key] for key in ("trust_env", "follow_redirects", "timeout")} == {
        "trust_env": False,
        "follow_redirects": False,
        "timeout": 60,
    }
    assert all(value == 60 for value in sent.extensions["timeout"].values())
    receipt = result["receipt"]
    assert receipt["request_body"] == sent.content.decode("utf-8")
    assert receipt["request_configuration"] == {
        "max_completion_tokens": 2048,
        "max_retries": 0,
        "timeout": 60,
        "trust_env": False,
        "follow_redirects": False,
    }
    assert receipt["adapter_sha256"] == hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
    assert receipt["provider"] == "nvidia"
    assert receipt["requested_model"] == "azure/openai/gpt-6.1-sol"
    assert receipt["observed_model"] == "observed-model"
    assert receipt["usage"]["total_tokens"] == 27
    assert receipt["system_fingerprint"] == "synthetic-fingerprint"
    assert receipt["assistant_response"]["tool_calls"][0]["function"]["arguments"] == '{"total": 42}'
    assert receipt["status"] == "completed"
    assert "synthetic-key" not in json.dumps(result)


@pytest.mark.parametrize("index", [None, True, 0.0, 1, "0"])
def test_rejects_invalid_tool_call_index(
    index: Any, request_payload: dict[str, Any], completion_payload: dict[str, Any]
) -> None:
    completion_payload["choices"][0]["message"]["tool_calls"][0]["index"] = index
    with pytest.raises(adapter.AdapterError, match="invalid_response"):
        adapter.parse_action(completion_payload, adapter.tool_validators(request_payload["tools"]), 3)


@pytest.mark.parametrize(
    "schema_type,value,accepted", [("integer", 1, True), ("integer", 1.0, False), ("number", 1.0, True)]
)
def test_nested_integer_contract_preserves_number_schema(
    schema_type: str,
    value: int | float,
    accepted: bool,
    request_payload: dict[str, Any],
    completion_payload: dict[str, Any],
) -> None:
    request_payload["tools"][0]["function"]["parameters"]["properties"]["total"] = {
        "type": "array",
        "items": {"type": schema_type},
    }
    arguments = {"total": [value]}
    completion_payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(arguments)
    validators = adapter.tool_validators(request_payload["tools"])
    if accepted:
        assert adapter.parse_action(completion_payload, validators, 3) == {"tool": "submit", "arguments": arguments}
    else:
        with pytest.raises(adapter.AdapterError, match="invalid_arguments"):
            adapter.parse_action(completion_payload, validators, 3)


@pytest.mark.parametrize("remaining", [0, 3])
def test_stop_without_tool_finishes(
    remaining: int, request_payload: dict[str, Any], mock_provider: dict[str, Any]
) -> None:
    request_payload["remaining_tool_calls"] = remaining
    mock_provider["response"]["choices"][0].update(
        finish_reason="stop",
        message={"role": "assistant", "content": "Finished."},
    )
    assert adapter.decide(request_payload)["action"] == {"finish": True}
    body = json.loads(mock_provider["requests"][0].content)
    assert body["tool_choice"] == ("none" if remaining == 0 else "auto")


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_failure_no_redirect_or_retry(
    status: int,
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    mock_provider.update(status=status, response={"error": {"message": "secret-provider-body"}})
    with pytest.raises(adapter.AdapterError) as caught:
        adapter.decide(request_payload)
    assert caught.value.category == "provider_status_error"
    assert caught.value.receipt is not None
    assert caught.value.receipt["http_status"] == status
    assert caught.value.receipt["request_body"] == mock_provider["requests"][0].content.decode("utf-8")
    assert caught.value.receipt["request_configuration"]["max_retries"] == 0
    assert "secret-provider-body" not in json.dumps(caught.value.receipt)
    assert len(mock_provider["requests"]) == 1


@pytest.mark.parametrize(
    "error,category",
    [
        (httpx.ReadTimeout("secret-timeout"), "provider_timeout"),
        (httpx.ConnectError("secret-connection"), "provider_connection_error"),
    ],
)
def test_connection_failure_is_sanitized(
    error: Exception,
    category: str,
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    mock_provider["exception"] = error
    with pytest.raises(adapter.AdapterError) as caught:
        adapter.decide(request_payload)
    assert caught.value.category == category
    assert len(mock_provider["requests"]) == 1
    assert "secret-" not in json.dumps(caught.value.receipt)
    assert caught.value.receipt is not None
    assert caught.value.receipt["request_body"] == mock_provider["requests"][0].content.decode("utf-8")
    assert caught.value.receipt["request_configuration"]["timeout"] == 60


@pytest.mark.parametrize(
    "mutation,category",
    [
        ("missing_usage", "invalid_usage"),
        ("negative_usage", "invalid_usage"),
        ("wrong_usage_total", "invalid_usage"),
        ("boolean_usage", "invalid_usage"),
        ("multiple_choices", "invalid_response"),
        ("no_choices", "invalid_response"),
        ("missing_model", "invalid_response"),
        ("length", "invalid_response"),
        ("content_filter", "invalid_response"),
        ("stop_with_tool", "invalid_response"),
        ("multiple_calls", "invalid_response"),
        ("native_tool", "invalid_response"),
        ("unknown_tool", "invalid_response"),
        ("refusal", "invalid_response"),
        ("legacy_function", "invalid_response"),
        ("extra_function_field", "invalid_response"),
        ("missing_call_id", "invalid_response"),
        ("missing_call_function", "invalid_response"),
        ("bad_json", "invalid_arguments"),
        ("duplicate_keys", "invalid_arguments"),
        ("nonfinite_json", "invalid_arguments"),
        ("array_arguments", "invalid_arguments"),
        ("wrong_argument_type", "invalid_arguments"),
        ("integral_float", "invalid_arguments"),
        ("exponent_integer", "invalid_arguments"),
        ("fractional_float", "invalid_arguments"),
        ("boolean_argument", "invalid_arguments"),
        ("extra_argument", "invalid_arguments"),
    ],
)
def test_rejects_invalid_completion(
    mutation: str,
    category: str,
    request_payload: dict[str, Any],
    completion_payload: dict[str, Any],
) -> None:
    response = completion_payload
    choice = response["choices"][0]
    message = choice["message"]
    call = message["tool_calls"][0]
    if mutation == "missing_usage":
        response["usage"] = None
    elif mutation == "negative_usage":
        response["usage"]["prompt_tokens"] = -1
    elif mutation == "wrong_usage_total":
        response["usage"]["total_tokens"] = 28
    elif mutation == "boolean_usage":
        response["usage"]["prompt_tokens"] = True
    elif mutation == "multiple_choices":
        response["choices"].append(copy.deepcopy(choice))
    elif mutation == "no_choices":
        response["choices"] = []
    elif mutation == "missing_model":
        response["model"] = None
    elif mutation in {"length", "content_filter", "stop_with_tool"}:
        choice["finish_reason"] = "stop" if mutation == "stop_with_tool" else mutation
    elif mutation == "multiple_calls":
        message["tool_calls"].append(copy.deepcopy(call))
    elif mutation == "native_tool":
        call["type"] = "custom"
    elif mutation == "unknown_tool":
        call["function"]["name"] = "shell"
    elif mutation == "refusal":
        message["refusal"] = "Cannot continue."
    elif mutation == "legacy_function":
        message["function_call"] = {"name": "shell", "arguments": "{}"}
    elif mutation == "extra_function_field":
        call["function"]["extra"] = True
    elif mutation == "missing_call_id":
        del call["id"]
    elif mutation == "missing_call_function":
        del call["function"]
    else:
        call["function"]["arguments"] = {
            "bad_json": "{",
            "duplicate_keys": '{"total": 1, "total": 42}',
            "nonfinite_json": '{"total": NaN}',
            "array_arguments": "[]",
            "wrong_argument_type": '{"total": "42"}',
            "integral_float": '{"total": 1.0}',
            "exponent_integer": '{"total": 1e0}',
            "fractional_float": '{"total": 1.5}',
            "boolean_argument": '{"total": true}',
            "extra_argument": '{"total": 42, "extra": true}',
        }[mutation]
    with pytest.raises(adapter.AdapterError, match=category):
        adapter.parse_action(response, adapter.tool_validators(request_payload["tools"]), 3)


@pytest.mark.parametrize(
    "mutation,category",
    [
        ("length", "invalid_response"),
        ("id", "invalid_response"),
        ("function", "invalid_response"),
        ("integral_float", "invalid_arguments"),
    ],
)
def test_invalid_action_retains_observed_usage(
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
    mutation: str,
    category: str,
) -> None:
    choice = mock_provider["response"]["choices"][0]
    call = choice["message"]["tool_calls"][0]
    if mutation == "length":
        choice["finish_reason"] = "length"
    elif mutation == "integral_float":
        call["function"]["arguments"] = '{"total": 1.0}'
    else:
        del call[mutation]
    with pytest.raises(adapter.AdapterError) as caught:
        adapter.decide(request_payload)
    receipt = caught.value.receipt
    assert receipt is not None
    assert receipt["usage"]["total_tokens"] == 27
    assert caught.value.category == category
    assert receipt["status"] == category
    assert receipt["finish_reason"] == choice["finish_reason"]
    assert receipt["observed_model"] == "observed-model"
    assert receipt["request_body"] == mock_provider["requests"][0].content.decode("utf-8")
    assert "synthetic-key" not in json.dumps(receipt)


def test_no_calls_allowed_rejects_call(request_payload: dict[str, Any], mock_provider: dict[str, Any]) -> None:
    request_payload["remaining_tool_calls"] = 0
    with pytest.raises(adapter.AdapterError, match="invalid_response"):
        adapter.decide(request_payload)


@pytest.mark.parametrize("name", adapter.OVERRIDE_VARIABLES)
def test_rejects_endpoint_override(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    monkeypatch.setenv(name, "https://forbidden.invalid")
    with pytest.raises(adapter.AdapterError, match="invalid_configuration"):
        adapter.decide(request_payload)
    assert mock_provider["requests"] == []


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf")])
def test_rejects_invalid_timeout(
    timeout: float, request_payload: dict[str, Any], mock_provider: dict[str, Any]
) -> None:
    with pytest.raises(adapter.AdapterError, match="invalid_configuration"):
        adapter.decide(request_payload, timeout=timeout)
    assert mock_provider["requests"] == []


def test_never_falls_back_to_openai_key(
    monkeypatch: pytest.MonkeyPatch,
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    monkeypatch.delenv("NVIDIA_RECOVERY_API_KEY")
    with pytest.raises(adapter.AdapterError, match="missing_credential"):
        adapter.decide(request_payload)
    assert mock_provider["requests"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("instruction", None),
        ("history", {}),
        ("remaining_tool_calls", True),
        ("remaining_tool_calls", -1),
        ("tools", []),
        ("tools", [{"type": "web_search"}]),
        ("history", [{"tool": "shell", "arguments": {}, "result": {}}]),
        ("base_url", "https://forbidden.invalid"),
    ],
)
def test_invalid_request_fails_before_provider(
    field: str,
    value: Any,
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    request_payload[field] = value
    with pytest.raises(adapter.AdapterError, match="invalid_request"):
        adapter.decide(request_payload)
    assert mock_provider["requests"] == []


def test_remote_schema_reference_is_not_fetched(
    request_payload: dict[str, Any],
    completion_payload: dict[str, Any],
) -> None:
    request_payload["tools"][0]["function"]["parameters"] = {"$ref": "https://forbidden.invalid/schema"}
    with pytest.raises(adapter.AdapterError, match="invalid_arguments"):
        adapter.parse_action(completion_payload, adapter.tool_validators(request_payload["tools"]), 3)


def test_cli_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    monkeypatch.setattr(sys, "argv", ["adapter", "--model", "explicit-model", "--timeout", "10"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request_payload)))
    adapter.main()
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["receipt"]["requested_model"] == "explicit-model"
    assert result["receipt"]["request_configuration"]["timeout"] == 10
    assert json.loads(result["receipt"]["request_body"])["model"] == "explicit-model"
    assert mock_provider["client_options"]["timeout"] == 10


@pytest.mark.parametrize(
    "mode,category",
    [
        ("provider", "provider_status_error"),
        ("usage", "invalid_usage"),
        ("input", "invalid_request"),
        ("argument", "invalid_configuration"),
        ("unexpected", "adapter_failure"),
    ],
)
def test_cli_failure_is_fixed_category_only(
    mode: str,
    category: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request_payload: dict[str, Any],
    mock_provider: dict[str, Any],
) -> None:
    argv = ["adapter"]
    stdin = json.dumps(request_payload)
    if mode == "provider":
        mock_provider.update(status=500, response={"error": {"message": "secret-provider-body"}})
    elif mode == "usage":
        mock_provider["response"]["usage"] = None
    elif mode == "input":
        stdin = "secret-invalid-input"
    elif mode == "argument":
        argv += ["--base-url", "https://secret-forbidden.invalid"]
    else:
        monkeypatch.setattr(adapter, "OpenAI", MagicMock(side_effect=RuntimeError("secret-exception")))
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as caught:
        adapter.main()
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["adapter_error"] == category
    if mode == "unexpected":
        receipt = json.loads(captured.err)["receipt"]
        assert receipt["request_body"] is None
        assert receipt["request_configuration"] == {
            "max_completion_tokens": 2048,
            "max_retries": 0,
            "timeout": 60,
            "trust_env": False,
            "follow_redirects": False,
        }
    assert "secret-" not in captured.err
    assert "Traceback" not in captured.err
    assert "synthetic-key" not in captured.err
