# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scripted EA service controls against the external Workspace Assistant runtime."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def run(native_root, skill, output):
    from benchmark_runtime.workspace_assistant import prepare
    from src.common.domain_config import DomainConfig
    from src.common.partial_coverage_hints import ActionHints
    from src.common.sampler import ActionSequenceNGramModelSampler, LengthDistributionConfig
    from src.stage3_tasks.generate_tasks import _recluster_and_retry, generate_single_task

    output.mkdir(parents=True)
    prepared = prepare(SimpleNamespace(native_root=native_root, output=output / "resources", split="train"))
    config = DomainConfig.from_file(prepared)
    seed = config.seeds()[0]
    candidate, actions = seed.native, seed.actions
    service = config.service()
    native_task = service.build_task(candidate, {})
    positive = service.grade(native_task, actions)
    negative = service.grade(native_task, [])
    assert positive["reward"] == 1 and negative["reward"] == 0
    session, isolated = service.open(native_task), service.open(native_task)
    snapshot = session.snapshot()
    observed = [session.execute(action) for action in actions]
    session.restore(snapshot)
    assert [session.execute(action) for action in actions] == observed
    session.reset()
    assert [session.execute(action) for action in actions] == observed
    assert [isolated.execute(action) for action in actions] == observed
    environment = service.runtime.modules["environment"]
    native_environment = environment.make_environment(initial_state=native_task["initial_state"])
    expected = [
        environment.call(native_environment, action["name"], json.dumps(action["arguments"])) for action in actions
    ]
    assert [(result.content, result.error) for result in observed] == expected
    assert session.execute({"name": "nonexistent", "arguments": {}}).error is not None
    frame = session.env["containers"]["analytics"]._analytics_data
    numeric = next((column for column in frame.columns if str(frame[column].dtype).startswith("float")), None)
    if numeric is not None:
        frame.iloc[0, frame.columns.get_loc(numeric)] = 1.2345678901234567
    snapshot = session.snapshot()
    session.reset()
    session.restore(snapshot)
    assert session.env["containers"]["analytics"]._analytics_data.equals(frame)

    counts = {"patches": 0, "reviews": 0}
    fault = {"fixture": True}

    class ScriptedProvider:
        def __init__(self, **kwargs):
            pass

        def call(self, prompt, stage_name, **kwargs):
            if stage_name == "create_user_task":
                plan = json.JSONDecoder().raw_decode(prompt.split("Reference plan: ", 1)[1])[0]
                reference = [dict(copy.deepcopy(actions[0]), action_id=str(index)) for index in range(len(plan))]
                assert all(action["name"] == actions[0]["name"] for action in plan)
                return json.dumps({"task": candidate["task"], "reference_actions": reference})
            if stage_name == "generate_db_initialization":
                return '{"bad_fixture":true}' if fault["fixture"] else "{}"
            if stage_name == "patch_db":
                counts["patches"] += 1
                return "{}"
            raise AssertionError(stage_name)

    def review(**arguments):
        counts["reviews"] += 1
        assert arguments["reference_outputs"] and arguments["context"]["capabilities"]["grading"] == ["state"]
        return {"accepted": True, "candidate": arguments["candidate"], "fixture": arguments["fixture"]}

    def scripted_agent(**arguments):
        messages = arguments["messages"]
        if any(message["role"] == "tool" for message in messages):
            return {"role": "assistant", "content": "Completed scripted control."}
        count = sum(line.startswith("- ") for line in messages[0]["content"].splitlines())
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": str(index),
                    "type": "function",
                    "function": {"name": actions[0]["name"], "arguments": json.dumps(actions[0]["arguments"])},
                }
                for index in range(count)
            ],
        }

    config.llm_factory = ScriptedProvider
    config.authoring_review = review
    config.agent_completion = scripted_agent
    hints = ActionHints(actions, config.load_policy(), coverage_p=0, shuffle=False).system_prompt
    settings = {"model": "scripted", "coherence_model": "scripted", "max_steps": 5, "max_errors": 3}
    for tables in (None, [], {"email.emails": [None]}):
        invalid = service.validate(candidate, {"tables": tables}, hints=hints, settings=settings)
        assert not invalid.success and invalid.repair_scope == "fixture"
    for response in ([], None):
        config.authoring_review = lambda **kwargs: response
        invalid = service.validate(candidate, {}, hints=hints, settings=settings)
        assert not invalid.success and invalid.failed_step == "authoring_review"

    def broken_review(**kwargs):
        raise ValueError("provider authentication failure")

    config.authoring_review = broken_review
    try:
        service.validate(candidate, {}, hints=hints, settings=settings)
    except ValueError as error:
        assert "authentication" in str(error)
    else:
        raise AssertionError("Provider error was converted into candidate rejection")
    config.authoring_review = review
    config.options["required_grading"] = ["state", "answer"]
    assert service.validate(candidate, {}, hints=hints, settings=settings).unsupported == ("answer",)
    config.options["required_grading"] = ["state"]
    entry = {"action_sequence": [actions[0]["name"]], "cluster_idx": 0}
    generated, metadata = generate_single_task(
        entry, 0, 1, config.domain, "scripted", "scripted", 0, domain_config=config, max_patch_attempts=1
    )
    assert generated is not None and counts["patches"] == 1 and counts["reviews"] > 0, metadata
    assert Path(metadata["validation_evidence"]).is_file()
    export = service.export([generated.to_dict()], output / "native-tasks.jsonl")
    reloaded = service.runtime.modules["tasks"].load_tasks("generated", path=export)[0].row
    assert reloaded["id"] == generated.task.id
    assert service.grade(reloaded, actions)["reward"] == 1
    assert "hints" not in reloaded and "candidate" not in reloaded and "fixture" not in reloaded
    fault["fixture"] = False
    tools = output / "one-tool.json"
    tools.write_text(json.dumps({actions[0]["name"]: {"type": "WRITE", "parameters": {}}}))
    sampler = ActionSequenceNGramModelSampler(
        [],
        domain=config.domain,
        backend_spec=config.to_spec(),
        tool_spec_path=str(tools),
        length_distribution_config=LengthDistributionConfig(mode="empirical", empirical_lengths=[1, 2, 3]),
    )
    sampler.model.ingest_sequence([actions[0]["name"]], weight=1.0, positive=True)
    checkpoint = output / "checkpoint.json"
    sampler.save_state(str(checkpoint))
    clusters = output / "clusters.json"
    clusters.write_text(
        json.dumps(
            {
                "ngram_checkpoint": str(checkpoint),
                "backend_spec": config.to_spec(),
                "length_distribution": sampler._length_config.to_dict(),
            }
        )
    )
    config.sequence_validator_factory = lambda **kwargs: SimpleNamespace(
        validate=lambda sequence: SimpleNamespace(valid=True)
    )
    generated_tasks, metadata_rows = [generated, None], [metadata, {"success": False}]
    _recluster_and_retry(
        [1],
        [entry, dict(entry, cluster_idx=1)],
        generated_tasks,
        metadata_rows,
        str(clusters),
        config.domain,
        "scripted",
        "scripted",
        0,
        str(output / "recovery.jsonl"),
        domain_config=config,
        recluster_pool_size=3,
        max_patch_attempts=0,
    )
    assert generated_tasks[1] is not None and metadata_rows[1]["parent_sequence"] == entry["action_sequence"]
    assert not any(
        name == "tau2" or name.startswith(("tau2.", "src.backends.workspace_assistant")) for name in sys.modules
    )
    receipt = {
        "status": "deterministic_checks_passed",
        "bulk_generation_ready": False,
        "native_positive": positive,
        "native_negative": negative,
        "checks": {
            name: "passed"
            for name in (
                "incorrect_control",
                "reference_execution",
                "snapshot_reset_isolation",
                "native_output_parity",
                "snapshot_precision",
                "fixture_repair",
                "authoring_review",
                "hinted_execution",
                "native_export",
                "recovery",
            )
        },
        "live_model_calls": 0,
    }
    (output / "adapter-proof.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    native_root, skill_root, destination = sys.argv[1:]
    skill = Path(skill_root).resolve()
    sys.path.insert(0, str(skill / "vendor/taste"))
    sys.path.insert(0, str(skill.parents[1] / "eval-author/scripts"))
    run(Path(native_root).resolve(), skill, Path(destination).resolve())
