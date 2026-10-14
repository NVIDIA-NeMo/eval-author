<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Workflow scope checks

This is the first executable slice of Phase 2 in the determinism plan (#75).
It supplies a request matrix and a deterministic offline checker. It does not
select routes by prompt keywords, launch assistants, or change skill routing.

The [contracts](../tests/fixtures/workflow-scope/contracts.json) pin their source
revision and policy references. Cases cover broad help, explicit audit, inventory,
readiness, narrow audit validation, first-eval opening, recommendations, task
drafting, trace inspection, and follow-ups that broaden or narrow scope.
Each turn specifies exact prompt alternatives, prior context, selected flow,
permitted and required actions, allowed writes, and terminal handoff.
These are fixture-specific scopes, not universal permission grants.

## Capture format

Save capture artifacts outside the observed disposable repository. Take snapshots
while the workspace is quiescent, before and after each turn:

    python tools/check_workflow_scope.py snapshot --root /tmp/disposable-repo > /tmp/before.json
    # Run the authorized workflow with a capture-capable harness.
    python tools/check_workflow_scope.py snapshot --root /tmp/disposable-repo > /tmp/after.json
    python tools/check_workflow_scope.py check --evidence /tmp/capture.json --raw-trace /tmp/tool-trace.json

Snapshots record regular-file SHA-256 hashes, permission modes, directories,
symlink targets, and errors. They include dotfiles and do not follow symlinks.
Unsupported file types, reparse points, read errors, and observed mutations make
a snapshot incomplete. This is a quiescent-fixture check, not a filesystem
sandbox or an atomic snapshot across concurrent writers.

The supported raw trace envelope is JSON with a tool_calls array. Every call
has a unique nonempty string id, a tool name, and a zero-based integer turn index.
Calls are in chronological order; extra native payload fields
are retained. An adapter must extract this array from its complete native trace
and retain that original trace separately. Include failed and unexpected calls.

A normalized capture has this shape:

    {
      "schema_version": 1,
      "case": "generic-help",
      "evidence_kind": "synthetic",
      "contracts_sha256": "<hash of sorted compact contracts JSON>",
      "raw_trace_sha256": "<hash of exact raw trace bytes>",
      "turns": [{
        "prompt": "Help me with evals.",
        "context": "Fresh session; no prior authorization.",
        "flow": "eval-author",
        "handoff": "ask_goal",
        "events": [],
        "before": {"complete": true, "entries": {}, "errors": []},
        "after": {"complete": true, "entries": {}, "errors": []}
      }]
    }

Contract hashing uses Python's
sha256(json.dumps(contracts, sort_keys=True, separators=(",", ":")).encode()).
A tool event contains call_id, tool, action, and write_paths:

    {"call_id": "call-1", "tool": "exec_command", "action": "inventory", "write_paths": []}

The adapter classifies effects from the actual invocation and supplies every
attempted destination, including temporary writes, failed writes, deletions,
permission changes, and writes later undone. Each raw call ID must appear
exactly once in raw order, with matching tool names and turn indices. Unknown actions and missing coverage
make the evidence incomplete. Explicit empty events are valid for a welcome;
missing event lists are not.

Paths must be canonical repository-relative POSIX paths. Exact write rules
permit that entry; a trailing slash permits descendants by path component.
Only creation of required ancestor directories is implicit, never deletion or
permission changes. Both write attempts and snapshot differences are checked.
Follow-up snapshots must form an unbroken chain.

## Interpreting results

Exit 0 means supplied normalized evidence passes its contract; exit 1 means
an observed violation; exit 2 means malformed or incomplete evidence.
Known violations retain failure status even when other evidence is incomplete.
Reports preserve sorted findings and input digests; output goes only to stdout.

Every report declares evidence_scope as supplied-normalized-evidence and
host_acceptance as not_measured. Choosing evidence_kind captured does not
promote the result to host acceptance. Flow, action, context, and handoff labels
are supplied by the adapter/reviewer. Raw-call coverage does not independently
prove those labels, native trace extraction, or conversational semantics.
Snapshots cover only the chosen root and can miss unrecorded transient or
outside-root activity. This is not enforcement of shell permissions.

## Validation and remaining Phase 2 work

    uv run --locked pytest tests/test_workflow_scope.py -q

Mutation tests cover route swaps, forbidden execution, transient writes, path
escapes, missing/duplicate calls, altered evidence, follow-up scope, and file
observations. A disposable real scanner test checks source preservation and
its permitted report artifact without running providers. These tests establish
checker behavior, not live prompt-replay success.

Phase 2 remains incomplete until supported host adapters capture complete native
traces, their effect classifications are reviewed, and exact prompts and
paraphrases are replayed in fresh sessions. Retain native traces, captures,
snapshots, loaded skill digests, host/model identity, failures, and skipped cases.
Review semantic flow/handoff judgments independently. Missing-runtime and
cross-host cases need measurement. No live inference or customer evaluation
execution is enabled by this tool.
