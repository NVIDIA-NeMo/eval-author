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
            "interaction_type": "user_sim",
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
        "input_digests": {str(repo / path): hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in paths},
    }


def selected_user_sim(modules, repo: Path) -> dict:
    translated = translated_inputs(repo)
    value = creation.ready(modules, repo, examples=design(), simulation=True)
    bindings = modules[1].bind_user_sim_translation(repo, translated)
    bindings["bridge"] = {
        "source": "Synthetic proof fixture for state tests",
        "evidence": [creation.evidence(repo, ".eval-author/.working/interactive-proof.json")],
    }
    creation.select(modules, repo, value, user_sim_bindings=bindings, user_sim_settings=SIMULATOR_SETTINGS.copy())
    return value


def test_native_namespaced_task_translates_and_binds_to_agreed_plan_id(modules, tmp_path, monkeypatch):
    from harbor_user_sim import translate

    monkeypatch.chdir(tmp_path)
    path = task(tmp_path)
    write(tmp_path, path + "/user-sim.yaml", SCENARIO)
    write(tmp_path, path + "/tests/test.sh", "#!/bin/sh\nexit 0\n")
    (tmp_path / path / "environment").mkdir()
    config = tmp_path / ".eval-author/base-run.yaml"
    config.write_text(
        "tasks:\n  - path: .eval-author/tasks/refund\nagents:\n  - name: claude-code\nuser_agent:\n  name: codex\n"
    )
    value = creation.ready(modules, tmp_path, examples=design(), simulation=True)
    receipt = translate(
        tmp_path / path / "user-sim.yaml", config, tmp_path / ".eval-author/.working/translation"
    ).to_dict()
    binding = modules[1].bind_user_sim_translation(tmp_path, receipt)
    binding["bridge"] = {
        "source": "Synthetic interface proof for state test",
        "evidence": [creation.evidence(tmp_path, ".eval-author/.working/bridge-proof.json")],
    }
    creation.select(modules, tmp_path, value, user_sim_bindings=binding, user_sim_settings=SIMULATOR_SETTINGS.copy())
    assert report.user_sim_identity(tmp_path, "refund", path)["task_id"] == "refund"
    assert receipt["task_id"] == "refund"
    assert 'name = "org/refund"' in (tmp_path / path / "task.toml").read_text()
    assert creation.facts(modules, tmp_path)["execution_authorized"]


def test_translation_binding_covers_config_and_consumed_inputs_without_runtime_authority(modules, tmp_path):
    translated = translated_inputs(tmp_path)
    binding = modules[1].bind_user_sim_translation(tmp_path, translated)
    references = [binding["config"], *binding["inputs"]]
    assert {str(tmp_path / ref["path"]) for ref in references} == set(translated["input_paths"])
    assert all(ref["digest"] == hashlib.sha256((tmp_path / ref["path"]).read_bytes()).hexdigest() for ref in references)
    assert binding["user_sim"]["working_directory"] == "."
    assert binding["user_sim"]["scenario"] in binding["inputs"]
    assert "authorization" not in binding and "bridge" not in binding


@pytest.mark.parametrize("changed", ["scenario", "run_config", "new_external_input"])
def test_translation_receipt_cannot_bind_changed_sources_or_generated_configuration(modules, tmp_path, changed):
    translated = translated_inputs(tmp_path)
    scenario = tmp_path / ".eval-author/tasks/refund/user-sim.yaml"
    if changed == "scenario":
        scenario.write_text(SCENARIO.replace("I want to return my headphones.", "I need a replacement instead."))
        creation.ready(modules, tmp_path, examples=design(), simulation=True)
        assert creation.facts(modules, tmp_path)["first_task_approved"]
    else:
        config = Path(translated["run_config_path"])
        if changed == "new_external_input":
            extra = tmp_path / "prompts/new-target.md"
            extra.write_text("New target instructions")
            config.write_text(
                "agents:\n  - name: installed-target\n    kwargs:\n      prompt_template_path: prompts/new-target.md\n"
            )
            assert str(extra) not in translated["input_paths"]
        else:
            config.write_text("n_attempts: 9\n")
    before = copy.deepcopy(translated)
    with pytest.raises(ValueError, match="translation is stale.*Regenerate"):
        modules[1].bind_user_sim_translation(tmp_path, translated)
    assert translated == before


@pytest.mark.parametrize("invalid", ["missing", "omitted_input", "extra_digest", "duplicate_path", "invalid_digest"])
def test_translation_receipt_requires_exact_input_inventory_and_frozen_digests(modules, tmp_path, monkeypatch, invalid):
    translated = translated_inputs(tmp_path)
    if invalid == "missing":
        translated.pop("input_digests")
    elif invalid == "omitted_input":
        translated["input_digests"].pop(translated["input_paths"][0])
    elif invalid == "extra_digest":
        translated["input_digests"][str(tmp_path / "unexpected.txt")] = "a" * 64
    elif invalid == "duplicate_path":
        translated["input_paths"].append(translated["input_paths"][0])
    else:
        translated["input_digests"][translated["input_paths"][0]] = "invalid"
    monkeypatch.setattr(modules[0], "_file_digest", lambda *_: pytest.fail("Read before validating the frozen receipt"))
    with pytest.raises(ValueError, match="translation receipt|SHA-256 digest"):
        modules[1].bind_user_sim_translation(tmp_path, translated)


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


@pytest.mark.parametrize(
    "extra_type,selected_count", [("single_turn", 2), ("user_sim", 2), ("single_turn", 1), (None, 2)]
)
def test_user_sim_execution_rejects_multi_task_scope_without_dropping_authored_work(
    modules, tmp_path, extra_type, selected_count
):
    translated = translated_inputs(tmp_path)
    planned = design()
    if extra_type is not None:
        second = {**design()[0], "id": "policy", "example_ids": ["policy-1"], "interaction_type": extra_type}
        planned.append(second)
    value = creation.ready(modules, tmp_path, examples=planned, simulation=True)
    assert creation.facts(modules, tmp_path)["suite_approved"]
    before = modules[0].state_path(tmp_path).read_bytes()
    saved_tasks = copy.deepcopy(value["tasks"])
    binding = modules[1].bind_user_sim_translation(tmp_path, translated)
    value["selection"] = {
        **binding,
        "agent": "actual-agent",
        "model": "target-model",
        "source": "Attempted multi-task execution",
        "cases": [
            {"task_id": item["id"], "example_ids": item["example_ids"], "attempts": 1}
            for item in planned[:selected_count]
        ],
    }
    if len(value["selection"]["cases"]) < selected_count:
        value["selection"]["cases"].append({"task_id": "unapproved", "example_ids": ["extra"], "attempts": 1})
    with pytest.raises(ValueError, match="exactly one approved task.*Do not silently drop approved tasks"):
        creation.save(modules, tmp_path, value)
    loaded = modules[1].record(modules[0].load_state(tmp_path))
    assert loaded["tasks"] == saved_tasks and loaded["selection"] is None
    assert modules[0].state_path(tmp_path).read_bytes() == before


def test_authored_report_and_review_keep_interaction_type_and_bound_neutral_scenario(modules, tmp_path):
    translated_inputs(tmp_path)
    value = creation.start(modules, tmp_path, examples=design())
    modules[0].update(
        tmp_path,
        {"scopes": {"create": {"user_simulation": {"enabled": True, "source": "enable user simulation"}}}},
        expected_revision=modules[0].load_state(tmp_path, state_only=True)["revision"],
        state_only=True,
    )
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
    assert row["interaction_type"] == "user_sim" and row["user_sim_scenario"]["user_type"] == "neutral"
    table = importlib.import_module("first_eval_table_data")
    rows = table.authored_rows(tmp_path, state, ["refund"])
    observed = modules[1].observe(tmp_path, state)
    flow = importlib.import_module("conversation_flows.first_eval")
    checked = flow.authored_rows(value, {"task_rows": rows}, observed, ["refund"])
    output = flow.task_table(checked)
    assert "**User Simulation:** **refund**" in output
    assert "Neutral scenario: refund" in output
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


def test_settings_and_results_show_simulator_identity_without_adding_example_counts(rendering, monkeypatch):
    state, record, helper, renderer, _ = rendering
    state["scopes"]["create"]["user_simulation"] = {"enabled": True, "source": "enable user simulation"}
    record["plan"]["tasks"] = record["plan"]["tasks"][:1]
    record["plan"]["tasks"][0].update(
        {key: value for key, value in design()[0].items() if key not in {"id", "example_ids"}}
    )
    build = conversations.built
    monkeypatch.setattr(
        conversations, "built", lambda fixture, **kwargs: build(fixture, **{**kwargs, "all_tasks": False})
    )
    record["execution_settings"]["attempts"] = "1 per task, 1 total attempt"
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
    assert "**Interaction type:** User Simulation" in result and "**Neutral scenario:** refund" in result
    assert "**Simulator:** simulator / configured-user-model" in result
    assert "1/1 completed attempts" in result
    assert "persona variants" not in result
    record["execution_settings"]["user_sim"]["agent_model"] = "new-simulator-model"
    stale = renderer.render(conversations.payload(rendering, "results", **conversations.result_facts(rendering)))
    assert "Saved review identity" in stale and "new-simulator-model" not in stale
    assert "0/1 completed attempts" in stale


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_simulation_setting_is_scoped_revision_checked_and_metadata_only(modules, tmp_path, monkeypatch, journey):
    state_helper, _ = modules
    state_helper.initialize(tmp_path, journey=journey, state_only=True)
    before = state_helper.load_state(tmp_path, state_only=True)
    patch = {"scopes": {journey: {"user_simulation": {"enabled": True, "source": "enable user simulation"}}}}
    with pytest.raises(ValueError, match="expected-revision"):
        state_helper.update(tmp_path, patch, state_only=True)
    monkeypatch.setattr(state_helper, "ethos_digest", lambda *_: pytest.fail("Read Ethos for a setting change"))
    monkeypatch.setattr(state_helper, "task_digest", lambda *_: pytest.fail("Read tasks for a setting change"))
    updated = state_helper.update(tmp_path, patch, expected_revision=before["revision"], state_only=True)
    loaded = state_helper.load_state(tmp_path, state_only=True)
    from interaction_types import user_simulation_enabled

    assert user_simulation_enabled(loaded, journey)
    assert not user_simulation_enabled(loaded, "expand" if journey == "create" else "create")
    assert loaded["scopes"][journey]["user_simulation"]["source"] == "enable user simulation"
    assert all(updated[key] == before[key] for key in ("ethos", "prerequisites", "pending_question", "setup"))
    with pytest.raises(ValueError, match="State changed"):
        state_helper.update(tmp_path, patch, expected_revision=before["revision"], state_only=True)


def test_disabling_retains_results_and_reenabling_requires_fresh_run_authorization(modules, tmp_path):
    value = selected_user_sim(modules, tmp_path)
    creation.run(modules, tmp_path, value)
    creation.review_run(modules, tmp_path, value)
    state_helper, helper = modules
    before = state_helper.load_state(tmp_path)
    historical = copy.deepcopy(helper.record(before))
    for enabled, source in ((False, "disable user simulation"), (True, "enable user simulation")):
        state_helper.update(
            tmp_path,
            {"scopes": {"create": {"user_simulation": {"enabled": enabled, "source": source}}}},
            expected_revision=state_helper.load_state(tmp_path, state_only=True)["revision"],
            state_only=True,
        )
        loaded = state_helper.load_state(tmp_path)
        progress = helper.progress(tmp_path, loaded)
        record = helper.record(loaded)
        assert progress["user_simulation_blocked"] is (not enabled)
        assert not progress["execution_authorized"]
        assert record["execution_settings_presented"] is None
        assert "authorization" not in record["selection"]
        assert all(record[key] == historical[key] for key in ("plan", "tasks", "runs", "run_review"))
        assert progress["scope_approved"] and progress["suite_approved"]
        if not enabled:
            assert progress["next_stage"] == "simulation_disabled"


def test_legacy_simulation_suite_is_readable_but_off_without_explicit_choice(modules, tmp_path):
    selected_user_sim(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    state["scopes"]["create"].pop("user_simulation")
    before = copy.deepcopy(state)
    progress = modules[1].progress(tmp_path, state)
    assert progress["user_simulation_blocked"]
    assert not progress["execution_authorized"] and not progress["selection_current"]
    assert state == before


def test_disabled_simulation_resume_explains_enable_trigger_without_authoring(rendering):
    state, record, _, renderer, _ = rendering
    record["plan"]["tasks"] = design()
    before = copy.deepcopy(state)
    output = renderer.render(conversations.payload(rendering, "simulation_disabled"))
    assert "User simulation is disabled" in output
    assert "enable user simulation" in output
    assert state == before


@pytest.mark.parametrize("interaction_type", ["single_turn", "scripted_multi_step"])
def test_simulation_setting_does_not_change_ordinary_execution(modules, tmp_path, interaction_type):
    planned = [
        {
            "id": "refund",
            "example_ids": ["refund-1"],
            "interaction_type": interaction_type,
            "state_requirements": "Keep the same cart across requests.",
            "prerequisite_checks": ["The requested item is in the cart before its removal."],
        }
    ]
    value = creation.ready(modules, tmp_path, examples=planned)
    creation.select(modules, tmp_path, value)
    state_helper = modules[0]
    before = copy.deepcopy(value)
    for enabled in (True, False):
        state_helper.update(
            tmp_path,
            {"scopes": {"create": {"user_simulation": {"enabled": enabled, "source": "User setting change"}}}},
            expected_revision=state_helper.load_state(tmp_path, state_only=True)["revision"],
            state_only=True,
        )
        assert creation.facts(modules, tmp_path)["execution_authorized"]
        assert modules[1].record(state_helper.load_state(tmp_path)) == before


def test_disabled_simulation_cannot_record_new_task_construction(modules, tmp_path):
    value = creation.start(modules, tmp_path, examples=design())
    creation.approve(modules, tmp_path, value)
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="User simulation is disabled"):
        creation.author(modules, tmp_path, value, "refund")
    assert modules[0].state_path(tmp_path).read_bytes() == before
