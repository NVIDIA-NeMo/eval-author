# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))


def test_boundary_inventory_declares_real_consumer_and_predecessor_relations():
    import importlib.util

    assert importlib.util.find_spec("rubric_refund_boundaries") is not None
    panel = importlib.import_module("rubric_refund_boundaries")
    inventory = panel.control_inventory()
    assert inventory["independence"] is False
    assert set(inventory["consumer_cases"]) == {
        "forged-text",
        "forged-json",
        "wrong-task",
        "stale-after-failure",
        "valid-neighbor",
    }
    assert set(inventory["surfaces"]) == {
        "workspace",
        "temporary",
        "cache",
        "reward",
        "checks",
        "backend",
        "session",
        "helpers",
        "image",
    }
    assert inventory["relations"] == {
        "target-alone": 0,
        "target-after-a": 0,
        "target-after-b": 0,
        "target-reset": 0,
        "valid-neighbor": 1,
    }
    assert inventory["order"].index("sentinel-a") < inventory["order"].index("target-after-a")
    assert inventory["order"].index("sentinel-b") < inventory["order"].index("target-after-b")
    command = panel.native_command(Path("/private/panel"), "forged-json")
    assert command[-2:] == ["--verifier-import-path", "rubric_refund_boundary_runtime:BoundaryVerifier"]
    assert "--delete" in command


def test_reset_observation_requires_every_surface_and_real_sentinel():
    import copy

    import pytest

    panel = importlib.import_module("rubric_refund_boundaries")
    assert hasattr(panel, "validate_reset_files")
    paths = [
        "/work/refund-sentinel.json",
        "/tmp/refund-sentinel.json",
        "/work/.cache/refund-sentinel.json",
        "/logs/verifier/reward.txt",
        "/logs/verifier/reward.json",
        "/logs/verifier/checks.json",
    ]
    before = {p: {"exists": False, "content": None} for p in paths}
    panel.validate_reset_files("target-alone", before, before, {"events": [], "success": False})
    short = copy.deepcopy(before)
    short.pop(paths[0])
    with pytest.raises(ValueError):
        panel.validate_reset_files("target-alone", short, before, {})
    with pytest.raises(ValueError):
        panel.validate_reset_files("sentinel-a", before, before, {})


def test_runtime_observes_after_upload_before_native_consumer(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    assert importlib.util.find_spec("rubric_refund_boundary_runtime") is not None
    runtime = importlib.import_module("rubric_refund_boundary_runtime")
    events = []

    class Environment:
        async def upload_dir(self, **kwargs):
            events.append("upload")

        async def exec(self, **kwargs):
            events.append(kwargs)
            return SimpleNamespace(return_code=0, stdout="{}", stderr="")

    async def native(self):
        events.append("native-consumer")
        (tmp_path / "checks.json").write_text("{}")
        return SimpleNamespace(model_dump=lambda **kwargs: {"rewards": {"reward": 0}})

    monkeypatch.setattr(runtime.Verifier, "verify", native)
    monkeypatch.setattr(runtime, "container_identity", lambda environment: {"container": "unit-transport"})
    verifier = object.__new__(runtime.BoundaryVerifier)
    verifier.environment = Environment()  # ty: ignore[invalid-assignment]
    verifier.task = SimpleNamespace(paths=SimpleNamespace(tests_dir=tmp_path))  # ty: ignore[invalid-assignment]
    verifier.trial_paths = SimpleNamespace(  # ty: ignore[invalid-assignment]
        verifier_dir=tmp_path, trial_dir=tmp_path, reward_text_path=tmp_path / "reward.txt"
    )
    verifier.case = "forged-json"  # ty: ignore[unresolved-attribute]
    asyncio.run(verifier.verify())
    assert events[0] == "upload"
    assert events[-1] == "native-consumer"
    assert all(e["user"] in ("root", "65534") for e in events if isinstance(e, dict))
    record = json.loads((tmp_path / "boundary.json").read_text())
    assert record["case"] == "forged-json"
    assert record["after_upload"] == {}
    assert record["consumer"] == {"rewards": {"reward": 0}}
    assert verifier._skip_tests_upload is True


def test_boundary_projection_never_promotes_consumer_forgery():
    module = importlib.import_module("rubric_refund_boundaries")
    assert hasattr(module, "apply")
    assessment = importlib.import_module("rubric_example_assessment")
    rows = {}
    assessment._ensure_rows(rows)
    module.apply(rows, {"ac03_score": 0, "ac04_score": 2, "index_digest": "a" * 64})
    assert rows["AC-03"]["score"] == 0
    assert rows["AC-04"]["score"] == 2
    assert rows["AC-04"]["evidence_strength"] == "E2"
    assert rows["AC-02"]["score"] == 0


def test_boundary_collector_keeps_failed_attempts_unscored(tmp_path, monkeypatch):
    import subprocess

    panel = importlib.import_module("rubric_refund_boundaries")
    assert hasattr(panel, "collect")
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setattr(panel.collector, "preflight", lambda env: None)
    monkeypatch.setattr(
        panel.collector.recorder.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1)
    )
    result = panel.collect(tmp_path / "panel", None)
    assert result["counts"] == {"attempts": 11, "completed": 0, "infrastructure_errors": 11}
    assert result["ac03_score"] is None
    assert result["ac04_score"] is None


def test_registered_boundaries_relocate_and_reject_coherent_mutations(tmp_path):
    import json
    import os
    import shutil

    import pytest

    location = os.environ.get("EVAL_AUTHOR_TEST_BOUNDARY_PANEL")
    if not location:
        pytest.skip("requires current native boundary panel")
    panel = importlib.import_module("rubric_refund_boundaries")
    bundle = tmp_path / "moved"
    shutil.copytree(location, bundle, symlinks=True)
    raw = (bundle / "native-index.json").read_bytes()
    document = panel.collector.admission(raw, bundle)
    result = panel.validate(bundle, document)
    assert (result["ac03_score"], result["ac04_score"]) == (0, 2)
    assert result["counts"] == {"attempts": 11, "completed": 11, "infrastructure_errors": 1}
    assert result["independence"] is False
    for case, mutate in (
        ("target-after-a", lambda row: row["after_upload"]["files"].pop("/work/refund-sentinel.json")),
        ("forged-json", lambda row: row["injection"]["writes"].pop("/logs/verifier/reward.json")),
        ("stale-after-failure", lambda row: row["prior_consumer"]["rewards"].update(reward=0)),
        (
            "target-after-b",
            lambda row: row["runtime"].update(container=result["controls"][0]["observation"]["runtime"]["container"]),
        ),
    ):
        path = bundle / "trials" / case / "verifier/boundary.json"
        before = path.read_bytes()
        value = json.loads(before)
        mutate(value)
        path.write_text(json.dumps(value))
        edited = json.loads(json.dumps(document))
        for row in edited["files"]:
            if row["path"] == path.relative_to(bundle).as_posix():
                row["sha256"] = panel.collector.recorder.digest(path)
        with pytest.raises(ValueError):
            panel.validate(bundle, edited)
        with pytest.raises(ValueError):
            panel.collector.admission(json.dumps(edited).encode(), bundle)
        path.write_bytes(before)
    assert panel.validate(bundle, document)["ac04_score"] == 2
