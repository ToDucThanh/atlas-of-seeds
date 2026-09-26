data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
}

resource "aws_key_pair" "atlas" {
  key_name   = "atlas"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_security_group" "atlas" {
  name        = "atlas-sg"
  description = "Atlas: SSH from the operator, HTTPS from Cloudflare only"
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.atlas.id
  description       = "SSH from the operator"
  cidr_ipv4         = var.ssh_ingress_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

# One rule per Cloudflare range, so nobody can reach Caddy without going through Cloudflare.
# Port 80 stays closed: Cloudflare upgrades visitors to HTTPS at its edge.
resource "aws_vpc_security_group_ingress_rule" "https_cloudflare" {
  for_each = toset(data.cloudflare_ip_ranges.main.ipv4_cidrs)

  security_group_id = aws_security_group.atlas.id
  description       = "HTTPS from Cloudflare"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Terraform removes the default allow-all egress rule, so add it back (apt, uv, PyPI).
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.atlas.id
  description       = "All outbound traffic"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_instance" "atlas" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.atlas.key_name
  vpc_security_group_ids = [aws_security_group.atlas.id]

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    app_port        = local.app_port
    domain          = var.domain
    origin_cert_pem = cloudflare_origin_ca_certificate.origin.certificate
    origin_key_pem  = tls_private_key.origin.private_key_pem
    www_domain      = local.www_domain
  })

  credit_specification {
    cpu_credits = var.cpu_credits
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    encrypted   = true
    volume_size = var.root_volume_size
    volume_type = "gp3"
  }

  tags = {
    Name = "atlas"
  }

  lifecycle {
    # Cloud-init only runs on first boot, and a newer Ubuntu AMI shouldn't replace a running server.
    # Rebuild on purpose with: terraform apply -replace=aws_instance.atlas
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "atlas" {
  domain   = "vpc"
  instance = aws_instance.atlas.id

  tags = {
    Name = "atlas"
  }
}

# Account-wide, so it also catches costs from anything outside this configuration.
resource "aws_budgets_budget" "monthly" {
  name         = "atlas-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
