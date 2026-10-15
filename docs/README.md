# py-serverless-kit documentation

Write a Python function once, describe its triggers in `pskit.yml`, and deploy it to any of 10 clouds with Terraform. Moving to another cloud means changing `provider.name`. The handler code stays the same.

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, scaffold a project, run it locally, set up cloud credentials, deploy, inspect, remove |
| [Configuration reference](configuration.md) | Every `pskit.yml` key, `extends:`, `providers:`/`stages:` overrides, variables, escape hatches |
| [Events (triggers)](events.md) | `http`, `schedule`, `queue`, `storage`, `topic`, with shorthand forms and per-provider support |
| [Handler API](handlers.md) | `Event`, `HttpRequest`, `Record`, `Context`, `Response`, errors, `BatchResult`, `invoke()`, logging |
| [Workflows](workflows.md) | State machines: native AWS Step Functions / GCP Workflows, or the embedded engine anywhere |
| [Providers](providers.md) | Capability matrix plus one section per cloud: options, credentials, resources, limits |
| [CLI reference](cli.md) | Every `pskit` command and flag |
| [Local development](local-development.md) | `pskit dev`, `pskit invoke`, `.env`, testing with `LocalRunner` |
| [Dashboard](dashboard.md) | The generated HTML index of everything you deployed |
| [Architecture](architecture.md) | How config becomes Terraform and how the runtime dispatches events; third-party providers |
| [Migrating from AWS](migrating-from-aws.md) | Moving existing Lambda functions and Step Functions to pskit, then to other clouds |
| [Troubleshooting & FAQ](troubleshooting.md) | Common errors, provider gotchas, security notes |

Runnable examples for every scenario live in [`../examples`](../examples/README.md).

Maintainers: see [`../deploy_docs/PUBLISHING_TO_PYPI.md`](../deploy_docs/PUBLISHING_TO_PYPI.md) and [`../deploy_docs/CONTRIBUTING.md`](../deploy_docs/CONTRIBUTING.md).
