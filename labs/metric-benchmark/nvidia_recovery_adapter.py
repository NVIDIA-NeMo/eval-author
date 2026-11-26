# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Select one recovery action through the fixed NVIDIA inference endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

import httpx
from jsonschema import Draft202012Validator, validators
from jsonschema.protocols import Validator
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from referencing import Registry

BASE_URL = "https://inference-api.nvidia.com/v1"
DEFAULT_MODEL = "azure/openai/gpt-6.1-sol"
OVERRIDE_VARIABLES = ("OPENAI_BASE_URL", "NVIDIA_BASE_URL", "NVIDIA_RECOVERY_BASE_URL")
SYSTEM_INSTRUCTION = (
    "Select at most one next action using only the supplied task, tool schemas, and history. "
    "The host executes the selected function and returns its result on the next decision. "
    "Do not use native tools, files, shell, web, or other capabilities. "
    "When finished, respond without a tool call. Do not request parallel tool calls. "
    "Integer tool arguments must use JSON integer literals, not floats such as 1.0 or 1e0."
)

# The backend requires Python ints, unlike JSON Schema's integral-float semantics.
StrictIntegerValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda checker, value: type(value) is int),
)


class AdapterError(Exception):
    def __init__(self, category: str, receipt: dict[str, Any] | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.receipt = receipt


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AdapterError("invalid_configuration")


def reject_constant(value: str) -> None:
    raise ValueError("nonfinite_json")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def load_json(value: str) -> Any:
    return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def tool_validators(tools: Any) -> dict[str, Validator]:
    if not isinstance(tools, list) or not tools:
        raise AdapterError("invalid_request")
    validators: dict[str, Validator] = {}
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"type", "function"} or tool["type"] != "function":
            raise AdapterError("invalid_request")
        function = tool["function"]
        if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
            raise AdapterError("invalid_request")
        name = function.get("name")
        schema = function.get("parameters")
        if not isinstance(name, str) or not name or name in validators or not isinstance(schema, dict):
            raise AdapterError("invalid_request")
        Draft202012Validator.check_schema(schema)
        # An empty registry prevents argument validation from retrieving remote references.
        validators[name] = StrictIntegerValidator(schema, registry=Registry())
    return validators


def build_messages(request: Any) -> tuple[list[ChatCompletionMessageParam], dict[str, Validator]]:
    if not isinstance(request, dict) or set(request) - {
        "instruction",
        "tools",
        "history",
        "remaining_tool_calls",
        "response_contract",
    }:
        raise AdapterError("invalid_request")
    instruction = request.get("instruction")
    history = request.get("history")
    remaining = request.get("remaining_tool_calls")
    if not isinstance(instruction, str) or not instruction or not isinstance(history, list):
        raise AdapterError("invalid_request")
    if type(remaining) is not int or remaining < 0:
        raise AdapterError("invalid_request")
    validators = tool_validators(request.get("tools"))
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": instruction},
    ]
    for index, entry in enumerate(history):
        if not isinstance(entry, dict) or set(entry) != {"tool", "arguments", "result"}:
            raise AdapterError("invalid_request")
        name, arguments = entry["tool"], entry["arguments"]
        if not isinstance(name, str) or name not in validators or not isinstance(arguments, dict):
            raise AdapterError("invalid_request")
        call_id = f"history_{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": name, "arguments": encode(arguments)}}
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": encode(entry["result"])},
            ]
        )
    messages.append(
        {"role": "user", "content": f"Remaining tool calls: {remaining}. Select the next action or finish."}
    )
    return messages, validators


def parse_action(response: dict[str, Any], validators: dict[str, Validator], remaining: int) -> dict[str, Any]:
    choices = response.get("choices")
    usage = response.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        raise AdapterError("invalid_usage")
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
        raise AdapterError("invalid_usage")
    if not isinstance(response.get("model"), str) or not response["model"]:
        raise AdapterError("invalid_response")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise AdapterError("invalid_response")
    choice = choices[0]
    message = choice.get("message")
    if choice.get("index") != 0 or not isinstance(message, dict) or message.get("role") != "assistant":
        raise AdapterError("invalid_response")
    if message.get("refusal") or message.get("function_call"):
        raise AdapterError("invalid_response")
    calls = message.get("tool_calls")
    if choice.get("finish_reason") == "stop" and calls in (None, []):
        return {"finish": True}
    if choice.get("finish_reason") != "tool_calls" or not isinstance(calls, list) or len(calls) != 1 or remaining <= 0:
        raise AdapterError("invalid_response")
    call = calls[0]
    if (
        not isinstance(call, dict)
        or not {"id", "type", "function"} <= set(call)
        or set(call) - {"id", "type", "function", "index"}
        or call["type"] != "function"
    ):
        raise AdapterError("invalid_response")
    if "index" in call and (type(call["index"]) is not int or call["index"] != 0):
        raise AdapterError("invalid_response")
    function = call["function"]
    if not isinstance(call["id"], str) or not call["id"] or not isinstance(function, dict):
        raise AdapterError("invalid_response")
    if set(function) != {"name", "arguments"} or not isinstance(function["name"], str):
        raise AdapterError("invalid_response")
    name = function["name"]
    if name not in validators or not isinstance(function["arguments"], str):
        raise AdapterError("invalid_response")
    try:
        arguments = load_json(function["arguments"])
        if not isinstance(arguments, dict):
            raise ValueError("invalid_arguments")
        validators[name].validate(arguments)
    except Exception:
        raise AdapterError("invalid_arguments") from None
    return {"tool": name, "arguments": arguments}


def decide(request: Any, model: str = DEFAULT_MODEL, timeout: float = 60) -> dict[str, Any]:
    if any(name in os.environ for name in OVERRIDE_VARIABLES):
        raise AdapterError("invalid_configuration")
    if not isinstance(model, str) or not model or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise AdapterError("invalid_configuration")
    try:
        messages, validators = build_messages(request)
    except AdapterError:
        raise
    except Exception:
        raise AdapterError("invalid_request") from None
    key = os.environ.get("NVIDIA_RECOVERY_API_KEY")
    if not key or not key.strip():
        raise AdapterError("missing_credential")
    configuration = {
        "max_completion_tokens": 2048,
        "max_retries": 0,
        "timeout": timeout,
        "trust_env": False,
        "follow_redirects": False,
    }
    receipt: dict[str, Any] = {
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "provider": "nvidia",
        "base_url": BASE_URL,
        "requested_model": model,
        "request_configuration": configuration,
        "request_body": None,
        "status": "request_started",
    }

    def retain_request(outgoing: httpx.Request) -> None:
        # Retain the SDK-serialized model input, never authentication headers.
        receipt["request_body"] = outgoing.content.decode("utf-8")

    try:
        with httpx.Client(
            trust_env=configuration["trust_env"],
            follow_redirects=configuration["follow_redirects"],
            timeout=configuration["timeout"],
            event_hooks={"request": [retain_request]},
        ) as transport:
            with OpenAI(
                api_key=key,
                base_url=BASE_URL,
                http_client=transport,
                max_retries=configuration["max_retries"],
                timeout=configuration["timeout"],
                organization="",
                project="",
            ) as client:
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=cast(list[ChatCompletionToolParam], request["tools"]),
                    tool_choice="auto" if request["remaining_tool_calls"] else "none",
                    parallel_tool_calls=False,
                    n=1,
                    max_completion_tokens=configuration["max_completion_tokens"],
                )
        response = completion.model_dump(mode="json")
        choices = response.get("choices")
        receipt.update(
            {
                "observed_model": response.get("model"),
                "usage": response.get("usage"),
                "system_fingerprint": response.get("system_fingerprint"),
                "response_id": response.get("id"),
                "assistant_response": choices[0].get("message")
                if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                else None,
                "finish_reason": choices[0].get("finish_reason")
                if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                else None,
            }
        )
        action = parse_action(response, validators, request["remaining_tool_calls"])
    except AdapterError as error:
        receipt["status"] = error.category
        raise AdapterError(error.category, receipt) from None
    except APITimeoutError:
        receipt["status"] = "provider_timeout"
        raise AdapterError("provider_timeout", receipt) from None
    except APIStatusError as error:
        receipt.update(status="provider_status_error", http_status=error.status_code)
        raise AdapterError("provider_status_error", receipt) from None
    except APIConnectionError:
        receipt["status"] = "provider_connection_error"
        raise AdapterError("provider_connection_error", receipt) from None
    except Exception:
        receipt["status"] = "adapter_failure"
        raise AdapterError("adapter_failure", receipt) from None
    receipt["status"] = "completed"
    return {"action": action, "receipt": receipt}


def main() -> None:
    try:
        parser = ArgumentParser(description=__doc__)
        parser.add_argument("--model", default=DEFAULT_MODEL)
        parser.add_argument("--timeout", type=float, default=60)
        args = parser.parse_args()
        try:
            request = load_json(sys.stdin.read())
        except (ValueError, TypeError):
            raise AdapterError("invalid_request") from None
        print(encode(decide(request, args.model, args.timeout)))
    except Exception as error:
        failure: dict[str, Any] = {"adapter_error": "adapter_failure"}
        if isinstance(error, AdapterError):
            failure["adapter_error"] = error.category
            if error.receipt is not None:
                failure["receipt"] = error.receipt
        print(encode(failure), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
