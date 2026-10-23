"""Receive mail for your domain: the DNS records to add at your domain provider, and a check of what is live.

``plan()`` lists the records for a domain (``kavion.ai``), the address domain mail is received on (the domain itself
or a subdomain such as ``inbound.kavion.ai``), the mail server's name (``mx.kavion.ai``) and its public IP.
``check()`` looks each one up (DNS over HTTPS, so it works behind firewalls that block port 53; plain DNS as a
fallback) and reports ok / missing / different, plus where the domain's mail goes today: replacing an existing MX
(Microsoft 365, Google Workspace ...) would move *all* of the company's mail to pskit, so a subdomain is advised.

Records (``name`` is relative to the domain, as DNS providers show it; ``@`` is the domain itself):

* ``A mx -> <ip>`` (required): the mail server's address.
* ``MX <receive> -> mx.<domain>`` (required): mail for the receive domain goes to that server.
* ``MX *.<receive> -> mx.<domain>`` (subdomains): mail for ``anything@<sub>.<receive>``.
* ``TXT <receive> "v=spf1 -all"`` and ``TXT _dmarc.<receive> "v=DMARC1; p=reject"`` (recommended when that domain
  sends no mail): nobody can send mail *as* it. DKIM signs mail you *send*: nothing to publish for receiving; pskit
  checks the senders' DKIM, SPF and DMARC itself.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_PROVIDERS = (
    ("protection.outlook.com", "Microsoft 365"), ("google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"), ("zoho.", "Zoho Mail"), ("secureserver.net", "GoDaddy mail"),
    ("mimecast", "Mimecast"), ("pphosted.com", "Proofpoint"), ("amazonaws.com", "Amazon SES / WorkMail"),
    ("mailgun.org", "Mailgun"), ("sendgrid.net", "SendGrid"), ("icloud.com", "iCloud Mail"),
    ("protonmail.ch", "Proton Mail"), ("yandex", "Yandex Mail"), ("titan.email", "Titan Mail"),
)

Lookup = Callable[[str, str], List[str]]


class SetupError(ValueError):
    pass


def _domain(text: str, what: str) -> str:
    value = str(text or "").strip().strip(".").lower()
    labels = value.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise SetupError(f"{what} must be a domain name such as kavion.ai, got {text!r}")
    return value


def _relative(fqdn: str, domain: str) -> str:
    return "@" if fqdn == domain else fqdn[: -len(domain) - 1]


def provider_of(mx_host: str) -> str:
    host = mx_host.lower()
    return next((name for pattern, name in _PROVIDERS if pattern in host), "")


def plan(domain: str, subdomain: str = "", mx_host: str = "", ip: str = "", subdomains: bool = True,
         sends_mail: bool = False) -> Dict[str, Any]:
    """The records to add for receiving ``*@<subdomain>.<domain>`` (or ``*@<domain>``) on the server at ``ip``."""
    domain = _domain(domain, "domain")
    sub = str(subdomain or "").strip().strip(".").lower()
    if sub and not all(_LABEL.match(label) for label in sub.split(".")):
        raise SetupError(f"subdomain must be letters, digits and -, such as inbound, got {subdomain!r}")
    receive = f"{sub}.{domain}" if sub else domain
    mx = _domain(mx_host, "mail server name") if mx_host else f"mx.{domain}"
    if not (mx == domain or mx.endswith("." + domain)):
        raise SetupError(f"the mail server name must be under {domain} (e.g. mx.{domain}) so you can add its A record")
    address = str(ip or "").strip()
    family = "A"
    if address:
        try:
            family = "AAAA" if ipaddress.ip_address(address).version == 6 else "A"
        except ValueError:
            raise SetupError(f"server IP must be an IP address, got {ip!r}") from None
    records: List[Dict[str, Any]] = [
        {"type": family, "name": _relative(mx, domain), "fqdn": mx, "value": address or "<server public IP>",
         "required": True, "why": "The mail server's address: other mail servers connect here."},
        {"type": "MX", "name": _relative(receive, domain), "fqdn": receive, "value": mx, "priority": 10,
         "required": True, "why": f"Mail for anything@{receive} goes to {mx}."},
    ]
    if subdomains:
        records.append({"type": "MX", "name": "*." + _relative(receive, domain) if receive != domain else "*",
                        "fqdn": f"*.{receive}", "value": mx, "priority": 10, "required": False,
                        "why": f"Mail for anything@<subdomain>.{receive} (support, sales ...) goes there too."})
    if not sends_mail:
        records += [
            {"type": "TXT", "name": _relative(receive, domain), "fqdn": receive, "value": "v=spf1 -all",
             "required": False, "why": f"SPF: no server may send mail as @{receive} (it only receives), so forged "
                                       "mail from it is rejected by Gmail, Outlook ..."},
            {"type": "TXT", "name": "_dmarc." + _relative(receive, domain) if receive != domain else "_dmarc",
             "fqdn": f"_dmarc.{receive}", "value": "v=DMARC1; p=reject; adkim=s; aspf=s",
             "required": False, "why": "DMARC: receivers reject mail that claims to be from this domain and fails "
                                       "SPF / DKIM."},
        ]
    notes = [
        "DKIM: nothing to publish for receiving. DKIM signs mail you send (your mail provider gives you its own "
        "DKIM record for that); pskit checks the DKIM, SPF and DMARC of the mail it receives and gives the results "
        "to your function (event.data['authentication']).",
        "PTR (reverse DNS) matters only for sending mail; it is set at your hosting / cloud provider, not at the "
        "domain provider.",
        f"Mail servers deliver to port 25: open TCP 25 on {address or 'the server'} (cloud firewall / security "
        "group) and redirect it to the SMTP server's port, or set port: 25 (see the SMTP guide).",
    ]
    if sends_mail:
        notes.append(f"{receive} also sends mail: keep its existing SPF / DKIM / DMARC records as they are.")
    return {"domain": domain, "receive": receive, "mx": mx, "ip": address, "records": records, "notes": notes}


# ----------------------------------------------------------------------------- live check


def _doh(name: str, rtype: str) -> List[str]:
    url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": name, "type": rtype})
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310 - fixed public resolver
        data = json.loads(resp.read().decode("utf-8"))
    code = {"A": 1, "MX": 15, "TXT": 16, "AAAA": 28}[rtype]
    out = []
    for answer in data.get("Answer") or []:
        if answer.get("type") != code:
            continue
        value = str(answer.get("data") or "")
        if rtype == "TXT":
            value = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', value)) or value.strip('"')
        out.append(value.rstrip(".") if rtype != "TXT" else value)
    return out


def default_lookup(name: str, rtype: str) -> List[str]:
    try:
        return _doh(name, rtype)
    except Exception:  # noqa: BLE001 - HTTPS blocked: ask DNS directly
        from ..runtime.mail_auth import query

        return [r.rstrip(".") for r in query(name, rtype)]


def _mx_hosts(values: List[str]) -> List[str]:
    return [v.split()[-1].rstrip(".").lower() for v in values if v.strip()]


def _port_open(ip: str, port: int) -> str:
    try:
        with socket.create_connection((ip, port), timeout=5):
            return "open"
    except socket.timeout:
        return "no answer (a firewall drops it, or your network blocks outgoing port 25)"
    except OSError as exc:
        return f"closed ({exc.strerror or exc})"


def check(setup: Dict[str, Any], lookup: Optional[Lookup] = None, port: Optional[int] = None) -> Dict[str, Any]:
    """``plan()`` with each record's live state (``ok`` / ``missing`` / ``different``) and warnings."""
    ask = lookup or default_lookup
    domain, receive, mx = setup["domain"], setup["receive"], setup["mx"]

    def safe(name: str, rtype: str) -> List[str]:
        try:
            return ask(name, rtype)
        except Exception as exc:  # noqa: BLE001 - shown per record
            return [f"(lookup failed: {exc})"]

    records = []
    for rec in setup["records"]:
        item = dict(rec)
        if rec["type"] in ("A", "AAAA"):
            found = safe(rec["fqdn"], rec["type"])
            ok = rec["value"] in found if setup.get("ip") else bool(found)
        elif rec["type"] == "MX":
            name = rec["fqdn"].replace("*", "pskit-check", 1)
            found = safe(name, "MX")
            ok = mx in _mx_hosts(found)
        else:
            prefix = rec["value"].split()[0].rstrip(";").lower()  # v=spf1 / v=dmarc1
            found = [t for t in safe(rec["fqdn"], "TXT") if t.lower().startswith(prefix)]
            ok = any(t.replace(" ", "").lower() == rec["value"].replace(" ", "").lower() for t in found)
        item["found"] = found
        item["status"] = "ok" if ok else ("missing" if not found else "different")
        if rec["type"] == "TXT" and found and not ok and rec["fqdn"] in (domain, f"_dmarc.{domain}"):
            # The domain itself already has SPF / DMARC: it sends mail (Microsoft 365, Google ...). Keep them.
            item["status"] = "keep"
            item["why"] = "This domain already sends mail with this record: keep it as it is."
        records.append(item)
    warnings: List[str] = []
    root_mx = _mx_hosts(safe(domain, "MX"))
    elsewhere = [h for h in root_mx if h != mx]
    if elsewhere:
        who = provider_of(elsewhere[0]) or elsewhere[0]
        if receive == domain:
            warnings.append(f"{domain}'s mail goes to {who} today ({', '.join(elsewhere)}). Changing its MX to {mx} "
                            f"moves ALL mail for @{domain} to pskit and stops it arriving at {who}. Receive on a "
                            f"subdomain instead (e.g. inbound.{domain}): its MX is separate.")
        else:
            warnings.append(f"{domain} keeps receiving its own mail at {who}; only @{receive} comes to pskit. "
                            f"Do not change the MX of {domain} itself.")
    receive_spf = [t for t in safe(receive, "TXT") if t.lower().startswith("v=spf1")]
    if receive == domain and receive_spf and any(r["type"] == "TXT" and r["value"] == "v=spf1 -all"
                                                   for r in setup["records"]):
        warnings.append(f"{domain} already has an SPF record ({receive_spf[0]}): it sends mail. Keep it; do not add "
                        "v=spf1 -all.")
    result = {**setup, "records": records, "warnings": warnings, "current_mx": root_mx,
              "ready": all(r["status"] == "ok" for r in records if r["required"])}
    if port and setup.get("ip"):
        result["port_25"] = _port_open(setup["ip"], 25)
        if port != 25:
            result["port_server"] = _port_open(setup["ip"], int(port))
    return result


def server_ip(host: str) -> str:
    """The IP of a server name or address (for the A record)."""
    host = str(host or "").strip()
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        return str(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
    except OSError:
        return ""


def handler_stub(language: str, function: str) -> str:
    """A ready-to-edit handler for a new e-mail function."""
    if language == "node":
        return f'''"use strict";
// {function}: runs for every e-mail the SMTP server accepts (see docs/smtp/README.md).

exports.handle = async (event, ctx) => {{
  const mail = event.data;
  const to = mail.recipient; // {{address, local, subdomain, domain}}
  console.log(`mail ${{mail.id}} from ${{mail.from.address}} to ${{to.address}} ` +
    `(subdomain=${{to.subdomain || "-"}}, domain=${{to.domain}}) subject=${{JSON.stringify(mail.subject)}}`);
  console.log(`checks: ${{mail.authentication ? mail.authentication.summary : "off"}}`);
  for (const att of mail.attachments) {{
    const bytes = Buffer.from(att.content, "base64");
    console.log(`attachment ${{att.filename}} ${{att.content_type}} ${{bytes.length}} bytes`);
  }}
  return {{ id: mail.id, subdomain: to.subdomain, text: mail.text.slice(0, 200) }};
}};
'''
    return f'''"""{function}: runs for every e-mail the SMTP server accepts (see docs/smtp/README.md)."""

import base64
import logging

log = logging.getLogger(__name__)


def handle(event, ctx):
    mail = event.data
    to = mail["recipient"]  # {{address, local, subdomain, domain}}
    log.info("mail %s from %s to %s (subdomain=%s, domain=%s) subject=%r", mail["id"], mail["from"]["address"],
             to["address"], to["subdomain"] or "-", to["domain"], mail["subject"])
    auth = mail.get("authentication") or {{}}
    log.info("checks: %s", auth.get("summary", "off"))  # spf=pass dkim=pass dmarc=pass ...
    for att in mail["attachments"]:
        data = base64.b64decode(att["content"])
        log.info("attachment %s %s %d bytes", att["filename"], att["content_type"], len(data))
    return {{"id": mail["id"], "subdomain": to["subdomain"], "text": mail["text"][:200]}}
'''
