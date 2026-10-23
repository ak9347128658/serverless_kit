"""Receive mail for a domain: the DNS plan, its live check, handler stubs and the studio endpoints."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from py_serverless_kit.core import mail_setup as ms

from .test_studio import call, studio  # noqa: F401 - the fixture

# kavion.ai today: mail at Microsoft 365, sends with SPF and DMARC; nothing for inbound.kavion.ai yet.
LIVE = {
    ("kavion.ai", "MX"): ["0 kavion-ai.mail.protection.outlook.com"],
    ("kavion.ai", "TXT"): ["v=spf1 include:spf.protection.outlook.com ~all", "google-site-verification=x"],
    ("_dmarc.kavion.ai", "TXT"): ["v=DMARC1; p=reject; adkim=r; aspf=r"],
}


def dns(records):
    return lambda name, rtype: list(records.get((name, rtype), []))


def test_plan_for_a_subdomain():
    setup = ms.plan("Kavion.AI", "inbound", ip="13.68.248.150")
    assert setup["receive"] == "inbound.kavion.ai" and setup["mx"] == "mx.kavion.ai"
    rows = [(r["type"], r["name"], r["value"], r.get("priority"), r["required"]) for r in setup["records"]]
    assert rows == [
        ("A", "mx", "13.68.248.150", None, True),
        ("MX", "inbound", "mx.kavion.ai", 10, True),
        ("MX", "*.inbound", "mx.kavion.ai", 10, False),
        ("TXT", "inbound", "v=spf1 -all", None, False),
        ("TXT", "_dmarc.inbound", "v=DMARC1; p=reject; adkim=s; aspf=s", None, False),
    ]
    assert any("DKIM" in n and "nothing to publish" in n for n in setup["notes"])
    root = ms.plan("kavion.ai", ip="2001:db8::5", subdomains=False, sends_mail=True)
    assert [(r["type"], r["name"]) for r in root["records"]] == [("AAAA", "mx"), ("MX", "@")]
    for bad in (("kavion", ""), ("kavion.ai", "in bound"), ("kavion.ai", "", "mx.other.org")):
        with pytest.raises(ms.SetupError):
            ms.plan(*bad)
    with pytest.raises(ms.SetupError, match="IP address"):
        ms.plan("kavion.ai", ip="not-an-ip")


def test_check_warns_before_moving_company_mail_and_keeps_existing_spf():
    whole = ms.check(ms.plan("kavion.ai", ip="13.68.248.150"), dns(LIVE))
    assert any("ALL mail for @kavion.ai" in w and "Microsoft 365" in w for w in whole["warnings"])
    status = {(r["type"], r["name"]): r["status"] for r in whole["records"]}
    assert status[("MX", "@")] == "different" and status[("A", "mx")] == "missing"
    assert status[("TXT", "@")] == "keep" and status[("TXT", "_dmarc")] == "keep"  # it sends mail: keep them
    assert whole["current_mx"] == ["kavion-ai.mail.protection.outlook.com"] and not whole["ready"]

    live = {**LIVE, ("mx.kavion.ai", "A"): ["13.68.248.150"],
            ("inbound.kavion.ai", "MX"): ["10 mx.kavion.ai."], ("pskit-check.inbound.kavion.ai", "MX"): ["10 mx.kavion.ai"],
            ("inbound.kavion.ai", "TXT"): ["v=spf1 -all"]}
    sub = ms.check(ms.plan("kavion.ai", "inbound", ip="13.68.248.150"), dns(live))
    assert sub["ready"] and [w for w in sub["warnings"] if "Do not change the MX of kavion.ai" in w]
    status = {(r["type"], r["name"]): r["status"] for r in sub["records"]}
    assert status == {("A", "mx"): "ok", ("MX", "inbound"): "ok", ("MX", "*.inbound"): "ok",
                      ("TXT", "inbound"): "ok", ("TXT", "_dmarc.inbound"): "missing"}


def test_handler_stubs_compile_and_run():
    compile(ms.handler_stub("python", "on-mail"), "on_mail.py", "exec")
    node = shutil.which("node")
    if node:
        code = ms.handler_stub("node", "on-mail") + (
            "\nexports.handle({data: {id: 'm1', from: {address: 'a@b.c'}, recipient: {address: 'x@s.kavion.ai',"
            " subdomain: 's', domain: 'kavion.ai'}, subject: 'Hi', text: 'hello', attachments: ["
            "{filename: 'a.txt', content_type: 'text/plain', content: Buffer.from('ab').toString('base64')}],"
            " authentication: {summary: 'spf=pass'}}}).then((r) => console.log(JSON.stringify(r)));\n")
        out = subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout.strip().splitlines()[-1]) == {"id": "m1", "subdomain": "s", "text": "hello"}


def test_studio_endpoints(studio, monkeypatch):  # noqa: F811
    base, root, _httpd = studio
    monkeypatch.setattr(ms, "default_lookup", dns(LIVE))
    status, setup = call(base, "POST", "/api/mail-setup", {"domain": "kavion.ai", "subdomain": "inbound",
                                                          "ip": "13.68.248.150", "check": True})
    assert status == 200 and setup["receive"] == "inbound.kavion.ai" and setup["records"][0]["status"] == "missing"
    status, err = call(base, "POST", "/api/mail-setup", {"domain": "nodot"})
    assert status == 400 and "domain name" in err["error"]
    status, stub = call(base, "POST", "/api/mail-handler-stub", {"language": "python", "function": "on-mail"})
    assert status == 200 and "def handle(event, ctx)" in stub["text"]
    status, out = call(base, "POST", "/api/env-file", {"name": "SMTP_INBOUND_RELAY_PASSWORD", "value": "S3cret-x"})
    assert status == 200 and out["set"] is True
    assert "SMTP_INBOUND_RELAY_PASSWORD=S3cret-x" in (root / ".env.onprem").read_text("utf-8")
    status, out = call(base, "GET", "/api/env-file?names=SMTP_INBOUND_RELAY_PASSWORD,OTHER")
    assert out["set"] == {"SMTP_INBOUND_RELAY_PASSWORD": True, "OTHER": False} and "S3cret" not in json.dumps(out)
    assert call(base, "POST", "/api/env-file", {"name": "bad name", "value": "x"})[0] == 400
