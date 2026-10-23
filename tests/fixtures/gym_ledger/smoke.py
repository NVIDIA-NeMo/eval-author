#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run real NeMo Gym servers with a scripted policy control, never a model-performance claim."""

import argparse
import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import socket
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import aiohttp
import uvicorn
from fastapi import FastAPI

OmegaConf = importlib.import_module("omegaconf").OmegaConf
server_utils = importlib.import_module("nemo_gym.server_utils")
agent_module = importlib.import_module("responses_api_agents.simple_agent.app")


def response(output):
    return {
        "id": str(uuid4()),
        "created_at": 0,
        "model": "scripted-control",
        "object": "response",
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


def answer(text):
    return {
        "id": str(uuid4()),
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


async def serve(app, stack):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))

    async def close():
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()

    stack.push_async_callback(close)
    for _ in range(200):
        if server.started:
            return sock.getsockname()[1]
        if task.done():
            await task
            raise RuntimeError("server exited before startup")
        await asyncio.sleep(0.01)
    raise TimeoutError("server startup timed out")


async def run(output, resources_app, dataset):
    spec = importlib.util.spec_from_file_location("ledger_task", resources_app)
    if spec is None or spec.loader is None:
        raise ValueError("resources app could not be loaded")
    task_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = task_module
    spec.loader.exec_module(task_module)
    verifier_results = await importlib.import_module("nemo_gym.verifier_fixture").exercise_verifier_fixture(
        task_module.VERIFIER_FIXTURE, reward_range=(0, 1), determinism="unknown"
    )
    inputs = [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    if not inputs:
        raise ValueError("dataset is empty")
    mode = "reference"
    model = FastAPI()

    @model.post("/ng-rollout/{rollout_id}/v1/responses")
    @model.post("/v1/responses")
    async def respond(body: dict):
        # Deliberately scripted control policy: its rewards are not agent capability metrics.
        items = body["input"]
        if mode == "nop":
            return response([answer("")])
        tool_results = [item for item in items if item.get("type") == "function_call_output"]
        if tool_results:
            total = json.loads(tool_results[-1]["output"])["total"]
            return response([answer(str(total if mode == "reference" else total + 1))])
        instruction = next(item["content"] for item in items if item.get("role") == "user")
        amounts = json.loads(instruction.split("Amounts: ", 1)[1])
        return response(
            [
                {
                    "type": "function_call",
                    "id": str(uuid4()),
                    "call_id": str(uuid4()),
                    "name": "ledger_total",
                    "arguments": json.dumps({"amounts": amounts}),
                }
            ]
        )

    async with AsyncExitStack() as stack:
        pool = server_utils.set_global_aiohttp_client(server_utils.GlobalAIOHTTPAsyncClientConfig())
        stack.push_async_callback(pool.close)
        model_port = await serve(model, stack)
        config = OmegaConf.create(
            {
                "observability_enabled": True,
                "policy_model": {
                    "responses_api_models": {"scripted_control": {"host": "127.0.0.1", "port": model_port}}
                },
                "ledger": {"resources_servers": {"ledger": {"host": "127.0.0.1", "port": 1}}},
            }
        )
        client = server_utils.ServerClient(
            head_server_config=server_utils.BaseServerConfig(host="127.0.0.1", port=1), global_config_dict=config
        )
        resource = task_module.LedgerResourcesServer(
            config=task_module.LedgerConfig(name="ledger", entrypoint="app.py", host="127.0.0.1", port=1),
            server_client=client,
        )
        resource_port = await serve(resource.setup_webserver(), stack)
        config.ledger.resources_servers.ledger.port = resource_port
        agent = agent_module.SimpleAgent(
            config=agent_module.SimpleAgentConfig(
                name="ledger_agent",
                entrypoint="app.py",
                host="127.0.0.1",
                port=1,
                resources_server={"type": "resources_servers", "name": "ledger"},
                model_server={"type": "responses_api_models", "name": "policy_model"},
            ),
            server_client=client,
        )
        agent_port = await serve(agent.setup_webserver(), stack)
        config.ledger_agent = {"responses_api_agents": {"simple_agent": {"host": "127.0.0.1", "port": agent_port}}}
        output.mkdir(parents=True, exist_ok=False)
        verifier_report = importlib.import_module("nemo_gym.environment.onboarding").VerifierReport(
            name="cover_ledger_total",
            kind="environment",
            resources_server="ledger",
            manifest_path=str(resources_app.parents[2] / "environments/cover_ledger_total/manifest.yaml"),
            fixture_path=str(task_module.VERIFIER_FIXTURE.cases_path),
            cases=verifier_results,
        )
        (output / "verifier-report.json").write_text(json.dumps(verifier_report.to_dict()))
        (output / "executed-dataset.jsonl").write_bytes(dataset.read_bytes())
        (output / "executed-config.yaml").write_text(OmegaConf.to_yaml(config))
        (output / "executed-runtime.json").write_text(
            json.dumps(
                {
                    "gym_version": importlib.metadata.version("nemo-gym"),
                    "python": sys.version,
                }
            )
        )
        (output / "executed-reset.json").write_text(json.dumps({"scenario": "stateless ledger"}))
        # Exercise the documented native CLI against the same running NeMo Gym services.
        head = server_utils.HeadServer(config=server_utils.BaseServerConfig(host="127.0.0.1", port=1))
        head._cached_yaml = OmegaConf.to_yaml(config)
        head_port = await serve(head.setup_webserver(), stack)
        command = [
            str(Path(sys.executable).with_name("gym")),
            "eval",
            "run",
            "--no-serve",
            "--agent",
            "ledger_agent",
            "--input",
            str(dataset),
            "--output",
            str(output / "cli-rollouts.jsonl"),
            "--num-repeats",
            "2",
            "--concurrency",
            "1",
            "--health-check-workers",
            "1",
            "+head_server.host=127.0.0.1",
            f"+head_server.port={head_port}",
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=output,
        )
        try:
            log, _ = await asyncio.wait_for(process.communicate(), 45)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        (output / "cli.log").write_bytes(log)
        if process.returncode:
            raise RuntimeError(log.decode())
        cli_rows = [json.loads(line) for line in (output / "cli-rollouts.jsonl").read_text().splitlines()]
        assert len(cli_rows) == len(inputs) * 2
        assert all(row["reward"] == 1 for row in cli_rows)
        rows = []
        async with aiohttp.ClientSession() as session:
            for mode in ("reference", "reference", "nop", "wrong_answer"):
                for task_input in inputs:
                    body = {
                        **task_input,
                        "control": mode,
                        "_ng_rollout_id": str(uuid4()),
                        "task_id": "ledger-" + str(task_input["amounts"]),
                    }
                    async with session.post(f"http://127.0.0.1:{agent_port}/run", json=body) as result:
                        if result.status >= 400:
                            raise RuntimeError(await result.text())
                        result.raise_for_status()
                        row = await result.json()
                    assert row["reward"] == (1.0 if mode == "reference" else 0.0), row
                    rows.append(row)
        (output / "rollouts.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "controls": len(rows),
                    "native_cli_rollouts": len(cli_rows),
                    "verifier_cases": len(verifier_results),
                    "reference_passes": sum(row["reward"] == 1 for row in rows),
                    "negative_zero_rewards": sum(row["reward"] == 0 for row in rows),
                    "gym_version": importlib.metadata.version("nemo-gym"),
                    "model_performance_measured": False,
                    "transport": "HTTP",
                    "agent": "NeMo Gym SimpleAgent",
                    "policy": "scripted reference/no-action/wrong-answer",
                },
                indent=2,
            )
        )
        # NeMo Gym owns a process-wide aiohttp pool; close it on interpreter exit.
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resources-app", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    print(asyncio.run(run(args.output, args.resources_app, args.dataset)))
