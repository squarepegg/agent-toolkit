#!/usr/bin/env python3
"""Unit tests for the check broker (scripts/k8s/broker.py) and its role-side shim (scripts/sdlc-check): request
validation, workspace sync, the in-pod delete, and the file protocol against a fake broker. No cluster."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "k8s"))
_spec = importlib.util.spec_from_file_location("broker", SCRIPTS / "k8s/broker.py")
assert _spec is not None and _spec.loader is not None
broker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(broker)


class Requests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Path(tempfile.mkdtemp())
        (self.ws / ".sdlc-remote/req").mkdir(parents=True)

    def req(self, data, name="r.json") -> Path:
        p = self.ws / ".sdlc-remote/req" / name
        p.write_text(data if isinstance(data, str) else json.dumps(data))
        return p

    def test_a_valid_request(self) -> None:
        r = broker.parse_request(self.req({"id": "abc_1", "cmd": "./gradlew test", "cwd": "sub/dir"}), self.ws)
        self.assertEqual(r, {"id": "abc_1", "cmd": "./gradlew test", "cwd": "sub/dir"})

    def test_hostile_requests_are_refused(self) -> None:
        for what, data in {
            "id with a path": {"id": "../x", "cmd": "true"},
            "empty command": {"id": "a", "cmd": "  "},
            "cwd escapes": {"id": "a", "cmd": "true", "cwd": "../../etc"},
            "absolute cwd": {"id": "a", "cmd": "true", "cwd": "/etc"},
            "huge command": {"id": "a", "cmd": "x" * (broker.MAX_CMD + 1)},
            "not an object": [1, 2],
        }.items():
            with self.subTest(what), self.assertRaises(ValueError):
                broker.parse_request(self.req(data), self.ws)
        with self.assertRaises(ValueError):
            broker.parse_request(self.req("{" + " " * broker.MAX_REQUEST + "}"), self.ws)

    def test_a_linked_request_is_refused(self) -> None:
        target = self.ws / "elsewhere.json"
        target.write_text(json.dumps({"id": "a", "cmd": "true"}))
        link = self.ws / ".sdlc-remote/req/l.json"
        os.symlink(target, link)
        with self.assertRaises(ValueError):
            broker.parse_request(link, self.ws)


class Sync(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Path(tempfile.mkdtemp())
        (self.ws / "src/main").mkdir(parents=True)
        (self.ws / "src/main/A.kt").write_text("a")
        (self.ws / "build/classes").mkdir(parents=True)
        (self.ws / "build/classes/A.class").write_text("c")
        (self.ws / ".sdlc-remote/req").mkdir(parents=True)
        (self.ws / ".sdlc/build").mkdir(parents=True)
        (self.ws / ".sdlc/build/r.md").write_text("r")
        (self.ws / ".sdlc/tests.lock").write_text("{}")

    def test_build_outputs_and_broker_files_are_not_uploaded(self) -> None:
        snap = broker.snapshot(self.ws)
        self.assertIn("src/main/A.kt", snap)
        self.assertIn(".sdlc/tests.lock", snap, "inputs under .sdlc/ are uploaded")
        for skipped in ("build", "build/classes/A.class", ".sdlc-remote", ".sdlc/build/r.md"):
            self.assertNotIn(skipped, snap)

    def test_only_changes_are_sent_and_deletions_go_deepest_first(self) -> None:
        old = broker.snapshot(self.ws)
        self.assertEqual(broker.delta(old, old), ([], []))
        time.sleep(0.01)
        (self.ws / "src/main/A.kt").write_text("a2")
        (self.ws / "src/main/B.kt").write_text("b")
        changed, deleted = broker.delta(old, broker.snapshot(self.ws))
        self.assertEqual(changed, ["src/main/A.kt", "src/main/B.kt"])
        self.assertEqual(deleted, [])
        mid = broker.snapshot(self.ws)
        (self.ws / "src/main/A.kt").unlink()
        (self.ws / "src/main/B.kt").unlink()
        (self.ws / "src/main").rmdir()
        _, deleted = broker.delta(mid, broker.snapshot(self.ws))
        self.assertEqual(deleted[-1], "src/main", "a directory after its contents")

    def test_links_are_sent_as_links_not_followed(self) -> None:
        os.symlink("/etc/passwd", self.ws / "src/leak")
        snap = broker.snapshot(self.ws)
        self.assertEqual(snap["src/leak"][0], "l")
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(broker.pack_entries(self.ws, ["src/leak"]))) as t:
            m = t.getmembers()[0]
        self.assertTrue(m.issym(), "the archive holds the link, never /etc/passwd's contents")

    def test_the_in_pod_delete_stays_in_the_workspace(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "victim.txt"
        outside.write_text("keep")
        rel_out = os.path.relpath(outside, self.ws)
        subprocess.run([sys.executable, "-I", "-S", "-c", broker.DELETE_IN_POD, str(self.ws),
                        json.dumps(["src/main/A.kt", rel_out, "/etc/hosts"])], check=True)
        self.assertFalse((self.ws / "src/main/A.kt").exists())
        self.assertTrue(outside.exists(), "a path outside the workspace is never deleted")


class Shim(unittest.TestCase):
    """sdlc-check against a fake broker that answers like the real one."""

    def setUp(self) -> None:
        self.ws = Path(tempfile.mkdtemp())
        for d in ("req", "res"):
            (self.ws / ".sdlc-remote" / d).mkdir(parents=True)

    def fake_broker(self, exit_code: int, output: str) -> threading.Thread:
        def serve() -> None:
            rem = self.ws / ".sdlc-remote"
            for _ in range(200):
                reqs = [p for p in (rem / "req").glob("*.json") if not p.name.startswith(".")]
                if reqs:
                    r = broker.parse_request(reqs[0], self.ws)
                    reqs[0].unlink()
                    (rem / "res" / f"{r['id']}.log").write_text(output + f"\ncmd={r['cmd']} cwd={r['cwd']}\n")
                    broker.write_response(self.ws, r["id"], {"exit": exit_code})
                    return
                time.sleep(0.05)
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return t

    def shim(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "sdlc-check"), *args], cwd=cwd or self.ws,
                              capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, SDLC_REMOTE_START_TIMEOUT="5"))

    def test_output_and_exit_code_come_back(self) -> None:
        (self.ws / ".sdlc-remote/ready").write_text("pod")
        (self.ws / "sub").mkdir()
        t = self.fake_broker(3, "BUILD FAILED")
        r = self.shim("./gradlew", "test", "--tests", "a b", cwd=self.ws / "sub")
        t.join()
        self.assertEqual(r.returncode, 3)
        self.assertIn("BUILD FAILED", r.stdout)
        self.assertIn("cmd=./gradlew test --tests 'a b' cwd=sub", r.stdout, "arguments are quoted, cwd is relative")

    def test_no_runner_is_97_never_a_local_run(self) -> None:
        (self.ws / ".sdlc-remote/unavailable").write_text("backend unusable: no isolation probe")
        r = self.shim("touch", str(self.ws / "ran-locally"))
        self.assertEqual(r.returncode, 97)
        self.assertFalse((self.ws / "ran-locally").exists())
        self.assertIn("no runner", r.stderr)


if __name__ == "__main__":
    unittest.main()
