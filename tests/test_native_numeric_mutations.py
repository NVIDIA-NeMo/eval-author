from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/eval-author-rubric/scripts'
sys.path.insert(0, str(SCRIPTS))


def test_mutant_is_exact_and_base_is_unchanged(tmp_path):
    import native_numeric_mutations as n
    before = n.collector.inventory(n.collector.BASE)
    task = tmp_path / 'task'
    n.materialize(task, 'missing-endpoint', tmp_path / 'state')
    n.validate_task(task, 'missing-endpoint', tmp_path / 'state')
    assert n.collector.inventory(n.collector.BASE) == before
    (task / 'instruction.md').write_text('arbitrary changed bytes')
    import pytest
    with pytest.raises(ValueError):
        n.validate_task(task, 'missing-endpoint', tmp_path / 'state')


def test_all_mutations_reconstruct_exactly(tmp_path):
    import native_numeric_mutations as n
    for case in n.CASES:
        task = tmp_path / case
        n.materialize(task, case, tmp_path / 'state')
        n.validate_task(task, case, tmp_path / 'state')
        assert n.collector.inventory(task) != n.collector.inventory(n.collector.BASE)


def test_retained_native_inventory_rejects_changed_raw(tmp_path):
    import os
    import json
    import shutil
    import pytest
    import native_numeric_mutations as n
    source = os.environ.get('NATIVE_NUMERIC_TEST_BUNDLE')
    if not source:
        pytest.skip('explicit real native bundle required')
    registry = Path(os.environ['NATIVE_NUMERIC_TEST_REGISTRY'])
    bundle = Path(source)
    document = n.admission(bundle, registry)
    projection = n.validate(bundle, document)
    assert sum(len(n.findings(projection, c)) for c in n.CASES) == 7
    copied = tmp_path / 'copied'
    copied.mkdir()
    for name in n.NumericPanel.directories:
        shutil.copytree(bundle / name, copied / name)
    for name in (*n.NumericPanel.documents, 'native-index.json'):
        shutil.copy2(bundle / name, copied / name)
    n.validate(copied, n.admission(copied, registry))
    target = copied / 'cases/persistent-state/trials/target-after/result.json'
    raw = json.loads(target.read_text())
    raw['verifier_result']['rewards']['reward'] = 0
    target.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        n.validate(copied, document)
