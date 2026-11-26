# Phase 7 evidence: Kubernetes deployment and scaling

Local Minikube cluster (profile `credit-risk`, Docker driver), namespace `credit-risk`, run on 2026-11-26 with
[`scripts/k8s_demo.sh`](../../../scripts/k8s_demo.sh). The whole output is in [`k8s-demo.log`](k8s-demo.log).
Explanation and limits: [docs/kubernetes.md](../../kubernetes.md).

| Exit criterion | Shown by | Result |
|---|---|---|
| Multiple replicas serving traffic | step 0 and 3: two ready API pods, Service endpoints list both; in-cluster load of 600 requests | the two pods served 303 and 297 of the 600 requests; all returned 200 |
| Failed pod automatically replaced | step 2: one serving pod deleted | a new pod with a new name became ready within seconds; the Service stayed available |
| Probes demonstrably affect routing | step 4: bad `MODEL_PATH` | the new pod answered 503 on `/health`, stayed `0/1`, was absent from the Service endpoints, events show `Readiness probe failed`; the rollout stalled and the old pods kept serving |
| Rolling update and rollback | step 5: image `v1` → `v2` → `v1` | one pod at a time, `/health` reported model version `2-demo` after the update; `kubectl rollout undo` restored `v1` |
| Scale up and down | step 1: `kubectl scale` 2 → 3 → 2 | as expected |

Also verified: every manifest in `k8s/` passes a server-side dry run, and the in-cluster Prometheus (`k8s/monitoring/prometheus.yaml`) discovers and scrapes both API pods.
Not exercised: the optional HorizontalPodAutoscaler (`k8s/hpa.yaml`) under load.

Re-run: see "Run it" in [docs/kubernetes.md](../../kubernetes.md). The API key used by the demo is a random value created by the script inside the cluster Secret; it is not in this log.
