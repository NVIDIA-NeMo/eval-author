# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG TARGETARCH
COPY --from=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends git curl jq make ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*
RUN case "$TARGETARCH" in \
      amd64) a=x64;   s=8cb6d6a0035a3c5d9c40a58852ca361569eafc2cecbbeef4521e0fefc8ccbc37 ;; \
      arm64) a=arm64; s=170852b7815ef74e1212f87e69fa7f2730f31716479177df436d3eaa832d8ca5 ;; \
      *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/omp "https://github.com/can1357/oh-my-pi/releases/download/v18.8.3/omp-linux-$a" \
 && echo "$s  /usr/local/bin/omp" | sha256sum -c - && chmod 0755 /usr/local/bin/omp
ENV PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy PI_NO_TITLE=1
WORKDIR /work/repo
