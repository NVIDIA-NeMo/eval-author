# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Flag Harbor tasks whose agent can reach the repository the task was built from.

This reads task settings only; it never fetches anything. For the task and each
declared step, Harbor applies the step's agent network policy, then the task's
``[agent]`` policy, then the ``[environment]`` policy, which defaults to
``public``. A job config can still add allowed hosts at run time, which this
check can't see.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import TypedDict

from rubric_identity import diagnostic, finish, read_selected
from rubric_layout import join, label_task
from rubric_overlap import task_origin


class Network(TypedDict):
    phase: str
    mode: str
    allowed_hosts: list[str]
    source: str


def _hosts(table: dict) -> list[str]:
    hosts = table.get("allowed_hosts", [])
    # Match Harbor's normalization: trim, lowercase, and drop a trailing dot.
    return sorted(
        {host.strip().lower().rstrip(".") for host in hosts if isinstance(host, str)}
        if isinstance(hosts, list)
        else set()
    )


def _explicit(table: object) -> tuple[str, list[str]] | None:
    if not isinstance(table, dict) or not isinstance(table.get("network_mode"), str):
        return None
    return table["network_mode"], _hosts(table)


def _baseline(document: dict) -> tuple[str, list[str], str]:
    environment = document.get("environment", {})
    if isinstance(environment, dict):
        if isinstance(environment.get("network_mode"), str):
            return environment["network_mode"], _hosts(environment), "environment"
        if environment.get("allow_internet") is False:
            return "no-network", [], "environment"
    return "public", [], "default"


def agent_networks(document: dict) -> list[Network]:
    """Return the agent's effective network policy for the task and for each declared step.

    Harbor applies a step's ``[steps.agent]`` policy first, then the task's
    ``[agent]`` policy, then the ``[environment]`` baseline.
    """
    task_policy = _explicit(document.get("agent"))
    mode, hosts, source = _baseline(document)
    phases: list[Network] = [
        {"phase": "task", "mode": task_policy[0], "allowed_hosts": task_policy[1], "source": "agent"}
        if task_policy
        else {"phase": "task", "mode": mode, "allowed_hosts": hosts, "source": source}
    ]
    steps = document.get("steps", [])
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        step_policy = _explicit(step.get("agent")) if isinstance(step, dict) else None
        name = step.get("name") if isinstance(step, dict) and isinstance(step.get("name"), str) else str(index)
        if step_policy:
            phases.append(
                {"phase": f"step:{name}", "mode": step_policy[0], "allowed_hosts": step_policy[1], "source": "step"}
            )
        else:
            phases.append({**phases[0], "phase": f"step:{name}"})
    return phases


def _reachable(network: Network, host: str) -> bool:
    if network["mode"] == "public":
        return True
    if network["mode"] != "allowlist":
        return False
    return any(host == pattern or fnmatch.fnmatch(host, pattern) for pattern in network["allowed_hosts"])


def check_external(root: Path, selected: object) -> dict:
    """Check one task: ``selected`` is ``{"task": <task directory>}``.

    ``external.source_reachable`` (fail) means the task names the repository and
    commit it was built from, and some agent phase can reach that repository's
    host, where later commits may hold the fix. ``external.network_open``
    (unknown) means the agent has open network access but no source repository
    was identified.
    """
    if not isinstance(selected, dict) or set(selected) != {"task"}:
        raise ValueError("invalid external selection")
    layout = label_task(root, selected)
    task = layout["task"]
    result: dict = {"task": task, "networks": [], "origin_host": None, "source_reachable": None}
    if layout["format"] != "harbor":
        return {**result, **_status([diagnostic("external.task_not_harbor", task, "unknown")])}
    root = Path(root).resolve(strict=True)
    data, _ = read_selected(root, join(task, "task.toml"))
    try:
        document = tomllib.loads(data.decode("utf-8")) if data is not None else None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        document = None
    if document is None:
        return {**result, **_status([diagnostic("external.task_config_invalid", task, "unknown")])}
    networks = agent_networks(document)
    origin, diagnostics = task_origin(root, layout)
    host = origin.split("/", 1)[0] if origin else None
    diagnostics = [{**row, "code": row["code"].replace("overlap.", "external.", 1)} for row in diagnostics]
    result.update(networks=networks, origin_host=host)
    if any(network["mode"] not in ("public", "allowlist", "no-network") for network in networks):
        diagnostics.append(diagnostic("external.network_mode_unknown", task, "unknown"))
    elif host is not None:
        result["source_reachable"] = any(_reachable(network, host) for network in networks)
        if result["source_reachable"]:
            diagnostics.append(diagnostic("external.source_reachable", task))
    if any(network["mode"] == "public" for network in networks) and not result["source_reachable"]:
        diagnostics.append(diagnostic("external.network_open", task, "unknown"))
    return {**result, **_status(diagnostics)}


def _status(diagnostics: list[dict]) -> dict:
    summary = finish([], diagnostics)
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
