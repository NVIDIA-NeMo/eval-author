# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate and reconcile current ordinary base/mutation reports without launches."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
runner = importlib.import_module("rubric_mutation_runner")
extensions = importlib.import_module("rubric_numeric_matrix_extensions")
create02 = importlib.import_module("rubric_create02_integration")


def load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def read_rows(record: dict[str, Any]) -> dict[str, Any]:
    return runner.observed_rows(record)


def decision_crosswalk(source_report: Path, intake: Path, deltas: Path) -> dict[str, Any]:
    report = load(source_report)["report"]
    measurements = report["measurements"]
    owners = {
        "environment": measurements["general_review"],
        "contract": measurements["scoped_reviews"]["contract"],
        "fairness": measurements["scoped_reviews"]["fairness"],
        "upper-method": measurements["upper_method_review"],
    }
    previous = {row["kind"]: row for row in load(deltas)["requests"]}
    questions = {
        "environment": "Retain the existing action/permission and local-state decisions for this exact current request? This covers simulated ledger actions and harness reset only, not real payments, runtime completeness or independent qualification.",
        "contract": "Approve and retain this exact current public/enforcement crosswalk? The optional completion point remains optional; positive refunds, inclusive approval timing and final ledger reconciliation retain their published meanings. This does not approve blind discovery or level 3.",
        "fairness": "Approve and retain only the current scripted reference-versus-split-refund comparison as an intended policy variant, not an unjustified accommodation? Both recorded arms and their setup differences are in the exact request. No model-performance or broader sensitivity claim is made.",
        "upper-method": "Qualify and retain the disclosed nonblind consumer/session and public-first interpretation method for this exact refreshed packet, or defer it? This is not a blind or independent-model method. Its legacy AC-03/AC-04 scopes do not qualify the broader current AUDIT-03/ENV-03-S01 predicates automatically.",
    }
    rows = []
    for kind, owner in owners.items():
        request_path = intake / (kind + "-request.json")
        request = load(request_path)
        if request != owner["request"]:
            raise ValueError("intake request differs from ordinary report: " + kind)
        old = previous[kind]
        event = load(Path(old["old_record"]))["event"]
        rows.append(
            {
                "kind": kind,
                "binding": request["binding"],
                "request": str(request_path),
                "request_sha256": digest(request_path),
                "question": questions[kind],
                "current_receipt": owner.get("receipt"),
                "decision": None,
                "status": "retained" if owner.get("receipt") else "pending_exact_owner_decision",
                "automatic_evidence": owner.get("automatic"),
                "boundary": request["boundary"],
                "prior_decisions": event["decisions"],
                "prior_retention_consent": event["retention_consent"],
                "prior_record": old["old_record"],
                "changed_paths": old["changed_paths"],
                "approval_transferred": False,
            }
        )
    return {
        "schema": "current-rubric-decision-crosswalk-v1",
        "source_report": str(source_report),
        "source_report_sha256": digest(source_report),
        "decisions_recorded": False,
        "scope": "Example/simulation only. Execution permission and accepted four-row source review do not answer these requests.",
        "rows": rows,
    }


def current_create_run(matrix_path: Path, output: Path, options: dict[str, Any]) -> dict[str, Any]:
    old = load(matrix_path)
    freeze = load(matrix_path.with_name("source-freeze.json"))
    if freeze != create02.identity():
        raise ValueError("CREATE-02 integration closure changed; preserve old evidence and stop")
    original = next(run for run in old["runs"] if run.get("extension_kind") == "create02-subject")
    run = deepcopy(original)
    record = run["assessment"]
    saved = load(Path(record["report"]))
    selection = output / "create02-selection.json"
    save(selection, saved["selection"])
    command = [
        sys.executable,
        "-B",
        str(SCRIPTS / "rubric_create02_integration.py"),
        "--subject-consumer",
        "--root",
        saved["root"],
        "--selection",
        str(selection),
        "--output",
        str(output / "create02"),
    ]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key, value in options.items():
        if key.startswith("source-review-"):
            env["EVAL_AUTHOR_" + key.replace("-", "_").upper()] = str(value)
    result = subprocess.run(command, capture_output=True, env=env, timeout=180)
    (output / "create02.stdout").write_bytes(result.stdout)
    (output / "create02.stderr").write_bytes(result.stderr)
    save(output / "create02-command.json", {"command": command, "exit": result.returncode})
    if result.returncode not in (0, 1):
        raise ValueError("ordinary CREATE-02 consumer failed")
    record.update(command=command, report=str(output / "create02/assessment.json"), exit=result.returncode)
    if read_rows(record)["CREATE-02"]["score"] != 0:
        raise ValueError("accepted CREATE-02 zero is no longer admitted")
    run["retained_repair_identity_checked"] = True
    run["retained_repair_replay"] = "deferred"
    return run


def coverage(matrix: dict[str, Any]) -> dict[str, Any]:
    definitions = runner.canonical_definitions()
    records: dict[str, tuple[str, dict[str, Any], dict[str, Any] | None]] = {
        matrix["base"]["report"]: ("full-base", matrix["base"], None)
    }
    for run in matrix["runs"]:
        records[run["assessment"]["report"]] = (run["case"], run["assessment"], run)
    for record in matrix.get("supporting_reports", []):
        records[record["report"]] = (record["case"], record, None)
    values_by_report = {path: read_rows(record) for path, (_, record, _) in records.items()}
    paired_rows = {run["case"]: read_rows(run.get("criterion_baseline", matrix["base"])) for run in matrix["runs"]}
    cells: list[dict[str, Any]] = []
    report_checks = []
    for path, (case, record, run) in records.items():
        values = values_by_report[path]
        if not values:
            report_checks.append({"case": case, "report": path, "state": "execution_blocked"})
            continue
        report_checks.append(
            {
                "case": case,
                "report": path,
                "check_count": sum(len(row["checks"]) for row in values.values()),
                "outcomes": dict(Counter(outcome for row in values.values() for outcome in row["checks"].values())),
            }
        )
    for ident, definition in definitions.items():
        for score in range(4):
            observations = []
            for path, (case, record, run) in records.items():
                if run and run.get("evidence_control_kind") == "operational_failure":
                    continue
                values = values_by_report[path]
                if not values or values[ident]["score"] != score:
                    continue
                base = paired_rows[case][ident]["score"] if run else score if case == "full-base" else None
                targeted = run is not None and ident in run.get("current_expected", {})
                observations.append(
                    {
                        "case": case,
                        "report": path,
                        "score": score,
                        "paired_base": base,
                        "targeted": targeted,
                        "qualified_base_drop": type(base) is int and base == 3 and score < base,
                        "lower_base_drop": type(base) is int and score < base,
                        "repair_report": run.get("criterion_repair", matrix["repair"])["report"]
                        if run
                        else matrix["repair"]["report"]
                        if case == "full-base"
                        else None,
                    }
                )
            observations.sort(key=lambda item: (not item["qualified_base_drop"], not item["targeted"], item["case"]))
            state = (
                "qualified_base_drop"
                if any(item["qualified_base_drop"] for item in observations)
                else "observed_unqualified_control"
                if any(item["case"] != "full-base" for item in observations)
                else "full_base_observation"
                if observations
                else "pending"
            )
            cells.append(
                {
                    "criterion": ident,
                    "score": score,
                    "state": state,
                    "observations": observations,
                    "anchor": definition["scoring"][str(score)],
                    "remaining": None
                    if observations
                    else (
                        "Current automatic evidence is ready. Obtain the exact Environment, contract or narrow fairness decision in decision-crosswalk.json, then reassess only affected reports."
                        if score == 2
                        and ident
                        in {
                            "provenance.comparison-fairness",
                            "provenance.solver-tool-access",
                            "provenance.external-state",
                            "task-contract.clear-contract",
                            "task-contract.discoverable-requirements",
                            "task-contract.scoring-authority",
                        }
                        else (
                            "Run deterministic change-injection invalidation controls, preserve original lineage and validate the restored counterpart; no independent review or new owner decision is required."
                            if ident == "provenance.evidence-revision-consistency" and score == 3
                            else runner.matrix_remaining(ident, score)
                        )
                    ),
                }
            )
    base_rows = read_rows(matrix["base"])
    base_items = load(Path(matrix["base"]["report"]))["report"]["items"]
    return {
        "schema": "current-rubric-measured-coverage-v1",
        "inventory": matrix["current_inventory"],
        "base_report": matrix["base"]["report"],
        "base_qualified": matrix["base_qualified"],
        "base_scores": matrix["base_scores"],
        "base_gaps": {
            key: row["remaining_gaps"]
            or [
                check["remaining_gap"]
                for check in base_items[key]["check_results"]
                if check["outcome"] not in {"PASS", "NOT_APPLICABLE"} and check.get("remaining_gap")
            ]
            for key, row in base_rows.items()
        },
        "base_score_counts": dict(
            Counter("Unassessed" if value is None else str(value) for value in matrix["base_scores"].values())
        ),
        "cells": cells,
        "counts": dict(Counter(cell["state"] for cell in cells)),
        "numeric_observed": sum(bool(cell["observations"]) for cell in cells),
        "lower_numeric_observed": sum(bool(cell["observations"]) and cell["score"] < 3 for cell in cells),
        "missing_evidence_cells": matrix["missing_evidence_cells"],
        "missing_evidence_drops": sum(cell["demonstrated_drop"] for cell in matrix["missing_evidence_cells"]),
        "operational_failure_controls": matrix["operational_failure_controls"],
        "report_checks": report_checks,
        "physical_cases": len(matrix["runs"]),
        "separate_supporting_reports": len(matrix.get("supporting_reports", [])),
        "verification": {"ordinary_generation": "completed", "retained_replay": "deferred", "full_tests": "not_run"},
        "limits": [
            "Observed cells are derived from emitted scores, including collateral outcomes; targets never supply scores.",
            "Criterion-local bases are not a single fully qualified base. No aggregate merit score is computed.",
            "A score of zero is not an execution error. Explicit operational failures are excluded from numeric and missing-evidence coverage.",
            "Task credit and per-check rewards remain in each linked ordinary measurement, on their own scales.",
            "Retained repairs are contextual evidence, not fresh native repaired-task qualification.",
            "Task validity, native executability and model performance are separate. The base uses admitted Harbor evidence. Scripted valid controls show feasibility, not guaranteed model success. Difficult cases and legitimate solver failures remain valid.",
            "The separate direct-Docker upper packet does not establish full Harbor acceptance. No task simplification, weaker grading or answer exposure is used to raise scores.",
        ],
    }


def render(value: dict[str, Any], crosswalk: dict[str, Any], output: Path) -> str:
    def link(path: str) -> str:
        return os.path.relpath(Path(path).with_name("results.md"), output)

    def score(number: int | None) -> str:
        return "Unassessed" if number is None else str(number)

    lines = [
        "# Current base and mutation coverage",
        "",
        f"**{value['numeric_observed']} observed numeric cells; {value['lower_numeric_observed']} lower-anchor cells.** These are observations, not qualification of one perfect base.",
        "",
        "- Base: `" + json.dumps(value["base_score_counts"], sort_keys=True) + "`. No aggregate score.",
        f"- Deliberate missing-evidence drops: {value['missing_evidence_drops']}; operational-failure controls: {len(value['operational_failure_controls'])}.",
        "- Ordinary generation completed. Retained replay and full tests are deferred. No native or model launches.",
        "- [Machine coverage](coverage.json), [full matrix](matrix.json), [decision crosswalk](decision-crosswalk.json).",
        "",
        "| Criterion | Full base | 0 | 1 | 2 | 3 | Missing-evidence drop |",
        "|---|---:|---|---|---|---|---|",
    ]
    for ident, number in value["base_scores"].items():
        columns = []
        for cell in (cell for cell in value["cells"] if cell["criterion"] == ident):
            witness = next(iter(cell["observations"]), None)
            suffix = " qualified-base drop" if cell["state"] == "qualified_base_drop" else ""
            columns.append(f"[{cell['score']}]({link(witness['report'])}){suffix}" if witness else "Pending")
        missing = next(cell for cell in value["missing_evidence_cells"] if cell["criterion"] == ident)
        lines.append(
            "| "
            + ident
            + " | ["
            + score(number)
            + "]("
            + link(value["base_report"])
            + ") | "
            + " | ".join(columns)
            + " | "
            + ("Observed" if missing["demonstrated_drop"] else "Not demonstrated")
            + " |"
        )
    lines += ["", "## Exact owner decisions", "", crosswalk["scope"], ""]
    for row in crosswalk["rows"]:
        lines += [
            "### " + row["kind"] + " — " + row["status"],
            "",
            (
                "Retained answer: "
                + "; ".join(dict.fromkeys(decision["content"] for decision in row["decision"].values()))
            )
            if row["status"] == "retained"
            else row["question"],
            "",
            "Binding: `" + row["binding"] + "`. [Exact request](" + os.path.relpath(row["request"], output) + ").",
            "",
        ]
    lines += ["## Full-base qualification gaps", ""]
    for ident, gaps in value["base_gaps"].items():
        if value["base_scores"][ident] != 3:
            detail = " ".join(gaps) or runner.matrix_remaining(ident, 3)
            lines.append("- **" + ident + "**: " + detail)
    lines += ["", "## Unobserved numeric cells", ""]
    lines += [
        "- **" + cell["criterion"] + " / " + str(cell["score"]) + "**: " + cell["remaining"]
        for cell in value["cells"]
        if not cell["observations"]
    ]
    if value.get("upper_supporting_evidence"):
        upper = value["upper_supporting_evidence"]
        lines += [
            "",
            "## Separate new-family supporting evidence",
            "",
            "[Ordinary upper report]("
            + link(upper["report"])
            + "). This separate subject supplies no automatic full-base credit.",
            "",
        ]
        for request in upper["requests"]["requests"]:
            lines.append("- **" + request["criterion"] + "**: " + request["remaining_gate"])
    for support in value.get("validated_supporting_evidence", []):
        lines += [
            "",
            "## " + support["title"],
            "",
            support["summary"],
            "",
            "[Owning report](" + link(support["report"]) + "). No original-base score transfer.",
        ]
    lines += [
        "",
        "## Retained disagreements",
        "",
        "The original action-only CREATE-02 zero target remains actual 2. The separate accepted stale-template subject supplies observed 0; its repaired subject remains Unassessed. Hidden-completion does not establish a hidden condition when verifier source is allowed authority.",
        "",
        "## Evidence boundaries",
        "",
    ]
    lines += ["- " + limit for limit in value["limits"]]
    return "\n".join(lines) + "\n"


def refresh_decisions(matrix_path: Path, readbacks_path: Path, output: Path) -> int:
    """Refresh exact affected report bindings; never collect or record decisions."""
    original = load(matrix_path)
    if original["definitions_sha256"] != runner.definitions_digest():
        raise ValueError("matrix definitions changed")
    readbacks = load(readbacks_path)
    bindings = {value["readback"]["event"]["request"]["binding"] for value in readbacks.values()}
    for value in readbacks.values():
        path = Path(value["path"])
        receipt = value["readback"]
        if digest(path) != receipt["digest"] or path.with_suffix(".revoked").exists():
            raise ValueError("decision changed or revoked")
        document = load(path)
        if document["event"] != receipt["event"] or document["trust_basis"] != receipt["trust_basis"]:
            raise ValueError("readback does not match the exact stored event")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    records = {
        original["base"]["report"]: ("base", original["base"]),
        original["repair"]["report"]: ("repair", original["repair"]),
    }
    for run in original["runs"]:
        for field in ("assessment", "criterion_baseline", "criterion_repair"):
            if field in run:
                record = run[field]
                records.setdefault(record["report"], (run["case"] + "-" + field, record))
    replacements: dict[str, Any] = {}
    manifest = []
    for path, (name, record) in records.items():
        saved = load(Path(path))
        measurements = saved["report"]["measurements"]
        # Requests can be nested in independently owned scoped projections.
        affected = sorted(binding for binding in bindings if binding in json.dumps(measurements))
        if not affected:
            manifest.append({"report": path, "sha256": digest(Path(path)), "state": "retained_unchanged_binding"})
            continue
        fresh = runner.matrix_assess(
            Path(saved["root"]), saved["selection"], Path(saved["root"]) / output.name / name, record["options"]
        )
        if fresh["exit"] not in (0, 1) or not read_rows(fresh):
            save(output / "refresh-manifest.json", manifest)
            raise ValueError("affected ordinary assessment failed: " + name)
        replacements[path] = fresh
        manifest.append(
            {
                "report": path,
                "sha256": digest(Path(path)),
                "state": "regenerated",
                "bindings": affected,
                "current": fresh,
            }
        )
        save(output / "refresh-manifest.json", manifest)
        print("refreshed " + name, flush=True)
    runs = deepcopy(original["runs"])
    for run in runs:
        for field in ("assessment", "criterion_baseline", "criterion_repair"):
            if field in run:
                run[field] = replacements.get(run[field]["report"], run[field])
        # Cached full rows are not admission and must follow their owning report.
        run["full_emitted_items"] = load(Path(run["assessment"]["report"]))["report"]["items"]
        for key, path in run.get("repair_relation", {}).items():
            if path in replacements:
                run["repair_relation"][key] = replacements[path]["report"]
    base = replacements.get(original["base"]["report"], original["base"])
    repair = replacements.get(original["repair"]["report"], original["repair"])
    matrix = runner.matrix_summary(base, runs, repair)
    matrix.update(
        options=original["options"],
        owning_matrix=str(matrix_path),
        owning_matrix_sha256=digest(matrix_path),
        verification={"state": "deferred", "all_replayed": False},
    )
    crosswalk = load(matrix_path.with_name("decision-crosswalk.json"))
    for row in crosswalk["rows"]:
        value = readbacks[row["kind"]]
        if row["binding"] != value["readback"]["event"]["request"]["binding"]:
            raise ValueError("crosswalk binding differs")
        row.update(
            current_receipt=value["readback"],
            decision=value["readback"]["event"]["decisions"],
            status="retained",
            readback_path=str(readbacks_path),
        )
    crosswalk.update(
        decisions_recorded=True, source_report=base["report"], source_report_sha256=digest(Path(base["report"]))
    )
    value = coverage(matrix)
    value["verification"].update(
        affected_reports_regenerated=len(replacements), unchanged_reports_retained=len(records) - len(replacements)
    )
    save(output / "matrix.json", matrix)
    save(output / "coverage.json", value)
    save(output / "decision-crosswalk.json", crosswalk)
    (output / "summary.md").write_text(render(value, crosswalk, output))
    for entry in readbacks.values():
        if (
            digest(Path(entry["path"])) != entry["readback"]["digest"]
            or Path(entry["path"]).with_suffix(".revoked").exists()
        ):
            raise ValueError("decision changed during refresh")
    save(
        output / "generation.json",
        {
            "argv": sys.argv,
            "source_sha256": digest(Path(__file__)),
            "native_launches": 0,
            "model_launches": 0,
            "retained_replay": "deferred",
        },
    )
    print(
        json.dumps(
            {
                "report": str(output / "summary.md"),
                "base": value["base_score_counts"],
                "lower_numeric_observed": value["lower_numeric_observed"],
                "numeric_observed": value["numeric_observed"],
                "missing_drops": value["missing_evidence_drops"],
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--original-finish",
        action="store_true",
        help="Admit exact original scope/history and separately verified Audit; no collection or owner intake",
    )
    parser.add_argument(
        "--suite-native-root",
        type=Path,
        help="Separately admit the closed full-Trial viability companion; no launches or score transfer",
    )
    parser.add_argument(
        "--contract-review-root",
        type=Path,
        help="Attach the exact nonblind original-B corroboration; method qualification requires scoped owner readback",
    )
    parser.add_argument(
        "--provenance-release-review-root",
        type=Path,
        help="Targeted original exposure/restricted-release corroboration only; no full assessment",
    )
    parser.add_argument("--matrix", type=Path)
    parser.add_argument(
        "--reuse-current",
        action="store_true",
        help="Validate retained evidence and rebuild coverage without the full report batch",
    )
    parser.add_argument("--source-freeze", type=Path)
    parser.add_argument("--source-review-operator-store", type=Path)
    parser.add_argument("--source-review-operator-trust", choices=["localhost-assessment-operator-v1"])
    parser.add_argument("--registry", type=Path, action="append")
    parser.add_argument("--reviewed-companions", choices=["finite-upper-v1"])
    parser.add_argument("--qualification-root", type=Path)
    parser.add_argument("--upper-root", type=Path)
    parser.add_argument("--native-followup-root", type=Path)
    parser.add_argument("--create02-matrix", type=Path)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--intake", type=Path)
    parser.add_argument("--request-deltas", type=Path)
    parser.add_argument(
        "--refresh-decisions", type=Path, help="Genuine owning readbacks; refresh affected reports only"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--upper-evidence-root", type=Path, help="Optional retained supporting packet, never inherited as base credit"
    )
    # This CLI never replays; replay is always a separate final gate. Accepted for older commands.
    parser.add_argument("--defer-replay", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    if args.provenance_release_review_root:
        if args.original_finish or args.reuse_current or args.refresh_decisions or args.matrix:
            parser.error("targeted provenance/release review is separate from full/reuse/refresh workflows")
        from current_rubric_finish import run_provenance_review

        return run_provenance_review(args)
    if args.matrix is None:
        parser.error("--matrix is required outside targeted provenance/release review")
    if args.contract_review_root and not (args.original_finish and args.suite_native_root):
        parser.error("--contract-review-root requires --original-finish and --suite-native-root")
    if args.suite_native_root and not args.original_finish:
        parser.error("--suite-native-root requires --original-finish")
    if args.original_finish:
        if args.reuse_current or args.refresh_decisions or args.reviewed_companions or not args.source_freeze:
            parser.error("original finish is separate from reuse/refresh and requires --source-freeze")
        from current_rubric_finish import run

        return run(args)
    if args.reuse_current:
        from current_rubric_support import reuse_current

        return reuse_current(args)
    if args.reviewed_companions:
        parser.error("reviewed companions require --reuse-current")
    if args.refresh_decisions:
        return refresh_decisions(args.matrix, args.refresh_decisions, args.output)
    if not all((args.create02_matrix, args.source_report, args.intake, args.request_deltas)):
        parser.error("initial generation requires CREATE-02 matrix, source report, intake and request deltas")
    args.output.mkdir(parents=True, exist_ok=False)
    original = load(args.matrix)
    if original["definitions_sha256"] != runner.definitions_digest():
        raise ValueError("historical matrix is not current")
    crosswalk = decision_crosswalk(args.source_report, args.intake, args.request_deltas)
    save(args.output / "decision-crosswalk.json", crosswalk)
    # Existing native cases are admitted again; no collector or retained replay is invoked.
    if (
        extensions.native_admission(Path(original["native_bundle"]), Path(original["native_registry"]))
        != original["native_admission"]
    ):
        raise ValueError("native admission differs")
    addition = current_create_run(args.create02_matrix, args.output, original["options"])
    matrix = runner.matrix_summary(original["base"], original["runs"] + [addition], original["repair"])
    matrix.update(
        options=original["options"],
        owning_matrix=str(args.matrix),
        owning_matrix_sha256=digest(args.matrix),
        create02_matrix=str(args.create02_matrix),
        verification={"state": "deferred", "all_replayed": False},
    )
    save(args.output / "matrix.json", matrix)
    save(args.output / "decision-crosswalk.json", crosswalk)
    value = coverage(matrix)
    if args.upper_evidence_root:
        upper = args.upper_evidence_root
        requests = load(upper / "qualified-review-requests.json")
        for request in requests["requests"]:
            for evidence in request["evidence"]:
                if digest(Path(evidence["path"])) != evidence["sha256"]:
                    raise ValueError("upper supporting evidence changed")
        upper_report = load(upper / "ordinary-report-current/assessment.json")
        value["upper_supporting_evidence"] = {
            "report": str(upper / "ordinary-report-current/assessment.json"),
            "report_sha256": digest(upper / "ordinary-report-current/assessment.json"),
            "items": upper_report["report"]["items"],
            "readback": load(upper / "readback.json"),
            "requests": requests,
            "status": "retained supporting packet; exact request artifact hashes checked; no base score transfer",
        }
    save(args.output / "coverage.json", value)
    (args.output / "summary.md").write_text(render(value, crosswalk, args.output))
    save(
        args.output / "generation.json",
        {"argv": sys.argv, "source_sha256": digest(Path(__file__)), "native_launches": 0, "model_launches": 0},
    )
    print(
        json.dumps(
            {
                "report": str(args.output / "summary.md"),
                "counts": value["counts"],
                "base": value["base_score_counts"],
                "lower_numeric_observed": value["lower_numeric_observed"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
