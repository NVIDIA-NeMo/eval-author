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
from rubric_configuration import compare_sourced_config  # noqa: E402
from rubric_freshness import check_freshness  # noqa: E402
from rubric_identity import compare_config, finish, identify, resolve_route, text  # noqa: E402
from rubric_links import check_approvals, check_links, check_sourced_approvals  # noqa: E402
from rubric_native import project_native  # noqa: E402
from w02_report import build_report  # noqa: E402
from w02_scoring import assess  # noqa: E402


def measure(root, selection):
    json.dumps(selection, allow_nan=False)
    if not isinstance(selection, dict):
        raise ValueError("selection must be an object")
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
    if "freshness" in selection:
        measurements["freshness"] = check_freshness(root, artifacts, selection["freshness"])
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
    return {
        "schema": "rubric-measurements-v2",
        "experimental": True,
        "scope": "explicit-local-selection",
        "preflight_success": False,
        "anti_cheat_scores": None,
        "measurements": measurements,
        "items": build_report(assess(measurements, selection, root=root), measurements, selection, root),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = measure(args.root.absolute(), loads(args.selection.read_text()))
        output = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    except (OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schema": "rubric-measurements-v2",
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
