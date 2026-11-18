# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
"""Owning native orchestration for the complete numeric matrix.

No native collection or model invocation. Native inputs must already have owning
registry admission.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import rubric_mutation_runner as runner
import rubric_table


def extension_specs(kind):
    if kind == "native":
        return {
            "missing-endpoint": {
                "provenance.observed-configuration": 0,
                "provenance.solver-tool-access": 0,
                "provenance.dependency-substitution": 0,
            },
            "exposed-reference": {"AC-01": 0},
            "persistent-state": {"AC-04": 0, "provenance.external-state": 0},
            "partial-gpu-route": {"provenance.runtime-route": 1},
        }
    raise ValueError("unknown extension kind")


def native_admission(bundle, registry):
    import native_numeric_mutations as native

    document = native.admission(bundle, registry)
    projection = native.validate(bundle, document)
    if set(native.CASES) != set(extension_specs("native")):
        raise ValueError("native case inventory changed")
    for case, expected in extension_specs("native").items():
        if {k: v[0] for k, v in native.findings(projection, case).items()} != expected:
            raise ValueError("native findings disagree with external catalog")
    return {
        "index_sha256": hashlib.sha256((bundle / "native-index.json").read_bytes()).hexdigest(),
        "owning_validator": str(Path(native.__file__).resolve()),
        "validated": True,
    }


def physical_identity(record):
    saved = json.loads(rubric_table.read_private(Path(record["report"])))
    # Paths and selection matter: equal scores alone do not identify a case.
    value = {
        "root": str(Path(saved["root"]).resolve()),
        "selection": saved["selection"],
        "options": record["options"],
        "kind": record.get("kind", "base"),
        "native_bundle": record.get("native_bundle"),
        "native_case": record.get("native_case"),
    }
    return hashlib.sha256(rubric_table.encoded(value).encode()).hexdigest()


def native_record(bundle, registry, case, report, command):
    saved = json.loads(rubric_table.read_private(report))
    return {
        "root": saved["root"],
        "report": str(report),
        "command": command,
        "exit": 1,
        "state": "assessed",
        "options": {},
        "kind": "native",
        "native_bundle": str(bundle),
        "native_registry": str(registry),
        "native_case": case,
    }


def replay(record, logs=None):
    if record.get("kind") != "native":
        return runner.matrix_replay(record, log=False)
    command = [
        sys.executable,
        "-B",
        str(runner.SCRIPTS / "native_numeric_mutations.py"),
        "--bundle",
        record["native_bundle"],
        "--registry",
        record["native_registry"],
        "--case",
        record["native_case"],
        "--verify-existing",
        str(Path(record["report"]).parent),
    ]
    result = subprocess.run(
        command, capture_output=True, timeout=600, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    )
    if logs:
        runner.write_json(
            logs / (record["native_case"] + ".replay.json"), {"command": command, "exit": result.returncode}
        )
        (logs / (record["native_case"] + ".stderr")).write_bytes(result.stderr)
    return (
        result.returncode == record["exit"]
        and result.stdout == Path(record["report"]).with_name("results.md").read_bytes()
    )


def run_extensions(output, base, options, bundle, registry, native_reports=None):
    proof = native_admission(bundle, registry)  # Admit before importing any emitted observations.
    native_runs = []
    for case, expected in extension_specs("native").items():
        dest = (native_reports / case) if native_reports else (output / "native" / case)
        command = [
            sys.executable,
            "-B",
            str(runner.SCRIPTS / "native_numeric_mutations.py"),
            "--bundle",
            str(bundle),
            "--registry",
            str(registry),
            "--case",
            case,
            "--report",
            str(dest),
        ]
        if not native_reports:
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            result = subprocess.run(
                command, capture_output=True, timeout=600, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            )
            (dest.parent / (case + ".stdout")).write_bytes(result.stdout)
            (dest.parent / (case + ".stderr")).write_bytes(result.stderr)
            if result.returncode not in (0, 1):
                raise ValueError("owning native report generation failed: " + case)
        record = native_record(bundle, registry, case, dest / "assessment.json", command)
        native_runs.append(
            {
                "case": case,
                "expected": expected,
                "assessment": record,
                "extension_kind": "native",
                "reason": "Current owning native mutation validator and ordinary table projection.",
                "repair_description": "Restore unchanged base task/selection. Shared full-base repair reuses unchanged native receipts; mutant evidence does not transfer.",
                "native_admission": proof,
                "task_bytes_changed": True,
            }
        )
    return native_runs, proof


def enrich(result):
    """Keep all emitted metadata and explicit repair relations, not score-only joins."""
    base_items = json.loads(Path(result["base"]["report"]).read_bytes())["report"]["items"]
    repairs = {"full_base": result["repair"]["report"]}
    if result.get("leakage_controls"):
        repairs["leakage_source_only"] = result["leakage_controls"]["repair"]["report"]
    identities = {}
    for run in result["runs"]:
        record = run["assessment"]
        ident = physical_identity(record)
        if ident in identities:
            raise ValueError("duplicate physical case identity: " + run["case"])
        identities[ident] = run["case"]
        saved = json.loads(rubric_table.read_private(Path(record["report"])))
        run["physical_identity"] = ident
        run["full_emitted_items"] = saved["report"]["items"]
        paired_items = (
            json.loads(Path(run["criterion_baseline"]["report"]).read_bytes())["report"]["items"]
            if run.get("criterion_baseline")
            else base_items
        )
        run["full_metadata_changed"] = [
            key for key, value in saved["report"]["items"].items() if value != paired_items[key]
        ]
        run["repair_relation"] = {"full_base": repairs["full_base"]}
        if run.get("criterion_repair"):
            run["repair_relation"]["criterion_paired"] = run["criterion_repair"]["report"]
        source_kind = run.get("extension_kind")
        if source_kind == "leakage" and "leakage_source_only" in repairs:
            run["repair_relation"]["leakage_source_only"] = repairs["leakage_source_only"]
    for row in result["rows"]:
        run = next(r for r in result["runs"] if r["case"] == row["case"])
        row["physical_identity"] = run["physical_identity"]
        row["repair_reports"] = run["repair_relation"]
        item = run["full_emitted_items"][row["criterion"]]
        if run.get("extension_kind") == "native":
            row["reason"] = item.get("native_numeric_mutations", {}).get("finding", row["reason"])
    for cell in result["cells"]:
        cell["check_ids"] = [c["id"] for c in runner.canonical_definitions()[cell["criterion"]]["checks"]]
        cell["base_report"] = result["base"]["report"]
        cell["repair_report"] = result["repair"]["report"]
        for attempt in cell["attempts"]:
            run = next(r for r in result["runs"] if r["case"] == attempt["case"])
            attempt["physical_identity"] = run["physical_identity"]
            attempt["repair_reports"] = run["repair_relation"]
        if cell["status"] == "unsupported":
            cell["remaining"] = runner.matrix_remaining(cell["criterion"], cell["expected"])
    result["rows"].sort(key=lambda row: (row["case"], row["criterion"]))
    result["physical_case_count"] = len(identities)
    result["base_score_counts"] = {str(n): sum(v == n for v in result["base_scores"].values()) for n in range(4)}
    result["limits"] = [
        "Current scope includes all seven leakage criteria; legacy 18-row results are historical, not current qualification.",
        "Source-only mutant scores do not inherit native or owner authority.",
        "Exact rollback/full-base repair is not fresh native regrading of changed bytes.",
        "Local operator-recorded actual scoped answer; no independently authenticated actor/event or cryptographic authority.",
        "Original expectation disagreements and all collateral rows are retained.",
    ]
    return result


def verify(path, options, *, base_already_verified=False):
    result = json.loads(path.read_text())
    if result["options"] != options:
        raise ValueError("explicit trust options differ")
    original = json.loads(Path(result["original_matrix"]).read_text())
    proof = original.get("verification")
    if not base_already_verified:
        proof = runner.verify_matrix(Path(result["original_matrix"]), options)
    # Original case/repair records cannot be dropped or replaced by extension data.
    if result["base"] != original["base"] or result["repair"] != original["repair"]:
        raise ValueError("full-base identity differs")
    original_cases = {r["case"]: r for r in original["runs"]}
    extra = []
    for run in result["runs"]:
        if not run.get("extension_kind") or run.get("extension_kind") == "leakage":
            old = original_cases.pop(run["case"])
            for key in old:
                if old[key] != run[key]:
                    raise ValueError("original collateral metadata changed: " + key)
        else:
            runner.validate_matrix_expectation(run)
            extra.append(run)
    if original_cases:
        raise ValueError("original cases omitted")
    if {(r["extension_kind"], r["case"]) for r in extra} != {("native", case) for case in extension_specs("native")}:
        raise ValueError("incomplete extension inventory")
    if native_admission(Path(result["native_bundle"]), Path(result["native_registry"])) != result["native_admission"]:
        raise ValueError("native admission changed")
    for run in extra:
        record = run["assessment"]
        if run["extension_kind"] == "native":
            if (
                record.get("kind"),
                record.get("native_case"),
                record.get("native_bundle"),
                record.get("native_registry"),
            ) != ("native", run["case"], result["native_bundle"], result["native_registry"]):
                raise ValueError("native case/registry identity differs")
    records = [r["assessment"] for r in extra]
    distinct = {str(Path(r["report"]).resolve()): r for r in records}
    with ThreadPoolExecutor(max_workers=4) as pool:
        passed = list(pool.map(replay, distinct.values()))
    if not all(passed):
        raise ValueError(
            "extension ordinary replay failed: "
            + str([r["report"] for r, p in zip(distinct.values(), passed) if not p])
        )
    rebuilt = runner.matrix_summary(result["base"], result["runs"], result["repair"])
    if result.get("leakage_controls"):
        rebuilt["leakage_controls"] = result["leakage_controls"]
    enrich(rebuilt)
    for key in rebuilt:
        if result[key] != rebuilt[key]:
            raise ValueError("matrix derivation differs: " + key)
    pending = {(c["criterion"], c["expected"]) for c in result["cells"] if c["status"] == "unsupported"}
    allowed = {(k, 3) for k in runner.canonical_definitions()}
    lower = [c for c in result["cells"] if c["expected"] < 3]
    return {
        "all_replayed": True,
        "original_reports_replayed": proof["distinct_reports_replayed"],
        "extension_reports_replayed": len(distinct),
        "distinct_reports_replayed": proof["distinct_reports_replayed"] + len(distinct),
        "numeric_cells": len(result["cells"]),
        "lower_numeric_cells": len(lower),
        "all_lower_numeric_covered": all(c["status"] != "unsupported" for c in lower),
        "only_deferred_upper_remaining": bool(pending) and pending <= allowed,
        "full_base_repair_equal": True,
        "native_owning_admission": True,
        "original_exact_repairs": proof["exact_repairs"],
        "frozen_sources_preserved": True,
        "blocked_cases": sum(r["assessment"]["state"] != "assessed" for r in result["runs"]),
    }


def complete(original_matrix, output, options, bundle, registry, native_reports=None, *, defer_replay=False):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    original = json.loads(original_matrix.read_text())
    if original.get("definitions_sha256") != runner.definitions_digest():
        raise ValueError("historical matrix cannot be extended as current; generate a fresh current base matrix first")
    if original["options"] != options:
        raise ValueError("base matrix explicit options differ")
    runs, proof = run_extensions(output, original, options, bundle, registry, native_reports)
    result = runner.matrix_summary(original["base"], original["runs"] + runs, original["repair"])
    result.update(
        original_matrix=str(original_matrix),
        options=options,
        native_bundle=str(bundle),
        native_registry=str(registry),
        native_admission=proof,
        source_report=original["source_report"],
        frozen_source_inventory=original["frozen_source_inventory"],
        base_task_identity=original["base_task_identity"],
        leakage_controls=original.get("leakage_controls", {}),
    )
    runner.write_json(output / "current-inventory.json", runner.inventory())
    from rubric_leakage_recipes import pending_requests

    runner.write_json(
        output / "owner-requests.json",
        pending_requests([r for r in result["runs"] if r.get("extension_kind") == "leakage"]),
    )
    enrich(result)
    runner.write_json(output / "matrix.json", result)
    result["verification"] = (
        {"all_replayed": False, "state": "deferred"} if defer_replay else verify(output / "matrix.json", options)
    )
    runner.write_json(output / "matrix.json", result)
    (output / "summary.md").write_text(render(result, output))
    return result


def render(result, output):
    import shlex

    def relative(path):
        return os.path.relpath(path, output)

    lines = [
        "# Consolidated numeric matrix",
        "",
        "Base qualification is reported separately. No new native/model execution; replay status is explicit below.",
        "",
        "- Coverage: `" + json.dumps(result["counts"], sort_keys=True) + "`.",
        "- Base: `" + json.dumps(result["base_score_counts"], sort_keys=True) + "`.",
        "- Verification: `" + json.dumps(result["verification"], sort_keys=True) + "`.",
        "- [Full-base repair]("
        + relative(Path(result["repair"]["report"]).with_name("results.md"))
        + "). Exact rollback, not fresh native regrading.",
        "- [Machine matrix](matrix.json) retains every emitted row, check, evidence reference, collateral change, original disagreement and repair relation.",
        "",
        "| Criterion | Base | 0 | 1 | 2 | 3 |",
        "|---|---:|---|---|---|---|",
    ]
    for ident, value in result["base_scores"].items():
        columns = []
        for cell in [c for c in result["cells"] if c["criterion"] == ident]:
            matched = next((a for a in cell["attempts"] if a["matched"]), None)
            path = matched["report"] if matched else result["base"]["report"] if value == cell["expected"] else None
            columns.append(
                "[" + str(cell["expected"]) + "](" + relative(Path(path).with_name("results.md")) + ")"
                if path
                else "Deferred"
            )
        lines.append(
            "| " + ident + " | " + ("Unassessed" if value is None else str(value)) + " | " + " | ".join(columns) + " |"
        )
    lines += [
        "",
        "## Cases and retained disagreements",
        "",
        "| Case | Criterion | Base | Expected | Actual | Repair | Reason |",
        "|---|---|---:|---:|---:|---:|---|",
    ]

    def score(value):
        return "Unassessed" if value is None else str(value)

    for row in result["rows"]:
        lines.append(
            f"| [{row['case']}]({relative(Path(row['report']).with_name('results.md'))}) | {row['criterion']} | [{score(row['base'])}]({relative(Path(row['base_report']).with_name('results.md'))}) | {score(row['expected'])} | {score(row['actual'])} | [{score(row['repair'])}]({relative(Path(row['repair_report']).with_name('results.md'))}) | {row['evidence_control_kind']}: {rubric_table.cell(row['reason'])} |"
        )
    lines += ["", "## Legacy targets without an equivalent numeric predicate", ""]
    for run in result["runs"]:
        for ident, reason in run.get("legacy_expectation_limits", {}).items():
            lines.append(f"- {run['case']} / {ident} / original target {run['expected'][ident]}: {reason}")
    lines += ["", "## Deferred only", ""]
    lines += [
        f"- {c['criterion']} / {c['expected']}: {c['remaining']}"
        for c in result["cells"]
        if c["status"] == "unsupported"
    ]
    lines += ["", "## Scope", ""] + ["- " + limit for limit in result["limits"]]
    lines += [
        "",
        "## Read-only ordinary verification",
        "",
        "```sh",
        shlex.join(
            [
                sys.executable,
                "-B",
                str(runner.SCRIPTS / "rubric_mutation_runner.py"),
                "--verify-matrix",
                str(output / "matrix.json"),
                *runner.matrix_flags(result["options"]),
            ]
        ),
        "```",
        "",
    ]
    return "\n".join(lines)
