# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check a selected ETHOS.md with the installed ethos-write validator."""

from __future__ import annotations

import hashlib
import types
from pathlib import Path

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
        exec(compile(data, str(path), "exec"), module.__dict__)
    except Exception:  # Any failure in the external validator leaves validity unknown.
        return None
    validate, error = getattr(module, "validate", None), getattr(module, "EthosError", None)
    if not callable(validate) or not (isinstance(error, type) and issubclass(error, Exception)):
        return None
    return validate, error, hashlib.sha256(data).hexdigest()


def check_ethos(root, artifacts, selected, validator_path):
    """Validate one selected `ethos` artifact's current bytes.

    The validator is the ethos-write script, never a copy. Structural validity is
    not confirmed intent or semantic adequacy; no rubric score is assigned.
    """
    if not isinstance(selected, dict) or set(selected) != {"artifact_id"} or not text(selected["artifact_id"]):
        raise ValueError("invalid ethos selection")
    matches = [row for row in artifacts if isinstance(row, dict) and row.get("id") == selected["artifact_id"]]
    if len(matches) != 1 or matches[0].get("role") != "ethos" or not isinstance(matches[0].get("path"), str):
        raise ValueError("invalid ethos selection")
    record = matches[0]
    subject = record["id"]
    result = {
        "artifact_id": subject,
        "digest": None,
        "validator_digest": None,
        "valid": None,
        "error": None,
        "warnings": [],
    }
    if record.get("access") != "available":
        return {**result, **_status([diagnostic("ethos.not_available", subject, "unknown")])}
    data, reason = read_selected(Path(root).resolve(), record["path"])
    if reason:
        return {**result, **_status([diagnostic("ethos." + reason, subject, _severity(reason))])}
    diagnostics = []
    result["digest"] = hashlib.sha256(data).hexdigest()
    if record.get("digest") is not None and record["digest"] != result["digest"]:
        diagnostics.append(diagnostic("ethos.digest_mismatch", subject))
    validator = _load_validator(validator_path) if validator_path is not None else None
    if validator is None:
        diagnostics.append(diagnostic("ethos.validator_unavailable", subject, "unknown"))
        return {**result, **_status(diagnostics)}
    validate, error, result["validator_digest"] = validator
    try:
        # Match the validator CLI's universal-newline UTF-8 read.
        content = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        result.update(valid=False, error="not UTF-8 text")
        diagnostics.append(diagnostic("ethos.invalid", subject))
        return {**result, **_status(diagnostics)}
    try:
        warnings = validate(content)
    except error as exc:
        result.update(valid=False, error=str(exc))
        diagnostics.append(diagnostic("ethos.invalid", subject))
    except Exception:  # A validator defect is not evidence about the document.
        diagnostics.append(diagnostic("ethos.validator_failed", subject, "unknown"))
    else:
        result.update(valid=True, warnings=[str(warning) for warning in warnings])
    return {**result, **_status(diagnostics)}


def _severity(reason):
    return "fail" if reason == "reference_invalid" else "unknown"


def _status(diagnostics):
    summary = finish([], diagnostics)
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
