#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Launch the sandboxed omp PR review: an agent container on an internal network that can reach only a
# key-holding, budget-metering model proxy and an allowlisting egress proxy. The model key never enters the agent.
set -euo pipefail

usage() {
  echo "usage: run.sh --repo DIR --context DIR --out DIR [--status FILE] [--selftest-only] [--keep-work]" >&2
  exit 2
}

REPO="" CONTEXT="" OUT="" STATUS_FILE="" SELFTEST_ONLY=0 KEEP_WORK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --context) CONTEXT="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    # Run status (phase, exit code, duration, model spend) for the review poster; written outside the sandbox.
    --status) STATUS_FILE="${2:-}"; shift 2 ;;
    --selftest-only) SELFTEST_ONLY=1; shift ;;
    # Local debugging: keep the scratch directory (repo copy, transcript, logs) instead of deleting it.
    --keep-work) KEEP_WORK=1; shift ;;
    *) usage ;;
  esac
done

START=$(date +%s)
PHASE=setup
fail_input() { echo "run.sh: $1" >&2; exit 2; }
[ -n "${OMP_REVIEW_INFERENCE_HUB_API_KEY:-}" ] || fail_input "OMP_REVIEW_INFERENCE_HUB_API_KEY is empty"
[[ "${OMP_REVIEW_MODEL:-}" =~ ^[A-Za-z0-9._:/-]{1,200}$ ]] || fail_input "OMP_REVIEW_MODEL is missing or invalid"
[[ "${OMP_REVIEW_CONTEXT_WINDOW:-}" =~ ^[1-9][0-9]{0,8}$ ]] || fail_input "OMP_REVIEW_CONTEXT_WINDOW must be a positive integer"
[[ "${OMP_REVIEW_MAX_TOKENS:-}" =~ ^[1-9][0-9]{0,8}$ ]] || fail_input "OMP_REVIEW_MAX_TOKENS must be a positive integer"
[[ "${OMP_REVIEW_BUDGET_USD:-}" =~ ^[0-9]{1,6}(\.[0-9]{1,2})?$ ]] || fail_input "OMP_REVIEW_BUDGET_USD must be a dollar amount"
[ -n "${OMP_REVIEW_PRICES:-}" ] || fail_input "OMP_REVIEW_PRICES is empty"
# Wall-clock budget for the whole sandbox run; the workflow step timeout must leave room beyond it.
[[ "${OMP_REVIEW_MINUTES:=60}" =~ ^[1-9][0-9]{0,3}$ ]] || fail_input "OMP_REVIEW_MINUTES must be a positive integer"
DEADLINE=$((START + OMP_REVIEW_MINUTES * 60))
# CI runs the agent under gVisor (runsc); runc is the local default where gVisor is not installed.
RUNTIME="${OMP_REVIEW_RUNTIME:-runc}"
case "$RUNTIME" in
  runc | runsc) ;;
  *) fail_input "OMP_REVIEW_RUNTIME must be runc or runsc" ;;
esac
[ -n "$REPO" ] && [ -d "$REPO" ] || fail_input "--repo must be an existing directory"
[ -n "$CONTEXT" ] && [ -d "$CONTEXT" ] || fail_input "--context must be an existing directory"
[ -n "$OUT" ] || fail_input "--out is required"
mkdir -p "$OUT" || fail_input "cannot create --out"
REPO=$(cd "$REPO" && pwd)
CONTEXT=$(cd "$CONTEXT" && pwd)
OUT=$(cd "$OUT" && pwd)
export OMP_REVIEW_INFERENCE_HUB_API_KEY OMP_REVIEW_MODEL OMP_REVIEW_MAX_TOKENS OMP_REVIEW_BUDGET_USD OMP_REVIEW_PRICES

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TOOLS_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
ID="omp-review-${GITHUB_RUN_ID:-local}-$$"
WORK=$(mktemp -d)
mkdir -p "$WORK/home" "$WORK/tmp" "$WORK/render"
SELECTOR="inference-hub/$OMP_REVIEW_MODEL:high"
# The model proxy runs on the agent image's base image.
PROXY_IMAGE="python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"

# Ask the model proxy for its metered spend and record the run status for the review poster.
write_status() {
  local code=$1 timed_out=0
  [ "$code" -eq 124 ] && timed_out=1
  local fields=(--phase "$PHASE" --code "$code" --seconds "$(($(date +%s) - START))" --timed-out "$timed_out")
  # shellcheck disable=SC2016 # Python source, not shell
  local summary='import json, sys, urllib.request
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
try:
    spend = json.load(urllib.request.urlopen("http://127.0.0.1:8080/omp-review/spend", timeout=5))
except OSError:
    spend = {}
status = {"phase": args["--phase"], "exit_code": int(args["--code"]), "seconds": int(args["--seconds"]),
          "timed_out": args["--timed-out"] == "1", "spent_usd": spend.get("spent_usd"),
          "budget_usd": spend.get("budget_usd"), "refused": spend.get("refused"), "requests": spend.get("requests"),
          "hub_key_spend_delta_usd": spend.get("hub_key_spend_delta_usd")}
print(json.dumps(status))'
  local status
  status=$(docker exec "$ID-model" python3 -c "$summary" "${fields[@]}" 2>/dev/null) \
    || status=$(printf '{"phase": "%s", "exit_code": %d, "seconds": %d, "timed_out": %s}' \
      "$PHASE" "$code" "$(($(date +%s) - START))" "$([ "$timed_out" -eq 1 ] && echo true || echo false)")
  echo "run.sh: status $status" >&2
  [ -z "$STATUS_FILE" ] || printf '%s\n' "$status" >"$STATUS_FILE"
}

# Every container carries the omp-review label so the workflow can stop them even if this script is killed.
cleanup() {
  local code=$?
  write_status "$code" || true
  docker rm -f "$ID-model" "$ID-egress" >/dev/null 2>&1 || true
  docker network rm "$ID-int" "$ID-ext" >/dev/null 2>&1 || true
  docker image rm "omp-review-agent:$ID" "omp-review-egress:$ID" >/dev/null 2>&1 || true
  if [ "$KEEP_WORK" -eq 1 ]; then
    echo "run.sh: kept $WORK" >&2
  else
    rm -rf "$WORK" 2>/dev/null || true
  fi
  exit "$code"
}
trap cleanup EXIT

# Mount a throwaway copy: host-side git (actions/checkout's post step) later runs in $REPO, and a .git/config
# written by the sandbox (core.fsmonitor, hooks) would execute there.
cp -a "$REPO" "$WORK/repo"

cat >"$WORK/render/models.yml" <<EOF
providers:
  inference-hub:
    baseUrl: http://model-proxy:8080
    api: anthropic-messages
    auth: apiKey
    apiKey: OMP_REVIEW_PROXY_PLACEHOLDER
    models:
      - id: "$OMP_REVIEW_MODEL"
        name: "$OMP_REVIEW_MODEL (Inference Hub)"
        reasoning: true
        input: [text, image]
        contextWindow: $OMP_REVIEW_CONTEXT_WINDOW
        maxTokens: $OMP_REVIEW_MAX_TOKENS
        thinking: { mode: anthropic-adaptive, efforts: [low, medium, high, xhigh, max] }
EOF
# Nobody can answer the ask tool in CI; deny it rather than let a question end the run.
cat >"$WORK/render/omp-config.yml" <<EOF
modelRoles:
  default: "$SELECTOR"
  slow: "$SELECTOR"
  smol: "$SELECTOR"
  task: "$SELECTOR"
  plan: "$SELECTOR"
defaultThinkingLevel: high
async:
  enabled: false
tools:
  approvalMode: yolo
  approval:
    ask: deny
EOF

docker build -q -t "omp-review-agent:$ID" -f "$SCRIPT_DIR/agent.Dockerfile" "$SCRIPT_DIR" >/dev/null
docker build -q -t "omp-review-egress:$ID" -f "$SCRIPT_DIR/egress.Dockerfile" "$SCRIPT_DIR" >/dev/null

docker network create --internal "$ID-int" >/dev/null
docker network create "$ID-ext" >/dev/null

docker run -d --name "$ID-model" --label omp-review --network "$ID-ext" --user 65534:65534 --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  -e OMP_REVIEW_INFERENCE_HUB_API_KEY -e OMP_REVIEW_MODEL -e OMP_REVIEW_MAX_TOKENS -e OMP_REVIEW_BUDGET_USD \
  -e OMP_REVIEW_PRICES -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$TOOLS_DIR/omp_review_model_proxy.py:/proxy/omp_review_model_proxy.py:ro" \
  "$PROXY_IMAGE" python3 /proxy/omp_review_model_proxy.py >/dev/null
docker network connect --alias model-proxy "$ID-int" "$ID-model"
docker run -d --name "$ID-egress" --label omp-review --network "$ID-ext" --security-opt no-new-privileges \
  "omp-review-egress:$ID" >/dev/null
docker network connect --alias egress-proxy "$ID-int" "$ID-egress"
# gVisor cannot reach Docker's embedded DNS on user-defined networks, so pin the sidecar names in /etc/hosts.
sidecar_ip() { docker inspect -f "{{(index .NetworkSettings.Networks \"$ID-int\").IPAddress}}" "$1"; }
MODEL_IP=$(sidecar_ip "$ID-model")
EGRESS_IP=$(sidecar_ip "$ID-egress")

# The only place the agent container's flags live; the model key is never passed here.
# gVisor uses about two host PIDs per sandboxed process, hence the PID limit.
agent_run() {
  docker run --rm --label omp-review --runtime "$RUNTIME" --network "$ID-int" \
    --add-host "model-proxy:$MODEL_IP" --add-host "egress-proxy:$EGRESS_IP" \
    --user "$(id -u):$(id -g)" --read-only --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 8192 --memory "${OMP_REVIEW_MEMORY:-12g}" \
    --cpus "${OMP_REVIEW_CPUS:-3}" \
    -e HOME=/home/reviewer -e PI_CODING_AGENT_DIR=/home/reviewer/.omp/agent -e TMPDIR=/tmp \
    -e UV_CACHE_DIR=/home/reviewer/.cache/uv \
    -e HTTP_PROXY=http://egress-proxy:3128 -e HTTPS_PROXY=http://egress-proxy:3128 \
    -e http_proxy=http://egress-proxy:3128 -e https_proxy=http://egress-proxy:3128 \
    -e NO_PROXY=model-proxy,localhost,127.0.0.1 -e no_proxy=model-proxy,localhost,127.0.0.1 \
    -e OMP_REVIEW_PROXY_PLACEHOLDER=proxy-injected -e OMP_REVIEW_SELECTOR="$SELECTOR" \
    -e OMP_REVIEW_DEADLINE="$DEADLINE" \
    -v "$WORK/repo":/work/repo -v "$CONTEXT":/work/context:ro -v "$OUT":/work/out \
    -v "$SCRIPT_DIR":/work/config:ro -v "$WORK/render":/work/render:ro \
    -v "$WORK/home":/home/reviewer -v "$WORK/tmp":/tmp \
    "omp-review-agent:$ID" "$@"
}

ready=0
for _ in $(seq 30); do
  model_code=$(agent_run curl -s -o /dev/null -w '%{http_code}' http://model-proxy:8080/ || true)
  egress_code=$(agent_run curl -sI -m 10 -x http://egress-proxy:3128 -o /dev/null -w '%{http_code}' https://pypi.org/simple/ || true)
  if [ "$model_code" = 403 ] && [ "$egress_code" = 200 ]; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "sidecars not ready (model-proxy=$model_code egress-proxy=$egress_code)" >&2
  docker logs "$ID-model" >&2 || true
  docker logs "$ID-egress" >&2 || true
  exit 1
fi

PHASE=selftest
pass() { echo "selftest: PASS $1"; }
fail() { echo "selftest: FAIL $1" >&2; exit 1; }

if agent_run curl -sS -m 5 --noproxy '*' https://pypi.org/simple/ >/dev/null 2>&1; then
  fail direct-egress-blocked
fi
pass direct-egress-blocked

if agent_run curl -sS -m 10 -x http://egress-proxy:3128 https://example.com/ >/dev/null 2>&1; then
  fail allowlist-enforced
fi
pass allowlist-enforced

[ "$(agent_run curl -s -o /dev/null -w '%{http_code}' http://model-proxy:8080/v1/models || true)" = 403 ] \
  || fail model-proxy-restricted
pass model-proxy-restricted

agent_env=$(agent_run sh -c 'env; tr "\0" "\n" </proc/self/environ') || fail no-key-in-agent
if grep -F -q -- "$OMP_REVIEW_INFERENCE_HUB_API_KEY" <<<"$agent_env"; then
  fail no-key-in-agent
fi
pass no-key-in-agent

if [ "$SELFTEST_ONLY" -eq 1 ]; then
  omp_print() {
    # shellcheck disable=SC2016 # expanded inside the agent container
    agent_run sh -c 'mkdir -p "$PI_CODING_AGENT_DIR" && cp /work/render/models.yml "$PI_CODING_AGENT_DIR/" &&
      omp -p --model "$OMP_REVIEW_SELECTOR" --thinking high --config /work/render/omp-config.yml --no-session --no-lsp "$1"' \
      omp-print "$1"
  }
  reply=$(omp_print "Reply with exactly the word OK.") || fail model-ping
  grep -q OK <<<"$reply" || fail model-ping
  pass model-ping
  expected=$(git -C "$REPO" rev-parse HEAD)
  reply=$(omp_print "Use the bash tool to run: git -C /work/repo rev-parse HEAD. Reply with only the 40-character SHA it printed.") \
    || fail tool-roundtrip
  grep -q -- "$expected" <<<"$reply" || fail tool-roundtrip
  pass tool-roundtrip
  exit 0
fi

PHASE=review
status=0
agent_run bash /work/config/agent-entrypoint.sh || status=$?
exit "$status"
