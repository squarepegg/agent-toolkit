#!/usr/bin/env python3
"""Deterministic pins for skills/dst/evals/probes.json (free, no model; CI runs it).

Each probe has a pass and a fail fixture in evals/probe-fixtures.json. The grader (scripts/skill_ab.missing) must accept
the pass text and reject the fail text, so a probe whose `expect` groups are too loose or too strict is caught here,
not after a paid run. Every probe must have a fixture, and every `expect` group must be needed (dropping any single
group from the pass text's grading must change the verdict for at least the fail text).
"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import skill_ab  # noqa: E402

PROBES = json.load(open(os.path.join(HERE, "..", "evals", "probes.json")))
FIXTURES = json.load(open(os.path.join(HERE, "..", "evals", "probe-fixtures.json")))


class ProbeGraders(unittest.TestCase):
    def test_every_probe_has_a_fixture_and_no_fixture_is_orphaned(self):
        self.assertEqual(sorted(p["id"] for p in PROBES), sorted(FIXTURES))

    def test_pass_text_passes_and_fail_text_fails(self):
        for p in PROBES:
            fx = FIXTURES[p["id"]]
            self.assertEqual(skill_ab.missing(p, fx["pass"]), [], f"{p['id']}: pass fixture should satisfy every group")
            self.assertNotEqual(skill_ab.missing(p, fx["fail"]), [], f"{p['id']}: fail fixture should miss a group")

    def test_each_group_is_load_bearing(self):
        """removing the text that satisfies one group must make the grader fail: no group is dead weight"""
        for p in PROBES:
            pass_text = FIXTURES[p["id"]]["pass"].lower()
            for g in p["expect"]:
                stripped = pass_text
                for alt in g: stripped = stripped.replace(alt.lower(), "")
                self.assertTrue(skill_ab.missing(p, stripped), f"{p['id']}: group {g[0]!r} never constrains anything")

    def test_split_labels_and_ids(self):
        ids = [p["id"] for p in PROBES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({p.get("split", "dev") for p in PROBES} <= {"dev", "heldout"})
        self.assertGreaterEqual(sum(p.get("split") == "heldout" for p in PROBES), 2)
        self.assertGreaterEqual(sum(p.get("split", "dev") == "dev" for p in PROBES), 3)

    def test_every_expect_group_is_answerable_from_the_skill(self):
        """a probe may only ask for things the skill text (SKILL.md, reference/, templates/) actually says"""
        corpus = ""
        for base in ("SKILL.md", "reference", "templates"):
            p = os.path.join(HERE, "..", base)
            files = [p] if os.path.isfile(p) else [os.path.join(dp, f) for dp, _, fs in os.walk(p) for f in fs]
            for f in sorted(files):
                try: corpus += open(f, encoding="utf-8").read().lower() + "\n"
                except UnicodeDecodeError: pass
        for p in PROBES:
            for g in p["expect"]:
                self.assertTrue(any(a.lower() in corpus for a in g), f"{p['id']}: nothing in the skill says any of {g}")


if __name__ == "__main__":
    unittest.main()
