"""On-premises provider: every trigger renders, and missing services are declared."""

import json
from pathlib import Path

from tests.conftest import full_config

CONFIG = """
service: demo
provider:
  name: onprem
  options:
    host: 127.0.0.1
    port: 9090
    services:
      queue: builtin
      storage: {use: directory, path: /data/buckets}
      topic: builtin
functions:
  hello:
    handler: app.handlers.hello
    events:
      - http: GET /hello
  tick:
    handler: app.handlers.tick
    events:
      - schedule: 7 minutes
      - schedule: {cron: "0 12 * * 1", input: {report: weekly}, timezone: UTC}
  worker:
    handler: app.handlers.worker
    events:
      - queue: {name: jobs, dead_letter: jobs-dlq, max_receive: 3}
      - storage: {bucket: uploads, prefix: in/, suffix: .csv}
      - topic: {name: events, filter: {event_type: [created]}}
"""


def _errors(project):
    return [str(i) for i in project.validate() if i.level == "error"]


def test_full_config_accepts_every_trigger(make_project):
    project = make_project(full_config("onprem"))
    assert _errors(project) == []
    assert not any("capped by the function timeout" in str(i) for i in project.validate())


def test_render_deploys_builtin_services(make_project, render):
    project = make_project(CONFIG)
    assert _errors(project) == []
    tf = render(project)
    assert tf["terraform"]["required_providers"] == {}
    assert "provider" not in tf
    host = tf["resource"]["terraform_data"]["host"]
    create, destroy = host["provisioner"]
    assert create["local-exec"]["command"] == "apply"
    assert create["local-exec"]["interpreter"][-1] == "onprem_deploy.py"
    assert destroy["local-exec"]["when"] == "destroy"
    assert destroy["local-exec"]["command"] == "destroy"

    root = project.work_dir / "onprem_app"
    spec = json.loads((root / "pskit_host.json").read_text("utf-8"))
    assert spec["endpoint"] == "http://127.0.0.1:9090"
    assert spec["services"]["queue"]["use"] == "builtin"
    assert spec["services"]["queue"]["path"].endswith("queues")
    # A leading slash is absolute on POSIX and drive-anchored on Windows.
    assert spec["services"]["storage"]["use"] == "directory"
    assert Path(spec["services"]["storage"]["path"]) == (project.root / Path("/data/buckets"))
    assert spec["services"]["workflow"]["use"] == "embedded"
    worker = spec["functions"]["worker"]["events"]
    queue = next(ev for ev in worker if ev["type"] == "queue")
    assert queue["dead_letter"] == "jobs-dlq"
    assert queue["visibility_timeout"] >= 30
    assert (root / "onprem_ctl.py").is_file()
    assert (root / "onprem_deploy.py").is_file()
    assert spec["deploy"]["method"] == "local"
    assert (root / "staging" / "pskit_manifest.json").is_file()
    assert (root / "staging" / "onprem_handler.py").is_file()
    assert not (root / "app").exists()

    out = tf["output"]["pskit"]["value"]
    assert out["endpoint"] == "http://127.0.0.1:9090"
    assert out["functions"]["hello"]["url"] == "http://127.0.0.1:9090"
    assert out["functions"]["tick"]["url"] is None
    assert out["services"]["storage"]["use"] == "directory"


def test_directory_binding_requires_a_path(make_project):
    project = make_project(CONFIG.replace("path: /data/buckets", "use: directory"))
    assert any("path is required" in err for err in _errors(project))


def test_schedule_service_is_only_builtin(make_project):
    text = CONFIG.replace("queue: builtin", "schedule: {use: directory, path: /tmp}")
    assert any("services.schedule" in err for err in _errors(make_project(text)))


def test_auth_is_rejected(make_project):
    text = CONFIG.replace("http: GET /hello", "http: {path: /hello, auth: jwt}")
    assert any("auth `jwt`" in err for err in _errors(make_project(text)))


def test_impossible_cron_is_an_error(make_project):
    text = CONFIG.replace('cron: "0 12 * * 1"', 'cron: "0 0 31 2 *"')
    assert any("does not match" in err for err in _errors(make_project(text)))


def test_foreign_terraform_is_omitted(make_project, render):
    text = CONFIG.replace(
        "name: onprem\n",
        "name: onprem\n"
        "  terraform:\n"
        "    extra:\n"
        "      resource:\n"
        "        aws_dynamodb_table:\n"
        "          orders: {name: orders, hash_key: id, billing_mode: PAY_PER_REQUEST}\n"
        "        terraform_data:\n"
        "          note: {input: {ok: true}}\n"
        "      output:\n"
        '        orders_table_arn: {value: "${aws_dynamodb_table.orders.arn}"}\n'
        '        kept: {value: "yes"}\n',
    )
    project = make_project(text)
    warnings = [str(i) for i in project.validate() if i.level == "warning"]
    assert any("aws_dynamodb_table" in warning for warning in warnings)
    tf = render(project)
    assert "aws_dynamodb_table" not in tf["resource"]
    assert "note" in tf["resource"]["terraform_data"]
    assert "orders_table_arn" not in tf["output"]
    assert tf["output"]["kept"]["value"] == "yes"


SSH_CONFIG = """
service: demo
provider:
  name: onprem
  architecture: x86_64
  options:
    host: 0.0.0.0
    port: 9090
    deploy:
      method: ssh
      host: 10.0.1.12
      user: pskit
      path: /opt/pskit/demo
      python: bundle
      target_os: linux
      install: systemd
      sudo: true
functions:
  hello:
    handler: app.handlers.hello
    events:
      - http: GET /hello
"""

DOCKER_CONFIG = """
service: demo
provider:
  name: onprem
  options:
    host: 0.0.0.0
    port: 9090
    deploy:
      method: docker
      image: pskit-demo:dev
      docker_host: ssh://pskit@10.0.1.12
functions:
  hello:
    handler: app.handlers.hello
    events:
      - http: GET /hello
"""


def test_ssh_bundle_systemd_renders(make_project, render):
    project = make_project(SSH_CONFIG)
    assert _errors(project) == []
    tf = render(project)
    root = project.work_dir / "onprem_app"
    spec = json.loads((root / "pskit_host.json").read_text("utf-8"))
    assert spec["deploy"]["method"] == "ssh"
    assert spec["deploy"]["python"] == "bundle"
    assert spec["deploy"]["ssh"]["host"] == "10.0.1.12"
    assert spec["deploy"]["ssh"]["path"] == "/opt/pskit/demo"
    assert spec["endpoint"] == "http://10.0.1.12:9090"
    assert spec["data_dir"] == "/opt/pskit/demo/data"
    assert (root / "pskit.service").is_file()
    unit = (root / "pskit.service").read_text("utf-8")
    assert "ExecStart=" in unit and "/opt/pskit/demo/python/bin/python3" in unit
    assert tf["output"]["pskit"]["value"]["deploy"] == "ssh"
    flags = project.provider.pip_platform_for(project.config)
    assert "--platform" in flags and "manylinux2014_x86_64" in flags


def test_ssh_url_shorthand(make_project, render):
    text = """
service: demo
provider:
  name: onprem
  options:
    host: 0.0.0.0
    port: 9090
    deploy: ssh://ops@factory.internal:2222/opt/pskit/demo
functions:
  hello:
    handler: app.handlers.hello
    events:
      - http: GET /hello
"""
    project = make_project(text)
    assert _errors(project) == []
    render(project)
    spec = json.loads((project.work_dir / "onprem_app" / "pskit_host.json").read_text("utf-8"))
    assert spec["deploy"]["method"] == "ssh"
    assert spec["deploy"]["ssh"]["user"] == "ops"
    assert spec["deploy"]["ssh"]["host"] == "factory.internal"
    assert spec["deploy"]["ssh"]["port"] == 2222
    assert spec["deploy"]["ssh"]["path"] == "/opt/pskit/demo"


def test_ssh_requires_host(make_project):
    text = SSH_CONFIG.replace("      host: 10.0.1.12\n", "")
    assert any("host is required" in err for err in _errors(make_project(text)))


def test_unknown_deploy_method(make_project):
    text = CONFIG.replace("port: 9090\n", "port: 9090\n    deploy: {method: ftp}\n")
    assert any("method must be one of" in err for err in _errors(make_project(text)))


def test_systemd_rejected_on_windows_target(make_project):
    text = SSH_CONFIG.replace("target_os: linux", "target_os: windows")
    assert any("systemd is only valid" in err for err in _errors(make_project(text)))


def test_docker_writes_dockerfile(make_project, render):
    project = make_project(DOCKER_CONFIG)
    assert _errors(project) == []
    render(project)
    root = project.work_dir / "onprem_app"
    spec = json.loads((root / "pskit_host.json").read_text("utf-8"))
    assert spec["deploy"]["method"] == "docker"
    assert spec["host"] == "0.0.0.0"
    assert spec["data_dir"] == "/var/pskit/data"
    assert spec["deploy"]["ssh"]["host"] == "10.0.1.12"
    assert spec["deploy"]["ssh"]["user"] == "pskit"
    assert spec["deploy"]["docker"]["install_docker"] is True
    assert spec["deploy"]["sudo"] is True
    docker = (root / "Dockerfile").read_text("utf-8")
    assert docker.startswith("FROM python:3.12-slim-bookworm")
    assert "onprem_ctl.py" in docker
    assert spec["endpoint"] == "http://10.0.1.12:9090"
    warnings = [str(i) for i in project.validate() if i.level == "warning"]
    assert any("pem" in warning for warning in warnings)


REMOTE_DOCKER = """
service: demo
provider:
  name: onprem
  options:
    host: 0.0.0.0
    port: 9090
    deploy:
      method: docker
      host: 10.0.1.12
      user: pskit
      pem: keys/server.pem
      image: pskit-demo:dev
functions:
  hello:
    handler: app.handlers.hello
    events:
      - http: GET /hello
"""


def test_remote_docker_pem_is_stored_absolute(make_project, render):
    project = make_project(REMOTE_DOCKER)
    assert _errors(project) == []
    render(project)
    spec = json.loads((project.work_dir / "onprem_app" / "pskit_host.json").read_text("utf-8"))
    identity = Path(spec["deploy"]["ssh"]["identity_file"])
    assert identity.is_absolute()
    assert identity.name == "server.pem"
    assert spec["deploy"]["docker"]["install_docker"] is True
    assert spec["endpoint"] == "http://10.0.1.12:9090"


def test_local_pip_platform_stays_native(make_project):
    project = make_project(CONFIG)
    assert project.provider.pip_platform_for(project.config) == []
