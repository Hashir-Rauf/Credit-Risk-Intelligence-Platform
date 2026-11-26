# Kubernetes deployment and scaling (Phase 7)

Owner: `@Hashir-Rauf`. Manifests: [`k8s/`](../k8s). Demo: [`scripts/k8s_demo.sh`](../scripts/k8s_demo.sh).
Evidence: [`docs/evidence/phase-7/`](evidence/phase-7/k8s-demo.log) (full output of one run of the demo).

This is a **local** cluster (Minikube, Docker driver) to show orchestration, self-healing and scaling. It is not the production path: production runs on ECS ([aws-deployment.md](aws-deployment.md)).

## What is deployed
| File | What it is |
|---|---|
| `namespace.yaml` | Namespace `credit-risk`, Pod Security `restricted` (enforced) |
| `api-config.yaml` | ConfigMap: non-secret settings (`LOG_LEVEL`, `MODEL_PATH`, `MAX_BATCH`, `MAX_BODY_BYTES`) |
| *(Secret `api-secret`)* | `API_KEY`, created by the demo script from a random value. **Not in git** |
| `api-deployment.yaml` | 2 replicas, rolling update (`maxUnavailable: 0`, `maxSurge: 1`), readiness + liveness probes on `/health`, requests/limits, non-root (uid 10001), read-only root filesystem, all capabilities dropped |
| `api-service.yaml` | `ClusterIP` Service `api:8000` |
| `api-pdb.yaml` | PodDisruptionBudget `minAvailable: 1` |
| `frontend-deployment.yaml` | Next.js frontend + `NodePort` Service; `API_KEY` from the same Secret, held server-side only |
| `hpa.yaml` | *Optional.* HorizontalPodAutoscaler 2-4 replicas on CPU (needs `metrics-server`) |
| `monitoring/prometheus.yaml` | Prometheus that discovers the API pods through the Kubernetes API and loads the same alert rules as the Compose stack |
| `demo/loadgen-job.yaml` | In-cluster load generator used by the demo |

The image tag is the only thing that changes per release (the SHA in real use; `v1` and `v2` here). **No `latest`.**

## Run it
```bash
minikube start -p credit-risk --driver=docker
docker build -f docker/Dockerfile.api --target with-model --build-arg GIT_SHA=demo-v1 --build-arg MODEL_VERSION=1     -t credit-risk-api:v1 .
docker build -f docker/Dockerfile.api --target with-model --build-arg GIT_SHA=demo-v2 --build-arg MODEL_VERSION=2-demo -t credit-risk-api:v2 .
docker build -t credit-risk-frontend:v1 frontend
for i in credit-risk-api:v1 credit-risk-api:v2 credit-risk-frontend:v1; do minikube -p credit-risk image load "$i" --daemon; done
KCTX=credit-risk ./scripts/k8s_demo.sh | tee docs/evidence/phase-7/k8s-demo.log
```
(`--target with-model` needs `models/` from `dvc pull`.) The demo is idempotent: it creates the namespace and Secret if they are missing and applies the manifests.

Prometheus on the cluster:
```bash
kubectl --context credit-risk -n credit-risk create configmap prometheus-rules \
  --from-file=alert_rules.yml=monitoring/alert_rules.yml --dry-run=client -o yaml | kubectl --context credit-risk apply -f -
kubectl --context credit-risk apply -f k8s/monitoring/prometheus.yaml
kubectl --context credit-risk -n credit-risk port-forward svc/prometheus 9090:9090      # then open /targets
```
Both API pods show as targets (`job=credit-risk-api-pods`, label `pod=<name>`), so every dashboard panel can be split per pod.

## What the demo showed (see the log for the exact output)
| Step | What happened |
|---|---|
| Scale 2 → 3 → 2 | `kubectl scale` brought a third pod up (`1/1 Running` after about 6 s) and back down |
| Self-healing | One serving pod was deleted; the ReplicaSet created a replacement with a new name and a young AGE; the Service never dropped below one ready pod |
| Load spreading | 600 requests (8 threads) from a pod inside the cluster: **303 and 297** of them reached the two API pods (per-pod `api_requests_total{route="/predict",status="200"}`); all 600 returned 200, p50 ≈ 0.21 s, p95 ≈ 0.52 s |
| Probes affect routing | `kubectl set env … MODEL_PATH=/nonexistent/…` made the new pod fail readiness (`503` on `/health`, events `Readiness probe failed`). It stayed `0/1`, was **not** listed in the Service's endpoints, and the rollout stalled while the two old pods kept serving. `kubectl rollout undo` repaired it |
| Rolling update + rollback | `kubectl set image … credit-risk-api:v2` replaced the pods one at a time without an outage (`/health` reported model version `2-demo`); `kubectl rollout undo` returned to `v1` |

(The liveness probe also saw the 503s of the broken pod; it is deliberately slower — 40 s initial delay, 4 failures — so a slow start is not killed. A pod that stays broken would be restarted.)

## How a Service balances load
A `ClusterIP` Service gets a virtual IP. `kube-proxy` on each node programs the node's packet filter (iptables rules or IPVS) so that a new TCP connection to that IP is forwarded to one of the **ready** pods behind it, chosen pseudo-randomly (iptables mode) or round-robin (IPVS default). Readiness is what puts a pod into or out of that set: the endpoint controller adds a pod's address only while its readiness probe passes.

Limitations to be honest about:
* **Per connection, not per request.** Once a client keeps one connection open (HTTP keep-alive, gRPC), all its requests go to the same pod. The demo's load generator opens a new connection each time, which is why the spread is even. A `kubectl port-forward` to a Service pins everything to a single pod.
* **No session affinity** unless `sessionAffinity: ClientIP` is set (we do not need it: the API is stateless).
* **No awareness of load or latency**; a slow pod gets as many new connections as a fast one. A service mesh or an L7 proxy would do better.
* **Single node, local only.** There is no real failure domain: if the machine dies, everything dies. Pod anti-affinity and multiple nodes would be needed for real availability.
* **No cluster autoscaling.** The optional HPA adds pods up to the node's capacity; nothing adds nodes.
* The API keeps counters per process, so metrics must be scraped **per pod** (done) and summed in queries.
* The model is baked into the image; changing the model means a new image and a rolling update.

## HPA (optional, not exercised)
`hpa.yaml` passes a server-side dry run. It was **not** run under load in this project: it needs `minikube addons enable metrics-server -p credit-risk`, and it would fight a manual `kubectl scale` (so apply it only after the scaling demo).
