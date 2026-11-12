# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Portable TASTE boundaries and optional real native adapter controls."""

import os
import shutil
import subprocess
import sys

import pytest
from test_taste_backends import ROOT, SKILL, bundle, run_source


def test_copied_skill_sequence_runtime_and_ea_service_loading(tmp_path):
    copied = tmp_path / "skills/integrations/taste"
    shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    scripts = tmp_path / "skills/eval-author/scripts"
    shutil.copytree(
        ROOT / "skills/eval-author/scripts/benchmark_runtime",
        scripts / "benchmark_runtime",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    result = subprocess.run(
        [sys.executable, str(copied / "scripts/taste_runtime.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    root = bundle(tmp_path / "input")
    run_source(
        f"""
from pathlib import Path
sys.path.insert(0,{str(scripts)!r})
from src.common.domain_config import DomainConfig
config=DomainConfig("mb1",resources_dir={str(root)!r},task_service="benchmark_runtime.workspace_assistant:Service",options={{"native_root":"/not/installed"}})
assert "reference_actions" in config.prompt_manager().get_prompt("create_user_task")
assert "benchmark_runtime.workspace_assistant" not in sys.modules
import benchmark_runtime.workspace_assistant as module
assert Path(module.__file__).is_relative_to(Path({str(scripts)!r}))
assert not any(name in sys.modules for name in ("environment","fixture_state","tau2","litellm"))
""",
        vendor=copied / "vendor/taste",
    )


@pytest.mark.parametrize("backend", ["workspace_assistant", "tau2"])
def test_real_native_ea_service_controls(backend, tmp_path):
    prefix = f"EVAL_AUTHOR_TASTE_{backend.upper()}"
    python = os.environ.get(prefix + "_PYTHON")
    root = os.environ.get(prefix + "_ROOT")
    if not python or not root:
        pytest.skip(f"Set {prefix}_PYTHON and {prefix}_ROOT for native checks")
    result = subprocess.run(
        [python, str(ROOT / f"tests/taste_{backend}_controls.py"), root, str(SKILL), str(tmp_path / backend)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ea_authoring_review_synthesizes_criteria_and_rejects_changed_reference(tmp_path):
    root = bundle(tmp_path)
    run_source(f"""
import copy
sys.path.insert(0,{str(ROOT / "skills/eval-author/scripts")!r})
from benchmark_runtime.authoring import review_candidate,AuthoringError
from src.common.domain_config import DomainConfig
config=DomainConfig("mb1",resources_dir={str(root)!r})
candidate={{"task":{{"request":"Increment once."}},"reference_actions":[{{"name":"increment","arguments":{{"amount":1}}}}]}}
fixture={{"start":0}}
def reviewer(**kwargs):
    reviewed=kwargs["candidate"]
    reviewed["task"]["expected_value"]=1
    return {{"accepted":True,"candidate":reviewed,"fixture":kwargs["fixture"]}}
config.authoring_review=reviewer
reviewed,state=review_candidate(config,candidate,fixture,settings={{"model":"scripted"}},reference_outputs=["1"])
assert reviewed["task"]["expected_value"] == 1 and "expected_value" not in candidate["task"]
def changed_reference(**kwargs):
    result=reviewer(**kwargs)
    result["candidate"]["reference_actions"][0]["arguments"]["amount"]=2
    return result
config.authoring_review=changed_reference
try: review_candidate(config,candidate,fixture,settings={{"model":"scripted"}})
except AuthoringError as error: assert "reference actions" in str(error)
else: raise AssertionError("Hinted reference changed during review")
config.authoring_review=lambda **kwargs: {{"accepted":False,"issues":["unsupported requirement"]}}
try: review_candidate(config,candidate,fixture,settings={{"model":"scripted"}})
except AuthoringError: pass
else: raise AssertionError("Rejected candidate accepted")
def unavailable(**kwargs): raise RuntimeError("review provider unavailable")
config.authoring_review=unavailable
try: review_candidate(config,candidate,fixture,settings={{"model":"scripted"}})
except RuntimeError: pass
else: raise AssertionError("Provider error converted to candidate rejection")
""")
