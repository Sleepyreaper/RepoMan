terraform {
  required_version = ">= 1.9.0, < 2.0.0"
}

variable "environment" {
  description = "Deployment environment."
  type        = string

  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "Environment must be dev, test, or prod."
  }
}

resource "terraform_data" "example" {
  input = {
    environment = var.environment
    managed_by  = "terraform"
  }
}

