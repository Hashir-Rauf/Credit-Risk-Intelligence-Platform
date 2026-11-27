# Remaining AWS evidence runs (runbook)

Run these in order after the two pending PRs are merged. Each step says what to click and what to screenshot. Save images in
`docs/evidence/phase-6/screenshots/` with the names given.

## 1. Blocked deployment (failed quality gate)
The branch `demo/failing-gate` holds one commit (7ea957c) whose `reports/evaluation.json` says `gate.passed: false`. It is never merged.
1. GitHub > Actions > **Deploy** > Run workflow. Branch `main`, `git_ref` = `7ea957c6050e4e1b44ffc11b8fad7cc21517d558`, `model_version` empty.
2. Approve the `production` environment prompt.
3. Expected: the job fails at **Refuse to deploy a model that did not pass its quality gate**; nothing is built or pushed and the service keeps its revision.
4. Screenshot: the failed run page with that step red (`gate-blocked-deploy.png`). Confirm in ECS that the revision number did not change.
5. Afterwards delete the branch: `git push origin --delete demo/failing-gate`.

## 2. Rollback to the previous revision
1. Note the current revision (ECS > Clusters > credit-risk > credit-risk-api > Deployments) and the frontend address (`./infra/aws/endpoint.sh`).
2. GitHub > Actions > **Rollback** > Run workflow, `target` = `previous`, approve.
3. Expected: ECS rolls to the earlier revision, `services-stable` passes, the smoke test passes (`rollback-run.png`, ECS deployments `rollback-ecs.png`).
4. Side effect: the earlier revision may not contain the web UI container. To get it back, run **Deploy** again (workflow_dispatch, defaults).
5. The public IP changes with every task replacement; re-read it with `./infra/aws/endpoint.sh`.

## 3. Alarm firing
The 5xx alarm needs a real HTTP 5xx, which the service does not produce on purpose. Use a manual state change and label it as such:
```bash
aws cloudwatch set-alarm-state --profile credit-risk --region ap-southeast-2 \
  --alarm-name credit-risk-api-5xx --state-value ALARM --state-reason "demo: manual state change for the evidence run"
```
Screenshot the alarm in ALARM (`alarm-firing.png`), then the e-mail if the SNS subscription is confirmed (`alarm-email.png`). The alarm returns to OK
by itself at the next evaluation. State in the report that this alarm was triggered manually; the metric filter and threshold are unchanged.

## 4. Cost view
Billing and Cost Management > Cost Explorer (grouped by service, month to date), or AWS Settings > Billing. Screenshot with credits
remaining visible (`cost-explorer.png`). Cost data lags by up to a day.

## 5. Teardown (only when the demo is finished)
```bash
./infra/aws/teardown.sh --yes
```
Then follow the leftover checks in the README "Clean up" section and screenshot the empty ECS cluster list and ECR (`teardown-clean.png`). The DVC bucket
is kept on purpose. Do this last: after it the live URL no longer answers.

After the runs, tell Claude to update the status table in `report/main.tex` and `REPORT.md`, add the screenshots, and rebuild the PDF.
