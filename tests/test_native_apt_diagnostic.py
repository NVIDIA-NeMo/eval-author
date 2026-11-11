# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/diagnose_native_apt_https.sh"
URL = "https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/dists/noble-security/InRelease"
IPS = ("185.125.189.69", "185.125.189.37", "185.125.189.36")


def test_diagnostic_probes_fixed_verified_https_routes_with_bounded_curl(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    state = tmp_path / "calls.json"
    state.write_text("[]")
    curl = binary / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "state = Path(os.environ['CURL_TEST_STATE'])\n"
        "calls = json.loads(state.read_text())\n"
        "calls.append(sys.argv[1:])\n"
        "state.write_text(json.dumps(calls))\n"
        "metrics = sys.argv[sys.argv.index('--write-out') + 1]\n"
        "for field, value in [('http_code', '000'), ('time_connect', '0.000000'), "
        "('time_appconnect', '0.000000'), ('time_total', '5.000000')]:\n"
        "    metrics = metrics.replace('%{' + field + '}', value)\n"
        "print(metrics, end='')\n"
        "print('private curl error', file=sys.stderr)\n"
        "sys.exit(28)\n"
    )
    curl.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(HELPER)],
        env={"PATH": str(binary) + os.pathsep + os.defpath, "CURL_TEST_STATE": str(state)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    calls = json.loads(state.read_text())
    assert len(calls) == 4
    expected = []
    for index, args in enumerate(calls):
        label = f"native_apt_https_ip_{IPS[index]}" if index < 3 else "native_apt_https_dns"
        metrics = f"{label} http=%{{http_code}} connect=%{{time_connect}} appconnect=%{{time_appconnect}} total=%{{time_total}}\n"
        route = ["--resolve", f"snapshot.ubuntu.com:443:{IPS[index]}"] if index < 3 else []
        assert args == [
            "--disable",
            "--silent",
            "--output",
            "/dev/null",
            "--proto",
            "=https",
            "--noproxy",
            "*",
            "--connect-timeout",
            "5",
            "--max-time",
            "5",
            "--write-out",
            metrics,
            *route,
            URL,
        ]
        expected.append(f"{label} http=000 connect=0.000000 appconnect=0.000000 total=5.000000\n")
    assert result.stdout == "".join(expected)
    assert result.stderr == ""
