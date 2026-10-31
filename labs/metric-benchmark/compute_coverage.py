# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compute coverage for one case from saved baseline evidence."""

import argparse
from pathlib import Path

from metric_benchmark.coverage import invoke_coverage
from metric_benchmark.io import cli, write_json
from metric_benchmark.scoring import load_score_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--case", required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cached-only", action="store_true")

    def run(args: argparse.Namespace) -> None:
        loaded, _, raw, subsets, selection, record_root = load_score_inputs(
            args.cases, args.analysis, args.case, args.results_root, allow_unsigned=args.allow_unsigned
        )
        description = loaded.load_description(args.case)
        write_json(
            args.output,
            invoke_coverage(
                raw,
                subsets,
                selection,
                loaded.settings.coverage,
                description,
                record_root,
                args.work_root,
                cached_only=args.cached_only,
            ),
        )

    cli(parser, run)


if __name__ == "__main__":
    main()
