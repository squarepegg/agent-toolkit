#!/usr/bin/env bash
# Fake referee. FAKE_REFEREE=valid | invalid
python3 - "${FAKE_REFEREE:-valid}" <<'PY'
import json, sys
f = json.load(open("referee/findings.json"))["findings"]
json.dump({"verdicts": [{"id": x["id"], "verdict": sys.argv[1], "spec_ref": "AC-1", "reason": "selftest"} for x in f]},
          open("referee/verdicts.json", "w"))
PY
