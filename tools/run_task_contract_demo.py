# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retain authored task-contract inspection and real local Ethos measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NotRequired, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rubric_demo_common import ArtifactRecord, artifact_record, run_measure  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "skills/eval-author-audit/evals/files/tasks/order-total"
REFERENCE = ROOT / "skills/eval-author-rubric/references/task-contract-authority.json"
MEASURE = ROOT / "skills/eval-author-rubric/scripts/measure.py"
CRITERIA = (
    "task-contract.clear-contract",
    "task-contract.discoverable-requirements",
    "task-contract.scoring-authority",
)


class Citation(TypedDict):
    path: str
    digest: str
    start_line: int
    end_line: int
    quote: str


class ContractObservation(TypedDict):
    total_required: bool
    summary_required: bool
    condition_count: int
    expected_total: str
    fixture_total: str
    fixture_order_count: int
    summary_expected: dict[str, str | int] | None
    reward_rule: str
    rubric_scores: NotRequired[dict[str, int | None]]
    evidence_strengths: NotRequired[dict[str, str | None]]


class CrosswalkCondition(TypedDict):
    id: str
    status: str
    solver_authority: Citation | None
    correctness_authority: str | None
    evidence: list[Citation]
    reviewer_kind: NotRequired[str]


class Crosswalk(TypedDict):
    schema: str
    provenance: str
    not_measured_model_judgment: bool
    not_emitted_rubric_score: bool
    solver_visible_inventory: list[str]
    inventory_scope: str
    scorer_inventory: str
    conditions: list[CrosswalkCondition]
    semantic_notes: list[str]


class DemoCase(TypedDict):
    id: str
    kind: str
    measurement_kind: str
    expected: Any
    observed: Any
    matched: bool
    assessment_state: str
    rubric_scores: dict[str, int | None]
    emitted_criterion_ids: list[str]
    report: str
    measure_exit_code: int
    next_action: str
    reviewer_kind: NotRequired[str]
    finding: NotRequired[str]
    crosswalk: NotRequired[str]
    selected_digest: NotRequired[str]
    structure_usable: NotRequired[bool]
    usable_authority: NotRequired[bool]
    owner_confirmation: NotRequired[dict[str, str | None]]
    validator_source: NotRequired[str | None]


class SourceBinding(TypedDict):
    source: str
    digest: str
    retained: str


class DemoPanel(TypedDict):
    schema: str
    native_execution: bool
    model_execution: bool
    human_adjudication: bool
    aggregate_score: None
    source_bindings: dict[str, SourceBinding]
    cases: list[DemoCase]
    case_count: int
    matched_count: int
    scope: str


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def cite(output: Path, path: str, quote: str) -> Citation:
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


def measure(
    output: Path, directory: Path, selection: dict[str, Any], validator: Path | None = None
) -> tuple[dict[str, Any], int]:
    write_json(directory / "selection.json", selection)
    return run_measure(directory, validator=validator)


def artifact(directory: Path, ident: str, path: str, role: str, selected_digest: str | None = None) -> ArtifactRecord:
    return artifact_record(
        directory, ident, path, role, selected_digest=selected_digest, revision=None, source="local-demo-fixture"
    )


def contract_observation(report: dict[str, Any]) -> ContractObservation:
    inspection = report["items"]["task-contract.discoverable-requirements"]["source_inspection"]
    condition_ids = {condition["id"] for condition in inspection["conditions"]}
    return {
        "total_required": "total-output" in condition_ids,
        "summary_required": "summary-output" in condition_ids,
        "condition_count": inspection["condition_count"],
        "expected_total": inspection["expected_total"],
        "fixture_total": inspection["fixture_total"],
        "fixture_order_count": inspection["fixture_order_count"],
        "summary_expected": inspection["summary_expected"],
        "reward_rule": inspection["reward_rule"],
        "rubric_scores": {criterion: report["items"][criterion]["score"] for criterion in CRITERIA},
        "evidence_strengths": {criterion: report["items"][criterion]["evidence_strength"] for criterion in CRITERIA},
    }


def contract_case(
    output: Path, ident: str, scorer: str, expected: ContractObservation, sources: dict[str, bytes]
) -> DemoCase:
    directory = output / ident
    directory.mkdir()
    for name, data in sources.items():
        (directory / name).write_bytes(data)
    (directory / "test.sh").write_text(scorer, encoding="utf-8")
    prefix = ident + "/"
    instruction = sources["instruction.md"].decode().splitlines()[-1]

    def evidence(name: str, quote: str) -> Citation:
        return cite(output, prefix + name, quote)

    total_quote = (
        "    ok = total == '250.50' and summary == {'currency': 'USD', 'order_count': 3}"
        if expected["summary_required"]
        else "    ok = total == '250.50'"
    )
    conditions: list[CrosswalkCondition] = [
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
    crosswalk: Crosswalk = {
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
            "No currency field exists in orders.csv; hidden summary condition is a task-contract.discoverable-requirements candidate, not automatically task-contract.clear-contract",
            "Clean neighbor removes only summary read and equality; not a human-approved scorer repair",
        ],
    }
    write_json(directory / "crosswalk.json", crosswalk)
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

    expected_scores: dict[str, int | None] = dict.fromkeys(CRITERIA)
    expected_strengths: dict[str, str | None] = dict.fromkeys(CRITERIA)
    if expected["summary_required"]:
        expected_scores["task-contract.discoverable-requirements"] = 0
        expected_strengths["task-contract.discoverable-requirements"] = "E1"
    expected = {**expected, "rubric_scores": expected_scores, "evidence_strengths": expected_strengths}
    observed = contract_observation(report)
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


def ethos_cases(output: Path) -> list[DemoCase]:
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
    cases: list[DemoCase] = []
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
                "structure_usable": observed["status"] == "pass",
                "usable_authority": False,
                # This fixture has no reviewed user turn. Do not manufacture a
                # journey-state confirmation or duplicate its owning validator.
                "owner_confirmation": {
                    "status": "NOT_RUN",
                    "owner": "skills/eval-author/scripts/journey_state.py",
                    "confirmed_digest": None,
                    "confirmation_source": None,
                },
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


def collect(output: Path) -> DemoPanel:
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
    source_bindings: dict[str, SourceBinding] = {}
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
    expected: ContractObservation = {
        "total_required": True,
        "summary_required": True,
        "condition_count": 5,
        "expected_total": "250.50",
        "fixture_total": "250.50",
        "fixture_order_count": 3,
        "summary_expected": {"currency": "USD", "order_count": 3},
        "reward_rule": "binary-all-or-nothing",
    }
    clean_expected: ContractObservation = {
        **expected,
        "summary_required": False,
        "condition_count": 3,
        "summary_expected": None,
    }
    cases = [
        contract_case(output, "planted-summary", scorer, expected, sources),
        contract_case(output, "clean-neighbor", clean, clean_expected, sources),
    ]
    cases.extend(ethos_cases(output))
    panel: DemoPanel = {
        "schema": "task-contract-demo-v2",
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
    titles = {item["id"]: item["title"] for item in canonical["items"]}
    write_json(output / "panel.json", panel)
    (output / "report.md").write_text(
        "# Task contract and Ethos local demonstration\n\n"
        + panel["scope"]
        + ". No human adjudication, model, native or container execution.\n\n"
        + "The static crosswalks contain authored inspections, not emitted rubric scores. "
        + "The clean neighbor is not human-approved. An Unassessed score does not establish that a task is free of defects.\n\n"
        + "The installed ethos-write validator checks Ethos structure. Owner confirmation: NOT_RUN. "
        + "The owning skills/eval-author/scripts/journey_state.py validates saved-byte confirmation. "
        + "This demo supplies no reviewed user turn. It creates no confirmation or journey-state approval. "
        + "Usable structure does not establish owner authority.\n\n"
        + "Criteria: "
        + "; ".join(f"{item['title']} (`{item['id']}`)" for item in canonical["items"])
        + ".\n\n"
        + "\n".join(
            f"- {case['id']}: expected-vs-observed match={case['matched']}; "
            + "; ".join(
                f"{titles[criterion]}="
                + (
                    "Unassessed"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new local output directory")
    args = parser.parse_args(argv)
    try:
        output = args.output.absolute()
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        panel = collect(output)
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        print(json.dumps({"schema": "task-contract-demo-v2", "status": "invalid", "code": "demo.failed"}))
        return 2
    print(json.dumps(panel, indent=2, allow_nan=False))
    return 0 if panel["matched_count"] == panel["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
