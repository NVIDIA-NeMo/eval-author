# syntax=docker/dockerfile:1
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Build through generator/build.py: it stages the context (the four skill trees and
# harbor_shim.py) and passes their glob digest as GENERATOR_SOURCE_SHA256.
ARG BASE_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.9

FROM ${UV_IMAGE} AS uv

FROM ${BASE_IMAGE}
ARG GENERATOR_SOURCE_SHA256
RUN test -n "${GENERATOR_SOURCE_SHA256}" || { echo "GENERATOR_SOURCE_SHA256 build arg is required" >&2; exit 1; }
LABEL org.nvidia.eval-author.generator-source-sha256="${GENERATOR_SOURCE_SHA256}"

USER root
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

# The helper scripts need Python 3.11 or later; install one when the base lacks it.
RUN if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then \
        uv python install 3.12 && ln -sf "$(uv python find 3.12)" /usr/local/bin/python3; \
    fi \
    && python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version'

# Harbor 0.24.0 stays off PATH. The shim's shebang and task_evidence.py use its Python.
RUN uv venv --python ">=3.12" /opt/harbor-real \
    && uv pip install --python /opt/harbor-real/bin/python "harbor==0.24.0" \
    && test "$(/opt/harbor-real/bin/harbor --version)" = "0.24.0"

COPY skills/ /opt/skills/

# Warm the uv cache for every inline-metadata script and --with/--with-requirements
# invocation the skills run, then go offline: a missing package is an image-build bug.
WORKDIR /tmp/uv-warm
RUN set -eux; \
    audit=/opt/skills/eval-author-audit; \
    uv run --with-requirements "$audit/requirements.txt" "$audit/scripts/audit_spec/measure.py" --help >/dev/null; \
    uv run --with-requirements "$audit/requirements.txt" "$audit/scripts/audit_spec/report.py" --help >/dev/null; \
    uv run --with pyyaml --with jsonschema "$audit/scripts/audit_spec/generate.py" --help >/dev/null; \
    uv run --with pyyaml --with jsonschema "$audit/scripts/audit_spec/validate.py" --help >/dev/null; \
    uv run /opt/skills/eval-author-task-create/scripts/task_pipeline.py --help >/dev/null; \
    python3 /opt/skills/eval-author/scripts/journey_state.py --help >/dev/null; \
    python3 /opt/skills/eval-author/scripts/locate_runtime.py --help >/dev/null; \
    /opt/harbor-real/bin/python /opt/skills/eval-author-task-create/scripts/task_evidence.py --help >/dev/null; \
    chmod -R a+rwX "$UV_CACHE_DIR"
ENV UV_OFFLINE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --chmod=0755 harbor_shim.py /usr/local/bin/harbor
ENV PATH=/usr/local/bin:${PATH}
WORKDIR /
