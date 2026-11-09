# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate matrix accounting, native Harbor observations, and evidence paths."""

import argparse
from pathlib import Path

from metric_benchmark.config import load_cases
from metric_benchmark.io import cli, write_json
from metric_benchmark.validation import validate_experiment_results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--case", help="validate only this case; default: all configured cases")
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args: argparse.Namespace) -> None:
        loaded = load_cases(args.cases, allow_unsigned=args.allow_unsigned)
        write_json(args.output, validate_experiment_results(loaded, args.results_root, case_id=args.case))

    cli(parser, run)


if __name__ == "__main__":
    main()
