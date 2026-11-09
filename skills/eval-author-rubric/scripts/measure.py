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
from rubric_identity import compare_config, finish, identify, resolve_route  # noqa: E402
from rubric_links import check_approvals, check_links  # noqa: E402
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
    approvals = check_approvals(selection.get("approvals", []), selection.get("decisions", []), artifacts)
    if identity["status"] != "pass":
        approvals = {**approvals, **finish([], approvals["diagnostics"] + identity["diagnostics"])}
    measurements = {"identity": identity, "route": route, "config": config, "links": links, "approvals": approvals}
    return {
        "schema": "rubric-measurements-v2",
        "experimental": True,
        "scope": "explicit-local-selection",
        "preflight_success": False,
        "anti_cheat_scores": None,
        "measurements": measurements,
        "items": assess(measurements, selection, root=root),
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
