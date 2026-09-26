data "cloudflare_zone" "main" {
  filter = {
    name = var.domain
  }
}

data "cloudflare_ip_ranges" "main" {}

resource "cloudflare_dns_record" "apex" {
  zone_id = data.cloudflare_zone.main.zone_id
  name    = var.domain
  type    = "A"
  content = aws_eip.atlas.public_ip
  proxied = true
  ttl     = 1 # automatic; required for proxied records
}

# Caddy answers www with a redirect to the apex.
resource "cloudflare_dns_record" "www" {
  zone_id = data.cloudflare_zone.main.zone_id
  name    = local.www_domain
  type    = "CNAME"
  content = var.domain
  proxied = true
  ttl     = 1
}

resource "cloudflare_zone_setting" "main" {
  for_each = local.zone_settings

  zone_id    = data.cloudflare_zone.main.zone_id
  setting_id = each.key
  value      = each.value
}

# The certificate Caddy shows to Cloudflare. Only Cloudflare trusts it, which is all Full (strict) needs.
# The private key is generated here and so lives in the Terraform state: keep the state local and private.
resource "tls_private_key" "origin" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "origin" {
  private_key_pem = tls_private_key.origin.private_key_pem
  dns_names       = [var.domain, "*.${var.domain}"]

  subject {
    common_name = var.domain
  }
}

resource "cloudflare_origin_ca_certificate" "origin" {
  csr                = tls_cert_request.origin.cert_request_pem
  hostnames          = [var.domain, "*.${var.domain}"]
  request_type       = "origin-rsa"
  requested_validity = 5475 # 15 years
}
