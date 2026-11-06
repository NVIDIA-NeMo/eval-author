<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Live runtime contracts and diagnostic boundaries

The [CI report guide](../skill-evaluation-ci.md) covers operation, local commands,
requirements, and report interpretation. This note records the reasons for the
native integration and evidence boundaries; the implementation remains the source
of truth for version pins and supported configurations.

## Guarding the native adapter

SkillEvaluator 0.7.0 does not expose Harbor's `--agent-kwarg version=<release>`;
its per-agent configuration accepts only a model. The adapter therefore checks
the evaluator's Git revision in `direct_url.json`, package versions, and the
private agent-kwargs table before adding the OpenCode version. Source hashes
cover the private call paths used by the adapter. Existing overrides or contract
drift stop dispatch instead of silently adapting to a different native runtime.

The native command builder forwards the pin through preflight and both paired
arms without replacing the gateway, Docker backend, or grader. Harbor can report
a configured version without executing version detection, so that field is not
binary attestation. The patched path independently checks the host/container
binary hashes and version command and writes a private trial receipt.

The standalone binary replaces only the activated path's redundant NVM/npm
OpenCode bootstrap. Harbor's base setup and required system dependencies remain.
The patched CLI embeds its own runtime; installing another NVM-managed Node 22
would add an unused network dependency before every trial. The ordinary,
unactivated installer is unchanged.

## Task-bound prepared images

An environment-content hash alone cannot prevent one arm from consuming the
other arm's image or changing resource and working-directory settings. Each
identity also binds the native task name, paired arm, normalized full task
configuration, and context paths, modes, and bytes. Only exact same-name native
gateway credential/provider placeholders can differ between credential-free and
credentialed staging. Other overrides require new preparation.

The original Dockerfile is validated before native staging can replace an
unsupported file with a default. The supported grammar is deliberately narrow:
one leading `FROM`, without parser directives, heredocs, additional stages, or
inherited `ONBUILD` triggers. Builds layer from inspected immutable image IDs,
not mutable aliases. Explicit installation shells avoid inheriting incompatible
custom `SHELL` instructions; task execution metadata is preserved.

Startup and phase network-control policies can build native sidecar images
outside Compose. Those policies are rejected before startup rather than treating
Compose's no-build/no-pull flags as sufficient coverage. An externally
disconnected offline setup test does not establish support for these policies.

Preparation uses one monotonic deadline across Docker operations, with separate
job-budget checks before credential handoff and evaluator dispatch. A smaller
remaining preparation allowance must never consume the reserved execution and
reporting time. Process timeout bounds do not prove that all descendants or
Docker-daemon build work stopped.

## Public diagnostic evidence

Public diagnostics are allowlisted projections, not redacted raw logs. Even a
credential-free build can print task content, paths, URLs, or other private data.
The absence of inference credentials is not permission to upload build logs or
exception messages. Fixed categories and bounded counters make failures
observable without publishing their underlying payloads.

Native agent output is not a trusted channel for identifying infrastructure
failures. A `NonZeroAgentExitCodeError` may carry the observed signature
`bun_node_epipe` when a bounded non-JSON block in the same trial's `opencode.txt`
matches the Bun/Node stream-error pattern. JSON payloads and ordinary EPIPE
mentions do not qualify. Identical spoofed raw output is indistinguishable;
this is an observation, not proof of the originating stream, process, or cause.
Only stdout and runtime stderr actually retained in that file are covered.

The signature does not change `agent_command_failed`, incomplete status,
scoring, or retry behavior. Missing diagnostic evidence remains unknown; an
exit code, empty CLI output, or a failed command's `--format=json` flag cannot
establish provider failure or whether response headers arrived.

## Acceptance limits

Prepared-image reuse, a cold preparation run, native offline controls, and
model-backed evaluation are different checks. Evidence must identify the tested
source and platform; ARM64 execution does not establish AMD64 execution, and a
warm-cache success does not validate a cold CI time budget. A successful later
run does not determine the cause of an earlier first-response timeout or prove
cleanup after forced evaluator termination.
