"""Project scaffolds for ``pskit init``."""

from __future__ import annotations

from typing import Dict

DEFAULT_REGIONS = {
    "aws": "us-east-1",
    "azure": "eastus",
    "gcp": "us-central1",
    "alibaba": "cn-hangzhou",
    "oracle": "us-ashburn-1",
    "ibm": "us-south",
    "digitalocean": "nyc1",
    "tencent": "ap-guangzhou",
    "huawei": "cn-north-4",
    "cloudflare": "global",
}

PROVIDER_OPTIONS = {
    "gcp": "  options:\n    project: ${env:GOOGLE_CLOUD_PROJECT}\n",
    "azure": "  options:\n    subscription_id: ${env:ARM_SUBSCRIPTION_ID}\n",
    "oracle": (
        '  options:\n    compartment_id: ${env:OCI_COMPARTMENT_ID}\n    subnet_ids: ["${env:OCI_SUBNET_ID}"]\n'
        "    image: ${env:OCI_FUNCTION_IMAGE}\n"
    ),
    "digitalocean": "  options:\n    namespace: ${env:DO_FUNCTIONS_NAMESPACE}\n",
    "cloudflare": "  options:\n    account_id: ${env:CLOUDFLARE_ACCOUNT_ID}\n",
}

HANDLERS_HTTP = '''"""HTTP handlers. This code runs unchanged on every supported cloud."""

from py_serverless_kit import Context, Event, NotFound, Response

_ITEMS = {"1": {"id": "1", "name": "first item"}}


def hello(event: Event, ctx: Context):
    name = event.http.query.get("name", "world")
    ctx.logger.info("saying hello", extra={"name": name})
    return {"message": f"Hello, {name}!", "provider": event.provider, "stage": ctx.stage}


def get_item(event: Event, ctx: Context):
    item = _ITEMS.get(event.http.path_params["id"])
    if item is None:
        raise NotFound(f"item {event.http.path_params['id']} does not exist")
    return item


def create_item(event: Event, ctx: Context):
    body = event.http.json()
    item = {"id": str(len(_ITEMS) + 1), **body}
    _ITEMS[item["id"]] = item
    return Response.json(item, status=201)
'''

HANDLERS_EVENTS = '''

def nightly_report(event: Event, ctx: Context):
    """Runs on a schedule; ``event.data`` is the optional ``input`` from the YAML."""
    ctx.logger.info("nightly report", extra={"input": event.data})
    return {"ok": True}


def process_jobs(event: Event, ctx: Context):
    """Queue consumer: only failed records are retried where the cloud supports it."""
    from py_serverless_kit import BatchResult

    result = BatchResult()
    for record in event.records:
        try:
            job = record.json()
            ctx.logger.info("processing job", extra={"job": job})
        except Exception:  # noqa: BLE001
            result.fail(record)
    return result


def on_upload(event: Event, ctx: Context):
    for record in event.records:
        ctx.logger.info("object event", extra={"bucket": record.bucket, "key": record.key, "action": record.action})
'''

HANDLERS_WORKFLOW = """

def validate_order(event: Event, ctx: Context):
    order = dict(event.data or {})
    if order.get("amount", 0) <= 0:
        raise ValueError("amount must be positive")
    order["valid"] = True
    return order


def charge_order(event: Event, ctx: Context):
    order = dict(event.data)
    order["charged"] = True
    return order
"""

CONFIG_HTTP = """service: {service}

provider:
  name: {provider}
  region: {region}
  stage: dev
  runtime: python3.12
  memory: 256
  timeout: 15
  environment:
    LOG_LEVEL: INFO
{options}
functions:
  hello:
    handler: app.handlers.hello
    description: Says hello
    events:
      - http: GET /hello

  get-item:
    handler: app.handlers.get_item
    events:
      - http: GET /items/{{id}}

  create-item:
    handler: app.handlers.create_item
    events:
      - http:
          method: POST
          path: /items
          cors: true
"""

CONFIG_EVENTS = """
  nightly-report:
    handler: app.handlers.nightly_report
    events:
      - schedule:
          cron: "0 2 * * *"
          input: {{report: daily}}

  process-jobs:
    handler: app.handlers.process_jobs
    events:
      - queue: {{name: {service}-jobs, batch_size: 10}}

  on-upload:
    handler: app.handlers.on_upload
    events:
      - storage: {{bucket: {service}-uploads-change-me, events: [created], suffix: .csv}}
"""

CONFIG_WORKFLOW = """
  validate-order:
    handler: app.handlers.validate_order

  charge-order:
    handler: app.handlers.charge_order

workflows:
  order-pipeline:
    description: Validate then charge an order
    start_at: Validate
    states:
      Validate:
        type: task
        function: validate-order
        retry: [{{errors: ["*"], max_attempts: 2}}]
        catch: [{{errors: ["ValueError"], next: Rejected}}]
        next: Charge
      Charge:
        type: task
        function: charge-order
        end: true
      Rejected:
        type: fail
        error: OrderRejected
"""

TEST_FILE = """from py_serverless_kit.local import LocalRunner, http_event
from py_serverless_kit.core.project import Project


def test_hello():
    runner = LocalRunner(Project.load())
    resp = runner.invoke("hello", http_event("GET", "/hello?name=test"))
    assert resp.status == 200
"""

GITIGNORE = ".pskit/\n.venv/\n__pycache__/\n*.pyc\n.env\n"


def render(template: str, service: str, provider: str) -> Dict[str, str]:
    region = DEFAULT_REGIONS.get(provider, "")
    config = CONFIG_HTTP
    handlers = HANDLERS_HTTP
    if template in ("events", "full"):
        config += CONFIG_EVENTS
        handlers += HANDLERS_EVENTS
    if template in ("workflow", "full"):
        config += CONFIG_WORKFLOW
        handlers += HANDLERS_WORKFLOW
    return {
        "pskit.yml": config.format(
            service=service, provider=provider, region=region, options=PROVIDER_OPTIONS.get(provider, "")
        ),
        "app/__init__.py": "",
        "app/handlers.py": handlers,
        "requirements.txt": "# Runtime dependencies of your functions (py-serverless-kit's runtime is vendored automatically)\n",
        "tests/test_handlers.py": TEST_FILE,
        ".gitignore": GITIGNORE,
    }
