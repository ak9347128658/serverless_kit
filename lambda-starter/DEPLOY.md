# Deploying lambda-starter

How to deploy the three functions in this project (`hello`, `processor` and `items`) with [py-serverless-kit](https://pypi.org/project/py-serverless-kit/) (`pskit`) to any supported cloud.

The handler code in `app/handlers.py` is the same for every cloud. Only the config file changes:

| File | Cloud | Tier |
|---|---|---|
| `aws.pskit.yml` | AWS Lambda + API Gateway HTTP API | stable |
| `gcp.pskit.yml` | Google Cloud Functions (2nd gen) | stable |
| `azure.pskit.yml` | Azure Functions (Linux Consumption) | stable |
| `alibaba.pskit.yml` | Alibaba Function Compute 3.0 | beta |
| `tencent.pskit.yml` | Tencent SCF + API Gateway | beta |
| `huawei.pskit.yml` | Huawei FunctionGraph + APIG | beta |
| `ibm.pskit.yml` | IBM Code Engine functions | beta |
| `digitalocean.pskit.yml` | DigitalOcean Functions | beta |
| `oracle.pskit.yml` | Oracle OCI Functions (container images) | experimental |
| `cloudflare.pskit.yml` | Cloudflare Python Workers | experimental |

`pskit deploy` packages the code, generates Terraform for that cloud, and runs `terraform init` and `terraform apply`.

---

## Step 1: Pick your config file

**Option A: keep the prefixed name** and pass it with `-c` on every command:

```bash
pskit deploy -c aws.pskit.yml
pskit info   -c aws.pskit.yml
```

**Option B: make it the default** by renaming it to `pskit.yml`, then drop `-c`:

```bash
mv aws.pskit.yml pskit.yml           # PowerShell: Rename-Item aws.pskit.yml pskit.yml
pskit deploy
```

The rest of this guide uses `-c <cloud>.pskit.yml`. With option B, leave out the `-c` part.

## Step 2: Install the tools (one time)

| Tool | Needed for | Install |
|---|---|---|
| Python ≥ 3.9 | all | <https://www.python.org/downloads/> |
| Terraform ≥ 1.5 (or OpenTofu) | all | Windows `winget install Hashicorp.Terraform` · macOS `brew install hashicorp/tap/terraform` · <https://developer.hashicorp.com/terraform/install> |
| Cloud CLI | logging in and calling functions | see the table in [Step 5](#step-5-log-in-to-your-cloud) |

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS / Linux:         source .venv/bin/activate
pip install py-serverless-kit pytest
pskit --version
pskit providers              # what each cloud supports
```

## Step 3: Set the prefix, app name and region

Every config file starts with the same block:

```yaml
custom:
  prefix: ${env:PSKIT_PREFIX, myco}           # <-- change me
  app: ${env:PSKIT_APP, lambda-starter}       # <-- change me
  region: ${env:DEPLOY_REGION, us-east-1}     # <-- change me

service: ${self:custom.prefix}-${self:custom.app}
```

`${env:NAME, default}` reads an environment variable and falls back to the default. You can set the values in either of two ways:

- **edit the defaults** in the file, e.g. `prefix: ${env:PSKIT_PREFIX, acme}`, or
- **set environment variables**:

  ```bash
  export PSKIT_PREFIX=acme PSKIT_APP=orders DEPLOY_REGION=us-east-1          # bash
  ```
  ```powershell
  $env:PSKIT_PREFIX="acme"; $env:PSKIT_APP="orders"; $env:DEPLOY_REGION="us-east-1"   # PowerShell
  ```

**Naming rules:** `<prefix>-<app>` must be lowercase letters, digits and `-`, start with a letter, and be 2–32 characters long. Each function is deployed as `<prefix>-<app>-<stage>-<function>`, e.g. `acme-orders-dev-items`.

Also fill in every line marked **`<-- change me (required)`** under `provider.options` (see [Step 4](#step-4-cloud-specific-settings)).

> Use the same prefix, app and region on every later deploy and remove. If you change them, pskit treats it as a new service: it creates new resources, and the old ones remain. To rename, run `pskit remove` first.

Check the config:

```bash
pskit validate -c aws.pskit.yml
# acme-orders -> AWS Lambda [stable] stage=dev region=us-east-1
# Configuration is valid.
```

## Step 4: Cloud-specific settings

| Cloud | Required in the file (or env var) | Default region | Notes |
|---|---|---|---|
| aws | nothing | `us-east-1` | Optional `options.profile` |
| gcp | `options.project` (`GOOGLE_CLOUD_PROJECT`) | `us-central1` | Enable the Cloud Functions, Cloud Run, Cloud Build, Artifact Registry and IAM APIs. Don't add a root `main.py` |
| azure | `options.subscription_id` (`ARM_SUBSCRIPTION_ID`) | `eastus` | All 3 functions share one Function App, with no `/api` prefix |
| alibaba | nothing | `cn-hangzhou` | Optional `options.profile`, `iam.role` |
| tencent | nothing | `ap-guangzhou` | Runtime python3.10. Memory must be 64, 128 or a multiple of 128 |
| huawei | `options.apig_instance_id` (`HW_APIG_INSTANCE_ID`) | `cn-north-4` | Needs a dedicated API Gateway instance for HTTP |
| ibm | nothing | `us-south` | Runtime python3.11, max timeout 120 s |
| digitalocean | `options.namespace` (`DO_FUNCTIONS_NAMESPACE`) | `nyc1` | Get it from `doctl serverless namespaces list`. `doctl` must be on PATH |
| oracle | `options.compartment_id`, `options.subnet_ids`, `options.image_repository` or `options.image` (`OCI_COMPARTMENT_ID`, `OCI_SUBNET_ID`, `OCI_IMAGE_REPOSITORY`) | `us-ashburn-1` | Docker is needed when using `image_repository`. Add an IAM policy so API Gateway can call functions (the validate warning shows it) |
| cloudflare | `options.account_id`, `options.workers_subdomain` (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_WORKERS_SUBDOMAIN`) | n/a | Standard library and Pyodide packages only. `requirements.txt` isn't installed |

## Step 5: Log in to your cloud

| Cloud | Log in | Check |
|---|---|---|
| aws | `aws configure` or `aws sso login --profile p` + `AWS_PROFILE=p` | `aws sts get-caller-identity` |
| gcp | `gcloud auth application-default login` (or `GOOGLE_APPLICATION_CREDENTIALS=key.json`) | `gcloud config list` |
| azure | `az login` + `ARM_SUBSCRIPTION_ID` (or `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID`) | `az account show` |
| alibaba | `ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY` | `aliyun sts GetCallerIdentity` |
| tencent | `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` | `tccli sts GetCallerIdentity` |
| huawei | `HW_ACCESS_KEY` / `HW_SECRET_KEY` / `HW_REGION_NAME` | |
| ibm | `IC_API_KEY` | `ibmcloud login --apikey $IC_API_KEY` |
| digitalocean | `doctl auth init` | `doctl serverless status` |
| oracle | `oci setup config` (writes `~/.oci/config`) | `oci iam region list` |
| cloudflare | `CLOUDFLARE_API_TOKEN` | |

The identity you deploy with needs permission to create functions, roles and policies, API gateways and log groups. In a sandbox account, an admin role is the simplest choice.

## Step 6: Test locally (optional, no cloud needed)

```bash
pytest -q                                        # uses pskit.yml, else aws.pskit.yml
PSKIT_CONFIG=gcp.pskit.yml pytest -q             # test against another config
pskit dev -c aws.pskit.yml                       # http://127.0.0.1:3000/hello
pskit invoke -c aws.pskit.yml -f processor -d '{"numbers":[1,2,3]}'
```

## Step 7: Preview and deploy

```bash
pskit package -c aws.pskit.yml --skip-deps   # optional: build the bundle + Terraform only (.pskit/aws-dev/)
pskit plan    -c aws.pskit.yml               # optional: terraform plan

pskit deploy  -c aws.pskit.yml               # deploy (auto-approves)
pskit deploy  -c aws.pskit.yml --confirm     # or: show the plan first, then apply
```

The first deploy takes a few minutes because Terraform downloads the cloud's provider plugin.

## Step 8: Get URLs and IDs

```bash
pskit info -c aws.pskit.yml            # endpoint(s) and function ids/ARNs from the last deploy
pskit info -c aws.pskit.yml --refresh  # re-read from Terraform
pskit dashboard --open                 # HTML overview of every cloud/stage you deployed
```

The data is also saved in `.pskit/<cloud>-<stage>/deployment.json`.

How the HTTP functions are exposed:

| Cloud | URL shape |
|---|---|
| aws | one API Gateway base URL: `<endpoint>/hello`, `<endpoint>/items` |
| azure | one Function App URL: `https://<app>.azurewebsites.net/hello`, `/items` |
| tencent, huawei, oracle | one API Gateway base URL, with the same paths |
| gcp, alibaba, ibm, digitalocean, cloudflare | **one URL per function**: call `<hello URL>/hello` and `<items URL>/items` |

## Step 9: Call the functions

### HTTP: `hello` and `items`

```bash
URL=<base URL from pskit info>        # for per-function URLs, use the items URL for /items

curl "$URL/hello?name=Ada"
curl "$URL/items"
curl "$URL/items/1"
curl -X POST "$URL/items"   -H "content-type: application/json" -d '{"name":"pen","price":2}'
curl -X PUT  "$URL/items/1" -H "content-type: application/json" -d '{"name":"book","price":12}'
```

PowerShell:

```powershell
Invoke-RestMethod "$URL/hello?name=Ada"
Invoke-RestMethod "$URL/items" -Method Post -ContentType "application/json" -Body '{"name":"pen","price":2}'
Invoke-RestMethod "$URL/items/1" -Method Put -ContentType "application/json" -Body '{"name":"book","price":12}'
```

### Invoke-only: `processor`

| Cloud | How to call it |
|---|---|
| aws | `aws lambda invoke --function-name <ARN> --cli-binary-format raw-in-base64-out --payload '{"numbers":[1,2,3]}' out.json` |
| gcp | `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" -H "content-type: application/json" -d '{"numbers":[1,2,3]}' <processor URL>` |
| azure | `curl -X POST "<app URL>/_pskit/invoke/processor?code=<function key>" -d '{"numbers":[1,2,3]}'` |
| ibm, cloudflare | POST the JSON to the processor's URL from `pskit info` |
| digitalocean | `doctl serverless functions invoke <name> -p numbers:[1,2,3]` |
| oracle | `oci fn function invoke --function-id <OCID> --file - --body '{"numbers":[1,2,3]}'` |
| alibaba, tencent, huawei | the cloud's console, SDK or CLI "invoke function" command |

On AWS from PowerShell, put the payload in a file: `--payload fileb://payload.json`.

The result is `{"count": 3, "sum": 6, "request_id": "..."}`.

## Step 10: Update, other stages, remove

```bash
pskit deploy -c aws.pskit.yml                   # after code changes; unchanged bundles aren't re-uploaded
pskit deploy -c aws.pskit.yml --stage prod      # separate copy: <prefix>-<app>-prod-* (512 MB, see stages:)
pskit info   -c aws.pskit.yml --stage prod

pskit remove -c aws.pskit.yml                   # delete the dev stage (asks for confirmation)
pskit remove -c aws.pskit.yml --stage prod -y
```

## Terraform state: important

Terraform remembers what it created in `.pskit/<cloud>-<stage>/terraform.tfstate`. That folder is git-ignored.

- **Don't delete `.pskit/`** while resources exist. Without the state, pskit can't update or remove them.
- For teams and CI, use a remote backend. Add it under `provider:` in your config:

```yaml
provider:
  state:
    backend: s3                     # gcs (GCP), azurerm (Azure), or any Terraform backend
    config:
      bucket: acme-terraform-state  # <-- your bucket (create it once)
      key: ${self:service}/${opt:stage}.tfstate
      region: us-east-1
```

## Deploying from CI (GitHub Actions, AWS example)

```yaml
name: deploy
on:
  push:
    branches: [main]
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: lambda-starter
    env:
      PSKIT_PREFIX: acme
      PSKIT_APP: orders
      DEPLOY_REGION: us-east-1
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - uses: hashicorp/setup-terraform@v3
        with: {terraform_wrapper: false}
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/github-deployer   # <-- your role
          aws-region: us-east-1
      - run: pip install py-serverless-kit pytest
      - run: pytest -q
      - run: pskit deploy -c aws.pskit.yml --stage prod
```

CI needs the remote state backend above. Otherwise each run starts with empty state.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No config file found` | Pass `-c <cloud>.pskit.yml`, or rename that file to `pskit.yml` |
| `terraform not found` | Install Terraform and reopen the terminal, or set `PSKIT_TERRAFORM_BIN` |
| Credential errors / expired token | Log in again (Step 5) and run the check command |
| `AccessDenied` / `Forbidden` during deploy | The deploying identity lacks permissions to create functions, roles, gateways or logs |
| `already exists` | The names collide with an earlier deploy, or the state was lost. Change the prefix, or delete the old resources |
| Service name invalid | `<prefix>-<app>` must be lowercase and 2–32 chars, and start with a letter |
| HTTP 500 | Check the cloud's function logs. Temporarily add `PSKIT_DEBUG: "1"` to `provider.environment` (dev only) |
| AWS `Invalid base64` on invoke | Add `--cli-binary-format raw-in-base64-out` |
| AWS HTTP 503/504 | API Gateway caps requests at 30 s |
| DigitalOcean: `doctl is not on PATH` | Install `doctl` and run `doctl auth init` |
| Oracle: API returns 404/500 | Add the API Gateway → functions IAM policy shown by `pskit validate` |
