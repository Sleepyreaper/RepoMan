# Common Terraform PR problems

These scenarios show what RepoMan denies and the remediation it gives to the developer.

## 1. Public storage endpoint

**Problem**

```hcl
resource "azurerm_storage_account" "customer" {
  public_network_access_enabled   = true
  allow_nested_items_to_be_public = true
}
```

**RepoMan decision:** DENY

**How to fix**

```hcl
resource "azurerm_storage_account" "customer" {
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
}
```

Add the customer's approved private endpoint module before regenerating the plan.

## 2. Wrong geography and missing ownership tags

**Problem**

```hcl
location = "westus"
tags = {
  environment = "prod"
}
```

**RepoMan decision:** DENY

**How to fix**

Use `eastus2` or `centralus`, then add `owner`, `cost_center`, and
`data_classification` tags. The exact allowed regions and tag keys come from
`.repoman/policy.json`, not from a prompt.

## 3. Accidental replacement or deletion

Changing an immutable resource property can produce plan actions
`["delete", "create"]`.

**RepoMan decision:** DENY

**How to fix**

Remove the replacement. If it is an intentional customer change, use a
separately reviewed policy exception rather than changing the PR prompt or
bypassing the check.

## 4. Secret or variable values committed to Git

**Problem**

```hcl
client_secret = "literal-secret"
```

or a committed `prod.tfvars`.

**RepoMan decision:** DENY

**How to fix**

Rotate the exposed value, remove it from Git, and reference the customer's
approved secret store through an input variable.

## 5. Missing provider lock file

**RepoMan decision:** DENY

**How to fix**

Run `terraform init` and commit `.terraform.lock.hcl` so CI and developers use
the same provider selections.

## Try both outcomes

```bash
python3 src/repoman.py evaluate \
  --policy .repoman/policy.json \
  --plan examples/plans/denied.json \
  --report /tmp/repoman-denied.md

python3 src/repoman.py evaluate \
  --policy .repoman/policy.json \
  --plan examples/plans/approved.json \
  --report /tmp/repoman-approved.md
```

