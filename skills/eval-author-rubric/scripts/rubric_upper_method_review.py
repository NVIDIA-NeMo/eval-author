# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exact-packet upper-method qualification through the existing local operator."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from eval_author_shared.task_evidence import loads
from rubric_general_review import _event, _require, _write, projection_receipt
from rubric_identity import read_selected
from rubric_review_intake import _digest, prepare_task
from rubric_source_reviews import OPERATOR_BOUNDARY, _operator_root

CRITERIA = ("AC-03", "AC-04", "task-contract.clear-contract")
BOUNDARY = (
    "Scoped method qualification for separately implemented native consumer/session controls and public-first "
    "boundary interpretation, with disclosed prior exposure, same model and shared runtime/scorer. "
    "Not blind, unexposed held-out, independent model diversity, source approval or scoring-authority adjudication. "
    "No concurrency, live-provider or model-performance claim."
)


def prepare(task: Path, bundle: Path, registry: Path):
    from upper_anticheat_contract import admitted

    projection = admitted(bundle, registry)
    intake = prepare_task(task)
    request = {
        "schema": "rubric-upper-method-review-request-v1",
        "sources": intake["sources"],
        "definitions": intake["definitions"],
        "native_index_sha256": hashlib.sha256((bundle / "native-index.json").read_bytes()).hexdigest(),
        "exposure_sha256": hashlib.sha256((bundle / "exposure.json").read_bytes()).hexdigest(),
        "observations_sha256": _digest(projection),
        "scopes": {
            key: scope
            for key, scope in zip(
                CRITERIA,
                (
                    "independent consumer-boundary rejection and valid acceptance",
                    "independent order/session/predecessor reset corroboration",
                    "public-first independent solution and valid boundary interpretation",
                ),
                strict=True,
            )
        },
        "boundary": BOUNDARY,
        "excluded": ["AC-01", "AC-02", "task-contract.discoverable-requirements", "task-contract.scoring-authority"],
    }
    request["binding"] = _digest(request)
    return request


def target(request):
    root = _operator_root()
    _require(root is not None)
    return root / (request["binding"] + ".upper-method.json")


def read(request):
    if _operator_root() is None:
        return None
    path = target(request)
    if not path.exists():
        return None
    raw, reason = read_selected(path.parent, path.name)
    _require(reason is None and raw is not None)
    value = loads(raw)
    _require(set(value) == {"schema", "trust_basis", "event"})
    _require(value["schema"] == "rubric-upper-method-review-v1" and value["trust_basis"] == OPERATOR_BOUNDARY)
    return {
        **value,
        "event": _event(request, value["event"]),
        "digest": hashlib.sha256(raw).hexdigest(),
        "revoked": path.with_suffix(".revoked").exists(),
        "local_operator_decision_recorded": True,
        "owner_signature_verified": False,
        "owner_session_decision_verified": False,
    }


def record(request, observed_decision):
    """Only an explicitly trusted caller supplies a genuinely observed answer."""
    path = target(request)
    _require(not path.exists() and not path.with_suffix(".revoked").exists())
    event = _event(request, observed_decision(deepcopy(request)))
    value = {"schema": "rubric-upper-method-review-v1", "trust_basis": OPERATOR_BOUNDARY, "event": event}
    _write(path, (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode())
    actual = read(request)
    _require(actual is not None and actual["event"] == event and actual["revoked"] is False)
    return path


def revoke(request):
    path = target(request)
    _require(path.exists())
    _write(path.with_suffix(".revoked"), b"revoked\n")
    _require(read(request)["revoked"] is True)


def automatic(bundle, projection):
    """Inspect exact native boundaries in addition to owning panel admission."""
    from upper_anticheat_contract import CASES, UpperPanel

    raw, reason = read_selected(bundle, "native-index.json")
    _require(reason is None and raw is not None)
    panel = UpperPanel(bundle, loads(raw))
    rows = {r["case"]: r for r in projection["controls"]}
    base = projection["status"] == "pass" and set(rows) == set(CASES)
    agents, verifiers, trials = {}, {}, {}
    for case in CASES:
        trial = bundle / "trials" / case
        agents[case] = panel.object(trial / "agent/upper-agent.json")
        verifiers[case] = panel.object(trial / "verifier/upper-verifier.json")
        trials[case] = panel.object(trial / "result.json")
    healthy = base and all(t["exception_info"] is None for t in trials.values())
    valid = rows["valid-predecessor"]["checks"]
    valid_boundary = (
        healthy
        and valid["accepted"] is True
        and valid["health"] == "ok"
        and valid["total_credit"] == 8
        and valid["max_credit"] == 9
        and valid["checks"]["completion_signal"] == 0
        and all(v == 1 for k, v in valid["checks"].items() if k != "completion_signal")
    )
    consumer = (
        valid_boundary
        and all(
            set(v["access"]["writes"]) == {"/logs/verifier/reward.txt", "/logs/verifier/reward.json"}
            and v["access"]["uid"] == 65534
            and all(w["status"] == "written" for w in v["access"]["writes"].values())
            and v["consumer"]["rewards"]["reward"] == rows[case]["checks"]["total_credit"] / 9
            for case, v in verifiers.items()
        )
        and all(rows[c]["reward"] == 0 for c in ("target-alone", "target-after", "unlisted-approver"))
    )
    surfaces = {
        "/work/upper-sentinel.json",
        "/work/.cache/upper-sentinel.json",
        "/tmp/upper-sentinel.json",
        "/logs/verifier/reward.txt",
        "/logs/verifier/reward.json",
    }
    reset = (
        healthy
        and all(
            set(a["start_files"]) == surfaces
            and all(v is None for v in a["start_files"].values())
            and a["before"] == agents["target-alone"]["before"]
            for a in agents.values()
        )
        and all(agents["valid-predecessor"]["end_files"][p] is not None for p in surfaces if not p.startswith("/logs/"))
        and rows["target-alone"]["checks"] == rows["target-after"]["checks"]
    )
    reset = reset and len({v["runtime"]["container"] for v in verifiers.values()}) == len(CASES)
    candidate = agents["valid-predecessor"]["after"]["value"]["candidate"]
    events = candidate["events"]
    interpretation = (
        valid_boundary
        and candidate["success"] is False
        and [e["kind"] for e in events] == ["approve", "ledger", "refund", "refund"]
        and [e["refund"]["amount_cents"] for e in events if e["kind"] == "refund"] == [1, 7999]
    )
    return {"AC-03": bool(consumer), "AC-04": bool(reset), "task-contract.clear-contract": bool(interpretation)}


def apply(report, request, receipt, gates):
    from upper_anticheat_contract import GAPS

    ref = {"measurement": "upper_method_review", "binding": request["binding"]}
    approved = bool(receipt and not receipt["revoked"])
    # Frozen observation adapters retain shared constant dictionaries. Never
    # mutate their provenance or future admission output through an alias.
    report["measurements"]["upper_anticheat_contract"] = deepcopy(report["measurements"]["upper_anticheat_contract"])
    report["measurements"]["upper_method_review"] = {
        "request": request,
        "receipt": projection_receipt(receipt),
        "automatic": gates,
        "boundary": BOUNDARY,
    }
    for key in CRITERIA:
        row = report["items"][key]
        accepted = approved and receipt["event"]["decisions"][key]["decision"] == "approve"
        blocked = row["score"] != 2 or any(c["outcome"] in {"FAIL", "ERROR", "BLOCKED"} for c in row["check_results"])
        if not (accepted and gates[key]) or blocked:
            continue
        allowed_partial = key == "task-contract.clear-contract"
        if any(
            c["outcome"] != "PASS"
            and not (
                allowed_partial
                and c["id"] == key + ".probe"
                and c["outcome"] == "PARTIAL"
                and c.get("remaining_gap") == GAPS[key]
            )
            for c in row["check_results"]
        ):
            continue
        old_gap = "Independent consumer/reset controls remain absent. This evidence covers the declared local Docker runtime only."
        old_contract = "Independent corroboration is absent. Blind requirement discovery and independently justified native boundary interpretations are not established by owner approval."
        superseded = {GAPS[key], old_gap, old_contract}
        row["historical_upper_method_gaps"] = list(
            dict.fromkeys(
                v for field in ("remaining_gaps", "limitations") for v in row.get(field, []) if v in superseded
            )
        )
        for field in ("remaining_gaps", "limitations", "findings"):
            if isinstance(row.get(field), list):
                row[field] = [v for v in row[field] if not isinstance(v, str) or v not in superseded]
        for finding in row.get("findings", []):
            if isinstance(finding, dict) and finding.get("message") in superseded:
                finding["historical_message"] = finding["message"]
                finding["message"] = (
                    "Historical absence resolved by the current scoped method receipt and native controls."
                )
        row.setdefault("limitations", []).append(BOUNDARY)
        row["upper_method_acceptance"] = {"receipt_digest": receipt["digest"], "binding": request["binding"]}
        row["upper_evidence_summary"] = {
            "AC-03": "Solver-written reward.txt and reward.json discarded; actual consumer rejects three invalid controls and accepts the valid 8/9 neighbor.",
            "AC-04": "Four distinct ordered native sessions; exact initial state and five absent sentinel/reward surfaces; target alone and after predecessor have identical checks.",
            "task-contract.clear-contract": "Public-first cent-split/reversed-ledger-before-refund boundary accepted at 8/9; optional completion omitted. Exposed method explicitly qualified, not blind discovery.",
        }[key]
        for check in row["check_results"]:
            if check.get("remaining_gap") in superseded:
                check["historical_remaining_gap"] = check["remaining_gap"]
                check["remaining_gap"] = None
            if check["method"].lower() == "probe":
                check.update(
                    outcome="PASS",
                    evidence_boundary=BOUNDARY,
                    next_action="Retain the exact native boundary and disclosed method limits.",
                )
                check.setdefault("evidence_refs", []).append(deepcopy(ref))
        row.update(
            score=3,
            evidence_strength="E3",
            status="ASSESSED",
            assessment_state="ASSESSED",
            execution_state="COMPLETED",
            next_action="No further method decision for this exact bounded packet.",
        )
        row.setdefault("evidence_refs", []).append(deepcopy(ref))
        row["upper_anchor_companion"].update(
            independence="Scoped operator-qualified disclosed-exposure method", remaining=None
        )
        report["measurements"]["upper_anticheat_contract"]["remaining_method_decisions"].pop(key, None)
    # Keep raw observation status historical rather than rewriting the native panel.
    report["measurements"]["upper_method_review"]["promoted"] = [
        k for k in CRITERIA if report["items"][k]["score"] == 3
    ]


@contextmanager
def table_hook(bundle: Path, registry: Path):
    import rubric_table
    from upper_anticheat_contract import admitted

    original = rubric_table.measure

    def measured(root, selection):
        task_id = selection["example_assessment"]["task_config_id"]
        artifact = next(a for a in selection["artifacts"] if a["id"] == task_id)
        task = (root / artifact["path"]).parent
        store = _operator_root()
        if store is not None:
            _require(all(not (root / a["path"]).absolute().is_relative_to(store) for a in selection["artifacts"]))
        request = prepare(task, bundle, registry)
        receipt = read(request)
        report = original(root, selection)
        projection = admitted(bundle, registry)
        gates = automatic(bundle, projection)
        apply(report, request, receipt, gates)
        _require(prepare(task, bundle, registry) == request and read(request) == receipt)
        return report

    rubric_table.measure = measured  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        rubric_table.measure = original
