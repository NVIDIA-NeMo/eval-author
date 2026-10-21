# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One construction contract, with lossless, explicit imports of historical formats."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from . import legacy_construction as trace_format
from . import legacy_plan as plan_format

SCHEMA = "nemo.eval_author.construction.v2"
PATH = "private/construction.json"
CUSTOM_ARMS = trace_format.CUSTOM_ARMS
ConstructionError = trace_format.ConstructionError
PlanError = plan_format.PlanError
# Historical API helpers remain available to integrations.
load = plan_format.load
execution = trace_format.execution
qualify = trace_format.qualify


def _projection(value):
    return {
        "schema": plan_format.SCHEMA,
        "task_id": value["task_id"],
        "provider": value["provider"],
        "requirements": [{k: r[k] for k in ("id", "statement", "authority")} for r in value["requirements"]],
        "inputs": [{k: r[k] for k in ("path", "basis", "source", "rationale", "sha256")} for r in value["inputs"]],
        "checks": value["checks"],
        "cases": [{"case": c["case"], "observations": c["observations"]} for c in value["cases"]],
        "prerequisites": [
            {k: e[k] for k in ("before", "after", "bypass_case", "check")} for e in value["prerequisites"]
        ],
    }


def _trace_projection(value):
    """In-memory provider adapter; never writes a second authoritative contract."""
    state = copy.deepcopy(value["starting_state"])
    state["artifacts"] = [
        {
            "path": "task/" + r["path"],
            "sha256": r["sha256"],
            "basis": r["basis"],
            "source": r["source"],
            "edits": r["edits"],
            "review": r["review"],
        }
        for r in value["inputs"]
    ]
    return {
        "schema": trace_format.SCHEMA,
        "candidate_sha256": value["bindings"]["candidate_sha256"],
        "trace_sha256": value["bindings"]["trace_sha256"],
        "requirements": [
            {
                "id": r["id"],
                "candidate_index": r["candidate_index"],
                "checks": [c["id"] for c in value["checks"] if r["id"] in c["requirements"]],
            }
            for r in value["requirements"]
        ],
        "probes": value["probes"],
        "controls": [
            {
                "id": c["case"],
                "arm": c["kind"],
                "requirements": c["requirements"],
                "fails": [o["check"] for o in c["observations"] if o["expected"]["equals"] == "FAIL"],
                "reaches": c["reaches"],
                "dependency": c["dependency"],
                "rationale": c["rationale"],
                "review": c["review"],
            }
            for c in value["cases"]
            if c["kind"] in CUSTOM_ARMS
        ],
        "dependencies": [
            {"id": e["id"], "prerequisite": e["before"], "dependent": e["after"], "bypass_control": e["bypass_case"]}
            for e in value["prerequisites"]
        ],
        "starting_state": state,
        "review": value["review"],
    }


def _migration(value):
    for source in value["migration"]:
        plan_format.fields(source, {"path", "sha256", "schema"})
        _, sha = load(Path(source["path"]))
        if sha != source["sha256"]:
            raise PlanError("historical construction source changed")


def assess(value, mode="proposal", task=None, contract=None, workspace=None):
    plan_format.fields(
        value,
        {
            "schema",
            "task_id",
            "provider",
            "requirements",
            "inputs",
            "checks",
            "cases",
            "prerequisites",
            "probes",
            "starting_state",
            "review",
            "bindings",
            "migration",
        },
    )
    if value["schema"] != SCHEMA or mode not in {"proposal", "draft"}:
        raise PlanError("unsupported construction schema or stage")
    if value["provider"] not in {"harbor", "gym"}:
        raise PlanError("unsupported construction provider")
    plan_format.text(value["task_id"])
    if not isinstance(value["migration"], list) or len(value["migration"]) > 16:
        raise PlanError("migration sources must be a bounded list")
    _migration(value)
    requirements = plan_format.indexed(value["requirements"])
    checks = plan_format.indexed(value["checks"])
    cases = plan_format.indexed(value["cases"], "case")
    if not requirements or (mode == "draft" and not checks):
        raise PlanError("requirements and draft scored checks are required")
    unresolved = []
    for key, row in requirements.items():
        plan_format.fields(row, {"id", "statement", "authority", "candidate_index"})
        plan_format.text(row["statement"])
        plan_format.fields(row["authority"], {"basis", "reference"})
        if row["authority"]["basis"] not in {"specification", "user_decision", "independent_reference", "unresolved"}:
            raise PlanError("invalid grading authority")
        plan_format.text(row["authority"]["reference"])
        if row["authority"]["basis"] == "unresolved":
            unresolved.append(key)
    inputs = value["inputs"]
    if not isinstance(inputs, list) or len(inputs) > 1024:
        raise PlanError("input inventory must be bounded")
    paths = set()
    for row in inputs:
        plan_format.fields(row, {"path", "sha256", "basis", "source", "rationale", "edits", "review"})
        plan_format.text(row["path"])
        plan_format.text(row["rationale"])
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts or row["path"] in paths:
            raise PlanError("input paths must be distinct and task-relative")
        paths.add(row["path"])
        if value["bindings"] is None and (row["edits"] or row["review"] is not None):
            raise PlanError("native input provenance uses source and rationale; trace review requires bindings")
    if not isinstance(value["prerequisites"], list) or len(value["prerequisites"]) > 256:
        raise PlanError("prerequisites must be a bounded list")
    if not isinstance(value["probes"], list) or len(value["probes"]) > 32:
        raise PlanError("probes must be a bounded list")
    covered = set()
    for row in checks.values():
        plan_format.fields(row, {"id", "requirements", "success"})
        refs = row["requirements"]
        if (
            not isinstance(refs, list)
            or not refs
            or len(set(refs)) != len(refs)
            or not set(refs) <= requirements.keys()
        ):
            raise PlanError("scored check must map distinct declared requirements")
        covered.update(refs)
        plan_format.assertion(row["success"])
    if mode == "draft" and covered != requirements.keys():
        raise PlanError("map every requirement to a scored check")
    for case in cases.values():
        plan_format.fields(
            case, {"case", "kind", "observations", "requirements", "reaches", "dependency", "rationale", "review"}
        )
        plan_format.text(case["rationale"])
        if (
            not isinstance(case["requirements"], list)
            or not case["requirements"]
            or len(set(case["requirements"])) != len(case["requirements"])
            or not set(case["requirements"]) <= requirements.keys()
        ):
            raise PlanError("case requirements must reference distinct declared requirements")
        if not isinstance(case["reaches"], list):
            raise PlanError("case reach probes must be a list")
        if case["kind"] not in {
            "reference",
            "incorrect",
            "empty",
            "alternative",
            "side_effect",
            "agent",
            "unresolved",
            "nop",
            "oracle",
            "negative",
            "copy",
            "bypass",
        }:
            raise PlanError("unknown control kind")
        if mode == "draft" and case["kind"] == "unresolved":
            raise PlanError("resolve imported control kinds before construction")
        if value["bindings"] is None and (
            case["reaches"] or case["dependency"] is not None or case["review"] is not None
        ):
            raise PlanError("trace control metadata requires source bindings")
        observations = plan_format.indexed(case["observations"], "check")
        if not observations:
            raise PlanError("each case needs scored observations")
        for key, observation in observations.items():
            plan_format.fields(observation, {"check", "expected"})
            plan_format.assertion(observation["expected"])
            if key not in checks or observation["expected"]["pointer"] != checks[key]["success"]["pointer"]:
                raise PlanError("control must observe its mapped scored check")
    edges = set()
    for edge in value["prerequisites"]:
        plan_format.fields(edge, {"id", "before", "after", "bypass_case", "check"})
        before, after, case, check = (edge[k] for k in ("before", "after", "bypass_case", "check"))
        if before not in requirements or after not in requirements or before == after or (before, after) in edges:
            raise PlanError("invalid prerequisite endpoints")
        if case not in cases or check not in checks or after not in checks[check]["requirements"]:
            raise PlanError("bypass must target the dependent requirement")
        observed = {o["check"]: o["expected"] for o in cases[case]["observations"]}
        if check not in observed or not plan_format.disjoint(checks[check]["success"], observed[check]):
            raise PlanError("bypass must contradict its dependent scored check")
        edges.add((before, after))
    remaining = set(requirements)
    while remaining:
        roots = {r for r in remaining if not any(a in remaining and b == r for a, b in edges)}
        if not roots:
            raise PlanError("cyclic prerequisite graph")
        remaining -= roots
    if mode == "draft" and unresolved:
        raise PlanError("resolve grading authority before construction: " + ", ".join(unresolved))
    if value["bindings"] is None:
        if value["probes"] or value["starting_state"] is not None or value["review"] is not None:
            raise PlanError("trace evidence requires explicit candidate and trace bindings")
        result = plan_format.validate(_projection(value), mode, task, contract)
        if contract is not None:
            for name, case in cases.items():
                if case["kind"] != contract["cases"][name]["kind"]:
                    raise PlanError("case kind differs from native result contract")
    else:
        if workspace is None or value["provider"] != "harbor":
            raise PlanError("trace construction needs its private Harbor workspace")
        plan_format.fields(value["bindings"], {"candidate_sha256", "trace_sha256"})
        candidate = trace_format.load(workspace, "candidate.json")
        if not isinstance(candidate, dict):
            raise PlanError("candidate must be an object")
        for row in requirements.values():
            index = row["candidate_index"]
            if type(index) is not int or not 0 <= index < len(candidate["requirements"]):
                raise PlanError("invalid candidate requirement mapping")
            if row["statement"] != candidate["requirements"][index]["description"]:
                raise PlanError("requirement statement differs from bound candidate")
        for key, check in checks.items():
            if check["success"] != {"pointer": "/checks/" + key, "equals": "PASS"}:
                raise PlanError("trace checks use the retained per-check PASS/FAIL observations")
        for case in cases.values():
            if case["kind"] not in CUSTOM_ARMS | {"nop", "oracle"}:
                raise PlanError("unsupported trace proof arm")
            for observation in case["observations"]:
                if observation["expected"].get("equals") not in {"PASS", "FAIL"}:
                    raise PlanError("trace proof requires per-check PASS/FAIL observations")
        kinds = {c["kind"] for c in cases.values()}
        if not {"nop", "oracle", "negative", "alternative"} <= kinds:
            raise PlanError("trace construction requires NOP, Oracle, negative and alternative controls")
        for case in cases.values():
            if case["kind"] in {"nop", "oracle", "alternative"}:
                expected = "FAIL" if case["kind"] == "nop" else "PASS"
                observations = {o["check"]: o["expected"] for o in case["observations"]}
                if set(observations) != set(checks) or any(o.get("equals") != expected for o in observations.values()):
                    raise PlanError("built-in and alternative controls must cover every scored check")
            elif any(o["expected"].get("equals") != "FAIL" for o in case["observations"]):
                raise PlanError("negative controls declare their targeted failing checks only")
        context = trace_format.validate(workspace, manifest=_trace_projection(value))
        result = {
            "status": "unresolved" if unresolved else "structurally_valid",
            "mode": mode,
            "requirements": len(requirements),
            "checks": len(checks),
            "inputs": len(value["inputs"]),
            "provenance": context["provenance"],
            "execution": "unmeasured",
        }
    result["unresolved_requirements"] = unresolved
    result["unmapped_requirements"] = sorted(requirements.keys() - covered)
    if result["unmapped_requirements"]:
        result["status"] = "incomplete"
    return result


def from_plan(value, contract=None):
    plan_format.validate(value, "proposal", contract=contract)
    result = copy.deepcopy(value)
    result.update(schema=SCHEMA, probes=[], starting_state=None, review=None, bindings=None, migration=[])
    for row in result["requirements"]:
        row["candidate_index"] = None
    for row in result["inputs"]:
        row.update(edits=[], review=None)
    for index, row in enumerate(result["prerequisites"]):
        row["id"] = f"dependency-{index + 1}"
    for case in result["cases"]:
        case.update(
            kind=contract["cases"][case["case"]]["kind"] if contract else "unresolved",
            requirements=sorted(
                {
                    r
                    for o in case["observations"]
                    for check in value["checks"]
                    if check["id"] == o["check"]
                    for r in check["requirements"]
                }
            ),
            reaches=[],
            dependency=None,
            rationale="Imported declared native behavior",
            review=None,
        )
    return result


def active_path(root):
    marker = root / "private/active-construction.json"
    if not marker.exists() and not marker.is_symlink():
        if (root / "private/repairs").is_dir():
            from .task_evidence import _repairs

            repairs = _repairs(root)
            if repairs and repairs[-1]["superseded"].get("active-construction.json") is not None:
                raise PlanError("activate the repaired canonical contract before recording fresh proof")
        return root / PATH
    value, _ = load(marker)
    plan_format.fields(value, {"schema", "path", "sha256"})
    if value["schema"] != "nemo.eval_author.active_construction.v1":
        raise PlanError("unknown active construction marker")
    path = trace_format.secure_path(root, value["path"])
    if not path.is_relative_to(root / "private"):
        raise PlanError("active construction must remain private")
    _, sha = load(path)
    if sha != value["sha256"]:
        raise PlanError("active construction changed; activate a fresh reviewed revision")
    return path


def activate(root, path):
    root = root.resolve()
    path = path.resolve()
    if not path.is_relative_to(root / "private"):
        raise PlanError("active construction must be inside the workspace private directory")
    # Historical proof stays attached to its old workspace and contract. A new
    # workspace is required to activate a migrated contract after proof exists.
    if (root / "validation.json").exists() or (root / "private/active-construction.json").exists():
        raise PlanError("record the current revision's repair before activating another contract")
    has_history = any((root / p).exists() for p in ("private/run-inputs", "private/probes", "private/repairs"))
    if has_history:
        from .task_evidence import _repairs

        repairs = _repairs(root)
        if (
            not repairs
            or repairs[-1]["superseded"].get("active-construction.json") is None
            or (root / "reproducibility.json").exists()
        ):
            raise PlanError("retain the historical proof workspace; activate in a fresh workspace")
    document, sha = load(path)
    if document.get("schema") != SCHEMA:
        raise PlanError("activation requires a canonical construction contract")
    assess(document, "draft", workspace=root)
    from .task_evidence import write_new

    write_new(
        root / "private/active-construction.json",
        {
            "schema": "nemo.eval_author.active_construction.v1",
            "path": path.relative_to(root).as_posix(),
            "sha256": sha,
        },
    )
    return {"active_contract": str(path), "sha256": sha, "execution": "unmeasured"}


def from_trace(root):
    context = trace_format.validate(root)
    old = context["manifest"]
    candidate = trace_format.load(root, "candidate.json")
    summary = trace_format.load(root, "summary.json")
    if not isinstance(candidate, dict) or not isinstance(summary, dict):
        raise PlanError("candidate and summary must be objects")
    requirements = [
        {
            "id": r["id"],
            "candidate_index": r["candidate_index"],
            "statement": candidate["requirements"][r["candidate_index"]]["description"],
            "authority": {
                "basis": "unresolved",
                "reference": "Review the retained ground-truth inventory and requirement evidence",
            },
        }
        for r in old["requirements"]
    ]
    checks: list[dict[str, Any]] = [
        {
            "id": key,
            "requirements": [r["id"] for r in old["requirements"] if key in r["checks"]],
            "success": {"pointer": "/checks/" + key, "equals": "PASS"},
        }
        for key in sorted(context["checks"])
    ]

    def observations(fails):
        return [
            {
                "check": c["id"],
                "expected": {"pointer": c["success"]["pointer"], "equals": "FAIL" if c["id"] in fails else "PASS"},
            }
            for c in checks
        ]

    used = {c["id"] for c in old["controls"]}

    def builtin_id(kind):
        name = "builtin-" + kind
        while name in used:
            name += "-builtin"
        used.add(name)
        return name

    cases = [
        {
            "case": builtin_id(name),
            "kind": name,
            "observations": observations(context["checks"] if name == "nop" else set()),
            "requirements": [r["id"] for r in requirements],
            "reaches": [],
            "dependency": None,
            "rationale": "Built-in control",
            "review": None,
        }
        for name in ("nop", "oracle")
    ]
    for control in old["controls"]:
        cases.append(
            {
                "case": control["id"],
                "kind": control["arm"],
                "observations": (
                    observations(set())
                    if control["arm"] == "alternative"
                    else [o for o in observations(control["fails"]) if o["check"] in control["fails"]]
                ),
                **{k: control[k] for k in ("requirements", "reaches", "dependency", "rationale", "review")},
            }
        )
    inputs = [
        {**row, "path": row["path"].removeprefix("task/"), "rationale": row["review"]["note"]}
        for row in old["starting_state"]["artifacts"]
    ]
    prerequisites = []
    for edge in old["dependencies"]:
        control = context["controls"][edge["bypass_control"]]
        check = next(c for c in checks if edge["dependent"] in c["requirements"] and c["id"] in control["fails"])
        prerequisites.append(
            {
                "id": edge["id"],
                "before": edge["prerequisite"],
                "after": edge["dependent"],
                "bypass_case": edge["bypass_control"],
                "check": check["id"],
            }
        )
    return {
        "schema": SCHEMA,
        "task_id": summary["task_id"],
        "provider": "harbor",
        "requirements": requirements,
        "inputs": inputs,
        "checks": checks,
        "cases": cases,
        "prerequisites": prerequisites,
        "probes": old["probes"],
        "starting_state": {k: v for k, v in old["starting_state"].items() if k != "artifacts"},
        "review": old["review"],
        "bindings": {k: old[k] for k in ("candidate_sha256", "trace_sha256")},
        "migration": [],
    }


def migrate(source, output, workspace=None, contract=None):
    value, sha = load(source)
    if value["schema"] == plan_format.SCHEMA:
        result = from_plan(value, contract)
    elif value["schema"] == trace_format.SCHEMA:
        if workspace is None or source.resolve() != (workspace / PATH).resolve():
            raise PlanError("trace migration requires the original workspace and construction path")
        result = from_trace(workspace)
    else:
        raise PlanError("migration expects a historical construction format")
    result["migration"] = [{"path": str(source.resolve()), "sha256": sha, "schema": value["schema"]}]
    assess(result, workspace=workspace, contract=contract)
    if output.resolve() == source.resolve():
        raise PlanError("migration output must not replace historical construction bytes")
    from .task_evidence import write_new

    if ".eval-author" not in output.resolve().parts or (
        workspace and output.resolve().is_relative_to((workspace / "task").resolve())
    ):
        raise PlanError("migration output must remain private under .eval-author and outside task/")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_new(output, result)
    return {
        "path": str(output.resolve()),
        "schema": SCHEMA,
        "execution": "unmeasured",
        "unresolved_authority": [r["id"] for r in result["requirements"] if r["authority"]["basis"] == "unresolved"],
    }


def validate(value, mode="proposal", task=None, contract=None):
    # Existing workspace API and native plan API share the canonical semantics.
    if isinstance(value, Path):
        path = active_path(value)
        document, sha = load(path)
        if document["schema"] == trace_format.SCHEMA:
            return trace_format.validate(value)
        assess(document, "draft", workspace=value)
        context = trace_format.validate(value, manifest=_trace_projection(document))
        context["sha256"] = sha
        context["canonical_path"] = path.relative_to(value).as_posix()
        return context
    if value.get("schema") == plan_format.SCHEMA:
        return assess(from_plan(value, contract), mode, task, contract)
    return assess(value, mode, task, contract)


def bind(path, task, contract):
    if path.resolve().is_relative_to(task.resolve()):
        raise PlanError("construction contract must remain private outside the task tree")
    value, sha = load(path)
    if value.get("schema") == plan_format.SCHEMA:
        # Preserve the exact historical binding shape for old receipts.
        return plan_format.bind(path, task, contract)
    assessment = assess(value, "draft", task, contract)
    return {"policy": SCHEMA, "path": str(path.resolve()), "sha256": sha, "assessment": assessment}


def recheck(binding, task, contract):
    plan_format.fields(binding, {"policy", "path", "sha256", "assessment"})
    if binding.get("policy") == plan_format.SCHEMA:
        return plan_format.recheck(binding, task, contract)
    plan_format.fields(binding, {"policy", "path", "sha256", "assessment"})
    if binding["policy"] != SCHEMA or bind(Path(binding["path"]), task, contract) != binding:
        raise PlanError("bound construction contract changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--mode", choices=("proposal", "draft"), default="proposal")
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--migrate-to", type=Path)
    parser.add_argument(
        "--activate", action="store_true", help="activate a reviewed canonical contract in a fresh workspace"
    )
    args = parser.parse_args()
    try:
        contract = load(args.contract)[0] if args.contract else None
        if contract:
            from .task_evidence import validate_contract

            validate_contract(contract)
        if args.activate:
            if args.workspace is None or args.migrate_to:
                raise PlanError("activation requires --workspace and cannot be combined with migration")
            result = activate(args.workspace, args.plan)
        elif args.migrate_to:
            result = migrate(args.plan, args.migrate_to, args.workspace, contract)
        else:
            value, _ = load(args.plan)
            result = (
                assess(value, args.mode, args.task_dir, contract, args.workspace)
                if value.get("schema") == SCHEMA
                else validate(value, args.mode, args.task_dir, contract)
            )
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}))
        return 1
