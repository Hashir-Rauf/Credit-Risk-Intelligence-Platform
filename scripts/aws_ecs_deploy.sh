#!/usr/bin/env bash
# Register a new task-definition revision with the given image, point the service at it, wait until stable and
# smoke-test it. If the smoke test (or the wait) fails, the service is pointed back at the previous revision.
#
#   scripts/aws_ecs_deploy.sh <image-uri> <model-version> <git-sha> [frontend-image-uri]
#
# Environment: SG_ID (security group for the smoke test). Needs aws, jq, curl.
set -euo pipefail

image="${1:?image uri}"; model_version="${2:?model version}"; git_sha="${3:?git sha}"; frontend_image="${4:-}"
cluster="${CLUSTER:-credit-risk}"; service="${SERVICE:-credit-risk-api}"; family="${FAMILY:-credit-risk-api}"
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
# The AWS CLI needs a native path for file:// (Git Bash on Windows); on a Linux runner this is a no-op.
native() { cygpath -m "$1" 2>/dev/null || echo "$1"; }

previous="$(aws ecs describe-services --cluster "$cluster" --services "$service" --query 'services[0].taskDefinition' --output text)"
previous_count="$(aws ecs describe-services --cluster "$cluster" --services "$service" --query 'services[0].desiredCount' --output text)"
echo "previous revision: $previous (desired count $previous_count)"

aws ecs describe-task-definition --task-definition "$family" --query taskDefinition > $work/taskdef.json
jq --arg img "$image" --arg mv "$model_version" --arg sha "$git_sha" '
  del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy)
  | .containerDefinitions[0].image = $img
  | .containerDefinitions[0].environment |= (map(select(.name != "MODEL_VERSION" and .name != "GIT_SHA"))
      + [{name: "MODEL_VERSION", value: $mv}, {name: "GIT_SHA", value: $sha}])' $work/taskdef.json > $work/taskdef.new.json

# Optional web frontend: a second, non-essential container in the same task (it reaches the API on localhost and
# holds the API key server-side). Non-essential so that a frontend crash never takes the scoring API down.
if [ -n "$frontend_image" ]; then
  jq --arg fimg "$frontend_image" '
    . as $td
    | ($td.containerDefinitions | map(select(.name == "api")) | .[0]) as $api
    | .cpu = "512" | .memory = "1024"
    | .containerDefinitions |= (map(select(.name != "frontend")) + [{
        name: "frontend",
        image: $fimg,
        essential: false,
        portMappings: [{containerPort: 3000, protocol: "tcp"}],
        environment: [
          {name: "API_URL", value: "http://127.0.0.1:8000"},
          {name: "PORT", value: "3000"},
          {name: "HOSTNAME", value: "0.0.0.0"}
        ],
        secrets: ($api.secrets | map(select(.name == "API_KEY"))),
        logConfiguration: ($api.logConfiguration | .options["awslogs-stream-prefix"] = "frontend"),
        healthCheck: {
          command: ["CMD", "node", "-e", "fetch(\"http://127.0.0.1:3000/api/health\").then(r=>process.exit([200,503].includes(r.status)?0:1)).catch(()=>process.exit(1))"],
          interval: 30, timeout: 5, retries: 3, startPeriod: 30
        }
      }])' $work/taskdef.new.json > $work/taskdef.fe.json
  mv $work/taskdef.fe.json $work/taskdef.new.json
fi
new="$(aws ecs register-task-definition --cli-input-json "file://$(native "$work/taskdef.new.json")" --query 'taskDefinition.taskDefinitionArn' --output text)"
echo "registered: $new"

roll_back() {
  echo "::error::deployment failed; rolling back to $previous"
  if [ "$previous_count" = "0" ]; then
    aws ecs update-service --cluster "$cluster" --service "$service" --task-definition "$previous" --desired-count 0 >/dev/null
  else
    aws ecs update-service --cluster "$cluster" --service "$service" --task-definition "$previous" >/dev/null
    aws ecs wait services-stable --cluster "$cluster" --services "$service" || true
  fi
  exit 1
}

aws ecs update-service --cluster "$cluster" --service "$service" --task-definition "$new" --desired-count 1 >/dev/null
echo "waiting for the service to become stable..."
aws ecs wait services-stable --cluster "$cluster" --services "$service" || roll_back
"$here/aws_smoke.sh" || roll_back
echo "deployed $new"
{
  echo "### Deployed"
  echo "- image: \`$image\`"
  echo "- task definition: \`$new\`"
  echo "- previous (rollback target): \`$previous\`"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
