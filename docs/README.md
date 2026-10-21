# py-serverless-kit documentation

Write a Python function once, describe its triggers in `pskit.yml`, and deploy it to any of 10 clouds or to an on-premises host with Terraform. Moving to another target means changing `provider.name`. The handler code stays the same.

| Page | What it covers |
|---|---|
| [Scenarios](scenarios.md) | Step-by-step guides by use case: HTTP API, queues, existing resources, Redis/MinIO on premises, databases, workflows, schedules, stages, logs, testing |
| [Getting started](getting-started.md) | Install, scaffold a project, run it locally, set up cloud credentials, deploy, inspect, remove |
| [Configuration reference](configuration.md) | Every `pskit.yml` key, `extends:`, `providers:`/`stages:` overrides, variables, escape hatches |
| [Events (triggers)](events.md) | `http`, `schedule`, `queue`, `storage`, `topic`, with shorthand forms and per-provider support |
| [Node.js and TypeScript](nodejs.md) | Handlers in JavaScript or TypeScript: runtimes per provider, `tsc`, npm dependencies, local runs |
| [Handler API](handlers.md) | `Event`, `HttpRequest`, `Record`, `Context`, `Response`, errors, `BatchResult`, `invoke()`, logging |
| [Workflows](workflows.md) | State machines: native AWS Step Functions / GCP Workflows, or the embedded engine anywhere |
| [Workflow executions](workflow-executions.md) | Execution ids, async start, status/history/cancel from the CLI, dashboard and HTTP |
| [Resources](resources.md) | `resources:`: managed or external storage, queues, topics, databases and other services; on-prem Redis and S3 drivers |
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
