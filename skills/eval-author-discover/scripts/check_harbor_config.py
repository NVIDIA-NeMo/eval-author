# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preflight one explicitly selected Harbor config without scanning the repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from _checks import CheckResult, required_failures
from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))

from runtime_policy import RuntimePolicyError, require_supported  # noqa: E402


class RequiredEnvironmentVariable(BaseModel):
    name: str
    has_default: bool
    declared_in: str


class PreflightResult(BaseModel):
    preflight_passed: bool
    runtime_verified: Literal[False] = False
    working_directory: str
    config_path: str
    checks: list[CheckResult]
    required_env_vars: list[RequiredEnvironmentVariable]


async def preflight(repo: Path, config_path: Path) -> PreflightResult:
    require_supported("harbor", "discovery", version("harbor"))
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
    return PreflightResult(
        preflight_passed=not required_failures(outcome.checks),
        working_directory=str(repo),
        config_path=str(config_path),
        checks=outcome.checks,
        required_env_vars=[
            RequiredEnvironmentVariable(
                name=entry.name, has_default=entry.default is not None, declared_in=str(entry.declared_in)
            )
            for entry in outcome.required_env_vars
        ],
    )


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
    except RuntimePolicyError as error:
        print(json.dumps({"preflight_passed": False, "error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, ImportError):
        print(
            json.dumps({"preflight_passed": False, "error": "Could not preflight the selected Harbor config"}),
            file=sys.stderr,
        )
        return 2
    print(result.model_dump_json())
    return 0 if result.preflight_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
