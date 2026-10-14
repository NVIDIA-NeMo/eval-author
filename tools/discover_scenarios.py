#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Manual QA for eval-author-discover: Probe, Explore, Judge, and Solve across Harbor and NeMo Gym, in 20 steps.

How to run it
-------------
From anywhere, with any Python 3.11+ interpreter (the script itself needs only the standard library):

    python3 tools/discover_scenarios.py         # run all 20 steps, pausing after each
    python3 tools/discover_scenarios.py 14      # resume at step 14; the work dir must hold steps 1-13's state
    python3 tools/discover_scenarios.py --list  # list the steps

Each step says what it is about to do, does its own setup (copying files from fixtures/, installing
a runtime, and so on), runs discovery, shows the report and its ✓/✗ expectations, then waits:
"Press Enter to continue, or 'q' to Quit". Step 1 starts from a clean slate. Nothing is ever
restored: a step either adds something, often broken, or copies in the fix.

Before you start
----------------
- uv on PATH: step 1 creates the venvs with it, and steps 2-3 install into them. Network access is
  needed to download harbor==0.23.0 (Python 3.12) and nemo-gym==0.6.0 (Python 3.13) from PyPI.
- Docker Desktop: step 4 asks you to quit it, step 6 to start it again, and Solve runs containers.
- A NeMo Gym checkout at GYM_REPO: step 1 copies the Gym workloads for the fixtures from it.

Everything is written under the work dir, an absolute path, so the script runs from any directory.
Only discover.py and render_report.py are found relative to this file, in this repository's
skills/eval-author-discover/scripts. Delete the work dir to clean up; step 1 also wipes it, except
for replacing the venvs.

Reports are saved to <work dir>/reports/step-NN.json and .md, and the running grid to reports/grid.md.

Environment:
    W         Work dir. Default: /tmp/eval-author-discover-steps
    GYM_REPO  NeMo Gym checkout the Gym workloads are copied from. Default: ~/Documents/repos/gym
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

WORK_DIR = Path(os.environ.get("W", "/tmp/eval-author-discover-steps"))
REPORTS_DIR = WORK_DIR / "reports"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "skills" / "eval-author-discover" / "scripts"
FIXTURES = WORK_DIR / "fixtures"
HARBOR_REPO = WORK_DIR / "harbor-repo"
GYM_REPO_DIR = WORK_DIR / "gym-repo"
GYM_CHECKOUT = Path(os.environ.get("GYM_REPO", Path.home() / "Documents" / "repos" / "gym"))
VENV_HARBOR = WORK_DIR / "venv-harbor"
VENV_GYM = WORK_DIR / "venv-gym"

# nemo-gym 0.5+ needs Python 3.13.14 or later; on older Pythons uv silently picks 0.4.0, which lacks
# the `gym env validate/test <name>` commands discovery runs.
_POLICY = json.loads((PROJECT_ROOT / "skills/eval-author/scripts/runtime-policy.json").read_text())["providers"]
_HARBOR_PROFILE = _POLICY["harbor"]["test_profiles"]["manual_discovery"]
_GYM_PROFILE = _POLICY["gym"]["test_profiles"]["manual_discovery"]
HARBOR_PACKAGE = f"harbor=={_HARBOR_PROFILE['version']}"
HARBOR_PYTHON_VERSION = _HARBOR_PROFILE["python"]
GYM_PACKAGE = f"nemo-gym=={_GYM_PROFILE['version']}"
GYM_PYTHON_VERSION = _GYM_PROFILE["python"]
IGNORED = shutil.ignore_patterns(".venv", ".venv.setup.lock", "__pycache__")
EXAMPLE = "example_single_tool_call"


# --------------------------------------------------------------------------------------------
# Helpers


def python(venv: Path) -> Path:
    return venv / "bin" / "python"


def copy(fixture: str, destination: Path) -> None:
    """Copy one fixture file or folder into a repo, replacing what is there."""
    source = FIXTURES / fixture
    if destination.is_dir():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, ignore=IGNORED)
    else:
        shutil.copy2(source, destination)
    print(f"  copied fixtures/{fixture} -> {destination.relative_to(WORK_DIR)}")


def write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)


def create_venv(venv: Path, python_version: str) -> None:
    if venv.exists():
        shutil.rmtree(venv)
    subprocess.run(["uv", "venv", "--quiet", "--python", python_version, str(venv)], check=True)
    print(f"  created {venv.name} (Python {python_version}, nothing installed)")


def install(venv: Path, package: str) -> None:
    print(f"  installing {package} into {venv.name} ...")
    env = {**os.environ, "VIRTUAL_ENV": str(venv)}
    subprocess.run(["uv", "pip", "install", "--quiet", package], check=True, env=env)


def docker_running() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def wait_for_docker(running: bool) -> None:
    """Ask the user to start or quit Docker Desktop, and wait until it has."""
    while docker_running() != running:
        input(f"\n>>> {'Start' if running else 'Quit'} Docker Desktop, then press Enter. ")
    print(f"  Docker is {'running' if running else 'stopped'}")


def gym_on_path_env() -> dict[str, str]:
    """Put venv-gym's `gym` on PATH, so discovery run from venv-harbor finds both runtimes without mixing venvs."""
    return {**os.environ, "PATH": f"{VENV_GYM / 'bin'}{os.pathsep}{os.environ['PATH']}"}


# --------------------------------------------------------------------------------------------
# Expectations and reports


@dataclass
class Outcome:
    passed: int = 0
    failed: int = 0
    lines: list[str] = field(default_factory=list)


CURRENT = Outcome()


def expect(label: str, test: Callable[[], object]) -> None:
    """Record ✓ or ✗ for one expectation; an exception counts as ✗."""
    try:
        ok, detail = bool(test()), ""
    except Exception as exc:  # noqa: BLE001 - failing to evaluate is a failed expectation
        ok, detail = False, f"  ({type(exc).__name__}: {exc})"
    CURRENT.passed += ok
    CURRENT.failed += not ok
    CURRENT.lines.append(f"  {'✓' if ok else '✗'} {label}{detail}")


@dataclass
class Result:
    """One discovery run: its exit code, parsed JSON report, and where both reports were saved."""

    exit_code: int
    report: dict
    json_path: Path
    markdown_path: Path

    def check(self, name: str) -> list[dict]:
        """Every check with this name."""
        return [item for item in self.report["checks"] if item["name"] == name]

    def dataset(self, path: str) -> dict:
        return next(item for item in self.report["datasets"] if item["path"] == path)

    def manifest(self, name: str) -> dict:
        return next(item for item in self.report["gym_manifests"] if item["name"] == name)

    @property
    def summary(self) -> str:
        """The user-facing reply at the top of the Markdown report."""
        text = self.markdown_path.read_text()
        return text.split("Discovery phases:")[0].removeprefix("# Eval Discovery").strip()


def discover(venv: Path, repo: Path, step: int, env: dict[str, str] | None = None) -> Result:
    """Run discover.py with the venv's interpreter, then render its Markdown report next to the JSON."""
    print(f"\nRunning discovery on {repo.relative_to(WORK_DIR)} with {venv.name} ...")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run = subprocess.run(
        [str(python(venv)), str(SCRIPTS_DIR / "discover.py"), "--repo", str(repo)],
        capture_output=True,
        text=True,
        env=env,
    )
    if not run.stdout:
        sys.exit(f"discover.py printed nothing. stderr:\n{run.stderr}")
    json_path = REPORTS_DIR / f"step-{step:02d}.json"
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(run.stdout)
    rendered = subprocess.run(
        [str(python(venv)), str(SCRIPTS_DIR / "render_report.py"), str(json_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    markdown_path.write_text(rendered.stdout)
    return Result(run.returncode, json.loads(run.stdout), json_path, markdown_path)


def show(result: Result) -> None:
    """Display the rendered report (without its evidence JSON), then the expectations."""
    report = result.markdown_path.read_text().split("## Evidence JSON")[0].rstrip()
    print(f"\n{'-' * 40} report: {result.markdown_path} {'-' * 40}\n")
    print(report)
    print(f"\n{'-' * 40} exit code {result.exit_code}; expectations {'-' * 40}")
    print("\n".join(CURRENT.lines))


def solve_status(entry: dict) -> str | None:
    return (entry.get("solve") or {}).get("status")


def ran(result: Result, key: str) -> list[dict]:
    return [entry for entry in result.report[key] if solve_status(entry) in ("passed", "failed")]


# --------------------------------------------------------------------------------------------
# Fixtures: every file a step copies. Step 1 builds them.

DOCKERFILE = "FROM ubuntu:24.04\nWORKDIR /workspace\n"


def task_toml(name: str) -> str:
    return f"""schema_version = "1.4"

[task]
name = "qa/{name}"
version = "1.0.0"
description = "Add two numbers"
authors = []
keywords = ["addition"]

[metadata]
difficulty = "easy"
category = "math"
tags = ["arithmetic"]

[verifier]
timeout_sec = 60.0

[agent]
timeout_sec = 60.0

[environment]
build_timeout_sec = 600.0
workdir = "/workspace"
cpus = 1
memory_mb = 1024
storage_mb = 1024
gpus = 0
mcp_servers = []
"""


def addition_task(name: str, a: int, b: int) -> None:
    """fixtures/harbor/<name>: a task that adds a and b; fixtures/solutions/<name>: its solve.sh."""
    folder = FIXTURES / "harbor" / name
    write(folder / "task.toml", task_toml(name))
    write(folder / "instruction.md", f"Compute {a} + {b} and write only the result to `/workspace/answer.txt`.\n")
    write(folder / "environment" / "Dockerfile", DOCKERFILE)
    write(
        folder / "tests" / "test.sh",
        "#!/usr/bin/env bash\nmkdir -p /logs/verifier\n"
        f'if [ "$(tr -d \'[:space:]\' < /workspace/answer.txt)" = "{a + b}" ]; then echo 1; else echo 0; fi'
        " > /logs/verifier/reward.txt\n",
        executable=True,
    )
    write(
        FIXTURES / "solutions" / name / "solve.sh",
        f"#!/usr/bin/env bash\necho {a + b} > /workspace/answer.txt\n",
        executable=True,
    )


def build_harbor_fixtures() -> None:
    for number in range(1, 6):
        addition_task(f"add-{number}", 100 + number, 200 + number)
    addition_task("extra-1", 40, 2)
    # add-3 as first copied: everything except tests/test.sh.
    shutil.copytree(FIXTURES / "harbor" / "add-3", FIXTURES / "harbor" / "add-3-no-test")
    (FIXTURES / "harbor" / "add-3-no-test" / "tests" / "test.sh").unlink()
    write(
        FIXTURES / "solutions" / "add-2-wrong" / "solve.sh",
        "#!/usr/bin/env bash\necho 0 > /workspace/answer.txt\n",
        executable=True,
    )
    write(FIXTURES / "harbor-job.yaml", "job_name: qa\ndatasets:\n  - path: ./math\nagents:\n  - name: oracle\n")
    write(
        FIXTURES / "custom_eval" / "run_eval.py",
        "from opentelemetry import trace\n\n"
        'tracer = trace.get_tracer("addition-eval")\n\n\n'
        "def score(output: str, expected: int) -> float:\n"
        '    with tracer.start_as_current_span("score"):\n'
        "        return float(output.strip() == str(expected))\n",
    )
    write(FIXTURES / "custom_eval" / "cases.jsonl", '{"a": 12, "b": 34, "expected": 46}\n')


def copy_from_checkout(relative: str, destination: str, *, skip_data: bool = False) -> None:
    """Copy a Gym workload folder, dropping the manifest field nemo-gym 0.6.0 rejects."""
    ignore = shutil.ignore_patterns(".venv", ".venv.setup.lock", "__pycache__", *(["data"] if skip_data else []))
    target = FIXTURES / "gym" / destination
    shutil.copytree(GYM_CHECKOUT / relative, target, ignore=ignore)
    for manifest in target.rglob("manifest.yaml"):
        lines = manifest.read_text().splitlines(keepends=True)
        manifest.write_text("".join(line for line in lines if not line.startswith("experimental:")))


def build_gym_fixtures() -> None:
    gym = FIXTURES / "gym"
    copy_from_checkout(f"environments/{EXAMPLE}", "example-manifest")
    copy_from_checkout(f"resources_servers/{EXAMPLE}", "example-server")
    copy_from_checkout("benchmarks/mmlu_prox", "mmlu_prox", skip_data=True)
    copy_from_checkout("environments/indian_banking", "indian_banking-manifest")
    copy_from_checkout("resources_servers/indian_banking", "indian_banking-server")

    # The example server with a verifier fixture whose recorded expectation is wrong.
    cases = gym / "example-server" / "tests" / "verifier_cases.jsonl"
    shutil.copy2(cases, gym / "verifier_cases-correct.jsonl")
    shutil.copytree(gym / "example-server", gym / "example-server-bad-fixture")
    flipped = []
    for line in cases.read_text().splitlines():
        case = json.loads(line)
        if case.get("expected_reward") == 1.0:
            case["expected_reward"] = 0.0
        flipped.append(json.dumps(case))
    write(gym / "example-server-bad-fixture" / "tests" / "verifier_cases.jsonl", "\n".join(flipped) + "\n")

    # A second workload on the example server whose config first points at a file that does not exist.
    manifest = (gym / "example-manifest" / "manifest.yaml").read_text()
    write(
        gym / "example_second-broken" / "manifest.yaml",
        manifest.replace(f"name: {EXAMPLE}", "name: example_second", 1),
    )
    write(
        gym / "example_second-broken" / "config.yaml",
        "config_paths:\n- resources_servers/no_such_server/configs/none.yaml\n",
    )
    shutil.copy2(gym / "example-manifest" / "config.yaml", gym / "example_second-config.yaml")


# --------------------------------------------------------------------------------------------
# The 20 steps


def step_01() -> Result:
    """Probe: nothing installed."""
    print("  starting from a clean slate")
    for path in (FIXTURES, HARBOR_REPO, GYM_REPO_DIR, REPORTS_DIR):
        if path.exists():
            shutil.rmtree(path)
    HARBOR_REPO.mkdir(parents=True)
    GYM_REPO_DIR.mkdir(parents=True)
    build_harbor_fixtures()
    build_gym_fixtures()
    print(f"  built fixtures under {FIXTURES}")
    create_venv(VENV_HARBOR, HARBOR_PYTHON_VERSION)
    create_venv(VENV_GYM, GYM_PYTHON_VERSION)
    r = discover(VENV_HARBOR, HARBOR_REPO, 1)
    expect("exit 1", lambda: r.exit_code == 1)
    expect(
        "Probe invalid; Explore, Judge, Solve skipped",
        lambda: (
            r.report["phases"] == {"probe": "invalid", "explore": "skipped", "judge": "skipped", "solve": "skipped"}
        ),
    )
    expect("says neither Harbor nor NeMo Gym is installed", lambda: "nor NeMo Gym is installed" in r.summary)
    return r


def step_02() -> Result:
    """Probe: Harbor installed."""
    install(VENV_HARBOR, HARBOR_PACKAGE)
    r = discover(VENV_HARBOR, HARBOR_REPO, 2)
    expect("Probe valid", lambda: r.report["phases"]["probe"] == "valid")
    expect("Explore invalid: no evals found", lambda: r.report["phases"]["explore"] == "invalid")
    expect(
        "says Harbor is installed but no evals were found",
        lambda: r.summary.startswith("You have Harbor installed, but it doesn't look like you have any evals"),
    )
    expect("no Configs or Datasets tables", lambda: "## Configs" not in r.markdown_path.read_text())
    return r


def step_03() -> Result:
    """Probe: Gym installed."""
    install(VENV_GYM, GYM_PACKAGE)
    r = discover(VENV_GYM, HARBOR_REPO, 3)
    expect("Probe valid", lambda: r.report["phases"]["probe"] == "valid")
    expect("gym check passes", lambda: r.check("gym")[0]["status"] == "pass")
    expect("Harbor missing is a required failure", lambda: r.check("harbor")[0]["severity"] == "required")
    return r


def step_04() -> Result:
    """Explore a Harbor dataset (Docker stopped)."""
    wait_for_docker(False)
    copy("harbor/add-1", HARBOR_REPO / "math" / "add-1")
    copy("harbor/add-2", HARBOR_REPO / "math" / "add-2")
    copy("harbor/add-3-no-test", HARBOR_REPO / "math" / "add-3")
    r = discover(VENV_HARBOR, HARBOR_REPO, 4)
    expect(
        "dataset math with 3 tasks",
        lambda: [(d["path"], d["task_count"]) for d in r.report["datasets"]] == [("math", 3)],
    )
    expect("theme: math", lambda: r.dataset("math")["theme"]["categories"] == {"math": 3})
    expect("missing job config is only advisory", lambda: r.check("config")[0]["severity"] == "advisory")
    expect("dataset-tasks fails naming add-3", lambda: "add-3" in r.check("dataset-tasks")[0]["message"])
    expect("backend fails (Docker stopped)", lambda: r.check("backend")[0]["status"] == "fail")
    expect("Judge invalid", lambda: r.report["phases"]["judge"] == "invalid")
    return r


def step_05() -> Result:
    """Fix the invalid task."""
    copy("harbor/add-3/tests/test.sh", HARBOR_REPO / "math" / "add-3" / "tests" / "test.sh")
    r = discover(VENV_HARBOR, HARBOR_REPO, 5)
    expect("dataset-tasks passes", lambda: r.check("dataset-tasks")[0]["status"] == "pass")
    expect("backend still fails (Docker stopped)", lambda: r.check("backend")[0]["status"] == "fail")
    expect("says to install or start Docker", lambda: "install Docker" in r.summary)
    return r


def step_06() -> Result:
    """Start Docker."""
    wait_for_docker(True)
    r = discover(VENV_HARBOR, HARBOR_REPO, 6)
    expect("backend passes", lambda: r.check("backend")[0]["status"] == "pass")
    expect("Judge valid", lambda: r.report["phases"]["judge"] == "valid")
    expect("Solve skipped: no solutions", lambda: r.dataset("math")["solve"]["status"] == "skipped")
    expect("runnable, exit 0", lambda: r.report["runnable"] and r.exit_code == 0)
    return r


def step_07() -> Result:
    """Add a job config."""
    copy("harbor-job.yaml", HARBOR_REPO / "harbor-job.yaml")
    r = discover(VENV_HARBOR, HARBOR_REPO, 7)
    expect("config found", lambda: [c["path"] for c in r.report["configs"]] == ["harbor-job.yaml"])
    expect("config passes Harbor's ladder", lambda: r.report["configs"][0]["runnable"])
    expect("Judge valid", lambda: r.report["phases"]["judge"] == "valid")
    return r


def step_08() -> Result:
    """Add a custom eval."""
    copy("custom_eval", HARBOR_REPO / "custom_eval")
    r = discover(VENV_HARBOR, HARBOR_REPO, 8)
    expect("custom_eval listed", lambda: any(c["path"] == "custom_eval" for c in r.report["other_eval_candidates"]))
    expect("asks whether to convert it", lambda: "convert any of it" in r.summary)
    return r


def step_09() -> Result:
    """Solve with a wrong solution."""
    for number in (1, 3):
        copy(f"solutions/add-{number}", HARBOR_REPO / "math" / f"add-{number}" / "solution")
    copy("solutions/add-2-wrong", HARBOR_REPO / "math" / "add-2" / "solution")
    r = discover(VENV_HARBOR, HARBOR_REPO, 9)
    expect("Solve ran 3 tasks", lambda: len(r.dataset("math")["solve"]["results"]) == 3)
    expect("Solve valid", lambda: r.report["phases"]["solve"] == "valid")
    expect("solve-reward advisory names add-2", lambda: "add-2" in r.check("solve-reward")[0]["message"])
    expect("no celebration: add-2 earned no reward", lambda: ("🎉" in r.markdown_path.read_text()) is False)
    return r


def step_10() -> Result:
    """Fix the wrong solution."""
    copy("solutions/add-2", HARBOR_REPO / "math" / "add-2" / "solution")
    r = discover(VENV_HARBOR, HARBOR_REPO, 10)
    expect("no solve-reward advisory", lambda: not r.check("solve-reward"))
    expect("Solve valid", lambda: r.report["phases"]["solve"] == "valid")
    expect("celebration line: everything operational", lambda: ("🎉" in r.markdown_path.read_text()) is True)
    return r


def step_11() -> Result:
    """Add a second dataset."""
    copy("harbor/extra-1", HARBOR_REPO / "extra" / "extra-1")
    copy("solutions/extra-1", HARBOR_REPO / "extra" / "extra-1" / "solution")
    r = discover(VENV_HARBOR, HARBOR_REPO, 11)
    expect("both datasets solved", lambda: [solve_status(d) for d in r.report["datasets"]] == ["passed", "passed"])
    expect("Solve valid", lambda: r.report["phases"]["solve"] == "valid")
    return r


def step_12() -> Result:
    """Sampling limits."""
    for number in (4, 5):
        copy(f"harbor/add-{number}", HARBOR_REPO / "math" / f"add-{number}")
        copy(f"solutions/add-{number}", HARBOR_REPO / "math" / f"add-{number}" / "solution")
    for number in (1, 2, 3):
        copy(f"harbor/add-{number}", HARBOR_REPO / f"d{number}" / f"add-{number}")
        copy(f"solutions/add-{number}", HARBOR_REPO / f"d{number}" / f"add-{number}" / "solution")
    r = discover(VENV_HARBOR, HARBOR_REPO, 12)
    statuses = lambda: [solve_status(d) for d in r.report["datasets"]]  # noqa: E731
    expect("Solve ran 4 datasets", lambda: len(ran(r, "datasets")) == 4)
    expect("1 dataset not sampled", lambda: statuses().count("not sampled") == 1)
    math = lambda: r.dataset("math")["solve"]  # noqa: E731
    expect("math ran 4 of its 5 tasks", lambda: len(math()["results"]) == 4)
    return r


def step_13() -> Result:
    """Gym manifest without its data."""
    copy("gym/example-manifest", GYM_REPO_DIR / "environments" / EXAMPLE)
    r = discover(VENV_GYM, GYM_REPO_DIR, 13)
    manifest = lambda: r.manifest(EXAMPLE)  # noqa: E731
    expect("manifest found with missing data", lambda: manifest()["missing_data"])
    expect("Judge skipped naming the missing file", lambda: "example.jsonl" in manifest()["judge"]["reason"])
    return r


def step_14() -> Result:
    """Gym resources server with a bad verifier fixture."""
    copy("gym/example-server-bad-fixture", GYM_REPO_DIR / "resources_servers" / EXAMPLE)
    r = discover(VENV_GYM, GYM_REPO_DIR, 14)
    expect("Judge valid (gym env validate)", lambda: r.manifest(EXAMPLE)["judge"]["status"] == "passed")
    expect("Solve invalid (gym env test)", lambda: r.manifest(EXAMPLE)["solve"]["status"] == "failed")
    return r


def step_15() -> Result:
    """Fix the verifier fixture."""
    copy(
        "gym/verifier_cases-correct.jsonl",
        GYM_REPO_DIR / "resources_servers" / EXAMPLE / "tests" / "verifier_cases.jsonl",
    )
    r = discover(VENV_GYM, GYM_REPO_DIR, 15)
    expect("Solve valid", lambda: r.manifest(EXAMPLE)["solve"]["status"] == "passed")
    expect("runnable, exit 0", lambda: r.report["runnable"] and r.exit_code == 0)
    expect("no Harbor checks: Gym handles these manifests", lambda: not r.check("harbor") and not r.check("harbor-cli"))
    return r


def step_16() -> Result:
    """Benchmark without prepared data."""
    copy("gym/mmlu_prox", GYM_REPO_DIR / "benchmarks" / "mmlu_prox")
    r = discover(VENV_GYM, GYM_REPO_DIR, 16)
    expect(
        "skipped with the prepare command",
        lambda: "gym eval prepare --benchmark mmlu_prox" in r.manifest("mmlu_prox")["judge"]["reason"],
    )
    expect("everything else still passes", lambda: r.report["runnable"])
    return r


def step_17() -> Result:
    """Gym workload with partial data."""
    copy("gym/indian_banking-manifest", GYM_REPO_DIR / "environments" / "indian_banking")
    copy("gym/indian_banking-server", GYM_REPO_DIR / "resources_servers" / "indian_banking")
    r = discover(VENV_GYM, GYM_REPO_DIR, 17)
    reason = lambda: r.manifest("indian_banking")["judge"]["reason"]  # noqa: E731
    expect("skipped listing train and validation", lambda: "train.jsonl" in reason() and "validation.jsonl" in reason())
    expect("everything else still passes", lambda: r.report["runnable"])
    return r


def step_18() -> Result:
    """Gym manifest with a broken config."""
    copy("gym/example_second-broken", GYM_REPO_DIR / "environments" / "example_second")
    r = discover(VENV_GYM, GYM_REPO_DIR, 18)
    expect("example_second judged failed", lambda: r.manifest("example_second")["judge"]["status"] == "failed")
    expect("hint gives the reproduce command", lambda: "gym env validate" in r.check("gym-validate")[0]["hint"])
    expect("Judge invalid", lambda: r.report["phases"]["judge"] == "invalid")
    expect(
        "Solve still runs the manifest that passed Judge", lambda: r.manifest(EXAMPLE)["solve"]["status"] == "passed"
    )
    expect("Solve skips the rejected one", lambda: r.manifest("example_second")["solve"]["status"] == "skipped")
    expect("not runnable, exit 1", lambda: not r.report["runnable"] and r.exit_code == 1)
    return r


def step_19() -> Result:
    """Copy the corrected config."""
    copy("gym/example_second-config.yaml", GYM_REPO_DIR / "environments" / "example_second" / "config.yaml")
    r = discover(VENV_GYM, GYM_REPO_DIR, 19)
    expect("example_second judged passed", lambda: r.manifest("example_second")["judge"]["status"] == "passed")
    expect("example_second solved", lambda: r.manifest("example_second")["solve"]["status"] == "passed")
    expect("Judge and Solve valid", lambda: r.report["phases"]["judge"] == r.report["phases"]["solve"] == "valid")
    return r


def step_20() -> Result:
    """Both runtimes together."""
    copy("gym/example-manifest", HARBOR_REPO / "environments" / EXAMPLE)
    copy("gym/example-server", HARBOR_REPO / "resources_servers" / EXAMPLE)
    copy("gym/example-manifest", HARBOR_REPO / "environments" / "example_second")
    copy("gym/example_second-config.yaml", HARBOR_REPO / "environments" / "example_second" / "config.yaml")
    manifest = HARBOR_REPO / "environments" / "example_second" / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace(f"name: {EXAMPLE}", "name: example_second", 1))
    r = discover(VENV_HARBOR, HARBOR_REPO, 20, env=gym_on_path_env())
    runtime = r.report["runtime"]
    expect("Probe finds both", lambda: runtime["harbor_importable"] and runtime["gym_available"])
    expect("Judge valid for both halves", lambda: r.report["phases"]["judge"] == "valid")
    expect("Solve ran 2 Harbor datasets", lambda: len(ran(r, "datasets")) == 2)
    expect("Solve ran 2 Gym manifests", lambda: len(ran(r, "gym_manifests")) == 2)
    expect("runnable, exit 0: everything green", lambda: r.report["runnable"] and r.exit_code == 0)
    expect("celebration line: everything operational", lambda: ("🎉" in r.markdown_path.read_text()) is True)
    return r


STEPS = [
    step_01, step_02, step_03, step_04, step_05, step_06, step_07, step_08, step_09, step_10,
    step_11, step_12, step_13, step_14, step_15, step_16, step_17, step_18, step_19, step_20,
]  # fmt: skip


def write_grid(rows: list[tuple[int, str, Outcome]]) -> str:
    lines = ["| Step | What | Expectations |", "|---|---|---|"]
    for number, title, outcome in rows:
        mark = "✅" if not outcome.failed else "❌"
        lines.append(f"| {number} | {title} | {mark} {outcome.passed} ✓ / {outcome.failed} ✗ |")
    text = "\n".join(lines) + "\n"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "grid.md").write_text(text)
    return text


def main() -> None:
    global CURRENT
    if "--list" in sys.argv:
        for number, step in enumerate(STEPS, start=1):
            print(f"{number:2}. {step.__doc__}")
        return
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    rows: list[tuple[int, str, Outcome]] = []
    for number, step in enumerate(STEPS[start - 1 :], start=start):
        print(f"\n{'=' * 100}\nStep {number} of {len(STEPS)}: {step.__doc__}\n{'=' * 100}")
        CURRENT = Outcome()
        result = step()
        show(result)
        rows.append((number, step.__doc__ or "", CURRENT))
        write_grid(rows)
        if number < len(STEPS):
            if input("\nPress Enter to continue, or 'q' to Quit: ").strip().lower() == "q":
                break
    print("\n" + write_grid(rows))


if __name__ == "__main__":
    main()
