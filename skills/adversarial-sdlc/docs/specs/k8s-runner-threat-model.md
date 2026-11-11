# Contract: what the k8s runner must guarantee

## Purpose
A laptop tool ("the orchestrator") offloads "code under test" to a dev Kubernetes cluster: build and test commands
(Gradle, JUnit, Pitest, detekt) on source code written by AI agents. Each call: create one pod in namespace
`sdlc-runner`, stream input directories into it through the Kubernetes API (`kubectl exec ... tar -x`), run one
command via `kubectl exec`, stream the `--rw` output directories back and extract them on the laptop, delete the
pod. The verdict (pass/fail of the code) is computed on the laptop from those outputs.

## Adversary
Everything running inside the runner pod: arbitrary code as the image's non-root user (uid 10001), able to
read and write every file inside the pod (including its copies of read-only inputs), run any process, use all
CPU/memory/disk/PIDs it can get, and shape its stdout/stderr and the contents of its output directories.
It may also try to persist between calls.

## Trusted
The laptop orchestrator and the human, kubectl and the human's credentials, the cluster control plane, kubelet,
containerd, the node image, the node autoscaler, the GitOps controller, the image as built (`build.sh`), the container registry.

## Must hold (each is a review target)
- G1. No path from the pod to the laptop's filesystem or processes except: the extracted `--rw` directories,
  confined to those directories; and stdout/stderr bytes written to log files.
- G2. No network from the pod to anything: internet, AWS APIs, IMDS (v1/v2), the Kubernetes API, cluster
  services, other pods, its own node's services (kubelet, hostNetwork agents), DNS.
- G3. No credentials of any kind reachable from the pod (service-account tokens, node role, Pod Identity, IRSA,
  pull secrets, the human's kubeconfig).
- G4. No effect on later calls through the cluster: nothing a pod does survives its deletion in a way a later
  pod could observe (node filesystem, image cache, shared volumes, kernel state within reason). The one
  intended channel is the `--rw` output returned to the laptop: it is untrusted data, it never decides a
  pass on its own (a passing gate is re-run with `--fresh`, from a fresh snapshot and without Pitest history),
  and it is parsed defensively (UTF-8-only, DTD-free, size-capped reports).
- G5. No denial of service beyond its own resources: other runner pods and other tenants keep working
  (CPU, memory, disk, PIDs, inodes, logs, API server load).
- G6. The orchestrator never runs against a production cluster by accident.
- G7. Fail closed: any failure of the sandbox itself (setup or teardown, partial copy, the overall deadline,
  an unexpected pod state) makes the call fail with exit 97, never succeed silently; no pod is left running
  indefinitely. A deadline the caller set for its own command inside the sandbox (`run-timeout`, exit 124)
  is the command's result, as on the docker backend: the gate reports it as a timeout.
- G8. Inputs inside the pod are what the laptop sent (complete copy); a change to read-only inputs during the
  run fails the call.

Not a guarantee: the honesty of the result. Inside its pod the code under test controls its own exit status,
outputs and the completion evidence the runner checks. The sandbox contains the
code; it does not make its result trustworthy. The gate treats outputs as untrusted data, and a pass is
re-run with `--fresh`. Forging the completion evidence can only turn a call into a failure (97).

Accepted residual risks: pods share one node-level disk-write cap, so a hostile runner can slow
other pods' disk writes on its node (availability only); the admission policy enforces only the fields
it lists, and the client sets the rest.

Out of scope: kernel/container-runtime escapes (no gVisor available), side channels between co-located
pods, a malicious cluster administrator.
