provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "atlas"
      ManagedBy = "Terraform"
    }
  }
}

# Reads the API token from the CLOUDFLARE_API_TOKEN environment variable.
provider "cloudflare" {}
