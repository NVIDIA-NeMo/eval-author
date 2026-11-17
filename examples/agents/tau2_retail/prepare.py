# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
TASK_NAME = "cancel-order"


def main() -> None:
    task = ROOT / ".eval-author/tasks" / TASK_NAME
    task.mkdir(parents=True, exist_ok=False)
    environment = task / "environment"
    environment.mkdir()
    tests = task / "tests"
    tests.mkdir()
    for name in ("acp_server.py", "session.py", "requirements.txt", "runtime-constraints.txt"):
        shutil.copyfile(ROOT / name, environment / name)
    shutil.copytree(ROOT / "upstream", environment / "upstream")
    shutil.copytree(ROOT / "data", environment / "data")
    shutil.copyfile(ROOT / "verify.py", tests / "verify.py")
    shutil.copyfile(ROOT / "data/tau2/domains/retail/db.json", tests / "seed.json")
    (tests / "test.sh").write_text("#!/bin/sh\nset -eu\npython /tests/verify.py\n")
    (tests / "Dockerfile").write_text("FROM python:3.12-slim\nCOPY . /tests/\n")
    (task / "instruction.md").write_text(
        "Help the retail customer through the connected conversation. Follow the unchanged retail policy.\n"
    )
    (task / "task.toml").write_text("""schema_version = "1.4"
artifacts = ["/app/state/db.json", "/app/output/transcript.json"]
[task]
name = "local/cancel-order"
version = "1.0.0"
[agent]
timeout_sec = 900.0
[environment]
network_mode = "public"
build_timeout_sec = 1200.0
[verifier]
environment_mode = "separate"
timeout_sec = 120.0
""")
    (environment / "Dockerfile").write_text("""FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt runtime-constraints.txt /tmp/
RUN pip install --no-cache-dir -c /tmp/runtime-constraints.txt -r /tmp/requirements.txt
COPY upstream /opt/retail-agent/upstream
COPY acp_server.py session.py /opt/retail-agent/
COPY data /opt/retail-data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TAU2_DATA_DIR=/opt/retail-data PYTHONPATH=/opt/retail-agent/upstream:/opt/retail-agent
RUN mkdir -p /app/state /app/output && cp /opt/retail-data/tau2/domains/retail/db.json /app/state/db.json
""")
    scenario = {
        "schema_version": 1,
        "task_id": "local/" + TASK_NAME,
        "user_type": "neutral",
        "goal": "Cancel order #W5918442 because it was ordered by mistake.",
        "start_prompt": "I'd like to cancel an order that I placed by mistake.",
        "user_facts": {"email": "sofia.rossi2645@example.com", "order_id": "#W5918442", "reason": "ordered by mistake"},
        "disclosure_rules": [
            "Give your email when asked to authenticate. Give the order identifier when asked which order to cancel."
        ],
        "conversation_prompt": "Answer the actual questions using your facts. When the agent proposes cancelling your order for the stated reason and asks for confirmation, reply with exactly yes. Do not confirm a different order or action.",
        "stopping_conditions": [
            "End when the agent reports the requested cancellation, or gives a final refusal or an unresolved blocker."
        ],
    }
    (task / "user-sim.yaml").write_text(yaml.safe_dump(scenario, sort_keys=False))
    config: dict[str, Any] = {
        "job_name": "retail-user-sim",
        # Harbor 0.24 compose cp resolves relative artifact paths from its project directory.
        "jobs_dir": str(ROOT / ".eval-author/jobs"),
        "n_attempts": 1,
        "orchestrator": {"n_concurrent_trials": 1},
        "agents": [
            {
                "import_path": "retail_harbor:RetailAgentHarbor",
                "model_name": "openai/azure/openai/gpt-4.1-mini",
                "override_timeout_sec": 900,
            }
        ],
        "user_agent": {
            "import_path": "retail_harbor:NeutralUserHarbor",
            "model_name": "openai/azure/openai/gpt-4.1-mini",
            "bridge": {"kind": "acp"},
        },
        "tasks": [{"path": ".eval-author/tasks/cancel-order"}],
    }
    roles: list[dict[str, Any]] = [*config["agents"], config["user_agent"]]
    for agent in roles:
        agent["env"] = {"OPENAI_API_KEY": "${OPENAI_API_KEY}", "OPENAI_BASE_URL": "${OPENAI_BASE_URL}"}
    path = ROOT / ".eval-author/base-run.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    print(json.dumps({"task": str(task), "base_config": str(path)}))


if __name__ == "__main__":
    main()
