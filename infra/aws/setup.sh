#!/usr/bin/env bash
# One-time (and repeatable) creation of the AWS foundation for the scoring API. Needs AWS credentials in the
# environment (for example `aws login`, then AWS_PROFILE=<profile>). Creates billable resources: read
# docs/aws-deployment.md (cost section) first.
#
#   ALERT_EMAIL=you@example.com ALLOWED_CIDR=203.0.113.7/32 ./infra/aws/setup.sh
#
# Optional: AWS_REGION (default: the CLI's configured region), CREATE_OIDC_PROVIDER=false when the account
# already trusts GitHub Actions (then also EXISTING_OIDC_PROVIDER_ARN=<arn>), GITHUB_REPO=<owner/name>,
# CREATE_BUDGET=true to add a budget (the project already has one), FRONTEND_CIDR=<range> to open only the web
# frontend (port 3000) to a different range than ALLOWED_CIDR (for example 0.0.0.0/0 for a deliberate public demo).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
: "${ALERT_EMAIL:?set ALERT_EMAIL (alarm notifications go here)}"
: "${ALLOWED_CIDR:?set ALLOWED_CIDR, for example 203.0.113.7/32 (your public IP); never 0.0.0.0/0}"
region="${AWS_REGION:-$(aws configure get region || true)}"
: "${region:?set AWS_REGION}"
export AWS_DEFAULT_REGION="$region"

aws sts get-caller-identity --query Arn --output text >/dev/null
echo "Region: $region"

vpc="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
if [ "$vpc" = "None" ]; then echo "No default VPC in $region. Pass VPC_ID and SUBNET_IDS instead." >&2; exit 1; fi
subnets="${SUBNET_IDS:-$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$vpc" Name=default-for-az,Values=true \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')}"
vpc="${VPC_ID:-$vpc}"

echo "== bootstrap stack (ECR, bucket, log group, API-key secret, alert topic)"
aws cloudformation deploy --stack-name credit-risk-bootstrap \
  --template-file "$here/cloudformation/bootstrap.yaml" \
  --parameter-overrides AlertEmail="$ALERT_EMAIL" CreateBudget="${CREATE_BUDGET:-false}" MonthlyBudgetUsd="${MONTHLY_BUDGET_USD:-10}" \
  --tags project=credit-risk --no-fail-on-empty-changeset

echo "== service stack (cluster, roles, service, alarms, GitHub deploy role)"
aws cloudformation deploy --stack-name credit-risk-service \
  --template-file "$here/cloudformation/service.yaml" --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides VpcId="$vpc" SubnetIds="$subnets" AllowedCidr="$ALLOWED_CIDR" \
  FrontendCidr="${FRONTEND_CIDR:-}" \
  GitHubRepo="${GITHUB_REPO:-Hashir-Rauf/Credit-Risk-Intelligence-Platform}" \
  CreateOidcProvider="${CREATE_OIDC_PROVIDER:-true}" ExistingOidcProviderArn="${EXISTING_OIDC_PROVIDER_ARN:-}" \
  --tags project=credit-risk --no-fail-on-empty-changeset

out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
echo
echo "Done. Add these as GitHub repository variables (Settings > Secrets and variables > Actions > Variables):"
echo "  AWS_REGION          = $region"
echo "  AWS_DEPLOY_ROLE_ARN = $(out credit-risk-service DeployRoleArn)"
echo "  AWS_SERVICE_SG_ID   = $(out credit-risk-service SecurityGroupId)"
echo "Confirm the SNS subscription e-mail sent to $ALERT_EMAIL, or alarms will not reach you."
