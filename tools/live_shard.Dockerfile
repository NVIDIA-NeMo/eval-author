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
# Pin the repository URI before apt has cached any Release metadata.
RUN printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s/\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' "$APT_SNAPSHOT" > /etc/apt/sources.list.d/ubuntu.sources && \
    printf 'EA_TOOLING_STAGE=apt_index\n' && apt-get update -o APT::Update::Error-Mode=any && \
    printf 'EA_TOOLING_STAGE=apt_install\n' && apt-get install -y --no-install-recommends \
    python3 python3-venv git curl ca-certificates bash iptables iproute2 xz-utils && \
    dpkg-query -W > /opt/execution-image-packages.txt && \
    rm -rf /var/lib/apt/lists/*
RUN printf 'EA_TOOLING_STAGE=compose_download\n' && curl --fail --location --silent --show-error \
    "https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-${COMPOSE_ARCH}" \
    --output /tmp/docker-compose && \
    printf 'EA_TOOLING_STAGE=compose_verify\n' && \
    printf '%s  %s\n' "$COMPOSE_SHA256" /tmp/docker-compose | sha256sum --check - && \
    install -D -m 0755 /tmp/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose && \
    rm /tmp/docker-compose && \
    printf 'EA_TOOLING_STAGE=versions\n' && test "$(docker compose version --short)" = 5.5.1 && \
    case "$(uv --version)" in 'uv 0.12.3'|"uv 0.12.3 (${COMPOSE_ARCH}-unknown-linux-musl)") ;; *) exit 1 ;; esac && \
    docker --version | grep -F 'Docker version 29.7.2,' && \
    dockerd --version | grep -F 'Docker version 29.7.2,' && printf 'EA_TOOLING_STAGE=complete\n'
ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 HARBOR_TELEMETRY=0
CMD ["dind", "docker-init", "--", "dockerd", "--host=unix:///var/run/docker.sock", "--storage-driver=overlay2", "--userland-proxy=false"]
