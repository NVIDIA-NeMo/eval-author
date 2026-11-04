#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build the source-pinned, locally patched OpenCode CLI; never publish a release."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath


def verify_digest(path: Path, expected: str) -> None:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("malformed SHA256 digest")
    if path.is_symlink() or sha256(path) != expected:
        raise ValueError(f"digest mismatch: {path.name}")


def extract_verified(archive: Path, dest: Path, digest: str) -> None:
    verify_digest(archive, digest)
    if dest.exists() or dest.is_symlink():
        raise ValueError("archive destination exists")
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.name in names:
                raise ValueError("unsafe archive path")
            names.add(member.name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("unsupported archive entry")
            target = dest / member.name
            if member.issym() and not (target.parent / member.linkname).resolve().is_relative_to(dest.resolve()):
                raise ValueError("unsafe archive link")
        links = {PurePosixPath(m.name) for m in members if m.issym()}
        if any(any(parent in links for parent in PurePosixPath(m.name).parents) for m in members):
            raise ValueError("archive writes through symlink")
        dest.mkdir(mode=0o700)
        # Links are installed last, and no archive entry can traverse them.
        for member in members:
            target = dest / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError("missing archive file contents")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        for member in members:
            if member.issym():
                target = dest / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
        try:
            if any(not (dest / link).resolve().is_relative_to(dest.resolve()) for link in links):
                raise ValueError("unsafe composed archive link")
        except (OSError, RuntimeError, ValueError) as error:
            for link in links:
                (dest / link).unlink()
            raise ValueError("unsafe archive link graph") from error


SELECTED_VERSION = "1.19.5"
SOURCE_COMMIT = "810c0eba1a7348036f8bdf61e6632d0c36c6876c"
SOURCE_URL = f"https://codeload.github.com/anomalyco/opencode/tar.gz/{SOURCE_COMMIT}"
SOURCE_SHA256 = "f1585ecebdf2dae5b17d3c588741e831326d81e651c04e886b6356e0dce74b8f"
LOCK_SHA256 = "f65cf6d303cf0634b8e09bb545b99de33c6be31d4314008f4b5e7a4a85bb9327"
BUN_VERSION = "1.4.4"
BUN_IMAGES = {
    "linux/amd64": "oven/bun@sha256:3f23864d3e8c923975c61971cabe67ee3d5a728f1e25a559256b95af719a3478",
    "linux/arm64": "oven/bun@sha256:f2b798e7ce1c056cd2149a6be7f014b3fde68a0c7165a3011ce3896e8404a6cd",
}
PATCH_FILE = Path(__file__).parent / "patches/opencode-nodesink-lifetime.patch"
PATCH_SHA256 = "0b115f0999e1459e4a10958b66f8a3d2d5e3c26672ba4d9f790dfaf560fcb6c9"
PATCH_FILES = {
    "src/NodeSink.ts": (
        "0ae36e1a60e5c5bb90e500229c60ccfd5b90b6776ea879541f0846b078852f52",
        "e991596fecc9e78c947aae26a414e44d2ac84871a946ce92f10e66c515ccd793",
    ),
    "dist/NodeSink.js": (
        "5ba09ce2b5773a807c4670142e65717dd9be351c50255943bdc10eb91ab6d077",
        "2e78d106edff080389aaad72ce71bfff1e6f20e3be5bbec74b13e8eb794aeea0",
    ),
}
EFFECT_URL = "https://registry.npmjs.org/@effect/platform-node-shared/-/platform-node-shared-4.0.0-beta.83.tgz"
EFFECT_INTEGRITY = "sha512-+yr/+PJmKTgmJq1QOINSBPgLu7Cjc4CZcotBXnGjyDEizOmimFgTkN2B8PBJAKIKUWYWfobjXqC+58/VhhPKAw=="
# This complete upstream catalog is published in the pinned source. Do not use
# the mutable live API or a synthetic/minimal model fixture for production builds.
MODELS_PATH = "packages/opencode/test/tool/fixtures/models-api.json"
MODELS_SHA256 = "876afae217cdff37c7267ba757d4d375a64eb52cdb2caa24c7f027b5142e3265"
BUILD_ARGS = [
    "--no-env-file",
    "run",
    "packages/opencode/script/build.ts",
    "--single",
    "--skip-install",
    "--skip-embed-web-ui",
]
INSTALL_ARGS = [
    "--no-env-file",
    "install",
    "--frozen-lockfile",
    "--ignore-scripts",
    "--filter",
    "opencode",
    "--os=*",
    "--cpu=*",
    "--libc=*",
]
PATCHED_VERSION = "1.19.5+nodesink.e991596fecc9"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def apply_patch(root: Path, patch: bytes, expected: dict[str, tuple[str, str]]) -> None:
    headers = re.findall(r"^--- a/(.+)\n\+\+\+ b/(.+)$", patch.decode(), re.MULTILINE)
    if len(headers) != len(expected) or {a for a, b in headers if a == b} != set(expected):
        raise ValueError("patch file scope mismatch")
    for name, (before, _) in expected.items():
        if not (root / name).resolve().is_relative_to(root.resolve()):
            raise ValueError("patch target escapes package")
        verify_digest(root / name, before)
    with tempfile.TemporaryDirectory(prefix="patch-", dir=root.parent) as directory:
        stage = Path(directory)
        for name in expected:
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, stage / name)
        result = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1"],
            input=patch,
            cwd=stage,
            env={"PATH": os.defpath, "LC_ALL": "C"},
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise ValueError("patch application failed")
        for name, (_, after) in expected.items():
            if sha256(stage / name) != after:
                raise ValueError("patch postimage mismatch")
        for name in expected:
            # Replace, not truncate: never modify hard-linked package cache bytes.
            os.replace(stage / name, root / name)


def native_manifest(binary: Path, result: subprocess.CompletedProcess, platform: str) -> dict:
    if result.returncode or result.stdout.strip() != PATCHED_VERSION:
        raise ValueError("native version check failed")
    with binary.open("rb") as stream:
        header = stream.read(64)
    machine = {"linux/amd64": 62, "linux/arm64": 183}.get(platform)
    if len(header) < 64 or header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != machine:
        raise ValueError("native ELF architecture check failed")
    return {
        "schema_version": 1,
        "binary_path": str(binary.resolve()),
        "sha256": sha256(binary),
        "expected_version": PATCHED_VERSION,
    }


def host_environment(output: Path) -> dict[str, str]:
    return {
        "HOME": str(output / "home"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LC_ALL": "C",
        "TMPDIR": str(output / "tmp"),
        "DOCKER_CONFIG": str(output / "docker-config"),
    }


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class DockerBuild:
    def __init__(self, output: Path, platform: str):
        self.output = output
        self.platform = platform
        self.env = host_environment(output)
        for name in ("home", "tmp", "docker-config"):
            (output / name).mkdir(mode=0o700)
        docker = shutil.which("docker")
        if not docker:
            raise ValueError("Docker CLI is required")
        # Local Unix socket only: never forward remote Docker TLS credentials.
        socket = Path("/var/run/docker.sock")
        if not socket.exists():
            socket = Path.home() / ".docker/run/docker.sock"
        if not socket.exists():
            raise ValueError("local Docker socket not found")
        self.argv = [docker, "--config", self.env["DOCKER_CONFIG"], "--host", f"unix://{socket}"]
        self.id = ""
        self.owner = "eval-author-build-" + uuid.uuid4().hex
        self.cleaned = False

    def command(self, argv: list[str], *, check: bool = True, timeout: int = 3600):
        result = subprocess.run(argv, env=self.env, text=True, capture_output=True, timeout=timeout)
        with (self.output / "build.log").open("a") as log:
            log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        if check and result.returncode:
            raise ValueError(f"command failed ({result.returncode}); see {self.output / 'build.log'}")
        return result

    def docker(self, *args: str, check: bool = True, timeout: int = 3600):
        return self.command([*self.argv, *args], check=check, timeout=timeout)

    def start(self):
        self.docker("pull", "--platform", self.platform, BUN_IMAGES[self.platform])
        self.id = self.docker(
            "create",
            "--name",
            self.owner,
            "--label",
            f"eval-author.build-owner={self.owner}",
            "--platform",
            self.platform,
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--workdir",
            "/work/source",
            "--entrypoint",
            "/bin/sleep",
            BUN_IMAGES[self.platform],
            "infinity",
        ).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{64}", self.id):
            raise ValueError("invalid owned container ID")
        self.docker("start", self.id)
        self.run("mkdir", "-p", "/work/home", "/work/tmp", "/work/cache")
        if self.run("bun", "--version").stdout.strip() != BUN_VERSION:
            raise ValueError("Bun version mismatch")

    def run(self, *args: str, build: bool = False, check: bool = True):
        env = [
            "HOME=/work/home",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "TMPDIR=/work/tmp",
            "BUN_INSTALL_CACHE_DIR=/work/cache",
            "LC_ALL=C",
        ]
        if build:
            env += [
                f"OPENCODE_VERSION={PATCHED_VERSION}",
                "OPENCODE_CHANNEL=latest",
                f"MODELS_DEV_API_JSON=/work/source/{MODELS_PATH}",
            ]
        return self.docker("exec", self.id, "env", "-i", *env, *args, check=check)

    def put(self, path: Path, target: str):
        source = str(path) + ("/." if path.is_dir() else "")
        self.docker("cp", source, f"{self.id}:{target}")

    def unpack(self, archive: Path, target: str, *, strip: int = 0):
        # Docker cp preserves host ownership on Desktop. Extract verified archives
        # as the container user so cap-drop=ALL still permits workspace installs.
        self.put(archive, "/work/input.tar.gz")
        self.run("tar", "--no-same-owner", "-xzf", "/work/input.tar.gz", f"--strip-components={strip}", "-C", target)

    def get(self, source: str, target: Path):
        self.docker("cp", f"{self.id}:{source}", str(target))

    def script(self, name: str, text: str):
        script = self.output / f"{name}.js"
        script.write_text(text)
        self.put(script, f"/work/{name}.js")
        return self.run("bun", "--no-env-file", "run", f"/work/{name}.js")

    def close(self):
        if not self.id:
            return
        owner = self.docker(
            "inspect", "--format", '{{index .Config.Labels "eval-author.build-owner"}}', self.id
        ).stdout.strip()
        if owner != self.owner:
            raise ValueError("refusing cleanup: container ownership mismatch")
        self.docker("rm", "--force", self.id)
        remaining = self.docker("ps", "-aq", "--filter", f"id={self.id}").stdout.strip()
        self.cleaned = not remaining
        save_json(self.output / "container.json", {"id": self.id, "owner": self.owner, "removed": self.cleaned})
        if not self.cleaned:
            raise ValueError("owned container cleanup could not be verified")


def fetch(runtime: DockerBuild, url: str, target: Path) -> None:
    runtime.command(
        [
            "curl",
            "--disable",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--tlsv1.2",
            "--retry",
            "3",
            "--connect-timeout",
            "30",
            "--max-time",
            "600",
            "--output",
            str(target),
            url,
        ],
        timeout=700,
    )


def file_inventory(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
    }


def verify_source(runtime: DockerBuild, original: dict[str, str]) -> None:
    runtime.script(
        "verify-source",
        """
import { createHash } from 'node:crypto';
import { lstatSync } from 'node:fs';
const expected = """
        + json.dumps(original)
        + """;
for (const [name, hash] of Object.entries(expected)) {
  const file = '/work/source/' + name;
  if (!lstatSync(file).isFile()) throw new Error('source type drift: ' + name);
  const digest = createHash('sha256').update(await Bun.file(file).bytes()).digest('hex');
  if (hash !== digest) throw new Error('source content drift: ' + name);
}
console.log('verified original source files: ' + Object.keys(expected).length);
""",
    )


def resolve_sink(runtime: DockerBuild) -> str:
    result = runtime.script(
        "resolve-sink",
        """
import {realpathSync} from 'node:fs';
const core = '/work/source/packages/core/src/cross-spawn-spawner.ts';
const platform = Bun.resolveSync('@effect/platform-node', core);
console.log(realpathSync(Bun.resolveSync('@effect/platform-node-shared/NodeSink', platform)));
""",
    )
    sink = result.stdout.strip()
    prefix = "/work/source/node_modules/.bun/"
    if (
        not sink.startswith(prefix)
        or not sink.endswith("/node_modules/@effect/platform-node-shared/dist/NodeSink.js")
        or ".." in PurePosixPath(sink).parts
    ):
        raise ValueError("unexpected compiler dependency resolution")
    return sink


def verify_consumed_patch(runtime: DockerBuild, sink: str) -> dict:
    result = runtime.script(
        "verify-consumed",
        """
import {realpathSync} from 'node:fs';
import {createHash} from 'node:crypto';
const sink = """
        + json.dumps(sink)
        + """;
const loaded = [];
const result = await Bun.build({entrypoints: [sink], conditions: ['bun', 'node'], target: 'bun', write: false,
  plugins: [{name: 'verify-default-loader', setup(b) {
    b.onLoad({filter: /\\/NodeSink\\.js$/}, async ({path}) => {
      loaded.push({path: realpathSync(path), sha256: createHash('sha256').update(await Bun.file(path).bytes()).digest('hex')});
      return undefined;
    });
  }}]});
if (!result.success || loaded.length !== 1) throw new Error('compiler dependency proof failed');
console.log(JSON.stringify(loaded[0]));
""",
    )
    loaded = json.loads(result.stdout)
    if loaded != {"path": sink, "sha256": PATCH_FILES["dist/NodeSink.js"][1]}:
        raise ValueError("compiler did not consume patched dependency")
    return loaded


def preserve_notices(runtime: DockerBuild, output: Path) -> None:
    runtime.script(
        "collect-notices",
        r"""
import {readdirSync, lstatSync, mkdirSync, copyFileSync} from 'node:fs';
import path from 'node:path';
const files = [];
function visit(dir) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry), stat = lstatSync(full);
    if (stat.isDirectory()) visit(full);
    else if (stat.isFile() && (/licen[sc]e|notice|copying|copyright/i.test(entry) || entry === 'package.json')) {
      const relative = path.relative('/work/source', full);
      const target = path.join('/work/notices/source', relative);
      mkdirSync(path.dirname(target), {recursive: true});
      copyFileSync(full, target);
      files.push(relative);
    }
  }
}
visit('/work/source');
await Bun.write('/work/notices/index.json', JSON.stringify(files.sort(), null, 2));
""",
    )
    runtime.run("mkdir", "-p", "/work/notices/bun")
    runtime.put(output / "bun-LICENSE.md", "/work/notices/bun/LICENSE.md")
    runtime.run("tar", "-czf", "/work/third-party-notices.tar.gz", "-C", "/work/notices", ".")
    runtime.get("/work/third-party-notices.tar.gz", output / "third-party-notices.tar.gz")


def build(
    output: Path,
    platform: str,
    dependency_archive: Path | None,
    dependency_sha256: str | None,
    export_dependencies: bool,
) -> dict:
    verify_digest(PATCH_FILE, PATCH_SHA256)
    runtime = DockerBuild(output, platform)
    identity = {
        "source_sha256": SOURCE_SHA256,
        "lock_sha256": LOCK_SHA256,
        "bun_image": BUN_IMAGES[platform],
        "platform": platform,
        "install_args": INSTALL_ARGS,
    }
    try:
        fetch(runtime, SOURCE_URL, output / "source.tar.gz")
        extract_verified(output / "source.tar.gz", output / "original", SOURCE_SHA256)
        source = output / "original" / f"opencode-{SOURCE_COMMIT}"
        verify_digest(source / "bun.lock", LOCK_SHA256)
        verify_digest(source / MODELS_PATH, MODELS_SHA256)
        if json.loads((source / "package.json").read_text())["packageManager"] != f"bun@{BUN_VERSION}":
            raise ValueError("source Bun requirement drift")
        original = file_inventory(source)
        fetch(runtime, EFFECT_URL, output / "effect.tgz")
        actual_sri = (
            "sha512-" + base64.b64encode(hashlib.sha512((output / "effect.tgz").read_bytes()).digest()).decode()
        )
        if actual_sri != EFFECT_INTEGRITY:
            raise ValueError("published dependency package integrity mismatch")
        extract_verified(output / "effect.tgz", output / "effect", sha256(output / "effect.tgz"))
        effect = output / "effect/package"
        effect_original = file_inventory(effect)
        fetch(
            runtime,
            f"https://raw.githubusercontent.com/oven-sh/bun/bun-v{BUN_VERSION}/LICENSE.md",
            output / "bun-LICENSE.md",
        )
        verify_digest(output / "bun-LICENSE.md", "362fe32c954ec829240bd7429c53d705eef08c6bfa45ffc2d02005f857cba4dd")
        runtime.start()
        if dependency_archive:
            if dependency_sha256 is None:
                raise ValueError("dependency archive requires explicit SHA256")
            verify_digest(dependency_archive, dependency_sha256)
            # A cache is an explicit trusted input, never a global Bun cache.
            # Re-extract safely on the host before copying into the owned container.
            extract_verified(dependency_archive, output / "reused", dependency_sha256)
            cached = output / "reused/source"
            if json.loads((cached / ".build-identity.json").read_text()) != identity:
                raise ValueError("dependency cache build identity mismatch")
            runtime.unpack(dependency_archive, "/work")
        else:
            runtime.unpack(output / "source.tar.gz", "/work/source", strip=1)
            runtime.run("bun", *INSTALL_ARGS)
        verify_source(runtime, original)
        sink = resolve_sink(runtime)
        package = str(PurePosixPath(sink).parent.parent)
        runtime.get(package, output / "installed-effect")
        if file_inventory(output / "installed-effect") != effect_original:
            raise ValueError("installed dependency differs from integrity-pinned published package")
        if export_dependencies:
            save_json(output / "dependency-identity.json", identity)
            runtime.put(output / "dependency-identity.json", "/work/source/.build-identity.json")
            runtime.run("tar", "--hard-dereference", "-czf", "/work/dependencies.tar.gz", "-C", "/work", "source")
            runtime.get("/work/dependencies.tar.gz", output / "dependencies.tar.gz")
            save_json(output / "dependency-cache.json", {**identity, "sha256": sha256(output / "dependencies.tar.gz")})
        runtime.docker("network", "disconnect", "bridge", runtime.id)
        apply_patch(effect, PATCH_FILE.read_bytes(), PATCH_FILES)
        for name in PATCH_FILES:
            runtime.run("rm", "--", f"{package}/{name}")
            runtime.put(effect / name, f"{package}/{name}")
        consumed = verify_consumed_patch(runtime, sink)
        runtime.run("bun", *BUILD_ARGS, build=True)
        verify_source(runtime, original)
        verify_consumed_patch(runtime, sink)
        arch = "x64" if platform == "linux/amd64" else "arm64"
        native = f"/work/source/packages/opencode/dist/opencode-linux-{arch}/bin/opencode"
        result = runtime.run(native, "--version", check=False)
        binary = output / "opencode"
        runtime.get(native, binary)
        binary.chmod(0o755)
        manifest = native_manifest(binary, result, platform)
        container_hash = runtime.run("sha256sum", native).stdout.split()[0]
        if manifest["sha256"] != container_hash:
            raise ValueError("native binary copy hash mismatch")
        preserve_notices(runtime, output)
        provenance = {
            "schema_version": 1,
            "official_artifact": False,
            "selected_version": SELECTED_VERSION,
            "observed_native_version": result.stdout.strip(),
            "binary_sha256": manifest["sha256"],
            "source_commit": SOURCE_COMMIT,
            "source_url": SOURCE_URL,
            "source_archive_sha256": SOURCE_SHA256,
            "source_files_verified": len(original),
            "lock_sha256": LOCK_SHA256,
            "bun_version": BUN_VERSION,
            "bun_image": BUN_IMAGES[platform],
            "platform": platform,
            "patch_sha256": PATCH_SHA256,
            "patch_files": PATCH_FILES,
            "dependency_url": EFFECT_URL,
            "dependency_integrity": EFFECT_INTEGRITY,
            "dependency_files_verified": len(effect_original),
            "compiler_consumed": consumed,
            "models_path": MODELS_PATH,
            "models_sha256": MODELS_SHA256,
            "models_origin": "complete catalog snapshot published in pinned upstream source",
            "install_args": INSTALL_ARGS,
            "build_args": BUILD_ARGS,
            "dependency_archive_reused_sha256": dependency_sha256,
            "build_network": "none",
            "release_upload_enabled": False,
            "web_ui_embedded": False,
            "builder_sha256": sha256(Path(__file__)),
            "notices_sha256": sha256(output / "third-party-notices.tar.gz"),
            "reproducibility": "pinned inputs and workflow; cross-host bit-for-bit identity not asserted",
        }
        save_json(output / "provenance.json", provenance)
    finally:
        runtime.close()
    # Ready manifest is the last write, after all checks and owned cleanup pass.
    save_json(output / "binary-manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new local output directory (never overwritten)")
    parser.add_argument(
        "--platform", choices=sorted(BUN_IMAGES), help="Linux container target; defaults to host architecture"
    )
    parser.add_argument(
        "--export-dependencies",
        action="store_true",
        help="retain an unpatched, identity-bound dependencies.tar.gz for explicit reuse",
    )
    parser.add_argument("--dependency-archive", type=Path, help="previously exported local dependencies archive")
    parser.add_argument(
        "--dependency-sha256", help="independently retained SHA256 of that exact archive; required for reuse"
    )
    args = parser.parse_args(argv)
    try:
        if "OPENCODE_RELEASE" in os.environ:
            raise ValueError("OPENCODE_RELEASE is forbidden, even when empty or 0")
        if bool(args.dependency_archive) != bool(args.dependency_sha256):
            raise ValueError("dependency reuse requires both archive and explicit SHA256")
        if args.dependency_archive:
            verify_digest(args.dependency_archive, args.dependency_sha256)
        platform = args.platform or {"x86_64": "linux/amd64", "aarch64": "linux/arm64", "arm64": "linux/arm64"}.get(
            host_platform.machine()
        )
        if platform not in BUN_IMAGES:
            raise ValueError("unsupported host architecture; select --platform explicitly")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("output already exists; choose a fresh output directory")
        output = args.output.resolve()
        output.mkdir(parents=True, mode=0o700)
        print(
            json.dumps(
                build(output, platform, args.dependency_archive, args.dependency_sha256, args.export_dependencies)
            )
        )
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
