terraform {
  required_version = ">= 1.8.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  # Deliberately fake credentials for the local ChangeGuard demo.
  access_key = "changeguard-demo"
  secret_key = "changeguard-demo"

  # Prevent AWS identity/metadata validation. Terraform still uses the real
  # provider schema and produces a real plan; no AWS account is contacted.
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
}
