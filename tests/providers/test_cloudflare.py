import hashlib
import json
import subprocess
import sys
import textwrap

from tests.conftest import full_config

CONFIG = """
service: demo
provider:
  name: cloudflare
  options: {account_id: acc1, workers_subdomain: myco, zone_id: zone1}
functions:
  hello:
    handler: app.handlers.hello
    events:
      - http: "GET /hello/{name}"
    options: {route: "api.example.com/hello*"}
  tick:
    handler: app.handlers.tick
    events:
      - schedule: 5 minutes
      - schedule: {cron: "0 12 * * 1", input: {report: weekly}}
"""

# Executes a generated worker module with a fake `workers` SDK, in a clean interpreter
# so the in-memory import hook cannot leak into the test process.
RUNNER = textwrap.dedent(
    """
    import asyncio, json, runpy, sys, types
    w = types.ModuleType("workers")
    class WorkerEntrypoint:
        env = None
    class Response:
        def __init__(self, body, status=200, headers=None):
            self.body, self.status, self.headers = body, status, headers
    w.WorkerEntrypoint, w.Response = WorkerEntrypoint, Response
    sys.modules["workers"] = w
    hello = runpy.run_path(sys.argv[1])
    class Req:
        method = "GET"
        url = "https://demo-dev-hello.myco.workers.dev/hello/ada"
        headers = {"cf-ray": "r1"}
        async def text(self):
            return ""
    r = asyncio.run(hello["Default"]().fetch(Req()))
    tick = runpy.run_path(sys.argv[2])
    class Ctl:
        cron = "0 12 * * 1"
        scheduledTime = 0
    s = asyncio.run(tick["Default"]().scheduled(Ctl()))
    import py_serverless_kit
    print(json.dumps({"status": r.status, "body": json.loads(r.body), "scheduled": s,
                      "runtime": py_serverless_kit.__file__}))
    """
)


def _errors(project):
    return [str(i) for i in project.validate() if i.level == "error"]


def test_validation(make_project):
    project = make_project(full_config("cloudflare"))
    issues = project.validate()
    joined = "\n".join(str(i) for i in issues if i.level == "error")
    assert "options.account_id is required" in joined
    for kind in ("queue", "storage", "topic"):
        assert f"`{kind}` events" in joined
    assert any("Pyodide" in str(i) for i in issues if i.level == "warning")


def test_render_and_run_worker(make_project, render, tmp_path):
    project = make_project(CONFIG)
    assert _errors(project) == []
    tf = render(project)
    assert tf["terraform"]["required_providers"]["cloudflare"]["source"] == "cloudflare/cloudflare"
    res = tf["resource"]
    script = res["cloudflare_workers_script"]["hello"]
    assert script["script_name"] == "demo-dev-hello"
    assert script["content_type"] == "text/x-python"
    assert script["compatibility_flags"] == ["python_workers"]
    hello_file = project.work_dir / "cloudflare" / "hello.py"
    tick_file = project.work_dir / "cloudflare" / "tick.py"
    assert script["content_file"] == "${path.module}/cloudflare/hello.py"
    assert script["content_sha256"] == hashlib.sha256(hello_file.read_bytes()).hexdigest()
    assert "route" not in script
    assert res["cloudflare_workers_route"]["hello"]["pattern"] == "api.example.com/hello*"
    assert "hello" in res["cloudflare_workers_script_subdomain"]
    assert "tick" not in res["cloudflare_workers_script_subdomain"]
    crons = [s["cron"] for s in res["cloudflare_workers_cron_trigger"]["tick"]["schedules"]]
    assert crons == ["*/5 * * * *", "0 12 * * 1"]
    out = tf["output"]["pskit"]["value"]
    assert out["functions"]["hello"]["url"] == "https://demo-dev-hello.myco.workers.dev"

    runner = tmp_path / "run_worker.py"
    runner.write_text(RUNNER)
    proc = subprocess.run(
        [sys.executable, "-I", str(runner), str(hello_file), str(tick_file)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == 200
    assert result["body"]["message"] == "hello ada"
    assert result["scheduled"] == {"type": "schedule", "data": {"report": "weekly"}}
    assert result["runtime"].startswith("/pskit_bundle/")
