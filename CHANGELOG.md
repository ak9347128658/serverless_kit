# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

> The function runtime is bundled into every deployment. After upgrading `py-serverless-kit`, run `pskit deploy` again to pick up runtime fixes.

## [Unreleased]

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
