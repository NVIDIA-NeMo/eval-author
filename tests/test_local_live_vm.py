# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Process-level fake-Lima tests; these do not establish real VM teardown."""

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


class StdinAdmissionTests(unittest.TestCase):
    def setUp(self):
        import local_live_vm as transport

        self.transport = transport
        self.temp = tempfile.TemporaryDirectory(prefix="")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.vm = transport.LocalVM()
        self.vm.root, self.vm.cli = root, root / "synthetic-cli"
        self.vm.cli.write_bytes(b"synthetic executable; never executed")
        self.vm.name = transport._vm_name("a" * 32)
        (root / "lima.yaml").write_text(json.dumps(transport._config(root / "share")))
        self.vm.spec = {
            "root": str(root),
            "lima_home": str(root / "lima"),
            "token": "a" * 32,
            "name": self.vm.name,
            "directories": {"": transport._identity(root)},
            "config_sha256": transport._digest(root / "lima.yaml"),
            "limactl_sha256": transport._digest(self.vm.cli),
            "deadline": 10000,
        }
        self.process = mock.Mock()
        self.process.poll.return_value = None
        self.vm.process = self.process

    def test_stdin_admission_rechecks_after_binding_validation(self):
        clock = [100.0]
        validate = self.transport._validate_binding

        def slow_validation(*args):
            validate(*args)
            clock[0] = 102.0

        with (
            mock.patch.object(self.transport.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(self.transport, "_validate_binding", side_effect=slow_validation) as binding,
            mock.patch.object(self.transport.subprocess, "run") as admission,
        ):
            with self.assertRaisesRegex(ValueError, "stdin admission"):
                self.vm.shell(["run"], timeout=3000, stdin=b"SYNTHETIC", stdin_deadline=101.0)
            binding.assert_called_once()
            admission.assert_not_called()

    def test_stdin_deadline_type_finite_and_expiration_boundaries(self):
        import math

        invalid = (
            True,
            False,
            "101",
            b"101",
            [],
            {},
            object(),
            float("nan"),
            float("inf"),
            -float("inf"),
            -1,
            0,
            99.0,
            100,
            100.0,
        )
        with mock.patch.object(self.transport.time, "monotonic", return_value=100.0):
            for deadline in invalid:
                with self.subTest(deadline=deadline), mock.patch.object(self.transport.subprocess, "run") as admission:
                    with self.assertRaisesRegex(ValueError, "stdin admission"):
                        self.vm.shell(["run"], timeout=3000, stdin=b"SYNTHETIC", stdin_deadline=deadline)
                    admission.assert_not_called()
            for deadline in (None, 101, 101.0, math.nextafter(100.0, math.inf)):
                with self.subTest(deadline=deadline), mock.patch.object(self.transport.subprocess, "run") as admission:
                    self.vm.shell(["run"], timeout=3000, stdin=b"SYNTHETIC", stdin_deadline=deadline)
                    self.assertEqual(admission.call_args.kwargs["input"], b"SYNTHETIC")
                    self.assertEqual(admission.call_args.kwargs["timeout"], 3000)

    def test_stdin_deadline_rechecked_after_final_local_environment_construction(self):
        clock = [100.0]
        environment = self.transport._env

        def slow_environment(*args):
            result = environment(*args)
            clock[0] = 101.0
            return result

        with (
            mock.patch.object(self.transport.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(self.transport, "_env", side_effect=slow_environment) as env,
            mock.patch.object(self.transport.subprocess, "run") as admission,
        ):
            with self.assertRaisesRegex(ValueError, "stdin admission"):
                self.vm.shell(["run"], timeout=3000, stdin=b"SYNTHETIC", stdin_deadline=101.0)
            env.assert_called_once()
            admission.assert_not_called()

    def test_future_stdin_deadline_does_not_bypass_other_shell_guards(self):
        cases = [
            ([], 3000, b"SYNTHETIC"),
            (["bad\0arg"], 3000, b"SYNTHETIC"),
            (["run"], 3000, "not bytes"),
            (["run"], 3000, b"x" * 65537),
            (["run"], 0, b"SYNTHETIC"),
            (["run"], 10000, b"SYNTHETIC"),
        ]
        with (
            mock.patch.object(self.transport.time, "monotonic", return_value=100.0),
            mock.patch.object(self.transport.subprocess, "run") as admission,
        ):
            for argv, timeout, stdin in cases:
                with self.subTest(argv=argv, timeout=timeout, stdin_type=type(stdin)), self.assertRaises(ValueError):
                    self.vm.shell(argv, timeout=timeout, stdin=stdin, stdin_deadline=101)
            self.process.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, "closed"):
                self.vm.shell(["run"], timeout=3000, stdin=b"SYNTHETIC", stdin_deadline=101)
            self.process.poll.return_value = None
            self.vm.cli.write_bytes(b"changed executable")
            with self.assertRaisesRegex(ValueError, "executable changed"):
                self.vm.shell(["run"], timeout=3000, stdin=b"SYNTHETIC", stdin_deadline=101)
            admission.assert_not_called()

    def test_omitted_stdin_deadline_preserves_default_eof_and_execution_timeout(self):
        with (
            mock.patch.object(self.transport.time, "monotonic", return_value=100.0),
            mock.patch.object(self.transport.subprocess, "run") as admission,
        ):
            for stdin in (None, b"SYNTHETIC"):
                with self.subTest(stdin=stdin):
                    self.vm.shell(["run"], timeout=3000, stdin=stdin)
                    self.assertEqual(admission.call_args.kwargs["input"], stdin)
                    self.assertEqual(admission.call_args.kwargs["timeout"], 3000)
                    expected = subprocess.DEVNULL if stdin is None else None
                    self.assertEqual(admission.call_args.kwargs["stdin"], expected)


class LocalVMTests(unittest.TestCase):
    def setUp(self):
        # Leave room for the real Lima socket guard even under a long TMPDIR.
        self.temp = tempfile.TemporaryDirectory(prefix="")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.cli = self.base / "limactl"
        self.cli.write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent("""\
            import json, os, pathlib, sys, time
            home = pathlib.Path(os.environ['LIMA_HOME'])
            root = home.parent
            args = sys.argv[1:]
            with (root / 'calls.jsonl').open('a') as f:
                f.write(json.dumps({'args': args, 'env': dict(os.environ)}) + '\\n')
            if args == ['--version']:
                print('limactl version 2.3.0')
            elif args[0] == 'start':
                assert (root / 'armed.json').exists()
                m = json.loads((root / 'ownership.json').read_text())
                name = args[args.index('--name') + 1]
                assert name == m['name']
                instance = home / name
                instance.mkdir()
                (instance / 'status').write_text('Running')
            elif args[0] == 'list':
                for instance in home.iterdir():
                    if (instance / 'status').exists():
                        print(json.dumps({'name': instance.name, 'status': (instance / 'status').read_text(), 'dir': str(instance)}))
            elif args[0] == 'stop':
                instance = home / args[-1]
                if instance.exists():
                    (instance / 'status').write_text('Stopped')
            elif args[0] == 'delete':
                instance = home / args[-1]
                if instance.exists():
                    (instance / 'status').unlink()
                    instance.rmdir()
            elif args[0] == 'shell':
                print('guest output')
            else:
                raise SystemExit(2)
            """)
        )
        self.cli.chmod(0o700)

    def test_inventory_stderr_warning_does_not_prevent_verified_cleanup(self):
        from local_live_vm import LocalVM

        warning = (
            'time="2026-11-06T17:13:24-07:00" level=warning '
            'msg="No instance found. Run `limactl create` to create an instance."\n'
        )
        original_cli = self.cli.read_text()
        for index, condition in enumerate(("not any(home.iterdir())", "True")):
            with self.subTest(warning_condition=condition):
                self.cli.write_text(original_cli)
                self.alter_cli(
                    "elif args[0] == 'list':",
                    f"elif args[0] == 'list':\n    if {condition}: sys.stderr.write({warning!r})",
                )
                vm = LocalVM.create(self.base / str(index), limactl=self.cli, job_seconds=30, command_seconds=5)
                self.addCleanup(vm.close)
                result = vm.close()
                self.assertEqual(result["status"], "closed")
                self.assertTrue(result["eliminated"])
                self.assertEqual(list(vm.lima_home.iterdir()), [])
                calls = [json.loads(line)["args"] for line in (vm.root / "calls.jsonl").read_text().splitlines()]
                inventory_serials = [i for i, args in enumerate(calls, 1) if args == ["list", "--json"]]
                self.assertEqual(len(inventory_serials), 3)
                for serial in inventory_serials:
                    output = vm.root / f"lima-{serial}.log"
                    diagnostic = vm.root / f"lima-{serial}.stderr.log"
                    expected = warning if condition == "True" or serial == inventory_serials[-1] else ""
                    self.assertEqual(diagnostic.read_text(), expected)
                    self.assertNotIn(warning, output.read_text())
                    for path in (output, diagnostic):
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                        self.assertFalse(path.is_symlink())
                    if serial == inventory_serials[-1]:
                        self.assertEqual(output.read_bytes(), b"")
                    else:
                        self.assertEqual(json.loads(output.read_text())["name"], vm.name)
                self.assertEqual(vm.root.stat().st_mode & 0o777, 0o700)
                self.assertNotIn(warning.strip(), (vm.root / "result.json").read_text())
                self.assertEqual(list(vm.share.iterdir()), [])

    def test_stderr_json_cannot_establish_owned_inventory(self):
        from local_live_vm import LocalVM

        self.alter_cli(
            "print(json.dumps({'name': instance.name, 'status': (instance / 'status').read_text(), 'dir': str(instance)}))",
            "print(json.dumps({'name': instance.name, 'status': (instance / 'status').read_text(), "
            "'dir': str(instance)}), file=sys.stderr)",
        )
        root = self.base / "job"
        with self.assertRaises(RuntimeError):
            vm = LocalVM.create(root, limactl=self.cli, job_seconds=30, command_seconds=5)
            self.addCleanup(vm.close)
        result = json.loads((root / "result.json").read_text())
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["eliminated"])
        self.assertFalse((root / "running.json").exists())
        self.assertEqual((root / "lima-3.log").read_bytes(), b"")
        spec = json.loads((root / "ownership.json").read_text())
        diagnostic = root / "lima-3.stderr.log"
        self.assertEqual(json.loads(diagnostic.read_text())["name"], spec["name"])
        self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)

    def test_invalid_final_stdout_stays_uncertain_with_retained_stderr(self):
        from local_live_vm import LocalVM

        original_cli = self.cli.read_text()
        invalid = {
            "malformed": "print('not JSON')",
            "truncated": "print('{\"name\":')",
            "valid_then_malformed": "print(json.dumps({'name': 'unrelated'})); print('not JSON')",
            "oversized": "print(' ' * 1048576 + '{')",
            "nonzero": "raise SystemExit(7)",
        }
        diagnostic_bytes = b"inventory diagnostic\n\xff\n"
        for index, (mode, response) in enumerate(invalid.items()):
            with self.subTest(mode=mode):
                self.cli.write_text(original_cli)
                self.alter_cli(
                    "elif args[0] == 'list':",
                    "elif args[0] == 'list':\n"
                    "    if not any(home.iterdir()):\n"
                    f"        sys.stderr.buffer.write({diagnostic_bytes!r})\n"
                    f"        {response}\n"
                    "        raise SystemExit(0)",
                )
                vm = LocalVM.create(self.base / str(index), limactl=self.cli, job_seconds=30, command_seconds=5)
                self.addCleanup(vm.close)
                result = vm.close()
                self.assertEqual(result["status"], "uncertain")
                self.assertFalse(result["eliminated"])
                self.assertEqual(list(vm.lima_home.iterdir()), [])
                calls = [json.loads(line)["args"] for line in (vm.root / "calls.jsonl").read_text().splitlines()]
                self.assertEqual(calls[-2:], [["delete", "--force", vm.name], ["list", "--json"]])
                diagnostic = vm.root / f"lima-{len(calls)}.stderr.log"
                self.assertEqual(diagnostic.read_bytes(), diagnostic_bytes)
                self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("inventory diagnostic", (vm.root / "result.json").read_text())

    def test_long_socket_path_rejected_before_any_effects(self):
        from local_live_vm import LocalVM

        root = self.base / ("long-root-" * 10)
        before = sorted(self.base.iterdir())
        with (
            mock.patch("local_live_vm.subprocess.Popen", side_effect=AssertionError("process started")) as start,
            mock.patch("local_live_vm.socket.socketpair", side_effect=AssertionError("IPC created")) as channel,
            mock.patch.object(Path, "mkdir", side_effect=AssertionError("directory created")) as mkdir,
        ):
            with self.assertRaisesRegex(ValueError, "Lima.*socket.*104.*shorter"):
                LocalVM.create(root, limactl=self.cli)
            mkdir.assert_not_called()
            channel.assert_not_called()
            start.assert_not_called()
        self.assertFalse(root.exists())
        self.assertEqual(sorted(self.base.iterdir()), before)

    def test_short_name_retains_full_token_and_private_bindings(self):
        from local_live_vm import LocalVM, _validate_binding

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        spec = json.loads((vm.root / "ownership.json").read_text())
        self.assertEqual(spec, vm.spec)
        self.assertRegex(spec["token"], r"^[0-9a-f]{32}$")
        expected_name = "ea-" + hashlib.sha256(spec["token"].encode("ascii")).hexdigest()[:8]
        self.assertEqual(vm.name, expected_name)
        self.assertEqual(spec["name"], expected_name)
        self.assertEqual(Path(spec["root"]), vm.root)
        self.assertEqual(Path(spec["lima_home"]), vm.root / "lima")
        self.assertEqual(spec["limactl_sha256"], hashlib.sha256(self.cli.read_bytes()).hexdigest())
        self.assertEqual(spec["config_sha256"], hashlib.sha256((vm.root / "lima.yaml").read_bytes()).hexdigest())
        self.assertEqual(set(spec["directories"]), {"", "lima", "home", "cache", "tmp", "share"})
        for name, identity in spec["directories"].items():
            path = vm.root / name
            self.assertFalse(path.is_symlink())
            self.assertEqual(identity, [path.stat().st_dev, path.stat().st_ino])
        _validate_binding(vm.root, self.cli, spec)
        self.assertTrue(vm.close()["eliminated"])

    def test_socket_path_limit_counts_full_encoded_path(self):
        from local_live_vm import LocalVM

        for prefix in ("x", "é"):
            for size in (103, 104, 105):
                with self.subTest(prefix=prefix, socket_bytes=size):
                    suffix = Path("lima") / "ea-12345678" / "ssh.sock.1234567890123456"
                    padding = size - len(os.fsencode(self.base / prefix / suffix))
                    self.assertGreaterEqual(padding, 0, "test TMPDIR must leave room for boundary fixtures")
                    root = self.base / (prefix + "x" * padding)
                    self.assertEqual(len(os.fsencode(root / suffix)), size)
                    if size < 104:
                        vm = LocalVM.create(root, limactl=self.cli, job_seconds=30, command_seconds=5)
                        self.addCleanup(vm.close)
                        self.assertEqual(len(os.fsencode(vm.lima_home / vm.name / suffix.name)), size)
                        self.assertTrue(vm.close()["eliminated"])
                    else:
                        with mock.patch.object(Path, "mkdir", side_effect=AssertionError("directory created")):
                            with self.assertRaisesRegex(ValueError, f"socket path is {size} bytes.*104.*shorter"):
                                LocalVM.create(root, limactl=self.cli)
                        self.assertFalse(root.exists())

    def test_tampered_name_or_full_token_rejected_before_guest_process(self):
        from local_live_vm import LocalVM

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        token = vm.spec["token"]
        other_token = token[:-1] + ("0" if token[-1] != "0" else "1")
        changes = (
            {"name": "ea-deadbeef" if vm.name != "ea-deadbeef" else "ea-00000000"},
            {"name": "ea-" + token},
            {"name": "../other"},
            {"token": token[:8]},
            {"token": other_token},
        )
        for change in changes:
            with self.subTest(change=change), mock.patch.dict(vm.spec, change):
                with mock.patch("local_live_vm.subprocess.run") as run:
                    with self.assertRaisesRegex(ValueError, "invalid owned name"):
                        vm.shell(["true"], timeout=2)
                    run.assert_not_called()
        self.assertTrue(vm.close()["eliminated"])

    def test_shell_stdin_bytes_are_bounded_and_not_persisted(self):
        from local_live_vm import LocalVM

        self.alter_cli("print('guest output')", "sys.stdout.buffer.write(sys.stdin.buffer.read())")
        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        marker = b"synthetic-stdin-marker-only"
        for payload in (b"", marker, marker + b"x" * (65536 - len(marker))):
            with self.subTest(payload_bytes=len(payload)):
                result = vm.shell(["read-stdin"], timeout=2, stdin=payload)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, payload)
                self.assertEqual(result.stderr, b"")
        self.assertTrue(vm.close()["eliminated"])
        calls = [json.loads(line) for line in (vm.root / "calls.jsonl").read_text().splitlines()]
        shell_calls = [call for call in calls if call["args"][0] == "shell"]
        self.assertEqual(len(shell_calls), 3)
        for call in shell_calls:
            self.assertEqual(call["args"], ["shell", "--workdir", "/", vm.name, "--", "read-stdin"])
            self.assertNotIn(marker.decode(), json.dumps(call["env"]))
        for path in vm.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(marker, path.read_bytes(), str(path))

    def test_shell_default_stdin_is_eof(self):
        from local_live_vm import LocalVM

        self.alter_cli("print('guest output')", "sys.stdout.buffer.write(sys.stdin.buffer.read())")
        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        result = vm.shell(["read-stdin"], timeout=2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(vm.close()["eliminated"])

    def test_shell_invalid_stdin_rejected_before_process_spawn(self):
        from local_live_vm import LocalVM

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        for payload in ("text", bytearray(b"x"), memoryview(b"x"), 1, False, b"x" * 65537):
            with self.subTest(payload_type=type(payload).__name__):
                with mock.patch("local_live_vm.subprocess.run") as run:
                    with self.assertRaisesRegex(ValueError, "stdin.*bytes.*65536"):
                        vm.shell(["read-stdin"], timeout=2, stdin=payload)
                    run.assert_not_called()
        self.assertTrue(vm.close()["eliminated"])

    def test_shell_stdin_preserves_nonzero_exit_and_timeout(self):
        from local_live_vm import LocalVM

        self.alter_cli(
            "print('guest output')",
            "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
            "    if args[-1] == 'slow': time.sleep(60)\n"
            "    raise SystemExit(7)",
        )
        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        result = vm.shell(["fail"], timeout=2, stdin=b"synthetic-input")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"synthetic-input")
        with self.assertRaises(subprocess.TimeoutExpired):
            vm.shell(["slow"], timeout=0.2, stdin=b"synthetic-input")
        with mock.patch("local_live_vm.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "exceeds VM lease"):
                vm.shell(["fail"], timeout=31, stdin=b"synthetic-input")
            run.assert_not_called()
        self.assertTrue(vm.close()["eliminated"])

    def test_owned_vm_lifecycle_and_guest_exit_are_distinct(self):
        self.assertIsNotNone(importlib.util.find_spec("local_live_vm"), "local VM supervisor is missing")
        from local_live_vm import LocalVM

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=30, command_seconds=5)
        self.addCleanup(vm.close)
        self.assertEqual(vm.shell(["true"], timeout=2).returncode, 0)
        self.assertTrue((vm.lima_home / vm.name).exists(), "guest exit must not imply VM deletion")
        result = vm.close()
        self.assertEqual(result["status"], "closed")
        self.assertTrue(result["eliminated"])
        self.assertFalse((vm.lima_home / vm.name).exists())
        self.assertEqual(vm.close(), result)
        config = json.loads((vm.root / "lima.yaml").read_text())
        self.assertEqual(
            config["mounts"], [{"location": str(vm.share), "mountPoint": "/mnt/ea-share", "writable": True}]
        )
        self.assertFalse(config["ssh"]["loadDotSSHPubKeys"])
        self.assertFalse(config["ssh"]["forwardAgent"])
        self.assertFalse(config["propagateProxyEnv"])
        calls = [json.loads(s) for s in (vm.root / "calls.jsonl").read_text().splitlines()]
        self.assertTrue(all(c["env"]["LIMA_HOME"] == str(vm.lima_home) for c in calls))
        self.assertTrue(all("SSH_AUTH_SOCK" not in c["env"] for c in calls))
        self.assertEqual(calls[-1]["args"], ["list", "--json"])

    def alter_cli(self, old, new):
        source = self.cli.read_text()
        self.assertIn(old, source)
        self.cli.write_text(source.replace(old, new))

    def wait_for(self, path):
        end = time.monotonic() + 12
        while not path.exists():
            self.assertLess(time.monotonic(), end, f"timed out waiting for {path.name}")
            time.sleep(0.05)
        return path

    def test_late_detached_creation_after_failed_start_stays_uncertain(self):
        original_cli = self.cli.read_text()
        for index, (mode, ending) in enumerate(
            (
                ("nonzero", "raise SystemExit(7)"),
                ("timeout", "time.sleep(60)"),
                ("signal", "os.kill(os.getpid(), 9)"),
                ("empty_success", "raise SystemExit(0)"),
            )
        ):
            with self.subTest(mode=mode):
                self.cli.write_text(original_cli)
                self.assert_late_creation_stays_uncertain(self.base / str(index), ending)

    def assert_late_creation_stays_uncertain(self, root, ending):
        from local_live_vm import LocalVM

        # Reproduce response loss with creation deliberately after the receipt,
        # not a scheduler-dependent sleep. The detached creator self-expires.
        creator = """
import pathlib, sys, time
root, instance = map(pathlib.Path, sys.argv[1:])
end = time.monotonic() + 10
while not (root / 'result.json').exists():
    if time.monotonic() >= end:
        raise SystemExit(1)
    time.sleep(0.02)
instance.mkdir()
(instance / 'status').write_text('Running')
(root / 'late-created').touch()
"""
        self.alter_cli(
            "instance.mkdir()",
            "import subprocess\n"
            f"    subprocess.Popen([sys.executable, '-c', {creator!r}, str(root), str(instance)], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
            "start_new_session=True)\n"
            f"    {ending}\n"
            "    instance.mkdir()",
        )
        with self.assertRaises(RuntimeError):
            LocalVM.create(root, limactl=self.cli, job_seconds=20, command_seconds=5, start_seconds=2)
        self.wait_for(root / "late-created")
        result = json.loads((root / "result.json").read_text())
        manifest = json.loads((root / "ownership.json").read_text())
        self.assertEqual((root / "lima" / manifest["name"] / "status").read_text(), "Running")
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["eliminated"])
        calls = [json.loads(s)["args"][0] for s in (root / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(calls.count("start"), 1)
        self.assertIn("stop", calls)
        self.assertIn("delete", calls)

    def test_lost_start_response_cleans_up_but_stays_uncertain(self):
        from local_live_vm import LocalVM

        self.alter_cli(
            "(instance / 'status').write_text('Running')",
            "(instance / 'status').write_text('Running'); raise SystemExit(7)",
        )
        root = self.base / "job"
        with self.assertRaises(RuntimeError):
            LocalVM.create(root, limactl=self.cli, job_seconds=20, command_seconds=5)
        result = json.loads((root / "result.json").read_text())
        self.assertFalse(result["eliminated"])
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["reason"], "start_failed")
        manifest = json.loads((root / "ownership.json").read_text())
        self.assertFalse((root / "lima" / manifest["name"]).exists())

    def test_owner_sigkill_during_start_or_after_start(self):
        original_cli = self.cli.read_text()
        for during_start in (True, False):
            with self.subTest(during_start=during_start):
                self.cli.write_text(original_cli)
                root = self.base / ("starting" if during_start else "running")
                if during_start:
                    self.alter_cli(
                        "(instance / 'status').write_text('Running')",
                        "(instance / 'status').write_text('Running')\n"
                        "    end = time.monotonic() + 10\n"
                        "    while not (root / 'start-release').exists():\n"
                        "        if time.monotonic() >= end: raise SystemExit(9)\n"
                        "        time.sleep(0.02)\n"
                        "    (root / 'start-completed').touch()",
                    )
                code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from local_live_vm import LocalVM
vm = LocalVM.create(Path(sys.argv[2]), limactl=Path(sys.argv[3]), job_seconds=20, command_seconds=5, start_seconds=8)
(vm.root / 'owner-ready').touch()
# Deliberately keep a descendant alive with an inherited IPC descriptor: EOF
# alone cannot identify the coordinator's death.
import subprocess
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'], pass_fds=(vm.channel.fileno(),))
time.sleep(60)
"""
                owner = subprocess.Popen(
                    [sys.executable, "-c", code, str(ROOT / "tools"), str(root), str(self.cli)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                try:
                    self.wait_for(root / "ownership.json")
                    manifest = json.loads((root / "ownership.json").read_text())
                    instance = root / "lima" / manifest["name"]
                    self.wait_for(instance / "status")
                    if not during_start:
                        self.wait_for(root / "owner-ready")
                    sentinel = root / "lima" / "unrelated"
                    sentinel.mkdir()
                    (sentinel / "status").write_text("Running")
                    os.kill(owner.pid, signal.SIGKILL)
                    owner.wait(timeout=3)
                    if during_start:
                        try:
                            time.sleep(0.3)
                            self.assertFalse((root / "result.json").exists(), "start must settle before teardown")
                        finally:
                            (root / "start-release").touch()
                    result = json.loads(self.wait_for(root / "result.json").read_text())
                    self.assertTrue(result["eliminated"])
                    self.assertEqual(result["reason"], "owner_died")
                    if during_start:
                        self.assertTrue((root / "start-completed").exists())
                        self.assertFalse((root / "running.json").exists(), "dead owner must not receive a lease")
                    self.assertFalse(instance.exists())
                    self.assertEqual((sentinel / "status").read_text(), "Running")
                    calls = [json.loads(s)["args"][0] for s in (root / "calls.jsonl").read_text().splitlines()]
                    self.assertEqual(calls.count("start"), 1)
                    self.assertEqual(calls.count("stop"), 1)
                    self.assertEqual(calls.count("delete"), 1)
                finally:
                    if owner.poll() is None:
                        owner.kill()
                        owner.wait()

    def test_unsuccessful_start_is_uncertain_even_after_exact_cleanup(self):
        from local_live_vm import LocalVM

        original_cli = self.cli.read_text()
        for mode, ending in (("timeout", "time.sleep(60)"), ("signal", "os.kill(os.getpid(), 9)")):
            with self.subTest(mode=mode):
                self.cli.write_text(original_cli)
                self.alter_cli(
                    "(instance / 'status').write_text('Running')",
                    f"(instance / 'status').write_text('Running'); {ending}",
                )
                root = self.base / mode
                started = time.monotonic()
                with self.assertRaises(RuntimeError):
                    LocalVM.create(root, limactl=self.cli, job_seconds=20, command_seconds=5, start_seconds=2)
                self.assertLess(time.monotonic() - started, 10)
                result = json.loads((root / "result.json").read_text())
                self.assertEqual(result["status"], "uncertain")
                self.assertFalse(result["eliminated"])
                manifest = json.loads((root / "ownership.json").read_text())
                self.assertFalse((root / "lima" / manifest["name"]).exists())
                calls = [json.loads(s)["args"][0] for s in (root / "calls.jsonl").read_text().splitlines()]
                self.assertEqual(calls.count("start"), 1)
                self.assertEqual(calls.count("stop"), 1)
                self.assertEqual(calls.count("delete"), 1)

    def test_owner_death_before_admission_never_runs_lima(self):
        root = self.base / "job"
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from local_live_vm import LocalVM
receive = LocalVM._receive
def pause_before_admission(self, expected, timeout):
    receive(self, expected, timeout)
    if expected == b'R':
        (self.root / 'before-admission').touch()
        time.sleep(60)
LocalVM._receive = pause_before_admission
LocalVM.create(Path(sys.argv[2]), limactl=Path(sys.argv[3]), job_seconds=20, command_seconds=5)
"""
        owner = subprocess.Popen(
            [sys.executable, "-c", code, str(ROOT / "tools"), str(root), str(self.cli)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            self.wait_for(root / "before-admission")
            owner.kill()
            owner.wait(timeout=3)
            result = json.loads(self.wait_for(root / "result.json").read_text())
            self.assertFalse(result["eliminated"])
            self.assertEqual(result["status"], "uncertain")
            self.assertFalse((root / "calls.jsonl").exists())
            self.assertEqual(list((root / "lima").iterdir()), [])
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=3)

    def test_hard_deadline_disposes_without_cooperation(self):
        from local_live_vm import LocalVM

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=5, command_seconds=5)
        self.addCleanup(vm.close)
        result = json.loads(self.wait_for(vm.root / "result.json").read_text())
        self.assertEqual(result["reason"], "deadline")
        self.assertTrue(result["eliminated"])
        self.assertFalse((vm.lima_home / vm.name).exists())

    def test_delete_success_without_absence_is_uncertain(self):
        from local_live_vm import LocalVM

        self.alter_cli("(instance / 'status').unlink()\n        instance.rmdir()", "pass")
        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=20, command_seconds=5)
        result = vm.close()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["eliminated"])

    def test_invalid_inventory_never_establishes_settlement_or_elimination(self):
        from local_live_vm import LocalVM

        original_cli = self.cli.read_text()
        invalid = {
            "nonzero": "raise SystemExit(7)",
            "malformed": "print('not JSON'); raise SystemExit(0)",
            "truncated": "print('{\"name\":'); raise SystemExit(0)",
            # A valid first record must not hide data beyond the read limit.
            "oversized": "m = json.loads((root / 'ownership.json').read_text()); "
            "print(json.dumps({'name': m['name'], 'dir': str(home / m['name']), "
            "'status': (home / m['name'] / 'status').read_text()})); "
            "print(' ' * 1048576 + '{'); raise SystemExit(0)",
        }
        for phase_index, phase in enumerate(("start", "cleanup")):
            for mode_index, (mode, response) in enumerate(invalid.items()):
                with self.subTest(phase=phase, mode=mode):
                    self.cli.write_text(original_cli)
                    self.alter_cli(
                        "elif args[0] == 'stop':",
                        "elif args[0] == 'stop':\n    (root / 'stop-attempted').touch()",
                    )
                    condition = "not " if phase == "start" else ""
                    self.alter_cli(
                        "elif args[0] == 'list':",
                        "elif args[0] == 'list':\n"
                        f"    if {condition}(root / 'stop-attempted').exists() "
                        "and not (root / 'invalid-served').exists():\n"
                        "        (root / 'invalid-served').touch()\n"
                        f"        {response}",
                    )
                    root = self.base / f"{phase_index}-{mode_index}"
                    try:
                        vm = LocalVM.create(root, limactl=self.cli, job_seconds=20, command_seconds=5)
                    except RuntimeError:
                        self.assertEqual(phase, "start")
                    else:
                        vm.close()
                    self.assertTrue((root / "invalid-served").exists())
                    result = json.loads((root / "result.json").read_text())
                    self.assertEqual(result["status"], "uncertain")
                    self.assertFalse(result["eliminated"])

    def test_inventory_cannot_hide_an_existing_instance(self):
        from local_live_vm import LocalVM

        self.alter_cli("elif args[0] == 'stop':", "elif args[0] == 'stop':\n    (root / 'hide-inventory').touch()")
        self.alter_cli(
            "for instance in home.iterdir():",
            "for instance in ([] if (root / 'hide-inventory').exists() else home.iterdir()):",
        )
        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=20, command_seconds=5)
        result = vm.close()
        self.assertFalse(result["eliminated"])
        calls = [json.loads(s)["args"][0] for s in (vm.root / "calls.jsonl").read_text().splitlines()]
        self.assertNotIn("delete", calls)

    def test_shell_refuses_replaced_private_home(self):
        from local_live_vm import LocalVM

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=20, command_seconds=5)
        moved = vm.root / "old-lima"
        vm.lima_home.rename(moved)
        vm.lima_home.mkdir()
        try:
            with self.assertRaises(ValueError):
                vm.shell(["true"], timeout=2)
        finally:
            vm.lima_home.rmdir()
            moved.rename(vm.lima_home)
            vm.close()

    def test_stop_timeout_is_bounded_and_uncertain(self):
        from local_live_vm import LocalVM

        self.alter_cli("elif args[0] == 'stop':", "elif args[0] == 'stop':\n    time.sleep(60)")
        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=20, command_seconds=2)
        started = time.monotonic()
        result = vm.close()
        self.assertLess(time.monotonic() - started, 6)
        self.assertFalse(result["eliminated"])
        self.assertTrue((vm.lima_home / vm.name).exists())

    def test_changed_control_identity_fails_closed(self):
        from local_live_vm import LocalVM

        vm = LocalVM.create(self.base / "job", limactl=self.cli, job_seconds=20, command_seconds=5)
        (vm.root / "lima.yaml").write_text("{}")
        result = vm.close()
        self.assertFalse(result["eliminated"])
        self.assertTrue((vm.lima_home / vm.name).exists())

    def test_reject_existing_root_without_touching_it(self):
        from local_live_vm import LocalVM

        root = self.base / "existing"
        root.mkdir()
        (root / "sentinel").write_text("keep")
        with self.assertRaises(FileExistsError):
            LocalVM.create(root, limactl=self.cli)
        self.assertEqual(list(root.iterdir()), [root / "sentinel"])

    def test_wrong_version_never_runs_mutating_commands(self):
        from local_live_vm import LocalVM

        self.alter_cli("limactl version 2.3.0", "limactl version 2.4.0")
        root = self.base / "job"
        with self.assertRaises(RuntimeError):
            LocalVM.create(root, limactl=self.cli, job_seconds=20, command_seconds=5)
        calls = [json.loads(s)["args"] for s in (root / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(calls, [["--version"]])


if __name__ == "__main__":
    unittest.main()
