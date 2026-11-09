# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Credential-free synthetic rubric mutations; never runs task/scorer code."""

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from eval_author_shared.task_evidence import digest, loads  # noqa: E402
from first_eval_state import fingerprint  # noqa: E402
from measure import measure  # noqa: E402


def fixture(root, mutation):
    files: dict[str, Any] = {
        "task": {"task_id": "task-1"},
        "entry": {"selector": "strict", "routes": {"strict": "scorer", "weak": "other"}},
        "scorer": {"checks": ["amount", "approval"]},
        "other": {"checks": ["claimed_success"]},
        "prompt": {"role": "judge-input"},
        "ethos": {"intent": "synthetic refund task"},
    }
    if mutation == "wrong-scorer":
        files["entry"]["selector"] = "weak"
    elif mutation == "ambiguous-route":
        files["entry"] = {"selector": None, "routes": files["entry"]["routes"]}
    scope: dict[str, dict[str, Any]] = {
        "task_artifacts": {"required": ["task"], "source": "task-inventory", "unresolved_material": []},
        "grading_dependencies": {
            "required": ["scorer", "other", "prompt"],
            "source": "grading-inventory",
            "unresolved_material": [],
            "review_source": "review",
        },
    }
    if mutation == "partial":
        scope["task_artifacts"]["unresolved_material"] = ["Unresolved generated task input reference"]
        scope["grading_dependencies"]["required"].append("absent")
        scope["grading_dependencies"]["unresolved_material"] = ["Unresolved scorer dependency: absent"]
    for name, inventory in scope.items():
        files[inventory["source"]] = {
            name: {key: copy.deepcopy(inventory[key]) for key in ("required", "unresolved_material")}
        }
    roles = {
        "task": "task",
        "entry": "configuration",
        "scorer": "scorer",
        "other": "scorer",
        "prompt": "judge_prompt",
        "ethos": "ethos",
        "task-inventory": "manifest",
        "grading-inventory": "manifest",
        "review": "review",
    }
    files["review"] = {
        "source": scope["grading_dependencies"]["source"],
        "required": copy.deepcopy(scope["grading_dependencies"]["required"]),
        "unresolved_material": copy.deepcopy(scope["grading_dependencies"]["unresolved_material"]),
        "complete": mutation != "partial",
    }
    records = []
    for name, value in files.items():
        path = root / (name + ".json")
        path.write_text(json.dumps(value, sort_keys=True))
        records.append(
            {
                "id": name,
                "role": roles[name],
                "path": path.name,
                "digest": digest(path),
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "task-inventory.json"
                if name == "task"
                else "grading-inventory.json"
                if name in ("scorer", "other", "prompt", "review")
                else "fixture:selection",
                "evidence": [{"path": path.name, "digest": digest(path)}],
            }
        )
    if mutation in ("missing", "denied", "unresolved"):
        records[0]["access"] = mutation
        records[0]["digest"] = None
        records[0]["evidence"] = []
    if mutation == "confirmed-missing":
        (root / "task.json").unlink()
    if mutation == "changed-task":
        (root / "task.json").write_text('{"task_id":"task-changed"}')
    config: dict = {
        "agent": "scripted",
        "run": "run-1",
        "task": "task-1",
        "tool_permissions": [],
        "provider": "harbor",
        "runtime_pins": {"harbor": "0.24.0"},
        "environment": {"network": "none"},
    }
    observed = copy.deepcopy(config)
    if mutation == "config-difference":
        observed["runtime_pins"]["harbor"] = "different"
    dependencies = {"scorer": ["prompt"], "other": [], "prompt": []}
    if mutation == "partial":
        dependencies["scorer"].append("absent")
    if mutation == "missing-dependency":
        dependencies["scorer"] = ["absent"]
    bindings = {row["id"]: row["digest"] for row in records}
    approval: dict = {
        "id": "approval",
        "decision_id": "decision",
        "scope": "task:task/substitution",
        "substitution": {"from": "other", "to": "scorer"},
        "bindings": copy.deepcopy(bindings),
    }
    decision = {key: copy.deepcopy(value) for key, value in approval.items() if key != "decision_id"}
    decision.update(id="decision", outcome="approved", source="fixture:user-turn")
    if mutation == "stale-approval":
        approval["bindings"]["ethos"] = "f" * 64
        decision["bindings"]["ethos"] = "f" * 64
    nodes = [{"id": "task", "role": "task"}, {"id": "run", "role": "run"}]
    links = [{"source": "run", "target": "task", "relation": "task"}]
    if mutation == "broken-reference":
        links = []
    return {
        "artifacts": records,
        "w02_scope": scope,
        "route": {"entrypoint": "entry", "expected_scorer": "scorer", "dependencies": dependencies},
        "declared_config": config,
        "observed_config": observed,
        "nodes": nodes,
        "links": links,
        "approvals": [approval],
        "decisions": [] if mutation == "missing-decision" else [decision],
    }


def run_panel():
    cases = loads((ROOT / "evals/rubric-measurements/cases.json").read_text())
    rows = []
    for case in cases["raw_measurements"]:
        with tempfile.TemporaryDirectory(prefix="rubric-measurements-") as directory:
            root = Path(directory).resolve()
            selection = fixture(root, case["mutation"])
            report = measure(root, selection)
            observation = report["measurements"][case["measurement"]]
            codes = sorted({row["code"] for row in observation.get("diagnostics", [])})
            matches = (
                observation["status"] == case["status"]
                and codes == case["codes"]
                and observation.get("differences", []) == case.get("differences", [])
            )
            rows.append(
                {
                    "id": case["id"],
                    "measurement": case["measurement"],
                    "expected": case,
                    "observed": observation,
                    "selection_digest": fingerprint(selection),
                    "matches_expectations": matches,
                }
            )
    assessments = []
    for case in cases["assessments"]:
        with tempfile.TemporaryDirectory(prefix="rubric-assessment-") as directory:
            root = Path(directory).resolve()
            selection = fixture(root, case["mutation"])
            request = root / "selection.json"
            request.write_text(json.dumps(selection, sort_keys=True, allow_nan=False))
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills/eval-author-rubric/scripts/measure.py"),
                    "--root",
                    str(root),
                    "--selection",
                    str(request),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = loads(process.stdout)
            expected_items = {
                **case["items"],
                **{key: {"score": None, "status": "NOT_ASSESSED"} for key in cases["not_assessed"]},
            }
            observed_items = report.get("items", {})
            matches = (
                process.returncode == case["cli_exit_code"]
                and not process.stderr
                and set(observed_items) == set(expected_items)
                and all(
                    all(observed_items[key].get(field) == value for field, value in expected.items())
                    for key, expected in expected_items.items()
                )
                and all(
                    sorted({row["code"] for row in report["measurements"][name].get("diagnostics", [])}) == codes
                    for name, codes in case["diagnostics"].items()
                )
            )
            assessments.append(
                {
                    "id": case["id"],
                    "expected": case,
                    "observed": report,
                    "cli_exit_code": process.returncode,
                    "selection_digest": fingerprint(selection),
                    "matches_expectations": matches,
                }
            )
    return {
        "schema": "rubric-mutation-panel-v2",
        "measurement": "synthetic-local-mutations",
        "status": "passed"
        if rows and assessments and all(row["matches_expectations"] for row in rows + assessments)
        else "failed",
        "cases_digest": fingerprint(cases),
        "cases": rows,
        "assessments": assessments,
        "counts": {"cases": len(rows), "assessments": len(assessments)},
        "native_execution": False,
        "agent_exploitation": False,
        "workflow_integration": False,
    }


def summary(result):
    lines = [
        f"W02 handcrafted assessment demo: {result['status']}",
        f"Raw measurement regressions: {sum(row['matches_expectations'] for row in result['cases'])}/{len(result['cases'])} matched",
        "Variant | DISC-01-A | DISC-01-B | DISC-01-C | expected match",
    ]
    for case in result["assessments"]:
        values = [
            json.dumps(case["observed"]["items"][key]["score"]) for key in ("DISC-01-A", "DISC-01-B", "DISC-01-C")
        ]
        lines.append(" | ".join([case["id"], *values, str(case["matches_expectations"])]))
    lines.extend(
        [
            "null = NOT_ASSESSED; the other eight criteria remain null in every variant.",
            "Scores are bounded 0/1/2; no 3 without independent corroboration.",
            "Wrong scorer: weak-route defect, not demonstrated exploitation.",
            "No native execution, solver attempts, or downstream workflow integration.",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()
    result = run_panel()
    print(summary(result) if args.format == "text" else json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    raise SystemExit(0 if result["status"] == "passed" else 1)
