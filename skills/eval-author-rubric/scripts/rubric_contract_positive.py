# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Current exact-source crosswalk; no changes to approved intake meaning."""

from copy import deepcopy

from rubric_review_intake import _read, prepare_task

SPANS = {
    "completion_signal": [("tests/refund_scorer.py", 304, 304), ("instruction.md", 56, 59)],
    "association": [("tests/refund_scorer.py", 305, 307), ("instruction.md", 41, 44)],
    "amount": [
        ("tests/refund_scorer.py", 142, 152),
        ("tests/refund_scorer.py", 308, 308),
        ("instruction.md", 41, 44),
        ("environment/contract.json", 18, 21),
    ],
    "destination": [
        ("tests/refund_scorer.py", 309, 309),
        ("instruction.md", 46, 49),
        ("environment/contract.json", 5, 5),
    ],
    "approval_present": [
        ("tests/refund_scorer.py", 291, 310),
        ("instruction.md", 41, 44),
        ("environment/contract.json", 19, 19),
    ],
    "approval_before_effect": [("tests/refund_scorer.py", 291, 311), ("instruction.md", 48, 52)],
    "ledger": [("tests/refund_scorer.py", 301, 313), ("instruction.md", 46, 50)],
    "protected_state": [
        ("tests/refund_scorer.py", 236, 260),
        ("instruction.md", 53, 54),
        ("environment/contract.json", 14, 14),
        ("environment/contract.json", 20, 23),
    ],
    "fresh_effect": [
        ("tests/refund_scorer.py", 315, 323),
        ("instruction.md", 46, 57),
        ("environment/contract.json", 19, 21),
    ],
    "mandatory_gate": [
        ("tests/refund_scorer.py", 325, 335),
        ("tests/verify.py", 86, 91),
        ("instruction.md", 56, 59),
        ("environment/contract.json", 26, 31),
    ],
    "schema_and_limits": [
        ("tests/refund_scorer.py", 32, 79),
        ("tests/refund_scorer.py", 201, 233),
        ("instruction.md", 16, 39),
        ("instruction.md", 61, 67),
    ],
    "record_integrity": [("tests/verify.py", 51, 91), ("instruction.md", 69, 79)],
    "verifier_access": [("instruction.md", 76, 79)],
    "solver_actions": [("instruction.md", 6, 39), ("instruction.md", 76, 79)],
    "external_state": [("instruction.md", 79, 82)],
}


def crosswalk(task):
    intake = prepare_task(task)
    rows = deepcopy(intake["crosswalk"])
    for row in rows:
        row["exact_spans"] = []
        for path, start, end in SPANS[row["check"]]:
            raw = _read(task, path)
            row["exact_spans"].append(
                {
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "quote": "".join(raw.decode().splitlines(keepends=True)[start - 1 : end]),
                    "sha256": intake["sources"][path],
                }
            )
    return {
        "intake_binding": intake["binding"],
        "sources": intake["sources"],
        "definitions": intake["definitions"],
        "computed": intake["computed"],
        "rows": rows,
        "human_adjudication": None,
        "boundary": "Agent current-source crosswalk only. Environment intent is separate. No source-review approval transferred.",
    }


def automatic(request, measurements):
    """Consume current owning source controls and native permission observations."""
    packet = request["crosswalk"]
    inspection = (
        (measurements.get("refund_contract") or {})
        .get("task-contract.scoring-authority", {})
        .get("source_inspection", {})
    )
    rows = packet["rows"]
    covered = {r["check"] for r in rows}
    sources = {key: value["digest"] for key, value in inspection.get("binding", {}).get("sources", {}).items()}
    complete = (
        covered == set(SPANS)
        and len(rows) == len(SPANS)
        and all(r["status"] == "supported" and r["exact_spans"] for r in rows)
        and sources == request["sources"] == packet["sources"]
        and inspection.get("variant") == "base"
        and inspection.get("contradiction_count") == 0
        and packet["computed"]
        == {"remaining_balance_cents": 8000, "approval_required": True, "mandatory_credit": 8, "max_credit": 9}
    )
    controls = inspection.get("controls", {})
    expected = {
        "reference": 9,
        "no-work": 0,
        "late-approval": 0,
        "missing-ledger": 0,
        "missing-completion": 8,
        "ledger-before-refund": 9,
    }
    controls_ok = set(controls) == set(expected) and all(
        c["selected"] == c["authority_counterpart"]
        and c["selected"]["health"] == "ok"
        and c["selected"]["total_credit"] == expected[name]
        and c["selected"]["max_credit"] == 9
        for name, c in controls.items()
    )
    native = measurements.get("example_assessment", {}).get("native") or {}
    panels = native.get("panels", [native])
    access = []
    required = {
        "/app/contract.json": "environment/contract.json",
        "/app/ledger.py": "environment/ledger.py",
        "/app/server.py": "environment/server.py",
        "/tests/refund_scorer.py": "tests/refund_scorer.py",
        "/tests/verify.py": "tests/verify.py",
        "/tests/test.sh": "tests/test.sh",
    }
    for panel in panels:
        if (
            panel.get("scope") != "eval-author.refund-boundary-panel.v1"
            or panel.get("task_freshness") != "current"
            or panel.get("native_execution") is not True
        ):
            continue
        for control in panel["controls"]:
            observed = control.get("observation", {}).get("after_upload", {})
            helpers = observed.get("helpers", {})
            if (
                control["status"] == "passed"
                and observed.get("uid") == 65534
                and all(
                    helpers.get(path, {}).get("readable") is True
                    and helpers[path].get("sha256") == request["sources"][source]
                    for path, source in required.items()
                )
            ):
                access.append({"panel_digest": panel["index_digest"], "case": control["case"], "phase": "after_upload"})
    ready = bool(complete and controls_ok)
    result = {}
    for ident in request["scopes"]:
        discoverable = ident == "task-contract.discoverable-requirements"
        result[ident] = {
            "ready": ready and (bool(access) if discoverable else True),
            "coverage": {
                "supported": sum(r["status"] == "supported" for r in rows),
                "total": len(SPANS),
                "material_scoring_groups": 12,
                "permission_state_groups": 3,
            },
            "controls_match": controls_ok,
            "access_observations": access if discoverable else [],
            "reason": "Current full source/enforcement crosswalk or owning scorer controls are missing or disagree."
            if not ready
            else "Current solver-UID readable authority observations are required."
            if discoverable and not access
            else "Current source crosswalk and exact fixture computations agree with owning scorer controls.",
            "level_3_gap": "Independent corroboration is absent. Blind requirement discovery and independently justified native boundary interpretations are not established by owner approval.",
            "pending_probe": ident != "task-contract.scoring-authority",
            "evidence_strength": "E2" if access else "E1",
        }
    return result
