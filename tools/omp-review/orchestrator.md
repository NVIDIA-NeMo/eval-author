<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Review pull request {{PR_URL}} (#{{PR_NUMBER}}). Perform a detailed review of this PR.{{REREVIEW}}

This run is unattended: never stop to ask anything, and always finish by writing /work/out/review.json.

## Environment
- The PR head is checked out at /work/repo (detached HEAD, full history, every branch as `origin/*`). `uv sync --locked` already ran; use `uv run --locked …`. If /tmp/omp-review/uv-sync.exit is not 0, the sync failed (output in /tmp/omp-review/uv-sync.log): report it as a finding when the PR caused it, and say which checks it prevented. You may create worktrees, e.g. `git worktree add /tmp/omp-review/base <base_sha>` for before/after comparisons. Do not edit tracked files in /work/repo.
- PR metadata: /work/context/pr.json (title, body, author, base/head refs and SHAs). Maintainer discussion: /work/context/discussion.json. Earlier automated findings: /work/context/prior-findings.json. Head commit check runs: /work/context/checks.json. Use these instead of `pr://`, `gh`, or the GitHub API; there are no GitHub credentials.
- The full test suite does not run here; the repository's CI runs it on this PR. /work/context/checks.json is a snapshot from when this review started, so those checks may still be running. Never run the full suite; run focused tests for the code under review.
- Network: PyPI and anonymous github.com reads work through the configured proxy; everything else is blocked and web_search is unavailable. Docker and model credentials are unavailable: say so when a check needs them instead of reporting it as a PR bug.
- Model calls share one budget for this run. `curl -s http://model-proxy:8080/omp-review/spend` shows `spent_usd` and `budget_usd`; calls that would pass the budget are refused. Once more than 80% is spent, dispatch no new work and write review.json from what you have.
- Scratch files go under /tmp/omp-review/<your-name>/.

## Procedure
1. Read pr.json, run `git -C /work/repo diff --stat <base_sha>...<head_sha>`, and read the small diffs to understand scope.
2. Split the change into 1–9 slices by area (e.g. one skill's scripts; skill instructions and references; docs; tests; CI and tools). Add one cross-cutting consistency slice when the PR spans more than one area.
3. Dispatch every slice in ONE `task` call, each task with `agent: "reviewer"`. Shared `context`: `# Goal` (PR number and title, /work/repo, the diff command with both SHAs, the environment facts above, the AGENTS.md rules relevant to this change, a 3–6 sentence PR summary you wrote) and `# Output contract`: report only real, evidence-backed findings introduced by this PR; line numbers on the PR head (RIGHT side), at most 10 lines, inside lines the diff adds or changes, verified by reading the file; prefer findings proven by execution — focused pytest, throwaway scripts, and the skill's own bundled scripts run in scratch workspaces exactly as the skill instructs an agent to — and put the command and observed output in the body; mark unverified claims [INFERENCE]; never edit tracked files, never run the full suite. Per task: `# Target` (exact files) and `# Focus` (concrete risks to check).
4. Wait until every reviewer has returned (call `wait` if any is still running).
5. Verify each finding: re-read the cited lines; re-run cheap reproductions when evidence is thin. Drop pre-existing, speculative, style-only, and duplicate findings; merge findings with one root cause. On a re-review, do not repeat a prior finding whose cited code is unchanged since the earlier review.
6. Write /work/out/review.json with the `write` tool, then stop. Never post anything anywhere.

## /work/out/review.json
Exactly these keys:
```json
{"version": 1, "head_sha": "<head_sha from pr.json>", "overall_correctness": "correct|incorrect",
 "explanation": "<1-3 plain-text sentences, ≤1000 chars>", "confidence": 0.0,
 "findings": [{"title": "<imperative, ≤80 chars>", "body": "<one paragraph, ≤4000 chars: bug, trigger, impact, evidence; optional ```suggestion block>",
   "priority": 0, "confidence": 0.0, "file_path": "<repo-relative>", "line_start": 1, "line_end": 1}]}
```
Priority: 0 blocks release or operations for any input (data corruption, auth bypass); 1 high, fix next cycle; 2 medium, fix eventually (edge-case mishandling); 3 nice to have. `incorrect` when any priority 0–2 finding remains. At most 50 findings, highest priority first; line_end − line_start ≤ 9. Any limit exceeded rejects the whole file and posts no findings, so trim command output in bodies to the decisive lines.
