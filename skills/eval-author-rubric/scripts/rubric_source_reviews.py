# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate source reviews and consume scoped owner-qualified alternatives.

V1 attaches unauthenticated judgments. V2 additionally retains the actual received
scope, prior exposure, separation evidence and independently reconstructed source
observations. Do not produce that reconstruction by copying comparison_basis().
Freeze the reconstruction before comparing with the owning assessment.

Only the four SOURCE_CHECKS can use this alternative. The owner must qualify the
exact packet and its documented separation. Trusted orchestration may record an
observed owner decision with explicit consent through operator_decision in
qualify_selected(). Configure EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE separately
from selected evidence and set EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST to
localhost-assessment-operator-v1. This explicitly trusts the assessment operator,
not an independently authenticated actor/event. Same-user arbitrary host code can
alter this store. File modes do not isolate it. Never derive a decision callback
from solver-selected status, JSON or transcripts. Retained replay rereads the
store. revoke_operator_decision() leaves a tombstone and preserves the decision.

The application-controlled session callback and private
EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE remain available for hosts that establish
owner authority at their session boundary. This module supplies no such service.

Prepare an undecided request with --decision-request PACKET. Signing is optional.
For the signed path, the owner supplies a decision and rationale, then signs with
ssh-keygen -Y sign -f OWNER_KEY -n eval-author-source-review DECISION.json.
The administrator sets EVAL_AUTHOR_SOURCE_REVIEW_TRUST to a private directory
containing allowed_signers (principal source-review-owner), PACKET_SHA256.json,
and PACKET_SHA256.json.sig. That directory must be outside selected artifacts.
The ordinary measure.py and retained replay consume and verify these records.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
from eval_author_shared.task_evidence import loads  # noqa: E402
from rubric_evidence_scoring import canonical_definitions  # noqa: E402

if TYPE_CHECKING:
    from rubric_example_assessment import Sources


class ReviewRecord(TypedDict):
    artifact_id: str
    binding_status: str
    authority_authenticated: bool
    independence_established: bool
    reviewer: dict[str, str] | None
    decisions: list[dict[str, Any]]
    acceptance: NotRequired[dict[str, Any]]
    reconstruction: NotRequired[dict[str, Any]]
    scope: NotRequired[dict[str, Any]]


SOURCE_CHECKS = {
    "provenance.task-artifact-access": "provenance.task-artifact-access.human-review",
    "provenance.grading-path": "provenance.grading-path.human-review-2",
    "provenance.grading-dependency-access": "provenance.grading-dependency-access.human-review-2",
    "provenance.declared-configuration": "provenance.declared-configuration.human-review-2",
}


def comparison_basis(row: dict[str, Any]) -> dict[str, Any]:
    """Compare source observations, not the first reviewer's merit or verdict."""
    return deepcopy(
        {
            "subject_id": row["subject_id"],
            "evidence_refs": row["evidence_refs"],
            "deterministic_evidence": [
                {"id": check["id"], "evidence": check["evidence"]}
                for check in row["check_results"]
                if check["method"] == "deterministic"
            ],
        }
    )


def _method_request(document: dict[str, Any], raw: bytes) -> dict[str, Any]:
    method = document["method"]
    _require(
        isinstance(method, dict)
        and set(method)
        == {
            "id",
            "producer_session_id",
            "received_scope",
            "prior_exposure",
            "separation_evidence",
            "unavailable_method_reason",
            "qualification_rationale",
        }
    )
    _require(method["id"] == "independent-source-reconstruction-v1")
    _require(all(_text(value) for key, value in method.items() if key != "prior_exposure"))
    _require(isinstance(method["prior_exposure"], list) and all(_text(v) for v in method["prior_exposure"]))
    _require(document["reviewer"]["kind"] == "agent")
    _require(document["reviewer"]["session_id"] != method["producer_session_id"])
    reconstruction = document["reconstruction"]
    _require(
        isinstance(reconstruction, dict) and bool(reconstruction) and reconstruction.keys() <= SOURCE_CHECKS.keys()
    )
    _require({d["check_id"] for d in document["decisions"]} == {SOURCE_CHECKS[k] for k in reconstruction})
    for result in reconstruction.values():
        _require(isinstance(result, dict) and set(result) == {"independent_result", "differences", "limitations"})
        _require(isinstance(result["independent_result"], dict))
        _require(isinstance(result["differences"], list))
        _require(
            isinstance(result["limitations"], list)
            and bool(result["limitations"])
            and all(_text(v) for v in result["limitations"])
        )
    return {
        "status": "pending_owner_qualification",
        "packet_digest": hashlib.sha256(raw).hexdigest(),
        "method": deepcopy(method),
        "criteria": sorted(reconstruction),
        "decision_required": (
            "Qualify this exact agent reconstruction and documented reviewer separation as the alternative "
            "to the listed independent source-review checks. Adjudicate its source comparison and all differences. "
            "This excludes task-contract authority, scorer correctness, runtime behavior and solver permissions."
        ),
    }


DEFINITION_FILES = (
    "artifact-configuration-provenance.json",
    "task-contract-authority.json",
    "artifact-configuration-provenance.md",
    "task-contract-authority.md",
    "anti-cheat.md",
)
BOUNDARY = (
    "Recorded source review only. Source and quotation bindings do not authenticate "
    "human identity, task-owner authority, independence or semantic correctness. "
    "These decisions cannot assign a score or replace measured checks."
)


def _require(condition: Any) -> None:
    if not condition:
        raise ValueError("invalid source review")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _extended_scope(
    sources: Sources, selector: dict[str, Any], document: dict[str, Any], review_path: str
) -> dict[str, Any]:
    from rubric_combined import project_panels, select_bundle
    from rubric_example_assessment import project as project_example
    from rubric_identity import read_selected

    _require(document["schema"] == "rubric-source-review-v2")
    _method_request(document, json.dumps(document).encode())
    marker = "Frozen reconstruction bindings: "
    separation = document["method"]["separation_evidence"]
    _require(separation.count(marker) == 1)
    bindings = loads(separation.split(marker, 1)[1].encode())
    _require(isinstance(bindings, dict) and "raw-reconstruction.json" in bindings)
    manifest_path = (Path(review_path).parent / "raw-reconstruction.json").as_posix()
    raw, reason = read_selected(sources.root, manifest_path)
    _require(reason is None and raw is not None)
    _require(hashlib.sha256(raw).hexdigest() == bindings["raw-reconstruction.json"])
    inventory = loads(raw)["selected_inventory"]
    _require(isinstance(inventory, list) and bool(inventory))
    indexed = {r["id"]: r for r in sources.identity["records"]}
    frozen_ids = set()
    paths = set()
    for entry in inventory:
        _require(isinstance(entry, dict) and {"id", "path", "role", "digest"} <= entry.keys())
        ident = entry["id"]
        _require(ident in indexed and ident not in frozen_ids and entry["path"] not in paths)
        _require(all(indexed[ident][key] == entry[key] for key in ("id", "path", "role", "digest")))
        _require(entry["role"] != "review" and sources.source(entry["path"]) is not None)
        frozen_ids.add(ident)
        paths.add(entry["path"])
    _require(
        document["sources"]
        == sorted([{"id": r["id"], "digest": r["digest"]} for r in inventory], key=lambda r: r["id"])
    )
    records = sources.identity["records"]
    _require({r["id"] for r in records if r["role"] == "review"} == set(selector.get("review_ids", [])))
    # Physical aliases can alter access policy or hide a replacement.
    identities = [(sources.root / r["path"]).stat() for r in records]
    _require(len({(s.st_dev, s.st_ino) for s in identities}) == len(identities))
    extras = [r for r in records if r["role"] != "review" and r["id"] not in frozen_ids]
    _require(bool(extras))
    task_path = Path(indexed[selector["task_config_id"]]["path"]).parent.as_posix()
    selected_native = selector.get("native_bundle_ids", [selector.get("native_bundle_id")])
    supplemental = []
    for record in extras:
        _require(record["id"] in selected_native and record["role"] == "dependency")
        _require(not Path(record["path"]).is_relative_to(task_path))
        content = sources.source(record["path"])
        _require(content is not None and loads(content).get("schema") == "eval-author.refund-heldout-panel.v1")
        canonical = select_bundle(sources.root, Path(record["path"]).parent.as_posix())
        _require(canonical == {"native_bundle_id": record["id"], "artifacts": [record]})
        panels = project_panels(sources, {"native_bundle_ids": [record["id"]]}, task_path)
        _require(len(panels) == 1 and panels[0]["scope"] == "eval-author.refund-heldout-panel.v1")
        panel = panels[0]
        _require(
            panel["rubric_score"] is None
            and panel["human_decisions"] == []
            and panel["independence_qualified"] is False
        )
        supplemental.append(
            {
                "artifact_id": record["id"],
                "path": record["path"],
                "digest": record["digest"],
                "scope": panel["scope"],
                "review_status": "unreviewed",
                "inherited_authority": False,
                "dissent": deepcopy([c for c in panel["controls"] if c["differences"]]),
            }
        )
    extended_selector = {**selector, "review_ids": []}
    frozen_selector = deepcopy(extended_selector)
    if "native_bundle_ids" in frozen_selector:
        remaining = [ident for ident in frozen_selector["native_bundle_ids"] if ident in frozen_ids]
        if remaining:
            frozen_selector["native_bundle_ids"] = remaining
        else:
            frozen_selector.pop("native_bundle_ids")
    elif frozen_selector.get("native_bundle_id") not in frozen_ids:
        frozen_selector.pop("native_bundle_id", None)
    frozen = project_example(
        sources.root,
        {
            "artifacts": [r for r in records if r["id"] in frozen_ids],
            "example_assessment": frozen_selector,
        },
    )
    extended = project_example(sources.root, {"artifacts": records, "example_assessment": extended_selector})
    frozen_native = frozen.get("native")
    extended_native = extended.get("native")
    frozen_panels = frozen_native.get("panels", [frozen_native]) if frozen_native else []
    extended_panels = extended_native.get("panels", [extended_native]) if extended_native else []
    extra_digests = {r["digest"] for r in extras}
    _require(frozen_panels == [p for p in extended_panels if p["index_digest"] not in extra_digests])
    # Recompute the whole source assessment in both scopes. Do not infer
    # unchanged findings from a matching subset of content hashes.
    omitted = {"native", "source_reviews", "native_execution", "boundary"}
    _require(
        {k: v for k, v in frozen.items() if k not in omitted} == {k: v for k, v in extended.items() if k not in omitted}
    )
    return {
        "kind": "frozen-reviewed-subselection",
        "inventory_digest": bindings["raw-reconstruction.json"],
        "reviewed_artifact_ids": sorted(frozen_ids),
        "supplemental_evidence": supplemental,
        "boundary": "Owner qualification covers only the original source conclusions. Supplemental observations and dissent remain unreviewed and receive no inherited authority.",
    }


def project(sources: Sources, selector: dict[str, Any]) -> dict[str, Any]:
    ids = selector.get("review_ids", [])
    _require(isinstance(ids, list) and all(_text(ident) for ident in ids) and len(set(ids)) == len(ids))
    indexed = {record["id"]: record for record in sources.identity["records"]}
    definitions = canonical_definitions()
    checks = {check["id"] for item in definitions.values() for check in item["checks"]}
    references = Path(__file__).resolve().parents[1] / "references"
    digests = {name: hashlib.sha256((references / name).read_bytes()).hexdigest() for name in DEFINITION_FILES}
    expected = sorted(
        [{"id": record["id"], "digest": record["digest"]} for record in indexed.values() if record["role"] != "review"],
        key=lambda row: row["id"],
    )
    records: list[ReviewRecord] = []
    for ident in ids:
        _require(ident in indexed and indexed[ident]["role"] == "review")
        raw = sources.source(indexed[ident]["path"])
        row: ReviewRecord = {
            "artifact_id": ident,
            "binding_status": "unavailable",
            "authority_authenticated": False,
            "independence_established": False,
            "reviewer": None,
            "decisions": [],
        }
        records.append(row)
        if raw is None:
            continue
        document = loads(raw)
        _require(
            isinstance(document, dict)
            and set(document)
            == {
                "schema",
                "task_config_id",
                "definition_digests",
                "sources",
                "reviewer",
                "decisions",
            }
            | ({"method", "reconstruction"} if document.get("schema") == "rubric-source-review-v2" else set())
        )
        _require(document["schema"] in {"rubric-source-review-v1", "rubric-source-review-v2"})
        _require(document["task_config_id"] == selector["task_config_id"])
        _require(document["definition_digests"] == digests)
        if document["sources"] != expected:
            row["scope"] = _extended_scope(sources, selector, document, indexed[ident]["path"])
        _require(all(record["digest"] for record in expected))
        _require(all(sources.source(indexed[record["id"]]["path"]) is not None for record in expected))
        reviewer = document["reviewer"]
        _require(isinstance(reviewer, dict) and set(reviewer) == {"kind", "identity", "session_id"})
        _require(reviewer["kind"] in ("human", "agent", "tool") and all(_text(value) for value in reviewer.values()))
        decisions = document["decisions"]
        _require(isinstance(decisions, list) and 0 < len(decisions) <= len(checks))
        seen = set()
        for decision in decisions:
            _require(isinstance(decision, dict) and set(decision) == {"check_id", "status", "rationale", "citations"})
            check_id = decision["check_id"]
            _require(_text(check_id) and check_id in checks and check_id not in seen)
            seen.add(check_id)
            _require(decision["status"] in ("supported", "unsupported", "contradicted", "ambiguous"))
            _require(_text(decision["rationale"]))
            citations = decision["citations"]
            _require(isinstance(citations, list) and 0 < len(citations) <= 64)
            for citation in citations:
                _require(
                    isinstance(citation, dict) and set(citation) == {"artifact_id", "start_line", "end_line", "quote"}
                )
                source_id = citation["artifact_id"]
                _require(_text(source_id) and source_id in indexed and indexed[source_id]["role"] != "review")
                _require(source_id in {record["id"] for record in document["sources"]})
                content = sources.source(indexed[source_id]["path"])
                if content is None:
                    raise ValueError("unavailable review citation")
                lines = content.decode().splitlines(keepends=True)
                start, end = citation["start_line"], citation["end_line"]
                _require(type(start) is int and type(end) is int and 1 <= start <= end <= len(lines))
                _require(_text(citation["quote"]) and citation["quote"] == "".join(lines[start - 1 : end]))
        row.update(binding_status="current", reviewer=deepcopy(reviewer), decisions=deepcopy(decisions))
        if document["schema"] == "rubric-source-review-v2":
            row["acceptance"] = _method_request(document, raw)
            admission = _owner_admission(raw, sources)
            if admission is not None:
                row["acceptance"].update(admission)
            row["reconstruction"] = deepcopy(document["reconstruction"])
    qualified_ids = [ident for row in records for ident in row.get("reconstruction", {})]
    _require(len(qualified_ids) == len(set(qualified_ids)))
    return {"status": "unknown", "records": records, "boundary": BOUNDARY}


def owner_decision_request(raw: bytes) -> dict[str, Any]:
    """Prepare one undecided request. The owner supplies scoped intent separately."""
    document = loads(raw)
    _require(isinstance(document, dict) and document.get("schema") == "rubric-source-review-v2")
    request = _method_request(document, raw)
    return {
        "schema": "rubric-source-review-qualification-v1",
        "packet_digest": request["packet_digest"],
        "method": request["method"],
        "criteria": request["criteria"],
        "decision_scope": request["decision_required"],
        "decision": None,
        "rationale": None,
    }


def _session_root() -> Path | None:
    location = os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE")
    if not location:
        return None
    root = Path(location).absolute()
    _require(not any(p.is_symlink() for p in (root, *root.parents)))
    _require(root.is_dir() and root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0)
    return root


def _session_event(raw: bytes, event: Any) -> dict[str, Any]:
    request = owner_decision_request(raw)
    _require(
        isinstance(event, dict)
        and set(event)
        == {
            "session_id",
            "message_id",
            "owner_id",
            "role",
            "purpose",
            "request",
            "decision",
            "rationale",
            "content",
            "save_authorized",
        }
    )
    _require(all(_text(v) for k, v in event.items() if k not in {"request", "save_authorized"}))
    _require(event["save_authorized"] is True)
    _require(event["role"] == "user" and event["purpose"] == "source_review_qualification")
    _require(event["request"] == request and event["decision"] in {"approve", "reject"})
    document = loads(raw)
    _require(event["session_id"] != document["reviewer"]["session_id"])
    return event


def record_session_decision(raw: bytes, owner_event: Callable[[dict[str, Any]], dict[str, Any] | None]) -> Path:
    """Application-only intake after an explicit owner decision in its trusted UI.

    The application supplies the callback and store through trusted configuration.
    It must check owner authority and retrieve the original user event, including
    consent to save it. Never expose this callback or store to solver tools. This
    module cannot authenticate transcript text or infer intent from an execution
    approval. No CLI accepts an event, decision, callback, or store path.
    """
    root = _session_root()
    _require(root is not None)
    request = owner_decision_request(raw)
    event = _session_event(raw, owner_event(deepcopy(request)))
    content = (
        json.dumps({"schema": "rubric-source-review-session-v1", "event": event}, sort_keys=True) + "\n"
    ).encode()
    target = root / (request["packet_digest"] + ".session.json")
    # Append-only per packet. The application must explicitly revoke before replacement.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def _session_admission(raw: bytes, sources: Sources) -> dict[str, Any] | None:
    from rubric_identity import read_selected

    root = _session_root()
    if root is None:
        return None
    _require(all(not (sources.root / r["path"]).absolute().is_relative_to(root) for r in sources.identity["records"]))
    name = hashlib.sha256(raw).hexdigest() + ".session.json"
    if not (root / name).exists():
        return None
    content, reason = read_selected(root, name)
    _require(reason is None and content is not None)
    info = (root / name).lstat()
    _require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0)
    receipt = loads(content)
    _require(isinstance(receipt, dict) and set(receipt) == {"schema", "event"})
    _require(receipt["schema"] == "rubric-source-review-session-v1")
    event = _session_event(raw, receipt["event"])
    return {
        "owner_signature_verified": False,
        "owner_session_decision_verified": True,
        "decision": event["decision"],
        "rationale": event["rationale"],
        "decision_digest": hashlib.sha256(content).hexdigest(),
        "owner_event_ref": {k: event[k] for k in ("owner_id", "session_id", "message_id")},
        "trust_basis": "Application-controlled local session store; the application establishes owner authority and explicit scoped intent. Transcript text is not authentication.",
    }


OPERATOR_TRUST = "localhost-assessment-operator-v1"
OPERATOR_BOUNDARY = (
    "Live owner decision recorded by the assessment operator under explicit localhost trust. "
    "No independently authenticated actor/event or cryptographic authority is claimed. "
    "A same-user process with arbitrary host code can alter the store; file modes are not isolation."
)


def _operator_root() -> Path | None:
    location = os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE")
    if not location:
        return None
    _require(os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST") == OPERATOR_TRUST)
    root = Path(location).absolute()
    _require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)))
    return root


def _operator_event(raw: bytes, event: Any) -> dict[str, Any]:
    _require(
        isinstance(event, dict)
        and set(event) == {"request", "decision", "rationale", "content", "retention_consent", "save_authorized"}
    )
    _require(event["request"] == owner_decision_request(raw))
    _require(event["save_authorized"] is True)
    _require(all(_text(event[k]) for k in ("decision", "rationale", "content", "retention_consent")))
    _require(event["decision"] in {"approve", "reject"})
    return deepcopy(event)


def record_operator_decision(raw: bytes, observed_decision: Callable[[dict[str, Any]], dict[str, Any] | None]) -> Path:
    """Trusted caller only. Record actual scoped intent, never infer approval.

    The caller observes the live owner answer and consent at its orchestration
    boundary. The rationale may reference the exact packet's disclosed method.
    No owner identity, session ID, signature or authenticated receipt is invented.
    This API and its environment are assessment-operator inputs, not solver tools.
    """
    from rubric_identity import read_selected

    root = _operator_root()
    _require(root is not None)
    request = owner_decision_request(raw)
    event = _operator_event(raw, observed_decision(deepcopy(request)))
    record = {"schema": "rubric-source-review-operator-v1", "trust_basis": OPERATOR_BOUNDARY, "event": event}
    content = (json.dumps(record, sort_keys=True) + "\n").encode()
    target = root / (request["packet_digest"] + ".operator.json")
    _require(not target.with_suffix(".revoked").exists())
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    readback, reason = read_selected(root, target.name)
    _require(reason is None and readback == content)
    return target


def revoke_operator_decision(raw: bytes) -> Path:
    """Operator-only revocation. Keep the original record for audit."""
    root = _operator_root()
    _require(root is not None)
    target = root / (owner_decision_request(raw)["packet_digest"] + ".operator.revoked")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(b"revoked\n")
        stream.flush()
        os.fsync(stream.fileno())
    _require(target.read_bytes() == b"revoked\n")
    return target


def _operator_admission(raw: bytes, sources: Sources) -> dict[str, Any] | None:
    from rubric_identity import read_selected

    root = _operator_root()
    if root is None:
        return None
    _require(all(not (sources.root / r["path"]).absolute().is_relative_to(root) for r in sources.identity["records"]))
    name = hashlib.sha256(raw).hexdigest() + ".operator.json"
    if not (root / name).exists():
        return None
    content, reason = read_selected(root, name)
    _require(reason is None and content is not None)
    record = loads(content)
    _require(isinstance(record, dict) and set(record) == {"schema", "trust_basis", "event"})
    _require(record["schema"] == "rubric-source-review-operator-v1" and record["trust_basis"] == OPERATOR_BOUNDARY)
    event = _operator_event(raw, record["event"])
    revoked = (root / name).with_suffix(".revoked").exists()
    return {
        "local_operator_decision_recorded": True,
        "owner_signature_verified": False,
        "owner_session_decision_verified": False,
        "decision": "reject" if revoked else event["decision"],
        "recorded_decision": event["decision"],
        "revoked": revoked,
        "rationale": event["rationale"],
        "content": event["content"],
        "retention_consent": event["retention_consent"],
        "decision_digest": hashlib.sha256(content).hexdigest(),
        "trust_basis": OPERATOR_BOUNDARY,
    }


def _owner_admission(raw: bytes, sources: Sources) -> dict[str, Any] | None:
    admissions = [
        a
        for a in (_session_admission(raw, sources), _signed_admission(raw, sources), _operator_admission(raw, sources))
        if a is not None
    ]
    _require(len({a["decision"] for a in admissions}) <= 1)
    return admissions[0] if admissions else None


def _signed_admission(raw: bytes, sources: Sources) -> dict[str, Any] | None:
    from rubric_identity import read_selected

    location = os.environ.get("EVAL_AUTHOR_SOURCE_REVIEW_TRUST")
    if not location:
        return None
    root = Path(location).absolute()
    _require(
        all(not (sources.root / row["path"]).absolute().is_relative_to(root) for row in sources.identity["records"])
    )
    _require(not any(p.is_symlink() for p in (root, *root.parents)))
    _require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0)
    request = owner_decision_request(raw)
    name = request["packet_digest"] + ".json"
    if not (root / name).exists():
        return None
    buffers = {}
    for key in (name, name + ".sig", "allowed_signers"):
        content, reason = read_selected(root, key)
        _require(reason is None and content is not None)
        buffers[key] = content
    # Verify captured bytes, never mutable trust files or a packet-provided key.
    with tempfile.TemporaryDirectory(prefix="source-review-signature-") as directory:
        scratch = Path(directory)
        for key in (name + ".sig", "allowed_signers"):
            (scratch / key).write_bytes(buffers[key])
        result = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(scratch / "allowed_signers"),
                "-I",
                "source-review-owner",
                "-n",
                "eval-author-source-review",
                "-s",
                str(scratch / (name + ".sig")),
            ],
            input=buffers[name],
            capture_output=True,
            timeout=10,
            check=False,
        )
    _require(result.returncode == 0)
    decision = loads(buffers[name])
    _require(isinstance(decision, dict) and set(decision) == set(request))
    _require(all(decision[k] == v for k, v in request.items() if k not in {"decision", "rationale"}))
    _require(decision["decision"] in {"approve", "reject"} and _text(decision["rationale"]))
    return {
        "owner_signature_verified": True,
        "decision": decision["decision"],
        "rationale": decision["rationale"],
        "decision_digest": hashlib.sha256(buffers[name]).hexdigest(),
        "trust_basis": "Externally configured allowed_signers for source-review-owner; local administrator must establish owner-key identity.",
    }


def _accept(rows: dict[str, Any], measurement: dict[str, Any]) -> None:
    for review in measurement["records"]:
        if "acceptance" not in review:
            continue
        for ident, reconstruction in review["reconstruction"].items():
            if ident not in rows:
                continue
            row = rows[ident]
            acceptance = deepcopy(review["acceptance"])
            if "scope" in review:
                acceptance["scope"] = deepcopy(review["scope"])
            comparable = json.dumps(reconstruction["independent_result"], sort_keys=True) == json.dumps(
                comparison_basis(row), sort_keys=True
            )
            contradictions = [
                {"artifact_id": other["artifact_id"], "decision": deepcopy(d)}
                for other in measurement["records"]
                for d in other["decisions"]
                if d["check_id"].rsplit(".", 1)[0] == ident and d["status"] != "supported"
            ]
            unavailable = [
                other["artifact_id"]
                for other in measurement["records"]
                if other.get("binding_status", "current") != "current"
            ]
            blocked = bool(contradictions or reconstruction["differences"] or not comparable or unavailable)
            acceptance.update(
                unavailable_reviews=unavailable,
                comparison_matches=comparable,
                contradictions=contradictions,
                differences=deepcopy(reconstruction["differences"]),
            )
            if blocked:
                acceptance["status"] = "blocked_comparison"
            elif acceptance.get("decision") == "reject":
                acceptance["status"] = "rejected_by_owner"
            elif (
                acceptance.get("owner_signature_verified")
                or acceptance.get("owner_session_decision_verified")
                or acceptance.get("local_operator_decision_recorded")
            ) and acceptance.get("decision") == "approve":
                acceptance["status"] = "accepted" if row["score"] == 2 else "blocked_level_2"
                if row["score"] == 2:
                    check = next(c for c in row["check_results"] if c["id"] == SOURCE_CHECKS[ident])
                    ref = {
                        "artifact_id": review["artifact_id"],
                        "packet_digest": acceptance["packet_digest"],
                        "decision_digest": acceptance["decision_digest"],
                    }
                    check.update(
                        outcome="PASS",
                        actual_method="qualified_agent_review",
                        remaining_gap=None,
                        evidence_boundary="Owner-qualified independent source reconstruction; agent identity retained.",
                    )
                    check["evidence_refs"].append(ref)
                    row.setdefault("method_qualification_refs", []).append(deepcopy(ref))
                    row.setdefault("approval_refs", []).append(deepcopy(ref))
                    row["source_review_pre_acceptance"] = {
                        "remaining_gaps": deepcopy(row.get("remaining_gaps", [])),
                        "limitations": deepcopy(row.get("limitations", [])),
                    }
                    row["remaining_gaps"] = []
                    row["limitations"] = deepcopy(reconstruction["limitations"])
                    row.update(score=3, evidence_strength="E3")
            row["source_review_acceptance"] = acceptance


def apply(rows: dict[str, Any], measurement: dict[str, Any]) -> None:
    _accept(rows, measurement)
    for review in measurement["records"]:
        for decision in review["decisions"]:
            ident = (
                decision["check_id"].split(":", 1)[0]
                if decision["check_id"].startswith("AC-")
                else decision["check_id"].rsplit(".", 1)[0]
            )
            if ident in rows:
                rows[ident].setdefault("recorded_source_reviews", []).append(
                    {
                        "artifact_id": review["artifact_id"],
                        "reviewer": deepcopy(review["reviewer"]),
                        "decision": deepcopy(decision),
                        "authority_authenticated": False,
                        "independence_established": (
                            rows[ident].get("source_review_acceptance", {}).get("status") == "accepted"
                            and "acceptance" in review
                        ),
                        "boundary": (
                            "Agent reconstruction accepted under the separately recorded owner qualification. "
                            "Reviewer identity is not authenticated by this packet."
                            if rows[ident].get("source_review_acceptance", {}).get("status") == "accepted"
                            and "acceptance" in review
                            else BOUNDARY
                        ),
                    }
                )


def qualify_selected(
    root: Path,
    selection: dict[str, Any],
    owner_event: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    *,
    operator_decision: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Application-only one-skill handoff using the ordinary measurement path.

    Ask only about complete, current reconstructions at the owning level 2.
    The trusted callback must display the exact packet and method, retrieve the
    genuine owner event, and obtain save consent. It cannot be supplied by a
    selection, transcript file or solver tool. Re-measure after intake so the
    normal consumer reads back the decision and enforces all qualification gates.
    """
    import importlib.util

    _require((owner_event is None) != (operator_decision is None))

    from rubric_identity import read_selected

    spec = importlib.util.spec_from_file_location("rubric_review_measure", Path(__file__).with_name("measure.py"))
    if spec is None or spec.loader is None:
        raise ValueError("measurement entry point unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.measure(root, selection)
    records = report["measurements"]["example_assessment"]["source_reviews"]["records"]
    indexed = {r["id"]: r for r in selection["artifacts"]}
    for record in records:
        if "reconstruction" not in record:
            continue
        criteria = list(record["reconstruction"])
        if not all(
            report["items"][ident]["score"] == 2
            and report["items"][ident]["source_review_acceptance"]["status"] == "pending_owner_qualification"
            for ident in criteria
        ):
            continue
        raw, reason = read_selected(root, indexed[record["artifact_id"]]["path"])
        if reason is not None or raw is None:
            raise ValueError("selected review unavailable")
        _require(hashlib.sha256(raw).hexdigest() == record["acceptance"]["packet_digest"])
        if operator_decision is not None:
            record_operator_decision(raw, operator_decision)
        else:
            assert owner_event is not None
            record_session_decision(raw, owner_event)
    return module.measure(root, selection)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from rubric_identity import read_selected

    parser = argparse.ArgumentParser(description="Prepare an unsigned, packet-scoped owner qualification request.")
    parser.add_argument("--decision-request", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        path = args.decision_request.absolute()
        raw, reason = read_selected(path.parent, path.name)
        _require(reason is None and raw is not None)
        print(json.dumps(owner_decision_request(raw), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "invalid", "code": "source_review.invalid"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
