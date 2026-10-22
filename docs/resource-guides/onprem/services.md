# One-click services for on-premises functions

pskit can **create** the queue, stream, bucket, database or cache your on-premises functions use, then add it to
`pskit.yml` as a resource and, for queues, streams and buckets, as a trigger. It starts the service as a Docker container
on this machine or on any server you can reach with SSH. That can be the server your functions run on or a different
one. It can also install Python or Docker on an SSH server.

| Service | Use as | Driver in `pskit.yml` | Default port | Backup |
|---|---|---|---|---|
| Redis | queue, stream, cache | `redis` (list), `redis_stream` (consumer group) | 6379 | RDB snapshot |
| Valkey | queue, stream, cache | `redis`, `redis_stream` | 6379 | RDB snapshot |
| Kafka (Redpanda, one node) | queue | `custom`: `pskit_drivers.kafka_queue:KafkaQueue` (adds `kafka-python`) | 9092 | data directory (`.tar.gz`) |
| NATS JetStream | queue | `custom`: `pskit_drivers.nats_queue:NatsQueue` (adds `nats-py`) | 4222 (monitor 8222) | data directory |
| RabbitMQ | queue | `custom`: `pskit_drivers.rabbitmq_queue:RabbitQueue` (adds `pika`) | 5672 (UI 15672) | definitions (queues, users) |
| ElasticMQ (SQS API) | queue | `sqs` with `endpoint` | 9324 (UI 9325) | none (in memory) |
| MinIO | storage | `minio` | 9000 (console 9001) | data directory |
| PostgreSQL | database | connection values | 5432 | `pg_dumpall` (`.sql`) |
| MySQL / MariaDB | database | connection values | 3306 | `mysqldump` / `mariadb-dump` (`.sql`) |
| MongoDB | database | connection values | 27017 | `mongodump --archive` |
| Memcached | cache | connection values | 11211 | none |
| Mailpit (SMTP) | custom | connection values | 1025 (web 8025) | none |
| Python 3, Docker | runtime on an SSH server | — | — | — |

## In the studio

1. **Resources → On-prem services → + Create service**.
2. Pick a service. For Redis / Valkey choose **Queue** (list), **Stream** (consumer group) or **Cache**.
3. **Where**: *This machine*, or *SSH server* with host, user, port, `.pem` and *use sudo*. When
   `provider.options.deploy` already targets a server, it is filled in.
4. Tick the functions to **trigger** (queues, streams and buckets). Each gets `queue: {resource: <key>}` or
   `storage: {resource: <key>}`.
5. **Create**. The output streams below the form. When it finishes, the resource, `provider.options.env_file` and the
   triggers are added to the draft; review them and **Save**.
6. **Deploy**, then test with **Resources → Test** or `pskit resources send <key>`.

Each row then offers **Details** (container state, health, endpoint, credentials with *Show values*, the container
log), **Backup**, **Add to pskit.yml** (if you removed it from the file) and **Remove** (keep or delete the data volume;
the resource and its triggers are taken out of the draft).

## From the command line

```bash
pskit resources onprem catalog
pskit resources onprem create jobs --service redis --attach worker --save
pskit resources onprem create events --service kafka --attach worker --save
pskit resources onprem create tasks --service nats --ssh ubuntu@10.0.0.7 --pem ~/.ssh/mq.pem --sudo --save
pskit resources onprem create stream --service valkey --role stream --attach worker --save
pskit resources onprem create orders-db --service postgres --ssh ubuntu@10.0.0.5 --pem key.pem --save
pskit resources onprem create py --service python --ssh ubuntu@10.0.0.5 --pem key.pem

pskit resources onprem list
pskit resources onprem status jobs [--reveal]
pskit resources onprem logs jobs
pskit resources onprem backup orders-db          # -> .pskit/backups/orders-db-<time>.sql
pskit resources onprem remove jobs [--delete-data] [--save]
```

Without `--save` the command prints the YAML to paste. `--save` rewrites `pskit.yml` (comments are not kept).

## What gets written

```yaml
provider:
  name: onprem
  options:
    env_file: .env.onprem            # added once; shipped to the host, never into Terraform state
resources:
  jobs:
    type: queue
    external: true                   # pskit's deploy does not create or delete it
    options:
      driver: redis
      url_env: PSKIT_JOBS_URL        # read from .env.onprem on the host
      key: jobs
functions:
  worker:
    events:
      - queue: {resource: jobs}
```

- **Passwords** are generated and written to the env file (`.env.onprem` unless `provider.options.env_file` names
  another), as `PSKIT_<KEY>_URL`, `PSKIT_<KEY>_PASSWORD`, `..._ACCESS_KEY` / `..._SECRET_KEY`. `pskit deploy` copies
  that file next to the host as `host.env`, and the host loads it at start. The file is added to `.gitignore` when the
  project has one. Handlers read the same variables: `ctx.env[resource("orders-db")["password_env"]]`.
- **Driver classes** for Kafka, NATS and RabbitMQ are copied once into `pskit_drivers/` under `package.source`, and the
  client library is appended to `requirements.txt` (on premises it **is** installed). Edit them freely; pskit never
  overwrites them.
- **What was created** is kept in `.pskit/onprem-services.json`, credentials included: keep `.pskit/` out of git.
- When the main provider is not `onprem`, everything goes under `providers.onprem:` so the other clouds are unaffected.

## Same server or a different one

The address in the connection is the SSH host (or `127.0.0.1` on this machine). Change it under *Advanced → Address
functions use* (`--address`) when the functions reach the server by another name or a private IP.

| Functions run | Service created | Works because |
|---|---|---|
| on this machine (`deploy.method: local`) | on this machine | both use `127.0.0.1` |
| on server A (`method: ssh`) | on server A | published on `0.0.0.0`, reached as `A` |
| on server A | on server B | published on `0.0.0.0` of B; open the port from A to B in the firewall |
| in a container (`method: docker`) | on this machine | use *Address* `host.docker.internal` (Docker Desktop) or the host's LAN IP |

A queue can also live in **another pskit host's built-in queue**, with no broker at all. On the host that consumes it:

```yaml
resources:
  orders:
    type: queue
    external: true
    options:
      driver: pskit_host
      endpoint: http://10.0.0.6:8080          # the other host
      queue: orders                            # its queue name there
      token_env: OTHER_HOST_ADMIN_TOKEN        # its provider.options.admin_token, if set
```

Messages are leased through that host's admin API (`/__pskit/queues/<name>/lease|ack|nack`), so a crash on either
side loses nothing.

## Use it from code and test it

*Resources → Test a resource* shows, for the selected resource, Python and Node.js code to paste into a handler
(publish + consume for queues, streams and buckets; a connection example for databases, caches and SMTP) with the
library to add, and **Test connection**, which runs from the on-prem host once it is deployed. In a terminal:

```bash
pskit resources check orders-db        # e.g. "127.0.0.1:5432 accepts connections (3.1 ms, from host)"
pskit resources send jobs -d '{"order_id": 1}'
```

## Removing

*Resources → Delete* on a resource backed by a one-click service asks what to do with the service: **pskit.yml
only**, **Stop service, keep data** (the volume stays; creating the service again with the same key reuses it) or
**Delete service and data**. For a built-in on-prem queue, bucket or topic it offers **Delete, keep data** or
**Delete with its data** (`pskit resources clear`).

## Backups

`Backup` streams a dump out of the container into `.pskit/backups/` on this machine, also from an SSH server:

| Service | File | Restore |
|---|---|---|
| Redis / Valkey | `.rdb` | stop the container, copy it to `/data/dump.rdb` in the volume, start it |
| PostgreSQL | `.sql` | `psql -U pskit -f file.sql postgres` |
| MySQL / MariaDB | `.sql` | `mysql -uroot -p < file.sql` |
| MongoDB | `.archive` | `mongorestore --archive=file.archive -u pskit -p ... --authenticationDatabase admin` |
| RabbitMQ | `.definitions.json` | `rabbitmqctl import_definitions file.json` (messages are not included) |
| Kafka, NATS, MinIO | `.tar.gz` of the data directory | stop the container, extract into the volume, start it |

Data directories are copied while the service runs. For Kafka and NATS, stop producers first for a consistent copy.

## Troubleshooting

| Message | Fix |
|---|---|
| `Docker is not running on this machine` | start Docker Desktop or the docker service |
| `Docker is not installed on <host>` | create the `docker` runtime first, or let *Create* install it (needs sudo or root) |
| `pull access denied` | the image moved; set *Advanced → Docker image* (`--image`) to a mirror you can pull |
| `port is already allocated` | pick another *Port* (`--port`) |
| `did not become ready within 90 s` | `pskit resources onprem logs <key>`; on small servers Kafka needs about 1 GB of RAM |
| `is not reachable from this machine; check the firewall` | the service runs; open the port from the functions' server |
| `sqs: ... QueueDoesNotExist` (ElasticMQ) | ElasticMQ forgets queues on restart; redeploy so the sqs driver gets `create_queue` (the default for non-AWS endpoints since 1.3.1) |
| `environment variable PSKIT_..._URL ... is not set on the host` | `provider.options.env_file` is missing or the file was deleted; recreate the service or add the line |
