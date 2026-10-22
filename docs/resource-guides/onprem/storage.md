# On-premises storage (`type: storage`)

Run functions when files appear in a bucket, and read or write objects: the host's built-in buckets, a shared folder,
MinIO or another S3-compatible store, or a bucket in a cloud (S3, R2, Azure Blob, GCS, ...).

The host lists each watched bucket every `poll_seconds` and compares ETags: a new or changed object emits `created`, a
missing one `deleted`. Objects already there at the first start are recorded, not emitted.

## Built-in bucket

```yaml
resources:
  uploads:
    type: storage                # a directory under data_dir, created at start

functions:
  ingest:
    handler: app.handlers.ingest
    events:
      - storage: {resource: uploads, prefix: in/, suffix: .csv, events: [created, deleted]}
```

A folder you already have (NFS, SMB, a scanner's output directory):

```yaml
resources:
  inbox:
    type: storage
    external: true
    options: {driver: directory, path: /mnt/scans}
```

## MinIO (or Ceph, Garage, SeaweedFS, any S3-compatible store)

### 1. Create the bucket and keys (MinIO console)

1. Run MinIO if you don't have it:
   `docker run -d -p 9000:9000 -p 9001:9001 -e MINIO_ROOT_USER=admin -e MINIO_ROOT_PASSWORD=<password> minio/minio server /data --console-address :9001`
2. Open the console at `http://<minio-host>:9001` and sign in.
3. **Buckets → Create Bucket** → name (for example `uploads`) → **Create Bucket**.
4. **Access Keys → Create access key** → optionally restrict it with a policy for that bucket → **Create** → copy the
   *Access Key* and *Secret Key* (or download them; the secret is shown once).

### 2. Collect the values

| Value | Where | Example |
|---|---|---|
| Endpoint | the S3 API port (not the console port) | `http://minio.internal:9000` |
| Bucket | **Buckets** | `uploads` |
| Access key / secret key | **Access Keys** | |

```bash
export MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=...      # on the host
```

### 3. Declare it

```yaml
resources:
  uploads:
    type: storage
    external: true
    name: uploads                 # the bucket
    options:
      driver: minio               # path-style requests to `endpoint`
      endpoint: http://minio.internal:9000
      access_key_env: MINIO_ACCESS_KEY
      secret_key_env: MINIO_SECRET_KEY
      prefix: incoming/           # only watch keys below it (recommended on large buckets)

functions:
  ingest:
    handler: app.handlers.ingest
    events: [{storage: {resource: uploads}}]
```

Other S3-compatible stores: `driver: s3` with `endpoint` (and `path_style: true` if needed).

## A bucket in a cloud

| Bucket | Driver | Required | Where to find the values |
|---|---|---|---|
| Amazon S3 | `aws` | `region` (+ `access_key_env` / `secret_key_env`, or `AWS_*` on the host) | S3 console → bucket → *Properties → AWS Region*; IAM access key |
| Cloudflare R2 | `r2` | `account_id`, `access_key_env`, `secret_key_env` | [cloudflare/storage.md](../cloudflare/storage.md#2-collect-the-values) (*Manage R2 API Tokens*) |
| Azure Blob | `azure_blob` | `connection_string_env` (or `account` + key / SAS), `container` | [azure/storage.md](../azure/storage.md#2-collect-the-values) |
| Google Cloud Storage | `gcs` | `token_command` or `access_token_env` | bucket name from the Cloud Storage console |
| Alibaba OSS, Tencent COS, Huawei OBS, Oracle, IBM COS, DigitalOcean Spaces | `oss`, `cos`, `obs`, `oci`, `ibm_cos`, `spaces` | `region` (+ `namespace` for `oci`), HMAC / S3 keys | each console's bucket overview and access-key page; presets in [resources.md](../../resources.md#storage-drivers) |

```yaml
resources:
  media:
    type: storage
    external: true
    name: company-media
    options:
      driver: r2
      account_id: 0123456789abcdef0123456789abcdef
      access_key_env: R2_ACCESS_KEY_ID
      secret_key_env: R2_SECRET_ACCESS_KEY
      prefix: incoming/
```

## Use it in a handler

```python
from py_serverless_kit import resource
from py_serverless_kit.runtime.onprem_host import put_object


def ingest(event, ctx):
    for record in event.records:
        ctx.logger.info("file", extra={"bucket": record.bucket, "key": record.key, "action": record.action})
        if record.action == "created":
            put_object(resource("uploads").native_name, "done/" + record.key, b"processed")
```

`record.key` has `prefix` stripped. `put_object` / `delete_object` go through the driver, so the same code writes to a
directory, MinIO or S3. To read object contents, use the store's client (`boto3`, `minio`, `azure-storage-blob`) or, for
built-in buckets, the file under `data_dir`.

## Test

```bash
pskit validate
pskit deploy
pskit resources send uploads -d '{"key": "incoming/a.csv", "content": "a,b"}'
curl -X PUT --data 'a,b' http://127.0.0.1:8080/__pskit/storage/uploads/incoming/b.csv
pskit logs -f --function ingest
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Existing files never trigger | expected: the first start records them; upload a new one |
| `SignatureDoesNotMatch` against MinIO | use the API port (9000), not the console (9001); check the keys |
| Slow polls on a big bucket | set `prefix`, raise `poll_seconds` |
| `set both access_key and secret_key (or neither)` | give both, or neither to use the `AWS_*` variables |
