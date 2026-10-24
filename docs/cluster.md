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
image to every server (**no container registry needed**), deploys, and watches capacity. **Resources** (Redis,
Postgres, MinIO, the SMTP server, ... from *Resources → On-prem services*) still run on one server each, exactly
as before.

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

With `options.cluster` set, `options.deploy` and `--server` are ignored for this project. The master becomes the
SSH target that `pskit logs`, `pskit trigger`, the dashboard and the studio use when the port is closed to your
machine.

### Ports to open

| Between the servers | |
|---|---|
| Swarm | 2377/tcp, 7946/tcp+udp, 4789/udp |
| Kubernetes (k3s) | 6443/tcp (to the master), 8472/udp, 10250/tcp |
| Nomad | 4646-4648/tcp, 4648/udp |
| **From users** | the functions' `port` (default 8080) on every server (Swarm / k3s) or on your load balancer (Nomad) |

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
| `pskit cluster list` / `pskit cluster remove NAME` | list / forget a cluster |

## Limits and notes

* Docker must run on **your** machine to build the image; servers need Linux (x86_64 or arm64) with SSH, and
  root or passwordless `sudo`.
* The main replica is one process on the master: schedules, built-in queues and workflow executions do not
  scale out. Use a queue driver (Redis, SQS, ...) when queue consumers must scale with the replicas.
* Nomad has no built-in load balancer: each server runs at most one web replica on the port, and the master's
  port is the main replica. Put nginx / HAProxy / Traefik in front of the servers.
* The orchestrators' own security is yours to tighten: limit the cluster ports to your network, and enable Nomad
  ACLs or Kubernetes RBAC as your policies need.
