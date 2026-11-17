#!/usr/bin/env python3
"""Deterministic pins for skills/regen/<sub>/evals/probes.json, all six sub-skills (free, no model; CI runs it).

Same contract as skills/dst/tests/test_probes.py, looped over the sub-skills: every probe has a pass and a fail fixture,
the grader (scripts/skill_ab.missing) accepts the pass text and rejects the fail text, every `expect` group is
load-bearing, and every group is answerable from that sub-skill's own text (SKILL.md, reference/, examples/, templates/).
Live runs: python3 scripts/skill_ab.py regen/<sub> (see skill_ab.py).
"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(REGEN))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import skill_ab  # noqa: E402

SUBS = ["architecture-constraints", "compaction-pass", "decision-capture", "deletion-test", "eval-tiering",
        "implementation-memory-audit"]


def load(sub, name):
    with open(os.path.join(REGEN, sub, "evals", name), encoding="utf-8") as f:
        return json.load(f)


def corpus(sub):
    out = ""
    for base in ("SKILL.md", "reference", "examples", "templates"):
        p = os.path.join(REGEN, sub, base)
        files = [p] if os.path.isfile(p) else [os.path.join(dp, f) for dp, _, fs in os.walk(p) for f in fs]
        for f in sorted(files):
            try:
                with open(f, encoding="utf-8") as fh:
                    out += fh.read().lower() + "\n"
            except UnicodeDecodeError:
                pass
    return out


class RegenProbes(unittest.TestCase):
    def test_sub_skill_list_is_complete(self):
        on_disk = sorted(d for d in os.listdir(REGEN) if os.path.isfile(os.path.join(REGEN, d, "SKILL.md")))
        self.assertEqual(on_disk, sorted(SUBS))

    def test_every_probe_has_a_fixture_and_no_fixture_is_orphaned(self):
        for sub in SUBS:
            self.assertEqual(sorted(p["id"] for p in load(sub, "probes.json")), sorted(load(sub, "probe-fixtures.json")), sub)

    def test_pass_text_passes_and_fail_text_fails(self):
        for sub in SUBS:
            fixtures = load(sub, "probe-fixtures.json")
            for p in load(sub, "probes.json"):
                fx = fixtures[p["id"]]
                self.assertEqual(skill_ab.missing(p, fx["pass"]), [], f"{p['id']}: pass fixture should satisfy every group")
                self.assertNotEqual(skill_ab.missing(p, fx["fail"]), [], f"{p['id']}: fail fixture should miss a group")

    def test_each_group_is_load_bearing(self):
        """removing the text that satisfies one group must make the grader fail: no group is dead weight"""
        for sub in SUBS:
            fixtures = load(sub, "probe-fixtures.json")
            for p in load(sub, "probes.json"):
                pass_text = fixtures[p["id"]]["pass"].lower()
                for g in p["expect"]:
                    stripped = pass_text
                    for alt in g:
                        stripped = stripped.replace(alt.lower(), "")
                    self.assertTrue(skill_ab.missing(p, stripped), f"{p['id']}: group {g[0]!r} never constrains anything")

    def test_split_labels_and_ids(self):
        ids = []
        for sub in SUBS:
            probes = load(sub, "probes.json")
            ids += [p["id"] for p in probes]
            for p in probes:
                self.assertTrue(p["q"].strip() and p["expect"] and all(g and all(isinstance(a, str) and a for a in g) for g in p["expect"]), p["id"])
            self.assertTrue({p.get("split", "dev") for p in probes} <= {"dev", "heldout"}, sub)
            self.assertGreaterEqual(sum(p.get("split") == "heldout" for p in probes), 1, sub)
            self.assertGreaterEqual(sum(p.get("split", "dev") == "dev" for p in probes), 2, sub)
        self.assertEqual(len(ids), len(set(ids)), "probe ids must be unique across the six sub-skills")

    def test_every_expect_group_is_answerable_from_the_sub_skill(self):
        """a probe may only ask for things that sub-skill's own text actually says"""
        for sub in SUBS:
            text = corpus(sub)
            for p in load(sub, "probes.json"):
                for g in p["expect"]:
                    self.assertTrue(any(a.lower() in text for a in g), f"{sub}/{p['id']}: nothing in the skill says any of {g}")


if __name__ == "__main__":
    unittest.main()
