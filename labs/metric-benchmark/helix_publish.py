# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish agents, unchanged tasks, or both, and save portable references."""

import argparse
import tempfile
from pathlib import Path

from metric_benchmark.config import load_cases
from metric_benchmark.helix_client import HelixDestination
from metric_benchmark.helix_publication import load_publication_settings, plan_publication, publish_helix_inputs
from metric_benchmark.helix_publisher import HelixPublisher
from metric_benchmark.helix_sources import References, combine_references, load_references, resource_selection
from metric_benchmark.io import cli, read_object, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--resources", type=resource_selection, default="agents,tasks")
    parser.add_argument("--references", type=Path)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args):
        previous = load_references(args.references) if args.references else None
        client = HelixPublisher.from_settings(args.client)
        try:
            loaded = load_cases(args.cases)
            plan = plan_publication(
                loaded,
                HelixDestination(base_url=client.base_url, workspace=client.workspace),
                load_publication_settings(args.publication),
                resources=args.resources,
            )
            # Validate merge membership before creating remote resources.
            if previous and (
                previous.publication.definitions != plan.definitions
                or previous.publication.destination != plan.destination
            ):
                raise ValueError("references belong to a different experiment or destination")
            with tempfile.TemporaryDirectory(prefix="helix-publication-") as temporary:
                scratch = Path(temporary)
                bindings = publish_helix_inputs(plan, scratch, client, source_root=loaded.root)
                references = References(
                    publication=plan, bindings=bindings, provenance=read_object(scratch / "provenance.json")
                )
                write_json(args.output, combine_references(previous, references))
        finally:
            client.client.close()

    cli(parser, run)


if __name__ == "__main__":
    main()
