"""Laptop-side on-prem apply/destroy: ssh, docker, and bundled CPython."""

from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

from py_serverless_kit.providers.onprem_remote import apply_docker, apply_ssh, dockerfile_text, extract_bundle_for_tests
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
