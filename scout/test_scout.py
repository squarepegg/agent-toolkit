#!/usr/bin/env python3
"""Offline tests for scout/scout.py (fixtures, no network). python3 scout/test_scout.py"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout  # noqa: E402

GH = json.dumps({"items": [
    {"html_url": "https://github.com/A/Skills", "full_name": "A/Skills", "description": "Agent skill pack with evals", "stargazers_count": 500, "pushed_at": "2026-11-10T00:00:00Z"},
    {"html_url": "https://github.com/b/unrelated", "full_name": "b/unrelated", "description": "A theme", "stargazers_count": 9000, "pushed_at": "2026-11-10T00:00:00Z"}]})
HN = json.dumps({"hits": [{"url": "https://www.github.com/a/skills.git", "title": "Show HN: skills", "points": 80, "created_at": "2026-11-11"},
                          {"url": None, "objectID": "42", "title": "Mutation testing for agents", "points": 120, "created_at": "2026-11-12"}]})
ARXIV = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2611.01234v2</id>
<title>Deterministic   evals for coding agents</title><summary>We benchmark.</summary><published>2026-11-09T00:00:00Z</published></entry></feed>"""


class Scout(unittest.TestCase):
    def items(self):
        return scout.parse_github(GH) + scout.parse_hn(HN) + scout.parse_arxiv(ARXIV)

    def test_norm_merges_variants(self):
        self.assertEqual(scout.norm("https://www.github.com/a/skills.git/"), scout.norm("https://github.com/A/Skills"))
        self.assertEqual(scout.norm("http://arxiv.org/abs/2611.01234v2"), scout.norm("https://arxiv.org/pdf/2611.01234.pdf"))

    def test_rank_dedupes_filters_and_orders(self):
        r = scout.rank(self.items(), ["skill", "eval", "mutation", "deterministic"], set())
        urls = [i["url"] for i in r]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertNotIn("https://github.com/b/unrelated", urls, "popular but irrelevant must be dropped")
        self.assertEqual(urls[0], "https://github.com/a/skills")
        self.assertEqual(r, scout.rank(list(reversed(self.items())), ["skill", "eval", "mutation", "deterministic"], set()), "order-independent")

    def test_seen_files_suppress(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "neg.md")
            open(p, "w").write("- 2026 | x (https://github.com/a/skills) | NO_DELTA\nsee arxiv.org: https://arxiv.org/abs/2611.01234\n")
            r = scout.rank(self.items(), ["skill", "eval", "mutation", "deterministic"], scout.seen_urls([p]))
            self.assertEqual([i["source"] for i in r], ["hn"])

    def test_keywords_match_at_word_start(self):
        it = [{"url": "https://x.dev/1", "title": "The latest release", "summary": "", "source": "hn", "pop": 1}]
        self.assertEqual(scout.rank(it, ["test"], set()), [])

    def test_digest_renders(self):
        md = scout.digest(scout.rank(self.items(), ["skill"], set()), 5, "2026-11-07", ["hn 'x': URLError"])
        self.assertIn("| score |", md)
        self.assertIn("Source errors", md)
        self.assertIn("nothing new", scout.digest([], 5, "2026-11-07", []))


if __name__ == "__main__":
    unittest.main()
