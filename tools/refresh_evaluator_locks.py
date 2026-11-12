# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recompile evaluator locks with uv 0.12.3, retaining pins unless explicitly selected for upgrade.

make check-evaluator-locks checks input/lock drift without modifying tracked files.
make refresh-evaluator-locks refreshes compatible locks without upgrading existing pins.
For a reviewed update: python tools/refresh_evaluator_locks.py --upgrade-package package==version
Review the diff and exercise all affected installed extras before accepting an update.
This checks consistency, not whether newer releases or advisories exist.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from install_evaluator import EXTRAS, LOCKS, installation_environment

UV_VERSION = "0.12.3"


def refresh_locks(*, check: bool = False, upgrades: tuple[str, ...] = (), locks: Path = LOCKS) -> None:
    if check and upgrades:
        raise ValueError("check mode cannot upgrade dependencies")
    env = installation_environment()
    version = subprocess.run(["uv", "--version"], env=env, check=True, capture_output=True, text=True)
    if version.stdout.split()[:2] != ["uv", UV_VERSION]:
        raise RuntimeError(f"evaluator lock generation requires uv {UV_VERSION}")
    # Seed outputs so uv retains reviewed pins rather than resolving latest versions.
    # Compile all profiles successfully before writing any tracked file.
    with tempfile.TemporaryDirectory(prefix="evaluator-locks-") as temporary:
        root = Path(temporary)
        relative = Path("tools/evaluator-locks")
        staged = root / relative
        staged.mkdir(parents=True)
        for filename in ("llm-overrides.in", *(f"{extra}.{suffix}" for extra in EXTRAS for suffix in ("in", "txt"))):
            shutil.copyfile(locks / filename, staged / filename)
        for extra in EXTRAS:
            command = [
                "uv",
                "pip",
                "compile",
                "--no-config",
                "--python-version",
                "3.12",
                "--universal",
                "--generate-hashes",
                "--only-binary",
                ":all:",
                "--no-annotate",
                "--no-emit-package",
                "skillevaluator",
            ]
            if extra != "security":
                command.extend(["--overrides", str(relative / "llm-overrides.in")])
            output = [str(relative / f"{extra}.in"), "-o", str(relative / f"{extra}.txt")]
            if upgrades:
                # Record the steady-state command so a targeted update passes the
                # next no-upgrade drift check without a header-only difference.
                command.extend(["--custom-compile-command", " ".join([*command, *output])])
            for upgrade in upgrades:
                command.extend(["--upgrade-package", upgrade])
            command.extend(output)
            subprocess.run(command, cwd=root, env=env, check=True, stdout=subprocess.DEVNULL)
        changed = [
            extra for extra in EXTRAS if (staged / f"{extra}.txt").read_bytes() != (locks / f"{extra}.txt").read_bytes()
        ]
        if check and changed:
            raise RuntimeError(
                f"evaluator locks differ from inputs: {', '.join(changed)}; run make refresh-evaluator-locks and review"
            )
        if not check:
            for extra in changed:
                shutil.copyfile(staged / f"{extra}.txt", locks / f"{extra}.txt")
        print("Evaluator locks checked: " + ", ".join(EXTRAS))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--upgrade-package", action="append", default=[])
    args = parser.parse_args()
    refresh_locks(check=args.check, upgrades=tuple(args.upgrade_package))
