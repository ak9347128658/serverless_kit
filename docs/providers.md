# Providers

Run `pskit providers` (or `pskit providers --json`) to print this matrix from the installed version.

| Provider | Tier | http | schedule | queue | storage | topic | workflows | Runtimes | Max timeout |
|---|---|---|---|---|---|---|---|---|---|
| [`aws`](#aws-aws-lambda) | stable | ✓ | ✓ | ✓ | ✓ | ✓ | native | 3.9–3.13 | 900 s |
| [`gcp`](#gcp-google-cloud-functions-2nd-gen) | stable | ✓ | ✓ | ✓ | ✓ | ✓ | native | 3.9–3.13 | 3600 s (540 s event) |
| [`azure`](#azure-azure-functions) | stable | ✓ | ✓ | ✓ | ✓ | ✓ | embedded | 3.9–3.12 | 600 s |
| [`alibaba`](#alibaba-function-compute-30) | beta | ✓ | ✓ | – | ✓ | ✓ | embedded | 3.9, 3.10, 3.12 | 3600 s |
| [`tencent`](#tencent-serverless-cloud-function-scf) | beta | ✓ | ✓ | – | ✓ | – | embedded | 3.9, 3.10 | 900 s |
| [`huawei`](#huawei-functiongraph) | beta | ✓ | ✓ | – | ✓ | ✓ | embedded | 3.9, 3.10, 3.12 | 900 s |
| [`ibm`](#ibm-code-engine-functions) | beta | ✓ | – | – | – | – | embedded | 3.11 | 120 s |
| [`digitalocean`](#digitalocean-functions) | beta | ✓ | ✓ | – | – | – | embedded | 3.9, 3.11 | 900 s |
| [`oracle`](#oracle-oci-functions) | experimental | ✓ | – | – | ✓ | – | embedded | 3.9, 3.11 | 300 s |
| [`cloudflare`](#cloudflare-python-workers) | experimental | ✓ | ✓ | – | – | – | embedded | 3.11–3.13 | 900 s |
| [`onprem`](#onprem-on-premises) | experimental | ✓ | ✓ | ✓ | ✓ | ✓ | embedded | 3.9–3.14 | 3600 s |

**Tiers**

- **stable:** the generated Terraform is validated against the real provider plugin, and the full trigger set is supported.
- **beta:** validated with `terraform validate`; event payload formats follow vendor documentation.
- **experimental:** works with notable constraints (container images, Pyodide, or a process on your own machine), so read the section below first.

**Common to all providers**

- Every provider gets `provider.region` (or its default region), `provider.options` (below), and `functions.<fn>.options`, which is merged into that provider's function resource as an escape hatch.
- Resources are tagged or labelled with `pskit-service`, `pskit-stage` and `managed-by: py-serverless-kit` where the cloud supports tags.
- With no pinned `runtime`, pskit picks python3.12 or the newest version below it that the cloud supports (see [configuration.md](configuration.md#default-runtime)).

Every generated configuration (all 11 providers) passes `terraform validate`. The repository's `scripts/validate_all_providers.py` re-checks this in CI. On-premises uses Terraform's built-in provider, so that check does not download a plugin for it.

---

## aws (AWS Lambda)

- **Terraform:** `hashicorp/aws >= 5.40`
- **Credentials:** standard AWS chain (`aws configure`, SSO, `AWS_PROFILE`, env keys, OIDC in CI)
- **Dependencies:** pip-installed into the zip as manylinux wheels (`x86_64` or `arm64`)

| Trigger | Implementation |
|---|---|
| http | API Gateway **HTTP API** (one per service, `$default` stage), Lambda proxy v2; `auth: iam` → `AWS_IAM`, `auth: jwt` → JWT authorizer; CORS on the API |
| schedule | EventBridge Scheduler (supports `timezone`) |
| queue | SQS (+ optional DLQ / FIFO), event source mapping with `ReportBatchItemFailures` |
| storage | S3 bucket (public access blocked) + bucket notification |
| topic | SNS topic + subscription (filter policy) |
| workflows | Step Functions state machine (`options.type: STANDARD \| EXPRESS` per workflow) |

**Options**

```yaml
provider:
  name: aws
  region: eu-west-1
  options:
    profile: my-profile            # AWS CLI profile
    assume_role_arn: arn:aws:iam::123456789012:role/deployer
    allowed_account_ids: ["123456789012"]
    artifact_bucket: my-deploy-bucket   # optional: upload code here instead of a pskit-created bucket
    artifact_upload: s3            # s3 (default) | direct (inline zip; only for small bundles)
  iam:
    statements:
      - {actions: ["dynamodb:GetItem"], resources: ["arn:aws:dynamodb:*:*:table/orders"]}
functions:
  api:
    handler: app.api.handler
    options:                       # merged into aws_lambda_function
      layers: ["arn:aws:lambda:eu-west-1:123456789012:layer:deps:3"]
      vpc_config: {subnet_ids: [subnet-1], security_group_ids: [sg-1]}
```

**Resources:** `aws_lambda_function`, `aws_iam_role` (+ policies), `aws_cloudwatch_log_group`, `aws_apigatewayv2_*`, `aws_lambda_permission`, `aws_scheduler_schedule`, `aws_sqs_queue`, `aws_lambda_event_source_mapping`, `aws_s3_bucket*`, `aws_sns_topic*`, `aws_sfn_state_machine`, `aws_s3_object` (the code artifact).

**Code upload:** the artifact is uploaded once to S3 (a private `<service>-<stage>-code-*` bucket created by pskit, or `options.artifact_bucket`) and every function reads it from there. This avoids the 50 MB inline limit and the AWS provider error `decomposing request: bufio.Scanner: token too long` that inline uploads hit with larger bundles. The deploying identity needs `s3:CreateBucket`/`PutObject` (or just `PutObject` on `artifact_bucket`).

**Limits:** `auth: key` isn't available on HTTP APIs. Integration timeout is capped at 30 s by API Gateway.

---

## gcp (Google Cloud Functions, 2nd gen)

- **Terraform:** `hashicorp/google >= 5.20`
- **Credentials:** `gcloud auth application-default login` or `GOOGLE_APPLICATION_CREDENTIALS`
- **Dependencies:** built remotely from `requirements.txt` (`functions-framework` is added automatically)

```yaml
provider:
  name: gcp
  region: europe-west1
  options:
    project: my-gcp-project               # required
    service_account: runtime@my-gcp-project.iam.gserviceaccount.com   # optional runtime identity
```

| Trigger | Implementation |
|---|---|
| http | function URL; `auth: none` adds an `allUsers` invoker binding, `auth: iam` doesn't |
| schedule | Cloud Scheduler (OIDC POST to the function, or Pub/Sub publish for event-only functions); `timezone` supported |
| queue / topic | Pub/Sub topic + Eventarc trigger (a queue is a topic on GCP) |
| storage | Cloud Storage bucket + Eventarc `finalized` / `deleted`; prefix/suffix applied by the runtime |
| workflows | Cloud Workflows, tasks call function URLs with OIDC |

Functions with several triggers are **split into multiple Cloud Functions** automatically (see [events.md](events.md#gcp-multi-trigger-functions-are-split-automatically)). A dedicated invoker service account is created for Scheduler, Eventarc and Workflows.

**Resources:** `google_storage_bucket` (+ object) for sources, `google_cloudfunctions2_function`, `google_cloud_run_service_iam_member`, `google_service_account`, `google_project_iam_member`, `google_cloud_scheduler_job`, `google_pubsub_topic`, `google_workflows_workflow`.

**Limits:** `auth: jwt`/`key` aren't supported. Event-triggered functions are limited to 540 s. `queue.dead_letter`/`fifo`/`batch_size` and `topic.filter` are not applied (warnings). `iam.statements` are ignored: grant roles to `options.service_account`. The bundle's root `main.py` is generated, so don't put your own `main.py` at the source root.

---

## azure (Azure Functions)

- **Terraform:** `hashicorp/azurerm >= 4.9` (needs a subscription id)
- **Credentials:** `az login` + `ARM_SUBSCRIPTION_ID`, or service principal env vars
- **Dependencies:** remote Oryx build from `requirements.txt` (`azure-functions` is added automatically)

```yaml
provider:
  name: azure
  region: westeurope
  options:
    subscription_id: ${env:ARM_SUBSCRIPTION_ID}   # or the env var itself
    resource_group: existing-rg                   # optional: use an existing resource group
    plan_sku: Y1                                  # default Y1 (Consumption)
    app_insights: true                            # default true
    storage_connection: OTHER_STORAGE             # app setting name, for `existing` queues/buckets in another account
    servicebus_namespace_id: /subscriptions/.../namespaces/my-sb   # for `existing` topics
    servicebus_connection: SB_CONNECTION                          # app setting with its connection string
```

One **Linux Consumption Function App** hosts every function of a service, using the Python v2 programming model: pskit generates `function_app.py` and `host.json`. `routePrefix` is empty, so your paths are served exactly as configured (no `/api`). `{path+}` becomes Azure's `{*path}`.

| Trigger | Implementation |
|---|---|
| http | `@app.route`; `auth: key` → function key |
| schedule | timer trigger (NCRONTAB, UTC) |
| queue | Storage Queue in the app's storage account (poison queue `<name>-poison`) |
| storage | Blob container + blob trigger (created/updated only) |
| topic | Service Bus namespace/topic + one subscription per function |

**Resources:** `azurerm_resource_group`, `azurerm_storage_account`, `azurerm_service_plan`, `azurerm_linux_function_app`, `azurerm_application_insights` + `azurerm_log_analytics_workspace`, `azurerm_storage_queue`, `azurerm_storage_container`, `azurerm_servicebus_*`.

**Limits:** `auth: iam`/`jwt` aren't supported. `environment` is app-wide (warning on conflicting values). Timers ignore `timezone`. Blob `deleted` events aren't available. `fifo`, `dead_letter` and `topic.filter` aren't applied. Invoke-only functions get `POST /_pskit/invoke/<name>` (function key).

---

## alibaba (Function Compute 3.0)

- **Terraform:** `aliyun/alicloud >= 1.230.0`
- **Credentials:** `ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY`, or `options.profile`
- **Default region:** `cn-hangzhou`
- **Dependencies:** vendored

```yaml
provider:
  name: alibaba
  region: cn-shanghai
  options:
    profile: default        # optional aliyun CLI profile
  iam:
    role: acs:ram::1234567890:role/fc-runtime   # use instead of iam.statements
```

| Trigger | Implementation |
|---|---|
| http | one anonymous HTTP trigger per function (`fcapp.run` URL) |
| schedule | timer trigger (`@every` or cron; `CRON_TZ=` when `timezone` is set) |
| storage | OSS bucket + OSS trigger (prefix/suffix) via a RAM trigger role |
| topic | MNS topic + trigger (`filter.tag`) |

**Resources:** `alicloud_fcv3_function`, `alicloud_fcv3_trigger`, `alicloud_oss_bucket`, `alicloud_message_service_topic`, `alicloud_ram_role` (+ policy attachment).

**Limits:** no queue events. HTTP `auth` must be `none`. `iam.statements` aren't translated to RAM (warning): set `iam.role`.

---

## tencent (Serverless Cloud Function, SCF)

- **Terraform:** `tencentcloudstack/tencentcloud >= 1.81.0`
- **Credentials:** `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY`, or `options.profile`
- **Default region:** `ap-guangzhou`
- **Dependencies:** vendored

```yaml
provider:
  name: tencent
  options:
    profile: default
    namespace: default      # SCF namespace shown in console links
functions:
  ingest:
    handler: app.ingest.handler
    events:
      - storage: {bucket: uploads-1250000000}   # COS buckets need the -<APPID> suffix
```

| Trigger | Implementation |
|---|---|
| http | API Gateway service, one API per route, released to `release` |
| schedule | timer trigger (7-field cron; input / enabled via trigger config) |
| storage | COS bucket + COS trigger (prefix/suffix) |

**Resources:** `tencentcloud_scf_function`, `tencentcloud_scf_trigger_config`, `tencentcloud_api_gateway_service` / `_api` / `_service_release`, `tencentcloud_cos_bucket`.

**Limits:** no queue or topic events. HTTP `auth` must be `none`. No `PATCH` (use `ANY`). `{path+}` becomes a prefix route. Memory must be 64, 128 or a multiple of 128 MB. `iam.statements` aren't translated (set `iam.role`). Each new artifact is copied to `artifact-<hash>.zip` so SCF re-uploads it.

---

## huawei (FunctionGraph)

- **Terraform:** `huaweicloud/huaweicloud >= 1.60.0`
- **Credentials:** `HW_ACCESS_KEY` / `HW_SECRET_KEY` (+ `HW_REGION_NAME`), or `options.profile`
- **Default region:** `cn-north-4`
- **Dependencies:** vendored

```yaml
provider:
  name: huawei
  options:
    apig_instance_id: 0123456789abcdef0123456789abcdef   # required for http events (dedicated APIG)
    app: default                                         # FunctionGraph app, default "default"
    enterprise_project_id: "0"                           # optional
  iam:
    role: my-functiongraph-agency                        # IAM agency name
```

| Trigger | Implementation |
|---|---|
| http | dedicated API Gateway group/APIs/environment; `auth: none \| iam \| key` (→ `NONE`/`IAM`/`APP`) |
| schedule | timer trigger (`Rate` or cron) |
| storage | OBS bucket + trigger (prefix/suffix) |
| topic | SMN topic + trigger |

**Resources:** `huaweicloud_fgs_function`, `huaweicloud_fgs_trigger`, `huaweicloud_apig_*`, `huaweicloud_obs_bucket`, `huaweicloud_smn_topic`.

**Limits:** no queue events. `{path+}` becomes a prefix route. `iam.statements` are ignored (use an agency in `iam.role`). Environment variables are passed as `user_data`.

---

## ibm (Code Engine functions)

- **Terraform:** `IBM-Cloud/ibm >= 1.70`
- **Credentials:** `IC_API_KEY`
- **Default region:** `us-south`
- **Dependencies:** vendored

```yaml
provider:
  name: ibm
  options:
    resource_group: default          # provider-level resource group (optional)
    project_id: 1234abcd-...         # use an existing Code Engine project (else one is created)
    resource_group_id: abcd...       # for the created project
    visibility: local_public         # managed_domain_mappings; also settable per function
    code_reference: icr.io/ns/bundle:tag   # optional: code bundle image instead of an inline upload
```

Every function gets a public HTTPS endpoint, and pskit's runtime does the routing.

**Resources:** `ibm_code_engine_project`, `ibm_code_engine_function`.

**Limits:** HTTP only: no schedule/queue/storage/topic (the Terraform provider has no function subscriptions). Only python3.11. Max 120 s and 4096 MB (memory maps to fixed CPU/memory sizes). `auth` must be `none`. Code is uploaded inline as base64, so keep bundles small or use `code_reference`.

---

## digitalocean (Functions)

- **Terraform:** no cloud provider plugin. pskit renders a `doctl serverless` project and deploys it from a `terraform_data` resource (`local-exec`), redeploying when the artifact changes and running `undeploy` on destroy.
- **Credentials:** `doctl auth init`; `doctl` must be on `PATH`
- **Dependencies:** vendored

```yaml
provider:
  name: digitalocean
  options:
    namespace: fn-00000000-0000-0000-0000-000000000000   # required: doctl serverless namespaces list
    api_host: https://faas-nyc1-2ef2e6cc.doserverless.co  # optional: enables URLs in outputs
```

| Trigger | Implementation |
|---|---|
| http | `web: raw` public functions |
| schedule | scheduled triggers in `project.yml` |

**Limits:** HTTP and schedule only. Runtimes python3.9 / python3.11. Memory 128–1024 MB. `auth` must be `none`. Each function gets its own copy of the bundle under `.pskit/digitalocean-<stage>/do_project/`, so on Windows keep project paths short or enable long paths.

---

## oracle (OCI Functions)

- **Terraform:** `oracle/oci >= 6.0`
- **Credentials:** `~/.oci/config`, or provider-block options `config_file_profile`, `auth`, `tenancy_ocid`, `user_ocid`, `fingerprint`, `private_key_path`
- **Default region:** `us-ashburn-1`
- **Dependencies:** installed inside the container image build (`fdk` added)

OCI Functions only run **container images**. pskit writes `func.py`, `func.yaml` and a `Dockerfile` into the bundle, then either:

```yaml
provider:
  name: oracle
  options:
    compartment_id: ocid1.compartment.oc1..aaaa       # required
    subnet_ids: [ocid1.subnet.oc1..aaaa]               # required
    image: iad.ocir.io/mytenancy/orders:1.0            # use a pre-built image, OR
    # image_repository: iad.ocir.io/mytenancy/orders   # pskit runs docker build + push (tag = artifact hash)
    gateway_subnet_id: ocid1.subnet.oc1..bbbb          # optional, default first subnet
    gateway_endpoint_type: PUBLIC                      # optional
```

| Trigger | Implementation |
|---|---|
| http | API Gateway + deployment (`{path+}` → `{path*}`) |
| storage | Object Storage bucket (object events enabled) + Events rule |

**Resources:** `oci_functions_application`, `oci_functions_function`, `oci_apigateway_gateway`, `oci_apigateway_deployment`, `oci_objectstorage_bucket`, `oci_events_rule`.

**Limits:** no schedule/queue/topic. `auth` must be `none`. Max 300 s. Events rules can't filter by prefix/suffix (filter in the handler). **You must add IAM policies** that let API Gateway and Events invoke functions, e.g. `ALLOW any-user to use functions-family in compartment <c> where ALL {request.principal.type='ApiGateway'}`.

---

## cloudflare (Python Workers)

- **Terraform:** `cloudflare/cloudflare >= 5.28, < 6.0`
- **Credentials:** `CLOUDFLARE_API_TOKEN`
- **Dependencies:** **not bundled**. Workers run on Pyodide, so only the standard library and Pyodide's built-in packages are available.

```yaml
provider:
  name: cloudflare
  options:
    account_id: 0123456789abcdef0123456789abcdef   # required
    workers_subdomain: my-team                     # <name>.my-team.workers.dev URLs in outputs
    workers_dev: true                              # default true
    zone_id: abcdef...                             # needed for custom routes
    compatibility_date: "2025-09-01"
functions:
  api:
    handler: app.api.handler
    options:
      route: api.example.com/*                     # optional zone route (needs zone_id)
```

One Worker script per function. pskit generates a single Python module per Worker that embeds your `.py` files plus the runtime and loads them through an import hook, so your package layout keeps working. Non-Python files aren't uploaded (warning).

| Trigger | Implementation |
|---|---|
| http | `workers.dev` subdomain and/or zone route |
| schedule | Cron Triggers (UTC) |

**Resources:** `cloudflare_workers_script`, `cloudflare_workers_script_subdomain`, `cloudflare_workers_cron_trigger`, `cloudflare_workers_route`.

**Limits:** HTTP and schedule only. `auth` must be `none` (validate tokens in your handler). Third-party packages beyond Pyodide's aren't available.

---

## onprem (On-premises)

- **Terraform:** built-in `terraform_data` (no plugin download). Create/destroy run `onprem_deploy.py` via `local-exec`
- **Credentials:** none for `method: local`. SSH keys / an SSH agent for `method: ssh`. Docker CLI (and optional `DOCKER_HOST`) for `method: docker`
- **Dependencies:** native pip wheels for a local host; manylinux / Windows / macOS wheels matching `deploy.target_os` and `architecture` for a remote host. `tzdata` is vendored so cron timezones work on Windows

```yaml
provider:
  name: onprem
  region: local
  options:
    host: 127.0.0.1          # bind address on the *target*. 0.0.0.0 is advertised as 127.0.0.1 unless advertise is set
    port: 8080               # 1-65535. Give each service its own port
    advertise: https://fn.internal.example   # optional URL written into outputs
    data_dir: .pskit/onprem-data             # default .pskit/onprem-data/<service>-<stage> (local); <path>/data on SSH
    poll_seconds: 0.5
    admin_token: "${env:PSKIT_ADMIN_TOKEN, }"  # header x-pskit-token on /__pskit/* except health
    services:
      schedule: builtin                        # the only schedule implementation
      queue: builtin                           # or {use: directory, path: /var/pskit/queues}
      storage: {use: directory, path: .onprem/buckets}
      topic: builtin
      workflow: embedded                       # builtin is an alias of embedded
    deploy:
      method: local          # local | ssh | docker   (default local)
```

`pskit deploy` writes the bundle under `.pskit/onprem-<stage>/onprem_app/` and runs `onprem_deploy.py apply`. `pskit remove` stops the process (or container / systemd unit) and does not delete `data_dir`.

Omitted services default to `builtin` and are created at start. `existing: true` on one queue, bucket or topic skips that create and waits until the directory appears. Names are a single path segment (`..`, slashes and NUL are rejected).

| Trigger | Implementation |
|---|---|
| http | the host process. `auth` must be `none` (check tokens in the handler) |
| schedule | in-process rate (any positive number of minutes, hours or days, including values that are not cron) and 5-field cron. A missing IANA timezone warns and is evaluated in UTC |
| queue | one JSON file per message. `batch_size`, `batch_window`, `visibility_timeout` (default `max(30, 6 x timeout)`), `dead_letter`, `max_receive` and `fifo` are honoured. `BatchResult` retries only the failed ids |
| storage | files under `<bucket>/<key>`. `prefix`, `suffix`, `created` and `deleted`. The first start records the current files and does not emit them |
| topic | fan-out. `filter` is an exact match on message attributes, then on JSON body fields |
| workflows | embedded engine inside the host, so a workflow is not cut off by a function timeout |

Publish from outside the process:

| Request | Effect |
|---|---|
| `GET /__pskit/health` | status, no token |
| `POST /__pskit/queues/<name>` | enqueue one JSON value, a list, or `{"messages": [...]}`. `202 {"queued": [ids]}` |
| `GET /__pskit/queues/<name>` | `{"depth": n}` |
| `POST /__pskit/topics/<name>` | publish, then deliver. `{"message", "attributes"}` sets attributes when those are the only keys |
| `PUT` / `DELETE /__pskit/storage/<bucket>/<key>` | write or remove an object, then deliver |
| `POST /__pskit/invoke/<function>` | in-process invoke |
| `POST /__pskit/workflows/<name>` | run the embedded workflow |

From a handler in that process (`PSKIT_ONPREM_ROOT` is set), call `submit_queue`, `submit_topic`, `put_object` or `delete_object` in `py_serverless_kit.runtime.onprem_host`.

**Deploy targets** (`options.deploy`)

| `method` | What runs | Target needs | Laptop needs |
|---|---|---|---|
| `local` (default) | detached process on this machine | Python (the interpreter that runs `pskit`) | — |
| `ssh` | process or systemd unit on a remote host | SSH. Python only if `python: system` | OpenSSH (`ssh`, `scp`) |
| `docker` | `docker build` + `docker run` | Docker engine, not Python | Docker CLI; set `docker_host: ssh://user@host` to use a remote daemon |

When the server has **no Python**, use `python: bundle` (a portable CPython from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) is fetched at apply and copied with the app) or `method: docker` (the image is `FROM python:<minor>-slim`). Air-gapped: `python_home` (copy a tree you already have) or `python_url` + `python_sha256` (internal mirror of an `install_only` tarball).

```yaml
# SSH + portable CPython + systemd (server does not need Python)
deploy:
  method: ssh
  host: 10.0.1.12                 # or deploy: ssh://pskit@10.0.1.12/opt/pskit/orders
  user: pskit
  port: 22
  identity_file: ${env:PSKIT_SSH_KEY}   # optional; otherwise the SSH agent
  known_hosts: accept-new         # accept-new | ignore | path to a known_hosts file
  path: /opt/pskit/orders
  python: bundle                  # auto | system | bundle
  target_os: linux                # auto | linux | windows | darwin
  target_arch: x86_64             # auto | x86_64 | arm64  (drives pip wheels)
  install: systemd                # process | systemd (linux only)
  sudo: true                      # needed to write /etc/systemd/system

# Docker (server needs Docker, not Python)
deploy:
  method: docker
  image: pskit/orders:dev
  docker_host: ssh://pskit@10.0.1.12   # empty = Docker on this laptop
  restart: unless-stopped
```

`options.host` is the bind address **on the target**. Use `0.0.0.0` so other machines can call a remote host. `advertise` is the URL stored in `pskit info`. SSH deploy defaults `advertise` to `http://<ssh host>:<port>` when it is unset.

**Resources:** `terraform_data.host` with create (`onprem_deploy.py apply`) and destroy (`onprem_deploy.py destroy`) `local-exec` provisioners.

**Limits:** no cloud authorizer and no IAM mapping. A `terraform.extra` resource from another cloud (`aws_*`, `google_*`, `azurerm_*`, and the other cloud prefixes) is omitted with a warning. A state backend of `s3`, `gcs` or `azurerm` warns; set `provider.state.backend` empty (see `providers.onprem` in examples 11 and 15) to keep state on this machine. Delivery is at-least-once. A missed schedule fires once on the next poll, not as a burst. The host does not kill a handler at `timeout`; `ctx.remaining_time_ms()` still counts down. If `method` is `local` and `provider.runtime` is not the interpreter running `pskit`, validation warns and the host still uses that interpreter. `python: bundle` needs network on first apply unless `python_home` or a cached archive under `~/.cache/pskit/python-standalone` is present. systemd units are written to `/etc/systemd/system` (set `sudo: true` unless the SSH user is root).

Examples: [`examples/16-on-premises`](../examples/16-on-premises) (local process), [`examples/17-on-premises-remote`](../examples/17-on-premises-remote) (SSH / Docker / bundled Python).

---

## Writing your own provider

See [architecture.md](architecture.md#third-party-providers) and [`deploy_docs/CONTRIBUTING.md`](../deploy_docs/CONTRIBUTING.md).
