#!/usr/bin/env python3
"""Weekly scout: pull candidate resources from higher-signal sources than a social feed, drop what we already saw,
rank, and print a markdown digest (CI posts it as an issue). It never adopts anything: a candidate earns a place only
through evals/candidates/<id>.json + evals/gate.py.

  python3 scout/scout.py [--days 7] [--top 15] [--seen FILE ...] [--out digest.md]
  GITHUB_TOKEN raises the GitHub search rate limit (optional).
Sources and keywords: scout/sources.json. Stdlib only. Already seen = URLs in --seen files, evals/negative-results.md, evals/candidates/.
"""
import argparse, datetime, json, math, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
URL = re.compile(r"https?://[^\s)\]>'\"`]+")
UA = {"User-Agent": "agent-toolkit-scout"}


def norm(url):
    u = urllib.parse.urlsplit(url.strip().rstrip(".,"))
    host = re.sub(r"^www\.", "", u.netloc.lower())
    path = re.sub(r"(\.git)?/*$", "", u.path)
    path = re.sub(r"^/(abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?(\.pdf)?$", r"/abs/\2", path) if host == "arxiv.org" else path
    return f"https://{host}{path.lower() if host == 'github.com' else path}"


def get(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


# --- parsers (pure; tested with fixtures)
def parse_github(text):
    return [{"url": r["html_url"], "title": r["full_name"], "summary": r.get("description") or "", "source": "github",
             "pop": int(r.get("stargazers_count") or 0), "date": (r.get("pushed_at") or "")[:10]} for r in json.loads(text).get("items", [])]


def parse_hn(text):
    out = []
    for h in json.loads(text).get("hits", []):
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append({"url": url, "title": h.get("title") or "", "summary": "", "source": "hn",
                    "pop": int(h.get("points") or 0), "date": (h.get("created_at") or "")[:10]})
    return out


def parse_arxiv(text):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(text).findall("a:entry", ns):
        g = lambda tag: (e.findtext(f"a:{tag}", "", ns) or "").strip()
        out.append({"url": g("id"), "title": re.sub(r"\s+", " ", g("title")), "summary": re.sub(r"\s+", " ", g("summary"))[:400],
                    "source": "arxiv", "pop": 0, "date": g("published")[:10]})
    return out


# --- fetchers
def fetch(src, since):
    kind, q = src["kind"], src["query"]
    if kind == "github":
        tok = os.environ.get("GITHUB_TOKEN")
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": f"{q} pushed:>={since} stars:>={src.get('min_stars', 20)}", "sort": "stars", "per_page": 30})
        return parse_github(get(url, {"Authorization": f"Bearer {tok}"} if tok else {}))
    if kind == "hn":
        ts = int(datetime.datetime.fromisoformat(since).replace(tzinfo=datetime.timezone.utc).timestamp())
        url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
            {"query": q, "tags": "story", "numericFilters": f"created_at_i>{ts},points>={src.get('min_points', 50)}", "hitsPerPage": 30})
        return parse_hn(get(url))
    if kind == "arxiv":
        url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 30})
        return [i for i in parse_arxiv(get(url)) if i["date"] >= since]
    raise ValueError(f"unknown source kind {kind}")


def seen_urls(paths):
    seen = set()
    for p in paths:
        if os.path.isdir(p):
            seen |= seen_urls([os.path.join(p, f) for f in os.listdir(p)])
        elif os.path.isfile(p):
            seen |= {norm(u) for u in URL.findall(open(p, encoding="utf-8", errors="ignore").read())}
    return seen


def rank(items, keywords, seen):
    """Drop seen and duplicate URLs; score = keyword relevance (dominant) + log popularity. Deterministic order."""
    best = {}
    for it in items:
        u = norm(it["url"])
        if u in seen:
            continue
        text = f"{it['title']} {it['summary']}".lower()
        hits = sorted({k for k in keywords if re.search(r"\b" + re.escape(k.lower()), text)})  # word start: "test" != "latest"
        score = round(2.0 * len(hits) + math.log1p(it["pop"]), 3)
        cand = {**it, "url": u, "hits": hits, "score": score}
        if u not in best or score > best[u]["score"]:
            best[u] = cand
    return sorted((i for i in best.values() if i["hits"]), key=lambda i: (-i["score"], i["url"]))


def digest(ranked, top, since, errors):
    lines = [f"Scout digest since {since}. Not adopted: to test one, add `evals/candidates/<id>.json` (copy `_template.json`), "
             "commit it, open a PR and label it `eval`.", "", "| score | source | item | matched |", "|---|---|---|---|"]
    for i in ranked[:top]:
        title = i["title"].replace("|", "/")[:90]
        lines.append(f"| {i['score']} | {i['source']} | [{title}]({i['url']}) | {', '.join(i['hits'])} |")
    if not ranked:
        lines.append("| | | nothing new | |")
    if errors:
        lines += ["", "Source errors: " + "; ".join(errors)]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--seen", nargs="*", default=[])
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, "sources.json")))
    since = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    items, errors = [], []
    for s in cfg["sources"]:
        try:
            items += fetch(s, since)
        except Exception as e:  # one dead source must not kill the digest
            errors.append(f"{s['kind']} '{s['query']}': {type(e).__name__}")
    seen = seen_urls([*a.seen, os.path.join(REPO, "evals", "negative-results.md"), os.path.join(REPO, "evals", "candidates")])
    md = digest(rank(items, cfg["keywords"], seen), a.top, since, errors)
    if a.out:
        open(a.out, "w").write(md)
    print(md)
    return 1 if errors and not items else 0


if __name__ == "__main__":
    sys.exit(main())
