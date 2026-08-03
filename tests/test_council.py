import unittest
from pathlib import Path

from src.repoman_council import (
    CouncilError,
    assert_patch_scope,
    assert_safe_demo_hcl,
)


ROOT = Path(__file__).resolve().parents[1]
SAFE_HCL = (ROOT / "examples/demo/customer/main.tf").read_text(encoding="utf-8")


class CouncilTests(unittest.TestCase):
    def test_safe_demo_hcl_is_accepted(self):
        assert_safe_demo_hcl(SAFE_HCL)

    def test_module_is_rejected(self):
        with self.assertRaises(CouncilError):
            assert_safe_demo_hcl(
                SAFE_HCL + '\nmodule "unsafe" { source = "example/module" }\n'
            )

    def test_extra_resource_is_rejected(self):
        with self.assertRaises(CouncilError):
            assert_safe_demo_hcl(
                SAFE_HCL
                + '\nresource "terraform_data" "extra" { input = { value = true } }\n'
            )

    def test_compact_external_data_source_is_rejected(self):
        with self.assertRaises(CouncilError):
            assert_safe_demo_hcl(
                SAFE_HCL + '\ndata"external""x"{program=["/bin/sh","-c","true"]}\n'
            )

    def test_change_outside_finding_scope_is_rejected(self):
        with self.assertRaises(CouncilError):
            assert_patch_scope(
                SAFE_HCL,
                SAFE_HCL.replace("fabrikamcustomerdata", "unexpectedrename"),
                [
                    {
                        "rule_id": "storage-private-network",
                        "subject": "terraform_data.storage_account (azurerm_storage_account)",
                    }
                ],
            )

    def test_interpolation_in_tag_key_is_rejected(self):
        with self.assertRaises(CouncilError):
            assert_safe_demo_hcl(
                SAFE_HCL.replace(
                    "cost_center         =",
                    '"leak-${file(\\"/etc/passwd\\")}" = "x"\n      cost_center =',
                )
            )


if __name__ == "__main__":
    unittest.main()
