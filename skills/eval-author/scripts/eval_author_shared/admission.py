# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Candidate admission combines privacy, source, authority and tool-access gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import trace_workspace as workspace
from .task_evidence import digest, write_new


def reviewed_source(root: Path) -> dict:
    summary, safe, safe_sha = workspace._tool_call_source(root)
    privacy = summary["privacy"]
    if not isinstance(privacy, dict) or not privacy["contextual_review_complete"]:
        raise workspace.ContractError("complete the contextual privacy review before admission")
    if privacy["blocking_reasons"]:
        raise workspace.ContractError("resolve blocking non-text evidence before admission")
    audit = workspace._load_object(root / privacy["audit_path"], label="privacy audit")
    report = workspace._load_object(root / privacy["path"], label="privacy report")
    review = audit.get("review", {})
    if (
        audit.get("safe_sha256") != safe_sha
        or not review.get("complete")
        or review.get("reviewer_kind") not in {"agent", "human"}
        or not review.get("note")
        or report != {k: privacy[k] for k in workspace._PRIVACY_REPORT_KEYS}
    ):
        raise workspace.ContractError("privacy review is incomplete, stale or inconsistent")
    # Recompute its denominator and findings against the exact reviewed safe copy.
    expected = workspace._privacy_audit(safe, safe_sha)
    if {k: v for k, v in audit.items() if k != "review"} != {k: v for k, v in expected.items() if k != "review"}:
        raise workspace.ContractError("privacy audit no longer matches the reviewed source")
    return privacy


def assess(root: Path) -> dict:
    root = workspace._ensure_task_dir(root)
    metadata = workspace._check_candidate(argparse.Namespace(task_dir=root))
    summary, safe, safe_sha = workspace._tool_call_source(root)
    candidate = workspace._load_object(root / "candidate.json", label="candidate")
    # A reasoned rejection is an admissible outcome without a task or proof.
    if candidate["status"] == "no_candidate":
        return {
            **metadata,
            "admitted": False,
            "disposition": "no_candidate",
            "reason_codes": candidate["reason_codes"],
            "readiness": "not_attempted",
        }
    privacy = reviewed_source(root)
    workspace._validate_tool_call_pipeline(root, require_decisions=True)
    # The detailed evidence audit is refreshed by inspect-trace; never infer
    # missing context from a truncated display or substitute it for authority.
    required_unknown = [
        r["name"] for r in candidate["software_requirements"] if r["required"] and r["availability"] == "unknown"
    ]
    return {
        **metadata,
        "admitted": True,
        "disposition": "candidate",
        "readiness": "unproven",
        "unknown_required_software": required_unknown,
        "ground_truth": candidate["ground_truth"]["availability"],
        "construction_authority": "requires_construction_contract",
        "bindings": {
            "candidate_sha256": digest(root / "candidate.json"),
            "safe_sha256": safe_sha,
            "privacy_audit_sha256": digest(root / privacy["audit_path"]),
            "privacy_report_sha256": digest(root / privacy["path"]),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = assess(args.task_dir)
        if args.out:
            root = args.task_dir.resolve()
            if not args.out.resolve().is_relative_to(root / "private"):
                raise workspace.ContractError("admission receipts must remain under private/")
            write_new(args.out, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"admitted": False, "error": str(error)}))
        return 1
