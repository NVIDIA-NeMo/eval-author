#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

export LC_ALL=C

url=https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/dists/noble-security/InRelease
for route in 185.125.189.69 185.125.189.37 185.125.189.36 dns; do
    resolve=()
    label=native_apt_https_dns
    if [[ "$route" != dns ]]; then
        resolve=(--resolve "snapshot.ubuntu.com:443:$route")
        label="native_apt_https_ip_$route"
    fi
    # Direct HTTPS matches APT's route; ignore curlrc and ambient proxy credentials.
    curl --disable --silent --output /dev/null --proto '=https' --noproxy '*' \
        --connect-timeout 5 --max-time 5 \
        --write-out "$label http=%{http_code} connect=%{time_connect} appconnect=%{time_appconnect} total=%{time_total}"$'\n' \
        "${resolve[@]}" "$url" 2>/dev/null || :
done
