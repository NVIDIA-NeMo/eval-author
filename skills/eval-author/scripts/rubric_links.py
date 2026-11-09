# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure measurements over explicit projections; never discover or write state."""

import re
from collections import Counter, defaultdict

ROLES = {"task", "run", "trace", "tool_call", "result", "dependency", "ethos", "scorer"}
PARENTS = {"run": ("task",), "trace": ("run",), "tool_call": ("trace",), "result": ("run", "tool_call")}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _diagnostic(code, subject, status="fail"):
    return {"code": code, "subject": subject, "status": status}


def _finish(diagnostics):
    rows = sorted({(d["subject"], d["code"], d["status"]) for d in diagnostics})
    return {
        "status": "fail" if any(row[2] == "fail" for row in rows) else "unknown" if rows else "pass",
        "diagnostics": [_diagnostic(code, subject, status) for subject, code, status in rows],
    }


def check_links(nodes: list, links: list) -> dict:
    """Measure a closed, explicit graph, not native trace completeness.

    Nodes are objects with nonempty, globally unique ``id`` and ``role``;
    other artifact fields are ignored. Qualify native step/call IDs before
    projection: native call IDs alone are not globally unique. Supported roles
    are task, run, trace, tool_call, result, dependency, ethos, scorer.
    Links have exactly source, target, relation, all literal strings. Parent
    relations point child -> parent: run/task, trace/run, tool_call/trace,
    result/run AND result/tool_call (a result is a projected tool observation).
    Each required parent has exactly one target. Result and call run ancestry
    must agree. A dependency relation may join any roles and must be acyclic.
    No missing records are inferred; absent required links are failures and an
    empty selection is unknown. Diagnostics are sorted, deduplicated codes.
    """
    diagnostics = []
    if not isinstance(nodes, list) or not isinstance(links, list):
        return _finish([_diagnostic("links.input_invalid", "selection")])
    if not nodes:
        diagnostics.append(_diagnostic("links.selection_missing", "selection", "unknown"))
    indexed = defaultdict(list)
    for node in nodes:
        if (
            not isinstance(node, dict)
            or not _text(node.get("id"))
            or not _text(node.get("role"))
            or node["role"] not in ROLES
        ):
            diagnostics.append(_diagnostic("links.node_invalid", "nodes"))
            continue
        indexed[node["id"]].append(node)
    unique = {}
    for ident, entries in indexed.items():
        if len(entries) != 1:
            diagnostics.append(_diagnostic("links.node_duplicate", ident))
        else:
            unique[ident] = entries[0]
    edges = Counter()
    for link in links:
        if (
            not isinstance(link, dict)
            or set(link) != {"source", "target", "relation"}
            or not all(_text(value) for value in link.values())
            or link["relation"] not in {"task", "run", "trace", "tool_call", "dependency"}
        ):
            diagnostics.append(_diagnostic("links.link_invalid", "links"))
            continue
        edges[(link["source"], link["target"], link["relation"])] += 1
    parents = defaultdict(set)
    dependencies = defaultdict(set)
    for (source, target, relation), count in sorted(edges.items()):
        if count > 1:
            diagnostics.append(_diagnostic("links.link_duplicate", source))
        for side, ident in (("source", source), ("target", target)):
            if ident not in indexed:
                diagnostics.append(_diagnostic(f"links.{side}_missing", ident))
        if source not in unique or target not in unique:
            continue
        if relation == "dependency":
            dependencies[source].add(target)
        elif relation not in PARENTS.get(unique[source]["role"], ()) or unique[target]["role"] != relation:
            diagnostics.append(_diagnostic("links.role_mismatch", source))
        else:
            parents[(source, relation)].add(target)
    for ident, node in unique.items():
        for relation in PARENTS.get(node["role"], ()):
            count = len(parents[(ident, relation)])
            if count != 1:
                code = "links.required_missing" if not count else "links.parent_ambiguous"
                diagnostics.append(_diagnostic(code, ident))

    def parent(ident, relation):
        candidates = parents.get((ident, relation), set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    for ident, node in unique.items():
        if node["role"] == "result":
            run = parent(ident, "run")
            call_run = parent(parent(parent(ident, "tool_call"), "trace"), "run")
            if run is not None and call_run is not None and run != call_run:
                diagnostics.append(_diagnostic("links.scope_mismatch", ident))
    # Iterative reachability avoids recursion limits for deep dependency chains.
    for origin in sorted(dependencies):
        pending, seen = list(dependencies[origin]), set()
        while pending:
            target = pending.pop()
            if target == origin:
                diagnostics.append(_diagnostic("links.dependency_cycle", origin))
                break
            if target not in seen:
                seen.add(target)
                pending.extend(dependencies.get(target, ()))
    return _finish(diagnostics)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _approval_record(row, decision=False):
    fields = {"id", "scope", "substitution", "bindings"}
    fields |= {"outcome", "source"} if decision else {"decision_id"}
    if not isinstance(row, dict) or set(row) != fields:
        return False
    if not all(_text(row[key]) for key in fields - {"substitution", "bindings"}):
        return False
    substitution, bindings = row["substitution"], row["bindings"]
    return (
        isinstance(substitution, dict)
        and set(substitution) == {"from", "to"}
        and all(_text(value) for value in substitution.values())
        and substitution["from"] != substitution["to"]
        and isinstance(bindings, dict)
        and all(_text(key) and _digest(value) for key, value in bindings.items())
    )


def check_approvals(approvals: list, decisions: list, current: list) -> dict:
    """Verify substitution decision references and byte bindings, not authority.

    This is an ephemeral projection of existing decisions, not a new approval
    ledger. Approvals have exactly id, decision_id, scope, substitution, bindings;
    decisions have exactly id, scope, outcome, source, substitution, bindings.
    Text is nonempty and literal. Substitution is {from: artifact_id, to:
    artifact_id}, distinct IDs. Bindings map artifact IDs to raw lowercase
    SHA-256 hex. A referenced decision must say outcome='approved', name the
    identical scope/substitution, and bind the same complete current selection.
    Scope is exactly ``task:<current task id>/substitution``; matching arbitrary
    free-text scope labels alone do not establish task relevance.

    ``current`` is a closed selection of observed artifact records with unique
    id, role, digest; extra identity fields are allowed. Artifact roles are
    nonempty labels, independent of the closed graph-node roles. It must include ethos,
    task, scorer, and both substitution endpoints plus every relevant dependency.
    An explicitly empty dependency set is allowed. Call separately for different
    selection scopes. Missing required roles/observations are unknown. A null
    digest or access other than available is unknown; absent access means the
    supplied digest is an observation. Known contradictory bindings fail.

    All three arguments are lists. Malformed records and duplicate IDs fail;
    empty approvals/current are unknown. No files or clocks are read. Freshness
    means digest equality with supplied current observations, not an age limit.
    The caller must independently establish selection completeness and source
    authenticity; even pass always reports authority_authenticated=False.
    """
    diagnostics = []

    def finish():
        return {**_finish(diagnostics), "authority_authenticated": False}

    if not all(isinstance(rows, list) for rows in (approvals, decisions, current)):
        diagnostics.append(_diagnostic("approvals.input_invalid", "selection"))
        return finish()
    if not approvals:
        diagnostics.append(_diagnostic("approvals.selection_missing", "approvals", "unknown"))
    if not current:
        diagnostics.append(_diagnostic("approvals.current_missing", "current", "unknown"))

    def index(rows, label, valid):
        grouped = defaultdict(list)
        for row in rows:
            if not valid(row):
                diagnostics.append(_diagnostic("approvals.record_invalid", label))
                continue
            grouped[row["id"]].append(row)
        result = {}
        for ident, entries in grouped.items():
            if len(entries) != 1:
                diagnostics.append(_diagnostic("approvals.id_duplicate", ident))
            else:
                result[ident] = entries[0]
        return result

    indexed_approvals = index(approvals, "approvals", _approval_record)
    indexed_decisions = index(decisions, "decisions", lambda row: _approval_record(row, decision=True))
    artifacts = index(
        current,
        "current",
        lambda row: (
            isinstance(row, dict)
            and {"id", "role", "digest"} <= row.keys()
            and _text(row["id"])
            and _text(row["role"])
            and (row["digest"] is None or _digest(row["digest"]))
            and row.get("access", "available") in ("available", "missing", "denied", "unresolved")
        ),
    )
    for role in ("ethos", "task", "scorer"):
        if not any(row["role"] == role for row in artifacts.values()):
            diagnostics.append(_diagnostic("approvals.role_missing", role, "unknown"))
    for ident, artifact in artifacts.items():
        if artifact["digest"] is None or artifact.get("access", "available") != "available":
            diagnostics.append(_diagnostic("approvals.current_unresolved", ident, "unknown"))
    for ident, approval in indexed_approvals.items():
        decision = indexed_decisions.get(approval["decision_id"])
        if decision is None:
            diagnostics.append(_diagnostic("approvals.decision_missing", ident))
            continue
        if decision["outcome"] != "approved":
            diagnostics.append(_diagnostic("approvals.not_approved", ident))
        for field in ("scope", "substitution"):
            if approval[field] != decision[field]:
                diagnostics.append(_diagnostic(f"approvals.{field}_mismatch", ident))
        if approval["bindings"] != decision["bindings"]:
            diagnostics.append(_diagnostic("approvals.binding_mismatch", ident))
        if current:
            task_scopes = {f"task:{key}/substitution" for key, row in artifacts.items() if row["role"] == "task"}
            for record in (approval, decision):
                if task_scopes and record["scope"] not in task_scopes:
                    diagnostics.append(_diagnostic("approvals.scope_unbound", ident))
                if not set(record["substitution"].values()) <= artifacts.keys():
                    diagnostics.append(_diagnostic("approvals.substitution_missing", ident))
                if record["bindings"].keys() != artifacts.keys():
                    diagnostics.append(_diagnostic("approvals.bindings_incomplete", ident))
                for artifact_id, bound_digest in record["bindings"].items():
                    observed = artifacts.get(artifact_id)
                    if (
                        observed is not None
                        and observed["digest"] is not None
                        and observed.get("access", "available") == "available"
                        and observed["digest"] != bound_digest
                    ):
                        diagnostics.append(_diagnostic("approvals.digest_stale", artifact_id))
    return finish()
