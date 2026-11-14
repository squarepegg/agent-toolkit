#!/usr/bin/env python3
"""Score a harness-ab run against the thresholds pre-registered in docs/ab-harness-paper-plan.md. Deterministic.

  python3 evals/harness-ab/report.py results/harness-ab/run.jsonl [--md out.md]

H1 (B vs A, per task): ADOPT iff mean check pass rate B >= A AND median main-agent input tokens B <= 0.9 x A.
H2 (C vs A, dst):      ADOPT iff total rule violations (dst_lint) C <= 0.7 x A AND no other check's pass count lower in C.
Unfinished runs (turn timeout) count as run, with whatever their checks scored.
"""
import json, statistics, sys


def load(p):
    rows = {}
    for line in open(p):
        x = json.loads(line); rows[(x["task"], x["arm"], x["i"])] = x
    return list(rows.values())


def arm(rows, task, a):
    return [r for r in rows if r["task"] == task and r["arm"] == a]


def tokens_in(r):
    u = r["usage"]; return u["input"] + u["cacheRead"] + u["cacheWrite"]


def rate(rs):
    return sum(r["passed"] for r in rs) / max(1, sum(r["n_checks"] for r in rs))


def summarize(rows):
    out, verdicts = [], {}
    for task in sorted({r["task"] for r in rows}):
        arms = sorted({r["arm"] for r in rows if r["task"] == task})
        keys = list(arm(rows, task, arms[0])[0]["checks"])
        out.append(f"\n### {task}\n\n| check | " + " | ".join(arms) + " |\n|---|" + "---|" * len(arms))
        for k in keys:
            out.append(f"| {k} | " + " | ".join(f"{sum(r['checks'][k] for r in arm(rows, task, a))}/{len(arm(rows, task, a))}" for a in arms) + " |")
        stats = {}
        for a in arms:
            rs = arm(rows, task, a)
            stats[a] = {"n": len(rs), "rate": rate(rs), "tok": statistics.median(tokens_in(r) for r in rs),
                        "cost": sum(r["usage"]["cost"] for r in rs), "viol": sum(r["detail"].get("rule_violations", 0) for r in rs),
                        "unfinished": sum(not r["finished"] for r in rs), "wall": statistics.median(r["wall_s"] for r in rs)}
        for label, f in (("pass rate", lambda s: f"{s['rate']:.0%}"), ("median input tokens", lambda s: f"{s['tok']:,.0f}"),
                         ("total cost", lambda s: f"${s['cost']:.2f}"), ("rule violations (sum)", lambda s: str(s["viol"])),
                         ("unfinished", lambda s: str(s["unfinished"])), ("median wall s", lambda s: f"{s['wall']:.0f}")):
            out.append(f"| **{label}** | " + " | ".join(f(stats[a]) for a in arms) + " |")
        A = stats.get("A")
        if A and "B" in stats:
            B = stats["B"]; ok = B["rate"] >= A["rate"] and B["tok"] <= 0.9 * A["tok"]
            verdicts[f"H1 {task}"] = (ok, f"rate {B['rate']:.0%} vs {A['rate']:.0%}; tokens {B['tok'] / A['tok']:.2f}x A (need <= 0.90)")
        if A and "C" in stats:
            C = stats["C"]; worse = [k for k in keys if k != "lint_clean" and sum(r["checks"][k] for r in arm(rows, task, "C")) < sum(r["checks"][k] for r in arm(rows, task, "A"))]
            ok = C["viol"] <= 0.7 * A["viol"] and not worse and A["viol"] > 0
            verdicts[f"H2 {task}"] = (ok, f"violations {C['viol']} vs {A['viol']} (need <= {0.7 * A['viol']:.1f}); worse checks: {worse or 'none'}")
    out.append("\n### Verdicts (pre-registered)\n")
    for k, (ok, why) in verdicts.items(): out.append(f"- {k}: {'ADOPT' if ok else 'REJECT'} ({why})")
    return "\n".join(out), verdicts


def main(a):
    if not a: print(__doc__); return 2
    rows = load(a[0]); md, _ = summarize(rows)
    print(md)
    if "--md" in a: open(a[a.index("--md") + 1], "w").write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
