variable "aws_region" {
  description = "AWS region for the server; pick the one closest to your visitors, since every globe drag is a round trip"
  type        = string
  default     = "ap-southeast-1"
}

variable "budget_alert_email" {
  description = "Email address that receives the monthly budget alerts"
  type        = string
}

variable "budget_limit_usd" {
  description = "Monthly AWS cost budget in USD; alerts at 80% actual and 100% forecast"
  type        = number
  default     = 30
}

variable "cpu_credits" {
  description = "Burstable CPU credit mode: unlimited stays fast and bills surplus CPU, standard caps cost but throttles"
  type        = string
  default     = "unlimited"

  validation {
    condition     = contains(["standard", "unlimited"], var.cpu_credits)
    error_message = "cpu_credits must be standard or unlimited."
  }
}

variable "domain" {
  description = "Apex domain of the Cloudflare zone; Atlas is served here and www redirects to it"
  type        = string
  default     = "atlasofseeds.com"
}

variable "instance_type" {
  description = "EC2 instance type; must be ARM (Graviton), since the AMI is arm64"
  type        = string
  default     = "t4g.small"
}

variable "root_volume_size" {
  description = "Size of the root gp3 volume in GiB"
  type        = number
  default     = 20
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to reach SSH, normally your own IP as a /32"
  type        = string

  validation {
    condition     = can(cidrhost(var.ssh_ingress_cidr, 0))
    error_message = "ssh_ingress_cidr must be a valid CIDR, for example 203.0.113.7/32."
  }
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key installed for the ubuntu user"
  type        = string
  default     = "~/.ssh/atlas.pub"
}
