#!/usr/bin/env bash
# Phase 7 demonstration on a local cluster (minikube or kind): replicas, self-healing, probes affecting
# routing, load spreading, rolling update and rollback. Output is meant to be saved as evidence:
#
#   KCTX=credit-risk ./scripts/k8s_demo.sh | tee docs/evidence/phase-7/k8s-demo.log
#
# Prerequisites: images credit-risk-api:v1, credit-risk-api:v2 and credit-risk-frontend:v1 loaded into the
# cluster (`minikube image load <image> -p <profile>`); see docs/kubernetes.md.
set -euo pipefail

export MSYS_NO_PATHCONV=1
ctx="${KCTX:-$(kubectl config current-context)}"
k() { kubectl --context "$ctx" -n credit-risk "$@"; }
cd "$(dirname "$0")/.."  # relative paths below also work with a Windows kubectl
step() { printf '\n==== %s\n' "$*"; }
pods() { k get pods -l app.kubernetes.io/name=api -o wide; }
version_of() { k get deploy api -o jsonpath='{.spec.template.spec.containers[0].image}'; }

step "0. Namespace, secret, manifests (context: $ctx)"
kubectl --context "$ctx" apply -f "k8s/namespace.yaml"
if ! k get secret api-secret >/dev/null 2>&1; then
  # A random key that never touches the repository or this log.
  k create secret generic api-secret --from-literal=API_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
fi
for f in api-config api-deployment api-service api-pdb frontend-deployment; do kubectl --context "$ctx" apply -f "k8s/$f.yaml"; done
k rollout status deploy/api --timeout=300s
k rollout status deploy/frontend --timeout=300s
pods
k get endpoints api

step "1. Scale 2 -> 3 -> 2"
k scale deploy/api --replicas=3
k rollout status deploy/api --timeout=300s
pods
k scale deploy/api --replicas=2
k rollout status deploy/api --timeout=120s

step "2. Self-healing: delete one pod, a replacement appears"
# Wait until the scale-down has finished so the pod we delete is a healthy serving pod, not one already leaving.
for _ in $(seq 1 30); do
  total="$(k get pods -l app.kubernetes.io/name=api --no-headers | wc -l)"
  leaving="$(k get pods -l app.kubernetes.io/name=api --no-headers | grep -c Terminating || true)"
  [ "$total" = "2" ] && [ "$leaving" = "0" ] && break
  sleep 2
done
echo "before:"; pods
victim="$(k get pods -l app.kubernetes.io/name=api -o jsonpath='{.items[0].metadata.name}')"
echo "deleting $victim"
k delete pod "$victim" --wait=false
for _ in $(seq 1 40); do
  ready="$(k get deploy api -o jsonpath='{.status.readyReplicas}')"
  [ "${ready:-0}" = "2" ] && ! k get pod "$victim" >/dev/null 2>&1 && break
  sleep 3
done
echo "after (a pod with a new name and a young AGE has replaced $victim):"; pods
k get events --sort-by=.lastTimestamp | grep -E "Killing|SuccessfulCreate" | tail -4 || true

step "3. Load test from inside the cluster: requests are spread over the pods"
k delete job loadgen --ignore-not-found >/dev/null
echo "(replacement pods start with no /predict counters, so the totals below come from this test only)"
kubectl --context "$ctx" apply -f "k8s/demo/loadgen-job.yaml"
k wait --for=condition=complete job/loadgen --timeout=300s
k logs job/loadgen
for pod in $(k get pods -l app.kubernetes.io/name=api -o name); do
  k exec "$pod" -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode())" \
    | grep -E '^api_requests_total\{.*route="/predict".*status="200"' | sed "s|^|AFTER  $pod |" || true
done

step "4. Probes affect routing: a bad MODEL_PATH makes the new pod fail readiness and stay out of the Service"
k set env deploy/api MODEL_PATH=/nonexistent/model.joblib
sleep 45
pods
echo "--- endpoints (only Ready pods are listed):"
k get endpoints api
echo "--- rollout is blocked, the old pods keep serving:"
k rollout status deploy/api --timeout=20s || true
bad="$(k get pods -l app.kubernetes.io/name=api --field-selector=status.phase=Running -o jsonpath='{range .items[?(@.status.containerStatuses[0].ready==false)]}{.metadata.name}{"\n"}{end}' | head -1)"
echo "--- probe failure events for $bad:"
k describe pod "$bad" | grep -E "Readiness|Unhealthy" | tail -4 || true
echo "--- repair: roll back the bad configuration"
k rollout undo deploy/api
k rollout status deploy/api --timeout=300s
pods
k get endpoints api

step "5. Rolling update v1 -> v2, then rollback"
echo "before: $(version_of)"
k set image deploy/api api=credit-risk-api:v2
k rollout status deploy/api --timeout=300s
echo "after:  $(version_of)"
pods
k exec "$(k get pods -l app.kubernetes.io/name=api -o name | head -1)" -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
k rollout history deploy/api
k rollout undo deploy/api
k rollout status deploy/api --timeout=300s
echo "rolled back: $(version_of)"
pods

step "Done"
