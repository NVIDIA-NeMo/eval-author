# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only, selected evidence freshness; no discovery or score assignment."""

import hashlib
from collections import Counter
from pathlib import Path

from eval_author_shared import task_evidence as native
from rubric_configuration import MAX_FILES, _read_file
from rubric_configuration import MAX_SOURCE_BYTES as MAX_SOURCE_BYTES
from rubric_identity import DIGEST, SelectedAccess, diagnostic, finish, identify, local_file, resolve_pointer, text


class FreshnessError(ValueError):
    pass


class _Unavailable(Exception):
    pass


def _require(condition):
    if not condition:
        raise FreshnessError("invalid selected binding")


class _Snapshots:
    def __init__(self, root, artifacts=()):
        self.root = root
        self.access = SelectedAccess(artifacts)
        self.files = {}

    def read(self, relative):
        reason = self.access.reason(relative)
        if reason:
            raise _Unavailable(reason)
        if relative not in self.files:
            _require(len(self.files) < MAX_FILES)
            local_file(self.root, relative)
            self.files[relative] = _read_file(self.root, relative)
        return self.files[relative]

    def bound(self, artifact):
        data, reason = self.read(artifact["path"])
        _require(reason is None)
        native.require_binding(hashlib.sha256(data).hexdigest(), artifact["digest"], "selected source changed")
        return data


def _load(root, artifact, snapshots=None):
    return native.loads((snapshots or _Snapshots(root)).bound(artifact))


def _binding_pointer(value, path):
    value, found = resolve_pointer(value, path)
    _require(found)
    return value


def _bound_dependencies(root, entry, selected, validate, snapshots):
    _require(set(entry) == {"id", "kind", "source", "pointer"} and entry["kind"] == "bindings")
    _require(text(entry["source"]))
    result, result_status = validate(entry["id"])
    source, source_status = validate(entry["source"])
    _require(result_status == source_status == "pass")
    retained = _binding_pointer(_load(root, source, snapshots), entry["pointer"])
    _require(isinstance(retained, dict) and set(retained) == {"evidence", "dependencies"})
    native.require_binding(retained["evidence"], {"id": result["id"], "digest": result["digest"]}, "wrong evidence")
    dependencies = retained["dependencies"]
    _require(isinstance(dependencies, list) and bool(dependencies))
    seen = set()
    for old in dependencies:
        _require(isinstance(old, dict) and set(old) == {"id", "digest", "revision", "impact"})
        _require(text(old["id"]) and old["id"] not in seen and old["id"] in selected)
        _require(isinstance(old["digest"], str) and DIGEST.fullmatch(old["digest"]))
        _require(old["revision"] is None or text(old["revision"]))
        _require(old["impact"] in ("material", "unknown"))
        seen.add(old["id"])
    return dependencies


def _native_control(root, entry, selected, validate, row, snapshots):
    from audit_verifier_evidence import Review, inspect_receipt

    _require(set(entry) == {"id", "kind", "task_dir"})
    task = local_file(root, entry["task_dir"])
    _require(task.is_dir())
    artifact, status = validate(entry["id"])
    _require(status == "pass")
    receipt = _load(root, artifact, snapshots)
    _require(isinstance(receipt, dict) and receipt.get("schema") == native.SCHEMA)
    _require(receipt.get("purpose") == "closure")

    class SelectedReview(Review):
        measures_task = False

        def __init__(self):
            self.repo = root
            self.manifests = {}

        def path(self, value):
            path = Path(value)
            path = path if path.is_absolute() else root / path
            _require(".." not in path.parts and path.is_relative_to(root))
            return path

        def task_digest(self, task):
            # Force retained assertion validation without claiming a current tree.
            return "unmeasured"

        def read(self, path):
            path = self.path(path)
            reason = snapshots.access.reason(path.relative_to(root).as_posix())
            if reason:
                raise _Unavailable(reason)
            matches = [
                ident
                for ident, candidates in selected.items()
                if any(text(candidate.get("path")) and root / candidate["path"] == path for candidate in candidates)
            ]
            _require(len(matches) == 1)
            record, state = validate(matches[0])
            _require(state == "pass")
            return snapshots.bound(record)

    review = SelectedReview()
    manifest_path = review.path(receipt["manifest"])
    manifest = review.object(manifest_path)
    # The selected-control validator does not rebuild Gym native attachments.
    contract = manifest.get("contract")
    _require(isinstance(contract, dict) and contract.get("provider") == "harbor")
    try:
        checked = inspect_receipt(review, local_file(root, artifact["path"]), receipt, {task}, verify_historical=True)
    except (AttributeError, OverflowError):
        # Shared native validation assumes object agents and float-sized numbers.
        raise FreshnessError("invalid selected native control") from None
    classification = checked.get("historical_classification", checked["classification"])
    _require(classification in ("passed", "false_rejection", "failed_control"))

    previous = manifest["task_sha256"]
    _require(isinstance(previous, str) and DIGEST.fullmatch(previous))
    row["native_classification"] = classification
    row["lineage"] = [
        {
            "id": entry["task_dir"],
            "old": {"digest": previous, "revision": None},
            "new": {"digest": None, "revision": None},
            "impact": "material",
        }
    ]
    row["status"] = "unresolved"


def check_freshness(root, artifacts, evidence):
    """Derive freshness over explicit source-bound dependencies.

    ``artifacts`` uses rubric_identity's selection records. ``evidence`` entries
    have exactly id (retained artifact), kind='bindings', source (selected JSON
    artifact ID), pointer (JSON pointer). The source object has exactly evidence
    {id,digest}, dependencies [{id,digest,revision,impact}]. Each dependency is
    selected, unique, and material or unknown. These are caller-supplied impact
    declarations, not a new persisted ledger or proof of material completeness.
    Changed ETHOS semantics remain unresolved. No rubric merit is assigned.

    Alternatively, an entry with exactly ``id``, ``kind='native_control'``, and
    ``task_dir`` (root-relative directory) selects an existing Harbor control
    receipt. Its receipt, manifest, and native result must each be explicitly
    selected artifacts. The existing audit selected-control validator checks
    retained assertions without scanning task files. There is no explicit task
    file closure in this selection format, so current task identity is unknown:
    native entries remain unresolved, with a null new digest and no inferred
    changed dependencies. A selected task directory does not authorize discovery.
    Agent/diagnostic runs, Gym attachments, baseline receipts and full closure
    qualification are not supported by this adapter. External dependencies and
    ETHOS require separate bindings; task-tree identity does not establish their
    completeness or semantic impact. No files or native commands are produced.

    ``usable`` means current evidence for review, not a passing task. Retained
    negative native assertions are preserved even when freshness is unknown.
    All reads use no-follow, bounded snapshots (1 MiB per file, 64 distinct
    selected files including explicit evidence references). Identity validation,
    parsing and native assertions share those bytes; this is not an atomic
    multi-file snapshot. Source hashes are not native execution attestation.
    This return value is a disposable derived report, never a replacement for
    retained sources.
    ``criterion_3`` stays ``not_assessed`` even when all bindings are current.
    """
    diagnostics, records = [], []
    root = Path(root).resolve()
    if not isinstance(artifacts, list) or not isinstance(evidence, list):
        return {**finish([], [diagnostic("freshness.input_invalid", "selection")]), "criterion_3": "not_assessed"}
    snapshots = _Snapshots(root, artifacts)
    selected = {}
    for artifact in artifacts:
        if isinstance(artifact, dict) and text(artifact.get("id")):
            selected.setdefault(artifact["id"], []).append(artifact)
    counts = Counter(entry["id"] for entry in evidence if isinstance(entry, dict) and text(entry.get("id")))

    def validate(ident):
        _require(ident in selected and len(selected[ident]) == 1)
        record = selected[ident][0]
        _require(record.get("access") in ("available", "missing", "denied", "unresolved"))
        # Reuse the identity schema, never its unbounded filesystem reads.
        checked = identify(root, [dict(record, access="unresolved")])
        _require(checked["status"] != "fail" and len(checked["records"]) == 1)
        if record["access"] != "available":
            return record, "unknown"
        _require(len(record["evidence"]) <= MAX_FILES)
        status = "pass" if record["digest"] and record["evidence"] else "unknown"
        for ref in [{"path": record["path"], "digest": record["digest"]}, *record["evidence"]]:
            data, reason = snapshots.read(ref["path"])
            if reason in ("artifact_missing", "access_denied"):
                status = "unknown"
                continue
            _require(reason is None)
            if ref["digest"] is not None:
                native.require_binding(hashlib.sha256(data).hexdigest(), ref["digest"], "selected source changed")
        return record, status

    if not evidence:
        diagnostics.append(diagnostic("freshness.selection_missing", "selection", "unknown"))
    for index, entry in enumerate(evidence):
        ident = entry.get("id") if isinstance(entry, dict) else None
        ident = ident if text(ident) else f"evidence:{index}"
        row = {"id": ident, "status": "unresolved", "usable": False, "changed_dependencies": [], "lineage": []}
        try:
            _require(isinstance(entry, dict) and text(entry.get("id")) and counts[ident] == 1)
            unresolved = False
            if entry.get("kind") == "native_control":
                _native_control(root, entry, selected, validate, row, snapshots)
                unresolved = row["status"] == "unresolved"
            else:
                dependencies = _bound_dependencies(root, entry, selected, validate, snapshots)
                for old in sorted(dependencies, key=lambda item: item["id"]):
                    current, status = validate(old["id"])
                    new = {"digest": current["digest"] if status == "pass" else None, "revision": current["revision"]}
                    previous = {"digest": old["digest"], "revision": old["revision"]}
                    row["lineage"].append({"id": old["id"], "old": previous, "new": new, "impact": old["impact"]})
                    if status != "pass":
                        unresolved = True
                    elif previous != new:
                        row["changed_dependencies"].append(old["id"])
                        unresolved |= current["role"] == "ethos" or old["impact"] == "unknown"
                row["status"] = "unresolved" if unresolved else "stale" if row["changed_dependencies"] else "current"
            row["usable"] = row["status"] == "current"
            if not row["usable"]:
                diagnostics.append(diagnostic("freshness." + row["status"], ident, "unknown" if unresolved else "fail"))
        except _Unavailable:
            diagnostics.append(diagnostic("freshness.unresolved", ident, "unknown"))
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            diagnostics.append(diagnostic("freshness.binding_invalid", ident))
        records.append(row)
    return {**finish(records, diagnostics), "criterion_3": "not_assessed"}
