# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build the G image from the four Eval Author skill trees and label it with their glob digest."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from generation_benchmark.digests import GENERATION_BENCHMARK_ROOT, METRIC_BENCHMARK_ROOT, glob_digest, iter_tree_files
from generation_benchmark.reference_generator import SOURCE_LABEL

REPO_ROOT = GENERATION_BENCHMARK_ROOT.parents[1]
GENERATOR_DIR = Path(__file__).resolve().parent
SKILL_TREES = ("eval-author", "eval-author-audit", "eval-author-task-create", "eval-author-environment")
DEFAULT_BASE_IMAGE = "python:3.12-slim"
# Copied to /opt/generation; outside the label digest, which covers only the skill trees.
GENERATION_FILES = {
    "entrypoint.py": GENERATOR_DIR / "entrypoint.py",
    "generation_controls.py": GENERATION_BENCHMARK_ROOT / "generation_controls.py",
}
GENERATION_PACKAGES = {
    "generation_benchmark": GENERATION_BENCHMARK_ROOT / "generation_benchmark",
    "metric_benchmark": METRIC_BENCHMARK_ROOT / "metric_benchmark",
}

__all__ = ["SOURCE_LABEL", "build_image", "generator_source_digest", "stage_build_context"]


GENERATOR_SOURCE_PATTERNS = [f"skills/{name}/**" for name in SKILL_TREES]


def generator_source_digest(repo_root: Path = REPO_ROOT) -> str:
    """The `glob` digest of the four skill trees, relative to the checkout root."""
    return glob_digest(GENERATOR_SOURCE_PATTERNS, [], repo_root)


def _copy_tree(source: Path, target: Path) -> None:
    for rel, path in iter_tree_files(source):
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def stage_build_context(repo_root: Path, context: Path) -> None:
    """Copy exactly the digested skill files, the generation code, the Dockerfile, and the shim into *context*."""
    for name in SKILL_TREES:
        _copy_tree(repo_root / "skills" / name, context / "skills" / name)
    generation = context / "generation"
    for name, source in GENERATION_PACKAGES.items():
        _copy_tree(source, generation / name)
    for name, source in GENERATION_FILES.items():
        shutil.copy2(source, generation / name)
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
