# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare public-only fresh-review inputs; never launch a model or invent consent."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'examples/refund/base'
PROMPT='''Read instruction.md first. Discover the goal, allowed actions, state, scoring obligations and all material authority using only the provided public task and rubric. Cite exact paths and spans for each obligation and record your discovery sequence. Report missing or ambiguous authority honestly. Derive a valid solution and boundary alternatives from the public task, and state expected per-check credit before any execution. Do not consult author notes, prior assessments, reference solutions, case catalogs or other sessions. Record any prior exposure before opening files. Do not infer blindness from a new session label. Save requirements.md, ambiguity.md, discovery.json (ordered path/actions), and proposed-controls.json. No model calls or grading run is requested inside this review. The parent will compare your sealed output with the complete scorer inventory after you finish. Do not ask for or receive expected outcomes during the session.
'''

def prepare(output: Path):
    if output.exists(): raise ValueError('destination exists')
    output.mkdir(parents=True,mode=0o700)
    # All these files are public under the frozen instruction. No solutions,
    # private records, existing reports, expectation catalogs or exposure notes.
    sources={'instruction.md':BASE/'instruction.md','app/contract.json':BASE/'environment/contract.json','app/ledger.py':BASE/'environment/ledger.py','app/server.py':BASE/'environment/server.py'}
    for name in ('test.sh','verify.py','refund_scorer.py'): sources['tests/'+name]=BASE/'tests'/name
    for name in ('anti-cheat.md','task-contract-authority.md','task-contract-authority.json','artifact-configuration-provenance.md','artifact-configuration-provenance.json'):
        sources['rubric/'+name]=HERE.parent/'references'/name
    manifest=[]
    for target,source in sources.items():
        path=output/target; path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,path)
        manifest.append({'path':target,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (output/'PROMPT.md').write_text(PROMPT)
    (output/'public-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args(); print(json.dumps(prepare(args.output.absolute()),sort_keys=True))
