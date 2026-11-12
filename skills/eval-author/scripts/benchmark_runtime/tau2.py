# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from benchmark_runtime.authoring import (
    AuthoringError,
    InfrastructureError,
    ToolResult,
    UnsupportedCapability,
    digest,
    review_candidate,
    validate_shapes,
    write_trace,
)


def _activate(options):
    root = Path(options["tau2_root"]).resolve()
    package_root = next((root / name for name in ("src", "vendor") if (root / name / "tau2").is_dir()), None)
    if package_root is None:
        raise UnsupportedCapability(f"Tau2 source package not found under {root}")
    if options.get("data_dir"):
        data_dir = Path(options["data_dir"]).resolve()
        loaded_utils = sys.modules.get("tau2.utils")
        if loaded_utils is not None and Path(loaded_utils.DATA_DIR).resolve() != data_dir:
            raise InfrastructureError("A different Tau2 data directory is already loaded; use a fresh process")
        os.environ["TAU2_DATA_DIR"] = str(data_dir)
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    module = importlib.import_module("tau2")
    if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(package_root):
        raise InfrastructureError("A different Tau2 runtime is already loaded; use a fresh process")


class Service:
    def __init__(self, config):
        self.config = config
        _activate(config.options)
        module_name = config.options.get("environment_module", f"tau2.domains.{config.domain}.environment")
        self.module = importlib.import_module(module_name)
        self.constructor = getattr(self.module, config.options.get("environment_function", "get_environment"))
        context = getattr(config, "context", {})
        self.baseline_digest = context.get("baseline_state_sha256")
        provenance = context.get("native_runtime", {})
        if provenance and provenance["source_sha256"] != _source_digest(Path(config.options["tau2_root"])):
            raise InfrastructureError("Tau2 source changed after preparation; prepare a fresh input bundle")

    def environment(self, **kwargs):
        env = self.constructor(**(self.config.options.get("environment_options", {}) | kwargs))
        if self.baseline_digest and "db" not in kwargs and "user_db" not in kwargs:
            baseline = {
                "agent_data": env.tools.db.model_dump(mode="json"),
                "user_data": env.user_tools.db.model_dump(mode="json") if env.user_tools else None,
            }
            if digest(baseline) != self.baseline_digest:
                raise InfrastructureError("Tau2 baseline data changed after preparation; prepare a fresh input bundle")
        return env

    @staticmethod
    def build(candidate, fixture):
        from tau2.data_model.tasks import Task

        if set(fixture) - {"agent_data", "user_data"}:
            raise AuthoringError("Tau2 fixture supports only agent_data and user_data")
        row = copy.deepcopy(candidate["task"])
        row.setdefault("id", "generated_unassigned")
        criteria = row.setdefault("evaluation_criteria", {})
        if not isinstance(criteria, dict) or not criteria.get("reward_basis"):
            raise ValueError("Tau2 tasks require explicit evaluation_criteria.reward_basis")
        criteria["actions"] = [
            {"action_id": str(index), "requestor": "assistant", **action}
            for index, action in enumerate(candidate["reference_actions"])
        ]
        state = row.get("initial_state") or {}
        state["initialization_data"] = copy.deepcopy(fixture)
        row["initial_state"] = state
        return Task.model_validate(row)

    def open(self, task):
        return Session(self, task)

    def replay(self, task, actions):
        from tau2.data_model.message import AssistantMessage, UserMessage

        session = self.open(task)
        initial = session.snapshot()
        messages = copy.deepcopy(task.initial_state.message_history or []) if task.initial_state else []
        for action in actions:
            call, response = session.exchange(action)
            kind = UserMessage if call.requestor == "user" else AssistantMessage
            messages.extend([kind(role=call.requestor, tool_calls=[call]), response])
            if response.error:
                return {"reward": 0.0, "error": response.content, "messages": messages}
        return {"reward": 1.0, "messages": messages, "state_changes": _state_changes(initial, session.snapshot())}

    def grade(self, task, actions):
        from tau2.data_model.tasks import RewardType
        from tau2.evaluator.evaluator_env import EnvironmentEvaluator

        reference = self.replay(task, actions)
        if reference["reward"] != 1.0:
            return reference
        messages = reference["messages"]
        basis = set(task.evaluation_criteria.reward_basis)
        components = {}
        if basis & {RewardType.DB, RewardType.ENV_ASSERTION}:
            components["environment"] = EnvironmentEvaluator.calculate_reward(
                environment_constructor=self.environment, task=task, full_trajectory=messages
            )
        if RewardType.ACTION in basis:
            from tau2.evaluator.evaluator_action import ActionEvaluator

            components["actions"] = ActionEvaluator.calculate_reward(task=task, full_trajectory=messages)
        reward = 1.0
        for component in components.values():
            reward *= component.reward
        return {
            "reward": reward,
            "messages": messages,
            "state_changes": reference["state_changes"],
            "components": {name: value.model_dump(mode="json") for name, value in components.items()},
            "conversation_criteria_pending": bool(basis & {RewardType.COMMUNICATE, RewardType.NL_ASSERTION}),
        }

    def run_conversation(self, task, *, hints, settings):
        expected_module = f"tau2.domains.{self.config.domain}.environment"
        if (
            self.config.options.get("environment_module", expected_module) != expected_module
            or self.config.options.get("environment_function", "get_environment") != "get_environment"
            or self.config.options.get("environment_options")
        ):
            raise UnsupportedCapability("Hinted conversations require the canonical native Tau2 environment")
        try:
            from tau2.agent.llm_agent import LLMAgent
            from tau2.registry import registry
            from tau2.run import run_task
        except ModuleNotFoundError as error:
            if (error.name or "").startswith("tau2."):
                raise UnsupportedCapability("Hinted conversations require the full external Tau2 runtime") from error
            raise InfrastructureError(f"Install the Tau2 runtime dependency: {error.name}") from error

        class HintedAgent(LLMAgent):
            @property
            def system_prompt(self):
                return super().system_prompt + "\n\n" + hints

        identity = hashlib.sha256(hints.encode()).hexdigest()[:24]
        agent_name = f"eval_author_hinted_{identity}"
        if agent_name not in registry.get_agents():
            if hasattr(registry, "register_agent_factory"):
                accepted = set(inspect.signature(LLMAgent).parameters)
                registry.register_agent_factory(
                    lambda **kwargs: HintedAgent(**{key: value for key, value in kwargs.items() if key in accepted}),
                    agent_name,
                )
            else:
                registry.register_agent(HintedAgent, agent_name)
        model = settings["model"]
        try:
            return run_task(
                # Native language bindings and evaluation both resolve the canonical domain.
                domain=self.config.domain,
                task=task,
                agent=agent_name,
                user="user_simulator",
                llm_agent=model,
                llm_user=self.config.options.get("user_model", model),
                max_steps=settings.get("max_steps", 100),
                max_errors=settings.get("max_errors", 10),
            )
        except Exception as error:
            raise InfrastructureError(f"Native Tau2 conversation failed: {error}") from error

    @staticmethod
    def conversation_result(simulation):
        reason = simulation.termination_reason
        return {
            "termination": getattr(reason, "value", reason),
            "reward": simulation.reward_info.reward,
            "payload": simulation.model_dump(mode="json"),
        }

    def validate(self, candidate, fixture, *, hints, settings):
        from src.backends.contracts import ValidationResult

        if settings.get("write_only"):
            return ValidationResult(
                False,
                False,
                False,
                error="Native Tau2 service requires full reference actions",
                unsupported=("write_only",),
            )
        try:
            validate_shapes(self.config, candidate, fixture)
            task = self.build(candidate, fixture)
        except ValueError as error:
            return ValidationResult(False, False, False, error=str(error), failed_step="authoring")
        try:
            observed = self.replay(task, candidate["reference_actions"])
        except (ValueError, AssertionError) as error:
            return ValidationResult(False, False, False, error=str(error), failed_step="reference")
        if observed["reward"] != 1.0:
            return ValidationResult(False, True, False, error=observed["error"], failed_step="reference")
        observed["messages"] = [message.model_dump(mode="json") for message in observed["messages"]]
        try:
            candidate, fixture = review_candidate(
                self.config, candidate, fixture, settings=settings, reference_outputs=observed
            )
        except AuthoringError as error:
            return ValidationResult(False, True, False, error=str(error), failed_step="authoring")
        except UnsupportedCapability as error:
            return ValidationResult(False, True, False, error=str(error), unsupported=(str(error),))
        try:
            task = self.build(candidate, fixture)
            reference = self.grade(task, candidate["reference_actions"])
        except (ValueError, AssertionError) as error:
            return ValidationResult(False, False, False, error=str(error), failed_step="reference")
        native_task = task.model_dump(mode="json")
        artifacts = {"native_task": native_task, "candidate": candidate, "fixture": fixture}
        if reference["reward"] != 1.0:
            return ValidationResult(
                False,
                True,
                False,
                error=reference.get("error") or f"Reference grading failed: {reference['components']}",
                failed_step="reference",
                **artifacts,
            )
        try:
            simulation = self.run_conversation(task, hints=hints, settings=settings)
        except UnsupportedCapability as error:
            return ValidationResult(False, True, False, error=str(error), unsupported=(str(error),), **artifacts)
        outcome = self.conversation_result(simulation)
        reason = outcome["termination"]
        success = outcome["reward"] == 1.0 and reason in {"user_stop", "agent_stop"}
        reference["messages"] = [message.model_dump(mode="json") for message in reference["messages"]]
        trace = write_trace(
            self.config,
            {"reference": reference, "hints": hints, "conversation": outcome["payload"]},
        )
        if reason in {
            "agent_error",
            "user_error",
            "infrastructure_error",
            "context_window_exceeded",
            "unexpected_error",
        }:
            raise InfrastructureError(
                f"Native Tau2 conversation terminated with {reason}; trace={trace.get('artifact_path')}"
            )
        return ValidationResult(
            success,
            True,
            success,
            error=None if success else f"Native conversation reward: {outcome['reward']}; termination: {reason}",
            failed_step=None if success else "gt_agent",
            gt_simulation=trace,
            evidence_path=trace.get("artifact_path"),
            **artifacts,
        )

    @staticmethod
    def export(rows, path):
        from tau2.data_model.tasks import Task

        parsed = []
        for row in rows:
            native = copy.deepcopy(row.get("native_task", row))
            native["id"] = row["id"]
            parsed.append(Task.model_validate(native).model_dump(mode="json"))
        path = Path(path)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in parsed))
        return path


class Session:
    def __init__(self, service, task):
        from tau2.data_model.tasks import Task

        self.service = service
        self.task = Task.model_validate(task) if isinstance(task, dict) else task.model_copy(deep=True)
        self.reset()

    def reset(self):
        self.env = self.service.environment()
        state = self.task.initial_state
        self.env.set_state(
            initialization_data=state.initialization_data if state else None,
            initialization_actions=state.initialization_actions if state else None,
            message_history=(state.message_history or []) if state else [],
        )

    def exchange(self, action):
        from tau2.data_model.message import ToolCall

        call = ToolCall(
            id=str(action.get("action_id", "reference")),
            name=action["name"],
            arguments=copy.deepcopy(action["arguments"]),
            requestor=action.get("requestor", "assistant"),
        )
        return call, self.env.get_response(call)

    def execute(self, action):
        _, response = self.exchange(action)
        return ToolResult(response.content, response.content if response.error else None)

    def snapshot(self):
        return {
            "agent_data": self.env.tools.db.model_dump(mode="json"),
            "user_data": self.env.user_tools.db.model_dump(mode="json") if self.env.user_tools else None,
        }

    def restore(self, state):
        db = type(self.env.tools.db).model_validate(copy.deepcopy(state["agent_data"]))
        kwargs = {"db": db}
        if self.env.user_tools:
            kwargs["user_db"] = type(self.env.user_tools.db).model_validate(copy.deepcopy(state["user_data"]))
        self.env = self.service.environment(**kwargs)


def _state_schema(model, container):
    schema = model.model_json_schema()
    schema.pop("required", None)
    schema["additionalProperties"] = False

    def relocate(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                    value[key] = item.replace("#/$defs/", f"#/properties/{container}/$defs/", 1)
                else:
                    relocate(item)
        elif isinstance(value, list):
            for item in value:
                relocate(item)

    relocate(schema)
    return schema


def _state_changes(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                changes.append({"path": [*path, key], "before": before.get(key), "after": after.get(key)})
            else:
                changes.extend(_state_changes(before[key], after[key], (*path, key)))
        return changes
    return [] if before == after else [{"path": list(path), "before": before, "after": after}]


def _state_examples(state):
    return {
        name: ({key: table[key] for key in list(table)[:2]} if isinstance(table, dict) else table[:2])
        if isinstance(table, (dict, list))
        else table
        for name, table in state.items()
    }


def prepare(args):
    options = {"tau2_root": str(Path(args.native_root).resolve())}
    if args.data_dir:
        options["data_dir"] = str(Path(args.data_dir).resolve())
    config = SimpleNamespace(domain=args.domain, options=options)
    service = Service(config)
    env = service.environment()
    from tau2.data_model.tasks import Task

    if args.seed_path:
        source = Path(args.seed_path)
        records = (
            [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
            if source.suffix == ".jsonl"
            else json.loads(source.read_text())
        )
        tasks = [Task.model_validate(row.get("task", row)) for row in records]
    else:
        tasks = service.module.get_tasks(args.split)
    if not tasks:
        raise ValueError("Selected Tau2 seed split is empty")
    seeds = []
    for task in tasks:
        native = task.model_dump(mode="json")
        actions = (native.get("evaluation_criteria") or {}).get("actions") or []
        seeds.append(
            {
                "id": task.id,
                "split": args.split,
                "native": {"task": native, "reference_actions": actions},
                "actions": actions,
            }
        )
    tools = {}
    for requestor, entries in (
        ("assistant", env.get_tools()),
        ("user", env.get_user_tools() if env.user_tools else []),
    ):
        for tool in entries:
            schema = tool.openai_schema
            toolkit = env.tools if requestor == "assistant" else env.user_tools
            tool_type = toolkit.tool_type(tool.name).value.upper()
            if tool_type not in {"READ", "WRITE", "GENERIC"}:
                raise UnsupportedCapability(f"Unsupported native tool type: {tool.name}={tool_type}")
            if tool.name in tools:
                raise UnsupportedCapability(f"Ambiguous tool name shared by participants: {tool.name}")
            tools[tool.name] = schema["function"] | {"requestor": requestor, "type": tool_type}
    properties = {"agent_data": _state_schema(type(env.tools.db), "agent_data")}
    if env.user_tools:
        properties["user_data"] = _state_schema(type(env.user_tools.db), "user_data")
    fixture_schema = {"type": "object", "properties": properties, "additionalProperties": False}
    baseline = {
        "agent_data": env.tools.db.model_dump(mode="json"),
        "user_data": env.user_tools.db.model_dump(mode="json") if env.user_tools else None,
    }
    mini = getattr(args, "dialogue_runner", "full") == "mini"
    mini_context = None
    if mini:
        from benchmark_runtime.tau2_mini import prepare_options

        additions, mini_context = prepare_options(args)
        options.update(additions)
    directory = Path(args.output).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    options.update(
        seed_path=str(directory / "seeds.jsonl"), seed_split=args.split, evidence_dir=str(directory / "evidence")
    )
    spec = {
        "schema": "taste.prepared-inputs.v1",
        "domain": args.domain,
        "resources_dir": str(directory),
        "options": options,
        "task_service": "benchmark_runtime.tau2_mini:MiniService" if mini else "benchmark_runtime.tau2:Service",
    }
    (directory / "policy.md").write_text(env.get_policy())
    (directory / "seeds.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in seeds))
    files = {
        "prepared.json": spec,
        "tool_spec.json": tools,
        "task_schema.json": Task.model_json_schema(),
        "fixture_schema.json": fixture_schema,
        "context.json": {
            "native_runtime": {
                "root": options["tau2_root"],
                "source_sha256": _source_digest(Path(options["tau2_root"])),
            },
            "baseline_state_sha256": digest(baseline),
            "baseline_state_examples": {
                key: _state_examples(value) for key, value in baseline.items() if value is not None
            },
            "fixture_semantics": "agent_data/user_data updates are applied by the native environment to baseline state.",
            "authoring_requirements": (
                "Supply explicit native reward_basis and criteria. Align user instructions, reference actions, "
                "initialization data, and native success criteria. Fixtures own initial_state.initialization_data; "
                "reference_actions own evaluation_criteria.actions. Do not place private hints in the user scenario."
            ),
        },
    }
    if mini_context is not None:
        files["context.json"]["mini_runtime"] = mini_context
    for name, value in files.items():
        (directory / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    return directory / "prepared.json"


def _source_digest(root):
    package_root = next(root / name / "tau2" for name in ("src", "vendor") if (root / name / "tau2").is_dir())
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(str(path.relative_to(package_root)).encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Prepare Tau2 inputs and native execution for TASTE")
    parser.add_argument("--native-root", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--seed-path")
    parser.add_argument("--split", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dialogue-runner", choices=("full", "mini"), default="full")
    parser.add_argument("--native-python", help="Selected mini interpreter; required for --dialogue-runner mini")
    parser.add_argument("--mini-config", help="Recorded native endpoint/limit JSON; required for mini")
    print(prepare(parser.parse_args()))


if __name__ == "__main__":
    main()
