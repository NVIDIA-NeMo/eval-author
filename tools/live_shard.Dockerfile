# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

ARG DOCKER_IMAGE
ARG UV_IMAGE
ARG BASE_IMAGE
FROM ${DOCKER_IMAGE} AS engine
FROM ${UV_IMAGE} AS uv
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive
ARG APT_SNAPSHOT=20261105T000000Z
ARG COMPOSE_ARCH
ARG COMPOSE_SHA256
COPY --from=engine /usr/local/bin/docker /usr/local/bin/dockerd /usr/local/bin/containerd /usr/local/bin/containerd-shim-runc-v2 /usr/local/bin/ctr /usr/local/bin/runc /usr/local/bin/
# The pinned upstream wrapper prepares cgroup-v2 nesting for resource-limited trials.
COPY --from=engine /usr/local/bin/dind /usr/local/bin/docker-init /usr/local/bin/
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=engine /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY live_shard_apt.sh /opt/live_shard_apt.sh
# Pin the repository URI before apt has cached any Release metadata.
# Prefer the smaller signed xz index over the snapshot's slow gzip delivery path.
# Bootstrap USN-8996-1's APT fix with retries disabled before parallel acquisition.
RUN printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s/\nSuites: noble-security\nComponents: main\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' "$APT_SNAPSHOT" > /etc/apt/sources.list.d/ubuntu.sources && \
    printf 'EA_TOOLING_STAGE=apt_index uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)" && apt-get update -o Acquire::Retries=0 -o APT::Update::Error-Mode=any -o Acquire::CompressionTypes::Order=xz && \
    printf 'EA_TOOLING_STAGE=apt_bootstrap uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)" && \
    apt-get install -y --no-install-recommends -o Acquire::Retries=0 apt=2.8.4ubuntu0.1 libapt-pkg6.0t64=2.8.4ubuntu0.1 && \
    apt_version=$(dpkg-query -W -f='${Version}' apt) && test -n "$apt_version" && dpkg --compare-versions "$apt_version" ge 2.8.4ubuntu0.1 && \
    libapt_version=$(dpkg-query -W -f='${Version}' libapt-pkg6.0t64) && test -n "$libapt_version" && dpkg --compare-versions "$libapt_version" ge 2.8.4ubuntu0.1 && \
    printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s/\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' "$APT_SNAPSHOT" > /etc/apt/sources.list.d/ubuntu.sources && \
    printf 'EA_TOOLING_STAGE=apt_full_index uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)" && apt-get update -o Acquire::Retries=1 -o APT::Update::Error-Mode=any -o Acquire::CompressionTypes::Order=xz && \
    bash /opt/live_shard_apt.sh /var/cache/apt/archives "$APT_SNAPSHOT" \
    python3 python3-venv git curl ca-certificates bash iptables iproute2 xz-utils && \
    dpkg-query -W > /opt/execution-image-packages.txt && \
    rm /opt/live_shard_apt.sh && \
    rm -rf /var/lib/apt/lists/*
RUN printf 'EA_TOOLING_STAGE=compose_download uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)" && curl --fail --location --silent --show-error \
    "https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-${COMPOSE_ARCH}" \
    --output /tmp/docker-compose && \
    printf 'EA_TOOLING_STAGE=compose_verify uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)" && \
    printf '%s  %s\n' "$COMPOSE_SHA256" /tmp/docker-compose | sha256sum --check - && \
    install -D -m 0755 /tmp/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose && \
    rm /tmp/docker-compose && \
    printf 'EA_TOOLING_STAGE=versions uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)" && test "$(docker compose version --short)" = 5.5.1 && \
    case "$(uv --version)" in 'uv 0.12.3'|"uv 0.12.3 (${COMPOSE_ARCH}-unknown-linux-musl)") ;; *) exit 1 ;; esac && \
    docker --version | grep -F 'Docker version 29.7.2,' && \
    dockerd --version | grep -F 'Docker version 29.7.2,' && printf 'EA_TOOLING_STAGE=complete uptime=%s\n' "$( { read -r t _ < /proc/uptime && printf '%s' "$t"; } 2>/dev/null)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 HARBOR_TELEMETRY=0
CMD ["dind", "docker-init", "--", "dockerd", "--host=unix:///var/run/docker.sock", "--storage-driver=overlay2", "--userland-proxy=false"]
