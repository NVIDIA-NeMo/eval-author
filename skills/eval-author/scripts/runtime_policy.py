# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared runtime policy. No provider or third-party package is imported.

Test profiles name reproducible test configurations, not proof of a passing run.
Version constraints deliberately accept only comma-separated comparisons against
stable numeric releases. Unrecognized constraints/versions fail closed.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

POLICY_PATH = Path(__file__).with_name("runtime-policy.json")


def load() -> dict:
    policy = json.loads(POLICY_PATH.read_text())
    if policy["schema_version"] != 1:
        raise ValueError("Unsupported runtime policy schema")
    return policy


def version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
        raise ValueError(f"Unsupported version {value!r}; require a stable numeric release")
    parts = [int(part) for part in value.split(".")]
    return (parts[0], parts[1], parts[2] if len(parts) == 3 else 0)


def satisfies(value: str, constraint: str) -> bool:
    actual = version(value)
    results = []
    for clause in constraint.split(","):
        match = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(\d+\.\d+(?:\.\d+)?)\s*", clause)
        if match is None:
            raise ValueError(f"Unsupported constraint {constraint!r}; use explicit stable-version comparisons")
        op, number = match.groups()
        expected = version(number)
        results.append(
            {
                ">=": actual >= expected,
                "<=": actual <= expected,
                "==": actual == expected,
                "!=": actual != expected,
                ">": actual > expected,
                "<": actual < expected,
            }[op]
        )
    return all(results)


def install_spec(provider: str) -> str:
    entry = load()["providers"][provider]
    return f"{entry['package']}=={entry['test_profiles'][entry['install_profile']]['version']}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--get", help="Dot-separated policy key; prints scalars or JSON")
    parser.add_argument("--install-spec", choices=["harbor", "gym"])
    args = parser.parse_args()
    value = load()
    if args.install_spec:
        print(install_spec(args.install_spec))
        return
    if args.get:
        for key in args.get.split("."):
            value = value[key]
    print(json.dumps(value, indent=2) if isinstance(value, (dict, list)) else value)


if __name__ == "__main__":
    main()
