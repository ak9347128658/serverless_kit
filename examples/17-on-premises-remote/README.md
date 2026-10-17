# 17 · On-premises, remote server

Example 16 runs the host on the laptop that runs `pskit deploy`. This example
copies the same kind of host onto a machine you SSH into — a factory box, a
VM, or a rack server. The handler code does not change.

Three ways to get code from this laptop onto that server:

| `options.deploy.method` | What the server needs | What the laptop needs | When to use it |
| --- | --- | --- | --- |
| `local` | this machine (example 16) | Python + pskit | laptop / the server *is* the deploy machine |
| `ssh` | SSH. Python only if `python: system` | OpenSSH (`ssh`, `scp`) | most on-prem Linux boxes |
| `docker` | Docker engine | Docker CLI (and SSH if `docker_host` is `ssh://…`) | the server has Docker and you do not want to manage Python |

When the server has **no Python**, pick one of:

- `method: ssh` and `python: bundle` — pskit downloads a portable CPython
  ([python-build-standalone](https://github.com/astral-sh/python-build-standalone))
  at apply time, copies it with the app, and starts systemd (this file)
- `method: docker` — the image is `FROM python:3.12-slim`; the server never
  installs Python
- `python_home: /path/to/portable-python` — air-gapped: you already have a
  CPython tree, pskit copies it instead of downloading

## This file (SSH + bundled Python + systemd)

```bash
export PSKIT_ONPREM_HOST=10.0.1.12          # SSH target
export PSKIT_ONPREM_USER=pskit
export PSKIT_SSH_KEY=$HOME/.ssh/id_ed25519  # optional
export PSKIT_ONPREM_URL=http://10.0.1.12:8090
pskit deploy
curl http://10.0.1.12:8090/hello?name=Ada
pskit remove                                 # systemctl stop; data dir stays
```

The SSH user needs `sudo` to write `/etc/systemd/system` (`deploy.sudo: true`).
Wheels are vendored as manylinux x86_64 because `target_os: linux`.

Shorthand for the same method:

```yaml
deploy: ssh://pskit@10.0.1.12/opt/pskit/factory-api
```

## Docker instead

The server needs a Docker engine. It does not need Python.

```yaml
provider:
  options:
    host: 0.0.0.0
    port: 8090
    deploy:
      method: docker
      image: pskit/factory-api:dev
      docker_host: ssh://pskit@10.0.1.12   # empty = Docker on this laptop
      restart: unless-stopped
```

`pskit deploy` runs `docker build` and `docker run` (with `DOCKER_HOST` when
`docker_host` is set). Data lives in a volume named after the container.

## Server already has Python

```yaml
deploy:
  method: ssh
  host: 10.0.1.12
  user: pskit
  path: /opt/pskit/factory-api
  python: system          # uses python3.12 on the server
  install: process        # start a detached process instead of systemd
```

## Air-gapped bundle

Download the install_only archive once on a machine with network, copy it in,
and point at the unpacked tree:

```yaml
deploy:
  method: ssh
  host: 10.0.1.12
  python: bundle
  python_home: ./vendor/python     # portable CPython directory, copied as-is
```

Or set `python_url` + `python_sha256` to an internal mirror of the
python-build-standalone `install_only` tarball.

## Bind address

`options.host` is the address the process binds on the **target**. Use
`0.0.0.0` so other machines can call it. `advertise` is the URL written into
`pskit info` and the dashboard.
