# RepoMan AI review council

The end-to-end prototype uses deterministic enforcement, customer-scoped RAG,
multiple Foundry models, generated Terraform remediation, and an independent
rubber-duck review.

## Model routing

| Deployment | Role |
| --- | --- |
| `gpt-5.4-mini` | Classify changed domains and construct the RAG query |
| `text-embedding-3-large` | Embed requirements and review evidence |
| `gpt-5.6-terra` | Reason over cited requirements and generate a minimal Terraform correction |
| `gpt-5.6-sol` | Independently challenge the correction and its evidence |

All deployments are hosted by `hallofjusticefoundry`.

## Azure resources

The prototype resources are isolated in `RepoMan-Prototype-RG`:

- User-assigned managed identity for GitHub OIDC
- Azure AI Search Basic index in Central US
- Storage account for future audit artifacts
- Container Apps environment for the productized API
- Log Analytics and Application Insights

GitHub uses workload identity federation. No Foundry or Search API key is stored
in GitHub.

## One-time setup

The repository defines these non-secret GitHub variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_OPENAI_ENDPOINT` — resource root or `/openai/v1` endpoint
- `AZURE_SEARCH_ENDPOINT`

The managed identity requires `Cognitive Services OpenAI User`,
`Search Index Data Reader`, and permission to create remediation branches
through GitHub's short-lived workflow token. Its federated credential subject is
`repo:Sleepyreaper/RepoMan:pull_request`.

Seed or update the fictional corpus with:

```bash
python -m pip install -r requirements-ai.txt
export AZURE_OPENAI_ENDPOINT=https://hallofjusticefoundry.openai.azure.com
export AZURE_SEARCH_ENDPOINT=https://repoman-prototype-b1672f.search.windows.net
PYTHONPATH=src python src/repoman_council.py index
```

## Review flow

1. The trusted `pull_request_target` workflow checks out only the base branch.
2. It fetches `examples/demo/customer/main.tf` from the PR through the GitHub
   API as untrusted data.
3. A structural HCL parser allowlists only the fixed `terraform` block and two
   literal `terraform_data` resources. Providers, modules, data sources,
   provisioners, backends, interpolations, unexpected attributes, and extra
   resources are rejected.
4. Terraform builds a real plan using only the built-in `terraform_data`
   resource and an empty provider mirror that prevents provider downloads.
5. RepoMan runs deterministic Fabrikam policy checks.
6. On failure, the router creates a focused RAG query.
7. Azure AI Search returns relevant, versioned Fabrikam controls with sources.
8. Terra produces cited findings and a complete corrected Terraform file.
9. RepoMan formats, validates, plans, and policy-checks the generated code in a
   temporary workspace.
10. Sol attempts to disprove the fix.
11. Only an accepted, deterministic-policy-clean fix becomes a draft
    remediation PR targeting the original PR branch.
12. A human reviews and merges that remediation PR; RepoMan never merges it.

## Security boundary

`pull_request_target` is privileged, so the job runs only when the PR targets
the repository default branch and checks out that trusted default branch
explicitly. It never checks out the PR branch. The GitHub write token is exposed
only to API steps, and Azure OIDC login occurs only after the untrusted file has
passed structural validation and credential-free planning.

The generated file is also structurally parsed. A deterministic semantic diff
rejects changes to fields that were not named by the original policy findings.
Production support for arbitrary Terraform still requires a separate sandboxed
worker without GitHub write credentials or model identity.

## Current limitation

The credential-free harness represents Azure resource attributes through
`terraform_data.input.resource_type`. This makes the demo fully runnable
without customer cloud credentials while preserving real Terraform formatting,
validation, planning, policy evaluation, code generation, and remediation.
