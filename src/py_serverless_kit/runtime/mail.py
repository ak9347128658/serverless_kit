"""Parse an e-mail into the portable ``smtp`` event payload (standard library only, Python 3.9+).

``parse_message(raw, envelope, domains)`` returns a JSON-able dict::

    {"id", "resource", "received_at", "size",
     "envelope": {"mail_from", "rcpt_to", "helo", "remote_ip", "authenticated_user", "tls"},
     "from": {address, name, local, host, subdomain, domain}, "to": [...], "cc": [...], "reply_to": [...],
     "subject", "date", "message_id", "in_reply_to", "references", "headers": {name: value},
     "text", "html",
     "attachments": [{"filename", "content_type", "size", "sha256", "content_id", "inline", "content" (base64)}]}

Each address is split into ``local`` (before @), ``host`` (after @), ``domain`` (the registered domain, e.g.
``example.com`` or ``example.co.uk``) and ``subdomain`` (``support`` for ``ann@support.example.com``, ``a.b`` for
``ann@a.b.example.com``, empty for the domain itself). The SMTP server's ``domains`` decide where the domain starts
(``mail.example.com`` configured: ``x@eu.mail.example.com`` has subdomain ``eu``); without them a short list of
two-level suffixes (``co.uk``, ``com.au`` ...) is used.
"""

from __future__ import annotations

import base64
import hashlib
from email import policy
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from typing import Any, Dict, Iterable, List, Optional

# Public suffixes with two labels that are common enough to matter when no domains are configured.
_TWO_LEVEL = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au", "edu.au",
    "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "co.jp", "ne.jp", "or.jp", "co.nz", "org.nz",
    "com.br", "com.cn", "net.cn", "org.cn", "com.mx", "co.za", "com.sg", "com.tr", "com.hk", "com.tw", "co.kr",
    "com.my", "com.ar", "com.sa", "com.pk", "com.ng", "co.id", "co.th", "com.vn", "com.ph", "com.eg", "co.il",
}


def split_host(host: str, domains: Iterable[str] = ()) -> Dict[str, str]:
    """``host`` -> ``{"host", "domain", "subdomain"}`` using the configured ``domains`` (longest match) first."""
    host = host.strip().strip(".").lower()
    for domain in sorted((d.strip().strip(".").lower() for d in domains if d), key=len, reverse=True):
        if host == domain:
            return {"host": host, "domain": domain, "subdomain": ""}
        if host.endswith("." + domain):
            return {"host": host, "domain": domain, "subdomain": host[: -len(domain) - 1]}
    labels = host.split(".")
    size = 3 if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL else 2
    domain = ".".join(labels[-size:])
    return {"host": host, "domain": domain, "subdomain": ".".join(labels[:-size])}


def split_address(address: str, name: str = "", domains: Iterable[str] = ()) -> Dict[str, str]:
    """``"Ann <ann@support.example.com>"`` parts: address, name, local, host, domain, subdomain."""
    address = address.strip().strip("<>")
    local, at, host = address.rpartition("@")
    if not at:
        local, host = address, ""
    out = {"address": address.lower() if host else address, "name": name, "local": local}
    out.update(split_host(host, domains) if host else {"host": "", "domain": "", "subdomain": ""})
    if host:
        out["address"] = f"{local}@{out['host']}"
    return out


def _addresses(msg: EmailMessage, header: str, domains: Iterable[str]) -> List[Dict[str, str]]:
    values = [str(v) for v in msg.get_all(header) or []]
    return [split_address(addr, name, domains) for name, addr in getaddresses(values) if addr]


def _text(part: Optional[MIMEPart]) -> str:
    if part is None:
        return ""
    try:
        return str(part.get_content())
    except (LookupError, ValueError, AssertionError):  # unknown charset or broken encoding
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)


def parse_message(
    raw: bytes,
    envelope: Optional[Dict[str, Any]] = None,
    domains: Iterable[str] = (),
    *,
    ident: str = "",
    resource: str = "",
    received_at: str = "",
    include_content: bool = True,
) -> Dict[str, Any]:
    """The event payload of one message (see the module docstring)."""
    domains = list(domains)
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    env = dict(envelope or {})
    rcpts = [split_address(r, "", domains) for r in env.get("rcpt_to") or []]
    sender_name, sender = parseaddr(str(msg.get("from") or ""))
    body_text = msg.get_body(preferencelist=("plain",))
    body_html = msg.get_body(preferencelist=("html",))
    attachments = []
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):  # message/rfc822 and other containers
            data = part.as_bytes()
        disposition = str(part.get_content_disposition() or "attachment")
        item: Dict[str, Any] = {
            "filename": part.get_filename() or "",
            "content_type": part.get_content_type(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_id": str(part.get("content-id") or "").strip("<>"),
            "inline": disposition == "inline",
        }
        if include_content:
            item["content"] = base64.b64encode(data).decode("ascii")
        attachments.append(item)
    headers: Dict[str, str] = {}
    for name, value in msg.items():
        key = name.lower()
        headers[key] = f"{headers[key]}, {value}" if key in headers else str(value)
    return {
        "id": ident,
        "resource": resource,
        "received_at": received_at,
        "size": len(raw),
        "envelope": {
            "mail_from": env.get("mail_from") or "",
            "rcpt_to": [r["address"] for r in rcpts],
            "helo": env.get("helo") or "",
            "remote_ip": env.get("remote_ip") or "",
            "authenticated_user": env.get("authenticated_user"),
            "tls": bool(env.get("tls")),
        },
        "recipients": rcpts,
        "from": split_address(sender or str(env.get("mail_from") or ""), sender_name, domains),
        "to": _addresses(msg, "to", domains),
        "cc": _addresses(msg, "cc", domains),
        "reply_to": _addresses(msg, "reply-to", domains),
        "subject": str(msg.get("subject") or ""),
        "date": str(msg.get("date") or ""),
        "message_id": str(msg.get("message-id") or "").strip("<>"),
        "in_reply_to": str(msg.get("in-reply-to") or "").strip("<>"),
        "references": str(msg.get("references") or ""),
        "headers": headers,
        "text": _text(body_text),
        "html": _text(body_html),
        "attachments": attachments,
    }


def _kb(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1048576:.1f} MB"


def summary(mail: Dict[str, Any], preview: int = 200) -> str:
    """One log line: id, from, each recipient with its subdomain / domain, subject, attachments, body preview."""
    to = ", ".join(
        f"{r['address']} (subdomain={r['subdomain'] or '-'} domain={r['domain']})" for r in mail.get("recipients") or []
    )
    files = ", ".join(f"{a['filename'] or '(unnamed)'} {a['content_type']} {_kb(int(a['size']))}"
                      for a in mail.get("attachments") or [])
    body = " ".join(str(mail.get("text") or "").split())[:preview]
    auth = (mail.get("authentication") or {}).get("summary")
    return (f"mail {mail.get('id')} from {(mail.get('from') or {}).get('address')} to {to} "
            f"{f'[{auth}] ' if auth else ''}subject={mail.get('subject')!r} size={_kb(int(mail.get('size') or 0))} "
            f"attachments={len(mail.get('attachments') or [])}{f' [{files}]' if files else ''} body={body!r}")


def log_fields(mail: Dict[str, Any]) -> Dict[str, Any]:
    """Structured log fields (no attachment content)."""
    return {
        "mail_id": mail.get("id"),
        "resource": mail.get("resource"),
        "from": (mail.get("from") or {}).get("address"),
        "from_name": (mail.get("from") or {}).get("name"),
        "to": [r["address"] for r in mail.get("recipients") or []],
        "subdomains": sorted({r["subdomain"] for r in mail.get("recipients") or []}),
        "domains": sorted({r["domain"] for r in mail.get("recipients") or []}),
        "cc": [a["address"] for a in mail.get("cc") or []],
        "subject": mail.get("subject"),
        "body_preview": " ".join(str(mail.get("text") or "").split())[:500],
        "attachments": [{"filename": a["filename"], "content_type": a["content_type"], "size": a["size"],
                         "sha256": a["sha256"]} for a in mail.get("attachments") or []],
        "size": mail.get("size"),
        "remote_ip": (mail.get("envelope") or {}).get("remote_ip"),
        "authenticated_user": (mail.get("envelope") or {}).get("authenticated_user"),
        "tls": (mail.get("envelope") or {}).get("tls"),
        "authentication": (mail.get("authentication") or {}).get("summary"),
    }


def build_message(payload: Dict[str, Any]) -> bytes:
    """A test message from ``{"from", "to", "cc", "subject", "text", "html", "attachments": [{"filename",
    "content_type", "content" (text) | "content_base64"}]}`` (``pskit trigger`` and the studio's test form)."""
    msg = EmailMessage()
    msg["From"] = str(payload.get("from") or "sender@example.org")
    to = payload.get("to") or ["inbox@example.com"]
    msg["To"] = ", ".join(to) if isinstance(to, list) else str(to)
    if payload.get("cc"):
        cc = payload["cc"]
        msg["Cc"] = ", ".join(cc) if isinstance(cc, list) else str(cc)
    msg["Subject"] = str(payload.get("subject") or "Test from pskit")
    msg.set_content(str(payload.get("text") or "Hello from pskit."))
    if payload.get("html"):
        msg.add_alternative(str(payload["html"]), subtype="html")
    for att in payload.get("attachments") or []:
        if att.get("content_base64") is not None:
            data = base64.b64decode(str(att["content_base64"]))
        else:
            data = str(att.get("content") or "").encode("utf-8")
        maintype, _, subtype = str(att.get("content_type") or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=str(att.get("filename") or "file.bin"))
    return msg.as_bytes()


def envelope_recipients(payload: Dict[str, Any]) -> List[str]:
    """Envelope recipients of a test payload: to + cc + bcc."""
    out: List[str] = []
    for field in ("to", "cc", "bcc"):
        value = payload.get(field) or ([] if field != "to" else ["inbox@example.com"])
        out += [a for _n, a in getaddresses(value if isinstance(value, list) else [str(value)]) if a]
    return out
