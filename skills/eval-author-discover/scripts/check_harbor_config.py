# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preflight one explicitly selected Harbor config without scanning the repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def preflight(repo: Path, config_path: Path) -> dict[str, object]:
    from _checks import required_failures
    from providers.harbor._explore import ConfigCandidate
    from providers.harbor._judge import judge_config, load_config_source
    from yaml import YAMLError

    try:
        data = load_config_source(config_path)
    except YAMLError as error:
        raise ValueError("Invalid Harbor configuration YAML") from error
    if not isinstance(data, dict):
        raise ValueError("Expected a native Harbor job configuration")
    outcome = await judge_config(ConfigCandidate(config_path, data, parsed=True), repo)
    return {
        "preflight_passed": not required_failures(outcome.checks),
        "runtime_verified": False,
        "working_directory": str(repo),
        "config_path": str(config_path),
        "checks": [entry.as_dict() for entry in outcome.checks],
        "required_env_vars": [
            {"name": entry.name, "has_default": entry.default is not None, "declared_in": str(entry.declared_in)}
            for entry in outcome.required_env_vars
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    repo = args.repo.expanduser().resolve()
    config = args.config.expanduser()
    config = (config if config.is_absolute() else repo / config).resolve()
    try:
        if not repo.is_dir():
            raise ValueError("The selected repository is not a directory")
        result = asyncio.run(preflight(repo, config))
    except (OSError, ValueError, ImportError):
        print(
            json.dumps({"preflight_passed": False, "error": "Could not preflight the selected Harbor config"}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result))
    return 0 if result["preflight_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
