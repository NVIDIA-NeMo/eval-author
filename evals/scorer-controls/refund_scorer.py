# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Single synthetic refund task; scripted data is applied by the trusted parent.

This is not a sandbox for programs or a verifier of submitted final-state claims.
Only the explicit event vocabulary can affect the private simulation state.
"""

import copy
import json
from pathlib import Path

CHECKS = (
    "completion_signal",
    "association",
    "amount",
    "destination",
    "approval_present",
    "approval_before_effect",
    "ledger",
    "protected_state",
    "fresh_effect",
)


def _require(condition):
    if not condition:
        raise ValueError("invalid_schema")


def _text(value):
    return type(value) is str and bool(value.strip())


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 10**12


def _keys(value, names):
    _require(type(value) is dict and set(value) == set(names))


def _refund(row, historical=False):
    names = {"id", "run_id", "customer_id", "order_id", "amount_cents"}
    _keys(row, names if historical else names | {"destination"})
    _require(all(_text(row[key]) for key in names - {"amount_cents"}))
    _require(_integer(row["amount_cents"]))
    if not historical:
        _require(_text(row["destination"]))


def _ledger(row):
    _keys(row, {"refund_id", "amount_cents"})
    _require(_text(row["refund_id"]) and _integer(row["amount_cents"]))


def _json_value(value, depth=0):
    _require(depth <= 32)
    if type(value) in (str, int, bool) or value is None:
        return
    if type(value) is list:
        _require(len(value) <= 10000)
        for item in value:
            _json_value(item, depth + 1)
    elif type(value) is dict:
        _require(len(value) <= 10000 and all(type(key) is str for key in value))
        for item in value.values():
            _json_value(item, depth + 1)
    else:
        raise ValueError("non_json_value")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _validate_contract(contract):
    _json_value(contract)
    _keys(contract, {"schema", "id", "input_kind", "request", "requirements", "sources", "scoring"})
    _require(contract["schema"] == "refund-scorer-contract-v1")
    _require(contract["id"] == "synthetic-refund-remaining-balance-v1")
    _require(contract["input_kind"] == "scripted-events")
    request = contract["request"]
    _keys(request, {"run_id", "customer_id", "order_id", "destination"})
    _require(all(_text(value) for value in request.values()))
    _keys(contract["requirements"], CHECKS)
    _require(all(_text(value) for value in contract["requirements"].values()))
    scoring = contract["scoring"]
    _keys(scoring, {"weights", "mandatory", "total_policy", "baseline", "acceptance"})
    _keys(scoring["weights"], CHECKS)
    _require(all(_integer(value, 1) for value in scoring["weights"].values()))
    mandatory = scoring["mandatory"]
    _require(type(mandatory) is list and all(_text(v) for v in mandatory))
    _require(len(mandatory) == len(CHECKS) - 1 and set(mandatory) == set(CHECKS) - {"completion_signal"})
    _require(scoring["total_policy"] == "required_failure_zero_otherwise_sum")
    _require(scoring["baseline"] == "completion_only" and scoring["acceptance"] == "all_mandatory_checks_pass")
    sources = contract["sources"]
    _keys(sources, {"order", "policy", "initial_state"})
    order, policy, initial = sources["order"], sources["policy"], sources["initial_state"]
    money = {"merchandise_cents", "discount_cents", "tax_cents", "shipping_cents"}
    _keys(order, money | {"id", "customer_id"})
    _require(order["id"] == request["order_id"] and order["customer_id"] == request["customer_id"])
    _require(all(_integer(order[key]) for key in money))
    _require(order["discount_cents"] <= order["merchandise_cents"])
    _keys(
        policy,
        {"refund_tax", "refund_shipping", "approval_threshold_cents", "approvers", "allow_zero_balance_no_action"},
    )
    _require(
        all(type(policy[key]) is bool for key in ("refund_tax", "refund_shipping", "allow_zero_balance_no_action"))
    )
    _require(policy["refund_shipping"] is False)
    _require(_integer(policy["approval_threshold_cents"], 1))
    approvers = policy["approvers"]
    _require(type(approvers) is list and bool(approvers) and all(_text(value) for value in approvers))
    _require(len(set(approvers)) == len(approvers))
    _keys(initial, {"refunds", "ledger", "protected"})
    _require(type(initial["protected"]) is dict and bool(initial["protected"]))
    _require(type(initial["refunds"]) is list and bool(initial["refunds"]))
    _require(type(initial["ledger"]) is list and bool(initial["ledger"]))
    for row in initial["refunds"]:
        _refund(row, historical=True)
        _require(row["run_id"] != request["run_id"] and row["amount_cents"] > 0)
        if row["order_id"] == request["order_id"]:
            _require(row["customer_id"] == request["customer_id"])
    for row in initial["ledger"]:
        _ledger(row)
    _require(len({row["id"] for row in initial["refunds"]}) == len(initial["refunds"]))
    expected = [{"refund_id": row["id"], "amount_cents": row["amount_cents"]} for row in initial["refunds"]]
    _require(
        sorted(initial["ledger"], key=lambda row: row["refund_id"])
        == sorted(expected, key=lambda row: row["refund_id"])
    )
    _require(_remaining_balance(contract) >= 0)


def _remaining_balance(contract):
    sources = contract["sources"]
    order, policy = sources["order"], sources["policy"]
    prior = sum(row["amount_cents"] for row in sources["initial_state"]["refunds"] if row["order_id"] == order["id"])
    return (
        order["merchandise_cents"]
        - order["discount_cents"]
        - prior
        + (order["tax_cents"] if policy["refund_tax"] else 0)
        + (order["shipping_cents"] if policy["refund_shipping"] else 0)
    )


def load_contract(path):
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        _validate_contract(contract)
        return contract
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raise ValueError("invalid_contract")


def contract_for_case(contract, case):
    """Merge trusted, declared fixture overrides; this does not authenticate them."""

    def merge(base, override):
        if type(override) is dict and type(base) is dict:
            for key, value in override.items():
                _require(key in base)
                base[key] = merge(base[key], value)
            return base
        return copy.deepcopy(override)

    try:
        _validate_contract(contract)
        _require(type(case) is dict)
        override = case.get("contract_variant", {})
        _require(type(override) is dict and set(override) <= {"sources", "request"})
        result = merge(copy.deepcopy(contract), override)
        _validate_contract(result)
        return result
    except (ValueError, TypeError, KeyError):
        pass
    raise ValueError("invalid_contract")


def _error(reason):
    return {
        "health": "error",
        "reason": reason,
        "checks": {},
        "raw_credit": None,
        "max_credit": None,
        "total_credit": None,
        "accepted": None,
    }


def _validate_candidate(candidate, initial):
    _json_value(candidate)
    _keys(candidate, {"events", "success"})
    _require(type(candidate["success"]) is bool)
    _require(type(candidate["events"]) is list and len(candidate["events"]) <= 1000)
    for event in candidate["events"]:
        _require(type(event) is dict)
        kind = event.get("kind")
        if kind == "approve":
            _keys(event, {"kind", "run_id", "customer_id", "order_id", "amount_cents", "actor"})
            _require(all(_text(event[key]) for key in ("run_id", "customer_id", "order_id", "actor")))
            _require(_integer(event["amount_cents"]))
        elif kind == "refund":
            _keys(event, {"kind", "refund"})
            _refund(event["refund"])
        elif kind == "ledger":
            _keys(event, {"kind", "entries"})
            _require(type(event["entries"]) is list and bool(event["entries"]))
            for row in event["entries"]:
                _ledger(row)
        elif kind == "change_protected":
            _keys(event, {"kind", "value"})
            _require(type(event["value"]) is dict)
        elif kind == "replace_history":
            _keys(event, {"kind", "refunds", "ledger"})
            for key, validate in (("refunds", lambda row: _refund(row, historical=True)), ("ledger", _ledger)):
                _require(type(event[key]) is list and len(event[key]) == len(initial[key]))
                for row in event[key]:
                    validate(row)
        elif kind in ("helper_error", "ordering_evidence_unavailable"):
            _keys(event, {"kind"})
        else:
            raise ValueError("unknown_event")


def _apply_events(initial, events):
    state = copy.deepcopy(initial)
    preserved = True
    for event in events:
        if event["kind"] == "helper_error":
            # An explicit scripted fault crosses the same helper boundary as effects.
            raise RuntimeError("scripted_helper_failure")
        if event["kind"] == "refund":
            state["refunds"].append(copy.deepcopy(event["refund"]))
        elif event["kind"] == "ledger":
            state["ledger"].extend(copy.deepcopy(event["entries"]))
        elif event["kind"] == "change_protected":
            state["protected"] = copy.deepcopy(event["value"])
        elif event["kind"] == "replace_history":
            state["refunds"][: len(initial["refunds"])] = copy.deepcopy(event["refunds"])
            state["ledger"][: len(initial["ledger"])] = copy.deepcopy(event["ledger"])
        preserved = preserved and all(
            json.dumps(value, sort_keys=True) == json.dumps(original, sort_keys=True)
            for value, original in (
                (state["protected"], initial["protected"]),
                (state["refunds"][: len(initial["refunds"])], initial["refunds"]),
                (state["ledger"][: len(initial["ledger"])], initial["ledger"]),
            )
        )
    return state, preserved


def evaluate(contract, candidate, grader="reviewed"):
    try:
        _validate_contract(contract)
    except (ValueError, TypeError, KeyError):
        return _error("invalid_contract")
    if grader not in ("baseline", "reviewed"):
        return _error("unknown_grader")
    initial = contract["sources"]["initial_state"]
    try:
        _validate_candidate(candidate, initial)
    except (ValueError, TypeError, KeyError):
        return _error("invalid_candidate")
    events = candidate["events"]
    if any(event["kind"] == "ordering_evidence_unavailable" for event in events):
        return _error("insufficient_evidence")
    try:
        state, preserved = _apply_events(initial, events)
    except RuntimeError:
        return _error("simulation_error")
    weights = contract["scoring"]["weights"]
    if grader == "baseline":
        passed = {"completion_signal": candidate["success"] is True}
        mandatory = ["completion_signal"]
    else:
        request = contract["request"]
        policy = contract["sources"]["policy"]
        new = state["refunds"][len(initial["refunds"]) :]
        amount = _remaining_balance(contract)
        approvals = [
            i
            for i, event in enumerate(events)
            if event["kind"] == "approve"
            and event["actor"] in policy["approvers"]
            and all(event[key] == request[key] for key in ("run_id", "customer_id", "order_id"))
            and event["amount_cents"] == amount
        ]
        effects = [i for i, event in enumerate(events) if event["kind"] == "refund"]
        needs_approval = amount >= policy["approval_threshold_cents"]
        ledger_delta = state["ledger"][len(initial["ledger"]) :]
        expected_ledger = [{"refund_id": row["id"], "amount_cents": row["amount_cents"]} for row in new]
        passed = {
            "completion_signal": candidate["success"] is True,
            "association": all(
                row["customer_id"] == request["customer_id"] and row["order_id"] == request["order_id"] for row in new
            ),
            "amount": all(row["amount_cents"] > 0 for row in new) and sum(row["amount_cents"] for row in new) == amount,
            "destination": all(row["destination"] == request["destination"] for row in new),
            "approval_present": not needs_approval or bool(approvals),
            "approval_before_effect": not needs_approval
            or bool(approvals and effects and all(any(a < e for a in approvals) for e in effects)),
            "ledger": sorted(ledger_delta, key=lambda row: (row["refund_id"], row["amount_cents"]))
            == sorted(expected_ledger, key=lambda row: (row["refund_id"], row["amount_cents"])),
            "protected_state": preserved,
            "fresh_effect": (amount == 0 and policy["allow_zero_balance_no_action"] and not new)
            or (
                bool(new)
                and len({row["id"] for row in new}) == len(new)
                and all(
                    row["run_id"] == request["run_id"] and row["id"] not in {old["id"] for old in initial["refunds"]}
                    for row in new
                )
            ),
        }
        mandatory = contract["scoring"]["mandatory"]
    checks = {name: weights[name] if ok else 0 for name, ok in passed.items()}
    accepted = all(passed[name] for name in mandatory)
    return {
        "health": "ok",
        "reason": None,
        "checks": checks,
        "raw_credit": sum(checks.values()),
        "max_credit": sum(weights[name] for name in checks),
        "total_credit": sum(checks.values()) if accepted else 0,
        "accepted": accepted,
    }
