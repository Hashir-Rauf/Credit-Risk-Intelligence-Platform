#!/usr/bin/env bash
# Print the public URL of the running task (there is no load balancer, so the address changes on every deployment)
# and, with --key, the API key from Secrets Manager. The key is printed only when you ask for it.
set -euo pipefail

task="$(aws ecs list-tasks --cluster credit-risk --service-name credit-risk-api --desired-status RUNNING \
  --query 'taskArns[0]' --output text)"
if [ "$task" = "None" ]; then echo "No running task. Run the deploy workflow first." >&2; exit 1; fi
eni="$(aws ecs describe-tasks --cluster credit-risk --tasks "$task" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value" --output text)"
ip="$(aws ec2 describe-network-interfaces --network-interface-ids "$eni" \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text)"
echo "http://$ip:8000"
echo "frontend: http://$ip:3000 (when deployed)" >&2
if [ "${1:-}" = "--key" ]; then
  aws secretsmanager get-secret-value --secret-id credit-risk-api/api-key --query SecretString --output text
fi
