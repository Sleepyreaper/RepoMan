# RepoMan architecture proposal

## Decision

Start with a **deterministic GitHub Action as the authoritative merge gate**.
Add Microsoft Foundry later as a constrained explanation assistant that cannot
overturn policy results.

RepoMan reports `approve` by passing its required status check and `deny` by
failing it. A GitHub ruleset makes that result enforceable. The prototype does
not submit a human-style approving review because pull-request workflows should
not receive write permissions merely to express a policy result.

## Options considered

| Option | Strengths | Risks | Recommendation |
| --- | --- | --- | --- |
| GitHub Action only | Deterministic, cheap, reviewable, fork-safe with read-only permissions | Remediation is template-driven | **Prototype and enforcement plane** |
| Action plus Foundry | Better explanations and customer-context synthesis | Added identity, cost, prompt-injection, and data-boundary concerns | **Phase 2, explanation only** |
| Foundry agent as decision maker | Flexible natural-language requirements | Nondeterministic outcomes are unsuitable for a merge gate | Do not use as the final authority |
| GitHub App plus policy service | Central policy, review API, audit, organization scale | More infrastructure and operations | Phase 3 |

## Prototype flow

```text
pull request
  -> read-only GitHub Actions workflow
  -> terraform fmt / init / validate / plan
  -> terraform show -json
  -> RepoMan deterministic evaluator
       -> customer policy JSON
       -> source controls
       -> plan controls
  -> annotations + Markdown summary + pass/fail status
  -> GitHub ruleset allows or blocks merge
```

## Why the model is not the gate

Customer requirements such as geography, public exposure, deletion protection,
and mandatory tags should produce the same answer for the same plan every time.
An LLM is useful after the decision: explain the failure, map it to an approved
module, or draft a corrected snippet. Its output must be labeled advisory and
must never convert a deterministic denial into approval.

## Phase 2: Foundry explanation assistant

1. The read-only PR workflow emits structured findings without secrets or plan
   values marked sensitive.
2. A trusted workflow obtains a short-lived Azure token through GitHub OIDC.
3. It sends only the policy ID, safe finding fields, and approved remediation
   catalog to a Foundry agent.
4. The agent returns a clearer explanation or proposed patch.
5. The deterministic RepoMan check remains the required merge gate.

Do not check out or execute pull-request code in a privileged
`pull_request_target` or `workflow_run` job. If a trusted follow-up workflow is
used, fetch only the required metadata and treat every PR-derived field as
untrusted input.

## Phase 3: organization service

Move policies and exceptions to a signed central service, distribute the check
as a reusable workflow or GitHub App, add audit storage, and optionally submit
formal GitHub review decisions. The app should have narrowly scoped checks and
pull-request permissions, while customer cloud access remains OIDC-based and
read-only for plans.

## Security boundaries

- Pin third-party actions to full commit SHAs.
- Set `permissions: contents: read` for the PR job.
- Never expose apply credentials to a pull-request workflow.
- Protect policy, workflow, action, and ownership files.
- Treat Terraform plan JSON as sensitive; do not publish it as a public artifact.
- Redact sensitive values before any model call.
- Make policy exceptions separate from the evaluated pull request.

