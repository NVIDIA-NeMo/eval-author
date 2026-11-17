# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
"""Exact out-of-place native mutations; never broadens frozen-panel admission."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from copy import deepcopy
import rubric_native_collect as collector
from rubric_example_native import PairPanel, audit, _trusted_module

SCRIPTS = Path(__file__).resolve().parent
SCHEMA = 'eval-author.native-numeric-mutations.v1'
CASES = ('missing-endpoint', 'exposed-reference', 'persistent-state', 'partial-gpu-route')


def write(path, value):
    collector.recorder.write_new(path, value)


def replacements(case, state):
    if case == 'missing-endpoint':
        return {'environment/server.py': [('default=Path("/run/refund/ledger.sock")', 'default=Path("/run/refund/unadvertised.sock")')]}, {}
    if case == 'exposed-reference':
        return {'environment/Dockerfile': [('WORKDIR /work', 'COPY exposed-reference.py /app/exposed-reference.py\nRUN chmod 444 /app/exposed-reference.py\nWORKDIR /work')]}, {'environment/exposed-reference.py': (collector.BASE / 'solution/reference.py').read_bytes()}
    if case == 'persistent-state':
        return {'environment/ledger.py': [('        self.reset()\n', '        self.record.with_suffix(".closed").unlink(missing_ok=True)\n        if self.record.exists():\n            self.value = decode(self.record.read_bytes())\n        else:\n            self.reset()\n')]}, {'environment/docker-compose.yaml': ('services:\n  main:\n    volumes:\n      - ' + json.dumps(str(state) + ':/var/lib/refund') + '\n').encode()}
    if case == 'partial-gpu-route':
        return {'task.toml': [('cpus = 1', 'cpus = 1\ngpus = 1\ngpu_types = ["A100"]')]}, {}
    if case == 'base':
        return {}, {}
    raise ValueError('unsupported mutation')


def materialize(task, case, state):
    shutil.copytree(collector.BASE, task)
    edits, adds = replacements(case, state)
    for name, changes in edits.items():
        p = task / name
        text = p.read_text()
        for old, new in changes:
            if text.count(old) != 1:
                raise ValueError('mutation source shape changed')
            text = text.replace(old, new)
        p.write_text(text)
    for name, raw in adds.items():
        p = task / name
        p.write_bytes(raw)
        p.chmod(0o644)


def validate_task(task, case, state):
    with tempfile.TemporaryDirectory(prefix='numeric-reconstruct-') as tmp:
        expected = Path(tmp) / 'task'
        materialize(expected, case, state)
        collector.require(collector.inventory(task) == collector.inventory(expected))


def producer_identity():
    identity = collector.producer_identity()
    identity['numeric_mutations'] = [{'path': p, 'sha256': collector.recorder.digest(SCRIPTS / p)} for p in ('native_numeric_mutations.py', 'native_numeric_mutations_runtime.py', 'rubric_refund_boundary_runtime.py')]
    from rubric_evidence_scoring import definitions_digest
    identity['definitions'] = definitions_digest()
    return identity


def provider_probe(task, output):
    """Call the actual provider adapter; no GPU allocation or agent launch."""
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.environments.docker.docker import DockerEnvironment
    config = Task(task).config
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger('native-numeric-provider')
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        environment = DockerEnvironment(environment_dir=task / 'environment', environment_name='numeric-provider', session_id='numeric-provider-validation', trial_paths=TrialPaths(output / 'provider-validation'), task_env_config=config.environment, network_policy=config.environment.resolve_baseline(), logger=logger)
        environment.validate_network_policy_support()
        return {'manifest': config.model_dump(mode='json'), 'adapter': 'harbor.environments.docker.docker.DockerEnvironment', 'status': 'accepted_with_gap', 'warnings': stream.getvalue().splitlines(), 'effective_gpus': environment._effective_gpus, 'allocation_started': False, 'model_execution': False}
    finally:
        logger.removeHandler(handler)


def trial_spec():
    return {'base': ('reference', 'empty'), 'missing-endpoint': ('reference',), 'exposed-reference': ('reference',), 'persistent-state': ('target-alone', 'predecessor', 'target-after')}


def native_command(case_dir, trial):
    return [sys.executable, '-I', '-B', '-c', 'import sys; sys.path.insert(0, ' + repr(str(SCRIPTS)) + '); from harbor.cli.main import app; app()', 'trial', 'start', '-p', str(case_dir / 'task'), '-a', 'native_numeric_mutations_runtime:NumericAgent', '--agent-kwarg', 'policy=' + ('reference' if trial in ('reference', 'predecessor') else 'empty'), '--trial-name', trial, '--trials-dir', str(case_dir / 'trials'), '--env', 'docker', '--delete', '--verifier-import-path', 'native_numeric_mutations_runtime:NumericVerifier']


def native_contract(trials):
    value = collector.contract()
    value['task_id'] = 'native-numeric-mutation'
    value['cases'].update({t: {'kind': 'reference' if t in ('reference', 'predecessor') else 'empty', 'requirement': 'Observe native task reward; numeric rubric merit is derived separately.', 'health': [{'pointer': '/exception_info', 'equals': None}], 'checks': [{'pointer': '/verifier_result/rewards/reward', 'min': 0, 'max': 1}]} for t in trials})
    return value


class NumericPanel(PairPanel):
    directories = ('cases',)
    documents = ('freeze.json', 'provider.json', 'teardown.json')


def file_inventory(bundle):
    rows = [{**r, 'path': 'cases/' + r['path']} for r in collector.inventory(bundle / 'cases')]
    for name in NumericPanel.documents:
        p = bundle / name
        rows.append({'path': name, 'sha256': collector.recorder.digest(p), 'mode': p.stat().st_mode & 0o777})
    return rows


def admission(bundle, registry):
    raw, reason = collector.read_selected(bundle, 'native-index.json')
    collector.require(reason is None and raw is not None)
    collector.require(not registry.is_relative_to(bundle) and not any(p.is_symlink() for p in (registry, *registry.parents)))
    collector.require(registry.stat().st_uid == os.getuid() and registry.stat().st_mode & 0o077 == 0)
    trusted, reason = collector.read_selected(registry, hashlib.sha256(raw).hexdigest() + '.json')
    collector.require(reason is None and raw == trusted)
    d = collector.recorder.loads(raw)
    collector.require(d['schema'] == SCHEMA and d['producer'] == producer_identity())
    return d


def validate(bundle, document):
    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult
    import warnings
    collector.require(document['schema'] == SCHEMA and document['producer'] == producer_identity())
    panel = NumericPanel(bundle, document)
    freeze = panel.object(bundle / 'freeze.json')
    collector.require(freeze['base'] == collector.inventory(collector.BASE))
    state = panel.origin / 'shared-state'
    for case in ('base', *CASES):
        validate_task(bundle / 'cases' / case / 'task', case, state)
    provider = panel.object(bundle / 'provider.json')
    collector.require(provider['manifest'] == Task(bundle / 'cases/partial-gpu-route/task').config.model_dump(mode='json'))
    collector.require(provider['effective_gpus'] == 1 and provider['allocation_started'] is False)
    collector.require(provider['warnings'] == ["Docker cannot select a GPU type; ignoring gpu_types=['A100']. GPU selection follows the Compose device request."])
    # Reconcile this exact warning to current installed provider source, independently of saved text.
    from harbor.environments.docker.docker import DockerEnvironment
    import inspect
    source = inspect.getsource(DockerEnvironment.__init__)
    collector.require('Docker cannot select a GPU type; ignoring gpu_types=' in source)
    rows = {}
    ids, containers = set(), set()
    teardown = collector.recorder.loads(panel.read(bundle / 'teardown.json'))
    scorer = _trusted_module(collector.BASE / 'tests/refund_scorer.py', '_numeric_scorer')
    contract = json.loads((collector.BASE / 'environment/contract.json').read_text())
    for case, trials in trial_spec().items():
        cd = bundle / 'cases' / case
        collector.require(panel.object(cd / 'contract.json') == native_contract(trials))
        manifest = panel.object(cd / 'manifest.json')
        collector.require(manifest['contract'] == native_contract(trials) and manifest['task_sha256'] == panel.task_digest(cd / 'task'))
        collector.require(set(p.name for p in (cd / 'trials').iterdir()) == set(trials))
        collector.require(set(p.name for p in (cd / 'receipts').iterdir()) == {t + '.json' for t in trials})
        collector.require(set(p.name for p in (cd / 'manifest.json.attempts').iterdir()) == {hashlib.sha256(t.encode()).hexdigest() + '.json' for t in trials})
        for trial in trials:
            receipt_path = cd / 'receipts' / (trial + '.json')
            receipt = panel.object(receipt_path)
            collector.require(receipt['command'] == native_command(panel.origin / 'cases' / case, trial))
            owned = audit.inspect_receipt(panel, receipt_path, receipt, {cd / 'task'}, include_integrity=True)
            collector.require(owned['integrity']['freshness'] == 'current' and owned['integrity']['status'] == 'consistent')
            td = cd / 'trials' / trial
            native = panel.object(td / 'result.json')
            TrialResult.model_validate(native)
            conf = TrialConfig.model_validate(native['config'])
            collector.require(conf == TrialConfig.model_validate(panel.object(td / 'config.json')))
            collector.require(native['exception_info'] is None and native['id'] not in ids)
            ids.add(native['id'])
            collector.require(conf.task.path == panel.origin / 'cases' / case / 'task' and conf.environment.type.value == 'docker' and conf.environment.delete is True)
            collector.require(conf.agent.name == 'native_numeric_mutations_runtime:NumericAgent' and conf.agent.model_name is None and conf.agent.kwargs == {'policy': 'reference' if trial in ('reference', 'predecessor') else 'empty'})
            collector.require(conf.verifier.import_path == 'native_numeric_mutations_runtime:NumericVerifier')
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                collector.require(native['task_checksum'] == Task(cd / 'task').checksum)
            observed = panel.object(td / 'agent/numeric.json')
            runtime = panel.object(td / 'verifier/numeric.json')
            collector.require(observed['uid'] == 65534 and observed['public']['status'] == 'readable')
            collector.require(observed['public']['sha256'] == collector.recorder.digest(collector.BASE / 'environment/contract.json'))
            cid = runtime['runtime']['container']
            collector.require(cid not in containers and runtime['container']['Id'] == cid)
            containers.add(cid)
            collector.require({'case': case, 'trial': trial, 'container': cid, 'removed': True} in teardown)
            from native_numeric_mutations_runtime import PROBE
            collector.require(runtime['probe_sha256'] == hashlib.sha256(PROBE.encode()).hexdigest())
            collector.require(runtime['container']['HostConfig']['Privileged'] is False)
            collector.require(runtime['container']['HostConfig']['NanoCpus'] == 1000000000 and runtime['container']['HostConfig']['Memory'] == 268435456)
            collector.require('empty: deny all controlled TCP egress' in runtime['runtime']['network_policy'])
            if case == 'persistent-state':
                collector.require(any(m['Destination'] == '/var/lib/refund' and m['Source'] == str(state) and m['RW'] is True for m in runtime['runtime']['mounts']))
            collector.require(runtime['components']['server'] == collector.recorder.digest(cd / 'task/environment/server.py'))
            collector.require(runtime['components']['ledger'] == collector.recorder.digest(cd / 'task/environment/ledger.py'))
            candidate = runtime['record']['candidate']
            expected = scorer.evaluate(contract, candidate, grader='reviewed')
            checks = panel.object(td / 'verifier/checks.json')
            collector.require(expected == checks)
            state_expected, _ = scorer._apply_events(contract['sources']['initial_state'], candidate['events'])
            collector.require(state_expected == runtime['record']['state'])
            reward = native['verifier_result']['rewards']['reward']
            collector.require(reward == checks['total_credit'] / checks['max_credit'])
            collector.require(runtime['reward'] == native['verifier_result'] and float(panel.read(td / 'verifier/reward.txt')) == reward)
            rows[case + '/' + trial] = {'observation': observed, 'runtime': runtime, 'reward': reward, 'checks': checks, 'started_at': native['started_at'], 'finished_at': native['finished_at'], 'id': native['id']}
    collector.require(len(teardown) == len(rows))
    return {'controls': rows, 'provider': provider, 'base_unchanged': True, 'source_identity': document['producer']}


def collect(bundle, plugins, registry):
    collector.require(not bundle.exists() and not any(p.is_symlink() for p in (bundle, *bundle.parents)))
    identity = producer_identity()
    bundle.mkdir(mode=0o700)
    env = collector.private_environment(bundle, plugins)
    collector.preflight(env)
    state = bundle / 'shared-state'
    state.mkdir(mode=0o700)
    for case in ('base', *CASES):
        cd = bundle / 'cases' / case
        cd.mkdir(parents=True)
        materialize(cd / 'task', case, state)
    write(bundle / 'freeze.json', {'base': collector.inventory(collector.BASE), 'mutations': list(CASES), 'scope': 'Scripted fault injection, not independent review or model performance. Original private record and reference meanings remain fixed.'})
    before = dict(os.environ)
    teardown = []
    try:
        os.environ.clear(); os.environ.update(env)
        write(bundle / 'provider.json', provider_probe(bundle / 'cases/partial-gpu-route/task', bundle))
        for case, trials in trial_spec().items():
            cd = bundle / 'cases' / case
            write(cd / 'contract.json', native_contract(trials))
            collector.recorder.prepare(cd / 'task', cd / 'contract.json', cd / 'manifest.json')
            for trial in trials:
                receipt = collector.recorder.run(cd / 'manifest.json', trial, trial, cd / 'receipts' / (trial + '.json'), cd / 'trials' / trial / 'result.json', None, native_command(cd, trial), timeout=420)
                print(case + '/' + trial + ': ' + receipt['status'], flush=True)
                runtime = json.loads((cd / 'trials' / trial / 'verifier/numeric.json').read_text())
                cid = runtime['runtime']['container']
                removed = not subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'id=' + cid], text=True, timeout=30).strip()
                teardown.append({'case': case, 'trial': trial, 'container': cid, 'removed': removed})
                collector.require(removed)
    finally:
        os.environ.clear(); os.environ.update(before)
    write(bundle / 'teardown.json', teardown)
    collector.require(identity == producer_identity())
    document = {'schema': SCHEMA, 'bundle': str(bundle), 'producer': identity, 'files': file_inventory(bundle)}
    result = validate(bundle, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (bundle / 'native-index.json').write_bytes(raw)
    with contextlib.ExitStack() as stack:
        old = os.environ.get('EVAL_AUTHOR_NATIVE_REGISTRY')
        os.environ['EVAL_AUTHOR_NATIVE_REGISTRY'] = str(registry)
        try:
            collector.register(raw, bundle)
        finally:
            if old is None: os.environ.pop('EVAL_AUTHOR_NATIVE_REGISTRY', None)
            else: os.environ['EVAL_AUTHOR_NATIVE_REGISTRY'] = old
    admission(bundle, registry)
    write(bundle / 'summary.json', {'rewards': {k: v['reward'] for k, v in result['controls'].items()}, 'base_unchanged': True})
    return result


def findings(projection, case):
    rows = projection['controls']
    positive = rows['base/reference']
    empty = rows['base/empty']
    collector.require(positive['reward'] == 1 and empty['reward'] == 0)
    collector.require(positive['observation']['reference']['status'] == 'FileNotFoundError')
    if case == 'missing-endpoint':
        r = rows[case + '/reference']
        collector.require(r['observation']['before'] == {'transport_error': 'FileNotFoundError'})
        collector.require(r['runtime']['declared_endpoint_exists'] is False and r['runtime']['other_endpoint_exists'] is True and r['reward'] == 0)
        reason = 'Native UID 65534 calls to the required /run/refund/ledger.sock fail with FileNotFoundError; root observation confirms the service instead created /run/refund/unadvertised.sock. Identical reference actions earn 1 on unchanged base and 0 here. This changes the actual service configuration, not the declared public contract.'
        return {k: (0, reason) for k in ('provenance.observed-configuration', 'provenance.solver-tool-access', 'provenance.dependency-substitution')}
    if case == 'exposed-reference':
        r = rows[case + '/reference']
        collector.require(r['observation']['reference'] == {'status':'readable', 'sha256':collector.recorder.digest(collector.BASE / 'solution/reference.py')})
        collector.require(r['reward'] == 1)
        return {'AC-01': (0, 'UID 65534 reads an image-layer copy byte-identical to the private reference solution. A permitted public-contract read succeeds in the same probe. The unchanged instruction explicitly excludes reference solutions from solver inputs; base denies this exact copy path. Public verifier source is not treated as private.')}
    if case == 'persistent-state':
        alone, prior, after = [rows[case + '/' + t] for t in ('target-alone','predecessor','target-after')]
        collector.require([alone['reward'],prior['reward'],after['reward']] == [0,1,1])
        collector.require(alone['finished_at'] <= prior['started_at'] and prior['finished_at'] <= after['started_at'])
        collector.require(after['observation']['before']['value'] == prior['runtime']['record'])
        collector.require(after['observation']['replies'] == [] and after['runtime']['record'] == prior['runtime']['record'])
        collector.require(alone['runtime']['runtime']['session'] != after['runtime']['runtime']['session'])
        reason = 'A real reference predecessor persists its exact candidate/state in the added shared backend mount. A distinct fresh Harbor target session performs no actions yet earns 1, versus 0 for the same target alone and unchanged-base empty control. The unchanged contract requires exact reset on service start. Captured drift therefore invalidates the independent-trial comparison; this is not permitted live variation.'
        return {'AC-04': (0, reason), 'provenance.external-state': (0, reason)}
    if case == 'partial-gpu-route':
        collector.require(projection['provider']['status'] == 'accepted_with_gap')
        return {'provenance.runtime-route': (1, 'Installed Harbor Task accepts a GPU-type manifest; the real DockerEnvironment constructor accepts the adapter and validates no-network policy but warns it ignores gpu_types=[A100]. GPU count survives as 1; type selection remains a provider-confirmed adapter gap. No GPU allocation or model execution was attempted. This is partial provider validation, not an unrun-to-one conversion or confirmed runtime incompatibility.')}
    return {}


@contextlib.contextmanager
def table_hook(bundle: Path, registry: Path, case: str):
    import rubric_table
    original = rubric_table.measure
    def measured(root, selection):
        task = bundle / 'cases' / case / 'task'
        expected = rubric_table.select_task(root, (task / 'task.toml').relative_to(root).as_posix())
        collector.require(selection == expected)
        document = admission(bundle, registry)
        projection = validate(bundle, document)
        derived = findings(projection, case)
        report = original(root, selection)
        report['measurements']['native_numeric_mutations'] = {'schema':SCHEMA,'case':case,'index_sha256':collector.recorder.digest(bundle / 'native-index.json'),'native_bundle':str(bundle),'controls':{k:{'reward':v['reward'],'checks':v['checks'],'native_run_id':v['id']} for k,v in projection['controls'].items()},'base_unchanged':True,'scope':'Changed task only. Frozen native receipts and accepted source reviews are not transferred.'}
        for ident, (score, reason) in derived.items():
            row = report['items'][ident]
            row.update(score=score,status='ASSESSED',assessment_state='ASSESSED',execution_state='COMPLETED',evidence_strength='E2' if case != 'partial-gpu-route' else 'E1',upper_evidence_summary=reason,next_action='Restore the exact original task/configuration; retain current-base repair evidence separately.')
            row['native_numeric_mutations'] = {'case':case,'finding':reason,'measurement':'native_numeric_mutations'}
            # Only the measured check is replaced; unrelated human/upper gates stay visible.
            for check in row['check_results']:
                # A defect establishes zero, but does not execute every listed check.
                method = check['method'].lower()
                owns_check = (method == 'deterministic' if case == 'partial-gpu-route' or ident in ('provenance.observed-configuration', 'provenance.dependency-substitution') else method == 'probe')
                if owns_check:
                    check.update(outcome='PARTIAL' if score == 1 else 'FAIL',remaining_gap=reason,next_action=row['next_action'])
                    check.setdefault('evidence', []).append({'helper':'native_numeric_mutations','value':{'case':case,'native_index':str(bundle / 'native-index.json'),'finding':reason}})
        collector.require(admission(bundle, registry) == document)
        collector.require(validate(bundle, document) == projection)
        from rubric_upper_companions import compact_identity_evidence
        compact_identity_evidence(report)
        return report
    rubric_table.measure = measured
    try: yield
    finally: rubric_table.measure = original


def reports(bundle, registry, output, base_matrix):
    import rubric_table
    collector.require(not output.exists())
    output.mkdir(mode=0o700)
    matrix = json.loads(base_matrix.read_text()) if base_matrix else None
    if matrix:
        base_saved = json.loads(Path(matrix['base']['report']).read_text())
        repair_saved = json.loads(Path(matrix['repair']['report']).read_text())
        collector.require({k:v['score'] for k,v in repair_saved['report']['items'].items()} == matrix['base_scores'])
        collector.require(base_saved['selection'] == repair_saved['selection'])
    projection = validate(bundle, admission(bundle, registry))
    extension = {'schema':SCHEMA,'bundle':str(bundle),'registry':str(registry),'base_task_unchanged':True,'rows':[],'physical_cases':list(CASES),'base_matrix':str(base_matrix) if base_matrix else None,'repair':matrix.get('repair') if matrix else None}
    root = bundle.parent
    for case in CASES:
        dest = output / case
        task = (bundle / 'cases' / case / 'task/task.toml').relative_to(root).as_posix()
        command = [sys.executable,'-B',str(Path(__file__).resolve()),'--bundle',str(bundle),'--registry',str(registry),'--case',case,'--report',str(dest)]
        with table_hook(bundle, registry, case), contextlib.redirect_stdout(io.StringIO()):
            status = rubric_table.main(['--root',str(root),'--task',task,'--output',str(dest)])
            replay = rubric_table.main(['--root',str(root),'--verify-existing',str(dest)])
        collector.require(status == replay and status in (0,1))
        actual = json.loads((dest / 'assessment.json').read_text())['report']['items']
        for criterion, (expected, reason) in findings(projection, case).items():
            extension['rows'].append({'case':case,'criterion':criterion,'expected':expected,'actual':actual[criterion]['score'],'matched':expected == actual[criterion]['score'],'base':matrix['base_scores'][criterion] if matrix else None,'reason':reason,'report':str(dest / 'assessment.json'),'command':command,'replay_command':command[:-2] + ['--verify-existing',str(dest)],'collateral_scores':{k:v['score'] for k,v in actual.items() if k != criterion}})
    collector.require(len(extension['rows']) == 7 and all(r['matched'] for r in extension['rows']))
    write(output / 'matrix-extension.json', extension)
    lines = ['# Native numeric matrix extension','', 'Seven requested cells; four physical configurations; seven real native trials plus one provider-only adapter validation. No models.','', '| Criterion | Base | Actual | Case | Evidence |','|---|---:|---:|---|---|']
    for row in extension['rows']:
        lines.append('| ' + row['criterion'] + ' | ' + str(row['base']) + ' | ' + str(row['actual']) + ' | ' + row['case'] + ' | [Report](' + row['report'] + ') |')
    lines.extend(['','Exact source reconstruction, registered raw inventory, provider/recorder validation, unchanged-base native positive/empty controls and every JSON/Markdown/HTML replay passed. Full current-base repair is reused from the existing matrix; changed-task sources receive no old approvals or unrelated numeric credit.','', 'The partial GPU route is constructor/manifest evidence only: allocation remains unrun. Other mutant criteria without sufficient current evidence remain Unassessed.'])
    (output / 'summary.md').write_text('\n'.join(lines) + '\n')
    return extension


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--collect',type=Path)
    p.add_argument('--bundle',type=Path)
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--docker-plugins',type=Path)
    p.add_argument('--reports',type=Path)
    p.add_argument('--base-matrix',type=Path)
    p.add_argument('--case',choices=CASES)
    p.add_argument('--report',type=Path)
    p.add_argument('--verify-existing',type=Path)
    args = p.parse_args(argv)
    if args.collect:
        collect(args.collect.absolute(), args.docker_plugins, args.registry.absolute())
        bundle = args.collect.absolute()
    else: bundle = args.bundle.absolute()
    if args.reports:
        result = reports(bundle,args.registry.absolute(),args.reports.absolute(),args.base_matrix)
        print(json.dumps({'rows':len(result['rows']),'reports':str(args.reports),'all_matched':all(r['matched'] for r in result['rows'])}))
    elif args.case:
        import rubric_table
        root = bundle.parent
        tail = ['--verify-existing',str(args.verify_existing)] if args.verify_existing else ['--task',(bundle / 'cases' / args.case / 'task/task.toml').relative_to(root).as_posix(),'--output',str(args.report)]
        with table_hook(bundle,args.registry.absolute(),args.case):
            return rubric_table.main(['--root',str(root),*tail])
    else:
        projection = validate(bundle,admission(bundle,args.registry.absolute()))
        print(json.dumps({'validated':True,'rewards':{k:v['reward'] for k,v in projection['controls'].items()}}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
