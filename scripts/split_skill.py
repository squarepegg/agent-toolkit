#!/usr/bin/env python3
"""Progressive disclosure for a SKILL.md: keep the core sections, move the rest into reference/sections/*.md
and leave a one-line pointer per moved section. Lossless by construction: every original line ends up in exactly
one output file, in order (`check` proves it). Stdlib only.

  python3 scripts/split_skill.py split <skill_dir> <out_dir> --keep "<heading regex>" [--keep ...]
  python3 scripts/split_skill.py check <skill_dir> <out_dir>      exit 1 unless the split is lossless

A section is a `## ` heading plus everything up to the next `## ` (or EOF); `#`-lines inside code fences are not
headings. The text before the first `## ` (frontmatter, title, intro) is always kept. The pointer block is
appended to SKILL.md as `## Details (read the file when you reach that step)`.
Idea: three-tier disclosure (index -> SKILL.md -> linked files), arXiv 2609.00006 §12.5.
"""
import os, re, shutil, sys

POINTER_HEAD = "## Details (read the file when you reach that step)"


def sections(text):
    """-> [(heading or None, [lines])]; fence-aware"""
    out, cur, head, fence = [], [], None, False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"): fence = not fence
        if not fence and line.startswith("## "):
            out.append((head, cur)); cur, head = [], line.rstrip("\n")
        cur.append(line)
    out.append((head, cur))
    return out


def slug(h):
    return re.sub(r"[^a-z0-9]+", "-", h[3:].lower()).strip("-")[:48]


def split(src, dst, keep):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    text = open(os.path.join(src, "SKILL.md")).read()
    kept, moved = [], []
    for i, (h, lines) in enumerate(sections(text)):
        if h is None or any(re.search(k, h) for k in keep): kept.append("".join(lines))
        else: moved.append((i, h, "".join(lines)))
    os.makedirs(os.path.join(dst, "reference", "sections"), exist_ok=True)
    ptr = ["\n" + POINTER_HEAD + "\n\n"]
    for i, h, body in moved:
        rel = f"reference/sections/{i:02d}-{slug(h)}.md"
        with open(os.path.join(dst, rel), "w") as f: f.write(body)
        ptr.append(f"- {h[3:]}: `{rel}`\n")
    body = "".join(kept)
    if not body.endswith("\n"): body += "\n"
    with open(os.path.join(dst, "SKILL.md"), "w") as f: f.write(body + ("".join(ptr) if moved else ""))
    return len(text), len(body) + (len("".join(ptr)) if moved else 0), len(moved)


def check(src, dst):
    """every original line in exactly one output, original order within each file, nothing else added"""
    orig = open(os.path.join(src, "SKILL.md")).read()
    new = open(os.path.join(dst, "SKILL.md")).read()
    if POINTER_HEAD in new: new = new[:new.index("\n" + POINTER_HEAD)] + "\n"
    sec_dir = os.path.join(dst, "reference", "sections")
    parts = {int(f[:2]): open(os.path.join(sec_dir, f)).read() for f in sorted(os.listdir(sec_dir))} if os.path.isdir(sec_dir) else {}
    rebuilt, kept_iter = [], iter([])
    secs = sections(orig)
    kept_text = new
    for i, (h, lines) in enumerate(secs):
        chunk = "".join(lines)
        if i in parts:
            if parts[i] != chunk: return False, f"section {i} differs"
        else:
            if not kept_text.startswith(chunk) and not (i == len(secs) - 1 and kept_text.rstrip("\n") == chunk.rstrip("\n")):
                return False, f"kept section {i} ({h}) not found in order"
            kept_text = kept_text[len(chunk):]
    if kept_text.strip(): return False, "extra text in SKILL.md"
    return True, f"lossless: {len(secs)} sections, {len(parts)} moved"


def main(a):
    if len(a) >= 3 and a[0] == "split":
        keep = [a[i + 1] for i, x in enumerate(a) if x == "--keep"]
        o, n, m = split(a[1], a[2], keep)
        ok, msg = check(a[1], a[2])
        print(f"SKILL.md {o} -> {n} chars, {m} sections moved; {msg}"); return 0 if ok else 1
    if len(a) == 3 and a[0] == "check":
        ok, msg = check(a[1], a[2]); print(msg); return 0 if ok else 1
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
