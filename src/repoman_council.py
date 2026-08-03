#!/usr/bin/env python3
"""Customer-grounded, multi-model Terraform review council."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import hcl2
import requests
from azure.identity import DefaultAzureCredential

try:
    from .repoman import evaluate_plan, load_json, validate_policy
except ImportError:
    from repoman import evaluate_plan, load_json, validate_policy


SEARCH_API_VERSION = "2026-04-01"
DEFAULT_INDEX = "fabrikam-energy-controls"
ALLOWED_RESOURCE_FIELDS = {
    "storage_account": {
        "resource_type",
        "name",
        "location",
        "public_network_access_enabled",
        "allow_nested_items_to_be_public",
        "approved_private_endpoint_module",
        "tags",
    },
    "key_vault": {
        "resource_type",
        "name",
        "location",
        "purge_protection_enabled",
        "tags",
    },
}
ALLOWED_RESOURCE_TYPES = {
    "storage_account": '"azurerm_storage_account"',
    "key_vault": '"azurerm_key_vault"',
}
TAG_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
ALLOWED_FIELDS_BY_RULE = {
    "allowed-region": {"location"},
    "required-tags": {"tags"},
    "storage-private-network": {
        "public_network_access_enabled",
        "approved_private_endpoint_module",
    },
    "storage-private-containers": {"allow_nested_items_to_be_public"},
    "key-vault-purge-protection": {"purge_protection_enabled"},
}


class CouncilError(RuntimeError):
    """Raised when the review council cannot produce a trustworthy result."""


def extract_output_text(response: dict[str, Any]) -> str:
    values = [
        content.get("text", "")
        for output in response.get("output", [])
        for content in output.get("content", [])
        if content.get("type") == "output_text"
    ]
    if not values:
        raise CouncilError(f"model returned no output text: {response.get('error')}")
    return "\n".join(values)


class AzureRestClient:
    def __init__(self) -> None:
        self.credential = DefaultAzureCredential(
            exclude_managed_identity_credential=True
        )
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        if not endpoint.endswith("/openai/v1"):
            endpoint = f"{endpoint}/openai/v1"
        if ".openai.azure.com/openai/v1" not in endpoint:
            raise CouncilError(
                "AZURE_OPENAI_ENDPOINT must identify an Azure OpenAI resource endpoint"
            )
        self.openai_endpoint = endpoint
        self.search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
        self.index_name = os.environ.get("REPOMAN_SEARCH_INDEX", DEFAULT_INDEX)

    def _token(self, scope: str) -> str:
        return self.credential.get_token(scope).token

    def _request(
        self,
        method: str,
        url: str,
        scope: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(3):
            response = requests.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self._token(scope)}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if response.ok:
                if not response.content:
                    return {}
                return response.json()
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise CouncilError(
                    f"{method} {url} failed ({response.status_code}): "
                    f"{response.text[:1000]}"
                )
            time.sleep(2**attempt)
        raise CouncilError(f"{method} {url} failed after retries")

    def embedding(self, text: str) -> list[float]:
        response = self._request(
            "POST",
            f"{self.openai_endpoint}/embeddings",
            "https://cognitiveservices.azure.com/.default",
            payload={
                "model": os.environ.get(
                    "REPOMAN_EMBEDDING_MODEL", "text-embedding-3-large"
                ),
                "input": text,
                "dimensions": 1536,
            },
        )
        return response["data"][0]["embedding"]

    def structured_response(
        self,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        *,
        max_output_tokens: int = 5000,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"{self.openai_endpoint}/responses",
            "https://cognitiveservices.azure.com/.default",
            payload={
                "model": model,
                "instructions": instructions,
                "input": input_text,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                "max_output_tokens": max_output_tokens,
            },
        )
        if response.get("status") == "incomplete":
            raise CouncilError(
                f"{model} response was incomplete: {response.get('incomplete_details')}"
            )
        try:
            return json.loads(extract_output_text(response))
        except json.JSONDecodeError as error:
            raise CouncilError("model returned invalid structured JSON") from error

    def create_index(self) -> None:
        schema = {
            "name": self.index_name,
            "description": "Fictional Fabrikam Energy controls for the RepoMan demo.",
            "fields": [
                {
                    "name": "id",
                    "type": "Edm.String",
                    "key": True,
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "customer",
                    "type": "Edm.String",
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "control_id",
                    "type": "Edm.String",
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "title",
                    "type": "Edm.String",
                    "searchable": True,
                    "retrievable": True,
                },
                {
                    "name": "content",
                    "type": "Edm.String",
                    "searchable": True,
                    "retrievable": True,
                },
                {
                    "name": "resource_types",
                    "type": "Collection(Edm.String)",
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "domains",
                    "type": "Collection(Edm.String)",
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "severity",
                    "type": "Edm.String",
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "source",
                    "type": "Edm.String",
                    "searchable": True,
                    "retrievable": True,
                },
                {
                    "name": "remediation",
                    "type": "Edm.String",
                    "searchable": True,
                    "retrievable": True,
                },
                {
                    "name": "content_vector",
                    "type": "Collection(Edm.Single)",
                    "searchable": True,
                    "retrievable": False,
                    "stored": False,
                    "dimensions": 1536,
                    "vectorSearchProfile": "repoman-vector-profile",
                },
            ],
            "vectorSearch": {
                "algorithms": [
                    {
                        "name": "repoman-hnsw",
                        "kind": "hnsw",
                        "hnswParameters": {
                            "m": 4,
                            "efConstruction": 400,
                            "efSearch": 500,
                            "metric": "cosine",
                        },
                    }
                ],
                "profiles": [
                    {
                        "name": "repoman-vector-profile",
                        "algorithm": "repoman-hnsw",
                    }
                ],
            },
        }
        self._request(
            "PUT",
            f"{self.search_endpoint}/indexes/{self.index_name}?api-version={SEARCH_API_VERSION}",
            "https://search.azure.com/.default",
            payload=schema,
        )

    def upload_controls(self, corpus: dict[str, Any]) -> None:
        documents = []
        for control in corpus["controls"]:
            content = "\n".join(
                [
                    control["title"],
                    control["requirement"],
                    control["rationale"],
                    control["remediation"],
                    control["approved_pattern"],
                ]
            )
            documents.append(
                {
                    "@search.action": "mergeOrUpload",
                    "id": control["control_id"].replace("-", "_"),
                    "customer": corpus["customer"],
                    "control_id": control["control_id"],
                    "title": control["title"],
                    "content": content,
                    "resource_types": control["resource_types"],
                    "domains": control["domains"],
                    "severity": control["severity"],
                    "source": control["source"],
                    "remediation": control["remediation"],
                    "content_vector": self.embedding(content),
                }
            )
        self._request(
            "POST",
            f"{self.search_endpoint}/indexes/{self.index_name}/docs/index?api-version={SEARCH_API_VERSION}",
            "https://search.azure.com/.default",
            payload={"value": documents},
        )

    def retrieve(self, query: str, customer: str, top: int = 6) -> list[dict[str, Any]]:
        vector = self.embedding(query)
        escaped_customer = customer.replace("'", "''")
        response = self._request(
            "POST",
            f"{self.search_endpoint}/indexes/{self.index_name}/docs/search?api-version={SEARCH_API_VERSION}",
            "https://search.azure.com/.default",
            payload={
                "search": query,
                "filter": f"customer eq '{escaped_customer}'",
                "select": (
                    "control_id,title,content,resource_types,domains,severity,"
                    "source,remediation"
                ),
                "top": top,
                "vectorQueries": [
                    {
                        "kind": "vector",
                        "vector": vector,
                        "fields": "content_vector",
                        "k": top,
                    }
                ],
            },
        )
        return response.get("value", [])


CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "domains": {"type": "array", "items": {"type": "string"}},
        "resource_types": {"type": "array", "items": {"type": "string"}},
        "search_query": {"type": "string"},
    },
    "required": ["domains", "resource_types", "search_query"],
    "additionalProperties": False,
}

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "control_id": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["control_id", "evidence", "reason", "fix"],
                "additionalProperties": False,
            },
        },
        "corrected_file": {"type": "string"},
    },
    "required": ["summary", "findings", "corrected_file"],
    "additionalProperties": False,
}

VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["accept", "revise", "human_review"],
        },
        "summary": {"type": "string"},
        "objections": {"type": "array", "items": {"type": "string"}},
        "required_changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "summary", "objections", "required_changes"],
    "additionalProperties": False,
}


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_demo_hcl(source_text: str) -> dict[str, dict[str, Any]]:
    try:
        parsed = hcl2.loads(source_text)
    except Exception as error:
        raise CouncilError(f"demo HCL cannot be parsed: {error}") from error

    top_level = set(parsed) - {"__comments__"}
    if top_level != {"terraform", "resource"}:
        raise CouncilError(
            "demo HCL may contain only one terraform block and terraform_data resources"
        )

    terraform_blocks = parsed.get("terraform")
    if (
        not isinstance(terraform_blocks, list)
        or len(terraform_blocks) != 1
        or set(terraform_blocks[0]) != {"required_version", "__is_block__"}
        or terraform_blocks[0]["required_version"] != '">= 1.9.0, < 2.0.0"'
    ):
        raise CouncilError("terraform block must preserve the approved required_version")

    resources: dict[str, dict[str, Any]] = {}
    for resource in parsed.get("resource", []):
        if set(resource) != {'"terraform_data"'}:
            raise CouncilError("only terraform_data resources are allowed")
        named_resources = resource['"terraform_data"']
        if not isinstance(named_resources, dict) or len(named_resources) != 1:
            raise CouncilError("each resource must have exactly one name")
        quoted_name, body = next(iter(named_resources.items()))
        name = quoted_name.strip('"')
        if name not in ALLOWED_RESOURCE_FIELDS or name in resources:
            raise CouncilError(f"unexpected terraform_data resource: {name}")
        if set(body) != {"input", "__is_block__"} or not isinstance(
            body.get("input"), dict
        ):
            raise CouncilError(f"{name} may define only a literal input object")
        input_value = body["input"]
        if set(input_value) != ALLOWED_RESOURCE_FIELDS[name]:
            raise CouncilError(f"{name} contains unexpected or missing input fields")
        if input_value.get("resource_type") != ALLOWED_RESOURCE_TYPES[name]:
            raise CouncilError(f"{name} represents an unexpected resource type")
        tags = input_value.get("tags")
        if not isinstance(tags, dict) or any(
            not TAG_KEY_PATTERN.fullmatch(key) for key in tags
        ):
            raise CouncilError(f"{name} contains an invalid tag key")
        if any(
            "${" in value
            for value in _walk_values(input_value)
            if isinstance(value, str)
        ):
            raise CouncilError("interpolations and function expressions are not allowed")
        resources[name] = input_value

    if set(resources) != set(ALLOWED_RESOURCE_FIELDS):
        raise CouncilError("demo HCL must contain storage_account and key_vault")
    return resources


def _walk_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [
            *value.keys(),
            *(item for child in value.values() for item in _walk_values(child)),
        ]
    if isinstance(value, list):
        return [item for child in value for item in _walk_values(child)]
    return [value]


def assert_safe_demo_hcl(source_text: str) -> None:
    parse_demo_hcl(source_text)


def assert_patch_scope(
    original: str, corrected: str, deterministic_findings: list[dict[str, Any]]
) -> None:
    before = parse_demo_hcl(original)
    after = parse_demo_hcl(corrected)
    if hcl2.loads(original)["terraform"] != hcl2.loads(corrected)["terraform"]:
        raise CouncilError("generated HCL changed the terraform block")
    allowed: dict[str, set[str]] = {}
    for finding in deterministic_findings:
        match = re.search(r"terraform_data\.([A-Za-z0-9_-]+)", finding["subject"])
        if not match:
            continue
        fields = ALLOWED_FIELDS_BY_RULE.get(finding["rule_id"], set())
        allowed.setdefault(match.group(1), set()).update(fields)

    changed: list[tuple[str, str]] = []
    for resource_name in before:
        for field in before[resource_name]:
            if before[resource_name][field] != after[resource_name][field]:
                changed.append((resource_name, field))
    unauthorized = [
        f"{resource}.{field}"
        for resource, field in changed
        if field not in allowed.get(resource, set())
    ]
    if unauthorized:
        raise CouncilError(
            "generated HCL changed fields outside deterministic findings: "
            + ", ".join(unauthorized)
        )


def validate_demo_hcl(
    corrected_file: str, policy: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    assert_safe_demo_hcl(corrected_file)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "main.tf"
        source.write_text(corrected_file, encoding="utf-8")
        (root / "empty-provider-mirror").mkdir()

        format_result = run(["terraform", "fmt", "main.tf"], root)
        if format_result.returncode != 0:
            raise CouncilError(f"terraform fmt failed: {format_result.stderr}")
        corrected_file = source.read_text(encoding="utf-8")

        for command in (
            [
                "terraform",
                "init",
                "-backend=false",
                "-input=false",
                f"-plugin-dir={root / 'empty-provider-mirror'}",
            ],
            ["terraform", "validate"],
            ["terraform", "plan", "-input=false", "-out=repoman.tfplan"],
            ["terraform", "show", "-json", "repoman.tfplan"],
        ):
            result = run(command, root)
            if result.returncode != 0:
                raise CouncilError(
                    f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}"
                )
            if command[:3] == ["terraform", "show", "-json"]:
                plan = json.loads(result.stdout)

        findings = evaluate_plan(policy, plan)
        return corrected_file, {
            "terraform_fmt": "passed",
            "terraform_validate": "passed",
            "terraform_plan": "passed",
            "policy": "passed" if not findings else "failed",
            "remaining_findings": [asdict(finding) for finding in findings],
        }


def controls_for_prompt(controls: list[dict[str, Any]]) -> str:
    return json.dumps(
        [
            {
                "control_id": control["control_id"],
                "title": control["title"],
                "content": control["content"],
                "source": control["source"],
                "remediation": control["remediation"],
            }
            for control in controls
        ],
        indent=2,
    )


def generate_fix(
    client: AzureRestClient,
    source_text: str,
    deterministic_findings: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    critique: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "source_file": "examples/demo/customer/main.tf",
        "terraform": source_text,
        "deterministic_findings": deterministic_findings,
        "retrieved_controls": json.loads(controls_for_prompt(controls)),
        "prior_critique": critique,
    }
    return client.structured_response(
        os.environ.get("REPOMAN_REASONER_MODEL", "gpt-5.6-terra"),
        (
            "You are RepoMan's Terraform requirement reasoner and fixer. Use only the "
            "provided evidence and retrieved Fabrikam controls. Correct every deterministic "
            "finding with the smallest possible edit. Preserve the credential-free "
            "terraform_data demo harness, resource names, comments, and unrelated values. "
            "Return the complete corrected HCL file, not markdown. Every finding must cite "
            "one retrieved control_id. Do not invent requirements."
        ),
        json.dumps(context),
        "repoman_fix",
        FIX_SCHEMA,
        max_output_tokens=16000,
    )


def verifier_review(
    client: AzureRestClient,
    original: str,
    proposed: str,
    fix: dict[str, Any],
    controls: list[dict[str, Any]],
    validation: dict[str, Any],
) -> dict[str, Any]:
    return client.structured_response(
        os.environ.get("REPOMAN_VERIFIER_MODEL", "gpt-5.6-sol"),
        (
            "Act as an independent rubber-duck reviewer. Try to disprove the proposed "
            "Terraform fix. Check every claim against the original code, corrected code, "
            "retrieved controls, and deterministic validation. Reject unsupported control "
            "citations, broadened scope, removed safeguards, or unresolved findings. "
            "Accept only when the patch is minimal, grounded, and all validation passed."
        ),
        json.dumps(
            {
                "original": original,
                "proposed": proposed,
                "reasoner_output": fix,
                "retrieved_controls": json.loads(controls_for_prompt(controls)),
                "validation": validation,
            }
        ),
        "repoman_verification",
        VERIFIER_SCHEMA,
        max_output_tokens=3000,
    )


def markdown_review(result: dict[str, Any]) -> str:
    verifier = result["verifier"]
    lines = [
        "# RepoMan AI review council",
        "",
        f"**Customer:** {result['customer']} (fictional demo)",
        "",
        f"**Council outcome:** `{result['outcome']}`",
        "",
        "## Model council",
        "",
        f"- Router: `{result['models']['router']}`",
        f"- Reasoner/fixer: `{result['models']['reasoner']}`",
        f"- Rubber duck: `{result['models']['verifier']}`",
        f"- Embeddings: `{result['models']['embedding']}`",
        "",
        "## Retrieved requirements",
        "",
    ]
    for control in result["retrieved_controls"]:
        lines.append(
            f"- **{control['control_id']}** — {control['title']}  \n"
            f"  Source: {control['source']}"
        )
    lines.extend(["", "## Reasoning and proposed fixes", ""])
    for finding in result["reasoner"]["findings"]:
        lines.extend(
            [
                f"### {finding['control_id']}",
                "",
                f"**Evidence:** {finding['evidence']}",
                "",
                f"**Why:** {finding['reason']}",
                "",
                f"**Fix:** {finding['fix']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Deterministic validation",
            "",
            f"- Terraform formatting: {result['validation']['terraform_fmt']}",
            f"- Terraform validation: {result['validation']['terraform_validate']}",
            f"- Terraform plan: {result['validation']['terraform_plan']}",
            f"- Fabrikam policy: {result['validation']['policy']}",
            "",
            "## Independent rubber-duck review",
            "",
            f"**Verdict:** `{verifier['verdict']}`",
            "",
            verifier["summary"],
            "",
        ]
    )
    if verifier["objections"]:
        lines.extend(["**Objections:**", ""])
        lines.extend(f"- {item}" for item in verifier["objections"])
        lines.append("")
    lines.extend(
        [
            "> RepoMan generated a separate remediation patch. A human must approve the remediation pull request.",
            "",
        ]
    )
    return "\n".join(lines)


def index_corpus(args: argparse.Namespace) -> int:
    corpus = load_json(Path(args.corpus), "requirement corpus")
    client = AzureRestClient()
    client.create_index()
    client.upload_controls(corpus)
    print(f"Indexed {len(corpus['controls'])} controls into {client.index_name}.")
    return 0


def guard_source(args: argparse.Namespace) -> int:
    assert_safe_demo_hcl(Path(args.source).read_text(encoding="utf-8"))
    print("RepoMan demo source guard passed.")
    return 0


def review(args: argparse.Namespace) -> int:
    policy = load_json(Path(args.policy), "policy")
    validate_policy(policy)
    plan = load_json(Path(args.plan), "Terraform plan")
    source_path = Path(args.source)
    source_text = source_path.read_text(encoding="utf-8")
    deterministic = [asdict(item) for item in evaluate_plan(policy, plan)]
    if not deterministic:
        raise CouncilError("AI remediation is only created for deterministic failures")

    client = AzureRestClient()
    router_model = os.environ.get("REPOMAN_ROUTER_MODEL", "gpt-5.4-mini")
    classification = client.structured_response(
        router_model,
        (
            "Classify the Terraform evidence for retrieval. Return concise domains, exact "
            "represented resource types, and one search query. Do not decide compliance."
        ),
        json.dumps(
            {
                "deterministic_findings": deterministic,
                "terraform": source_text,
            }
        ),
        "repoman_classification",
        CLASSIFIER_SCHEMA,
        max_output_tokens=1000,
    )
    controls = client.retrieve(
        classification["search_query"], policy["customer"]["name"]
    )
    if not controls:
        raise CouncilError("RAG returned no customer controls")

    fix = generate_fix(client, source_text, deterministic, controls)
    assert_patch_scope(source_text, fix["corrected_file"], deterministic)
    corrected, validation = validate_demo_hcl(fix["corrected_file"], policy)
    verifier = verifier_review(
        client, source_text, corrected, fix, controls, validation
    )

    if verifier["verdict"] == "revise":
        fix = generate_fix(client, source_text, deterministic, controls, verifier)
        assert_patch_scope(source_text, fix["corrected_file"], deterministic)
        corrected, validation = validate_demo_hcl(fix["corrected_file"], policy)
        verifier = verifier_review(
            client, source_text, corrected, fix, controls, validation
        )

    outcome = (
        "remediation_ready"
        if verifier["verdict"] == "accept" and validation["policy"] == "passed"
        else "human_review"
    )
    result = {
        "customer": policy["customer"]["name"],
        "outcome": outcome,
        "classification": classification,
        "deterministic_findings": deterministic,
        "retrieved_controls": [
            {
                "control_id": item["control_id"],
                "title": item["title"],
                "source": item["source"],
                "score": item.get("@search.score"),
            }
            for item in controls
        ],
        "reasoner": {**fix, "corrected_file": corrected},
        "validation": validation,
        "verifier": verifier,
        "models": {
            "router": router_model,
            "reasoner": os.environ.get("REPOMAN_REASONER_MODEL", "gpt-5.6-terra"),
            "verifier": os.environ.get("REPOMAN_VERIFIER_MODEL", "gpt-5.6-sol"),
            "embedding": os.environ.get(
                "REPOMAN_EMBEDDING_MODEL", "text-embedding-3-large"
            ),
        },
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    patch = "".join(
        difflib.unified_diff(
            source_text.splitlines(keepends=True),
            corrected.splitlines(keepends=True),
            fromfile=f"a/{source_path.as_posix()}",
            tofile=f"b/{source_path.as_posix()}",
        )
    )
    Path(args.patch).write_text(patch, encoding="utf-8")
    report = markdown_review(result)
    Path(args.report).write_text(report, encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(report)
    print(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repoman-council")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index")
    index_parser.add_argument(
        "--corpus", default="requirements/fabrikam-energy.json"
    )
    index_parser.set_defaults(handler=index_corpus)

    guard_parser = subparsers.add_parser("guard")
    guard_parser.add_argument("--source", required=True)
    guard_parser.set_defaults(handler=guard_source)

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--policy", required=True)
    review_parser.add_argument("--plan", required=True)
    review_parser.add_argument("--source", required=True)
    review_parser.add_argument("--output", default="repoman-ai-review.json")
    review_parser.add_argument("--patch", default="repoman-remediation.patch")
    review_parser.add_argument("--report", default="repoman-ai-review.md")
    review_parser.set_defaults(handler=review)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (CouncilError, KeyError, OSError, ValueError, requests.RequestException) as error:
        print(f"RepoMan council error: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
