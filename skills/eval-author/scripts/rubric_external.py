# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Flag Harbor tasks whose agent can reach a published source of the answer.

This reads task settings only; it never fetches anything. Harbor applies the
task's ``[agent]`` network policy when one is set and otherwise the
``[environment]`` policy, which defaults to ``public``. A job config can still add
allowed hosts at run time, which this check can't see.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import TypedDict

from rubric_identity import diagnostic, finish, read_selected
from rubric_layout import label_task
from rubric_overlap import task_origin

PUBLIC_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "huggingface.co", "codeberg.org")


class Network(TypedDict):
    mode: str
    allowed_hosts: list[str]
    source: str


def agent_network(document: dict) -> Network:
    """Return the agent phase's effective network policy from a parsed task.toml."""
    for section in ("agent", "environment"):
        table = document.get(section, {})
        if not isinstance(table, dict):
            continue
        mode = table.get("network_mode")
        if mode is None and section == "environment" and table.get("allow_internet") is False:
            mode = "no-network"
        if isinstance(mode, str):
            hosts = table.get("allowed_hosts", [])
            hosts = [host for host in hosts if isinstance(host, str)] if isinstance(hosts, list) else []
            return {"mode": mode, "allowed_hosts": sorted(hosts), "source": section}
    return {"mode": "public", "allowed_hosts": [], "source": "default"}


def _reachable(network: Network, host: str) -> bool:
    if network["mode"] == "public":
        return True
    if network["mode"] != "allowlist":
        return False
    return any(host == pattern or fnmatch.fnmatch(host, pattern) for pattern in network["allowed_hosts"])


def check_external(root: Path, selected: object) -> dict:
    """Check one task: ``selected`` is ``{"task": <task directory>}``.

    ``external.source_reachable`` (fail) means the task names a repository and
    commit on a public host the agent can reach, where later commits may hold the
    fix. ``external.network_open`` (unknown) means the agent has open network
    access but no published source was identified.
    """
    if not isinstance(selected, dict) or set(selected) != {"task"}:
        raise ValueError("invalid external selection")
    layout = label_task(root, selected)
    task = layout["task"]
    result: dict = {"task": task, "network": None, "origin_host": None, "source_reachable": None}
    if layout["format"] != "harbor":
        return {**result, **_status([diagnostic("external.task_not_harbor", task, "unknown")])}
    root = Path(root).resolve(strict=True)
    data, _ = read_selected(root, f"{task}/task.toml")
    try:
        document = tomllib.loads(data.decode("utf-8")) if data is not None else None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        document = None
    if document is None:
        return {**result, **_status([diagnostic("external.task_config_invalid", task, "unknown")])}
    network = agent_network(document)
    origin, diagnostics = task_origin(root, layout)
    host = origin.split("/", 1)[0] if origin else None
    diagnostics = [{**row, "code": row["code"].replace("overlap.", "external.", 1)} for row in diagnostics]
    result.update(network=network, origin_host=host)
    if network["mode"] not in ("public", "allowlist", "no-network"):
        diagnostics.append(diagnostic("external.network_mode_unknown", task, "unknown"))
    elif host is not None and host in PUBLIC_HOSTS:
        result["source_reachable"] = _reachable(network, host)
        if result["source_reachable"]:
            diagnostics.append(diagnostic("external.source_reachable", task))
    if network["mode"] == "public" and not result["source_reachable"]:
        diagnostics.append(diagnostic("external.network_open", task, "unknown"))
    return {**result, **_status(diagnostics)}


def _status(diagnostics: list[dict]) -> dict:
    summary = finish([], diagnostics)
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
