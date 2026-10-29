# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sequential case/agent orchestration of native Harbor jobs."""

import argparse
from pathlib import Path

from metric_benchmark.config import load_execution
from metric_benchmark.execution import execute_benchmark
from metric_benchmark.io import cli


def main() -> None:
    """Parse execution arguments, load the benchmark, and invoke the execution library."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args):
        execute_benchmark(
            load_execution(args.cases, args.execution, allow_unsigned=args.allow_unsigned), args.run_id, args.output
        )

    cli(parser, run)


if __name__ == "__main__":
    main()
