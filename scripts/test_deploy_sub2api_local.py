#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import types
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
    assert_pnpm_command_uses_resolved_pnpm_path()
    assert_pnpm_command_uses_resolved_corepack_path()
    assert_remote_script_stdin_uses_lf_bytes()
    assert_default_deploy_contract()
    assert_package_only_contract()
    assert_publish_only_contract()
    print("PASS deploy-sub2api-local dry-run contract")


def load_deploy_module() -> types.ModuleType:
    import importlib.util

    spec = importlib.util.spec_from_file_location("deploy_sub2api_local", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_pnpm_command_uses_resolved_pnpm_path() -> None:
    deploy = load_deploy_module()
    original_which = deploy.shutil.which

    def fake_which(name: str) -> str | None:
        if name == "pnpm":
            return "/opt/homebrew/bin/pnpm"
        if name == "corepack":
            raise AssertionError("corepack should not be checked when pnpm exists")
        return None

    deploy.shutil.which = fake_which
    try:
        assert deploy.resolve_pnpm_cmd(dry_run=False) == ["/opt/homebrew/bin/pnpm"]
    finally:
        deploy.shutil.which = original_which


def assert_pnpm_command_uses_resolved_corepack_path() -> None:
    deploy = load_deploy_module()
    original_which = deploy.shutil.which

    def fake_which(name: str) -> str | None:
        if name == "pnpm":
            return None
        if name == "corepack":
            return r"D:\Program Files\nodejs\corepack.cmd"
        return None

    deploy.shutil.which = fake_which
    try:
        assert deploy.resolve_pnpm_cmd(dry_run=False) == [r"D:\Program Files\nodejs\corepack.cmd", "pnpm"]
    finally:
        deploy.shutil.which = original_which


def assert_remote_script_stdin_uses_lf_bytes() -> None:
    deploy = load_deploy_module()
    original_run = deploy.subprocess.run
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    deploy.subprocess.run = fake_run
    try:
        deploy.run_remote_script(["ssh", "host", "bash", "-s"], "set -euo pipefail\r\nprintf ok\r\n")
    finally:
        deploy.subprocess.run = original_run

    if len(calls) != 1:
        print(f"expected one subprocess.run call, got {len(calls)}", file=sys.stderr)
        raise SystemExit(1)

    _args, kwargs = calls[0]
    if kwargs.get("input") != b"set -euo pipefail\nprintf ok\n":
        print(f"remote script stdin was not normalized LF bytes: {kwargs.get('input')!r}", file=sys.stderr)
        raise SystemExit(1)
    if kwargs.get("text") is not None:
        print(f"remote script stdin should not use text mode: {kwargs}", file=sys.stderr)
        raise SystemExit(1)
    if kwargs.get("check") is not True:
        print(f"remote script subprocess should still check errors: {kwargs}", file=sys.stderr)
        raise SystemExit(1)


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
    local_archive = Path("/tmp/prebuilt-runtime.tgz").resolve()
    output = run_dry_run(
        "--mode",
        "publish",
        "--archive",
        "/tmp/prebuilt-runtime.tgz",
        "--tag",
        "pub-tag",
    )

    require_line(output, "Mode:         publish")
    require_line(output, f"Archive:      {local_archive}")
    require_line(output, "scp -P 443")
    require_line(output, "root@107.174.48.241:/tmp/prebuilt-runtime.tgz")
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
