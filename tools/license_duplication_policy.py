# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Repository-specific exceptions for verified Tier 2 license-only duplicates."""

import re
from pathlib import Path

POLICY = "nvidia-apache-header-duplication-v1"
HEADER = [
    "<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->",
    "<!-- SPDX-License-Identifier: Apache-2.0 -->",
]
LOCATION = re.compile(r'"\(preamble\)" in ([A-Za-z0-9_./-]+\.md) \(lines ([1-9][0-9]*)-([1-9][0-9]*)\)')


def expected_license_duplicate(finding: dict, skill: Path) -> bool:
    """Fail closed unless every cited span is this repository's exact header."""
    if finding.get("check_name") != "duplicate" or finding.get("severity") != "high":
        return False
    message = finding.get("message")
    if not isinstance(message, str):
        return False
    title, separator, locations = message.partition(":\n  ")
    if not separator:
        return False
    matches = [LOCATION.fullmatch(location) for location in locations.split("\n  vs ")]
    if len(matches) < 2 or any(match is None for match in matches):
        return False
    spans = [match.groups() for match in matches if match is not None]
    names = sorted({name for name, _, _ in spans})
    if len(names) < 2 or title != "Duplicate content found across " + " and ".join(names):
        return False
    if finding.get("file_path") != spans[0][0] or finding.get("line_number") != int(spans[0][1]):
        return False
    root = skill.resolve()
    try:
        for name, first, last in spans:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
                return False
            path = root / relative
            if any(part.is_symlink() for part in (path, *path.parents)):
                return False
            if not path.resolve().is_relative_to(root) or path.stat().st_size > 1_000_000:
                return False
            lines = path.read_text().splitlines()
            start, end = int(first), int(last)
            if not 1 <= start <= end <= len(lines):
                return False
            if [line.strip() for line in lines[start - 1 : end] if line.strip()] != HEADER:
                return False
            # Headers must be at the start, optionally after SKILL.md frontmatter.
            prefix = "\n".join(lines[: start - 1]).strip()
            if prefix:
                frontmatter = prefix.splitlines()
                if (
                    name != "SKILL.md"
                    or len(frontmatter) < 3
                    or frontmatter[0] != "---"
                    or frontmatter[-1] != "---"
                    or "---" in frontmatter[1:-1]
                ):
                    return False
    except (OSError, UnicodeError, ValueError):
        return False
    return True
