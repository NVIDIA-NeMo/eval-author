# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Portable TASTE boundaries and optional real native adapter controls."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/integrations/taste"
VENDOR = SKILL / "vendor/taste"


def run_source(source, *, vendor=VENDOR):
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(vendor)!r})\n" + source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_sequence_runtime_and_shared_generation_import_without_tau2_or_providers():
    run_source("""
from src.common.sampler import ActionSequenceNGramModelSampler
from src.common.domain_config import DomainConfig
from src.stage3_tasks.generate_tasks import generate_single_task
from src.common.partial_coverage_hints import PartialCoverageHints
assert not any(name.split(".")[0] in {"tau2", "vertexai", "litellm"} for name in sys.modules)
""")


def test_incompatibility_and_infrastructure_errors_do_not_retry():
    run_source("""
from src.backends.contracts import InfrastructureError, ValidationResult
from src.stage3_tasks.validation_with_retry import validate_task_with_retries
class Generator:
    def generate(self, sequence): return {}, {}
    def patch_task(self,*args): raise AssertionError("must not patch unsupported task")
class Validator:
    def validate_task(self,*args): return ValidationResult(False,False,False,"answer unsupported","incompatible",unsupported=("answer",))
success, *rest = validate_task_with_retries([],Generator(),Validator())
assert not success and rest[-1] == 0
class Broken:
    def validate_task(self,*args): raise InfrastructureError("provider unavailable")
try: validate_task_with_retries([],Generator(),Broken())
except InfrastructureError: pass
else: raise AssertionError("Infrastructure failure was treated as a task negative")
class RepairGenerator(Generator):
    def patch_db(self, *args): raise RuntimeError("authentication failed")
class RepairValidator:
    def validate_task(self, *args): return ValidationResult(False,False,False,"bad state","custom",repair_scope="fixture")
try: validate_task_with_retries([],RepairGenerator(),RepairValidator())
except RuntimeError as error: assert "authentication failed" in str(error)
else: raise AssertionError("Provider failure during repair consumed candidate retries")
""")


def test_vendored_provenance_has_matching_bytes_and_no_datasets():
    import hashlib

    manifest = json.loads((VENDOR / "provenance.json").read_text())
    assert manifest["upstream_commit"] == "a60c05ba79b36d9fe782e28838712420a4bcbe97"
    assert manifest["permission"]["status"] == "user-confirmed"
    for record in manifest["files"]:
        if record["included"]:
            path = VENDOR / record["vendored_path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record["vendored_sha256"], record["path"]
    assert not list((VENDOR / "artifacts/domains").rglob("tasks.json"))
    assert not (VENDOR / "artifacts/ngram").exists()
    assert not (VENDOR / "tau2-bench").exists()


def test_length_policy_propagation_preserves_legacy_defaults_and_explicit_overrides():
    run_source("""
from types import SimpleNamespace
from src.common.sampler import LengthDistributionConfig
from src.stage2_cluster.cluster_and_validate import _choose_length_config, parse_args
args=parse_args(["--checkpoint","unused", "-k","2"])
upstream_default=LengthDistributionConfig(mode="skew_normal", gaussian_mean=7, gaussian_std=5, skew_alpha=2)
selected=LengthDistributionConfig(mode="empirical", empirical_lengths=[1,1,3])
sampler=SimpleNamespace(_length_config=selected, length_policy_explicit=True)
assert _choose_length_config(args,sampler,upstream_default,[]).mode == "empirical"
assert _choose_length_config(args,sampler,upstream_default,["--gaussian-mean=7"]).mode == "skew_normal"
sampler.length_policy_explicit=False
assert _choose_length_config(args,sampler,upstream_default,[]).mode == "skew_normal"
""")


def test_independent_backend_uses_shared_pipeline_without_builtin_adapters(tmp_path):
    run_source(f"""
import runpy
sys.argv = ["taste_portable_controls", {str(tmp_path / "counter")!r}]
runpy.run_path({str(ROOT / "tests/taste_portable_controls.py")!r}, run_name="__main__")
""")


def bundle(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "policy.md").write_text("Increment a counter using positive amounts.")
    (path / "tool_spec.json").write_text(json.dumps({"increment": {"type": "WRITE", "parameters": {}}}))
    (path / "seeds.jsonl").write_text("")
    (path / "task_schema.json").write_text('{"type":"object"}')
    (path / "fixture_schema.json").write_text('{"type":"object"}')
    return path


def test_normalized_seeds_preserve_empty_references_order_arguments_and_split(tmp_path):
    root = bundle(tmp_path)
    run_source(f"""
import copy
from src.common.domain_config import DomainConfig
from src.backends.contracts import sequence_of
config=DomainConfig("mb1", resources_dir={str(root)!r})
action={{"name":"increment","arguments":{{"id":"007","enabled":True}}}}
rows=[{{"id":"same","split":"train","actions":[]}},
      {{"id":"same","split":"train","native":{{"extra":"retain"}},"actions":[action,action]}}]
original=copy.deepcopy(rows)
seeds=config.seeds(rows)
assert [s.id for s in seeds] == ["same","same"]
assert [s.split for s in seeds] == ["train","train"]
assert [sequence_of(s) for s in seeds] == [[],["increment","increment"]]
seeds[1].actions[0]["arguments"].clear()
assert rows == original
try: config.seeds([{{"id":"raw","ground_truth":[]}}])
except ValueError: pass
else: raise AssertionError("Raw native record accepted")
""")


def test_sequence_stages_run_without_loading_task_service(tmp_path):
    root = bundle(tmp_path / "input")
    run_source(f"""
import json, os, runpy
from pathlib import Path
from unittest.mock import patch
from src.common.domain_config import DomainConfig
root=Path({str(root)!r})
rows=[{{"id":str(n),"split":"train","actions":[{{"name":"increment","arguments":{{}}}}]*n}} for n in [1,2,3]]
(root/"seeds.jsonl").write_text("\\n".join(json.dumps(r) for r in rows))
config=DomainConfig("mb1",resources_dir=str(root),task_service="unavailable_runtime:Service")
(root/"prepared.json").write_text(json.dumps(config.to_spec()))
(root/"lengths.json").write_text(json.dumps({{"mode":"empirical","empirical_lengths":[1,1,2,3]}}))
class Scripted:
    def __init__(self, **kwargs): pass
    def call(self, **kwargs): return '{{"valid":true,"reason":"control","problematic_indices":[]}}'
os.chdir(root)
with patch.object(DomainConfig,"llm_factory",Scripted):
    sys.argv=["train","--backend-config",str(root/"prepared.json"),"--num-samples","2","--length-config",str(root/"lengths.json"),"--save-dir",str(root/"training")]
    runpy.run_module("src.stage1_ngram.initialize_ngram_model",run_name="__main__")
    checkpoint=next((root/"artifacts/ngram/checkpoints").glob("*.json"))
    sys.argv=["cluster","--checkpoint",str(checkpoint),"-k","2","--pool-size","3","--output",str(root/"clusters.json")]
    runpy.run_module("src.stage2_cluster.cluster_and_validate",run_name="__main__")
assert (root/"clusters.json").is_file()
assert not any(name.startswith(("benchmark_runtime","tau2","unavailable_runtime")) for name in sys.modules)
try: config.service()
except ModuleNotFoundError: pass
else: raise AssertionError("Missing runtime was not reported when requested")
""")


def test_checkpoint_and_reference_distribution_preserve_duplicates_and_empty_seeds(tmp_path):
    root = bundle(tmp_path)
    run_source(f"""
import json
from pathlib import Path
from src.common.domain_config import DomainConfig
from src.common.sampler import ActionSequenceNGramModelSampler,LengthDistributionConfig
from src.common.sampler.length_distribution import load_reference_tasks
from src.common.sampler.action_sequence_clusterer import save_validated_clusters,load_validated_clusters
root=Path({str(root)!r})
config=DomainConfig("mb1",resources_dir=str(root))
rows=[{{"id":str(n),"actions":[{{"name":"increment","arguments":{{}}}}]*n}} for n in [0,1,2,2,3]]
(root/"seeds.jsonl").write_text("\\n".join(json.dumps(r) for r in rows))
policy=LengthDistributionConfig(mode="from_seed",reference_source=str(root/"seeds.jsonl"))
references=load_reference_tasks(policy,config)
assert [s.split for s in references] == ["reference"]*5
sampler=ActionSequenceNGramModelSampler(config.seeds(),domain="mb1",backend_spec=config.to_spec(),tool_spec_path=config.tool_spec_path,reference_tasks=references,length_distribution_config=policy)
sampler.ingest(["increment"]*9,positive=True)
checkpoint=root/"checkpoint.json"
sampler.save_state(str(checkpoint))
restored=ActionSequenceNGramModelSampler.load_state(str(checkpoint))
assert restored._pending_seeds == [[],["increment"],["increment"]*2,["increment"]*2,["increment"]*3]
assert restored._reference_lengths == [0,1,2,2,3]
assert restored.length_distribution.lengths == [0,1,2,2,3]
empirical=LengthDistributionConfig(mode="empirical",empirical_lengths=[1,1,3])
restored._length_config=empirical;restored._length_distribution=None
samples=[restored.sample_length() for _ in range(1000)]
assert set(samples) == {{1,3}} and samples.count(1)>samples.count(3)
artifact=root/"clusters.json"
save_validated_clusters(str(artifact),"mb1",0,str(checkpoint),policy.to_dict(),{{"only_write":True}},{{}},[],[],backend_spec=config.to_spec(),reference_lengths=restored._reference_lengths)
assert load_validated_clusters(str(artifact))["reference_lengths"] == [0,1,2,2,3]
state=sampler._serialize_state();state["config"].pop("backend_spec")
checkpoint.write_text(json.dumps(state))
try: ActionSequenceNGramModelSampler.load_state(str(checkpoint))
except ValueError: pass
else: raise AssertionError("Unbound legacy checkpoint was accepted")
""")


@pytest.mark.parametrize(
    "changed", ["seeds.jsonl", "policy.md", "tool_spec.json", "task_schema.json", "fixture_schema.json", "context.json"]
)
def test_prepared_inputs_detect_changed_resources(tmp_path, changed):
    root = bundle(tmp_path)
    run_source(f"""
from pathlib import Path
from src.common.domain_config import DomainConfig
from src.backends.contracts import IncompatibleBackend
config=DomainConfig("mb1",resources_dir={str(root)!r})
spec=config.to_spec()
Path({str(root / changed)!r}).write_text('{{"changed":true}}')
try: DomainConfig.from_spec(spec)
except IncompatibleBackend: pass
else: raise AssertionError("Changed prepared resource accepted")
""")


def test_resume_binds_resources_lengths_clusters_and_models(tmp_path):
    root = bundle(tmp_path)
    run_source(f"""
from src.backends.artifacts import bind_run
from src.backends.contracts import IncompatibleBackend
from src.common.domain_config import DomainConfig
spec=DomainConfig("mb1",resources_dir={str(root)!r}).to_spec()
length={{"mode":"empirical","empirical_lengths":[1,1,3]}}
settings={{"clusters_sha256":"original","model":"scripted"}}
bind_run({str(root)!r},spec,length,resume=False,settings=settings)
bind_run({str(root)!r},spec,length,resume=True,settings=settings)
for selected,distribution,options in [({{**spec,"domain":"other"}},length,settings),(spec,{{"mode":"empirical","empirical_lengths":[1,3]}},settings),(spec,length,{{**settings,"model":"other"}}),(spec,length,{{**settings,"clusters_sha256":"changed"}})]:
    try: bind_run({str(root)!r},selected,distribution,resume=True,settings=options)
    except IncompatibleBackend: pass
    else: raise AssertionError("Incompatible resume accepted")
try: DomainConfig.from_spec({{"backend":"tau2","domain":"airline"}})
except IncompatibleBackend: pass
else: raise AssertionError("Legacy native configuration accepted")
""")


def test_taste_hints_preserve_masking_rounding_shuffle_and_actor(tmp_path):
    run_source("""
from src.common.partial_coverage_hints import ActionHints
from unittest.mock import patch
acts=[{"name":"lookup","arguments":{"id":"007"}} for _ in range(4)]
zero=ActionHints(acts,"policy",coverage_p=0,shuffle=False).system_prompt
assert "id=???" not in zero
partial=ActionHints(acts,"policy",coverage_p=0.33,shuffle=False).make_agent_instructions_from_actions()
assert partial.count("id=???") == 1 and partial.count('"007"') == 3
hints=ActionHints(acts+[{"name":"user_check","arguments":{"id":"private"},"requestor":"user"}],"policy",coverage_p=1,shuffle=True)
with patch("src.common.partial_coverage_hints.random.shuffle",side_effect=lambda x:x.reverse()) as shuffle:
    text=hints.make_agent_instructions_from_actions()
assert text.startswith("- user: user_check") and text.count("id=???") == 4
assert '"private"' in text and shuffle.call_count == 1
""")


def test_entire_taste_runtime_has_no_benchmark_code():
    import ast

    forbidden = {
        "airline",
        "telecom",
        "retail",
        "tau2",
        "workspace_assistant",
        "user_scenario",
        "evaluation_criteria",
        "ground_truth",
    }
    for path in (VENDOR / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in forbidden, (path, node.lineno, node.value)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("tau2", "benchmark_runtime", "src.backends.tau2", "src.backends.workspace_assistant")
                ), (path, node.lineno)
    assert not (VENDOR / "src/backends/tau2").exists()
    assert not (VENDOR / "src/backends/workspace_assistant").exists()
    assert not (VENDOR / "artifacts/domains").exists()


def test_task_service_contract_rejects_missing_native_result_and_changed_sequence(tmp_path):
    root = bundle(tmp_path)
    run_source(f"""
from src.common.domain_config import DomainConfig
from src.backends.contracts import ValidationResult,InfrastructureError
from src.stage3_tasks.task_validator import TaskValidator
candidate={{"task":{{}},"reference_actions":[{{"name":"increment","arguments":{{"amount":1}}}}]}}
config=DomainConfig("mb1",resources_dir={str(root)!r})
class Service:
    def validate(self,*args,**kwargs): return ValidationResult(True,True,True)
config._service=Service()
validator=TaskValidator(domain_config=config)
try: validator.validate_task(candidate,{{}})
except InfrastructureError: pass
else: raise AssertionError("Success without native task was accepted")
class Changed:
    def validate(self,*args,**kwargs):
        return ValidationResult(True,True,True,native_task={{}},candidate={{"task":{{}},"reference_actions":[]}})
config._service=Changed()
try: TaskValidator(domain_config=config).validate_task(candidate,{{}})
except InfrastructureError: pass
else: raise AssertionError("Service changed the selected sequence")
""")


def test_service_refinements_survive_exhaustion_and_trace_is_retained(tmp_path):
    root = bundle(tmp_path)
    run_source(f"""
import json
from pathlib import Path
from src.common.domain_config import DomainConfig
from src.backends.contracts import ValidationResult
from src.stage3_tasks.task_validator import TaskValidator
from src.stage3_tasks.validation_with_retry import validate_task_with_retries
candidate={{"task":{{"request":"Increment"}},"reference_actions":[{{"name":"increment","arguments":{{"amount":1}}}}]}}
class Generator:
    def generate(self,sequence): return candidate,{{}}
class Service:
    def validate(self,candidate,fixture,**kwargs):
        candidate["task"]["criteria"]={{"expected":1}}
        return ValidationResult(False,True,False,"hinted failed","hinted_execution",candidate=candidate,fixture={{"start":0}},gt_simulation={{"messages":[{{"role":"user","content":"Confirm"}}]}})
config=DomainConfig("mb1",resources_dir={str(root)!r},options={{"evidence_dir":{str(root / "evidence")!r}}})
config._service=Service()
success,effective,fixture,result,retries=validate_task_with_retries(["increment"],Generator(),TaskValidator(domain_config=config),max_retries=0,max_patch_attempts=0)
assert not success and effective["task"]["criteria"] == {{"expected":1}} and fixture == {{"start":0}}
receipt=json.loads(Path(result.evidence_path).read_text())
trace=json.loads(Path(receipt["hinted_trace"]).read_text())
assert trace["messages"][0]["content"] == "Confirm" and "supplied_hints" in trace
""")


def test_repairs_receive_schemas_without_optional_context(tmp_path):
    root = bundle(tmp_path)
    (root / "fixture_schema.json").write_text('{"type":"object","required":["opaque_start"]}')
    (root / "task_schema.json").write_text('{"type":"object","required":["opaque_request"]}')
    run_source(f"""
from src.common.domain_config import DomainConfig
from src.stage3_tasks.task_generation import TaskGenerator
config=DomainConfig("mb1",resources_dir={str(root)!r})
class Scripted:
    def __init__(self,**kwargs): pass
    def call(self,prompt,stage_name,**kwargs):
        assert 'opaque_start' in prompt and 'opaque_request' in prompt and 'increment' in prompt
        if stage_name == 'patch_db': return '{{"opaque_start":0}}'
        return '{{"task":{{"opaque_request":"Increment"}},"reference_actions":[{{"name":"increment","arguments":{{"amount":1}}}}]}}'
config.llm_factory=Scripted
generator=TaskGenerator(domain_config=config)
candidate={{"task":{{"opaque_request":"Increment"}},"reference_actions":[{{"name":"increment","arguments":{{"amount":1}}}}]}}
assert generator.patch_db(candidate,{{}},"bad fixture") == {{"opaque_start":0}}
assert generator.patch_task(candidate,{{}},"bad args")["task"]["opaque_request"] == "Increment"
""")
