output "instance_id" {
  description = "ID of the Atlas EC2 instance"
  value       = aws_instance.atlas.id
}

output "origin_certificate_expires_on" {
  description = "When the Cloudflare origin certificate expires; Cloudflare sends no reminder"
  value       = cloudflare_origin_ca_certificate.origin.expires_on
}

output "public_ip" {
  description = "Elastic IP of the server (reachable only on SSH from ssh_ingress_cidr)"
  value       = aws_eip.atlas.public_ip
}

output "site_url" {
  description = "Public URL of Atlas"
  value       = "https://${var.domain}"
}

output "ssh_command" {
  description = "Command to SSH into the server"
  value       = "ssh -i ${trimsuffix(var.ssh_public_key_path, ".pub")} ubuntu@${aws_eip.atlas.public_ip}"
}
