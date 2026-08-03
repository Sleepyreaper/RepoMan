#!/usr/bin/env python3
"""Deterministic Terraform pull-request policy evaluator."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXIT_APPROVE = 0
EXIT_DENY = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    subject: str
    message: str
    fix: str
    file: str | None = None
    line: int | None = None


class PolicyError(ValueError):
    """Raised when policy input is invalid."""


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PolicyError(f"{label} not found: {path}") from error
    except json.JSONDecodeError as error:
        raise PolicyError(
            f"{label} is not valid JSON at line {error.lineno}, column {error.colno}: {path}"
        ) from error

    if not isinstance(value, dict):
        raise PolicyError(f"{label} must contain a JSON object: {path}")
    return value


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("version") != 1:
        raise PolicyError("policy version must be 1")
    if not isinstance(policy.get("customer"), dict):
        raise PolicyError("policy must define a customer object")
    if not policy["customer"].get("name"):
        raise PolicyError("policy customer.name is required")
    if not isinstance(policy.get("requirements"), dict):
        raise PolicyError("policy must define a requirements object")


def get_nested(value: Any, dotted_path: str) -> tuple[bool, Any]:
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index < len(current):
                current = current[index]
                continue
        return False, None
    return True, current


def is_unknown(after_unknown: Any, dotted_path: str) -> bool:
    found, value = get_nested(after_unknown, dotted_path)
    return found and value is True


def normalize_resource(
    resource: dict[str, Any], after: dict[str, Any], after_unknown: dict[str, Any]
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Map the credential-free demo harness to its represented Azure resource."""
    address = resource.get("address", "unknown")
    resource_type = resource.get("type", "unknown")
    if resource_type != "terraform_data":
        return address, resource_type, after, after_unknown

    input_value = after.get("input")
    if not isinstance(input_value, dict):
        return address, resource_type, after, after_unknown

    represented_type = input_value.get("resource_type")
    if not isinstance(represented_type, str):
        return address, resource_type, after, after_unknown

    unknown_input = after_unknown.get("input")
    if not isinstance(unknown_input, dict):
        unknown_input = {}
    normalized_after = {
        key: value for key, value in input_value.items() if key != "resource_type"
    }
    normalized_unknown = {
        key: value for key, value in unknown_input.items() if key != "resource_type"
    }
    return (
        f"{address} ({represented_type})",
        represented_type,
        normalized_after,
        normalized_unknown,
    )


def evaluate_plan(
    policy: dict[str, Any], plan: dict[str, Any]
) -> list[Finding]:
    requirements = policy["requirements"]
    findings: list[Finding] = []
    changes = plan.get("resource_changes", [])
    if not isinstance(changes, list):
        raise PolicyError("plan resource_changes must be an array")

    destructive_addresses = []
    for resource in changes:
        if not isinstance(resource, dict):
            continue
        change = resource.get("change", {})
        actions = change.get("actions", [])
        if "delete" in actions:
            destructive_addresses.append(resource.get("address", "unknown"))

    max_destroyed = requirements.get("max_destroyed_resources")
    if isinstance(max_destroyed, int) and len(destructive_addresses) > max_destroyed:
        findings.append(
            Finding(
                rule_id="destructive-change-limit",
                title="Destructive change limit exceeded",
                subject=", ".join(destructive_addresses),
                message=(
                    f"Plan destroys or replaces {len(destructive_addresses)} resource(s); "
                    f"the customer limit is {max_destroyed}."
                ),
                fix=(
                    "Remove the destroy/replace operation. If destruction is intentional, "
                    "change the customer policy through its separately reviewed exception process."
                ),
            )
        )

    forbidden_types = {
        resource_type: rule
        for rule in requirements.get("forbidden_resource_types", [])
        for resource_type in rule.get("resource_types", [])
    }
    allowed_regions = set(requirements.get("allowed_regions", []))
    required_tags = requirements.get("required_tags", {})

    for resource in changes:
        if not isinstance(resource, dict):
            continue

        change = resource.get("change", {})
        actions = change.get("actions", [])
        after = change.get("after")
        after_unknown = change.get("after_unknown") or {}

        if after is None or actions == ["delete"] or actions == ["no-op"]:
            continue
        if not isinstance(after, dict):
            after = {}

        address, resource_type, after, after_unknown = normalize_resource(
            resource, after, after_unknown
        )

        forbidden_rule = forbidden_types.get(resource_type)
        if forbidden_rule:
            findings.append(
                Finding(
                    rule_id="forbidden-resource-type",
                    title="Forbidden resource type",
                    subject=address,
                    message=forbidden_rule["message"],
                    fix=forbidden_rule["fix"],
                )
            )

        if allowed_regions:
            region_attribute = "location" if "location" in after else "region"
            if is_unknown(after_unknown, region_attribute):
                findings.append(
                    Finding(
                        rule_id="allowed-region",
                        title="Deployment region must be known",
                        subject=address,
                        message="The planned region is unknown, so customer residency cannot be verified.",
                        fix=(
                            "Make the resource location resolve during planning and choose one of: "
                            + ", ".join(sorted(allowed_regions))
                            + "."
                        ),
                    )
                )
            elif region_attribute in after and after[region_attribute] not in allowed_regions:
                findings.append(
                    Finding(
                        rule_id="allowed-region",
                        title="Deployment region is not approved",
                        subject=address,
                        message=f"`{after[region_attribute]}` is outside the customer's allowed regions.",
                        fix="Use one of: " + ", ".join(sorted(allowed_regions)) + ".",
                    )
                )

        if required_tags and "tags" in after:
            tags = after.get("tags")
            if not isinstance(tags, dict):
                tags = {}
            missing_tags = sorted(key for key in required_tags if not tags.get(key))
            if missing_tags:
                examples = ", ".join(
                    f"{key}={required_tags[key]}" for key in missing_tags
                )
                findings.append(
                    Finding(
                        rule_id="required-tags",
                        title="Required customer tags are missing",
                        subject=address,
                        message="Missing tags: " + ", ".join(missing_tags) + ".",
                        fix=f"Add the missing keys to `tags`, for example: {examples}.",
                    )
                )
        elif required_tags and is_unknown(after_unknown, "tags"):
            findings.append(
                Finding(
                    rule_id="required-tags",
                    title="Required customer tags must be known",
                    subject=address,
                    message="The planned tags are unknown, so ownership and governance cannot be verified.",
                    fix="Make all required tags resolve during planning.",
                )
            )

        for rule in requirements.get("attribute_rules", []):
            if resource_type not in rule.get("resource_types", []):
                continue
            attribute = rule.get("attribute")
            if not attribute:
                raise PolicyError("every attribute rule must define attribute")
            found, actual = get_nested(after, attribute)
            allowed_values = rule.get("allowed_values", [])
            if is_unknown(after_unknown, attribute):
                actual_text = "unknown at plan time"
            elif not found:
                actual_text = "not explicitly configured"
            elif actual in allowed_values:
                continue
            else:
                actual_text = repr(actual)

            findings.append(
                Finding(
                    rule_id=rule["id"],
                    title=rule["title"],
                    subject=address,
                    message=f"{rule['message']} Current value: {actual_text}.",
                    fix=rule["fix"],
                )
            )

    return findings


def path_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern)


def evaluate_source(
    policy: dict[str, Any], repo_root: Path
) -> list[Finding]:
    requirements = policy["requirements"]
    findings: list[Finding] = []

    for required_file in requirements.get("required_files", []):
        if not (repo_root / required_file).is_file():
            findings.append(
                Finding(
                    rule_id="required-file",
                    title="Required Terraform file is missing",
                    subject=required_file,
                    message=f"`{required_file}` is required for reproducible provider selection.",
                    fix="Run `terraform init` and commit the generated lock file.",
                    file=required_file,
                )
            )

    files = [
        path
        for path in repo_root.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".terraform" not in path.parts
    ]
    forbidden_patterns = requirements.get("forbidden_file_globs", [])
    for path in files:
        relative = path.relative_to(repo_root).as_posix()
        for pattern in forbidden_patterns:
            if path_matches(relative, pattern):
                findings.append(
                    Finding(
                        rule_id="forbidden-file",
                        title="Sensitive Terraform file must not be committed",
                        subject=relative,
                        message=f"`{relative}` matches forbidden pattern `{pattern}`.",
                        fix=(
                            "Remove the file from Git, rotate any exposed values, and store runtime "
                            "values in the approved secret or variable service."
                        ),
                        file=relative,
                    )
                )
                break

    if requirements.get("forbid_inline_secrets"):
        secret_pattern = re.compile(
            r'(?im)^\s*(password|client_secret|api_key|access_key|secret_key|token)\s*=\s*"([^"$][^"]*)"'
        )
        for path in files:
            if path.suffix != ".tf":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(repo_root).as_posix()
            for match in secret_pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    Finding(
                        rule_id="inline-secret",
                        title="Possible inline secret detected",
                        subject=f"{relative}:{line}",
                        message=f"`{match.group(1)}` appears to contain a literal secret.",
                        fix=(
                            "Remove and rotate the value. Reference an input variable backed by the "
                            "approved secret store instead."
                        ),
                        file=relative,
                        line=line,
                    )
                )

    return findings


def markdown_report(policy: dict[str, Any], findings: list[Finding]) -> str:
    customer = policy["customer"]
    decision = "DENY" if findings else "APPROVE"
    icon = "❌" if findings else "✅"
    policy_digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    evaluated_sha = os.environ.get("GITHUB_SHA", "local")
    lines = [
        f"# {icon} RepoMan decision: {decision}",
        "",
        f"**Customer:** {customer['name']}",
        "",
        f"**Context:** {customer.get('context', 'Not specified')}",
        "",
        f"**Policy:** v{policy['version']} (`sha256:{policy_digest}`)",
        "",
        f"**Evaluated commit:** `{evaluated_sha}`",
        "",
    ]

    if not findings:
        lines.extend(
            [
                "The Terraform plan satisfies the configured customer requirements.",
                "",
                "> RepoMan is a policy status check. Configure it as a required check in a GitHub ruleset to enforce this decision.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"RepoMan found **{len(findings)} blocking issue(s)**.",
            "",
            "| Rule | Subject | Problem |",
            "| --- | --- | --- |",
        ]
    )
    for finding in findings:
        message = finding.message.replace("|", "\\|").replace("\n", " ")
        subject = finding.subject.replace("|", "\\|")
        lines.append(f"| `{finding.rule_id}` | `{subject}` | {message} |")

    lines.extend(["", "## How to fix", ""])
    for index, finding in enumerate(findings, start=1):
        lines.extend(
            [
                f"### {index}. {finding.title}",
                "",
                f"**Affected:** `{finding.subject}`",
                "",
                finding.fix,
                "",
            ]
        )
    lines.extend(
        [
            "> Re-run `terraform plan -out=tfplan` and `terraform show -json tfplan > plan.json` after making the changes.",
            "",
        ]
    )
    return "\n".join(lines)


def escape_workflow_command(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_github_output(
    decision: str, findings: list[Finding], report_file: Path, report: str
) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"decision={decision}\n")
            output.write(f"finding-count={len(findings)}\n")
            output.write(f"report-file={report_file}\n")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(report)

    for finding in findings:
        properties = [f"title=RepoMan: {escape_workflow_command(finding.title)}"]
        if finding.file:
            properties.append(f"file={escape_workflow_command(finding.file)}")
        if finding.line:
            properties.append(f"line={finding.line}")
        detail = escape_workflow_command(
            f"[{finding.rule_id}] {finding.subject}: {finding.message} Fix: {finding.fix}"
        )
        print(f"::error {','.join(properties)}::{detail}")


def evaluate(args: argparse.Namespace) -> int:
    try:
        policy_path = Path(args.policy).resolve()
        plan_path = Path(args.plan).resolve()
        repo_root = Path(args.repo_root).resolve()
        report_file = Path(args.report).resolve()

        policy = load_json(policy_path, "policy")
        validate_policy(policy)
        plan = load_json(plan_path, "Terraform plan")
        findings = evaluate_plan(policy, plan)
        if not args.plan_only:
            findings = evaluate_source(policy, repo_root) + findings
        report = markdown_report(policy, findings)

        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(report, encoding="utf-8")
        decision = "deny" if findings else "approve"

        if args.github:
            emit_github_output(decision, findings, report_file, report)
        print(report)
        return EXIT_DENY if findings else EXIT_APPROVE
    except (OSError, PolicyError) as error:
        print(f"RepoMan configuration error: {error}", file=sys.stderr)
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repoman")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = subparsers.add_parser(
        "evaluate", help="evaluate a Terraform plan against a customer policy"
    )
    evaluate_parser.add_argument("--policy", required=True)
    evaluate_parser.add_argument("--plan", required=True)
    evaluate_parser.add_argument("--repo-root", default=".")
    evaluate_parser.add_argument("--report", default="repoman-report.md")
    evaluate_parser.add_argument("--github", action="store_true")
    evaluate_parser.add_argument("--plan-only", action="store_true")
    evaluate_parser.set_defaults(handler=evaluate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
