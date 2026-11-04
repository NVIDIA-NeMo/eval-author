# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run pinned Tier 3 evaluation, optionally substituting a verified OpenCode binary."""

import json
import os
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distribution
from types import MappingProxyType

from patched_opencode_runtime import CONFIGURED_VERSION as OPENCODE_VERSION
from patched_opencode_runtime import EVALUATOR_REVISION


def main():
    if sys.argv[1:3] != ["tier3", "evaluate"]:
        raise ValueError("OpenCode version adapter is restricted to tier3 evaluate")
    required = os.environ.get("EA_REQUIRE_PATCHED_OPENCODE", "0")
    if required not in ("0", "1"):
        raise ValueError("EA_REQUIRE_PATCHED_OPENCODE must be 0 or 1")
    if required == "1" and not os.environ.get("EA_PATCHED_OPENCODE_MANIFEST"):
        raise ValueError("patched OpenCode requires EA_PATCHED_OPENCODE_MANIFEST")
    message = "OpenCode version adapter requires pinned SkillEvaluator 0.7.0 commit and Harbor 0.24.0"
    try:
        evaluator = distribution("skillevaluator")
        origin = json.loads(evaluator.read_text("direct_url.json") or "{}")
        if (
            evaluator.version != "0.7.0"
            or distribution("harbor").version != "0.24.0"
            or not isinstance(origin, dict)
            or not isinstance(origin.get("vcs_info"), dict)
            or origin["vcs_info"].get("vcs") != "git"
            or origin["vcs_info"].get("commit_id") != EVALUATOR_REVISION
            or "dir_info" in origin
        ):
            raise ValueError(message)
    except (PackageNotFoundError, OSError, ValueError) as exc:
        raise ValueError(message) from exc

    runner = import_module("skillevaluator.tier3.harbor.runner")
    table = getattr(runner, "_HARBOR_AGENT_KWARGS", None)
    # v0.7.0 lacks a public agent-version option. Review this private contract
    # on upgrades; never overwrite an upstream OpenCode pin or another agent.
    expected = {
        "codex": {"reasoning_effort": "high", "version": "0.161.0"},
        "claude-code": {"version": "2.1.292"},
    }
    if (
        not isinstance(table, MappingProxyType)
        or table != expected
        or any(not isinstance(options, MappingProxyType) for options in table.values())
    ):
        raise ValueError("upstream agent-kwargs contract changed; review the OpenCode version adapter")
    if "EA_PATCHED_OPENCODE_MANIFEST" in os.environ:
        from patched_opencode_runtime import activate

        activate(os.environ["EA_PATCHED_OPENCODE_MANIFEST"])
    setattr(
        runner,
        "_HARBOR_AGENT_KWARGS",
        MappingProxyType({**table, "opencode": MappingProxyType({"version": OPENCODE_VERSION})}),
    )
    import_module("skillevaluator.cli").cli()


if __name__ == "__main__":
    main()
