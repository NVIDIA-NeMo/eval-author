# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Install the reviewed evaluator closure into an isolated Python 3.12 environment."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from patched_opencode_runtime import EVALUATOR_REVISION

LOCKS = Path(__file__).resolve().parent / "evaluator-locks"
EXTRAS = ("security", "tier2", "tier3")


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
    # The only source build is the immutable evaluator. Its backend and backend
    # dependencies were installed above; neither dependency resolver may run here.
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--extra", choices=EXTRAS, required=True)
    args = parser.parse_args()
    install(args.python, args.extra)
