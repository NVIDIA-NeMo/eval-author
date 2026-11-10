# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Install the evaluator and its pinned dependencies in an isolated Python 3.12 environment."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from patched_opencode_runtime import EVALUATOR_REVISION

LOCKS = Path(__file__).resolve().parent / "evaluator-locks"
EXTRAS = ("security", "tier2", "tier3")


def validate_environment(extra: str, distributions=None) -> None:
    """Check dependencies for the selected extras. Allow only the reviewed LiteLLM exception."""
    import importlib.metadata
    import json

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    if extra not in EXTRAS:
        raise ValueError("unsupported evaluator extra")
    installed = {}
    for distribution in distributions if distributions is not None else importlib.metadata.distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        if name in installed:
            raise RuntimeError(f"duplicate installed distribution: {name}")
        installed[name] = distribution
    evaluator = installed.get("skillevaluator")
    if evaluator is None:
        raise RuntimeError("missing evaluator")
    provenance = json.loads(evaluator.read_text("direct_url.json") or "{}")
    if (
        evaluator.version != "0.7.0"
        or provenance.get("url") != "https://github.com/NVIDIA/SkillEvaluator.git"
        or provenance.get("vcs_info", {}).get("vcs") != "git"
        or provenance.get("vcs_info", {}).get("commit_id") != EVALUATOR_REVISION
    ):
        raise RuntimeError("unreviewed evaluator provenance")
    overrides = [
        Requirement(line)
        for line in (LOCKS / "llm-overrides.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if (
        len(overrides) != 1
        or overrides[0].name != "litellm"
        or overrides[0].url
        or overrides[0].marker
        or overrides[0].extras
    ):
        raise RuntimeError("unreviewed override policy")
    specs = list(overrides[0].specifier)
    if len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
        raise RuntimeError("override must pin one exact version")
    pin = specs[0].version
    exception = Requirement('litellm<1.94.0.dev0,>=1.92.0; extra == "llm"')
    pending = [(name, "") for name in installed] + [(canonicalize_name("skillevaluator"), extra)]
    visited = set()
    admitted = False
    while pending:
        name, active_extra = pending.pop()
        if active_extra:
            active_extra = canonicalize_name(active_extra)
            declared = {canonicalize_name(value) for value in installed[name].metadata.get_all("Provides-Extra", [])}
            if active_extra not in declared:
                raise RuntimeError(f"undeclared extra: {name}[{active_extra}]")
        if (name, active_extra) in visited:
            continue
        visited.add((name, active_extra))
        for raw in installed[name].requires or ():
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate({"extra": active_extra}):
                continue
            dependency = canonicalize_name(requirement.name)
            target = installed.get(dependency)
            if target is None:
                raise RuntimeError(f"missing active dependency: {name} -> {requirement}")
            if name == "skillevaluator" and dependency == "litellm" and extra in ("tier2", "tier3"):
                if requirement != exception or target.version != pin:
                    raise RuntimeError("unreviewed evaluator LiteLLM combination")
                admitted = True
            elif requirement.url or target.version not in requirement.specifier:
                raise RuntimeError(f"incompatible active dependency: {name} -> {requirement}")
            pending.extend((dependency, selected) for selected in requirement.extras)
    if extra in ("tier2", "tier3"):
        if not admitted:
            raise RuntimeError("expected evaluator LiteLLM requirement missing")
        print(
            f"Compatibility policy passed with intentional upstream metadata conflict: "
            f"skillevaluator[llm] requires {exception.specifier}; locally pinned litellm=={pin}. "
            "This is not metadata consistency or live-provider acceptance."
        )


def install(python: str, extra: str) -> None:
    if extra not in EXTRAS:
        raise ValueError("unsupported evaluator extra")
    # Do not inherit provider credentials or uv index/override/build settings.
    env = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "TMPDIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")
        if name in os.environ
    }
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    common = ["uv", "pip", "install", "--python", python, "--no-config"]
    subprocess.run(
        [*common, "--require-hashes", "--only-binary", ":all:", "--requirements", str(LOCKS / f"{extra}.txt")],
        env=env,
        check=True,
    )
    # Build only the evaluator at the pinned revision. Its build backend and
    # dependencies are already installed. Neither dependency resolver may run here.
    subprocess.run(
        [
            *common,
            "--no-deps",
            "--no-build-isolation",
            f"skillevaluator[{extra}] @ git+https://github.com/NVIDIA/SkillEvaluator.git@{EVALUATOR_REVISION}",
        ],
        env=env,
        check=True,
    )
    subprocess.run(["uv", "pip", "check", "--python", python, "--no-config"], env=env, check=True)
    # uv pip check does not activate optional dependencies. Check the selected
    # extras recursively in the target interpreter without rewriting metadata.
    subprocess.run(
        [python, "-s", "-B", str(Path(__file__).resolve()), "--check-environment", "--extra", extra],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python")
    parser.add_argument("--check-environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--extra", choices=EXTRAS, required=True)
    args = parser.parse_args()
    if args.check_environment:
        validate_environment(args.extra)
    elif args.python:
        install(args.python, args.extra)
    else:
        parser.error("--python is required for installation")
