"""Unit tests for scripts/k8s/run.py's copy-in (pack) and copy-out (safe_extract): no cluster needed.

The output archive is written by the adversary (code under test in the pod), so every case below must be
refused without touching anything outside the --rw dir, and a valid archive must replace the dir exactly.
Run: python3 tests/k8s_unit_test.py   (selftest.sh runs it in its quick section)
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("k8srun", ROOT / "scripts/k8s/run.py")
assert _spec is not None and _spec.loader is not None
k8s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(k8s)
_tspec = importlib.util.spec_from_file_location("sdlc_tool", ROOT / "scripts/sdlc_tool.py")
assert _tspec is not None and _tspec.loader is not None
sdlc_tool = importlib.util.module_from_spec(_tspec)
_tspec.loader.exec_module(sdlc_tool)


def tar_of(entries) -> bytes:
    """entries: (name, kind, payload) with kind file|dir|sym|hard|fifo|chr; names as written in the archive."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, kind, payload in entries:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                data = payload.encode() if isinstance(payload, str) else payload
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
                continue
            ti.type = {"dir": tarfile.DIRTYPE, "sym": tarfile.SYMTYPE, "hard": tarfile.LNKTYPE,
                       "fifo": tarfile.FIFOTYPE, "chr": tarfile.CHRTYPE}[kind]
            if kind in ("sym", "hard"):
                ti.linkname = payload
            if kind == "dir":
                ti.mode = 0o755
            t.addfile(ti)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="k8s-unit-")).resolve()
        self.rw = self.tmp / "proj" / "build"
        self.rw.mkdir(parents=True)
        (self.rw / "old.txt").write_text("old")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.r = str(self.rw).lstrip("/")
        self._env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refused(self, data: bytes, why: str) -> None:
        before = sorted(p.name for p in self.tmp.rglob("*"))
        with self.assertRaises(SystemExit) as cm:
            k8s.safe_extract(data, str(self.rw))
        self.assertEqual(cm.exception.code, 97, why)
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*")), before, f"{why}: the host changed")
        self.assertEqual((self.rw / "old.txt").read_text(), "old", f"{why}: the rw dir changed")
        self.assertEqual(list(self.outside.iterdir()), [], f"{why}: something was written outside")


class SafeExtract(Base):
    def test_valid_archive_replaces_the_rw_dir(self) -> None:
        k8s.safe_extract(tar_of([(self.r, "dir", None), (f"{self.r}/reports", "dir", None),
                                 (f"{self.r}/reports/a.xml", "file", "<x/>")]), str(self.rw))
        self.assertEqual((self.rw / "reports/a.xml").read_text(), "<x/>")
        self.assertFalse((self.rw / "old.txt").exists(), "the rw dir must equal the pod's copy")
        self.assertEqual([p.name for p in self.rw.parent.iterdir() if p.name.startswith(".sdlc-k8s-out-")], [],
                         "the staging dir is removed")

    def test_absolute_path(self) -> None:
        self.refused(tar_of([(str(self.outside / "x"), "file", "x")]), "absolute path")

    def test_dotdot_escape(self) -> None:
        self.refused(tar_of([(f"{self.r}/../../outside/x", "file", "x")]), "../ escape")

    def test_leading_dotdot(self) -> None:
        self.refused(tar_of([("../x", "file", "x")]), "leading ..")

    def test_sibling_prefix(self) -> None:
        self.refused(tar_of([(self.r + "-evil/x", "file", "x")]), "a sibling dir sharing the prefix")

    def test_symlink_out(self) -> None:
        self.refused(tar_of([(f"{self.r}/leak", "sym", "/etc/passwd")]), "absolute symlink")

    def test_symlink_chain_through_self_link(self) -> None:
        # d -> . then d/l -> ../x: textually inside, physically l -> ../x (outside). Must be refused.
        self.refused(tar_of([(f"{self.r}/d", "sym", "."), (f"{self.r}/d/l", "sym", "../x")]), "symlink chain")

    def test_relative_symlink_inside_is_also_refused(self) -> None:
        self.refused(tar_of([(f"{self.r}/a", "file", "a"), (f"{self.r}/b", "sym", "a")]), "any link")

    def test_hardlink(self) -> None:
        self.refused(tar_of([(f"{self.r}/h", "hard", "etc/passwd")]), "hard link")

    def test_device_and_fifo(self) -> None:
        self.refused(tar_of([(f"{self.r}/dev", "chr", None)]), "character device")
        self.refused(tar_of([(f"{self.r}/p", "fifo", None)]), "fifo")

    def test_corrupt_archive(self) -> None:
        self.refused(b"not a tar archive at all" * 100, "corrupt archive")

    def test_size_cap(self) -> None:
        os.environ["SDLC_K8S_MAX_OUTPUT_MB"] = "1"
        self.refused(tar_of([(f"{self.r}/big", "file", b"\0" * (2 * 1024 * 1024))]), "over the size cap")

    def test_entry_cap(self) -> None:
        os.environ["SDLC_K8S_MAX_OUTPUT_ENTRIES"] = "5"
        self.refused(tar_of([(f"{self.r}/f{i}", "file", "x") for i in range(10)]), "over the entry cap")

    def test_setuid_bits_are_dropped(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            ti = tarfile.TarInfo(f"{self.r}/s")
            ti.size, ti.mode = 1, 0o4755
            t.addfile(ti, io.BytesIO(b"x"))
        k8s.safe_extract(buf.getvalue(), str(self.rw))
        self.assertFalse(os.stat(self.rw / "s").st_mode & 0o4000, "setuid must not survive extraction")


class Pack(Base):
    def names(self, dirs, mounts, skip) -> list[str]:
        out = self.tmp / "in.tar"
        k8s.pack(dirs, mounts, skip, {}, out)
        with tarfile.open(out, mode="r:") as t:
            return t.getnames()

    def test_mount_points_are_left_out_and_contents_kept(self) -> None:
        src = self.tmp / "proj"
        (src / "a.kt").write_text("a")
        (self.rw / "b.txt").write_text("b")
        os.symlink("a.kt", src / "link")
        names = self.names([str(src)], [str(src), str(self.rw)], [])
        p = str(src).lstrip("/")
        self.assertNotIn(p, names, "the top mount point itself is not in the archive")
        self.assertNotIn(self.r, names, "a nested mount point is not in the archive")
        self.assertIn(f"{p}/a.kt", names)
        self.assertIn(f"{self.r}/b.txt", names)
        self.assertIn(f"{p}/link", names, "an input link is sent as a link, not followed")

    def test_denied_and_nested_rw_subtrees_are_skipped(self) -> None:
        src = self.tmp / "proj"
        (src / "secret").mkdir()
        (src / "secret/key").write_text("k")
        (self.rw / "b.txt").write_text("b")
        names = self.names([str(src)], [str(src)], [str(src / "secret"), str(self.rw)])
        self.assertFalse(any("secret" in n for n in names), "a denied subtree must not be copied")
        self.assertFalse(any(n.startswith(self.r) for n in names), "the nested rw dir is copied separately")

    def test_unreadable_input_fails_instead_of_being_skipped(self) -> None:
        src = self.tmp / "proj"
        locked = src / "locked"
        locked.mkdir()
        (locked / "x").write_text("x")
        locked.chmod(0)
        try:
            with self.assertRaises(k8s.Fail):
                self.names([str(src)], [str(src)], [])
            with self.assertRaises(k8s.Fail):
                k8s.manifest(str(src), [])
        finally:
            locked.chmod(0o755)

    def test_manifest_sees_the_executable_bit_and_contents(self) -> None:
        src = self.tmp / "proj"
        (src / "run.sh").write_text("echo")
        a = k8s.manifest(str(src), [str(self.rw)])
        (src / "run.sh").chmod(0o755)
        b = k8s.manifest(str(src), [str(self.rw)])
        self.assertNotEqual(a, b, "the executable bit is part of the manifest")
        (src / "run.sh").chmod(0o700)
        self.assertEqual(b, k8s.manifest(str(src), [str(self.rw)]), "other mode bits are normalised away")
        (src / "run.sh").write_text("echo!")
        self.assertNotEqual(b, k8s.manifest(str(src), [str(self.rw)]), "contents are part of the manifest")

    def test_inputs_are_sent_world_readable_without_ownership(self) -> None:
        src = self.tmp / "proj"
        (src / "private.txt").write_text("p")
        (src / "private.txt").chmod(0o600)
        (src / "tool").write_text("t")
        (src / "tool").chmod(0o700)
        out = self.tmp / "in.tar"
        k8s.pack([str(src)], [], [str(self.rw)], {}, out)
        with tarfile.open(out, mode="r:") as t:
            m = {x.name.rsplit("/", 1)[-1]: x for x in t.getmembers()}
        self.assertEqual(m["private.txt"].mode, 0o644, "a 0600 input is readable by the other container user")
        self.assertEqual(m["tool"].mode, 0o755)
        self.assertEqual((m["tool"].uid, m["tool"].uname), (0, ""), "ownership is not sent")

    def test_special_files_fail_instead_of_hanging(self) -> None:
        src = self.tmp / "proj"
        os.mkfifo(src / "pipe")
        with self.assertRaises(k8s.Fail):
            k8s.manifest(str(src), [str(self.rw)])
        with self.assertRaises(k8s.Fail):
            self.names([str(src)], [str(src)], [str(self.rw)])

    def test_read_only_links_must_stay_inside_read_only_inputs(self) -> None:
        src = self.tmp / "proj"
        (src / "a.kt").write_text("a")
        (src / "secret").mkdir()
        (src / "ok").symlink_to("a.kt")
        k8s.check_ro_links([str(src)], [str(self.rw)], [str(src / "secret")])
        for name, target in (("into-rw", "build/Foo.kt"), ("into-deny", "secret/k"), ("outside", "/etc/passwd")):
            with self.subTest(name):
                link = src / name
                link.symlink_to(target)
                with self.assertRaises(k8s.Fail):
                    k8s.check_ro_links([str(src)], [str(self.rw)], [str(src / "secret")])
                link.unlink()

    def test_a_file_named_like_a_nonce_is_an_ordinary_input(self) -> None:
        (self.rw / ".sdlc-mount-check-config").write_text("mine")
        self.assertIn(f"{self.r}/.sdlc-mount-check-config", self.names([str(self.rw)], [str(self.rw)], []))
        self.assertIn(".sdlc-mount-check-config", k8s.manifest(str(self.rw), []))

    def test_extract_from_a_streamed_file(self) -> None:
        f = self.tmp / "out.tar"
        f.write_bytes(tar_of([(self.r, "dir", None), (f"{self.r}/r.xml", "file", "<r/>")]))
        k8s.safe_extract(f, str(self.rw))
        self.assertEqual((self.rw / "r.xml").read_text(), "<r/>")


class Reports(unittest.TestCase):
    """Reports are written by code under test and parsed on the laptop."""

    def parse(self, data: bytes):
        f = Path(tempfile.mkdtemp()) / "r.xml"
        f.write_bytes(data)
        try:
            return sdlc_tool.parse_report_xml(f)
        finally:
            shutil.rmtree(f.parent, ignore_errors=True)

    def test_plain_reports_parse(self) -> None:
        self.assertEqual(self.parse(b'<?xml version="1.0" encoding="UTF-8"?><testsuite tests="1"/>').tag, "testsuite")
        self.assertEqual(self.parse('<testsuite name="\u00e9"/>'.encode()).get("name"), "\u00e9")
        self.assertEqual(self.parse('<?xml version="1.0" encoding="utf-8"?><x>\u00e9</x>'.encode()).text, "\u00e9")

    def test_hostile_reports_are_refused(self) -> None:
        for what, data in {
            "UTF-16 with a DTD": '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'.encode("utf-16"),
            "UTF-16 without BOM": "<!DOCTYPE x><x/>".encode("utf-16-le"),
            "declared Latin-1": b'<?xml version="1.0" encoding="ISO-8859-1"?><x/>',
            "DTD": b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>',
            "invalid UTF-8": b"<x>\xff</x>",
            "malformed": b"<x><y></x>",
            "long declaration, other encoding": b'<?xml version="1.0"' + b" " * 300 + b'encoding="ISO-8859-1"?><x/>',
        }.items():
            with self.subTest(what), self.assertRaises(ValueError):
                self.parse(data)

    def test_size_cap(self) -> None:
        os.environ["SDLC_MAX_REPORT_MB"] = "1"
        try:
            with self.assertRaises(ValueError):
                self.parse(b"<x>" + b"a" * 2_000_000 + b"</x>")
        finally:
            os.environ.pop("SDLC_MAX_REPORT_MB")


class ClusterPolicies(unittest.TestCase):
    NS = {"kubernetes.io/metadata.name": "sdlc-runner"}

    def cnp(self, subject: dict, action: str) -> dict:
        return {"metadata": {"name": "c"}, "spec": {"subject": subject, "egress": [{"action": action}]}}

    def test_selectors(self) -> None:
        self.assertTrue(k8s.selects({}, self.NS), "an empty selector selects every namespace")
        self.assertFalse(k8s.selects(None, self.NS))
        self.assertFalse(k8s.selects({"matchLabels": {"team": "x"}}, self.NS))
        self.assertTrue(k8s.selects({"matchExpressions": [{"key": "team", "operator": "DoesNotExist"}]}, self.NS))
        self.assertTrue(k8s.selects({"matchExpressions": [{"key": "k", "operator": "Odd"}]}, self.NS), "unknown: fail closed")

    def test_cluster_policies_that_could_open_a_path(self) -> None:
        self.assertTrue(k8s.cluster_policy_opens(self.cnp({"namespaces": {}}, "Accept"), self.NS))
        self.assertTrue(k8s.cluster_policy_opens(self.cnp({"pods": {"namespaceSelector": {}, "podSelector": {}}}, "Pass"), self.NS))
        self.assertFalse(k8s.cluster_policy_opens(self.cnp({"namespaces": {}}, "Deny"), self.NS))
        self.assertFalse(k8s.cluster_policy_opens(self.cnp({"namespaces": {"matchLabels": {"team": "x"}}}, "Accept"), self.NS))
        self.assertTrue(k8s.cluster_policy_opens(self.cnp({"somethingNew": {}}, "Accept"), self.NS), "unknown subject: fail closed")


class Bypass(unittest.TestCase):
    def test_unverified_only_for_loopback_api_servers(self) -> None:
        saved = dict(os.environ)
        try:
            os.environ["SDLC_K8S_ALLOW_UNVERIFIED"] = "1"
            for server in ("https://127.0.0.1:58909", "https://localhost:6443", "https://[::1]:6443"):
                os.environ["SDLC_K8S_SERVER"] = server
                self.assertTrue(k8s.unverified_allowed(), server)
            for server in ("https://ABC.gr7.us-east-2.eks.amazonaws.com", "https://127.0.0.1.example.com", ""):
                os.environ["SDLC_K8S_SERVER"] = server
                with self.subTest(server), self.assertRaises(SystemExit):
                    k8s.unverified_allowed()
            os.environ["SDLC_K8S_ALLOW_UNVERIFIED"] = "0"
            self.assertFalse(k8s.unverified_allowed())
        finally:
            os.environ.clear()
            os.environ.update(saved)


_dspec = importlib.util.spec_from_file_location("dockerrun", ROOT / "scripts/docker/run.py")
assert _dspec is not None and _dspec.loader is not None
dockerrun = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(dockerrun)


class DockerEnv(unittest.TestCase):
    def test_code_containers_get_only_passenv(self) -> None:
        saved = dict(os.environ)
        try:
            os.environ.update({"GRADLE_ENTERPRISE_ACCESS_KEY": "secret", "SDLC_NOTIFY_CMD": "x", "MY_FLAG": "1",
                               "SDLC_DOCKER_PASSENV": "MY_FLAG", "AWS_SECRET_ACCESS_KEY": "secret"})
            code = dockerrun.forwarded_env({"HOME": "/home/sdlc"}, role=False)
            self.assertEqual(code, {"MY_FLAG": "1"}, "code under test sees only explicitly passed variables")
            os.environ.update({"SDLC_MODEL_build": "m", "SDLC_SLACK_TOKEN": "t", "SDLC_API_KEY": "k"})
            role = dockerrun.forwarded_env({"HOME": "/home/sdlc"}, role=True)
            self.assertIn("SDLC_MODEL_build", role, "model roles still get the harness settings")
            for k in ("AWS_SECRET_ACCESS_KEY", "GRADLE_ENTERPRISE_ACCESS_KEY", "SDLC_NOTIFY_CMD", "SDLC_SLACK_TOKEN",
                      "SDLC_API_KEY"):
                self.assertNotIn(k, role, f"{k}: a role can send what it reads to the model provider")
        finally:
            os.environ.clear()
            os.environ.update(saved)


class KeepCopyOut(unittest.TestCase):
    """SDLC_K8S_KEEP: the in-pod script tars only the listed outputs (gzip); safe_extract restores just those."""

    def run_keep(self, paths: list[str], out: Path) -> None:
        with open(out, "wb") as f:
            subprocess.run([sys.executable, "-I", "-S", "-c", k8s.KEEP_IN_POD, json.dumps(paths)], stdout=f, check=True)

    def test_only_the_kept_outputs_come_back(self) -> None:
        d = Path(tempfile.mkdtemp())
        rw = d / "proj" / "build"
        (rw / "reports/pitest").mkdir(parents=True)
        (rw / "reports/pitest/mutations.xml").write_text("<mutations/>")
        (rw / "kotlin/caches").mkdir(parents=True)
        (rw / "kotlin/caches/big.bin").write_bytes(b"x" * 100000)
        tar = d / "out.tgz"
        self.run_keep([str(rw / "reports/pitest/mutations.xml"), str(rw / "missing/dir")], tar)
        # The host's rw dir: restored from the archive alone (replace semantics), so only kept outputs remain.
        (rw / "stale.txt").write_text("from an earlier run")
        k8s.safe_extract(tar, str(rw))
        self.assertEqual((rw / "reports/pitest/mutations.xml").read_text(), "<mutations/>")
        self.assertFalse((rw / "kotlin").exists(), "unkept outputs are not copied back")
        self.assertFalse((rw / "stale.txt").exists())
        self.assertLess(tar.stat().st_size, 2000, "compressed, and without the cache")

    def test_a_link_among_kept_outputs_is_refused(self) -> None:
        d = Path(tempfile.mkdtemp())
        rw = d / "proj" / "build"
        (rw / "reports").mkdir(parents=True)
        os.symlink("/etc/passwd", rw / "reports/leak")
        tar = d / "out.tgz"
        self.run_keep([str(rw / "reports")], tar)
        with self.assertRaises(SystemExit):
            k8s.safe_extract(tar, str(rw))


class RoleCpus(unittest.TestCase):
    def test_role_containers_are_capped_when_code_runs_on_k8s(self) -> None:
        saved = dict(os.environ)
        try:
            os.environ.pop("SDLC_DOCKER_ROLE_CPUS", None)
            os.environ.update({"SDLC_DOCKER_CPUS": "6", "SDLC_SANDBOX_BACKEND_CODE": "k8s"})
            self.assertEqual(dockerrun.role_cpus(True), "2", "a model role builds in its warm pod, not here")
            self.assertEqual(dockerrun.role_cpus(False), "6")
            os.environ["SDLC_SANDBOX_BACKEND_CODE"] = ""
            self.assertEqual(dockerrun.role_cpus(True), "6", "without k8s, roles build locally and keep the CPUs")
        finally:
            os.environ.clear()
            os.environ.update(saved)


class Env(unittest.TestCase):
    def test_only_allowlisted_variables_reach_the_pod(self) -> None:
        saved = dict(os.environ)
        try:
            os.environ.update({"GRADLE_ENTERPRISE_ACCESS_KEY": "secret", "AWS_SECRET_ACCESS_KEY": "secret",
                               "SDLC_MODEL_build": "x", "MY_FLAG": "1", "SDLC_K8S_PASSENV": "MY_FLAG"})
            env = dict(e.split("=", 1) for e in k8s.env_for_pod({"mounts": []}))
            self.assertNotIn("GRADLE_ENTERPRISE_ACCESS_KEY", env)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
            self.assertNotIn("SDLC_MODEL_build", env)
            self.assertEqual(env.get("MY_FLAG"), "1", "explicitly passed variables do reach the pod")
        finally:
            os.environ.clear()
            os.environ.update(saved)


class Context(unittest.TestCase):
    def test_production_contexts_are_refused(self) -> None:
        for ctx in ("prd-cluster", "arn:aws:eks:us-east-2:1:cluster/prd-cluster-eks", "app-prod",
                    "production"):
            os.environ["SDLC_K8S_CONTEXT"] = ctx
            with self.assertRaises(SystemExit, msg=ctx):
                k8s.context()
        os.environ["SDLC_K8S_CONTEXT"] = "dev-cluster"
        self.assertEqual(k8s.context(), "dev-cluster")

    def test_check_never_exits(self) -> None:
        os.environ.pop("SDLC_K8S_CONTEXT", None)
        self.assertIn("configuration incomplete", k8s.check() or "")

    def test_cluster_identity_is_pinned_by_server(self) -> None:
        kc = Path(tempfile.mkdtemp()) / "config"
        kc.write_text("apiVersion: v1\nkind: Config\nclusters:\n- name: c\n  cluster: {server: 'https://real.example:443'}\n"
                      "contexts:\n- name: dev-x\n  context: {cluster: c, user: u}\nusers:\n- name: u\n  user: {}\n"
                      "current-context: dev-x\n")
        saved = dict(os.environ)
        try:
            os.environ.update({"KUBECONFIG": str(kc), "SDLC_K8S_CONTEXT": "dev-x"})
            os.environ["SDLC_K8S_SERVER"] = "https://other.example:443"
            self.assertIn("not the pinned", k8s.cluster_identity_problem() or "", "a context aimed elsewhere is refused")
            os.environ["SDLC_K8S_SERVER"] = "https://real.example:443"
            self.assertIsNone(k8s.cluster_identity_problem())
            os.environ.pop("SDLC_K8S_SERVER")
            self.assertIn("SDLC_K8S_SERVER", k8s.cluster_identity_problem() or "")
        finally:
            os.environ.clear()
            os.environ.update(saved)
            shutil.rmtree(kc.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
