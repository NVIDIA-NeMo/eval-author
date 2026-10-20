#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate private construction declarations; this never establishes execution proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

SCHEMA = "nemo.eval_author.construction_plan.v1"
MAX_BYTES = 1024 * 1024


class PlanError(ValueError):
    """A declaration is malformed, unresolved for construction, or changed."""


def load(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise PlanError("plan must be a bounded regular file")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise PlanError("plan exceeds size limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PlanError(f"duplicate key: {key}")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs)
    json.dumps(value, allow_nan=False)
    if not isinstance(value, dict):
        raise PlanError("plan must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def fields(value, required: set[str], optional: set[str] | None = None):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise PlanError(f"expected fields: {', '.join(sorted(required))}")


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise PlanError("nonempty text required")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise PlanError("IDs must be lowercase kebab-case")


def indexed(rows, key="id"):
    if not isinstance(rows, list) or len(rows) > 256:
        raise PlanError("expected a list of at most 256 entries")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PlanError("expected object entry")
        identifier(row.get(key))
        if row[key] in result:
            raise PlanError(f"duplicate {key}: {row[key]}")
        result[row[key]] = row
    return result


def assertion(value):
    if not isinstance(value, dict) or set(value) not in ({"pointer", "equals"}, {"pointer", "min", "max"}):
        raise PlanError("assertion needs pointer and equals or min/max")
    pointer = value["pointer"]
    if not isinstance(pointer, str) or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise PlanError("assertion needs an absolute JSON pointer")
    json.dumps(value, allow_nan=False)
    if "min" in value and (
        any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) for k in ("min", "max"))
        or value["min"] > value["max"]
    ):
        raise PlanError("assertion bounds must be finite and ordered")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def accepts(check, value):
    if "equals" in check:
        return type(value) is type(check["equals"]) and value == check["equals"]
    return type(value) in (int, float) and check["min"] <= value <= check["max"]


def disjoint(success, expected):
    """An expected control observation must contradict this SAME scored check."""
    if success["pointer"] != expected["pointer"]:
        return False
    if "equals" in expected:
        return not accepts(success, expected["equals"])
    if "equals" in success:
        return not accepts(expected, success["equals"])
    return expected["max"] < success["min"] or expected["min"] > success["max"]


def input_file(task: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(p in {"..", ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"} for p in path.parts)
    ):
        raise PlanError("input path must be task-relative and outside excluded caches")
    current = task
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise PlanError("input symlinks are unsupported")
    if not current.is_file() or not current.resolve().is_relative_to(task.resolve()):
        raise PlanError(f"missing regular task input: {relative}")
    return current


def validate(plan: dict, mode: str, task: Path | None = None, contract: dict | None = None) -> dict:
    fields(plan, {"schema", "task_id", "provider", "requirements", "inputs", "checks", "cases", "prerequisites"})
    if plan["schema"] != SCHEMA or mode not in ("proposal", "draft") or plan["provider"] not in ("harbor", "gym"):
        raise PlanError("unsupported schema, mode, or provider")
    text(plan["task_id"])
    requirements = indexed(plan["requirements"])
    if not requirements:
        raise PlanError("declare at least one intended requirement")
    unresolved = []
    for key, row in requirements.items():
        fields(row, {"id", "statement", "authority"})
        text(row["statement"])
        fields(row["authority"], {"basis", "reference"})
        if row["authority"]["basis"] not in ("specification", "user_decision", "independent_reference", "unresolved"):
            raise PlanError("authority must be a specification, user decision, independent reference, or unresolved")
        text(row["authority"]["reference"])
        if row["authority"]["basis"] == "unresolved":
            unresolved.append(key)
    if mode == "draft" and unresolved:
        raise PlanError("unresolved grading authority: " + ", ".join(unresolved))
    if not isinstance(plan["inputs"], list) or len(plan["inputs"]) > 256:
        raise PlanError("inputs must be a bounded list")
    paths = set()
    for row in plan["inputs"]:
        fields(row, {"path", "basis", "source", "rationale", "sha256"})
        for key in ("path", "source", "rationale"):
            text(row[key])
        if row["basis"] not in ("copied", "authored"):
            raise PlanError("input basis must be copied or authored")
        if row["path"] in paths:
            raise PlanError("duplicate input path")
        paths.add(row["path"])
        if row["sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"])):
            raise PlanError("input hash must be SHA-256 or null for a proposal")
        if mode == "draft":
            if task is None or hashlib.sha256(input_file(task, row["path"]).read_bytes()).hexdigest() != row["sha256"]:
                raise PlanError("materialized input hash mismatch")
    checks = indexed(plan["checks"])
    covered = set()
    pointers = set()
    for row in checks.values():
        fields(row, {"id", "requirements", "success"})
        refs = row["requirements"]
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(r, str) or r not in requirements for r in refs)
            or len(set(refs)) != len(refs)
        ):
            raise PlanError("check must reference distinct declared requirements")
        covered.update(refs)
        assertion(row["success"])
        if row["success"]["pointer"] in pointers:
            raise PlanError("one scored observation cannot masquerade as multiple checks")
        pointers.add(row["success"]["pointer"])
    if mode == "draft" and covered != set(requirements):
        raise PlanError("every requirement needs a mapped scored check")
    cases = indexed(plan["cases"], "case")
    mapped = {}
    for name, row in cases.items():
        fields(row, {"case", "observations"})
        observations = indexed(row["observations"], "check")
        if not observations:
            raise PlanError("each mapped case needs observations")
        expected = []
        for key, item in observations.items():
            fields(item, {"check", "expected"})
            if key not in checks:
                raise PlanError("case names an undeclared check")
            assertion(item["expected"])
            if item["expected"]["pointer"] != checks[key]["success"]["pointer"]:
                raise PlanError("case observation must target the mapped scored check")
            expected.append(canonical(item["expected"]))
        mapped[name] = observations
        if contract is not None:
            native = contract.get("cases", {}).get(name)
            if native is None or native.get("kind") == "diagnostic":
                raise PlanError("case must name a declared non-diagnostic native case")
            if sorted(expected) != sorted(canonical(c) for c in native["checks"]):
                raise PlanError("mapped observations must equal this native case's behavior assertions")
            if native["kind"] in ("reference", "alternative", "agent"):
                if set(observations) != set(checks) or any(
                    canonical(o["expected"]) != canonical(checks[k]["success"]) for k, o in observations.items()
                ):
                    raise PlanError("positive and agent cases must assert every declared success condition")
            if native["kind"] == "incorrect" and not any(
                disjoint(checks[k]["success"], o["expected"]) for k, o in observations.items()
            ):
                raise PlanError("incorrect case must reject a mapped success condition")
    if contract is not None:
        if plan["task_id"] != contract.get("task_id") or plan["provider"] != contract.get("provider"):
            raise PlanError("plan and native contract identities differ")
        required = {k for k, v in contract["cases"].items() if v["kind"] != "diagnostic"}
        if set(cases) != required:
            raise PlanError("map every non-diagnostic native case exactly once")
    elif mode == "draft":
        raise PlanError("draft validation requires the native result contract")
    if not isinstance(plan["prerequisites"], list) or len(plan["prerequisites"]) > 256:
        raise PlanError("prerequisites must be a bounded list")
    edges = set()
    for edge in plan["prerequisites"]:
        fields(edge, {"before", "after", "bypass_case", "check"})
        before, after = edge["before"], edge["after"]
        if (
            not isinstance(before, str)
            or not isinstance(after, str)
            or before not in requirements
            or after not in requirements
            or before == after
            or (before, after) in edges
        ):
            raise PlanError("prerequisites need distinct declared requirements and unique edges")
        edges.add((before, after))
        key, name = edge["check"], edge["bypass_case"]
        if (
            not isinstance(key, str)
            or not isinstance(name, str)
            or key not in checks
            or name not in mapped
            or key not in mapped[name]
        ):
            raise PlanError("prerequisite requires a mapped bypass observation")
        if checks[key]["requirements"] != [after] or not disjoint(
            checks[key]["success"], mapped[name][key]["expected"]
        ):
            raise PlanError("bypass must contradict the dependent check, not an unrelated or aggregate check")
        if contract is not None and contract["cases"][name]["kind"] != "incorrect":
            raise PlanError("bypass must be a native incorrect control")
    remaining = set(requirements)
    while remaining:
        roots = {r for r in remaining if not any(a in remaining and b == r for a, b in edges)}
        if not roots:
            raise PlanError("cyclic prerequisite graph")
        remaining -= roots
    return {
        "status": "unresolved" if unresolved else "structurally_valid",
        "mode": mode,
        "requirements": len(requirements),
        "inputs": len(paths),
        "checks": len(checks),
        "planned_prerequisites": len(edges),
        "unresolved_requirements": unresolved,
        "execution": "unmeasured",
        "provenance_scope": "declared_materialized_inputs" if mode == "draft" else "proposed_inputs",
    }


def bind(path: Path, task: Path, contract: dict) -> dict:
    if path.resolve().is_relative_to(task.resolve()):
        raise PlanError("construction plan must remain private outside the task tree")
    plan, digest = load(path)
    assessment = validate(plan, "draft", task, contract)
    return {"policy": SCHEMA, "path": str(path.resolve()), "sha256": digest, "assessment": assessment}


def recheck(binding: Any, task: Path, contract: dict) -> None:
    fields(binding, {"policy", "path", "sha256", "assessment"})
    if binding["policy"] != SCHEMA or bind(Path(binding["path"]), task, contract) != binding:
        raise PlanError("bound construction plan changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--mode", choices=("proposal", "draft"), default="proposal")
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--contract", type=Path)
    args = parser.parse_args()
    try:
        plan, _ = load(args.plan)
        contract = None
        if args.contract:
            from task_evidence import read, validate_contract

            contract = read(args.contract)
            validate_contract(contract)
        if args.mode == "draft":
            if args.task_dir is None or contract is None:
                raise PlanError("draft mode requires --task-dir and --contract")
            result = bind(args.plan, args.task_dir, contract)["assessment"]
        else:
            result = validate(plan, args.mode, args.task_dir, contract)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
