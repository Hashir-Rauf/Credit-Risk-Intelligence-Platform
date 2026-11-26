#!/usr/bin/env bash
# Point the service at an earlier task-definition revision and smoke-test it.
#
#   scripts/aws_rollback.sh previous        # the revision before the one the service runs now
#   scripts/aws_rollback.sh 7               # a specific revision number
#   scripts/aws_rollback.sh <git-sha>       # the newest revision whose image is tagged with that SHA
set -euo pipefail

target="${1:-previous}"
cluster="${CLUSTER:-credit-risk}"; service="${SERVICE:-credit-risk-api}"; family="${FAMILY:-credit-risk-api}"
here="$(cd "$(dirname "$0")" && pwd)"

current="$(aws ecs describe-services --cluster "$cluster" --services "$service" --query 'services[0].taskDefinition' --output text)"
echo "current revision: $current"

if [ "$target" = "previous" ]; then
  # Revisions are numbered upwards; the newest one below the current one that has a real (non-bootstrap) image.
  current_number="${current##*:}"
  chosen=""
  for arn in $(aws ecs list-task-definitions --family-prefix "$family" --status ACTIVE --sort DESC --query 'taskDefinitionArns[]' --output text); do
    number="${arn##*:}"
    [ "$number" -lt "$current_number" ] || continue
    image="$(aws ecs describe-task-definition --task-definition "$arn" --query 'taskDefinition.containerDefinitions[0].image' --output text)"
    case "$image" in *:bootstrap) continue ;; esac
    chosen="$arn"; break
  done
elif [[ "$target" =~ ^[0-9]+$ ]]; then
  chosen="$(aws ecs describe-task-definition --task-definition "$family:$target" --query 'taskDefinition.taskDefinitionArn' --output text)"
else
  chosen=""
  for arn in $(aws ecs list-task-definitions --family-prefix "$family" --status ACTIVE --sort DESC --query 'taskDefinitionArns[]' --output text); do
    image="$(aws ecs describe-task-definition --task-definition "$arn" --query 'taskDefinition.containerDefinitions[0].image' --output text)"
    case "$image" in *":$target") chosen="$arn"; break ;; esac
  done
fi
[ -n "${chosen:-}" ] || { echo "::error::no matching earlier revision found for '$target'" >&2; exit 1; }
echo "rolling back to: $chosen"

aws ecs update-service --cluster "$cluster" --service "$service" --task-definition "$chosen" --desired-count 1 >/dev/null
aws ecs wait services-stable --cluster "$cluster" --services "$service"
"$here/aws_smoke.sh"
{
  echo "### Rolled back"
  echo "- from: \`$current\`"
  echo "- to: \`$chosen\`"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
