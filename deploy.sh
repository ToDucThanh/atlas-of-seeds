#!/usr/bin/env bash
# Ship the app to the Atlas server: copy the code, install locked dependencies, restart. See DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")"
HOST=ubuntu@$(terraform -chdir=infra output -raw public_ip)
KEY=~/.ssh/atlas

rsync -az --delete \
  --exclude .venv --exclude .git --exclude __pycache__ \
  --exclude .ruff_cache --exclude .flet --exclude infra \
  -e "ssh -i $KEY" ./ "$HOST:~/atlas/"

ssh -i "$KEY" "$HOST" 'cd ~/atlas && ~/.local/bin/uv sync --locked --no-dev && sudo systemctl restart atlas && systemctl is-active atlas'
