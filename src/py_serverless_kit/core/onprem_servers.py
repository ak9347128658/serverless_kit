"""Saved SSH servers: name a machine once, then create services, install software, open consoles and deploy to it.

Stored in ``.pskit/onprem-servers.json`` (out of git, like the rest of ``.pskit``). Only connection details are
kept: a password typed in the studio goes to ``.pskit/ssh/<user>@<host>-<port>.pass`` (owner-only), never into the
JSON, ``pskit.yml`` or Terraform state.

``provider.options.deploy: {server: NAME}`` in ``pskit.yml`` (or ``pskit deploy --server NAME``) deploys the
functions to that machine: its host, user, port and login replace those in ``options.deploy``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SERVERS_FILE = Path(".pskit") / "onprem-servers.json"
SERVER_ENV = "PSKIT_DEPLOY_SERVER"
LOCAL = "local"  # ``--server local``: this machine, whatever the last deploy used
DEPLOYED_FILE = "onprem_deployed.json"  # .pskit/onprem-<stage>/: where the last successful deploy went
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")
_KEEP = ("host", "user", "port", "identity_file", "password_file", "auth", "sudo", "known_hosts")

RunFn = Callable[[List[str], Optional[Path]], Tuple[int, str]]

# One SSH round trip: what the machine is and whether pskit can use it.
_FACTS = (
    "echo os=$( (. /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\") || uname -s); "
    "echo arch=$(uname -m); echo cpus=$(nproc 2>/dev/null); "
    "echo memory_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null); "
    "echo disk_free=$(df -h / 2>/dev/null | awk 'NR==2 {print $4}'); "
    "echo docker=$(docker --version 2>/dev/null | sed 's/,.*//'); "
    "echo docker_access=$(docker info >/dev/null 2>&1 && echo yes || (sudo -n docker info >/dev/null 2>&1 && echo sudo) || echo no); "
    "echo sudo=$(sudo -n true 2>/dev/null && echo yes || echo no); "
    "echo python=$(python3 --version 2>/dev/null); echo node=$(node -v 2>/dev/null); "
    "echo nginx=$(nginx -v 2>&1 | grep -o 'nginx/[0-9.]*')"
)


class ServerError(ValueError):
    pass


def _path(root: Path) -> Path:
    return root / SERVERS_FILE


def load_servers(root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_path(root).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("servers") if isinstance(data, dict) else None
    return {k: v for k, v in (servers or {}).items() if isinstance(v, dict)}


def _write(root: Path, servers: Dict[str, Dict[str, Any]]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps({"servers": servers}, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def list_servers(root: Path) -> List[Dict[str, Any]]:
    from .onprem_services import describe_target

    return [
        {"name": name, **{k: v for k, v in item.items() if k != "password"},
         "label": describe_target({**item, "method": "ssh"})}
        for name, item in sorted(load_servers(root).items())
    ]


def get_server(root: Path, name: str) -> Dict[str, Any]:
    server = load_servers(root).get(str(name))
    if server is None:
        known = ", ".join(sorted(load_servers(root))) or "none yet"
        raise ServerError(f"no saved SSH server {name!r} (saved: {known}). Add it: pskit resources onprem servers add")
    return dict(server)


def server_target(root: Path, name: str) -> Dict[str, Any]:
    """The service target (``method: ssh``) of a saved server."""
    return {"method": "ssh", **get_server(root, name), "server": str(name)}


def save_server(root: Path, name: str, raw: Dict[str, Any], *, description: str = "") -> Dict[str, Any]:
    """Add or replace server ``name`` (validated like a service target; a password is saved to a file)."""
    from .onprem_services import ServiceError, normalize_target

    if not _NAME.match(str(name)):
        raise ServerError("server name: start with a letter; letters, digits, _ . - (max 63)")
    if str(name).lower() == "local":
        raise ServerError("server name 'local' is reserved for this machine (--server local)")
    previous = load_servers(root).get(name) or {}
    body = {**raw, "method": "ssh"}
    body.pop("server", None)
    if body.get("auth") == "password" and not body.get("password") and not body.get("password_file"):
        body["password_file"] = previous.get("password_file") or ""    # editing: keep the saved password
    try:
        target = normalize_target(body, root)
    except ServiceError as exc:
        raise ServerError(str(exc)) from None
    item = {k: target[k] for k in _KEEP if target.get(k) not in (None, "")}
    item["sudo"] = bool(target.get("sudo"))
    if description:
        item["description"] = str(description)[:200]
    servers = load_servers(root)
    servers[name] = item
    _write(root, servers)
    return item


def remove_server(root: Path, name: str) -> None:
    servers = load_servers(root)
    item = servers.pop(str(name), None)
    if item is None:
        raise ServerError(f"no saved SSH server {name!r}")
    _write(root, servers)
    secret = str(item.get("password_file") or "")
    still_used = any(s.get("password_file") == secret for s in servers.values())
    if secret and not still_used and Path(secret).parent == root / ".pskit" / "ssh" and os.path.isfile(secret):
        os.remove(secret)


def test_server(root: Path, name: str, *, run: Optional[RunFn] = None) -> Dict[str, Any]:
    """Log in once and report the OS, CPU, memory, disk, Docker access, sudo and installed runtimes."""
    from .onprem_services import ServiceError, _default_run, _ssh_argv, check_ssh_login

    target = server_target(root, name)
    try:
        if run is None:
            check_ssh_login(target)
    except ServiceError as exc:
        return {"name": name, "ok": False, "error": str(exc)}
    code, text = (run or _default_run)(_ssh_argv(target, _FACTS), None)
    facts: Dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and re.match(r"^[a-z_]+$", key):
            facts[key] = value.strip()
    if code or "os" not in facts:
        return {"name": name, "ok": False, "error": text.strip()[-600:] or f"ssh exited with {code}"}
    return {"name": name, "ok": True, "facts": facts}


_LOGIN_KEYS = ("host", "user", "port", "ssh_port", "pem", "identity", "identity_file", "password_file", "auth",
               "known_hosts", "server")


def resolve_deploy(options: Dict[str, Any], root: Path, stage: str, server: Optional[str] = None) -> Optional[str]:
    """Pick the machine ``onprem-<stage>`` deploys to, in place on ``options.deploy``. Returns the server name.

    ``server`` (``--server`` / ``PSKIT_DEPLOY_SERVER``) wins; ``local`` means this machine. Next comes
    ``options.deploy`` in pskit.yml (``server``, ``host`` or ``method``). Otherwise the stage goes back to the
    saved server it was last deployed to, so a deploy from the studio's machine picker sticks for later
    deploys, triggers, tests and logs.
    """
    if server == LOCAL:
        deploy = options.get("deploy")
        kept = {k: v for k, v in deploy.items() if k not in _LOGIN_KEYS} if isinstance(deploy, dict) else {}
        options["deploy"] = {**kept, "method": "local"}
        return None
    if server:
        return apply_to_deploy(options, root, server)
    deploy = options.get("deploy")
    configured = isinstance(deploy, str) or (
        isinstance(deploy, dict) and any(deploy.get(k) for k in ("server", "host", "method"))
    )
    if configured:
        return apply_to_deploy(options, root)
    remembered = deployed_server(root, stage)
    if not remembered:
        return None
    if remembered not in load_servers(root):
        raise ServerError(
            f"onprem-{stage} was last deployed to saved server {remembered!r}, which no longer exists. "
            f"Pick the machine: --server NAME, or --server {LOCAL} for this machine"
        )
    return apply_to_deploy(options, root, remembered)


def deployed_server(root: Path, stage: str) -> Optional[str]:
    """The saved server ``onprem-<stage>`` was last deployed to (None: this machine, an inline host, or never)."""
    try:
        data = json.loads((root / ".pskit" / f"onprem-{stage}" / DEPLOYED_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    server = data.get("server") if isinstance(data, dict) else None
    return str(server) if server else None


def apply_to_deploy(options: Dict[str, Any], root: Path, server: Optional[str] = None) -> Optional[str]:
    """Merge a saved server into ``options.deploy`` (in place). Returns the server name used, or None.

    ``server`` (``--server`` / ``PSKIT_DEPLOY_SERVER``) wins over ``options.deploy.server``. The server's host, user,
    port and login replace those in ``options.deploy``; method, path, python, install, ... stay. A deploy without a
    method becomes ``method: ssh``.
    """
    deploy = options.get("deploy")
    name = server or (deploy.get("server") if isinstance(deploy, dict) else None)
    if not name:
        return None
    item = get_server(root, str(name))
    if not isinstance(deploy, dict):
        deploy = {"method": str(deploy)} if isinstance(deploy, str) and deploy in ("ssh", "scp", "docker") else {}
    deploy = dict(deploy)
    deploy.setdefault("method", "ssh")
    for key in ("identity_file", "pem", "identity", "password_file", "auth"):
        deploy.pop(key, None)
    deploy.update({"host": item["host"], "user": item.get("user") or "root", "port": int(item.get("port") or 22)})
    if item.get("identity_file"):
        deploy["pem"] = item["identity_file"]
    for key in ("password_file", "auth", "known_hosts"):
        if item.get(key):
            deploy[key] = item[key]
    if item.get("sudo") and "sudo" not in deploy:
        deploy["sudo"] = True
    deploy["server"] = str(name)
    options["deploy"] = deploy
    return str(name)
