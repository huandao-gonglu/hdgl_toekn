#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "deploy_sub2api_local.py"


def require_line(output: str, needle: str) -> None:
    if needle not in output:
        print(
            f"missing expected output:\n{needle}\n\nactual output:\n{output}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> None:
    assert_default_deploy_contract()
    assert_package_only_contract()
    assert_publish_only_contract()
    print("PASS deploy-sub2api-local dry-run contract")


def run_dry_run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def assert_default_deploy_contract() -> None:
    output = run_dry_run("--tag", "test-tag")

    require_line(output, "Mode:         deploy")
    require_line(output, "Archive:      ")
    require_line(output, "pnpm --dir frontend install --frozen-lockfile")
    require_line(output, "pnpm --dir frontend run build")
    require_line(output, "test -f backend/internal/web/dist/index.html")
    require_line(output, "env CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -tags embed")
    require_line(output, "tar -czf ")
    require_line(output, "sub2api resources docker-entrypoint.sh")
    require_line(output, "scp -P 443 ")
    require_line(output, "root@107.174.48.241:/tmp/sub2api-local-test-tag-runtime.tgz")
    require_line(output, "docker build -f Dockerfile.runtime -t sub2api-local:test-tag .")
    require_line(output, "image: sub2api-local:test-tag")
    require_line(output, "docker compose up -d sub2api")
    require_line(output, "docker compose ps sub2api")
    require_line(output, "curl -fsS https://hdgl.us.ci/health")

    if "Dockerfile.server-go-build" in output:
        print(f"unexpected remote Go build Dockerfile in output:\n{output}", file=sys.stderr)
        raise SystemExit(1)

    ssh_index = output.find("ssh -p 443")
    remote_output = output[ssh_index:] if ssh_index >= 0 else ""
    if "go build -tags embed" in remote_output:
        print(f"unexpected remote go build in output:\n{output}", file=sys.stderr)
        raise SystemExit(1)


def assert_package_only_contract() -> None:
    output = run_dry_run("--mode", "package", "--tag", "pkg-tag")

    require_line(output, "Mode:         package")
    require_line(output, "Archive:      ")
    require_line(output, "pnpm --dir frontend run build")
    require_line(output, "env CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -tags embed")
    require_line(output, "tar -czf ")

    forbidden = ["scp -P", "ssh -p", "docker compose up -d sub2api", "curl -fsS"]
    for needle in forbidden:
        if needle in output:
            print(f"unexpected publish command in package-only output ({needle}):\n{output}", file=sys.stderr)
            raise SystemExit(1)


def assert_publish_only_contract() -> None:
    output = run_dry_run(
        "--mode",
        "publish",
        "--archive",
        "/tmp/prebuilt-runtime.tgz",
        "--tag",
        "pub-tag",
    )

    require_line(output, "Mode:         publish")
    require_line(output, "Archive:      /tmp/prebuilt-runtime.tgz")
    require_line(output, "scp -P 443 /tmp/prebuilt-runtime.tgz root@107.174.48.241:/tmp/prebuilt-runtime.tgz")
    require_line(output, "docker build -f Dockerfile.runtime -t sub2api-local:pub-tag .")
    require_line(output, "docker compose up -d sub2api")
    require_line(output, "curl -fsS https://hdgl.us.ci/health")

    forbidden = ["pnpm --dir frontend", "go build -tags embed", "tar -czf"]
    for needle in forbidden:
        if needle in output:
            print(f"unexpected package command in publish-only output ({needle}):\n{output}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
