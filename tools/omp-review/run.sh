#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Launch the sandboxed omp PR review: an agent container on an internal network that can reach only a
# key-injecting model proxy and an allowlisting egress proxy. The model key never enters the agent container.
set -euo pipefail

usage() {
  echo "usage: run.sh --repo DIR --context DIR --out DIR [--selftest-only] [--keep-work]" >&2
  exit 2
}

REPO="" CONTEXT="" OUT="" SELFTEST_ONLY=0 KEEP_WORK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --context) CONTEXT="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --selftest-only) SELFTEST_ONLY=1; shift ;;
    # Local debugging: keep the scratch directory (repo copy, transcript, test logs) instead of deleting it.
    --keep-work) KEEP_WORK=1; shift ;;
    *) usage ;;
  esac
done

fail_input() { echo "run.sh: $1" >&2; exit 2; }
[ -n "${OMP_REVIEW_INFERENCE_HUB_API_KEY:-}" ] || fail_input "OMP_REVIEW_INFERENCE_HUB_API_KEY is empty"
[[ "${OMP_REVIEW_MODEL:-}" =~ ^[A-Za-z0-9._:/-]{1,200}$ ]] || fail_input "OMP_REVIEW_MODEL is missing or invalid"
[[ "${OMP_REVIEW_CONTEXT_WINDOW:-}" =~ ^[1-9][0-9]{0,8}$ ]] || fail_input "OMP_REVIEW_CONTEXT_WINDOW must be a positive integer"
[[ "${OMP_REVIEW_MAX_TOKENS:-}" =~ ^[1-9][0-9]{0,8}$ ]] || fail_input "OMP_REVIEW_MAX_TOKENS must be a positive integer"
case "${OMP_REVIEW_MODEL_API:-}" in
  anthropic-messages | openai-completions) ;;
  *) fail_input "OMP_REVIEW_MODEL_API must be anthropic-messages or openai-completions" ;;
esac
[ -n "$REPO" ] && [ -d "$REPO" ] || fail_input "--repo must be an existing directory"
[ -n "$CONTEXT" ] && [ -d "$CONTEXT" ] || fail_input "--context must be an existing directory"
[ -n "$OUT" ] || fail_input "--out is required"
mkdir -p "$OUT" || fail_input "cannot create --out"
REPO=$(cd "$REPO" && pwd)
CONTEXT=$(cd "$CONTEXT" && pwd)
OUT=$(cd "$OUT" && pwd)
export OMP_REVIEW_INFERENCE_HUB_API_KEY

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ID="omp-review-${GITHUB_RUN_ID:-local}-$$"
WORK=$(mktemp -d)
mkdir -p "$WORK/home" "$WORK/tmp" "$WORK/render"
SELECTOR="inference-hub/$OMP_REVIEW_MODEL:high"
MODEL_PROXY_IMAGE="nginx:1.31.8-alpine@sha256:fed7e14be83cbd2661a75cdc5b872d936cf155d46c327084d30b8df19ae88d72"

# Every container carries the omp-review label so the workflow can stop them even if this script is killed.
cleanup() {
  docker rm -f "$ID-model" "$ID-egress" >/dev/null 2>&1 || true
  docker network rm "$ID-int" "$ID-ext" >/dev/null 2>&1 || true
  docker image rm "omp-review-agent:$ID" "omp-review-egress:$ID" >/dev/null 2>&1 || true
  if [ "$KEEP_WORK" -eq 1 ]; then
    echo "run.sh: kept $WORK" >&2
  else
    rm -rf "$WORK" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Mount a throwaway copy: host-side git (actions/checkout's post step) later runs in $REPO, and a .git/config
# written by the sandbox (core.fsmonitor, hooks) would execute there.
cp -a "$REPO" "$WORK/repo"

if [ "$OMP_REVIEW_MODEL_API" = anthropic-messages ]; then
  base_url=http://model-proxy:8080
  thinking='{ mode: anthropic-adaptive, efforts: [low, medium, high, xhigh, max] }'
  compat=""
else
  base_url=http://model-proxy:8080/v1
  thinking='{ mode: effort, efforts: [low, medium, high] }'
  compat="        compat: { supportsReasoningEffort: true }"
fi
cat >"$WORK/render/models.yml" <<EOF
providers:
  inference-hub:
    baseUrl: $base_url
    api: $OMP_REVIEW_MODEL_API
    auth: apiKey
    apiKey: OMP_REVIEW_PROXY_PLACEHOLDER
    models:
      - id: "$OMP_REVIEW_MODEL"
        name: "$OMP_REVIEW_MODEL (Inference Hub)"
        reasoning: true
        input: [text, image]
        contextWindow: $OMP_REVIEW_CONTEXT_WINDOW
        maxTokens: $OMP_REVIEW_MAX_TOKENS
        thinking: $thinking
${compat}
EOF
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
EOF

docker build -q -t "omp-review-agent:$ID" -f "$SCRIPT_DIR/agent.Dockerfile" "$SCRIPT_DIR" >/dev/null
docker build -q -t "omp-review-egress:$ID" -f "$SCRIPT_DIR/egress.Dockerfile" "$SCRIPT_DIR" >/dev/null

docker network create --internal "$ID-int" >/dev/null
docker network create "$ID-ext" >/dev/null

docker run -d --name "$ID-model" --label omp-review --network "$ID-ext" --security-opt no-new-privileges \
  -e OMP_REVIEW_INFERENCE_HUB_API_KEY \
  -v "$SCRIPT_DIR/model-proxy.conf.template:/etc/nginx/templates/default.conf.template:ro" \
  "$MODEL_PROXY_IMAGE" >/dev/null
docker network connect --alias model-proxy "$ID-int" "$ID-model"
docker run -d --name "$ID-egress" --label omp-review --network "$ID-ext" --security-opt no-new-privileges \
  "omp-review-egress:$ID" >/dev/null
docker network connect --alias egress-proxy "$ID-int" "$ID-egress"

# The only place the agent container's flags live; the model key is never passed here.
agent_run() {
  docker run --rm --label omp-review --network "$ID-int" --user "$(id -u):$(id -g)" --read-only --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 4096 --memory "${OMP_REVIEW_MEMORY:-12g}" \
    --cpus "${OMP_REVIEW_CPUS:-3}" \
    -e HOME=/home/reviewer -e PI_CODING_AGENT_DIR=/home/reviewer/.omp/agent -e TMPDIR=/tmp \
    -e UV_CACHE_DIR=/home/reviewer/.cache/uv \
    -e HTTP_PROXY=http://egress-proxy:3128 -e HTTPS_PROXY=http://egress-proxy:3128 \
    -e http_proxy=http://egress-proxy:3128 -e https_proxy=http://egress-proxy:3128 \
    -e NO_PROXY=model-proxy,localhost,127.0.0.1 -e no_proxy=model-proxy,localhost,127.0.0.1 \
    -e OMP_REVIEW_PROXY_PLACEHOLDER=proxy-injected -e OMP_REVIEW_SELECTOR="$SELECTOR" \
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

agent_env=$(agent_run sh -c 'env; cat /proc/self/environ') || fail no-key-in-agent
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

status=0
agent_run bash /work/config/agent-entrypoint.sh || status=$?
exit "$status"
