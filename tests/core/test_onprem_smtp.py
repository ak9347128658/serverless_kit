"""The SMTP on-prem service: settings, docker run arguments, pskit.yml edits, guide and test events."""

from __future__ import annotations

import base64
import copy
import email
import email.policy

import pytest

from py_serverless_kit.config.schema import ServiceConfig
from py_serverless_kit.core import onprem_services as svc
from py_serverless_kit.core import onprem_smtp

from .test_onprem_services import RAW, FakeDocker

SSH = {"method": "ssh", "host": "20.40.60.80", "user": "ubuntu", "identity_file": "key.pem"}


def _raw(deploy=None, **options):
    raw = copy.deepcopy(RAW)
    raw["functions"]["on-mail"] = {"handler": "app.handlers.on_mail"}
    raw["provider"]["options"] = {**options, **({"deploy": deploy} if deploy else {})}
    return raw


def _create(tmp_path, raw, **body):
    fake = FakeDocker()
    req = {"key": "inbound", "service": "smtp", "domains": ["inbound.example.com"], **body}
    entry = svc.create(tmp_path, req, raw, lambda _l: None, run=fake, wait_seconds=1)
    run = next(c for c in fake.calls if "run" in c or any("'run'" in part for part in c))
    return entry, run


def test_local_service_publishes_the_port_and_reaches_the_host_through_docker(tmp_path):
    entry, run = _create(tmp_path, _raw(port=9000), attach=["on-mail"])
    assert run[:2] == ["docker", "run"]
    assert "127.0.0.1:25:25" in run and "host.docker.internal:host-gateway" in run
    env = dict(run[i + 1].split("=", 1) for i, arg in enumerate(run) if arg == "-e")
    assert env["PSKIT_FUNCTIONS_URL"] == "http://host.docker.internal:9000"
    assert env["PSKIT_SMTP_DOMAINS"] == "inbound.example.com" and env["PSKIT_SMTP_PORT"] == "25"
    assert base64.b64decode(env["PSKIT_SMTP_JS"]) == onprem_smtp.SCRIPT.read_bytes()
    assert "PSKIT_ADMIN_TOKEN" not in env
    assert run[-3:] == ["sh", "-c", onprem_smtp.START] and run[-4] == "node:22-alpine"
    assert entry["resource"] == {"type": "smtp", "external": True, "connection": {
        "host": "127.0.0.1", "port": 25, "domains": "inbound.example.com", "hostname": "mail.inbound.example.com"}}
    assert entry["smtp"]["network"] == "bridge" and not entry["env"] and "token" not in entry


def test_ssh_service_on_the_functions_server_uses_the_host_network(tmp_path):
    raw = _raw(deploy={"host": "20.40.60.80", "user": "ubuntu"}, admin_token="s3cret")
    entry, run = _create(tmp_path, raw, target=SSH, port=2525)
    command = run[-1]
    assert "'--network' 'host'" in command and "'-p'" not in command
    assert "'PSKIT_FUNCTIONS_URL=http://127.0.0.1:8080'" in command
    assert "'PSKIT_SMTP_PORT=2525'" in command and "'PSKIT_SMTP_BIND=0.0.0.0'" in command
    assert "'PSKIT_ADMIN_TOKEN=s3cret'" in command
    assert entry["address"] == "20.40.60.80"


def test_functions_url_defaults_and_errors(tmp_path):
    other = {"method": "ssh", "host": "20.40.60.81"}
    raw = _raw(deploy={"host": "20.40.60.80"})
    assert onprem_smtp.check({"domains": "a.example.com"}, raw, tmp_path, other)["functions_url"] == \
        "http://20.40.60.80:8080"
    with pytest.raises(svc.ServiceError, match="SSH server"):
        onprem_smtp.check({"domains": "a.example.com"}, raw, tmp_path, {"method": "local"})
    with pytest.raises(svc.ServiceError, match="functions run on this machine"):
        onprem_smtp.check({"domains": "a.example.com"}, _raw(), tmp_path, other)
    explicit = onprem_smtp.check({"domains": "a.example.com", "functions_url": "https://fn.example.com/"}, _raw(),
                                 tmp_path, other)
    assert explicit["functions_url"] == "https://fn.example.com"
    with pytest.raises(svc.ServiceError, match="domains"):
        onprem_smtp.check({}, _raw(), tmp_path, {"method": "local"})
    with pytest.raises(svc.ServiceError, match="not a domain"):
        onprem_smtp.check({"domains": ["not a domain"]}, _raw(), tmp_path, {"method": "local"})
    with pytest.raises(svc.ServiceError, match="leave user and password empty"):
        svc.check_request({"key": "m", "service": "smtp", "domains": "a.example.com", "password": "x" * 10},
                          tmp_path, _raw())


def test_admin_token_from_the_env_file(tmp_path):
    raw = _raw(admin_token="${env:HOST_TOKEN}")
    with pytest.raises(svc.ServiceError, match="HOST_TOKEN"):
        onprem_smtp.admin_token(raw, tmp_path, ".env.onprem")
    (tmp_path / ".env.onprem").write_text("HOST_TOKEN=abc\n", "utf-8")
    assert onprem_smtp.admin_token(raw, tmp_path, ".env.onprem") == "abc"


def test_apply_adds_the_resource_and_smtp_triggers(tmp_path):
    entry, _run = _create(tmp_path, _raw(), attach=["on-mail"])
    out = svc.apply_to_raw(_raw(), entry)
    assert out["resources"]["inbound"]["type"] == "smtp"
    assert out["functions"]["on-mail"]["events"] == [{"smtp": {"resource": "inbound"}}]
    config = ServiceConfig.model_validate(out)
    assert config.functions["on-mail"].events[0].config.name == "inbound"
    assert svc.remove_from_raw(out, "inbound")["functions"]["on-mail"]["events"] == []


def test_usage_guide_lists_the_dns_records(tmp_path):
    _create(tmp_path, _raw(deploy={"host": "20.40.60.80"}), target=SSH)
    text = svc.service_usage(tmp_path, "inbound", probe=False)["markdown"]
    assert "| A | `mail.inbound.example.com` | `20.40.60.80` |" in text
    assert "| MX | `inbound.example.com` | `10 mail.inbound.example.com` |" in text
    assert "Records" in text and "- smtp: {resource: inbound}" in text
    private = onprem_smtp.dns_records({"address": "10.0.0.5", "smtp": {"domains": ["x.example.com"]}})
    assert private[0]["value"] == "<public IP of the server>"


def test_catalog_has_the_smtp_service():
    item = next(c for c in svc.catalog() if c["id"] == "smtp")
    assert item["group"] == "Mail" and item["roles"] == ["smtp"] and item["auth"] is False


def test_test_event_has_the_simple_fields_ses_records_and_raw_content():
    ev = onprem_smtp.test_event({"from": "Jane <jane@client.org>", "to": ["support@example.com"],
                                 "cc": "team@example.com", "subject": "Order", "text": "Hi", "html": "<p>Hi</p>",
                                 "attachments": [{"filename": "a.txt", "content_type": "text/plain",
                                                  "content": "Total: 42"}]}, "inbound")
    assert ev["from"] == "jane@client.org" and ev["from_name"] == "Jane" and ev["cc"] == ["team@example.com"]
    assert base64.b64decode(ev["attachments"][0]["content"]) == b"Total: 42" and ev["attachments"][0]["size"] == 9
    ses = ev["Records"][0]["ses"]
    assert ev["Records"][0]["eventSource"] == "aws:ses"
    assert ses["mail"]["commonHeaders"]["subject"] == "Order"
    assert ses["mail"]["destination"] == ["support@example.com", "team@example.com"]
    message = email.message_from_bytes(base64.b64decode(ev["content"]), policy=email.policy.default)
    assert message["Subject"] == "Order" and [p.get_filename() for p in message.iter_attachments()] == ["a.txt"]
    assert onprem_smtp.test_event(ev) is ev
