# Atlas infrastructure

Terraform for Atlas at https://atlasofseeds.com: one t4g.small in Singapore (ap-southeast-1) running Atlas behind Caddy, reachable only through Cloudflare. [DEPLOY.md](../DEPLOY.md) is the full guide: credentials (Step 2), applying (Step 3), deploying the app (Step 4) and troubleshooting.

| File | What it creates |
| --- | --- |
| `main.tf` | EC2 instance (Ubuntu 24.04 arm64), key pair, Elastic IP, security group, monthly budget |
| `cloudflare.tf` | DNS for the apex and `www`, zone SSL settings, the origin certificate |
| `cloud-init.yaml.tftpl` | First-boot setup: swap, Caddy with the origin certificate, uv, the `atlas` systemd unit |

## Quick reference

```bash
# Token with Zone Read, DNS Edit, Zone Settings Edit, SSL and Certificates Edit, saved as in DEPLOY.md Step 2
export CLOUDFLARE_API_TOKEN=$(tr -d '[:space:]' < ~/.config/atlas/cloudflare-token)
terraform init
terraform plan
terraform apply

terraform apply -replace=aws_instance.atlas   # rebuild the server (cloud-init and AMI changes only apply this way)
terraform destroy                             # everything except the domain and the zone
```

`terraform.tfvars` (git-ignored) sets `budget_alert_email` and `ssh_ingress_cidr`; see `terraform.tfvars.example` for the optional overrides.

The state holds the origin certificate's private key: keep it local, and never commit or copy it anywhere.
