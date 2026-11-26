#!/usr/bin/env bash
# Smoke-test the deployed API from a GitHub runner: /health, an authenticated /predict, and a rejected
# request without the key. The runner's address is allowed into the security group only for the duration of
# the test and removed again on exit (even on failure).
#
# Environment: CLUSTER (default credit-risk), SERVICE (default credit-risk-api), SG_ID (required).
set -euo pipefail

cluster="${CLUSTER:-credit-risk}"
service="${SERVICE:-credit-risk-api}"
: "${SG_ID:?SG_ID is required}"
here="$(cd "$(dirname "$0")" && pwd)"

task="$(aws ecs list-tasks --cluster "$cluster" --service-name "$service" --desired-status RUNNING --query 'taskArns[0]' --output text)"
[ "$task" != "None" ] || { echo "no running task" >&2; exit 1; }
eni="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value" --output text)"
ip="$(aws ec2 describe-network-interfaces --network-interface-ids "$eni" --query 'NetworkInterfaces[0].Association.PublicIp' --output text)"
base="http://$ip:8000"

runner="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')/32"
opened=0
cleanup() {
  if [ "$opened" = 1 ]; then
    for port in 8000 3000; do
      aws ec2 revoke-security-group-ingress --group-id "$SG_ID" --protocol tcp --port "$port" --cidr "$runner" >/dev/null 2>&1 || true
    done
  fi
}
trap cleanup EXIT
for port in 8000 3000; do
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port "$port" --cidr "$runner" >/dev/null 2>&1 && opened=1
done

echo "== /health"
for attempt in $(seq 1 30); do
  code="$(curl -s -o /tmp/health.json -w '%{http_code}' --max-time 5 "$base/health" || true)"
  [ "$code" = "200" ] && break
  echo "  attempt $attempt: HTTP $code"; sleep 5
done
[ "$code" = "200" ] || { echo "health check failed (HTTP $code)" >&2; exit 1; }
cat /tmp/health.json; echo

key="$(aws secretsmanager get-secret-value --secret-id credit-risk-api/api-key --query SecretString --output text)"
[ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::$key" || true

echo "== /predict without a key must be rejected"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST "$base/predict" \
  -H 'Content-Type: application/json' --data @"$here/sample_predict.json")"
[ "$code" = "401" ] || { echo "expected 401 without a key, got $code" >&2; exit 1; }

echo "== /predict with the key"
body="$(curl -fsS --max-time 20 -X POST "$base/predict" -H 'Content-Type: application/json' \
  -H "X-API-Key: $key" --data @"$here/sample_predict.json")"
echo "$body"
score="$(echo "$body" | jq -r '.predictions[0].risk_score')"
mv="$(echo "$body" | jq -r '.model_version')"
echo "$score" | jq -e '. >= 0 and . <= 1' >/dev/null || { echo "risk_score out of range: $score" >&2; exit 1; }
[ -n "$mv" ] && [ "$mv" != "null" ] || { echo "model version missing" >&2; exit 1; }
echo "prediction ok, model version $mv"

# The web frontend runs as a second container of the same task when one was deployed.
has_frontend="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task"   --query "length(tasks[0].containers[?name=='frontend'])" --output text)"
if [ "$has_frontend" != "0" ]; then
  fe="http://$ip:3000"
  echo "== frontend"
  for attempt in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$fe/api/health" || true)"
    [ "$code" = "200" ] && break
    echo "  attempt $attempt: HTTP $code"; sleep 5
  done
  [ "$code" = "200" ] || { echo "frontend /api/health failed (HTTP $code); it must reach the API (HTTP $code)" >&2; exit 1; }
  page="$(curl -fsS --max-time 15 "$fe/")"
  echo "$page" | grep -q "Score a customer" || { echo "frontend page did not render the scoring form" >&2; exit 1; }
  echo "frontend ok"
fi
