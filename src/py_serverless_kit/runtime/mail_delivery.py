"""Deliver received mail to functions, and the host's mail admin API (standard library only).

The host builds one :class:`MailDelivery` from ``pskit_host.json``: ``smtp`` (the servers, by resource name) and the
functions' ``smtp`` events::

    {"type": "smtp", "name": "inbound-mail", "recipients": ["*@support.example.com"], "subdomains": ["support"]}

A function gets a message when one of its envelope recipients matches ``recipients`` (globs, default all) and
``subdomains`` (default all; ``""`` is the domain itself). It is invoked once per message with an ``smtp`` event
whose ``data`` is :func:`runtime.mail.parse_message` plus ``matched`` (its recipients). Every message is logged
(from, to, subdomain, domain, subject, body preview, attachments), and its deliveries are kept with it.

Admin API under ``/__pskit/mail``: ``GET`` (servers), ``GET /<name>?offset=&limit=&status=`` (a page),
``POST /<name>`` (inject: raw message/rfc822, or JSON ``{"from", "to", "subject", "text", "html", "attachments"}``),
``GET /<name>/<id>`` (parsed, attachments without content), ``GET /<name>/<id>/raw`` (the .eml),
``GET /<name>/<id>/attachments/<n>``, ``POST /<name>/<id>/redeliver``, ``DELETE /<name>/<id>``, ``DELETE /<name>``.
"""

from __future__ import annotations

import fnmatch
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import mail as mailparse
from . import smtp_server
from .event import Event, EventType

Reply = Tuple[int, Dict[str, str], bytes]


def _json(status: int, payload: Any) -> Reply:
    return status, {"content-type": "application/json"}, json.dumps(payload, default=str).encode("utf-8")


class MailDelivery:
    def __init__(
        self,
        spec: Dict[str, Any],
        data_dir: Path,
        invoke: Callable[[str, Event], Any],
        log: Callable[..., Any],
    ) -> None:
        self.configs: Dict[str, Dict[str, Any]] = {
            str(name): {**cfg, "name": str(name), "users": self._passwords(cfg.get("users") or {})}
            for name, cfg in (spec.get("smtp") or {}).items()
        }
        self.listeners: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for fn_name, fn in (spec.get("functions") or {}).items():
            for ev in fn.get("events") or []:
                if ev.get("type") == "smtp":
                    self.listeners.setdefault(str(ev.get("name")), []).append((fn_name, ev))
        self.boxes = {name: smtp_server.Mailbox(Path(data_dir) / "smtp" / name) for name in self.configs}
        self.invoke = invoke
        self.log = log
        self.servers: Dict[str, smtp_server.SmtpServer] = {}
        self._queue: queue.Queue[Tuple[str, str]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    @staticmethod
    def _passwords(users: Dict[str, Any]) -> Dict[str, str]:
        """``{name: {"password_env": VAR}}`` -> ``{name: password}`` from the environment (the host's env file)."""
        out: Dict[str, str] = {}
        for name, body in users.items():
            var = body.get("password_env") if isinstance(body, dict) else None
            value = os.environ.get(str(var or "")) if var else (body if isinstance(body, str) else None)
            if value:
                out[str(name)] = value
        return out

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> List[str]:
        """Start every SMTP server and the delivery worker; queue mail not delivered before a restart."""
        notes: List[str] = []
        if not self.configs:
            return notes
        self._worker = threading.Thread(target=self._run, name="pskit-mail-delivery", daemon=True)
        self._worker.start()
        for name, cfg in self.configs.items():
            if cfg.get("auth") and not cfg["users"]:
                notes.append(f"smtp {name}: auth is on but no user has a password in the env file; every login fails")
            try:
                server, _thread = smtp_server.start(cfg, self.boxes[name], self._submitter(name), self._log)
            except (OSError, ValueError) as exc:
                where = f"{cfg.get('bind') or '0.0.0.0'}:{cfg.get('port')}"  # noqa: S104 - message text
                notes.append(f"smtp {name}: cannot listen on {where}: {exc}"
                             + (" (ports below 1024 need root: use 2525 and redirect 25, see the SMTP guide)"
                                if int(cfg.get("port") or 0) < 1024 else ""))
                continue
            self.servers[name] = server
            bound = f"{server.server_address[0]!s}:{server.server_address[1]!s}"
            self._log("INFO", f"smtp {name}: listening on {bound} "
                              f"for {', '.join(cfg.get('domains') or ['any domain'])}"
                              f"{' and subdomains' if cfg.get('subdomains', True) else ''}"
                              f"{', AUTH required' if cfg.get('auth') else ''}"
                              f"{', STARTTLS' if server.tls_context else ''}")
            for meta in self.boxes[name].page(0, 10000, "received")["items"]:
                self.submit(name, meta["id"])
        return notes

    def stop(self) -> None:
        for server in self.servers.values():
            server.shutdown()
            server.server_close()
        self.servers.clear()
        self._queue.put(("", ""))

    def _log(self, level: str, message: str, **kw: Any) -> None:
        self.log(level, "smtp", message, **kw)

    def _submitter(self, name: str) -> Callable[[str], None]:
        return lambda ident: self.submit(name, ident)

    def submit(self, name: str, ident: str) -> None:
        self._queue.put((name, ident))

    def _run(self) -> None:
        while True:
            name, ident = self._queue.get()
            if not name:
                return
            try:
                self.deliver(name, ident)
            except Exception as exc:  # noqa: BLE001 - one bad message must not stop delivery
                self._log("ERROR", f"smtp {name}: delivering {ident} failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ delivery
    @staticmethod
    def _matches(ev: Dict[str, Any], rcpt: Dict[str, str]) -> bool:
        patterns = [str(p).lower() for p in ev.get("recipients") or []]
        if patterns and not any(fnmatch.fnmatch(rcpt["address"], p) for p in patterns):
            return False
        subs = [str(s).lower() for s in ev.get("subdomains") or []]
        return not subs or any(fnmatch.fnmatch(rcpt["subdomain"], s) for s in subs)

    def parsed(self, name: str, ident: str, include_content: bool = True) -> Dict[str, Any]:
        box = self.boxes[name]
        meta = box.meta(ident)
        return mailparse.parse_message(box.raw(ident), meta.get("envelope"), self.configs[name].get("domains") or [],
                                       ident=ident, resource=name, received_at=str(meta.get("received_at") or ""),
                                       include_content=include_content)

    def deliver(self, name: str, ident: str) -> Dict[str, Any]:
        box = self.boxes[name]
        mail = self.parsed(name, ident)
        fields = mailparse.log_fields(mail)
        self._log("INFO", mailparse.summary(mail), fields=fields)
        deliveries: Dict[str, Any] = {}
        listeners = self.listeners.get(name) or []
        for fn_name, ev in listeners:
            matched = [r for r in mail["recipients"] if self._matches(ev, r)]
            if not matched:
                continue
            data = {**mail, "matched": matched, "recipient": matched[0]}
            self._log("INFO", f"mail {ident} -> {fn_name} (recipient {matched[0]['address']}, "
                              f"subdomain={matched[0]['subdomain'] or '-'}, domain={matched[0]['domain']})",
                      function=fn_name, fields=fields)
            try:
                self.invoke(fn_name, Event(type=EventType.SMTP, data=data, source=f"onprem.smtp.{name}"))
                deliveries[fn_name] = {"ok": True}
            except Exception as exc:  # noqa: BLE001 - recorded with the message; redeliver from the studio
                deliveries[fn_name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self._log("ERROR", f"mail {ident} -> {fn_name} failed: {type(exc).__name__}: {exc}", function=fn_name)
        if not deliveries:
            status = "unmatched"
            self._log("WARNING", f"mail {ident}: no function matches its recipients "
                                 f"{', '.join(fields['to'])}")
        else:
            status = "delivered" if all(d["ok"] for d in deliveries.values()) else "failed"
        summary = {"from": fields["from"], "to": fields["to"], "subject": fields["subject"],
                   "subdomains": fields["subdomains"], "domains": fields["domains"],
                   "attachments": len(fields["attachments"])}
        return box.update(ident, status=status, deliveries=deliveries, summary=summary)

    # ------------------------------------------------------------------ admin API
    def admin(self, method: str, parts: List[str], query: Dict[str, str], raw: bytes,
              content_type: str = "") -> Reply:
        """``parts`` after ``/__pskit/mail``."""
        if not parts:
            if method != "GET":
                return _json(405, {"error": "GET the SMTP servers"})
            return _json(200, {"items": [
                {"name": n, "port": c.get("port"), "domains": c.get("domains") or [], "auth": bool(c.get("auth")),
                 "listening": n in self.servers, "functions": [f for f, _e in self.listeners.get(n) or []],
                 "count": len(self.boxes[n].ids())} for n, c in sorted(self.configs.items())]})
        name = parts[0]
        if name not in self.boxes:
            return _json(404, {"error": f"no SMTP server {name!r}; known: {', '.join(sorted(self.boxes)) or 'none'}"})
        box = self.boxes[name]
        try:
            if len(parts) == 1:
                if method == "GET":
                    offset = max(0, int(query.get("offset") or 0))
                    limit = max(1, min(500, int(query.get("limit") or 50)))
                    return _json(200, {"name": name, **box.page(offset, limit, query.get("status") or "")})
                if method == "POST":
                    return self._inject(name, raw, content_type)
                if method == "DELETE":
                    return _json(200, {"deleted": box.purge()})
                return _json(405, {"error": "GET, POST or DELETE"})
            ident = parts[1]
            if len(parts) == 2:
                if method == "GET":
                    return _json(200, {**box.meta(ident), "mail": self.parsed(name, ident, include_content=False)})
                if method == "DELETE":
                    return _json(200, {"deleted": box.delete(ident)})
            if len(parts) == 3 and parts[2] == "raw" and method == "GET":
                return 200, {"content-type": "message/rfc822"}, box.raw(ident)
            if len(parts) == 3 and parts[2] == "redeliver" and method == "POST":
                return _json(200, self.deliver(name, ident))
            if len(parts) == 4 and parts[2] == "attachments" and method == "GET":
                index = int(parts[3])
                atts = self.parsed(name, ident)["attachments"]
                if not 0 <= index < len(atts):
                    return _json(404, {"error": f"mail {ident} has {len(atts)} attachment(s)"})
                import base64

                att = atts[index]
                headers = {"content-type": att["content_type"],
                           "content-disposition": f'attachment; filename="{att["filename"] or "attachment"}"'}
                return 200, headers, base64.b64decode(att["content"])
        except smtp_server.MailboxError as exc:
            return _json(404, {"error": str(exc.args[0] if exc.args else exc)})
        except ValueError as exc:
            return _json(400, {"error": str(exc)})
        return _json(404, {"error": "unknown mail endpoint"})

    def _inject(self, name: str, raw: bytes, content_type: str) -> Reply:
        """A message posted to the host (tests, ``pskit trigger``): stored and delivered like one from SMTP."""
        if "json" in content_type or raw[:1] in (b"{", b"["):
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("post a JSON object or a raw message (message/rfc822)")
            message = mailparse.build_message(payload)
            rcpts = mailparse.envelope_recipients(payload)
            sender = str(payload.get("from") or "sender@example.org")
        else:
            message = raw
            parsed = mailparse.parse_message(raw, {}, include_content=False)
            rcpts = [a["address"] for a in parsed["to"] + parsed["cc"]]
            sender = parsed["from"]["address"]
        cfg = self.configs[name]
        refused = [r for r in rcpts
                   if not smtp_server.recipient_domain_ok(r, cfg.get("domains") or [], cfg.get("subdomains", True))]
        if refused or not rcpts:
            raise ValueError(f"recipients outside the server's domains ({', '.join(cfg.get('domains') or [])}): "
                             f"{', '.join(refused) or 'none given'}")
        envelope = {"mail_from": sender, "rcpt_to": rcpts, "helo": "pskit-admin", "remote_ip": "admin-api",
                    "authenticated_user": "admin", "tls": False}
        ident = self.boxes[name].save(message, envelope)
        return _json(200, self.deliver(name, ident))
