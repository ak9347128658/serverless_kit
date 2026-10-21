# CLI reference

```
pskit [--version] [-h] <command> [options]
```

`py-serverless-kit` is an alias of `pskit`.

`pskit --help` lists every command and `workflow` subcommand, grouped (getting started, develop, deploy, operate,
reference) with a one-line example each. `pskit COMMAND --help` shows every option of a command plus examples.

| Group | Commands |
|---|---|
| Getting started | `init`, `studio`, `doctor` |
| Develop | `dev`, `invoke`, `validate` |
| Deploy | `package`, `plan`, `deploy`, `info`, `remove`, `tf` |
| Operate | `logs`, `dashboard`, `workflow run / show / start / executions / status / cancel` |
| Reference | `providers` |

## Common project options

Every command that loads a project accepts:

| Flag | Meaning |
|---|---|
| `-c, --config FILE` | config file (default: `pskit.yml`, `pskit.yaml`, `serverless_kit.yml`, `serverless_kit.yaml`) |
| `-p, --provider NAME` | override `provider.name` (`aws`, `azure`, `gcp`, `alibaba`, `oracle`, `ibm`, `digitalocean`, `tencent`, `huawei`, `cloudflare`, `onprem`) |
| `-s, --stage NAME` | stage, e.g. `dev`, `staging`, `prod` |
| `-r, --region NAME` | override `provider.region` |
| `-v, --verbose` | print tracebacks on errors |

Configuration, packaging, provider and Terraform errors print a single `Error: …` line and exit with code 1.

## `pskit init [DIRECTORY]`

Scaffold a project (default directory `.`).

| Flag | Default | |
|---|---|---|
| `--name` | directory name | service name |
| `-p, --provider` | `aws` | target cloud (fills in region and required options) |
| `-t, --template` | `http` | `http`, `events` (+ schedule/queue/storage), `workflow` (+ order workflow), `full` (everything) |
| `--force` | | overwrite existing files |

```bash
pskit init orders-api -p gcp -t full
```

## `pskit validate`

Validate the config for the selected provider: schema, unsupported triggers/options, runtime, timeouts, required options, and that handler modules exist. Prints errors and warnings, and exits 1 on errors.

```bash
pskit validate --provider azure --stage prod
```

## `pskit package`

Build `.pskit/<provider>-<stage>/artifact.zip` and `main.tf.json` without calling the cloud.

| Flag | |
|---|---|
| `--skip-deps` | don't pip-install `requirements.txt` into the bundle |

## `pskit plan`

Package, `terraform init`, then `terraform plan`.

| Flag | |
|---|---|
| `--skip-deps` | as above |
| `--destroy` | plan a destroy |

## `pskit deploy`

Package, then `terraform init` and `terraform apply`. Saves `deployment.json`, prints endpoints and ids, and regenerates the dashboard.

| Flag | |
|---|---|
| `--skip-deps` | don't vendor dependencies |
| `--confirm` | run `terraform plan -out tfplan`, then apply that plan instead of `-auto-approve` |
| `--upgrade` | `terraform init -upgrade` (update provider plugins) |
| `--no-dashboard` | don't regenerate `.pskit/dashboard/index.html` |

Hooks `before_package`, `after_package`, `before_deploy` and `after_deploy` run around these steps.

## `pskit remove`

`terraform destroy` for this service, stage and provider. Marks the deployment record as `removed`.

| Flag | |
|---|---|
| `-y, --yes` | don't ask for confirmation |
| `--keep-resources` | keep managed buckets, queues and topics (and their data); only functions, triggers and gateways are destroyed |

With `--keep-resources` the kept resources are taken out of the Terraform state first, so pskit no longer manages
them: delete them in the cloud console when you no longer need them, or import them into another stack. The
code-upload bucket is not kept. On Azure the storage account, Service Bus namespace and resource group that hold kept
queues, containers or topics are kept as well. Supported on AWS, GCP, Azure, Alibaba, Tencent, Huawei and Oracle; the
other providers create no buckets, queues or topics.

## `pskit info`

Show the deployed functions, triggers, URLs and resource ids.

| Flag | |
|---|---|
| `--json` | machine-readable record |
| `--refresh` | re-read `terraform output` instead of the saved record |

Before anything is deployed, `info` shows the configured functions with status `not deployed`.

## `pskit dashboard`

Generate the HTML dashboard from every `.pskit/*/deployment.json`. It doesn't take the common project options.

| Flag | Default | |
|---|---|---|
| `-d, --dir` | `.` | project root |
| `-o, --output FILE` | `.pskit/dashboard/index.html` | where to write the HTML |
| `--serve` | | serve the live dashboard (page + JSON API: host health, logs, workflow executions) |
| `--host` | `127.0.0.1` | bind address for `--serve`. A non-loopback address prints a warning |
| `--port` | `8765` | port for `--serve` |
| `--open` | | open in the browser |

See [dashboard.md](dashboard.md).

## `pskit studio`

Open the local web UI for editing the project in forms instead of YAML (functions, triggers, workflows, resources,
on-prem drivers, provider options, code) and running validate / deploy / invoke. It doesn't take the common project options.

| Flag | Default | |
|---|---|---|
| `-d, --dir` | `.` | project folder (an empty folder starts the new-project wizard) |
| `--host` | `127.0.0.1` | bind address. A non-loopback address prints a warning |
| `--port` | `8770` | port |
| `--open` | | open in the browser |
| `--vscode-image IMAGE` | `codercom/code-server:4.96.4` | image for Full VS Code (also `PSKIT_STUDIO_VSCODE_IMAGE`) |

It prints `http://127.0.0.1:8770/#token=...`. The token is required on every API call, so open that exact URL.
See [studio.md](studio.md).

## `pskit doctor`

Check that this machine can deploy to a provider, and print how to fix what's missing on the current OS: Terraform /
OpenTofu, Python, the cloud's CLI, credentials, required provider options and optional SDKs. For `onprem` it checks
what the deploy method needs: nothing for `local`; `ssh` / `scp`, the host and the private key for `ssh`; the Docker
CLI and a running daemon for `docker`. Nothing is sent anywhere and no command prompts; each tool probe has a short
timeout. It doesn't take the common project options.

| Flag | Default | |
|---|---|---|
| `-p, --provider NAME` | `provider.name` in pskit.yml | provider to check. Required without a pskit.yml |
| `-c, --config FILE` | `pskit.yml` | config to read provider options from |
| `--json` | | the full report (ids, statuses, links, steps for every OS) |

```bash
pskit doctor
pskit doctor -p azure
pskit doctor -p onprem --json
```

```text
pskit doctor: aws
 ✓ Python: Python 3.12.10
 ✓ Terraform (or OpenTofu): Terraform v1.9.8
 ! AWS CLI: aws not found on PATH; used to sign in and inspect resources
     Install AWS CLI, then open a new terminal.
       winget install Amazon.AWSCLI
       Install the AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
 ✗ AWS credentials: no AWS credentials in the environment or ~/.aws
     Add an access key profile here, run `aws configure`, or `aws sso login` for SSO profiles.
       $ aws configure
```

`✓` ok, `✗` missing (exit code 1), `!` recommended, `i` optional. A missing cloud CLI is only a warning: Terraform
needs credentials, not the CLI. The same checks appear in `pskit studio` (Setup), where you can also add an AWS
profile, run `az login` / `gcloud auth application-default login`, and test an SSH connection.

## `pskit dev`

Local HTTP server that routes requests to your handlers, with hot reload.

| Flag | Default |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` | `3000` |
| `--env-file` | `.env` |

See [local-development.md](local-development.md).

## `pskit invoke`

Invoke one function locally with a synthetic event.

| Flag | |
|---|---|
| `-f, --function` | **required**, function name |
| `-t, --type` | `http`, `schedule`, `queue`, `storage`, `topic`, `invoke`, `workflow` (default: the function's first event type, or `invoke`) |
| `-d, --data` | JSON payload, `@file.json`, or plain text |
| `--method` | HTTP method (default: the route's method or GET) |
| `--path` | HTTP path incl. query string (default: the route's path) |
| `-H, --header` | `"Name: value"`, repeatable |
| `--env-file` | default `.env` |

```bash
pskit invoke -f hello --path "/hello?name=Ada"
pskit invoke -f create-item -d '{"name": "lamp"}'                  # POST route, JSON body
pskit invoke -f worker -t queue -d '[{"id": 1}, {"id": 2}]'        # two records
pskit invoke -f on-upload -t storage -d '{"bucket": "b", "key": "in/a.csv"}'
pskit invoke -f nightly                                            # schedule: uses the configured input
```

Output is the JSON result. For HTTP it's `{"status", "headers", "body"}`; a `BatchResult` prints as `batchItemFailures`. If the handler raises, the traceback is printed and the exit code is 1.

## `pskit workflow`

```bash
pskit workflow run NAME [-i JSON|@file] [--sleep] [--history]
pskit workflow show NAME [--format auto|asl|gcp|json]
```

- `run` executes the workflow locally on the embedded engine. `wait` states are skipped unless `--sleep` is given. A failed run prints `FAILED <error>: <cause>` and exits with code 2.
- `show` prints the compiled definition. `auto` picks ASL for AWS, Cloud Workflows for GCP, and the portable JSON otherwise.

Deployed workflows (on-premises host, AWS Step Functions, Google Cloud Workflows; AWS/GCP need `pip install "py-serverless-kit[aws]"` / `[gcp]`). These read `.pskit/<provider>-<stage>/deployment.json`, so deploy first:

```bash
pskit workflow start NAME [-i JSON|@file] [--wait] [--json]
pskit workflow executions [NAME] [--status S] [-n LIMIT] [--offset N] [--token T] [--json]
pskit workflow status EXECUTION_ID [-w NAME] [--history] [--watch] [--json]
pskit workflow cancel EXECUTION_ID [-w NAME]
```

| Command | Flags |
|---|---|
| `start` | `-i, --input` JSON or `@file.json`. `--wait`: wait for the result (on-premises; native engines return at once; an embedded cloud orchestrator is always synchronous). Prints `execution_id`, status and a console link |
| `executions` | newest first. `--status` is `RUNNING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `CANCELLED` or `ABORTED`. `-n, --limit` (default 20). `--offset` pages on premises; `--token` (printed after a page) pages AWS/GCP |
| `status` | `-w, --workflow` helps AWS/GCP resolve the id. `--history` prints every state event. `--watch` polls every 2 s until the run finishes |
| `cancel` | stops the run before its next state (embedded) or calls `StopExecution` / `cancel` (AWS / GCP) |

See [workflow-executions.md](workflow-executions.md).

## `pskit logs`

Show, list or delete the logs of a deployment. On premises: the host's buffer (handler logging, `print` output, one entry per invocation, host notes). AWS: CloudWatch Logs. GCP: Cloud Logging (read only). Azure: Log Analytics when the deployment has a `log_analytics_workspace_id` output (read only). Elsewhere, use the console link from `pskit info`. Requirements per provider: [dashboard.md](dashboard.md#log-sources).

| Flag | Default | |
|---|---|---|
| `-f, --follow` | | keep printing new entries (polls every second) |
| `-n, --lines` | `100` | how many recent entries to show first |
| `--function NAME` | | only this function |
| `--level LEVEL` | | minimum level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--source` | | `function`, `host`, `stdout`, `stderr` or `invocation` |
| `-q, --grep TEXT` | | only entries containing this text (case-insensitive) |
| `--json` | | one JSON object per line |
| `--streams` | | list log streams (CloudWatch streams, or one per function on premises) with their ids |
| `--delete STREAM` | | delete a stream by id (repeatable). On premises the id is a function name, or `__host__` for host notes |
| `--delete-all` | | delete every stream, or only those of `--function` |
| `-y, --yes` | | don't ask before deleting |

```bash
pskit logs -f
pskit logs --function worker --level warning -n 500
pskit logs --stage prod --source invocation --json | jq .fields.duration_ms
pskit logs --provider aws --streams
pskit logs --provider aws --function worker --delete-all --yes
```

On AWS, `--level` and `--source` are applied to parsed lines and `-q` becomes a CloudWatch filter pattern. `-f` polls every 3 seconds on clouds.

The admin token comes from the generated `pskit_host.json`. See [providers.md](providers.md#logs-and-stats).

## `pskit providers`

Print the capability matrix of the installed providers (`--json` for details: regions, runtimes, max timeout, Terraform provider, notes).

## `pskit tf -- ARGS…`

Run any Terraform command in the generated workspace (after `package` or `deploy`):

```bash
pskit tf -- state list
pskit tf --stage prod -- output -json
pskit tf -- import aws_s3_bucket.b_uploads my-existing-bucket
```

## Environment variables

| Variable | Used by |
|---|---|
| `PSKIT_TERRAFORM_BIN` | path or name of `terraform` / `tofu` |
| `PSKIT_LOG_FORMAT`, `PSKIT_LOG_LEVEL`, `LOG_LEVEL` | runtime logging (CLI defaults to `text`) |
| `PSKIT_DEBUG` | include exception details in local/remote 500s |
