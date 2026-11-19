# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ordinary CREATE-02 zero plus fresh local repaired-scorer controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import rubric_create02_subject as subject
import rubric_create02_subject_runtime as runtime
import rubric_table as table

REPAIR_ID = "create02-current-order-repaired-subject"
REPAIR_SCOPE = "finite-heldout-refund-current-order-repair-v1"
FINDING = "CREATE02-HELDOUT-STALE-TEMPLATE-LABEL-01"
NOTE = (
    "Historical ordinary wording 'Correct cue-selected answers are not penalized' is false as a published-credit claim: "
    "the defective scorer gives correct current-order answers zero. Legitimate shortcuts are not condemned. "
    "The accepted observation is local E2; actual contextual agent review adds limited E1, not independence."
)
LIMITS = [
    "Fresh deterministic local execution, not native execution, model evaluation or independent corroboration.",
    "Changed scorer and new instances inherit no owner/agent qualification; repaired criterion score remains Unassessed.",
    "Semantic option identities are not randomized. New instances remain in the same synthetic family.",
    "All seven leakage upper anchors, original contract/discovery independence and matched fresh-session review-benefit comparison remain separate.",
]


def load(path: Path) -> Any:
    return json.loads(table.read_private(path))


def write(path: Path, value: Any) -> None:
    path.write_text(table.encoded(value) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repaired_source() -> bytes:
    raw = Path(runtime.__file__).read_bytes()
    old = b'expected = task["development_key_cache"][task["template_id"]]'
    if raw.count(old) != 1:
        raise ValueError("unsupported scorer repair")
    return raw.replace(old, b'expected = entitlement(task["evidence"])', 1)


def identity() -> dict[str, Any]:
    return {
        "integration_sha256": sha(Path(__file__)),
        "matrix_consumer_sha256": sha(Path(__file__).with_name("rubric_mutation_runner.py")),
        "canonical_null_consumer_sha256": sha(Path(__file__).with_name("rubric_evidence_scoring.py")),
        "original": subject.implementation(),
        "repaired_runtime_sha256": hashlib.sha256(repaired_source()).hexdigest(),
    }


def repair_declaration() -> dict[str, Any]:
    return {
        "schema": REPAIR_SCOPE,
        "claim": subject.CLAIM,
        "instances": [
            {"partition": "retained-affected" if i < 2 else "fresh-same-family", "task": runtime.instance(i)}
            for i in range(4)
        ],
        "change": "Published scorer derives the key from the current order instead of its development template cache.",
        "approval_inherited": False,
    }


def build_repair(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, mode=0o700)
    write(root / "subject.json", repair_declaration())
    (root / "subject-runtime.py").write_bytes(repaired_source())
    artifacts = []
    for ident, name, role in (
        (REPAIR_ID, "subject.json", "task"),
        (REPAIR_ID + "-runtime", "subject-runtime.py", "dependency"),
    ):
        value = sha(root / name)
        artifacts.append(
            {
                "id": ident,
                "path": name,
                "role": role,
                "digest": value,
                "access": "available",
                "revision": None,
                "selection_origin": "separate fresh repair, no inherited approval",
                "source": name,
                "evidence": [{"path": name, "digest": value}],
            }
        )
    selected = {"artifacts": artifacts}
    write(root / "selection.json", selected)
    return selected


def admit(root: Path, selected: dict[str, Any]) -> None:
    if set(selected) != {"artifacts"} or len(selected["artifacts"]) != 2:
        raise ValueError("repair must be selected alone")
    expected = {REPAIR_ID: ("subject.json", "task"), REPAIR_ID + "-runtime": ("subject-runtime.py", "dependency")}
    if {a["id"] for a in selected["artifacts"]} != set(expected):
        raise ValueError("repair inventory differs")
    for artifact in selected["artifacts"]:
        if (artifact["path"], artifact["role"]) != expected[artifact["id"]] or artifact["access"] != "available":
            raise ValueError("repair artifact alias")
    if subject.identify(root, selected["artifacts"])["status"] != "pass":
        raise ValueError("repair source identity differs")
    if (
        load(root / "subject.json") != repair_declaration()
        or table.read_private(root / "subject-runtime.py") != repaired_source()
    ):
        raise ValueError("unsupported repaired source")


def independent_checks(task: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    evidence = task["evidence"]
    order = evidence["order"]
    expected = order["merchandise_cents"] - order["discount_cents"] - sum(evidence["prior_refunds_cents"])
    expected += order["tax_cents"] if evidence["refund_tax"] else 0
    checks = {
        "amount_cents": type(answer.get("amount_cents")) is int and answer["amount_cents"] == expected,
        "destination": answer.get("destination") == evidence["original_payment"],
    }
    return {"checks": checks, "credit": int(all(checks.values())), "maximum": 1, "health": "ok"}


def execute_arm(
    directory: Path,
    task: dict[str, Any],
    presentation: dict[str, Any],
    policy: str,
    scorer: ModuleType,
    evidence: bool,
    styled: bool,
) -> dict[str, Any]:
    directory.mkdir(parents=True)
    (directory / "presentation.json").write_text(json.dumps(presentation, indent=4 if styled else None))
    if evidence:
        (directory / "evidence.json").write_text(json.dumps(task["evidence"]))
    decision = scorer.consume(directory, policy)
    result = {
        **decision,
        "candidate_sha256": subject.digest(decision["answer"]),
        "input_inventory": {p.name: sha(p) for p in sorted(directory.iterdir())},
        "feasible": scorer.feasible(task, decision["answer"]),
        "published": scorer.published_score(task, decision["answer"]),
        "evidence_oracle": independent_checks(task, decision["answer"]),
    }
    return result


def collect_repair(root: Path, selected: dict[str, Any]) -> dict[str, Any]:
    import tempfile

    admit(root, selected)
    bound = identity()
    repaired = ModuleType("trusted_current_order_repaired_runtime")
    # Execute only a narrow transformation of trusted current source, never retained Python.
    exec(compile(repaired_source(), "<trusted-current-order-repair>", "exec"), repaired.__dict__)
    pairs, defective_pairs = [], []
    inventories = {}
    with tempfile.TemporaryDirectory(prefix="create02-fresh-repair-") as temporary:
        work = Path(temporary)
        for index, record in enumerate(repair_declaration()["instances"]):
            task = record["task"]
            aligned = deepcopy(task["presentation"])
            aligned["template_label"] = "current-order"
            misleading = deepcopy(aligned)
            misleading["template_label"] = "draft-default"
            swapped = deepcopy(aligned)
            swapped["options"].reverse()
            for kind, left, right, evidence, styled in (
                ("misleading-cue", aligned, misleading, True, False),
                ("cue-only", aligned, misleading, False, False),
                ("nuisance-swap", aligned, swapped, True, False),
                ("style-only", aligned, aligned, True, True),
            ):
                for policy in ("cue-only", "evidence-first") if evidence else ("cue-only",):
                    for version, scorer, destination in (
                        ("defective", runtime, defective_pairs),
                        ("repaired", repaired, pairs),
                    ):
                        label = f"{version}/{index}-{kind}-{policy}"
                        baseline = execute_arm(work / label / "a", task, left, policy, scorer, evidence, False)
                        intervention = execute_arm(work / label / "b", task, right, policy, scorer, evidence, styled)
                        destination.append(
                            {
                                "instance": index,
                                "partition": record["partition"],
                                "class": kind,
                                "policy": policy,
                                "task_sha256": subject.digest(task),
                                "scorer_sha256": sha(Path(runtime.__file__))
                                if version == "defective"
                                else bound["repaired_runtime_sha256"],
                                "baseline": baseline,
                                "intervention": intervention,
                                "published_transition": [
                                    baseline["published"]["credit"],
                                    intervention["published"]["credit"],
                                ],
                                "evidence_transition": [
                                    baseline["evidence_oracle"]["credit"],
                                    intervention["evidence_oracle"]["credit"],
                                ],
                            }
                        )
        for path in sorted(work.rglob("*.json")):
            inventories[path.relative_to(work).as_posix()] = {"sha256": sha(path), "content": path.read_text()}
    for bad, good in zip(defective_pairs, pairs, strict=True):
        for arm in ("baseline", "intervention"):
            if bad[arm]["input_inventory"] != good[arm]["input_inventory"] or bad[arm]["answer"] != good[arm]["answer"]:
                raise ValueError("fixed-policy matched scorer comparison differs")
            if not good[arm]["feasible"] or good[arm]["published"] != good[arm]["evidence_oracle"]:
                raise ValueError("fresh repaired scoring fails current-order checks")
    admit(root, selected)
    if identity() != bound:
        raise ValueError("repair source changed during collection")
    return {
        "schema": "create02-fresh-repair-observations-v1",
        "scope": REPAIR_SCOPE,
        "binding": bound,
        "selection_sha256": subject.digest(selected),
        "pairs": pairs,
        "defective_counterparts": defective_pairs,
        "inputs": inventories,
        "execution_state": "COMPLETED",
        "fresh_policy_execution": True,
        "native_execution": False,
        "model_execution": False,
        "independent": False,
        "approval_inherited": False,
        "limitations": LIMITS,
        "material_bypass_observed": False,
    }


def assess_repair(root: Path, selected: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    from rubric_leakage_current import reconcile

    projection = collect_repair(root, selected)
    result = reconcile(report, {})
    result["measurements"]["create02_subject"] = projection
    row = result["items"]["CREATE-02"]
    row.update(
        score=None,
        status="NOT_ASSESSED",
        assessment_state="NOT_ASSESSED",
        execution_state="COMPLETED",
        evidence_strength="E2",
        assessment_scope=REPAIR_SCOPE,
        findings=[
            "Fresh executions on retained-affected and new same-family orders reject stale answers and credit correct current-order answers on both checks."
        ],
        remaining_gaps=LIMITS,
        limitations=LIMITS,
        evidence_refs=[{"measurement": "create02_subject", "digest": subject.digest(projection["binding"])}],
    )
    for check in row["check_results"]:
        check.update(
            outcome="NOT_RUN" if check["id"].endswith("C02") else "PASS",
            evidence_refs=row["evidence_refs"],
            remaining_gap=LIMITS[1] if check["id"].endswith("C02") else LIMITS[2],
        )
    return result


def repair_cli(arguments: list[str], *, repaired: bool = True) -> int:
    original = table.assess_table

    def dispatch(root: Path, selected: dict[str, Any], task: str | None) -> dict[str, Any]:
        from rubric_evidence_scoring import _empty_rows

        if repaired:
            admit(root, selected)
        elif subject.selected_input(root, selected) is None:
            raise ValueError("separate accepted subject required")
        result = original(root, selected, task)
        if not repaired:
            row = result["items"]["CREATE-02"]
            row["historical_bound_findings"] = deepcopy(row["findings"])
            row["findings"] = [
                text.replace(
                    "Correct cue-selected answers are not penalized.",
                    "Correct current-order answers receive published zero; this review does not condemn legitimate shortcuts.",
                )
                for text in row["findings"]
            ]
        if repaired:
            result = assess_repair(root, selected, result)
        # Non-task selections omit contract rows upstream. Use owning canonical null rows.
        for ident, row in _empty_rows().items():
            if ident.startswith("task-contract.") and ident not in result["items"]:
                result["items"][ident] = row
        return result

    table.assess_table = dispatch  # ty: ignore[invalid-assignment]
    try:
        return table.main(arguments)
    finally:
        table.assess_table = original


def replay_record(record: dict[str, Any]) -> bool:
    saved = load(Path(record["report"]))
    repaired = any(a["id"] == REPAIR_ID for a in saved["selection"]["artifacts"])
    prefix = [str(Path(__file__)), "--repair-consumer" if repaired else "--subject-consumer"]
    argv = [
        sys.executable,
        "-B",
        *prefix,
        "--root",
        record["root"],
        "--verify-existing",
        str(Path(record["report"]).parent),
    ]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key, value in record["options"].items():
        env["EVAL_AUTHOR_" + key.replace("-", "_").upper()] = str(value)
    result = subprocess.run(argv, capture_output=True, env=env, timeout=600)
    return (
        result.returncode == record["exit"]
        and result.stdout == Path(record["report"]).with_name("results.md").read_bytes()
    )


def verify_integration(output: Path) -> dict[str, Any]:
    import rubric_mutation_runner as runner

    matrix = load(output / "matrix.json")
    if load(output / "source-freeze.json") != identity():
        raise ValueError("integration source freeze differs")
    rebuilt = runner.matrix_summary(matrix["base"], matrix["runs"], matrix["repair"])
    if any(matrix[key] != value for key, value in rebuilt.items()):
        raise ValueError("current matrix derivation differs")
    records = [matrix["base"], matrix["repair"], matrix["fresh_repair"]]
    records.extend(run["assessment"] for run in matrix["runs"])
    records.extend(
        run[field] for run in matrix["runs"] for field in ("criterion_baseline", "criterion_repair") if field in run
    )
    distinct = {str(Path(record["report"]).resolve()): record for record in records}
    for record in distinct.values():
        if not runner.matrix_replay(record, log=False):
            raise ValueError("ordinary replay failed: " + record["report"])
    return {"all_replayed": True, "distinct_reports_replayed": len(distinct)}


def command(argv: list[str], log: Path) -> int:
    result = subprocess.run(
        argv, capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=600
    )
    write(log, {"argv": argv, "exit": result.returncode, "stderr": result.stderr, "stdout_tail": result.stdout[-1000:]})
    if result.returncode not in (0, 1):
        raise ValueError("ordinary consumer failed; see " + str(log))
    return result.returncode


def integrate(output: Path, approved: Path, leakage_path: Path) -> dict[str, Any]:
    import rubric_mutation_runner as runner

    output.mkdir(parents=True, mode=0o700)
    old = load(approved / "assessment/assessment.json")
    binding_before = subject.implementation()
    # New output path; historical predecision report and exact approved packet remain untouched.
    new_subject = output / "accepted-subject"
    subject_command = [
        sys.executable,
        "-B",
        str(Path(subject.__file__)),
        "--output",
        str(new_subject),
        "--defer-replay",
    ]
    command(subject_command, output / "subject-command.json")
    subject_command = [
        sys.executable,
        "-B",
        str(Path(__file__)),
        "--subject-consumer",
        "--root",
        str(new_subject),
        "--selection",
        str(new_subject / "selection.json"),
        "--output",
        str(new_subject / "current-assessment"),
    ]
    command(subject_command, output / "subject-current-command.json")
    saved = load(new_subject / "current-assessment/assessment.json")
    observation = saved["report"]["measurements"]["create02_subject"]
    if (
        observation["binding"] != old["report"]["measurements"]["create02_subject"]["binding"]
        or observation["pending_requests"]
        or saved["report"]["items"]["CREATE-02"]["score"] != 0
    ):
        raise ValueError("exact qualified zero not emitted")
    repair_root = output / "repaired-subject"
    build_repair(repair_root)
    repair_command = [
        sys.executable,
        "-B",
        str(Path(__file__)),
        "--repair-consumer",
        "--root",
        str(repair_root),
        "--selection",
        str(repair_root / "selection.json"),
        "--output",
        str(repair_root / "assessment"),
    ]
    repair_exit = command(repair_command, output / "repair-command.json")
    repair_saved = load(repair_root / "assessment/assessment.json")
    repaired = repair_saved["report"]["measurements"]["create02_subject"]
    leakage = load(leakage_path)
    options = {
        "source-review-operator-store": os.environ["EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE"],
        "source-review-operator-trust": os.environ["EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST"],
    }

    def record(root: Path, report: Path, cmd: list[str], exit_code: int = 1) -> dict[str, Any]:
        return {
            "root": str(root),
            "report": str(report),
            "command": cmd,
            "exit": exit_code,
            "state": "assessed",
            "options": options,
            "kind": "create02-subject",
        }

    changed = record(new_subject, new_subject / "current-assessment/assessment.json", subject_command)
    repair = record(repair_root, repair_root / "assessment/assessment.json", repair_command, repair_exit)
    baseline = record(approved, approved / "assessment/assessment.json", [], 1)
    runs = deepcopy(leakage["runs"])
    runs.append(
        {
            "case": "create02-heldout-stale-template-zero",
            "expected": {"CREATE-02": 0},
            "assessment": changed,
            "criterion_baseline": repair,
            "criterion_repair": repair,
            "historical_predecision": baseline,
            "extension_kind": "create02-subject",
            "evidence_control_kind": "numeric_or_repair",
            "reason": "One exact owner/agent-qualified wrong-current-order cue bypass, separate from original action-only subject.",
            "repair_description": "Fresh repaired-scorer and defective counterpart policy executions on retained-affected and new same-family orders; no inherited qualification.",
            "task_bytes_changed": True,
            "shared_finding_id": FINDING,
        }
    )
    matrix = runner.matrix_summary(leakage["source_repairs"]["baseline"], runs, leakage["source_repairs"]["repair"])
    matrix.update(
        options=options,
        original_leakage_results=str(leakage_path),
        verification={"state": "deferred", "all_replayed": False},
        scope="Current canonical matrix; local leakage observations only. Other criteria await current native/contract integration; no legacy scores imported.",
        fresh_repair=repair,
        shared_defects=[
            {
                "id": FINDING,
                "primary": "CREATE-02",
                "independent_defect_count": 1,
                "secondary": "AUDIT-03 relevance only; no additional numeric score or approval.",
            }
        ],
    )
    old_row = next(row for row in matrix["rows"] if row["case"] == "leakage-CREATE-02.0")
    if (old_row["expected"], old_row["actual"]) != (0, 2):
        raise ValueError("historical target mismatch lost")
    write(output / "matrix.json", matrix)
    arms = [arm for pair in repaired["pairs"] for arm in (pair["baseline"], pair["intervention"])]
    bad_arms = [arm for pair in repaired["defective_counterparts"] for arm in (pair["baseline"], pair["intervention"])]
    leakage_ids = sorted(
        {
            row["criterion"]
            for row in matrix["rows"]
            if row["case"].startswith("leakage-") and row["case"].endswith(".base")
        }
    )
    summary = {
        "ordinary_zero": saved["report"]["items"]["CREATE-02"]["score"],
        "zero_report": changed["report"],
        "repair_report": repair["report"],
        "repair_criterion_score": repair_saved["report"]["items"]["CREATE-02"]["score"],
        "fresh_repair_pairs": len(repaired["pairs"]),
        "fresh_repair_arms": len(arms),
        "fresh_defective_counterpart_arms": len(bad_arms),
        "repaired_wrong_full_credit": sum(
            a["published"]["credit"] == 1 and a["evidence_oracle"]["credit"] == 0 for a in arms
        ),
        "repaired_correct_full_credit": sum(
            a["published"]["credit"] == a["evidence_oracle"]["credit"] == 1 for a in arms
        ),
        "defective_wrong_full_credit": sum(
            a["published"]["credit"] == 1 and a["evidence_oracle"]["credit"] == 0 for a in bad_arms
        ),
        "current_inventory": matrix["current_inventory"],
        "matrix_counts": matrix["counts"],
        "leakage_lower_cells_covered": sum(
            c["criterion"] in leakage_ids and c["expected"] < 3 and c["status"] != "unsupported"
            for c in matrix["cells"]
        ),
        "original_zero_target_actual": 2,
        "replay": "deferred",
        "tests": "not run",
        "native": False,
        "models": False,
        "presentation_correction": NOTE,
        "limitations": LIMITS,
    }
    write(output / "integration-results.json", summary)
    lines = [
        "# CREATE-02 qualified zero and fresh local repair",
        "",
        NOTE,
        "",
        "| Subject | CREATE-02 | Result |",
        "|---|---:|---|",
        "| [New accepted subject](accepted-subject/current-assessment/results.md) | 0 | Exact scoped consent and actual E1 contextual agent review retained. |",
        "| Original action-only CREATE-02.0 | 2 (target 0) | Frozen disagreement retained; claim unchanged. |",
        "| [Fresh repaired subject](repaired-subject/assessment/results.md) | Unassessed | Fresh local repair completed, but no changed-source owner/agent qualification transferred. |",
        "",
        f"Fresh repair: {len(repaired['pairs'])} pairs / {len(arms)} arms plus {len(bad_arms)} separately executed defective counterpart arms. Two retained-affected and two new same-family order instances. All repaired published checks equal independent current-order checks; wrong answers are not assigned full credit.",
        "",
        "[Current numeric matrix](matrix.json): 23 criteria / 76 checks / 92 numeric cells / 23 missing-evidence cells. Local seven-criterion lower-anchor coverage only; other current native/contract cells remain pending, not borrowed from legacy 18-row evidence.",
        "",
        "## Local lower-anchor matrix",
        "",
        "| Criterion | 0 | 1 | 2 | 3 |",
        "|---|---|---|---|---|",
    ]
    leakage_ids = sorted(
        {r["criterion"] for r in matrix["rows"] if r["case"].startswith("leakage-") and r["case"].endswith(".base")}
    )
    for ident in leakage_ids:
        values = []
        for cell in (c for c in matrix["cells"] if c["criterion"] == ident):
            match = next((a for a in cell["attempts"] if a["matched"]), None)
            values.append(
                f"[{cell['expected']}]({os.path.relpath(Path(match['report']).with_name('results.md'), output)})"
                if match
                else "Pending"
            )
        lines.append("| " + ident + " | " + " | ".join(values) + " |")
    lines += [
        "",
        "One shared defect only: " + FINDING + ". AUDIT-03 relevance is not an extra score.",
        "",
        "## Remaining",
        *("- " + item for item in LIMITS),
        "- Eight native families require final current-source collection; retained replay and tests are deferred until the final stage.",
    ]
    (output / "results.md").write_text("\n".join(lines) + "\n")
    if subject.implementation() != binding_before:
        raise ValueError("approved implementation changed")
    write(output / "source-freeze.json", identity())
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in {"--repair-consumer", "--subject-consumer"}:
        return repair_cli(argv[1:], repaired=argv[0] == "--repair-consumer")
    if len(argv) == 2 and argv[0] == "--verify-integration":
        print(json.dumps(verify_integration(Path(argv[1]).absolute())))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approved-subject", required=True, type=Path)
    parser.add_argument("--leakage-results", required=True, type=Path)
    parser.add_argument("--source-review-operator-store", required=True, type=Path)
    parser.add_argument("--source-review-operator-trust", required=True, choices=["localhost-assessment-operator-v1"])
    parser.add_argument("--defer-replay", action="store_true", required=True)
    args = parser.parse_args(argv)
    os.environ["EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE"] = str(args.source_review_operator_store.absolute())
    os.environ["EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST"] = args.source_review_operator_trust
    print(
        json.dumps(integrate(args.output.absolute(), args.approved_subject.absolute(), args.leakage_results.absolute()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
