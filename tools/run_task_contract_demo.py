# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retain authored task-contract inspection and real local Ethos measurements."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "skills/eval-author-audit/evals/files/tasks/order-total"
REFERENCE = ROOT / "skills/eval-author-rubric/references/task-contract-authority.json"
MEASURE = ROOT / "skills/eval-author-rubric/scripts/measure.py"
CRITERIA = ("CREATE-03", "CREATE-04", "CREATE-05-A")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def cite(output, path, quote):
    data = (output / path).read_bytes()
    lines = data.decode().splitlines()
    selected = quote.splitlines()
    matches = [index for index in range(len(lines)) if lines[index : index + len(selected)] == selected]
    if len(matches) != 1:
        raise ValueError("authored evidence span no longer matches source")
    return {
        "path": path,
        "digest": digest(data),
        "start_line": matches[0] + 1,
        "end_line": matches[0] + len(selected),
        "quote": "\n".join(selected),
    }


def measure(output, directory, selection, validator=None):
    write_json(directory / "selection.json", selection)
    command = [
        sys.executable,
        "-B",
        str(MEASURE),
        "--root",
        str(directory),
        "--selection",
        str(directory / "selection.json"),
    ]
    if validator is not None:
        command += ["--ethos-validator", str(validator)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    (directory / "measure.stdout.json").write_text(completed.stdout, encoding="utf-8")
    (directory / "measure.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    report = json.loads(completed.stdout)
    if completed.returncode not in (0, 1) or report.get("schema") != "rubric-measurements-v2":
        raise ValueError("measurement failed; inspect retained measurement output")
    write_json(directory / "measure-report.json", report)
    return report, completed.returncode


def artifact(directory, ident, path, role, selected_digest=None):
    actual = digest((directory / path).read_bytes())
    return {
        "id": ident,
        "role": role,
        "path": path,
        "digest": selected_digest if selected_digest is not None else actual,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "local-demo-fixture",
        "evidence": [{"path": path, "digest": actual}],
    }


def inspect(scorer, orders):
    program = scorer.split("python - <<'CHECK'\n", 1)[1].rsplit("\nCHECK", 1)[0]
    tree = ast.parse(program)
    constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ok" for target in node.targets)
        and isinstance(node.value, (ast.Compare, ast.BoolOp))
    )
    total_comparison = next(
        node
        for node in ast.walk(assignment)
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "total"
    )
    expected_total = ast.literal_eval(total_comparison.comparators[0])
    rows = list(csv.DictReader(io.StringIO(orders)))
    fixture_total = format(sum((Decimal(row["amount"]) for row in rows), Decimal(0)), ".2f")
    summary_required = "/workspace/summary.json" in constants
    comparisons = [node for node in ast.walk(assignment) if isinstance(node, ast.Compare)]
    summary_comparisons = [
        node for node in comparisons if isinstance(node.left, ast.Name) and node.left.id == "summary"
    ]
    summary_expected = ast.literal_eval(summary_comparisons[0].comparators[0]) if summary_comparisons else None
    rewards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Name) and node.test.id == "ok"
    ]
    binary_reward = (
        len(rewards) == 1
        and ast.literal_eval(rewards[0].body) == "1.0"
        and ast.literal_eval(rewards[0].orelse) == "0.0"
    )
    return {
        "total_required": "/workspace/total.txt" in constants,
        "summary_required": summary_required,
        "condition_count": 5 if summary_required else 3,
        "expected_total": expected_total,
        "fixture_total": fixture_total,
        "fixture_order_count": len(rows),
        "summary_expected": summary_expected,
        "reward_rule": "binary-all-or-nothing" if binary_reward else "unrecognized",
    }


def contract_case(output, ident, scorer, expected, sources):
    directory = output / ident
    directory.mkdir()
    for name, data in sources.items():
        (directory / name).write_bytes(data)
    (directory / "test.sh").write_text(scorer, encoding="utf-8")
    prefix = ident + "/"
    instruction = sources["instruction.md"].decode().splitlines()[-1]

    def evidence(name, quote):
        return cite(output, prefix + name, quote)

    total_quote = (
        "    ok = total == '250.50' and summary == {'currency': 'USD', 'order_count': 3}"
        if expected["summary_required"]
        else "    ok = total == '250.50'"
    )
    conditions = [
        {
            "id": "total-output",
            "status": "supported",
            "solver_authority": evidence("instruction.md", instruction),
            "correctness_authority": "solver-visible instruction",
            "evidence": [
                evidence("test.sh", "    total = Path('/workspace/total.txt').read_text().strip()"),
                evidence("instruction.md", instruction),
            ],
        },
        {
            "id": "total-value",
            "status": "supported",
            "solver_authority": evidence("instruction.md", instruction),
            "correctness_authority": "exact Decimal sum of solver-visible orders.csv; proposed agent mapping",
            "evidence": [
                evidence("test.sh", total_quote),
                evidence("orders.csv", sources["orders.csv"].decode()),
                evidence("instruction.md", instruction),
            ],
        },
    ]
    if expected["summary_required"]:
        conditions += [
            {
                "id": "summary-output",
                "status": "unsupported",
                "solver_authority": None,
                "correctness_authority": None,
                "evidence": [
                    evidence("test.sh", "    summary = json.loads(Path('/workspace/summary.json').read_text())"),
                    evidence("instruction.md", instruction),
                ],
            },
            {
                "id": "summary-value",
                "status": "unsupported",
                "solver_authority": None,
                "correctness_authority": "order count follows fixture; USD and summary output have no declared solver authority",
                "evidence": [evidence("test.sh", total_quote), evidence("instruction.md", instruction)],
            },
        ]
    conditions.append(
        {
            "id": "binary-reward",
            "status": "ambiguous",
            "solver_authority": None,
            "correctness_authority": "scorer declares binary aggregation; owner scoring adjudication not supplied",
            "evidence": [evidence("test.sh", "Path('/logs/verifier/reward.txt').write_text('1.0' if ok else '0.0')")],
        }
    )
    for row in conditions:
        row["reviewer_kind"] = "agent"
    crosswalk = {
        "schema": "task-contract-static-crosswalk-v1",
        "provenance": "explicitly-authored-static-crosswalk",
        "not_measured_model_judgment": True,
        "not_emitted_rubric_score": True,
        "solver_visible_inventory": [prefix + "instruction.md", prefix + "orders.csv"],
        "inventory_scope": "order-total Dockerfile exposes only orders.csv; no solver-scope access probe performed",
        "scorer_inventory": "full embedded CHECK program, including reads, exact equality, exceptions and reward writer",
        "conditions": conditions,
        "semantic_notes": [
            "strip() permits outer whitespace; exact total string imposes two fractional digits",
            "OSError or ValueError leaves ok False; no native scorer or boundary solution was executed",
            "No currency field exists in orders.csv; hidden summary condition is a CREATE-04 candidate, not automatically CREATE-03",
            "Clean neighbor removes only summary read and equality; not a human-approved scorer repair",
        ],
    }
    write_json(directory / "crosswalk.json", crosswalk)
    observed = inspect(scorer, sources["orders.csv"].decode())
    manifest = {
        "schema": "rubric-contract-controls-v1",
        "instruction_id": "instruction.md",
        "dockerfile_id": "Dockerfile",
        "scorer_id": "scorer",
        "test_data_id": "orders.csv",
        "task_config_id": "task.toml",
    }
    write_json(directory / "contract.json", manifest)
    selected = {
        "artifacts": [artifact(directory, name, name, "task") for name in sources]
        + [
            artifact(directory, "scorer", "test.sh", "scorer"),
            artifact(directory, "contract-manifest", "contract.json", "task"),
        ],
        "contract_controls": {"artifact_id": "contract-manifest"},
    }
    report, returncode = measure(output, directory, selected)
    emitted = [criterion for criterion in CRITERIA if criterion in report["items"]]
    scores = {criterion: report["items"][criterion]["score"] for criterion in CRITERIA}
    strengths = {criterion: report["items"][criterion]["evidence_strength"] for criterion in CRITERIA}
    expected_scores = dict.fromkeys(CRITERIA)
    expected_strengths = dict.fromkeys(CRITERIA)
    if expected["summary_required"]:
        expected_scores["CREATE-04"] = 0
        expected_strengths["CREATE-04"] = "E1"
    expected = {**expected, "rubric_scores": expected_scores, "evidence_strengths": expected_strengths}
    observed.update(rubric_scores=scores, evidence_strengths=strengths)
    return {
        "id": ident,
        "kind": "task_contract",
        "reviewer_kind": "agent",
        "measurement_kind": "emitted-bounded-source-inspection",
        "expected": expected,
        "observed": observed,
        "matched": expected == observed,
        "assessment_state": "human-review-pending",
        "finding": "planted-unrequested-summary-condition" if expected["summary_required"] else "agent-only-crosswalk",
        "rubric_scores": scores,
        "emitted_criterion_ids": emitted,
        "crosswalk": prefix + "crosswalk.json",
        "report": prefix + "measure-report.json",
        "measure_exit_code": returncode,
        "next_action": (
            "Owner independently adjudicates requirement and correctness mappings; "
            "the bounded source score is not human approval, and unassessed criteria remain null"
        ),
    }


def ethos_cases(output):
    validator = ROOT / "team-skills/ethos/ethos-write/scripts/validate_ethos.py"
    fixture = ROOT / "team-skills/ethos/ethos-write/evals/files/router-help/ETHOS.md"
    valid = fixture.read_bytes()
    (output / "sources/ETHOS.md").write_bytes(valid)
    (output / "sources/validate_ethos.py").write_bytes(validator.read_bytes())
    invalid = valid.replace(b"\n## Constraints\n", b"\n## Limits\n")
    stale = valid + b"\n"
    definitions = [
        ("ethos-valid", valid, digest(valid), validator, "pass", True, []),
        ("ethos-invalid", invalid, digest(invalid), validator, "fail", False, ["ethos.invalid"]),
        ("ethos-stale", stale, digest(valid), validator, "fail", True, ["ethos.digest_mismatch"]),
        ("ethos-missing-validator", valid, digest(valid), None, "unknown", None, ["ethos.validator_unavailable"]),
    ]
    cases = []
    for ident, content, selected_digest, owning_validator, status, validity, codes in definitions:
        directory = output / ident
        directory.mkdir()
        (directory / "ETHOS.md").write_bytes(content)
        selection = {
            "artifacts": [artifact(directory, "ethos", "ETHOS.md", "ethos", selected_digest)],
            "ethos": {"artifact_id": "ethos"},
        }
        report, returncode = measure(output, directory, selection, owning_validator)
        observed = report["measurements"]["ethos"]
        expected = {"status": status, "valid": validity, "diagnostic_codes": codes}
        projection = {
            "status": observed["status"],
            "valid": observed["valid"],
            "diagnostic_codes": [row["code"] for row in observed["diagnostics"]],
        }
        cases.append(
            {
                "id": ident,
                "kind": "ethos",
                "measurement_kind": "emitted-ethos-structure-check",
                "expected": expected,
                "observed": observed,
                "matched": expected == projection,
                "selected_digest": selected_digest,
                "usable_authority": observed["status"] == "pass",
                "rubric_scores": dict.fromkeys(CRITERIA),
                "emitted_criterion_ids": [criterion for criterion in CRITERIA if criterion in report["items"]],
                "report": ident + "/measure-report.json",
                "measure_exit_code": returncode,
                "assessment_state": "structure-only-not-confirmed-owner-intent",
                "validator_source": validator.relative_to(ROOT).as_posix() if owning_validator else None,
                "next_action": "Confirm owner intent separately; repair invalid, stale or unavailable inputs before citing Ethos",
            }
        )
    return cases


def collect(output):
    source_dir = output / "sources"
    source_dir.mkdir()
    files = {
        "instruction.md": TASK / "instruction.md",
        "orders.csv": TASK / "environment/orders.csv",
        "Dockerfile": TASK / "environment/Dockerfile",
        "task.toml": TASK / "task.toml",
        "test.sh": TASK / "tests/test.sh",
        "task-contract-authority.json": REFERENCE,
        "task-contract-authority.md": REFERENCE.with_suffix(".md"),
    }
    source_bindings = {}
    for name, path in files.items():
        data = path.read_bytes()
        (source_dir / name).write_bytes(data)
        source_bindings[name] = {
            "source": path.relative_to(ROOT).as_posix(),
            "digest": digest(data),
            "retained": "sources/" + name,
        }
    canonical = json.loads((source_dir / "task-contract-authority.json").read_text())
    if [item["id"] for item in canonical["items"]] != list(CRITERIA):
        raise ValueError("canonical criterion identities changed")
    scorer = (source_dir / "test.sh").read_text()
    clean = scorer.replace("    summary = json.loads(Path('/workspace/summary.json').read_text())\n", "").replace(
        " and summary == {'currency': 'USD', 'order_count': 3}", ""
    )
    sources = {
        name: (source_dir / name).read_bytes() for name in ("instruction.md", "orders.csv", "Dockerfile", "task.toml")
    }
    expected = {
        "total_required": True,
        "summary_required": True,
        "condition_count": 5,
        "expected_total": "250.50",
        "fixture_total": "250.50",
        "fixture_order_count": 3,
        "summary_expected": {"currency": "USD", "order_count": 3},
        "reward_rule": "binary-all-or-nothing",
    }
    clean_expected = {**expected, "summary_required": False, "condition_count": 3, "summary_expected": None}
    cases = [
        contract_case(output, "planted-summary", scorer, expected, sources),
        contract_case(output, "clean-neighbor", clean, clean_expected, sources),
        contract_case(output, "agent-only-review-pending", clean, clean_expected, sources),
    ]
    cases.extend(ethos_cases(output))
    panel = {
        "schema": "task-contract-demo-v1",
        "native_execution": False,
        "model_execution": False,
        "human_adjudication": False,
        "aggregate_score": None,
        "source_bindings": source_bindings,
        "cases": cases,
        "case_count": len(cases),
        "matched_count": sum(case["matched"] for case in cases),
        "scope": (
            "actual CLI bounded source scoring for frozen supported source shapes only, "
            "separate authored static crosswalks, and emitted Ethos structure checks"
        ),
    }
    write_json(output / "panel.json", panel)
    (output / "report.md").write_text(
        "# Task contract and Ethos local demonstration\n\n"
        + panel["scope"]
        + ". No human adjudication, model, native or container execution.\n\n"
        + "Static crosswalks are authored, not emitted rubric scores. "
        + "The clean neighbor is not human-approved; null scores are not a clean bill of health.\n\n"
        + "\n".join(
            f"- {case['id']}: expected-vs-observed match={case['matched']}; "
            + "; ".join(
                f"{criterion}="
                + (
                    "null"
                    if case["rubric_scores"][criterion] is None
                    else f"{case['rubric_scores'][criterion]}/{case['observed']['evidence_strengths'][criterion]}"
                )
                for criterion in CRITERIA
            )
            + f"; {case['assessment_state']}; [report]({case['report']})"
            for case in cases
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        output / "hashes.json",
        {
            path.relative_to(output).as_posix(): digest(path.read_bytes())
            for path in sorted(output.rglob("*"))
            if path.is_file()
        },
    )
    return panel


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new local output directory")
    args = parser.parse_args(argv)
    try:
        output = args.output.absolute()
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        panel = collect(output)
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        print(json.dumps({"schema": "task-contract-demo-v1", "status": "invalid", "code": "demo.failed"}))
        return 2
    print(json.dumps(panel, indent=2, allow_nan=False))
    return 0 if panel["matched_count"] == panel["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
