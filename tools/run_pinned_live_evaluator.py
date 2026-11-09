# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run pinned Tier 3 evaluation, optionally substituting a verified OpenCode binary."""

import json
import os
import re
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distribution
from types import MappingProxyType

from live_agent_diagnostics import runtime_failure_scope
from patched_opencode_runtime import CONFIGURED_VERSION as OPENCODE_VERSION
from patched_opencode_runtime import (
    EVALUATOR_REVISION,
    LIVE_EXECUTION_SECONDS,
    LIVE_HANDOFF_SECONDS,
    live_job_remaining,
)


def main():
    with runtime_failure_scope("insufficient_job_budget"):
        live_job_remaining(LIVE_EXECUTION_SECONDS + LIVE_HANDOFF_SECONDS)
    with runtime_failure_scope("patched_runtime_activation_failed"):
        configure_runtime()
        install_failure_channel()
    with runtime_failure_scope("insufficient_job_budget"):
        live_job_remaining(LIVE_EXECUTION_SECONDS + LIVE_HANDOFF_SECONDS)
    import_module("skillevaluator.cli").cli()


def install_failure_channel():
    keys = ("_EA_LIVE_FAILURE_PATH", "_EA_LIVE_FAILURE_TOKEN")
    channel = {key: os.environ[key] for key in keys if key in os.environ}
    if not channel:
        return
    if set(channel) != set(keys) or not re.fullmatch(r"[a-f0-9]{64}", channel[keys[1]]):
        raise ValueError("invalid runtime failure channel")

    def decorate(original):
        def launch(environment):
            result = original(environment)
            # Native runtime plans exclude ambient variables. Inject only at
            # the host Harbor launch, not into task, verifier or agent envs.
            result.update(channel)
            return result

        return launch

    for name in ("skillevaluator.tier3.harbor.runner", "skillevaluator.tier3.harbor.runtime_preflight"):
        module = import_module(name)
        setattr(module, "_harbor_launch_environment", decorate(module._harbor_launch_environment))


def configure_runtime():
    if sys.argv[1:3] != ["tier3", "evaluate"]:
        raise ValueError("OpenCode version adapter is restricted to tier3 evaluate")
    with runtime_failure_scope("prepared_image_mismatch"):
        preparation_required = os.environ.get("EA_REQUIRE_PREPARED_IMAGES", "0")
        if preparation_required not in ("0", "1"):
            raise ValueError("invalid required image preparation activation")
        if preparation_required == "1" or "EA_PREPARED_IMAGES_MANIFEST" in os.environ:
            if not os.environ.get("EA_PREPARED_IMAGES_MANIFEST") or not os.environ.get("EA_PATCHED_OPENCODE_MANIFEST"):
                raise ValueError("required image preparation manifests are missing")
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

        if "EA_PREPARED_IMAGES_MANIFEST" in os.environ:
            from patched_opencode_runtime import validate_prepared_run

            with runtime_failure_scope("prepared_image_mismatch"):
                validate_prepared_run(sys.argv[1:])
        activate(os.environ["EA_PATCHED_OPENCODE_MANIFEST"])
    setattr(
        runner,
        "_HARBOR_AGENT_KWARGS",
        MappingProxyType({**table, "opencode": MappingProxyType({"version": OPENCODE_VERSION})}),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("live evaluator failed; consult the allowlisted summary")
