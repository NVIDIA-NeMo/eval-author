# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compute paired mutant scores for one case from saved execution results."""

import argparse
from pathlib import Path

from metric_benchmark.io import cli, write_json
from metric_benchmark.scoring import compute_mutant_scores, load_score_inputs


def main() -> None:
    """Parse stage arguments and write one case's mutant-score JSON artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--case", required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args: argparse.Namespace) -> None:
        _, _, raw, subsets, selection, _ = load_score_inputs(
            args.cases, args.analysis, args.case, args.results_root, allow_unsigned=args.allow_unsigned
        )
        write_json(args.output, compute_mutant_scores(raw, subsets, selection))

    cli(parser, run)


if __name__ == "__main__":
    main()
