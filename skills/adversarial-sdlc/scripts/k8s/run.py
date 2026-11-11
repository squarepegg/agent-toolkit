#!/usr/bin/env python3
"""Kubernetes backend for sandbox.sh: code under test runs in a pod (docs/specs/k8s-runner-threat-model.md).

  run.py [--rw DIR]... [--ro DIR]... [--deny DIR]... [--net local|none] -- COMMAND [ARGS]
  run.py --check          exit 0 if the backend is usable and verified, else 1 (prints why)
  run.py --verify [PIDS]  isolation probe: a runner pod tries every escape; a pass is recorded
  run.py --ids            print the pinned image reference (SDLC_K8S_IMAGE=...)
  run.py --capacity       print the namespace's CPU quota (the gate sizes its shards from it)
  run.py --warm           start runner nodes ahead of a gate: placeholder pods (one per SDLC_K8S_WARM_NODE_CPU, default
                          32, of the quota) that end after SDLC_K8S_WARM_TTL s (default 900); sdlc.sh calls it before
                          the build and attack roles
  run.py --unwarm         remove the placeholders and wait until they are gone (the gate calls it first)

Only code under test comes here (gate steps, repro runs, mutant checks); model roles stay on docker.
Threat model and guarantees G1-G8: docs/specs/k8s-runner-threat-model.md .

One Job per call (Job-owned pods: AWS VPC CNI network policy is not reliable for standalone pods), deleted
and waited for afterwards. The pod has three containers sharing memory-backed emptyDir volumes:
- `mountpoints` (init, loader UID) creates the mount points of writable dirs nested in read-only ones;
- `load` (loader UID) receives the read-only inputs (`kubectl exec ... tar -x`), sent world-readable;
- `run` (runner UID) mounts those volumes readOnly (kernel-enforced) and the --rw dirs writable, and runs
  the command. Both UIDs are fresh per call (per-user kernel quotas are not shared between runners); PID 1
  of each container writes nothing to the container log; RLIMIT_FSIZE bounds every file, including the
  /dev/termination-log that Kubernetes keeps on the node disk. The code under test cannot change its inputs, and nothing it writes touches the node disk
  (all volumes are tmpfs, bounded by the containers' memory limits).
Before the command: a barrier probe (the pod must reach nothing: kubelet, node agents, IMDS, API, DNS,
internet), and a manifest check (the pod's copy equals the host's, file by file). After: outputs are
streamed with byte caps and replace the --rw dirs only if every entry is a regular file or directory
inside them. Every failure of the sandbox itself is exit 97; the command's own exit code otherwise.
The cluster is pinned by API server URL (SDLC_K8S_SERVER) and, optionally, CA hash (SDLC_K8S_CA_SHA256);
context names that look like production are refused as well.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent.parent
C_HOME, C_TMP = "/home/sdlc", "/tmp"  # noqa: S108 - container paths, private memory-backed volumes
STATE = Path(os.environ.get("SDLC_K8S_STATE") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "sdlc-k8s")
POLICY = "sdlc-runner-pods"  # the ValidatingAdmissionPolicy (and binding) the namespace relies on
IMAGE_PATH = "/usr/lib/jvm/sdlc-java-21/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PY = ["/usr/bin/python3", "-I", "-S"]  # isolated: no cwd, user site or env on sys.path
NONCE = ".sdlc-mount-check-"  # + the Job name: entry.py's rw mount probe file of one call
ENTRY_OK = f"{C_TMP}/.sdlc-entry-ok"  # written by entry.py when its checks passed and the command starts
ENTRY_STATUS = f"{C_TMP}/.sdlc-entry-status"  # written by entry.py with the command's exit status
START = time.monotonic()


class Fail(Exception):
    """A failure of the sandbox itself (exit 97), as opposed to the command's own result."""


class Interrupted(Exception):
    """SIGINT/SIGTERM: cleaned up through the normal path (97 if teardown cannot be confirmed)."""

    def __init__(self, sig: int) -> None:
        super().__init__(sig)
        self.sig = sig


def die(msg: str, code: int = 97) -> NoReturn:
    print(f"sandbox.sh (k8s): {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def num(name: str, default: int) -> int:
    v = os.environ.get(name, str(default))
    if not v.isdigit() or int(v) <= 0:
        die(f"{name} must be a positive integer, got {v!r}", 2)
    return int(v)


def cfg(name: str, default: str | None = None) -> str:
    v = os.environ.get(name) or default
    if v is None:
        die(f"{name} is not set")
    return v


def trace(what: str) -> None:
    """SDLC_K8S_TRACE=1: phase timings on stderr (seconds since this call started)."""
    if os.environ.get("SDLC_K8S_TRACE") == "1":
        print(f"sandbox.sh (k8s) trace: {time.monotonic() - START:7.1f}s {what}", file=sys.stderr, flush=True)


def remaining() -> float:
    """Seconds left of this call's overall deadline (SDLC_K8S_DEADLINE)."""
    left = num("SDLC_K8S_DEADLINE", 7200) - (time.monotonic() - START)
    if left <= 0:
        raise Fail("overall deadline reached")
    return left


# ------------------------------------------------------------------ configuration and identity

def image_ref() -> str:
    """Pinned by digest: SDLC_K8S_IMAGE, else the reference k8s/build.sh recorded."""
    ref = os.environ.get("SDLC_K8S_IMAGE", "")
    env = STATE / "images.env"
    if not ref and env.is_file():
        ref = next((ln.split("=", 1)[1].strip() for ln in env.read_text().splitlines() if ln.startswith("SDLC_K8S_IMAGE=")), "")
    if not ref:
        die("no image: run scripts/k8s/build.sh, or set SDLC_K8S_IMAGE")
    if "@sha256:" not in ref and os.environ.get("SDLC_K8S_ALLOW_TAG") != "1":
        die(f"image must be pinned by digest (repo@sha256:...): {ref} (SDLC_K8S_ALLOW_TAG=1 for local test clusters)")
    return ref


def context() -> str:
    ctx = cfg("SDLC_K8S_CONTEXT")
    if re.search(r"(^|[^a-z])(prd|prod|production)([^a-z]|$)", ctx, re.I) and os.environ.get("SDLC_K8S_ALLOW_PROD") != "1":
        die(f"refusing a production-looking context: {ctx}")
    return ctx


def namespace() -> str:
    return cfg("SDLC_K8S_NAMESPACE", "sdlc-runner")


def cluster_identity_problem() -> str | None:
    """The context must point at the pinned API server (and CA): a context name is not an identity."""
    want = os.environ.get("SDLC_K8S_SERVER", "")
    if not want:
        return "SDLC_K8S_SERVER (the expected API server URL) is not set"
    try:
        p = subprocess.run(["kubectl", "config", "view", "--raw", "--minify", "--context", context(), "-o", "json"],
                           capture_output=True, text=True, check=False, timeout=30)
        cl = json.loads(p.stdout)["clusters"][0]["cluster"]
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as e:
        return f"cannot read the kubeconfig entry of {context()}: {e}"
    if cl.get("server") != want:
        return f"context {context()} points at {cl.get('server')}, not the pinned {want}"
    ca = os.environ.get("SDLC_K8S_CA_SHA256", "")
    if ca and hashlib.sha256(base64.b64decode(cl.get("certificate-authority-data", ""))).hexdigest() != ca:
        return f"the CA of {context()} does not match SDLC_K8S_CA_SHA256"
    return None


def kubectl(*args: str, input_bytes: bytes | None = None, check: bool = True, timeout: float | None = None,
            ns: bool = True) -> subprocess.CompletedProcess:
    cmd = ["kubectl", "--context", context(), *(["--namespace", namespace()] if ns else []), *args]
    try:
        p = subprocess.run(cmd, input=input_bytes, capture_output=True, check=False,
                           timeout=min(timeout or 120, remaining()))
    except subprocess.TimeoutExpired as e:
        raise Fail(f"kubectl {args[0]} timed out") from e
    if check and p.returncode != 0:
        raise Fail(f"kubectl {' '.join(args[:2])} failed: {p.stderr.decode(errors='replace').strip()[:500]}")
    return p


def security_problem() -> str | None:
    """The namespace must carry the controls the threat model relies on (a partial sync must not pass)."""
    try:
        nsobj = json.loads(kubectl("get", "namespace", namespace(), "-o", "json", ns=False, timeout=30).stdout)
        if nsobj["metadata"].get("labels", {}).get("pod-security.kubernetes.io/enforce") != "restricted":
            return "the namespace does not enforce Pod Security 'restricted'"
        pols = json.loads(kubectl("get", "networkpolicies", "-o", "json", timeout=30).stdout)["items"]
        if not any(p["spec"].get("podSelector") == {} and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"}
                   for p in pols):
            return "no deny-all NetworkPolicy (empty podSelector, Ingress+Egress, no rules)"
        # Policies are additive: any rule anywhere in the namespace would open a path.
        opened = [p["metadata"]["name"] for p in pols if p["spec"].get("ingress") or p["spec"].get("egress")]
        if opened:
            return f"NetworkPolicies in the namespace allow traffic: {', '.join(opened)}"
        if optional_items("applicationnetworkpolicies.networking.k8s.aws", namespaced=True):
            return "ApplicationNetworkPolicies exist in the namespace (they can allow traffic)"
        for cnp in optional_items("clusternetworkpolicies.networking.k8s.aws", namespaced=False):
            if cluster_policy_opens(cnp, nsobj["metadata"].get("labels", {})):
                return f"ClusterNetworkPolicy {cnp['metadata']['name']} may allow traffic for the namespace"
        why = admission_problem()
        if why:
            return why
        sa = json.loads(kubectl("get", "serviceaccount", "sdlc-runner", "-o", "json", timeout=30).stdout)
        if sa.get("automountServiceAccountToken") is not False:
            return "service account sdlc-runner mounts a token"
        if not json.loads(kubectl("get", "resourcequota", "-o", "json", timeout=30).stdout)["items"]:
            return "no ResourceQuota in the namespace"
    except (Fail, ValueError, KeyError) as e:
        return f"cannot verify the namespace controls: {e}"
    return None


def optional_items(resource: str, namespaced: bool) -> list[dict]:
    """Items of a resource type the cluster may not have (missing type: none; any other error: Fail)."""
    p = kubectl("get", resource, "-o", "json", check=False, ns=namespaced, timeout=30)
    if p.returncode != 0:
        if b"doesn't have a resource type" in p.stderr:
            return []
        raise Fail(f"cannot list {resource}: {p.stderr.decode(errors='replace').strip()[:300]}")
    return json.loads(p.stdout)["items"]


def selects(sel: dict | None, labels: dict) -> bool:
    """Does a LabelSelector match these labels? Unknown operators count as a match (fail closed)."""
    if sel is None:
        return False
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in sel.get("matchExpressions") or []:
        k, op, vals = e.get("key"), e.get("operator"), e.get("values") or []
        if (op == "In" and labels.get(k) not in vals) or (op == "NotIn" and labels.get(k) in vals) \
                or (op == "Exists" and k not in labels) or (op == "DoesNotExist" and k in labels):
            return False
    return True


def cluster_policy_opens(cnp: dict, ns_labels: dict) -> bool:
    """A ClusterNetworkPolicy that could apply to the namespace and has a rule other than Deny."""
    spec = cnp.get("spec", {})
    subject = spec.get("subject") or {}
    if set(subject) - {"namespaces", "pods"}:
        return True
    applies = selects(subject.get("namespaces"), ns_labels) or \
        selects((subject.get("pods") or {}).get("namespaceSelector"), ns_labels)
    rules = (spec.get("ingress") or []) + (spec.get("egress") or [])
    return applies and any(r.get("action") != "Deny" for r in rules)


def admission_problem() -> str | None:
    try:
        vap = json.loads(kubectl("get", "validatingadmissionpolicy", POLICY, "-o", "json", ns=False, timeout=30).stdout)
        vb = json.loads(kubectl("get", "validatingadmissionpolicybinding", POLICY, "-o", "json", ns=False, timeout=30).stdout)
    except (Fail, ValueError) as e:
        return f"the admission policy {POLICY} or its binding is missing or unreadable: {e}"
    if vap["spec"].get("failurePolicy") != "Fail" or vb["spec"].get("policyName") != POLICY \
            or "Deny" not in vb["spec"].get("validationActions", []):
        return f"the admission policy {POLICY} is not enforcing (failurePolicy Fail, binding with Deny)"
    return None


def security_fingerprint() -> str:
    """Hash of the cluster objects the isolation depends on: a verification record is valid only for them."""
    parts = []
    for res, name, nsd in (("networkpolicies", "", True), ("validatingadmissionpolicy", POLICY, False),
                           ("validatingadmissionpolicybinding", POLICY, False),
                           ("ec2nodeclasses.karpenter.k8s.aws", os.environ.get("SDLC_K8S_NODE_POOL", "sdlc"), False),
                           ("nodepools.karpenter.sh", os.environ.get("SDLC_K8S_NODE_POOL", "sdlc"), False)):
        p = kubectl("get", res, *([name] if name else []), "-o", "json", check=False, ns=nsd, timeout=30)
        if p.returncode != 0 and b"doesn't have a resource type" not in p.stderr and b"NotFound" not in p.stderr:
            raise Fail(f"cannot read {res} {name}: {p.stderr.decode(errors='replace').strip()[:300]}")
        obj = json.loads(p.stdout) if p.returncode == 0 else None
        items = (obj or {}).get("items", [obj] if obj else [])
        parts.append([{"name": i["metadata"]["name"], "spec": i.get("spec")} for i in items])
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def cpu_value(v: str) -> float:
    return float(v[:-1]) / 1000 if v.endswith("m") else float(v)


def capacity() -> int:
    """vCPUs the namespace may use at once: the smallest of its ResourceQuotas' cpu limits (requests and limits),
    0 when none can be read (the caller falls back to its default)."""
    try:
        qs = json.loads(kubectl("get", "resourcequota", "-o", "json", timeout=30).stdout)["items"]
        caps = [cpu_value(str(q["status"]["hard"][k])) for q in qs for k in ("requests.cpu", "limits.cpu", "cpu")
                if k in q.get("status", {}).get("hard", {})]
        return int(min(caps)) if caps else 0
    except (Fail, ValueError, KeyError):
        return 0


WARM_LABEL = "sdlc-warm"


def warm() -> int:
    """Bring up runner nodes ahead of a gate: a placeholder Job of one large pod per node the gate may need (the
    namespace's CPU capacity / SDLC_K8S_WARM_NODE_CPU), running the pinned runner image's `sleep` (no code under
    test). Karpenter starts the nodes and they pull the image; the gate removes the placeholders before its own pods
    (they share the quota), and the empty nodes stay for the NodePool's consolidateAfter. The Job ends by itself
    after SDLC_K8S_WARM_TTL seconds (default 900). Best effort: exit 0 unless the backend is unusable."""
    why = check()
    if why:
        print(f"warm: backend unusable: {why}", file=sys.stderr)
        return 1
    node_cpu = num("SDLC_K8S_WARM_NODE_CPU", 32)
    # Leave room for one role's warm pod (k8s/broker.py), which starts at the same time and must not wait for
    # the placeholders to expire.
    room = num("SDLC_K8S_WARM_ROOM", 16)
    pods = max(1, (capacity() - room) // node_cpu) if capacity() else 1
    ttl = num("SDLC_K8S_WARM_TTL", 900)
    unwarm()
    run = Run()
    run.pick_uids()
    spec = job_spec(run.job.replace("sdlc-", "sdlc-warm-", 1), image_ref(), [], [], ttl, run.uids)
    pod = spec["spec"]["template"]["spec"]
    pod["initContainers"] = []
    pod["containers"] = [container("warm", run.uids[0], image_ref(), idle(ttl), [], f"{node_cpu - 4}", "2Gi")]
    pod["volumes"] = []
    for meta in (spec["metadata"], spec["spec"]["template"]["metadata"]):
        meta["labels"] = {"sdlc": WARM_LABEL, "app.kubernetes.io/part-of": "adversarial-sdlc"}
    spec["spec"].update({"parallelism": pods, "completions": pods, "activeDeadlineSeconds": ttl,
                         "ttlSecondsAfterFinished": 60})
    try:
        kubectl("apply", "-f", "-", input_bytes=json.dumps(spec).encode())
    except Fail as e:
        print(f"warm: {e}", file=sys.stderr)
        return 0
    print(f"warm: {pods} placeholder pod(s) of {node_cpu - 4} CPUs for {ttl} s ({spec['metadata']['name']})")
    return 0


def unwarm() -> int:
    """Remove the placeholders and wait until their pods are gone (they hold quota the gate needs)."""
    try:
        kubectl("delete", "jobs", "-l", f"sdlc={WARM_LABEL}", "--ignore-not-found", "--cascade=foreground",
                "--wait=true", "--timeout=90s", timeout=120)
        left = kubectl("get", "pods", "-l", f"sdlc={WARM_LABEL}", "-o", "name", timeout=30).stdout.split()
        if left:
            kubectl("delete", "pods", "-l", f"sdlc={WARM_LABEL}", "--grace-period=1", "--wait=true", "--timeout=60s",
                    timeout=90)
    except Fail as e:
        print(f"unwarm: {e}", file=sys.stderr)
        return 1
    return 0


def verified_record() -> Path:
    key = hashlib.sha256(f"{context()}\0{namespace()}\0{image_ref()}".encode()).hexdigest()[:16]
    return STATE / "verified" / f"{key}.json"


def unverified_allowed() -> bool:
    """SDLC_K8S_ALLOW_UNVERIFIED=1 skips the isolation record and the per-call barrier. Honoured only for a local
    test cluster (API server on loopback, e.g. kind), never for a remote one."""
    if os.environ.get("SDLC_K8S_ALLOW_UNVERIFIED") != "1":
        return False
    host = re.sub(r"^https?://", "", os.environ.get("SDLC_K8S_SERVER", "")).split("/")[0].rsplit(":", 1)[0].strip("[]")
    if host not in ("127.0.0.1", "localhost", "::1"):
        die(f"SDLC_K8S_ALLOW_UNVERIFIED=1 is only for local test clusters (API server on loopback), not {host or '?'}")
    return True


def check() -> str | None:
    """Why the backend is unusable, or None. Never exits."""
    try:
        context()
        image_ref()
        if os.environ.get("SDLC_K8S_NODE_POOL", "sdlc") == "" and os.environ.get("SDLC_K8S_ALLOW_ANY_NODE") != "1":
            return "SDLC_K8S_NODE_POOL is empty (runners would land on nodes without the sdlc firewall)"
        why = cluster_identity_problem()
        if why:
            return why
        with ThreadPoolExecutor(2) as pool:
            sec = pool.submit(security_problem)
            fp = pool.submit(security_fingerprint) if not unverified_allowed() else None
            why = sec.result()
            fingerprint = fp.result() if fp else None
        if why:
            return why
        if not unverified_allowed():
            rec = json.loads(verified_record().read_text())
            if time.time() - rec.get("at", 0) > num("SDLC_K8S_VERIFY_MAX_AGE_DAYS", 7) * 86400:
                return "the last isolation probe is too old (run scripts/k8s/run.py --verify)"
            if rec.get("fingerprint") != fingerprint:
                return "the cluster's security configuration changed since the last isolation probe (run --verify)"
    except SystemExit:
        return "configuration incomplete (SDLC_K8S_CONTEXT, image)"
    except Fail as e:
        return str(e)
    except (OSError, ValueError) as e:
        if isinstance(e, FileNotFoundError) and "kubectl" in str(e):
            return "kubectl not found"
        return "no isolation probe has passed for this context and image (run scripts/k8s/run.py --verify)"
    return None


# ------------------------------------------------------------------ arguments and file sets

def parse(argv: list[str]):
    rw: list[str] = []
    ro: list[str] = []
    deny: list[str] = []
    net = "local"
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return rw, ro, deny, net, argv[i + 1:]
        if a in ("--rw", "--ro", "--deny", "--net") and i + 1 < len(argv):
            v = argv[i + 1]
            {"--rw": rw, "--ro": ro, "--deny": deny}.get(a, []).append(os.path.realpath(v)) if a != "--net" else None
            if a == "--net":
                net = v
            i += 2
            continue
        die(f"unknown argument {a}", 2)
    die("missing -- COMMAND", 2)


def under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def walk(root: str, skip: list[str]):
    """os.walk that fails on unreadable subtrees and prunes the skipped (nested rw or denied) paths."""
    def boom(e: OSError) -> None:
        raise Fail(f"cannot read input {e.filename}: {e.strerror}")
    for dirpath, dirnames, filenames in os.walk(root, onerror=boom):
        remaining()
        dirnames[:] = sorted(d for d in dirnames if not any(under(os.path.join(dirpath, d), s) for s in skip))
        files = sorted(f for f in filenames if not any(under(os.path.join(dirpath, f), s) for s in skip))
        for f in files:
            mode = os.lstat(os.path.join(dirpath, f)).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise Fail(f"special file in the inputs (only files, directories and links): {os.path.join(dirpath, f)}")
        yield dirpath, dirnames, files


def manifest(root: str, skip: list[str]) -> dict[str, str]:
    """Every entry under root: type, executable bit (what normalise() sends) and content hash."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in walk(root, skip):
        for d in dirnames:
            p = os.path.join(dirpath, d)
            out[os.path.relpath(p, root)] = "L:" + os.readlink(p) if os.path.islink(p) else "d"
        for f in filenames:
            p = os.path.join(dirpath, f)
            if os.path.islink(p):
                out[os.path.relpath(p, root)] = "L:" + os.readlink(p)
                continue
            try:
                with open(p, "rb") as fh:
                    h = hashlib.sha256(fh.read()).hexdigest()
            except OSError as e:
                raise Fail(f"cannot read input {p}: {e}") from e
            out[os.path.relpath(p, root)] = f"f:{'x' if os.stat(p).st_mode & 0o100 else '-'}:{h}"
    return out


# Copy-out of selected outputs (SDLC_K8S_KEEP): a gzip tar of the listed paths that exist, with links kept as links
# (the host refuses them). Written to stdout.
KEEP_IN_POD = r'''
import json, os, sys, tarfile
paths = json.loads(sys.argv[1])
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz", compresslevel=1) as t:
    for p in paths:
        if os.path.lexists(p):
            t.add(p, arcname=p.lstrip("/"), recursive=True)
'''


MANIFEST_IN_POD = r'''
import hashlib, json, os, sys
plan, nonces = json.loads(sys.argv[1]), set(json.loads(sys.argv[2]))
u = lambda c, p: c == p or c.startswith(p.rstrip("/") + "/")
res = {}
for r, skips in plan.items():
    out = {}
    for dp, dn, fn in os.walk(r):
        dn[:] = sorted(d for d in dn if not any(u(os.path.join(dp, d), s) for s in skips))
        for d in dn:
            p = os.path.join(dp, d); out[os.path.relpath(p, r)] = ("L:" + os.readlink(p)) if os.path.islink(p) else "d"
        for f in sorted(fn):
            p = os.path.join(dp, f)
            if any(u(p, s) for s in skips) or p in nonces:
                continue
            if os.path.islink(p):
                out[os.path.relpath(p, r)] = "L:" + os.readlink(p); continue
            out[os.path.relpath(p, r)] = "f:%s:%s" % ("x" if os.stat(p).st_mode & 0o100 else "-", hashlib.sha256(open(p, "rb").read()).hexdigest())
    res[r] = out
print(json.dumps(res))
'''


def normalise(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Inputs are written by one container user and read by another: every entry becomes world-readable, and only
    the owner's executable bit is kept (what builds depend on). Ownership is not sent."""
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o755 if ti.isdir() or ti.mode & 0o100 else 0o644
    return ti


def check_ro_links(ro: list[str], rw: list[str], deny: list[str]) -> None:
    """A link in a read-only input must resolve inside the read-only inputs: one into a writable dir (or out of
    the inputs) would let the code under test change what the read-only tree shows."""
    for root in ro:
        for dirpath, dirnames, filenames in walk(root, rw + deny):
            for n in filenames + dirnames:
                p = os.path.join(dirpath, n)
                if os.path.islink(p):
                    t = os.path.realpath(p)
                    if not any(under(t, r) for r in ro) or any(under(t, w) for w in rw + deny):
                        raise Fail(f"a read-only input link resolves outside the read-only inputs: {p} -> {t}")


def pack(dirs: list[str], mounts: list[str], skip: list[str], extra_files: dict[str, bytes], out: Path) -> None:
    """A tar of dirs at their absolute paths (minus the leading /). Mount points themselves are left out
    (emptyDir roots: the container user cannot set their modes); skipped subtrees are not included."""
    mset = set(mounts)
    with tarfile.open(out, mode="w") as t:
        for top in sorted(dirs):
            for dirpath, dirnames, filenames in walk(top, skip):
                if dirpath not in mset:
                    t.add(dirpath, arcname=dirpath.lstrip("/"), recursive=False, filter=normalise)
                for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
                    p = os.path.join(dirpath, name)
                    t.add(p, arcname=p.lstrip("/"), recursive=False, filter=normalise)
        for path, data in extra_files.items():
            ti = tarfile.TarInfo(path.lstrip("/"))
            ti.size, ti.mode = len(data), 0o644
            t.addfile(ti, io.BytesIO(data))


def safe_extract(data: bytes | Path, rw_dir: str) -> None:
    """Replace the host's --rw dir with the pod's, accepting only regular files and directories.

    The archive is written by the adversary. Links are refused outright (Gradle, JUnit, Pitest and detekt write
    none): a textual check of link targets cannot see that a path runs through another link (`d -> .` then
    `d/l -> ../x` lands at `l -> ../x`, which a later extraction could write through). The archive goes into a
    fresh staging directory, so tarfile's own containment check applies, and the staged tree then replaces the
    --rw dir, as a bind mount would have left it (deletions included).
    """
    max_out = num("SDLC_K8S_MAX_OUTPUT_MB", 2048) * 1024 * 1024
    max_entries = num("SDLC_K8S_MAX_OUTPUT_ENTRIES", 500000)
    size = os.path.getsize(data) if isinstance(data, Path) else len(data)
    if size > max_out:
        die(f"output of {rw_dir} exceeds {max_out // 1048576} MB")
    root = rw_dir.lstrip("/")
    stage = tempfile.mkdtemp(prefix=".sdlc-k8s-out-", dir=os.path.dirname(rw_dir))
    try:
        try:
            source: dict = {"name": data} if isinstance(data, Path) else {"fileobj": io.BytesIO(data)}
            with tarfile.open(mode="r:*", **source) as t:  # plain or gzip (outputs are copied out compressed)
                members, total = [], 0
                for n, m in enumerate(t, 1):
                    if n % 1000 == 0:
                        remaining()
                    if n > max_entries:
                        die(f"output of {rw_dir} has more than {max_entries} entries")
                    name = os.path.normpath(m.name)
                    if m.name.startswith("/") or name.split("/")[0] == ".." or not (name == root or name.startswith(root + "/")):
                        die(f"refused output entry outside {rw_dir}: {m.name}")
                    if not (m.isfile() or m.isdir()):
                        die(f"refused output entry (only regular files and directories are accepted): {m.name}")
                    total += m.size
                    if total > max_out:
                        die(f"output of {rw_dir} expands beyond {max_out // 1048576} MB")
                    m.name = os.path.relpath(name, root)
                    members.append(m)
                # Python 3.12+ "data" filter: the destination is the empty stage, so containment is meaningful.
                t.extractall(stage, members=members, filter="data")
        except tarfile.TarError as e:
            die(f"unreadable output archive for {rw_dir}: {e}")
        for dirpath, dirnames, filenames in os.walk(stage):
            for n in dirnames + filenames:
                if os.path.islink(os.path.join(dirpath, n)):
                    die(f"refused output: a link appeared in the staged tree: {os.path.join(dirpath, n)}")
        os.makedirs(rw_dir, exist_ok=True)
        for entry in os.listdir(rw_dir):  # the dir itself stays: it may be a mount target
            p = os.path.join(rw_dir, entry)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
        remaining()
        for entry in os.listdir(stage):
            os.replace(os.path.join(stage, entry), os.path.join(rw_dir, entry))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ------------------------------------------------------------------ the Job

def container(name: str, uid: int, image: str, cmd: list[str], mounts: list[dict], cpu: str, mem: str,
              disk: str = "1Gi") -> dict:
    # ephemeral-storage covers what Kubernetes itself keeps on the node disk for the container
    # (/dev/termination-log, logs); all volumes are memory-backed.
    return {"name": name, "image": image, "imagePullPolicy": "IfNotPresent", "command": cmd, "workingDir": "/",
            "securityContext": {"runAsUser": uid, "runAsGroup": uid, "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": cpu, "memory": mem, "ephemeral-storage": "256Mi"},
                          "limits": {"cpu": cpu, "memory": mem, "ephemeral-storage": disk}},
            "volumeMounts": mounts}


def idle(deadline: int) -> list[str]:
    """PID 1 of a long-lived container: its stdout/stderr (the container log, on the node disk) go to /dev/null,
    so code that opens /proc/1/fd/1 cannot write the log."""
    return ["/bin/sh", "-c", f"exec >/dev/null 2>&1 </dev/null; trap 'exit 0' TERM INT; sleep {deadline} & wait"]


def job_spec(name: str, image: str, ro: list[str], rw: list[str], deadline: int, uids: tuple[int, int]) -> dict:
    run_uid, load_uid = uids
    vols, load_m, run_m = [], [], []
    disk = cfg("SDLC_K8S_DISK", "4Gi")
    for i, p in enumerate([C_HOME, C_TMP]):
        vols.append({"name": f"h{i}", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}})
        run_m.append({"name": f"h{i}", "mountPath": p})
    for i, p in enumerate(ro):
        vols.append({"name": f"r{i}", "emptyDir": {"medium": "Memory", "sizeLimit": cfg("SDLC_K8S_INPUT_MEMORY", "2Gi")}})
        load_m.append({"name": f"r{i}", "mountPath": p})
        run_m.append({"name": f"r{i}", "mountPath": p, "readOnly": True})
    for i, p in enumerate(rw):
        vols.append({"name": f"w{i}", "emptyDir": {"medium": "Memory", "sizeLimit": disk}})
        run_m.append({"name": f"w{i}", "mountPath": p})
    # Writable dirs nested in read-only ones need their mount point to exist before `run` starts.
    nested = sorted({p for p in rw for q in ro if under(p, q) and p != q})
    init = container("mountpoints", load_uid, image, ["mkdir", "-p", *nested] if nested else ["true"], load_m, "200m", "128Mi")
    load = container("load", load_uid, image, idle(deadline), load_m, "500m", cfg("SDLC_K8S_INPUT_MEMORY", "2Gi"))
    run = container("run", run_uid, image, idle(deadline), run_m, cfg("SDLC_K8S_CPU", "4"), cfg("SDLC_K8S_MEMORY", "8Gi"),
                    f"{num('SDLC_K8S_MAX_FILE_MB', 2048) + 512}Mi")
    pod: dict = {"restartPolicy": "Never", "serviceAccountName": "sdlc-runner", "automountServiceAccountToken": False,
                 "enableServiceLinks": False, "terminationGracePeriodSeconds": 1,
                 "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                 "initContainers": [init], "containers": [load, run], "volumes": vols}
    pool = os.environ.get("SDLC_K8S_NODE_POOL", "sdlc")
    if pool:
        pod["nodeSelector"] = {"category": pool}
        pod["tolerations"] = [{"key": "sdlc-runner.example.com/runner", "operator": "Equal", "value": "true", "effect": "NoSchedule"}]
    if os.environ.get("SDLC_K8S_PULL_SECRET", "regcred"):
        pod["imagePullSecrets"] = [{"name": os.environ.get("SDLC_K8S_PULL_SECRET", "regcred")}]
    labels = {"sdlc": "run", "app.kubernetes.io/part-of": "adversarial-sdlc"}
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "labels": labels},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": deadline, "ttlSecondsAfterFinished": 300,
                     "template": {"metadata": {"labels": labels, "annotations": {"karpenter.sh/do-not-disrupt": "true"}},
                                  "spec": pod}}}


class Run:
    def __init__(self) -> None:
        self.job = f"sdlc-{int(time.time())}-{secrets.token_hex(3)}"
        self.uids = (0, 0)
        self.pod = ""
        self.reaper: subprocess.Popen | None = None

    def pick_uids(self) -> None:
        """Fresh UIDs per call, not used by any active runner pod: per-user kernel quotas (inotify, ...) are not
        shared with another runner. (Two laptops starting at the same instant may still collide: 1 in ~10^12.)"""
        used = set(kubectl("get", "pods", "-l", "sdlc=run", "-o",
                           "jsonpath={.items[*].spec.containers[*].securityContext.runAsUser}", timeout=30).stdout.split())
        while True:
            self.uids = (1_000_000 + secrets.randbelow(1_000_000) * 2, 1_000_001 + secrets.randbelow(1_000_000) * 2)
            if not used & {str(u).encode() for u in self.uids}:
                return

    def start_reaper(self) -> None:
        """If this process dies, a detached child deletes the Job (retrying), then waits for the pods to go."""
        code = ("import os,sys,time,subprocess\n"
                "p=int(sys.argv[1]); base=sys.argv[2:]\n"
                "while True:\n"
                "    try: os.kill(p,0)\n"
                "    except OSError: break\n"
                "    time.sleep(2)\n"
                "for i in range(30):\n"
                "    if subprocess.run(base,capture_output=True).returncode==0: break\n"
                "    time.sleep(10)\n")
        cmd = ["kubectl", "--context", context(), "--namespace", namespace(), "delete", "job", self.job,
               "--ignore-not-found", "--cascade=foreground", "--wait=true", "--timeout=120s"]
        self.reaper = subprocess.Popen([sys.executable, "-c", code, str(os.getpid()), *cmd], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def cleanup(self) -> bool:
        """Delete the Job and wait until its pods are gone. False if that could not be confirmed."""
        ok = False
        for _ in range(3):
            try:
                p = subprocess.run(["kubectl", "--context", context(), "--namespace", namespace(), "delete", "job", self.job,
                                    "--ignore-not-found", "--cascade=foreground", "--wait=true", "--timeout=120s"],
                                   capture_output=True, check=False, timeout=150)
                left = subprocess.run(["kubectl", "--context", context(), "--namespace", namespace(), "get", "pods",
                                       "-l", f"job-name={self.job}", "-o", "name"], capture_output=True, text=True,
                                      check=False, timeout=60)
                if p.returncode == 0 and left.returncode == 0 and not left.stdout.strip():
                    ok = True
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(5)
        if ok and self.reaper and self.reaper.poll() is None:
            self.reaper.terminate()
        return ok




def start(run: Run, spec: dict) -> dict:
    kubectl("apply", "-f", "-", input_bytes=json.dumps(spec).encode())
    deadline = time.monotonic() + min(num("SDLC_K8S_SCHEDULE_TIMEOUT", 900), remaining())
    while time.monotonic() < deadline:
        names = kubectl("get", "pods", "-l", f"job-name={run.job}", "-o", "name", timeout=30).stdout.decode().split()
        if names:
            run.pod = names[0].split("/", 1)[1]
            break
        refused = kubectl("get", "events", "--field-selector", f"involvedObject.name={run.job},reason=FailedCreate",
                          "-o", "jsonpath={.items[*].message}", check=False, timeout=30).stdout.decode(errors="replace")
        if refused.strip():
            raise Fail(f"the cluster refused the runner pod: {refused.strip()[:600]}")
        time.sleep(1)
    if not run.pod:
        raise Fail("the Job created no pod in time")
    w = kubectl("wait", "--for=condition=Ready", f"pod/{run.pod}", f"--timeout={int(max(1, deadline - time.monotonic()))}s",
                check=False, timeout=deadline - time.monotonic() + 30)
    if w.returncode != 0:
        ev = kubectl("get", "events", "--field-selector", f"involvedObject.name={run.pod}", "-o",
                     "custom-columns=REASON:.reason,MSG:.message", check=False).stdout.decode(errors="replace")
        raise Fail(f"pod did not become ready: {w.stderr.decode(errors='replace').strip()[:300]}\n{ev[-800:]}")
    pod = json.loads(kubectl("get", "pod", run.pod, "-o", "json").stdout)
    if not any(r.get("kind") == "Job" for r in pod["metadata"].get("ownerReferences", [])):
        raise Fail("the pod is not owned by its Job")
    return pod


# ------------------------------------------------------------------ probes (barrier and --verify)

PROBE = r'''
import errno, json, os, socket, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
targets, max_pids, full = json.loads(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "full"
def tcp(t):
    name, ip, port = t
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((ip, port)); r = "CONNECTED"
    except socket.timeout:
        r = "blocked (timeout)"
    except ConnectionRefusedError:
        r = "REFUSED (reached the host)"
    except OSError as e:
        r = "blocked (%s)" % errno.errorcode.get(e.errno, e.errno)
    finally:
        s.close()
    return "%s %s:%s" % (name, ip, port), r
def udp(t):
    name, ip, port = t
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(3)
    try:
        u.sendto(bytes.fromhex("abcd01000001000000000000076578616d706c6503636f6d0000010001"), (ip, 53)); u.recvfrom(512)
        r = "ANSWERED"
    except OSError:
        r = "blocked"
    finally:
        u.close()
    return "%s %s:53" % (name, ip), r
def dns(host):
    try:
        return host, "RESOLVED %s" % socket.gethostbyname(host)
    except OSError:
        return host, "blocked"
pool = ThreadPoolExecutor(32)
t_res = pool.map(tcp, targets)
u_res = pool.map(udp, [t for t in targets if t[0] == "coredns"])
d_res = pool.map(dns, ("kubernetes.default.svc.cluster.local", "sts.us-east-2.amazonaws.com", "example.com") if full else ())
out = {"tcp": dict(t_res), "udp": dict(u_res), "dns": dict(d_res), "creds": {}, "pids": None}
for p in ("/var/run/secrets/kubernetes.io", "/var/run/secrets/eks.amazonaws.com", "/run/secrets", "/root/.kube",
          "/etc/kubernetes", "/var/lib/kubelet"):
    out["creds"][p] = "PRESENT" if os.path.exists(p) else "absent"
out["creds"]["AWS_* env"] = "PRESENT" if any(k.startswith("AWS_") for k in os.environ) else "absent"
if max_pids:
    procs = []
    try:
        while len(procs) < max_pids:
            procs.append(subprocess.Popen(["sleep", "60"]))
    except OSError:
        pass
    out["pids"] = len(procs)
    for pr in procs:
        pr.kill()
    for pr in procs:
        pr.wait()
print(json.dumps(out))
'''


def host_kubectl(*args: str) -> str:
    p = subprocess.run(["kubectl", "--context", context(), *args], capture_output=True, text=True, check=False, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else ""


def probe_targets(pod: dict, full: bool) -> list[tuple[str, str, int]]:
    host_ip = pod["status"].get("hostIP", "")
    t = [("kubelet", host_ip, 10250), ("aws-node-metrics", host_ip, 61678), ("aws-nodeagent", host_ip, 8162),
         ("kube-proxy-health", host_ip, 10256), ("imds", "169.254.169.254", 80), ("pod-identity", "169.254.170.23", 80),
         ("api-service", host_kubectl("-n", "default", "get", "svc", "kubernetes", "-o", "jsonpath={.spec.clusterIP}"), 443),
         ("coredns", host_kubectl("-n", "kube-system", "get", "svc", "kube-dns", "-o", "jsonpath={.spec.clusterIP}"), 53),
         ("internet", "1.1.1.1", 443)]
    if full:
        others = json.loads(host_kubectl("get", "pods", "-A", "--field-selector=status.phase=Running", "-o", "json") or '{"items": []}')
        other = next((p["status"]["podIP"] for p in others["items"] if p["metadata"]["namespace"] != namespace()
                      and p["status"].get("podIP") not in (None, p["status"].get("hostIP"))), "")
        try:
            sts = socket.gethostbyname("sts.us-east-2.amazonaws.com")
        except OSError:
            sts = ""
        svcs = json.loads(host_kubectl("get", "svc", "-A", "-o", "json") or '{"items": []}')["items"]
        ports = sorted({pt["nodePort"] for sv in svcs for pt in sv["spec"].get("ports", []) if pt.get("nodePort")}
                       | {sv["spec"]["healthCheckNodePort"] for sv in svcs if sv["spec"].get("healthCheckNodePort")})
        t += [(f"nodeport-{port}", host_ip, port) for port in ports[:40]]
        t += [("node-ssh", host_ip, 22), ("pod-identity-2703", "169.254.170.23", 2703),
              ("api-endpoint", host_kubectl("-n", "default", "get", "endpoints", "kubernetes", "-o",
                                            "jsonpath={.subsets[0].addresses[0].ip}"), 443),
              ("other-pod", other, 80), ("aws-sts", sts, 443)]
    return [x for x in t if x[1]]


def probe(run: Run, pod: dict, full: bool, max_pids: int) -> tuple[dict, list[str]]:
    res = kubectl("exec", run.pod, "-c", "run", "--", *PY, "-c", PROBE, json.dumps(probe_targets(pod, full)),
                  str(max_pids), "full" if full else "barrier", timeout=900)
    try:
        out = json.loads(res.stdout.decode())
    except ValueError as e:
        raise Fail("the probe returned no result") from e
    bad = [f"{k}: {v}" for sec in ("tcp", "dns", "udp", "creds") for k, v in out[sec].items()
           if not (v.startswith("blocked") or v == "absent")]
    if max_pids and out["pids"] is not None and out["pids"] >= max_pids:
        bad.append(f"pids: started {out['pids']} processes without hitting a limit")
    return out, bad


def node_problem(pod: dict) -> str | None:
    """The node this pod landed on must run the current security configuration: kubelet's effective pod PID
    limit, and a NodeClaim launched from the current revision of the sdlc EC2NodeClass."""
    node = pod["spec"].get("nodeName", "")
    want_pids = num("SDLC_K8S_POD_PIDS", 4096)
    try:
        conf = json.loads(kubectl("get", "--raw", f"/api/v1/nodes/{node}/proxy/configz", ns=False, timeout=30).stdout)
        pids = conf["kubeletconfig"].get("podPidsLimit")
        if pids is None or not 0 < int(pids) <= want_pids:
            return f"node {node}: kubelet podPidsLimit is {pids}, want at most {want_pids}"
        key = "karpenter.k8s.aws/ec2nodeclass-hash"
        nc = json.loads(kubectl("get", "ec2nodeclasses.karpenter.k8s.aws", os.environ.get("SDLC_K8S_NODE_POOL", "sdlc"),
                                "-o", "json", ns=False, timeout=30).stdout)
        claims = json.loads(kubectl("get", "nodeclaims.karpenter.sh", "-o", "json", ns=False, timeout=30).stdout)["items"]
        claim = next((c for c in claims if c.get("status", {}).get("nodeName") == node), None)
        if claim is None:
            return f"node {node} has no Karpenter NodeClaim (not an sdlc node)"
        have, current = claim["metadata"].get("annotations", {}).get(key), nc["metadata"].get("annotations", {}).get(key)
        if not have or have != current:
            return f"node {node} was launched from another EC2NodeClass revision ({have} != {current}); wait for drift replacement"
    except (Fail, ValueError, KeyError, TypeError) as e:
        return f"cannot verify node {node}: {e}"
    return None


def barrier(run: Run, pod: dict) -> None:
    """Before any untrusted code runs: the pod must reach nothing. VPC CNI's standard mode starts pods with a
    default-allow policy until their policies are in place, so this retries for a while before failing."""
    if unverified_allowed():
        return
    why = node_problem(pod)
    if why:
        raise Fail(why)
    until = time.monotonic() + min(num("SDLC_K8S_BARRIER_WAIT", 90), remaining())
    while True:
        _, bad = probe(run, pod, False, 0)
        if not bad:
            return
        if time.monotonic() > until:
            raise Fail("network isolation is not in effect for the runner pod: " + "; ".join(bad))
        time.sleep(3)


def verify(max_pids: int) -> int:
    """Isolation probe (G2, G3, G5) through the same code path as real calls. A pass is recorded per context,
    namespace and image; check() refuses to run work without a fresh one."""
    why = cluster_identity_problem() or security_problem()
    if why:
        die(why)
    run = Run()
    run.start_reaper()
    try:
        run.pick_uids()
        pod = start(run, job_spec(run.job, image_ref(), [], [], 900, run.uids))
        out, bad = probe(run, pod, True, max_pids)
        why = node_problem(pod)
        if why:
            bad.append(why)
        fingerprint = security_fingerprint()
    except Fail as e:
        gone = run.cleanup()
        die(str(e) if gone else f"{e}; also could not confirm that Job {run.job} and its pods are gone")
    if not run.cleanup():
        die(f"could not confirm that Job {run.job} and its pods are gone; nothing recorded")
    print(json.dumps({"host_ip": pod["status"].get("hostIP"), **out}, indent=2))
    if bad:
        print("ISOLATION PROBE FAILED:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    rec = verified_record()
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"at": time.time(), "context": context(), "image": image_ref(), "fingerprint": fingerprint,
                               "results": out}, indent=2))
    print(f"isolation probe passed; recorded {rec}")
    return 0


# ------------------------------------------------------------------ streaming with caps

def stream_exec(args: list[str], cap: int, to_file: Path | None) -> int:
    """Run `kubectl exec ...`, passing stdout to to_file (or our stdout) and stderr to our stderr, each capped
    at `cap` bytes; over the cap, or past the deadline, the transfer is killed and the call fails (97)."""
    p = subprocess.Popen(["kubectl", "--context", context(), "--namespace", namespace(), *args],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    over: list[str] = []
    errors: list[str] = []

    def pump(src, dst, what: str) -> None:
        n = 0
        try:
            for chunk in iter(lambda: src.read(65536), b""):
                n += len(chunk)
                if n > cap:
                    over.append(what)
                    p.kill()
                    return
                dst.write(chunk)
                dst.flush()
        except Exception as e:  # noqa: BLE001 - any lost byte fails the call
            errors.append(f"{what}: {e}")
            p.kill()

    out_fh = open(to_file, "wb") if to_file else sys.stdout.buffer  # noqa: SIM115 - closed below
    try:
        ts = [threading.Thread(target=pump, args=(p.stdout, out_fh, "stdout"), daemon=True),
              threading.Thread(target=pump, args=(p.stderr, sys.stderr.buffer, "stderr"), daemon=True)]
        for t in ts:
            t.start()
        try:
            rc = p.wait(timeout=remaining())
        except (subprocess.TimeoutExpired, Fail) as e:
            p.kill()
            raise Fail("overall deadline reached while the command ran") from e
        for t in ts:
            t.join(timeout=60)
        if any(t.is_alive() for t in ts):
            errors.append("the streams did not drain")
    finally:
        if to_file:
            out_fh.close()
    if over:
        raise Fail(f"{' and '.join(over)} exceeded {cap // 1048576} MB")
    if errors:
        raise Fail(f"streaming failed ({'; '.join(errors)})")
    return rc


def env_for_pod(plan: dict) -> list[str]:
    """An explicit allowlist: nothing from the laptop's environment reaches the code under test unless named in
    SDLC_K8S_PASSENV (credentials such as GRADLE_*/AWS_* variables must never be forwarded by accident)."""
    env = {"PATH": IMAGE_PATH, "JAVA_HOME": "/usr/lib/jvm/sdlc-java-21", "HOME": C_HOME, "TMPDIR": C_TMP,
           "LANG": "C.UTF-8", "GRADLE_USER_HOME": f"{C_HOME}/.gradle", "GRADLE_RO_DEP_CACHE": "/opt/gradle-ro/caches",
           "GATE_JAVA_HOME": "/usr/lib/jvm/sdlc-java-21",
           "JAVA_TOOL_OPTIONS": f"-Djava.io.tmpdir={C_TMP} -Duser.home={C_HOME}", "SDLC_DOCKER_ENTRY": json.dumps(plan),
           # No file may grow beyond this: bounds what Kubernetes keeps on the node disk (/dev/termination-log).
           "SDLC_ENTRY_FSIZE_MB": str(num("SDLC_K8S_MAX_FILE_MB", 2048))}
    for k in os.environ.get("SDLC_K8S_PASSENV", "").split():
        if k in os.environ and k not in env:
            env[k] = os.environ[k]
    return [f"{k}={v}" for k, v in sorted(env.items())]


# ------------------------------------------------------------------ one call

def call(run: Run, rw: list[str], ro_in: list[str], deny: list[str], cmd: list[str]) -> int:
    skill = os.path.realpath(SKILL_DIR)
    ro = sorted(set(ro_in + ([skill] if not any(under(skill, p) for p in ro_in + rw) else [])))
    rw = sorted(set(rw))
    for d in deny:
        if any(under(p, d) for p in ro + rw):
            raise Fail(f"a mounted dir lies inside a denied one: {d}")
    trace("checks done")
    pod = start(run, job_spec(run.job, image_ref(), ro, rw, num("SDLC_K8S_DEADLINE", 7200), run.uids))
    trace(f"pod ready on {pod['spec'].get('nodeName')}")
    barrier(run, pod)
    trace("barrier passed")
    plan: dict = {"mounts": [], "gradle_dists": False, "pi_settings": {}}
    nonces: dict[str, bytes] = {}
    for p in sorted(ro + rw):
        if p in rw:
            nonce = f"{NONCE}{run.job}"
            nonces[os.path.join(p, nonce)] = b""
            plan["mounts"].append({"path": p, "mode": "rw", "nonce": nonce})
        else:
            names = sorted(n for n in os.listdir(p) if not any(under(os.path.join(p, n), d) for d in deny))
            plan["mounts"].append({"path": p, "mode": "ro",
                                   "listing": hashlib.sha256("\n".join(names).encode("utf-8", "surrogateescape")).hexdigest()})
        if (Path(p) / "gradle/wrapper/gradle-wrapper.properties").is_file():
            plan["gradle_dists"] = True
    check_ro_links(ro, rw, deny)
    with tempfile.TemporaryDirectory(prefix="sdlc-k8s-") as tmp:
        # Read-only inputs go in through `load` (they become read-only for `run`); writable ones through `run`.
        skip_ro = rw + deny
        for container_name, dirs, skip, extra in (("load", ro, skip_ro, {}), ("run", rw, deny, nonces)):
            if not dirs:
                continue
            tar = Path(tmp) / f"{container_name}.tar"
            pack(dirs, ro + rw, skip, extra, tar)
            data = tar.read_bytes()
            # A dropped exec stream is retried: extracting the same archive again overwrites in place, and the
            # manifest comparison below proves the copy complete either way.
            for attempt in range(1, 4):
                put = kubectl("exec", "-i", run.pod, "-c", container_name, "--", "tar", "-C", "/", "-m", "-xf", "-",
                              input_bytes=data, check=False, timeout=1800)
                if put.returncode == 0:
                    break
                if attempt == 3:
                    raise Fail(f"could not copy the inputs into the pod: {put.stderr.decode(errors='replace').strip()[-300:]}")
                trace(f"copy-in attempt {attempt} failed, retrying")
                time.sleep(2 * attempt)
        trace("inputs copied in")
        want = {p: manifest(p, [q for q in rw + deny if q != p] if p in ro else deny) for p in ro + rw}
        skips = {p: (rw + deny if p in ro else deny) for p in ro + rw}
        got = json.loads(kubectl("exec", run.pod, "-c", "run", "--", *PY, "-c", MANIFEST_IN_POD,
                                 json.dumps(skips), json.dumps(sorted(nonces)), timeout=900).stdout.decode())
        for p in ro + rw:
            if got.get(p) != want[p]:
                diff = sorted(set(want[p].items()) ^ set((got.get(p) or {}).items()))[:5]
                raise Fail(f"the pod's copy of {p} differs from the host's: {diff}")
        trace("manifests match")
        cwd = os.path.realpath(os.getcwd())
        workdir = cwd if any(under(cwd, p) for p in ro + rw) else C_HOME
        rc = stream_exec(["exec", run.pod, "-c", "run", "--", "env", "-i", "-C", workdir, *env_for_pod(plan), f"SDLC_ENTRY_OK={ENTRY_OK}", f"SDLC_ENTRY_STATUS={ENTRY_STATUS}",
                          *PY, str(SKILL_DIR / "scripts" / "docker" / "entry.py"), *cmd],
                         num("SDLC_K8S_MAX_LOG_MB", 64) * 1048576, None)
        trace(f"command finished ({rc})")
        if kubectl("exec", run.pod, "-c", "run", "--", "test", "-f", ENTRY_OK, check=False).returncode != 0:
            raise Fail("the command's start is not confirmed: the entry checks failed in the pod, or the exec connection "
                       "broke (see the messages above); no outputs were copied back")
        # Bounded: the file is in workload-writable /tmp (a link to /dev/zero must not flood the laptop).
        done = kubectl("exec", run.pod, "-c", "run", "--", "head", "-c", "16", ENTRY_STATUS, check=False)
        if done.returncode != 0 or done.stdout.decode().strip() != str(rc):
            raise Fail(f"the command's completion is not confirmed (kubectl exec exit {rc}, status "
                       f"{done.stdout.decode().strip() or 'missing'}): a broken connection or a command still running")
        st = json.loads(kubectl("get", "pod", run.pod, "-o", "json").stdout)
        cs = {c["name"]: c for c in st["status"].get("containerStatuses", [])}
        if st["status"].get("phase") != "Running" or "running" not in cs.get("run", {}).get("state", {}):
            raise Fail(f"the runner pod stopped during the command (phase {st['status'].get('phase')}, "
                       f"{cs.get('run', {}).get('state')}): deadline, eviction or out of memory")
        for p in rw:
            out = Path(tmp) / f"out-{abs(hash(p))}.tar"
            # The command has finished: reading its output dir again after a dropped exec stream is safe. Exceeding
            # a cap raises in stream_exec and is not retried.
            # SDLC_K8S_KEEP (absolute paths, set by the gate): only what the caller reads comes back; the rest of the
            # dir is not copied (the host's copy of it ends up empty, as a fresh build dir). Always compressed.
            keep = [k for k in os.environ.get("SDLC_K8S_KEEP", "").split() if under(os.path.realpath(k), p)]
            if "SDLC_K8S_KEEP" in os.environ:
                pull = ["exec", run.pod, "-c", "run", "--", *PY, "-c", KEEP_IN_POD,
                        json.dumps([os.path.realpath(k) for k in keep])]
            else:
                pull = ["exec", run.pod, "-c", "run", "--", "tar", "-C", "/", "-czf", "-", p.lstrip("/")]
            for attempt in range(1, 4):
                if stream_exec(pull, num("SDLC_K8S_MAX_OUTPUT_MB", 2048) * 1048576, out) == 0:
                    break
                if attempt == 3:
                    raise Fail(f"could not read the output dir {p} from the pod")
                trace(f"copy-out attempt {attempt} for {p} failed, retrying")
                time.sleep(2 * attempt)
            safe_extract(out, p)
    remaining()  # past the deadline, even a finished call fails
    trace("outputs copied out")
    return rc


def main(argv: list[str]) -> int:
    if argv[:1] == ["--verify"]:
        return verify(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 5000)
    if argv[:1] == ["--check"]:
        why = check()
        if why:
            print(why)
            return 1
        return 0
    if argv[:1] == ["--warm"]:
        return warm()
    if argv[:1] == ["--unwarm"]:
        return unwarm()
    if argv[:1] == ["--capacity"]:
        print(capacity())
        return 0
    if argv[:1] == ["--ids"]:
        print(f"SDLC_K8S_IMAGE={image_ref()}")
        return 0
    rw, ro, deny, net, cmd = parse(argv)
    if net == "all":
        die("model roles do not run on the k8s backend (they need the local gateway); use docker for roles", 2)
    if not cmd:
        die("missing COMMAND", 2)
    # sandbox.sh has just run --check (as SDLC_DOCKER_CHECKED for the docker backend).
    why = None if os.environ.pop("SDLC_K8S_CHECKED", "") == "1" else check()
    if why:
        die(why)
    run = Run()

    def on_signal(sig, _frame):
        raise Interrupted(sig)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    run.start_reaper()
    rc = 97
    try:
        run.pick_uids()
        rc = call(run, rw, ro, deny, cmd)
    except Interrupted as e:
        print(f"sandbox.sh (k8s): interrupted by signal {e.sig}", file=sys.stderr, flush=True)
        rc = 128 + e.sig
    except Fail as e:
        print(f"sandbox.sh (k8s): {e}", file=sys.stderr, flush=True)
        rc = 97
    except Exception as e:  # noqa: BLE001 - any unexpected failure of the sandbox is a sandbox failure (G7)
        print(f"sandbox.sh (k8s): internal error: {e.__class__.__name__}: {e}", file=sys.stderr, flush=True)
        rc = 97
    finally:
        ok = run.cleanup()
        trace("cleanup done")
        if not ok:
            print(f"sandbox.sh (k8s): could not confirm that Job {run.job} and its pods are gone", file=sys.stderr, flush=True)
            rc = 97
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
