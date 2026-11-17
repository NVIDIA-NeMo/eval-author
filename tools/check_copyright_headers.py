#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate source-file copyright and license headers."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

NVIDIA_COPYRIGHT = "SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved."
SPDX_LICENSE = "SPDX-License-Identifier: Apache-2.0"

HASH_SUFFIXES = {".env", ".py", ".sh", ".toml", ".yaml", ".yml"}
HTML_SUFFIXES = {".md"}
HASH_FILENAMES = {".env.example", ".gitignore", "Makefile"}
# Templates copied verbatim into users' repositories carry no header.
HEADERLESS_TEMPLATES = {"team-skills/ethos/ethos-write/references/templates/ETHOS.md"}
# NVSkills CI generates and signs these reports. Adding local headers would
# invalidate the signatures, so preserve their bytes in check and fix modes.
# Keep this list explicit: other files in these skills still require headers.
SIGNED_NVSKILLS_REPORTS = {
    "team-skills/ethos/ethos-explore/BENCHMARK.md",
    "team-skills/ethos/ethos-explore/skill-card.md",
    "team-skills/ethos/ethos-orchestrate/BENCHMARK.md",
    "team-skills/ethos/ethos-orchestrate/skill-card.md",
    "team-skills/ethos/ethos-write/BENCHMARK.md",
    "team-skills/ethos/ethos-write/skill-card.md",
}


def _tracked_and_untracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return [PROJECT_ROOT / name for name in result.stdout.decode().split("\0") if name]


def _comment_style(path: Path) -> str | None:
    if path.name in HASH_FILENAMES or path.suffix in HASH_SUFFIXES:
        return "hash"
    if path.suffix in HTML_SUFFIXES:
        return "html"
    return None


def _nvidia_header(style: str) -> str:
    if style == "hash":
        return f"# {NVIDIA_COPYRIGHT}\n# {SPDX_LICENSE}\n"
    if style == "html":
        return f"<!-- {NVIDIA_COPYRIGHT} -->\n<!-- {SPDX_LICENSE} -->\n"
    raise ValueError(f"Unsupported comment style: {style}")


def _header_offset(content: str, style: str) -> int:
    if content.startswith("#!"):
        end = content.find("\n")
        return end + 1 if end >= 0 else 0

    if style == "html" and content.startswith("---\n"):
        end = content.find("\n---\n", len("---\n"))
        return end + len("\n---\n") if end >= 0 else 0

    return 0


def _has_nvidia_header(content: str, style: str) -> bool:
    header = _nvidia_header(style)
    return content.startswith(header, _header_offset(content, style))


def _add_nvidia_header(path: Path, content: str, style: str) -> None:
    header = _nvidia_header(style)
    offset = _header_offset(content, style)
    prefix, remainder = content[:offset], content[offset:]
    spacer = "\n" if remainder else ""
    updated = f"{prefix}{header}{spacer}{remainder}"
    path.write_text(updated, encoding="utf-8")


def check_headers(*, fix_nvidia: bool) -> list[Path]:
    invalid: list[Path] = []
    for path in _tracked_and_untracked_files():
        style = _comment_style(path)
        if style is None or not path.is_file():
            continue
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        if relative_path in HEADERLESS_TEMPLATES or relative_path in SIGNED_NVSKILLS_REPORTS:
            continue

        content = path.read_text(encoding="utf-8")
        if _has_nvidia_header(content, style):
            continue
        if fix_nvidia and "SPDX-FileCopyrightText" not in content[:2000]:
            _add_nvidia_header(path, content, style)
            continue
        invalid.append(path.relative_to(PROJECT_ROOT))
    return invalid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix-nvidia",
        action="store_true",
        help="add the exact NVIDIA SPDX header to files known to be NVIDIA-authored",
    )
    args = parser.parse_args()

    invalid = check_headers(fix_nvidia=args.fix_nvidia)
    if invalid:
        print("Files with missing or invalid copyright/license headers:")
        for path in invalid:
            print(f"  {path}")
        print("Run `make update-copyright-headers` only for NVIDIA-authored files.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
