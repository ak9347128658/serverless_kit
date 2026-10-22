# Secret managers on premises

Keep API keys, database passwords and certificates in a secret manager instead of files, and read them from handlers
with **one call that works on every provider**:

```python
from py_serverless_kit import get_secret, get_secrets, put_secret

def handler(event, ctx):
    password = get_secret("db-password")              # a plain secret
    webhook = get_secret("stripe", "webhook")         # one field of a JSON secret
    every = get_secrets("stripe")                     # {"key": "...", "webhook": "..."}
    put_secret("last-run", ctx.request_id)            # create or update (Vault / Secrets Manager)
```

```js
const { getSecret, getSecrets, putSecret } = require("pskit");

exports.handler = async (event, ctx) => {
  const password = await getSecret("db-password");
  const webhook = await getSecret("stripe", "webhook");
  const fallback = await getSecret("optional", { default: "" });
};
```

No extra library: the calls are part of the pskit runtime (Python standard library, Node.js built-ins). Values are
cached for 60 seconds per function instance.

## One click: OpenBao or LocalStack

*Resources → On-prem services → + Create service → Secret managers* (or the CLI):

| Service | API it speaks | Use it from | Keeps data |
|---|---|---|---|
| **OpenBao** (`openbao`) | HashiCorp Vault (KV v2 at `secret/`) | `get_secret`, `hvac`, `node-vault`, `vault` / `bao` CLI, Terraform's vault provider | yes: file storage in a volume, unsealed automatically on start |
| **LocalStack** (`localstack`, *Use as: Secret manager*) | AWS Secrets Manager + SSM Parameter Store | `get_secret`, boto3, `@aws-sdk/client-secrets-manager`, the AWS CLI | yes (`PERSISTENCE=1`), for development and tests |

```bash
pskit resources onprem create secrets --service openbao --save                    # this machine
pskit resources onprem create secrets --service openbao --server edge1 --save     # a saved SSH server
pskit resources onprem create aws-secrets --service localstack --role secrets --save
```

Name it **`secrets`** and handlers need no `store=`: it is the default store. Another key works too:
`get_secret("db-password", store="aws-secrets")` / `getSecret("db-password", { store: "aws-secrets" })`.

Creating it writes the connection to the env file (`.env.onprem`), which the host loads:

```bash
PSKIT_SECRETS_DRIVER=vault
PSKIT_SECRETS_URL=http://10.0.0.5:8200
PSKIT_SECRETS_TOKEN=...            # the service password is the OpenBao token
PSKIT_SECRETS_MOUNT=secret
```

OpenBao's password (from the create form, or generated) becomes a root token. Its first start initialises it with one
unseal key, kept in the data volume so every restart unseals by itself. That is convenient on one server; the volume
and backups then hold the key, so protect them like the secrets. For a hardened cluster (several unseal keys, auto-unseal
with a KMS, policies per app), run OpenBao or Vault yourself and point pskit at it (below).

## Add and read secrets

| Where | How |
|---|---|
| Studio | *On-prem services → Console* on the service: `kv put secret/db-password value=S3cret`, `kv get secret/db-password` (OpenBao) or `secretsmanager create-secret --name db-password --secret-string S3cret` (LocalStack) |
| Web UI | OpenBao at `http://<host>:8200/ui/`, sign in with *Token* = the password (`PSKIT_<KEY>_TOKEN`) |
| CLI | `pskit resources onprem console secrets "kv put secret/stripe key=sk_1 webhook=wh_2"` |
| Code | `put_secret("stripe", {"key": "sk_1", "webhook": "wh_2"})` |
| Any program | `vault kv get -address=http://host:8200 secret/db-password`, `hvac`, boto3 with `endpoint_url` |

A secret with one field named `value` is a plain secret: `get_secret(name)` returns it. With several fields,
`get_secret(name, field)` picks one and `get_secrets(name)` returns all of them.

## An existing Vault, OpenBao or AWS Secrets Manager

Set the store variables yourself (env file, `functions.*.environment`, or the host's environment):

| Store | Variables |
|---|---|
| Vault / OpenBao | `PSKIT_SECRETS_DRIVER=vault`, `PSKIT_SECRETS_URL=https://vault.internal:8200`, `PSKIT_SECRETS_TOKEN` (or `token_env` in your own tooling), optional `PSKIT_SECRETS_MOUNT` |
| AWS Secrets Manager | `PSKIT_SECRETS_DRIVER=aws`, optional `PSKIT_SECRETS_REGION`; credentials from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`, or `PSKIT_SECRETS_ACCESS_KEY` / `_SECRET_KEY`. `PSKIT_SECRETS_URL` for a compatible endpoint |
| Environment only | nothing: `get_secret("db-password")` reads `PSKIT_SECRET_DB_PASSWORD`, then `DB_PASSWORD` |

## On the clouds

The same handler code runs everywhere:

- **AWS Lambda**: set `PSKIT_SECRETS_DRIVER: aws` in the function's `environment` and allow
  `secretsmanager:GetSecretValue` (and `PutSecretValue` / `CreateSecret` for `put_secret`) in its role. Lambda's
  credentials are used:

  ```yaml
  functions:
    api:
      handler: app.handlers.api
      environment: {PSKIT_SECRETS_DRIVER: aws}
      iam:
        statements:
          - {effect: Allow, actions: ["secretsmanager:GetSecretValue"], resources: ["arn:aws:secretsmanager:*:*:secret:*"]}
  ```
- **Other clouds** (Azure Key Vault, GCP Secret Manager, ...): map the secret into the function's environment with the
  provider's own reference, and the `env` driver reads it (`PSKIT_SECRET_<NAME>` or `<NAME>`). Or run OpenBao on a
  server and use the `vault` driver.

## Troubleshooting

| Message | Fix |
|---|---|
| `secret 'x' not found: set PSKIT_SECRET_X ...` | no store is configured for this function: check the env file has `PSKIT_SECRETS_URL` / `_TOKEN`, and that `provider.options.env_file` points to it |
| `vault: reading 'x' failed (403)` | wrong token: `PSKIT_<KEY>_TOKEN` must match the OpenBao password |
| `secret store host:8200 is unreachable` | the functions' server cannot reach the port: same server, or open 8200 from the functions' server to the secrets server |
| `secretsmanager: ... AccessDenied` | add the Secrets Manager permissions to the function's role |
