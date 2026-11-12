# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def controls(root, skill, output):
    sys.path.insert(0, str(skill / "vendor/taste"))
    sys.path.insert(0, str(skill.parents[1] / "eval-author/scripts"))
    from benchmark_runtime.authoring import AuthoringError, validate_shapes
    from benchmark_runtime.tau2 import prepare
    from src.common.domain_config import DomainConfig

    rows = [json.loads(line) for line in (root / "data/benchmark.jsonl").read_text().splitlines()]
    output.mkdir(parents=True, exist_ok=False)
    configs = {}
    for domain in ("airline", "retail", "telecom"):
        tasks = [row["task"] for row in rows if row["config"]["domain"] == domain]
        source = output / f"{domain}-seeds.json"
        source.write_text(json.dumps(tasks))
        prepared = prepare(
            SimpleNamespace(
                native_root=str(root),
                domain=domain,
                data_dir=str(root / "data"),
                seed_path=str(source),
                split="native_control",
                output=str(output / domain),
            )
        )
        config = DomainConfig.from_file(prepared)
        configs[domain] = config
        service = config.service()
        assert service.environment().get_domain_name() == domain
        assert config.task_schema and json.loads(config.get_db_schema_str())
        assert config.load_policy() and json.loads(Path(config.tool_spec_path).read_text())
        assert [seed.id for seed in config.seeds()] == [task["id"] for task in tasks]

    from loguru import logger

    logger.remove()
    config = configs["airline"]
    service = config.service()
    candidate = copy.deepcopy(
        next(
            seed.native
            for seed in config.seeds()
            if any(action["name"] == "cancel_reservation" for action in seed.actions)
        )
    )
    candidate["task"]["evaluation_criteria"]["reward_basis"] = ["DB"]
    task = service.build(candidate, {})
    actions = candidate["reference_actions"]
    assert service.grade(task, actions)["reward"] == 1.0
    assert service.grade(task, [])["reward"] == 0.0
    first, second = service.open(task), service.open(task)
    initial = first.snapshot()
    assert second.snapshot() == initial
    for action in actions:
        actual = first.execute(action)
        assert actual.error is None
    assert first.snapshot() != initial
    assert second.snapshot() == initial
    first.restore(initial)
    assert first.snapshot() == initial
    first.execute(actions[0])
    first.reset()
    assert first.snapshot() == initial
    assert first.execute({"name": "nonexistent", "arguments": {}}).error is not None
    record = {"id": task.id, "candidate": candidate, "fixture": {}, "native_task": task.model_dump(mode="json")}
    native_path = service.export([record], output / "native-tasks.jsonl")
    from tau2.data_model.tasks import Task

    reloaded = Task.model_validate(json.loads(native_path.read_text()))
    assert reloaded == task
    assert service.grade(reloaded, actions)["reward"] == 1.0
    try:
        validate_shapes(config, candidate, {"unrecognized_state": {}})
    except AuthoringError:
        pass
    else:
        raise AssertionError("Unknown native state containers were accepted")

    review_calls = []

    def review(**kwargs):
        assert kwargs["reference_outputs"]["state_changes"]
        review_calls.append(kwargs)
        return {"accepted": True, "candidate": kwargs["candidate"], "fixture": kwargs["fixture"]}

    config.authoring_review = review
    supplied = {}

    class ScriptedSimulation:
        reward_info = SimpleNamespace(reward=1.0)
        termination_reason = "user_stop"

        @staticmethod
        def model_dump(**kwargs):
            return {"scripted_control": True, "reward_info": {"reward": 1.0}, "termination_reason": "user_stop"}

    def conversation(task, *, hints, settings):
        supplied.update(hints=hints, settings=settings)
        assert service.grade(task, actions)["reward"] == 1.0
        return ScriptedSimulation()

    service.run_conversation = conversation
    hints = "Private hint control"
    result = service.validate(candidate, {}, hints=hints, settings={"model": "scripted"})
    assert result.success and result.native_task == task.model_dump(mode="json")
    assert result.evidence_path and Path(result.evidence_path).is_file()
    assert len(review_calls) == 1 and supplied["hints"] == hints
    assert hints not in json.dumps(result.native_task)

    from src.common.sampler.length_distribution import LengthDistributionConfig
    from src.stage3_tasks.generate_tasks import generate_single_task
    from src.stage3_tasks.task_set import TaskSetConfig, TaskSetManager

    class ScriptedAuthor:
        def call(self, *, prompt, stage_name):
            assert len(prompt) < 250_000
            return json.dumps(candidate if stage_name == "create_user_task" else {})

    config.llm_factory = lambda **kwargs: ScriptedAuthor()
    generated, metadata = generate_single_task(
        {"action_sequence": [action["name"] for action in actions]},
        0,
        1,
        config.domain,
        "scripted",
        "scripted",
        0,
        domain_config=config,
    )
    assert generated is not None and metadata["success"]
    receipt = json.loads(Path(metadata["validation_evidence"]).read_text())
    assert receipt["hinted_trace"] and Path(receipt["hinted_trace"]).is_file()
    directory = TaskSetManager(str(output / "task_sets"), domain_config=config).save(
        "scripted",
        [generated.to_dict()],
        TaskSetConfig(
            "scripted", config.domain, "empty", 1, LengthDistributionConfig(mode="from_seed"), "scripted", "prepared"
        ),
        num_generated_tasks=1,
    )
    exported = Task.model_validate(json.loads((Path(directory) / "native-tasks.jsonl").read_text()))
    assert exported.id == "generated_0" and service.grade(exported, actions)["reward"] == 1.0
    limited = service.validate(candidate, {}, hints=hints, settings={"model": "scripted", "write_only": True})
    assert not limited.success and limited.unsupported == ("write_only",)
    ScriptedSimulation.termination_reason = "max_steps"
    exhausted = service.validate(candidate, {}, hints=hints, settings={"model": "scripted"})
    assert not exhausted.success and "max_steps" in exhausted.error
    ScriptedSimulation.termination_reason = "user_stop"

    def provider_failure(**kwargs):
        raise ValueError("provider unavailable")

    config.authoring_review = provider_failure
    try:
        service.validate(candidate, {}, hints=hints, settings={"model": "scripted"})
    except ValueError as error:
        assert "provider unavailable" in str(error)
    else:
        raise AssertionError("Provider failure was treated as a repairable task rejection")
    receipt = {
        "status": "deterministic_checks_passed",
        "checks": {
            "all_domain_preparation": "passed",
            "native_positive_and_negative": "passed",
            "reset_restore_isolation": "passed",
            "native_export_reload": "passed",
            "authoring_reference_observation": "passed",
            "hint_handoff_privacy": "passed",
            "shared_generation_and_native_export": "passed",
            "unsupported_write_only_terminal": "passed",
            "budget_exhaustion_cannot_pass": "passed",
            "infrastructure_fail_fast": "passed",
        },
        "native_conversation_with_user_simulator": "unmeasured",
        "model_calls": "none_scripted_control",
    }
    (output / "runtime-proof.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    native_root, skill_root, destination = sys.argv[1:]
    controls(Path(native_root).resolve(), Path(skill_root).resolve(), Path(destination).resolve())
