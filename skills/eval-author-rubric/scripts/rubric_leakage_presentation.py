# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Display current scoped dispositions without rewriting retained measurements."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def presentation(report: dict[str, Any]) -> dict[str, Any]:
    projection = report.get("measurements", {}).get("leakage_bounded")
    if not projection or not projection.get("reviews"):
        return report
    result = deepcopy(report)
    projection = result["measurements"]["leakage_bounded"]
    ident = projection["criterion"]
    row = result["items"][ident]
    current = {}
    for kind, record in projection["reviews"].items():
        if record and not record["revoked"]:
            current[kind] = record["event"]["decisions"][ident]["decision"]
    if not current:
        return report
    row["display_review_summary"] = "; ".join(f"{kind}: retained {decision}" for kind, decision in current.items())
    row["display_review_summary"] += (
        ". Unchanged answered scopes need no repeated consent; retained deferrals remain incomplete."
    )
    required = {"owner", "agent"} if ident == "CREATE-02" else {"owner"}
    if all(current.get(kind) == "approve" for kind in required):
        for check in row["check_results"]:
            if check["outcome"] == "PASS" and check["method"] in {"llm_as_judge", "human_review", "review"}:
                check["historical_predecision_gap"] = check.get("remaining_gap", "")
                check["remaining_gap"] = (
                    "Exact scoped disposition retained and unrevoked; independent corroboration remains separate."
                )
        observation = projection.get("observations") or {}
        if "qualification" in observation:
            observation["historical_predecision_qualification"] = observation["qualification"]
            observation["qualification"] = (
                "Exact scoped owner/agent decisions retained; no independent or highest-anchor qualification."
            )
        stale = "Inspectable local exposure inventory; known broader provenance gaps are not yet bounded by a reviewed statement."
        if ident == "AUDIT-05-B" and row["score"] == 2 and stale in row.get("findings", []):
            row.setdefault("historical_predecision_findings", []).append(stale)
            row["findings"] = [finding for finding in row["findings"] if finding != stale]
    return result
