# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

> The function runtime is bundled into every deployment. After upgrading `py-serverless-kit`, run `pskit deploy` again to pick up runtime fixes.

## [Unreleased]

## [1.0.0] - 2026-10-21

### Added
- **Node.js and TypeScript functions** (`provider.runtime: nodejs18 | nodejs20 | nodejs22 | nodejs24`) on every provider. A service is either Python or Node.js. Handlers use the same `module.function` form, and the `pskit` package (vendored as `pskit_runtime/`) has the same Event / Context / Response / BatchResult API in camelCase, plus `invoke()`, resources and the embedded workflow engine. TypeScript is compiled with `tsc` while packaging (`package.node`). The on-prem host and `pskit dev` / `pskit invoke` run Node.js handlers through a JSON-lines worker. See `docs/nodejs.md` and `deploy_docs/NODE_RUNTIME.md`.
- `pskit remove --keep-resources`: destroy functions and triggers but keep managed buckets, queues and topics with their data (they leave the Terraform state). Studio's Deploy page has the matching switch.
- Studio:
  - **Project environment** creates `.venv` with a chosen interpreter and installs `requirements.txt`, or runs `npm install` for Node.js services.
  - Full VS Code: pick Python and Node.js versions; studio builds a code-server image with them once.
  - Triggers: **Create & select** adds a new queue, bucket or topic to Resources and selects it.
  - JS/TS editing: highlighting, handler stubs in TypeScript or ESM/CommonJS JavaScript, and Node.js runtimes in the runtime pickers.
  - On-prem deploy fields for Node.js (`node`, `node_url`, `node_sha256`, `host_python`).

## [0.9.0] - 2026-10-21

### Added
- `enabled: false` on functions and workflows: kept in `pskit.yml`, skipped on deploy. Combined with `providers.<cloud>` overrides it switches items (or single triggers) off on one cloud only.
- **Azure native workflows** on Azure Logic Apps (Consumption) with `engine: native` (`auto` stays embedded). The pskit output includes the trigger callback URL, and `pskit workflow start` uses it. Azure also outputs `log_analytics_workspace_id` when Application Insights is on.
- **Cloud logs** (`core/cloud_logs.py`): `pskit logs` and the dashboard read logs from AWS CloudWatch, GCP Cloud Logging and Azure Log Analytics, as well as on-prem. Log streams can be listed and deleted (AWS, on-prem) with `pskit logs --streams / --delete / --delete-all` and in the dashboard. On-prem hosts gain `GET /__pskit/logs/streams` and `DELETE /__pskit/logs`.
- **`pskit doctor`** (`core/setup_check.py`): checks Terraform, the cloud CLI, credentials, required provider options and on-prem SSH/Docker, with download links and per-OS install steps.
- `pskit --help` lists every command and workflow subcommand with examples; help is no longer truncated.
- Studio:
  - **Setup & credentials** page: the checks above, an `aws configure`-style profile form, provider environment variables for the session, browser logins (`az login`, `gcloud auth ...`) and an SSH connection test.
  - **Logs** page and per-function live logs: tail, list streams, delete selected or all.
  - File picker for the on-prem SSH private key.
  - Dropdowns (with a custom value) for region, stage, memory, timeout, log retention, plan SKUs, SSH user, timezone and more.
  - Schedules **at a specific time**: daily, weekdays, chosen days, monthly or yearly at HH:MM in a timezone.
  - Trigger options each provider rejects or ignores are derived from its validation and shown in the form. Unsupported auth modes are disabled, and only supported trigger types are offered.
  - The queue, bucket and topic source picker offers a new resource, a project resource, a pick from the cloud account (optionally added as an external resource), or a typed name.
  - Enable or disable a function or workflow everywhere or only on the current provider. Before deploy, and when switching provider, unsupported items are listed with a one-click "disable on this provider".
  - Clear the recent jobs list (all finished jobs, or one).

## [0.8.0] - 2026-10-21

### Added
- **`pskit studio`**: a local web UI to build a project without writing YAML.
  - New-project wizard; provider cards for all 11 targets with their capabilities; provider, region, runtime and provider-option forms; on-prem deploy method, services and tuning.
  - AWS-console-style **Create function** wizard. Trigger types follow what the selected provider supports, with HTTP path-parameter preview and CORS/auth, a cron/rate builder, and a queue/storage/topic source picker (new, from Resources, **Browse account** or typed).
  - Visual workflow builder: a diagram plus an inspector for every state type, choice rule groups, retry/catch, and nested parallel/map.
  - Resources with an on-prem driver picker for every built-in driver and custom classes; secret fields read from env vars.
  - Code page with a CodeMirror editor, or **Full VS Code**: code-server pulled into Docker on demand, run on the project and embedded.
  - Deploy page (validate/package/plan/deploy/info/remove with live logs, invoke, local workflow runs); raw-YAML mode with a diff against disk.
  - Edits stay in a draft that is validated live and saved with backups and conflict detection. `${...}` values are kept as written.
  - Session token, loopback bind, Host/Origin checks. See `docs/studio.md`.
- Studio account discovery: lists existing SQS queues, S3 buckets, SNS topics and state machines (boto3); Pub/Sub topics and subscriptions and GCS buckets (google-cloud-*); Azure queues and containers (connection string); and on-prem directories.
- `providers/onprem_catalog.py`: a data catalog of on-prem driver fields, kept in sync with validation by a test.

### Fixed
- The dashboard server reads a POST body before rejecting the request (403/415), so Windows no longer resets the connection before the client reads the error.

## [0.7.0] - 2026-10-21

### Added
- On-premises drivers that watch queues and buckets in other providers: `sqs` (AWS, JSON protocol + SigV4), `azure_queue` and `azure_blob` (Shared Key, SAS or connection string; Azurite), `pubsub` (pull subscription) and `gcs` (access token, `token_command` or the metadata server; emulators), `mns` (Alibaba Cloud) and `cloudflare_queue` (HTTP pull consumer). Leases use each service's own visibility timeout, so delivery stays at-least-once. See the provider matrix in `docs/resources.md`.
- S3 storage presets `aws`, `oss`, `cos`, `obs`, `oci`, `ibm_cos`, `spaces`, `r2` and `minio` (or `driver: s3` + `provider:`). `s3` without `endpoint` now targets AWS (virtual-host style), and credentials fall back to the `AWS_*` environment variables.
- `custom` queue and storage drivers: `class: package.module:ClassName` from your app, constructed with the remaining options. Import errors, constructor errors and missing methods are reported with the class name. `docs/resources.md` has a RabbitMQ example (AMQP with manual acks).
- Any on-prem driver parameter can be written as `<name>_env: VARIABLE`, read from the host's environment at start, so secrets stay out of `pskit_host.json` and Terraform state.
- `examples/19-custom-drivers`: runnable `custom` drivers (`SqliteQueue` with visibility timeout, receive counts and a dead-letter table; `FolderStorage` for local/NFS/SMB folders) plus RabbitMQ (pika) and Kafka (kafka-python) templates, all at-least-once.
- `examples/20-onprem-external-clouds`: one on-prem host consuming SQS, Azure Storage Queue, Pub/Sub, MNS and Cloudflare Queues and watching S3, Azure Blob, GCS, R2 and MinIO, with every secret read through `*_env`.

### Fixed
- The on-premises host makes the app importable before building its queue/storage drivers, so a `custom` driver class from the app loads at start.
- The on-premises host sets `PSKIT_ONPREM_ROOT` before building its drivers, so relative paths in driver options resolve against the host directory.

## [0.6.0] - 2026-10-21

### Added
- **`resources:`** for storage, queues, topics, databases, caches and custom services. A resource is managed (pskit creates it) or `external: true` (pskit only attaches it). Bound functions get `PSKIT_RESOURCES` and `PSKIT_RESOURCE_<KEY>_TYPE/NAME/EXTERNAL/<connection key>` variables, plus `ARN`/`URL` on AWS and `ID` for Pub/Sub on GCP. `functions:` limits which functions get a binding. `queue`, `storage` and `topic` events accept `resource: <key>`. Standalone managed resources are created on AWS, GCP, Azure and on premises; other providers report a validation error. AWS roles get a `pskit-resources` policy for the bound resources. See `docs/resources.md`.
- On-premises `provider.options` `execution_retention`, `workflow_workers`, `log_buffer`, `log_level` and `capture_output` to tune the host's execution store, workflow concurrency and log capture.
- `resource()`, `resources()` and `Resource` in `py_serverless_kit`, to read bindings the same way on every cloud.
- On-premises drivers for resources: `redis` queues (stdlib RESP client; `<key>`, `<key>:processing` and `<key>:claims`, with crash recovery) and `s3` storage (SigV4, MinIO and any S3-compatible store, `prefix`, `path_style`, `session_token`), plus a per-resource `directory`. Unreachable backends become host notes and the host keeps running.
- Workflow execution ids everywhere. The on-premises host tracks runs in `<data_dir>/executions/`: status, current state, history (`enter`/`exit`/`caught`/`retry`/`fail`), input, output and error. `GET /__pskit/executions[/<id>]` and `POST /__pskit/executions/<id>/cancel` inspect and control runs. Cancel stops before the next state and wakes `wait` states. `workflows.<name>.timeout` is enforced by the embedded engine (`TIMED_OUT`), and runs interrupted by a restart become `ABORTED`. Cloud orchestrators return `execution_id` with their synchronous result. See `docs/workflow-executions.md`.
- `pskit workflow start | executions | status | cancel` for deployed workflows on the on-premises host, AWS Step Functions (`pip install "py-serverless-kit[aws]"`) and Google Cloud Workflows (new `[gcp]` extra: `google-cloud-workflows`).
- On-premises logs: the host captures logging, `print`/stdout/stderr, one entry per invocation and host notes, tagged with function and request id, in memory and in `<data_dir>/logs/host.jsonl` (rotated). `GET /__pskit/logs` (`since`, `before`, `limit`, `level`, `function`, `source`, `q`), `GET /__pskit/stats` (invocations, errors and durations per function, queue depths), and `pskit logs [-f]`.
- A new React + Vite dashboard (single self-contained page) with sortable, paginated tables, deployment tabs (Functions, Workflows, Executions, Logs, Resources, Outputs) and light/dark themes. `pskit dashboard --serve` (new `--host`) adds a JSON API for live host health and stats, a log tail, and workflow executions (list, start, follow, cancel). The API checks the Host header and same-origin POSTs and keeps admin tokens server-side.
- `docs/scenarios.md`: step-by-step guides by use case.

### Changed
- **Breaking (on premises):** `POST /workflows/<name>` and `POST /__pskit/workflows/<name>` start the workflow in the background and answer `202 {"execution_id", "status": "RUNNING", "status_url", ...}`. Add `?wait=true` (or `Prefer: wait`, or `x-pskit-wait: true`) to get the old synchronous result. That result now also includes `execution_id`, `started_at` and `stopped_at`.
- `deployment.json` (and `pskit info --json`) include the deployment `key` (`<provider>-<stage>`) and `resources` (connection key names only).

### Fixed
- The on-premises host drains the request body before answering `401` for a missing admin token. On Windows the client previously saw a connection reset instead of the 401.
- Re-validating a dumped config no longer leaves each event's config as a plain dict.

Redeploy to pick up the new runtime. Handler code is unchanged.

## [0.5.2] - 2026-10-19

### Fixed
- On-premises SSH deploy as a normal user such as `azureuser`. With `deploy.sudo: true`, apply creates `deploy.path` with `sudo -n mkdir` and `chown`s it to the SSH user before `scp`. A user who cannot write `/opt` no longer fails with `Permission denied`.

Redeploy the on-premises host after upgrading. Handler code is unchanged.

## [0.5.1] - 2026-10-19

### Fixed
- On-premises `python: bundle` on Windows. Unpacking a Linux CPython into the project raised `WinError 206` (`The filename or extension is too long`) on setuptools test files under `pkg_resources/tests`. A remote deploy now copies that archive straight into `release.tar.gz` and leaves those test files out. A partial `onprem_app/python` tree from the failed unpack is removed on the next deploy.

Redeploy the on-premises host after upgrading. Handler code is unchanged.

## [0.5.0] - 2026-10-19

### Changed
- On-premises remote deploy copies one `release.tar.gz` with `scp` and starts it from a script on the server. `options.deploy.method: scp` is the same path as `ssh`. A `.pem` (`pem` or `identity_file`) is passed with `IdentitiesOnly`. `pskit deploy` asks for that path on a terminal for SSH as well as Docker; press Enter on the SSH prompt to keep the SSH agent or the default key. Docker on a server still requires a key.
- The server script restarts a systemd unit (`systemctl restart`, so a redeploy replaces the running process), installs and starts Docker when `method: docker` and `docker` is missing, and uses `sudo -n` so a missing sudoers rule fails immediately. It exports a normal `PATH` (non-login SSH often cannot see `systemctl` or `python3`). `pskit-run.sh` starts a bundled CPython when one was copied, then `python3.x`, then `python3`.
- Apply waits until `http://127.0.0.1:<port>/__pskit/health` answers. A dead process or container fails the deploy and prints `journalctl` or `docker logs`. A process that binds `0.0.0.0` is checked on `127.0.0.1`, including when `advertise` is a public URL.

Redeploy on-premises hosts to pick up the new provisioner. Handler code is unchanged.

## [0.4.0] - 2026-10-19

### Added
- On-premises Docker deploy to a server. Set `options.deploy.method: docker` and `options.deploy.host`. `pskit deploy` asks for a `.pem` private key when `options.deploy.pem` is unset (`identity_file` is the same field). The image is built on the machine that runs `pskit`, copied over SSH, and started with `docker run`. If `docker` is missing on the server, apply installs it (`docker.io` via apt, `docker` via dnf or yum, or `docker` via apk) and then loads the image. `install_docker: false` skips the install and fails if Docker is absent. `sudo` defaults to true for this path so the SSH user can install and start the daemon.

Redeploy on-premises Docker hosts to pick up the new provisioner. Handler code is unchanged.

## [0.3.0] - 2026-10-17

### Added
- On-premises deploy targets. `provider.options.deploy.method` is `local` (default, previous behaviour), `ssh` (copy the host over OpenSSH and start a process or systemd unit), or `docker` (build an image that already contains Python). When the server has no Python, set `python: bundle` to ship a portable CPython, or use `method: docker`. `python_home` / `python_url` cover air-gapped mirrors. Wheels are vendored for the target OS/arch (manylinux when the server is Linux).
- Example [17-on-premises-remote](examples/17-on-premises-remote): SSH + bundled interpreter + systemd.

### Fixed
- On-prem host: Windows process helpers (`ctypes.windll`, `subprocess.DETACHED_PROCESS`) are gated on `sys.platform == "win32"` so mypy on Linux/macOS CI no longer reports missing attributes.

Redeploy on-premises hosts to pick up the new `onprem_deploy.py` provisioner. Handler code is unchanged.

## [0.2.0] - 2026-10-17

### Added
- On-premises provider (`provider.name: onprem`). `pskit deploy` starts one long-lived Python process on the machine that runs the command. HTTP is served directly. Schedules, file queues (with dead-letter and partial batch failure), directory storage and file topics are created at start when they do not already exist. Point `provider.options.services.<kind>` at `{use: directory, path: ...}` to use a directory you already have. `existing: true` waits for that directory instead of creating it. Workflows run on the embedded engine inside the host, and `invoke()` stays in-process. `pskit remove` stops the process and leaves the data directory in place.
- Example [16-on-premises](examples/16-on-premises). Every other example has an `providers.onprem` block (its own port, public auth where a cloud authorizer does not apply, local state where the file used a cloud backend).

No change to handlers that already run on a cloud. Redeploy to pick up the new runtime if you use `BatchResult` or `invoke()` on premises.

## [0.1.3] - 2026-10-15

### Fixed
- AWS: `pskit deploy` failed with `creating Lambda Function ...: decomposing request: bufio.Scanner: token too long` once the bundle (e.g. with vendored dependencies) got large, because the zip was sent inline in every `CreateFunction` request. The artifact is now uploaded once to S3 (`aws_s3_object`) and each Lambda function references it via `s3_bucket`/`s3_key`. This also lifts the 50 MB inline upload limit.

### Added
- AWS options `artifact_bucket` (use an existing bucket for code uploads instead of the private `<service>-<stage>-code-*` bucket pskit creates) and `artifact_upload: s3 | direct` (`direct` restores the old inline upload).

No runtime changes. Existing AWS deployments get a new code bucket on the next `pskit deploy`; Lambda functions are updated in place.

## [0.1.2] - 2026-10-15

### Fixed
- Python 3.9 support: `pskit package`/`deploy` (and the Cloudflare provider) crashed with `TypeError: write_text() got an unexpected keyword argument 'newline'`, because `Path.write_text(newline=...)` only exists on Python 3.10+. Generated files are now written as UTF-8 bytes with `\n` line endings on every Python version.
- GCP adapter: the storage object `size` check now narrows `None` explicitly, so mypy passes on Python 3.9 CI. Runtime behavior is unchanged.

No runtime behavior changes: functions deployed with 0.1.1 don't need to be redeployed.

## [0.1.1] - 2026-10-15

### Changed
- Author and maintainer set to Asif Khan @ Kavion.ai (`py_serverless_kit.__author__`, package metadata, LICENSE).
- Project URLs (homepage, docs, issues, changelog) now point to https://github.com/ak9347128658/serverless_kit.
- README links are absolute so they work on the PyPI project page; added CI/PyPI/license badges.
- `scripts/validate_all_providers.py` explains that Terraform is optional instead of printing a traceback when it isn't installed.

### Added
- `deploy_docs/How_to_deploy.md`: short PyPI release guide with PowerShell commands and troubleshooting (e.g. TestPyPI `403`).
- `deploy_docs/HOW_TO_INSTALL_TERRAFORM.md`: installing Terraform on Windows.

No runtime changes: functions deployed with 0.1.0 don't need to be redeployed.

## [0.1.0] - 2026-10-15

### Added
- Portable handler API: `Event`, `HttpRequest`, `Record`, `Headers`, `Context`, `Response`, HTTP error classes, `BatchResult`, `invoke()`.
- `pskit.yml` configuration with `extends:`, `providers:` and `stages:` overrides, and `${env:}`, `${opt:}`, `${self:}`, `${file()}` and `${tf:}` variables.
- Providers: AWS (stable), Google Cloud (stable), Azure (stable), Alibaba Cloud (beta), Huawei Cloud (beta), Tencent Cloud (beta), DigitalOcean (beta), IBM Cloud (beta), Oracle Cloud (experimental), Cloudflare Workers (experimental).
- Triggers: HTTP (path params, CORS, auth), schedule (rate/cron), queue, storage, topic.
- Workflows: native AWS Step Functions and Google Cloud Workflows compilers, plus an embedded engine for the other providers and local runs.
- CLI: `init`, `validate`, `package`, `plan`, `deploy`, `remove`, `info`, `dashboard`, `dev`, `invoke`, `workflow run|show`, `providers`, `tf`.
- Deployment dashboard (self-contained HTML).
- Local dev server with hot reload, and `LocalRunner` for tests.
- `scripts/validate_all_providers.py`, which runs `terraform validate` for every provider.
