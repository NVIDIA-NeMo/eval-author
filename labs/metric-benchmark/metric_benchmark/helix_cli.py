# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Thin entry points for the exploratory Helix execution libraries."""

import argparse
import json
from pathlib import Path

from metric_benchmark.config import load_cases
from metric_benchmark.helix import (
    HelixBindings,
    HelixExecution,
    NotReady,
    Submission,
    download_helix_results,
    load_yaml,
    plan_submission,
    submit_helix_evals,
)
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.io import read_json, write_json


def submit_main():
    parser = argparse.ArgumentParser(description="Submit prepared cases to native Helix taskset runs (prototype)")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--execution", required=True, type=Path)
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context", help="Existing authenticated Helix CLI context")
    parser.add_argument("--allow-unsigned", action="store_true", help="Permit missing pins; verify supplied pins")
    parser.add_argument("--plan-only", action="store_true", help="Write requests without connecting to Helix")
    args = parser.parse_args()
    try:
        plan = plan_submission(
            load_cases(args.cases, allow_unsigned=args.allow_unsigned),
            load_yaml(args.execution, HelixExecution),
            load_yaml(args.bindings, HelixBindings),
            args.run_id,
        )
        if args.plan_only:
            write_json(args.output / "submission.json", plan)
            print(f"Planned {len(plan.runs)} runs; nothing submitted.")
            return
        client = HelixClient.from_context(plan.execution.workspace, context=args.context)
        try:
            accepted = submit_helix_evals(plan, args.output, client)
        finally:
            client.client.close()
        print(json.dumps({"submitted_runs": [row["run_id"] for row in accepted]}))
    except (ValueError, OSError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


def download_main():
    parser = argparse.ArgumentParser(description="Check and collect full Helix task evidence (prototype)")
    parser.add_argument(
        "--submission", required=True, type=Path, help="Submission directory containing submission.json"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context", help="Existing authenticated Helix CLI context")
    args = parser.parse_args()
    try:
        plan = Submission.model_validate(read_json(args.submission / "submission.json"))
        client = HelixClient.from_context(plan.execution.workspace, context=args.context)
        try:
            collection = download_helix_results(args.submission, args.output, client)
        finally:
            client.client.close()
        print(json.dumps({"collected_trials": len(collection["observations"]), "analysis_ready": False}))
    except NotReady as error:
        parser.exit(2, f"{error}\n")
    except (ValueError, OSError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
