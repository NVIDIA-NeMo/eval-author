# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""External source-control recipes; not imported by the assessment consumer."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import example_fixtures as fixtures
import rubric_table

SOURCE_CASES = (
    "inventory",
    "private-copy",
    "answer-filename",
    "split-duplicate",
    "prompt-reuse",
    "withhold-source-inventory",
)
CONTROL_INTERVENTIONS = {
    "AUDIT-03": ("complete", "completion-grader", "omit-access-class"),
    "AUDIT-04-B": ("retained-and-new", "unrepaired-retained", "new-only"),
    "AUDIT-05-B": ("bounded-disclosure", "cleanliness-claim", "omit-unknown-scope"),
    "AUDIT-05-S03": ("frozen-confirmation", "exposed-confirmation", "omit-alternative"),
    "TRACE-04-A": ("withhold-material", "release-prohibited-fixture", "omit-context-channel"),
    "ENV-03-S01": ("reset-all", "retain-candidate", "omit-order"),
    "CREATE-02": ("four-controls", "copy-answer-cue", "cue-only"),
}
CONTROL_CASES = {
    ident + "." + arm: (ident, arm)
    for ident in CONTROL_INTERVENTIONS
    for arm in ("base", "0", "1", "2", "missing", "operational", "repair")
}
CASES = SOURCE_CASES + tuple(CONTROL_CASES)


def control_plan(case: str) -> dict[str, Any]:
    from rubric_leakage_controls import SCHEMA, SCOPE

    ident, arm = CONTROL_CASES[case]
    healthy, defect, partial = CONTROL_INTERVENTIONS[ident]
    return {
        "schema": SCHEMA,
        "scope": SCOPE,
        "criterion": ident,
        "intervention": defect if arm == "0" else partial if arm == "1" else healthy,
        "evidence": "withhold" if arm == "missing" else "operational-failure" if arm == "operational" else "retain",
    }


def expectations(case: str) -> dict[str, Any]:
    if case not in CASES:
        raise ValueError("unknown leakage recipe")
    if case in CONTROL_CASES:
        ident, arm = CONTROL_CASES[case]
        # CREATE-02.0 retains the historical target, not a promised outcome. The
        # repaired action-only subject cannot demonstrate the required claim
        # contradiction; its measured scope need is retained in the report.
        return {ident: None if arm in {"missing", "operational"} else 2 if arm in {"base", "repair"} else int(arm)}
    # These exercise actual bounded observations, not simulated owner decisions.
    return {"AUDIT-05-B": None if case == "withhold-source-inventory" else 1, "CREATE-02": None, "AUDIT-05-S03": None}


def materialize(output: Path, case: str) -> dict[str, Any]:
    output = fixtures._separate_output(output)
    if case not in CASES or output.exists():
        raise ValueError("new output and known leakage recipe required")
    output.mkdir(mode=0o700, parents=True)
    shutil.copytree(fixtures.BASE, output / "task")
    if case == "private-copy":
        shutil.copyfile(output / "task/solution/reference.py", output / "task/environment/reference-copy.py")
    elif case == "answer-filename":
        (output / "task/solution/answer.txt").write_text("canary4719\n")
        (output / "task/environment/answer_canary4719.txt").write_text("Inert filename-only cue control.\n")
    elif case == "split-duplicate":
        shutil.copytree(output / "task", output / "confirmation")
    elif case == "prompt-reuse":
        (output / "agent-context.txt").write_bytes((output / "task/solution/reference.py").read_bytes())
    selected = rubric_table.select_task(output, "task/task.toml")
    if case in CONTROL_CASES:
        from rubric_leakage_controls import INPUT_ID

        path = output / "leakage-control.json"
        path.write_text(json.dumps(control_plan(case), sort_keys=True) + "\n")
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        selected["artifacts"].append(
            {
                "id": INPUT_ID,
                "path": path.name,
                "role": "dependency",
                "digest": value,
                "access": "available",
                "revision": None,
                "selection_origin": "explicit bounded simulation control",
                "source": path.name,
                "evidence": [{"path": path.name, "digest": value}],
            }
        )
        return selected
    if case == "withhold-source-inventory":
        for record in selected["artifacts"]:
            record["access"] = "unresolved"
        return selected
    selected.update(layout={"task": "task"}, leakage={"task": "task"}, external={"task": "task"})
    if case == "split-duplicate":
        selected["artifacts"].extend(rubric_table.select_task(output, "confirmation/task.toml")["artifacts"])
        selected["overlap"] = {
            "splits": {"development": ["task"], "confirmation": ["confirmation"]},
            "required": ["development", "confirmation"],
        }
    elif case == "prompt-reuse":
        path = output / "agent-context.txt"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        selected["artifacts"].append(
            {
                "id": "agent-context:" + digest,
                "path": path.name,
                "role": "dependency",
                "digest": digest,
                "access": "available",
                "revision": None,
                "selection_origin": "explicit context control",
                "source": path.name,
                "evidence": [{"path": path.name, "digest": digest}],
            }
        )
        selected["reuse"] = {"tasks": ["task"], "paths": ["agent-context.txt"]}
    return selected


def verify_input(root: Path, case: str, selection: dict[str, Any]) -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="leakage-recipe-") as directory:
        expected_root = Path(directory) / "expected"
        expected = materialize(expected_root, case)
        if selection != expected:
            raise ValueError("retained leakage selection differs from recipe")
        for name in ("task", "confirmation"):
            path = expected_root / name
            if path.exists() and fixtures._snapshot(root / name) != fixtures._snapshot(path):
                raise ValueError("retained leakage mutation differs from recipe")
        if (
            case in CONTROL_CASES
            and (root / "leakage-control.json").read_bytes() != (expected_root / "leakage-control.json").read_bytes()
        ):
            raise ValueError("retained bounded control intervention differs")
        if (
            case == "prompt-reuse"
            and (root / "agent-context.txt").read_bytes() != (expected_root / "agent-context.txt").read_bytes()
        ):
            raise ValueError("retained prompt-reuse control differs")


def evidence_kind(case: str) -> str:
    if case not in CONTROL_CASES:
        return "missing_evidence" if case == "withhold-source-inventory" else "source_observation"
    arm = CONTROL_CASES[case][1]
    return {"operational": "operational_failure", "missing": "missing_evidence"}.get(arm, "numeric_or_repair")


def run(output: Path, options: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import rubric_mutation_runner as runner

    runs: list[dict[str, Any]] = []
    source_options = {k: v for k, v in options.items() if not k.startswith("upper-")}
    for case in CASES:
        root = output / case
        selected = materialize(root, case)
        record = runner.matrix_assess(root, selected, root / "assessment", source_options)
        if record["state"] != "assessed":
            raise ValueError("ordinary leakage report generation failed: " + case)
        verify_observation(json.loads(rubric_table.read_private(Path(record["report"])))["report"], case)
        runs.append(
            {
                "case": "leakage-" + case,
                "expected": expectations(case),
                "assessment": record,
                "extension_kind": "leakage",
                "reason": "Current bounded local Ledger/scorer and source controls, explicitly synthetic fixtures. Numeric targets are external; actual owner/method decisions remain pending, and no native/independent authority transfers."
                if case in CONTROL_CASES
                else "Owning source measurements inspect a real materialized control; no semantic approval or native execution inferred.",
                "repair_description": "Fresh criterion repair is leakage-"
                + CONTROL_CASES[case][0]
                + ".repair; retained-affected AND new-task checks are owned by containment."
                if case in CONTROL_CASES
                else "Restore exact task/context/split selection; source-only repair and original full-base repair are distinct.",
                "task_bytes_changed": case in ("private-copy", "answer-filename"),
                "evidence_control_kind": evidence_kind(case),
            }
        )
    indexed = {record["case"]: record for record in runs}
    for record in runs:
        name = record["case"].removeprefix("leakage-")
        if name in CONTROL_CASES:
            ident, _ = CONTROL_CASES[name]
            record["criterion_baseline"] = indexed["leakage-" + ident + ".base"]["assessment"]
            record["criterion_repair"] = indexed["leakage-" + ident + ".repair"]["assessment"]
    root = output / "repair"
    selected = materialize(root, "inventory")
    repair = runner.matrix_assess(root, selected, root / "assessment", source_options)
    if repair["state"] != "assessed":
        raise ValueError("ordinary leakage source repair failed")
    for record in runs:
        if record["case"].removeprefix("leakage-") in SOURCE_CASES:
            record["criterion_baseline"] = runs[0]["assessment"]
            record["criterion_repair"] = repair
    return runs, {"baseline": runs[0]["assessment"], "repair": repair}


def verify_observation(report: dict[str, Any], case: str) -> None:
    if case in CONTROL_CASES:
        from rubric_leakage_controls import digest

        ident, arm = CONTROL_CASES[case]
        observed = report["measurements"]["leakage_bounded"]
        if observed["criterion"] != ident or observed["binding"]["plan"] != digest(control_plan(case)):
            raise ValueError("wrong bounded control projection")
        state = "NOT_RUN" if arm == "missing" else "ERROR" if arm == "operational" else "COMPLETED"
        if observed["execution_state"] != state or (observed["observations"] is None) != (
            arm in {"missing", "operational"}
        ):
            raise ValueError("bounded control observation missing")
        # Numeric targets remain external. Pending owner/method review is a real
        # matrix gap, never a reason to manufacture a passing target score.
        return
    sources = report["measurements"]["leakage_current"]["sources"]
    if case == "private-copy":
        found = any(
            f["kind"] == "full"
            and not f["allowed"]
            and f["private"].endswith("/solution/reference.py")
            and f["public"].endswith("/environment/reference-copy.py")
            for f in sources["leakage"]["findings"]
        )
    elif case == "answer-filename":
        found = any(
            f["kind"] == "filename" and f["public"].endswith("/environment/answer_canary4719.txt")
            for f in sources["leakage"]["findings"]
        )
    elif case == "split-duplicate":
        found = any(f["kind"] == "duplicate" for f in sources["overlap"]["findings"])
    elif case == "prompt-reuse":
        found = any(
            f["kind"] == "private-line" and f["file"] == "agent-context.txt" for f in sources["reuse"]["findings"]
        )
    elif case == "withhold-source-inventory":
        found = not sources
    elif case == "inventory":
        found = bool(sources["layout"]["files"])
    else:
        raise ValueError("unknown leakage observation recipe")
    if not found:
        raise ValueError("owning measurement did not observe intended control: " + case)


def pending_requests(runs: list[dict[str, Any]]) -> dict[str, Any]:
    from rubric_leakage_intake import approved, request

    grouped: dict[str, dict[str, Any]] = {}
    for record in runs:
        saved = json.loads(rubric_table.read_private(Path(record["assessment"]["report"])))
        projection = saved["report"].get("measurements", {}).get("leakage_bounded")
        if not projection or projection["execution_state"] != "COMPLETED":
            continue
        ident = projection["criterion"]
        for kind in ("owner", "agent") if ident == "CREATE-02" else ("owner",):
            if approved(projection.get("reviews", {}).get(kind), ident):
                continue
            value = request(projection, kind=kind)
            entry = grouped.setdefault(value["binding"], {"request": value, "cases": [], "reports": []})
            entry["cases"].append(record["case"])
            entry["reports"].append(record["assessment"])
    return {
        "schema": "rubric-leakage-pending-requests-v1",
        "owner_questions": {
            ident: [
                entry
                for entry in grouped.values()
                if entry["request"]["kind"] == "owner" and ident in entry["request"]["scopes"]
            ]
            for ident in CONTROL_INTERVENTIONS
        },
        "agent_requests": [entry for entry in grouped.values() if entry["request"]["kind"] == "agent"],
        "exact_request_count": len(grouped),
        "boundary": "One batch by criterion, with each exact variant binding retained. No approval or retention consent is inferred.",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    import rubric_mutation_runner as runner

    parser = argparse.ArgumentParser(description="Final-stage bounded leakage simulation; no native/model execution")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-review-operator-store", type=Path)
    parser.add_argument("--source-review-operator-trust", choices=["localhost-assessment-operator-v1"])
    parser.add_argument(
        "--defer-replay",
        action="store_true",
        help="Collect ordinary observations and exact requests without retained replay",
    )
    args = parser.parse_args(argv)
    output = fixtures._separate_output(args.output)
    if output.exists():
        raise ValueError("new leakage output required")
    output.mkdir(mode=0o700, parents=True)
    options = {
        key.replace("_", "-"): str(value)
        for key, value in vars(args).items()
        if key not in {"output", "defer_replay"} and value is not None
    }
    runs, repairs = run(output, options)
    requests = pending_requests(runs)
    runner.write_json(output / "owner-requests.json", requests)
    if not args.defer_replay:
        distinct = {
            str(Path(record["report"]).resolve()): record
            for record in [*(r["assessment"] for r in runs), *repairs.values()]
        }
        for record in distinct.values():
            if not runner.matrix_replay(record):
                raise ValueError("ordinary leakage replay failed")
    verification = "deferred" if args.defer_replay else "ordinary-replay-verified"
    result = {
        "runs": runs,
        "source_repairs": repairs,
        "pending_requests": requests,
        "verification": verification,
        "qualification": "pending-owner-and-independent-evidence",
    }
    (output / "leakage-results.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                "result": str(output / "leakage-results.json"),
                "owner_requests": str(output / "owner-requests.json"),
                "pending_requests": requests["exact_request_count"],
                "replay": "deferred" if args.defer_replay else "verified",
                "numeric_targets_not_asserted": True,
            }
        )
    )
    return 1 if requests["exact_request_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
