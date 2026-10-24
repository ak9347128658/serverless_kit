# Scaling cluster (on premises)

By default the on-prem provider runs all functions in **one host process on one server**. Nothing about that
changes. A **scaling cluster** adds a second option: a **master server** plus any number of **joined servers**,
run by one of three orchestrators:

| Orchestrator | What pskit installs | Load balancing | Good for |
|---|---|---|---|
| **Docker Swarm** (`swarm`) | Docker on every server; `swarm init` on the master, `swarm join` on the others | The routing mesh: the functions' port answers on **every** server | The simplest setup; Docker you already know |
| **Kubernetes** (`kubernetes`) | [k3s](https://k3s.io) (lightweight Kubernetes, no Docker needed) | k3s ServiceLB: the functions' port answers on **every** server | Teams that use `kubectl`, or want to grow into Kubernetes |
| **HashiCorp Nomad** (`nomad`) | Docker plus the Nomad agent ([releases.hashicorp.com](https://releases.hashicorp.com/nomad/), 1.11.3 by default) as a systemd unit | One web replica per server on the port; put your load balancer (nginx, HAProxy, Traefik) in front of all servers | Teams that use Nomad / Consul |

pskit does everything over SSH from your machine: it installs the orchestrator, joins servers, copies the function
image to every server (**no container registry needed**), deploys, and watches capacity.

Every deploy and every resource picks where it runs:

| | One server (as before) | A scaling cluster |
|---|---|---|
| **Functions** | `pskit deploy --server edge1` / `options.deploy.server` | `pskit deploy --cluster prod` / `options.cluster: prod` |
| **Resources** (Redis, Postgres, MinIO, SMTP, ...) | `pskit resources onprem create cache --service redis --server edge1` | `... --cluster prod` (the server with the most free memory) or `--cluster prod --node edge2` |

In the studio: *Deploy → Run on: One machine / Scaling cluster*, and *Create a service → Where: This machine / One
SSH server / Scaling cluster*.

## How the functions run in a cluster

Each deployment (`pskit-<service>-<stage>`) runs the function host image as two workloads:

```
                      users ──► :8080 on any server (Swarm mesh / k3s ServiceLB / your LB for Nomad)
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
         web replica          web replica          web replica        ← `replicas` copies, spread over servers
     (HTTP routes, invoke)  (HTTP routes, invoke)  (HTTP routes, invoke)
              │ queues, topics, buckets, workflows, logs, /__pskit admin
              └────────────────────┼────────────────────┘
                                   ▼
                          main replica (1, on the master)
        schedules · built-in queues / topics / buckets · workflow executions · logs · stats · data volume
```

* **web** replicas serve your HTTP routes and `invoke()` calls. Adding replicas or servers adds HTTP capacity.
* The **main** replica runs on the master with the data volume. It owns everything stateful: schedules run once,
  not once per replica; built-in queues, topics and buckets live in one place; workflow executions are recorded
  there; queue, storage and topic triggers run there.
* A web replica hands those to main. `submit_queue()`, `put_object()`, workflow starts and `/__pskit/*` admin
  calls are forwarded to it. Its log lines and invocation counts are sent to main every second, tagged with
  `replica`, so `pskit logs`, the studio's **Logs** view and `/__pskit/stats` show the whole cluster.
* Resources with a driver (Redis, SQS, S3, MinIO, ...) are used directly by every replica, as on one server.

Your handler code does not change.

## Set up

1. **Save the servers** (once): *Resources → On-prem services → Servers → + Add server*, or

   ```bash
   pskit resources onprem servers add edge1 --ssh root@10.0.0.5 --pem ~/.ssh/edge.pem
   pskit resources onprem servers add edge2 --ssh root@10.0.0.6 --pem ~/.ssh/edge.pem
   ```

   The SSH user needs root or passwordless `sudo`.

2. **Create the cluster** on the master: studio *Scaling cluster → + New cluster*, or

   ```bash
   pskit cluster create prod --orchestrator swarm --master edge1      # or kubernetes / nomad
   ```

   Servers in one private network should use their private IP: `--advertise 10.0.0.5` (and `--advertise` on join).

3. **Join servers**, now or any time later (also while functions are running):

   ```bash
   pskit cluster join prod edge2
   ```

   pskit installs the agent, joins it, and copies the images already deployed, so replicas can start there at once.
   Joining again is safe. A server left half-joined by a failed join, removed while it was off, or still in an
   old swarm leaves that membership first. pskit refuses only a server that manages another swarm with nodes or
   services, or whose swarm is locked.

4. **Point the functions at the cluster** in `pskit.yml` (studio: the *Deploy this project's functions to …*
   switch on the cluster page):

   ```yaml
   provider:
     name: onprem
     options:
       cluster: prod                     # or the mapping below
       # cluster:
       #   name: prod
       #   replicas: 3                   # web replicas (default 2), plus 1 main replica
       #   cpu: 0.25                     # CPU cores each replica reserves (default 0.25)
       #   memory: 256                   # MB each replica reserves (default 256)
       port: 8080
       admin_token: "${env:PSKIT_ADMIN_TOKEN}"
   ```

5. **Deploy**: `pskit deploy` (or studio *Deploy*). pskit builds the image on this machine (Docker must run here),
   copies it to every server that does not have it yet, applies the stack / manifest / job on the master, and
   waits until `/__pskit/health` answers. `pskit remove` deletes the workloads. The main replica's data stays on
   the master: a Docker volume for Swarm, `/var/lib/pskit/<stack>` for Kubernetes and Nomad.

   The image is sent gzip-compressed in 16 MB parts, each with a checksum. A dropped connection (`scp: Couldn't
   send packet: Broken pipe`) is retried up to 6 times with growing pauses. Each retry sends only the parts that
   are missing, and parts already copied stay on the server, so running `pskit deploy` again resumes the copy. The
   deploy stops only if the master cannot get the image. A worker that fails gets a warning, and replicas placed on
   it start once a later deploy finishes the copy.

### One server or the cluster, per deploy

Where a deploy goes, first match wins:

1. `pskit deploy --cluster NAME` (studio: *Run on → Scaling cluster*): that cluster. The replicas / cpu / memory of
   `options.cluster` are kept when it names another cluster.
2. `pskit deploy --server NAME` or `--server local` (studio: *Run on → One machine*): that one machine, even when
   pskit.yml has `options.cluster`.
3. `options.cluster` in pskit.yml, then `options.deploy` (`server`, `host`, ...).
4. Where the stage was last deployed: a cluster or a saved server. The stage remembers it, like a server.

So `pskit deploy --cluster prod` once moves a stage to the cluster, and `pskit deploy --server edge1` once moves it
back to one server. The old place keeps running until you `pskit remove` it there. On a cluster the master is the
SSH target that `pskit logs`, `pskit trigger`, the dashboard and the studio use when the port is closed to your
machine; `pskit info` shows `deployed on: cluster prod (master edge1 (10.0.0.5))`.

## Per-server settings: home path, bind address, data directory

A saved server can carry three optional defaults (*Add a server* in the studio, or
`pskit resources onprem servers add edge1 --ssh root@10.0.0.5 --home /srv/pskit --bind 10.0.0.5 --data-dir /data/pskit`):

| Setting | Functions on this server | Resources on this server | In a cluster |
|---|---|---|---|
| **Home path** | app in `<home>/<service>-<stage>` (instead of `/opt/pskit/...`) | – | scripts and image copies in `<home>/pskit-cluster` |
| **Bind address** | the host listens on it (`options.host`) | port published on it | a specific IP is the server's cluster address |
| **Data directory** | `options.data_dir` (built-in queues, buckets, ...) | data in `<data>/<container>` (bind mount, not a Docker volume) | the master keeps the main replica's data in `<data>/<stack>` |

pskit.yml wins: `deploy.path`, `data_dir` and a `host` other than the default `127.0.0.1`. Settings apply to what you
create or deploy afterwards; existing services keep their volumes.

## Resources on a cluster

A resource can run on one server of a cluster:

```bash
pskit resources onprem create cache --service redis --cluster prod                # auto placement
pskit resources onprem create orders-db --service postgres --cluster prod --node edge2
```

* **Auto placement** measures every server of the cluster (as `pskit cluster status` does) and picks the reachable
  one with the most free memory that is under every capacity limit. When every server is over a limit the create
  fails with "join a server", the same signal as the capacity alert.
* The address written to the env file is that server's **cluster address** (its `--advertise` address), which the
  function replicas on every server reach.
* It is a plain Docker container with a data volume on that server, as on one server: status, logs, backups, the
  web UI and the data browser work the same. It is **one instance, not replicated**: the orchestrator does not
  move it. Use a managed or replicated database when that matters.
* With `options.cluster` in pskit.yml, *Create a service* and `pskit resources onprem create` default to the
  cluster.
* `pskit cluster status` and the studio's cluster page list the resources and the server each runs on. When that
  server leaves the cluster (or the cluster is forgotten) the resource keeps running there as a one-server resource.

### Ports to open

| Between the servers | |
|---|---|
| Swarm | 2377/tcp, 7946/tcp+udp, 4789/udp |
| Kubernetes (k3s) | 6443/tcp (to the master), 8472/udp, 10250/tcp |
| Nomad | 4646-4648/tcp, 4648/udp |
| **From users** | the functions' `port` (default 8080) on every server (Swarm / k3s) or on your load balancer (Nomad) |

## Live requests: which server handled what

The cluster page's **Live requests** card lists requests as they happen: time, the **server** and **replica** that
handled each one, method and path, status, duration and function, plus a bar per server (click one to see only its
requests). It polls the main replica every 2 seconds, and only while the card is on screen, the browser tab is
visible and *Live* is on. Off screen it does nothing.

```bash
pskit cluster requests prod --follow
#   12:04:53  edge1        main                   200     45.4 ms  send: POST /send
#   12:04:53  edge2        node-b-2               500     10.9 ms  hello: GET /hello
```

Each replica knows the server it runs on (`PSKIT_NODE`, set by the orchestrator) and records it with each request;
web replicas ship their entries to the main replica every second. `GET /__pskit/requests?since=N` on the
deployment's port returns them (admin token required).

## Monitor, scale and add servers when it is full

The studio's **Scaling cluster** page shows every server's CPU (over one second), memory and disk with the
limits, the orchestrator's view (ready, drained, offline), each deployment's running / desired replicas, and
replicas that cannot be placed. It refreshes every 30 seconds. From a shell:

```bash
pskit cluster status prod
#   server         role    state       cpu   mem  disk  load
#   edge1          master  ready       91%   62%   40%  3.1 2.8 2.5  FULL (cpu)
#   edge2          worker  ready       88%   71%   35%  2.9 2.7 2.6  FULL (cpu)
#   limits: cpu 85%  memory 85%  disk 90%
#   workload pskit-shop-prod_web: 4/6 running
#   pending  pskit-shop-prod_web.5: no suitable node (insufficient resources on 2 nodes)
# FULL: all 2 server(s) are over a capacity limit; 2 replica(s) cannot be placed. Add a server: pskit cluster join prod SERVER
```

The cluster counts as **full** when any of these holds:

* every reachable, schedulable server is over a limit (default CPU 85 %, memory 85 %, disk 90 %);
* replicas cannot be placed: the orchestrator found no server with the CPU / memory a replica reserves;
* no server is ready.

**Add capacity on the go.** Join another server: *+ Join server* in the studio (it can save a new server in the
same dialog), or `pskit cluster join prod edge3`. Then raise the replicas: the *Scale* button for a quick change,
or `options.cluster.replicas` so the next deploy keeps it. To retire a server, `pskit cluster leave prod edge2`
drains it first, so its replicas move to the others.

### Alert DevOps (optional)

When the cluster is full, pskit can call **one of your functions** and / or POST to a **webhook** (Slack, Teams,
Google Chat or any URL), at most once per cooldown (default 30 minutes):

```bash
pskit cluster alerts prod --function notify-devops --webhook https://hooks.slack.com/services/... --cooldown 30
pskit cluster alerts prod --cpu 80 --memory 90 --disk 90       # when a server counts as full
pskit cluster alerts prod --test                               # send a test alert now
```

The function gets `event.data`:

```json
{
  "type": "pskit.cluster.capacity", "test": false, "cluster": "prod", "orchestrator": "swarm", "full": true,
  "subject": "[pskit] cluster prod needs another server",
  "message": "pskit cluster prod is full: all 2 server(s) are over a capacity limit. Add a server: pskit cluster join prod SERVER",
  "reasons": ["all 2 server(s) are over a capacity limit"],
  "nodes": [{"server": "edge1", "role": "master", "cpu": 91.0, "memory": 62.0, "disk": 40.0, "over": ["cpu"], "...": "..."}],
  "workloads": [{"name": "pskit-shop-prod_web", "running": 4, "desired": 6}],
  "pending": ["..."], "capacity": {"cpu": 85, "memory": 85, "disk": 90}, "checked_at": "2026-10-24T09:00:00Z"
}
```

A mail to DevOps from that function (any SMTP relay you have):

```python
import os
import smtplib
from email.message import EmailMessage


def notify_devops(event, ctx):
    alert = event.data
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["ALERT_FROM"], os.environ["DEVOPS_EMAIL"], alert["subject"]
    lines = [alert["message"], ""] + [
        f"{n['server']:<12} cpu {n['cpu']}%  memory {n['memory']}%  disk {n['disk']}%  {'FULL' if n['over'] else ''}"
        for n in alert["nodes"]
    ]
    msg.set_content("\n".join(lines))
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)
    return {"sent": True}
```

The function runs on the deployed host of the stage set with `--stage` (or any deployed on-prem stage of this
project): it must be deployed. The webhook body has `text` (what Slack / Teams show) plus the same fields.

**Who checks?** Checks run where pskit runs:

* the studio, while it is open, with *Watch while the studio runs* (every 60 s by default);
* `pskit cluster watch prod --interval 60` around the clock on an always-on machine (a systemd unit, `tmux`, ...),
  or `pskit cluster check prod` from cron. `check` exits with status 3 when the cluster is full.

Recent alerts are listed on the cluster page (and kept in `.pskit/onprem-clusters.json`).

## Where things are kept

| What | Where |
|---|---|
| Clusters, joined servers, limits, alert settings, recent alerts, deployments | `.pskit/onprem-clusters.json` (out of git) |
| SSH logins | the saved servers (`.pskit/onprem-servers.json`; passwords in `.pskit/ssh/`) |
| Join tokens | never stored: read from the master at each join; the join script deletes itself |
| Scripts and manifests pskit runs | `~/pskit-cluster/` on the servers |

## Commands

| Command | |
|---|---|
| `pskit cluster create NAME -o swarm\|kubernetes\|nomad --master SERVER [--advertise IP]` | install on the master and save |
| `pskit cluster join NAME SERVER [--advertise IP]` | add a server (installs, joins, copies images) |
| `pskit cluster leave NAME SERVER` | drain and remove a server |
| `pskit cluster status NAME [--json]` | servers, replicas, full or not |
| `pskit cluster check NAME [--no-alert]` | status once and the alert when full (exit 3 when full) |
| `pskit cluster watch NAME [--interval 60]` | check until Ctrl+C |
| `pskit cluster alerts NAME [--function F] [--stage S] [--webhook URL] [--cooldown M] [--cpu/--memory/--disk %] [--test]` | alert settings |
| `pskit cluster scale NAME --replicas N [--stack S]` | web replicas now (until the next deploy) |
| `pskit cluster requests NAME [--follow] [--stack S]` | recent requests and the server / replica of each |
| `pskit cluster list` / `pskit cluster remove NAME` | list / forget a cluster |
| `pskit deploy --cluster NAME` / `pskit deploy --server NAME` | deploy the functions to the cluster / to one server |
| `pskit resources onprem create KEY --service S --cluster NAME [--node SERVER]` | a resource on a server of the cluster |

## Limits and notes

* Docker must run on **your** machine to build the image; servers need Linux (x86_64 or arm64) with SSH, and
  root or passwordless `sudo`.
* The main replica is one process on the master: schedules, built-in queues and workflow executions do not
  scale out. Use a queue driver (Redis, SQS, ...) when queue consumers must scale with the replicas.
* Nomad has no built-in load balancer: each server runs at most one web replica on the port, and the master's
  port is the main replica. Put nginx / HAProxy / Traefik in front of the servers.
* The orchestrators' own security is yours to tighten: limit the cluster ports to your network, and enable Nomad
  ACLs or Kubernetes RBAC as your policies need.
