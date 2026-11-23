# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import ast
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_IMAGE = "python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90"
CONTROLS = ("reference", "alternative", "noop", "wrong-body", "extra-reply", "wrong-id", "duplicate-reply")


def runtime_files(runtime_dir: Path, entrypoints: tuple[str, ...]) -> set[Path]:
    """Find sibling Python imports without copying unrelated runtime files."""
    pending = [runtime_dir / name for name in entrypoints]
    files: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in files:
            continue
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing or symlinked runtime source: {source}")
        tree = ast.parse(source.read_text(), filename=str(source))
        files.add(source)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules = [node.module]
                else:
                    modules = [alias.name for alias in node.names]
            for module in modules:
                path = runtime_dir.joinpath(*module.split("."))
                candidate = path.with_suffix(".py")
                if candidate.is_file():
                    pending.append(candidate)
                elif path.is_dir() and (path / "__init__.py").exists():
                    raise ValueError(f"Runtime packages require explicit packaging: {module}")
    return files


def build(
    output: Path,
    control: str = "reference",
    *,
    runtime_dir: Path | None = None,
    network_mode: str = "no-network",
) -> Path:
    if control not in CONTROLS:
        raise ValueError(f"Unknown control: {control}")
    if network_mode not in {"no-network", "public"}:
        raise ValueError(f"Unknown network mode: {network_mode}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output must be absent or empty: {output}")
    runtime_dir = runtime_dir or HERE.parent / "runtime"
    public = runtime_files(runtime_dir, ("client.py", "mcp_server.py"))
    backend = runtime_files(runtime_dir, ("service.py",))
    if any(path.name in {"service.py", "grade.py", "build.py"} for path in public):
        raise ValueError("Public runtime imports a private entrypoint.")
    main = output / "environment" / "main"
    sidecar = output / "environment" / "workplace"
    tests = output / "tests"
    solution = output / "solution"
    for directory in (main, sidecar, tests, solution):
        directory.mkdir(parents=True, exist_ok=True)
    for sources, destination in ((public, main), (backend, sidecar)):
        for source in sources:
            target = destination / source.relative_to(runtime_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    shutil.copy2(HERE / "seed.json", sidecar / "seed.json")
    shutil.copy2(HERE / "seed.json", tests / "seed.json")
    shutil.copy2(HERE / "grade.py", tests / "grade.py")
    shutil.copy2(HERE / "test.sh", tests / "test.sh")
    # Harbor's Docker egress controller puts services in one network namespace.
    workplace_url = "http://127.0.0.1:8080" if network_mode == "no-network" else "http://workplace:8080"
    (solution / "solve.sh").write_text(
        (HERE / "controls" / f"{control}.sh").read_text().replace("@WORKPLACE_URL@", workplace_url)
    )
    for path in (tests / "test.sh", solution / "solve.sh"):
        path.chmod(0o755)
    (output / "instruction.md").write_text(
        (HERE / "instruction.md").read_text().replace("@WORKPLACE_URL@", workplace_url)
    )
    shutil.copy2(HERE / "compose.yaml", output / "environment" / "docker-compose.yaml")
    for source, destination in (
        ("Dockerfile.main", output / "environment" / "Dockerfile"),
        ("Dockerfile.workplace", sidecar / "Dockerfile"),
        ("Dockerfile.verifier", tests / "Dockerfile"),
    ):
        destination.write_text((HERE / source).read_text().replace("@BASE_IMAGE@", BASE_IMAGE))
    (output / "task.toml").write_text(
        (HERE / "task.toml")
        .read_text()
        .replace("@NETWORK_MODE@", network_mode)
        .replace("@WORKPLACE_URL@", workplace_url)
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a separate-verifier Workplace Harbor task.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control", choices=CONTROLS, default="reference")
    parser.add_argument(
        "--network-mode",
        choices=("no-network", "public"),
        default="no-network",
        help="Public is an explicit local fallback, not isolated proof.",
    )
    args = parser.parse_args()
    try:
        print(build(args.output, args.control, network_mode=args.network_mode))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Build failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
