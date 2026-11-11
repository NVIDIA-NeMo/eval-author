#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
export LC_ALL=C

# Observation only: preserve bootstrap's fail-closed status and existing CI cap.
timeout --kill-after=1s 25s bash "$(dirname "${BASH_SOURCE[0]}")/diagnose_native_apt_https.sh" 2>/dev/null || :

root=$1
mkdir -m 700 "$root"
root=$(cd "$root" && pwd -P)
export TMPDIR="$root"
architecture=$(dpkg --print-architecture)
case "$architecture" in
    amd64) triplet=x86_64-linux-gnu ;;
    arm64) triplet=aarch64-linux-gnu ;;
    *) printf 'Unsupported native APT architecture\n' >&2; exit 1 ;;
esac
mkdir -p "$root/lists/partial" "$root/archives/partial" "$root/config-parts"
printf 'Dir::Etc::parts "%s/config-parts";\nDir::Etc::main "%s/apt.conf";\n' "$root" "$root" > "$root/apt.conf"
export APT_CONFIG="$root/apt.conf"
printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/\nSuites: noble-security\nComponents: main\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' > "$root/ubuntu.sources"
# USN-8996-1: acquire without retries, never install or change host APT state.
options=(-o "Dir::Etc::sourcelist=$root/ubuntu.sources" -o Dir::Etc::sourceparts=-
    -o Dir::Etc::main=- -o Dir::Etc::parts=- -o "Dir::State::lists=$root/lists"
    -o "Dir::Cache::archives=$root/archives" -o "Dir::Cache::pkgcache=$root/pkgcache.bin"
    -o "Dir::Cache::srcpkgcache=$root/srcpkgcache.bin" -o Acquire::Retries=0
    -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30
    -o APT::Update::Error-Mode=any -o Acquire::CompressionTypes::Order=xz
    -o Acquire::ForceHash=SHA256)
apt-get "${options[@]}" update
cd "$root/archives"
apt-get "${options[@]}" download apt=2.8.4ubuntu0.1 libapt-pkg6.0t64=2.8.4ubuntu0.1
for package in apt libapt-pkg6.0t64; do
    archive="$root/archives/${package}_2.8.4ubuntu0.1_${architecture}.deb"
    metadata=$(dpkg-deb --show --showformat='${Package}\n${Version}\n${Architecture}' "$archive")
    [[ "$metadata" == "$(printf '%s\n2.8.4ubuntu0.1\n%s' "$package" "$architecture")" ]] || {
        printf 'Unexpected native APT package provenance\n' >&2; exit 1;
    }
done
for package in apt libapt-pkg6.0t64; do
    dpkg-deb --extract "$root/archives/${package}_2.8.4ubuntu0.1_${architecture}.deb" "$root"
done
test -f "$root/usr/lib/$triplet/libapt-pkg.so.6.0"
version=$(LD_LIBRARY_PATH="$root/usr/lib/$triplet" "$root/usr/lib/apt/apt-helper" --version)
[[ "$version" == "apt 2.8.4 ($architecture)" ]] || {
    printf 'Unexpected native APT executable version\n' >&2; exit 1;
}
