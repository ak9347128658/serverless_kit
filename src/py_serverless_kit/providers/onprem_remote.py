"""Laptop-side apply/destroy for an on-premises host.

Terraform ``local-exec`` runs this with the same interpreter as ``pskit deploy``.
The target never needs pskit installed. ``python: bundle`` ships a portable
CPython; ``method: docker`` builds an image that already contains Python.
A remote Docker target (``host`` or ``docker_host: ssh://...``) builds that
image on this machine, installs Docker on the server when it is missing, then
copies the image over SSH and runs it.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from .onprem_spec import json_targets_remote_docker, python_bin_for

_CACHE = Path(os.environ.get("PSKIT_CACHE_DIR") or (Path.home() / ".cache" / "pskit"))
RunFn = Callable[..., "subprocess.CompletedProcess[Any]"]


class DeployError(Exception):
    pass


def main(root: Path) -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    spec_path = root / "pskit_host.json"
    if not spec_path.is_file():
        sys.stderr.write(f"missing {spec_path}\n")
        return 2
    import json

    spec = json.loads(spec_path.read_text("utf-8"))
    deploy = spec.get("deploy") or {}
    method = str(deploy.get("method") or "local")
    try:
        if action == "apply":
            if method == "ssh":
                apply_ssh(root, spec)
            elif method == "docker":
                apply_docker(root, spec)
            else:
                apply_local(root, spec)
            return 0
        if action == "destroy":
            if method == "ssh":
                destroy_ssh(root, spec)
            elif method == "docker":
                destroy_docker(root, spec)
            else:
                destroy_local(root, spec)
            return 0
    except DeployError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    sys.stderr.write("usage: onprem_deploy.py [apply|destroy]\n")
    return 2


def apply_local(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    py = _ensure_python(root, spec)
    _run([str(py), str(root / "onprem_ctl.py"), "start"], cwd=root, run=run)


def destroy_local(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    py = _python_exe(root, spec, require_bundle=False)
    _run([str(py), str(root / "onprem_ctl.py"), "stop"], cwd=root, run=run, check=False)


def apply_ssh(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    deploy = spec["deploy"]
    ssh = deploy["ssh"]
    _ensure_python(root, spec)
    remote = ssh["path"]
    _ssh(ssh, f"mkdir -p { _shq(remote) }", run=run)
    for name in ("pskit_host.json", "onprem_ctl.py", "pskit.service", "staging", "python"):
        src = root / name
        if src.exists():
            _scp(ssh, src, f"{remote}/{name}", run=run)
    py = python_bin_for(_view(deploy), remote)
    if deploy.get("install") == "systemd":
        unit = spec.get("unit_name") or "pskit-host"
        unit_src = f"{remote}/pskit.service"
        dest = f"/etc/systemd/system/{unit}.service"
        copy = f"cp { _shq(unit_src) } { _shq(dest) }"
        if deploy.get("sudo"):
            copy = "sudo " + copy
        reload = "systemctl daemon-reload && systemctl enable --now " + _shq(unit)
        if deploy.get("sudo"):
            reload = "sudo bash -lc " + _shq(reload)
        _ssh(ssh, copy, run=run)
        _ssh(ssh, reload, run=run)
        return
    _ssh(ssh, f"cd { _shq(remote) } && { _shq(py) } onprem_ctl.py start", run=run)


def destroy_ssh(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    del root
    deploy = spec["deploy"]
    ssh = deploy["ssh"]
    remote = ssh["path"]
    if deploy.get("install") == "systemd":
        unit = spec.get("unit_name") or "pskit-host"
        cmd = "systemctl disable --now " + _shq(unit)
        if deploy.get("sudo"):
            cmd = "sudo " + cmd
        _ssh(ssh, cmd, run=run, check=False)
        return
    py = python_bin_for(_view(deploy), remote)
    _ssh(ssh, f"cd { _shq(remote) } && { _shq(py) } onprem_ctl.py stop", run=run, check=False)


def apply_docker(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    deploy = spec["deploy"]
    if json_targets_remote_docker(deploy):
        apply_docker_remote(root, spec, run=run)
        return
    docker = deploy["docker"]
    env = _docker_env(docker.get("host") or "")
    image = docker["image"]
    container = docker["container"]
    _run(["docker", "build", "-t", image, "."], cwd=root, run=run, env=env)
    _run(["docker", "rm", "-f", container], cwd=root, run=run, env=env, check=False)
    _run(_docker_run_argv(docker, spec), cwd=root, run=run, env=env)


def apply_docker_remote(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    """Build on this machine, install Docker on the server if needed, then load and run."""
    deploy = spec["deploy"]
    docker = deploy["docker"]
    ssh = deploy["ssh"]
    image = str(docker["image"])
    identity = str(ssh.get("identity_file") or "")
    if not identity:
        raise DeployError(
            "remote Docker deploy needs a .pem private key. "
            "Set options.deploy.pem, or run `pskit deploy` in a terminal and enter the path."
        )
    if not os.path.isfile(identity):
        raise DeployError(f"SSH private key not found: {identity}")
    _run(["docker", "build", "-t", image, "."], cwd=root, run=run)
    tar_path = root / "image.tar"
    try:
        _run(["docker", "save", "-o", str(tar_path), image], cwd=root, run=run)
        remote_tar = _remote_tar(str(docker["container"]))
        remote_dir = remote_tar.rsplit("/", 1)[0]
        install = bool(docker.get("install_docker", True))
        _ssh(ssh, _docker_setup_script(install=install, remote_dir=remote_dir), run=run)
        _scp(ssh, tar_path, remote_tar, run=run)
        _ssh(ssh, _docker_load_script(docker, spec, remote_tar), run=run)
    finally:
        if tar_path.is_file():
            tar_path.unlink()


def destroy_docker(root: Path, spec: Dict[str, Any], *, run: Optional[RunFn] = None) -> None:
    deploy = spec["deploy"]
    docker = deploy["docker"]
    if json_targets_remote_docker(deploy):
        ssh = deploy["ssh"]
        container = str(docker["container"])
        script = _docker_shell_header() + f"dk rm -f {_shq(container)} || true\n"
        _ssh(ssh, script, run=run, check=False)
        return
    env = _docker_env(docker.get("host") or "")
    _run(["docker", "rm", "-f", docker["container"]], cwd=root, run=run, env=env, check=False)


def _docker_run_argv(docker: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    publish = str(docker.get("publish") or f"{spec.get('port') or 8080}:{spec.get('port') or 8080}")
    argv = [
        "docker",
        "run",
        "-d",
        "--name",
        str(docker["container"]),
        "--restart",
        str(docker.get("restart") or "unless-stopped"),
        "-p",
        publish,
        "-v",
        f"{docker['container']}-data:/var/pskit/data",
    ]
    argv.extend(str(item) for item in (docker.get("args") or []))
    argv.append(str(docker["image"]))
    return argv


def _remote_tar(container: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", container).strip("-") or "pskit"
    # Home-relative so the SSH user can write it, and so it is not a shared temp dir.
    return f"pskit-images/{slug}/image.tar"


def _docker_shell_header() -> str:
    return (
        "set -eu\n"
        "as_root() { if [ \"$(id -u)\" -eq 0 ]; then \"$@\"; else sudo \"$@\"; fi; }\n"
        "dk() { if docker info >/dev/null 2>&1; then docker \"$@\"; else as_root docker \"$@\"; fi; }\n"
    )


def _docker_setup_script(*, install: bool, remote_dir: str) -> str:
    """Create the staging directory and install Docker when the server does not have it."""
    lines = [_docker_shell_header(), f"mkdir -p {_shq(remote_dir)}\n"]
    if install:
        lines.append(
            "if ! command -v docker >/dev/null 2>&1; then\n"
            "  if command -v apt-get >/dev/null 2>&1; then\n"
            "    export DEBIAN_FRONTEND=noninteractive\n"
            "    as_root apt-get update\n"
            "    as_root apt-get install -y docker.io\n"
            "  elif command -v dnf >/dev/null 2>&1; then\n"
            "    as_root dnf install -y docker\n"
            "  elif command -v yum >/dev/null 2>&1; then\n"
            "    as_root yum install -y docker\n"
            "  elif command -v apk >/dev/null 2>&1; then\n"
            "    as_root apk add --no-cache docker\n"
            "  else\n"
            '    echo "pskit: Docker is not installed and this server has no apt-get, dnf, yum, or apk" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "  if command -v systemctl >/dev/null 2>&1; then\n"
            "    as_root systemctl enable --now docker || as_root service docker start || true\n"
            "  elif command -v service >/dev/null 2>&1; then\n"
            "    as_root service docker start || true\n"
            "  fi\n"
            "fi\n"
        )
    lines.append(
        "if ! command -v docker >/dev/null 2>&1; then\n"
        '  echo "pskit: Docker is not installed on the server. Set options.deploy.install_docker: true or install it, then redeploy." >&2\n'
        "  exit 1\n"
        "fi\n"
        "dk info >/dev/null\n"
    )
    return "".join(lines)


def _docker_load_script(docker: Dict[str, Any], spec: Dict[str, Any], remote_tar: str) -> str:
    publish = str(docker.get("publish") or f"{spec.get('port') or 8080}:{spec.get('port') or 8080}")
    restart = str(docker.get("restart") or "unless-stopped")
    container = str(docker["container"])
    image = str(docker["image"])
    parts = [
        "dk",
        "run",
        "-d",
        "--name",
        _shq(container),
        "--restart",
        _shq(restart),
        "-p",
        _shq(publish),
        "-v",
        _shq(f"{container}-data:/var/pskit/data"),
    ]
    for item in docker.get("args") or []:
        parts.append(_shq(str(item)))
    parts.append(_shq(image))
    run = " ".join(parts)
    return (
        _docker_shell_header()
        + f"dk load -i {_shq(remote_tar)}\n"
        + f"dk rm -f {_shq(container)} || true\n"
        + run
        + "\n"
        + f"rm -f {_shq(remote_tar)}\n"
    )


def dockerfile_text(runtime: str, port: int) -> str:
    from .onprem_spec import docker_base_image

    base = docker_base_image(runtime)
    return (
        f"FROM {base}\n"
        "WORKDIR /opt/pskit\n"
        "COPY staging/ /opt/pskit/app/\n"
        "COPY pskit_host.json onprem_ctl.py /opt/pskit/\n"
        "ENV PYTHONUNBUFFERED=1\n"
        f"EXPOSE {port}\n"
        'CMD ["python", "onprem_ctl.py", "foreground"]\n'
    )


def systemd_unit_text(desc: str, remote_path: str, python_bin: str, user: str) -> str:
    return (
        "[Unit]\n"
        f"Description={desc}\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={remote_path}\n"
        f"ExecStart={python_bin} {remote_path}/onprem_ctl.py foreground\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"User={user}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _view(deploy: Dict[str, Any]) -> Any:
    from .onprem_spec import DeploySpec

    ssh = deploy.get("ssh") or {}
    docker = deploy.get("docker") or {}
    bundle = deploy.get("python_bundle") or {}
    return DeploySpec(
        method=str(deploy.get("method") or "local"),
        python=str(deploy.get("python") or "system"),
        target_os=str(deploy.get("target_os") or "linux"),
        target_arch=str(deploy.get("target_arch") or "x86_64"),
        runtime=str(deploy.get("runtime") or "python3.12"),
        ssh_host=str(ssh.get("host") or ""),
        ssh_user=str(ssh.get("user") or ""),
        ssh_port=int(ssh.get("port") or 22),
        identity_file=str(ssh.get("identity_file") or ""),
        known_hosts=str(ssh.get("known_hosts") or "accept-new"),
        remote_path=str(ssh.get("path") or ""),
        sudo=bool(deploy.get("sudo")),
        install=str(deploy.get("install") or "process"),
        image=str(docker.get("image") or ""),
        container=str(docker.get("container") or ""),
        docker_host=str(docker.get("host") or ""),
        restart=str(docker.get("restart") or "unless-stopped"),
        publish=str(docker.get("publish") or ""),
        docker_args=list(docker.get("args") or []),
        install_docker=bool(docker.get("install_docker", False)),
        python_url=str(bundle.get("url") or ""),
        python_sha256=str(bundle.get("sha256") or ""),
        python_home=str(bundle.get("home") or ""),
    )


def _python_exe(root: Path, spec: Dict[str, Any], *, require_bundle: bool) -> Path:
    deploy = spec.get("deploy") or {}
    if deploy.get("python") == "bundle":
        exe = root / "python" / ("python.exe" if (deploy.get("target_os") == "windows") else Path("bin") / "python3")
        if exe.is_file():
            return exe
        if require_bundle:
            raise DeployError(f"bundled interpreter missing at {exe}")
    return Path(sys.executable)


def _ensure_python(root: Path, spec: Dict[str, Any]) -> Path:
    deploy = spec.get("deploy") or {}
    if deploy.get("python") != "bundle":
        return Path(sys.executable) if deploy.get("method") == "local" else _python_exe(root, spec, require_bundle=False)
    dest = root / "python"
    marker = dest / ".pskit-python"
    bundle = deploy.get("python_bundle") or {}
    home = str(bundle.get("home") or "")
    url = str(bundle.get("url") or "")
    sha = str(bundle.get("sha256") or "")
    if dest.is_dir() and marker.is_file() and marker.read_text("utf-8").strip() in (url, home, "ok"):
        return _python_exe(root, spec, require_bundle=True)
    if dest.exists():
        shutil.rmtree(dest)
    if home:
        src = Path(home)
        if not src.is_dir():
            raise DeployError(f"options.deploy.python_home {home!r} is not a directory")
        shutil.copytree(src, dest)
        marker.write_text(home, "utf-8")
        return _python_exe(root, spec, require_bundle=True)
    if not url:
        raise DeployError("python: bundle needs options.deploy.python_url or python_home")
    archive = _download(url, sha)
    dest.mkdir(parents=True)
    with tarfile.open(archive, "r:*") as tar:
        _extract_tar(tar, dest.parent)
    extracted = dest.parent / "python"
    if extracted.resolve() != dest.resolve() and extracted.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        extracted.rename(dest)
    if not dest.is_dir():
        raise DeployError(f"bundle at {url} did not contain a python/ directory")
    marker.write_text(url, "utf-8")
    return _python_exe(root, spec, require_bundle=True)


def _download(url: str, sha256: str) -> Path:
    _CACHE.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1] or "python.tgz"
    path = _CACHE / "python-standalone" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and (not sha256 or _sha256(path) == sha256.lower()):
        return path
    req = Request(url, headers={"User-Agent": "py-serverless-kit"})  # noqa: S310 - URL from config
    try:
        with urlopen(req, timeout=120) as resp:  # noqa: S310
            data = resp.read()
    except (URLError, OSError, TimeoutError) as exc:
        raise DeployError(
            f"could not download bundled CPython from {url}: {exc}. "
            "Set options.deploy.python_home to a portable interpreter, or use method: docker."
        ) from exc
    if sha256 and hashlib.sha256(data).hexdigest() != sha256.lower():
        raise DeployError(f"sha256 mismatch for {url}")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def _extract_tar(tar: tarfile.TarFile, dest: Path) -> None:
    # filter= exists on 3.12+; keep 3.9-compatible.
    if sys.version_info >= (3, 12):
        tar.extractall(dest, filter="data")  # noqa: S202
    else:
        tar.extractall(dest)  # noqa: S202


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _docker_env(host: str) -> Dict[str, str]:
    env = dict(os.environ)
    if host:
        env["DOCKER_HOST"] = host
    return env


def _ssh_base(ssh: Dict[str, Any]) -> List[str]:
    argv = ["ssh", "-o", "BatchMode=yes", "-p", str(ssh.get("port") or 22)]
    identity = str(ssh.get("identity_file") or "")
    if identity:
        argv.extend(["-i", identity])
    known = str(ssh.get("known_hosts") or "accept-new")
    if known == "accept-new":
        argv.extend(["-o", "StrictHostKeyChecking=accept-new"])
    elif known == "ignore":
        null = "NUL" if sys.platform == "win32" else "/dev/null"
        argv.extend(["-o", "StrictHostKeyChecking=no", "-o", f"UserKnownHostsFile={null}"])
    elif known:
        argv.extend(["-o", f"UserKnownHostsFile={known}", "-o", "StrictHostKeyChecking=yes"])
    return argv


def _target(ssh: Dict[str, Any]) -> str:
    return f"{ssh['user']}@{ssh['host']}"


def _ssh(ssh: Dict[str, Any], command: str, *, run: Optional[RunFn], check: bool = True) -> None:
    argv = _ssh_base(ssh) + [_target(ssh), command]
    _run(argv, run=run, check=check)


def _scp(ssh: Dict[str, Any], src: Path, dest: str, *, run: Optional[RunFn]) -> None:
    argv = ["scp", "-r", "-o", "BatchMode=yes", "-P", str(ssh.get("port") or 22)]
    identity = str(ssh.get("identity_file") or "")
    if identity:
        argv.extend(["-i", identity])
    known = str(ssh.get("known_hosts") or "accept-new")
    if known == "accept-new":
        argv.extend(["-o", "StrictHostKeyChecking=accept-new"])
    elif known == "ignore":
        null = "NUL" if sys.platform == "win32" else "/dev/null"
        argv.extend(["-o", "StrictHostKeyChecking=no", "-o", f"UserKnownHostsFile={null}"])
    argv.extend([str(src), f"{_target(ssh)}:{dest}"])
    _run(argv, run=run)


def _shq(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _run(
    argv: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    run: Optional[RunFn] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = True,
) -> None:
    fn = run or subprocess.run
    try:
        proc = fn(list(argv), cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise DeployError(f"command not found: {argv[0]}. Install it on this machine and retry.") from exc
    code = int(proc.returncode or 0)
    if check and code:
        err = str(proc.stderr or proc.stdout or "")[-4000:]
        raise DeployError(f"command failed ({code}): {' '.join(argv)}\n{err}")


def extract_bundle_for_tests(archive: Path, dest: Path) -> Path:
    """Used by tests: unpack an install_only-style archive into dest/python."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        _extract_tar(tar, dest)
    python = dest / "python"
    if not python.is_dir():
        raise DeployError("archive did not contain python/")
    return python
