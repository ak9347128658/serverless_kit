# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

> The function runtime is bundled into every deployment. After upgrading `py-serverless-kit`, run `pskit deploy` again to pick up runtime fixes.

## [Unreleased]

## [0.1.2] - 2026-10-15

### Fixed
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
