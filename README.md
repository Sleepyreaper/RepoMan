# RepoMan

RepoMan is a customer-aware Terraform pull-request gate. It evaluates a
Terraform plan against deterministic requirements, blocks noncompliant changes,
and tells the developer exactly how to fix each issue.

## Prototype capabilities

- Customer-specific policy in `.repoman/policy.json`
- Terraform plan JSON evaluation
- Required regions and governance tags
- Public-access and resilience controls
- Destructive-change limits
- Forbidden resource types
- Lock-file, state, variable-file, and inline-secret checks
- GitHub annotations, job summary, action outputs, and merge-blocking exit code
- Approved and denied example plans with remediation

## Run locally

```bash
python3 -m unittest discover -s tests -v

python3 src/repoman.py evaluate \
  --policy .repoman/policy.json \
  --plan examples/plans/approved.json \
  --report /tmp/repoman-approved.md
```

The denied scenario intentionally exits with status `1`:

```bash
python3 src/repoman.py evaluate \
  --policy .repoman/policy.json \
  --plan examples/plans/denied.json \
  --report /tmp/repoman-denied.md
```

## Live proof on GitHub

The required workflow evaluates `examples/demo/current-plan.json` as its final
merge gate. A pull request that changes this file to a noncompliant plan gets a
red **RepoMan Terraform policy** check, inline errors, and a **How to fix**
summary. A compliant plan gets a green check and can merge.

For a presentation:

1. Open the denied demo PR and show the blocked merge button.
2. Open its failed **RepoMan Terraform policy** check and expand the
   **Evaluate the pull request plan** step.
3. Show the job summary listing each violated customer rule and remediation.
4. Open the approved demo PR and show the same required check passing.

## Use in a Terraform repository

Generate a speculative plan, convert it to Terraform's documented JSON format,
and invoke RepoMan:

```yaml
name: Terraform PR gate

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  merge_group:

permissions:
  contents: read

jobs:
  repoman:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false

      - uses: hashicorp/setup-terraform@b9cd54a3c349d3f38e8881555d616ced269862dd
        with:
          terraform_version: "1.15.5"
          terraform_wrapper: false

      - run: terraform fmt -check -recursive -diff
      - run: terraform init -backend=false -input=false
      - run: terraform validate
      - run: terraform plan -input=false -out=tfplan
      - run: terraform show -json tfplan > plan.json

      - uses: YOUR-ORG/repoman@PINNED_COMMIT_SHA
        with:
          policy-file: .repoman/policy.json
          plan-json: plan.json
```

For a real cloud plan, authenticate with a read-only planning identity through
GitHub OIDC rather than a long-lived secret.

Configure **RepoMan Terraform policy** as a required status check in a GitHub
ruleset. Passing is RepoMan's approval; failing is its denial.

Keep the required workflow unfiltered so GitHub always receives a conclusion;
path-filtered required checks can remain pending when a pull request does not
match the filter.

## Design

- [Architecture and phased Foundry design](docs/architecture.md)
- [Terraform check-in requirements](docs/terraform-check-in-requirements.md)
- [Common PR problems and fixes](examples/pr-scenarios.md)

The recommended architecture keeps deterministic policy as the authority and
uses Microsoft Foundry only as an optional, constrained explanation layer.

## Research basis

- [GitHub secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub OpenID Connect](https://docs.github.com/en/actions/concepts/security/openid-connect)
- [GitHub repository rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [HashiCorp: Automate Terraform with GitHub Actions](https://developer.hashicorp.com/terraform/tutorials/automation/github-actions)
- [HashiCorp Terraform JSON output format](https://developer.hashicorp.com/terraform/internals/json-format)
- [Microsoft Foundry agent identity](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity)
