import json
import tempfile
import unittest
from pathlib import Path

from src.repoman import evaluate_plan, evaluate_source, markdown_report


ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / ".repoman/policy.json").read_text(encoding="utf-8"))


class RepoManTests(unittest.TestCase):
    def test_approved_plan_has_no_findings(self):
        plan = json.loads(
            (ROOT / "examples/plans/approved.json").read_text(encoding="utf-8")
        )

        findings = evaluate_plan(POLICY, plan)

        self.assertEqual([], findings)
        self.assertIn("APPROVE", markdown_report(POLICY, findings))

    def test_denied_plan_returns_actionable_findings(self):
        plan = json.loads(
            (ROOT / "examples/plans/denied.json").read_text(encoding="utf-8")
        )

        findings = evaluate_plan(POLICY, plan)
        rule_ids = {finding.rule_id for finding in findings}

        self.assertIn("allowed-region", rule_ids)
        self.assertIn("required-tags", rule_ids)
        self.assertIn("storage-private-network", rule_ids)
        self.assertIn("storage-private-containers", rule_ids)
        self.assertIn("key-vault-purge-protection", rule_ids)
        self.assertIn("forbidden-resource-type", rule_ids)
        self.assertIn("destructive-change-limit", rule_ids)
        report = markdown_report(POLICY, findings)
        self.assertIn("How to fix", report)
        self.assertIn("private endpoint", report)

    def test_source_checks_detect_lockfile_secret_and_tfvars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tf").write_text(
                'resource "example" "bad" {\n  password = "do-not-commit"\n}\n',
                encoding="utf-8",
            )
            (root / "prod.tfvars").write_text("example = true\n", encoding="utf-8")

            findings = evaluate_source(POLICY, root)
            rule_ids = {finding.rule_id for finding in findings}

            self.assertEqual({"required-file", "inline-secret", "forbidden-file"}, rule_ids)

    def test_terraform_data_demo_resource_uses_represented_type(self):
        plan = {
            "resource_changes": [
                {
                    "address": "terraform_data.storage",
                    "type": "terraform_data",
                    "change": {
                        "actions": ["create"],
                        "after": {
                            "input": {
                                "resource_type": "azurerm_storage_account",
                                "location": "westus",
                                "public_network_access_enabled": True,
                                "allow_nested_items_to_be_public": True,
                                "tags": {"environment": "prod"},
                            }
                        },
                        "after_unknown": {},
                    },
                }
            ]
        }

        findings = evaluate_plan(POLICY, plan)
        rule_ids = {finding.rule_id for finding in findings}

        self.assertIn("allowed-region", rule_ids)
        self.assertIn("required-tags", rule_ids)
        self.assertIn("storage-private-network", rule_ids)
        self.assertIn("storage-private-containers", rule_ids)


if __name__ == "__main__":
    unittest.main()
