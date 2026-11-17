# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neutral scenarios retain their inputs while current execution claims expire."""

import copy
import hashlib
import importlib
from pathlib import Path

import pytest
import test_first_eval_rendering as conversations
import test_first_eval_state as creation
from test_html_report import report, task, write

modules = creation.modules
rendering = conversations.creation

SCENARIO = (
    "schema_version: 1\ntask_id: refund\ngoal: Return the headphones\n"
    "start_prompt: I want to return my headphones.\nuser_facts:\n  order: PRIVATE-ORDER-2084\n"
)
SIMULATOR_SETTINGS = {"agent_model": "simulator / configured-user-model", "conversation_limits": "180 seconds total"}


def design() -> list[dict]:
    return [
        {
            "id": "refund",
            "example_ids": ["refund-1"],
            "test_type": "user_sim",
            "test_type_reason": "Confirm the agent's actual proposed return.",
            "state_requirements": "Keep the same customer session.",
            "conversation_scenario": "Provide the order when asked, then confirm the correct return.",
        }
    ]


def translated_inputs(repo: Path) -> dict:
    paths = [
        ".eval-author/tasks/refund/user-sim.yaml",
        ".eval-author/native.yaml",
        ".eval-author/.working/user-sim/run-1/run.yaml",
        ".eval-author/.working/user-sim/run-1/persona.txt",
        ".eval-author/.working/user-sim/run-1/user-prompt.md",
        ".eval-author/.working/user-sim/run-1/bridge-prompt.md",
        "prompts/target.md",
    ]
    for path in paths:
        creation.evidence(repo, path, SCENARIO if path.endswith("user-sim.yaml") else "retained input " + path)
    return {
        "schema_version": 1,
        "task_id": "refund",
        "user_type": "neutral",
        "runtime_ready": False,
        "run_config_path": str(repo / paths[2]),
        "working_directory": str(repo),
        "input_paths": [str(repo / path) for path in paths],
    }


def selected_user_sim(modules, repo: Path) -> dict:
    translated = translated_inputs(repo)
    value = creation.ready(modules, repo, examples=design())
    bindings = modules[1].bind_user_sim_translation(repo, translated)
    bindings["bridge"] = {
        "source": "Synthetic proof fixture for state tests",
        "evidence": [creation.evidence(repo, ".eval-author/.working/interactive-proof.json")],
    }
    creation.select(modules, repo, value, user_sim_bindings=bindings, user_sim_settings=SIMULATOR_SETTINGS.copy())
    return value


def test_translation_binding_covers_config_and_consumed_inputs_without_runtime_authority(modules, tmp_path):
    translated = translated_inputs(tmp_path)
    binding = modules[1].bind_user_sim_translation(tmp_path, translated)
    references = [binding["config"], *binding["inputs"]]
    assert {str(tmp_path / ref["path"]) for ref in references} == set(translated["input_paths"])
    assert all(ref["digest"] == hashlib.sha256((tmp_path / ref["path"]).read_bytes()).hexdigest() for ref in references)
    assert binding["user_sim"]["working_directory"] == "."
    assert binding["user_sim"]["scenario"] in binding["inputs"]
    assert "authorization" not in binding and "bridge" not in binding


@pytest.mark.parametrize("location", ["input", "working_directory", "symlink"])
def test_translation_binding_rejects_outside_or_symlink_inputs_before_reading(modules, tmp_path, monkeypatch, location):
    translated = translated_inputs(tmp_path)
    if location == "input":
        translated["input_paths"].append(str(tmp_path.parent / "outside-prompt.md"))
    elif location == "working_directory":
        translated["working_directory"] = str(tmp_path.parent)
    else:
        link = tmp_path / "linked-prompt.md"
        link.symlink_to(tmp_path / "prompts/target.md")
        translated["input_paths"].append(str(link))
    monkeypatch.setattr(
        modules[0], "_file_digest", lambda *_: pytest.fail("Read before validating all input locations")
    )
    with pytest.raises(ValueError, match="inside the repository|symlink"):
        modules[1].bind_user_sim_translation(tmp_path, translated)


@pytest.mark.parametrize(
    "change", ["model", "limits", "native_config", "generated_prompt", "external_prompt", "scenario", "proof"]
)
def test_user_sim_changes_retain_runs_but_expire_current_execution_claims(modules, tmp_path, change):
    value = selected_user_sim(modules, tmp_path)
    creation.run(modules, tmp_path, value)
    creation.review_run(modules, tmp_path, value)
    creation.save(modules, tmp_path, value, journey_status={"create": "complete"})
    prior = creation.facts(modules, tmp_path)
    history = copy.deepcopy(value["runs"])
    review = copy.deepcopy(value["run_review"])
    assert prior["complete"] and prior["suite"]["planned_attempts"] == 1
    if change in {"model", "limits"}:
        key = "agent_model" if change == "model" else "conversation_limits"
        value["execution_settings"]["user_sim"][key] = "changed setting"
        creation.save(modules, tmp_path, value, journey_status={"create": "in_progress"})
    else:
        paths = {
            "native_config": ".eval-author/native.yaml",
            "generated_prompt": ".eval-author/.working/user-sim/run-1/user-prompt.md",
            "external_prompt": "prompts/target.md",
            "scenario": ".eval-author/tasks/refund/user-sim.yaml",
            "proof": ".eval-author/.working/interactive-proof.json",
        }
        (tmp_path / paths[change]).write_text("changed bytes")
    before = modules[0].state_path(tmp_path).read_bytes()
    loaded = modules[0].load_state(tmp_path)
    progress = modules[0].view(loaded)["first_eval"]
    saved = modules[1].record(loaded)
    assert saved["runs"] == history and saved["run_review"] == review
    assert not progress["complete"] and not progress["selection_current"]
    assert not progress["execution_authorized"] and not progress["reviewed_run_current"]
    assert progress["suite"]["completed_attempts"] == 0
    assert progress["first_task_approved"] == (change != "scenario")
    assert progress["plan_digest"] == prior["plan_digest"]
    assert modules[0].state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("missing", ["settings", "binding", "proof"])
def test_translated_user_sim_cannot_replace_explicit_settings_or_observed_runner_proof(modules, tmp_path, missing):
    selected_user_sim(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    value = modules[1].record(state)
    if missing == "settings":
        value["execution_settings"].pop("user_sim")
    else:
        value["selection"].pop("user_sim" if missing == "binding" else "bridge")
    progress = modules[1].derive(state, modules[1].observe(tmp_path, state))
    assert progress["scope_approved"] and progress["first_task_approved"]
    assert not progress["selection_current"] and not progress["execution_authorized"]


@pytest.mark.parametrize(
    "invalid", [{"agent_model": "user"}, {"agent_model": "user", "conversation_limits": ""}, {"user_type": "impatient"}]
)
def test_simulator_display_settings_require_identity_and_conversation_limits(modules, tmp_path, invalid):
    value = selected_user_sim(modules, tmp_path)
    value["execution_settings"]["user_sim"] = invalid
    with pytest.raises(ValueError, match="Execution settings require"):
        creation.save(modules, tmp_path, value)


def test_authored_report_and_review_keep_test_type_and_bound_neutral_scenario(modules, tmp_path):
    translated_inputs(tmp_path)
    value = creation.start(modules, tmp_path, examples=design())
    creation.approve(modules, tmp_path, value)
    task(tmp_path, name="refund")
    write(
        tmp_path,
        ".eval-author/tasks/refund/README.md",
        "# Refund\n## Behavior under test\nConfirm before returning.\n## Expected result\nOnly the confirmed return is saved.\n",
    )
    value["tasks"]["refund"] = {
        "path": ".eval-author/tasks/refund",
        "sha256": modules[0].task_digest(tmp_path, ".eval-author/tasks/refund"),
        "review": None,
        "environment": None,
        "validation": None,
        "limitations": [],
    }
    state = creation.save(modules, tmp_path, value)
    snapshot = report.refresh(tmp_path)
    row = snapshot["tasks"][0]
    assert row["test_type"] == "user_sim" and row["user_sim_scenario"]["user_type"] == "neutral"
    table = importlib.import_module("first_eval_table_data")
    rows = table.authored_rows(tmp_path, state, ["refund"])
    observed = modules[1].observe(tmp_path, state)
    flow = importlib.import_module("conversation_flows.first_eval")
    checked = flow.authored_rows(value, {"task_rows": rows}, observed, ["refund"])
    output = flow.task_table(checked)
    assert "User-sim" in output and "Neutral scenario: refund" in output
    assert ".eval-author/tasks/refund/user-sim.yaml" in output
    assert "PRIVATE-ORDER-2084" not in output
    markdown = (tmp_path / ".eval-author/report_markdown/tasks.md").read_text()
    assert "Neutral scenario: refund" in markdown and "PRIVATE-ORDER-2084" not in markdown
    assert snapshot["plan_counts"] == {"proposed": 0, "authored": 1, "validated": 0, "executed": 0}
    tampered = copy.deepcopy(rows)
    tampered[0]["user_sim_scenario"]["digest"] = "b" * 64
    with pytest.raises(ValueError, match="current neutral scenario identity"):
        flow.authored_rows(value, {"task_rows": tampered}, observed, ["refund"])
    (tmp_path / ".eval-author/tasks/refund/user-sim.yaml").write_text(SCENARIO.replace("2084", "2085"))
    with pytest.raises(ValueError, match="differs from its saved task revision"):
        table.authored_rows(tmp_path, state, ["refund"])


def test_settings_and_results_show_simulator_identity_without_adding_example_counts(rendering):
    state, record, helper, renderer, _ = rendering
    record["plan"]["tasks"][0].update(
        {key: value for key, value in design()[0].items() if key not in {"id", "example_ids"}}
    )
    conversations.finished(rendering)
    record["execution_settings"]["user_sim"] = SIMULATOR_SETTINGS.copy()
    selection = record["selection"]
    scenario = {"path": ".eval-author/tasks/refund/user-sim.yaml", "digest": conversations.SHA}
    selection["inputs"] = [scenario, {"path": ".eval-author/runtime/user-prompt.md", "digest": conversations.SHA}]
    selection["user_sim"] = {
        "task_id": "refund",
        "user_type": "neutral",
        "scenario": scenario,
        "working_directory": ".",
    }
    selection["bridge"] = {
        "source": "Synthetic observed proof",
        "evidence": [{"path": ".eval-author/runtime/proof.json", "digest": conversations.SHA}],
    }
    progress = helper.derive(state, conversations.observations(record))
    selection["settings_digest"] = progress["settings_digest"]
    record["execution_settings_presented"]["digest"] = progress["settings_digest"]
    progress = helper.derive(state, conversations.observations(record))
    selection["authorization"]["digest"] = progress["selection_digest"]
    record["runs"][0]["selection_digest"] = progress["selection_digest"]
    record["run_review"]["digest"] = helper.fingerprint(record["runs"][0])
    settings = renderer.render(
        conversations.payload(rendering, "execution_settings", links=conversations.links(rendering))
    )
    assert "Neutral, using simulator / configured-user-model" in settings
    assert "Conversation limits" in settings and "180 seconds total" in settings
    result = renderer.render(conversations.payload(rendering, "results", **conversations.result_facts(rendering)))
    assert "**Test type:** User-sim" in result and "**Neutral scenario:** refund" in result
    assert "**Simulator:** simulator / configured-user-model" in result
    assert "2/2 completed attempts" in result
    assert "persona variants" not in result
    record["execution_settings"]["user_sim"]["agent_model"] = "new-simulator-model"
    stale = renderer.render(conversations.payload(rendering, "results", **conversations.result_facts(rendering)))
    assert "Saved review identity" in stale and "new-simulator-model" not in stale
    assert "0/2 completed attempts" in stale
