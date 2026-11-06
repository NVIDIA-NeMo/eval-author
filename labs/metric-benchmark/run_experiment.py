# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute a configured benchmark through reusable stage libraries."""

import argparse
from pathlib import Path

from metric_benchmark.io import cli
from metric_benchmark.pipeline import run_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results", type=Path, help="existing execution root; skip agent execution")
    parser.add_argument("--cached-only", action="store_true", help="also prohibit new coverage calls")

    def run(args: argparse.Namespace) -> None:
        run_experiment(
            args.benchmark,
            args.run_id,
            args.output,
            results_root=args.results,
            cached_only=args.cached_only,
            allow_unsigned=args.allow_unsigned,
        )

    cli(parser, run)


if __name__ == "__main__":
    main()
