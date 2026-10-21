"""Full VS Code in the browser: code-server in Docker, pulled on first use.

The container mounts the project, listens on a free loopback port and runs with
``--auth none``. It is reachable only from this machine, the same as the studio.
When the studio itself runs in a container that talks to the host's Docker
(socket mounted), set ``PSKIT_STUDIO_HOST_ROOT`` to the project's path on the
host so the bind mount points at the right folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DEFAULT_IMAGE = "codercom/code-server:4.96.4"
CONTAINER_PORT = 8080
WORKSPACE = "/home/coder/project"
Runner = Callable[[List[str], float], "subprocess.CompletedProcess[str]"]


def _run(cmd: List[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, never a shell
        cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def host_path(root: Path) -> str:
    """The project path as the Docker daemon sees it."""
    mapped = os.environ.get("PSKIT_STUDIO_HOST_ROOT")
    if mapped:
        return mapped
    text = str(root.resolve())
    if sys.platform == "win32":
        # Docker Desktop accepts C:\path and C:/path; forward slashes avoid escaping issues.
        return text.replace("\\", "/")
    return text


class VSCode:
    def __init__(
        self, root: Path, image: Optional[str] = None, docker: str = "docker", runner: Optional[Runner] = None
    ) -> None:
        self.root = root.resolve()
        self.image = image or os.environ.get("PSKIT_STUDIO_VSCODE_IMAGE") or DEFAULT_IMAGE
        self.docker = docker
        self.runner: Runner = runner or _run
        digest = hashlib.sha1(str(self.root).encode(), usedforsecurity=False).hexdigest()[:10]
        self.container = f"pskit-studio-{digest}"
        self.log_job: Optional[str] = None

    def _docker(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        return self.runner([self.docker, *args], timeout)

    # ------------------------------------------------------------------ status
    def docker_info(self) -> Dict[str, Any]:
        try:
            proc = self._docker("version", "--format", "{{.Server.Version}}", timeout=10)
        except FileNotFoundError:
            return {"available": False, "error": "Docker is not installed (the `docker` command was not found)."}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "error": f"Docker did not answer: {exc}"}
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return {
                "available": False,
                "error": "Docker is installed but the daemon is not reachable"
                + (f": {detail[-1]}" if detail else ". Start Docker Desktop / the docker service."),
            }
        return {"available": True, "version": proc.stdout.strip()}

    def image_present(self) -> bool:
        try:
            return self._docker("image", "inspect", self.image, timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _inspect(self) -> Optional[Dict[str, Any]]:
        try:
            proc = self._docker("container", "inspect", self.container, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return None
        return data[0] if isinstance(data, list) and data else None

    def _url(self, info: Dict[str, Any]) -> Optional[str]:
        ports = ((info.get("NetworkSettings") or {}).get("Ports") or {}).get(f"{CONTAINER_PORT}/tcp") or []
        for binding in ports:
            port = binding.get("HostPort")
            if port:
                return f"http://127.0.0.1:{port}/?folder={WORKSPACE}"
        return None

    def status(self) -> Dict[str, Any]:
        docker = self.docker_info()
        out: Dict[str, Any] = {
            "docker": docker,
            "image": self.image,
            "image_present": False,
            "running": False,
            "container": self.container,
            "log_job": self.log_job,
        }
        if not docker.get("available"):
            return out
        out["image_present"] = self.image_present()
        info = self._inspect()
        if info is not None:
            running = bool((info.get("State") or {}).get("Running"))
            out["running"] = running
            if running:
                out["url"] = self._url(info)
        return out

    # ------------------------------------------------------------------ start / stop
    def start(self, job: Any) -> int:
        """Body of the studio job: pull if needed, (re)use the container, wait until it answers."""
        docker = self.docker_info()
        if not docker.get("available"):
            job.emit(str(docker.get("error")), "stderr")
            return 1
        info = self._inspect()
        if info is not None and (info.get("State") or {}).get("Running"):
            url = self._url(info)
            job.emit(f"VS Code is already running: {url}", "stdout")
            return 0
        if info is not None:
            job.emit("removing the stopped container", "system")
            self._docker("rm", "-f", self.container)
        if not self.image_present():
            job.emit(f"pulling {self.image} (first run only, a few hundred MB)...", "system")
            code = self._stream([self.docker, "pull", self.image], job)
            if code != 0:
                job.emit(f"docker pull failed with exit code {code}", "stderr")
                return code
        port = free_port()
        cmd = [
            "run",
            "-d",
            "--name",
            self.container,
            "-p",
            f"127.0.0.1:{port}:{CONTAINER_PORT}",
            "-v",
            f"{host_path(self.root)}:{WORKSPACE}",
            "--label",
            "managed-by=pskit-studio",
        ]
        if hasattr(os, "getuid") and sys.platform != "win32" and not os.environ.get("PSKIT_STUDIO_HOST_ROOT"):
            cmd += ["-u", f"{os.getuid()}:{os.getgid()}"]  # files stay owned by you
        cmd += [self.image, "--auth", "none", "--bind-addr", f"0.0.0.0:{CONTAINER_PORT}", WORKSPACE]
        job.emit("$ docker " + " ".join(cmd), "system")
        proc = self._docker(*cmd, timeout=120)
        if proc.returncode != 0:
            job.emit((proc.stderr or proc.stdout or "docker run failed").strip(), "stderr")
            return proc.returncode or 1
        url = f"http://127.0.0.1:{port}/?folder={WORKSPACE}"
        job.emit("waiting for VS Code to answer...", "system")
        if not _wait_http(f"http://127.0.0.1:{port}/healthz", 60):
            job.emit("code-server did not answer within 60 s; check `docker logs " + self.container + "`", "stderr")
            return 1
        job.emit(f"VS Code is ready: {url}", "stdout")
        return 0

    def stop(self) -> Dict[str, Any]:
        try:
            self._docker("rm", "-f", self.container, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"stopped": False, "error": str(exc)}
        return {"stopped": True}

    def _stream(self, cmd: List[str], job: Any) -> int:
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            )
        except OSError as exc:
            job.emit(str(exc), "stderr")
            return 127
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            job.emit(raw.decode("utf-8", "replace"), "stdout")
        return proc.wait()


def _wait_http(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as resp:  # noqa: S310 - loopback URL we just created
                if resp.status < 500:
                    return True
        except HTTPError as exc:
            if exc.code < 500:
                return True
            time.sleep(0.5)
        except (URLError, OSError, ValueError):
            time.sleep(0.5)
    return False
