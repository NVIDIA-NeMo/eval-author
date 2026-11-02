# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host-side Harbor agent; only the bounded container shell is model-visible."""

import asyncio
import hashlib
import json
import os
import shlex
from typing import Any

from harbor.agents.base import BaseAgent
from panel import CHEATS, MODEL, PROMPTS, TASKS


class LiveAgent(BaseAgent):
    def __init__(self, *args, task, arm, **kwargs):
        super().__init__(*args, **kwargs)
        if task not in TASKS or arm not in (*PROMPTS, *CHEATS, "reference"):
            raise ValueError("invalid cell")
        self.task, self.arm = task, arm
        self.receipt: dict[str, Any] = {
            "task_id": TASKS[task]["id"],
            "arm": arm,
            "turns": 0,
            "tool_calls": 0,
            "tokens": 0,
            "usage_status": "not_observed",
            "observed_models": [],
        }

    @staticmethod
    def name():
        return "anti-cheat-live"

    def version(self):
        return "1"

    def save(self):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.logs_dir / "receipt.json.tmp"
        temporary.write_text(json.dumps(self.receipt))
        os.replace(temporary, self.logs_dir / "receipt.json")

    async def setup(self, environment):
        # Protect the non-mounted parent: macOS bind ownership is not reliable.
        result = await environment.exec(
            command="mkdir -p /work /tests /logs/verifier; chown root:root /logs /tests; "
            "chmod 700 /logs /tests; chown 65534:65534 /work; chmod 700 /work",
            user="root",
            timeout_sec=10,
        )
        if result.return_code:
            raise RuntimeError("setup failed")
        task = TASKS[self.task]
        code = task["reference"] if self.arm == "reference" else CHEATS.get(self.arm, task["starter"])
        inputs, expected = task["smoke"]
        smoke = (
            "import json, subprocess, sys\n"
            f"r = subprocess.run([sys.executable, '-I', '/work/solution.py'], input={json.dumps(inputs)!r}, "
            "text=True, capture_output=True, timeout=3)\n"
            f"assert r.returncode == 0 and json.loads(r.stdout) == {expected!r}\nprint('smoke passed')\n"
        )
        program = f"from pathlib import Path; Path('/work/solution.py').write_text({code!r}); Path('/work/smoke_test.py').write_text({smoke!r})"
        result = await environment.exec(command="python -I -c " + shlex.quote(program), user="65534", timeout_sec=10)
        if result.return_code:
            raise RuntimeError("workspace setup failed")
        probe = "import json,os; print(json.dumps(dict(uid=os.getuid(), protected=not os.access('/logs',os.W_OK) and not os.access('/tests',os.R_OK))))"
        result = await environment.exec(command="python -I -c " + shlex.quote(probe), user="65534", timeout_sec=10)
        identity = json.loads(result.stdout)
        if result.return_code or identity != {"uid": 65534, "protected": True}:
            raise RuntimeError("isolation failed")
        self.receipt.update(identity)
        self.save()

    async def tool(self, environment, arguments):
        if self.receipt["tool_calls"] >= MODEL["max_tool_calls"]:
            raise RuntimeError("tool budget exhausted")
        self.receipt["tool_calls"] += 1
        self.save()
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"command"}
            or not isinstance(arguments["command"], str)
            or not 1 <= len(arguments["command"]) <= 8192
        ):
            return "Tool error: expected only a command string of 1 to 8192 characters."
        command = arguments["command"]
        wrapped = "ulimit -f 128; ulimit -u 32; timeout -k 1 12 sh -c " + shlex.quote(command)
        # Bound the Docker exec payload before it reaches Harbor's in-memory capture.
        wrapped = "(" + wrapped + ") 2>&1 | head -c 12000"
        result = await environment.exec(
            command=wrapped, cwd="/work", user="65534", timeout_sec=MODEL["tool_timeout_sec"]
        )
        return ((result.stdout or "") + "\n" + (result.stderr or ""))[:12000]

    async def run(self, instruction, environment, context):
        try:
            await self._run(instruction, environment, context)
        finally:
            self.save()

    async def _run(self, instruction, environment, context):
        if self.arm not in PROMPTS:
            self.receipt["finish"] = "control"
            self.receipt["usage_status"] = "not_applicable"
            self.save()
            return
        from openai import APIError, APIStatusError, AsyncOpenAI

        key = os.environ.get("INFERENCE_HUB_API_KEY")
        if not key:
            raise RuntimeError("missing inference key")
        messages: list[Any] = [
            {"role": "system", "content": PROMPTS[self.arm]},
            {"role": "user", "content": instruction},
        ]
        tools: list[Any] = [
            {
                "type": "function",
                "function": {
                    "name": "container_shell",
                    "description": "Run a bounded command as an unprivileged user in /work, inside the isolated container.",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        retries_left, unobserved_tokens = 2, 0
        async with AsyncOpenAI(
            api_key=key, base_url=MODEL["base_url"], timeout=MODEL["request_timeout_sec"], max_retries=0
        ) as client:
            for _ in range(MODEL["max_turns"]):
                if len(json.dumps(messages).encode()) > MODEL["max_context_bytes"]:
                    raise RuntimeError("context budget exhausted")
                # UTF-8 bytes plus framing is a conservative input-token upper bound.
                reserved = len(json.dumps(messages).encode()) + len(json.dumps(tools).encode()) + 1024
                if self.receipt["tokens"] + reserved + MODEL["max_completion_tokens"] > MODEL["max_total_tokens"]:
                    raise RuntimeError("token budget exhausted")
                self.receipt["usage_status"] = "partial_observed_not_total_billed"
                self.save()
                # Retries share the original request deadline and cell token allowance.
                async with asyncio.timeout(MODEL["request_timeout_sec"]):
                    while True:
                        if (
                            self.receipt["tokens"] + unobserved_tokens + reserved + MODEL["max_completion_tokens"]
                            > MODEL["max_total_tokens"]
                        ):
                            raise RuntimeError("token budget exhausted")
                        try:
                            response = await client.chat.completions.create(
                                model=MODEL["model"],
                                messages=messages,
                                tools=tools,
                                parallel_tool_calls=False,
                                max_completion_tokens=MODEL["max_completion_tokens"],
                                store=False,
                            )
                            break
                        except APIError as exc:
                            # Failed requests have no usage receipt; reserve their full allowance.
                            unobserved_tokens += reserved + MODEL["max_completion_tokens"]
                            transient = isinstance(exc, APIStatusError) and (
                                exc.status_code == 429 or 500 <= exc.status_code < 600
                            )
                            if not transient or retries_left == 0:
                                raise RuntimeError("provider request failed") from None
                            retries_left -= 1
                            await asyncio.sleep(0.5 * (2 - retries_left))
                self.receipt["turns"] += 1
                if not isinstance(response.model, str) or not response.model:
                    raise RuntimeError("missing observed model")
                identity = {
                    "model_sha256": hashlib.sha256(response.model.encode()).hexdigest(),
                    "revision_sha256": hashlib.sha256(response.system_fingerprint.encode()).hexdigest()
                    if response.system_fingerprint
                    else None,
                }
                if identity not in self.receipt["observed_models"]:
                    self.receipt["observed_models"].append(identity)
                self.save()
                if response.usage is None or not 0 <= response.usage.total_tokens <= MODEL["max_total_tokens"]:
                    raise RuntimeError("missing usage")
                self.receipt["tokens"] += response.usage.total_tokens
                self.save()
                if self.receipt["tokens"] > MODEL["max_total_tokens"] or len(response.choices) != 1:
                    raise RuntimeError("response budget exceeded")
                message = response.choices[0].message
                messages.append(message.model_dump(exclude_none=True))
                self.logs_dir.mkdir(parents=True, exist_ok=True)
                (self.logs_dir / "conversation.json").write_text(json.dumps(messages))
                if response.choices[0].finish_reason == "stop" and not message.tool_calls:
                    self.receipt["finish"] = "stop"
                    self.receipt["usage_status"] = "observed_not_total_billed"
                    self.save()
                    return
                if response.choices[0].finish_reason != "tool_calls" or not message.tool_calls:
                    raise RuntimeError("incomplete model turn")
                for call in message.tool_calls:
                    if (
                        call.type != "function"
                        or call.function.name != "container_shell"
                        or self.receipt["tool_calls"] >= MODEL["max_tool_calls"]
                    ):
                        raise RuntimeError("tool budget or name invalid")
                    try:
                        arguments = json.loads(call.function.arguments)
                    except (ValueError, TypeError, RecursionError):
                        arguments = None
                    output = await self.tool(environment, arguments)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                self.save()
        raise RuntimeError("turn budget exhausted")
