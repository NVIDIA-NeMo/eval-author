# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate supplied values and format literal conversation content without I/O."""

from __future__ import annotations

import html
import re
from urllib.parse import quote, urlsplit


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


def link(target: object, label: str) -> str:
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
    return f"[{literal(label, 'link label')}](<{target}>)"
