#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$repo_root/scripts/deploy-sub2api-local.sh"

output="$($script --dry-run --tag test-tag)"

require_line() {
  local needle="$1"
  if ! grep -Fq -- "$needle" <<<"$output"; then
    printf 'missing expected output:\n%s\n\nactual output:\n%s\n' "$needle" "$output" >&2
    exit 1
  fi
}

require_line 'pnpm --dir frontend install --frozen-lockfile'
require_line 'pnpm --dir frontend run build'
require_line 'test -f backend/internal/web/dist/index.html'
require_line 'env CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -tags embed'
require_line 'tar -czf '
require_line 'sub2api resources docker-entrypoint.sh'
require_line 'scp -P 443 '
require_line 'root@107.174.48.241:/tmp/sub2api-local-test-tag-runtime.tgz'
require_line 'docker build -f Dockerfile.runtime -t sub2api-local:test-tag .'
require_line 'image: sub2api-local:test-tag'
require_line 'docker compose up -d sub2api'
require_line 'docker compose ps sub2api'
require_line 'curl -fsS https://hdgl.us.ci/health'

if grep -Fq 'Dockerfile.server-go-build' <<<"$output"; then
  printf 'unexpected remote Go build Dockerfile in output:\n%s\n' "$output" >&2
  exit 1
fi

if grep -Fq 'go build -tags embed' <<<"$(sed -n '/ssh -p 443/,$p' <<<"$output")"; then
  printf 'unexpected remote go build in output:\n%s\n' "$output" >&2
  exit 1
fi

printf 'PASS deploy-sub2api-local dry-run contract\n'
