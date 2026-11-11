# Restate operations notes

Field notes for running Restate: invocation triage, timeouts, Kafka subscriptions, and the
metadata `data-loss` failure mode. Commands assume a `restate` pod reachable by `kubectl exec`
and the admin API on port 9070.

## Invocation triage

```sh
# Deployments the cluster knows about. A redeploy without registration leaves invocations stuck.
kubectl exec <restate-pod> -- restate deployments list

# Non-completed invocations. Add status, target, and age columns for the service you are debugging.
kubectl exec <restate-pod> -- restate sql \
  "SELECT id, target_service_name, target_handler_name, status FROM sys_invocation WHERE status != 'completed' LIMIT 20"

# Binary columns need a cast before length or text comparisons.
kubectl exec <restate-pod> -- restate sql \
  "SELECT CAST(key AS VARCHAR), octet_length(value) FROM <table> LIMIT 10"
```

Port-forwarding to the admin API flakes; retry against a different pod before concluding the
service is down:

```sh
kubectl port-forward pod/<restate-pod> 19070:9070
curl -s localhost:19070/services | jq .
curl -s localhost:19070/deployments | jq .
```

## Timeouts

- `inactivity_timeout` defaults to 1 minute. A value that is too low aborts healthy work; a
  production service once ran with 2 seconds.
- Per-service `abort_timeout` can be changed through the admin API. It is not persistent:
  `curl -X PATCH localhost:19070/services/<Service> \
     -H 'content-type: application/json' -d '{"abort_timeout":"3h"}'`
  Persist the value in the deployment CRD, or the next reconcile drops it.
- Operator upgrades: one minor version at a time; CRD schema changes land with the operator.

## Kafka subscriptions

Two failure modes dominate:

1. **Registration runs before the service is deployed.** Restate returns an error for an unknown
   sink. Order the job after the deployment is ready.
2. **A retried job creates duplicates or fails on 409.** Check `/subscriptions` first and treat
   409 as success. `register-kafka-subscriptions.sh` in this repo does both.

Source and sink URIs are `kafka://<cluster>/<topic>` and `service://<Service>/<handler>`.
The service must already be registered; the subscription only wires delivery.

## Metadata `data-loss` state

Symptom: every pod starts with an error like

```
A previous generation of my node N1 has marked this log-server's storage-state as data-loss
```

and never joins the cluster.

Cause: the cluster recorded `storage_state = data-loss` for the node in its metadata store
(`nodes_config` and partition-epoch files in the object store). The condition is sticky: new pods
reusing the same node names inherit the marker and are rejected as a previous failed generation.

Recovery:

- **Fresh environment:** delete the metadata prefix and the node PVCs, then redeploy. The cluster
  bootstraps cleanly.
- **Anything with data you care about:** treat this as a data-loss event and restore from
  snapshots. Editing `nodes_config` is a binary-format repair with no supported tool; try it only
  on a copy, and only after the snapshot restore path has been ruled out.

Prevention:

- Back up the metadata bucket and snapshot bucket, and test the restore.
- Alert on `data-loss` state; it does not clear itself.
- Do not reuse node names for a cluster that lost its storage unless the metadata is reset.
