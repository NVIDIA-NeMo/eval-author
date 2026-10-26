# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate supplied values and format literal conversation content without I/O."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import quote, urlsplit

import conversation_templates as shared


def checklist(rows: list[dict]) -> str:
    return "\n".join(
        f"- [{'x' if row['complete'] else ' '}] **{row['label']}**"
        + (f" — **{shared.CURRENT_STEP}**" if row["current"] else "")
        for row in rows
    )


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def literal(value: object, label: str, *, table: bool = False) -> str:
    """Keep supplied prose inside its cell/paragraph, never as Markdown structure."""
    value = text(value, label)
    lines = [" ".join(line.split()) for line in value.split("\n")]
    value = "\n".join(lines) if table else " ".join(line for line in lines if line)
    value = html.escape(value, quote=False)
    value = re.sub(r"([\\`*\[\]_{}#!])", r"\\\1", value).replace("|", "&#124;")
    value = re.sub(r"^([-+]|[0-9]+[.)])(?= )", r"\\\1", value)
    return value.replace("\n", "<br>")


def mapping(value: object, label: str, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError(f"{label} must be an object with only {', '.join(sorted(keys))}")
    return value


def json_context(value: object) -> str:
    """Keep supplied JSON data inside an enclosing Markdown code fence."""
    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
        .replace("`", "\\u0060")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def link(target: object, label: str, *, fragment: object | None = None) -> str:
    """Format a caller-verified destination; never resolve or inspect it here."""
    target = text(target, "link")
    if any(ord(char) < 32 or char in "<>" for char in target):
        raise ValueError("Links must not contain control characters or angle brackets")
    parsed = urlsplit(target)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Links require an HTTP(S) URL or an absolute local path")
        target = quote(target, safe="/:#?&=%@+;,")
    else:
        if not target.startswith("/") or target.startswith("//"):
            raise ValueError("Local links require a supplied absolute path")
        target = quote(target, safe="/")
    if fragment is not None:
        anchor = text(fragment, "link fragment")
        if any(char.isspace() or ord(char) < 32 or char in "<>#" for char in anchor):
            raise ValueError("Link fragments must be a single verified anchor without a leading #")
        if parsed.scheme and parsed.fragment:
            raise ValueError("Supply a link fragment separately from the URL fragment")
        target += "#" + quote(anchor, safe="-._~:")
    return f"[{literal(label, 'link label')}](<{target}>)"
