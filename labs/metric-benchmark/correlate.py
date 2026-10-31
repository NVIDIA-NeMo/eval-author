# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tie-aware descriptive Spearman correlation over matched subset scores."""

import argparse
from pathlib import Path

from metric_benchmark.correlation import correlate
from metric_benchmark.coverage import CoverageScores
from metric_benchmark.io import cli, read_json, write_json
from metric_benchmark.scoring import MutantScores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("coverage", "mutant-scores", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--missing-policy", choices=("error", "exclude"), default="error")

    def run(args: argparse.Namespace) -> None:
        write_json(
            args.output,
            correlate(
                CoverageScores.model_validate(read_json(args.coverage)),
                MutantScores.model_validate(read_json(args.mutant_scores)),
                missing_policy=args.missing_policy,
            ),
        )

    cli(parser, run)


if __name__ == "__main__":
    main()
