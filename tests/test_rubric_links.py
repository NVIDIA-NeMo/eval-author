# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts/rubric_links.py"


def checker():
    assert SCRIPT.is_file(), "The reference-integrity measurement is not implemented"
    spec = importlib.util.spec_from_file_location("rubric_links", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def graph():
    nodes = [
        {"id": name, "role": role}
        for name, role in (
            ("t", "task"),
            ("r", "run"),
            ("tr", "trace"),
            ("c", "tool_call"),
            ("out", "result"),
            ("dep", "dependency"),
        )
    ]
    links = [
        {"source": source, "target": target, "relation": relation}
        for source, target, relation in (
            ("r", "t", "task"),
            ("tr", "r", "run"),
            ("c", "tr", "trace"),
            ("out", "r", "run"),
            ("out", "c", "tool_call"),
            ("t", "dep", "dependency"),
        )
    ]
    return nodes, links


def test_explicit_graph_passes_without_mutating_inputs():
    nodes, links = graph()
    before = copy.deepcopy((nodes, links))
    result = checker().check_links(nodes, links)
    assert result == {"status": "pass", "diagnostics": []}
    assert (nodes, links) == before


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing_target", "links.target_missing"),
        ("duplicate_node", "links.node_duplicate"),
        ("duplicate_link", "links.link_duplicate"),
        ("missing_parent", "links.required_missing"),
        ("wrong_role", "links.role_mismatch"),
        ("ambiguous_parent", "links.parent_ambiguous"),
        ("cross_run", "links.scope_mismatch"),
        ("cycle", "links.dependency_cycle"),
    ],
)
def test_graph_mutations_fail(mutation, code):
    nodes, links = graph()
    if mutation == "missing_target":
        links[0]["target"] = "absent"
    elif mutation == "duplicate_node":
        nodes.append(copy.deepcopy(nodes[0]))
    elif mutation == "duplicate_link":
        links.append(copy.deepcopy(links[0]))
    elif mutation == "missing_parent":
        links.pop(2)
    elif mutation == "wrong_role":
        links[0]["target"] = "dep"
    elif mutation == "ambiguous_parent":
        nodes.append({"id": "t2", "role": "task"})
        links.append({"source": "r", "target": "t2", "relation": "task"})
    elif mutation == "cross_run":
        nodes.append({"id": "r2", "role": "run"})
        links.append({"source": "r2", "target": "t", "relation": "task"})
        links[3]["target"] = "r2"
    elif mutation == "cycle":
        links.append({"source": "dep", "target": "t", "relation": "dependency"})
    result = checker().check_links(nodes, links)
    assert result["status"] == "fail"
    assert code in {item["code"] for item in result["diagnostics"]}
    assert result == checker().check_links(list(reversed(nodes)), list(reversed(links)))


def test_empty_selection_is_unknown():
    assert checker().check_links([], [])["status"] == "unknown"


@pytest.mark.parametrize(
    "nodes,links",
    [
        (None, []),
        ([], None),
        ([None], []),
        ([{"id": [], "role": "task"}], []),
        ([{"id": "t", "role": []}], []),
        (graph()[0], [{"source": "r", "target": [], "relation": "task"}]),
        (graph()[0], [{"source": "r", "target": "t", "relation": "typo"}]),
    ],
)
def test_malformed_graph_fails_without_exception(nodes, links):
    assert checker().check_links(nodes, links)["status"] == "fail"


def approval_case():
    current = [
        {"id": ident, "role": role, "digest": digit * 64}
        for ident, role, digit in (
            ("ethos", "ethos", "a"),
            ("t", "task", "b"),
            ("s", "scorer", "c"),
            ("old", "dependency", "d"),
            ("new", "dependency", "e"),
        )
    ]
    bindings = {row["id"]: row["digest"] for row in current}
    approval = {
        "id": "a1",
        "decision_id": "d1",
        "scope": "task:t/substitution",
        "substitution": {"from": "old", "to": "new"},
        "bindings": bindings,
    }
    decision = {
        "id": "d1",
        "scope": approval["scope"],
        "outcome": "approved",
        "source": "user:turn-17",
        "substitution": copy.deepcopy(approval["substitution"]),
        "bindings": copy.deepcopy(bindings),
    }
    return [approval], [decision], current


def test_approval_binding_pass_is_not_user_authentication():
    data = approval_case()
    before = copy.deepcopy(data)
    module = checker()
    assert hasattr(module, "check_approvals"), "Approval measurement is not implemented"
    result = module.check_approvals(*data)
    assert result == {"status": "pass", "diagnostics": [], "authority_authenticated": False}
    assert data == before


@pytest.mark.parametrize(
    "collection,field,value,code",
    [
        (0, "decision_id", "absent", "approvals.decision_missing"),
        (1, "scope", "other-task", "approvals.scope_mismatch"),
        (1, "outcome", "rejected", "approvals.not_approved"),
        (1, "source", "", "approvals.record_invalid"),
        (1, "substitution", {"from": "new", "to": "old"}, "approvals.substitution_mismatch"),
        (0, "substitution", {"from": "old", "to": "absent"}, "approvals.substitution_missing"),
        (0, "bindings", {"t": "b" * 64}, "approvals.bindings_incomplete"),
        (1, "bindings", {"t": "b" * 64}, "approvals.bindings_incomplete"),
        (0, "bindings", {"t": "sha256:" + "b" * 64}, "approvals.record_invalid"),
        (0, "scope", [], "approvals.record_invalid"),
    ],
)
def test_approval_mutations_fail(collection, field, value, code):
    data = approval_case()
    data[collection][0][field] = value
    result = checker().check_approvals(*data)
    assert result["status"] == "fail"
    assert code in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("artifact", range(5))
def test_every_current_artifact_digest_invalidates_stale_approval(artifact):
    data = approval_case()
    data[2][artifact]["digest"] = "f" * 64
    result = checker().check_approvals(*data)
    assert result["status"] == "fail"
    assert "approvals.digest_stale" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("collection", range(3))
def test_duplicate_approval_decision_or_current_identity_fails(collection):
    data = approval_case()
    data[collection].append(copy.deepcopy(data[collection][0]))
    result = checker().check_approvals(*data)
    assert result["status"] == "fail"
    assert "approvals.id_duplicate" in {item["code"] for item in result["diagnostics"]}
    assert result == checker().check_approvals(*(list(reversed(rows)) for rows in data))


@pytest.mark.parametrize("change", ["no_approvals", "no_current", "missing_role", "digest_unknown", "access_denied"])
def test_incomplete_approval_evidence_is_unknown(change):
    data = approval_case()
    if change == "no_approvals":
        data[0].clear()
    elif change == "no_current":
        data[2].clear()
    elif change == "missing_role":
        data[2][0]["role"] = "dependency"
    elif change == "digest_unknown":
        data[2][0]["digest"] = None
    else:
        data[2][0]["access"] = "denied"
    result = checker().check_approvals(*data)
    assert result["status"] == "unknown"
    assert result["authority_authenticated"] is False


@pytest.mark.parametrize(
    "collection,value",
    [
        (0, None),
        (1, {}),
        (2, "bad"),
        (0, [None]),
        (1, [None]),
        (2, [None]),
        (0, [{"id": []}]),
        (2, [{"id": "ethos", "role": [], "digest": "a" * 64}]),
    ],
)
def test_malformed_approval_inputs_fail_without_exception(collection, value):
    data = list(approval_case())
    data[collection] = value
    assert checker().check_approvals(*data)["status"] == "fail"


def test_matching_decision_scope_must_name_a_current_task():
    data = approval_case()
    for rows in data[:2]:
        rows[0]["scope"] = "task:unrelated/substitution"
    result = checker().check_approvals(*data)
    assert result["status"] == "fail"
    assert "approvals.scope_unbound" in {item["code"] for item in result["diagnostics"]}


def test_new_dependency_is_not_covered_by_old_approval():
    data = approval_case()
    data[2].append({"id": "added", "role": "dependency", "digest": "f" * 64})
    result = checker().check_approvals(*data)
    assert result["status"] == "fail"
    assert "approvals.bindings_incomplete" in {item["code"] for item in result["diagnostics"]}


def test_rewritten_approval_binding_cannot_change_decision_binding():
    data = approval_case()
    data[0][0]["bindings"]["old"] = "f" * 64
    result = checker().check_approvals(*data)
    assert result["status"] == "fail"
    assert "approvals.binding_mismatch" in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("role", ["manifest", "review", "configuration", "judge_prompt", "task_input"])
def test_approval_bindings_accept_artifact_roles_without_expanding_graph_roles(role):
    approvals, decisions, current = approval_case()
    current.append({"id": "supporting", "role": role, "digest": "f" * 64})
    for record in (approvals[0], decisions[0]):
        record["bindings"]["supporting"] = "f" * 64
    result = checker().check_approvals(approvals, decisions, current)
    assert result["status"] == "pass"
    assert result["authority_authenticated"] is False
    assert checker().check_links([{"id": "supporting", "role": role}], [])["status"] == "fail"
    current[-1]["digest"] = "e" * 64
    assert "approvals.digest_stale" in {
        row["code"] for row in checker().check_approvals(approvals, decisions, current)["diagnostics"]
    }
