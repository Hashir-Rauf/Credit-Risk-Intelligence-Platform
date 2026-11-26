#!/usr/bin/env bash
# Register a new task-definition revision with the given image, point the service at it, wait until stable and
# smoke-test it. If the smoke test (or the wait) fails, the service is pointed back at the previous revision.
#
#   scripts/aws_ecs_deploy.sh <image-uri> <model-version> <git-sha>
#
# Environment: SG_ID (security group for the smoke test). Needs aws, jq, curl.
set -euo pipefail

image="${1:?image uri}"; model_version="${2:?model version}"; git_sha="${3:?git sha}"
cluster="${CLUSTER:-credit-risk}"; service="${SERVICE:-credit-risk-api}"; family="${FAMILY:-credit-risk-api}"
here="$(cd "$(dirname "$0")" && pwd)"

previous="$(aws ecs describe-services --cluster "$cluster" --services "$service" --query 'services[0].taskDefinition' --output text)"
previous_count="$(aws ecs describe-services --cluster "$cluster" --services "$service" --query 'services[0].desiredCount' --output text)"
echo "previous revision: $previous (desired count $previous_count)"

aws ecs describe-task-definition --task-definition "$family" --query taskDefinition > /tmp/taskdef.json
jq --arg img "$image" --arg mv "$model_version" --arg sha "$git_sha" '
  del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy)
  | .containerDefinitions[0].image = $img
  | .containerDefinitions[0].environment |= (map(select(.name != "MODEL_VERSION" and .name != "GIT_SHA"))
      + [{name: "MODEL_VERSION", value: $mv}, {name: "GIT_SHA", value: $sha}])' /tmp/taskdef.json > /tmp/taskdef.new.json
new="$(aws ecs register-task-definition --cli-input-json file:///tmp/taskdef.new.json --query 'taskDefinition.taskDefinitionArn' --output text)"
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
