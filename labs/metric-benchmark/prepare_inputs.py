# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download registry metadata or prepare task and experiment bundles."""

import argparse
import subprocess
from pathlib import Path

from harbor.constants import DEFAULT_REGISTRY_URL
from metric_benchmark.io import cli
from metric_benchmark.preparation import download_registry, prepare_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    registry = commands.add_parser("registry", help="download a Harbor JSON registry snapshot")
    registry.add_argument("--url", default=DEFAULT_REGISTRY_URL)
    registry.add_argument("--output", type=Path, required=True)
    for name in ("tasks", "benchmark"):
        command = commands.add_parser(name, help=f"prepare a {name} bundle")
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--cached-only", action="store_true", help="verify an existing bundle without downloads")

    def run(args: argparse.Namespace) -> None:
        try:
            if args.operation == "registry":
                download_registry(args.url, args.output)
            else:
                prepare_inputs(
                    args.source, args.output, benchmark=args.operation == "benchmark", cached_only=args.cached_only
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"Git source resolution failed: {error}") from error

    cli(parser, run)


if __name__ == "__main__":
    main()
