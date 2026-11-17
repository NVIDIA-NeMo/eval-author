# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build evidence-only selections. Native receipts and task bytes remain intact."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import rubric_table
from rubric_leakage_current import GAPS, canonical_definitions, definitions_digest

SCRIPTS = Path(__file__).resolve().parent
PANEL_SCOPES = {
    "AUDIT-03": ("eval-author.refund-attack-panel.v1", "eval-author.refund-boundary-panel.v1"),
    "ENV-03-S01": ("eval-author.refund-boundary-panel.v1",),
    **{
        "provenance." + name: ("eval-author.refund-runtime-observation.v1",)
        for name in (
            "observed-configuration",
            "runtime-route",
            "solver-tool-access",
            "dependency-substitution",
            "external-state",
        )
    },
}
AUDIT_CRITERIA = {"provenance.evidence-revision-consistency", "provenance.comparison-fairness"}
# The numeric runner retains these physical recipes and external expectations.
# They select historical panel scopes only, never current criterion scores.
HISTORICAL_PANEL_SCOPES = {
    "AC-01": ("eval-author.refund-access-panel.v1",),
    "AC-02": ("eval-author.refund-attack-panel.v1",),
    "AC-03": ("eval-author.refund-boundary-panel.v1",),
    "AC-04": ("eval-author.refund-boundary-panel.v1",),
}


def catalog() -> dict[str, Any]:
    entries = {}
    for ident, definition in canonical_definitions().items():
        if ident in PANEL_SCOPES:
            supported = True
            removal = list(PANEL_SCOPES[ident])
            limit = "Withhold the owning registered panels together. Retain source observations and other panels, even when they prevent a null. Shared criterion evidence changes together; raw receipts are never edited."
        elif ident in AUDIT_CRITERIA:
            supported = True
            removal = "audit_companion"
            limit = "The Audit consumer couples revision tracking with the comparison inventory. Both projections are withheld."
        elif ident == "provenance.declared-configuration":
            supported = True
            removal = "example_assessment.configuration_id"
            limit = "Task and Docker declarations remain evidence of partial identity. Removing the companion cannot establish a missing-evidence null."
        elif ident in GAPS:
            supported = False
            removal = None
            limit = GAPS[ident] + " No selective removal of this prerequisite is supported. Local source observations remain selected."
        elif ident.startswith("task-contract."):
            supported = False
            removal = None
            limit = "The admitted baseline has no qualified human adjudication to remove. Source controls remain selected; existing null is not a performed removal."
        else:
            supported = False
            removal = None
            limit = "Required source access is coupled to every admitted native panel. Withholding it rejects panel admission; removing every panel would discard unrelated evidence. No selective missing-evidence control is supported."
        entries[ident] = {
            "supported": supported,
            "removal": removal,
            "limit": limit,
            "checks": definition["checks"],
            "scoring": definition["scoring"],
            "evidence": definition.get("evidence", []),
        }
    return entries


def withhold(
    selection: dict[str, Any], criterion: str, panel_scopes: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    changed = deepcopy(selection)
    selector = changed["example_assessment"]
    if criterion in AUDIT_CRITERIA:
        if "audit_companion" not in changed:
            raise ValueError("required evidence is not selected")
        changed.pop("audit_companion")
        return changed, ["audit_companion"]
    if criterion == "provenance.declared-configuration":
        if "configuration_id" not in selector:
            raise ValueError("required evidence is not selected")
        return changed, [selector.pop("configuration_id")]
    scopes = PANEL_SCOPES.get(criterion, HISTORICAL_PANEL_SCOPES.get(criterion))
    if scopes is None:
        raise ValueError("unsupported selective evidence criterion")
    ids = selector.get("native_bundle_ids", [])
    removed = [ident for ident in ids if panel_scopes[ident] in scopes]
    if not removed:
        raise ValueError("required evidence is not selected")
    retained = [ident for ident in ids if ident not in removed]
    if retained:
        selector["native_bundle_ids"] = retained
    else:
        selector.pop("native_bundle_ids")
    changed.pop("audit_companion", None)
    return changed, removed


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def invoke(root: Path, output: Path, *, replay: bool = False) -> dict[str, Any]:
    argv = [sys.executable, "-B", str(SCRIPTS / "rubric_table.py"), "--root", str(root)]
    argv += (
        ["--verify-existing", str(output / "report")]
        if replay
        else ["--selection", str(output / "selection.json"), "--output", str(output / "report")]
    )
    run = subprocess.run(argv, capture_output=True, timeout=300, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    label = "replay" if replay else "assessment"
    (output / (label + ".stdout")).write_bytes(run.stdout)
    (output / (label + ".stderr")).write_bytes(run.stderr)
    record = {"argv": argv, "exit": run.returncode}
    write_json(output / (label + ".command.json"), record)
    return record


def assess(root: Path, selection: dict[str, Any], output: Path) -> dict[str, Any]:
    output.mkdir(mode=0o700)
    write_json(output / "selection.json", selection)
    run = invoke(root, output)
    if run["exit"] not in (0, 1) or not (output / "report/assessment.json").is_file():
        raise ValueError("ordinary assessment failed: " + str(output))
    replay = invoke(root, output, replay=True)
    verified = (
        replay["exit"] == run["exit"]
        and (output / "replay.stdout").read_bytes() == (output / "assessment.stdout").read_bytes()
    )
    if not verified:
        raise ValueError("ordinary retained replay failed: " + str(output))
    return {"report": str(output / "report/assessment.json"), "exit": run["exit"], "replay_verified": verified}


def rows(assessment: dict[str, Any]) -> dict[str, Any]:
    saved = json.loads(rubric_table.read_private(Path(assessment["report"])))
    if "current_inventory" not in saved["report"]:
        raise ValueError("historical criterion inventory requires a fresh current assessment")
    values = saved["report"]["items"]
    definitions = canonical_definitions()
    if values.keys() != definitions.keys():
        raise ValueError("incomplete criterion inventory")
    for ident, definition in definitions.items():
        if [c.get("check_id", c.get("id")) for c in values[ident]["check_results"]] != [c["id"] for c in definition["checks"]]:
            raise ValueError("incomplete check inventory")
    return values


def projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": row["score"],
        "evidence_strength": row.get("evidence_strength"),
        "checks": {c["id"]: c["outcome"] for c in row["check_results"]},
        "findings": row.get("findings", []),
    }


def observations(
    before: dict[str, Any], after: dict[str, Any], restored: dict[str, Any], criterion: str
) -> dict[str, Any]:
    changed_ids = [ident for ident in before if projection(before[ident]) != projection(after[ident])]
    preserved = all(
        (row["score"] != 0 or after[ident]["score"] == 0)
        and all(
            next(c for c in after[ident]["check_results"] if c["id"] == check["id"])["outcome"] == check["outcome"]
            for check in row["check_results"]
            if check["outcome"] in {"FAIL", "ERROR"}
        )
        for ident, row in before.items()
    )
    return {
        "baseline_score": before[criterion]["score"],
        "observed_score": after[criterion]["score"],
        "status": ("preexisting_null" if before[criterion]["score"] is None else "selective_missing")
        if after[criterion]["score"] is None
        else "partial_evidence_retained",
        "demonstrated_selective_drop": before[criterion]["score"] is not None and after[criterion]["score"] is None,
        "fresh_repair_matches": before == restored,
        "preserved_defects": preserved,
        "unchanged_criteria": [ident for ident in before if ident not in changed_ids],
        "collateral_criteria": [ident for ident in changed_ids if ident != criterion],
        "changes": {
            ident: {"before": projection(before[ident]), "after": projection(after[ident])} for ident in changed_ids
        },
    }


def run_case(
    root: Path,
    selection: dict[str, Any],
    baseline: dict[str, Any],
    criterion: str,
    panel_scopes: dict[str, str],
    output: Path,
    *,
    repair_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output.mkdir(mode=0o700)
    changed, removed = withhold(selection, criterion, panel_scopes)
    assessment = assess(root, changed, output / "changed")
    repair = repair_assessment or assess(root, selection, output / "repair")
    before, after, restored = rows(baseline), rows(assessment), rows(repair)
    observed = observations(before, after, restored, criterion)
    result = {
        **observed,
        "criterion": criterion,
        "removed_evidence_ids": removed,
        "panel_scopes": panel_scopes,
        "baseline_assessment": baseline,
        "assessment": assessment,
        "repair_assessment": repair,
        "native_rerun": False,
        "native_neighbors": panel_comparison(
            json.loads(rubric_table.read_private(Path(baseline["report"])))["report"],
            json.loads(rubric_table.read_private(Path(assessment["report"])))["report"],
        ),
        "base_qualified": all(r["score"] == 3 for r in before.values()),
        "selection_artifacts_unchanged": selection["artifacts"] == changed["artifacts"],
        "comparison_scope": "Scores, check outcomes, evidence strength and findings. Full evidence remains in ordinary reports.",
        "definitions_sha256": definitions_digest(),
    }
    if not result["fresh_repair_matches"] or not result["preserved_defects"]:
        raise ValueError("repair or defect preservation failed")
    write_json(output / "result.json", result)
    return result


def panel_comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    inventories = []
    for report in (before, after):
        native = report["measurements"].get("example_assessment", {}).get("native")
        panels = native.get("panels", [native]) if native else []
        inventories.append({p["index_digest"]: p for p in panels})
    original, retained = inventories
    return {
        "retained_panels_equal": all(original.get(key) == value for key, value in retained.items()),
        "retained_panel_digests": sorted(retained),
        "withheld_panel_digests": sorted(original.keys() - retained.keys()),
        "baseline_operational_errors": sum(p["counts"].get("infrastructure_errors", 0) for p in original.values()),
        "retained_operational_errors": sum(p["counts"].get("infrastructure_errors", 0) for p in retained.values()),
    }


def selected_scopes(root: Path, selection: dict[str, Any]) -> dict[str, str]:
    selected_ids = selection["example_assessment"].get("native_bundle_ids", [])
    return {
        row["id"]: json.loads(rubric_table.read_private(root / row["path"]))["schema"]
        for row in selection["artifacts"]
        if row["id"] in selected_ids
    }


def verify_case(result: dict[str, Any], *, checked: set[str] | None = None) -> None:
    baseline = json.loads(rubric_table.read_private(Path(result["baseline_assessment"]["report"])))
    if selected_scopes(Path(baseline["root"]), baseline["selection"]) != result["panel_scopes"]:
        raise ValueError("retained panel scope differs")
    changed = json.loads(rubric_table.read_private(Path(result["assessment"]["report"])))
    if not panel_comparison(baseline["report"], changed["report"])["retained_panels_equal"]:
        raise ValueError("retained native neighbor changed")
    wanted, removed = withhold(baseline["selection"], result["criterion"], result["panel_scopes"])
    if changed["selection"] != wanted or removed != result["removed_evidence_ids"]:
        raise ValueError("retained removal differs from recipe")
    if result["definitions_sha256"] != definitions_digest():
        raise ValueError("canonical definitions changed")
    before, after, repair = [rows(result[key]) for key in ("baseline_assessment", "assessment", "repair_assessment")]
    expected_observations = observations(before, after, repair, result["criterion"])
    expected_observations.update(
        native_neighbors=panel_comparison(baseline["report"], changed["report"]),
        native_rerun=False,
        selection_artifacts_unchanged=baseline["selection"]["artifacts"] == changed["selection"]["artifacts"],
        base_qualified=all(row["score"] == 3 for row in before.values()),
    )
    if any(result.get(key) != value for key, value in expected_observations.items()):
        raise ValueError("retained observation differs")
    if before != repair or result["observed_score"] != after[result["criterion"]]["score"]:
        raise ValueError("retained observation or repair differs")
    for key in ("baseline_assessment", "assessment", "repair_assessment"):
        record = result[key]
        report = Path(record["report"])
        if checked is not None and str(report) in checked:
            continue
        saved = json.loads(rubric_table.read_private(report))
        replay = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "rubric_table.py"),
                "--root",
                saved["root"],
                "--verify-existing",
                str(report.parent),
            ],
            capture_output=True,
            timeout=300,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        if replay.returncode != record["exit"] or replay.returncode not in (0, 1):
            raise ValueError("retained assessment replay failed")
        if checked is not None:
            checked.add(str(report))


def source_inventory(root: Path, selection: dict[str, Any]) -> dict[str, str]:
    from rubric_identity import read_selected

    paths = {row["path"] for row in selection["artifacts"]}
    for row in selection["artifacts"]:
        if row["path"].endswith("/native-index.json"):
            raw, reason = read_selected(root, row["path"])
            if reason or raw is None:
                raise ValueError("unreadable native inventory")
            paths.update((Path(row["path"]).parent / value["path"]).as_posix() for value in json.loads(raw)["files"])
    inventory = {}
    for path in sorted(paths):
        raw, reason = read_selected(root, path)
        if reason or raw is None:
            raise ValueError("unreadable selected source")
        inventory[path] = hashlib.sha256(raw).hexdigest()
    return inventory


def reconcile(
    baseline: dict[str, Any], results: dict[str, Any], selection: dict[str, Any], scopes: dict[str, str]
) -> list[dict[str, Any]]:
    observed = rows(baseline)
    entries = []
    for ident, policy in catalog().items():
        entry = {
            "criterion": ident,
            "baseline_score": observed[ident]["score"],
            "policy": policy,
            "status": "unsupported",
            "result": None,
        }
        if ident in results:
            result = results[ident]
            entry.update(status=result["status"], result=result)
        elif policy["supported"]:
            try:
                withhold(selection, ident, scopes)
            except ValueError:
                entry["status"] = "not_selected"
            else:
                raise ValueError("supported removal was not executed")
        entries.append(entry)
    return entries


def run(source_report: Path, output: Path) -> dict[str, Any]:
    saved = json.loads(rubric_table.read_private(source_report))
    root, selection = Path(saved["root"]), saved["selection"]
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("output must be a new private directory")
    output.mkdir(mode=0o700)
    original = source_inventory(root, selection)
    write_json(output / "source-inventory.json", original)
    baseline = assess(root, selection, output / "baseline")
    # One fresh identical selection supplies the common repair for evidence-only controls.
    repair = assess(root, selection, output / "repair")
    if rows(baseline) != rows(repair):
        raise ValueError("fresh repair differs from baseline")
    scopes = selected_scopes(root, selection)
    results = {}
    for ident, policy in catalog().items():
        if not policy["supported"]:
            continue
        try:
            withhold(selection, ident, scopes)
        except ValueError:
            continue
        results[ident] = run_case(root, selection, baseline, ident, scopes, output / ident, repair_assessment=repair)
        write_json(output / "runs.json", results)
    entries = reconcile(baseline, results, selection, scopes)
    result = {
        "schema": "rubric-selective-evidence-mutations-v1",
        "criteria": entries,
        "source_report": str(source_report),
        "baseline": baseline,
        "repair": repair,
        "panel_scopes": scopes,
        "definitions_sha256": definitions_digest(),
        "source_bytes_preserved": original == source_inventory(root, selection),
        "source_inventory": str(output / "source-inventory.json"),
        "counts": {
            state: sum(row["status"] == state for row in entries)
            for state in (
                "selective_missing",
                "preexisting_null",
                "partial_evidence_retained",
                "unsupported",
                "not_selected",
            )
        },
        "distinct_removals": len(
            {json.dumps(value["removed_evidence_ids"], sort_keys=True) for value in results.values()}
        ),
        "qualified_base": all(row["score"] == 3 for row in rows(baseline).values()),
        "limits": [
            "Evidence selectors change out of place; original task, sources and receipts remain unchanged.",
            "Shared panel removals are reported separately per criterion, not as independent controls.",
            "Frozen Audit binds the complete selected panel set. Panel removal also withholds that incompatible Audit companion.",
            "Fresh repair is a new ordinary assessment of the restored selection shared by all cases, not new native execution.",
            "No synthetic receipts, human decisions, expected scores or criterion labels are sent to assessment.",
        ],
    }
    if not result["source_bytes_preserved"]:
        raise ValueError("source bytes changed")
    write_json(output / "reconciliation.json", result)
    return result


def verify(path: Path) -> None:
    result = json.loads(rubric_table.read_private(path))
    saved = json.loads(rubric_table.read_private(Path(result["baseline"]["report"])))
    original = json.loads(rubric_table.read_private(Path(result["source_inventory"])))
    if original != source_inventory(Path(saved["root"]), saved["selection"]):
        raise ValueError("source inventory changed")
    runs = {row["criterion"]: row["result"] for row in result["criteria"] if row["result"] is not None}
    entries = reconcile(result["baseline"], runs, saved["selection"], result["panel_scopes"])
    counts = {
        state: sum(row["status"] == state for row in entries)
        for state in (
            "selective_missing",
            "preexisting_null",
            "partial_evidence_retained",
            "unsupported",
            "not_selected",
        )
    }
    if (
        result["criteria"] != entries
        or result["counts"] != counts
        or result["definitions_sha256"] != definitions_digest()
        or result["distinct_removals"]
        != len({json.dumps(v["removed_evidence_ids"], sort_keys=True) for v in runs.values()})
        or result["qualified_base"] != all(row["score"] == 3 for row in rows(result["baseline"]).values())
        or result["source_bytes_preserved"] is not True
        or result["panel_scopes"] != selected_scopes(Path(saved["root"]), saved["selection"])
    ):
        raise ValueError("retained reconciliation differs")
    checked: set[str] = set()
    for value in runs.values():
        verify_case(value, checked=checked)
    if not runs:
        for name in ("baseline", "repair"):
            record = result[name]
            output = Path(record["report"]).parent.parent
            if invoke(Path(saved["root"]), output, replay=True)["exit"] != record["exit"]:
                raise ValueError("retained assessment replay failed")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--registry", action="append", type=Path, default=[])
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.registry:
        os.environ["EVAL_AUTHOR_NATIVE_REGISTRIES"] = os.pathsep.join(str(p.absolute()) for p in args.registry)
    if args.verify:
        verify(args.verify)
        print(json.dumps({"verified": True}))
    else:
        if args.source_report is None or args.output is None:
            parser.error("--source-report and --output are required")
        result = run(args.source_report.absolute(), args.output.absolute())
        print(
            json.dumps(
                {
                    "counts": result["counts"],
                    "distinct_removals": result["distinct_removals"],
                    "report": str(args.output / "reconciliation.json"),
                }
            )
        )
    return 0


def project_numeric(root: Path, selection: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Derive narrow numeric conditions from owning, current observations.

    Inventory documents are claims to check, never trusted outcomes. Observation
    scopes restrict what the assessment claims to have checked, not admission:
    whole native panels remain validated and their failures remain visible.
    """
    from rubric_example_assessment import Sources

    example = report.get("measurements", {}).get("example_assessment")
    scope = selection.get("evidence_scope", {})
    if not isinstance(scope, dict) or scope.keys() - {"configuration_fields", "actions", "components", "state_cases"}:
        raise ValueError("unsupported numeric observation scope")
    if not example:
        if scope:
            raise ValueError("observation scope requires current owning evidence")
        return {}
    result = {}
    semantic = report.get("measurements", {}).get("refund_contract", {}).get("task-contract.scoring-authority", {})
    inspection = semantic.get("source_inspection", {})
    if inspection.get("variant") in {"completion-only", "approval-after-effect", "ledger-omission"}:
        from rubric_refund_contract_evidence import _current, _controls, _selected, _program, DEFECTS
        import tempfile
        current = _current()
        _, selected_bytes, variant = _selected(root, selection)
        scorer, grader = _program(current, selected_bytes, variant)
        namespace = {"__name__": "trusted_numeric_ledger"}
        exec(compile(current["environment/ledger.py"], "<trusted-current-ledger>", "exec"), namespace)
        contract = json.loads(current["environment/contract.json"])
        control_name = DEFECTS[variant][0]
        control = _controls(contract)[control_name]
        # Public act/complete handlers dispatch to these exact guarded methods;
        # the unchanged server, shell and verifier are full-closure admitted.
        with tempfile.TemporaryDirectory(prefix="numeric-reach-") as temporary:
            ledger = namespace["Ledger"](contract, Path(temporary) / "record.json")
            for event in control["events"]:
                ledger.act(event)
            ledger.complete(control["success"])
            reached = ledger.read()
            credit = scorer["evaluate"](contract, reached["candidate"], grader=grader)
        if credit["health"] != "ok" or credit["total_credit"] <= 0 or reached["candidate"] != control:
            raise ValueError("source shortcut reach or credit not established")
        finding = {"score": 0, "strength": "E1", "reason": DEFECTS[variant][2],
                   "shared_finding": "refund-reachable-" + variant, "variant": variant,
                   "control": control_name, "actual_credit": credit,
                   "reach": "Current public handler dispatch, Ledger guards/state, verifier record reconciliation and reward writer; bounded Ledger/scorer methods exercised, not a native solver trial.",
                   "binding": inspection["binding"]}
        result["AC-02"] = finding
        if variant == "completion-only":
            result["AC-03"] = deepcopy(finding)
    sources = Sources(root, selection["artifacts"])
    if example["task_config_accessible"]:
        import tomllib
        from harbor.models.task.config import TaskConfig
        from pydantic import ValidationError

        raw_config = sources.source(example["task_config"])
        if raw_config is None:
            raise ValueError("selected configuration became unavailable")
        try:
            TaskConfig.model_validate(tomllib.loads(raw_config.decode()))
        except ValidationError as error:
            result["provenance.runtime-route"] = {
                "score": 0, "strength": "E2", "reason": "The installed Harbor TaskConfig rejects the selected manifest; this is provider compatibility failure, not agent behavior.",
                "validator": "harbor.models.task.config.TaskConfig",
                "errors": [{"location": list(e["loc"]), "type": e["type"]} for e in error.errors()],
                "manifest_digest": hashlib.sha256(raw_config).hexdigest(),
            }
    claims = [a for a in selection["artifacts"] if a["role"] == "assessment_inventory"]
    if len(claims) > 1:
        raise ValueError("ambiguous assessment inventory")
    if claims:
        raw = sources.source(claims[0]["path"])
        if raw is None:
            raise ValueError("inventory claim unavailable")
        claim = json.loads(raw)
        if (not isinstance(claim, dict) or set(claim) != {"schema", "task_config_id", "scorer", "agent"}
                or claim["schema"] != "rubric-assessment-inventory-v1"
                or claim["task_config_id"] != selection["example_assessment"]["task_config_id"]):
            raise ValueError("invalid inventory claim")
        companion = example["configuration"].get("companion")
        if claim["scorer"] is not None:
            if not isinstance(claim["scorer"], str) or not example["route_sources_accessible"]:
                raise ValueError("inventory scorer is not comparable")
            if claim["scorer"] != example["scorer"]:
                result["provenance.grading-path"] = {
                    "score": 0, "strength": "E1", "reason": "The claimed scorer contradicts the literal active verifier entrypoint.",
                    "claimed": claim["scorer"], "observed": example["scorer"], "source": claims[0],
                }
        if claim["agent"] is not None:
            if (not isinstance(claim["agent"], dict) or set(claim["agent"]) != {"name", "kind"}
                    or not all(isinstance(v, str) and v for v in claim["agent"].values())
                    or not companion or not companion["complete"]):
                raise ValueError("inventory agent is not comparable")
            if claim["agent"] != companion["agent"]:
                result["provenance.declared-configuration"] = {
                    "score": 0, "strength": "E1", "reason": "Two sourced declarations name different evaluated agents; the claimed setup has no consistent material agent identity.",
                    "claimed": claim["agent"], "observed": companion["agent"], "source": claims[0],
                }
    if scope:
        native = example.get("native") or {}
        panels = native.get("panels", [native])
        runtime = [p for p in panels if p.get("scope") == "eval-author.refund-runtime-observation.v1"]
        if len(runtime) != 1 or runtime[0].get("task_freshness") != "current":
            raise ValueError("partial observation scope requires owning native admission")
        panel = runtime[0]
        environment = panel["environment"]
        available = {
            "configuration_fields": sorted({r["field"] for r in panel["configuration_comparison"]}),
            "actions": sorted(environment["contract"]["actions"]),
            "components": sorted(environment["components"]),
            "state_cases": sorted(r["case"] for r in environment["action_controls"]),
        }
        criteria = {
            "configuration_fields": "provenance.observed-configuration",
            "actions": "provenance.solver-tool-access",
            "components": "provenance.dependency-substitution",
            "state_cases": "provenance.external-state",
        }
        for key, names in scope.items():
            if (not isinstance(names, list) or not names or len(set(names)) != len(names)
                    or not all(isinstance(n, str) and n in available[key] for n in names)
                    or set(names) == set(available[key])):
                raise ValueError("partial scope must name a nonempty proper observed subset")
            if key == "actions" and names != ["read", "error"]:
                raise ValueError("only the admitted read/error partial action scope is supported")
            if key == "state_cases" and names != ["nop"]:
                raise ValueError("only initial-state partial scope is supported")
            if key == "configuration_fields" and any(
                r["status"] not in {"match", "justified", "not_applicable"}
                for r in panel["configuration_comparison"] if r["field"] in names
            ):
                raise ValueError("selected configuration fields are not demonstrated matches")
            if key == "components" and any(environment["components"][n]["status"] != "real" for n in names):
                raise ValueError("selected components are not reconciled")
            if key in {"actions", "state_cases"} and not all(
                r["uid"] == 65534 and r["initial_state_matches"] and r["errors_preserve_exact_state"]
                for r in environment["action_controls"]
            ):
                raise ValueError("partial solver/state observations failed")
            result[criteria[key]] = {
                "score": 1, "strength": "E2", "selected": names,
                "uncovered": sorted(set(available[key]) - set(names)),
                "owning_panel": panel["index_digest"],
                "reason": "Actual admitted " + key + " observations cover the selected subset; other material observations are explicitly outside this assessment scope.",
            }
    if not scope:
        native = example.get("native") or {}
        panels = native.get("panels", [native])
        scopes = {p.get("scope") for p in panels}
        runtime = next((p for p in panels if p.get("scope") == "eval-author.refund-runtime-observation.v1"), None)
        if runtime and runtime.get("task_freshness") == "current" and runtime.get("native_execution") is True:
            controls = runtime["environment"]["action_controls"]
            healthy = (len(controls) == 3 and {c["case"] for c in controls} == {"nop", "actions", "reset"}
                       and all(c["uid"] == 65534 and c["initial_state_matches"] and c["errors_preserve_exact_state"] for c in controls))
            permissions = runtime["observations"]["actions"]["permissions"]
            partial = {
                "AC-01": ("eval-author.refund-access-panel.v1", healthy and permissions["/app/contract.json"]["readable"] and not permissions["/var/lib/refund/record.json"]["readable"],
                          "Actual solver reads establish the public-contract/private-record boundary only; image layers and alternate access paths are outside the selected panel scope."),
                "AC-02": ("eval-author.refund-attack-panel.v1", healthy and {c["case"]: c["reward"] for c in controls} == {"nop": 0.0, "actions": 1.0, "reset": 0.0},
                          "Actual no-work/action controls establish partial defenses; full failure, copied-answer, forged-output and alternative classes remain outside the selected panel scope."),
                "AC-03": ("eval-author.refund-boundary-panel.v1", healthy,
                          "Owning recorder validates current task/configuration/results; forged and stale reward consumer paths are outside the selected panel scope."),
                "AC-04": ("eval-author.refund-boundary-panel.v1", healthy,
                          "Fresh native initial/reset states match exactly; a bounded predecessor and remaining persistence surfaces are outside the selected panel scope."),
            }
            for ident, (full_scope, ready, reason) in partial.items():
                if full_scope not in scopes and ready:
                    result[ident] = {"score": 1, "strength": "E2", "reason": reason, "owning_panel": runtime["index_digest"]}
    return result


def apply_numeric(report: dict[str, Any], projection: dict[str, Any]) -> None:
    report["measurements"]["numeric_scope"] = deepcopy(projection)
    for ident, observation in projection.items():
        row = report["items"][ident]
        if row["score"] == 0 and observation["score"] != 0:
            continue
        if any(c["outcome"] in {"FAIL", "ERROR", "BLOCKED"} for c in row["check_results"]):
            if observation["score"] != 0:
                continue
        row.update(score=observation["score"], status="ASSESSED", assessment_state="ASSESSED",
                   execution_state="PARTIAL", evidence_strength=observation["strength"])
        row["remaining_gaps"] = [observation["reason"], *row.get("remaining_gaps", [])]
        row.setdefault("findings", []).append({"code": "numeric.scope", "message": observation["reason"]})
        for check in row["check_results"]:
            if check["method"].lower() not in {"deterministic", "probe"} or check["outcome"] in {"FAIL", "ERROR", "BLOCKED"}:
                continue
            if observation["strength"] == "E1" and check["method"].lower() == "probe":
                continue
            check.update(outcome="FAIL" if observation["score"] == 0 else "PARTIAL", remaining_gap=observation["reason"])
            check.setdefault("evidence", []).append({"helper": "numeric_scope", "value": {"measurement": "numeric_scope", "criterion": ident}})


if __name__ == "__main__":
    raise SystemExit(main())
