#!/usr/bin/env python3
"""Tests for evals/gate.py and the skill_ab -> gate path (stdlib, no model; uses evals/fake_pi.py).
python3 evals/test_gate.py"""
import copy, json, os, random, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gate  # noqa: E402

C = {"id": "c1", "source": "test", "claim": "x", "skill": "dst", "samples": 3}


def res(with_, without, held_with=(), held_without=(), samples=3, cost_with=0.01, finished=True):
    rows = []
    for arm, dev, held in (("with", with_, held_with), ("without", without, held_without)):
        for split, counts in (("dev", dev), ("heldout", held)):
            for i, k in enumerate(counts):
                for n in range(samples):
                    rows.append({"probe": f"{split}{i}", "split": split, "arm": arm, "n": n, "correct": n < k,
                                 "cost_usd": cost_with if arm == "with" else 0.01, "finished": finished or arm == "without"})
    return {"skill": "dst", "samples": samples, "pi_args": ["--model", "m/x"], "date": "2026-01-01", "rows": rows}


def v(r, **kw):
    return gate.decide({**C, **kw}, r)[0]


class Verdicts(unittest.TestCase):
    def test_each_verdict(self):
        self.assertEqual(v(res([2, 2, 2], [1, 1, 1], [2], [2])), "ADOPT")
        self.assertEqual(v(res([2, 1, 1], [1, 1, 1])), "NO_DELTA")
        self.assertEqual(v(res([3] * 10, [3] * 9 + [2])), "CEILING")
        self.assertEqual(v(res([1, 3, 3], [3, 0, 0])), "REGRESSION")
        self.assertEqual(v(res([3, 3], [0, 0], [1], [3])), "REGRESSION")
        self.assertEqual(v(res([3, 3], [0, 0], cost_with=0.05)), "TOO_COSTLY")
        self.assertEqual(v(res([3, 3], [0, 0], finished=False)), "INCONCLUSIVE")

    def test_thresholds_come_from_the_candidate(self):
        r = res([2, 1, 1], [1, 1, 1])
        self.assertEqual(v(r, min_gain=1), "ADOPT")
        self.assertEqual(v(res([3, 3], [0, 0], cost_with=0.05), max_cost_ratio=10), "ADOPT")

    def test_bad_input(self):
        good = res([2, 2], [0, 0])
        bad = []
        r = copy.deepcopy(good); r["pi_args"] = []; bad.append(("model not pinned", C, r))
        r = copy.deepcopy(good); r["samples"] = 2; bad.append(("samples", C, r))
        r = copy.deepcopy(good); r["skill"] = "doubt"; bad.append(("skill", C, r))
        r = copy.deepcopy(good); r["rows"] = [x for x in r["rows"] if not (x["arm"] == "with" and x["probe"] == "dev1")]; bad.append(("arms differ", C, r))
        r = copy.deepcopy(good); r["rows"] = [x for x in r["rows"] if x["arm"] == "with"]; bad.append(("one arm", C, r))
        r = copy.deepcopy(good); r["rows"] = [x for x in r["rows"] if x["n"] != 2]; bad.append(("missing sample", C, r))
        r = copy.deepcopy(good); r["rows"] = [x for x in r["rows"] if not (x["arm"] == "without" and x["probe"] == "dev1" and x["n"] == 0)]
        bad.append(("baseline arm missing a sample", C, r))
        bad.append(("candidate registered other samples", {**C, "samples": 2}, good))
        bad.append(("short sha", {**C, "fetch": {"repo": "x", "ref": "abcdef1"}, "with_path": "s"}, good))
        bad.append(("unknown key", {**C, "min_gian": 0}, good))
        bad.append(("negative threshold", {**C, "min_gain": -1}, good))
        bad.append(("unpinned fetch", {**C, "fetch": {"repo": "x", "ref": "main"}, "with_path": "s"}, good))
        bad.append(("no heldout-only", C, res([], [], [3], [0])))
        for name, c, r in bad:
            with self.assertRaises(gate.BadInput, msg=name):
                gate.decide(c, r)

    def test_properties(self):
        """Seeded random runs: deterministic; never ADOPT in both directions; identical arms never ADOPT;
        one more correct answer on the `with` arm never turns ADOPT into anything else."""
        rng = random.Random(7)
        for _ in range(500):
            k = rng.randint(1, 6)
            a = [rng.randint(0, 3) for _ in range(k)]; b = [rng.randint(0, 3) for _ in range(k)]
            h = [rng.randint(0, 3) for _ in range(rng.randint(0, 3))]
            r = res(a, b, h, h)
            first = v(r)
            self.assertEqual(first, v(copy.deepcopy(r)))
            self.assertFalse(first == "ADOPT" and v(res(b, a, h, h)) == "ADOPT")
            self.assertNotEqual(v(res(b, b, h, h)), "ADOPT")
            if first == "ADOPT":
                i = rng.randrange(k); a2 = list(a); a2[i] = min(3, a2[i] + 1)
                self.assertEqual(v(res(a2, b, h, h)), "ADOPT")


class Registration(unittest.TestCase):
    def test_candidate_must_be_committed_and_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            g = lambda *a: subprocess.run(["git", "-C", t, *a], check=True, capture_output=True)
            g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
            p = os.path.join(t, "c1.json")
            with open(p, "w") as f:
                json.dump(C, f)
            old = gate.ROOT
            try:
                gate.ROOT = t
                with self.assertRaises(gate.BadInput):
                    gate.registered(p)
                g("add", "c1.json"); g("commit", "-qm", "c")
                gate.registered(p)
                with open(p, "w") as f:
                    json.dump({**C, "min_gain": 0}, f)
                with self.assertRaises(gate.BadInput):
                    gate.registered(p)
            finally:
                gate.ROOT = old


class EndToEnd(unittest.TestCase):
    def test_skill_ab_with_fake_pi_then_gate(self):
        """skill_ab speaks the pi rpc protocol, records what the gate needs, and the gate reads it."""
        probes = json.load(open(os.path.join(ROOT, "skills", "dst", "evals", "probes.json")))
        with tempfile.TemporaryDirectory() as t:
            good, bad = os.path.join(t, "good"), os.path.join(t, "bad")
            for d, text in ((good, "conformance against the real thing; known bug; 150 seeds, no docker; mutation; "
                                   "production can reach it"), (bad, "no idea")):
                os.makedirs(os.path.join(d, "evals"))
                for name in ("answer-pass.md", "answer-fail.md"):
                    with open(os.path.join(d, "evals", name), "w") as f:
                        f.write(text)
            out = os.path.join(t, "r.json")
            env = {**os.environ, "EVAL_PI": os.path.join(HERE, "fake_pi.py"), "FAKE_PI_SKILL": bad,
                   "EVAL_PI_ARGS": json.dumps(["--model", "fake/m"])}
            r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "skill_ab.py"), "dst", "--samples", "2",
                                "--with-path", good, "--out", out], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.load(open(out))
            self.assertEqual(data["probes_sha256"], gate.sha256(os.path.join(ROOT, "skills", "dst", "evals", "probes.json")))
            self.assertEqual(len(data["rows"]), len(probes) * 2 * 2)
            self.assertEqual({x["split"] for x in data["rows"]}, {"dev", "heldout"})
            self.assertEqual(gate.decide({**C, "samples": 2}, data)[0], "ADOPT")


class Files(unittest.TestCase):
    def test_candidates_are_valid(self):
        d = os.path.join(HERE, "candidates")
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or f.startswith("_"):
                continue
            c = json.load(open(os.path.join(d, f)))
            gate.validate_candidate(c, f)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", c["skill"], "evals", "probes.json")), f)

    def test_every_probe_file_has_a_dev_probe_and_unique_ids(self):
        import glob
        for f in glob.glob(os.path.join(ROOT, "skills", "*", "evals", "probes.json")):
            ps = json.load(open(f))
            self.assertEqual(len({p["id"] for p in ps}), len(ps), f)
            self.assertTrue(all(p.get("split", "dev") in ("dev", "heldout") for p in ps), f)
            self.assertEqual(ps[0].get("split", "dev"), "dev", f"{f}: the first probe is the fixture-pinned one; keep it dev")


if __name__ == "__main__":
    unittest.main()
