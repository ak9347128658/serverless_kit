"""pskit's AWS Secrets Manager compatible server (aws/secretsmanager) and its on-prem service.

The servers run for real here (Python always, Node.js when ``node`` is installed) and are driven through pskit's
own SigV4 client (``get_secret`` / ``put_secret``), raw API calls, the web console and, when installed, boto3.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from py_serverless_kit.core import onprem_browser, onprem_console, onprem_secretsmanager
from py_serverless_kit.core import onprem_services as svc
from py_serverless_kit.runtime import secrets as rt_secrets
from py_serverless_kit.runtime.drivers._aws import sign

from .test_onprem_services import RAW, FakeDocker

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "aws" / "secretsmanager"
SSH = {"method": "ssh", "host": "20.40.60.80", "user": "ubuntu", "identity_file": "key.pem"}
NODE = shutil.which("node")
IMPLS = [
    pytest.param([sys.executable, str(SOURCE / "python" / "server.py")], id="python"),
    pytest.param([NODE or "node", str(SOURCE / "nodejs" / "server.js")], id="nodejs",
                 marks=pytest.mark.skipif(not NODE, reason="node is not installed")),
]
ACCESS, SECRET = "AKIDPSKIT", "correct-horse-battery"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, argv, data: Path, **env) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.data = data
        full = {**os.environ, "SM_PORT": str(self.port), "SM_BIND": "127.0.0.1", "SM_DATA_DIR": str(data),
                "SM_ACCESS_KEY_ID": ACCESS, "SM_SECRET_ACCESS_KEY": SECRET, **env}
        self.proc = subprocess.Popen(argv, env=full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=1) as resp:
                    if resp.status == 200:
                        return
            except OSError:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    out = self.proc.stdout.read().decode() if self.proc.stdout else ""
                    raise RuntimeError(f"server did not start: {out}") from None
                time.sleep(0.1)

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(10)
        if self.proc.stdout:
            self.proc.stdout.close()

    def call(self, target, payload, *, access=ACCESS, secret=SECRET, region="us-east-1", when=None):
        body = json.dumps(payload).encode()
        headers = sign("POST", f"127.0.0.1:{self.port}", "/", {}, {
            "content-type": "application/x-amz-json-1.1", "x-amz-target": f"secretsmanager.{target}"},
            hashlib.sha256(body).hexdigest(), access, secret, region, when or datetime.now(timezone.utc),
            service="secretsmanager")
        req = urllib.request.Request(self.url + "/", data=body, method="POST")
        for k, v in headers.items():
            if k != "host":
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture(params=IMPLS)
def server(request, tmp_path):
    srv = Server(request.param, tmp_path / "data")
    yield srv
    srv.stop()


def _ok(server, target, payload):
    status, out = server.call(target, payload)
    assert status == 200, out
    return out


# ----------------------------------------------------------------------------- the servers


def test_versions_stages_and_partial_arns(server):
    created = _ok(server, "CreateSecret", {"Name": "prod/db", "SecretString": '{"user":"u","password":"p1"}',
                                           "Description": "db", "Tags": [{"Key": "team", "Value": "pay"}]})
    assert created["ARN"].startswith("arn:aws:secretsmanager:us-east-1:000000000000:secret:prod/db-")
    first = created["VersionId"]
    put = _ok(server, "PutSecretValue", {"SecretId": created["ARN"][:-7], "SecretString": "two"})
    assert put["VersionStages"] == ["AWSCURRENT"]
    assert _ok(server, "GetSecretValue", {"SecretId": "prod/db"})["SecretString"] == "two"
    previous = _ok(server, "GetSecretValue", {"SecretId": created["ARN"], "VersionStage": "AWSPREVIOUS"})
    assert previous["VersionId"] == first and previous["SecretString"] == '{"user":"u","password":"p1"}'
    described = _ok(server, "DescribeSecret", {"SecretId": "prod/db"})
    assert described["VersionIdsToStages"] == {first: ["AWSPREVIOUS"], put["VersionId"]: ["AWSCURRENT"]}
    assert described["Tags"] == [{"Key": "team", "Value": "pay"}] and described["Description"] == "db"
    _ok(server, "UpdateSecretVersionStage", {"SecretId": "prod/db", "VersionStage": "AWSCURRENT",
                                             "MoveToVersionId": first, "RemoveFromVersionId": put["VersionId"]})
    assert _ok(server, "GetSecretValue", {"SecretId": "prod/db"})["VersionId"] == first
    # The same request token and value is a retry; another value under it is refused.
    token = "a" * 36
    _ok(server, "PutSecretValue", {"SecretId": "prod/db", "SecretString": "x", "ClientRequestToken": token})
    _ok(server, "PutSecretValue", {"SecretId": "prod/db", "SecretString": "x", "ClientRequestToken": token})
    status, out = server.call("PutSecretValue", {"SecretId": "prod/db", "SecretString": "y", "ClientRequestToken": token})
    assert status == 400 and out["__type"] == "ResourceExistsException"


def test_list_filters_paging_delete_and_restore(server):
    for name in ("app/a", "app/b", "other"):
        _ok(server, "CreateSecret", {"Name": name, "SecretString": name, "Tags": [{"Key": "env", "Value": "dev"}]})
    names = lambda out: [s["Name"] for s in out["SecretList"]]  # noqa: E731
    assert names(_ok(server, "ListSecrets", {"Filters": [{"Key": "name", "Values": ["APP/"]}]})) == ["app/a", "app/b"]
    assert names(_ok(server, "ListSecrets", {"Filters": [{"Key": "name", "Values": ["!app"]}]})) == ["other"]
    first = _ok(server, "ListSecrets", {"MaxResults": 2})
    assert names(first) == ["app/a", "app/b"] and first["NextToken"]
    assert names(_ok(server, "ListSecrets", {"MaxResults": 2, "NextToken": first["NextToken"]})) == ["other"]
    deleted = _ok(server, "DeleteSecret", {"SecretId": "app/a", "RecoveryWindowInDays": 7})
    assert deleted["DeletionDate"] > time.time() + 6 * 86400
    status, out = server.call("GetSecretValue", {"SecretId": "app/a"})
    assert status == 400 and out["__type"] == "InvalidRequestException"
    status, out = server.call("CreateSecret", {"Name": "app/a", "SecretString": "again"})
    assert status == 400 and out["__type"] == "InvalidRequestException"
    assert "app/a" not in names(_ok(server, "ListSecrets", {}))
    assert "app/a" in names(_ok(server, "ListSecrets", {"IncludePlannedDeletion": True}))
    _ok(server, "RestoreSecret", {"SecretId": "app/a"})
    assert _ok(server, "GetSecretValue", {"SecretId": "app/a"})["SecretString"] == "app/a"
    _ok(server, "DeleteSecret", {"SecretId": "other", "ForceDeleteWithoutRecovery": True})
    status, out = server.call("DescribeSecret", {"SecretId": "other"})
    assert status == 400 and out["__type"] == "ResourceNotFoundException"
    batch = _ok(server, "BatchGetSecretValue", {"SecretIdList": ["app/a", "other"]})
    assert [v["Name"] for v in batch["SecretValues"]] == ["app/a"] and batch["Errors"][0]["SecretId"] == "other"
    password = _ok(server, "GetRandomPassword", {"PasswordLength": 24, "ExcludePunctuation": True})["RandomPassword"]
    assert len(password) == 24 and password.isalnum()


def test_signature_is_checked(server):
    status, out = server.call("ListSecrets", {}, secret="wrong")
    assert status == 400 and out["__type"] == "InvalidSignatureException"
    status, out = server.call("ListSecrets", {}, access="NOBODY")
    assert out["__type"] == "UnrecognizedClientException"
    status, out = server.call("ListSecrets", {}, when=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert out["__type"] == "InvalidSignatureException" and "expired" in out["message"]
    req = urllib.request.Request(server.url + "/", data=b"{}", method="POST",
                                 headers={"x-amz-target": "secretsmanager.ListSecrets"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert json.loads(err.value.read())["__type"] == "MissingAuthenticationTokenException"


def test_data_file_survives_a_restart_and_is_private(server, tmp_path):
    _ok(server, "CreateSecret", {"Name": "kept", "SecretBinary": "AAFoaQ=="})
    server.stop()
    stored = json.loads((server.data / "secretsmanager.json").read_text())
    assert [s["Name"] for s in stored["secrets"]] == ["kept"]
    if os.name != "nt":
        assert (server.data / "secretsmanager.json").stat().st_mode & 0o777 == 0o600
    # The other implementation reads the same file.
    other = IMPLS[1].values[0] if server.proc.args[0] == sys.executable else IMPLS[0].values[0]
    if other[0] == "node" and not NODE:
        return
    again = Server(other, server.data)
    try:
        assert _ok(again, "GetSecretValue", {"SecretId": "kept"})["SecretBinary"] == "AAFoaQ=="
    finally:
        again.stop()
    server.proc = again.proc  # the fixture stops it again: harmless


def test_pskit_get_secret_and_put_secret(server, monkeypatch):
    for name, value in {"DRIVER": "aws", "URL": server.url, "REGION": "us-east-1", "ACCESS_KEY": ACCESS,
                        "SECRET_KEY": SECRET}.items():
        monkeypatch.setenv(f"PSKIT_VAULT1_{name}", value)
    monkeypatch.setattr(rt_secrets, "_cache", {})
    rt_secrets.put_secret("stripe", {"key": "sk_1", "webhook": "wh_1"}, store="vault1")
    assert rt_secrets.get_secret("stripe", "webhook", store="vault1") == "wh_1"
    rt_secrets.put_secret("stripe", {"key": "sk_2"}, store="vault1")   # PutSecretValue on the existing secret
    assert rt_secrets.get_secrets("stripe", store="vault1", refresh=True) == {"key": "sk_2"}
    with pytest.raises(rt_secrets.SecretNotFound):
        rt_secrets.get_secret("missing", store="vault1")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_pskit_node_runtime_reads_it(server, tmp_path):
    _ok(server, "CreateSecret", {"Name": "api", "SecretString": '{"token":"t-1"}'})
    script = tmp_path / "read.js"
    runtime = (ROOT / "src" / "py_serverless_kit" / "runtime_js" / "secrets.js").as_posix()
    script.write_text(f'const {{ getSecret }} = require("{runtime}");\n'
                      'getSecret("api", "token", { store: "vault1" }).then((v) => console.log(v), (e) => {\n'
                      '  console.error(e.message); process.exit(1); });\n')
    env = {**os.environ, "PSKIT_VAULT1_DRIVER": "aws", "PSKIT_VAULT1_URL": server.url,
           "PSKIT_VAULT1_ACCESS_KEY": ACCESS, "PSKIT_VAULT1_SECRET_KEY": SECRET, "PSKIT_VAULT1_REGION": "us-east-1"}
    out = subprocess.run([NODE, str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "t-1"


def test_boto3_works_unchanged(server):
    boto3 = pytest.importorskip("boto3")
    sm = boto3.client("secretsmanager", endpoint_url=server.url, region_name="eu-west-1",
                      aws_access_key_id=ACCESS, aws_secret_access_key=SECRET)
    arn = sm.create_secret(Name="b/one", SecretString="v1")["ARN"]
    assert ":eu-west-1:" in arn
    sm.put_secret_value(SecretId="b/one", SecretString="v2")
    assert sm.get_secret_value(SecretId=arn)["SecretString"] == "v2"
    pages = list(sm.get_paginator("list_secrets").paginate(PaginationConfig={"PageSize": 1}))
    assert [s["Name"] for p in pages for s in p["SecretList"]] == ["b/one"]
    with pytest.raises(sm.exceptions.ResourceNotFoundException):
        sm.get_secret_value(SecretId="nope")
    bad = boto3.client("secretsmanager", endpoint_url=server.url, region_name="eu-west-1",
                       aws_access_key_id=ACCESS, aws_secret_access_key="wrong")
    with pytest.raises(bad.exceptions.ClientError):
        bad.list_secrets()


def test_web_console_needs_a_login_and_the_csrf_token(server):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(path, body, headers=None):
        req = urllib.request.Request(server.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    with opener.open(server.url + "/ui/", timeout=5) as resp:
        page = resp.read().decode()
        assert "Secrets Manager" in page and "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert post("/ui/api/ListSecrets", {})[0] == 401
    assert post("/ui/login", {"user": ACCESS, "password": "wrong"})[0] == 401
    status, login = post("/ui/login", {"user": ACCESS, "password": SECRET})
    assert status == 200 and login["csrf"]
    assert post("/ui/api/ListSecrets", {})[0] == 403          # cookie without the page's token
    headers = {"X-SM-CSRF": login["csrf"]}
    assert post("/ui/api/CreateSecret", {"Name": "from-ui", "SecretString": "x"}, headers)[0] == 200
    status, listed = post("/ui/api/ListSecrets", {}, headers)
    assert [s["Name"] for s in listed["SecretList"]] == ["from-ui"]
    post("/ui/logout", {}, headers)
    assert post("/ui/api/ListSecrets", {}, headers)[0] == 401


def test_refuses_to_start_without_credentials(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SM_")}
    out = subprocess.run([sys.executable, str(SOURCE / "python" / "server.py")], env=env, capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 2 and "SM_SECRET_ACCESS_KEY" in out.stderr


# ----------------------------------------------------------------------------- the on-prem service


def _create(tmp_path, **body):
    fake = FakeDocker()
    req = {"key": "secrets", "service": "secretsmanager", "password": "Sup3r-secret-pw", **body}
    entry = svc.create(tmp_path, req, RAW, lambda _l: None, run=fake, wait_seconds=1)
    return entry, fake.calls


def test_catalog_lists_both_servers():
    items = {i["id"]: i for i in svc.catalog()}
    for sid, lang in (("secretsmanager", "Python"), ("secretsmanager-node", "Node.js")):
        item = items[sid]
        assert item["group"] == "Secrets" and item["auth"] and item["port"] == 4584 and item["backup"]
        assert lang in item["label"] and item["gui"] == ["Secrets console"]
        assert item["browsers"] == ["aws-secrets"] and onprem_console.CONSOLES[sid]["input"] == "cli"


def test_create_copies_the_server_into_a_stock_image(tmp_path):
    entry, calls = _create(tmp_path, user="AKIDLOCAL")
    create = next(c for c in calls if c[:2] == ["docker", "create"])
    env = dict(create[i + 1].split("=", 1) for i, arg in enumerate(create) if arg == "-e")
    assert env["SM_ACCESS_KEY_ID"] == "AKIDLOCAL" and env["SM_SECRET_ACCESS_KEY"] == "Sup3r-secret-pw"
    assert "127.0.0.1:4584:4584" in create and create[-3:] == ["python:3.12-alpine", "python", "/app/server.py"]
    cp = next(c for c in calls if c[:2] == ["docker", "cp"])
    assert cp[-1].endswith(":/app") and cp[2].endswith(".")
    assert calls.index(create) < calls.index(cp) < calls.index(["docker", "start", entry["container"]])
    assert entry["env"] == {"PSKIT_SECRETS_DRIVER": "aws", "PSKIT_SECRETS_URL": "http://127.0.0.1:4584",
                            "PSKIT_SECRETS_REGION": "us-east-1", "PSKIT_SECRETS_ACCESS_KEY": "AKIDLOCAL",
                            "PSKIT_SECRETS_SECRET_KEY": "Sup3r-secret-pw"}
    assert entry["resource"]["type"] == "custom" and entry["resource"]["connection"]["driver"] == "aws"
    assert "PSKIT_SECRETS_SECRET_KEY=Sup3r-secret-pw" in (tmp_path / ".env.onprem").read_text()
    links = svc.gui_links(entry)
    assert links[0]["url"] == "http://127.0.0.1:4584/ui/" and "AKIDLOCAL" in links[0]["login"]


def test_create_over_ssh_copies_with_scp_then_docker_cp(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "check_ssh_login", lambda _t: None)
    entry, calls = _create(tmp_path, service="secretsmanager-node", target=SSH)
    texts = [" ".join(c) for c in calls]
    scps = [t for t in texts if t.startswith("scp") or " scp " in t]
    assert sorted(t.rsplit("/", 1)[-1] for t in scps) == ["index.html", "server.js"]
    remote = f"/tmp/pskit-{entry['container']}-app"
    assert any(f"'cp' '{remote}/.' '{entry['container']}:/app'" in t for t in texts)
    create = next(t for t in texts if "docker 'create'" in t)
    assert "'node:22-alpine' 'node' '/app/server.js'" in create and "'0.0.0.0:4584:4584'" in create
    assert entry["env"]["PSKIT_SECRETS_URL"] == "http://20.40.60.80:4584"


def test_copied_files_are_the_repository_copy():
    files = onprem_secretsmanager.app_files("secretsmanager-node")
    assert files["server.js"] == (SOURCE / "nodejs" / "server.js").read_bytes()
    assert files["index.html"] == (SOURCE / "ui" / "index.html").read_bytes()


def test_usage_guide_and_browser_use_the_service_keys(tmp_path, monkeypatch):
    entry, _calls = _create(tmp_path, user="AKIDLOCAL", key="vault1")
    usage = svc.service_usage(tmp_path, "vault1", probe=False)["markdown"]
    assert "boto3.client(" in usage and 'get_secret("prod/db", "password", store="vault1")' in usage
    assert "PSKIT_VAULT1_SECRET_KEY" in usage and "http://127.0.0.1:4584/ui/" in usage
    seen = {}

    def fake_fetch(_control, _method, _path, _data, headers, timeout=None):
        seen.update(headers)
        return 200, {}, b'{"SecretList": []}'

    monkeypatch.setattr(onprem_browser, "_fetch", fake_fetch)
    assert onprem_browser.browse(entry, "aws-secrets", "list", {}) == {"items": [], "next": None}
    assert seen["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDLOCAL/")
