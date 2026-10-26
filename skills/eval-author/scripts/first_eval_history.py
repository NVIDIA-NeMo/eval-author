# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Immutable creation snapshots with one bounded predecessor reference in current state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from copy import deepcopy
from pathlib import Path

from working_paths import atomic_write, json_bytes, working_path

PREFIX = ".eval-author/.working/history/create/"
METADATA = {"history", "history_head"}


def validate_reference(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"revision", "path", "digest"}:
        raise ValueError("Creation history requires a content-addressed snapshot reference")
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("Creation history requires a nonnegative revision")
    checksum = value["digest"]
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Creation history requires a lowercase SHA-256 digest")
    if value["path"] != f"{PREFIX}{checksum}.json":
        raise ValueError("Creation history paths must match their content-addressed snapshot digest")


def current(value: dict) -> dict:
    return {key: deepcopy(item) for key, item in value.items() if key not in METADATA}


def inherit(before: dict | None, after: dict) -> None:
    for field, default in (("history", []), ("history_head", None)):
        prior = (before or {}).get(field, default)
        if after.get(field, prior) != prior:
            raise ValueError("Creation history is retained automatically")
        if before and field in before:
            after[field] = deepcopy(prior)


def _path(repo: Path, reference: dict) -> Path:
    validate_reference(reference)
    path = repo
    for part in reference["path"].split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError("Creation history must not traverse a symlink")
    return path


def _read(repo: Path, reference: dict) -> dict:
    from journey_state import MAX_BYTES, _pairs

    path = _path(repo, reference)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ValueError("Creation history snapshot is unsafe or oversized")
            payload = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ValueError("Creation history snapshot is unavailable or unsafe") from exc
    if len(payload) > MAX_BYTES or hashlib.sha256(payload).hexdigest() != reference["digest"]:
        raise ValueError("Creation history snapshot digest does not match its retained reference")
    try:
        value = json.loads(payload, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Creation history snapshot is invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "revision", "record", "previous"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["revision"]) is not int
        or value["revision"] != reference["revision"]
        or not isinstance(value["record"], dict)
        or METADATA & value["record"].keys()
    ):
        raise ValueError("Malformed creation history snapshot")
    previous = value["previous"]
    if previous is not None:
        validate_reference(previous)
        if previous["revision"] >= value["revision"]:
            raise ValueError("Creation history snapshot revisions must precede their successor")
    return value


def snapshots(repo: Path, value: dict | None):
    for item in (value or {}).get("history", []):
        yield item["record"]
    head = (value or {}).get("history_head")
    while head is not None:
        envelope = _read(repo, head)
        yield envelope["record"]
        head = envelope["previous"]


def _write(repo: Path, revision: int, record: dict, previous: dict | None) -> dict:
    from journey_state import MAX_BYTES

    if previous is not None and previous["revision"] >= revision:
        raise ValueError("Creation history snapshot revisions must precede their successor")
    envelope = {"schema_version": 1, "revision": revision, "record": current(record), "previous": previous}
    payload = json_bytes(envelope)
    if len(payload) > MAX_BYTES:
        raise ValueError("Creation history snapshot is too large; link domain artifacts instead of embedding them")
    checksum = hashlib.sha256(payload).hexdigest()
    reference = {"revision": revision, "path": f"{PREFIX}{checksum}.json", "digest": checksum}
    path = working_path(repo, "history", "create", f"{checksum}.json")
    if path.exists():
        if _read(repo, reference) != envelope:
            raise ValueError("Creation history cannot be rewritten")
    else:
        atomic_write(path, payload)
    return reference


def retain(repo: Path, before: dict | None, after: dict, revision: int) -> None:
    if before is None:
        return
    head = before.get("history_head")
    # Validate the retained chain even when this mutation adds no native progress.
    for _ in snapshots(repo, before):
        pass
    if head is not None and head["revision"] >= revision:
        raise ValueError("Creation history must precede the current state revision")
    for item in before.get("history", []):
        if item["revision"] >= revision:
            raise ValueError("Creation history must precede the current state revision")
        if head is not None and item["revision"] <= head["revision"]:
            raise ValueError("Creation history snapshot revisions must retain their order")
        head = _write(repo, item["revision"], item["record"], head)
    if current(before) != current(after):
        head = _write(repo, revision, before, head)
    after.pop("history", None)
    if head is not None:
        after["history_head"] = head
