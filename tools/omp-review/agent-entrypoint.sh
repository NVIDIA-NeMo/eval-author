#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Only /work/out/review.json leaves the sandbox; transcripts and logs stay in /tmp (kept locally with --keep-work).
set -euo pipefail
mkdir -p "$PI_CODING_AGENT_DIR" /tmp/omp-review/sessions
cp /work/render/models.yml "$PI_CODING_AGENT_DIR/models.yml"
cd /work/repo
sync_status=0
# Bounded so a hanging build hook cannot hold the job past its timeout.
timeout 15m uv sync --locked >/tmp/omp-review/uv-sync.log 2>&1 || sync_status=$?
echo "$sync_status" >/tmp/omp-review/uv-sync.exit
status=0
omp -p --mode json --model "$OMP_REVIEW_SELECTOR" --thinking high --config /work/render/omp-config.yml \
  --session-dir /tmp/omp-review/sessions --no-lsp --no-title --approval-mode yolo --max-time 50m --cwd /work/repo \
  </work/context/prompt.md >/tmp/omp-review/events.jsonl 2>/tmp/omp-review/omp.stderr || status=$?
[ "$status" -eq 0 ] && [ -s /work/out/review.json ] || { echo "omp exit $status; review.json missing or empty" >&2; exit 1; }
