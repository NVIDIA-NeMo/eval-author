# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class InfrastructureError(RuntimeError):
    pass


class UnsupportedCapability(ValueError):
    pass


class AuthoringError(ValueError):
    pass


@dataclass(frozen=True)
class ToolResult:
    content: str
    error: str | None = None


def validate_shapes(config, candidate, fixture):
    import jsonschema

    if not isinstance(candidate, dict) or set(candidate) != {"task", "reference_actions"}:
        raise AuthoringError("Candidate requires exactly task and reference_actions")
    if not isinstance(candidate["task"], dict) or not isinstance(candidate["reference_actions"], list):
        raise AuthoringError("Candidate task must be an object and reference_actions must be an ordered array")
    try:
        jsonschema.validate(candidate["task"], config.task_schema)
    except jsonschema.ValidationError as error:
        raise AuthoringError(f"Candidate schema: {error.message}") from error
    if not isinstance(fixture, dict):
        raise AuthoringError("Fixture must be a JSON object")
    try:
        jsonschema.validate(fixture, json.loads(config.get_db_schema_str()))
    except jsonschema.ValidationError as error:
        raise AuthoringError(f"Fixture schema: {error.message}") from error
    for action in candidate["reference_actions"]:
        if not isinstance(action, dict) or not isinstance(action.get("name"), str) or not action["name"]:
            raise AuthoringError("Reference actions require a nonempty tool name")
        if not isinstance(action.get("arguments"), dict):
            raise AuthoringError("Reference action arguments must be an object")


def review_candidate(config, candidate, fixture, *, settings, reference_outputs=None):
    validate_shapes(config, candidate, fixture)
    context = config.context
    callback = getattr(config, "authoring_review", None)
    arguments = {
        "candidate": copy.deepcopy(candidate),
        "fixture": copy.deepcopy(fixture),
        "context": copy.deepcopy(context),
        "settings": copy.deepcopy(settings),
        "reference_outputs": copy.deepcopy(reference_outputs),
    }
    if callback is not None:
        response = callback(**arguments)
    else:
        caller = config.make_llm(
            model_name=settings.get("coherence_model") or settings["model"],
            max_output_tokens=settings.get("max_output_tokens", 65536),
        )
        prompt = (
            "Review and align this candidate with the native execution semantics, policy, starting fixture, and "
            "observed reference results. Preserve the exact reference_actions, their arguments and order. "
            "Ensure the public request/user scenario is solvable with the available tools and interaction mode; "
            "keep hidden references and fixture-only facts private. Where the task schema supports native "
            "assertions or success criteria, synthesize all necessary task-specific checks from the observed "
            "outcome, including user-side state where applicable. Preserve every intended requirement and constraint; "
            "never drop requested outcomes or verification checks to obtain a pass. Align wording and data with "
            "the actual end state only where the intended scope is preserved. Reject unsupported required grading "
            "or a task that cannot be repaired without changing reference actions. "
            "Return one JSON object with accepted (boolean), candidate, fixture, issues (array), and "
            "unsupported (array of required capabilities the native runtime cannot provide). "
            "If accepted is true, include the complete candidate and fixture conforming to the supplied schemas.\n"
            + json.dumps(
                {
                    **arguments,
                    "policy": config.load_policy(),
                    "task_schema": config.task_schema,
                    "fixture_schema": json.loads(config.get_db_schema_str()),
                },
                ensure_ascii=False,
            )
        )
        # Provider failures propagate. Only malformed model output is a candidate error.
        response = caller.call(prompt=prompt, stage_name="authoring_review")
    if isinstance(response, str):
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        try:
            response = json.loads(text)
        except json.JSONDecodeError as error:
            raise AuthoringError(f"Invalid authoring review JSON: {error}") from error
    if isinstance(response, dict) and response.get("unsupported"):
        raise UnsupportedCapability(f"Required native capabilities are unsupported: {response['unsupported']}")
    if not isinstance(response, dict) or response.get("accepted") is not True:
        raise AuthoringError(f"Native authoring review rejected candidate: {response}")
    reviewed = response.get("candidate")
    reviewed_fixture = response.get("fixture")
    validate_shapes(config, reviewed, reviewed_fixture)
    if reviewed["reference_actions"] != candidate["reference_actions"]:
        raise AuthoringError("Authoring review changed the reference actions used by the supplied hints")
    return copy.deepcopy(reviewed), copy.deepcopy(reviewed_fixture)


def complete(*, model, messages, tools):
    import litellm

    result = litellm.completion(model=model, messages=messages, tools=tools, temperature=0.0)
    return cast(Any, result).choices[0].message.model_dump(exclude_none=True)


def run_fixed_request(session, messages, tools, *, settings, completion=complete):
    messages = copy.deepcopy(messages)
    predicted = []
    errors = 0
    terminated = False
    for _ in range(settings.get("max_steps", 100)):
        message = completion(model=settings["model"], messages=copy.deepcopy(messages), tools=copy.deepcopy(tools))
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            terminated = True
            break
        for call in calls:
            function = call["function"]
            try:
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise AuthoringError("tool arguments must be an object")
            except (ValueError, TypeError) as error:
                content, diagnostic = json.dumps({"error": str(error)}), str(error)
            else:
                action = {"name": function["name"], "arguments": arguments, "requestor": "assistant"}
                predicted.append(action)
                result = session.execute(action)
                content, diagnostic = result.content, result.error
            errors += diagnostic is not None
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        if errors >= settings.get("max_errors", 10):
            break
    return {
        "messages": messages,
        "actions": predicted,
        "terminated": terminated,
        "final_answer": (messages[-1].get("content") or "") if terminated else "",
    }


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_trace(config, trace, *, prefix="hinted"):
    directory = config.options.get("evidence_dir")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{prefix}-{uuid.uuid4().hex}.json"
        trace["artifact_path"] = str(target)
        target.write_text(json.dumps(trace, indent=2) + "\n")
    return trace
