#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render conversations from supplied state and observed facts, without side effects.

The caller owns readiness, inspection, digest reconciliation, and recording actual
replies through journey_state. Rendering neither obtains approval nor advances work.
The CLI reads only its explicit input (or stdin), never a repository or saved state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Even the initial orientation must not create bytecode caches beside the skill.
sys.dont_write_bytecode = True

import conversation_templates as shared  # noqa: E402
import journey_state  # noqa: E402
from conversation_flows import HANDLERS, PREVIEWS, STAGES  # noqa: E402
from conversation_formatting import mapping  # noqa: E402


def render(payload: dict) -> str:
    """Dispatch a supplied presentation without clocks, I/O, or state changes."""
    payload = mapping(payload, "input", {"stage", "journey", "ready", "state", "revision", "facts"})
    stage = payload.get("stage")
    if stage not in STAGES:
        raise ValueError("Unknown conversation stage")
    assert isinstance(stage, str)
    state = payload.get("state")
    journey = state.get("journey") if isinstance(state, dict) else payload.get("journey")
    handler = HANDLERS.get((journey, stage)) if isinstance(journey, str) else None
    if handler is None:
        # A shared flow validates unselected and invalid journey input itself.
        handler = HANDLERS.get((None, stage))
    if handler is None:
        raise ValueError("Conversation stage is unavailable for the selected journey")
    return handler(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", help="Presentation JSON file, or - for stdin; never a repository path")
    mode.add_argument("--stage", choices=sorted(PREVIEWS), help="Render a stateless orientation or table preview")
    parser.add_argument("--journey", choices=tuple(shared.JOURNEYS))
    parser.add_argument("--ready", action="store_true", help="The user has actually answered the readiness prompt")
    args = parser.parse_args()
    try:
        if args.input:
            if args.journey or args.ready:
                raise ValueError("--input cannot be combined with preview options")
            raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
            payload = json.loads(raw, object_pairs_hook=journey_state._pairs)
        else:
            payload = {"stage": args.stage, "journey": args.journey, "ready": args.ready}
        sys.stdout.buffer.write(render(payload).encode("utf-8"))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "persisted": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
