"""The on-premises host serves HTTP and the backing services it deploys."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from py_serverless_kit.core.deployer import Deployer
from py_serverless_kit.runtime.onprem_host import Host, activate_layout

HANDLERS = """
import os
from pathlib import Path

from py_serverless_kit import BatchResult, Context, Event, invoke


def hello(event: Event, ctx: Context):
    return {"provider": event.provider, "name": event.http.query.get("name", "")}


def parent(event: Event, ctx: Context):
    return invoke("child", {"n": 2})


def child(event: Event, ctx: Context):
    return {"n": event.data["n"] + 1}


def tick(event: Event, ctx: Context):
    Path(os.environ["OUT"]).write_text("schedule:" + json_dump(event.data), encoding="utf-8")
    return {"ok": True}


def json_dump(value):
    import json
    return json.dumps(value)


def worker(event: Event, ctx: Context):
    result = BatchResult()
    done = []
    for record in event.records:
        if record.text() == "fail":
            result.fail(record)
        else:
            done.append(record.text())
    path = Path(os.environ["OUT"])
    prev = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(prev + ",".join(done), encoding="utf-8")
    return result


def on_upload(event: Event, ctx: Context):
    text = ",".join(f"{record.action}:{record.key}" for record in event.records)
    Path(os.environ["OUT"]).write_text("storage:" + text, encoding="utf-8")


def on_orders(event: Event, ctx: Context):
    kind = event.records[0].attributes.get("event_type", "")
    Path(os.environ["OUT"]).write_text("topic:" + kind, encoding="utf-8")


def on_all(event: Event, ctx: Context):
    Path(os.environ["ALL"]).write_text(event.records[0].text(), encoding="utf-8")


def step(event: Event, ctx: Context):
    data = dict(event.data or {})
    data["seen"] = True
    return data
"""

CONFIG = """
service: demo
provider:
  name: onprem
  runtime: python3.12
  options:
    host: 127.0.0.1
    port: 8099
    data_dir: {data}
    services:
      storage: {{use: directory, path: {buckets}}}
  environment:
    OUT: "{out}"
    ALL: "{all_path}"
functions:
  hello:
    handler: app.handlers.hello
    events: [{{http: GET /hello}}]
  parent:
    handler: app.handlers.parent
  child:
    handler: app.handlers.child
  tick:
    handler: app.handlers.tick
    events: [{{schedule: 1 minutes}}]
  worker:
    handler: app.handlers.worker
    events:
      - queue: {{name: jobs, batch_size: 10, max_receive: 2, dead_letter: jobs-dlq}}
  on-upload:
    handler: app.handlers.on_upload
    events:
      - storage: {{bucket: uploads, prefix: in/, suffix: .csv, events: [created, deleted]}}
  on-orders:
    handler: app.handlers.on_orders
    events:
      - topic: {{name: orders, filter: {{event_type: [created]}}}}
  on-all:
    handler: app.handlers.on_all
    events: [{{topic: orders}}]
  step:
    handler: app.handlers.step
workflows:
  ping:
    start_at: Go
    states:
      Go: {{type: task, function: step, end: true}}
"""


def _project(make_project, tmp_path: Path):
    data = tmp_path / "data"
    buckets = tmp_path / "buckets"
    out = tmp_path / "out.txt"
    all_path = tmp_path / "all.txt"
    text = CONFIG.format(
        data=data.as_posix(),
        buckets=buckets.as_posix(),
        out=out.as_posix(),
        all_path=all_path.as_posix(),
    )
    project = make_project(text, files={"app/handlers.py": HANDLERS})
    Deployer(project, echo=lambda _m: None).package(skip_deps=True)
    root = project.work_dir / "onprem_app"
    activate_layout(root)
    host = Host(root)
    host.open()
    return host, out, all_path


def _server(host: Host):
    httpd = host.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _json(method: str, url: str, body=None, token=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    if token:
        req.add_header("x-pskit-token", token)
    with urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_http_invoke_schedule_queue_storage_topic_workflow(make_project, tmp_path):
    host, out, all_path = _project(make_project, tmp_path)
    httpd, base = _server(host)
    try:
        status, health = _json("GET", base + "/__pskit/health")
        assert status == 200 and health["ok"] is True
        assert health["services"]["storage"]["use"] == "directory"
        assert health["services"]["queue"]["use"] == "builtin"

        status, hello = _json("GET", base + "/hello?name=Ada")
        assert status == 200 and hello == {"provider": "onprem", "name": "Ada"}
        assert host.invoke("parent", __import__("py_serverless_kit").Event(type="invoke")) == {"n": 3}

        host.rewind_schedules(120)
        host.pump_schedules(__import__("time").time())
        assert out.read_text(encoding="utf-8").startswith("schedule:")

        status, queued = _json("POST", base + "/__pskit/queues/jobs", ["ok", "fail"])
        assert status == 202 and len(queued["queued"]) == 2
        host.pump_queues(__import__("time").time())
        assert "ok" in out.read_text(encoding="utf-8")
        assert host.broker.queue_depth("jobs") == 1
        host.pump_queues(__import__("time").time())
        assert host.broker.queue_depth("jobs") == 0
        assert host.broker.queue_depth("jobs-dlq") == 1

        raw = Request(base + "/__pskit/storage/uploads/in/a.csv", data=b"h,v\n", method="PUT")
        with urlopen(raw, timeout=5) as resp:
            assert resp.status == 201
        assert "storage:created:in/a.csv" in out.read_text(encoding="utf-8")
        note = Request(base + "/__pskit/storage/uploads/note.txt", data=b"x", method="PUT")
        with urlopen(note, timeout=5) as resp:
            assert resp.status == 201
        assert "note.txt" not in out.read_text(encoding="utf-8")

        status, published = _json(
            "POST",
            base + "/__pskit/topics/orders",
            {"message": {"id": 1}, "attributes": {"event_type": "created"}},
        )
        assert status == 202 and published["published"]
        assert out.read_text(encoding="utf-8").endswith("topic:created") or "topic:created" in out.read_text(
            encoding="utf-8"
        )
        assert all_path.read_text(encoding="utf-8")

        status, workflow = _json("POST", base + "/__pskit/workflows/ping", {"order": 1})
        assert status == 200
        body = workflow if "output" in workflow else workflow
        assert body["status"] == "SUCCEEDED"
        assert body["output"]["seen"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_admin_token_gates_the_broker(make_project, tmp_path):
    host, _out, _all = _project(make_project, tmp_path)
    host.spec["admin_token"] = "secret"
    httpd, base = _server(host)
    try:
        try:
            _json("POST", base + "/__pskit/queues/jobs", {"a": 1})
            raised = False
        except HTTPError as exc:
            raised = True
            assert exc.code == 401
        assert raised
        status, _body = _json("POST", base + "/__pskit/queues/jobs", {"a": 1}, token="secret")
        assert status == 202
        status, hello = _json("GET", base + "/hello")
        assert status == 200 and hello["provider"] == "onprem"
    finally:
        httpd.shutdown()
        httpd.server_close()
