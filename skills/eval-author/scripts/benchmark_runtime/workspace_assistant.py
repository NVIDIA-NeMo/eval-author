# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import copy
import hashlib
import importlib
import inspect
import json
import sys
from pathlib import Path

from benchmark_runtime.authoring import ToolResult, UnsupportedCapability, review_candidate, write_trace


def source_fingerprint(root):
    root = Path(root).resolve()
    paths = [root / name for name in ("bootstrap.py", "environment.py", "fixture_state.py", "tasks.py", "scoring.py")]
    paths.extend(path for path in (root / "vendor/workplace_assistant").rglob("*") if path.suffix in {".py", ".csv"})
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
        if path.is_file()
    }


def native(root):
    root = Path(root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    modules = {}
    for name in ("environment", "fixture_state", "tasks", "scoring"):
        module = importlib.import_module(name)
        if Path(module.__file__ or "").resolve().parent != root:
            raise RuntimeError(f"{name} resolved outside selected Workspace Assistant runtime {root}")
        modules[name] = module
    return modules


TASK_SCHEMA = {
    "type": "object",
    "required": ["request"],
    "additionalProperties": False,
    "properties": {"request": {"type": "string", "minLength": 1}},
}


class Runtime:
    def __init__(self, root):
        self.modules = native(root)

    def fixture(self, entities):
        # A complete fixture is explicit; absence selects the native baseline.
        # Generated append patches never replace existing rows or deduplicate IDs.
        if not isinstance(entities, dict) or set(entities) - {"tables"}:
            raise ValueError("Workspace Assistant generated fixture must contain only append-only tables")
        tables = entities.get("tables", {})
        if not isinstance(tables, dict):
            raise ValueError(
                "Workspace Assistant fixture tables must be an object mapping table names to arrays of rows"
            )
        if any(
            not isinstance(rows, list) or any(not isinstance(row, list) for row in rows) for rows in tables.values()
        ):
            raise ValueError("Workspace Assistant fixture table additions must be arrays of row arrays")
        runtime = self.modules
        baseline = runtime["fixture_state"].export_initial_state(runtime["environment"].make_environment())
        for name, rows in tables.items():
            table = baseline["tables"][name]
            start = max(table["index"], default=-1) + 1
            table["data"].extend(copy.deepcopy(rows))
            table["index"].extend(range(start, start + len(rows)))
        runtime["environment"].make_environment(initial_state=baseline)
        return baseline

    def fixture_schema(self):
        runtime = self.modules
        env = runtime["environment"].make_environment()
        state = runtime["fixture_state"].export_initial_state(env)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"tables": {"type": "object", "additionalProperties": False, "properties": {}}},
        }
        tables = schema["properties"]["tables"]["properties"]
        for key, table in state["tables"].items():
            container, field = key.split(".")
            frame = getattr(env["containers"][container], field)
            cells = [
                {"type": "boolean"} if frame[col].dtype == bool else {"type": ["string", "null"]}
                for col in table["columns"]
            ]
            tables[key] = {
                "type": "array",
                "description": f"Append rows in column order: {table['columns']}",
                "items": {
                    "type": "array",
                    "prefixItems": cells,
                    "items": False,
                    "minItems": len(cells),
                    "maxItems": len(cells),
                },
            }
        return json.dumps(schema)

    def context(self):
        runtime = self.modules
        env = runtime["environment"].make_environment()
        schemas = env["schemas"]
        writes = {
            "email_send_email",
            "email_delete_email",
            "email_forward_email",
            "email_reply_email",
            "calendar_create_event",
            "calendar_delete_event",
            "calendar_update_event",
            "analytics_create_plot",
            "project_management_create_task",
            "project_management_delete_task",
            "project_management_update_task",
            "customer_relationship_manager_update_customer",
            "customer_relationship_manager_add_customer",
            "customer_relationship_manager_delete_customer",
        }
        tool_spec = {
            s["name"]: {
                "type": "WRITE" if s["name"] in writes else "READ",
                "description": s.get("description", ""),
                "parameters": s["parameters"]["properties"],
            }
            for s in schemas
        }
        return {
            "schemas": env["schemas"],
            "tool_spec": tool_spec,
            "fixture_schema": json.loads(self.fixture_schema()),
            "implementations": {name: inspect.getsource(fn) for name, fn in env["functions"].items()},
            "capabilities": {
                "interaction": "fixed_request",
                "grading": ["state"],
            },
            "limitations": ["Native scoring does not grade final answers or prove rendered file existence."],
        }

    def build_task(self, candidate, fixture, *, template, policy):
        actions = candidate["reference_actions"]
        if any(action.get("requestor", "assistant") != "assistant" for action in actions):
            raise UnsupportedCapability("Workspace Assistant has no user tools or user simulator")
        request = candidate["task"]["request"]
        if not isinstance(request, str) or not request.strip():
            raise ValueError("Workspace Assistant requires a nonempty initial request")
        row = copy.deepcopy(template)
        row.update(
            id="generated_unassigned",
            category="taste_generated",
            initial_state=self.fixture(fixture),
            ground_truth=[
                {"name": action["name"], "arguments": json.dumps(action["arguments"], ensure_ascii=False)}
                for action in actions
            ],
        )
        row["responses_create_params"]["input"] = [
            {"role": "system", "content": policy},
            {"role": "user", "content": request},
        ]
        self.modules["tasks"].model_input(self.modules["tasks"].Task("generated", row))
        return row

    def open(self, task):
        return Session(self.modules, task)

    def grade(self, task, actions, *, final_answer=""):
        return self.modules["scoring"].score(
            [{"name": action["name"], "arguments": json.dumps(action["arguments"])} for action in actions],
            task["ground_truth"],
            initial_state=task.get("initial_state"),
        )


class Session:
    def __init__(self, modules, task):
        self.modules = modules
        self.initial = copy.deepcopy(task.get("initial_state"))
        self.reset()

    def reset(self):
        self.env = self.modules["environment"].make_environment(initial_state=copy.deepcopy(self.initial))

    def execute(self, action):
        if action.get("requestor", "assistant") != "assistant":
            raise UnsupportedCapability("Workspace Assistant supports assistant tool calls only")
        output, error = self.modules["environment"].call(self.env, action["name"], json.dumps(action["arguments"]))
        return ToolResult(output, error)

    def snapshot(self):
        # Native fixture JSON truncates float precision; runtime snapshots retain exact frames.
        return {
            name: {
                field: value.copy(deep=True) for field, value in vars(container).items() if hasattr(value, "to_json")
            }
            for name, container in self.env["containers"].items()
        }

    def restore(self, state):
        replacement = self.modules["environment"].make_environment()
        for name, fields in state.items():
            for field, frame in fields.items():
                setattr(replacement["containers"][name], field, frame.copy(deep=True))
        self.env = replacement


class Service:
    def __init__(self, config):
        self.config = config
        root = config.options["native_root"]
        expected = config.context.get("native_source_fingerprint")
        if expected is None or expected != source_fingerprint(root):
            raise RuntimeError("Native Workspace Assistant code or fixture data changed; prepare fresh inputs")
        self.runtime = Runtime(root)

    def build_task(self, candidate, fixture):
        return self.runtime.build_task(
            candidate,
            fixture,
            template=self.config.context["native_task_template"],
            policy=self.config.load_policy(),
        )

    def open(self, task):
        return self.runtime.open(task)

    def grade(self, task, actions, *, final_answer=""):
        return self.runtime.grade(task, actions, final_answer=final_answer)

    def _reference(self, task, actions):
        import jsonschema

        schemas = {schema["name"]: schema for schema in task["responses_create_params"]["tools"]}
        for action in actions:
            if action["name"] not in schemas:
                raise ValueError(f"Unknown tool name: {action['name']}")
            jsonschema.validate(action["arguments"], schemas[action["name"]]["parameters"])
        session = self.open(task)
        outputs = [session.execute(action) for action in actions]
        if any(output.error for output in outputs):
            raise ValueError(f"Reference execution failed: {[output.error for output in outputs if output.error]}")
        return [output.content for output in outputs]

    def validate(self, candidate, fixture, *, hints, settings):
        import jsonschema
        from src.backends.contracts import ValidationResult

        from benchmark_runtime.authoring import AuthoringError, complete, digest, run_fixed_request, validate_shapes

        unsupported = set(self.config.options.get("required_grading", ["state"])) - {"state"}
        if settings.get("write_only") or settings.get("adversarial_user"):
            unsupported.add("interactive/write-only hints")
        if unsupported:
            return ValidationResult(
                False,
                False,
                False,
                "Unsupported required capabilities",
                "incompatible",
                unsupported=tuple(sorted(unsupported)),
            )
        try:
            self.runtime.fixture(fixture)
        except (ValueError, KeyError, TypeError) as error:
            return ValidationResult(False, False, False, str(error), "fixture", repair_scope="fixture")
        try:
            validate_shapes(self.config, candidate, fixture)
            task = self.build_task(candidate, fixture)
            outputs = self._reference(task, candidate["reference_actions"])
        except UnsupportedCapability as error:
            return ValidationResult(False, False, False, str(error), "incompatible", unsupported=(str(error),))
        except (AuthoringError, ValueError, KeyError, TypeError, jsonschema.ValidationError) as error:
            return ValidationResult(False, True, False, str(error), "reference")
        try:
            candidate, fixture = review_candidate(
                self.config, candidate, fixture, settings=settings, reference_outputs=outputs
            )
        except UnsupportedCapability as error:
            return ValidationResult(False, False, False, str(error), "incompatible", unsupported=(str(error),))
        except AuthoringError as error:
            return ValidationResult(False, True, False, str(error), "authoring_review")
        try:
            task = self.build_task(candidate, fixture)
            self._reference(task, candidate["reference_actions"])
        except UnsupportedCapability as error:
            return ValidationResult(False, False, False, str(error), "incompatible", unsupported=(str(error),))
        except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as error:
            return ValidationResult(False, True, False, str(error), "reviewed_reference")
        messages, tools, _ = self.runtime.modules["tasks"].model_input(
            self.runtime.modules["tasks"].Task("generated", task)
        )
        messages = [
            {"role": "system", "content": hints},
            *(message for message in messages if message["role"] == "user"),
        ]
        trace = run_fixed_request(
            self.open(task),
            messages,
            tools,
            settings=settings,
            completion=getattr(self.config, "agent_completion", complete),
        )
        grading = self.grade(task, trace["actions"], final_answer=trace["final_answer"])
        success = trace["terminated"] and grading["reward"] == 1.0
        trace.update(
            model=settings["model"],
            task_sha256=digest(task),
            prepared_inputs=self.config.to_spec(),
            hint_settings=copy.deepcopy(settings),
            grading=grading,
            candidate=candidate,
            fixture=fixture,
        )
        write_trace(self.config, trace)
        return ValidationResult(
            success,
            True,
            success,
            None if success else "Hinted run failed or exhausted its budget",
            None if success else "hinted_execution",
            gt_simulation=trace,
            native_task=task,
            candidate=candidate,
            fixture=fixture,
        )

    def export(self, rows, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        native_rows = []
        for record in rows:
            native_row = copy.deepcopy(record["native_task"])
            if not isinstance(native_row, dict):
                raise ValueError("Export requires a validated native task")
            native_row["id"] = record["id"]
            self.runtime.modules["tasks"].model_input(self.runtime.modules["tasks"].Task("generated", native_row))
            native_rows.append(native_row)
        target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in native_rows))
        return target


def prepare(args):
    root = Path(args.native_root).resolve()
    runtime = Runtime(root)
    seed_path = (
        Path(args.seed_tasks).resolve()
        if getattr(args, "seed_tasks", None)
        else root / "data/upstream" / f"{args.split}.jsonl"
    )
    rows = [json.loads(line) for line in seed_path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("Selected Workspace Assistant seed split is empty")
    context = runtime.context()
    context["native_source_fingerprint"] = source_fingerprint(root)
    template = copy.deepcopy(rows[0])
    template["ground_truth"] = []
    template["responses_create_params"]["input"] = []
    template.pop("initial_state", None)
    context["native_task_template"] = template
    context["interaction_instructions"] = "Use one complete initial request; no user simulator or follow-up user turns."
    directory = Path(args.output).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "policy.md").write_text(
        "\n".join(
            message["content"] for message in rows[0]["responses_create_params"]["input"] if message["role"] == "system"
        )
    )
    for name, content in (
        ("tool_spec", context.pop("tool_spec")),
        ("task_schema", TASK_SCHEMA),
        ("fixture_schema", json.loads(runtime.fixture_schema())),
        ("context", context),
    ):
        (directory / f"{name}.json").write_text(json.dumps(content, indent=2) + "\n")
    seeds = []
    for row in rows:
        actions = []
        for index, action in enumerate(row["ground_truth"]):
            arguments = json.loads(action["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("Reference arguments must encode an object")
            actions.append(
                {"name": action["name"], "arguments": arguments, "requestor": "assistant", "action_id": str(index)}
            )
        example = {
            "task": {
                "request": "\n".join(
                    message["content"]
                    for message in row["responses_create_params"]["input"]
                    if message["role"] == "user"
                )
            },
            "reference_actions": copy.deepcopy(actions),
        }
        seeds.append({"id": str(row["id"]), "split": args.split, "native": example, "actions": actions})
    (directory / "seeds.jsonl").write_text("".join(json.dumps(seed, ensure_ascii=False) + "\n" for seed in seeds))
    spec = {
        "schema": "taste.prepared-inputs.v1",
        "domain": "workspace_assistant",
        "resources_dir": str(directory),
        "task_service": "benchmark_runtime.workspace_assistant:Service",
        "options": {
            "native_root": str(root),
            "seed_split": args.split,
            "required_grading": ["state"],
            "evidence_dir": str(directory / "evidence"),
        },
    }
    (directory / "prepared.json").write_text(json.dumps(spec, indent=2) + "\n")
    print(directory / "prepared.json")
    return directory / "prepared.json"


def main():
    parser = argparse.ArgumentParser(
        description="Prepare Workspace Assistant task inputs and native validation for generation"
    )
    parser.add_argument("--native-root", required=True)
    parser.add_argument("--seed-tasks")
    parser.add_argument("--split", choices=["train"], default="train")
    parser.add_argument("--output", required=True)
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
