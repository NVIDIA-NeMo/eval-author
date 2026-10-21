# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Owner-specific command surfaces over the shared workspace implementation."""

import json
import sys

PRIVACY = frozenset({"init", "prepare", "audit-evidence", "review-privacy", "batch-prepare", "batch-status"})
ENVIRONMENT = frozenset(
    {
        "inventory-tool-calls",
        "plan-tool-call-access",
        "resolve-tool-call-access",
        "generate-mock-tool-calls",
        "validate-task",
        "record-reproducibility",
        "record-review-inputs",
        "review-solutions",
        "check-runtime",
    }
)
EVIDENCE = frozenset({"probe", "record-run-inputs", "record-validation", "record-repair", "finalize", "check"})
PUBLICATION = frozenset({"prepare-publication", "review-publication", "export"})


def main(owner, argv=None):
    from . import trace_workspace

    argv = sys.argv[1:] if argv is None else argv
    allowed = {"privacy": PRIVACY, "environment": ENVIRONMENT, "evidence": EVIDENCE, "publication": PUBLICATION}[owner]
    if not argv or argv[0] not in allowed:
        print(json.dumps({"owner": owner, "commands": sorted(allowed)}))
        return 0 if argv == ["--help"] else 2
    try:
        args = trace_workspace._parser().parse_args(argv)
        if owner == "publication":
            # Existing export is deliberately Harbor-only. Native Gym remains
            # private until a provider-specific release contract exists.
            root = trace_workspace._ensure_task_dir(args.task_dir)
            if (root / "gym").exists():
                raise trace_workspace.ContractError(
                    "native Gym publication is not supported; retain the private handoff"
                )
        if owner in {"evidence", "environment"} and argv[0] in {"record-run-inputs", "probe", "record-review-inputs"}:
            from .admission import assess

            if not assess(args.task_dir)["admitted"]:
                raise trace_workspace.ContractError("no_candidate cannot enter construction or execution")
        if owner == "environment" and argv[0] == "generate-mock-tool-calls":
            from .admission import reviewed_source

            reviewed_source(args.task_dir)
        result = args.run(args)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("valid", True) else 1
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error), "owner": owner}))
        return 1
