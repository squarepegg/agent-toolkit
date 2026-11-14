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


S2 = json.dumps({"total": 3, "data": [
    {"title": "DeepSWE: evaluating coding agents", "abstract": "A benchmark.", "citationCount": 100, "influentialCitationCount": 26,
     "publicationDate": "2026-07-08", "externalIds": {"ArXiv": "2607.07946"}, "url": "https://www.semanticscholar.org/paper/x"},
    {"title": "Mutation testing with LLMs", "abstract": None, "citationCount": 12, "influentialCitationCount": 0,
     "publicationDate": "2026-06-01", "externalIds": {}, "url": "https://www.semanticscholar.org/paper/y"},
    {"title": "no url", "citationCount": 50, "externalIds": {}}]})


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

    def test_star_growth_from_the_newest_snapshot(self):
        items = self.items()
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "seen.md")
            old = scout.snapshot([{"source": "github", "repo": "a/skills", "pop": 100}], "2026-11-01")
            new = scout.snapshot([{"source": "github", "repo": "a/skills", "pop": 430}], "2026-11-12")
            open(p, "w").write(f"issue 1\n{old}\nissue 2\n{new}\n<!-- scout-stars {{broken -->\n")
            snap = scout.load_snapshot([p])
            self.assertEqual(snap["date"], "2026-11-12")
            scout.add_growth(items, snap, "2026-11-14")
            gh = {i["repo"]: i["growth"] for i in items if i["source"] == "github"}
            self.assertEqual(gh["a/skills"], round(70 * 7 / 2))       # 500 - 430 over 2 days, scaled to a week
            self.assertIsNone(gh["b/unrelated"], "not in the snapshot: unknown, not 0")
            self.assertNotIn("growth", [k for i in items if i["source"] != "github" for k in i])
            self.assertEqual(scout.seen_urls([p]), set(), "a snapshot must never mark repos as seen")

    def test_same_day_or_missing_snapshot_gives_unknown_growth(self):
        for snap in (None, {"date": "2026-11-14", "stars": {"a/skills": 1}}):
            items = scout.add_growth(scout.parse_github(GH), snap, "2026-11-14")
            self.assertTrue(all(i["growth"] is None for i in items))

    def test_growth_outranks_a_bigger_but_static_repo(self):
        mk = lambda repo, pop, growth: {"url": f"https://github.com/{repo}", "title": "skill", "summary": "", "source": "github",
                                         "repo": repo, "pop": pop, "growth": growth}
        r = scout.rank([mk("big/static", 20000, 0), mk("small/rising", 800, 600)], ["skill"], set())
        self.assertEqual(r[0]["repo"], "small/rising")

    def test_citations_batch_lookup(self):
        calls = []
        def fake(url, headers, data):
            calls.append((url, headers, data))
            return json.dumps([{"citationCount": 40, "influentialCitationCount": 5}])
        items = scout.parse_arxiv(ARXIV) + scout.parse_github(GH)
        self.assertIsNone(scout.add_citations(items, fake, key="k"))
        self.assertEqual(calls[0][2], {"ids": ["ARXIV:2611.01234"]})
        self.assertEqual(calls[0][1], {"x-api-key": "k"})
        paper = [i for i in items if i["source"] == "arxiv"][0]
        self.assertEqual((paper["cites"], paper["influential"]), (40, 5))
        self.assertIn("40 cites (5 influential)", scout.evidence(paper))
        plain = {**paper, "cites": 45, "influential": 0}
        self.assertGreater(scout.signal(paper), scout.signal(plain), "40 + 2x5 influential beats 45 plain citations")

    def test_citation_failure_is_unknown_not_zero(self):
        def boom(*a):
            raise TimeoutError()
        items = scout.parse_arxiv(ARXIV)
        self.assertIn("semantic scholar", scout.add_citations(items, boom))
        self.assertIsNone(items[0].get("cites"))
        self.assertEqual(scout.evidence(items[0]), "citations unknown")
        def unknown_paper(*a):
            return json.dumps([None])
        items = scout.parse_arxiv(ARXIV)
        self.assertIsNone(scout.add_citations(items, unknown_paper))
        self.assertIsNone(items[0]["cites"])

    def test_get_retries_rate_limits_then_gives_up(self):
        import urllib.error, urllib.request
        attempts, waits = [], []
        real = urllib.request.urlopen
        def flaky(req, timeout):
            attempts.append(1)
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, None)
        urllib.request.urlopen = flaky
        try:
            with self.assertRaises(urllib.error.HTTPError):
                scout.get("https://x.invalid/", retries=(1, 2), sleep=waits.append)
            self.assertEqual((len(attempts), waits), (3, [1, 2]))
            attempts.clear(); waits.clear()
            def not_found(req, timeout):
                attempts.append(1)
                raise urllib.error.HTTPError(req.full_url, 404, "no", {}, None)
            urllib.request.urlopen = not_found
            with self.assertRaises(urllib.error.HTTPError):
                scout.get("https://x.invalid/", retries=(1, 2), sleep=waits.append)
            self.assertEqual((len(attempts), waits), (1, []), "a 404 is not retried")
        finally:
            urllib.request.urlopen = real

    def test_s2_parse_prefers_arxiv_url_and_keeps_citations(self):
        ps = scout.parse_s2(S2)
        self.assertEqual([p["url"] for p in ps], ["https://arxiv.org/abs/2607.07946", "https://www.semanticscholar.org/paper/y"])
        self.assertEqual((ps[0]["cites"], ps[0]["influential"], ps[0]["source"]), (100, 26, "s2"))
        self.assertIn("100 cites (26 influential), 2026-07", scout.evidence(ps[0]))

    def test_s2_dedupes_against_arxiv_and_seen(self):
        items = scout.parse_s2(S2) + scout.parse_arxiv(ARXIV.replace("2611.01234v2", "2607.07946"))
        r = scout.rank(items, ["benchmark", "agent", "mutation"], set())
        self.assertEqual(len([i for i in r if i["url"] == "https://arxiv.org/abs/2607.07946"]), 1)
        self.assertEqual(r[0]["source"], "s2", "the cited copy outranks the uncited arXiv copy")
        r = scout.rank(items, ["benchmark", "agent", "mutation"], {"https://arxiv.org/abs/2607.07946"})
        self.assertNotIn("https://arxiv.org/abs/2607.07946", [i["url"] for i in r])

    def test_more_cited_paper_ranks_higher(self):
        r = scout.rank(scout.parse_s2(S2), ["llm", "agent", "testing", "benchmark"], set())
        self.assertEqual(r[0]["cites"], 100)

    def test_papers_get_their_own_section_and_top(self):
        items = scout.add_growth(self.items() + scout.parse_s2(S2), None, "2026-11-14")
        md = scout.digest(scout.rank(items, ["skill", "benchmark", "mutation"], set()), 1, "2026-11-07", [])
        repos, papers = md.split("### Papers")
        self.assertIn("### Repos and posts", repos)
        self.assertNotIn("| s2 |", repos)
        self.assertEqual(papers.count("| s2 |") + papers.count("| arxiv |"), 1, "--top applies per section")
        self.assertEqual(repos.count("| github |") + repos.count("| hn |"), 1)

    def test_s2_source_request(self):
        seen = {}
        real = scout.get
        scout.get = lambda url, headers=None, **k: seen.update(url=url, headers=headers) or S2
        try:
            out = scout.fetch({"kind": "s2", "query": "agent + eval", "months": 6, "min_citations": 10, "max": 1}, "2026-11-07")
        finally:
            scout.get = real
        q = dict(__import__("urllib.parse").parse.parse_qsl(seen["url"].split("?", 1)[1]))
        self.assertEqual((q["sort"], q["minCitationCount"], q["query"]), ("citationCount:desc", "10", "agent + eval"))
        self.assertTrue(q["publicationDateOrYear"].endswith(":"))
        self.assertEqual(len(out), 1, "max caps the source")

    def test_digest_renders(self):
        items = scout.add_growth(self.items(), None, "2026-11-14")
        md = scout.digest(scout.rank(items, ["skill"], set()), 5, "2026-11-07", ["hn 'x': URLError"], scout.snapshot(items, "2026-11-14"))
        self.assertIn("new to snapshot", md)
        self.assertIn('<!-- scout-stars {"date":"2026-11-14"', md)
        self.assertIn("| score |", md)
        self.assertIn("Source errors", md)
        self.assertIn("nothing new", scout.digest([], 5, "2026-11-07", []))


if __name__ == "__main__":
    unittest.main()
