"""``pskit studio``: build a project in the browser instead of writing YAML by hand.

Serves ``studio.html`` (a single-file React build of ``dashboard-ui/``) and the
JSON API it uses. The studio can write files and run deploys, so every API call
needs the session token printed on start (``x-studio-token``). The server also
binds loopback by default, checks the ``Host`` header (DNS rebinding), requires
a same-origin ``Origin`` and a JSON body on writes, and confines file access to
the project.
"""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .._version import __version__
from ..dashboard.server import _allowed_hosts, is_loopback
from . import devenv, files, project, system
from .discover import discover
from .jobs import JobManager
from .project import StudioError
from .vscode import VSCode

PAGE = Path(__file__).with_name("studio.html")
MAX_BODY = 2 * 1024 * 1024
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self' data:; frame-src http://127.0.0.1:* http://localhost:*; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_MUTATING = ("POST", "PUT", "DELETE")


class Studio:
    """State shared by request handlers."""

    def __init__(self, root: Path, token: Optional[str] = None, vscode_image: Optional[str] = None) -> None:
        self.root = Path(root).resolve()
        self.token = token if token is not None else secrets.token_urlsafe(24)
        self.jobs = JobManager(self.root)
        self.vscode = VSCode(self.root, image=vscode_image)
        self._meta: Optional[Dict[str, Any]] = None

    def meta(self) -> Dict[str, Any]:
        if self._meta is None:
            from .meta import build_meta

            self._meta = build_meta()
        return self._meta

    def page(self) -> bytes:
        try:
            return PAGE.read_bytes()
        except OSError:
            return b"<!doctype html><title>pskit studio</title><p>studio.html is missing from this install.</p>"

    def raw_config(self) -> Dict[str, Any]:
        path = project.config_path(self.root)
        if path is None:
            return {}
        raw, _ = project.parse_text(path.read_text("utf-8"))
        return raw or {}


def _param(params: Dict[str, List[str]], name: str) -> Optional[str]:
    raw = (params.get(name) or [""])[0].strip()
    return raw or None


def _int(params: Dict[str, List[str]], name: str, default: int) -> int:
    raw = _param(params, name)
    if raw is None:
        return default
    if not raw.isdigit():
        raise StudioError(400, f"{name} must be a non-negative integer")
    return int(raw)


def _obj(body: Any) -> Dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise StudioError(400, "request body must be a JSON object")
    return body


def make_handler(app: Studio) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pskit-studio"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return None

        # ------------------------------------------------------------ io
        def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("x-frame-options", "DENY")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _length(self) -> int:
            try:
                length = int(self.headers.get("content-length") or "0")
            except ValueError:
                raise StudioError(400, "invalid content-length") from None
            if length < 0 or length > MAX_BODY:
                raise StudioError(413, f"request body must be at most {MAX_BODY} bytes")
            return length

        def _body(self) -> Any:
            length = self._length()
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise StudioError(400, "request body must be JSON") from None

        def _drain(self) -> None:
            # Unread request body: read a bounded amount, then close, so the client gets the error
            # instead of a reset connection.
            if (self.headers.get("content-length") or "0").strip() in ("", "0"):
                return
            self.close_connection = True
            try:
                self.connection.settimeout(0.5)
                left = 8 * 1024 * 1024  # enough for any realistic oversized request
                while left > 0:
                    chunk = self.rfile.read1(min(left, 8192))  # type: ignore[attr-defined]
                    if not chunk:
                        break
                    left -= len(chunk)
            except OSError:
                pass

        # ------------------------------------------------------------ checks
        def _host_ok(self) -> bool:
            allowed = getattr(self.server, "allowed_hosts", None)
            return allowed is None or (self.headers.get("host") or "").strip().lower() in allowed

        def _token_ok(self) -> bool:
            sent = self.headers.get("x-studio-token") or ""
            return bool(app.token) and secrets.compare_digest(sent.encode(), app.token.encode())

        def _write_problem(self) -> Optional[Tuple[int, str]]:
            origin = self.headers.get("origin")
            if origin is not None:
                host = (self.headers.get("host") or "").strip().lower()
                parsed = urlsplit(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != host:
                    return 403, "cross-origin request refused"
            if self.command in ("POST", "PUT"):
                ctype = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    return 415, "requests must use content-type: application/json"
            return None

        # ------------------------------------------------------------ verbs
        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def do_PUT(self) -> None:  # noqa: N802
            self._handle()

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle()

        def _handle(self) -> None:
            method = "GET" if self.command == "HEAD" else self.command
            if not self._host_ok():
                self._drain()
                return self._json(421, {"error": "unexpected Host header"})
            url = urlsplit(self.path)
            try:
                if not url.path.startswith("/api/"):
                    if method != "GET":
                        raise StudioError(405, "method not allowed")
                    if url.path in ("/", "/index.html", "/studio.html"):
                        return self._send(
                            200, app.page(), "text/html; charset=utf-8", {"content-security-policy": _CSP}
                        )
                    raise StudioError(404, "not found")
                if url.path == "/api/health" and method == "GET":
                    return self._json(200, {"ok": True, "version": __version__, "root": str(app.root)})
                if not self._token_ok():
                    raise StudioError(401, "studio token required", code="token")
                if method in _MUTATING:
                    problem = self._write_problem()
                    if problem:
                        raise StudioError(*problem)
                status, payload = self._api(method, url.path, parse_qs(url.query, keep_blank_values=True))
                self._json(status, payload)
            except StudioError as exc:
                if exc.status in (401, 403, 413, 415):
                    self._drain()
                self._json(exc.status, {"error": exc.message, **exc.extra})
            except Exception as exc:  # noqa: BLE001 - last-resort JSON error
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        # ------------------------------------------------------------ routes
        def _api(self, method: str, path: str, params: Dict[str, List[str]]) -> Tuple[int, Any]:
            parts = [unquote(p) for p in path.split("/") if p][1:]
            root = app.root
            if parts == ["meta"] and method == "GET":
                return 200, app.meta()
            if parts == ["project"]:
                if method == "GET":
                    return 200, project.get_project(root)
                if method == "PUT":
                    body = _obj(self._body())
                    return 200, project.save_project(root, body.get("raw"), body.get("mtime"), bool(body.get("force")))
            if parts == ["project", "validate"] and method == "POST":
                body = _obj(self._body())
                return 200, project.validate(root, body.get("raw"), body.get("provider"), body.get("stage"))
            if parts == ["project", "parse"] and method == "POST":
                body = _obj(self._body())
                text = body.get("text")
                if not isinstance(text, str):
                    raise StudioError(400, "text must be a string")
                raw, issues = project.parse_text(text)
                return 200, {"raw": raw, "issues": issues}
            if parts == ["project", "init"] and method == "POST":
                body = _obj(self._body())
                return 201, project.init_project(
                    root,
                    str(body.get("service") or ""),
                    str(body.get("provider") or ""),
                    str(body.get("template") or "http"),
                    bool(body.get("force")),
                    str(body.get("language") or "python"),
                )
            if parts == ["files", "tree"] and method == "GET":
                return 200, files.tree(root)
            if parts == ["files"]:
                if method == "GET":
                    return 200, files.read_file(root, _param(params, "path"))
                if method == "PUT":
                    body = _obj(self._body())
                    return 200, files.write_file(
                        root, body.get("path"), body.get("text"), body.get("mtime"), bool(body.get("force"))
                    )
            if parts == ["handlers", "ensure"] and method == "POST":
                body = _obj(self._body())
                events = body.get("events") or []
                if not isinstance(events, list):
                    raise StudioError(400, "events must be a list")
                return 200, project.ensure_handler(root, str(body.get("handler") or ""), [str(e) for e in events])
            if parts == ["discover"] and method == "GET":
                provider = _param(params, "provider") or str((app.raw_config().get("provider") or {}).get("name") or "")
                return 200, discover(
                    root, provider, _param(params, "kind") or "", _param(params, "region"), app.raw_config()
                )
            if parts == ["env"] and method == "GET":
                reqs = str((app.raw_config().get("package") or {}).get("requirements") or "requirements.txt")
                return 200, devenv.status(root, reqs)
            if parts == ["env", "setup"] and method == "POST":
                body = _obj(self._body())
                raw = app.raw_config()
                runtime = str((raw.get("provider") or {}).get("runtime") or "")
                language = str(body.get("language") or ("node" if runtime.startswith("nodejs") else "python"))
                if language not in ("python", "node"):
                    raise StudioError(400, "language must be python or node")
                try:
                    steps = devenv.plan(
                        root,
                        language=language,
                        python=str(body["python"]) if body.get("python") else None,
                        recreate=bool(body.get("recreate")),
                        requirements=str((raw.get("package") or {}).get("requirements") or "requirements.txt"),
                    )
                except ValueError as exc:
                    raise StudioError(400, str(exc)) from None
                label = "npm install" if language == "node" else "Create .venv and install requirements"
                job = app.jobs.run_callable("env", lambda j: devenv.run_steps(root, steps, j.emit), label=label)
                return 202, {"job_id": job.id}
            if parts == ["fs"] and method == "GET":
                return 200, system.browse(_param(params, "path"))
            if parts == ["setup"] and method == "GET":
                provider = _param(params, "provider") or str((app.raw_config().get("provider") or {}).get("name") or "")
                return 200, system.setup_status(provider, app.raw_config(), root)
            if parts == ["setup", "env"] and method == "POST":
                body = _obj(self._body())
                value = body.get("value")
                return 200, system.set_env(str(body.get("name") or ""), None if value is None else str(value))
            if parts == ["setup", "aws-profile"] and method == "POST":
                return 200, system.aws_profile(_obj(self._body()))
            if parts == ["setup", "login"] and method == "POST":
                body = _obj(self._body())
                action = str(body.get("action") or "")
                job = app.jobs.run_process("login", system.login_command(action), label=f"login: {action}")
                return 202, {"job_id": job.id}
            if parts == ["setup", "ssh-test"] and method == "POST":
                body = _obj(self._body())
                job = app.jobs.run_process("ssh-test", system.ssh_command(body.get("deploy")), label="SSH test")
                return 202, {"job_id": job.id}
            if parts and parts[0] == "logs":
                provider = _param(params, "provider") or str((app.raw_config().get("provider") or {}).get("name") or "")
                stage = _param(params, "stage") or str((app.raw_config().get("provider") or {}).get("stage") or "dev")
                if parts == ["logs"] and method == "GET":
                    return 200, system.logs_query(
                        root,
                        provider,
                        stage,
                        since=_int(params, "since", 0),
                        limit=max(1, min(1000, _int(params, "limit", 200))),
                        level=_param(params, "level"),
                        function=_param(params, "function"),
                        q=_param(params, "q"),
                        source=_param(params, "source"),
                    )
                if parts == ["logs", "streams"] and method == "GET":
                    return 200, system.logs_streams(root, provider, stage, _param(params, "function"))
                if parts == ["logs", "delete"] and method == "POST":
                    return 200, system.logs_delete(root, provider, stage, _obj(self._body()))
            if parts == ["jobs"]:
                if method == "GET":
                    return 200, {"jobs": app.jobs.list()}
                if method == "DELETE":
                    return 200, app.jobs.clear()
                if method == "POST":
                    body = _obj(self._body())
                    args = body.get("args") or {}
                    if not isinstance(args, dict):
                        raise StudioError(400, "args must be an object")
                    job = app.jobs.start(str(body.get("command") or ""), body.get("provider"), body.get("stage"), args)
                    return 202, {"job_id": job.id}
            if len(parts) == 2 and parts[0] == "jobs" and method == "DELETE":
                return 200, app.jobs.clear(parts[1])
            if len(parts) == 2 and parts[0] == "jobs" and method == "GET":
                since = _param(params, "since") or "0"
                if not since.isdigit():
                    raise StudioError(400, "since must be an integer")
                return 200, app.jobs.get(parts[1]).snapshot(int(since))
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel" and method == "POST":
                self._body()
                return 200, app.jobs.cancel(parts[1])
            if parts == ["vscode"] and method == "GET":
                return 200, app.vscode.status()
            if parts == ["vscode", "start"] and method == "POST":
                body = _obj(self._body())
                if body.get("image"):
                    image = str(body["image"])
                    if any(c.isspace() for c in image) or image.startswith("-"):
                        raise StudioError(400, "invalid image name")
                    app.vscode.image = image
                try:
                    app.vscode.set_runtimes(
                        str(body["python"]) if body.get("python") else None,
                        str(body["node"]) if body.get("node") else None,
                    )
                except ValueError as exc:
                    raise StudioError(400, str(exc)) from None
                job = app.jobs.run_callable("vscode", app.vscode.start, label="VS Code (code-server)")
                app.vscode.log_job = job.id
                return 202, {"job_id": job.id}
            if parts == ["vscode", "stop"] and method == "POST":
                self._body()
                stopped = app.vscode.stop()
                state = app.vscode.status()  # the UI renders the panel from the full state
                state.update(stopped)
                return 200, state
            raise StudioError(404 if method == "GET" else 405, "unknown API endpoint")

    return Handler


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    allowed_hosts: Optional[set] = None
    studio: Optional[Studio] = None


def make_server(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8770,
    token: Optional[str] = None,
    vscode_image: Optional[str] = None,
) -> StudioHTTPServer:
    app = Studio(root, token=token, vscode_image=vscode_image)
    httpd = StudioHTTPServer((host, port), make_handler(app))
    httpd.allowed_hosts = _allowed_hosts(host, httpd.server_address[1])
    httpd.studio = app
    return httpd


def studio_url(httpd: StudioHTTPServer) -> str:
    host, port = httpd.server_address[0], httpd.server_address[1]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else str(host)  # noqa: S104
    token = httpd.studio.token if httpd.studio else ""
    return f"http://{shown}:{port}/#token={token}"


__all__ = ["Studio", "StudioHTTPServer", "is_loopback", "make_server", "studio_url"]
