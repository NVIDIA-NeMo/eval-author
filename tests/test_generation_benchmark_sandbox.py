# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import subprocess

import pytest
from generator.build import SOURCE_LABEL, build_image, generator_source_digest

pytestmark = pytest.mark.skipif(
    os.environ.get("GENERATION_BENCHMARK_DOCKER_TEST") != "1",
    reason="G image tests need Docker and run only when GENERATION_BENCHMARK_DOCKER_TEST=1",
)

AUDIT = "/opt/skills/eval-author-audit"


@pytest.fixture(scope="module")
def image() -> str:
    return build_image()


def in_image(image: str, script: str, *docker_args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", *docker_args, image, "bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=1800,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_image_label_is_the_host_glob_digest_of_the_four_skill_trees(image: str) -> None:
    labels = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(labels)[SOURCE_LABEL] == generator_source_digest()


def test_python_dependencies_are_predownloaded(image: str) -> None:
    script = (
        f"uv run --with-requirements {AUDIT}/requirements.txt {AUDIT}/scripts/audit_spec/measure.py --help >/dev/null"
        " && uv run /opt/skills/eval-author-task-create/scripts/task_pipeline.py --help >/dev/null"
    )
    in_image(image, script, "--network", "none")
