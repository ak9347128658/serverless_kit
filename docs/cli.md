# CLI reference

```
pskit [--version] [-h] <command> [options]
```

`py-serverless-kit` is an alias of `pskit`.

## Common project options

Every command that loads a project accepts:

| Flag | Meaning |
|---|---|
| `-c, --config FILE` | config file (default: `pskit.yml`, `pskit.yaml`, `serverless_kit.yml`, `serverless_kit.yaml`) |
| `-p, --provider NAME` | override `provider.name` (`aws`, `azure`, `gcp`, `alibaba`, `oracle`, `ibm`, `digitalocean`, `tencent`, `huawei`, `cloudflare`) |
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
| `--serve` | | serve it over HTTP on 127.0.0.1 |
| `--port` | `8765` | port for `--serve` |
| `--open` | | open in the browser |

See [dashboard.md](dashboard.md).

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
