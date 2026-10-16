#!/bin/sh
set -eu
mkdir -p /logs/verifier
python - <<'CHECK'
import json, re
from pathlib import Path
ok = False
try:
    answer = json.loads(Path('/workspace/answer.json').read_text())
    ok = answer.get('status') == 'ok' and answer.get('skus') == ['BOLT-17'] and bool(re.search(r'\b14\s+units\b', answer.get('answer','')))
except (OSError, ValueError, TypeError):
    pass
Path('/logs/verifier/reward.txt').write_text('1.0' if ok else '0.0')
CHECK
