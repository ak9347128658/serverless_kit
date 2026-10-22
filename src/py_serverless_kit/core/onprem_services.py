"""Create, inspect, back up and remove the services on-premises functions use.

One call starts Redis, Valkey, RabbitMQ, an SQS-compatible queue (ElasticMQ),
PostgreSQL, MySQL, MariaDB, MongoDB, MinIO, Memcached or Mailpit as a Docker
container, either on this machine (``local``) or on any server reachable with
OpenSSH (``ssh``), which may or may not be the server the functions run on. It can
also install Python or Docker on an SSH server.

Each service produces a ``resources:`` entry (driver options and connection values)
that :func:`apply_to_raw` adds to ``pskit.yml``, optionally with a trigger on the
functions it is attached to. Passwords never go into ``pskit.yml``: they are written
to the project's on-prem env file (``provider.options.env_file``, default
``.env.onprem``), which the host loads at start, and referenced as ``<name>_env``.

What was created lives in ``.pskit/onprem-services.json`` (with the credentials, so
``.pskit/`` must stay out of version control). pskit runs no cloud API here: only
``docker`` and ``ssh`` / ``scp`` commands, shown in the output as they run.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

STATE_FILE = Path(".pskit") / "onprem-services.json"
BACKUP_DIR = Path(".pskit") / "backups"
DEFAULT_ENV_FILE = ".env.onprem"
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_HOST = re.compile(r"^[A-Za-z0-9_.:\[\]-]{1,253}$")
_USER = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]{0,63}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,254}$")

Emit = Callable[[str], None]
# (argv, stdout file or None) -> (exit code, captured output). Injected by tests.
RunFn = Callable[[List[str], Optional[Path]], Tuple[int, str]]


class ServiceError(Exception):
    """A request that cannot be carried out; the message says what to change."""


def _svc(
    label: str,
    image: str,
    port: int,
    roles: Sequence[str],
    *,
    extra_ports: Sequence[int] = (),
    data: str = "",
    env: Optional[Dict[str, str]] = None,
    command: Sequence[str] = (),
    health: str = "",
    backup: str = "",
    backup_ext: str = "",
    notes: str = "",
    run_args: Sequence[str] = (),
    init: str = "",
) -> Dict[str, Any]:
    return {
        "init": init,
        "label": label,
        "kind": "service",
        "image": image,
        "port": port,
        "extra_ports": list(extra_ports),
        "roles": list(roles),
        "data": data,
        "env": dict(env or {}),
        "command": list(command),
        "health": health,
        "backup": backup,
        "backup_ext": backup_ext,
        "notes": notes,
        "run_args": list(run_args),
    }


# `{password}`, `{user}`, `{database}`, `{bucket}`, `{name}` (the resource key), `{address}` and
# `{port}` are filled in when the service is created. `health`, `init` (once, after it is ready)
# and `backup` run inside the container with `sh -c`; backup writes to stdout.
# A role is the resource type the service is used as; `stream` is a queue on a Redis stream.
ROLE_TYPE = {"stream": "queue"}
SERVICES: Dict[str, Dict[str, Any]] = {
    "redis": _svc(
        "Redis",
        "redis:7-alpine",
        6379,
        ("queue", "stream", "cache"),
        data="/data",
        env={"REDIS_PASSWORD": "{password}"},
        command=("sh", "-c", 'exec redis-server --appendonly yes --requirepass "$REDIS_PASSWORD"'),
        health='redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping | grep -q PONG',
        backup='redis-cli -a "$REDIS_PASSWORD" --no-auth-warning SAVE >/dev/null && cat /data/dump.rdb',
        backup_ext="rdb",
        notes="Queue (redis driver: leased lists), stream (redis_stream driver: consumer group, several hosts) "
        "or a cache for handlers.",
    ),
    "valkey": _svc(
        "Valkey",
        "valkey/valkey:8-alpine",
        6379,
        ("queue", "stream", "cache"),
        data="/data",
        env={"VALKEY_PASSWORD": "{password}"},
        command=("sh", "-c", 'exec valkey-server --appendonly yes --requirepass "$VALKEY_PASSWORD"'),
        health='valkey-cli -a "$VALKEY_PASSWORD" --no-auth-warning ping | grep -q PONG',
        backup='valkey-cli -a "$VALKEY_PASSWORD" --no-auth-warning SAVE >/dev/null && cat /data/dump.rdb',
        backup_ext="rdb",
        notes="Open-source Redis fork; same redis and redis_stream drivers.",
    ),
    "rabbitmq": _svc(
        "RabbitMQ",
        "rabbitmq:3-management-alpine",
        5672,
        ("queue",),
        extra_ports=(15672,),
        data="/var/lib/rabbitmq",
        env={"RABBITMQ_DEFAULT_USER": "{user}", "RABBITMQ_DEFAULT_PASS": "{password}"},
        health="rabbitmq-diagnostics -q ping",
        backup="rabbitmqctl export_definitions -",
        backup_ext="definitions.json",
        notes="AMQP with manual acks through a small driver class pskit adds to your app (needs pika). "
        "Management UI on port 15672. Backups hold definitions (queues, users), not messages.",
        run_args=("--hostname", "pskit-rabbitmq"),
    ),
    "elasticmq": _svc(
        "ElasticMQ (SQS-compatible)",
        "softwaremill/elasticmq-native:1.6.11",
        9324,
        ("queue",),
        extra_ports=(9325,),
        notes="Amazon SQS API, used through the sqs driver. Keeps messages in memory: "
        "a restart empties it, so use it for development and tests. UI on port 9325.",
    ),
    "postgres": _svc(
        "PostgreSQL",
        "postgres:16-alpine",
        5432,
        ("database",),
        data="/var/lib/postgresql/data",
        env={"POSTGRES_USER": "{user}", "POSTGRES_PASSWORD": "{password}", "POSTGRES_DB": "{database}"},
        health='pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        backup='pg_dumpall -U "$POSTGRES_USER"',
        backup_ext="sql",
    ),
    "mysql": _svc(
        "MySQL",
        "mysql:8.4",
        3306,
        ("database",),
        data="/var/lib/mysql",
        env={
            "MYSQL_ROOT_PASSWORD": "{password}",
            "MYSQL_DATABASE": "{database}",
            "MYSQL_USER": "{user}",
            "MYSQL_PASSWORD": "{password}",
        },
        health='mysqladmin ping -h 127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --silent',
        backup='mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --all-databases --single-transaction',
        backup_ext="sql",
    ),
    "mariadb": _svc(
        "MariaDB",
        "mariadb:11",
        3306,
        ("database",),
        data="/var/lib/mysql",
        env={
            "MARIADB_ROOT_PASSWORD": "{password}",
            "MARIADB_DATABASE": "{database}",
            "MARIADB_USER": "{user}",
            "MARIADB_PASSWORD": "{password}",
        },
        health="healthcheck.sh --connect --innodb_initialized",
        backup='mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --all-databases --single-transaction',
        backup_ext="sql",
    ),
    "mongodb": _svc(
        "MongoDB",
        "mongo:7",
        27017,
        ("database",),
        data="/data/db",
        env={"MONGO_INITDB_ROOT_USERNAME": "{user}", "MONGO_INITDB_ROOT_PASSWORD": "{password}"},
        health="mongosh --quiet --eval 'db.runCommand({ping: 1}).ok' | grep -q 1",
        backup='mongodump --archive -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" '
        "--authenticationDatabase admin",
        backup_ext="archive",
    ),
    # MinIO no longer publishes images on Docker Hub or quay.io; Bitnami's archived build still
    # pulls anonymously. `image` can point at your own mirror.
    "minio": _svc(
        "MinIO (S3-compatible)",
        "bitnamilegacy/minio:2025.7.23",
        9000,
        ("storage",),
        extra_ports=(9001,),
        data="/bitnami/minio/data",
        env={"MINIO_ROOT_USER": "{user}", "MINIO_ROOT_PASSWORD": "{password}", "MINIO_DEFAULT_BUCKETS": "{bucket}"},
        health="curl -fs http://127.0.0.1:9000/minio/health/ready >/dev/null",
        init='mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null '
        "&& mc mb --ignore-existing local/{bucket}",
        backup="tar czf - -C /bitnami/minio/data .",
        backup_ext="tar.gz",
        notes="Buckets watched through the minio driver. Console on port 9001.",
    ),
    "kafka": _svc(
        "Kafka (Redpanda, single node)",
        "redpandadata/redpanda:v24.3.6",
        9092,
        ("queue",),
        data="/var/lib/redpanda/data",
        command=(
            "redpanda",
            "start",
            "--mode",
            "dev-container",
            "--smp",
            "1",
            "--memory",
            "1G",
            "--kafka-addr",
            "internal://0.0.0.0:9093,external://0.0.0.0:9092",
            "--advertise-kafka-addr",
            "internal://127.0.0.1:9093,external://{address}:{port}",
        ),
        health="rpk cluster health -X brokers=127.0.0.1:9093 | grep -Eq 'Healthy:[[:space:]]+true'",
        init="rpk topic create {name} -X brokers=127.0.0.1:9093 || true",
        backup="tar czf - -C /var/lib/redpanda/data .",
        backup_ext="tar.gz",
        notes="Kafka API (Redpanda, no ZooKeeper). Consumed through a driver class pskit adds to your app "
        "(needs kafka-python). No authentication: keep the port on a private network.",
    ),
    "nats": _svc(
        "NATS JetStream",
        "nats:2.10-alpine",
        4222,
        ("queue",),
        extra_ports=(8222,),
        data="/data",
        env={"NATS_PASSWORD": "{password}"},
        command=("sh", "-c", 'exec nats-server -js -sd /data -m 8222 --user {user} --pass "$NATS_PASSWORD"'),
        health="wget -qO- 'http://127.0.0.1:8222/healthz?js-enabled-only=true' | grep -q ok",
        backup="tar czf - -C /data .",
        backup_ext="tar.gz",
        notes="A JetStream stream with a durable pull consumer, through a driver class pskit adds to your app "
        "(needs nats-py). Monitoring on port 8222.",
    ),
    "memcached": _svc("Memcached", "memcached:1.6-alpine", 11211, ("cache",), notes="In-memory cache; nothing to back up."),
    "mailpit": _svc(
        "Mailpit (SMTP)",
        "axllent/mailpit:latest",
        1025,
        ("custom",),
        extra_ports=(8025,),
        notes="An SMTP server that keeps every mail for inspection (web UI on port 8025). For development and tests.",
    ),
}

RUNTIMES: Dict[str, Dict[str, Any]] = {
    "python": {
        "label": "Python 3",
        "kind": "runtime",
        "roles": [],
        "notes": "Installs python3 (with venv and pip) on an SSH server so the host can run with python: system.",
        "check": "python3 --version",
    },
    "docker": {
        "label": "Docker",
        "kind": "runtime",
        "roles": [],
        "notes": "Installs and starts Docker on an SSH server (needed by every service above, and by method: docker).",
        "check": "docker --version",
    },
}

_RUNTIME_INSTALL = {
    "python": (
        "if ! command -v python3 >/dev/null 2>&1 || ! python3 -m venv --help >/dev/null 2>&1; then\n"
        "  if command -v apt-get >/dev/null 2>&1; then\n"
        "    export DEBIAN_FRONTEND=noninteractive\n"
        "    as_root apt-get update\n"
        "    as_root apt-get install -y python3 python3-venv python3-pip\n"
        "  elif command -v dnf >/dev/null 2>&1; then\n"
        "    as_root dnf install -y python3 python3-pip\n"
        "  elif command -v yum >/dev/null 2>&1; then\n"
        "    as_root yum install -y python3 python3-pip\n"
        "  elif command -v apk >/dev/null 2>&1; then\n"
        "    as_root apk add --no-cache python3 py3-pip\n"
        "  else\n"
        '    echo "pskit: no apt-get, dnf, yum or apk on this server; install Python 3 by hand" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "fi\n"
        "python3 --version\n"
    ),
}


def catalog() -> List[Dict[str, Any]]:
    """Everything that can be created, for forms and ``pskit resources catalog``."""
    out = []
    for name, svc in SERVICES.items():
        out.append(
            {
                "id": name,
                "label": svc["label"],
                "kind": "service",
                "image": svc["image"],
                "port": svc["port"],
                "extra_ports": svc["extra_ports"],
                "roles": svc["roles"],
                "backup": bool(svc["backup"]),
                "persistent": bool(svc["data"]),
                "notes": svc["notes"],
            }
        )
    for name, rt in RUNTIMES.items():
        out.append({"id": name, "label": rt["label"], "kind": "runtime", "roles": [], "notes": rt["notes"]})
    return out


# ----------------------------------------------------------------------------- targets


def default_target(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The functions' SSH server when ``provider.options.deploy`` targets one, else this machine."""
    provider = (raw or {}).get("provider") or {}
    options = provider.get("options") if provider.get("name") == "onprem" else None
    if options is None:
        options = ((((raw or {}).get("providers") or {}).get("onprem") or {}).get("provider") or {}).get("options")
    deploy = (options or {}).get("deploy") if isinstance(options, dict) else None
    if isinstance(deploy, dict) and deploy.get("host"):
        target: Dict[str, Any] = {
            "method": "ssh",
            "host": str(deploy["host"]),
            "user": str(deploy.get("user") or "root"),
            "port": int(deploy.get("ssh_port") or deploy.get("port") or 22),
            "sudo": bool(deploy.get("sudo")),
        }
        identity = deploy.get("pem") or deploy.get("identity_file")
        if identity:
            target["identity_file"] = str(identity)
        return target
    return {"method": "local"}


def normalize_target(raw: Any, root: Path) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"method": "local"}
    method = str(raw.get("method") or "local")
    if method == "local":
        return {"method": "local"}
    if method != "ssh":
        raise ServiceError("target.method must be local or ssh")
    host = str(raw.get("host") or "").strip()
    if not _HOST.match(host) or host.startswith("-"):
        raise ServiceError("target.host must be a host name or IP address")
    user = str(raw.get("user") or "root").strip()
    if not _USER.match(user):
        raise ServiceError("target.user must be a plain user name")
    try:
        port = int(raw.get("port") or 22)
    except (TypeError, ValueError):
        raise ServiceError("target.port must be a number") from None
    if not 1 <= port <= 65535:
        raise ServiceError("target.port must be between 1 and 65535")
    target: Dict[str, Any] = {"method": "ssh", "host": host, "user": user, "port": port, "sudo": bool(raw.get("sudo"))}
    identity = str(raw.get("identity_file") or raw.get("pem") or "").strip()
    if identity:
        path = Path(identity).expanduser()
        if not path.is_absolute():
            path = root / path
        target["identity_file"] = str(path)
    known = str(raw.get("known_hosts") or "").strip()
    if known:
        target["known_hosts"] = known
    return target


def describe_target(target: Dict[str, Any]) -> str:
    if target.get("method") == "ssh":
        return f"{target.get('user')}@{target.get('host')}:{target.get('port', 22)}"
    return "this machine"


def _ssh_argv(target: Dict[str, Any], command: str) -> List[str]:
    from ..providers.onprem_remote import _ssh_base, _target

    ssh = {
        "host": target["host"],
        "user": target["user"],
        "port": target.get("port") or 22,
        "identity_file": target.get("identity_file") or "",
        "known_hosts": target.get("known_hosts") or "accept-new",
    }
    return _ssh_base(ssh) + [_target(ssh), command]


def _shq(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def docker_argv(target: Dict[str, Any], args: Sequence[str]) -> List[str]:
    """``docker <args>`` on the target. Over SSH it falls back to ``sudo -n docker``."""
    if target.get("method") != "ssh":
        return ["docker", *args]
    quoted = " ".join(_shq(str(a)) for a in args)
    if target.get("sudo"):
        command = f"sudo -n docker {quoted}"
    else:
        command = f"if docker info >/dev/null 2>&1; then docker {quoted}; else sudo -n docker {quoted}; fi"
    return _ssh_argv(target, command)


# ----------------------------------------------------------------------------- running commands


def _default_run(argv: List[str], stdout: Optional[Path]) -> Tuple[int, str]:
    try:
        if stdout is not None:
            with open(stdout, "wb") as handle:
                proc = subprocess.run(argv, stdout=handle, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)  # noqa: S603
            return proc.returncode, proc.stderr.decode("utf-8", "replace")
        proc = subprocess.run(  # noqa: S603 - explicit argv, no shell
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return 127, f"command not found: {argv[0]}. Install it on this machine and retry."
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


class _Runner:
    def __init__(self, emit: Emit, run: Optional[RunFn]) -> None:
        self.emit = emit
        self.run_fn = run or _default_run

    def __call__(self, argv: List[str], *, check: bool = True, show: bool = True, stdout: Optional[Path] = None) -> str:
        if show:
            self.emit("$ " + " ".join(_mask(a) for a in argv))
        code, out = self.run_fn(argv, stdout)
        text = out.strip()
        if text and show:
            for line in text.splitlines()[-40:]:
                self.emit("  " + line)
        if check and code:
            last = text.splitlines()[-1] if text else "no output"
            raise ServiceError(f"command failed ({code}): {_mask(last)}")
        return out


def _mask(arg: str) -> str:
    # `-e NAME=secret` and URLs with passwords stay out of logs.
    text = str(arg)
    if re.match(r"^[A-Z0-9_]*(PASSWORD|PASS|SECRET)[A-Z0-9_]*=", text):
        return text.split("=", 1)[0] + "=***"
    text = re.sub(r"(://[^:/@\s]*:)[^@\s]+@", r"\1***@", text)
    return re.sub(r"((?:PASSWORD|PASS|SECRET)[A-Z0-9_]*=)[^ ']+", r"\1***", text)


# ----------------------------------------------------------------------------- state and env file


def load_state(root: Path) -> Dict[str, Any]:
    path = root / STATE_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"services": {}}
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return {"services": {}}
    return data


def save_state(root: Path, state: Dict[str, Any]) -> None:
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def env_file_of(raw: Optional[Dict[str, Any]]) -> str:
    provider = (raw or {}).get("provider") or {}
    options = provider.get("options") if provider.get("name") == "onprem" else None
    if not isinstance(options, dict):
        override = (((raw or {}).get("providers") or {}).get("onprem") or {}).get("provider") or {}
        options = override.get("options")
    value = options.get("env_file") if isinstance(options, dict) else None
    return str(value) if value else DEFAULT_ENV_FILE


def update_env_file(path: Path, values: Dict[str, Optional[str]]) -> None:
    """Set (or with ``None`` remove) ``KEY=value`` lines, keeping everything else."""
    lines = path.read_text("utf-8").splitlines() if path.is_file() else []
    pending = dict(values)
    out: List[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if "=" in line and key in pending:
            value = pending.pop(key)
            if value is not None:
                out.append(f"{key}={value}")
            continue
        out.append(line)
    for key, value in pending.items():
        if value is not None:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(out).rstrip("\n") + "\n" if out else "").encode("utf-8"))


def _ensure_ignored(root: Path, entry: str) -> None:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return
    lines = [line.strip() for line in gitignore.read_text("utf-8").splitlines()]
    if entry in lines or "/" + entry in lines:
        return
    text = gitignore.read_text("utf-8")
    gitignore.write_bytes((text.rstrip("\n") + f"\n# pskit on-prem service credentials\n{entry}\n").encode("utf-8"))


# ----------------------------------------------------------------------------- create


def _var_prefix(key: str) -> str:
    return "PSKIT_" + re.sub(r"[^A-Za-z0-9]", "_", key).upper()


def _bucket_name(key: str) -> str:
    name = re.sub(r"[^a-z0-9.-]", "-", key.lower()).strip("-.")
    return (name + "-bucket")[:63] if len(name) < 3 else name[:63]


def _container_name(project: str, key: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", f"pskit-{project}-{key}" if project else f"pskit-{key}")
    return base[:120]


def check_request(body: Dict[str, Any], root: Path, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate a create request and fill in defaults. Raises :class:`ServiceError`."""
    key = str(body.get("key") or "").strip()
    if not _NAME.match(key):
        raise ServiceError("key must start with a letter and use letters, digits, - or _ (max 63)")
    service = str(body.get("service") or "")
    if service not in SERVICES and service not in RUNTIMES:
        raise ServiceError(f"service must be one of {', '.join([*SERVICES, *RUNTIMES])}")
    target = normalize_target(body.get("target") or default_target(raw), root)
    if service in RUNTIMES:
        if target["method"] != "ssh":
            raise ServiceError(f"{RUNTIMES[service]['label']} is installed on an SSH server; set target.method: ssh")
        return {"key": key, "service": service, "kind": "runtime", "target": target}
    spec = SERVICES[service]
    if key in load_state(root)["services"] and not body.get("replace"):
        raise ServiceError(f"{key!r} already exists; remove it first or pick another key")
    role = str(body.get("role") or spec["roles"][0])
    if role not in spec["roles"]:
        raise ServiceError(f"{spec['label']} can be used as {', '.join(spec['roles'])}, not {role!r}")
    try:
        port = int(body.get("port") or spec["port"])
    except (TypeError, ValueError):
        raise ServiceError("port must be a number") from None
    if not 1 <= port <= 65535:
        raise ServiceError("port must be between 1 and 65535")
    image = str(body.get("image") or spec["image"]).strip()
    if not _IMAGE.match(image):
        raise ServiceError("image must be a Docker image reference such as redis:7-alpine")
    address = str(body.get("address") or "").strip()
    if address and (not _HOST.match(address) or address.startswith("-")):
        raise ServiceError("address must be a host name or IP address")
    if not address:
        address = target["host"] if target["method"] == "ssh" else "127.0.0.1"
    bind = str(body.get("bind") or ("0.0.0.0" if target["method"] == "ssh" else "127.0.0.1"))  # noqa: S104
    if bind not in ("0.0.0.0", "127.0.0.1") and not re.match(r"^[0-9.]{7,15}$", bind):  # noqa: S104
        raise ServiceError("bind must be 0.0.0.0, 127.0.0.1 or an IPv4 address of the server")
    attach = body.get("attach") or []
    if isinstance(attach, str):
        attach = [attach]
    if not isinstance(attach, list) or not all(isinstance(a, str) and _NAME.match(a) for a in attach):
        raise ServiceError("attach must be a list of function names")
    project = str((raw or {}).get("service") or "")
    return {
        "project": project,
        "key": key,
        "service": service,
        "kind": "service",
        "role": role,
        "target": target,
        "port": port,
        "image": image,
        "address": address,
        "bind": bind,
        "attach": list(attach),
        "container": _container_name(project, key),
        "volume": _container_name(project, key) + "-data" if spec["data"] else "",
    }


def _connection(req: Dict[str, Any], password: str) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
    """(resource entry, env file values, notes) for a service used in ``req['role']``."""
    key, service, role = req["key"], req["service"], req["role"]
    addr, port = req["address"], req["port"]
    prefix = _var_prefix(key)
    user = "pskit"
    database = re.sub(r"[^A-Za-z0-9_]", "_", key)
    env: Dict[str, str] = {}
    notes: List[str] = []
    res: Dict[str, Any] = {"type": ROLE_TYPE.get(role, role), "external": True}
    if service in ("redis", "valkey"):
        env[f"{prefix}_URL"] = f"redis://:{password}@{addr}:{port}/0"
        env[f"{prefix}_PASSWORD"] = password
        if role in ("queue", "stream"):
            driver = "redis" if role == "queue" else "redis_stream"
            res["options"] = {"driver": driver, "url_env": f"{prefix}_URL", "key": key}
        else:
            res["connection"] = {
                "host": addr,
                "port": port,
                "url_env": f"{prefix}_URL",
                "password_env": f"{prefix}_PASSWORD",
            }
    elif service == "rabbitmq":
        env[f"{prefix}_URL"] = f"amqp://{user}:{password}@{addr}:{port}/%2F"
        env[f"{prefix}_PASSWORD"] = password
        res["options"] = {
            "driver": "custom",
            "class": "pskit_drivers.rabbitmq_queue:RabbitQueue",
            "url_env": f"{prefix}_URL",
            "queue": key,
        }
        res["connection"] = {"management_url": f"http://{addr}:15672", "user": user}
        notes.append("added pskit_drivers/rabbitmq_queue.py to your code and pika to requirements.txt")
    elif service == "kafka":
        project = re.sub(r"[^A-Za-z0-9_.-]", "-", str(req.get("project") or "pskit"))
        res["options"] = {
            "driver": "custom",
            "class": "pskit_drivers.kafka_queue:KafkaQueue",
            "bootstrap_servers": f"{addr}:{port}",
            "topic": key,
            "group_id": f"{project}-{key}",
        }
        notes.append("added pskit_drivers/kafka_queue.py to your code and kafka-python to requirements.txt")
    elif service == "nats":
        env[f"{prefix}_URL"] = f"nats://{user}:{password}@{addr}:{port}"
        env[f"{prefix}_PASSWORD"] = password
        res["options"] = {
            "driver": "custom",
            "class": "pskit_drivers.nats_queue:NatsQueue",
            "url_env": f"{prefix}_URL",
            "stream": re.sub(r"[^A-Za-z0-9_-]", "_", key),
            "subject": f"{key}.jobs",
            "durable": "pskit",
        }
        res["connection"] = {"monitor_url": f"http://{addr}:8222"}
        notes.append("added pskit_drivers/nats_queue.py to your code and nats-py to requirements.txt")
    elif service == "elasticmq":
        res["options"] = {
            "driver": "sqs",
            "queue_url": f"http://{addr}:{port}/000000000000/{key}",
            "endpoint": f"http://{addr}:{port}",
            "region": "us-east-1",
            # ElasticMQ accepts any credentials; these are not secrets.
            "access_key": "pskit",
            "secret_key": "pskit",
        }
        res["connection"] = {"ui_url": f"http://{addr}:9325"}
    elif service in ("postgres", "mysql", "mariadb"):
        scheme = "postgresql" if service == "postgres" else "mysql"
        env[f"{prefix}_URL"] = f"{scheme}://{user}:{password}@{addr}:{port}/{database}"
        env[f"{prefix}_PASSWORD"] = password
        res["connection"] = {
            "host": addr,
            "port": port,
            "database": database,
            "user": user,
            "password_env": f"{prefix}_PASSWORD",
            "url_env": f"{prefix}_URL",
        }
    elif service == "mongodb":
        env[f"{prefix}_URL"] = f"mongodb://{user}:{password}@{addr}:{port}/?authSource=admin"
        env[f"{prefix}_PASSWORD"] = password
        res["connection"] = {
            "host": addr,
            "port": port,
            "user": user,
            "password_env": f"{prefix}_PASSWORD",
            "url_env": f"{prefix}_URL",
        }
    elif service == "minio":
        bucket = _bucket_name(key)
        env[f"{prefix}_ACCESS_KEY"] = user
        env[f"{prefix}_SECRET_KEY"] = password
        if bucket != key:
            res["name"] = bucket
        res["options"] = {
            "driver": "minio",
            "endpoint": f"http://{addr}:{port}",
            "bucket": bucket,
            "access_key_env": f"{prefix}_ACCESS_KEY",
            "secret_key_env": f"{prefix}_SECRET_KEY",
        }
        res["connection"] = {"console_url": f"http://{addr}:9001"}
    elif service == "memcached":
        res["connection"] = {"host": addr, "port": port}
    elif service == "mailpit":
        res["connection"] = {"smtp_host": addr, "smtp_port": port, "web_url": f"http://{addr}:8025"}
    return res, env, notes


def _fill(text: str, values: Dict[str, str]) -> str:
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def _values(req: Dict[str, Any], password: str = "") -> Dict[str, str]:
    return {
        "password": password,
        "user": "pskit",
        "database": re.sub(r"[^A-Za-z0-9_]", "_", req["key"]),
        "bucket": _bucket_name(req["key"]),
        "name": req["key"],
        "address": str(req.get("address") or "127.0.0.1"),
        "port": str(req.get("port") or ""),
    }


def run_args(req: Dict[str, Any], password: str) -> List[str]:
    """``docker run`` arguments for a service request (password included)."""
    spec = SERVICES[req["service"]]
    values = _values(req, password)
    argv = ["run", "-d", "--name", req["container"], "--restart", "unless-stopped"]
    argv += ["--label", f"pskit.service={req['service']}", "--label", f"pskit.key={req['key']}"]
    bind = req["bind"]
    argv += ["-p", f"{bind}:{req['port']}:{spec['port']}"]
    for extra in spec["extra_ports"]:
        argv += ["-p", f"{bind}:{extra}:{extra}"]
    for name, template in spec["env"].items():
        argv += ["-e", f"{name}={_fill(template, values)}"]
    if spec["data"]:
        argv += ["-v", f"{req['volume']}:{spec['data']}"]
    argv += list(spec["run_args"])
    argv.append(req["image"])
    argv += [_fill(part, values) for part in spec["command"]]
    return argv


def create(
    root: Path,
    body: Dict[str, Any],
    raw: Optional[Dict[str, Any]],
    emit: Emit,
    *,
    run: Optional[RunFn] = None,
    wait_seconds: float = 90,
) -> Dict[str, Any]:
    """Start the service (or install the runtime) and record it. Returns the state entry."""
    req = check_request(body, root, raw)
    sh = _Runner(emit, run)
    target = req["target"]
    emit(f"Creating {req['service']} as {req['key']!r} on {describe_target(target)}")
    if req["kind"] == "runtime":
        _install_runtime(req["service"], target, sh)
        entry = {
            "key": req["key"],
            "service": req["service"],
            "kind": "runtime",
            "target": target,
            "created_at": _now(),
        }
        state = load_state(root)
        state["services"][req["key"]] = entry
        save_state(root, state)
        return entry
    spec = SERVICES[req["service"]]
    _ensure_docker(target, sh, install=bool(body.get("install_docker", True)))
    password = secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    sh(docker_argv(target, ["rm", "-f", req["container"]]), check=False, show=False)
    sh(docker_argv(target, ["pull", req["image"]]))
    if req["volume"]:
        sh(docker_argv(target, ["volume", "create", req["volume"]]), show=False)
    sh(docker_argv(target, run_args(req, password)))
    _wait_ready(req, spec, target, sh, wait_seconds, emit)
    if spec["init"]:
        sh(docker_argv(target, ["exec", req["container"], "sh", "-c", _fill(spec["init"], _values(req))]))
    if req["service"] == "elasticmq":
        _elasticmq_queue(req, emit)
    resource, env_values, notes = _connection(req, password)
    if req["service"] in DRIVER_TEMPLATES:
        _add_driver(root, raw, req["service"])
    env_file = env_file_of(raw)
    if env_values:
        update_env_file(root / env_file, dict(env_values))
        _ensure_ignored(root, env_file)
        notes.append(f"credentials written to {env_file} (loaded by the host; keep it out of git)")
    entry = {
        "key": req["key"],
        "service": req["service"],
        "kind": "service",
        "role": req["role"],
        "target": target,
        "image": req["image"],
        "container": req["container"],
        "volume": req["volume"],
        "port": req["port"],
        "address": req["address"],
        "bind": req["bind"],
        "attach": req["attach"],
        "env_file": env_file,
        "env": env_values,
        "resource": resource,
        "notes": notes,
        "created_at": _now(),
    }
    state = load_state(root)
    state["services"][req["key"]] = entry
    save_state(root, state)
    for note in notes:
        emit(note)
    emit(f"{spec['label']} is ready at {req['address']}:{req['port']}")
    return entry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_docker(target: Dict[str, Any], sh: _Runner, *, install: bool) -> None:
    if target.get("method") != "ssh":
        out = sh(["docker", "version", "--format", "{{.Server.Version}}"], check=False, show=False)
        if not re.search(r"\d+\.\d+", out):
            raise ServiceError(
                "Docker is not running on this machine. Start Docker Desktop (or the docker service) and retry."
            )
        return
    probe = "command -v docker >/dev/null 2>&1 && echo present || echo missing"
    out = sh(_ssh_argv(target, probe), show=False)
    if "present" in out:
        return
    if not install:
        raise ServiceError(f"Docker is not installed on {target['host']}; create the `docker` runtime first")
    _install_runtime("docker", target, sh)


def _install_runtime(name: str, target: Dict[str, Any], sh: _Runner) -> None:
    from ..providers.onprem_remote import _docker_install_block, _docker_ready_block, _docker_shell_header

    header = _docker_shell_header()
    if name == "docker":
        script = header + _docker_install_block() + _docker_ready_block() + "dk --version\n"
    else:
        script = header + _RUNTIME_INSTALL[name]
    # A script file: multi-line SSH commands are re-quoted by Windows OpenSSH.
    import tempfile

    from ..providers.onprem_remote import _ssh_options, _target

    ssh = {
        "host": target["host"],
        "user": target["user"],
        "port": target.get("port") or 22,
        "identity_file": target.get("identity_file") or "",
        "known_hosts": target.get("known_hosts") or "accept-new",
    }
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"pskit-install-{name}.sh"
        local.write_bytes(script.encode("utf-8"))
        remote = f"/tmp/pskit-install-{name}.sh"  # noqa: S108 - removed right after
        sh(["scp", *_ssh_options(ssh, port_flag="-P"), str(local), f"{_target(ssh)}:{remote}"])
        sh(_ssh_argv(target, f"sh {remote}; code=$?; rm -f {remote}; exit $code"))


def _wait_ready(
    req: Dict[str, Any], spec: Dict[str, Any], target: Dict[str, Any], sh: _Runner, timeout: float, emit: Emit
) -> None:
    started = time.monotonic()
    deadline = started + timeout
    health = _fill(spec["health"], _values(req))
    emit("waiting for the service to accept connections...")
    while True:
        state = sh(
            docker_argv(target, ["inspect", "-f", "{{.State.Status}}", req["container"]]), check=False, show=False
        ).strip()
        if state and state not in ("running", "created", "restarting"):
            logs = sh(docker_argv(target, ["logs", "--tail", "30", req["container"]]), check=False, show=False)
            raise ServiceError(f"the container stopped ({state}). Last log lines:\n{logs.strip()}")
        if state == "running":
            if health:
                code, _out = sh.run_fn(docker_argv(target, ["exec", req["container"], "sh", "-c", health]), None)
                if code == 0:
                    return
            elif _tcp_open(req["address"], req["port"]):
                return
            elif target.get("method") == "ssh" and time.monotonic() - started > 10:
                # The port may be firewalled from here but open to the functions' server.
                emit(f"note: {req['address']}:{req['port']} is not reachable from this machine; check the firewall")
                return
        if time.monotonic() > deadline:
            raise ServiceError(f"{spec['label']} did not become ready within {int(timeout)} s")
        time.sleep(2)


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _elasticmq_queue(req: Dict[str, Any], emit: Emit) -> None:
    """Create the queue through the SQS query API (ElasticMQ needs no signature)."""
    from urllib.parse import urlencode
    from urllib.request import urlopen

    url = f"http://{req['address']}:{req['port']}/?" + urlencode(
        {"Action": "CreateQueue", "QueueName": req["key"], "Version": "2012-11-05"}
    )
    for _ in range(15):
        try:
            with urlopen(url, timeout=5) as resp:  # noqa: S310 - the service just created
                resp.read()
            emit(f"created queue {req['key']}")
            return
        except OSError:
            time.sleep(2)
    emit(f"note: could not create queue {req['key']} from this machine; create it in the ElasticMQ UI (port 9325)")


# service -> (module written to <source>/pskit_drivers/, requirement added to requirements.txt)
DRIVER_TEMPLATES = {
    "rabbitmq": ("rabbitmq_queue", "pika>=1.3"),
    "kafka": ("kafka_queue", "kafka-python>=2.0.3"),
    "nats": ("nats_queue", "nats-py>=2.6"),
}


def _add_driver(root: Path, raw: Optional[Dict[str, Any]], service: str) -> None:
    """Copy the driver class into the app (once) and add its client library to requirements."""
    module, requirement = DRIVER_TEMPLATES[service]
    package = ((raw or {}).get("package") or {}) if isinstance((raw or {}).get("package"), dict) else {}
    source = root / str(package.get("source") or ".")
    folder = source / "pskit_drivers"
    folder.mkdir(parents=True, exist_ok=True)
    init = folder / "__init__.py"
    if not init.exists():
        init.write_bytes(b'"""Queue drivers added by pskit for on-premises services."""\n')
    template = Path(__file__).with_name("onprem_templates") / f"{module}.py.txt"
    target = folder / f"{module}.py"
    if not target.exists():
        target.write_bytes(template.read_bytes().replace(b"\r\n", b"\n"))
    reqs = root / str(package.get("requirements") or "requirements.txt")
    text = reqs.read_text("utf-8") if reqs.is_file() else ""
    name = re.split(r"[<>=!~\[ ]", requirement, maxsplit=1)[0]
    if not re.search(rf"^\s*{re.escape(name)}\b", text, re.MULTILINE | re.IGNORECASE):
        reqs.write_bytes((text.rstrip("\n") + ("\n" if text.strip() else "") + requirement + "\n").encode("utf-8"))


# ----------------------------------------------------------------------------- inspect / back up / remove


def _entry(root: Path, key: str) -> Dict[str, Any]:
    entry = load_state(root)["services"].get(key)
    if not isinstance(entry, dict):
        raise ServiceError(f"no on-prem service {key!r} was created from this project")
    return entry


def public_entry(entry: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    """The state entry without secrets (unless ``reveal``), for the UI and ``--json``."""
    out = copy.deepcopy(entry)
    if not reveal and isinstance(out.get("env"), dict):
        out["env"] = {k: ("***" if any(s in k for s in ("PASSWORD", "SECRET", "URL")) else v) for k, v in out["env"].items()}
    out["target_label"] = describe_target(out.get("target") or {})
    return out


def list_services(root: Path) -> List[Dict[str, Any]]:
    return [public_entry(e) for _k, e in sorted(load_state(root)["services"].items()) if isinstance(e, dict)]


def status(root: Path, key: str, *, run: Optional[RunFn] = None, reveal: bool = False) -> Dict[str, Any]:
    entry = _entry(root, key)
    lines: List[str] = []
    sh = _Runner(lines.append, run)
    target = entry.get("target") or {"method": "local"}
    out = public_entry(entry, reveal)
    if entry.get("kind") == "runtime":
        check = RUNTIMES[entry["service"]]["check"]
        code, text = sh.run_fn(_ssh_argv(target, check), None)
        out.update({"state": "installed" if code == 0 else "missing", "version": text.strip().splitlines()[-1:]})
        return out
    fmt = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.StartedAt}}|{{.Config.Image}}"
    code, text = sh.run_fn(docker_argv(target, ["inspect", "-f", fmt, entry["container"]]), None)
    if code:
        out.update({"state": "missing", "error": text.strip()[-300:]})
        return out
    parts = (text.strip().splitlines() or [""])[-1].split("|")
    parts += [""] * (4 - len(parts))
    out.update({"state": parts[0], "health": parts[1] or None, "started_at": parts[2], "running_image": parts[3]})
    spec = SERVICES.get(entry["service"]) or {}
    if parts[0] == "running" and spec.get("health"):
        health = _fill(spec["health"], _values({**entry, "key": key}))
        code, _ = sh.run_fn(docker_argv(target, ["exec", entry["container"], "sh", "-c", health]), None)
        out["ready"] = code == 0
    elif parts[0] == "running":
        out["ready"] = _tcp_open(str(entry.get("address")), int(entry.get("port") or 0))
    return out


def logs(root: Path, key: str, *, tail: int = 200, run: Optional[RunFn] = None) -> str:
    entry = _entry(root, key)
    if entry.get("kind") == "runtime":
        return ""
    sh = _Runner(lambda _l: None, run)
    _code, text = sh.run_fn(docker_argv(entry["target"], ["logs", "--tail", str(int(tail)), entry["container"]]), None)
    return text


def backup(root: Path, key: str, emit: Emit, *, run: Optional[RunFn] = None, dest: Optional[Path] = None) -> Path:
    """Stream a dump from the container into ``.pskit/backups/<key>-<time>.<ext>``."""
    entry = _entry(root, key)
    spec = SERVICES.get(str(entry.get("service")))
    if not spec or not spec["backup"]:
        raise ServiceError(f"{entry.get('service')} keeps nothing to back up")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = dest or (root / BACKUP_DIR / f"{key}-{stamp}.{spec['backup_ext']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    sh = _Runner(emit, run)
    emit(f"Backing up {key} ({spec['label']}) from {describe_target(entry['target'])}")
    argv = docker_argv(entry["target"], ["exec", entry["container"], "sh", "-c", spec["backup"]])
    try:
        sh(argv, stdout=path)
    except ServiceError:
        path.unlink(missing_ok=True)
        raise
    size = path.stat().st_size if path.is_file() else 0
    if size == 0:
        path.unlink(missing_ok=True)
        raise ServiceError("the backup command produced no data")
    emit(f"saved {path} ({size} bytes)")
    state = load_state(root)
    item = state["services"].get(key)
    if isinstance(item, dict):
        item.setdefault("backups", []).append({"path": str(path), "size": size, "at": _now()})
        del item["backups"][:-20]
        save_state(root, state)
    return path


def remove(
    root: Path, key: str, emit: Emit, *, delete_data: bool = False, run: Optional[RunFn] = None
) -> Dict[str, Any]:
    """Stop and delete the container (and its volume with ``delete_data``) and forget it."""
    entry = _entry(root, key)
    sh = _Runner(emit, run)
    if entry.get("kind") != "runtime":
        sh(docker_argv(entry["target"], ["rm", "-f", entry["container"]]), check=False)
        if delete_data and entry.get("volume"):
            sh(docker_argv(entry["target"], ["volume", "rm", entry["volume"]]), check=False)
        elif entry.get("volume"):
            emit(f"kept the data volume {entry['volume']} (delete it with --delete-data)")
        env_file = root / str(entry.get("env_file") or DEFAULT_ENV_FILE)
        if entry.get("env") and env_file.is_file():
            update_env_file(env_file, {name: None for name in entry["env"]})
    else:
        emit(f"{entry['service']} stays installed on {describe_target(entry['target'])}; pskit only forgets it")
    state = load_state(root)
    state["services"].pop(key, None)
    save_state(root, state)
    return entry


# ----------------------------------------------------------------------------- pskit.yml


def _event_kind(resource_type: str) -> Optional[str]:
    return resource_type if resource_type in ("queue", "storage", "topic") else None


def apply_to_raw(raw: Dict[str, Any], entry: Dict[str, Any], attach: Optional[List[str]] = None) -> Dict[str, Any]:
    """``pskit.yml`` (as a dict) with the service's resource, env file and triggers added.

    When the main provider is not ``onprem``, everything goes under ``providers.onprem``
    so the other clouds are unaffected.
    """
    out = copy.deepcopy(raw)
    if entry.get("kind") == "runtime" or not isinstance(entry.get("resource"), dict):
        return out
    key = str(entry["key"])
    resource = copy.deepcopy(entry["resource"])
    onprem_main = ((out.get("provider") or {}).get("name")) == "onprem"
    scope = out if onprem_main else out.setdefault("providers", {}).setdefault("onprem", {})
    scope.setdefault("resources", {})[key] = resource
    if entry.get("env"):
        # `scope` is the whole file when onprem is the main provider, else providers.onprem.
        options = scope.setdefault("provider", {}).setdefault("options", {})
        options.setdefault("env_file", str(entry.get("env_file") or DEFAULT_ENV_FILE))
    kind = _event_kind(str(resource.get("type")))
    names = attach if attach is not None else list(entry.get("attach") or [])
    if kind:
        for fn in names:
            base_fn = (out.get("functions") or {}).get(fn)
            if not isinstance(base_fn, dict):
                raise ServiceError(f"unknown function {fn!r}")
            if onprem_main:
                events = base_fn.setdefault("events", [])
            else:
                override = scope.setdefault("functions", {}).setdefault(fn, {})
                if "events" not in override:
                    override["events"] = copy.deepcopy(base_fn.get("events") or [])
                events = override["events"]
            if not any(isinstance(e, dict) and (e.get(kind) or {}).get("resource") == key for e in events):
                events.append({kind: {"resource": key}})
    return out


def remove_from_raw(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    """``pskit.yml`` without resource ``key`` and the triggers that use it."""
    out = copy.deepcopy(raw)
    scopes = [out, ((out.get("providers") or {}).get("onprem") or {})]
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        resources = scope.get("resources")
        if isinstance(resources, dict):
            resources.pop(key, None)
            if not resources and scope is not out:
                scope.pop("resources", None)
        for fn in (scope.get("functions") or {}).values():
            if isinstance(fn, dict) and isinstance(fn.get("events"), list):
                fn["events"] = [
                    e
                    for e in fn["events"]
                    if not (isinstance(e, dict) and any(isinstance(v, dict) and v.get("resource") == key for v in e.values()))
                ]
    return out


def snippet(entry: Dict[str, Any]) -> str:
    """The YAML a user would paste by hand."""
    import yaml

    if entry.get("kind") == "runtime":
        return ""
    return yaml.safe_dump({"resources": {entry["key"]: entry["resource"]}}, sort_keys=False)


if __name__ == "__main__":  # pragma: no cover - manual check of the catalog
    json.dump(catalog(), sys.stdout, indent=2)
