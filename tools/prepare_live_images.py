# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare exact native trial images without inference credentials."""

import argparse
import os
from pathlib import Path

from patched_opencode_runtime import LIVE_JOB_DEADLINE_ENV, preparation_failure, prepare_images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--binary-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(os.environ.get(key) for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")):
        parser.error("conflicting Python instrumentation")
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "DOCKER_HOST", LIVE_JOB_DEADLINE_ENV)
        if key in os.environ
    }
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1", HARBOR_TELEMETRY="0")
    os.environ.clear()
    os.environ.update(environment)
    try:
        prepare_images(args.skill.resolve(), args.output.resolve(), args.binary_manifest.resolve())
    except Exception as error:
        details = preparation_failure(error)
        parser.exit(
            1,
            "image preparation failed: "
            + " ".join(key + "=" + value for key, value in details.items())
            + "; inference was not started\n",
        )
    print("prepared trial images verified")


if __name__ == "__main__":
    main()
