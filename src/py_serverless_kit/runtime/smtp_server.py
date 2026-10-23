"""The on-prem host's SMTP server: receives mail for your domains and hands each message to functions.

Standard library only (Python 3.9+). One server per ``smtp`` resource (``pskit_host.json`` -> ``smtp``)::

    {"name": "inbound-mail", "port": 2525, "bind": "0.0.0.0", "domains": ["example.com"], "subdomains": true,
     "auth": false, "users": {"relay": "<password>"}, "max_size": 26214400, "tls_cert": "", "tls_key": "",
     "require_tls": false, "allowed_senders": [], "allowed_ips": [], "hostname": "mx.example.com"}

* Only recipients in ``domains`` (and their subdomains, unless ``subdomains: false``) are accepted, so it is never an
  open relay; mail is never forwarded anywhere.
* ``auth: true`` requires SMTP AUTH (PLAIN / LOGIN) before MAIL FROM, for apps and devices that submit mail. Mail
  from the internet (other mail servers delivering to your MX) never authenticates: keep ``auth: false`` there and
  narrow it with ``allowed_senders`` / ``allowed_ips``. AUTH is offered only after STARTTLS, unless ``require_tls``
  is false and there is no certificate (a private network).
* ``tls_cert`` / ``tls_key`` (PEM paths on the host) enable STARTTLS.

Accepted mail is written to ``<data_dir>/smtp/<resource>/`` (``<id>.eml`` + ``<id>.json``) *before* ``250`` is
answered, then delivered to functions in the background; a host restart delivers what was not delivered yet.
"""

from __future__ import annotations

import base64
import fnmatch
import hmac
import ipaddress
import json
import os
import re
import socket
import socketserver
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_MAX_SIZE = 25 * 1024 * 1024
_MAX_LINE = 8192
_MAX_RCPT = 100
_ADDR = re.compile(r"<([^<>]*)>")
_ID = re.compile(r"^[0-9]{14}-[0-9a-f]{8}$")


class MailboxError(KeyError):
    pass


# ----------------------------------------------------------------------------- spool


class Mailbox:
    """``<id>.eml`` (the message as received) and ``<id>.json`` (envelope, status, deliveries) per mail."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _meta(self, ident: str) -> Path:
        if not _ID.match(ident):
            raise MailboxError(f"no mail {ident!r}")
        return self.path / f"{ident}.json"

    def save(self, raw: bytes, envelope: Dict[str, Any]) -> str:
        ident = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
        tmp = self.path / f".{ident}.tmp"
        tmp.write_bytes(raw)
        os.replace(tmp, self.path / f"{ident}.eml")
        self.write(ident, {"id": ident, "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "size": len(raw), "envelope": envelope, "status": "received", "deliveries": {}})
        return ident

    def write(self, ident: str, meta: Dict[str, Any]) -> None:
        path = self._meta(ident)
        tmp = path.with_name(f".{ident}.json.tmp")
        tmp.write_bytes(json.dumps(meta, sort_keys=True, default=str).encode("utf-8"))
        os.replace(tmp, path)

    def meta(self, ident: str) -> Dict[str, Any]:
        try:
            return dict(json.loads(self._meta(ident).read_text("utf-8")))
        except (OSError, ValueError):
            raise MailboxError(f"no mail {ident!r}") from None

    def update(self, ident: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            meta = self.meta(ident)
            meta.update(changes)
            self.write(ident, meta)
            return meta

    def raw(self, ident: str) -> bytes:
        self._meta(ident)
        try:
            return (self.path / f"{ident}.eml").read_bytes()
        except OSError:
            raise MailboxError(f"no mail {ident!r}") from None

    def ids(self) -> List[str]:
        return sorted((p.stem for p in self.path.glob("*.json") if _ID.match(p.stem)), reverse=True)

    def page(self, offset: int = 0, limit: int = 50, status: str = "") -> Dict[str, Any]:
        ids = self.ids()
        items = []
        for ident in ids:
            try:
                meta = self.meta(ident)
            except MailboxError:
                continue
            if status and meta.get("status") != status:
                continue
            items.append(meta)
        return {"items": items[offset:offset + limit], "total": len(items), "offset": offset}

    def delete(self, ident: str) -> bool:
        meta = self._meta(ident)
        found = meta.exists()
        for path in (meta, self.path / f"{ident}.eml"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return found

    def purge(self) -> int:
        count = 0
        for ident in self.ids():
            count += int(self.delete(ident))
        return count


# ----------------------------------------------------------------------------- config


def _ip_allowed(ip: str, allowed: List[str]) -> bool:
    if not allowed:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for item in allowed:
        try:
            if addr in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def recipient_domain_ok(address: str, domains: List[str], subdomains: bool) -> bool:
    host = address.rpartition("@")[2].strip().lower().rstrip(".")
    if not host:
        return False
    if not domains:
        return True
    for domain in domains:
        domain = domain.lower().strip(".")
        if host == domain or (subdomains and host.endswith("." + domain)):
            return True
    return False


# ----------------------------------------------------------------------------- protocol


class _Session(socketserver.StreamRequestHandler):
    server: SmtpServer
    timeout = 300

    def setup(self) -> None:
        super().setup()
        self.tls = False
        self.user: Optional[str] = None
        self.helo = ""
        self.peer = str(self.client_address[0])
        self._reset()

    def _reset(self) -> None:
        self.mail_from: Optional[str] = None
        self.rcpts: List[str] = []

    def send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("utf-8"))
        self.wfile.flush()

    def readline(self) -> Optional[bytes]:
        line = self.rfile.readline(_MAX_LINE + 2)
        if not line:
            return None
        if len(line) > _MAX_LINE and not line.endswith(b"\n"):
            self.send("500 5.5.6 Line too long")
            while line and not line.endswith(b"\n"):  # drain the rest
                line = self.rfile.readline(_MAX_LINE)
            return b""
        return line

    def handle(self) -> None:
        cfg = self.server.config
        if not _ip_allowed(self.peer, cfg.get("allowed_ips") or []):
            self.send("554 5.7.1 Your address is not allowed to send here")
            self.server.log("WARNING", f"smtp {cfg['name']}: refused connection from {self.peer} (allowed_ips)")
            return
        self.send(f"220 {self.server.hostname} ESMTP pskit ready")
        failures = 0
        while True:
            try:
                line = self.readline()
            except (OSError, ssl.SSLError):
                return
            if line is None:
                return
            if not line.strip():
                continue
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            verb, _, arg = text.partition(" ")
            verb = verb.upper()
            handler = getattr(self, "cmd_" + verb, None)
            if handler is None:
                failures += 1
                self.send("502 5.5.2 Command not recognized")
                if failures > 20:
                    self.send("421 4.7.0 Too many errors, closing")
                    return
                continue
            try:
                if handler(arg.strip()) is False:
                    return
            except (OSError, ssl.SSLError):
                return

    # -- commands
    def _ehlo_lines(self) -> List[str]:
        cfg = self.server.config
        lines = [self.server.hostname, f"SIZE {int(cfg.get('max_size') or DEFAULT_MAX_SIZE)}", "8BITMIME",
                 "ENHANCEDSTATUSCODES"]
        if self.server.tls_context is not None and not self.tls:
            lines.append("STARTTLS")
        if cfg.get("auth") and self._auth_offered():
            lines.append("AUTH PLAIN LOGIN")
        return lines

    def _auth_offered(self) -> bool:
        return self.tls or (self.server.tls_context is None and not self.server.config.get("require_tls"))

    def cmd_EHLO(self, arg: str) -> None:
        self.helo = arg
        self._reset()
        lines = self._ehlo_lines()
        for line in lines[:-1]:
            self.send("250-" + line)
        self.send("250 " + lines[-1])

    def cmd_HELO(self, arg: str) -> None:
        self.helo = arg
        self._reset()
        self.send(f"250 {self.server.hostname}")

    def cmd_STARTTLS(self, _arg: str) -> Optional[bool]:
        if self.server.tls_context is None or self.tls:
            self.send("454 4.7.0 TLS not available")
            return None
        self.send("220 2.0.0 Ready to start TLS")
        conn = self.server.tls_context.wrap_socket(self.connection, server_side=True)
        self.connection = self.request = conn
        self.rfile = conn.makefile("rb")
        self.wfile = conn.makefile("wb")
        self.tls = True
        self.helo = ""
        self.user = None
        self._reset()
        return None

    def cmd_AUTH(self, arg: str) -> Optional[bool]:
        cfg = self.server.config
        if not cfg.get("auth"):
            self.send("503 5.5.1 AUTH not enabled")
            return None
        if not self._auth_offered():
            self.send("538 5.7.11 Encryption required: STARTTLS first")
            return None
        if self.user:
            self.send("503 5.5.1 Already authenticated")
            return None
        mech, _, initial = arg.partition(" ")
        mech = mech.upper()
        try:
            if mech == "PLAIN":
                if not initial:
                    self.send("334 ")
                    initial = (self.readline() or b"").decode().strip()
                _authzid, user, password = base64.b64decode(initial).decode("utf-8").split("\0", 2)
            elif mech == "LOGIN":
                if initial:
                    user = base64.b64decode(initial).decode("utf-8")
                else:
                    self.send("334 VXNlcm5hbWU6")
                    user = base64.b64decode((self.readline() or b"").strip()).decode("utf-8")
                self.send("334 UGFzc3dvcmQ6")
                password = base64.b64decode((self.readline() or b"").strip()).decode("utf-8")
            else:
                self.send("504 5.5.4 Use AUTH PLAIN or AUTH LOGIN")
                return None
        except (ValueError, UnicodeDecodeError):
            self.send("501 5.5.2 Cannot decode the credentials")
            return None
        expected = (cfg.get("users") or {}).get(user)
        if expected is not None and hmac.compare_digest(str(expected).encode(), password.encode()):
            self.user = user
            self.send("235 2.7.0 Authentication successful")
            return None
        self.server.log("WARNING", f"smtp {cfg['name']}: failed login for {user!r} from {self.peer}")
        time.sleep(1)
        self.send("535 5.7.8 Authentication credentials invalid")
        return None

    def cmd_MAIL(self, arg: str) -> None:
        cfg = self.server.config
        if not self.helo:
            self.send("503 5.5.1 Say EHLO first")
            return
        if cfg.get("require_tls") and self.server.tls_context is not None and not self.tls:
            self.send("530 5.7.0 Must issue a STARTTLS command first")
            return
        if cfg.get("auth") and not self.user:
            self.send("530 5.7.0 Authentication required")
            return
        if self.mail_from is not None:
            self.send("503 5.5.1 Nested MAIL command")
            return
        match = _ADDR.search(arg) if arg.upper().startswith("FROM:") else None
        if match is None:
            self.send("501 5.5.4 Syntax: MAIL FROM:<address>")
            return
        sender = match.group(1).strip()
        size = re.search(r"\bSIZE=(\d+)", arg, re.I)
        if size and int(size.group(1)) > int(cfg.get("max_size") or DEFAULT_MAX_SIZE):
            self.send("552 5.3.4 Message too big")
            return
        allowed = cfg.get("allowed_senders") or []
        if allowed and not any(fnmatch.fnmatch(sender.lower(), p.lower()) for p in allowed):
            self.server.log("WARNING", f"smtp {cfg['name']}: refused sender {sender!r} from {self.peer} "
                                       "(allowed_senders)")
            self.send("550 5.7.1 Sender not allowed")
            return
        self.mail_from = sender
        self.send("250 2.1.0 Ok")

    def cmd_RCPT(self, arg: str) -> None:
        cfg = self.server.config
        if self.mail_from is None:
            self.send("503 5.5.1 Need MAIL before RCPT")
            return
        match = _ADDR.search(arg) if arg.upper().startswith("TO:") else None
        if match is None or "@" not in match.group(1):
            self.send("501 5.5.4 Syntax: RCPT TO:<address>")
            return
        rcpt = match.group(1).strip()
        if not recipient_domain_ok(rcpt, cfg.get("domains") or [], cfg.get("subdomains", True)):
            self.server.log("WARNING", f"smtp {cfg['name']}: refused recipient {rcpt!r} from {self.peer} "
                                       "(not one of the domains)")
            self.send("550 5.7.1 Relaying denied: not a domain of this server")
            return
        if len(self.rcpts) >= _MAX_RCPT:
            self.send("452 4.5.3 Too many recipients")
            return
        self.rcpts.append(rcpt)
        self.send("250 2.1.5 Ok")

    def cmd_DATA(self, _arg: str) -> Optional[bool]:
        cfg = self.server.config
        if not self.rcpts:
            self.send("503 5.5.1 Need RCPT before DATA")
            return None
        self.send("354 End data with <CR><LF>.<CR><LF>")
        limit = int(cfg.get("max_size") or DEFAULT_MAX_SIZE)
        chunks: List[bytes] = []
        size = 0
        too_big = False
        while True:
            line = self.rfile.readline(_MAX_LINE * 128)
            if not line:
                return False
            if line in (b".\r\n", b".\n"):
                break
            if line.startswith(b".."):
                line = line[1:]
            size += len(line)
            if size > limit:
                too_big = True
                chunks = []
                continue
            if not too_big:
                chunks.append(line)
        if too_big:
            self.send("552 5.3.4 Message too big")
            self._reset()
            return None
        now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        received = (f"Received: from {self.helo or 'unknown'} ([{self.peer}]) by {self.server.hostname} with "
                    f"{'ESMTPS' if self.tls else 'ESMTP'}{'A' if self.user else ''} (pskit); {now}\r\n").encode()
        envelope = {"mail_from": self.mail_from, "rcpt_to": list(self.rcpts), "helo": self.helo,
                    "remote_ip": self.peer, "authenticated_user": self.user, "tls": self.tls}
        try:
            ident = self.server.mailbox.save(received + b"".join(chunks), envelope)
        except OSError as exc:
            self.server.log("ERROR", f"smtp {cfg['name']}: could not store a message: {exc}")
            self.send("451 4.3.0 Could not store the message, try again later")
            self._reset()
            return None
        self.send(f"250 2.0.0 Ok: queued as {ident}")
        self._reset()
        self.server.received(ident)
        return None

    def cmd_RSET(self, _arg: str) -> None:
        self._reset()
        self.send("250 2.0.0 Ok")

    def cmd_NOOP(self, _arg: str) -> None:
        self.send("250 2.0.0 Ok")

    def cmd_VRFY(self, _arg: str) -> None:
        self.send("252 2.5.2 Cannot VRFY user, but will accept message")

    def cmd_HELP(self, _arg: str) -> None:
        self.send("214 2.0.0 EHLO HELO STARTTLS AUTH MAIL RCPT DATA RSET NOOP QUIT")

    def cmd_QUIT(self, _arg: str) -> bool:
        self.send("221 2.0.0 Bye")
        return False


class SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        config: Dict[str, Any],
        mailbox: Mailbox,
        on_message: Callable[[str], None],
        log: Callable[[str, str], None],
    ) -> None:
        self.config = dict(config)
        self.mailbox = mailbox
        self.on_message = on_message
        self.log = log
        self.hostname = str(config.get("hostname") or socket.getfqdn() or "localhost")
        self.tls_context: Optional[ssl.SSLContext] = None
        if config.get("tls_cert") and config.get("tls_key"):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(str(config["tls_cert"]), str(config["tls_key"]))
            self.tls_context = ctx
        bind = str(config.get("bind") or "0.0.0.0")  # noqa: S104 - a mail server listens on the network
        super().__init__((bind, int(config.get("port") or 2525)), _Session)

    def received(self, ident: str) -> None:
        self.on_message(ident)


def start(
    config: Dict[str, Any], mailbox: Mailbox, on_message: Callable[[str], None], log: Callable[[str, str], None]
) -> Tuple[SmtpServer, threading.Thread]:
    server = SmtpServer(config, mailbox, on_message, log)
    thread = threading.Thread(target=server.serve_forever, name=f"pskit-smtp-{config['name']}", daemon=True)
    thread.start()
    return server, thread
