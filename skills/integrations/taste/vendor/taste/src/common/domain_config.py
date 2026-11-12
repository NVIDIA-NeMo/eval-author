"""Prepared, data-only inputs shared by TASTE stages."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from src.backends.contracts import IncompatibleBackend, Seed

INPUT_SCHEMA = "taste.prepared-inputs.v1"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "prompts"


class DomainConfig:
    llm_factory: Callable[..., Any] | None = None

    def __init__(
        self,
        domain: str,
        *,
        resources_dir: str,
        options: dict | None = None,
        task_service: str | None = None,
        input_digests: dict | None = None,
        schema: str = INPUT_SCHEMA,
    ):
        if schema != INPUT_SCHEMA:
            raise IncompatibleBackend(f"Expected prepared input schema {INPUT_SCHEMA!r}; prepare a fresh input bundle")
        if not isinstance(domain, str) or not domain:
            raise ValueError("Prepared input domain must be a nonempty label")
        self.domain = domain
        self.domain_dir = str(Path(resources_dir).resolve())
        if not Path(self.domain_dir).is_dir():
            raise ValueError(f"Prepared resource directory not found: {self.domain_dir}")
        self.options = copy.deepcopy(options or {})
        for key in ("seed_path", "prompts_dir", "evidence_dir"):
            if self.options.get(key):
                path = Path(self.options[key])
                if not path.is_absolute():
                    path = Path(self.domain_dir) / path
                self.options[key] = str(path.resolve())
        self.task_service = task_service
        self._service = None
        self.prompts_dir = str(PROMPTS_DIR)
        self.tool_spec_path = str(Path(self.domain_dir) / "tool_spec.json")
        self.constants = self._read_resource("domain.json", default={})
        if input_digests is not None and input_digests != self.input_digests():
            raise IncompatibleBackend("Prepared resources, seeds, or prompts changed; prepare a fresh run")

    def _read_resource(self, name: str, *, default=None):
        path = Path(self.domain_dir) / name
        if not path.is_file() and default is not None:
            return copy.deepcopy(default)
        return json.loads(path.read_text())

    @property
    def task_schema(self) -> dict:
        return self._read_resource("task_schema.json")

    @property
    def context(self) -> dict:
        return self._read_resource("context.json", default={})

    def get_db_schema_str(self) -> str:
        return json.dumps(self._read_resource("fixture_schema.json"), indent=2)

    @property
    def action_group_map(self) -> dict[str, str] | None:
        return self.constants.get("action_group_map")

    def input_digests(self) -> dict[str, str]:
        root = Path(self.domain_dir)
        names = (
            "policy.md",
            "tool_spec.json",
            "domain.json",
            "task_schema.json",
            "fixture_schema.json",
            "context.json",
        )
        paths = {name: root / name for name in names}
        paths["seeds"] = Path(self.options.get("seed_path", root / "seeds.jsonl"))
        for label, directory in (("prompts", self.prompts_dir), ("prompt_overrides", self.options.get("prompts_dir"))):
            if directory:
                prompt_root = Path(directory)
                for path in sorted(prompt_root.rglob("*.txt")):
                    paths[f"{label}/{path.relative_to(prompt_root)}"] = path
        return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items() if path.is_file()}

    def to_spec(self) -> dict:
        return {
            "schema": INPUT_SCHEMA,
            "domain": self.domain,
            "resources_dir": self.domain_dir,
            "options": copy.deepcopy(self.options),
            "task_service": self.task_service,
            "input_digests": self.input_digests(),
        }

    @classmethod
    def from_spec(cls, spec: dict):
        if not isinstance(spec, dict) or spec.get("schema") != INPUT_SCHEMA or "backend" in spec:
            raise IncompatibleBackend(
                f"Expected {INPUT_SCHEMA!r}; native backend configurations are unsupported. "
                "Prepare normalized seeds and resources in a fresh input bundle."
            )
        return cls(**spec)

    @classmethod
    def from_file(cls, path):
        source = Path(path).resolve()
        spec = json.loads(source.read_text())
        if isinstance(spec, dict) and isinstance(spec.get("resources_dir"), str):
            resources = Path(spec["resources_dir"])
            if not resources.is_absolute():
                spec["resources_dir"] = str(source.parent / resources)
        return cls.from_spec(spec)

    def load_policy(self) -> str:
        return (Path(self.domain_dir) / "policy.md").read_text()

    def load_tasks(self):
        path = Path(self.options.get("seed_path", Path(self.domain_dir) / "seeds.jsonl"))
        with path.open() as source:
            if path.suffix == ".jsonl":
                return [json.loads(line) for line in source if line.strip()]
            return json.load(source)

    def seeds(self, records=None, *, split: str | None = None) -> list[Seed]:
        records = self.load_tasks() if records is None else records
        if not isinstance(records, list):
            raise ValueError("Prepared seeds must be an array of normalized records or JSONL rows")
        seeds = []
        for index, row in enumerate(records):
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not isinstance(row.get("actions"), list)
            ):
                raise ValueError(f"Seed {index} must contain string id and normalized actions array")
            source_split = row.get("split", split or self.options.get("seed_split", "unspecified"))
            example = row.get("native", {})
            if not isinstance(source_split, str) or not isinstance(example, dict):
                raise ValueError(f"Seed {index} requires a string split and an optional object example")
            for action in row["actions"]:
                if (
                    not isinstance(action, dict)
                    or not isinstance(action.get("name"), str)
                    or not isinstance(action.get("arguments"), dict)
                ):
                    raise ValueError(f"Seed {index} actions require a name and arguments object")
            seeds.append(Seed(row["id"], source_split, copy.deepcopy(example), copy.deepcopy(row["actions"])))
        return seeds

    def service(self):
        if self._service is None:
            from src.backends.registry import load_task_service

            self._service = load_task_service(self.task_service, config=self)
        return self._service

    def make_llm(self, **kwargs):
        factory = self.llm_factory
        if factory is None:
            from src.common.call_to_llm import LLMCaller

            factory = LLMCaller
        return factory(**kwargs)

    def prompt_manager(self):
        from src.common.prompt_manager import PromptManager

        manager = PromptManager(prompts_dir=self.prompts_dir, domain=self.domain)
        overrides = self.options.get("prompts_dir")
        if overrides:
            manager.prompts.update(PromptManager(prompts_dir=overrides, domain=self.domain).prompts)
        if self.context:
            context = json.dumps(self.context, ensure_ascii=False)
            appendix = "\nPrepared task context (also applies during repair):\n" + context.replace("{", "{{").replace(
                "}", "}}"
            )
            for name in manager.prompts:
                manager.prompts[name] += appendix
        return manager
