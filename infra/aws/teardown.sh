#!/usr/bin/env bash
# Remove everything infra/aws/setup.sh and the deploy workflow created, then list what is left.
# Billable resources: the ECS service/task, the public IPv4 address, the ECR images, the S3 objects, the secret.
# Run it with the same credentials as setup.sh:   ./infra/aws/teardown.sh --yes
set -euo pipefail

if [ "${1:-}" != "--yes" ]; then
  echo "This deletes the ECS service, cluster, ECR images, artifact bucket contents, secret, logs and roles."
  echo "Re-run with --yes to continue."
  exit 1
fi

stack_exists() { aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1; }

if stack_exists credit-risk-service; then
  echo "== stop the service so the task (and its public IP) stops billing"
  aws ecs update-service --cluster credit-risk --service credit-risk-api --desired-count 0 >/dev/null 2>&1 || true
  echo "== delete service stack"
  aws cloudformation delete-stack --stack-name credit-risk-service
  aws cloudformation wait stack-delete-complete --stack-name credit-risk-service
fi

echo "== deregister and delete task definition revisions"
for arn in $(aws ecs list-task-definitions --family-prefix credit-risk-api --status ACTIVE --query 'taskDefinitionArns[]' --output text); do
  aws ecs deregister-task-definition --task-definition "$arn" >/dev/null
done
inactive="$(aws ecs list-task-definitions --family-prefix credit-risk-api --status INACTIVE --query 'taskDefinitionArns[]' --output text)"
# delete-task-definitions accepts at most 10 per call
set -- $inactive
while [ "$#" -gt 0 ]; do
  batch=("${@:1:10}"); shift "${#batch[@]}"
  aws ecs delete-task-definitions --task-definitions "${batch[@]}" >/dev/null
done

if stack_exists credit-risk-bootstrap; then
  bucket="$(aws cloudformation describe-stacks --stack-name credit-risk-bootstrap \
    --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text)"
  echo "== empty the artifact bucket ($bucket)"
  aws s3 rm "s3://$bucket" --recursive >/dev/null || true
  echo "== delete bootstrap stack (ECR repository is emptied with it)"
  aws cloudformation delete-stack --stack-name credit-risk-bootstrap
  aws cloudformation wait stack-delete-complete --stack-name credit-risk-bootstrap
fi

# A secret is scheduled for deletion by default; force it so the name is free and nothing is retained.
for secret in credit-risk-api/api-key credit-risk-api/prediction-log-salt; do
  aws secretsmanager delete-secret --secret-id "$secret" --force-delete-without-recovery >/dev/null 2>&1 || true
done

echo
echo "== what is left (everything below should be empty or 'None')"
echo "stacks:   $(aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED --query "StackSummaries[?starts_with(StackName,'credit-risk')].StackName" --output text)"
echo "clusters: $(aws ecs list-clusters --query "clusterArns[?contains(@, 'credit-risk')]" --output text)"
echo "ecr:      $(aws ecr describe-repositories --query "repositories[?repositoryName=='credit-risk-api'].repositoryName" --output text 2>/dev/null || true)"
echo "secrets:  $(aws secretsmanager list-secrets --query "SecretList[?starts_with(Name,'credit-risk')].Name" --output text)"
echo "logs:     $(aws logs describe-log-groups --log-group-name-prefix /ecs/credit-risk --query 'logGroups[].logGroupName' --output text)"
echo "buckets:  $(aws s3api list-buckets --query "Buckets[?contains(Name,'credit-risk')].Name" --output text)"
echo "The IAM OIDC provider for GitHub is deleted with the service stack unless it pre-existed (CREATE_OIDC_PROVIDER=false)."
