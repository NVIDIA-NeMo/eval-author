# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reference generator: settings and operator answers, the launcher, signing, and the G container run."""

from __future__ import annotations

import copy
import functools
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import eval_author_generation
import pytest
import yaml
from eval_author_generation import DockerCli, container_name, start_harbor_sidecar
from generation_benchmark.digests import iter_tree_files, pythonpath_env
from generation_benchmark.models import GenerationAgent, GenerationRequest, ScoreRequest
from generation_benchmark.reference_generator import (
    INTERNAL_NETWORK_ERROR,
    SOURCE_LABEL,
    ImageInfo,
    OperatorAnswers,
    ReferenceSettings,
)
from generation_benchmark.signing import check_experiment, lock_path_for, sign_experiment
from generator.build import REPO_ROOT, SKILL_TREES, build_image, generator_source_digest, stage_build_context
from generator.entrypoint import covered_tools
from generator.harbor_shim import TOKEN_ENV, URL_ENV
from generator.sidecar import default_bind_host
from harbor.models.trial.config import AgentConfig
from metric_benchmark.io import read_json, write_json
from pydantic import ValidationError
from test_generation_benchmark_models import write_sample_experiment

GEN_LAB_ROOT = REPO_ROOT / "labs" / "generation-benchmark"
SMOKE = GEN_LAB_ROOT / "examples" / "smoke"
REFERENCE_EXPERIMENT = SMOKE / "generation-experiment-reference.yml"
RUN_CLI = GEN_LAB_ROOT / "run_generation_experiment.py"
IMAGE = "sha256:" + "a" * 64
MARKER = ".eval-author/.working/fake-agent-marker"
DRAFT_ONLY = ["python3", "/opt/generation/fake_agent.py", "--draft-only"]
EXPECTED_MOUNTS = {
    "/input": True,
    "/output": False,
    "/request.json": True,
    "/mounts/trials": True,
    "/mounts/audit.md": True,
    "/mounts/gap-report.json": True,
    "/mounts/prompt.md": True,
    "/mounts/operator-answers.yaml": True,
}

docker_only = pytest.mark.skipif(
    os.environ.get("GENERATION_BENCHMARK_DOCKER_TEST") != "1",
    reason="G container tests need Docker and run only when GENERATION_BENCHMARK_DOCKER_TEST=1",
)


def smoke_answers() -> dict[str, Any]:
    return yaml.safe_load((SMOKE / "operator-answers.yaml").read_text(encoding="utf-8"))


def matching_image(image_id: str) -> ImageInfo:
    return ImageInfo(id=image_id, labels={SOURCE_LABEL: generator_source_digest()})


# --- settings and operator answers ---


def test_settings_reject_unknown_fields_and_launcher_variables() -> None:
    base = {
        "image_id": IMAGE,
        "prompt": {"path": "prompt.md"},
        "operator_answers": {"path": "operator-answers.yaml"},
        "agent_argv": ["codex"],
    }
    assert ReferenceSettings.model_validate(base).entrypoint == ["python3", "/opt/generation/entrypoint.py"]
    for bad in (
        {**base, "mounts": ["/"]},
        {**base, "image_id": "latest"},
        {**base, "provider_env": [TOKEN_ENV]},
        {**base, "network": "host"},
        {**base, "agent_argv": []},
    ):
        with pytest.raises(ValidationError):
            ReferenceSettings.model_validate(bad)


def test_operator_answers_reject_unknown_fields_and_mismatched_authorizations() -> None:
    OperatorAnswers.model_validate(smoke_answers())
    top = smoke_answers()
    top["extra"] = True
    nested = smoke_answers()
    nested["journey_update"]["scopes"]["expand"]["authorizations"]["network"] = True
    prerequisite = smoke_answers()
    prerequisite["journey_update"]["prerequisites"]["python"] = {"status": "confirmed", "source": "operator"}
    mismatched = smoke_answers()
    mismatched["authorizations"]["model_calls"] = True
    for bad in (top, nested, prerequisite):
        with pytest.raises(ValidationError, match="Extra inputs"):
            OperatorAnswers.model_validate(bad)
    with pytest.raises(ValidationError, match="authorizations differs"):
        OperatorAnswers.model_validate(mismatched)


def test_entrypoint_compares_the_agent_under_test_covered_set() -> None:
    split = {
        "covered": ["alpha", "beta", "gamma"],
        "coverage_by_evidence_class": {"agent_under_test": {"covered": ["b", "a"]}},
    }
    assert covered_tools(split) == ["a", "b"]
    assert covered_tools({"covered": ["beta", "alpha"]}) == ["alpha", "beta"]
    with pytest.raises(ValueError):
        covered_tools({})


def test_build_context_ships_the_generation_code_outside_the_label_digest(tmp_path: Path) -> None:
    stage_build_context(REPO_ROOT, tmp_path)
    generation = tmp_path / "generation"
    for name in ("entrypoint.py", "fake_agent.py", "generation_controls.py"):
        assert (generation / name).is_file()
    assert (generation / "generation_benchmark" / "reference_generator.py").is_file()
    assert (generation / "metric_benchmark" / "io.py").is_file()
    assert not list(tmp_path.rglob("__pycache__"))
    assert sorted(path.name for path in (tmp_path / "skills").iterdir()) == sorted(SKILL_TREES)


# --- launcher, with Docker and the sidecar injected ---


class FakeSidecar:
    url = "http://127.0.0.1:4321"
    container_url = "http://host.docker.internal:4321"
    token = "secret-token"

    def __init__(self) -> None:
        self.stopped = False

    def stop(self, timeout: float | None = None) -> None:
        self.stopped = True


class FakeDocker(DockerCli):
    """Records Docker calls; `start` runs *script* (with the output dir as argv[1]) as the container."""

    def __init__(
        self,
        output_dir: Path,
        *,
        image: ImageInfo | None = None,
        script: str = "",
        on_start: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.output_dir = output_dir
        self.image = image
        self.script = script
        self.on_start = on_start
        self.calls: list[tuple[str, Any]] = []
        self.env: dict[str, str] = {}
        self.process: subprocess.Popen[bytes] | None = None

    def inspect(self, image_id: str) -> ImageInfo | None:
        self.calls.append(("inspect", image_id))
        return self.image

    def start(self, args: list[str], env: Mapping[str, str]) -> subprocess.Popen[bytes]:
        self.calls.append(("start", args))
        self.env = dict(env)
        self.process = subprocess.Popen([sys.executable, "-c", self.script, str(self.output_dir)])
        if self.on_start is not None:
            self.on_start()
        return self.process

    def stop(self, name: str) -> None:
        self.calls.append(("stop", name))
        if self.process is not None:
            self.process.terminate()

    def remove(self, name: str) -> None:
        self.calls.append(("remove", name))

    def names(self) -> list[str]:
        return [call for call, _ in self.calls]


WRITE_RESPONSE = (
    "import json, sys, pathlib\n"
    "pathlib.Path(sys.argv[1], 'response.json').write_text(json.dumps("
    "{'schema': 'nemo.eval_author.generation_response.v1', 'attempts': [], 'unattempted': []}))\n"
)


@dataclass
class HostTree:
    request: Path
    workspace: Path
    output: Path
    prompt: Path
    answers: Path

    def run(self, docker: DockerCli, sidecar: FakeSidecar | None = None) -> int:
        def start(agent: GenerationAgent, config: AgentConfig, pythonpath: list[Path]) -> FakeSidecar:
            assert agent.agent_bundle_id == "smoke-agent"
            assert config.import_path == "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"
            if sidecar is None:
                raise OSError("address already in use")
            return sidecar

        return eval_author_generation.run(self.request, self.workspace, self.output, docker=docker, start_sidecar=start)

    def response(self) -> dict[str, Any]:
        payload = read_json(self.output / "response.json")
        assert isinstance(payload, dict)
        return payload


def host_tree(
    root: Path,
    *,
    settings: dict[str, Any] | None = None,
    answers: dict[str, Any] | None = None,
    prompt: str = "Close `{{tool_name}}`.\n",
    harbor: dict[str, Any] | None = None,
) -> HostTree:
    """The paths a stage 3 run hands the launcher: a pending dir, its subset metric dir, and settings files."""
    case = root / "cases" / "case-0003"
    pending = case / "generation" / "subset-000001.pending-42"
    workspace = pending / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "ETHOS.md").write_text("ethos\n", encoding="utf-8")
    output = pending / "output"
    output.mkdir()
    subset = case / "metric-subsets" / "subset-000001"
    trial = subset / "workspace" / "trials" / "alpha"
    trial.mkdir(parents=True)
    write_json(
        trial / "result.json",
        {"config": {"agent": {"import_path": "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"}}},
    )
    setup = case / "metric-setup"
    (setup / ".eval-author").mkdir(parents=True)
    (setup / ".eval-author" / "audit.md").write_text("# audit\n", encoding="utf-8")
    (subset / "output").mkdir()
    write_json(subset / "output" / "audit-coverage-report.json", {"covered": []})
    score_request = ScoreRequest.model_validate(
        {
            "case_id": "smoke",
            "scope": "subset",
            "subset_id": "s1",
            "metric_setup_dir": str(setup),
            "tasks": [{"task_id": "alpha", "revision": 1}],
            "trials": [{"task_id": "alpha", "revision": 1, "status": "completed", "trial_path": "trials/alpha"}],
        }
    )
    write_json(subset / "request.json", score_request.to_payload())
    files = root / "experiment"
    files.mkdir()
    prompt_path = files / "prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    answers_path = files / "operator-answers.yaml"
    answers_path.write_text(yaml.safe_dump(answers or smoke_answers()), encoding="utf-8")
    request = GenerationRequest.model_validate(
        {
            "case_id": "smoke",
            "eval_subset_id": "s1",
            "tasks": [{"task_id": "alpha", "revision": 1}],
            "metric_output_dir": str(subset / "output"),
            "agent": {
                "agent_bundle_id": "smoke-agent",
                "revision": "1",
                "harbor": harbor or {"import_path": "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"},
            },
            "settings": {
                "image_id": IMAGE,
                "prompt": {"path": str(prompt_path)},
                "operator_answers": {"path": str(answers_path)},
                "agent_argv": ["codex", "exec", "-"],
                "provider_env": ["PRESENT_KEY", "ABSENT_KEY"],
                "network": "default",
                **(settings or {}),
            },
        }
    )
    write_json(pending / "request.json", request.to_payload())
    return HostTree(pending / "request.json", workspace, output, prompt_path, answers_path)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"settings": {"volumes": ["/:/host"]}}, "invalid_settings"),
        ({"settings": {"network": "internal"}}, INTERNAL_NETWORK_ERROR),
        ({"prompt": "{{tool_name}} {{secret}}"}, "invalid_settings"),
    ],
)
def test_invalid_settings_fail_before_any_docker_call(tmp_path: Path, change: dict[str, Any], error: str) -> None:
    tree = host_tree(tmp_path, **change)
    docker = FakeDocker(tree.output, image=matching_image(IMAGE))
    assert tree.run(docker, FakeSidecar()) == 2
    assert tree.response()["error"].startswith(error)
    assert docker.calls == []


@pytest.mark.parametrize("field", ["unknown", "authorizations"])
def test_bad_operator_answers_fail_before_any_docker_call(tmp_path: Path, field: str) -> None:
    answers = smoke_answers()
    if field == "unknown":
        answers["journey_update"]["notes"] = "extra"
    else:
        answers["authorizations"]["docker"] = False
    tree = host_tree(tmp_path, answers=answers)
    docker = FakeDocker(tree.output, image=matching_image(IMAGE))
    assert tree.run(docker, FakeSidecar()) == 2
    assert tree.response()["error"].startswith("invalid_settings")
    assert docker.calls == []


def test_success_mounts_only_the_named_paths_and_removes_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRESENT_KEY", "provider-value")
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    tree = host_tree(tmp_path)
    sidecar = FakeSidecar()
    docker = FakeDocker(tree.output, image=matching_image(IMAGE), script=WRITE_RESPONSE)
    assert tree.run(docker, sidecar) == 0
    assert docker.names() == ["inspect", "remove", "start", "remove"]
    assert sidecar.stopped
    args = docker.calls[2][1]
    name = args[args.index("--name") + 1]
    assert re.fullmatch(r"eval-gen-case-0003-subset-000001-[0-9a-f]{12}", name)
    assert docker.calls[1] == docker.calls[3] == ("remove", name)
    assert "--add-host=host.docker.internal:host-gateway" in args
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]
    parsed = {dict(part.split("=", 1) for part in m.split(",") if "=" in part)["target"]: m for m in mounts}
    assert {target: m.endswith(",readonly") for target, m in parsed.items()} == EXPECTED_MOUNTS
    assert f"source={tree.workspace.resolve()}," in parsed["/input"]
    assert f"source={tree.prompt.resolve()}," in parsed["/mounts/prompt.md"]
    assert not any("docker.sock" in m or f"source={REPO_ROOT}," in m for m in mounts)
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "-e"] == [URL_ENV, TOKEN_ENV, "PRESENT_KEY"]
    assert (docker.env[URL_ENV], docker.env[TOKEN_ENV]) == (sidecar.container_url, sidecar.token)
    assert sidecar.token not in " ".join(args)
    assert args[-3:] == ["python3", IMAGE, "/opt/generation/entrypoint.py"]
    assert args[-4] == "--entrypoint"


def test_failed_container_records_its_exit_and_removes_the_container(tmp_path: Path) -> None:
    tree = host_tree(tmp_path)
    sidecar = FakeSidecar()
    docker = FakeDocker(tree.output, image=matching_image(IMAGE), script="raise SystemExit(5)")
    assert tree.run(docker, sidecar) == 5
    assert tree.response()["error"] == "container exited 5"
    assert docker.names()[-1] == "remove" and sidecar.stopped


@pytest.mark.parametrize(
    "image", [None, ImageInfo(IMAGE, {SOURCE_LABEL: "sha256:" + "0" * 64}), ImageInfo("sha256:" + "b" * 64)]
)
def test_image_mismatch_starts_no_container(tmp_path: Path, image: ImageInfo | None) -> None:
    tree = host_tree(tmp_path)
    docker = FakeDocker(tree.output, image=image)
    assert tree.run(docker, FakeSidecar()) == 2
    assert tree.response()["error"] == "image_mismatch"
    assert docker.names() == ["inspect", "remove"]


def test_sidecar_bind_failure_is_adapter_failed(tmp_path: Path) -> None:
    tree = host_tree(tmp_path)
    docker = FakeDocker(tree.output, image=matching_image(IMAGE))
    assert tree.run(docker, None) == 2
    assert tree.response()["error"] == "adapter_failed"
    assert docker.names() == ["inspect", "remove"]


def test_sigterm_stops_the_container_and_sidecar_and_exits_143(tmp_path: Path) -> None:
    tree = host_tree(tmp_path)
    sidecar = FakeSidecar()
    timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM))
    docker = FakeDocker(
        tree.output, image=matching_image(IMAGE), script="import time; time.sleep(60)", on_start=timer.start
    )
    previous = signal.getsignal(signal.SIGTERM)
    assert tree.run(docker, sidecar) == 143
    assert docker.names() == ["inspect", "remove", "start", "stop", "remove"]
    assert sidecar.stopped
    assert docker.process is not None and docker.process.poll() is not None
    assert tree.response()["error"] == "terminated"
    assert signal.getsignal(signal.SIGTERM) == previous


def test_agent_inputs_resolve_from_the_recorded_trial_config(tmp_path: Path) -> None:
    tree = host_tree(tmp_path, harbor={"import_path": "{input:source}"})
    request = GenerationRequest.model_validate(read_json(tree.request))
    trials = Path(request.metric_output_dir).parent / "workspace" / "trials"
    config = eval_author_generation.resolve_agent_config(request, trials)
    assert config.import_path == "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"


def test_container_name_falls_back_to_sanitized_ids(tmp_path: Path) -> None:
    request = GenerationRequest.model_validate(
        {
            "case_id": "Case 7/x",
            "eval_subset_id": "s:1",
            "tasks": [{"task_id": "alpha", "revision": 1}],
            "metric_output_dir": "/m",
            "agent": {"agent_bundle_id": "a", "revision": "1", "harbor": {}},
        }
    )
    path = tmp_path / "request.json"
    write_json(path, request.to_payload())
    assert re.fullmatch(r"eval-gen-Case-7-x-s-1-[0-9a-f]{12}", container_name(path, request))


# --- signing ---


def _reference_shaped_experiment(root: Path, *, answers: dict[str, Any] | None = None) -> Path:
    exp = write_sample_experiment(root, with_setup=False)
    shutil.copyfile(SMOKE / "prompt.md", exp.parent / "prompt.md")
    (exp.parent / "operator-answers.yaml").write_text(yaml.safe_dump(answers or smoke_answers()), encoding="utf-8")
    raw = yaml.safe_load(exp.read_text(encoding="utf-8"))
    raw["generation"]["digest"] = {"method": "glob", "patterns": ["prompt.md"]}
    raw["generation"]["settings"] = {
        "image_id": IMAGE,
        "prompt": {"path": "prompt.md"},
        "operator_answers": {"path": "operator-answers.yaml"},
        "agent_argv": ["codex"],
    }
    exp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return exp


def test_signing_checks_the_image_label_against_the_generation_digest(tmp_path: Path) -> None:
    exp = _reference_shaped_experiment(tmp_path / "exp")
    inspected: list[str] = []

    def labelled(label: str | None) -> Callable[[str], ImageInfo | None]:
        def inspect(image_id: str) -> ImageInfo | None:
            inspected.append(image_id)
            return None if label is None else ImageInfo(image_id, {SOURCE_LABEL: label})

        return inspect

    message = f"G image {IMAGE} missing or built from different skills; rebuild and re-sign"
    for label in (None, "sha256:" + "0" * 64):
        with pytest.raises(ValueError, match=re.escape(message)):
            sign_experiment(exp, image_inspector=labelled(label))
    assert not lock_path_for(exp).exists()
    from generation_benchmark.digests import glob_digest

    digest = glob_digest(["prompt.md"], [], exp.parent)
    sign_experiment(exp, image_inspector=labelled(digest))
    check_experiment(exp, image_inspector=labelled(digest))
    assert inspected == [IMAGE] * 4


def test_signing_rejects_bad_operator_answers(tmp_path: Path) -> None:
    answers = smoke_answers()
    answers["authorizations"]["harbor"] = False
    exp = _reference_shaped_experiment(tmp_path / "exp", answers=answers)
    with pytest.raises(ValueError, match="authorizations differs"):
        sign_experiment(exp, image_inspector=lambda image_id: pytest.fail("inspected before answers"))


def test_settings_without_image_id_never_inspect_an_image() -> None:
    def refuse(image_id: str) -> ImageInfo | None:
        raise AssertionError(f"inspected {image_id}")

    check_experiment(SMOKE / "generation-experiment.yml", image_inspector=refuse)


def test_committed_reference_lock_pins_the_skill_trees_and_settings() -> None:
    lock = read_json(lock_path_for(REFERENCE_EXPERIMENT))
    assert isinstance(lock, dict)
    assert lock_path_for(REFERENCE_EXPERIMENT).name == "generation-experiment-reference.lock.json"
    digest = lock["entries"]["generation.digest"]["sha256"]
    assert digest == generator_source_digest()
    for key in ("generation.settings.prompt", "generation.settings.operator_answers"):
        assert key in lock["entries"]
    settings = yaml.safe_load(REFERENCE_EXPERIMENT.read_text(encoding="utf-8"))["generation"]["settings"]
    check_experiment(REFERENCE_EXPERIMENT, image_inspector=lambda image_id: ImageInfo(image_id, {SOURCE_LABEL: digest}))
    assert ReferenceSettings.model_validate(settings).image_id == settings["image_id"]


# --- the G container (Docker) ---


@dataclass
class ReferenceRun:
    image: str
    experiment: Path
    work_root: Path

    @property
    def generation(self) -> Path:
        return self.work_root / "cases" / "case-0000" / "generation" / "subset-000000"

    @property
    def output(self) -> Path:
        return self.generation / "output"

    def response(self, output: Path | None = None) -> dict[str, Any]:
        payload = read_json((output or self.output) / "response.json")
        assert isinstance(payload, dict)
        return payload


def _events(transcript: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def reference_run(tmp_path_factory: pytest.TempPathFactory) -> ReferenceRun:
    """Run the reference smoke experiment from a mirror whose skill trees match the image just built."""
    image = build_image()
    checkout = tmp_path_factory.mktemp("reference") / "checkout"
    for name in SKILL_TREES:
        for rel, path in iter_tree_files(REPO_ROOT / "skills" / name):
            target = checkout / "skills" / name / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    smoke = checkout / "labs" / "generation-benchmark" / "examples" / "smoke"
    shutil.copytree(SMOKE, smoke, ignore=shutil.ignore_patterns("__pycache__"))
    raw = yaml.safe_load(REFERENCE_EXPERIMENT.read_text(encoding="utf-8"))
    raw["generation"]["settings"]["image_id"] = image
    raw["generation"]["settings"]["agent_argv"] += ["--write-marker", MARKER]
    experiment = smoke / REFERENCE_EXPERIMENT.name
    experiment.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    sign_experiment(experiment, resign=True)
    work_root = checkout.parent / "work"
    result = subprocess.run(
        [
            sys.executable,
            str(RUN_CLI),
            "--benchmark",
            str(experiment),
            "--run-id",
            "ref",
            "--work-root",
            str(work_root),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=3600,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    return ReferenceRun(image, experiment, work_root)


def launch(
    run: ReferenceRun,
    root: Path,
    *,
    settings: dict[str, Any] | None = None,
    max_gaps: int | None = None,
    workspace: Path | None = None,
    metric_output_dir: Path | None = None,
    start_sidecar: eval_author_generation.StartSidecar = start_harbor_sidecar,
) -> tuple[int, Path]:
    """Rerun the launcher on the published subset's request with changes, outside the work root."""
    request = read_json(run.generation / "request.json")
    assert isinstance(request, dict)
    request = copy.deepcopy(request)
    request["settings"].update(settings or {})
    request["max_gaps"] = max_gaps
    if metric_output_dir is not None:
        request["metric_output_dir"] = str(metric_output_dir)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "request.json", request)
    output = root / "output"
    output.mkdir()
    code = eval_author_generation.run(
        root / "request.json", workspace or run.generation / "workspace", output, start_sidecar=start_sidecar
    )
    return code, output


def _no_container(name: str) -> bool:
    listed = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return listed.stdout.strip() == ""


@docker_only
def test_reference_run_accepts_gamma_and_keeps_delta_a_draft(reference_run: ReferenceRun) -> None:
    output = reference_run.output
    response = reference_run.response()
    assert response["error"] is None and response["unattempted"] == []
    by_slug = {attempt["slug"]: attempt for attempt in response["attempts"]}
    assert sorted(by_slug) == ["cover-delta", "cover-gamma"]
    assert by_slug["cover-delta"]["status"] == "draft"
    assert by_slug["cover-delta"]["pending_question"] == "Run the closure trials for cover-delta?"
    assert (by_slug["cover-gamma"]["status"], by_slug["cover-gamma"]["task_path"]) == ("accepted", "tasks/cover-gamma")
    assert sorted(path.name for path in (output / "tasks").iterdir()) == ["cover-gamma"]
    lines = [json.loads(line) for line in (output / "logs" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(line["slug"], line["status"], line["pending_question"]) for line in lines] == [
        ("cover-delta", "draft", "Run the closure trials for cover-delta?"),
        ("cover-gamma", "accepted", None),
    ]
    verification = read_json(output / "logs" / "cover-gamma" / "verify.json")
    assert isinstance(verification, dict) and verification["accepted"] is True
    check = read_json(output / "logs" / "cover-gamma" / "check-acceptance.json")
    assert isinstance(check, dict) and check["accepted"] is True and check["valid"] is True
    for slug in by_slug:
        assert (output / "logs" / slug / "transcript.jsonl").stat().st_size > 0


@docker_only
def test_baseline_report_matches_the_stage2_gap_report(reference_run: ReferenceRun) -> None:
    gap = read_json(
        reference_run.work_root
        / "cases"
        / "case-0000"
        / "metric-subsets"
        / "subset-000000"
        / "output"
        / "audit-coverage-report.json"
    )
    baseline = read_json(reference_run.output / "baseline-report.json")
    assert covered_tools(baseline) == covered_tools(gap) == ["alpha", "beta"]
    assert reference_run.response()["baseline_report_path"] == "baseline-report.json"


@docker_only
def test_driver_sees_seeded_journey_state_and_a_restored_snapshot(reference_run: ReferenceRun) -> None:
    homes = set()
    for slug in ("cover-delta", "cover-gamma"):
        events = _events(reference_run.output / "logs" / slug / "transcript.jsonl")
        start = next(event for event in events if event["type"] == "start")
        homes.add(start["codex_home"])
        marker = next(event for event in events if event["type"] == "marker")
        assert marker["present_at_start"] is False, slug
        state = next(event for event in events if event["type"] == "show")["output"]["state"]
        assert state["ethos"]["stage"] == "complete" and state["ethos"]["confirmed_digest"]
        assert state["journey"] == "expand" and state["journey_status"]["expand"] == "in_progress"
        assert state["pending_question"] is None and state["blockers"] == []
        for key in ("python", "runtime"):
            assert state["prerequisites"][key]["status"] == "verified"
        assert "/opt/harbor-real/bin/python" in state["prerequisites"]["python"]["source"]
    assert len(homes) == 2


@docker_only
def test_output_tasks_are_plain_host_owned_copies(reference_run: ReferenceRun) -> None:
    task = reference_run.output / "tasks" / "cover-gamma"
    for path in [task, *task.rglob("*")]:
        info = path.lstat()
        assert not stat.S_ISLNK(info.st_mode)
        assert info.st_uid == os.getuid(), path
        assert info.st_mode & stat.S_IRUSR
    for script in ("tests/test.sh", "solution/solve.sh"):
        assert (task / script).stat().st_mode & stat.S_IXUSR
    assert "TOOL: gamma" in (task / "instruction.md").read_text(encoding="utf-8")
    assert _no_container("eval-gen-case-0000-subset-000000-")


@docker_only
def test_wrong_image_id_is_image_mismatch(reference_run: ReferenceRun, tmp_path: Path) -> None:
    code, output = launch(reference_run, tmp_path, settings={"image_id": "sha256:" + "0" * 64})
    assert code == 2
    assert reference_run.response(output)["error"] == "image_mismatch"
    request = GenerationRequest.model_validate(read_json(tmp_path / "request.json"))
    assert _no_container(container_name(tmp_path / "request.json", request))


@docker_only
def test_sidecar_that_cannot_bind_is_adapter_failed(reference_run: ReferenceRun, tmp_path: Path) -> None:
    with socket.socket() as busy:
        busy.bind((default_bind_host(), 0))
        busy.listen()
        port = busy.getsockname()[1]
        code, output = launch(reference_run, tmp_path, start_sidecar=functools.partial(start_harbor_sidecar, port=port))
    assert code == 2
    assert reference_run.response(output)["error"] == "adapter_failed"


@docker_only
def test_host_journey_state_is_replaced_and_max_gaps_truncates(reference_run: ReferenceRun, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    shutil.copytree(reference_run.generation / "workspace", workspace)
    working = workspace / ".eval-author" / ".working"
    working.mkdir(parents=True, exist_ok=True)
    (working / "journey-state.json").write_text(json.dumps({"repository": "/host/checkout"}), encoding="utf-8")
    (working / "journey-state.lock").write_text("", encoding="utf-8")
    code, output = launch(
        reference_run, tmp_path / "run", settings={"agent_argv": DRAFT_ONLY}, max_gaps=1, workspace=workspace
    )
    assert code == 0, reference_run.response(output)
    response = reference_run.response(output)
    assert [(a["slug"], a["status"]) for a in response["attempts"]] == [("cover-delta", "draft")]
    assert response["unattempted"] == ["gamma"]
    assert not (output / "tasks").exists() or not any((output / "tasks").iterdir())
    events = _events(output / "logs" / "cover-delta" / "transcript.jsonl")
    assert next(event for event in events if event["type"] == "show")["output"]["state"]["repository"] == "/workspace"


@docker_only
def test_altered_covered_set_is_baseline_measurement_mismatch(reference_run: ReferenceRun, tmp_path: Path) -> None:
    subset = tmp_path / "metric-subsets" / "subset-000000"
    shutil.copytree(reference_run.work_root / "cases" / "case-0000" / "metric-subsets" / "subset-000000", subset)
    report_path = subset / "output" / "audit-coverage-report.json"
    report = read_json(report_path)
    assert isinstance(report, dict)
    report["coverage_by_evidence_class"]["agent_under_test"]["covered"] = ["alpha"]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    code, output = launch(reference_run, tmp_path / "run", metric_output_dir=subset / "output")
    assert code == 3
    assert reference_run.response(output)["error"] == "baseline_measurement_mismatch"
    assert (output / "baseline-report.json").is_file()


@docker_only
def test_host_timeout_stops_container_and_sidecar(reference_run: ReferenceRun, tmp_path: Path) -> None:
    request = read_json(reference_run.generation / "request.json")
    assert isinstance(request, dict)
    request["settings"]["agent_argv"] = ["sleep", "600"]
    write_json(tmp_path / "request.json", request)
    (tmp_path / "output").mkdir()
    name = container_name(tmp_path / "request.json", GenerationRequest.model_validate(request))
    process = subprocess.Popen(
        [sys.executable, "-m", "eval_author_generation", "--request", "request.json"]
        + ["--workspace", str(reference_run.generation / "workspace"), "--output-dir", "output"],
        cwd=tmp_path,
        env=pythonpath_env(),
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    seen: list[str] = []
    agent_running = threading.Event()

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            seen.append(line)
            if "running the coding agent" in line:
                agent_running.set()

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    try:
        assert agent_running.wait(timeout=900), "".join(seen[-40:])
        os.killpg(process.pid, signal.SIGTERM)
        assert process.wait(timeout=90) == 143
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    reader.join(timeout=10)
    listening = re.search(r"sidecar listening on http://([\d.]+):(\d+)", "".join(seen))
    assert listening is not None
    host, port = listening.groups()
    assert _no_container(name)
    with pytest.raises(ConnectionRefusedError), socket.create_connection((host, int(port)), timeout=5):
        pass
