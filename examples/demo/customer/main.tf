terraform {
  required_version = ">= 1.9.0, < 2.0.0"
}

# Credential-free demo harness. Each terraform_data resource represents the
# planned attributes of the Azure resource named by resource_type.
resource "terraform_data" "storage_account" {
  input = {
    resource_type                    = "azurerm_storage_account"
    name                             = "fabrikamcustomerdata"
    location                         = "westus"
    public_network_access_enabled    = true
    allow_nested_items_to_be_public  = true
    approved_private_endpoint_module = "none"
    tags = {
      cost_center         = "ENERGY-042"
      data_classification = "confidential"
      environment         = "prod"
      owner               = "grid-platform"
    }
  }
}

resource "terraform_data" "key_vault" {
  input = {
    resource_type            = "azurerm_key_vault"
    name                     = "fabrikam-customer-kv"
    location                 = "centralus"
    purge_protection_enabled = false
    tags = {
      cost_center         = "ENERGY-042"
      data_classification = "confidential"
      environment         = "prod"
      owner               = "grid-platform"
    }
  }
}
