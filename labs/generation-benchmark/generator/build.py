# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build the G image from the four Eval Author skill trees and label it with their glob digest."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from generation_benchmark.digests import GENERATION_BENCHMARK_ROOT, glob_digest, iter_tree_files

REPO_ROOT = GENERATION_BENCHMARK_ROOT.parents[1]
GENERATOR_DIR = Path(__file__).resolve().parent
SKILL_TREES = ("eval-author", "eval-author-audit", "eval-author-task-create", "eval-author-environment")
SOURCE_LABEL = "org.nvidia.eval-author.generator-source-sha256"
DEFAULT_BASE_IMAGE = "python:3.12-slim"


GENERATOR_SOURCE_PATTERNS = [f"skills/{name}/**" for name in SKILL_TREES]


def generator_source_digest(repo_root: Path = REPO_ROOT) -> str:
    """The `glob` digest of the four skill trees, relative to the checkout root."""
    return glob_digest(GENERATOR_SOURCE_PATTERNS, [], repo_root)


def stage_build_context(repo_root: Path, context: Path) -> None:
    """Copy exactly the digested skill files, the Dockerfile, and the shim into *context*."""
    for name in SKILL_TREES:
        source = repo_root / "skills" / name
        for rel, path in iter_tree_files(source):
            target = context / "skills" / name / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    shutil.copy2(GENERATOR_DIR / "Dockerfile", context / "Dockerfile")
    shutil.copy2(GENERATOR_DIR / "harbor_shim.py", context / "harbor_shim.py")


def build_image(
    *,
    base_image: str = DEFAULT_BASE_IMAGE,
    tag: str | None = None,
    repo_root: Path = REPO_ROOT,
    docker: str = "docker",
) -> str:
    """Build the G image and return its `sha256:` image id."""
    digest = generator_source_digest(repo_root)
    with tempfile.TemporaryDirectory(prefix="g-image-") as scratch:
        context = Path(scratch) / "context"
        stage_build_context(repo_root, context)
        iidfile = Path(scratch) / "iid"
        command = [
            docker,
            "build",
            "--build-arg",
            f"BASE_IMAGE={base_image}",
            "--build-arg",
            f"GENERATOR_SOURCE_SHA256={digest}",
            "--iidfile",
            str(iidfile),
        ]
        if tag:
            command += ["--tag", tag]
        subprocess.run([*command, str(context)], check=True)
        return iidfile.read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", default=DEFAULT_BASE_IMAGE, help="coding-agent base image")
    parser.add_argument("--tag", help="optional image tag")
    args = parser.parse_args(argv)
    print(build_image(base_image=args.base_image, tag=args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
