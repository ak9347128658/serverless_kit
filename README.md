# py-serverless-kit

[![CI](https://github.com/ak9347128658/serverless_kit/actions/workflows/ci.yml/badge.svg)](https://github.com/ak9347128658/serverless_kit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/py-serverless-kit.svg)](https://pypi.org/project/py-serverless-kit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/ak9347128658/serverless_kit/blob/main/LICENSE)

**Write your Python functions once. Deploy them to any of 10 clouds, or to your own machine, by changing one line of YAML.**

`py-serverless-kit` (CLI: `pskit`) is a Serverless-Framework-style toolkit built on **Terraform**. Your handlers use one small, provider-neutral API (`Event`, `Context`, `Response`). A `pskit.yml` file says which cloud they run on and what triggers them: HTTP, schedules, queues, storage, topics. The same file can also define workflows in the style of Step Functions. `pskit deploy` generates Terraform for the chosen cloud, applies it, and builds an HTML dashboard of everything you deployed.

```python
# app/handlers.py - identical on AWS, Azure, GCP, Alibaba, Oracle, IBM, DigitalOcean, Tencent, Huawei, Cloudflare, and on premises
from py_serverless_kit import Event, Context, NotFound

def get_order(event: Event, ctx: Context):
    order = ORDERS.get(event.http.path_params["id"])
    if order is None:
        raise NotFound("no such order")      # -> HTTP 404 JSON on every cloud
    return order                              # -> HTTP 200 JSON
```

```yaml
# pskit.yml
service: orders
provider:
  name: aws          # <- change to gcp, azure, onprem, ... and redeploy. That's the migration.
  region: us-east-1
functions:
  get-order:
    handler: app.handlers.get_order
    events:
      - http: GET /orders/{id}
```

```bash
pskit deploy                      # terraform apply on AWS
pskit deploy --provider gcp       # same code, Google Cloud Functions
pskit dashboard --open            # every function, trigger, URL and resource id
```

---

## Why

Teams that build on AWS Lambda and Step Functions end up with code full of API Gateway event parsing, SQS record loops and hand-written ASL. When the business needs another cloud, every function has to be rewritten. `py-serverless-kit` fixes this at both layers:

| Layer | Locked-in way | With pskit |
|---|---|---|
| Handler code | Parses `event["requestContext"]["http"]["method"]`, `Records[].body`, ... | Uses `event.http.method`, `event.records[].body`: the same on every cloud |
| Triggers / infra | SAM / CloudFormation / console clicks | One `pskit.yml`, rendered to Terraform for the target cloud |
| Workflows | Step Functions ASL | Portable state machine, compiled to Step Functions, Google Cloud Workflows, or run by the built-in engine everywhere else |
| Visibility | One console per cloud | One generated dashboard across all clouds and stages |

## Features

- **10 clouds and an on-premises host:** AWS Lambda, Azure Functions, Google Cloud Functions (gen2), Alibaba Function Compute, Oracle Functions, IBM Code Engine, DigitalOcean Functions, Tencent SCF, Huawei FunctionGraph, Cloudflare Workers, plus a process on your own machine or a remote server (`onprem`: local, SSH/SCP of one archive with a `.pem`, or Docker — the image is built locally, Docker is installed on the server when it is missing, and deploy waits until the host is healthy — or a bundled CPython when the server has no Python).
- **Triggers:** HTTP (path params, CORS, auth), schedules (rate/cron, translated into each cloud's cron dialect), queues, object storage, pub/sub topics, and direct function-to-function `invoke()`.
- **Workflows:** task / choice / wait / pass / parallel / map / succeed / fail with retry and catch. Native on AWS and GCP; an embedded engine on the other clouds; runnable locally with `pskit workflow run`.
- **Terraform underneath:** real, reviewable `main.tf.json`, any backend (S3, GCS, azurerm, ...), works with OpenTofu. Escape hatches: `terraform.extra`, `${tf:...}` references, per-function `options`.
- **Config that scales:** `extends:`, per-provider `providers:` blocks, per-stage `stages:` blocks, and `${env:}`, `${opt:}`, `${self:}`, `${file()}` variables.
- **Local dev:** `pskit dev` (hot-reloading HTTP server), `pskit invoke` with synthetic queue/storage/schedule events, and a `LocalRunner` for pytest.
- **Dashboard:** a self-contained `index.html` listing every deployment, function, trigger, endpoint, ARN/id, console link and workflow.
- **Production details:** deterministic artifacts (no redeploy when nothing changed), partial batch failures (`BatchResult`), structured JSON logs, consistent HTTP error mapping, stack traces hidden from clients, a stdlib-only runtime (no extra dependencies inside your function), and hooks (`before_deploy`, ...).

## Supported providers

| Provider | Tier | HTTP | Schedule | Queue | Storage | Topic | Workflows |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| AWS Lambda | stable | ✅ | ✅ | ✅ SQS | ✅ S3 | ✅ SNS | native (Step Functions) |
| Google Cloud Functions | stable | ✅ | ✅ | ✅ Pub/Sub | ✅ GCS | ✅ Pub/Sub | native (Cloud Workflows) |
| Azure Functions | stable | ✅ | ✅ | ✅ Storage Queue | ✅ Blob | ✅ Service Bus | embedded |
| Alibaba Function Compute | beta | ✅ | ✅ | – | ✅ OSS | ✅ MNS | embedded |
| Huawei FunctionGraph | beta | ✅ | ✅ | – | ✅ OBS | ✅ SMN | embedded |
| Tencent SCF | beta | ✅ | ✅ | – | ✅ COS | – | embedded |
| DigitalOcean Functions | beta | ✅ | ✅ | – | – | – | embedded |
| IBM Code Engine | beta | ✅ | – | – | – | – | embedded |
| Oracle Cloud Functions | experimental | ✅ | – | – | ✅ | – | embedded |
| Cloudflare Workers (Python) | experimental | ✅ | ✅ | – | – | – | embedded |
| On-premises | experimental | ✅ | ✅ | ✅ files | ✅ directory | ✅ files | embedded (local, SSH, or Docker) |

Run `pskit providers` for the live matrix. If a config uses a trigger the target cloud can't support, **`pskit validate` fails with a clear message instead of deploying something half-working.**

> **Maturity, honestly stated:** the Terraform generated for every provider is checked with `terraform validate` against the real provider plugins (in CI, too). The *stable* providers cover the most surface area. *beta* and *experimental* providers follow vendor docs for event payloads that `validate` can't check, so try them on a non-production account first. See [docs/providers.md](https://github.com/ak9347128658/serverless_kit/blob/main/docs/providers.md) for each provider's details and limits.

## Install

```bash
pip install py-serverless-kit
```

You also need **Terraform ≥ 1.5** (or OpenTofu) on your `PATH`, plus credentials for your cloud. See [Getting started](https://github.com/ak9347128658/serverless_kit/blob/main/docs/getting-started.md#cloud-credentials).

## Quick start

```bash
pskit init my-api --provider aws --template full   # templates: http | events | workflow | full
cd my-api
pskit dev                                          # http://127.0.0.1:3000/hello
pskit invoke -f hello --path "/hello?name=Ada"
pskit workflow run order-pipeline -i '{"amount": 42}'
pskit deploy --stage dev
pskit info
pskit dashboard --open
pskit remove --stage dev
```

### Move to another cloud

Change `provider.name`, or keep one file with per-cloud blocks:

```yaml
provider:
  name: aws
  region: us-east-1
providers:              # applied only when that provider is selected
  gcp:   {region: europe-west1, options: {project: my-project}}
  azure: {region: westeurope, options: {subscription_id: "${env:ARM_SUBSCRIPTION_ID}"}}
```

```bash
pskit deploy --provider gcp
pskit deploy --provider azure --stage prod
```

### Triggers at a glance

```yaml
functions:
  api:
    handler: app.api.handle
    events:
      - http: {method: POST, path: /orders, cors: true}
      - http: GET /orders/{id}
  nightly:
    handler: app.jobs.nightly
    events:
      - schedule: {cron: "0 2 * * *", input: {report: daily}, timezone: Europe/Berlin}
  worker:
    handler: app.jobs.process
    timeout: 60
    events:
      - queue: {name: orders, batch_size: 10, dead_letter: orders-dlq}
  thumbnails:
    handler: app.media.on_upload
    events:
      - storage: {bucket: my-uploads-bucket, events: [created], suffix: .jpg}
  audit:
    handler: app.audit.on_event
    events:
      - topic: order-events
```

```python
from py_serverless_kit import BatchResult, Event, Context

def process(event: Event, ctx: Context):
    result = BatchResult()               # retry only failed messages where the cloud supports it
    for record in event.records:
        try:
            handle(record.json())
        except Exception:
            result.fail(record)
    return result
```

### Workflows (portable Step Functions)

```yaml
workflows:
  order-pipeline:
    start_at: Validate
    states:
      Validate:
        type: task
        function: validate-order
        retry: [{errors: [TimeoutError], max_attempts: 3, backoff_rate: 2}]
        catch: [{errors: [ValueError], next: Rejected}]
        next: Route
      Route:
        type: choice
        choices: [{variable: "$.amount", gt: 1000, next: ManualReview}]
        default: Charge
      ManualReview: {type: wait, seconds: 3600, next: Charge}
      Charge: {type: task, function: charge-order, end: true}
      Rejected: {type: fail, error: OrderRejected}
```

The same definition becomes **AWS Step Functions** on AWS, **Google Cloud Workflows** on GCP, and an **embedded orchestrator function** on the other clouds. `pskit workflow show order-pipeline --format asl` prints the generated ASL.

## How it works

```
 pskit.yml ──► config loader ──► provider validation ──► packager ──────────► artifact.zip
 (extends, providers:,          (capabilities,           (your code + stdlib-only
  stages:, ${vars})              limits, runtimes)        runtime + manifest + shim)
                                        │
                                        ▼
                              provider renderer ──► .pskit/<provider>-<stage>/main.tf.json
                                                              │ terraform init / apply
                                                              ▼
                                       deployment.json ──► dashboard/index.html

 At runtime:  cloud event ──► shim ──► runtime.adapters.<cloud> ──► Event ──► your handler
              your result ──► Response / raw value ──► adapter ──► native cloud response
```

More: [docs/architecture.md](https://github.com/ak9347128658/serverless_kit/blob/main/docs/architecture.md).

## Examples

Sixteen runnable scenarios live in [`examples/`](https://github.com/ak9347128658/serverless_kit/tree/main/examples/). They are tested in CI.

| | |
|---|---|
| [01-hello-http](https://github.com/ak9347128658/serverless_kit/tree/main/examples/01-hello-http) | Minimal HTTP function |
| [02-rest-api](https://github.com/ak9347128658/serverless_kit/tree/main/examples/02-rest-api) | CRUD, path params, validation errors, CORS, cookies, binary, JWT |
| [03-scheduled-jobs](https://github.com/ak9347128658/serverless_kit/tree/main/examples/03-scheduled-jobs) | rate/cron schedules with input and time zones |
| [04-queue-worker](https://github.com/ak9347128658/serverless_kit/tree/main/examples/04-queue-worker) | Queue consumer, partial batch failures, dead-letter queue |
| [05-storage-events](https://github.com/ak9347128658/serverless_kit/tree/main/examples/05-storage-events) | Object created/deleted with filters |
| [06-pubsub-topic](https://github.com/ak9347128658/serverless_kit/tree/main/examples/06-pubsub-topic) | Topic fan-out and filters |
| [07-workflow-orders](https://github.com/ak9347128658/serverless_kit/tree/main/examples/07-workflow-orders) | Every workflow state type, retry/catch |
| [08-multi-cloud](https://github.com/ak9347128658/serverless_kit/tree/main/examples/08-multi-cloud) | Same code, all 11 providers |
| [16-on-premises](https://github.com/ak9347128658/serverless_kit/tree/main/examples/16-on-premises) | HTTP, schedule, queue, storage, topic and a workflow on one machine |
| [09-function-invoke](https://github.com/ak9347128658/serverless_kit/tree/main/examples/09-function-invoke) | Function-to-function `invoke()` |
| [10-stages-and-config](https://github.com/ak9347128658/serverless_kit/tree/main/examples/10-stages-and-config) | Stages, variables, secrets |
| [11-custom-terraform](https://github.com/ak9347128658/serverless_kit/tree/main/examples/11-custom-terraform) | `terraform.extra`, `${tf:...}`, remote state, hooks |
| [12-async-and-context](https://github.com/ak9347128658/serverless_kit/tree/main/examples/12-async-and-context) | async handlers, logging, deadlines |
| [13-migrate-from-aws-lambda](https://github.com/ak9347128658/serverless_kit/tree/main/examples/13-migrate-from-aws-lambda) | Before/after migration of Lambda + Step Functions |
| [14-testing](https://github.com/ak9347128658/serverless_kit/tree/main/examples/14-testing) | Unit-testing handlers and workflows with pytest |
| [15-ci-cd-deploy](https://github.com/ak9347128658/serverless_kit/tree/main/examples/15-ci-cd-deploy) | GitHub Actions deploys per stage |

## Documentation

| | |
|---|---|
| [Getting started](https://github.com/ak9347128658/serverless_kit/blob/main/docs/getting-started.md) | Install, credentials, first deploy |
| [Configuration reference](https://github.com/ak9347128658/serverless_kit/blob/main/docs/configuration.md) | Every `pskit.yml` key, variables, `extends`, stages |
| [Events](https://github.com/ak9347128658/serverless_kit/blob/main/docs/events.md) | HTTP, schedule, queue, storage, topic, with a per-provider matrix |
| [Handler API](https://github.com/ak9347128658/serverless_kit/blob/main/docs/handlers.md) | `Event`, `Context`, `Response`, errors, `BatchResult`, `invoke()` |
| [Workflows](https://github.com/ak9347128658/serverless_kit/blob/main/docs/workflows.md) | State machine reference, engines, limitations |
| [Providers](https://github.com/ak9347128658/serverless_kit/blob/main/docs/providers.md) | Options, credentials, resources and limits per cloud |
| [CLI](https://github.com/ak9347128658/serverless_kit/blob/main/docs/cli.md) | Every command and flag |
| [Local development](https://github.com/ak9347128658/serverless_kit/blob/main/docs/local-development.md) | `pskit dev`, `invoke`, testing |
| [Dashboard](https://github.com/ak9347128658/serverless_kit/blob/main/docs/dashboard.md) | The deployment UI |
| [Architecture](https://github.com/ak9347128658/serverless_kit/blob/main/docs/architecture.md) | Internals and third-party providers |
| [Migrating from AWS](https://github.com/ak9347128658/serverless_kit/blob/main/docs/migrating-from-aws.md) | Lambda + Step Functions → pskit |
| [Troubleshooting](https://github.com/ak9347128658/serverless_kit/blob/main/docs/troubleshooting.md) | Common errors and fixes |

## Contributing & releasing

- Contributor guide: [deploy_docs/CONTRIBUTING.md](https://github.com/ak9347128658/serverless_kit/blob/main/deploy_docs/CONTRIBUTING.md)
- Quick PyPI release guide: [deploy_docs/How_to_deploy.md](https://github.com/ak9347128658/serverless_kit/blob/main/deploy_docs/How_to_deploy.md)
- Publishing to PyPI (step by step): [deploy_docs/PUBLISHING_TO_PYPI.md](https://github.com/ak9347128658/serverless_kit/blob/main/deploy_docs/PUBLISHING_TO_PYPI.md)
- Security policy: [SECURITY.md](https://github.com/ak9347128658/serverless_kit/blob/main/SECURITY.md)

## Author

Created and maintained by **Asif Khan @ [Kavion.ai](https://kavion.ai)**.

Source code, issues and releases: <https://github.com/ak9347128658/serverless_kit>

## License

MIT, see [LICENSE](https://github.com/ak9347128658/serverless_kit/blob/main/LICENSE).
