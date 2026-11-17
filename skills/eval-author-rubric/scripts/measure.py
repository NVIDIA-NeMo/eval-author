# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Experimental read-only rubric measurements of an explicit selection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
from eval_author_shared.task_evidence import loads  # noqa: E402
from rubric_authority import check_authority  # noqa: E402
from rubric_configuration import compare_sourced_config  # noqa: E402
from rubric_ethos import check_ethos  # noqa: E402
from rubric_freshness import check_freshness  # noqa: E402
from rubric_identity import compare_config, finish, identify, read_selected, resolve_route, text  # noqa: E402
from rubric_links import check_approvals, check_links, check_sourced_approvals  # noqa: E402
from rubric_native import project_native  # noqa: E402
from rubric_reconstruction import project_reconstruction  # noqa: E402
from rubric_report import build_report  # noqa: E402
from rubric_scoring import assess  # noqa: E402


def measure(root, selection, ethos_validator=None):
    selected_root = Path(root).absolute()
    root_identity = None
    if isinstance(selection, dict) and ("scorer_controls" in selection or "example_assessment" in selection):
        if any(path.is_symlink() for path in (selected_root, *selected_root.parents)):
            raise ValueError("selected evidence root is a symlink")
        stat = selected_root.stat()
        root_identity = (stat.st_dev, stat.st_ino)
    root = Path(root).resolve(strict=True)
    json.dumps(selection, allow_nan=False)
    if not isinstance(selection, dict):
        raise ValueError("selection must be an object")
    supported = {
        "approval_sources",
        "approvals",
        "artifacts",
        "audit_companion",
        "authority",
        "decisions",
        "contract_controls",
        "criterion_evidence",
        "declared_config",
        "entrypoint",
        "ethos",
        "expected_scorer",
        "evidence_scope",
        "example_assessment",
        "freshness",
        "links",
        "native",
        "nodes",
        "observed_config",
        "reconstruction",
        "route",
        "rubric_scope",
        "scorer_controls",
        "sourced_config",
    }
    if selection.keys() - supported:
        raise ValueError("unsupported selection field")
    artifacts = selection.get("artifacts", [])
    identity = identify(root, artifacts)
    route = (
        resolve_route(root, artifacts, selection["route"])
        if selection.get("route") is not None
        else {
            "status": "unknown",
            "diagnostics": [{"code": "route.not_selected", "subject": "route", "status": "unknown"}],
        }
    )
    config = compare_config(selection.get("declared_config", {}), selection.get("observed_config", {}))
    links = check_links(selection.get("nodes", []), selection.get("links", []))
    approvals = (
        check_sourced_approvals(
            root,
            selection.get("approvals", []),
            selection.get("decisions", []),
            identity,
            selection["approval_sources"],
        )
        if "approval_sources" in selection
        else check_approvals(selection.get("approvals", []), selection.get("decisions", []), artifacts)
    )
    if identity["status"] != "pass":
        approvals = {**approvals, **finish([], approvals["diagnostics"] + identity["diagnostics"])}
    measurements = {"identity": identity, "route": route, "config": config, "links": links, "approvals": approvals}
    if "sourced_config" in selection:
        selected_config = selection["sourced_config"]
        if not isinstance(selected_config, dict) or not {"declared", "observed"} <= selected_config.keys():
            raise ValueError("invalid sourced configuration selection")
        if selected_config.keys() - {"declared", "observed", "binding"}:
            raise ValueError("invalid sourced configuration selection")
        measurements["sourced_config"] = compare_sourced_config(
            root,
            artifacts,
            selected_config["declared"],
            selected_config["observed"],
            binding=selected_config.get("binding"),
        )
    if "reconstruction" in selection:
        measurements["reconstruction"] = project_reconstruction(root, artifacts, selection["reconstruction"])
    if "freshness" in selection:
        measurements["freshness"] = check_freshness(root, artifacts, selection["freshness"])
    if "ethos" in selection:
        measurements["ethos"] = check_ethos(root, artifacts, selection["ethos"], ethos_validator)
    if "authority" in selection:
        measurements["authority"] = check_authority(root, artifacts, selection["authority"])
    if "native" in selection:
        selected_native = selection["native"]
        if not isinstance(selected_native, dict) or set(selected_native) != {
            "receipt_ids",
            "manifest_ids",
            "result_ids",
            "trace_ids",
        }:
            raise ValueError("invalid native selection")
        if not all(isinstance(ids, list) and all(text(ident) for ident in ids) for ids in selected_native.values()):
            raise ValueError("invalid native selection")
        measurements["native"] = project_native(root, identity, **selected_native)
    if "scorer_controls" in selection:
        from rubric_scorer_controls import project

        measurements["scorer_controls"] = project(root, selection)
    items = build_report(assess(measurements, selection, root=root), measurements, selection, root)
    if "example_assessment" in selection:
        from rubric_example_assessment import apply, project

        measurements["example_assessment"] = project(root, selection)
        apply(items, measurements["example_assessment"])
        from rubric_example_native import apply as apply_native

        native = measurements["example_assessment"].get("native")
        if native and "panels" in native:
            from rubric_combined import apply_panels

            apply_panels(items, native["panels"], apply_native)
        else:
            apply_native(items, native)
        current_example = project(root, selection)
        stat = selected_root.stat()
        if (
            current_example != measurements["example_assessment"]
            or identify(root, artifacts) != identity
            or root_identity != (stat.st_dev, stat.st_ino)
            or any(path.is_symlink() for path in (selected_root, *selected_root.parents))
        ):
            raise ValueError("selected evidence changed during assessment")
    if "scorer_controls" in selection:
        from rubric_scorer_controls import project

        current = project(root, selection)
        stat = selected_root.stat()
        if (
            current != measurements["scorer_controls"]
            or identify(root, artifacts) != identity
            or root_identity != (stat.st_dev, stat.st_ino)
            or any(path.is_symlink() for path in (selected_root, *selected_root.parents))
        ):
            raise ValueError("selected evidence changed during assessment")
    if "contract_controls" in selection:
        from rubric_contract_controls import assess_contract_controls

        contract_rows = assess_contract_controls(root, selection)
        for ident, row in contract_rows.items():
            if ident in items and items[ident]["score"] is not None:
                if row["score"] != items[ident]["score"]:
                    raise ValueError("conflicting canonical scores")
            else:
                items[ident] = row
        if assess_contract_controls(root, selection) != contract_rows or identify(root, artifacts) != identity:
            raise ValueError("selected evidence changed during assessment")
    demonstration = {}
    recorded = {}
    if "criterion_evidence" in selection:
        from rubric_evidence_scoring import assess_evidence

        evidence_rows = assess_evidence(root, selection)
        for ident, row in evidence_rows.items():
            if not row["selected"]:
                continue
            if row["assessment_scope"] == "synthetic-scoring-demonstration":
                demonstration[ident] = row
            else:
                recorded[ident] = row
                if ident not in items:
                    items[ident] = row
                elif row["score"] is not None and row["score"] != items[ident]["score"]:
                    raise ValueError("conflicting canonical scores")
        if assess_evidence(root, selection) != evidence_rows or identify(root, artifacts) != identity:
            raise ValueError("selected evidence changed during assessment")
    refund_contract = None
    if "example_assessment" in selection:
        from rubric_refund_contract_evidence import apply as apply_refund_contract
        from rubric_refund_contract_evidence import project as project_refund_contract

        refund_contract = project_refund_contract(root, selection)
        if refund_contract is not None:
            measurements["refund_contract"] = refund_contract
            apply_refund_contract(items, refund_contract)
    if "example_assessment" in measurements:
        from rubric_source_reviews import apply as apply_reviews

        apply_reviews(items, measurements["example_assessment"]["source_reviews"])
    general_review = None
    if "example_assessment" in measurements:
        from rubric_general_review import apply as apply_general_review
        from rubric_general_review import project as project_general_review

        general_review = project_general_review(root, selection, measurements)
        if general_review is not None:
            measurements["general_review"] = general_review
            apply_general_review(items, general_review)
    report = {
        **({"criterion_demonstration": demonstration} if demonstration else {}),
        **({"recorded_criterion_evidence": recorded} if recorded else {}),
        "schema": "rubric-measurements-v3",
        "experimental": True,
        "scope": "explicit-local-selection",
        "preflight_success": False,
        "anti_cheat_scores": {key: items[key]["score"] for key in ("AC-01", "AC-02", "AC-03", "AC-04")}
        if "scorer_controls" in selection
        else None,
        "measurements": measurements,
        "items": items,
    }
    audit = None
    if "audit_companion" in selection:
        from rubric_audit_companion import project_report, selected

        audit = selected(root, selection)
        report = project_report(report, audit)
    from rubric_general_review import apply_scoped, project_scoped

    scoped = project_scoped(root, selection, report["measurements"], audit)
    if scoped:
        report["measurements"]["scoped_reviews"] = scoped
        for projection in scoped.values():
            apply_scoped(report["items"], projection)
    if refund_contract is not None:
        from rubric_refund_contract_evidence import project as project_refund_contract

        stat = selected_root.stat()
        if (
            project_refund_contract(root, selection) != refund_contract
            or identify(root, artifacts) != identity
            or root_identity != (stat.st_dev, stat.st_ino)
            or any(path.is_symlink() for path in (selected_root, *selected_root.parents))
        ):
            raise ValueError("selected evidence changed during assessment")
    if general_review is not None:
        from rubric_general_review import project as project_general_review

        if project_general_review(root, selection, measurements) != general_review:
            raise ValueError("scoped operator review changed during assessment")
    if project_scoped(root, selection, report["measurements"], audit) != scoped:
        raise ValueError("scoped owner adjudication changed during assessment")
    from rubric_evidence_scope_mutations import project_numeric, apply_numeric

    numeric = project_numeric(root, selection, report)
    if numeric:
        apply_numeric(report, numeric)
        if project_numeric(root, selection, report) != numeric:
            raise ValueError("numeric scope evidence changed during assessment")
    from rubric_semantic_numeric_mutations import project as project_semantic, apply as apply_semantic

    semantic = project_semantic(root, selection)
    if semantic is not None:
        apply_semantic(report, semantic)
        if project_semantic(root, selection) != semantic:
            raise ValueError("semantic subject changed during assessment")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ethos-validator", type=Path, help="ethos-write's scripts/validate_ethos.py")
    parser.add_argument(
        "--verify-existing", type=Path, help="Compare a retained report with a fresh read-only assessment"
    )
    args = parser.parse_args(argv)
    try:
        report = measure(args.root.absolute(), loads(args.selection.read_text()), args.ethos_validator)
        output = json.dumps(report, indent=2, allow_nan=False)
        if args.verify_existing:
            root = args.root.absolute()
            data, reason = read_selected(root, args.verify_existing.absolute().relative_to(root).as_posix())
            if (
                reason
                or data is None
                or json.dumps(loads(data), sort_keys=True, allow_nan=False)
                != json.dumps(report, sort_keys=True, allow_nan=False)
            ):
                raise ValueError("retained report disagrees with current evidence")
    except (OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schema": "rubric-measurements-v3",
                    "status": "invalid",
                    "code": "selection.invalid",
                    "preflight_success": False,
                }
            )
        )
        return 2
    print(output)
    return 0 if all(row["status"] in ("pass", "equal") for row in report["measurements"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
