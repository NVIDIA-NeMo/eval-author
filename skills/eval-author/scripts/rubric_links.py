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


def check_sourced_approvals(root, approvals: list, decisions: list, identity: dict, sources: dict) -> dict:
    """Bind projected decisions to selected retained JSON records, not authority.

    ``identity`` is an identify() result. ``sources`` maps each projected decision
    ID to exactly {artifact_id, pointer}; pointers use strict JSON Pointer syntax.
    The selected value must contain the existing check_approvals decision fields
    verbatim with no additional fields (contradictory status cannot be ignored).
    Existing audit {digest, source} checkpoints and journey task acceptances do
    not record substitution scope/endpoints: they cannot authorize a substitution.

    Material scope is the complete identity selection minus selected decision
    source artifacts, whose identity role must be ``decision``. Approval binding
    IDs must equal that material selection;
    source artifacts cannot also be substitution endpoints or material bindings.
    Sources are supporting evidence, not recursively bound material. Every file
    and evidence digest is rechecked through the shared bounded identity reader.
    Evidence returns only decision_id, artifact_id, pointer and whole-file digest;
    neither retained content nor provenance text is exported. No state is written.
    """
    from pathlib import Path

    from eval_author_shared.task_evidence import loads
    from rubric_identity import MAX_RECORDS, identify, pointer_tokens, resolve_pointer, validate_json_tree

    diagnostics, evidence = [], []

    def finish():
        return {**_finish(diagnostics), "authority_authenticated": False, "evidence": evidence}

    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("records"), list)
        or identity.get("status") not in ("pass", "fail", "unknown")
        or not isinstance(identity.get("diagnostics"), list)
        or not isinstance(sources, dict)
        or not all(isinstance(rows, list) for rows in (approvals, decisions))
    ):
        diagnostics.append(_diagnostic("approvals.input_invalid", "selection"))
        return finish()
    records = identity["records"]
    for row in identity["diagnostics"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"code", "subject", "status"}
            or not _text(row["code"])
            or not _text(row["subject"])
            or row["status"] not in ("fail", "unknown")
        ):
            diagnostics.append(_diagnostic("approvals.input_invalid", "selection"))
            return finish()
        diagnostics.append(dict(row))
    if identity["status"] != "pass" and not diagnostics:
        diagnostics.append(_diagnostic("approvals.identity_unresolved", "selection", identity["status"]))
    if any(len(rows) > MAX_RECORDS for rows in (records, approvals, decisions, sources)):
        diagnostics.append(_diagnostic("approvals.selection_limit", "selection", "unknown"))
        return finish()
    if not all(_approval_record(row) for row in approvals) or not all(
        _approval_record(row, decision=True) for row in decisions
    ):
        diagnostics.append(_diagnostic("approvals.record_invalid", "selection"))
        return finish()
    try:
        for ident, ref in sources.items():
            if (
                not _text(ident)
                or not isinstance(ref, dict)
                or set(ref) != {"artifact_id", "pointer"}
                or not _text(ref["artifact_id"])
            ):
                raise ValueError("invalid source reference")
            pointer_tokens(ref["pointer"])
    except ValueError:
        diagnostics.append(_diagnostic("approvals.source_invalid", "selection"))
        return finish()

    root = Path(root).resolve()
    reader = _ApprovalSources(root)
    observed = identify(Path(root), records, hasher=reader.digest)
    diagnostics.extend(observed["diagnostics"])
    artifacts = {row["id"]: row for row in observed["records"]}
    supporting = {ref["artifact_id"] for ref in sources.values()}
    material = [row for row in observed["records"] if row["id"] not in supporting]
    raw = check_approvals(approvals, decisions, material)
    diagnostics.extend(raw["diagnostics"])
    decision_ids = {row["id"] for row in decisions}
    if sources.keys() - decision_ids:
        diagnostics.append(_diagnostic("approvals.source_unreferenced", "selection"))
    for approval in approvals:
        if supporting.intersection(approval["bindings"]) or supporting.intersection(approval["substitution"].values()):
            diagnostics.append(_diagnostic("approvals.source_in_material", approval["id"]))
    documents = {}
    for decision in sorted(decisions, key=lambda row: row["id"]):
        ident = decision["id"]
        ref = sources.get(ident)
        if ref is None:
            diagnostics.append(_diagnostic("approvals.source_missing", ident))
            continue
        artifact = artifacts.get(ref["artifact_id"])
        if artifact is None:
            diagnostics.append(_diagnostic("approvals.source_unselected", ident))
            continue
        if artifact["role"] != "decision":
            diagnostics.append(_diagnostic("approvals.source_role_invalid", ident))
            continue
        if artifact["access"] != "available" or artifact["digest"] is None:
            diagnostics.append(_diagnostic("approvals.source_unavailable", ident, "unknown"))
            continue
        if any(row["subject"] == artifact["id"] for row in observed["diagnostics"]):
            continue
        try:
            path = artifact["path"]
            if path not in documents:
                documents[path] = loads(reader.read(path))
                validate_json_tree(documents[path])
            retained, found = resolve_pointer(documents[path], ref["pointer"])
        except (ValueError, TypeError, RecursionError):
            diagnostics.append(_diagnostic("approvals.source_invalid", ident))
            continue
        if not found:
            diagnostics.append(_diagnostic("approvals.source_pointer_missing", ident))
        elif not isinstance(retained, dict) or not decision.keys() <= retained.keys():
            diagnostics.append(_diagnostic("approvals.source_record_incomplete", ident, "unknown"))
        elif not _approval_record(retained, decision=True):
            diagnostics.append(_diagnostic("approvals.source_record_invalid", ident))
        elif any(retained[key] != value for key, value in decision.items()):
            diagnostics.append(_diagnostic("approvals.source_mismatch", ident))
        else:
            evidence.append({"decision_id": ident, **ref, "digest": artifact["digest"]})
    return finish()


class _ApprovalSources:
    """Reuse one bounded snapshot for identity hashing and decision selection."""

    def __init__(self, root):
        from rubric_identity import MAX_TOTAL_BYTES

        self.root = root
        self.remaining = MAX_TOTAL_BYTES
        self.files = {}

    def read(self, path):
        from rubric_identity import MAX_FILES, MAX_SOURCE_BYTES, SelectedReadError, read_charge, read_selected

        if path not in self.files:
            if len(self.files) >= MAX_FILES:
                raise SelectedReadError("file_limit")
            limit = min(MAX_SOURCE_BYTES, self.remaining)
            data, reason = read_selected(self.root, path, limit)
            self.remaining -= read_charge(data, reason, limit)
            if reason == "source_too_large" and limit < MAX_SOURCE_BYTES:
                reason = "total_bytes_limit"
            self.files[path] = data, reason
        data, reason = self.files[path]
        if reason or data is None:
            raise SelectedReadError(reason or "reference_invalid")
        return data

    def digest(self, path):
        import hashlib

        return hashlib.sha256(self.read(path.relative_to(self.root).as_posix())).hexdigest()
