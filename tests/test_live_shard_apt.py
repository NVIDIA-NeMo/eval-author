# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/live_shard_apt.sh"
PACKAGES = ["python3", "python3-venv", "git", "curl", "ca-certificates", "bash", "iptables", "iproute2", "xz-utils"]


@pytest.fixture
def apt_commands(tmp_path):
    """Record Linux APT/chown boundaries; run real shell, chmod, and file moves."""
    binary = tmp_path / "bin"
    binary.mkdir()
    archive = tmp_path / "archives"
    archive.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"active": 0, "peak": 0, "calls": [], "finished": []}))
    fake = binary / "apt-get"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import fcntl, json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
state = Path(os.environ["APT_TEST_STATE"])
def update(callback):
    with state.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        value = json.load(stream)
        callback(value)
        stream.seek(0)
        json.dump(value, stream)
        stream.truncate()
def record(value):
    value["calls"].append([Path(sys.argv[0]).name, args])
if Path(sys.argv[0]).name == "dpkg-query":
    assert args[:2] == ["-W", "-f=${Version}"]
    assert args[2] in ("apt", "libapt-pkg6.0t64")
    version = os.environ.get("APT_TEST_VERSION" if args[2] == "apt" else "APT_TEST_LIB_VERSION", "2.8.4ubuntu0.1")
    print(version, end="")
    sys.exit(int(os.environ.get("APT_TEST_QUERY_EXIT" if args[2] == "apt" else "APT_TEST_LIB_QUERY_EXIT", "0")))
if Path(sys.argv[0]).name == "dpkg":
    assert args[0] == "--compare-versions" and args[2:] == ["ge", "2.8.4ubuntu0.1"]
    sys.exit(0 if args[1] in ("2.8.4ubuntu0.1", "2.8.4ubuntu0.2") else 1)
update(record)
if Path(sys.argv[0]).name == "chown":
    if args[0] == "_apt:root":
        assert Path(args[1]).stat().st_mode & 0o777 == 0o700
    sys.exit(int(os.environ.get("APT_TEST_CHOWN_EXIT", "0")))
if Path(sys.argv[0]).name == "apt-get":
    if "--print-uris" in args:
        print(os.environ["APT_TEST_PLAN"], end="")
        sys.exit(int(os.environ.get("APT_TEST_PLAN_EXIT", "0")))
    assert "--no-download" in args
    assert not json.loads(state.read_text())["active"]
    archive = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("Dir::Cache::archives=")))
    for destination in json.loads(state.read_text())["finished"]:
        assert (archive / Path(destination).name).read_bytes() == b"synthetic package"
    sys.exit(int(os.environ.get("APT_TEST_INSTALL_EXIT", "0")))
def start(value):
    value["active"] += 1
    value["peak"] = max(value["peak"], value["active"])
update(start)
time.sleep(0.1)
triples = args[args.index("download-file") + 1:]
assert len(triples) % 3 == 0
for url, destination, digest in zip(triples[::3], triples[1::3], triples[2::3]):
    Path(destination).write_bytes(b"synthetic package")
def finish(value):
    value["active"] -= 1
    value["finished"].extend(triples[1::3])
update(finish)
fail = os.environ.get("APT_TEST_DOWNLOAD_FAIL")
sys.exit(1 if fail and (fail == "1" or any(fail in name for name in triples[1::3])) else 0)
"""
    )
    fake.chmod(0o755)
    (binary / "apt-helper").symlink_to(fake)
    (binary / "chown").symlink_to(fake)
    (binary / "dpkg-query").symlink_to(fake)
    (binary / "dpkg").symlink_to(fake)
    env = {
        "PATH": str(binary) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "APT_TEST_STATE": str(state),
        "APT_TEST_PLAN": "".join(plan_entry(i) for i in range(9)),
    }

    def run(*, snapshot="20261105T000000Z", **changes):
        result = subprocess.run(
            ["/bin/bash", str(HELPER), str(archive), snapshot, *PACKAGES],
            env={**env, **changes},
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result, json.loads(state.read_text())

    return run, archive


def plan_entry(index):
    name = f"pkg{index}_1%3a2.0+ubuntu1_arm64.deb"
    return (
        f"'https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/pool/main/p/pkg/{name}' {name} 123 SHA256:{'a' * 64}\n"
    )


@pytest.mark.parametrize("version,query_exit", [("2.8.3", "0"), ("", "0"), ("", "1")])
def test_unpatched_or_missing_apt_rejects_before_acquisition(apt_commands, version, query_exit):
    run, archive = apt_commands
    result, state = run(APT_TEST_VERSION=version, APT_TEST_QUERY_EXIT=query_exit)
    assert result.returncode != 0
    assert not state["calls"]
    assert not list(archive.iterdir())


@pytest.mark.parametrize("version,query_exit", [("2.8.3", "0"), ("", "0"), ("", "1"), ("2.8.4ubuntu0.1", "1")])
def test_unpatched_or_missing_libapt_rejects_before_acquisition(apt_commands, version, query_exit):
    run, archive = apt_commands
    result, state = run(APT_TEST_LIB_VERSION=version, APT_TEST_LIB_QUERY_EXIT=query_exit)
    assert result.returncode != 0
    assert not state["calls"]
    assert not list(archive.iterdir())


@pytest.mark.parametrize("version", ["2.8.4ubuntu0.1", "2.8.4ubuntu0.2"])
def test_patched_apt_allows_acquisition(apt_commands, version):
    run, _ = apt_commands
    result, state = run(APT_TEST_VERSION=version)
    assert result.returncode == 0, result.stderr
    assert len(state["finished"]) == 9


def test_recipe_bootstraps_patched_apt_without_retries_before_helper():
    recipe = (ROOT / "tools/live_shard.Dockerfile").read_text().replace("\\\n", " ")
    update = "apt-get update -o Acquire::Retries=0"
    bootstrap = (
        "apt-get install -y --no-install-recommends -o Acquire::Retries=0 "
        "apt=2.8.4ubuntu0.1 libapt-pkg6.0t64=2.8.4ubuntu0.1"
    )
    helper = 'bash /opt/live_shard_apt.sh /var/cache/apt/archives "$APT_SNAPSHOT"'
    assert recipe.index(update) < recipe.index(bootstrap) < recipe.index(helper)
    assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in recipe
    assert "--allow-unauthenticated" not in recipe
    assert "apt-get upgrade" not in recipe and "apt-get dist-upgrade" not in recipe


@pytest.fixture
def bootstrap_commands(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    sources = tmp_path / "ubuntu.sources"
    state = tmp_path / "bootstrap.json"
    state.write_text("[]")
    fake = binary / "apt-get"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
state = Path(os.environ["APT_TEST_STATE"])
calls = json.loads(state.read_text())
source = Path(os.environ["APT_TEST_SOURCES"]).read_text()
calls.append([name, args, source])
state.write_text(json.dumps(calls))
if name == "apt-get":
    phase = "index" if args[0] == "update" else "bootstrap"
    if phase == "index" and "Suites: noble noble-updates noble-security" in source:
        phase = "full"
    sys.exit(7 if os.environ.get("APT_TEST_FAIL") == phase else 0)
if name == "dpkg-query":
    if args == ["-W"]:
        sys.exit(0)
    assert args[:2] == ["-W", "-f=${Version}"]
    package = args[2]
    version = os.environ.get("APT_TEST_APT" if package == "apt" else "APT_TEST_LIB", "2.8.4ubuntu0.1")
    print(version, end="")
    sys.exit(1 if os.environ.get("APT_TEST_MISSING") == package else 0)
if name == "dpkg":
    assert args[0] == "--compare-versions"
    assert args[2:] == ["ge", "2.8.4ubuntu0.1"]
    sys.exit(0 if args[1] in ("2.8.4ubuntu0.1", "2.8.4ubuntu0.2") else 1)
assert name == "dependency-helper"
"""
    )
    fake.chmod(0o755)
    for name in ("dpkg", "dpkg-query", "dependency-helper"):
        (binary / name).symlink_to(fake)
    helper = tmp_path / "helper.sh"
    helper.write_text('exec dependency-helper "$@"\n')
    recipe = (ROOT / "tools/live_shard.Dockerfile").read_text().replace("\\\n", " ")
    script = recipe.split("\nRUN ", 1)[1].split("\nRUN ", 1)[0]
    script = script.replace("/etc/apt/sources.list.d/ubuntu.sources", str(sources))
    script = script.replace("/opt/live_shard_apt.sh", str(helper))
    script = script.replace("/opt/execution-image-packages.txt", str(tmp_path / "packages.txt"))
    script = script.replace("/var/lib/apt/lists", str(tmp_path / "lists"))
    script = script.replace("/var/cache/apt/archives", str(tmp_path / "archives"))
    env = {
        "PATH": str(binary) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "APT_SNAPSHOT": "20261105T000000Z",
        "APT_TEST_STATE": str(state),
        "APT_TEST_SOURCES": str(sources),
    }

    def run(**changes):
        result = subprocess.run(
            ["/bin/sh", "-c", script],
            env={**env, **changes},
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result, json.loads(state.read_text())

    return run, tmp_path


def test_generated_recipe_bootstraps_security_only_before_full_update(bootstrap_commands):
    run, tmp_path = bootstrap_commands
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert [name for name, _, _ in calls] == [
        "apt-get",
        "apt-get",
        "dpkg-query",
        "dpkg",
        "dpkg-query",
        "dpkg",
        "apt-get",
        "dependency-helper",
        "dpkg-query",
    ]
    security = (
        "Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/\n"
        "Suites: noble-security\nComponents: main\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    full = security.replace("Suites: noble-security", "Suites: noble noble-updates noble-security").replace(
        "Components: main", "Components: main universe"
    )
    assert [source for _, _, source in calls] == [security] * 6 + [full] * 3
    update = [
        "update",
        "-o",
        "Acquire::Retries=0",
        "-o",
        "APT::Update::Error-Mode=any",
        "-o",
        "Acquire::CompressionTypes::Order=xz",
    ]
    assert calls[0][1] == update
    assert calls[1][1] == [
        "install",
        "-y",
        "--no-install-recommends",
        "-o",
        "Acquire::Retries=0",
        "apt=2.8.4ubuntu0.1",
        "libapt-pkg6.0t64=2.8.4ubuntu0.1",
    ]
    assert calls[2][1] == ["-W", "-f=${Version}", "apt"]
    assert calls[4][1] == ["-W", "-f=${Version}", "libapt-pkg6.0t64"]
    assert calls[6][1] == [arg.replace("Acquire::Retries=0", "Acquire::Retries=1") for arg in update]
    assert calls[7][1] == [str(tmp_path / "archives"), "20261105T000000Z", *PACKAGES]
    assert [line.split(" uptime=")[0] for line in result.stdout.splitlines()] == [
        "EA_TOOLING_STAGE=apt_index",
        "EA_TOOLING_STAGE=apt_bootstrap",
        "EA_TOOLING_STAGE=apt_full_index",
    ]


@pytest.mark.parametrize("failure,expected", [("index", 1), ("bootstrap", 2), ("full", 7)])
def test_generated_recipe_failed_apt_command_aborts_transaction(bootstrap_commands, failure, expected):
    run, tmp_path = bootstrap_commands
    result, calls = run(APT_TEST_FAIL=failure)
    assert result.returncode == 7
    assert len(calls) == expected
    assert calls[-1][0] == "apt-get"
    assert (tmp_path / "helper.sh").is_file()
    assert not (tmp_path / "packages.txt").exists()
    assert not any(name == "dependency-helper" for name, _, _ in calls)
    assert [args for name, args, _ in calls if name == "apt-get"].count(calls[-1][1]) == 1
    if failure != "full":
        assert all("Suites: noble-security\nComponents: main\n" in source for _, _, source in calls)
        assert all("Acquire::Retries=1" not in args for _, args, _ in calls)
        assert "EA_TOOLING_STAGE=apt_full_index" not in result.stdout


@pytest.mark.parametrize("package", ["apt", "libapt-pkg6.0t64"])
@pytest.mark.parametrize("version,missing", [("2.8.3", False), ("", False), ("", True), ("2.8.4ubuntu0.1", True)])
def test_generated_recipe_rejects_unpatched_or_missing_package_before_full_sources(
    bootstrap_commands, package, version, missing
):
    run, _ = bootstrap_commands
    result, calls = run(
        **{
            "APT_TEST_APT" if package == "apt" else "APT_TEST_LIB": version,
            "APT_TEST_MISSING": package if missing else "",
        }
    )
    assert result.returncode != 0
    assert [name for name, _, _ in calls[:2]] == ["apt-get", "apt-get"]
    assert len(calls) == (3 if package == "apt" else 5) + (0 if missing or not version else 1)
    assert calls[-1][0] == ("dpkg-query" if missing or not version else "dpkg")
    assert all("Suites: noble-security\nComponents: main\n" in source for _, _, source in calls)
    assert "EA_TOOLING_STAGE=apt_full_index" not in result.stdout


def test_recipe_removes_build_only_helper_after_installation():
    recipe = (ROOT / "tools/live_shard.Dockerfile").read_text().replace("\\\n", " ")
    install = recipe.index('bash /opt/live_shard_apt.sh /var/cache/apt/archives "$APT_SNAPSHOT"')
    remove = recipe.index("rm /opt/live_shard_apt.sh")
    assert install < remove < recipe.index("rm -rf /var/lib/apt/lists/*")
    assert "RUN " not in recipe[install:remove]
    assert recipe.count("/opt/live_shard_apt.sh") == 3  # COPY, execution, removal; no runtime consumer.


@pytest.mark.parametrize("snapshot", ["", ".*", "20261105T000000Z/pool", "20261105T000000Z\n"])
def test_snapshot_argument_is_validated_before_plan(apt_commands, snapshot):
    run, _ = apt_commands
    result, state = run(snapshot=snapshot)
    assert result.returncode != 0
    assert not state["calls"]


def test_snapshot_argument_selects_exact_transaction(apt_commands):
    run, _ = apt_commands
    snapshot = "20261104T000000Z"
    result, state = run(snapshot=snapshot, APT_TEST_PLAN=plan_entry(0).replace("20261105T000000Z", snapshot))
    assert result.returncode == 0, result.stderr
    assert len(state["finished"]) == 1


@pytest.fixture
def native_bootstrap_commands(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    state = tmp_path / "native.json"
    state.write_text("[]")
    fake = binary / "apt-get"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
state = Path(os.environ["APT_TEST_STATE"])
calls = json.loads(state.read_text())
calls.append([name, args])
state.write_text(json.dumps(calls))
if name == "curl":
    sys.exit(28)
elif name == "timeout":
    assert args[:3] == ["--kill-after=1s", "25s", "bash"]
    if os.environ.get("APT_TEST_DIAGNOSTIC_FAIL"):
        sys.exit(124)
    os.execv("/bin/bash", ["bash", *args[3:]])
elif name == "dpkg":
    assert args == ["--print-architecture"]
    print("amd64")
elif name == "dpkg-query":
    print("2.8.3")
elif name == "apt-get":
    phase = "update" if "update" in args else "download"
    if os.environ.get("APT_TEST_FAIL") == phase:
        sys.exit(7)
    if phase == "download":
        for package in ("apt", "libapt-pkg6.0t64"):
            Path(f"{package}_2.8.4ubuntu0.1_amd64.deb").write_bytes(b"synthetic archive")
elif name == "dpkg-deb":
    if args[0] == "--show":
        package = Path(args[2]).name.split("_")[0]
        print(package)
        print(os.environ.get("APT_TEST_VERSION", "2.8.4ubuntu0.1"))
        print("amd64")
    else:
        assert args[0] == "--extract"
        root = Path(args[2])
        (root / "usr/lib/apt/methods").mkdir(parents=True, exist_ok=True)
        (root / "usr/lib/x86_64-linux-gnu").mkdir(parents=True, exist_ok=True)
        (root / "usr/lib/x86_64-linux-gnu/libapt-pkg.so.6.0").touch()
        helper = root / "usr/lib/apt/apt-helper"
        helper.write_text("#!/bin/sh\\nprintf 'apt 2.8.4 (amd64)\\\\n'\\n")
        helper.chmod(0o755)
else:
    raise AssertionError(name)
"""
    )
    fake.chmod(0o755)
    for name in ("dpkg", "dpkg-query", "dpkg-deb", "curl", "timeout"):
        (binary / name).symlink_to(fake)
    root = tmp_path / "private-apt"

    def run(**changes):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "tools/bootstrap_native_apt.sh"), str(root)],
            env={"PATH": str(binary) + os.pathsep + os.defpath, "APT_TEST_STATE": str(state), **changes},
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result, json.loads(state.read_text()), root

    return run


@pytest.mark.parametrize("diagnostic_failure", [False, True])
@pytest.mark.parametrize("bootstrap_failure", ["", "update", "download"])
def test_native_diagnostic_is_bounded_prebootstrap_and_preserves_status(
    native_bootstrap_commands, diagnostic_failure, bootstrap_failure
):
    result, calls, root = native_bootstrap_commands(
        APT_TEST_DIAGNOSTIC_FAIL="1" if diagnostic_failure else "", APT_TEST_FAIL=bootstrap_failure
    )
    assert result.returncode == (7 if bootstrap_failure else 0)
    assert calls[0] == ["timeout", ["--kill-after=1s", "25s", "bash", str(ROOT / "tools/diagnose_native_apt_https.sh")]]
    assert [name for name, _ in calls].count("curl") == (0 if diagnostic_failure else 4)
    if bootstrap_failure:
        assert not any(name == "dpkg-deb" for name, _ in calls)
        assert not (root / "usr").exists()
        if bootstrap_failure == "update":
            assert sum(name == "apt-get" for name, _ in calls) == 1


def test_native_bootstrap_isolates_fixed_apt_from_vulnerable_host(native_bootstrap_commands):
    result, calls, root = native_bootstrap_commands()
    assert result.returncode == 0, result.stderr
    assert not any(name == "dpkg-query" for name, _ in calls)
    apt_calls = [args for name, args in calls if name == "apt-get"]
    assert len(apt_calls) == 2
    assert all("Acquire::Retries=0" in args for args in apt_calls)
    assert apt_calls[1][-3:] == ["download", "apt=2.8.4ubuntu0.1", "libapt-pkg6.0t64=2.8.4ubuntu0.1"]
    assert all("install" not in args and "upgrade" not in args for args in apt_calls)
    assert all(f"Dir::Etc::sourcelist={root}/ubuntu.sources" in args for args in apt_calls)
    assert all("Dir::Etc::sourceparts=-" in args for args in apt_calls)
    assert "Suites: noble-security\nComponents: main\n" in (root / "ubuntu.sources").read_text()
    assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in (root / "ubuntu.sources").read_text()
    assert (root / "usr/lib/apt/apt-helper").is_file()


@pytest.mark.parametrize("failure", ["update", "download"])
def test_native_bootstrap_failed_acquisition_never_extracts(native_bootstrap_commands, failure):
    result, calls, root = native_bootstrap_commands(APT_TEST_FAIL=failure)
    assert result.returncode == 7
    assert not any(name == "dpkg-deb" for name, _ in calls)
    assert not (root / "usr").exists()


@pytest.mark.parametrize("version", ["2.8.3", "", "2.8.4ubuntu0.2"])
def test_native_bootstrap_rejects_unexpected_archive_before_extraction(native_bootstrap_commands, version):
    result, calls, root = native_bootstrap_commands(APT_TEST_VERSION=version)
    assert result.returncode != 0
    assert not any(name == "dpkg-deb" and args[0] == "--extract" for name, args in calls)
    assert not (root / "usr").exists()


def test_ci_bootstraps_private_apt_before_both_python_test_groups():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.12", "3.13"]
    bootstrap = next(step for step in job["steps"] if "bootstrap_native_apt.sh" in step.get("run", ""))
    tests = next(step for step in job["steps"] if step.get("name") == "Run tests across runner CPUs")
    assert job["steps"].index(bootstrap) < job["steps"].index(tests)
    assert bootstrap["run"] == 'bash tools/bootstrap_native_apt.sh "$RUNNER_TEMP/native-apt"'
    assert bootstrap["timeout-minutes"] == 3
    assert tests["env"]["EVAL_AUTHOR_NATIVE_APT_ROOT"] == "${{ runner.temp }}/native-apt"


@pytest.fixture(scope="session")
def native_apt():
    private = os.environ.get("EVAL_AUTHOR_NATIVE_APT_ROOT")
    helper = Path(private) / "usr/lib/apt/apt-helper" if private else Path("/usr/lib/apt/apt-helper")
    if not helper.is_file():
        if private or os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("native APT bootstrap required on CI")
        pytest.skip("native APT required")
    env = {}
    if private:
        architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True, timeout=10).strip()
        triplet = {"amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}[architecture]
        env["LD_LIBRARY_PATH"] = str(Path(private) / "usr/lib" / triplet)
        assert (Path(env["LD_LIBRARY_PATH"]) / "libapt-pkg.so.6.0").is_file()
        for package in ("apt", "libapt-pkg6.0t64"):
            archive = Path(private) / "archives" / f"{package}_2.8.4ubuntu0.1_{architecture}.deb"
            metadata = subprocess.check_output(
                ["dpkg-deb", "--show", "--showformat=${Package}\n${Version}\n${Architecture}", str(archive)],
                text=True,
                timeout=10,
            ).strip()
            assert metadata == f"{package}\n2.8.4ubuntu0.1\n{architecture}"
        version = subprocess.check_output([str(helper), "--version"], env=env, text=True, timeout=10).strip()
        assert version == f"apt 2.8.4 ({architecture})"
    else:
        for package in ("apt", "libapt-pkg6.0t64"):
            installed = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", package], capture_output=True, text=True, timeout=10
            )
            assert installed.returncode == 0 and installed.stdout, "patched APT and libapt required"
            floor = subprocess.run(["dpkg", "--compare-versions", installed.stdout, "ge", "2.8.4ubuntu0.1"], timeout=10)
            assert floor.returncode == 0, "patched APT and libapt required for native retry test"
    return helper, env


@pytest.mark.parametrize("failure", ["transient", "permanent", "hash"])
def test_native_apt_retries_only_transient_downloads(tmp_path, failure, native_apt):
    import hashlib
    import http.server
    import threading

    helper, native_env = native_apt
    method_options = ["-o", f"Dir::Bin::methods={helper.parent / 'methods'}"]
    payload = b"synthetic package bytes"
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            code = 503 if failure == "transient" and len(requests) == 1 else 200
            if failure == "permanent":
                code = 404
            self.send_response(code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "package.deb"
    digest = hashlib.sha256(b"wrong" if failure == "hash" else payload).hexdigest()
    try:
        result = subprocess.run(
            [
                str(helper),
                *method_options,
                "-o",
                "Acquire::Retries=1",
                "download-file",
                f"http://127.0.0.1:{server.server_port}/package.deb",
                str(output),
                f"SHA256:{digest}",
            ],
            env={"PATH": os.defpath, "HOME": str(tmp_path), **native_env},
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(requests) == (2 if failure == "transient" else 1), result.stderr
    assert (result.returncode == 0) == (failure == "transient"), result.stderr
    if failure == "transient":
        assert output.read_bytes() == payload


def test_bounded_acquisition_precedes_same_no_download_install(apt_commands):
    run, archive = apt_commands
    result, state = run()
    assert result.returncode == 0, result.stderr
    assert 1 < state["peak"] <= 4
    assert len(state["finished"]) == 9
    apt = [args for name, args in state["calls"] if name == "apt-get"]
    assert len(apt) == 2
    assert "--print-uris" in apt[0] and "Acquire::ForceHash=SHA256" in apt[0]
    assert "--no-download" in apt[1]
    for args in apt:
        assert args[args.index("install") + 1 :] == PACKAGES
        assert "--no-install-recommends" in args
        assert "-y" in args
        assert f"Dir::Cache::archives={archive}" in args
    downloads = [args for name, args in state["calls"] if name == "apt-helper"]
    assert len(downloads) == 4
    assert sorted((len(args) - 3) // 3 for args in downloads) == [2, 2, 2, 3]
    for args in downloads:
        assert args[:3] == ["-o", "Acquire::Retries=1", "download-file"]
        for url, destination, digest in zip(args[3::3], args[4::3], args[5::3]):
            assert digest == "SHA256:" + "a" * 64
            assert Path(destination).parent.parent == archive
            assert url.endswith(Path(destination).name)
    assert [line.split(" uptime=")[0] for line in result.stdout.splitlines()] == [
        "EA_TOOLING_STAGE=apt_acquire",
        "EA_TOOLING_STAGE=apt_plan_validate",
        "EA_TOOLING_STAGE=apt_staging",
        "EA_TOOLING_STAGE=apt_download",
        "EA_TOOLING_STAGE=apt_promote",
        "EA_TOOLING_STAGE=apt_install",
        "EA_TOOLING_STAGE=apt_cleanup",
    ]
    for line in result.stdout.splitlines():
        assert re.fullmatch(r"EA_TOOLING_STAGE=[a-z_]+ uptime=(?:[0-9]{1,9}\.[0-9]{2})?", line)


@pytest.mark.parametrize(
    "bad_line",
    [
        plan_entry(0).replace("SHA256:", "MD5Sum:"),
        plan_entry(0).replace("a" * 64, "a" * 63),
        plan_entry(0).replace("'https:", "'file:"),
        plan_entry(0).replace("snapshot.ubuntu.com", "untrusted.example"),
        plan_entry(0).replace("20261105T000000Z", "20261104T000000Z"),
        plan_entry(0).replace("' pkg0_", "' ../pkg0_"),
        plan_entry(0).replace(" 123 ", " -1 "),
        plan_entry(0).replace(" 123 ", " 123;touch /tmp/injected "),
        plan_entry(0).rstrip() + " extra\n",
        "unexpected output\n",
        plan_entry(1),
    ],
)
def test_entire_plan_validated_before_any_download(apt_commands, bad_line):
    run, _ = apt_commands
    result, state = run(APT_TEST_PLAN=plan_entry(1) + bad_line)
    assert result.returncode != 0
    assert len(state["calls"]) == 1
    assert not state["finished"]
    assert "EA_TOOLING_STAGE=apt_install" not in result.stdout


@pytest.mark.parametrize("failure", ["plan", "download", "first_batch"])
def test_acquisition_failure_never_installs_or_retries(apt_commands, failure):
    run, archive = apt_commands
    result, state = run(
        **{
            ("APT_TEST_PLAN_EXIT" if failure == "plan" else "APT_TEST_DOWNLOAD_FAIL"): "pkg0_"
            if failure == "first_batch"
            else "1"
        }
    )
    assert result.returncode != 0
    assert sum(name == "apt-get" for name, _ in state["calls"]) == 1
    assert "EA_TOOLING_STAGE=apt_install" not in result.stdout
    assert len(state["finished"]) == len(set(state["finished"]))
    assert state["active"] == 0
    assert not list(archive.iterdir())
    if failure == "first_batch":
        assert len(state["finished"]) == 9


def test_failed_install_does_not_emit_cleanup_start(apt_commands):
    run, _ = apt_commands
    result, _ = run(APT_TEST_INSTALL_EXIT="7")
    assert result.returncode == 7
    assert "EA_TOOLING_STAGE=apt_install uptime=" in result.stdout
    assert "EA_TOOLING_STAGE=apt_cleanup" not in result.stdout


def test_empty_transaction_does_not_launch_downloader(apt_commands):
    run, _ = apt_commands
    result, state = run(APT_TEST_PLAN="")
    assert result.returncode == 0, result.stderr
    assert [name for name, _ in state["calls"]] == ["apt-get", "apt-get"]


def test_downloads_use_owned_private_staging_then_promote(apt_commands):
    run, archive = apt_commands
    before = archive.stat()
    result, state = run()
    assert result.returncode == 0, result.stderr
    staging = {Path(destination).parent for destination in state["finished"]}
    assert len(staging) == 1
    stage = staging.pop()
    assert stage != archive and stage.parent == archive
    assert ["chown", ["_apt:root", str(stage)]] in state["calls"]
    assert not stage.exists()
    assert archive.stat().st_mode == before.st_mode
    assert archive.stat().st_uid == before.st_uid
    assert len(list(archive.glob("*.deb"))) == 9
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in archive.glob("*.deb"))


def test_staging_ownership_failure_never_downloads(apt_commands):
    run, archive = apt_commands
    result, state = run(APT_TEST_CHOWN_EXIT="1")
    assert result.returncode != 0
    assert all(name != "apt-helper" for name, _ in state["calls"])
    assert "EA_TOOLING_STAGE=apt_install" not in result.stdout
    assert not list(archive.iterdir())


@pytest.mark.parametrize("count", [1, 4, 57])
def test_batch_coverage_and_process_count(apt_commands, count):
    run, archive = apt_commands
    result, state = run(APT_TEST_PLAN="".join(plan_entry(i) for i in range(count)))
    assert result.returncode == 0, result.stderr
    downloads = [args for name, args in state["calls"] if name == "apt-helper"]
    assert len(downloads) == min(4, count)
    assert 1 <= state["peak"] <= 4
    expected = {f"pkg{i}_1%3a2.0+ubuntu1_arm64.deb" for i in range(count)}
    assert {Path(destination).name for destination in state["finished"]} == expected
    assert len(state["finished"]) == count
    assert {path.name for path in archive.iterdir()} == expected
