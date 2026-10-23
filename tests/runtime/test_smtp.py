"""SMTP events on premises: the host's SMTP server, mail parsing, delivery to functions, the mail admin API."""

from __future__ import annotations

import base64
import json
import shutil
import smtplib
import socket
import ssl
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from py_serverless_kit.core.deployer import Deployer
from py_serverless_kit.runtime import mail as mailparse
from py_serverless_kit.runtime import smtp_server
from py_serverless_kit.runtime.mail_delivery import MailDelivery
from py_serverless_kit.runtime.onprem_host import Host, activate_layout


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(check, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def _message(to: str = "ann@support.example.com", attach: bool = True) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Customer <customer@client.org>"
    msg["To"] = to
    msg["Cc"] = "boss@example.com"
    msg["Subject"] = "Invoice 42"
    msg.set_content("Please find the invoice attached.\nThanks")
    msg.add_alternative("<p>Please find the <b>invoice</b> attached.</p>", subtype="html")
    if attach:
        msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="invoice-42.pdf")
        msg.add_attachment(b"a,b\n1,2\n", maintype="text", subtype="csv", filename="lines.csv")
    return msg


# ----------------------------------------------------------------------------- parsing


def test_addresses_are_split_into_local_subdomain_and_domain():
    a = mailparse.split_address("Ann@EU.Support.Example.com", "Ann", ["example.com"])
    assert a == {"address": "Ann@eu.support.example.com", "name": "Ann", "local": "Ann",
                 "host": "eu.support.example.com", "domain": "example.com", "subdomain": "eu.support"}
    assert mailparse.split_host("mail.example.com", ["mail.example.com"])["subdomain"] == ""
    assert mailparse.split_host("x.eu.mail.example.com", ["example.com", "mail.example.com"])["subdomain"] == "x.eu"
    # Without configured domains: known two-level suffixes.
    assert mailparse.split_host("sales.shop.co.uk")["domain"] == "shop.co.uk"
    assert mailparse.split_host("sales.shop.co.uk")["subdomain"] == "sales"
    assert mailparse.split_host("a.b.example.org") == {"host": "a.b.example.org", "domain": "example.org",
                                                         "subdomain": "a.b"}


def test_parse_message_body_headers_and_attachments():
    raw = _message().as_bytes()
    mail = mailparse.parse_message(raw, {"mail_from": "customer@client.org", "rcpt_to": ["ann@support.example.com"]},
                                   ["example.com"], ident="m1", resource="inbound")
    assert mail["from"]["address"] == "customer@client.org" and mail["from"]["name"] == "Customer"
    assert mail["recipients"][0]["subdomain"] == "support" and mail["recipients"][0]["domain"] == "example.com"
    assert [c["address"] for c in mail["cc"]] == ["boss@example.com"]
    assert mail["subject"] == "Invoice 42" and "invoice attached" in mail["text"] and "<b>invoice</b>" in mail["html"]
    names = [(a["filename"], a["content_type"], a["size"]) for a in mail["attachments"]]
    assert names == [("invoice-42.pdf", "application/pdf", 13), ("lines.csv", "text/csv", 8)]
    assert base64.b64decode(mail["attachments"][0]["content"]) == b"%PDF-1.4 fake"
    line = mailparse.summary(mail)
    assert "subdomain=support domain=example.com" in line and "invoice-42.pdf" in line and "Please find" in line
    fields = mailparse.log_fields(mail)
    assert fields["subdomains"] == ["support"] and "content" not in fields["attachments"][0]


def test_build_message_for_tests():
    raw = mailparse.build_message({"from": "a@x.org", "to": ["b@sales.example.com"], "subject": "Hi", "text": "yo",
                                   "attachments": [{"filename": "n.txt", "content": "note"}]})
    mail = mailparse.parse_message(raw, {}, ["example.com"])
    assert mail["to"][0]["subdomain"] == "sales" and mail["attachments"][0]["filename"] == "n.txt"
    assert mailparse.envelope_recipients({"to": "b@x.com", "cc": ["c@y.com"], "bcc": ["d@z.com"]}) == [
        "b@x.com", "c@y.com", "d@z.com"]


# ----------------------------------------------------------------------------- server


def _server(tmp_path: Path, **cfg):
    received = []
    box = smtp_server.Mailbox(tmp_path / "box")
    config = {"name": "inbound", "port": _free_port(), "bind": "127.0.0.1", "domains": ["example.com"],
              "subdomains": True, "hostname": "mx.test", **cfg}
    server, _thread = smtp_server.start(config, box, received.append, lambda *_a, **_k: None)
    return server, box, received, config["port"]


def test_server_accepts_only_its_domains_and_spools_before_ok(tmp_path):
    server, box, received, port = _server(tmp_path)
    try:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            code, caps = client.ehlo("client.test")
            assert code == 250 and b"AUTH" not in caps and b"SIZE" in caps
            refused = client.sendmail("x@client.org", ["ann@support.example.com", "relay@other.org"],
                                      _message().as_bytes())
            assert refused == {"relay@other.org": (550, b"5.7.1 Relaying denied: not a domain of this server")}
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                client.sendmail("x@client.org", ["someone@evil-example.com"], b"Subject: x\r\n\r\nhi")
        _wait(lambda: received)
        meta = box.meta(received[0])
        assert meta["status"] == "received" and meta["envelope"]["rcpt_to"] == ["ann@support.example.com"]
        assert meta["envelope"]["remote_ip"] == "127.0.0.1" and meta["envelope"]["helo"] == "client.test"
        assert box.raw(received[0]).startswith(b"Received: from client.test ([127.0.0.1]) by mx.test")
    finally:
        server.shutdown()
        server.server_close()


def test_subdomains_off_size_limit_and_allowed_senders(tmp_path):
    server, _box, _received, port = _server(tmp_path, subdomains=False, max_size=2000,
                                            allowed_senders=["*@client.org"])
    try:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                client.sendmail("x@client.org", ["ann@support.example.com"], b"Subject: x\r\n\r\nhi")
            with pytest.raises(smtplib.SMTPSenderRefused):
                client.sendmail("spam@bad.org", ["ann@example.com"], b"Subject: x\r\n\r\nhi")
            with pytest.raises(smtplib.SMTPSenderRefused) as too_big:
                client.sendmail("x@client.org", ["ann@example.com"], b"Subject: x\r\n\r\n" + b"y" * 5000)
            assert too_big.value.smtp_code == 552
    finally:
        server.shutdown()
        server.server_close()


def test_auth_required_plain_and_login(tmp_path):
    server, _box, received, port = _server(tmp_path, auth=True, users={"relay": "S3cret-pass"})
    try:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            client.ehlo("c")
            assert client.has_extn("auth")
            with pytest.raises(smtplib.SMTPSenderRefused) as no_auth:
                client.sendmail("x@client.org", ["ann@example.com"], b"Subject: x\r\n\r\nhi")
            assert no_auth.value.smtp_code == 530
            with pytest.raises(smtplib.SMTPAuthenticationError):
                client.login("relay", "wrong")
        for mech in ("PLAIN", "LOGIN"):
            with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
                client.ehlo("c")
                client.user, client.password = "relay", "S3cret-pass"
                auth = client.auth_plain if mech == "PLAIN" else client.auth_login
                assert client.auth(mech, auth)[0] == 235
                client.sendmail("x@client.org", ["ann@example.com"], b"Subject: x\r\n\r\nhi")
        _wait(lambda: len(received) == 2)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to make a test certificate")
def test_starttls_then_auth(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=mx.test",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    server, box, received, port = _server(tmp_path, auth=True, users={"relay": "S3cret-pass"}, require_tls=True,
                                          tls_cert=str(cert), tls_key=str(key))
    try:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            client.ehlo("c")
            assert client.has_extn("starttls") and not client.has_extn("auth")  # no AUTH in clear text
            with pytest.raises(smtplib.SMTPSenderRefused):
                client.sendmail("x@client.org", ["ann@example.com"], b"Subject: x\r\n\r\nhi")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            client.starttls(context=ctx)
            client.ehlo("c")
            assert client.has_extn("auth")
            client.login("relay", "S3cret-pass")
            client.sendmail("x@client.org", ["ann@example.com"], b"Subject: x\r\n\r\nhi")
        _wait(lambda: received)
        meta = box.meta(received[0])
        assert meta["envelope"]["tls"] is True and meta["envelope"]["authenticated_user"] == "relay"
        assert b"with ESMTPSA" in box.raw(received[0])
    finally:
        server.shutdown()
        server.server_close()


def test_mail_received_before_a_restart_is_delivered_on_start(tmp_path, monkeypatch):
    calls = []
    spec = {"smtp": {"inbound": {"port": _free_port(), "bind": "127.0.0.1", "domains": ["example.com"]}},
            "functions": {"f": {"events": [{"type": "smtp", "name": "inbound"}]}}}
    delivery = MailDelivery(spec, tmp_path, lambda fn, ev: calls.append((fn, ev.data["subject"])),
                            lambda *_a, **_k: None)
    delivery.lookup = lambda name, rtype: []
    ident = delivery.boxes["inbound"].save(_message("ann@example.com").as_bytes(),
                                           {"mail_from": "c@client.org", "rcpt_to": ["ann@example.com"]})
    try:
        delivery.start()
        _wait(lambda: calls)
        assert calls == [("f", "Invoice 42")]
        _wait(lambda: delivery.boxes["inbound"].meta(ident)["status"] == "delivered")
    finally:
        delivery.stop()


# ----------------------------------------------------------------------------- end to end on the host

HANDLERS = '''
import json
import os
from pathlib import Path


def support(event, ctx):
    mail = event.data
    Path(os.environ["OUT"], "support.json").write_text(json.dumps({
        "type": event.type,
        "recipient": mail["recipient"],
        "matched": [r["address"] for r in mail["matched"]],
        "from": mail["from"]["address"],
        "subject": mail["subject"],
        "files": [[a["filename"], a["size"]] for a in mail["attachments"]],
        "pdf": mail["attachments"][0]["content"] if mail["attachments"] else "",
        "auth": mail["authentication"]["summary"],
    }), encoding="utf-8")
    print(f"support mail from {mail['from']['address']} subdomain {mail['recipient']['subdomain']}")


def everything(event, ctx):
    path = Path(os.environ["OUT"], "all.txt")
    prev = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(prev + event.data["subject"] + ";", encoding="utf-8")
'''

CONFIG = """
service: mailer
provider:
  name: onprem
  runtime: python3.12
  options: {{host: 127.0.0.1, port: {http}, data_dir: {data}}}
  environment: {{OUT: "{out}"}}
resources:
  inbound:
    type: smtp
    options: {{domains: [example.com], port: {smtp}, bind: 127.0.0.1, hostname: mx.test}}
functions:
  support:
    handler: app.handlers.support
    events:
      - smtp: {{resource: inbound, subdomains: [support]}}
  everything:
    handler: app.handlers.everything
    events: [{{smtp: inbound}}]
"""


def test_mail_runs_the_matching_functions_and_is_logged(make_project, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    smtp_port = _free_port()
    project = make_project(CONFIG.format(http=_free_port(), data=(tmp_path / "data").as_posix(), out=out.as_posix(),
                                         smtp=smtp_port), files={"app/handlers.py": HANDLERS})
    assert not [i for i in project.validate() if i.level == "error"]
    Deployer(project, echo=lambda _m: None).package(skip_deps=True)
    root = project.work_dir / "onprem_app"
    spec = json.loads((root / "pskit_host.json").read_text("utf-8"))
    assert spec["smtp"]["inbound"]["domains"] == ["example.com"] and spec["smtp"]["inbound"]["port"] == smtp_port
    activate_layout(root)
    host = Host(root)
    host.mail.lookup = lambda name, rtype: []  # no DNS in tests: SPF / DKIM / DMARC "none"
    host.open()
    host.mail.start()
    httpd = host.make_server(0)
    import threading

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/__pskit/mail"
    try:
        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as client:
            client.send_message(_message("Ann <ann@support.example.com>"))
            client.sendmail("c@client.org", ["bob@example.com"], b"From: c@client.org\r\nSubject: plain\r\n\r\nhi\r\n")
        _wait(lambda: (out / "support.json").is_file() and "plain;" in ((out / "all.txt").read_text("utf-8")
                                                                       if (out / "all.txt").is_file() else ""))
        got = json.loads((out / "support.json").read_text("utf-8"))
        assert got["type"] == "smtp" and got["recipient"]["subdomain"] == "support"
        assert got["auth"] == "spf=none(client.org) dkim=none dmarc=none(p=none)"
        assert got["recipient"]["domain"] == "example.com" and got["from"] == "customer@client.org"
        assert got["matched"] == ["ann@support.example.com"]  # boss@example.com (cc) is not a support address
        assert got["files"] == [["invoice-42.pdf", 13], ["lines.csv", 8]]
        assert base64.b64decode(got["pdf"]) == b"%PDF-1.4 fake"
        assert sorted((out / "all.txt").read_text("utf-8").strip(";").split(";")) == ["Invoice 42", "plain"]

        logs = host.logs.query(limit=200)["entries"]
        text = "\n".join(str(item.get("message")) for item in logs)
        assert "subdomain=support domain=example.com" in text and "invoice-42.pdf" in text
        delivered = [e for e in logs if e.get("function") == "support" and "mail " in str(e.get("message"))]
        assert delivered and delivered[0]["fields"]["subdomains"] == ["", "support"]  # ann@support + boss@ (cc)
        assert delivered[0]["fields"]["attachments"][0]["filename"] == "invoice-42.pdf"

        with urlopen(base, timeout=5) as resp:
            servers = json.loads(resp.read())["items"]
        assert servers[0]["name"] == "inbound" and servers[0]["listening"] and servers[0]["count"] == 2
        with urlopen(base + "/inbound?limit=10", timeout=5) as resp:
            page = json.loads(resp.read())
        assert page["total"] == 2 and {m["status"] for m in page["items"]} == {"delivered"}
        first = next(m for m in page["items"] if m["summary"]["subject"] == "Invoice 42")
        assert first["deliveries"] == {"support": {"ok": True}, "everything": {"ok": True}}
        with urlopen(f"{base}/inbound/{first['id']}", timeout=5) as resp:
            detail = json.loads(resp.read())
        assert "content" not in detail["mail"]["attachments"][0]
        with urlopen(f"{base}/inbound/{first['id']}/attachments/0", timeout=5) as resp:
            assert resp.read() == b"%PDF-1.4 fake"
        with urlopen(f"{base}/inbound/{first['id']}/raw", timeout=5) as resp:
            assert b"Subject: Invoice 42" in resp.read()

        # pskit trigger / the studio inject a test mail through the admin API.
        req = Request(base + "/inbound", data=json.dumps({"to": ["x@support.example.com"], "subject": "injected",
                                                          "attachments": [{"filename": "a.txt", "content": "A"}]}
                                                         ).encode(), method="POST")
        req.add_header("content-type", "application/json")
        with urlopen(req, timeout=10) as resp:
            injected = json.loads(resp.read())
        assert injected["status"] == "delivered" and set(injected["deliveries"]) == {"support", "everything"}
        bad = Request(base + "/inbound", data=json.dumps({"to": ["x@other.org"]}).encode(), method="POST")
        bad.add_header("content-type", "application/json")
        with pytest.raises(Exception) as refused:
            urlopen(bad, timeout=5)
        assert getattr(refused.value, "code", None) == 400

        with urlopen(Request(f"{base}/inbound/{first['id']}", method="DELETE"), timeout=5) as resp:
            assert json.loads(resp.read())["deleted"] is True
    finally:
        httpd.shutdown()
        host.mail.stop()


def test_smtp_is_onprem_only_and_validated(make_project):
    aws = make_project("""
service: mail-test
provider: {name: aws, region: eu-west-1}
resources:
  inbound: {type: smtp, options: {domains: [example.com]}}
functions:
  f: {handler: app.handlers.hello, events: [{smtp: inbound}]}
""")
    assert any("does not support `smtp` events" in i.message for i in aws.validate())
    bad = make_project("""
service: mail-test
provider: {name: onprem}
resources:
  inbound: {type: smtp, options: {auth: true}}
functions:
  f: {handler: app.handlers.hello, events: [{smtp: inbound}]}
""")
    assert any("options.domains" in i.message for i in bad.validate())
    users = make_project("""
service: mail-test
provider: {name: onprem}
resources:
  inbound:
    type: smtp
    options: {domains: [example.com], auth: true, users: {relay: {password_env: RELAY_PW}}}
functions:
  f: {handler: app.handlers.hello, events: [{smtp: {resource: inbound, recipients: ["*@example.com"]}}]}
""")
    found = [(i.level, i.message) for i in users.validate() if "smtp" in i.where or "inbound" in i.where]
    assert any(level == "warning" and "clear text" in msg for level, msg in found)
    assert not any(level == "error" for level, _m in found)


def test_block_spoofed_mail_from_our_own_domain(tmp_path):
    calls = []
    spec = {"smtp": {"inbound": {"port": _free_port(), "bind": "127.0.0.1", "domains": ["inbound.kavion.ai", "kavion.ai"],
                                 "block_spoofed": True}},
            "functions": {"f": {"events": [{"type": "smtp", "name": "inbound"}]}}}
    delivery = MailDelivery(spec, tmp_path, lambda fn, ev: calls.append(ev.data["authentication"]["summary"]),
                            lambda *_a, **_k: None)
    spf = {("kavion.ai", "TXT"): ["v=spf1 ip4:40.107.0.0/16 -all"], ("partner.org", "TXT"): ["v=spf1 ip4:198.51.100.0/24 -all"]}
    delivery.lookup = lambda name, rtype: spf.get((name, rtype), [])
    box = delivery.boxes["inbound"]
    forged = box.save(b"From: CEO <ceo@kavion.ai>\r\nSubject: wire money\r\n\r\nnow",
                      {"mail_from": "ceo@kavion.ai", "rcpt_to": ["pay@inbound.kavion.ai"], "remote_ip": "203.0.113.9",
                       "helo": "evil"})
    genuine = box.save(b"From: Ann <ann@partner.org>\r\nSubject: invoice\r\n\r\nhi",
                       {"mail_from": "ann@partner.org", "rcpt_to": ["pay@inbound.kavion.ai"],
                        "remote_ip": "198.51.100.7", "helo": "mx.partner.org"})
    meta = delivery.deliver("inbound", forged)
    assert meta["status"] == "blocked" and "one of this server's domains" in meta["summary"]["blocked"] and not calls
    assert meta["authentication"]["spf"]["result"] == "fail"
    assert delivery.deliver("inbound", genuine)["status"] == "delivered"
    assert calls == ["spf=pass(partner.org) dkim=none dmarc=none(p=none)"]
    delivery.lookup = lambda name, rtype: (_ for _ in ()).throw(AssertionError("DNS asked again"))
    assert delivery.deliver("inbound", genuine)["status"] == "delivered"  # results kept with the message
