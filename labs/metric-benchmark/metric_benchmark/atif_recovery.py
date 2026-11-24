# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recover empty embedded ATIF children from separately recorded exact-ID traces."""

import copy
from pathlib import Path
from typing import Any

from metric_benchmark.io import digest_file, read_object, write_json
from metric_benchmark.models import resolve_evidence


def recover_embedded_children(native: Path, output: Path) -> Path | None:
    if output.resolve().is_relative_to(native.resolve()) or output.exists():
        raise ValueError("recovery requires a new output directory outside native evidence")
    source = resolve_evidence(native, "agent/trajectory.json")
    trajectory = read_object(source)

    def children_of(node: dict[str, Any]) -> list[dict[str, Any]]:
        children = node.get("subagent_trajectories")
        if children is None:
            return []
        if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
            raise ValueError("embedded child trajectories must be a list of objects")
        return children

    def needs_recovery(node: dict[str, Any]) -> bool:
        children = children_of(node)
        return any(child.get("steps") == [] or needs_recovery(child) for child in children)

    if not needs_recovery(trajectory):
        return None
    root_id = trajectory.get("trajectory_id") or trajectory.get("session_id")
    if not isinstance(root_id, str) or not root_id:
        raise ValueError("root trace requires a trajectory ID for recovery")
    index_path = resolve_evidence(native, "agent/trajectory-index.json")
    index = read_object(index_path)
    recorded: dict[str, tuple[Path, dict[str, Any]]] = {}
    inputs = {"agent/trajectory.json": digest_file(source), "agent/trajectory-index.json": digest_file(index_path)}
    sessions = index.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("child trace inventory requires a sessions list")
    for session in sessions:
        if not isinstance(session, dict) or type(session.get("root")) is not bool:
            raise ValueError("child trace inventory requires explicit boolean root markers")
        if session["root"]:
            continue
        if not isinstance(session.get("path"), str) or not session["path"]:
            raise ValueError("child trace inventory requires a recorded path")
        path = resolve_evidence(native / "agent", session["path"])
        inputs[path.relative_to(native).as_posix()] = digest_file(path)
        child = read_object(path)
        identity = child.get("trajectory_id")
        if not isinstance(identity, str) or not identity or identity in recorded:
            raise ValueError("child trace inventory has absent or ambiguous trajectory IDs")
        recorded[identity] = path, child
    recovered = []

    def restore(node: dict[str, Any], ancestors: set[str]) -> None:
        children = children_of(node)
        for position, stub in enumerate(children):
            identity = stub.get("trajectory_id")
            if not isinstance(identity, str) or not identity or identity in ancestors:
                raise ValueError("embedded child has an absent or cyclic trajectory ID")
            child = stub
            if stub.get("steps") == []:
                match = recorded.get(identity)
                if match is None:
                    raise ValueError("empty embedded child has no exact-ID recorded trace")
                path, candidate = match
                if (
                    not isinstance(candidate.get("steps"), list)
                    or not candidate["steps"]
                    or any(candidate.get(key) != value for key, value in stub.items() if key != "steps")
                ):
                    raise ValueError("recorded child is empty or conflicts with embedded metadata")
                child = copy.deepcopy(candidate)
                children[position] = child
                relative = path.relative_to(native).as_posix()
                inputs[relative] = digest_file(path)
                recovered.append({"trajectory_id": identity, "source": relative})
            restore(child, ancestors | {identity})

    restore(trajectory, {root_id})
    destination = output / "agent/trajectory.json"
    write_json(destination, trajectory)
    write_json(
        output / "recovery.json",
        {
            "operation": "fill_empty_embedded_children_by_exact_trajectory_id_v1",
            "source_root": str(native.resolve()),
            "inputs": inputs,
            "recovered": recovered,
            "output_sha256": digest_file(destination),
        },
    )
    return output
