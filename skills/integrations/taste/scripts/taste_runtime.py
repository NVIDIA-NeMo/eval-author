# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run bundled TASTE stages from a prepared input bundle."""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "taste"
sys.path.insert(0, str(VENDOR))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run an upstream stage in a private workspace")
    run.add_argument("--backend-config", required=True)
    run.add_argument("--workspace", required=True)
    run.add_argument("--stage", required=True, choices=["train", "cluster", "generate"])
    run.add_argument("stage_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config_path = Path(args.backend_config).resolve()
    from src.common.domain_config import DomainConfig

    spec = DomainConfig.from_file(config_path).to_spec()
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["TASTE_WORKSPACE"] = str(workspace)
    os.chdir(workspace)
    argv = args.stage_args
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if args.stage == "train":
        argv = ["--backend-config", str(config_path), *argv]
    module = {
        "train": "src.stage1_ngram.initialize_ngram_model",
        "cluster": "src.stage2_cluster.cluster_and_validate",
        "generate": "src.stage3_tasks.generate_tasks",
    }[args.stage]
    if args.stage != "train":
        from src.backends.artifacts import require_same_backend

        option = "--checkpoint" if args.stage == "cluster" else "--clusters"
        value = None
        for index, argument in enumerate(argv):
            if argument.startswith(option + "="):
                value = argument.split("=", 1)[1]
            elif argument == option and index + 1 < len(argv):
                value = argv[index + 1]
        if not value:
            parser.error(f"{args.stage} requires {option}")
        artifact = json.loads(Path(value).read_text())
        saved = artifact.get("backend_spec") or artifact.get("config", {}).get("backend_spec")
        if saved is None:
            raise ValueError("Stage input lacks a prepared input binding; regenerate it from the prepared bundle")
        require_same_backend(spec, saved)
    sys.argv = [module, *argv]
    importlib.import_module(module).main()


if __name__ == "__main__":
    main()
