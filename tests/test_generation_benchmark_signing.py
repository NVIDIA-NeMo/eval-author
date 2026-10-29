# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import re
import shutil

import pytest
import yaml
from generation_benchmark.config import load_generation_benchmark
from generation_benchmark.signing import check_generation_benchmark, sign_generation_benchmark
from metric_benchmark.config import input_digest
from test_generation_benchmark_config import (
    DESCRIPTION_PATH,
    GENERATION_AGENT_DIR,
    TASK_DIR,
    prepared_generation_benchmark,
)


def manifest_bytes(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*.yml")}


def remove_pins(root):
    for path in root.rglob("*.yml"):
        path.write_bytes(re.sub(rb"(?m)^\s*sha256: [^\r\n]*\r?\n", b"", path.read_bytes()))


def test_unsigned_to_signed_generation_round_trip(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    remove_pins(tmp_path)
    with pytest.raises(ValueError, match="missing SHA"):
        load_generation_benchmark(path)
    unsigned = load_generation_benchmark(path, allow_unsigned=True)
    assert unsigned.execution.inputs.allow_unsigned
    sign_generation_benchmark(path, resign=True)
    check_generation_benchmark(path)
    assert not load_generation_benchmark(path).execution.inputs.allow_unsigned
    agent_path = tmp_path / GENERATION_AGENT_DIR / "agent.yml"
    agent_path.write_text(agent_path.read_text() + "# drift\n")
    with pytest.raises(ValueError, match="input digest differs"):
        load_generation_benchmark(path, allow_unsigned=True)
    with pytest.raises(ValueError, match="input digest differs"):
        check_generation_benchmark(path)
    sign_generation_benchmark(path, resign=True)
    check_generation_benchmark(path)


def test_generation_signing_refusal_leaves_manifests_unchanged(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    before = manifest_bytes(tmp_path)
    with pytest.raises(ValueError, match="SHA fields already exist"):
        sign_generation_benchmark(path, resign=False)
    shutil.rmtree(tmp_path / TASK_DIR / "gen-task-a")
    with pytest.raises(ValueError, match="missing pinned input"):
        sign_generation_benchmark(path, resign=True)
    assert manifest_bytes(tmp_path) == before


def test_generation_inputs_resign_after_config_edit(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    generator = tmp_path / "configs/generator.yaml"
    generator.write_text(generator.read_text() + "# edit\n")
    with pytest.raises(ValueError, match="input digest differs"):
        load_generation_benchmark(path)
    generation_path = tmp_path / "generation.yml"
    definition = yaml.safe_load(generation_path.read_text())
    definition["inputs"]["generator_config"]["sha256"] = input_digest(generator)
    generation_path.write_text(yaml.safe_dump(definition, sort_keys=False))
    sign_generation_benchmark(path, resign=True)
    check_generation_benchmark(path)


def test_resign_preserves_description_pin(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    before = (tmp_path / DESCRIPTION_PATH).read_bytes()
    (tmp_path / DESCRIPTION_PATH).write_text('{"required": ["gen-task-a", "gen-task-b"]}\n')
    sign_generation_benchmark(path, resign=True)
    check_generation_benchmark(path)
    assert (tmp_path / DESCRIPTION_PATH).read_bytes() != before
