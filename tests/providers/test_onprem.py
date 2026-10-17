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
    assert "onprem_ctl.py start" in create["local-exec"]["command"]
    assert destroy["local-exec"]["when"] == "destroy"
    assert "onprem_ctl.py stop" in destroy["local-exec"]["command"]

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
