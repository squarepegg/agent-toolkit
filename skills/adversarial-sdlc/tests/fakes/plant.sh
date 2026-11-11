#!/usr/bin/env bash
cat > mutants.json <<'M'
[{"name": "P1 lower bound off by one", "spec_ref": "INV-1", "file": "src/main/kotlin/clamp/Clamp.kt",
  "old": "maxOf(lo, minOf(x, hi))", "new": "maxOf(lo + 1, minOf(x, hi))"}]
M
