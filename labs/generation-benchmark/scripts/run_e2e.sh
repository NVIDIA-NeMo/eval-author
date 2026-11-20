#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Generation benchmark E2E: smoke (template G) or live (Codex G).
# Optional validation helper; not part of the checked-in configuration tests.
# Override GENERATION_FIXTURES_ROOT and GENERATION_WORK_ROOT as needed.
# Before doing any work, the script lists every leftover from an earlier run:
# GENERATION_WORK_ROOT, plus tasks.generated.yml and generated-runtime/ in the case.
# Remove every path in that list before running again.
# Success: driver exit 0 and at least one subset has a new task with delta_m > 0.

set -euo pipefail

MODE="${1:-}"
if [[ "$MODE" != "smoke" && "$MODE" != "live" ]]; then
  echo "usage: $0 smoke|live" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FIXTURES_ROOT="${GENERATION_FIXTURES_ROOT:-$REPO_ROOT/../nemo-eval-author-fixtures}"
CASE_DIR="$FIXTURES_ROOT/cases/generation-benchmark-poc"
WORK_ROOT="${GENERATION_WORK_ROOT:-/tmp/gen-bench-poc-e2e}"
resolve_path() {
  python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$1"
}
CASE_DIR="$(resolve_path "$CASE_DIR")"
WORK_ROOT="$(resolve_path "$WORK_ROOT")"
LOG_PATH="${GENERATION_E2E_LOG:-/tmp/gen-bench-poc-e2e-run.log}"
RUN_ID="${GENERATION_RUN_ID:-poc-${MODE}-$(date +%Y%m%d-%H%M%S)}"

if [[ "$MODE" == "smoke" ]]; then
  GEN_FLAG=(--generation fake)
else
  GEN_FLAG=(--generation codex)
fi

export EVAL_AUTHOR_ROOT="${EVAL_AUTHOR_ROOT:-$REPO_ROOT}"
export GENERATION_CASE_ROOT="$CASE_DIR"
export PYTHONPATH="$REPO_ROOT/labs/generation-benchmark:$REPO_ROOT/labs/metric-benchmark"

cd "$REPO_ROOT"
if command -v direnv >/dev/null 2>&1; then
  eval "$(direnv export bash 2>/dev/null)" || true
fi

# Closure oracle repeats use configs/agent-oracle.yml (Codex + inference API) in smoke and live.
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://inference-api.nvidia.com/v1}"
if [[ -z "${OPENAI_API_KEY:-}" && -n "${NVIDIA_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="$NVIDIA_API_KEY"
fi

if [[ "$MODE" == "live" ]]; then
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "live E2E requires OPENAI_API_KEY or NVIDIA_API_KEY (load direnv / inference credentials)" >&2
    exit 2
  fi
fi

# One list for the scratch work root and the generated tasks saved in the case.
# The driver repeats the case check after this; reporting both here avoids a
# second failure after the operator clears only the work root.
require_fresh_experiment() {
  local -a problems=()
  if [[ -z "$WORK_ROOT" ]]; then
    problems+=("GENERATION_WORK_ROOT is empty")
  elif [[ -L "$WORK_ROOT" ]]; then
    problems+=("$WORK_ROOT (work root is a symlink)")
  elif [[ -e "$WORK_ROOT" && ! -d "$WORK_ROOT" ]]; then
    problems+=("$WORK_ROOT (work root exists and is not a directory)")
  elif [[ -d "$WORK_ROOT" && -n "$(ls -A "$WORK_ROOT")" ]]; then
    problems+=("$WORK_ROOT (work root already contains files)")
  fi
  local manifest="$CASE_DIR/tasks.generated.yml"
  local runtime="$CASE_DIR/generated-runtime"
  if [[ -e "$manifest" ]]; then
    problems+=("$manifest")
  fi
  if [[ -d "$runtime" && -n "$(ls -A "$runtime")" ]]; then
    problems+=("$runtime")
  fi
  if ((${#problems[@]} == 0)); then
    return 0
  fi
  echo "Remove these before running the experiment:" >&2
  local item
  for item in "${problems[@]}"; do
    echo "- $item" >&2
  done
  return 1
}

require_fresh_experiment

uv run python "$CASE_DIR/prepare_benchmark.py" "${GEN_FLAG[@]}" --eval-author "$EVAL_AUTHOR_ROOT"

set +e
uv run python labs/generation-benchmark/run_generation_benchmark.py \
  --benchmark "$CASE_DIR/experiment.yml" \
  --case poc \
  --work-root "$WORK_ROOT" \
  --run-id "$RUN_ID" \
  2>&1 | tee "$LOG_PATH"
STATUS=${PIPESTATUS[0]}
set -e

if [[ "$STATUS" -ne 0 ]]; then
  echo "benchmark driver failed: exit $STATUS (log: $LOG_PATH)" >&2
  exit "$STATUS"
fi

export WORK_ROOT
uv run python - <<'PY'
import json
import os
import sys
from pathlib import Path

work = Path(os.environ["WORK_ROOT"])
results = json.load((work / "generation-results.json").open(encoding="utf-8"))
analysis = json.load((work / "generation-analysis.json").open(encoding="utf-8"))
subsets = results.get("subsets") or []
if len(subsets) < 2:
    sys.exit(f"expected 2 subsets in generation-results.json, got {len(subsets)}")
by_id = {row.get("eval_subset_id"): row for row in analysis.get("subsets") or []}
improved = []
for row in subsets:
    subset_id = row.get("eval_subset_id")
    new_ids = row.get("new_task_ids") or []
    delta = (by_id.get(subset_id) or {}).get("delta_m")
    print(f"  {subset_id}: delta_m={delta} new_task_ids={new_ids}")
    if new_ids and isinstance(delta, (int, float)) and delta > 0:
        improved.append(subset_id)
if not improved:
    sys.exit("expected at least one subset with a new task and delta_m > 0")
mode = results.get("generation_mode", "unknown")
print(f"  generation_mode={mode}")
print("E2E OK:", work)
PY
