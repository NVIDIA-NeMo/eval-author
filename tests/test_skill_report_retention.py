# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stage_skill_reports", ROOT / "tools/stage_skill_reports.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_full_reports_retained_byte_for_byte_without_runtime_secrets_or_aliases(tmp_path):
    source, output = tmp_path / "source", tmp_path / "archive"
    reports = ["eval-author/reports/scan.json", "check-0/reports/context.json", "check-1/results/skill/run/result.json"]
    for name in reports:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"findings": [{"description": "complete finding"}]}\n')
    for name in ["home/.config/credentials.json", "check-1/console.log", "check-1/results/skill/run/config.json"]:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SECRET")
    (source / "check-1/results/skill/latest").symlink_to("run", target_is_directory=True)
    (source / "eval-author/reports/linked.json").symlink_to(source / "home/.config/credentials.json")
    manifest = module.stage(source, output)
    assert {row["path"] for row in manifest} == set(reports)
    for row in manifest:
        raw = (output / row["path"]).read_bytes()
        assert raw == (source / row["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
    assert not any(b"SECRET" in p.read_bytes() for p in output.rglob("*") if p.is_file())


def test_missing_outputs_after_install_failure_are_not_fabricated(tmp_path):
    assert module.stage(tmp_path / "missing", tmp_path / "archive") == []
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize(
    "workflow,job,condition",
    [
        ("ci.yml", "skill-evaluator", "always() && github.event.repository.private == true"),
        (
            "skill-evaluation-live.yml",
            "evaluate",
            "always() && matrix.id != 'none' && github.event.repository.private == true",
        ),
    ],
)
def test_full_reports_are_separate_private_only_and_attempted_after_failures(workflow, job, condition):
    steps = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"][job]["steps"]
    stage = next(step for step in steps if step.get("name", "").startswith("Stage full"))
    upload = next(step for step in steps if step.get("name", "").startswith("Retain full"))
    for step in (stage, upload):
        assert step["if"].removeprefix("${{").removesuffix("}}").strip() == condition
    assert upload["with"]["retention-days"] == 30
    assert upload["with"]["path"].endswith("-reports/")
    assert "github.run_attempt" in upload["with"]["name"]
    if job == "evaluate":
        assert "matrix.id" in upload["with"]["name"]
    assert steps.index(stage) < steps.index(upload)
