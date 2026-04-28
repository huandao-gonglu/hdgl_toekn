#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

remote_host="${REMOTE_HOST:-107.174.48.241}"
remote_port="${REMOTE_PORT:-443}"
remote_user="${REMOTE_USER:-root}"
remote_dir="${REMOTE_DIR:-/opt/sub2api}"
remote_tmp_dir="${REMOTE_TMP_DIR:-/tmp}"
image_repo="${IMAGE_REPO:-sub2api-local}"
health_url="${HEALTH_URL:-https://hdgl.us.ci/health}"
image_tag="${IMAGE_TAG:-}"
target_goos="${TARGET_GOOS:-linux}"
target_goarch="${TARGET_GOARCH:-amd64}"
commit_value="${COMMIT:-}"
version_value="${VERSION:-}"
date_value="${DATE:-}"

dry_run=0
skip_frontend_build=0
skip_local_build=0
skip_remote_image_build=0
health_check=1
keep_archive=0
build_args=()
pnpm_cmd=(pnpm)

usage() {
  cat <<'EOF'
Usage: scripts/deploy-sub2api-local.sh [options]

Build frontend locally, cross-compile the Linux Go binary locally, upload runtime
artifacts to the server, build a lightweight runtime image on the server, update
docker-compose.override.yml, and restart only the sub2api service.

Options:
  --dry-run                 Print commands without executing them
  --skip-frontend-build     Reuse existing backend/internal/web/dist
  --skip-local-build        Reuse existing local runtime payload
  --skip-build              Skip remote Docker image build and only update/restart
  --keep-tar                Keep the local runtime archive after deployment
  --no-health-check         Skip final public health check
  --tag TAG                 Docker image tag (default: local-<git-sha>[-dirty]-<timestamp>)
  --image-repo NAME         Docker image repository (default: sub2api-local)
  --goos GOOS               Local Go target OS (default: linux)
  --goarch GOARCH           Local Go target arch (default: amd64)
  --commit COMMIT           Build metadata commit value
  --version VERSION         Build metadata version value
  --date DATE               Build metadata date value
  --remote-host HOST        SSH host (default: 107.174.48.241)
  --remote-port PORT        SSH port (default: 443)
  --remote-user USER        SSH user (default: root)
  --remote-dir DIR          Remote compose directory (default: /opt/sub2api)
  --remote-tmp-dir DIR      Remote temporary build directory parent (default: /tmp)
  --health-url URL          Health URL (default: https://hdgl.us.ci/health)
  --build-arg KEY=VALUE     Extra remote docker build argument; repeatable.
                            COMMIT/VERSION/DATE are consumed by local Go build.
  -h, --help                Show this help

Environment variables with matching uppercase names can also set defaults:
REMOTE_HOST, REMOTE_PORT, REMOTE_USER, REMOTE_DIR, REMOTE_TMP_DIR, IMAGE_REPO,
HEALTH_URL, IMAGE_TAG, TARGET_GOOS, TARGET_GOARCH, COMMIT, VERSION, DATE.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

print_cmd() {
  printf '+'
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
  printf '\n'
}

run_cmd() {
  if ((dry_run)); then
    print_cmd "$@"
  else
    print_cmd "$@"
    "$@"
  fi
}

shell_quote() {
  printf '%q' "$1"
}

while (($#)); do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --skip-frontend-build)
      skip_frontend_build=1
      shift
      ;;
    --skip-local-build)
      skip_local_build=1
      shift
      ;;
    --skip-build)
      skip_remote_image_build=1
      shift
      ;;
    --keep-tar)
      keep_archive=1
      shift
      ;;
    --no-health-check)
      health_check=0
      shift
      ;;
    --tag)
      [[ $# -ge 2 ]] || die "--tag requires a value"
      image_tag="$2"
      shift 2
      ;;
    --image-repo)
      [[ $# -ge 2 ]] || die "--image-repo requires a value"
      image_repo="$2"
      shift 2
      ;;
    --goos)
      [[ $# -ge 2 ]] || die "--goos requires a value"
      target_goos="$2"
      shift 2
      ;;
    --goarch)
      [[ $# -ge 2 ]] || die "--goarch requires a value"
      target_goarch="$2"
      shift 2
      ;;
    --commit)
      [[ $# -ge 2 ]] || die "--commit requires a value"
      commit_value="$2"
      shift 2
      ;;
    --version)
      [[ $# -ge 2 ]] || die "--version requires a value"
      version_value="$2"
      shift 2
      ;;
    --date)
      [[ $# -ge 2 ]] || die "--date requires a value"
      date_value="$2"
      shift 2
      ;;
    --remote-host)
      [[ $# -ge 2 ]] || die "--remote-host requires a value"
      remote_host="$2"
      shift 2
      ;;
    --remote-port)
      [[ $# -ge 2 ]] || die "--remote-port requires a value"
      remote_port="$2"
      shift 2
      ;;
    --remote-user)
      [[ $# -ge 2 ]] || die "--remote-user requires a value"
      remote_user="$2"
      shift 2
      ;;
    --remote-dir)
      [[ $# -ge 2 ]] || die "--remote-dir requires a value"
      remote_dir="$2"
      shift 2
      ;;
    --remote-tmp-dir)
      [[ $# -ge 2 ]] || die "--remote-tmp-dir requires a value"
      remote_tmp_dir="$2"
      shift 2
      ;;
    --health-url)
      [[ $# -ge 2 ]] || die "--health-url requires a value"
      health_url="$2"
      shift 2
      ;;
    --build-arg)
      [[ $# -ge 2 ]] || die "--build-arg requires KEY=VALUE"
      case "$2" in
        COMMIT=*) commit_value="${2#COMMIT=}" ;;
        VERSION=*) version_value="${2#VERSION=}" ;;
        DATE=*) date_value="${2#DATE=}" ;;
        *) build_args+=(--build-arg "$2") ;;
      esac
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

if [[ -z "$image_tag" ]]; then
  git_sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || printf 'nogit')"
  dirty_suffix=""
  if ! git -C "$repo_root" diff --quiet --ignore-submodules -- 2>/dev/null || \
     ! git -C "$repo_root" diff --cached --quiet --ignore-submodules -- 2>/dev/null; then
    dirty_suffix="-dirty"
  fi
  image_tag="local-${git_sha}${dirty_suffix}-$(date +%Y%m%d%H%M%S)"
fi

if [[ -z "$commit_value" ]]; then
  commit_value="$image_tag"
fi

if [[ -z "$version_value" ]]; then
  version_value="$(tr -d '\r\n' < "$repo_root/backend/cmd/server/VERSION")"
fi

if [[ -z "$date_value" ]]; then
  date_value="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

image="${image_repo}:${image_tag}"
safe_name="$(printf '%s-%s' "$image_repo" "$image_tag" | tr '/:' '--' | tr -c 'A-Za-z0-9_.-' '-')"
payload_dir="${TMPDIR:-/tmp}/${safe_name}-payload"
local_archive="${TMPDIR:-/tmp}/${safe_name}-runtime.tgz"
remote_archive="${remote_tmp_dir%/}/${safe_name}-runtime.tgz"
remote_build_dir="${remote_tmp_dir%/}/${safe_name}-build"
remote_target="${remote_user}@${remote_host}"

ldflags="-s -w -X main.Version=${version_value} -X main.Commit=${commit_value} -X main.Date=${date_value} -X main.BuildType=release"

remote_docker_build_cmd="docker build -f Dockerfile.runtime -t $(shell_quote "$image")"
if ((${#build_args[@]})); then
  for arg in "${build_args[@]}"; do
    remote_docker_build_cmd+=" $(shell_quote "$arg")"
  done
fi
remote_docker_build_cmd+=" ."

remote_script="$(cat <<EOF
set -euo pipefail
rm -rf $(shell_quote "$remote_build_dir")
mkdir -p $(shell_quote "$remote_build_dir")
tar -xzf $(shell_quote "$remote_archive") -C $(shell_quote "$remote_build_dir")
cd $(shell_quote "$remote_build_dir")
test -x sub2api
test -d resources
test -f docker-entrypoint.sh
cat > Dockerfile.runtime <<'DOCKERFILE'
ARG ALPINE_IMAGE=alpine:3.21
ARG POSTGRES_IMAGE=postgres:18-alpine

FROM \${POSTGRES_IMAGE} AS pg-client

FROM \${ALPINE_IMAGE}
LABEL maintainer="Wei-Shaw <github.com/Wei-Shaw>"
LABEL description="Sub2API - AI API Gateway Platform"
LABEL org.opencontainers.image.source="https://github.com/Wei-Shaw/sub2api"
RUN apk add --no-cache \
    ca-certificates \
    tzdata \
    su-exec \
    libpq \
    zstd-libs \
    lz4-libs \
    krb5-libs \
    libldap \
    libedit \
    && rm -rf /var/cache/apk/*
COPY --from=pg-client /usr/local/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pg-client /usr/local/bin/psql /usr/local/bin/psql
COPY --from=pg-client /usr/local/lib/libpq.so.5* /usr/local/lib/
RUN addgroup -g 1000 sub2api && \
    adduser -u 1000 -G sub2api -s /bin/sh -D sub2api
WORKDIR /app
COPY --chown=sub2api:sub2api sub2api /app/sub2api
COPY --chown=sub2api:sub2api resources /app/resources
RUN mkdir -p /app/data && chown sub2api:sub2api /app/data
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/sub2api"]
DOCKERFILE
if [ $(shell_quote "$skip_remote_image_build") -eq 0 ]; then
  $remote_docker_build_cmd
fi
cd $(shell_quote "$remote_dir")
cat > docker-compose.override.yml <<'YAML'
services:
  sub2api:
    image: $image
YAML
if docker compose version >/dev/null 2>&1; then
  docker compose up -d sub2api
  docker compose ps sub2api
else
  docker-compose up -d sub2api
  docker-compose ps sub2api
fi
rm -f $(shell_quote "$remote_archive")
rm -rf $(shell_quote "$remote_build_dir")
EOF
)"

cleanup() {
  if ((!dry_run && !keep_archive)); then
    rm -f "$local_archive"
    rm -rf "$payload_dir"
  fi
}
trap cleanup EXIT

if ((!dry_run)); then
  require_cmd go
  require_cmd scp
  require_cmd ssh
  require_cmd tar
  if ((!skip_frontend_build)); then
    if ! command -v pnpm >/dev/null 2>&1; then
      require_cmd corepack
      export COREPACK_ENABLE_DOWNLOAD_PROMPT="${COREPACK_ENABLE_DOWNLOAD_PROMPT:-0}"
      pnpm_cmd=(corepack pnpm)
    fi
  fi
  if ((health_check)); then
    require_cmd curl
  fi
fi

cd "$repo_root"

printf 'Deploy image: %s\n' "$image"
printf 'Remote host:  %s:%s\n' "$remote_host" "$remote_port"
printf 'Remote dir:   %s\n' "$remote_dir"
printf 'Target:       %s/%s\n' "$target_goos" "$target_goarch"

if ((!skip_local_build)); then
  run_cmd rm -rf "$payload_dir"
  run_cmd mkdir -p "$payload_dir"

  if ((!skip_frontend_build)); then
    run_cmd "${pnpm_cmd[@]}" --dir frontend install --frozen-lockfile
    run_cmd "${pnpm_cmd[@]}" --dir frontend run build
  fi

  run_cmd test -f backend/internal/web/dist/index.html
  (
    cd "$repo_root/backend"
    run_cmd env CGO_ENABLED=0 GOOS="$target_goos" GOARCH="$target_goarch" \
      go build \
      -tags embed \
      -ldflags "$ldflags" \
      -trimpath \
      -o "$payload_dir/sub2api" \
      ./cmd/server
  )
  run_cmd chmod +x "$payload_dir/sub2api"
  run_cmd cp -R backend/resources "$payload_dir/resources"
  run_cmd cp deploy/docker-entrypoint.sh "$payload_dir/docker-entrypoint.sh"
  if command -v xattr >/dev/null 2>&1; then
    run_cmd xattr -cr "$payload_dir"
  fi
fi

run_cmd env COPYFILE_DISABLE=1 tar -czf "$local_archive" -C "$payload_dir" sub2api resources docker-entrypoint.sh
run_cmd scp -P "$remote_port" "$local_archive" "${remote_target}:${remote_archive}"

if ((dry_run)); then
  print_cmd ssh -p "$remote_port" "$remote_target" bash -s
  printf '%s\n' "$remote_script" | sed 's/^/  /'
else
  print_cmd ssh -p "$remote_port" "$remote_target" bash -s
  ssh -p "$remote_port" "$remote_target" bash -s <<<"$remote_script"
fi

if ((health_check)); then
  run_cmd curl -fsS "$health_url"
  printf '\n'
fi
