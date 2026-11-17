# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate experiment configs and copy their selected inputs."""

import argparse
from pathlib import Path

from metric_benchmark.config import read_yaml
from metric_benchmark.generation import Selection, generate_experiment
from metric_benchmark.io import cli
from pydantic import TypeAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-source", type=Path, required=True, help="directory containing cases.yml and prepared inputs"
    )
    parser.add_argument("--execution", type=Path, required=True, help="execution settings YAML")
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new experiment directory outside the source")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--selection", type=Path, help="YAML list of case selections with optional mutant/task IDs and analysis subsets"
    )
    selection.add_argument(
        "--case", action="append", dest="case_ids", help="include a complete case; repeat to combine cases"
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        help="analysis YAML for all source cases before selection (default: source analysis.yml); references use the source root",
    )
    parser.add_argument(
        "--agent-settings", type=Path, help="Harbor configuration overrides applied to every included agent"
    )

    def run(args: argparse.Namespace) -> None:
        result = generate_experiment(
            args.benchmark_source,
            args.output,
            execution=args.execution,
            benchmark_id=args.benchmark_id,
            selection=TypeAdapter(list[Selection]).validate_python(read_yaml(args.selection))
            if args.selection
            else None,
            case_ids=args.case_ids,
            analysis=args.analysis,
            agent_settings=args.agent_settings,
        )
        print(result.model_dump_json(indent=2))

    cli(parser, run)


if __name__ == "__main__":
    main()
