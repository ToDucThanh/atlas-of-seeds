locals {
  www_domain = "www.${var.domain}"

  # Flet listens here; only Caddy on the same machine talks to it.
  app_port = 8000

  zone_settings = {
    always_use_https = "on"
    min_tls_version  = "1.2"
    ssl              = "strict" # never "flexible": Caddy would redirect Cloudflare's plain HTTP forever
    websockets       = "on"     # Atlas's page talks to the server over a WebSocket at /ws
  }
}
