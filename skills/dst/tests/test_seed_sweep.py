"""Tests for scripts/seed-sweep.sh using a fake test command. Run: python3 skills/dst/tests/test_seed_sweep.py"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed-sweep.sh"

# Logs "<start> <count> <args>" per invocation; fails when the chunk contains FAIL_SEED, writing a JUnit XML report.
FAKE = r"""#!/usr/bin/env bash
echo "$SIM_SEED_START $SIM_SEEDS $*" >> "$FAKE_LOG"
if [[ -n "${FAIL_SEED:-}" && "$FAIL_SEED" -ge "$SIM_SEED_START" && "$FAIL_SEED" -lt $((SIM_SEED_START + SIM_SEEDS)) ]]; then
  mkdir -p "$FAKE_RESULTS/build/test-results/test"
  echo "<failure message=\"invariant violated on seed=$FAIL_SEED at step=3\"/>" > "$FAKE_RESULTS/build/test-results/test/TEST-x.xml"
  exit "${FAKE_EXIT:-1}"
fi
exit 0
"""


class SeedSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.log, self.results = t / "calls.log", t / "results"
        self.results.mkdir()
        self.fake = t / "fake.sh"
        self.fake.write_text(FAKE)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)

    def sweep(self, *args, env=None, command=True):
        e = {**os.environ, "FAKE_LOG": str(self.log), "FAKE_RESULTS": str(self.results), **(env or {})}
        for k in ("SIM_SEED", "SIM_SEEDS", "SIM_SEED_START"):
            e.pop(k, None)
        cmd = ["bash", str(SCRIPT), *args]
        if command:
            cmd += ["--", str(self.fake), "marker-arg"]
        return subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=self.tmp.name)

    def calls(self):
        return [l.split()[:2] for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_chunks_and_ranges(self):
        r = self.sweep("--start", "100", "--total", "25", "--chunk", "10", "--results", str(self.results))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [["100", "10"], ["110", "10"], ["120", "5"]])
        self.assertIn("seeds 120..124", r.stderr)
        self.assertIn("all 25 seeds (100..124) passed", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_last_chunk_never_overshoots(self):
        self.sweep("--start", "1", "--total", "20", "--chunk", "10", "--results", str(self.results))
        self.assertEqual(self.calls(), [["1", "10"], ["11", "10"]])

    def test_defaults_and_args_passthrough(self):
        r = self.sweep("--total", "5", "--results", str(self.results))
        self.assertEqual(self.calls(), [["1", "5"]])
        self.assertIn("marker-arg", self.log.read_text())

    def test_stops_at_first_failing_chunk_and_reports_seed(self):
        r = self.sweep("--total", "40", "--chunk", "10", "--results", str(self.results), env={"FAIL_SEED": "13"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls(), [["1", "10"], ["11", "10"]])  # chunks 21.. never run
        self.assertEqual(r.stdout.strip(), "13")
        self.assertIn("FAILED in chunk 11..20", r.stderr)
        self.assertIn("first failing seed: 13", r.stderr)
        self.assertIn("repro: SIM_SEED=13 ", r.stderr)
        self.assertIn("marker-arg", r.stderr.split("repro:")[1])

    def test_stale_reports_from_earlier_chunk_are_ignored(self):
        old = self.results / "build" / "test-results" / "test"
        old.mkdir(parents=True)
        (old / "TEST-old.xml").write_text('seed=999')
        os.utime(old / "TEST-old.xml", (1, 1))
        r = self.sweep("--total", "10", "--chunk", "10", "--results", str(self.results), env={"FAIL_SEED": "4", "FAKE_EXIT": "1"})
        self.assertEqual(r.stdout.strip(), "4")

    def test_failure_without_report_says_so(self):
        r = self.sweep("--total", "10", "--chunk", "10", "--results", str(self.results),
                       env={"FAIL_SEED": "4", "FAKE_RESULTS": str(Path(self.tmp.name) / "elsewhere")})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("no seed in reports", r.stderr)

    def test_usage_errors_exit_2(self):
        for args, kw in ((["--chunk", "0"], {}), (["--chunk", "10001"], {}), (["--chunk", "x"], {}),
                         (["--bogus"], {}), ([], {"command": False}), (["--total", "5"], {"command": False})):
            r = self.sweep(*args, **kw)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
        self.assertFalse(self.log.exists())

    def test_help_exit_2(self):
        r = self.sweep("-h", command=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("seed-sweep.sh", r.stderr)

    def test_chunk_10000_allowed(self):
        r = self.sweep("--total", "3", "--chunk", "10000", "--results", str(self.results))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [["1", "3"]])


if __name__ == "__main__":
    unittest.main()
