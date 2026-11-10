#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
export LC_ALL=C
export PATH="$PATH:/usr/lib/apt"

archives=$1
snapshot=$2
shift 2
[[ "$snapshot" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { printf 'Invalid APT snapshot\n' >&2; exit 1; }
# USN-8996-1: retries require the redirect/hardlink fix in the bootstrapped APT.
apt_version=$(dpkg-query -W -f='${Version}' apt)
[[ -n "$apt_version" ]] && dpkg --compare-versions "$apt_version" ge 2.8.4ubuntu0.1 || {
    printf 'Patched APT required for acquisition retries\n' >&2; exit 1;
}
options=(-y --no-install-recommends -o "Dir::Cache::archives=$archives" -o Acquire::Retries=0)
plan=$(mktemp "${TMPDIR:-/tmp}/live-shard-apt.XXXXXX")
jobs=()
job_count=0
staging=
trap 'rm -f "$plan"; if [[ -n "$staging" ]]; then rm -rf -- "$staging"; fi' EXIT

printf 'EA_TOOLING_STAGE=apt_acquire uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
# print-uris defaults to MD5, and is not an authentication check. The recipe's
# signed-index update supplies trust; force SHA256 and keep normal install checks.
apt-get "${options[@]}" -qq --print-uris -o Acquire::ForceHash=SHA256 install "$@" > "$plan"
printf 'EA_TOOLING_STAGE=apt_plan_validate uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
entry="^'(https://snapshot[.]ubuntu[.]com/ubuntu/${snapshot}/pool/[a-zA-Z0-9/%+._:~-]+)' ([a-zA-Z0-9][a-zA-Z0-9%+._:~-]*[.]deb) ([1-9][0-9]*) (SHA256:[a-f0-9]{64})$"
names=("")
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ $entry ]] || { printf 'Invalid APT acquisition plan\n' >&2; exit 1; }
    url=${BASH_REMATCH[1]}
    name=${BASH_REMATCH[2]}
    digest=${BASH_REMATCH[4]}
    for previous in "${names[@]}"; do
        [[ "$previous" != "$name" ]] || { printf 'Duplicate APT archive\n' >&2; exit 1; }
    done
    names+=("$name")
    jobs+=("$url" "$name" "$digest")
    job_count=$((job_count + 3))
done < "$plan"
# apt-helper writes to its supplied destination, unlike apt-get's partial dir.
# Give _apt only a fresh private child, never ownership of the archive cache.
printf 'EA_TOOLING_STAGE=apt_staging uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
if (( job_count )); then
    staging=$(mktemp -d "$archives/live-shard-apt.XXXXXX")
    chmod 700 "$staging"
    chown _apt:root "$staging"
fi
# APT 2.7.14's DoDownloadFile queues all URL/file/hash triples before AcquireRun:
# https://github.com/Debian/apt/blob/2.7.14/cmdline/apt-helper.cc
# methods/http.cc reuses open connections. Keep four long-lived native queues,
# not one process (and fresh connection setup) per archive.
pids=("")
printf 'EA_TOOLING_STAGE=apt_download uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
for ((worker=0; worker<4; worker++)); do
    batch=()
    for ((i=worker*3; i<job_count; i+=12)); do
        batch+=("${jobs[i]}" "$staging/${jobs[i+1]}" "${jobs[i+2]}")
    done
    if (( ${#batch[@]} )); then
        # Retry transient acquisition failures once within the existing build cap.
        # APT retains hash validation and rejects permanent acquisition errors.
        apt-helper -o Acquire::Retries=1 download-file "${batch[@]}" &
        pids+=("$!")
    fi
done
failed=0
for pid in "${pids[@]}"; do
    [[ -n "$pid" ]] || continue
    wait "$pid" || failed=1
done
(( failed == 0 )) || exit 1
printf 'EA_TOOLING_STAGE=apt_promote uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
for ((i=1; i<job_count; i+=3)); do
    chown root:root "$staging/${jobs[i]}"
    chmod 644 "$staging/${jobs[i]}"
    mv -- "$staging/${jobs[i]}" "$archives/${jobs[i]}"
done
printf 'EA_TOOLING_STAGE=apt_install uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
apt-get "${options[@]}" --no-download install "$@"
printf 'EA_TOOLING_STAGE=apt_cleanup uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
