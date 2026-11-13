# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check a selected ETHOS.md's structure and recorded confirmation."""

from __future__ import annotations

import contextlib
import hashlib
import io
import types
from pathlib import Path

import journey_state
from eval_author_shared.task_evidence import loads
from rubric_identity import MAX_SOURCE_BYTES, diagnostic, finish, read_selected, text


def _load_validator(path):
    """Execute the validator from the bytes that are hashed; return (validate, error class, digest)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) > MAX_SOURCE_BYTES:
        return None
    module = types.ModuleType("ethos_write_validator")
    module.__file__ = str(path)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(data, str(path), "exec"), module.__dict__)
    except (Exception, SystemExit):  # Any failure in the external validator leaves validity unknown.
        return None
    validate, error = getattr(module, "validate", None), getattr(module, "EthosError", None)
    if not callable(validate) or not (isinstance(error, type) and issubclass(error, Exception)):
        return None
    return validate, error, hashlib.sha256(data).hexdigest()


def _selected(artifacts, ident, role):
    matches = [row for row in artifacts if isinstance(row, dict) and row.get("id") == ident]
    if len(matches) != 1 or matches[0].get("role") != role or not isinstance(matches[0].get("path"), str):
        raise ValueError("invalid ethos selection")
    return matches[0]


def _read(root, record, prefix, limit=MAX_SOURCE_BYTES):
    """Return (bytes, diagnostics); bytes is None when the read cannot proceed."""
    subject = record["id"]
    if record.get("access") != "available":
        return None, [diagnostic(prefix + "not_available", subject, "unknown")]
    data, reason = read_selected(root, record["path"], limit)
    if reason:
        return None, [diagnostic(prefix + reason, subject, "fail" if reason == "reference_invalid" else "unknown")]
    if record.get("digest") is not None and record["digest"] != hashlib.sha256(data).hexdigest():
        return data, [diagnostic(prefix + "digest_mismatch", subject)]
    return data, []


def _structure(data, validator_path, subject):
    """Run the installed validator on the selected bytes."""
    result = {"validator_digest": None, "valid": None, "error": None, "warnings": []}
    validator = _load_validator(validator_path) if validator_path is not None else None
    if validator is None:
        return result, [diagnostic("ethos.validator_unavailable", subject, "unknown")]
    validate, error, result["validator_digest"] = validator
    try:
        # Match the validator CLI's universal-newline UTF-8 read.
        content = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        result.update(valid=False, error="not UTF-8 text")
        return result, [diagnostic("ethos.invalid", subject)]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            warnings = validate(content)
    except error as exc:
        result.update(valid=False, error=str(exc))
        return result, [diagnostic("ethos.invalid", subject)]
    except (Exception, SystemExit):  # A validator defect is not evidence about the document.
        return result, [diagnostic("ethos.validator_failed", subject, "unknown")]
    result.update(valid=True, warnings=[str(warning) for warning in warnings])
    return result, []


def _confirmation(root, record, ethos, digest):
    """Compare Eval Author's saved Ethos confirmation with the selected document's current bytes."""
    subject = ethos["id"]
    result = {
        "artifact_id": record["id"],
        "status": None,
        "stage": None,
        "confirmed_digest": None,
        "source": None,
        "validation": None,
    }
    data, diagnostics = _read(root, record, "ethos.confirmation_", journey_state.MAX_BYTES)
    if data is None or diagnostics:
        return result, diagnostics
    try:
        state = loads(data.decode("utf-8"))
        journey_state.validate(state)
    except (ValueError, TypeError, UnicodeDecodeError):
        return result, [diagnostic("ethos.confirmation_invalid", record["id"])]
    saved = state["ethos"]
    result.update(
        stage=saved["stage"],
        confirmed_digest=saved["confirmed_digest"],
        source=saved["confirmation_source"],
        validation=saved["validation"],
    )
    if saved["path"] != ethos["path"]:
        status, found = "other_document", diagnostic("ethos.confirmation_other_document", subject, "unknown")
    elif saved["confirmed_digest"] is None:
        status, found = "unconfirmed", diagnostic("ethos.unconfirmed", subject, "unknown")
    elif digest is None:
        status, found = None, None
    elif saved["confirmed_digest"] != digest:
        status, found = "stale", diagnostic("ethos.confirmation_stale", subject)
    elif saved["stage"] != "complete":
        status, found = "under_review", diagnostic("ethos.confirmation_under_review", subject, "unknown")
    else:
        status, found = "confirmed", None
    result["status"] = status
    return result, [found] if found else []


def check_ethos(root, artifacts, selected, validator_path):
    """Check one selected `ethos` artifact.

    Structure uses the ethos-write validator, never a copy. Confirmation, when a
    `journey_state` artifact is selected, compares Eval Author's saved confirmed
    digest with the document's current bytes. A match shows the saved binding,
    not who approved the content or that it states adequate intent. No rubric
    score is assigned.
    """
    if (
        not isinstance(selected, dict)
        or "artifact_id" not in selected
        or selected.keys() - {"artifact_id", "confirmation_artifact_id"}
        or not all(text(value) for value in selected.values())
    ):
        raise ValueError("invalid ethos selection")
    root = Path(root).resolve()
    record = _selected(artifacts, selected["artifact_id"], "ethos")
    confirmation = (
        _selected(artifacts, selected["confirmation_artifact_id"], "journey_state")
        if "confirmation_artifact_id" in selected
        else None
    )
    subject = record["id"]
    result = {"artifact_id": subject, "digest": None, "confirmation": None}
    data, diagnostics = _read(root, record, "ethos.")
    structure = {"validator_digest": None, "valid": None, "error": None, "warnings": []}
    if data is not None:
        result["digest"] = hashlib.sha256(data).hexdigest()
        structure, found = _structure(data, validator_path, subject)
        diagnostics += found
    if confirmation is not None:
        result["confirmation"], found = _confirmation(root, confirmation, record, result["digest"])
        diagnostics += found
    summary = finish([], diagnostics)
    return {**result, **structure, "status": summary["status"], "diagnostics": summary["diagnostics"]}
