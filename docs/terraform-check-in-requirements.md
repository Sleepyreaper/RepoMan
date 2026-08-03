# Terraform check-in requirements

RepoMan's recommended minimum gate for customer Terraform repositories is:

1. `terraform fmt -check -recursive -diff` passes.
2. `terraform init -backend=false` and `terraform validate` pass in every changed root module.
3. `.terraform.lock.hcl` is committed and reviewed when provider selections change.
4. A speculative plan is saved and converted with `terraform show -json`.
5. The plan contains no customer-policy violations: geography, public access, required tags, approved resource types, resilience settings, or destructive changes.
6. State, `.tfvars`, credentials, tokens, and literal secrets are not committed.
7. Every denial identifies the policy, affected resource, observed value, and a concrete remediation.
8. The RepoMan status check is required by a GitHub ruleset and cannot be bypassed by changing only the PR description.
9. Pull-request workflows use read-only repository permissions and do not expose cloud secrets to untrusted code.
10. Cloud access, when planning requires it, uses GitHub OIDC and a read-only planning identity scoped to the customer environment.

## Customer policy ownership

The policy file is code, but it should not be owned by the same developers who
are subject to it. Protect `.repoman/**`, `.github/workflows/**`, and
`CODEOWNERS` with a platform/security owner and require their review.

## Exceptions

Exceptions must be explicit, time-bound, attributable, and separately approved.
Do not allow free-form prompt text to weaken a deterministic rule. A production
implementation should store exceptions in a signed central policy service or a
protected repository.

