"""Gate typecheck: which compiled classes' excluded methods (toString/equals/hashCode/componentN/copy) are compiler-made."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("sdlc_tool", ROOT / "scripts/sdlc_tool.py")
if _s is None or _s.loader is None:
    raise RuntimeError("cannot load sdlc_tool.py")
st = importlib.util.module_from_spec(_s)
_s.loader.exec_module(st)


def cls(name: str, *methods: str) -> dict:
    return {"name": name, "methods": list(methods)}


class GeneratedMembers(unittest.TestCase):
    def test_plain_data_class(self):
        c = cls("p/Claim", "component1", "component2", "copy", "copy$default", "toString", "hashCode", "equals")
        self.assertTrue(st.compiler_generated_members(c, set()))

    def test_data_class_with_value_class_parameter_is_mangled(self):
        c = cls("p/Ref", "component1-AbC12", "component2", "copy-XyZ9", "copy-XyZ9$default", "toString", "equals")
        self.assertTrue(st.compiler_generated_members(c, set()))

    def test_value_class(self):
        c = cls("p/SubjectId", "constructor-impl", "box-impl", "unbox-impl", "toString", "toString-impl", "hashCode", "equals")
        self.assertTrue(st.compiler_generated_members(c, set()))

    def test_data_object_only_when_declared_in_source(self):
        c = cls("p/ViewResult$NotFound", "toString", "hashCode", "equals")
        self.assertTrue(st.compiler_generated_members(c, {"NotFound"}))
        self.assertFalse(st.compiler_generated_members(c, set()))

    def test_ordinary_class_with_overrides_is_not_generated(self):
        c = cls("p/Thing", "toString", "hashCode", "equals", "componentX", "copyFrom")
        self.assertFalse(st.compiler_generated_members(c, set()))

    def test_copy_without_component1_is_not_data_like(self):
        self.assertFalse(st.compiler_generated_members(cls("p/T", "copy", "toString"), set()))

    def test_kotlin_data_objects_reads_main_sources_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src/main").mkdir(parents=True)
            (root / "src/test").mkdir(parents=True)
            (root / "src/main/A.kt").write_text("sealed interface R { data object NotFound : R\n object Plain : R }\n// data object Commented\n")
            (root / "src/test/B.kt").write_text("data object TestOnly\n")
            os.environ.pop("GATE_MAIN_PATHS", None)
            self.assertEqual(st.kotlin_data_objects(root), {"NotFound"})


if __name__ == "__main__":
    unittest.main()
