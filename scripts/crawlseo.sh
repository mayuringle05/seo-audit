#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/crawlseo"
action="${1:-status}"
if [[ "$action" == init ]]; then
  python3 - <<'PY'
from pathlib import Path
import secrets
p = Path('.env')
if p.exists():
    raise SystemExit('.env already exists; preserved without modification')
with p.open('x') as f:
    f.write('CRAWLSEO_IMAGE=ghcr.io/crawlseo/crawlseo:latest\n')
    f.write('POSTGRES_PASSWORD=' + secrets.token_hex(24) + '\n')
    f.write('APP_SECRET=' + secrets.token_hex(32) + '\n')
    f.write('NEXTAUTH_SECRET=' + secrets.token_hex(32) + '\n')
    f.write('GOOGLE_CLIENT_ID=REPLACE_ME\nGOOGLE_CLIENT_SECRET=REPLACE_ME\n')
p.chmod(0o600)
print('Created credentials. Add Google OAuth values in crawlseo/.env. No services started.')
PY
  exit 0
fi
command -v docker >/dev/null || { echo 'Docker Desktop or Docker Compose is required.' >&2; exit 2; }
case "$action" in
  start)
    python3 - <<'PY'
from pathlib import Path
p = Path('.env')
if not p.exists() or 'REPLACE_ME' in p.read_text():
    raise SystemExit('Run init and fill Google OAuth credentials in crawlseo/.env first.')
PY
    # Fetch once, then lock app image digest; start does not silently upgrade it again.
    python3 - <<'PY'
from pathlib import Path
import subprocess
p = Path('.env')
text = p.read_text()
line = next(x for x in text.splitlines() if x.startswith('CRAWLSEO_IMAGE='))
image = line.split('=', 1)[1]
if '@sha256:' not in image:
    subprocess.run(['docker', 'pull', image], check=True)
    digest = subprocess.check_output(['docker', 'image', 'inspect', '--format={{index .RepoDigests 0}}', image], text=True).strip()
    if not digest.startswith('ghcr.io/crawlseo/crawlseo@sha256:'):
        raise SystemExit('Could not resolve official image digest')
    p.write_text(text.replace(line, 'CRAWLSEO_IMAGE=' + digest))
PY
    docker compose -f compose.yaml up -d --wait
    echo 'Dashboard: http://localhost:3000 — built-in crawl trigger disabled.'
    ;;
  stop) docker compose -f compose.yaml stop ;;
  status) docker compose -f compose.yaml ps ;;
  logs) docker compose -f compose.yaml logs --tail=100 ;;
  *) echo 'Usage: scripts/crawlseo.sh init|start|stop|status|logs' >&2; exit 2 ;;
esac
