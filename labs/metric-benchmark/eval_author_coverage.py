# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Author an audit and compute its coverage using Eval Author."""

import argparse
from pathlib import Path

from metric_benchmark.eval_author import compute_audit_coverage, source_digest
from metric_benchmark.io import cli, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--request", type=Path)
    mode.add_argument("--print-source-digest", type=Path, metavar="EVAL_AUTHOR_ROOT")
    parser.add_argument("--output", type=Path)

    def run(args):
        if args.print_source_digest:
            if args.output:
                parser.error("--output is only valid with --request")
            print(source_digest(args.print_source_digest.resolve()))
        else:
            if not args.output:
                parser.error("--request requires --output")
            write_json(args.output, compute_audit_coverage(args.request))

    cli(parser, run)


if __name__ == "__main__":
    main()
