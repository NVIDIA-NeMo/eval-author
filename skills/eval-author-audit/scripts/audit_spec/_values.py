# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared value normalization for audit measurement and reporting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

EVIDENCE_CLASS_ORDER = (
    "agent_under_test",
    "reference_solution",
    "nop",
    "control",
)
EVIDENCE_CLASSES = frozenset(EVIDENCE_CLASS_ORDER)

_HARBOR_CONTROL_IDENTITIES = {
    "harbor.agents.nop:NopAgent": "nop",
    "harbor.agents.oracle:OracleAgent": "reference_solution",
    "nop": "nop",
    "oracle": "reference_solution",
}


def optional_string(value: object) -> str | None:
    """Return stripped non-empty strings and drop every other value."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def dedupe_names(names: Iterable[str]) -> list[str]:
    """Dedupe a name iterable while preserving first-seen order."""
    return list(dict.fromkeys(names))


def subject_evidence_class(subject: dict[str, Any]) -> str | None:
    """Return a validated evidence class stamped on a coverage subject, if any."""
    value = subject.get("evidence_class")
    if isinstance(value, str) and value in EVIDENCE_CLASSES:
        return value
    return None


def harbor_evidence_class_from_result(result: dict[str, Any]) -> str | None:
    """Map Harbor trial result metadata to an evidence class when config.agent is present."""
    config = result.get("config")
    if not isinstance(config, dict):
        return None
    agent = config.get("agent")
    if not isinstance(agent, dict):
        return None
    name, import_path = agent.get("name"), agent.get("import_path")
    for value in (name, import_path):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return None
    if name and import_path:
        return None
    identity = import_path or name
    if not isinstance(identity, str) or not identity.strip():
        return None
    return _HARBOR_CONTROL_IDENTITIES.get(identity, "agent_under_test")
