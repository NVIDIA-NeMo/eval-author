# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Opt-in keyless acceptance for collector/native lifecycle containment.

Run with EVAL_AUTHOR_LIFECYCLE_ACCEPTANCE=1 in the pinned live interpreter.
Docker controls additionally require EVAL_AUTHOR_LIFECYCLE_DOCKER_IMAGE to be
an existing immutable busybox-compatible image ID. No build or pull is allowed.
These are acceptance assertions, not expected-failure demonstrations.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from collect_skill_evaluations import execute  # noqa: E402

CHILD = """
import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
mode = sys.argv[2]
image = sys.argv[3]
project = sys.argv[4]
if mode == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
def create():
    cid = subprocess.check_output(['docker', 'create', '--network', 'none',
        '--label', 'com.docker.compose.project='+project, image, 'sleep', '30'],
        text=True).strip()
    (root/'container.json').write_text(json.dumps({'id':cid, 'image':image, 'project':project}))
    subprocess.run(['docker','start',cid],check=True,stdout=subprocess.DEVNULL)
if image and mode != 'late':
    create()
(root/'ready').write_text('ready')
time.sleep(2)
if image and mode == 'late':
    assert (root/'collector-finished').exists(), 'late creation must follow evaluator reap'
    create()
(root/'escaped').write_text(str(time.monotonic()))
"""
WORKER = """
import os, signal, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from skillevaluator.tier3.harbor.runner import _run_bounded_harbor_process
root=Path(sys.argv[1])
pool=ThreadPoolExecutor(max_workers=1)
future=pool.submit(_run_bounded_harbor_process,
    [sys.executable,'-c',sys.argv[2],str(root),sys.argv[3],sys.argv[4],sys.argv[5]],
    env=dict(os.environ),cwd=root,stdin_text=None,
    timeout_seconds=0.5 if sys.argv[6]=='native' else None,
    max_output_bytes=8192,diagnostic_tail_chars=256,secret_values=set())
if sys.argv[6]=='forced':
    deadline=time.monotonic()+10
    while not (root/'ready').exists() and time.monotonic()<deadline:
        time.sleep(.01)
    os.kill(os.getpid(),signal.SIGKILL)
future.result()
"""


def keyless_env(root):
    home = root / "home"
    home.mkdir(mode=0o700)
    return {
        "PATH": str(Path(sys.executable).parent) + ":/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(home),
        "TMPDIR": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "HARBOR_TELEMETRY": "0",
    }


def require_native():
    if os.environ.get("EVAL_AUTHOR_LIFECYCLE_ACCEPTANCE") != "1":
        pytest.skip("opt-in native lifecycle acceptance")
    if importlib.util.find_spec("skillevaluator") is None:
        pytest.fail("acceptance requested without pinned native runtime")
    from importlib.metadata import distribution

    native = distribution("skillevaluator")
    assert native.version == "0.7.0"
    assert distribution("harbor").version == "0.24.0"
    direct_url = native.read_text("direct_url.json")
    assert direct_url is not None
    assert json.loads(direct_url)["vcs_info"]["commit_id"] == "3fd2cf57e4a2af01f282e001f7515b6e76f18ec7"


def run_case(root, env, mode, termination, image="", project=""):
    argv = [sys.executable, "-c", WORKER, str(root), CHILD, mode, image, project, termination]
    # The collector itself owns and kills the evaluator; native launches occur
    # in the evaluator's real ThreadPoolExecutor worker, not its main thread.
    import threading

    result = []
    error = []

    def collect():
        try:
            result.append(execute(argv, ROOT, env, root / "collector.log", 1.5 if termination == "collector" else 12))
            (root / "collector-finished").write_text(str(time.monotonic()))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=collect)
    started = time.monotonic()
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive(), "collector exceeded bounded control deadline"
    assert not error, "collector raised instead of returning bounded status"
    assert (root / "ready").is_file(), "native worker did not reach the real detached launch"
    # Children self-expire; never discover or kill processes by PID/name.
    deadline = started + 8
    while time.monotonic() < deadline:
        time.sleep(0.05)
    return result[0]


def test_collector_reaps_direct_timeout(tmp_path):
    code, reason = execute(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        ROOT,
        keyless_env(tmp_path),
        tmp_path / "direct.log",
        0.1,
    )
    assert code < 0
    assert reason == "timeout"


@pytest.mark.parametrize(
    ("mode", "termination"),
    [("cooperative", "native"), ("ignore", "native"), ("cooperative", "collector"), ("cooperative", "forced")],
)
def test_native_worker_child_cannot_outlive_evaluator(tmp_path, mode, termination):
    require_native()
    code, reason = run_case(tmp_path, keyless_env(tmp_path), mode, termination)
    assert code != 0
    assert reason == ("timeout" if termination == "collector" else None)
    assert not (tmp_path / "escaped").exists(), "detached Harbor workload survived evaluator termination"


@pytest.mark.parametrize("mode", ["cooperative", "late"])
def test_native_worker_docker_resources_cannot_outlive_evaluator(tmp_path, mode):
    require_native()
    image = os.environ.get("EVAL_AUTHOR_LIFECYCLE_DOCKER_IMAGE", "")
    if not image:
        pytest.skip("explicit immutable local Docker image required")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    import secrets

    project = "ea-lifecycle-" + secrets.token_hex(16)
    env = keyless_env(tmp_path)

    def docker(*args):
        return subprocess.run(["docker", *args], env=env, text=True, capture_output=True, timeout=15, check=True)

    assert docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip() == image
    sentinel = docker(
        "create",
        "--network",
        "none",
        "--label",
        "com.docker.compose.project=" + project + "-other",
        image,
        "sleep",
        "30",
    ).stdout.strip()
    assert re.fullmatch(r"[a-f0-9]{64}", sentinel)
    owned = None
    try:
        docker("start", sentinel)
        code, reason = run_case(tmp_path, env, mode, "forced", image, project)
        assert code < 0 and reason is None
        receipt = json.loads((tmp_path / "container.json").read_text())
        owned = receipt["id"]
        assert re.fullmatch(r"[a-f0-9]{64}", owned)
        assert receipt == {"id": owned, "image": image, "project": project}
        other = json.loads(docker("inspect", sentinel).stdout)[0]
        assert other["Id"] == sentinel and other["State"]["Running"] is True
        remaining = docker("ps", "-aq", "--no-trunc", "--filter", "id=" + owned).stdout.strip()
        assert not remaining, "run-owned Docker container survived evaluator death"
    finally:
        # Exact IDs from this test only. Recheck image/project before removal.
        if owned is None and (tmp_path / "container.json").exists():
            owned = json.loads((tmp_path / "container.json").read_text())["id"]
        for cid, expected_project in ((owned, project), (sentinel, project + "-other")):
            if cid is None:
                continue
            assert re.fullmatch(r"[a-f0-9]{64}", cid)
            rows = docker("ps", "-aq", "--no-trunc", "--filter", "id=" + cid).stdout.strip()
            if rows:
                observed = json.loads(docker("inspect", cid).stdout)[0]
                assert observed["Id"] == cid and observed["Image"] == image
                assert observed["Config"]["Labels"]["com.docker.compose.project"] == expected_project
                docker("rm", "-f", cid)
            assert not docker("ps", "-aq", "--no-trunc", "--filter", "id=" + cid).stdout.strip()
