# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author-task-create/scripts"))
plan_module = importlib.import_module("construction_plan")
evidence = importlib.import_module("task_evidence")


@pytest.fixture
def declaration(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "input.txt").write_text("7\n")
    success = {"pointer": "/reward", "equals": 1}
    failure = {"pointer": "/reward", "equals": 0}
    contract = {
        "task_id": "count",
        "provider": "harbor",
        "cases": {
            kind: {"kind": kind, "checks": [success if kind in ("reference", "agent") else failure]}
            for kind in ("reference", "incorrect", "empty", "agent")
        },
    }
    plan = {
        "schema": plan_module.SCHEMA,
        "task_id": "count",
        "provider": "harbor",
        "requirements": [
            {
                "id": "total",
                "statement": "Return the total.",
                "authority": {"basis": "specification", "reference": "Reviewed sum rule"},
            }
        ],
        "inputs": [
            {
                "path": "input.txt",
                "basis": "authored",
                "source": "Designed test input",
                "rationale": "A deterministic single record",
                "sha256": hashlib.sha256((task / "input.txt").read_bytes()).hexdigest(),
            }
        ],
        "checks": [{"id": "total", "requirements": ["total"], "success": success}],
        "cases": [
            {"case": name, "observations": [{"check": "total", "expected": row["checks"][0]}]}
            for name, row in contract["cases"].items()
        ],
        "prerequisites": [],
    }
    return task, contract, plan


def test_unresolved_proposal_is_not_a_build_authority(declaration):
    task, contract, plan = declaration
    plan["requirements"][0]["authority"]["basis"] = "unresolved"
    plan["inputs"][0]["sha256"] = None
    (task / "input.txt").unlink()
    result = plan_module.validate(plan, "proposal")
    assert result["status"] == "unresolved"
    assert result["execution"] == "unmeasured"
    with pytest.raises(plan_module.PlanError, match="unresolved grading authority"):
        plan_module.validate(plan, "draft", task, contract)


def test_authored_inputs_bind_exact_bytes(declaration, tmp_path):
    task, contract, plan = declaration
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    binding = plan_module.bind(path, task, contract)
    assert binding["assessment"]["execution"] == "unmeasured"
    plan_module.recheck(binding, task, contract)
    path.write_text(json.dumps(plan, indent=2))
    with pytest.raises(plan_module.PlanError, match="changed"):
        plan_module.recheck(binding, task, contract)
    path.write_text(json.dumps(plan))
    (task / "input.txt").write_text("8\n")
    with pytest.raises(plan_module.PlanError, match="input hash"):
        plan_module.recheck(binding, task, contract)


def test_unrelated_native_assertion_does_not_cover_requirement(declaration):
    task, contract, plan = declaration
    contract["cases"]["incorrect"]["checks"] = [{"pointer": "/logging", "equals": False}]
    with pytest.raises(plan_module.PlanError, match="native case"):
        plan_module.validate(plan, "draft", task, contract)


def test_prerequisite_requires_dependent_failure(declaration):
    task, contract, plan = declaration
    plan["requirements"].append(
        {
            "id": "loaded",
            "statement": "Load the input",
            "authority": {"basis": "specification", "reference": "Input contract"},
        }
    )
    plan["checks"][0]["requirements"].append("loaded")
    plan["prerequisites"] = [{"before": "loaded", "after": "total", "bypass_case": "incorrect", "check": "total"}]
    with pytest.raises(plan_module.PlanError, match="dependent check"):
        plan_module.validate(plan, "draft", task, contract)


def test_incorrect_control_cannot_repeat_success(declaration):
    task, contract, plan = declaration
    contract["cases"]["incorrect"]["checks"][0]["equals"] = 1
    with pytest.raises(plan_module.PlanError, match="reject a mapped success"):
        plan_module.validate(plan, "draft", task, contract)


def test_private_plan_not_shipped_to_agent(declaration):
    task, contract, plan = declaration
    path = task / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(plan_module.PlanError, match="private outside"):
        plan_module.bind(path, task, contract)


def test_prerequisite_declares_a_specific_dependent_observation(declaration):
    task, contract, plan = declaration
    plan["requirements"].append(
        {
            "id": "loaded",
            "statement": "Load the input.",
            "authority": {"basis": "specification", "reference": "Input rule"},
        }
    )
    loaded = {"pointer": "/loaded", "equals": True}
    plan["checks"].append({"id": "loaded", "requirements": ["loaded"], "success": loaded})
    for case in plan["cases"]:
        if case["case"] in ("reference", "agent"):
            case["observations"].append({"check": "loaded", "expected": loaded})
            contract["cases"][case["case"]]["checks"].append(loaded)
    plan["prerequisites"] = [{"before": "loaded", "after": "total", "bypass_case": "incorrect", "check": "total"}]
    result = plan_module.validate(plan, "draft", task, contract)
    assert result["planned_prerequisites"] == 1
    assert result["execution"] == "unmeasured"


@pytest.mark.parametrize("provider", ["harbor", "gym"])
def test_nonbinary_success_threshold_is_preserved(declaration, provider):
    task, contract, plan = declaration
    plan["provider"] = contract["provider"] = provider
    # A partial-reward success threshold is a task decision, not an implicit one.
    success = {"pointer": "/reward", "min": 0.75, "max": 1.0}
    plan["checks"][0]["success"] = success
    for case in plan["cases"]:
        if case["case"] in ("reference", "agent"):
            case["observations"][0]["expected"] = success
            contract["cases"][case["case"]]["checks"] = [success]
    assert plan_module.validate(plan, "draft", task, contract)["status"] == "structurally_valid"
