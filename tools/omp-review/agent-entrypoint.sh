#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
mkdir -p "$PI_CODING_AGENT_DIR" /tmp/omp-review /work/out/sessions
cp /work/render/models.yml "$PI_CODING_AGENT_DIR/models.yml"
cd /work/repo
sync_status=0
uv sync --locked >/tmp/omp-review/uv-sync.log 2>&1 || sync_status=$?
echo "$sync_status" >/tmp/omp-review/uv-sync.exit
( uv run --locked pytest -q -n auto -p no:cacheprovider >/tmp/omp-review/full-tests.log 2>&1; echo $? >/tmp/omp-review/full-tests.exit ) &
status=0
omp -p --mode json --model "$OMP_REVIEW_SELECTOR" --thinking high --config /work/render/omp-config.yml \
  --session-dir /work/out/sessions --no-lsp --no-title --approval-mode yolo --max-time 50m --cwd /work/repo \
  </work/context/prompt.md >/work/out/events.jsonl || status=$?
cp /tmp/omp-review/uv-sync.log /tmp/omp-review/full-tests.log /work/out/ 2>/dev/null || true
[ "$status" -eq 0 ] && [ -s /work/out/review.json ] || { echo "omp exit $status; review.json missing or empty" >&2; exit 1; }
