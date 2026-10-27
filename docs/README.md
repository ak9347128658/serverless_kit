# py-serverless-kit documentation

Write a Python function once, describe its triggers in `pskit.yml`, and deploy it to any of 10 clouds or to an on-premises host with Terraform. Moving to another target means changing `provider.name`. The handler code stays the same.

| Page | What it covers |
|---|---|
| [Scenarios](scenarios.md) | Step-by-step guides by use case: HTTP API, queues, existing resources, Redis/MinIO on premises, databases, workflows, schedules, stages, logs, testing |
| [Getting started](getting-started.md) | Install, scaffold a project, run it locally, set up cloud credentials, deploy, inspect, remove |
| [Configuration reference](configuration.md) | Every `pskit.yml` key, `extends:`, `providers:`/`stages:` overrides, variables, escape hatches |
| [Events (triggers)](events.md) | `http`, `schedule`, `queue`, `storage`, `topic`, `smtp`, with shorthand forms and per-provider support |
| [Functions on several servers (on premises)](placement.md) | Each function in its own folder with its own requirements; groups of functions on different servers (or clusters) that call each other, share queues and run workflows across machines |
| [Network and firewall rules (on premises)](onprem-network.md) | Which server must allow which inbound / outbound port for one server, function groups, clusters (Swarm, k3s, Nomad), joining servers, load balancers, resources and SMTP |
| [Load balancers (on premises)](load-balancers.md) | Six algorithms (round robin, sticky, weighted, IP hash, least connections, least time); one load balancer shared by projects and clusters by path |
| [Scaling cluster (on premises)](cluster.md) | Run on-prem functions on a master plus joined servers with Docker Swarm, Kubernetes (k3s) or Nomad: join servers on the go, monitor CPU / memory / disk, scale replicas, alert DevOps (a function or webhook) when every server is full |
| [E-mail triggers (SMTP)](smtp/README.md) | On premises: an SMTP server (Node.js) that receives mail for your domain and runs functions with from, to, subject, body and attachments (also in the Amazon SES event shape); DNS (A / MX) records, port 25, testing |
| [Node.js and TypeScript](nodejs.md) | Handlers in JavaScript or TypeScript: runtimes per provider, `tsc`, npm dependencies, local runs |
| [Handler API](handlers.md) | `Event`, `HttpRequest`, `Record`, `Context`, `Response`, errors, `BatchResult`, `invoke()`, logging |
| [Workflows](workflows.md) | State machines: native AWS Step Functions / GCP Workflows, or the embedded engine anywhere |
| [Workflow executions](workflow-executions.md) | Execution ids, async start, status/history/cancel from the CLI, dashboard and HTTP |
| [Resources](resources.md) | `resources:`: managed or external storage, queues, topics, databases and other services; on-prem Redis and S3 drivers |
| [Existing cloud code](resource-guides/compatibility.md) | Lambda ARNs, boto3 Lambda / SQS code, S3 / Azure / GCS SDKs: what works on each provider, the one-line change, the pskit way |
| [pskit client and HTTP API](client.md) | Call an on-prem host from any Python / Node.js program or over HTTP: queues, buckets, topics, functions; tunnels and timeouts |
| [Using resources from code](resource-usage.md) | Libraries to install (Python / Node.js) for every resource type and provider, handler code, local and deployed tests |
| [Resource guides](resource-guides/README.md) | Step by step, per cloud and type: create a queue, bucket, topic, database, cache or other service in the console, find its details and add it to `pskit.yml` |
| [Providers](providers.md) | Capability matrix plus one section per cloud: options, credentials, resources, limits |
| [CLI reference](cli.md) | Every `pskit` command and flag |
| [Local development](local-development.md) | `pskit dev`, `pskit invoke`, `.env`, testing with `LocalRunner` |
| [Studio](studio.md) | `pskit studio`: build functions, triggers, workflows, resources and on-prem drivers in a web UI, edit code (built-in editor or VS Code in Docker) and deploy |
| [Dashboard](dashboard.md) | The deployment UI: static page, or live with logs and workflow executions (`--serve`) |
| [Architecture](architecture.md) | How config becomes Terraform and how the runtime dispatches events; third-party providers |
| [Migrating from AWS](migrating-from-aws.md) | Moving existing Lambda functions and Step Functions to pskit, then to other clouds |
| [Troubleshooting & FAQ](troubleshooting.md) | Common errors, provider gotchas, security notes |

Runnable examples for every scenario live in [`../examples`](../examples/README.md).

Maintainers: see [`../deploy_docs/PUBLISHING_TO_PYPI.md`](../deploy_docs/PUBLISHING_TO_PYPI.md) and [`../deploy_docs/CONTRIBUTING.md`](../deploy_docs/CONTRIBUTING.md).
