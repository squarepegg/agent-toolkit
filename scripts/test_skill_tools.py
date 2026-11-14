#!/usr/bin/env python3
"""Unit tests for scripts/skill_ab.py grading and the eval fixtures (stdlib, no model): every probe file is valid, and
each skill's evals/answer-pass.md passes its graders while answer-fail.md fails them (pins the grader both ways).
python3 scripts/test_skill_tools.py"""
import glob, json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import skill_ab  # noqa: E402
import check_skills  # noqa: E402

PROBES = sorted(glob.glob(os.path.join(ROOT, "skills", "*", "evals", "probes.json")))


class Grader(unittest.TestCase):
    def test_all_groups_must_match_any_of_case_insensitive(self):
        p = {"expect": [["Gray failure", "grey failure"], ["fault"]]}
        self.assertEqual(skill_ab.missing(p, "GREY FAILURE and a Fault"), [])
        self.assertEqual(skill_ab.missing(p, "gray failure only"), ["fault"])
        self.assertEqual(skill_ab.missing(p, ""), ["Gray failure", "fault"])


class Probes(unittest.TestCase):
    def test_probe_files_exist_and_are_well_formed(self):
        self.assertGreaterEqual(len(PROBES), 2)
        for f in PROBES:
            for pr in json.load(open(f)):
                self.assertTrue(pr["id"] and pr["q"], f)
                self.assertTrue(pr["expect"] and all(g and all(isinstance(a, str) and a for a in g) for g in pr["expect"]), f)

    def test_fixtures_pin_grader_both_ways(self):
        for f in PROBES:
            d = os.path.dirname(f); pr = json.load(open(f))[0]
            good = open(os.path.join(d, "answer-pass.md")).read(); bad = open(os.path.join(d, "answer-fail.md")).read()
            self.assertEqual(skill_ab.missing(pr, good), [], f"{d}: answer-pass.md should pass")
            self.assertNotEqual(skill_ab.missing(pr, bad), [], f"{d}: answer-fail.md should fail")

    def test_fixtures_are_public_clean(self):
        for f in glob.glob(os.path.join(ROOT, "skills", "*", "evals", "*.md")):
            low = open(f).read().lower()
            self.assertEqual([b for b in check_skills.PUBLIC_FORBID if b in low], [], f)


class Checker(unittest.TestCase):
    def test_repo_is_clean(self):
        self.assertEqual(check_skills.check(), [])

    def test_frontmatter_parser(self):
        self.assertIsNone(check_skills.frontmatter("no fm"))
        self.assertEqual(check_skills.frontmatter("---\nname: a\ndescription: b c\n---\nx")["name"], "a")
        self.assertIn("_bad", check_skills.frontmatter("---\nname: a\n  wrapped\n---\n"))


if __name__ == "__main__":
    unittest.main()
