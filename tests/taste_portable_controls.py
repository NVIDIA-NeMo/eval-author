# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Independent counter runtime exercising TASTE's shared pipeline with scripted providers."""

import copy
import json
import sys
from pathlib import Path


def run(destination):
    from src.backends.contracts import ValidationResult
    from src.common.domain_config import DomainConfig
    from src.common.sampler import ActionSequenceNGramModelSampler, LengthDistributionConfig
    from src.stage2_cluster.action_sequence_validator import ActionSequenceValidator
    from src.stage3_tasks.generate_tasks import _recluster_and_retry, generate_single_task
    from src.stage3_tasks.task_set import TaskSetConfig, TaskSetManager

    destination.mkdir()
    (destination / "policy.md").write_text("Increment the counter by positive amounts.")
    (destination / "tool_spec.json").write_text(json.dumps({"increment": {"type": "WRITE", "parameters": {}}}))
    action = {"name": "increment", "arguments": {"amount": 1}}
    example = {"task": {"intent": "Increment once."}, "reference_actions": [action]}
    (destination / "seeds.jsonl").write_text(
        json.dumps({"id": "seed", "split": "train", "native": example, "actions": [action]}) + "\n"
    )
    (destination / "task_schema.json").write_text(
        json.dumps({"type": "object", "required": ["intent"], "properties": {"intent": {"type": "string"}}})
    )
    (destination / "fixture_schema.json").write_text(
        json.dumps({"type": "object", "required": ["start"], "properties": {"start": {"type": "integer"}}})
    )
    prompts = destination / "prompts"
    for stage, templates in {
        "stage2": {"validate_action_sequence": "{action_sequence}\n{policy}\n{tool_spec}"},
        "stage3": {
            "create_user_task": "{reference_plan}\n{example_task}\n{policy}",
            "generate_db_initialization": "{task_context}\n{action_arguments}\n{db_schema}",
            "patch_task": "{task_json}\n{error}",
            "patch_db": "{db_json}\n{error}",
        },
    }.items():
        (prompts / stage).mkdir(parents=True)
        for name, template in templates.items():
            (prompts / stage / (name + ".txt")).write_text(template)

    class Service:
        def validate(self, candidate, fixture, *, hints, settings):
            if fixture != {"start": 0}:
                return ValidationResult(
                    False, False, False, "counter needs a start value", "counter_state", repair_scope="fixture"
                )
            actions = candidate["reference_actions"]
            if any(a["arguments"]["amount"] <= 0 for a in actions):
                return ValidationResult(False, True, False, "counter needs positive increments", "counter_operation")
            assert "???" in hints
            # A scripted conversation exercises follow-up user turns and real state changes.
            value = fixture["start"]
            messages = [
                {"role": "user", "content": candidate["task"]["intent"]},
                {"role": "assistant", "content": "Confirm the increment?"},
                {"role": "user", "content": "Confirmed."},
            ]
            for action in actions:
                value += action["arguments"]["amount"]
                messages.append({"role": "tool", "content": str(value)})
            messages.append({"role": "assistant", "content": "Done."})
            native = {
                "key": "new",
                "text": candidate["task"]["intent"],
                "program": [a["arguments"]["amount"] for a in actions],
                "start": fixture["start"],
                "ticket": "preserve",
            }
            return ValidationResult(
                True,
                True,
                True,
                native_task=native,
                gt_simulation={"hints": hints, "messages": messages, "final_state": value},
            )

        def export(self, rows, path):
            path = Path(path)
            native = []
            for row in rows:
                task = copy.deepcopy(row["native_task"])
                task["key"] = row["id"]
                native.append(task)
            path.write_text("\n".join(json.dumps(row) for row in native) + "\n")
            return path

    config = DomainConfig(
        "counter-world",
        resources_dir=str(destination),
        options={"seed_split": "train", "evidence_dir": str(destination / "evidence"), "prompts_dir": str(prompts)},
    )
    config._service = Service()
    counts = {"patch_db": 0, "patch_task": 0, "create_user_task": 0}
    faults = {"fixture": True, "candidate": False, "malformed": 0}

    class Scripted:
        def __init__(self, **kwargs):
            pass

        def call(self, prompt, stage_name, **kwargs):
            if stage_name == "validate_action_sequence":
                return '{"valid":true,"reason":"counter control","problematic_indices":[]}'
            if stage_name == "create_user_task":
                counts[stage_name] += 1
                if faults["malformed"]:
                    faults["malformed"] -= 1
                    return "[]"
                actions = json.JSONDecoder().raw_decode(prompt)[0]
                if faults["candidate"]:
                    actions[0]["arguments"] = {"amount": -1}
                for action in actions:
                    if not isinstance(action["arguments"], dict):
                        action["arguments"] = {"amount": 1}
                return json.dumps({"task": {"intent": "Increment the counter."}, "reference_actions": actions})
            if stage_name == "generate_db_initialization":
                return "{}" if faults["fixture"] else '{"start":0}'
            if stage_name == "patch_db":
                counts[stage_name] += 1
                return '{"start":0}'
            if stage_name == "patch_task":
                counts[stage_name] += 1
                candidate = json.JSONDecoder().raw_decode(prompt)[0]
                candidate["reference_actions"][0]["arguments"]["amount"] = 1
                return json.dumps(candidate)
            raise AssertionError(stage_name)

    config.llm_factory = Scripted
    sequence_validator = ActionSequenceValidator(domain_config=config)
    assert sequence_validator.validate(["increment"]).valid
    lengths = LengthDistributionConfig(mode="empirical", empirical_lengths=[1, 1, 2, 3])
    sampler = ActionSequenceNGramModelSampler(
        config.seeds(),
        domain=config.domain,
        backend_spec=config.to_spec(),
        tool_spec_path=config.tool_spec_path,
        length_distribution_config=lengths,
    )
    sampler.model.ingest_sequence(["increment"], weight=1, positive=True)
    checkpoint = destination / "checkpoint.json"
    sampler.save_state(str(checkpoint))
    restored = ActionSequenceNGramModelSampler.load_state(str(checkpoint))
    assert restored._length_config is not None
    assert restored._length_config.empirical_lengths == [1, 1, 2, 3]
    entry = {"action_sequence": ["increment"], "cluster_idx": 0}
    generated, metadata = generate_single_task(
        entry, 0, 1, config.domain, "scripted", "scripted", 0, domain_config=config, max_patch_attempts=1
    )
    assert generated is not None and counts["patch_db"] == 1 and counts["patch_task"] == 0
    faults.update(fixture=False, candidate=True)
    candidate, _ = generate_single_task(
        entry, 1, 2, config.domain, "scripted", "scripted", 0, domain_config=config, max_patch_attempts=1
    )
    assert candidate is not None and counts["patch_task"] == 1
    faults["candidate"] = False
    faults["malformed"] = 1
    before = counts["create_user_task"]
    regenerated, _ = generate_single_task(
        entry, 0, 1, config.domain, "scripted", "scripted", 1, domain_config=config, max_patch_attempts=0
    )
    assert regenerated is not None and counts["create_user_task"] == before + 2
    faults["malformed"] = 2
    exhausted, failure = generate_single_task(
        entry, 0, 1, config.domain, "scripted", "scripted", 1, domain_config=config, max_patch_attempts=0
    )
    assert exhausted is None and failure["retries_used"] == 1 and "JSON object" in failure["error"]
    artifact = destination / "clusters.json"
    artifact.write_text(
        json.dumps(
            {
                "ngram_checkpoint": str(checkpoint),
                "backend_spec": config.to_spec(),
                "length_distribution": lengths.to_dict(),
            }
        )
    )
    config.sequence_validator_factory = lambda **kwargs: sequence_validator
    generated_tasks, metadata_rows = [generated, None], [metadata, {"success": False}]
    _recluster_and_retry(
        [1],
        [entry, dict(entry, cluster_idx=1)],
        generated_tasks,
        metadata_rows,
        str(artifact),
        config.domain,
        "scripted",
        "scripted",
        0,
        str(destination / "progress.jsonl"),
        domain_config=config,
        recluster_pool_size=3,
        max_patch_attempts=0,
    )
    assert generated_tasks[1] is not None and metadata_rows[1]["parent_sequence"] == ["increment"]
    manager = TaskSetManager(str(destination / "sets"), domain_config=config)
    rows = [task.to_dict() for task in generated_tasks]
    task_config = TaskSetConfig("counter", config.domain, "empty", 2, lengths, "scripted", "prepared")
    saved = Path(manager.save("counter", rows, task_config, num_generated_tasks=2))
    assert json.loads((saved / "tasks.json").read_text()) == rows
    native = json.loads((saved / "native-tasks.jsonl").read_text().splitlines()[0])
    assert native["ticket"] == "preserve" and native["key"] == rows[0]["id"]
    assert "???" not in json.dumps(native)
    assert "hints" not in native
    assert not any(
        name.startswith(("src.backends.tau2", "src.backends.workspace_assistant", "tau2", "litellm"))
        for name in sys.modules
    )
    print(
        "Independent backend: sampling, sequence validation, fixture/candidate repair, recovery, hints, replay, grading and export passed."
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]))
