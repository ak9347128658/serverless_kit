"""Laptop-side on-prem apply/destroy: ssh, docker, and bundled CPython."""

from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

from py_serverless_kit.providers.onprem_remote import (
    DeployError,
    apply_docker,
    apply_ssh,
    destroy_docker,
    dockerfile_text,
    extract_bundle_for_tests,
)
from py_serverless_kit.providers.onprem_spec import parse_deploy, python_bin_for, standalone_url


class _Proc:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_standalone_url_linux_x86():
    url = standalone_url("python3.12", "linux", "x86_64")
    assert "cpython-3.12.12+20251010-x86_64-unknown-linux-gnu-install_only.tar.gz" in url


def test_python_bin_bundle_linux():
    spec = parse_deploy(
        {"deploy": {"method": "ssh", "host": "10.0.0.1", "python": "bundle", "target_os": "linux"}},
        service="demo",
        stage="dev",
        runtime="python3.12",
        architecture="x86_64",
    )
    assert python_bin_for(spec, "/opt/pskit/demo") == "/opt/pskit/demo/python/bin/python3"


def test_apply_ssh_copies_and_starts(tmp_path: Path):
    root = tmp_path / "onprem_app"
    root.mkdir()
    (root / "onprem_ctl.py").write_text("print(1)\n", "utf-8")
    (root / "pskit_host.json").write_text("{}", "utf-8")
    staging = root / "staging"
    staging.mkdir()
    (staging / "app.py").write_text("x\n", "utf-8")
    spec = {
        "deploy": {
            "method": "ssh",
            "python": "system",
            "target_os": "linux",
            "target_arch": "x86_64",
            "runtime": "python3.12",
            "install": "process",
            "sudo": False,
            "ssh": {
                "host": "10.0.1.12",
                "user": "pskit",
                "port": 22,
                "identity_file": "",
                "known_hosts": "accept-new",
                "path": "/opt/pskit/demo",
            },
            "docker": {},
            "python_bundle": {},
        }
    }
    runner = _Proc()
    apply_ssh(root, spec, run=runner)
    joined = [" ".join(c) for c in runner.calls]
    assert any(c[0] == "ssh" and "mkdir -p" in " ".join(c) for c in runner.calls)
    assert any(c[0] == "scp" and "onprem_ctl.py" in " ".join(c) for c in runner.calls)
    assert any("onprem_ctl.py start" in line for line in joined)


def test_apply_docker_builds_and_runs(tmp_path: Path):
    root = tmp_path / "onprem_app"
    root.mkdir()
    spec = {
        "port": 8080,
        "deploy": {
            "method": "docker",
            "docker": {
                "image": "pskit-demo:dev",
                "container": "pskit-demo-dev",
                "host": "",
                "restart": "unless-stopped",
                "publish": "8080:8080",
                "args": [],
            },
        },
    }
    runner = _Proc()
    apply_docker(root, spec, run=runner)
    assert runner.calls[0][:4] == ["docker", "build", "-t", "pskit-demo:dev"]
    assert runner.calls[1][:3] == ["docker", "rm", "-f"]
    run = runner.calls[2]
    assert run[:3] == ["docker", "run", "-d"]
    assert "pskit-demo:dev" in run


def _remote_spec(pem: Path, *, install_docker: bool = True) -> dict:
    return {
        "port": 8080,
        "deploy": {
            "method": "docker",
            "sudo": True,
            "ssh": {
                "host": "10.0.1.12",
                "user": "pskit",
                "port": 22,
                "identity_file": str(pem),
                "known_hosts": "accept-new",
                "path": "/tmp/pskit-demo",
            },
            "docker": {
                "image": "pskit-demo:dev",
                "container": "pskit-demo-dev",
                "host": "",
                "restart": "unless-stopped",
                "publish": "8080:8080",
                "args": ["--memory", "256m"],
                "install_docker": install_docker,
            },
        },
    }


def test_apply_docker_remote_builds_locally_and_installs(tmp_path: Path):
    root = tmp_path / "onprem_app"
    root.mkdir()
    pem = tmp_path / "server.pem"
    pem.write_text("key\n", encoding="utf-8")
    runner = _Proc()
    apply_docker(root, _remote_spec(pem), run=runner)
    assert runner.calls[0][:4] == ["docker", "build", "-t", "pskit-demo:dev"]
    assert runner.calls[1][:3] == ["docker", "save", "-o"]
    assert runner.calls[1][-1] == "pskit-demo:dev"
    joined = [" ".join(c) for c in runner.calls]
    setup = next(line for line in joined if "apt-get install -y docker.io" in line)
    assert "dnf install -y docker" in setup
    assert "yum install -y docker" in setup
    assert "apk add --no-cache docker" in setup
    assert "command -v docker" in setup
    assert any(c[0] == "scp" and "-i" in c and str(pem) in c and "image.tar" in " ".join(c) for c in runner.calls)
    load = next(line for line in joined if "dk load -i" in line)
    assert "pskit-demo:dev" in load
    assert "--memory" in load
    assert "256m" in load
    assert not (root / "image.tar").exists()


def test_apply_docker_remote_skips_install_when_disabled(tmp_path: Path):
    root = tmp_path / "onprem_app"
    root.mkdir()
    pem = tmp_path / "server.pem"
    pem.write_text("key\n", encoding="utf-8")
    runner = _Proc()
    apply_docker(root, _remote_spec(pem, install_docker=False), run=runner)
    setup = next(" ".join(c) for c in runner.calls if c[0] == "ssh" and "command -v docker" in " ".join(c))
    assert "apt-get install" not in setup
    assert "Docker is not installed on the server" in setup


def test_apply_docker_remote_requires_pem(tmp_path: Path):
    root = tmp_path / "onprem_app"
    root.mkdir()
    spec = _remote_spec(tmp_path / "missing.pem")
    spec["deploy"]["ssh"]["identity_file"] = ""
    try:
        apply_docker(root, spec, run=_Proc())
    except DeployError as exc:
        assert ".pem" in str(exc)
    else:
        raise AssertionError("expected DeployError")


def test_destroy_docker_remote_does_not_install(tmp_path: Path):
    root = tmp_path / "onprem_app"
    root.mkdir()
    pem = tmp_path / "server.pem"
    pem.write_text("key\n", encoding="utf-8")
    runner = _Proc()
    destroy_docker(root, _remote_spec(pem), run=runner)
    assert len(runner.calls) == 1
    command = " ".join(runner.calls[0])
    assert runner.calls[0][0] == "ssh"
    assert "dk rm -f" in command
    assert "apt-get install" not in command


def test_parse_docker_pem_and_ssh_url():
    spec = parse_deploy(
        {"deploy": {"method": "docker", "host": "10.0.1.12", "user": "pskit", "pem": "keys/server.pem"}},
        service="demo",
        stage="dev",
        runtime="python3.12",
        architecture="x86_64",
    )
    assert spec.identity_file.replace("\\", "/").endswith("keys/server.pem")
    assert spec.install_docker is True
    assert spec.sudo is True
    assert spec.ssh_host == "10.0.1.12"
    via = parse_deploy(
        {"deploy": {"method": "docker", "docker_host": "ssh://ops@10.1.1.5:2222"}},
        service="demo",
        stage="dev",
        runtime="python3.12",
        architecture="x86_64",
    )
    assert via.ssh_host == "10.1.1.5"
    assert via.ssh_user == "ops"
    assert via.ssh_port == 2222
    assert via.install_docker is True
    local = parse_deploy(
        {"deploy": {"method": "docker", "image": "pskit-demo:dev"}},
        service="demo",
        stage="dev",
        runtime="python3.12",
        architecture="x86_64",
    )
    assert local.ssh_host == ""
    assert local.install_docker is False
    assert local.sudo is False


def test_dockerfile_uses_runtime():
    text = dockerfile_text("python3.11", 9090)
    assert "FROM python:3.11-slim-bookworm" in text
    assert "EXPOSE 9090" in text


def test_extract_install_only_archive(tmp_path: Path):
    archive = tmp_path / "python.tgz"
    payload = tmp_path / "src"
    (payload / "python" / "bin").mkdir(parents=True)
    (payload / "python" / "bin" / "python3").write_text("#!/bin/sh\n", "utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload / "python", arcname="python")
    dest = tmp_path / "out"
    python = extract_bundle_for_tests(archive, dest)
    assert (python / "bin" / "python3").is_file()


def test_parse_rejects_unsafe_host():
    try:
        parse_deploy(
            {"deploy": {"method": "ssh", "host": "10.0.0.1; rm -rf /"}},
            service="demo",
            stage="dev",
            runtime="python3.12",
            architecture="x86_64",
        )
    except ValueError as exc:
        assert "not safe" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_as_json_round_trip_bundle_url():
    spec = parse_deploy(
        {"deploy": {"method": "ssh", "host": "10.0.0.1", "python": "bundle"}},
        service="demo",
        stage="dev",
        runtime="python3.12",
        architecture="x86_64",
    )
    body = spec.as_json()
    assert "install_only.tar.gz" in body["python_bundle"]["url"]
    assert body["ssh"]["path"].startswith("/opt/pskit/")
    SimpleNamespace(**body["ssh"])
    json.dumps(body)
