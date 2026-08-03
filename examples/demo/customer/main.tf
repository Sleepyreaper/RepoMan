terraform {
  required_version = ">= 1.9.0, < 2.0.0"
}

# Credential-free demo harness. Each terraform_data resource represents the
# planned attributes of the Azure resource named by resource_type.
resource "terraform_data" "storage_account" {
  input = {
    resource_type                    = "azurerm_storage_account"
    name                             = "fabrikamcustomerdata"
    location                         = "eastus2"
    public_network_access_enabled    = false
    allow_nested_items_to_be_public  = false
    approved_private_endpoint_module = "Fabrikam/private-storage/azurerm"
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
    purge_protection_enabled = true
    tags = {
      cost_center         = "ENERGY-042"
      data_classification = "confidential"
      environment         = "prod"
      owner               = "grid-platform"
    }
  }
}
