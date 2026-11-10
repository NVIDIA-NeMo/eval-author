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
    assert args == ["-W", "-f=${Version}", "apt"]
    print(os.environ.get("APT_TEST_VERSION", "2.8.4ubuntu0.1"), end="")
    sys.exit(int(os.environ.get("APT_TEST_QUERY_EXIT", "0")))
if Path(sys.argv[0]).name == "dpkg":
    assert args == ["--compare-versions", os.environ.get("APT_TEST_VERSION", "2.8.4ubuntu0.1"), "ge", "2.8.4ubuntu0.1"]
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


@pytest.mark.parametrize("version", ["2.8.4ubuntu0.1", "2.8.4ubuntu0.2"])
def test_patched_apt_allows_acquisition(apt_commands, version):
    run, _ = apt_commands
    result, state = run(APT_TEST_VERSION=version)
    assert result.returncode == 0, result.stderr
    assert len(state["finished"]) == 9


def test_recipe_bootstraps_patched_apt_without_retries_before_helper():
    recipe = (ROOT / "tools/live_shard.Dockerfile").read_text().replace("\\\n", " ")
    update = "apt-get update -o Acquire::Retries=0"
    bootstrap = "apt-get install -y --no-install-recommends -o Acquire::Retries=0 apt=2.8.4ubuntu0.1"
    helper = 'bash /opt/live_shard_apt.sh /var/cache/apt/archives "$APT_SNAPSHOT"'
    assert recipe.index(update) < recipe.index(bootstrap) < recipe.index(helper)
    assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in recipe
    assert "--allow-unauthenticated" not in recipe
    assert "apt-get upgrade" not in recipe and "apt-get dist-upgrade" not in recipe


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


@pytest.mark.parametrize("failure", ["transient", "permanent", "hash"])
def test_native_apt_retries_only_transient_downloads(tmp_path, failure):
    import hashlib
    import http.server
    import threading

    helper = Path("/usr/lib/apt/apt-helper")
    if not helper.is_file():
        pytest.skip("native APT required")
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
                "-o",
                "Acquire::Retries=1",
                "download-file",
                f"http://127.0.0.1:{server.server_port}/package.deb",
                str(output),
                f"SHA256:{digest}",
            ],
            env={"PATH": os.defpath, "HOME": str(tmp_path)},
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
