# Dashboard

`pskit deploy` (and `pskit remove`) regenerate a single self-contained HTML page listing everything this project has deployed, across all providers and stages:

```
.pskit/dashboard/index.html
```

```bash
pskit dashboard               # regenerate
pskit dashboard --open        # regenerate and open in the browser
pskit dashboard --serve       # serve on http://127.0.0.1:8765/index.html
pskit dashboard -o site/index.html
```

## What it shows

- **Summary cards:** deployments, functions, HTTP functions, workflows, providers.
- **One section per deployment** (`service · stage`): provider and tier, status (`deployed` / `removed` / `local`), region, last update, artifact hash, API endpoint, pskit version.
- **Functions table:**
  - name and the cloud resource name;
  - type (`http`, `scheduled`, `event-driven`, `http+event`, `invoke-only`; generated orchestrators are flagged);
  - triggers (e.g. `GET /items/{id}`, `cron(0 2 * * *)`, `queue jobs (batch 10)`);
  - runtime, architecture, memory, timeout;
  - clickable endpoint URLs;
  - resource id/ARN with a copy button, and a console link;
  - expandable details: handler, description, environment variable **names** (never values).
- **Workflows table:** engine (native / embedded), state flow, task functions, state machine id and console link.
- A filter box plus provider and stage selectors, and light/dark themes.

It has no external scripts, fonts or network calls, so it works from `file://`, any static host, or a CI artifact.

## Where the data comes from

Each deploy writes `.pskit/<provider>-<stage>/deployment.json`, built from the config plus the `pskit` Terraform output. The dashboard reads all of these files: deploy `aws` and `gcp`, `dev` and `prod`, and they all appear on one page. `pskit info --json` prints the same record.

If nothing is deployed yet, `pskit dashboard` renders the configured functions as a `local` record. `pskit dev` serves that view at `/__pskit/`.

Because `.pskit/` is git-ignored and local, CI runs and other machines each have their own records. To share a dashboard:

- upload `.pskit/dashboard/index.html` as a CI artifact. [`examples/15-ci-cd-deploy`](../examples/15-ci-cd-deploy) does this;
- or publish it to a private static site or bucket.

The page contains resource names, URLs and ids, so treat it as internal.
