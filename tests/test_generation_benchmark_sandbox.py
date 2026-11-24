# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from generator.build import SOURCE_LABEL, build_image, generator_source_digest
from generator.harbor_shim import TOKEN_ENV, URL_ENV
from generator.sidecar import HarborSidecar
from test_generation_benchmark_sidecar import make_task, smoke_agent

pytestmark = pytest.mark.skipif(
    os.environ.get("GENERATION_BENCHMARK_DOCKER_TEST") != "1",
    reason="G image tests need Docker and run only when GENERATION_BENCHMARK_DOCKER_TEST=1",
)

SHIM_SOURCE = Path(__file__).resolve().parents[1] / "labs" / "generation-benchmark" / "generator" / "harbor_shim.py"
AUDIT = "/opt/skills/eval-author-audit"
TASK_EVIDENCE = "/opt/skills/eval-author-task-create/scripts/task_evidence.py"
REWARD = "/verifier_result/rewards/reward"


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


def test_harbor_on_path_is_the_shim_and_docker_is_absent(image: str) -> None:
    out = in_image(
        image,
        "which harbor; /opt/harbor-real/bin/harbor --version; harbor --version; "
        "test ! -e /var/run/docker.sock && echo no-socket; command -v docker || echo no-docker-cli; "
        "cat /usr/local/bin/harbor",
        "--network",
        "none",
    )
    which, real_version, shim_version, socket, docker_cli, *shim = out.splitlines()
    assert which == "/usr/local/bin/harbor"
    assert real_version == shim_version == "0.24.0"
    assert (socket, docker_cli) == ("no-socket", "no-docker-cli")
    assert "\n".join(shim) + "\n" == SHIM_SOURCE.read_text(encoding="utf-8")


def test_locate_runtime_selects_the_shim_for_authoring(image: str) -> None:
    out = in_image(
        image,
        "cd /tmp && python3 /opt/skills/eval-author/scripts/locate_runtime.py --flow authoring --provider harbor",
        "--network",
        "none",
    )
    selected = json.loads(out)["selection"]["harbor"]["selected"]
    assert (selected["cli"], selected["python"], selected["version"]) == (
        "/usr/local/bin/harbor",
        "/opt/harbor-real/bin/python",
        "0.24.0",
    )


def test_task_init_works_and_docker_backed_commands_name_the_sidecar(image: str) -> None:
    out = in_image(
        image,
        "cd /tmp && harbor task init demo/hello >/dev/null && test -f hello/task.toml && echo created; "
        "for cmd in 'view jobs' 'jobs start -p hello' 'trial summarize x'; do "
        'harbor $cmd 2>&1 && exit 1; echo "exit=$?"; done',
        "--network",
        "none",
    )
    lines = out.splitlines()
    assert lines[0] == "created"
    assert lines.count("exit=2") == 3
    assert sum("host Harbor sidecar" in line for line in lines) == 3


def _contract() -> dict:
    def case(kind: str, requirement: str, reward: float) -> dict:
        return {
            "kind": kind,
            "requirement": requirement,
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": REWARD, "min": reward, "max": reward}],
        }

    return {
        "task_id": "alpha",
        "provider": "harbor",
        "native_run_id": "/id",
        "alternative_not_applicable": "alpha has one accepted answer",
        "side_effect_not_applicable": "alpha writes one file",
        "cases": {
            "reference": case("reference", "the oracle solution writes ready", 1.0),
            "incorrect": case("incorrect", "a wrong answer scores zero", 0.0),
            "empty": case("empty", "doing nothing scores zero", 0.0),
            "agent": case("agent", "the agent under test writes ready", 1.0),
        },
    }


@pytest.fixture
def sidecar() -> Iterator[HarborSidecar]:
    agent, config = smoke_agent()
    with HarborSidecar(agent, config) as server:
        yield server


def test_container_trials_through_the_sidecar_produce_verified_task_evidence(
    image: str, sidecar: HarborSidecar, tmp_path: Path
) -> None:
    """`task_evidence.py run` creates each `<trials>/<run-id>/` before the shim writes into it."""
    src = tmp_path / "src"
    make_task(src)
    (src / "contract.json").write_text(json.dumps(_contract()), encoding="utf-8")
    trial = "-- harbor trial start -p /work/alpha --trials-dir /work/trials"
    script = f"""
set -euo pipefail
mkdir /work && cp -r /src/alpha /work/alpha && cd /work
PY=/opt/harbor-real/bin/python
TE={TASK_EVIDENCE}
$PY $TE prepare --task-dir /work/alpha --contract /src/contract.json --out /work/manifest.json >/dev/null
record() {{
  case=$1; id=$2; shift 2
  $PY $TE run --manifest /work/manifest.json --case $case --run-id $id \\
    --out /work/receipts/$id.json --result /work/trials/$id/result.json "$@" >/dev/null
}}
record reference reference-1 {trial} -a oracle --trial-name reference-1
record incorrect incorrect-1 {trial} -a generation_controls:ScriptControl --trial-name incorrect-1
record empty empty-1 {trial} -a nop --trial-name empty-1
for n in 1 2; do
  record agent agent-$n --trace /work/trials/agent-$n/agent/trajectory.json \\
    {trial} -a smoke-agent --trial-name agent-$n
done
$PY - <<'EOF'
import json, sys
from pathlib import Path
sys.path.insert(0, "/opt/skills/eval-author-task-create/scripts")
import task_evidence
receipts = sorted(Path("/work/receipts").glob("*.json"))
agents = task_evidence.verify_receipts(receipts, "alpha")
summary = {{
    "verified_agents": sorted(agents),
    "receipts": {{p.stem: json.loads(p.read_text())["status"] for p in receipts}},
    "rewards": {{
        p.parent.name: json.loads(p.read_text())["verifier_result"]["rewards"]["reward"]
        for p in Path("/work/trials").glob("*/result.json")
    }},
    "script_control_log": Path("/work/trials/incorrect-1/agent/script-control.txt").read_text(),
}}
print(json.dumps(summary))
EOF
"""
    env = {**os.environ, URL_ENV: sidecar.container_url, TOKEN_ENV: sidecar.token}
    out = in_image(image, script, "-e", URL_ENV, "-e", TOKEN_ENV, "-v", f"{src}:/src:ro", env=env)
    summary = json.loads(out.splitlines()[-1])
    assert summary["verified_agents"] == ["agent-1", "agent-2"]
    assert set(summary["receipts"].values()) == {"passed"} and len(summary["receipts"]) == 5
    assert summary["rewards"] == {
        "reference-1": 1.0,
        "incorrect-1": 0.0,
        "empty-1": 0.0,
        "agent-1": 1.0,
        "agent-2": 1.0,
    }
    assert summary["script_control_log"].endswith("exit_code=0\n")
