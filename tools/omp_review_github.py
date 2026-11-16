# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Minimal GitHub REST client shared by the omp review context and posting scripts."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

API_ROOT = "https://api.github.com"
BOT_LOGIN = "github-actions[bot]"
REVIEW_MARKER = "<!-- omp-review head="
FINDING_MARKER = "<!-- omp-review-finding -->"
FAILURE_MARKER = "<!-- omp-review-failure -->"
LABEL = "omp-review"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def _request(method: str, url: str, token: str, body: dict[str, Any] | None) -> tuple[int, Any, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, raw, link = response.status, response.read(), response.headers.get("Link", "")
    except urllib.error.HTTPError as error:
        status, raw, link = error.code, error.read(), ""
    path = url.removeprefix(API_ROOT)
    if status >= 500 or (status >= 400 and status not in {404, 422}):
        raise RuntimeError(f"GitHub {method} {path}: {status}")
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return status, parsed, link


def api(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    status, parsed, _ = _request(method, f"{API_ROOT}{path}", token, body)
    return status, parsed


def paginate(path: str, token: str) -> list[Any]:
    items: list[Any] = []
    url: str | None = f"{API_ROOT}{path}"
    while url:
        status, parsed, link = _request("GET", url, token, None)
        if status != 200 or not isinstance(parsed, list):
            raise RuntimeError(f"GitHub GET {url.removeprefix(API_ROOT)}: {status}")
        items.extend(parsed)
        match = _NEXT_LINK.search(link)
        url = match.group(1) if match else None
    return items
