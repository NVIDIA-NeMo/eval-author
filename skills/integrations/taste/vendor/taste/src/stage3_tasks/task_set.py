"""
Task set management: configuration, persistence, and loading.

Provides LengthDistributionConfig, TaskSetConfig, and TaskSetManager
for creating, saving, and reloading reusable task sets.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.common.sampler.length_distribution import LengthDistributionConfig

# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TaskSetConfig:
    """
    Full configuration for creating a task set.

    Fields:
        name: Human-readable label for this task set.
        domain: Configured domain name.
        seed_source: "original" | "empty" | file path to an existing tasks.json.
        num_tasks_to_generate: How many new tasks to create.
        length_distribution_config: How action sequence lengths are sampled.
        model_name: LLM model used for generation.
    """

    name: str
    domain: str
    seed_source: str  # "original" | "empty" | path
    num_tasks_to_generate: int
    length_distribution_config: LengthDistributionConfig
    model_name: str
    backend: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["length_distribution_config"] = self.length_distribution_config.to_dict()
        return d


# ---------------------------------------------------------------------------
# TaskSetManager
# ---------------------------------------------------------------------------

TASK_SETS_DIR = os.path.join(os.environ.get("TASTE_WORKSPACE", os.getcwd()), "task_sets")


def atomic_write_text(path: str | Path, content: str) -> None:
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_task_rows(directory: str, rows: List[Dict[str, Any]], *, domain_config):
    # Commit the resumable payload before rebuilding the derived native export.
    atomic_write_text(Path(directory) / "tasks.json", json.dumps(rows, indent=4) + "\n")
    return domain_config.service().export(rows, os.path.join(directory, "native-tasks.jsonl"))


class TaskSetManager:
    """
    Manages saving reusable task sets.

    Each task set is stored as a directory under ``task_sets/`` containing:
        - tasks.json   – list of task dicts (seed + generated)
        - config.json  – the TaskSetConfig used to create the set
        - metadata.json – creation timestamp, counts, length stats
    """

    def __init__(self, base_dir: str = TASK_SETS_DIR, *, domain_config):
        self.base_dir = base_dir
        self.domain_config = domain_config

    # -- public API ---------------------------------------------------------

    def save(
        self,
        name: str,
        all_tasks: List[Dict[str, Any]],
        config: TaskSetConfig,
        num_seed_tasks: int = 0,
        num_generated_tasks: int = 0,
    ) -> str:
        """
        Persist a task set to disk.

        Args:
            name: Directory name under task_sets/.
            all_tasks: List of task dicts (seed + generated).
            config: The configuration that produced this set.
            num_seed_tasks: Number of seed tasks included.
            num_generated_tasks: Number of newly generated tasks.

        Returns:
            Absolute path to the saved task set directory.
        """
        task_set_dir = os.path.join(self.base_dir, name)
        os.makedirs(task_set_dir, exist_ok=True)

        if config.backend != "prepared" or config.domain != self.domain_config.domain:
            raise ValueError("Task set identity does not match its backend configuration")
        native_path = save_task_rows(task_set_dir, all_tasks, domain_config=self.domain_config)

        # config.json
        config_path = os.path.join(task_set_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config.to_dict(), f, indent=4)

        # metadata.json
        lengths = self._extract_lengths(all_tasks)
        metadata = {
            "name": name,
            "created_at": datetime.now().isoformat(),
            "num_tasks": len(all_tasks),
            "native_export": str(native_path),
            "num_seed_tasks": num_seed_tasks,
            "num_generated_tasks": num_generated_tasks,
            "length_stats": self._length_stats(lengths) if lengths else {},
        }
        metadata_path = os.path.join(task_set_dir, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)

        return task_set_dir

    # -- private helpers ----------------------------------------------------

    def _resolve_tasks_path(self, name_or_path: str) -> str:
        """Resolve a name or path to an actual tasks.json file path."""
        # Direct file path
        if os.path.isfile(name_or_path):
            return name_or_path

        # Task-set name -> task_sets/<name>/tasks.json
        candidate = os.path.join(self.base_dir, name_or_path, "tasks.json")
        if os.path.isfile(candidate):
            return candidate

        raise FileNotFoundError(
            f"Cannot find tasks at '{name_or_path}'. Checked as file path and as task set name under {self.base_dir}/."
        )

    def _extract_lengths(self, tasks: List[Dict[str, Any]]) -> List[int]:
        return [len(task["candidate"]["reference_actions"]) for task in tasks]

    @staticmethod
    def _length_stats(lengths: List[int]) -> Dict[str, Any]:
        """Compute summary statistics for a list of lengths."""
        if not lengths:
            return {}
        import numpy as np

        arr = np.array(lengths)
        return {
            "min": int(arr.min()),
            "max": int(arr.max()),
            "mean": round(float(arr.mean()), 2),
            "median": round(float(np.median(arr)), 2),
            "std": round(float(arr.std()), 2),
            "p10": round(float(np.percentile(arr, 10)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "distribution": {str(length): int(c) for length, c in zip(*np.unique(arr, return_counts=True))},
        }
