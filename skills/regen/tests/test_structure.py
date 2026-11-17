"""Structural checks for skills/regen: frontmatter, unique names, relative path references resolve,
and no private identifiers anywhere in the tree (this repo is public).

Run: python3 skills/regen/tests/test_structure.py
"""
import re
import unittest
from pathlib import Path

REGEN = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "build", ".gradle"}

# Written in pieces so this file does not contain the strings it forbids.
FORBIDDEN = [
    "led" + "ger",
    "scy" + "lla",
    "clever" + "tap",
    "singular" + "'s",
    "co" + "pilot",
    "nic " + "st",
    "pe" + "gg",
    "as" + "tra",
    "gra" + "ce_",
    "for" + "tis",
    "pass" + "port",
]

PATH_SEGMENTS = {"reference", "examples", "scripts", "templates", "_shared"}
THIS_SKILL = "<this-skill-dir>/"


def all_files():
    out = []
    for p in sorted(REGEN.rglob("*")):
        if p.is_file() and not (SKIP_DIRS & set(p.relative_to(REGEN).parts)):
            out.append(p)
    return out


def skill_dirs():
    return sorted(p for p in REGEN.iterdir()
                  if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(("_", "."))
                  and p.name != "tests")


def frontmatter(path):
    """-> dict of single-line `key: value` pairs between the leading --- fences."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    fm = {}
    for ln in lines[1:end]:
        m = re.match(r"^([A-Za-z][\w-]*):[ \t]*(.*)$", ln)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def referenced_paths(md):
    """Repo-relative-looking paths in backtick spans and markdown link targets of one .md file.

    Yields (raw, relative-path-string, is_dir)."""
    text = md.read_text(encoding="utf-8")
    spans = re.findall(r"`([^`\n]+)`", text) + re.findall(r"\]\(([^)\s]+)\)", text)
    for span in spans:
        for word in re.split(r"[\s'\"=]+", span):
            word = word.lstrip("(").rstrip(".,;:)")
            word = word.replace(THIS_SKILL, "") if word.startswith(THIS_SKILL) else word
            if not word or re.search(r"[<>*]|\.\.\.|://|^[~/]", word):
                continue
            if not (PATH_SEGMENTS & set(word.split("/"))) or "/" not in word:
                continue
            if md.name == "README.md" and word.count("/") == 1 and word.endswith("/"):
                continue  # README speaks generically of "each skill's `examples/` folder"
            yield word, word.rstrip("/"), word.endswith("/")


def resolves(md, rel):
    """A reference may be relative to the file, or to skills/regen (README style). README also
    explains how skills reach `../_shared`, which is relative to a skill directory."""
    bases = [md.parent, REGEN]
    if md.name == "README.md" and rel.startswith("../"):
        bases += skill_dirs()
    return any((base / rel).exists() for base in bases)


class Frontmatter(unittest.TestCase):
    def test_every_skill_dir_has_skill_md_with_single_line_fields(self):
        dirs = skill_dirs()
        self.assertGreaterEqual(len(dirs), 6, [d.name for d in dirs])
        for d in dirs:
            skill = d / "SKILL.md"
            self.assertTrue(skill.is_file(), f"{d.name} has no SKILL.md")
            fm = frontmatter(skill)
            self.assertIsNotNone(fm, f"{skill}: missing --- frontmatter fences")
            for key in ("name", "description"):
                self.assertTrue(fm.get(key), f"{skill}: missing or empty single-line `{key}:`")
            self.assertLessEqual(len(fm["description"]), 1024, f"{skill}: description too long")

    def test_names_are_unique_and_match_directory(self):
        names = {}
        for d in skill_dirs():
            name = frontmatter(d / "SKILL.md")["name"]
            self.assertNotIn(name, names, f"{d.name} duplicates the name of {names.get(name)}")
            names[name] = d.name
            self.assertEqual(name, d.name, f"{d.name}/SKILL.md names itself {name!r}")

    def test_multiline_description_is_rejected_by_the_parser(self):
        p = REGEN / "tests" / "_tmp_check.md"
        try:
            p.write_text("---\nname: x\ndescription:\n  wrapped\n---\n")
            self.assertEqual(frontmatter(p)["description"], "")
        finally:
            p.unlink()

    def test_readme_mentions_every_skill(self):
        readme = (REGEN / "README.md").read_text(encoding="utf-8")
        for d in skill_dirs():
            self.assertIn(f"`{d.name}`", readme, f"README.md never mentions {d.name}")


class References(unittest.TestCase):
    def markdown_files(self):
        files = [REGEN / "README.md", *sorted((REGEN / "_shared").glob("*.md")),
                 *[d / "SKILL.md" for d in skill_dirs()]]
        return [f for f in files if f.is_file()]

    def test_every_relative_path_reference_exists(self):
        checked = 0
        missing = []
        for md in self.markdown_files():
            for raw, rel, is_dir in referenced_paths(md):
                checked += 1
                if not resolves(md, rel):
                    missing.append(f"{md.relative_to(REGEN)}: `{raw}`")
                elif is_dir:
                    self.assertTrue(any((b / rel).is_dir() for b in (md.parent, REGEN, *skill_dirs())),
                                    f"{md.relative_to(REGEN)}: `{raw}` is not a directory")
        self.assertEqual(missing, [], "dangling references:\n  " + "\n  ".join(missing))
        self.assertGreater(checked, 25, "reference extraction found suspiciously few paths")

    def test_extractor_flags_a_dangling_reference(self):
        p = REGEN / "tests" / "_tmp_refs.md"
        try:
            p.write_text("See `reference/does-not-exist.md` and [x](../_shared/nope.md) and `reference/` and "
                         "`python3 <this-skill-dir>/scripts/ghost.py arg`.\n")
            found = {rel: resolves(p, rel) for _, rel, _ in referenced_paths(p)}
        finally:
            p.unlink()
        self.assertEqual(found, {"reference/does-not-exist.md": False, "../_shared/nope.md": False,
                                 "reference": False, "scripts/ghost.py": False})

    def test_every_shipped_script_is_referenced_from_docs(self):
        docs = "\n".join(f.read_text(encoding="utf-8") for f in self.markdown_files())
        for p in all_files():
            rel = p.relative_to(REGEN)
            if rel.parts[0] == "tests" or "scripts" not in rel.parts:
                continue
            self.assertIn(p.name, docs, f"{rel} is shipped but no skill doc mentions it")


class Hygiene(unittest.TestCase):
    def test_no_forbidden_strings_in_any_file_or_path(self):
        hits = []
        files = all_files()
        self.assertGreater(len(files), 30)
        for p in files:
            rel = str(p.relative_to(REGEN))
            try:
                low = p.read_text(encoding="utf-8").lower()
            except UnicodeDecodeError:
                low = ""
            for bad in FORBIDDEN:
                if bad in low or bad in rel.lower():
                    hits.append(f"{rel}: {bad!r}")
        self.assertEqual(hits, [])

    def test_plain_word_singular_is_allowed(self):
        self.assertNotIn("singular" + "'s", "a singular point")


if __name__ == "__main__":
    unittest.main()
