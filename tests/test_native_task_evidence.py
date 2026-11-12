# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"))
from eval_author_shared import construction, native_evidence
from eval_author_shared import task_evidence as evidence

RUNNER = """
import hashlib, json, sys
from pathlib import Path
task, output, kind, identity, reward = sys.argv[1:]
task, output = Path(task), Path(output)
output.mkdir(exist_ok=False)
def write(name, data):
    path = output / name
    path.write_text(json.dumps(data))
    return bind(path)
def bind(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
native = write("raw-result.json", {"reward": int(reward), "error": None})
raw_trace = write("raw-trace.json", {"run_id": identity, "kind": kind, "actual_reward": int(reward)})
inputs = {name: bind(task / (name + ".json")) for name in ["task", "config", "runtime", "reset"]}
receipt = {"schema": "nemo.eval_author.native_execution.v1", "task_id": "sum", "run_id": identity,
           "status": "complete", "evidence_class": "primary_unhinted" if kind == "agent" else "reference_control",
           "inputs": inputs, "result": native, "artifacts": [raw_trace]}
if kind == "agent":
    atif = write("trajectory.json", {"schema_version": "3", "id": identity, "session_id": identity, "session_basis": "execution.run_id",
        "aggregate": {}, "root_spans": [{"id": "observed", "kind": "CHAIN", "output": "Synthetic control"}]})
    receipt.update(normalized_trace=atif, conversion=write("conversion.json", {
        "native_result": native, "task_sha256": inputs["task"]["sha256"], "normalized_trace": atif,
        "run_id": identity, "trace_id": identity, "evidence_class": "primary_unhinted"}))
write("execution.json", receipt)
"""


def write(path, value):
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def native_task(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    runner = tmp_path / "runner.py"
    runner.write_text(RUNNER)
    for name in ("task", "config", "reset"):
        write(task / f"{name}.json", {"value": 7})
    write(task / "runtime.json", {"files": [native_evidence.binding(runner)]})
    success = {"pointer": "/native/reward", "min": 1, "max": 1}
    failure = {"pointer": "/native/reward", "min": 0, "max": 0}
    contract = {
        "task_id": "sum",
        "provider": "native",
        "native_run_id": "/native_run_id",
        "native": {name: f"{name}.json" for name in native_evidence.INPUTS},
        "alternative_not_applicable": "Single numeric result in this unit control",
        "side_effect_not_applicable": "This test control has no writable state",
        "cases": {
            kind: {
                "kind": kind,
                "requirement": "Return the known sum",
                "health": [{"pointer": "/native/error", "equals": None}],
                "checks": [success if kind in ("agent", "reference") else failure],
            }
            for kind in ("reference", "incorrect", "empty", "agent")
        },
    }
    plan = {
        "schema": construction.plan_format.SCHEMA,
        "task_id": "sum",
        "provider": "native",
        "requirements": [
            {
                "id": "total",
                "statement": "Return the sum",
                "authority": {"basis": "specification", "reference": "Synthetic numeric rule"},
            }
        ],
        "inputs": [
            {
                "path": p.name,
                "basis": "authored",
                "source": "Synthetic test",
                "rationale": "Evidence boundary control",
                "sha256": evidence.digest(p),
            }
            for p in task.iterdir()
        ],
        "checks": [{"id": "total", "requirements": ["total"], "success": success}],
        "cases": [
            {"case": name, "observations": [{"check": "total", "expected": row["checks"][0]}]}
            for name, row in contract["cases"].items()
        ],
        "prerequisites": [],
    }
    manifest = tmp_path / "manifest.json"
    evidence.prepare(
        task,
        write(tmp_path / "contract.json", contract),
        manifest,
        construction_plan=write(tmp_path / "construction.json", construction.from_plan(plan, contract)),
    )
    return task, runner, manifest


def execute(tmp_path, native_task, kind, reward, identity=None):
    task, runner, manifest = native_task
    identity = identity or kind
    out = tmp_path / identity
    trace = out / "trajectory.json" if kind == "agent" else None
    names = ["raw-result.json", "raw-trace.json"] + (["trajectory.json", "conversion.json"] if trace else [])
    source = write(
        tmp_path / f"{identity}-source.json",
        {"execution": str(out / "execution.json"), "artifacts": [str(out / name) for name in names]},
    )
    receipt_path = tmp_path / f"{identity}-receipt.json"
    receipt = evidence.run(
        manifest,
        kind,
        identity,
        receipt_path,
        tmp_path / f"{identity}-normalized.json",
        trace,
        [sys.executable, str(runner), str(task), str(out), kind, identity, str(reward)],
        purpose="baseline",
        native_source=source,
    )
    return receipt_path, receipt


def complete(tmp_path, native_task, agent_reward=0):
    return [
        execute(tmp_path, native_task, kind, reward)[0]
        for kind, reward in [("reference", 1), ("incorrect", 0), ("empty", 0), ("agent", agent_reward)]
    ]


def test_native_task_qualifies_with_real_agent_failure_but_cannot_close_gap(tmp_path, native_task):
    receipts = complete(tmp_path, native_task)
    agents = evidence.verify_receipts(receipts, "sum", qualification=True, agent_runs=1)
    assert agents["agent"]["status"] == "behavior_failed"
    with pytest.raises(evidence.EvidenceError, match="baseline receipts cannot"):
        evidence.verify_receipts(receipts, "sum")


def test_native_qualification_requires_every_attempt_and_control(tmp_path, native_task):
    receipts = complete(tmp_path, native_task)
    with pytest.raises(evidence.EvidenceError, match="every declared control"):
        evidence.verify_receipts(receipts[1:], "sum", qualification=True, agent_runs=1)
    execute(tmp_path, native_task, "agent", 0, "failed-second")
    with pytest.raises(evidence.EvidenceError, match="all registered baseline attempts"):
        evidence.verify_receipts(receipts, "sum", qualification=True, agent_runs=1)


@pytest.mark.parametrize("mutation", ["native_result", "runtime", "hinted", "task", "trace_session", "conversion"])
def test_native_qualification_rejects_stale_or_misclassified_evidence(tmp_path, native_task, mutation):
    receipts = complete(tmp_path, native_task)
    task, runner, manifest = native_task
    if mutation == "native_result":
        write(tmp_path / "agent/raw-result.json", {"reward": 1, "error": None})
    elif mutation == "runtime":
        runner.write_text(RUNNER + "\n# changed\n")
    elif mutation == "task":
        write(task / "task.json", {"value": 8})
    else:
        execution = evidence.read(tmp_path / "agent/execution.json")
        if mutation == "hinted":
            execution["evidence_class"] = "hinted_feasibility"
        else:
            path = tmp_path / "agent" / ("trajectory.json" if mutation == "trace_session" else "conversion.json")
            value = evidence.read(path)
            value["id" if mutation == "trace_session" else "task_sha256"] = "wrong"
            write(path, value)
            execution["normalized_trace" if mutation == "trace_session" else "conversion"] = native_evidence.binding(
                path
            )
        write(tmp_path / "agent/execution.json", execution)
    with pytest.raises((evidence.EvidenceError, construction.PlanError)):
        evidence.verify_receipts(receipts, "sum", qualification=True, agent_runs=1)


def test_failed_negative_control_does_not_qualify(tmp_path, native_task):
    receipts = [
        execute(tmp_path, native_task, kind, reward)[0]
        for kind, reward in [("reference", 1), ("incorrect", 1), ("empty", 0), ("agent", 0)]
    ]
    with pytest.raises(evidence.EvidenceError, match="health and behavior"):
        evidence.verify_receipts(receipts, "sum", qualification=True, agent_runs=1)


def test_native_execution_requires_fresh_sources_before_running(tmp_path, native_task):
    task, runner, manifest = native_task
    source = write(
        tmp_path / "source.json", {"execution": str(tmp_path / "execution.json"), "artifacts": [str(runner)]}
    )
    with pytest.raises(evidence.EvidenceError, match="output must be new"):
        evidence.run(
            manifest,
            "reference",
            "stale",
            tmp_path / "receipt.json",
            tmp_path / "result.json",
            None,
            [sys.executable, str(runner)],
            purpose="baseline",
            native_source=source,
        )


def test_native_agent_requires_normalized_trace_even_for_baseline(tmp_path, native_task):
    with pytest.raises(evidence.EvidenceError, match="fresh trace"):
        evidence.run(
            native_task[2],
            "agent",
            "missing",
            tmp_path / "r.json",
            tmp_path / "n.json",
            None,
            [sys.executable, "-c", "pass"],
            purpose="baseline",
        )
