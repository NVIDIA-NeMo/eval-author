# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""TASTE hints and representative recovery for an externally executed Stage 3."""

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "taste"
sys.path.insert(0, str(VENDOR))


def _read(path):
    return json.loads(Path(path).read_text())


def _hints(args):
    from src.common.partial_coverage_hints import ActionHints

    candidate = _read(args.candidate)
    if not isinstance(candidate, dict) or not isinstance(candidate.get("reference_actions"), list):
        raise ValueError("Candidate requires a reference_actions array")
    actions = candidate["reference_actions"]
    for action in actions:
        if (
            not isinstance(action, dict)
            or not isinstance(action.get("name"), str)
            or not isinstance(action.get("arguments"), dict)
            or not isinstance(action.get("requestor", "assistant"), str)
        ):
            raise ValueError("Reference actions require a tool name, arguments object and optional requestor")
    random.seed(args.seed)
    hints = ActionHints(
        actions, Path(args.policy).read_text(), coverage_p=args.coverage_p, shuffle=args.shuffle
    ).system_prompt
    return {
        "schema": "taste.hints.v1",
        "hints": hints,
        "reference_actions": actions,
        "hint_policy": {"coverage_p": args.coverage_p, "shuffle": args.shuffle, "seed": args.seed},
    }


def _outcomes(records, entries):
    if not isinstance(records, list):
        raise ValueError("Outcomes must be a list with one record per usable cluster")
    by_cluster = {}
    for record in records:
        if not isinstance(record, dict) or type(record.get("cluster_idx")) is not int:
            raise ValueError("Every outcome requires an integer cluster_idx")
        index = record["cluster_idx"]
        if index in by_cluster:
            raise ValueError(f"Duplicate outcome for cluster {index}")
        sequence = record.get("action_sequence")
        if (
            type(record.get("success")) is not bool
            or not isinstance(sequence, list)
            or not all(isinstance(name, str) for name in sequence)
        ):
            raise ValueError("Every outcome requires boolean success and its effective action_sequence")
        by_cluster[index] = record
    if set(by_cluster) != {entry["cluster_idx"] for entry in entries}:
        raise ValueError("Outcomes must cover exactly the usable clusters")
    return [by_cluster[entry["cluster_idx"]] for entry in entries]


def _recover(args):
    from src.common.domain_config import DomainConfig
    from src.common.sampler.action_sequence_clusterer import load_validated_clusters
    from src.stage3_tasks.generate_tasks import _build_entries_from_clusters, select_recovery_representatives

    if args.pool_size <= 0 or args.temperature <= 0:
        raise ValueError("Recovery pool size and temperature must be positive")
    clusters_path = Path(args.clusters).resolve()
    outcome_records = _read(args.outcomes)
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("Recovery workspace must be the existing TASTE run directory")
    os.chdir(workspace)
    artifact = load_validated_clusters(str(clusters_path))
    config = DomainConfig.from_spec(artifact["backend_spec"])
    entries = _build_entries_from_clusters(str(clusters_path))
    outcomes = _outcomes(outcome_records, entries)
    failed = [index for index, outcome in enumerate(outcomes) if not outcome["success"]]
    recovery = select_recovery_representatives(
        failed,
        entries,
        outcomes,
        str(clusters_path),
        config.domain,
        args.model_name,
        recluster_pool_size=args.pool_size,
        recluster_temperature=args.temperature,
        domain_config=config,
    )
    return {
        "schema": "taste.recovery.v1",
        "backend_spec": config.to_spec(),
        "clusters_sha256": hashlib.sha256(clusters_path.read_bytes()).hexdigest(),
        "settings": {"model_name": args.model_name, "pool_size": args.pool_size, "temperature": args.temperature},
        **recovery,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    hints = commands.add_parser("hints", help="Build reproducible private hints without calling a model or service")
    hints.add_argument("--candidate", required=True)
    hints.add_argument("--policy", required=True)
    hints.add_argument("--coverage-p", type=float, default=0.33)
    hints.add_argument("--shuffle", action=argparse.BooleanOptionalAction, default=True)
    hints.add_argument("--seed", type=int, required=True)
    hints.add_argument("--output", required=True)
    recover = commands.add_parser("recover", help="Select one replacement batch using sequence plausibility calls")
    recover.add_argument("--clusters", required=True)
    recover.add_argument("--outcomes", required=True)
    recover.add_argument("--workspace", required=True)
    recover.add_argument("--model-name", required=True)
    recover.add_argument("--pool-size", type=int, default=500)
    recover.add_argument("--temperature", type=float, default=1.5)
    recover.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    result = _hints(args) if args.command == "hints" else _recover(args)
    from src.stage3_tasks.task_set import atomic_write_text

    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output, json.dumps(result, indent=2) + "\n")
    print(str(output))


if __name__ == "__main__":
    main()
