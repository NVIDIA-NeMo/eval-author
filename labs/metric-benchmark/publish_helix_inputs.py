# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish prepared task and agent bundles and request their Helix image builds."""

import argparse
from pathlib import Path

from metric_benchmark.config import HelixExecution, load_execution
from metric_benchmark.helix_publication import load_publication_settings, plan_publication, publish_helix_inputs
from metric_benchmark.io import cli, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context")
    parser.add_argument("--plan-only", action="store_true")

    def run(args):
        loaded = load_execution(args.cases, args.execution)
        if not isinstance(loaded.settings, HelixExecution):
            raise ValueError("publication requires backend: helix")
        plan = plan_publication(loaded.inputs, loaded.settings, load_publication_settings(args.publication))
        if args.plan_only:
            write_json(args.output / "publication.json", plan)
            return
        from metric_benchmark.helix_publisher import HelixPublisher

        client = HelixPublisher.from_context(loaded.settings.workspace, context=args.context)
        try:
            plan.execution = client.resolve_execution(plan.execution)
            publish_helix_inputs(plan, args.output, client)
        finally:
            client.client.close()

    cli(parser, run)


if __name__ == "__main__":
    main()
