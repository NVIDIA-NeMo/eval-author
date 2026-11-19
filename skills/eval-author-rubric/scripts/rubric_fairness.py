# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Narrow owner-adjudicated scripted setup comparison, never model performance."""

from rubric_audit_companion import digest, typed_diff


def automatic(request):
    comparison = request["comparison"]
    arms = comparison.get("arms", [])
    ready = False
    differences = []
    if len(arms) == 2:
        differences = typed_diff(arms[0]["configuration"], arms[1]["configuration"])
        for row in differences:
            row["classification"] = (
                "run_identity"
                if row["pointer"] == "/trial_name"
                else "scripted_policy_variant"
                if row["pointer"] == "/agent/kwargs/case"
                else "unreviewed_setup_difference"
            )
        ready = (
            comparison.get("model_study") is False
            and [a["case"] for a in arms] == ["reference", "split-alternative"]
            and len({a["native_id"] for a in arms}) == 2
            and all(
                a["solver_uid"] == 65534
                and a["checks"]["health"] == "ok"
                and a["checks"]["total_credit"] == a["checks"]["max_credit"] == 9
                and len(a["checks"]["checks"]) == 9
                and all(type(v) is int and v == 1 for v in a["checks"]["checks"].values())
                and a["reward"] == 1
                for a in arms
            )
            and differences == comparison["setup_differences"]
            and {r["pointer"] for r in differences} == {"/agent/kwargs/case", "/trial_name"}
            and comparison["owner_question"]["difference_refs"] == ["/agent/kwargs/case"]
            and comparison["owner_question"]["binding"] == digest(arms)
        )
    return {
        "provenance.comparison-fairness": {
            "ready": bool(ready),
            "differences": differences,
            "reason": "Current admitted scripted arms and their full versioned setup diff are required; altered, missing or undisclosed setup cannot inherit this rationale.",
            "level_3_gap": "No matched sensitivity evidence across plausible setups. Score 2 covers only the disclosed, owner-justified reference versus split-refund policy comparison; not model performance.",
            "pending_probe": False,
            "evidence_strength": "E2",
        }
    }
