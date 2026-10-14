#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bind a complete factual ledger to structured evidence; check omissions and edits.

This checks typed claims, not free prose or source truth. A narrative requires
separate semantic review. Empty collections and nulls are facts, not missing data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from discovery_determinism import encoded, loads, read, sha

SCHEMA = "nemo.report_facts.v1"
STAGES = {"inventory", "prerequisites", "validation", "sampled_execution", "coverage", "acceptance"}


def facts(value, pointer="") -> dict:
    if isinstance(value, dict) and value:
        result = {}
        for key, child in sorted(value.items()):
            result.update(facts(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1")))
        return result
    if isinstance(value, list) and value:
        result = {}
        for index, child in enumerate(value):
            result.update(facts(child, pointer + "/" + str(index)))
        return result
    return {pointer: value}


def ledger(source: Path, stage: str) -> dict:
    if stage not in STAGES:
        raise ValueError("unknown evidence stage")
    raw = source.read_bytes()
    payload = loads(raw)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("source must be a nonempty structured result")
    return {
        "schema": SCHEMA,
        "source_sha256": sha(raw),
        "stage": stage,
        "claims": facts(payload),
        "narrative_review": "not_measured",
    }


def check(source: Path, candidate: dict, stage: str) -> dict:
    expected = ledger(source, stage)
    if candidate.get("schema") != SCHEMA or not isinstance(candidate.get("claims"), dict):
        raise ValueError("invalid factual ledger")
    claims, actual = expected["claims"], candidate["claims"]
    missing = sorted(claims.keys() - actual.keys())
    extra = sorted(actual.keys() - claims.keys())
    extra.extend("unrecognized-ledger-field:" + key for key in sorted(candidate.keys() - expected.keys()))
    contradicted = sorted(key for key in claims.keys() & actual.keys() if encoded(claims[key]) != encoded(actual[key]))
    binding = all(candidate.get(key) == expected[key] for key in ("source_sha256", "stage", "narrative_review"))
    return {
        "schema": SCHEMA,
        "passed": binding and not (missing or extra or contradicted),
        "source_bound": binding,
        "missing": missing,
        "unsupported": extra,
        "contradicted": contradicted,
        "narrative_review": "not_measured",
        "claim_count": len(claims),
    }


def render(candidate: dict) -> str:
    # JSON quoting keeps newlines, Markdown and HTML in values from creating claims.
    rows = [
        "# Structured evidence",
        "",
        f"Stage: {candidate['stage']}",
        f"Source SHA-256: {candidate['source_sha256']}",
        "",
        "```json",
        json.dumps(candidate["claims"], sort_keys=True, indent=2, ensure_ascii=True),
        "```",
        "",
    ]
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            check(args.source, read(args.candidate), args.stage) if args.candidate else ledger(args.source, args.stage)
        )
        if args.render:
            if args.candidate:
                raise ValueError("render the source ledger, not an unchecked candidate")
            print(render(result), end="")
        else:
            print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result.get("passed", True) else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"passed": False, "incomplete": True, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
