#!/usr/bin/env python3
"""Unit tests for scripts/triage.py. Run: python3 triage_unit_test.py   (SCRIPTS_DIR overrides the code under test)"""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.environ.get("SCRIPTS_DIR", os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)
import triage  # noqa: E402

CFG = triage.load_rules(os.path.join(SCRIPTS, "..", "reference", "risk-rules.json"))


def diff(path, *added, removed=0):
    body = "".join(f"+{a}\n" for a in added) + "-x\n" * removed
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


def run(text):
    return triage.triage(triage.parse_diff(text), CFG)


class Tiers(unittest.TestCase):
    def test_empty_change_is_t0(self):
        self.assertEqual(run("")["tier"], "T0")

    def test_docs_and_tests_only_is_t0(self):
        r = run(diff("README.md", "hi") + diff("tests/a_test.py", "password = 'x'") + diff("docs/x.sql", "drop table t"))
        self.assertEqual((r["tier"], r["matches"]), ("T0", []))

    def test_plain_source_is_t1(self):
        self.assertEqual(run(diff("src/util.py", "x = 1"))["tier"], "T1")

    def test_auth_path_is_t3(self):
        r = run(diff("src/auth/login.py", "x = 1"))
        self.assertEqual((r["tier"], r["factors"]["boundary"]), ("T3", 3))

    def test_material_in_two_factors_bumps_one_tier(self):
        # public contract (impact 2) + error swallow (detectability 2) -> 2 material factors -> T3
        r = run(diff("api/openapi.yaml", "a: b") + diff("src/x.py", "except Exception: pass"))
        self.assertEqual(r["factors"]["impact"], 2)
        self.assertEqual(r["factors"]["detectability"], 2)
        self.assertEqual(r["tier"], "T3")

    def test_single_material_factor_stays_t2(self):
        self.assertEqual(run(diff("api/openapi.yaml", "a: b"))["tier"], "T2")

    def test_bump_caps_at_t3(self):
        r = run(diff("src/auth/a.py", "x") + diff("db/migrations/1.sql", "alter table t add c int"))
        self.assertEqual(r["tier"], "T3")

    def test_content_rule_needs_added_line_not_removed(self):
        t = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-DROP TABLE t\n+ok = 1\n"
        self.assertEqual(run(t)["tier"], "T1")

    def test_content_rule_fires_on_added_line(self):
        self.assertEqual(run(diff("src/a.py", "cur.execute('DROP TABLE t')"))["factors"]["recovery"], 3)

    def test_swallow_rule_spans_adjacent_lines_and_single_line(self):
        self.assertEqual(run(diff("src/a.py", "try:", "    f()", "except Exception:", "    pass"))["factors"]["detectability"], 2)
        self.assertEqual(run(diff("src/a.py", "except ValueError: pass"))["factors"]["detectability"], 2)
        self.assertEqual(run(diff("src/a.py", "except ValueError:", "    log(e)", "    pass_through()"))["factors"]["detectability"], 0)

    def test_path_and_added_must_both_match(self):
        cfg = {"rules": [{"id": "r", "domain": "d", "factor": "impact", "score": 3, "path": "\\.py$", "added": "BOOM", "why": "w"}],
               "tiers": CFG["tiers"]}
        self.assertEqual(triage.triage(triage.parse_diff(diff("a.py", "fine")), cfg)["tier"], "T0")
        self.assertEqual(triage.triage(triage.parse_diff(diff("a.txt", "BOOM")), cfg)["tier"], "T0")
        self.assertEqual(triage.triage(triage.parse_diff(diff("a.py", "BOOM")), cfg)["tier"], "T3")

    def test_required_matches_tier(self):
        self.assertEqual(run(diff("src/auth/a.py", "x"))["required"]["finder_passes"], 2)
        self.assertEqual(run(diff("src/u.py", "x"))["required"]["finder_passes"], 1)


class Counting(unittest.TestCase):
    def test_changed_lines_count_adds_and_removes_not_headers(self):
        r = run(diff("src/u.py", "a", "b", removed=3) + diff("README.md", "c"))
        self.assertEqual((r["changed_lines"], r["files"]), (6, 2))


class Split(unittest.TestCase):
    def test_mixed_risky_domains_suggest_split(self):
        r = run(diff("src/auth/a.py", "x") + diff("db/migrations/1.sql", "alter table t add c int"))
        self.assertTrue(r["split"]["suggested"])
        self.assertEqual(sorted(r["split"]["groups"]), ["data", "security"])

    def test_one_risky_domain_no_split(self):
        self.assertFalse(run(diff("src/auth/a.py", "x"))["split"]["suggested"])

    def test_large_risky_change_suggests_split(self):
        r = run(diff("api/openapi.yaml", *["l"] * 801))
        self.assertTrue(r["split"]["suggested"])

    def test_large_low_risk_change_does_not(self):
        self.assertFalse(run(diff("src/u.py", *["l"] * 900))["split"]["suggested"])


class Determinism(unittest.TestCase):
    def test_file_order_does_not_matter(self):
        a, b = diff("src/auth/a.py", "x"), diff("api/openapi.yaml", "y")
        self.assertEqual(json.dumps(run(a + b), sort_keys=True), json.dumps(run(b + a), sort_keys=True))

    def test_cli_is_byte_identical_across_runs(self):
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as f:
            f.write(diff("src/auth/a.py", "x") + diff("README.md", "y"))
        try:
            cmd = [sys.executable, os.path.join(SCRIPTS, "triage.py"), "--diff-file", f.name, "--json"]
            outs = {subprocess.run(cmd, capture_output=True, text=True, check=True).stdout for _ in range(3)}
            self.assertEqual(len(outs), 1)
        finally:
            os.unlink(f.name)

    def test_git_base_mode(self):
        with tempfile.TemporaryDirectory() as d:
            sh = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)
            sh("git", "init", "-q", "-b", "main"); sh("git", "config", "user.email", "t@t"); sh("git", "config", "user.name", "t")
            open(os.path.join(d, "a.md"), "w").write("a\n"); sh("git", "add", "."); sh("git", "commit", "-qm", "1")
            sh("git", "checkout", "-qb", "feat")
            os.makedirs(os.path.join(d, "auth")); open(os.path.join(d, "auth", "x.py"), "w").write("x = 1\n")
            sh("git", "add", "."); sh("git", "commit", "-qm", "2")
            out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "triage.py"), "--base", "main", "--json"],
                                 cwd=d, capture_output=True, text=True, check=True).stdout
            self.assertEqual(json.loads(out)["tier"], "T3")


class RulesValidation(unittest.TestCase):
    def bad(self, rule):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"rules": [rule], "tiers": CFG["tiers"]}, f)
        try:
            with self.assertRaises(SystemExit) as cm:
                triage.load_rules(f.name)
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(f.name)

    def test_rejects_bad_rules(self):
        ok = {"id": "r", "domain": "d", "factor": "impact", "score": 1, "path": "x", "why": "w"}
        self.bad({**ok, "factor": "vibes"})
        self.bad({**ok, "score": 4})
        self.bad({**ok, "score": True})
        self.bad({**ok, "path": "("})
        self.bad({**ok, "extra": 1})
        self.bad({k: v for k, v in ok.items() if k != "path"})

    def test_shipped_rules_load(self):
        self.assertGreater(len(CFG["rules"]), 5)


if __name__ == "__main__":
    unittest.main()
