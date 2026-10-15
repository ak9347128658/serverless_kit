# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

> The function runtime is bundled into every deployment. After upgrading `py-serverless-kit`, run `pskit deploy` again to pick up runtime fixes.

## [Unreleased]

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
