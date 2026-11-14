#!/usr/bin/env python3
"""Weekly scout: pull candidate resources from higher-signal sources than a social feed, drop what we already saw,
rank, and print a markdown digest (CI posts it as an issue). It never adopts anything: a candidate earns a place only
through evals/candidates/<id>.json + evals/gate.py.

  python3 scout/scout.py [--days 7] [--top 15] [--seen FILE ...] [--out digest.md]
  GITHUB_TOKEN raises the GitHub search rate limit (optional). S2_API_KEY raises the Semantic Scholar limit (optional).
Sources and keywords: scout/sources.json (a source may set its own "days"). Stdlib only.
Already seen = URLs in --seen files, evals/negative-results.md, evals/candidates/.

Score = 2 x keywords matched + log1p(popularity) + 2 x log1p(stars gained per week) + 2 x log1p(citations + 2 x influential).
The weights are a starting guess, not measured. Signals:
  stars/week  GitHub hides stargazer dates, so each digest stores every fetched repo's star count in a hidden
              comment (`<!-- scout-stars {...} -->`, names only, so it never marks anything as seen); the next run
              reads the newest one from --seen and scales the difference to a week. Unknown on a repo's first run.
  citations   Semantic Scholar batch lookup for arXiv items (citationCount, influentialCitationCount). Unknown, not 0,
              if the lookup fails. A week-old paper has almost none, so arXiv sources look back further ("days").
"""
import argparse, datetime, json, math, os, re, sys, time, urllib.error, urllib.parse, urllib.request
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


SNAP = re.compile(r"<!-- scout-stars (\{.*?\}) -->", re.S)
ARXIV_ID = re.compile(r"^https://arxiv\.org/abs/(\d{4}\.\d{4,5})$")
BACKOFF = (3, 10, 30)   # seconds between retries on 429/5xx/timeouts


def get(url, headers=None, data=None, retries=BACKOFF, sleep=time.sleep):
    """GET (or POST with JSON `data`), retrying rate limits, server errors and timeouts."""
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {**UA, **(headers or {}), **({"Content-Type": "application/json"} if body else {})}
    for i, wait in enumerate([*retries, None]):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdrs), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if wait is None or not (e.code == 429 or e.code >= 500):
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if wait is None:
                raise
        sleep(wait)


# --- parsers (pure; tested with fixtures)
def parse_github(text):
    return [{"url": r["html_url"], "title": r["full_name"], "summary": r.get("description") or "", "source": "github",
             "repo": r["full_name"].lower(), "pop": int(r.get("stargazers_count") or 0), "date": (r.get("pushed_at") or "")[:10]} for r in json.loads(text).get("items", [])]


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
    if src.get("days"):
        since = (datetime.date.today() - datetime.timedelta(days=int(src["days"]))).isoformat()
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
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": int(src.get("max", 30))})
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


# --- signals
def load_snapshot(paths):
    """Newest star snapshot in the given files -> {"date": "YYYY-MM-DD", "stars": {"owner/name": n}} or None."""
    best = None
    for p in paths:
        if not os.path.isfile(p):
            continue
        for m in SNAP.finditer(open(p, encoding="utf-8", errors="ignore").read()):
            try:
                snap = json.loads(m.group(1))
                datetime.date.fromisoformat(snap["date"]); dict(snap["stars"])
            except (ValueError, KeyError, TypeError):
                continue
            if best is None or snap["date"] > best["date"]:
                best = snap
    return best


def add_growth(items, snap, today):
    """Sets item["growth"] = stars gained per week since the snapshot (None if the repo is new to it)."""
    days = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(snap["date"])).days if snap else 0
    for it in items:
        if it["source"] != "github":
            continue
        then = snap["stars"].get(it["repo"]) if snap and days >= 1 else None
        it["growth"] = None if then is None else round(max(0, it["pop"] - int(then)) * 7 / days)
    return items


def snapshot(items, today):
    stars = {it["repo"]: it["pop"] for it in sorted(items, key=lambda i: i.get("repo", "")) if it["source"] == "github"}
    return "<!-- scout-stars " + json.dumps({"date": today, "stars": stars}, separators=(",", ":")) + " -->"


def add_citations(items, getter=get, key=None):
    """Semantic Scholar batch lookup for arXiv items. Sets cites/influential, or leaves them None. -> error or None."""
    papers = [it for it in items if it["source"] == "arxiv" and ARXIV_ID.match(norm(it["url"]))]
    if not papers:
        return None
    ids = sorted({"ARXIV:" + ARXIV_ID.match(norm(it["url"])).group(1) for it in papers})
    try:
        out = []
        for i in range(0, len(ids), 500):
            out += json.loads(getter("https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount,influentialCitationCount,externalIds",
                                     {"x-api-key": key} if key else {}, {"ids": ids[i:i + 500]}))
    except Exception as e:
        return f"semantic scholar: {type(e).__name__}"
    by_id = {}
    for pid, row in zip(ids, out):
        if row:
            by_id[pid] = (int(row.get("citationCount") or 0), int(row.get("influentialCitationCount") or 0))
    for it in papers:
        c = by_id.get("ARXIV:" + ARXIV_ID.match(norm(it["url"])).group(1))
        it["cites"], it["influential"] = c if c else (None, None)
    return None


def signal(it):
    return (math.log1p(it["pop"]) + 2 * math.log1p(it.get("growth") or 0)
            + 2 * math.log1p((it.get("cites") or 0) + 2 * (it.get("influential") or 0)))


def evidence(it):
    k = lambda n: f"{n / 1000:.1f}k" if n >= 1000 else str(n)
    if it["source"] == "github":
        return f"{k(it['pop'])} stars, " + ("new to snapshot" if it.get("growth") is None else f"+{k(it['growth'])}/wk")
    if it["source"] == "hn":
        return f"{it['pop']} points"
    if it.get("cites") is None:
        return "citations unknown"
    return f"{it['cites']} cites ({it['influential']} influential)"


def rank(items, keywords, seen):
    """Drop seen and duplicate URLs; score = keyword relevance + signal (see module doc). Deterministic order."""
    best = {}
    for it in items:
        u = norm(it["url"])
        if u in seen:
            continue
        text = f"{it['title']} {it['summary']}".lower()
        hits = sorted({k for k in keywords if re.search(r"\b" + re.escape(k.lower()), text)})  # word start: "test" != "latest"
        score = round(2.0 * len(hits) + signal(it), 3)
        cand = {**it, "url": u, "hits": hits, "score": score}
        if u not in best or score > best[u]["score"]:
            best[u] = cand
    return sorted((i for i in best.values() if i["hits"]), key=lambda i: (-i["score"], i["url"]))


def digest(ranked, top, since, errors, snap=""):
    lines = [f"Scout digest since {since}. Not adopted: to test one, add `evals/candidates/<id>.json` (copy `_template.json`), "
             "commit it, open a PR and label it `eval`.", "", "| score | source | item | signal | matched |", "|---|---|---|---|---|"]
    for i in ranked[:top]:
        title = i["title"].replace("|", "/")[:90]
        lines.append(f"| {i['score']} | {i['source']} | [{title}]({i['url']}) | {evidence(i)} | {', '.join(i['hits'])} |")
    if not ranked:
        lines.append("| | | nothing new | | |")
    if errors:
        lines += ["", "Source errors: " + "; ".join(errors)]
    return "\n".join(lines) + "\n" + (f"\n{snap}\n" if snap else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--seen", nargs="*", default=[])
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, "sources.json")))
    today = datetime.date.today().isoformat()
    since = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    items, errors = [], []
    for s in cfg["sources"]:
        try:
            items += fetch(s, since)
        except Exception as e:  # one dead source must not kill the digest
            errors.append(f"{s['kind']} '{s['query']}': {type(e).__name__}")
    add_growth(items, load_snapshot(a.seen), today)
    err = add_citations(items, key=os.environ.get("S2_API_KEY"))
    if err:
        errors.append(err)
    seen = seen_urls([*a.seen, os.path.join(REPO, "evals", "negative-results.md"), os.path.join(REPO, "evals", "candidates")])
    md = digest(rank(items, cfg["keywords"], seen), a.top, since, errors, snapshot(items, today))
    if a.out:
        open(a.out, "w").write(md)
    print(md)
    return 1 if errors and not items else 0


if __name__ == "__main__":
    sys.exit(main())
