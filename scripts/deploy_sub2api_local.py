#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import shlex
import stat
import string
import subprocess
import sys
import tarfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def env_default(name: str, default: str) -> str:
    return os.environ.get(name, default)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def quote(value: object) -> str:
    return shlex.quote(str(value))


def format_command(args: list[object], env: dict[str, str] | None = None) -> str:
    parts: list[object] = []
    if env:
        parts.append("env")
        parts.extend(f"{key}={value}" for key, value in env.items())
    parts.extend(args)
    return "+ " + " ".join(quote(part) for part in parts)


def print_cmd(args: list[object], env: dict[str, str] | None = None) -> None:
    print(format_command(args, env))


class Runner:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run

    def run(
        self,
        args: list[object],
        *,
        cwd: Path | None = None,
        env_overlay: dict[str, str] | None = None,
        print_env: dict[str, str] | None = None,
    ) -> None:
        print_cmd(args, print_env)
        if self.dry_run:
            return

        env = os.environ.copy()
        if env_overlay:
            env.update(env_overlay)
        subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)

    def show(self, args: list[object], *, print_env: dict[str, str] | None = None) -> None:
        print_cmd(args, print_env)


def run_capture(args: list[str], *, cwd: Path = REPO_ROOT) -> str | None:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_is_dirty() -> bool:
    worktree = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet", "--ignore-submodules", "--"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    staged = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet", "--ignore-submodules", "--"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return worktree.returncode != 0 or staged.returncode != 0


def require_cmd(name: str) -> None:
    if shutil.which(name) is None:
        die(f"missing required command: {name}")


def find_cmd(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        die(f"missing required command: {name}")
    return path


def resolve_pnpm_cmd(*, dry_run: bool) -> list[str]:
    if dry_run:
        return ["pnpm"]

    pnpm = shutil.which("pnpm")
    if pnpm is not None:
        return [pnpm]

    os.environ.setdefault("COREPACK_ENABLE_DOWNLOAD_PROMPT", "0")
    return [find_cmd("corepack"), "pnpm"]


def safe_artifact_name(image_repo: str, image_tag: str) -> str:
    raw = f"{image_repo}-{image_tag}".replace("/", "-").replace(":", "-")
    allowed = set(string.ascii_letters + string.digits + "_.-")
    return "".join(ch if ch in allowed else "-" for ch in raw)


def remove_tree(path: Path, runner: Runner) -> None:
    runner.show(["rm", "-rf", path])
    if not runner.dry_run:
        shutil.rmtree(path, ignore_errors=True)


def make_dir(path: Path, runner: Runner) -> None:
    runner.show(["mkdir", "-p", path])
    if not runner.dry_run:
        path.mkdir(parents=True, exist_ok=True)


def require_file(path: Path, display_path: str, runner: Runner) -> None:
    runner.show(["test", "-f", display_path])
    if not runner.dry_run and not path.is_file():
        die(f"missing required file: {display_path}")


def copy_tree(src: Path, dst: Path, display_src: str, display_dst: Path, runner: Runner) -> None:
    runner.show(["cp", "-R", display_src, display_dst])
    if not runner.dry_run:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def copy_file(src: Path, dst: Path, display_src: str, display_dst: Path, runner: Runner) -> None:
    runner.show(["cp", display_src, display_dst])
    if not runner.dry_run:
        shutil.copy2(src, dst)


def chmod_executable(path: Path, runner: Runner) -> None:
    runner.show(["chmod", "+x", path])
    if not runner.dry_run:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def add_tar_path(tar: tarfile.TarFile, source: Path, arcname: str) -> None:
    for path in sorted(source.rglob("*")) if source.is_dir() else [source]:
        name = f"{arcname}/{path.relative_to(source).as_posix()}" if source.is_dir() else arcname
        info = tar.gettarinfo(str(path), arcname=name)
        if path.is_file() and path.name in {"sub2api", "docker-entrypoint.sh"}:
            info.mode |= 0o755
        if path.is_file():
            with path.open("rb") as handle:
                tar.addfile(info, handle)
        else:
            tar.addfile(info)


def create_archive(archive: Path, payload_dir: Path, runner: Runner) -> None:
    runner.show(
        [
            "tar",
            "-czf",
            archive,
            "-C",
            payload_dir,
            "sub2api",
            "resources",
            "docker-entrypoint.sh",
        ],
        print_env={"COPYFILE_DISABLE": "1"},
    )
    if runner.dry_run:
        return

    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        add_tar_path(tar, payload_dir / "sub2api", "sub2api")
        add_tar_path(tar, payload_dir / "resources", "resources")
        add_tar_path(tar, payload_dir / "docker-entrypoint.sh", "docker-entrypoint.sh")


def build_remote_script(
    *,
    remote_archive: str,
    remote_build_dir: str,
    remote_dir: str,
    image: str,
    skip_remote_image_build: bool,
    build_args: list[str],
) -> str:
    remote_docker_build_cmd = f"docker build -f Dockerfile.runtime -t {quote(image)}"
    for arg in build_args:
        remote_docker_build_cmd += f" --build-arg {quote(arg)}"
    remote_docker_build_cmd += " ."

    return f"""set -euo pipefail
rm -rf {quote(remote_build_dir)}
mkdir -p {quote(remote_build_dir)}
tar -xzf {quote(remote_archive)} -C {quote(remote_build_dir)}
cd {quote(remote_build_dir)}
test -x sub2api
test -d resources
test -f docker-entrypoint.sh
cat > Dockerfile.runtime <<'DOCKERFILE'
ARG ALPINE_IMAGE=alpine:3.21
ARG POSTGRES_IMAGE=postgres:18-alpine

FROM ${{POSTGRES_IMAGE}} AS pg-client

FROM ${{ALPINE_IMAGE}}
LABEL maintainer="Wei-Shaw <github.com/Wei-Shaw>"
LABEL description="Sub2API - AI API Gateway Platform"
LABEL org.opencontainers.image.source="https://github.com/Wei-Shaw/sub2api"
RUN apk add --no-cache \\
    ca-certificates \\
    tzdata \\
    su-exec \\
    libpq \\
    zstd-libs \\
    lz4-libs \\
    krb5-libs \\
    libldap \\
    libedit \\
    && rm -rf /var/cache/apk/*
COPY --from=pg-client /usr/local/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pg-client /usr/local/bin/psql /usr/local/bin/psql
COPY --from=pg-client /usr/local/lib/libpq.so.5* /usr/local/lib/
RUN addgroup -g 1000 sub2api && \\
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
if [ {quote(0 if not skip_remote_image_build else 1)} -eq 0 ]; then
  {remote_docker_build_cmd}
fi
cd {quote(remote_dir)}
cat > docker-compose.override.yml <<'YAML'
services:
  sub2api:
    image: {image}
YAML
if docker compose version >/dev/null 2>&1; then
  docker compose up -d sub2api
  docker compose ps sub2api
else
  docker-compose up -d sub2api
  docker-compose ps sub2api
fi
rm -f {quote(remote_archive)}
rm -rf {quote(remote_build_dir)}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Package and/or publish Sub2API runtime artifacts. The default deploy mode "
            "builds frontend, cross-compiles the Linux Go binary, uploads artifacts, "
            "builds the remote runtime image, and restarts sub2api."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them")
    parser.add_argument(
        "--mode",
        choices=("package", "publish", "deploy"),
        default=env_default("DEPLOY_MODE", "deploy"),
        help="Operation mode: package only, publish an existing archive, or package and publish",
    )
    parser.add_argument(
        "--archive",
        default=env_default("ARCHIVE", ""),
        help="Runtime archive path to create (package/deploy) or publish (publish)",
    )
    parser.add_argument("--skip-frontend-build", action="store_true", help="Reuse existing backend/internal/web/dist")
    parser.add_argument("--skip-local-build", action="store_true", help="Reuse existing local runtime payload")
    parser.add_argument("--skip-build", action="store_true", help="Skip remote Docker image build and only update/restart")
    parser.add_argument("--keep-tar", action="store_true", help="Keep the local runtime archive after deployment")
    parser.add_argument("--no-health-check", action="store_true", help="Skip final public health check")
    parser.add_argument("--tag", default=env_default("IMAGE_TAG", ""), help="Docker image tag")
    parser.add_argument("--image-repo", default=env_default("IMAGE_REPO", "sub2api-local"), help="Docker image repository")
    parser.add_argument("--goos", default=env_default("TARGET_GOOS", "linux"), help="Local Go target OS")
    parser.add_argument("--goarch", default=env_default("TARGET_GOARCH", "amd64"), help="Local Go target arch")
    parser.add_argument("--commit", default=env_default("COMMIT", ""), help="Build metadata commit value")
    parser.add_argument("--version", default=env_default("VERSION", ""), help="Build metadata version value")
    parser.add_argument("--date", default=env_default("DATE", ""), help="Build metadata date value")
    parser.add_argument("--remote-host", default=env_default("REMOTE_HOST", "107.174.48.241"), help="SSH host")
    parser.add_argument("--remote-port", default=env_default("REMOTE_PORT", "443"), help="SSH port")
    parser.add_argument("--remote-user", default=env_default("REMOTE_USER", "root"), help="SSH user")
    parser.add_argument("--remote-dir", default=env_default("REMOTE_DIR", "/opt/sub2api"), help="Remote compose directory")
    parser.add_argument(
        "--remote-tmp-dir",
        default=env_default("REMOTE_TMP_DIR", "/tmp"),
        help="Remote temporary build directory parent",
    )
    parser.add_argument("--health-url", default=env_default("HEALTH_URL", "https://hdgl.us.ci/health"), help="Health URL")
    parser.add_argument("--build-arg", action="append", default=[], metavar="KEY=VALUE", help="Extra remote docker build arg")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runner = Runner(args.dry_run)

    image_tag = args.tag
    if not image_tag:
        git_sha = run_capture(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"]) or "nogit"
        dirty_suffix = "-dirty" if git_is_dirty() else ""
        timestamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        image_tag = f"local-{git_sha}{dirty_suffix}-{timestamp}"

    commit_value = args.commit or image_tag
    version_value = args.version
    if not version_value:
        version_value = (REPO_ROOT / "backend" / "cmd" / "server" / "VERSION").read_text(encoding="utf-8").strip()
    date_value = args.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    build_args: list[str] = []
    for build_arg in args.build_arg:
        if build_arg.startswith("COMMIT="):
            commit_value = build_arg.removeprefix("COMMIT=")
        elif build_arg.startswith("VERSION="):
            version_value = build_arg.removeprefix("VERSION=")
        elif build_arg.startswith("DATE="):
            date_value = build_arg.removeprefix("DATE=")
        else:
            build_args.append(build_arg)

    image = f"{args.image_repo}:{image_tag}"
    safe_name = safe_artifact_name(args.image_repo, image_tag)
    temp_parent = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    payload_dir = temp_parent / f"{safe_name}-payload"
    local_archive = Path(args.archive).expanduser() if args.archive else temp_parent / f"{safe_name}-runtime.tgz"
    if not local_archive.is_absolute():
        local_archive = (REPO_ROOT / local_archive).resolve()
    remote_tmp_dir = args.remote_tmp_dir.rstrip("/")
    remote_archive = f"{remote_tmp_dir}/{local_archive.name}"
    remote_build_dir = f"{remote_tmp_dir}/{safe_name}-build"
    remote_target = f"{args.remote_user}@{args.remote_host}"

    if args.mode == "publish" and not args.archive:
        die("--mode publish requires --archive")

    ldflags = (
        f"-s -w -X main.Version={version_value} -X main.Commit={commit_value} "
        f"-X main.Date={date_value} -X main.BuildType=release"
    )
    remote_script = build_remote_script(
        remote_archive=remote_archive,
        remote_build_dir=remote_build_dir,
        remote_dir=args.remote_dir,
        image=image,
        skip_remote_image_build=args.skip_build,
        build_args=build_args,
    )

    if not args.dry_run:
        if args.mode in {"package", "deploy"}:
            require_cmd("go")
        if args.mode in {"publish", "deploy"}:
            require_cmd("scp")
            require_cmd("ssh")
        if args.mode in {"publish", "deploy"} and not args.no_health_check:
            require_cmd("curl")

    pnpm_cmd = (
        resolve_pnpm_cmd(dry_run=args.dry_run)
        if args.mode in {"package", "deploy"} and not args.skip_frontend_build
        else ["pnpm"]
    )

    print(f"Deploy image: {image}")
    print(f"Mode:         {args.mode}")
    print(f"Archive:      {local_archive}")
    print(f"Remote host:  {args.remote_host}:{args.remote_port}")
    print(f"Remote dir:   {args.remote_dir}")
    print(f"Target:       {args.goos}/{args.goarch}")

    try:
        if args.mode in {"package", "deploy"} and not args.skip_local_build:
            remove_tree(payload_dir, runner)
            make_dir(payload_dir, runner)

            if not args.skip_frontend_build:
                runner.run([*pnpm_cmd, "--dir", "frontend", "install", "--frozen-lockfile"], cwd=REPO_ROOT)
                runner.run([*pnpm_cmd, "--dir", "frontend", "run", "build"], cwd=REPO_ROOT)

            require_file(
                REPO_ROOT / "backend" / "internal" / "web" / "dist" / "index.html",
                "backend/internal/web/dist/index.html",
                runner,
            )
            runner.run(
                [
                    "go",
                    "build",
                    "-tags",
                    "embed",
                    "-ldflags",
                    ldflags,
                    "-trimpath",
                    "-o",
                    payload_dir / "sub2api",
                    "./cmd/server",
                ],
                cwd=REPO_ROOT / "backend",
                env_overlay={"CGO_ENABLED": "0", "GOOS": args.goos, "GOARCH": args.goarch},
                print_env={"CGO_ENABLED": "0", "GOOS": args.goos, "GOARCH": args.goarch},
            )
            chmod_executable(payload_dir / "sub2api", runner)
            copy_tree(
                REPO_ROOT / "backend" / "resources",
                payload_dir / "resources",
                "backend/resources",
                payload_dir / "resources",
                runner,
            )
            copy_file(
                REPO_ROOT / "deploy" / "docker-entrypoint.sh",
                payload_dir / "docker-entrypoint.sh",
                "deploy/docker-entrypoint.sh",
                payload_dir / "docker-entrypoint.sh",
                runner,
            )
            xattr = shutil.which("xattr")
            if xattr is not None:
                runner.run([xattr, "-cr", payload_dir])

        if args.mode in {"package", "deploy"}:
            create_archive(local_archive, payload_dir, runner)

        if args.mode == "package":
            print(f"Runtime archive ready: {local_archive}")
            return

        require_file(local_archive, str(local_archive), runner)
        runner.run(["scp", "-P", args.remote_port, local_archive, f"{remote_target}:{remote_archive}"])

        ssh_cmd = ["ssh", "-p", args.remote_port, remote_target, "bash", "-s"]
        print_cmd(ssh_cmd)
        if args.dry_run:
            for line in remote_script.splitlines():
                print(f"  {line}")
        else:
            subprocess.run(ssh_cmd, input=remote_script, text=True, check=True)

        if not args.no_health_check:
            runner.run(["curl", "-fsS", args.health_url])
            if not args.dry_run:
                print()
    finally:
        if not args.dry_run:
            if args.mode == "deploy" and not args.keep_tar and not args.archive:
                local_archive.unlink(missing_ok=True)
            if args.mode in {"package", "deploy"} and not args.keep_tar:
                shutil.rmtree(payload_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
