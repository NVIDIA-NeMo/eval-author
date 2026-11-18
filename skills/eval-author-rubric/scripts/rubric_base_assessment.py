# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run one combined refund base assessment with explicit local evidence scope."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any

from rubric_combined import registry_for, select_bundle
from rubric_table import select_task


def validate_base(root: Path, task: str) -> None:
    from rubric_native_collect import BASE, inventory

    select_task(root, task)
    if inventory(root / Path(task).parent) != inventory(BASE):
        raise ValueError("combined assessment requires unchanged base bytes and modes")


def select_panels(root: Path, task: str, bundles: list[str]) -> dict[str, Any]:
    validate_base(root, task)
    selection = select_task(root, task)
    ids = []
    for bundle in bundles:
        from rubric_table import read_private

        raw = read_private(root / bundle / "native-index.json")
        with registry_for(raw):
            native = select_bundle(root, bundle)
        selection["artifacts"].extend(native["artifacts"])
        ids.append(native["native_bundle_id"])
    selection["example_assessment"]["native_bundle_ids"] = ids
    return selection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--task", help="Root-relative unchanged refund base task.toml")
    parser.add_argument(
        "--bundle", action="append", default=[], help="Root-relative admitted panel; repeat for each scope"
    )
    parser.add_argument(
        "--registry", action="append", type=Path, default=[], help="Trusted private local registry; repeat as needed"
    )
    parser.add_argument(
        "--selection", type=Path, help="Reuse an agent-prepared selection, including accepted source reviews"
    )
    parser.add_argument(
        "--source-review-operator-store", type=Path, help="Trusted local operator store, outside selected evidence"
    )
    parser.add_argument("--source-review-operator-trust", choices=["localhost-assessment-operator-v1"])
    parser.add_argument("--source-review-session-store", type=Path, help="Optional trusted host session store")
    parser.add_argument("--source-review-signed-store", type=Path, help="Optional signed-decision trust store")
    parser.add_argument(
        "--upper-environment-bundle", type=Path, help="Admitted operational companion outside source-review selection"
    )
    parser.add_argument(
        "--upper-anticheat-bundle", type=Path, help="Observational companion; no automatic score promotion"
    )
    parser.add_argument(
        "--upper-anticheat-registry",
        type=Path,
        help="Explicit local admission registry for the observational companion",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-existing", type=Path)
    parser.add_argument(
        "--defer-replay",
        action="store_true",
        help="Generate only; a batch orchestrator must perform final retained replay",
    )
    parser.add_argument(
        "--approve-local-native",
        action="store_true",
        help="Collect missing attack, boundary and runtime panels without models",
    )
    audit = parser.add_mutually_exclusive_group()
    audit.add_argument("--audit-inspect", action="store_true", help="Inspect admitted result bindings without mutation")
    audit.add_argument(
        "--approve-audit-exercise",
        action="store_true",
        help="Run fixed Audit changes and recovery in private copies; no native or model runs",
    )
    parser.add_argument("--docker-plugins", type=Path)
    args = parser.parse_args(argv)
    from rubric_example_assessment import configuration_main
    from rubric_native_collect import collect_observation
    from rubric_refund_boundaries import collect as collect_boundaries
    from rubric_refund_panel import collect as collect_attacks
    from rubric_table import main as table_main

    trust = {
        "EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE": args.source_review_operator_store,
        "EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST": args.source_review_operator_trust,
        "EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE": args.source_review_session_store,
        "EVAL_AUTHOR_SOURCE_REVIEW_TRUST": args.source_review_signed_store,
    }
    previous = {key: os.environ.get(key) for key in (*trust, "EVAL_AUTHOR_NATIVE_REGISTRIES")}
    companions = contextlib.ExitStack()
    try:
        if bool(args.upper_anticheat_bundle) != bool(args.upper_anticheat_registry):
            raise ValueError("upper Anticheat companion requires its explicit registry")
        if args.upper_environment_bundle:
            from rubric_upper_companions import environment_hook

            companions.enter_context(environment_hook(args.upper_environment_bundle.absolute()))
        if args.upper_anticheat_bundle:
            from upper_anticheat_contract import table_hook

            companions.enter_context(
                table_hook(args.upper_anticheat_bundle.absolute(), args.upper_anticheat_registry.absolute())
            )
            from rubric_upper_method_review import table_hook as method_hook

            companions.enter_context(
                method_hook(args.upper_anticheat_bundle.absolute(), args.upper_anticheat_registry.absolute())
            )
            if not args.upper_environment_bundle:
                # The Environment wrapper normally links repeated identities.
                # Standalone Anticheat needs the same lossless bounded rendering.
                import rubric_table
                from rubric_upper_companions import compact_identity_evidence

                original_measure = rubric_table.measure

                def compact_measured(root, selection):
                    report = original_measure(root, selection)
                    compact_identity_evidence(report)
                    return report

                rubric_table.measure = compact_measured  # ty: ignore[invalid-assignment]
                companions.callback(setattr, rubric_table, "measure", original_measure)
        if bool(args.source_review_operator_store) != bool(args.source_review_operator_trust):
            raise ValueError("operator store requires explicit trust")
        for key, value in trust.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = str(value.absolute()) if isinstance(value, Path) else value
        root = args.root.resolve(strict=True)
        if args.registry:
            os.environ["EVAL_AUTHOR_NATIVE_REGISTRIES"] = os.pathsep.join(str(p.absolute()) for p in args.registry)
        if args.verify_existing:
            if (
                args.output
                or args.bundle
                or args.task
                or args.selection
                or args.approve_local_native
                or args.audit_inspect
                or args.approve_audit_exercise
                or args.defer_replay
            ):
                raise ValueError("replay is read-only")
            with contextlib.redirect_stdout(io.StringIO()):
                result = table_main(["--root", str(root), "--verify-existing", str(args.verify_existing)])
            print(json.dumps({"replay": "verified" if result in (0, 1) else "invalid", "assessment_exit": result}))
            return result
        if args.selection:
            if args.output is None or any(
                (args.task, args.bundle, args.approve_local_native, args.audit_inspect, args.approve_audit_exercise)
            ):
                raise ValueError("selection reuse does not collect or change evidence")
            output = args.output.absolute()
            if (
                output.exists()
                or not output.is_relative_to(root)
                or any(p.is_symlink() for p in (output, *output.parents))
            ):
                raise ValueError("unsafe combined output")
            output.mkdir(mode=0o700)
            report = output / "report"
            with contextlib.redirect_stdout(io.StringIO()):
                status = table_main(["--root", str(root), "--selection", str(args.selection), "--output", str(report)])
                replay = (
                    (
                        status
                        if args.defer_replay
                        else table_main(["--root", str(root), "--verify-existing", str(report)])
                    )
                    if status in (0, 1)
                    else 2
                )
            if replay != status or status == 2:
                raise ValueError("combined report failed replay")
            print(
                json.dumps(
                    {
                        "report": str(report),
                        "replay": "deferred" if args.defer_replay else "verified",
                        "assessment_exit": status,
                    }
                )
            )
            return status
        if args.output is None or args.task is None:
            raise ValueError("task and output required")
        validate_base(root, args.task)
        output = args.output.absolute()
        if output.exists() or not output.is_relative_to(root) or any(p.is_symlink() for p in (output, *output.parents)):
            raise ValueError("unsafe combined output")
        output.mkdir(mode=0o700)
        bundles = list(args.bundle)
        selected = select_panels(root, args.task, bundles) if bundles else select_task(root, args.task)
        from rubric_example_assessment import project

        initial = project(root, selected)
        native = initial.get("native")
        panels = native.get("panels", [native]) if native else []
        scopes = {panel["scope"] for panel in panels}
        required = [
            ("eval-author.refund-attack-panel.v1", "attacks", collect_attacks),
            ("eval-author.refund-boundary-panel.v1", "boundaries", collect_boundaries),
            ("eval-author.refund-runtime-observation.v1", "runtime", collect_observation),
        ]
        from rubric_refund_access import collect as collect_access

        if args.approve_local_native:
            required.append(("eval-author.refund-access-panel.v1", "access", collect_access))
        for scope, label, collect in required:
            if scope in scopes:
                continue
            if not args.approve_local_native:
                raise ValueError("missing panel requires local native approval")
            if not args.registry:
                raise ValueError("explicit private registry required for collection")
            os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"] = str(args.registry[0].absolute())
            target = output / label
            collect(target, args.docker_plugins)
            bundles.append(target.relative_to(root).as_posix())
        selected = select_panels(root, args.task, bundles)
        path = output / "selection.json"
        path.write_text(json.dumps(selected, sort_keys=True, indent=2))
        configured = output / "configuration"
        with contextlib.redirect_stdout(io.StringIO()):
            status = configuration_main(
                ["--root", str(root), "--selection", str(path), "--agent", "nop", "--output", str(configured)]
            )
        if status:
            raise ValueError("configuration preparation failed")
        if args.audit_inspect or args.approve_audit_exercise:
            from rubric_audit_companion import AuditSelection, exercise

            selected = json.loads((configured / "selection.json").read_text())
            audit_selection: AuditSelection = {"mode": "inspect"}
            if args.approve_audit_exercise:
                target = output / "audit"
                exercise(root, selected, target)
                audit_selection = {"mode": "exercise", "path": target.relative_to(root).as_posix()}
            selected["audit_companion"] = audit_selection
            (configured / "selection.json").write_text(json.dumps(selected, sort_keys=True, indent=2))
        report = output / "report"
        with contextlib.redirect_stdout(io.StringIO()):
            status = table_main(
                ["--root", str(root), "--selection", str(configured / "selection.json"), "--output", str(report)]
            )
            replay = (
                (status if args.defer_replay else table_main(["--root", str(root), "--verify-existing", str(report)]))
                if status in (0, 1)
                else 2
            )
        if replay != status or status == 2:
            raise ValueError("combined report failed replay")
        print(
            json.dumps(
                {
                    "report": str(report),
                    "replay": "deferred" if args.defer_replay else "verified",
                    "assessment_exit": status,
                }
            )
        )
        return status
    except (ValueError, OSError, KeyError, TypeError):
        print(
            "Combined assessment failed. Preserve partial output and check current sources, registry access and native approval."
        )
        return 2
    finally:
        companions.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
