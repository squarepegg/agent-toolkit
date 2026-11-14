#!/usr/bin/env python3
"""Deterministic checks for every skill in skills/ (stdlib only, no model, no network).

  python3 scripts/check_skills.py              check the repo; exit 1 on any failure
  python3 scripts/check_skills.py --self-test  plant one bug per check in a temp copy; exit 1 if any survives

Checks, all skills:
  frontmatter   SKILL.md starts with `---`, has single-line `name` and `description` (1..1024 chars); names unique
  install       README.md has an install line `ln -s "$PWD/skills/<dir>" ~/.agents/skills/<name>` for the skill
  hygiene       no private/employer strings (PUBLIC_FORBID) in any SKILL.md: this repo is public
Checks, skills with tests/golden.json  {"pins": [...], "max_chars": N, "refs": [...], "forbid": [...]}:
  pins          each pinned substring is still in SKILL.md (change a pin only on purpose)
  budget        SKILL.md length <= max_chars (skills load into context)
  refs          each referenced skill name exists in this repo AND appears as `name` in SKILL.md (hand-offs stay linked)
  forbid        skill-specific forbidden substrings are absent
"""
import json, os, re, shutil, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESC_MAX = 1024
PUBLIC_FORBID = ["fortis", "shrimplocker", "~/.agents/brain", "brain_search", "catalog_lookup", "repo_lookup"]


def frontmatter(text):
    """-> dict of single-line `key: value` pairs, or None when there is no frontmatter block"""
    if not text.startswith("---\n"): return None
    end = text.find("\n---", 4)
    if end < 0: return None
    fm = {}
    for line in text[4:end].split("\n"):
        m = re.match(r"^([a-z_-]+):\s*(.*)$", line)
        if m: fm[m.group(1)] = m.group(2).strip()
        elif line.strip(): fm.setdefault("_bad", []).append(line)
    return fm


def load_skills(root):
    out = {}
    sk = os.path.join(root, "skills")
    for d in sorted(os.listdir(sk)):
        p = os.path.join(sk, d, "SKILL.md")
        if os.path.isfile(p): out[d] = open(p, encoding="utf-8").read()
    return out


def check(root=ROOT):
    """-> list of failure strings"""
    fails, skills = [], load_skills(root)
    readme = open(os.path.join(root, "README.md"), encoding="utf-8").read()
    names = {}
    for d, text in skills.items():
        fm = frontmatter(text)
        if fm is None: fails.append(f"{d}: no frontmatter"); continue
        if fm.get("_bad"): fails.append(f"{d}: frontmatter lines not `key: value` on one line: {fm['_bad'][:2]}")
        name, desc = fm.get("name", ""), fm.get("description", "")
        if not name: fails.append(f"{d}: frontmatter name missing")
        if not desc: fails.append(f"{d}: description missing")
        elif len(desc) > DESC_MAX: fails.append(f"{d}: description {len(desc)} chars > {DESC_MAX}")
        if name in names: fails.append(f"{d}: name {name!r} also used by {names[name]}")
        names[name] = d
        if not re.search(r'ln -s "\$PWD/skills/' + re.escape(d) + r'"\s+~/\.agents/skills/' + re.escape(name) + r"\b", readme):
            fails.append(f"{d}: README has no install line for skills/{d} -> ~/.agents/skills/{name}")
        low = text.lower()
        for bad in PUBLIC_FORBID:
            if bad in low: fails.append(f"{d}: private string {bad!r} in public SKILL.md")
    for d, text in skills.items():
        gp = os.path.join(root, "skills", d, "tests", "golden.json")
        if not os.path.exists(gp): continue
        try: g = json.load(open(gp, encoding="utf-8"))
        except ValueError as e: fails.append(f"{d}: bad golden.json: {e}"); continue
        for pin in g.get("pins", []):
            if pin not in text: fails.append(f"{d}: pinned text missing: {pin!r}")
        if g.get("max_chars") is not None and len(text) > g["max_chars"]:
            fails.append(f"{d}: {len(text)} chars > budget {g['max_chars']}")
        for ref in g.get("refs", []):
            if ref not in names: fails.append(f"{d}: refers to skill {ref!r}, which is not in this repo")
            elif f"`{ref}`" not in text: fails.append(f"{d}: hand-off to `{ref}` no longer in SKILL.md")
        for bad in g.get("forbid", []):
            if bad in text: fails.append(f"{d}: forbidden text present: {bad!r}")
    return fails


# ---- planted bugs: each must be caught by the named check --------------------------------------------------------
def _edit(root, rel, fn):
    p = os.path.join(root, rel); t = open(p, encoding="utf-8").read(); open(p, "w", encoding="utf-8").write(fn(t))


def _golden_skill(root):
    for d in load_skills(root):
        gp = os.path.join(root, "skills", d, "tests", "golden.json")
        if os.path.exists(gp):
            g = json.load(open(gp))
            if g.get("pins") and g.get("refs") and g.get("max_chars"): return d, g
    raise SystemExit("self-test: no skill with pins, refs and max_chars in golden.json")


def planted(root):
    d, g = _golden_skill(root); sk = f"skills/{d}/SKILL.md"; pin, ref = g["pins"][0], g["refs"][0]
    name = frontmatter(load_skills(root)[d])["name"]
    other = next(x for x in load_skills(root) if x != d)
    return [
        ("pins", "pinned text missing", lambda: _edit(root, sk, lambda t: t.replace(pin, "XX"))),
        ("budget", "> budget", lambda: _edit(root, sk, lambda t: t + "x" * (g["max_chars"] + 1))),
        ("refs: hand-off removed", f"hand-off to `{ref}`", lambda: _edit(root, sk, lambda t: t.replace(f"`{ref}`", "`gone`"))),
        ("refs: target renamed", f"refers to skill {ref!r}", lambda: _edit(root, f"skills/{next(k for k, v in load_skills(root).items() if frontmatter(v).get('name') == ref)}/SKILL.md", lambda t: t.replace(f"name: {ref}", f"name: {ref}-renamed", 1))),
        ("frontmatter: description too long", "description", lambda: _edit(root, sk, lambda t: re.sub(r"description: (.*)", lambda m: "description: " + m.group(1) + "y" * DESC_MAX, t, count=1))),
        ("frontmatter: duplicate name", "also used by", lambda: _edit(root, f"skills/{other}/SKILL.md", lambda t: re.sub(r"name: .*", f"name: {name}", t, count=1))),
        ("install line missing", "no install line", lambda: _edit(root, "README.md", lambda t: re.sub(r'.*ln -s "\$PWD/skills/' + re.escape(d) + r'".*\n', "", t))),
        ("hygiene", "private string", lambda: _edit(root, sk, lambda t: t + "\nsee ~/.agents/brain/facts\n")),
    ]


def self_test():
    base = check(ROOT)
    if base: print("self-test needs a clean repo first:\n  " + "\n  ".join(base)); return 1
    survived = 0
    tmp0 = tempfile.mkdtemp()
    try:
        for i, (label, expect, _) in enumerate(planted(ROOT)):
            tmp = os.path.join(tmp0, str(i))
            shutil.copytree(os.path.join(ROOT, "skills"), os.path.join(tmp, "skills")); shutil.copy(os.path.join(ROOT, "README.md"), tmp)
            planted(tmp)[i][2]()
            hit = [f for f in check(tmp) if expect in f]
            print(f"{'caught  ' if hit else 'SURVIVED'} {label}" + (f"  ({hit[0]})" if hit else ""))
            survived += not hit
    finally:
        shutil.rmtree(tmp0, ignore_errors=True)
    print(f"{len(planted(ROOT)) - survived}/{len(planted(ROOT))} planted bugs caught")
    return 1 if survived else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv: sys.exit(self_test())
    fails = check()
    for f in fails: print("FAIL", f)
    print(f"{len(load_skills(ROOT))} skills, {len(fails)} failures")
    sys.exit(1 if fails else 0)
