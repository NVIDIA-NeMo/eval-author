# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


def archive(tmp_path, name="root/file", link=None):
    path = tmp_path / "source.tgz"
    with tarfile.open(path, "w:gz") as tar:
        entry = tarfile.TarInfo(name)
        if link is None:
            entry.size = 3
            tar.addfile(entry, io.BytesIO(b"abc"))
        else:
            entry.type = tarfile.SYMTYPE
            entry.linkname = link
            tar.addfile(entry)
    return path


def test_verified_extract(tmp_path):
    module = builder()
    assert hasattr(module, "extract_verified"), "verified extraction missing"
    path = archive(tmp_path)
    dest = tmp_path / "extracted"
    module.extract_verified(path, dest, hashlib.sha256(path.read_bytes()).hexdigest())
    assert (dest / "root/file").read_bytes() == b"abc"


@pytest.mark.parametrize("name,link", [("../escape", None), ("/absolute", None), ("root/escape", "../../escape")])
def test_archive_cannot_escape(tmp_path, name, link):
    module = builder()
    assert hasattr(module, "extract_verified"), "verified extraction missing"
    path = archive(tmp_path, name, link)
    with pytest.raises(ValueError, match="archive"):
        module.extract_verified(path, tmp_path / "dest", hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize("digest", ["0" * 64, "A" * 64, "abc", None])
def test_archive_digest_is_checked_before_extraction(tmp_path, digest):
    module = builder()
    assert hasattr(module, "extract_verified"), "verified extraction missing"
    path = archive(tmp_path)
    with pytest.raises(ValueError, match="digest"):
        module.extract_verified(path, tmp_path / "dest", digest)
    assert not (tmp_path / "dest").exists()


BUILDER = Path(__file__).resolve().parents[1] / "tools/build_patched_opencode.py"
VERSION = "1.19.5+nodesink.e991596fecc9"


def builder():
    assert BUILDER.is_file(), "production builder is missing"
    spec = importlib.util.spec_from_file_location("build_patched_opencode", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def elf(tmp_path):
    binary = tmp_path / "opencode"
    binary.write_bytes(b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\0" + b"\0" * 44)
    return binary


def test_patch_rejects_drift_before_writing(tmp_path):
    module = builder()
    assert hasattr(module, "apply_patch"), "strict patch application missing"
    (tmp_path / "src").mkdir()
    target = tmp_path / "src/NodeSink.ts"
    target.write_text("old\n")
    patch = b"--- a/src/NodeSink.ts\n+++ b/src/NodeSink.ts\n@@ -1 +1 @@\n-old\n+new\n"
    expected = {"src/NodeSink.ts": (hashlib.sha256(b"old\n").hexdigest(), hashlib.sha256(b"new\n").hexdigest())}
    module.apply_patch(tmp_path, patch, expected)
    assert target.read_text() == "new\n"
    with pytest.raises(ValueError, match="digest"):
        module.apply_patch(tmp_path, patch, expected)
    assert target.read_text() == "new\n"


def test_patch_wrong_hunk_and_postimage_are_atomic(tmp_path):
    module = builder()
    assert hasattr(module, "apply_patch"), "strict patch application missing"
    target = tmp_path / "file"
    target.write_text("old\n")
    expected = {"file": (hashlib.sha256(b"old\n").hexdigest(), "0" * 64)}
    for old in ["old", "wrong"]:
        patch = f"--- a/file\n+++ b/file\n@@ -1 +1 @@\n-{old}\n+new\n".encode()
        with pytest.raises(ValueError, match="patch"):
            module.apply_patch(tmp_path, patch, expected)
        assert target.read_text() == "old\n"


def test_cli_rejects_release_and_reused_output(tmp_path):
    import os
    import sys

    module = builder()
    assert hasattr(module, "main"), "production CLI missing"
    for extra, env in [([], {"OPENCODE_RELEASE": "0"}), ([], {})]:
        result = subprocess.run(
            [sys.executable, str(BUILDER), "--output", str(tmp_path), *extra],
            env={"PATH": os.defpath, **env},
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "Build failed: validation_failed" in result.stderr
    assert not (tmp_path / "binary-manifest.json").exists()


def test_subprocess_environment_has_no_inherited_credentials(tmp_path, monkeypatch):
    module = builder()
    assert hasattr(module, "host_environment"), "isolated environment missing"
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-credential")
    monkeypatch.setenv("BUN_OPTIONS", "--preload /untrusted/file")
    env = module.host_environment(tmp_path)
    assert set(env) == {"HOME", "PATH", "LC_ALL", "TMPDIR", "DOCKER_CONFIG"}
    assert env["HOME"] == str(tmp_path / "home")
    assert "OPENAI_API_KEY" not in env and "BUN_OPTIONS" not in env


def test_archive_rejects_composed_symlink_escape(tmp_path):
    path = tmp_path / "links.tgz"
    with tarfile.open(path, "w:gz") as tar:
        for name, target in [("root/a", ".."), ("root/b", "a/../escape")]:
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.SYMTYPE
            entry.linkname = target
            tar.addfile(entry)
    with pytest.raises(ValueError, match="archive"):
        builder().extract_verified(path, tmp_path / "dest", hashlib.sha256(path.read_bytes()).hexdigest())


def test_real_child_exit_is_not_hidden_by_version_output(tmp_path):
    import os
    import sys

    module = builder()
    runtime = object.__new__(module.DockerBuild)
    runtime.output = tmp_path
    runtime.env = {"PATH": os.defpath}
    result = runtime.command([sys.executable, "-I", "-c", f"print({VERSION!r});raise SystemExit(7)"], check=False)
    assert result.returncode == 7
    with pytest.raises(ValueError, match="native"):
        module.native_manifest(elf(tmp_path), result, "linux/amd64")
    with pytest.raises(ValueError, match="command failed \\(7\\)"):
        runtime.command([sys.executable, "-I", "-c", "raise SystemExit(7)"])


@pytest.mark.parametrize("platform,machine", [("linux/amd64", 62), ("linux/arm64", 183)])
def test_native_platform_requires_matching_elf(tmp_path, platform, machine):
    binary = elf(tmp_path)
    data = bytearray(binary.read_bytes())
    data[18:20] = machine.to_bytes(2, "little")
    binary.write_bytes(data)
    result = subprocess.CompletedProcess([], 0, VERSION + "\n", "")
    assert builder().native_manifest(binary, result, platform)["expected_version"] == VERSION
    data[18:20] = b"\0\0"
    binary.write_bytes(data)
    with pytest.raises(ValueError, match="native ELF"):
        builder().native_manifest(binary, result, platform)


def test_cli_rejects_unpaired_or_malformed_dependency_hash(tmp_path):
    import os
    import sys

    for args in [
        ["--dependency-sha256", "0" * 64],
        ["--dependency-archive", str(tmp_path / "cache")],
        ["--dependency-archive", str(tmp_path / "cache"), "--dependency-sha256", "A" * 64],
    ]:
        output = tmp_path / "output"
        result = subprocess.run(
            [sys.executable, str(BUILDER), "--output", str(output), *args],
            env={"PATH": os.defpath},
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert not output.exists()


def test_container_archive_extract_drops_host_ownership(tmp_path):
    module = builder()
    assert hasattr(module.DockerBuild, "unpack"), "portable owned extraction missing"
    runtime = object.__new__(module.DockerBuild)
    calls = []
    runtime.put = lambda *args: calls.append(("put", *args))
    runtime.run = lambda *args: calls.append(args)
    archive = tmp_path / "source.tar.gz"
    runtime.unpack(archive, "/work/source", strip=1)
    assert calls == [
        ("put", archive, "/work/input.tar.gz"),
        ("tar", "--no-same-owner", "-xzf", "/work/input.tar.gz", "--strip-components=1", "-C", "/work/source"),
    ]


def test_reviewed_patch_and_version_identity():
    module = builder()
    module.verify_digest(module.PATCH_FILE, module.PATCH_SHA256)
    assert (
        module.PATCHED_VERSION == module.SELECTED_VERSION + "+nodesink." + module.PATCH_FILES["src/NodeSink.ts"][1][:12]
    )
    assert set(module.PATCH_FILES) == {"src/NodeSink.ts", "dist/NodeSink.js"}


def test_container_cleanup_refuses_other_owner(tmp_path):
    module = builder()
    runtime = object.__new__(module.DockerBuild)
    runtime.id, runtime.owner, runtime.output = "a" * 64, "selected-owner", tmp_path
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess([], 0, "someone-else\n", "")

    runtime.docker = docker
    with pytest.raises(ValueError, match="ownership"):
        runtime.close()
    assert len(calls) == 1 and calls[0][0] == "inspect"
    assert not (tmp_path / "container.json").exists()


def test_container_cleanup_reads_back_exact_id(tmp_path):
    import json

    module = builder()
    runtime = object.__new__(module.DockerBuild)
    runtime.id, runtime.owner, runtime.output = "a" * 64, "selected-owner", tmp_path
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess([], 0, "selected-owner\n" if args[0] == "inspect" else "", "")

    runtime.docker = docker
    runtime.close()
    assert calls[-2:] == [("rm", "--force", "a" * 64), ("ps", "-aq", "--filter", "id=" + "a" * 64)]
    assert json.loads((tmp_path / "container.json").read_text()) == {
        "id": "a" * 64,
        "owner": "selected-owner",
        "removed": True,
    }


def test_directory_copy_preserves_source_root(tmp_path):
    module = builder()
    runtime = object.__new__(module.DockerBuild)
    runtime.id = "owned"
    calls = []
    runtime.docker = lambda *args: calls.append(args)
    runtime.put(tmp_path / ".", "/work/source")
    assert calls == [("cp", str(tmp_path) + "/.", "owned:/work/source")]


def test_native_manifest_is_adapter_contract(tmp_path):
    import hashlib

    binary = elf(tmp_path)
    result = subprocess.CompletedProcess([], 0, VERSION + "\n", "")
    assert builder().native_manifest(binary, result, "linux/amd64") == {
        "schema_version": 1,
        "binary_path": str(binary.resolve()),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "expected_version": VERSION,
    }


@pytest.mark.parametrize("exit_code,version", [(7, VERSION), (0, "1.19.5"), (0, VERSION + "x"), (0, "")])
def test_native_exit_and_version_fail_closed(tmp_path, exit_code, version):
    result = subprocess.CompletedProcess([], exit_code, version + "\n", "")
    with pytest.raises(ValueError, match="native"):
        builder().native_manifest(elf(tmp_path), result, "linux/amd64")


def test_downloaded_manifest_relocation_checks_real_bytes_and_version(tmp_path, monkeypatch):
    module = builder()
    assert hasattr(module, "relocate_manifest"), "downloaded artifact relocation is missing"
    monkeypatch.syspath_prepend(str(BUILDER.parent))
    from patched_opencode_runtime import load_manifest

    original = tmp_path / "builder"
    original.mkdir()
    binary = original / "opencode"
    binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{VERSION}'\n")
    manifest = {
        "schema_version": 1,
        "binary_path": str(binary),
        "sha256": module.sha256(binary),
        "expected_version": VERSION,
    }
    (original / "binary-manifest.json").write_text(json.dumps(manifest))
    downloaded = tmp_path / "downloaded"
    shutil.move(original, downloaded)
    output = tmp_path / "relocated"
    output.mkdir()
    module.relocate_manifest(downloaded / "binary-manifest.json", downloaded / "opencode", output)
    bound = load_manifest(output / "binary-manifest.json")
    assert bound == {**manifest, "binary_path": str(downloaded / "opencode")}
    assert not original.exists()


@pytest.mark.parametrize("tamper", ["bytes", "version", "exit", "extra_field", "symlink", "duplicate"])
def test_downloaded_artifact_tamper_never_creates_ready_manifest(tmp_path, monkeypatch, tamper):
    module = builder()
    assert hasattr(module, "relocate_manifest"), "downloaded artifact relocation is missing"
    monkeypatch.syspath_prepend(str(BUILDER.parent))
    binary = tmp_path / "opencode"
    version = "wrong" if tamper == "version" else VERSION
    binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\nexit {7 if tamper == 'exit' else 0}\n")
    data = {
        "schema_version": 1,
        "binary_path": "/vanished-builder/opencode",
        "sha256": module.sha256(binary),
        "expected_version": VERSION,
    }
    if tamper == "bytes":
        binary.write_text("tampered")
    if tamper == "extra_field":
        data["payload"] = "private task data"
    if tamper == "symlink":
        link = tmp_path / "link"
        link.symlink_to(binary)
        binary = link
    text = json.dumps(data)
    if tamper == "duplicate":
        text = text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    source = tmp_path / "download.json"
    source.write_text(text)
    output = tmp_path / "ready"
    output.mkdir()
    with pytest.raises(ValueError):
        module.relocate_manifest(source, binary, output)
    assert not (output / "binary-manifest.json").exists()


@pytest.mark.parametrize(
    "error,category",
    [
        (ValueError("PRIVATE source/task payload"), "validation_failed"),
        (subprocess.TimeoutExpired(["PRIVATE"], 1), "timeout"),
        (OSError("PRIVATE host path"), "io_failed"),
    ],
)
def test_build_failure_publishes_only_allowlisted_aggregate(tmp_path, monkeypatch, capsys, error, category):
    module = builder()

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, "build", failed)
    raw, artifact = tmp_path / "raw", tmp_path / "artifact"
    assert module.main(["--output", str(raw), "--artifact-output", str(artifact), "--platform", "linux/amd64"]) == 1
    summary = json.loads((artifact / "build-summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["failure_category"] == category
    assert summary["source_commit"] == module.SOURCE_COMMIT
    assert summary["platform"] == "linux/amd64"
    assert summary["binary_sha256"] is None
    assert type(summary["elapsed_seconds"]) is float
    assert {p.name for p in artifact.iterdir()} == {"build-summary.json"}
    assert "PRIVATE" not in json.dumps(summary) + str(capsys.readouterr())


def test_build_success_stages_exact_allowlist_not_raw_provenance(tmp_path, monkeypatch):
    module = builder()

    def built(output, platform, *args, **kwargs):
        binary = elf(output)
        binary.chmod(0o755)
        (output / "build.log").write_text("PRIVATE task data")
        (output / "provenance.json").write_text('{"injected":"PRIVATE"}')
        data = module.native_manifest(binary, subprocess.CompletedProcess([], 0, VERSION, ""), platform)
        module.save_json(output / "binary-manifest.json", data)
        return data

    monkeypatch.setattr(module, "build", built)
    artifact = tmp_path / "artifact"
    assert (
        module.main(
            ["--output", str(tmp_path / "raw"), "--artifact-output", str(artifact), "--platform", "linux/amd64"]
        )
        == 0
    )
    assert {p.name for p in artifact.iterdir()} == {"opencode", "binary-manifest.json", "build-summary.json"}
    summary = json.loads((artifact / "build-summary.json").read_text())
    assert summary["status"] == "complete" and summary["failure_category"] is None
    assert summary["binary_sha256"] == module.sha256(artifact / "opencode")
    assert "PRIVATE" not in (artifact / "build-summary.json").read_text()


def test_output_creation_failure_cannot_leak_a_secondary_exception(tmp_path, capsys):
    blocker = tmp_path / "PRIVATE-path"
    blocker.write_text("not a directory")
    assert builder().main(["--output", str(blocker / "child"), "--platform", "linux/amd64"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "Build failed: io_failed\n"
    assert captured.out == ""


def test_summary_write_failure_remains_sanitized(tmp_path, monkeypatch, capsys):
    module = builder()
    original = module.save_json
    writes = []

    def save(path, data):
        writes.append(path)
        if len(writes) > 1:
            raise OSError("PRIVATE filesystem details")
        original(path, data)

    def fail(*args):
        raise ValueError("PRIVATE build details")

    monkeypatch.setattr(module, "save_json", save)
    monkeypatch.setattr(module, "build", fail)
    assert module.main(["--output", str(tmp_path / "raw"), "--artifact-output", str(tmp_path / "artifact")]) == 1
    assert "PRIVATE" not in str(capsys.readouterr())
    assert json.loads((tmp_path / "artifact/build-summary.json").read_text())["status"] == "incomplete"
