# lambda-starter: 3 serverless functions with py-serverless-kit

A starter project built with [py-serverless-kit](https://pypi.org/project/py-serverless-kit/) (`pskit`). The same Python code deploys to AWS, GCP, Azure, Alibaba, Tencent, Huawei, IBM, DigitalOcean, Oracle or Cloudflare.

| # | Function | Trigger | What it does |
|---|---|---|---|
| 1 | `hello` | HTTP `GET /hello` | Returns a greeting (`?name=Ada`) |
| 2 | `processor` | none (invoke-only) | Called directly (on AWS, by its **ARN**) with a JSON payload; returns the count and sum of `numbers` |
| 3 | `items` | HTTP `GET /items`, `GET /items/{id}`, `POST /items`, `PUT /items/{id}` | One function that handles GET, POST and PUT |

**Deploying?** Follow [DEPLOY.md](DEPLOY.md).

```
lambda-starter/
  aws.pskit.yml           # one complete config per cloud  <-- edit custom: first
  gcp.pskit.yml
  azure.pskit.yml
  alibaba.pskit.yml
  tencent.pskit.yml
  huawei.pskit.yml
  ibm.pskit.yml
  digitalocean.pskit.yml
  oracle.pskit.yml
  cloudflare.pskit.yml
  app/handlers.py         # handler code, identical on every cloud
  requirements.txt        # runtime dependencies (empty here)
  tests/test_handlers.py  # local tests with LocalRunner
  DEPLOY.md               # deployment guide
```

## Config files

Each `<cloud>.pskit.yml` is standalone. Use it with `-c`, or rename the one you want to `pskit.yml`:

```bash
pskit deploy -c aws.pskit.yml        # keep the prefixed name
mv aws.pskit.yml pskit.yml           # ...or make it the default
pskit deploy
```

You can delete the files for clouds you don't use.

All names come from the `custom:` block at the top of each file:

```yaml
custom:
  prefix: ${env:PSKIT_PREFIX, myco}           # <-- change me
  app: ${env:PSKIT_APP, lambda-starter}       # <-- change me
  region: ${env:DEPLOY_REGION, us-east-1}     # <-- change me

service: ${self:custom.prefix}-${self:custom.app}
```

- Each function is named `<prefix>-<app>-<stage>-<function>`, e.g. `acme-orders-dev-items`.
- `${env:NAME, default}` reads an environment variable and falls back to the default.
- Cloud-specific required values, such as the GCP project or Azure subscription, are marked `<-- change me (required)`.
- `package.exclude` keeps the `*.pskit.yml` files and docs out of the deployed bundle.

## Quick start (AWS)

```bash
pip install py-serverless-kit pytest
export PSKIT_PREFIX=acme PSKIT_APP=orders DEPLOY_REGION=us-east-1

pytest -q
pskit dev -c aws.pskit.yml                       # http://127.0.0.1:3000/hello
pskit deploy -c aws.pskit.yml                    # needs Terraform + AWS credentials
pskit info -c aws.pskit.yml                      # API URL and function ARNs
```

## How this project was created

```bash
pskit init lambda-starter --provider aws --template http
```

The generated handlers were replaced with the three functions in `app/handlers.py`, and the config was split into one file per cloud.

## Handler basics

```python
from py_serverless_kit import BadRequest, Context, Event, NotFound, Response

def hello(event: Event, ctx: Context):
    return {"message": f"Hello, {event.http.query.get('name', 'world')}!"}   # dict -> JSON 200

def processor(event: Event, ctx: Context):
    numbers = (event.data or {}).get("numbers", [])     # event.data = invoke payload
    return {"count": len(numbers), "sum": sum(numbers)}

def items(event: Event, ctx: Context):
    method, item_id = event.http.method, event.http.path_params.get("id")
    ...  # GET list / GET one / POST -> 201 / PUT; raise NotFound -> 404
```

- `event.http.method`, `.path_params`, `.query`, `.headers`, `.json()` (bad JSON gives a 400 automatically).
- `event.data` holds the payload of an invoke-only call.
- Return a `dict` for JSON 200, or use `Response.json(body, status=201)`.
- Raise `BadRequest`, `NotFound`, `Conflict` and similar errors to return that status. Any other exception becomes a 500, and the traceback goes to the cloud's logs.
- `ctx.logger.info("msg", extra={...})` writes structured JSON logs.

> The `items` store is in memory, so data resets on a cold start and isn't shared between instances. Use a real database for real data.

## Local development

```bash
pytest -q                                   # uses pskit.yml if present, else aws.pskit.yml
PSKIT_CONFIG=gcp.pskit.yml pytest -q        # test with another cloud's config
pskit validate -c gcp.pskit.yml
pskit dev -c aws.pskit.yml
pskit invoke -c aws.pskit.yml -f processor -d '{"numbers":[1,2,3]}'
pskit invoke -c aws.pskit.yml -f items --method GET --path /items/1
```

> **PowerShell:** `curl` is an alias of `Invoke-WebRequest`, so use `curl.exe` or `Invoke-RestMethod` instead.
