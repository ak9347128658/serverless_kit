"""``pskit`` command line interface."""

from __future__ import annotations

import functools
import json
import os
import sys
import traceback
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

import click

from .._version import __version__
from ..config.loader import ConfigError
from ..config.schema import NODE_RUNTIMES, PROVIDERS, PYTHON_RUNTIMES

COMMON_ERRORS: tuple = ()


def _errors() -> tuple:
    global COMMON_ERRORS
    if not COMMON_ERRORS:
        from ..core.deployer import DeployError
        from ..packaging.builder import BuildError
        from ..providers.base import ProviderError
        from ..providers.schedule import ScheduleError
        from ..terraform.runner import TerraformError

        COMMON_ERRORS = (ConfigError, DeployError, BuildError, ProviderError, ScheduleError, TerraformError)
    return COMMON_ERRORS


def echo(msg: str = "") -> None:
    click.echo(msg)


def info(msg: str) -> None:
    click.secho(msg, fg="cyan")


def ok(msg: str) -> None:
    click.secho(msg, fg="green")


def fail(msg: str) -> None:
    click.secho(msg, fg="red", err=True)


def project_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Options shared by every command that loads the project."""

    @click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
    @click.option(
        "-p", "--provider", default=None, help=f"Override provider.name ({', '.join(PROVIDERS)}, or a plug-in)."
    )
    @click.option("-s", "--stage", default=None, help="Stage, e.g. dev / staging / prod.")
    @click.option("-r", "--region", default=None, help="Override provider.region.")
    @click.option("--server", "server", default=None,
                  help="On premises: use this saved SSH server (pskit resources onprem servers list); 'local' "
                       "for this machine. Default: the server the stage was last deployed to.")
    @click.option("-v", "--verbose", is_flag=True, help="Show tracebacks on errors.")
    @functools.wraps(fn)
    def wrapper(
        *args: Any,
        config_path: Optional[str],
        provider: Optional[str],
        stage: Optional[str],
        region: Optional[str],
        server: Optional[str],
        verbose: bool,
        **kwargs: Any,
    ) -> Any:
        from ..core.project import Project

        if server:
            from ..core.onprem_servers import SERVER_ENV

            os.environ[SERVER_ENV] = server
        try:
            project = Project.load(config_path, provider=provider, stage=stage, region=region)
            return fn(project, *args, **kwargs)
        except click.exceptions.Exit:
            raise
        except click.ClickException:
            raise
        except _errors() as exc:
            if verbose:
                traceback.print_exc()
            fail(f"Error: {exc}")
            sys.exit(1)
        except KeyError as exc:
            if verbose:
                traceback.print_exc()
            fail(f"Error: {exc.args[0] if exc.args else exc}")
            sys.exit(1)

    return wrapper


CONTEXT = {"help_option_names": ["-h", "--help"], "max_content_width": 120}

ROOT_EPILOG = """\b
Getting started
  init        Scaffold a project              pskit init my-api -p aws -t full
  studio      Build it in the browser         pskit studio --open
  doctor      Check tools and credentials     pskit doctor -p azure
\b
Develop
  dev         Local server, hot reload        pskit dev --port 3000
  invoke      Call one function locally       pskit invoke -f hello -d '{"name": "Ada"}'
  validate    Check config for a provider     pskit validate -p gcp -s prod
\b
Deploy
  package     Build zip + Terraform only      pskit package -s dev
  plan        terraform plan                  pskit plan --destroy
  deploy      Package and apply               pskit deploy -s prod --confirm
  info        What is deployed (URLs, ids)    pskit info --refresh
  remove      Destroy the stage               pskit remove -s dev --yes
  tf          Any terraform command           pskit tf -- state list
\b
Operate
  logs        Tail deployed logs              pskit logs -f --function worker
  trigger     Send a real test event          pskit trigger -f worker -e 0 -d '{"id": 1}'
  dashboard   Deployment dashboard            pskit dashboard --serve --open
  client      info | install-node | tunnel
              pskit client info
              pskit client install-node
              pskit client tunnel
  resources   list | send | check | usage | data | clear | delete | onprem
              pskit resources list -s dev
              pskit resources delete aws_s3_bucket.b_uploads -s dev
              pskit resources send jobs -d '{"order_id": 1}'
              pskit resources check orders-db
              pskit resources usage jobs
              pskit resources data jobs --offset 0 -n 20
              pskit resources clear jobs --yes
              pskit resources onprem create jobs --service redis --attach worker --save
  workflow    run | show | start | executions | status | cancel
              pskit workflow run order -i '{"id": 1}' --history
              pskit workflow start order -i @input.json --wait
              pskit workflow executions order --status FAILED
              pskit workflow status <execution-id> --watch
              pskit workflow cancel <execution-id>
              pskit workflow show order --format asl
\b
Reference
  providers   Capability matrix of every cloud   pskit providers --json
\b


Project options (most commands): -c/--config FILE  -p/--provider NAME  -s/--stage NAME  -r/--region NAME  -v/--verbose
Run `pskit COMMAND --help` for every option of a command. Docs: docs/cli.md"""


@click.group(context_settings=CONTEXT, epilog=ROOT_EPILOG)
@click.version_option(__version__, prog_name="py-serverless-kit")
def cli() -> None:
    """py-serverless-kit: write Python functions once, deploy them to any cloud.

    \b
    Typical flow:
      pskit doctor                   # check terraform, CLIs and credentials
      pskit init my-api --provider aws
      pskit dev                      # run locally
      pskit deploy --stage dev       # terraform apply
      pskit dashboard --open         # see everything you deployed
    """


# ----------------------------------------------------------------------------- init
@cli.command()
@click.argument("directory", default=".", type=click.Path(file_okay=False))
@click.option("--name", default=None, help="Service name (default: directory name).")
@click.option("-p", "--provider", default="aws", type=click.Choice(PROVIDERS), show_default=True)
@click.option(
    "-t", "--template", default="http", type=click.Choice(["http", "events", "workflow", "full"]), show_default=True
)
@click.option(
    "-l",
    "--language",
    default=None,
    type=click.Choice(["python", "javascript", "typescript"]),
    help="Handler language (default: python, or javascript with a nodejs --runtime).",
)
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(list(PYTHON_RUNTIMES + NODE_RUNTIMES)),
    help="Runtime written to pskit.yml (default python3.12 / nodejs20).",
)
@click.option("--typescript", "--ts", "ts", is_flag=True, help="Shorthand for --language typescript.")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def init(
    directory: str,
    name: Optional[str],
    provider: str,
    template: str,
    language: Optional[str],
    runtime: Optional[str],
    ts: bool,
    force: bool,
) -> None:
    """Scaffold a new project (pskit.yml, handlers, tests).

    
    Examples:
      pskit init my-api --provider aws
      pskit init . -p gcp -t workflow
      pskit init svc -p onprem -t full --name orders
      pskit init api -p aws --typescript          # Node.js 20 + TypeScript
      pskit init api -p gcp --runtime nodejs22    # JavaScript
    """
    import re

    from .templates import render

    target = Path(directory).resolve()
    service = name or re.sub(r"[^a-z0-9-]", "-", target.name.lower()).strip("-") or "my-service"
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", service):
        raise click.BadParameter(f"invalid service name {service!r}; use --name", param_hint="--name")
    if ts:
        language = "typescript"
    if language is None:
        language = "javascript" if runtime and runtime.startswith("nodejs") else "python"
    node = language in ("javascript", "typescript")
    if runtime and runtime.startswith("nodejs") != node:
        raise click.BadParameter(f"runtime {runtime} does not match language {language}", param_hint="--runtime")
    runtime = runtime or ("nodejs20" if node else "python3.12")
    files = render(template, service, provider, runtime=runtime, typescript=language == "typescript")
    target.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = target / rel
        if path.exists() and not force:
            echo(f"  skip   {rel} (exists)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if node:
            path.write_bytes(content.encode("utf-8"))  # LF on every OS
        else:
            path.write_text(content, "utf-8")
        echo(f"  create {rel}")
    if node:
        from ..packaging.node import write_types

        if write_types(target) is not None:
            echo("  create .pskit/types/pskit/index.d.ts (types for `import ... from \"pskit\"`)")
    ok(f"\nCreated {language} service '{service}' for {provider} in {target}")
    echo("Next steps:")
    if directory != ".":
        echo(f"  cd {directory}")
    if node:
        echo("  npm install          # when you add dependencies to package.json")
    echo("  pskit dev            # local server with hot reload")
    echo("  pskit deploy         # deploy with terraform")


# ----------------------------------------------------------------------------- validate
@cli.command()
@project_options
def validate(project) -> None:  # noqa: ANN001
    """Validate the config against the selected provider.

    
    Examples:
      pskit validate
      pskit validate -p azure -s prod
    """
    issues = project.validate()
    cfg = project.config
    info(
        f"{cfg.service} -> {project.provider.display_name} [{project.provider.tier}] stage={cfg.provider.stage} region={project.region}"
    )
    echo(f"  {len(cfg.functions)} function(s), {len(cfg.workflows)} workflow(s)")
    for name, engine in project.engines.items():
        echo(f"  workflow {name}: {engine} engine")
    for issue in issues:
        (fail if issue.level == "error" else echo)(f"  {issue}")
    if any(i.level == "error" for i in issues):
        sys.exit(1)
    ok("Configuration is valid.")


# ----------------------------------------------------------------------------- package / plan / deploy / remove
@cli.command()
@project_options
@click.option("--skip-deps", is_flag=True, help="Do not pip-install requirements into the bundle.")
def package(project, skip_deps: bool) -> None:  # noqa: ANN001
    """Build the artifact and Terraform (no cloud calls).

    Writes .pskit/<provider>-<stage>/artifact.zip and main.tf.json.

    
    Examples:
      pskit package
      pskit package -p gcp -s prod --skip-deps
    """
    from ..core.deployer import Deployer

    result = Deployer(project, echo=echo).package(skip_deps=skip_deps)
    ok(f"Packaged into {project.work_dir} ({result.resource_count} terraform resources)")


@cli.command()
@project_options
@click.option("--skip-deps", is_flag=True)
@click.option("--destroy", is_flag=True, help="Plan a destroy instead.")
def plan(project, skip_deps: bool, destroy: bool) -> None:  # noqa: ANN001
    """Show the terraform plan (what would change).

    
    Examples:
      pskit plan -s dev
      pskit plan --destroy
    """
    from ..core.deployer import Deployer

    changed = Deployer(project, echo=echo).plan(skip_deps=skip_deps, destroy=destroy)
    ok("Plan has changes." if changed else "No changes.")


@cli.command()
@project_options
@click.option("--skip-deps", is_flag=True, help="Do not pip-install requirements into the bundle.")
@click.option("--confirm", is_flag=True, help="Show the plan and apply it (instead of -auto-approve).")
@click.option("--upgrade", is_flag=True, help="terraform init -upgrade (update provider plugins).")
@click.option("--no-dashboard", is_flag=True, help="Do not regenerate the dashboard.")
def deploy(project, skip_deps: bool, confirm: bool, upgrade: bool, no_dashboard: bool) -> None:  # noqa: ANN001
    """Package and deploy with terraform apply.

    
    Examples:
      pskit deploy
      pskit deploy -s prod --confirm      # review the plan first
      pskit deploy -p onprem --skip-deps
    """
    from ..core.deployer import Deployer
    from ..dashboard.generator import write_dashboard

    _ask_onprem_pem(project)
    record = Deployer(project, echo=echo).deploy(skip_deps=skip_deps, auto_approve=not confirm, upgrade=upgrade)
    where = (record.get("deployed_on") or {}).get("label") or record["region"]
    ok(f"\nDeployed {record['service']} ({record['stage']}) to {record['provider_display']} {where}")
    _print_record(record)
    if not no_dashboard:
        path = write_dashboard(project.root)
        echo(f"\nDashboard: {path.as_uri()}")


@cli.command()
@project_options
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@click.option(
    "--keep-resources",
    is_flag=True,
    help="Keep managed buckets, queues and topics (and their data); destroy only functions and triggers.",
)
def remove(project, yes: bool, keep_resources: bool) -> None:  # noqa: ANN001
    """Destroy every resource of this service/stage.

    
    Examples:
      pskit remove -s dev
      pskit remove -s dev --yes
      pskit remove -s dev --keep-resources   # keep buckets, queues, topics
    """
    from ..core.deployer import Deployer
    from ..dashboard.generator import write_dashboard

    cfg = project.config
    if not yes:
        what = "functions and triggers (keeping buckets, queues and topics)" if keep_resources else "ALL resources"
        click.confirm(
            f"Destroy {what} of '{cfg.service}' stage '{cfg.provider.stage}' on {project.provider.display_name}?",
            abort=True,
        )
    kept = Deployer(project, echo=echo).remove(auto_approve=True, keep_resources=keep_resources)
    if kept:
        info(f"Kept {len(kept)} resource(s); they are no longer managed by pskit. Delete them in the console when done.")
    write_dashboard(project.root)
    ok("Removed.")


# ----------------------------------------------------------------------------- trigger (test events)
@cli.command(short_help="Send a real test event to a deployed function.")
@project_options
@click.option("-f", "--function", "function", default=None, help="Function to test (all with --list).")
@click.option("-e", "--event", "index", type=int, default=None, help="Trigger number (0 = first; see --list).")
@click.option("--direct", is_flag=True, help="Invoke the function directly instead of through a trigger.")
@click.option("-d", "--data", default=None, help="Payload JSON or @file.json (default: a sample for the trigger).")
@click.option("--path", "path", default=None, help="HTTP path incl. query string (http triggers), e.g. /items/1?x=y.")
@click.option("--list", "show", is_flag=True, help="Show how to test each trigger: payload, commands, console steps.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def trigger(  # noqa: ANN001
    project, function: Optional[str], index: Optional[int], direct: bool, data: Optional[str], path: Optional[str],
    show: bool, as_json: bool,
) -> None:
    """Send a real test event to a deployed function (queue message, upload, HTTP call...).

    \b
    Examples:
      pskit trigger --list                          # how to test every function
      pskit trigger -f worker -e 0 -d '{"order_id": 1}'      # e.g. an SQS message
      pskit trigger -f thumbnails -e 0 -d '{"key": "in/a.png", "content": "x"}'
      pskit trigger -f api -e 0 --path '/items/1'
      pskit trigger -f nightly --direct              # invoke without the trigger
    Test locally (nothing deployed) with `pskit invoke`.
    """
    from ..core.deployer import Deployer
    from ..core.testing import TriggerError, guide, send
    from ..local.runner import parse_data

    record = Deployer(project, echo=echo).load_record()
    info_ = guide(project, record)
    if show or function is None:
        rows = [f for f in info_["functions"] if function in (None, f["name"])]
        if as_json:
            click.echo(json.dumps(rows, indent=2, default=str))
            return
        if not info_["deployed"]:
            info(f"{info_['provider']}/{info_['stage']} is not deployed: only local tests (pskit invoke) apply.")
        for fn in rows:
            echo(click.style(f"\n{fn['name']}", bold=True) + (f"  {fn['description']}" if fn["description"] else ""))
            for t in fn["triggers"]:
                label = "direct invoke" if t["index"] < 0 else f"#{t['index']} {t['summary']}"
                echo(f"  {label}")
                echo(f"    payload: {json.dumps(t['sample'])}   ({t['payload_help']})")
                for cmd in t["local"]:
                    echo(f"    local:   {cmd}")
                remote = t["remote"]
                for cmd in remote["pskit"]:
                    echo(f"    deployed: {cmd}")
                if not remote["supported"] and info_["deployed"]:
                    echo(f"    deployed: {remote['reason']}")
                for cmd in remote["cli"]:
                    echo(f"    cloud CLI: {cmd}")
                for i, step in enumerate(remote["console"], 1):
                    echo(f"    console {i}. {step}")
        return
    if direct and index is not None:
        raise click.UsageError("use --event or --direct, not both")
    triggers = next((f["triggers"] for f in info_["functions"] if f["name"] == function), None)
    if triggers is None:
        raise click.UsageError(f"unknown function {function!r}")
    chosen = -1 if direct else (index if index is not None else triggers[0]["index"])
    sample = next((t["sample"] for t in triggers if t["index"] == chosen), None)
    payload = parse_data(data) if data is not None else sample
    try:
        result = send(project, record, function, chosen, payload, path=path)
    except TriggerError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    ok(f"Sent {result.get('kind')} event to {function}")
    click.echo(json.dumps(result, indent=2, default=str))
    info("Watch what the function did: pskit logs -f --function " + function)


# ----------------------------------------------------------------------------- resources
@cli.group()
def resources() -> None:
    """List or delete the buckets, queues and topics pskit created.

    \b
    Examples:
      pskit resources list -s dev
      pskit resources delete aws_s3_bucket.b_uploads -s dev
      pskit resources delete --all-kept -s dev     # after `pskit remove --keep-resources`
    """


def _resource_rows(project: Any) -> dict:
    from ..core.deployer import Deployer

    return Deployer(project, echo=echo).cloud_resources()


@resources.command("list", short_help="Deployed and kept buckets, queues and topics.")
@project_options
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def resources_list(project, as_json: bool) -> None:  # noqa: ANN001
    """Deployed and kept buckets, queues and topics, with their Terraform addresses."""
    found = _resource_rows(project)
    if as_json:
        click.echo(json.dumps(found, indent=2))
        return
    rows = found["deployed"] + found["kept"]
    if not rows:
        echo("No buckets, queues or topics created by pskit for this stage.")
        return
    echo(f"{'status':<10}{'kind':<8}{'name':<40}address")
    for r in rows:
        echo(f"{r['status']:<10}{r['kind']:<8}{r['name'][:39]:<40}{r['address']}")
    if found["kept"]:
        info("Kept resources were left by `pskit remove --keep-resources`; delete them with `pskit resources delete`.")


@resources.command("send", short_help="Send a test message / object to a queue, bucket or topic.")
@project_options
@click.argument("key", required=False)
@click.option("-d", "--data", default=None, help="Payload JSON or @file.json (default: a sample).")
@click.option("--list", "show", is_flag=True, help="How to test every resource: payload, commands, console steps.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def resources_send(project, key: Optional[str], data: Optional[str], show: bool, as_json: bool) -> None:  # noqa: ANN001
    """Test a resource: a message to a queue, an object to a bucket, a message to a topic.

    \b
    Examples:
      pskit resources send --list
      pskit resources send jobs -d '{"order_id": 1}'
      pskit resources send uploads -d '{"key": "in/a.csv", "content": "a,b"}'
      pskit resources send events -d '{"message": {"id": 1}, "attributes": {"type": "order"}}'
    Functions bound to the resource receive it through their triggers; watch them with `pskit logs -f`.
    """
    from ..core.deployer import Deployer
    from ..core.testing import TriggerError, resource_guide, send_resource
    from ..local.runner import parse_data

    record = Deployer(project, echo=echo).load_record()
    if show or key is None:
        info_ = resource_guide(project, record)
        rows = [r for r in info_["resources"] if key in (None, r["key"])]
        if as_json:
            click.echo(json.dumps(rows, indent=2, default=str))
            return
        if not rows:
            echo("No resources in pskit.yml.")
        for r in rows:
            label = f"{r['type']} {r['name']}" + (" (external)" if r["external"] else "")
            echo(click.style(f"\n{r['key']}", bold=True) + f"  {label}  -> {', '.join(r['functions']) or 'no functions'}")
            if r["sendable"]:
                echo(f"    payload: {json.dumps(r['sample'])}   ({r['payload_help']})")
                for cmd in r["remote"]["pskit"]:
                    echo(f"    pskit:     {cmd}")
                if not r["remote"]["supported"]:
                    echo(f"    pskit:     {r['remote']['reason']}")
                for cmd in r["remote"]["cli"]:
                    echo(f"    cloud CLI: {cmd}")
                for i, step in enumerate(r["remote"]["console"], 1):
                    echo(f"    console {i}. {step}")
            else:
                echo(f"    env vars:  {', '.join(r['env'])}")
                echo("    read it in a handler: resource(" + json.dumps(r["key"]) + ").get(\"<connection key>\")")
        return
    try:
        result = send_resource(project, record, key, parse_data(data) if data is not None else None)
    except TriggerError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    ok(f"Sent to {result['kind']} {result['name']}")
    click.echo(json.dumps(result, indent=2, default=str))
    if result.get("functions"):
        info("Bound functions: " + ", ".join(result["functions"]) + ". Watch them: pskit logs -f")


@resources.command("check", short_help="Check that a resource is reachable (connection, depth).")
@project_options
@click.argument("key")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def resources_check(project, key: str, as_json: bool) -> None:  # noqa: ANN001
    """Check resource KEY: from the on-prem host once deployed there, else from this machine.

    \b
    Queues report how many messages wait, buckets how many objects they hold; databases,
    caches and custom services get a connection check (Redis answers PING).
    """
    from ..core.deployer import Deployer
    from ..core.testing import TriggerError, check_resource

    try:
        result = check_resource(project, Deployer(project, echo=echo).load_record(), key)
    except TriggerError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json.dumps(result, indent=2))
    elif result.get("ok"):
        ok(f"{key}: {result['detail']} ({result['ms']} ms, from {result['checked_from']})")
    else:
        fail(f"{key}: {result['detail']} (from {result['checked_from']})")
    if not result.get("ok"):
        raise SystemExit(1)


@resources.command("usage", short_help="End-to-end guide: libraries, pskit.yml, code, tests for a resource.")
@project_options
@click.argument("key")
def resources_usage(project, key: str) -> None:  # noqa: ANN001
    """How to use resource KEY: what to install (Python / Node.js), the pskit.yml, handler code,
    local and deployed tests, provider notes and troubleshooting, as Markdown.

    \b
    Examples:
      pskit resources usage jobs
      pskit resources usage orders-db -p onprem > docs/orders-db.md
    """
    from ..core.deployer import Deployer
    from ..core.resource_usage import usage
    from ..core.testing import TriggerError

    try:
        out = usage(project, Deployer(project, echo=echo).load_record(), key)
    except TriggerError as exc:
        raise click.ClickException(str(exc)) from None
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    # Guides quote docs with arrows and dashes a legacy Windows console cannot print.
    click.echo(out["markdown"].encode(encoding, "replace").decode(encoding))


@resources.command("data", short_help="Browse what an on-prem queue, topic or bucket holds (paginated).")
@project_options
@click.argument("key")
@click.option("--offset", default=0, show_default=True, help="Skip this many items.")
@click.option("-n", "--limit", default=20, show_default=True, help="Items per page (max 500).")
@click.option("--prefix", default="", help="Buckets: only keys below this prefix.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def resources_data(project, key: str, offset: int, limit: int, prefix: str, as_json: bool) -> None:  # noqa: ANN001
    """Show a page of resource KEY: waiting messages (nothing is consumed) or objects.

    \b
    Examples:
      pskit resources data jobs
      pskit resources data jobs --offset 20 -n 20
      pskit resources data uploads --prefix in/
    """
    from ..core.deployer import Deployer
    from ..core.testing import TriggerError, browse_resource

    try:
        page = browse_resource(project, Deployer(project, echo=echo).load_record(), key, offset, limit, prefix)
    except TriggerError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json.dumps(page, indent=2))
        return
    total = page.get("total")
    if not page.get("supported"):
        info(f"{page.get('note')}" + (f" (depth {total})" if total is not None else ""))
        return
    shown = len(page["items"])
    info(f"{key}: items {offset + 1 if shown else 0}-{offset + shown} of {total if total is not None else '?'}")
    for item in page["items"]:
        if page["type"] == "storage":
            echo(f"  {item['size']:>10}  {item['key']}")
        else:
            extra = " (leased)" if item.get("leased") else ""
            echo(f"  {item['id'][:24]:<26}{item['body'][:100]}{extra}")
    if total is not None and offset + shown < total:
        echo(f"next page: pskit resources data {key} --offset {offset + shown} -n {limit}")


@resources.command("clear", short_help="Delete the data of a built-in on-prem queue, bucket or topic.")
@project_options
@click.argument("key")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
def resources_clear(project, key: str, yes: bool) -> None:  # noqa: ANN001
    """Delete every message / object of built-in resource KEY on the on-prem host (the resource stays)."""
    from ..core.deployer import Deployer
    from ..core.testing import TriggerError, purge_resource

    if not yes:
        click.confirm(f"Delete all data of {key} on the on-prem host?", abort=True)
    try:
        result = purge_resource(project, Deployer(project, echo=echo).load_record(), key)
    except TriggerError as exc:
        raise click.ClickException(str(exc)) from None
    ok(f"Deleted {result['deleted']} item(s) from {result['path']}")


@resources.command("delete", short_help="Delete buckets, queues or topics by address.")
@project_options
@click.argument("addresses", nargs=-1)
@click.option("--all-kept", is_flag=True, help="Delete every resource kept by `pskit remove --keep-resources`.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
def resources_delete(project, addresses: tuple, all_kept: bool, yes: bool) -> None:  # noqa: ANN001
    """Delete buckets, queues or topics (and their data) by address. See `pskit resources list`."""
    from ..core.deployer import Deployer

    targets = list(addresses)
    if all_kept:
        targets += [r["address"] for r in _resource_rows(project)["kept"] if r["address"] not in targets]
    if not targets:
        raise click.UsageError("pass one or more addresses (see `pskit resources list`) or --all-kept")
    if not yes:
        click.confirm(f"Permanently delete {len(targets)} resource(s) and their data: {', '.join(targets)}?", abort=True)
    deleted = Deployer(project, echo=echo).delete_resources(targets)
    ok(f"Deleted {len(deleted)} resource(s).")


# ----------------------------------------------------------------------------- resources onprem
@resources.group("onprem")
def resources_onprem() -> None:
    """Create services for on-premises functions with Docker, here or on an SSH server.

    \b
    Examples:
      pskit resources onprem catalog
      pskit resources onprem create jobs --service redis --attach worker --save
      pskit resources onprem create orders-db --service postgres --ssh ubuntu@10.0.0.5 --pem key.pem --save
      pskit resources onprem create py --service python --ssh ubuntu@10.0.0.5 --pem key.pem
      pskit resources onprem servers add edge1 --ssh ubuntu@10.0.0.5 --pem key.pem
      pskit resources onprem create node --service nodejs --version 22 --server edge1
      pskit resources onprem console cache "INFO server"
      pskit resources onprem terminal --server edge1
      pskit resources onprem status jobs
      pskit resources onprem backup orders-db
      pskit resources onprem remove jobs --delete-data --save
    Credentials go to the on-prem env file (default .env.onprem), never into pskit.yml.
    """


def _raw_project(config_path: Optional[str]) -> tuple:
    import yaml

    from ..config.loader import DEFAULT_FILES

    if config_path:
        path = Path(config_path).resolve()
    else:
        found = [Path.cwd() / name for name in DEFAULT_FILES if (Path.cwd() / name).is_file()]
        if not found:
            raise click.ClickException("no pskit.yml here; run inside a project or pass -c")
        path = found[0]
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(raw, dict):
        raise click.ClickException(f"{path} is not a mapping")
    return path.parent, path, raw


def _save_raw(path: Path, raw: dict) -> None:
    from ..studio.project import dump_yaml

    path.write_bytes(dump_yaml(raw).encode("utf-8"))
    ok(f"Updated {path.name} (comments are not kept)")


def _env_value(name: str) -> str:
    if name not in os.environ:
        raise click.ClickException(f"environment variable {name} is not set")
    return os.environ[name]


def _service_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    from ..core.onprem_services import ServiceError

    try:
        return fn(*args, **kwargs)
    except ServiceError as exc:
        raise click.ClickException(str(exc)) from None


@resources_onprem.command("catalog", short_help="Services and runtimes that can be created.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def resources_onprem_catalog(as_json: bool) -> None:
    from ..core.onprem_services import catalog

    items = catalog()
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return
    echo(f"{'id':<11}{'use as':<16}{'port':<7}notes")
    for item in items:
        roles = ", ".join(item["roles"]) or "runtime"
        echo(f"{item['id']:<11}{roles:<16}{str(item.get('port') or ''):<7}{item['notes']}")


@resources_onprem.command("create", short_help="Start a service (or install a runtime) and add it as a resource.")
@click.argument("key")
@click.option("--service", "service", required=True, help="See `pskit resources onprem catalog`.")
@click.option("--role", default=None, help="queue, cache, database, storage or custom (default: the first one).")
@click.option("--port", type=int, default=None, help="Port published on the server (default: the service's).")
@click.option("--image", default=None, help="Docker image (default: the catalog's).")
@click.option("--address", default=None, help="Address functions use to reach it (default: SSH host / 127.0.0.1).")
@click.option("--bind", default=None, help="Interface the port listens on (default: 0.0.0.0 over SSH, else 127.0.0.1).")
@click.option("--ssh", "ssh", default=None, help="user@host[:port] of the server. Default: options.deploy, else here.")
@click.option("--pem", default=None, help="Private key for --ssh (empty: the SSH agent or your default key).")
@click.option("--ssh-password-file", default=None, help="SSH with a password read from this file (uses sshpass).")
@click.option("--ssh-password-env", default=None,
              help="SSH with a password from this environment variable (uses sshpass; never stored).")
@click.option("--sudo", is_flag=True, help="Run docker / installs with sudo -n on the server.")
@click.option("--local", "local", is_flag=True, help="Create it on this machine even if the functions deploy over SSH.")
@click.option("--server", "server", default=None, help="A saved SSH server (pskit resources onprem servers list).")
@click.option("--version", "version", default=None, help="Software version to install (nodejs 22 / 20 / 18, java 21, go ...).")
@click.option("--user", "login", default=None, help="User to create (default: pskit; Redis / Valkey: default).")
@click.option("--password", default=None, help="Password to set (default: a generated one). Prefer --password-env.")
@click.option("--password-env", default=None, help="Read the password from this environment variable.")
@click.option("-a", "--attach", multiple=True, help="Add a trigger on this function (queue / storage / smtp). Repeatable.")
@click.option("--domains", default=None, help="smtp: the domains it receives mail for, comma separated.")
@click.option("--functions-url", default=None, help="smtp: the pskit host's URL (default: found from the deploy).")
@click.option("--max-size-mb", type=float, default=None, help="smtp: largest mail accepted (default 25).")
@click.option("--gui", is_flag=True, help="Also start its web UI (Adminer, Redis Commander, Kafka UI, Mongo Express).")
@click.option("--gui-login", type=click.Choice(["password", "none"]), default=None,
              help="Web UI sign-in: password (default) or none (keep it on 127.0.0.1, a private network or a tunnel).")
@click.option("--gui-password-env", default=None, help="Your own web UI password, from this environment variable.")
@click.option("--save", is_flag=True, help="Write the resource (and triggers) into pskit.yml.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
@click.option("--json", "as_json", is_flag=True, help="Print the created entry as JSON.")
def resources_onprem_create(  # noqa: PLR0913
    key: str,
    service: str,
    role: Optional[str],
    port: Optional[int],
    image: Optional[str],
    address: Optional[str],
    bind: Optional[str],
    ssh: Optional[str],
    pem: Optional[str],
    ssh_password_file: Optional[str],
    ssh_password_env: Optional[str],
    sudo: bool,
    local: bool,
    login: Optional[str],
    password: Optional[str],
    password_env: Optional[str],
    attach: tuple,
    gui: bool,
    gui_login: Optional[str],
    gui_password_env: Optional[str],
    server: Optional[str],
    version: Optional[str],
    domains: Optional[str],
    functions_url: Optional[str],
    max_size_mb: Optional[float],
    save: bool,
    config_path: Optional[str],
    as_json: bool,
) -> None:
    """Create KEY: a Docker container on this machine or an SSH server, ready for the on-prem host."""
    from ..core import onprem_services as svc

    root, path, raw = _raw_project(config_path)
    target: Optional[dict] = None
    if local:
        target = {"method": "local"}
    elif server:
        target = {"server": server, **({"sudo": True} if sudo else {})}
    elif ssh:
        user, _, rest = ssh.rpartition("@")
        host, _, ssh_port = rest.partition(":")
        target = {"method": "ssh", "user": user or "root", "host": host, "port": int(ssh_port or 22), "sudo": sudo}
        if pem:
            target["identity_file"] = pem
        if ssh_password_file:
            target["password_file"] = ssh_password_file
        elif ssh_password_env:
            target["password"] = _env_value(ssh_password_env)
    elif ssh_password_file or ssh_password_env or pem:
        raise click.ClickException("--pem / --ssh-password-* go with --ssh user@host")
    if password_env:
        password = _env_value(password_env)
    gui_password = _env_value(gui_password_env) if gui_password_env else None
    body = {
        "key": key,
        "service": service,
        "user": login,
        "password": password,
        "role": role,
        "port": port,
        "image": image,
        "address": address,
        "bind": bind,
        "attach": list(attach),
        "target": target,
        "gui": gui or None,
        "gui_login": gui_login if gui else None,
        "gui_password": gui_password if gui else None,
        "version": version,
        "domains": domains,
        "functions_url": functions_url,
        "max_size_mb": max_size_mb,
    }
    entry = _service_call(svc.create, root, {k: v for k, v in body.items() if v is not None}, raw, echo)
    if as_json:
        click.echo(json.dumps(svc.public_entry(entry), indent=2))
    if entry.get("kind") == "runtime":
        ok(f"{service} is installed on {svc.describe_target(entry['target'])}")
        return
    if save:
        _save_raw(path, _service_call(svc.apply_to_raw, raw, entry))
    elif not as_json:
        info("Add this to pskit.yml (or rerun with --save):")
        click.echo(svc.snippet(entry))
    if entry["resource"]["type"] == "smtp":
        click.echo(svc.service_usage(root, key, probe=False)["markdown"])
    elif entry["resource"]["type"] in ("queue", "storage", "topic"):
        info(f"Test it: pskit deploy, then pskit resources send {key}")
    elif entry.get("role") == "secrets":
        store = "" if key == "secrets" else f", store=\"{key}\""
        info(f'Read secrets in a handler: get_secret("name"{store}); add some with put_secret or the console')
    else:
        info(f'Read it in a handler: resource("{key}"); the password is in ctx.env[res["password_env"]]')


@resources_onprem.command("list", short_help="Services created from this project.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def resources_onprem_list(config_path: Optional[str], as_json: bool) -> None:
    from ..core.onprem_services import list_services

    root, _path, _raw = _raw_project(config_path)
    items = list_services(root)
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return
    if not items:
        echo("No on-prem services yet. Create one: pskit resources onprem create <key> --service redis")
        return
    echo(f"{'key':<20}{'service':<11}{'as':<10}{'where':<28}endpoint")
    for item in items:
        where = item["target_label"]
        endpoint = f"{item.get('address')}:{item.get('port')}" if item.get("port") else ""
        echo(f"{item['key']:<20}{item['service']:<11}{item.get('role') or 'runtime':<10}{where[:27]:<28}{endpoint}")


@resources_onprem.command("status", short_help="State, health and connection details of a service.")
@click.argument("key")
@click.option("--reveal", is_flag=True, help="Show passwords and URLs from the env file.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_status(key: str, reveal: bool, config_path: Optional[str]) -> None:
    from ..core.onprem_services import status

    root, _path, _raw = _raw_project(config_path)
    click.echo(json.dumps(_service_call(status, root, key, reveal=reveal), indent=2))


@resources_onprem.command("usage", short_help="Connect to a service from any program: code, terminal, security.")
@click.argument("key")
@click.option("--reveal", is_flag=True, help="Show passwords and URLs in the guide.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_usage(key: str, reveal: bool, config_path: Optional[str]) -> None:
    """How to use service KEY from any program: connection details, Python / Node.js clients, terminal tools."""
    from ..core.onprem_services import service_usage

    root, _path, _raw = _raw_project(config_path)
    text = _service_call(service_usage, root, key, reveal=reveal)["markdown"]
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    click.echo(text.encode(encoding, "replace").decode(encoding))


@resources_onprem.command("tunnel", short_help="Reach a service on a server through SSH (firewalled port).")
@click.argument("key")
@click.option("--local-port", type=int, default=None, help="Port on this machine (default: the remote port).")
@click.option("--remote-port", type=int, default=None, help="Port on the server (default: the service's; e.g. its web UI).")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_tunnel(
    key: str, local_port: Optional[int], remote_port: Optional[int], config_path: Optional[str]
) -> None:
    """Forward 127.0.0.1:<port> here to the service on its SSH server, until Ctrl+C.

    \b
    Use it when the service's port is closed in the server's firewall (connections time out):
    scripts on this machine then connect to 127.0.0.1 with the variables printed below.
    """
    import subprocess

    from ..core import onprem_services as svc

    root, _path, _raw = _raw_project(config_path)
    entry = _service_call(svc._entry, root, key)
    if entry.get("kind") == "runtime":
        raise click.ClickException(f"{key} is a runtime, not a service")
    remote = int(remote_port or entry["port"])
    port = int(local_port or remote)
    argv = _service_call(svc.tunnel_argv, entry["target"], port, remote)
    if remote != int(entry["port"]):
        info(f"Open http://127.0.0.1:{port}/ in your browser.")
    info(f"Forwarding 127.0.0.1:{port} -> {entry['address']}:{entry['port']} over SSH. Ctrl+C to stop.")
    for name, value in sorted(svc.local_env(entry, port).items()):
        echo(f"  export {name}='{value}'")
    if not entry.get("env"):
        echo(f"  connect your client to 127.0.0.1:{port}")
    try:
        raise SystemExit(subprocess.call(argv))
    except KeyboardInterrupt:
        ok("Tunnel closed.")


@resources_onprem.command("gui", short_help="Start (or --remove) the web UI of a service.")
@click.argument("key")
@click.option("--port", type=int, default=None, help="Port for the UI (default: Adminer 8089, Redis Commander 8082, ...).")
@click.option("--login", "gui_login", type=click.Choice(["password", "none"]), default="password", show_default=True,
              help="Sign-in of the companion UI; none = no login (keep it private).")
@click.option("--password-env", default=None, help="Your own UI password from this environment variable (default: generated).")
@click.option("--check", is_flag=True, help="Find out why a web UI does not open (container, ports, server, firewall).")
@click.option("--remove", is_flag=True, help="Stop and delete the companion web UI.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_gui(  # noqa: PLR0913
    key: str,
    port: Optional[int],
    gui_login: str,
    password_env: Optional[str],
    check: bool,
    remove: bool,
    config_path: Optional[str],
) -> None:
    """Web UIs of service KEY: built in (RabbitMQ, MinIO, ...) or a companion pskit starts next to it.

    \b
    pskit resources onprem gui cache                     # start Redis Commander (generated password)
    pskit resources onprem gui cache --login none        # no sign-in: keep it on 127.0.0.1 or a tunnel
    pskit resources onprem gui cache --check             # it does not open? find out why

    
    Companions: Adminer (PostgreSQL / MySQL / MariaDB), Redis Commander (Redis, Valkey, KeyDB, Dragonfly),
    Kafka UI (Kafka, Apache Kafka), Mongo Express (MongoDB). They share a private Docker network with the service.
    """
    from ..core import onprem_services as svc

    root, _path, _raw = _raw_project(config_path)
    if remove:
        _service_call(svc.remove_gui, root, key, echo)
        ok(f"Removed the web UI of {key}")
        return
    if check:
        result = _service_call(svc.gui_check, root, key, echo)
        if result["ok"]:
            ok("Every web UI answers from this machine.")
            return
        raise SystemExit(1)
    entry = _service_call(svc._entry, root, key)
    password = _env_value(password_env) if password_env else ""
    wants_change = port is not None or gui_login != "password" or bool(password)
    if svc.companion_for(str(entry.get("service"))) and (not isinstance(entry.get("gui"), dict) or wants_change):
        _service_call(svc.add_gui, root, key, echo, port=port, login=gui_login, password=password)
        entry = _service_call(svc._entry, root, key)
    links = svc.gui_links(entry)
    if not links:
        raise click.ClickException(f"{entry.get('service')} has no web UI")
    for link in links:
        echo(f"  {link['label']:<24} {link['url']:<32} login: {link['login']}")
    if entry.get("target", {}).get("method") == "ssh":
        info(f"Does not open? pskit resources onprem gui {key} --check  "
             f"(or skip the firewall: pskit resources onprem tunnel {key} --remote-port {links[0]['port']})")


# ----------------------------------------------------------------------------- saved SSH servers


@resources_onprem.group("servers")
def resources_onprem_servers() -> None:
    """Saved SSH servers: name a machine once, then create services, install software, open shells and deploy to it.

    \b
    pskit resources onprem servers add edge1 --ssh ubuntu@10.0.0.5 --pem ~/.ssh/edge1.pem
    pskit resources onprem servers add lab --ssh root@192.168.1.20 --ssh-password-env LAB_PW
    pskit resources onprem servers test edge1
    pskit resources onprem create cache --service redis --server edge1
    pskit deploy --server edge1                 # or provider.options.deploy: {server: edge1}
    """


@resources_onprem_servers.command("list")
@click.option("--json", "as_json", is_flag=True)
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_servers_list(as_json: bool, config_path: Optional[str]) -> None:
    from ..core.onprem_servers import list_servers

    root, _path, _raw = _raw_project(config_path)
    servers = list_servers(root)
    if as_json:
        click.echo(json.dumps(servers, indent=2))
        return
    if not servers:
        info("No saved servers. Add one: pskit resources onprem servers add NAME --ssh user@host [--pem FILE]")
        return
    for item in servers:
        login = "password" if item.get("password_file") else ("key " + item["identity_file"]) if item.get(
            "identity_file") else "agent / default key"
        echo(f"  {item['name']:<16} {item['label']:<36} {login}{'  sudo' if item.get('sudo') else ''}")


@resources_onprem_servers.command("add")
@click.argument("name")
@click.option("--ssh", "ssh", required=True, help="user@host[:port]")
@click.option("--pem", default=None, help="Private key (empty: the SSH agent or your default key).")
@click.option("--ssh-password-env", default=None, help="Log in with the password in this variable (sshpass).")
@click.option("--ssh-password-file", default=None, help="Log in with the password in this file (sshpass).")
@click.option("--sudo", is_flag=True, help="Run docker / installs with sudo -n.")
@click.option("--description", default="", help="A note shown in the studio.")
@click.option("--test/--no-test", "run_test", default=True, help="Log in once after saving (default: yes).")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_servers_add(  # noqa: PLR0913
    name: str, ssh: str, pem: Optional[str], ssh_password_env: Optional[str], ssh_password_file: Optional[str],
    sudo: bool, description: str, run_test: bool, config_path: Optional[str],
) -> None:
    """Save (or replace) server NAME."""
    from ..core import onprem_servers as srv

    root, _path, _raw = _raw_project(config_path)
    user, _, rest = ssh.rpartition("@")
    host, _, ssh_port = rest.partition(":")
    raw: dict = {"host": host, "user": user or "root", "port": int(ssh_port or 22), "sudo": sudo}
    if ssh_password_env:
        raw.update({"auth": "password", "password": _env_value(ssh_password_env)})
    elif ssh_password_file:
        raw.update({"auth": "password", "password_file": ssh_password_file})
    elif pem:
        raw.update({"auth": "key", "identity_file": pem})
    else:
        raw["auth"] = "agent"
    try:
        srv.save_server(root, name, raw, description=description)
    except srv.ServerError as exc:
        raise click.ClickException(str(exc)) from None
    ok(f"Saved server {name}")
    if run_test:
        _print_server_test(srv.test_server(root, name))


def _print_server_test(result: dict) -> None:
    if not result.get("ok"):
        raise click.ClickException(f"cannot use {result.get('name')}: {result.get('error')}")
    for key, value in result["facts"].items():
        echo(f"  {key:<14} {value or '-'}")
    ok(f"{result['name']} answers over SSH")


@resources_onprem_servers.command("test")
@click.argument("name")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_servers_test(name: str, config_path: Optional[str]) -> None:
    """Log in and show the OS, CPU, memory, disk, Docker access, sudo and installed runtimes."""
    from ..core import onprem_servers as srv

    root, _path, _raw = _raw_project(config_path)
    try:
        _print_server_test(srv.test_server(root, name))
    except srv.ServerError as exc:
        raise click.ClickException(str(exc)) from None


@resources_onprem_servers.command("remove")
@click.argument("name")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_servers_remove(name: str, config_path: Optional[str]) -> None:
    """Forget server NAME (services already created there keep running)."""
    from ..core import onprem_servers as srv

    root, _path, _raw = _raw_project(config_path)
    try:
        srv.remove_server(root, name)
    except srv.ServerError as exc:
        raise click.ClickException(str(exc)) from None
    ok(f"Removed server {name}")


# ----------------------------------------------------------------------------- consoles and terminals


@resources_onprem.command("console", short_help="Run commands in a service (redis-cli, psql, mongosh, rpk, ...).")
@click.argument("key")
@click.argument("command", nargs=-1)
@click.option("--shell", is_flag=True, help="A shell (sh) in the container instead of the service's client.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_console(key: str, command: tuple, shell: bool, config_path: Optional[str]) -> None:
    """Open the service's client in this terminal, or run one COMMAND and print the output.

    \b
    pskit resources onprem console cache                    # interactive redis-cli
    pskit resources onprem console cache "LLEN jobs"
    pskit resources onprem console orders-db "SELECT count(*) FROM orders;"
    pskit resources onprem console secrets "kv get secret/db-password"
    pskit resources onprem console cache --shell            # sh inside the container
    """
    from ..core import onprem_console as con

    root, _path, _raw = _raw_project(config_path)
    if command:
        try:
            result = con.run_line(root, key, " ".join(command), shell=shell)
        except (con.ConsoleError, Exception) as exc:  # noqa: BLE001 - ServiceError too
            raise click.ClickException(str(exc)) from None
        click.echo(result["output"].rstrip())
        raise SystemExit(result["exit_code"])
    import subprocess

    argv = _service_call(con.interactive_argv, root, key, shell=shell)
    raise SystemExit(subprocess.call(argv))  # noqa: S603


@resources_onprem.command("shell", short_help="A shell on a saved SSH server (or run one command there).")
@click.argument("server")
@click.argument("command", nargs=-1)
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_shell(server: str, command: tuple, config_path: Optional[str]) -> None:
    """SSH to SERVER with its saved login; with COMMAND, run it and print the output."""
    from ..core import onprem_console as con
    from ..core import onprem_servers as srv
    from ..core.onprem_services import _ssh_argv

    root, _path, _raw = _raw_project(config_path)
    try:
        target = srv.server_target(root, server)
    except srv.ServerError as exc:
        raise click.ClickException(str(exc)) from None
    if command:
        result = con.run_server(target, " ".join(command))
        click.echo(result["output"].rstrip())
        raise SystemExit(result["exit_code"])
    import subprocess

    argv = _ssh_argv(target, "")[:-1]
    if "BatchMode=yes" in argv:                      # an interactive login may ask for a key passphrase
        i = argv.index("BatchMode=yes")
        del argv[i - 1:i + 1]
    argv.insert(argv.index("ssh") + 1, "-t")
    raise SystemExit(subprocess.call(argv))  # noqa: S603


@resources_onprem.command("terminal", short_help="A full terminal in the browser (Docker): a server or a service.")
@click.argument("key", required=False)
@click.option("--server", "server", default=None, help="SSH to this saved server.")
@click.option("--shell", is_flag=True, help="With KEY: sh in the container instead of the service's client.")
@click.option("--list", "list_them", is_flag=True, help="Show running terminals.")
@click.option("--stop", "stop", default=None, help="Stop a terminal by name (pskit-term-7681), or 'all'.")
@click.option("--open/--no-open", "open_browser", default=True, help="Open it in the browser.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_terminal(  # noqa: PLR0913
    key: Optional[str], server: Optional[str], shell: bool, list_them: bool, stop: Optional[str], open_browser: bool,
    config_path: Optional[str],
) -> None:
    """ttyd in Docker on 127.0.0.1 (secret URL): an SSH session to a server, or the client / sh of a service.

    \b
    pskit resources onprem terminal --server edge1
    pskit resources onprem terminal cache            # redis-cli in the browser
    pskit resources onprem terminal --list
    pskit resources onprem terminal --stop all
    """
    from ..core import onprem_terminal as term

    if list_them or stop:
        running = term.list_terminals()
        for item in running:
            if stop in ("all", item["name"]):
                term.stop_terminal(item["name"])
                ok(f"Stopped {item['name']}")
            elif list_them:
                echo(f"  {item['name']:<18} {item['title']:<36} {item['status']}")
        if list_them and not running:
            info("No terminals running.")
        return
    if not key and not server:
        raise click.ClickException("give a service KEY or --server NAME")
    root, _path, _raw = _raw_project(config_path)
    try:
        result = term.start_terminal(root, echo, server=server, key=key, shell=shell)
    except (term.TerminalError, Exception) as exc:  # noqa: BLE001 - ServiceError / ServerError too
        raise click.ClickException(str(exc)) from None
    ok(f"Open {result['url']}  (stop: pskit resources onprem terminal --stop {result['name']})")
    if open_browser:
        import webbrowser

        webbrowser.open(result["url"])


@resources_onprem.command("ssh-key", short_help="Install an SSH key on a server that takes a password (once).")
@click.argument("destination")
@click.option("--key", "key_path", default=None, help="Key to install (default: ~/.ssh/pskit_ed25519, created if missing).")
@click.option("--password-env", default=None, help="Send the password from this variable (needs sshpass); default: asked.")
def resources_onprem_ssh_key(destination: str, key_path: Optional[str], password_env: Optional[str]) -> None:
    """Log in to DESTINATION (user@host[:port]) with its password once and install a key, then use the key.

    \b
    pskit resources onprem ssh-key azureuser@13.72.87.80
    pskit resources onprem create jobs --service elasticmq --ssh azureuser@13.72.87.80 --pem ~/.ssh/pskit_ed25519

    Works on Windows too (no sshpass needed: ssh asks the password in this terminal).
    """
    from ..core import onprem_services as svc

    user, _, rest = destination.rpartition("@")
    host, _, ssh_port = rest.partition(":")
    target = {"method": "ssh", "user": user or "root", "host": host, "port": int(ssh_port or 22)}
    password = _env_value(password_env) if password_env else ""
    path = _service_call(svc.install_ssh_key, target, echo, key_path=key_path, password=password)
    ok(f"Key login works. Use --pem {path} (studio: Private key), or in pskit.yml: provider.options.deploy.pem: {path}")


@resources_onprem.command("logs", short_help="The service container's recent output.")
@click.argument("key")
@click.option("-n", "--tail", default=200, show_default=True)
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_logs(key: str, tail: int, config_path: Optional[str]) -> None:
    from ..core.onprem_services import logs

    root, _path, _raw = _raw_project(config_path)
    click.echo(_service_call(logs, root, key, tail=tail))


@resources_onprem.command("backup", short_help="Dump a service's data to .pskit/backups.")
@click.argument("key")
@click.option("-o", "--output", default=None, help="File to write (default: .pskit/backups/<key>-<time>.<ext>).")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_backup(key: str, output: Optional[str], config_path: Optional[str]) -> None:
    from ..core.onprem_services import backup

    root, _path, _raw = _raw_project(config_path)
    dest = Path(output).resolve() if output else None
    saved = _service_call(backup, root, key, echo, dest=dest)
    ok(f"Backup written to {saved}")


@resources_onprem.command("remove", short_help="Stop and delete a service container.")
@click.argument("key")
@click.option("--delete-data", is_flag=True, help="Also delete its data volume.")
@click.option("--save", is_flag=True, help="Also remove the resource and its triggers from pskit.yml.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
def resources_onprem_remove(key: str, delete_data: bool, save: bool, yes: bool, config_path: Optional[str]) -> None:
    from ..core import onprem_services as svc

    root, path, raw = _raw_project(config_path)
    if not yes:
        what = "the container and its data" if delete_data else "the container (data is kept)"
        click.confirm(f"Delete {what} of {key}?", abort=True)
    _service_call(svc.remove, root, key, echo, delete_data=delete_data)
    if save:
        _save_raw(path, svc.remove_from_raw(raw, key))
    ok(f"Removed {key}")


# ----------------------------------------------------------------------------- client
@cli.group(short_help="Call the on-prem host from scripts.")
def client() -> None:
    """Use the deployed on-prem host from any program: Python / Node.js client, HTTP, tunnel.

    \b
    Examples:
      pskit client info                      # endpoint, token, reachability, code to copy
      pskit client install-node              # ./node_modules/pskit for scripts outside pskit
      pskit client tunnel                    # 127.0.0.1:8080 here -> the host on its server
    """


def _host_control(project: Any) -> Any:
    from ..core.deployer import Deployer
    from ..core.onprem_control import control_for

    if project.config.provider.name != "onprem":
        raise click.ClickException("the pskit client talks to an on-prem host: use -p onprem")
    record = Deployer(project, echo=echo).load_record()
    control = control_for(record or {}, project.root) if record else None
    if control is None:
        raise click.ClickException("the on-prem host is not deployed yet: run `pskit deploy`")
    return control


@client.command("info", short_help="Endpoint, token, reachability and code to call the host.")
@project_options
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def client_info(project, as_json: bool) -> None:  # noqa: ANN001
    """How to call the deployed host from scripts: Python, Node.js and curl, checked for reachability."""
    from ..core.client_guide import client_guide

    control = _host_control(project)
    guide = client_guide(project, control.base_url, bool(control.token))
    if as_json:
        click.echo(json.dumps(guide, indent=2))
        return
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    click.echo(guide["markdown"].encode(encoding, "replace").decode(encoding))


@client.command("install-node", short_help="Put the pskit Node.js package (with PskitClient) in node_modules.")
@click.option("--dir", "target_dir", default=".", show_default=True, help="Project whose node_modules gets it.")
def client_install_node(target_dir: str) -> None:
    """Copy the `pskit` package (no dependencies) to <dir>/node_modules/pskit, for scripts outside pskit."""
    from ..packaging.node import copy_runtime

    dest = Path(target_dir).resolve() / "node_modules" / "pskit"
    copy_runtime(dest)
    ok(f"Installed pskit into {dest}")
    echo('Use it: const { PskitClient } = require("pskit");')


@client.command("tunnel", short_help="Forward the host's port here over SSH (firewalled host port).")
@project_options
@click.option("--local-port", type=int, default=None, help="Port on this machine (default: the host's port).")
def client_tunnel(project, local_port: Optional[int]) -> None:  # noqa: ANN001
    """127.0.0.1:<port> here -> the on-prem host on its SSH server, until Ctrl+C."""
    import subprocess

    from ..core.onprem_services import tunnel_argv
    from ..providers.onprem_spec import parse_deploy

    cfg = project.config
    spec = parse_deploy(cfg.provider.options, service=cfg.service, stage=cfg.provider.stage,
                        runtime=cfg.provider.runtime, architecture=cfg.provider.architecture)
    if not spec.ssh_host:
        raise click.ClickException("the host runs on this machine; no tunnel needed")
    remote = int(cfg.provider.options.get("port") or 8080)
    port = int(local_port or remote)
    target = {"method": "ssh", "host": spec.ssh_host, "user": spec.ssh_user or "root", "port": spec.ssh_port,
              "identity_file": spec.identity_file or "", "known_hosts": spec.known_hosts}
    argv = tunnel_argv(target, port, remote)
    info(f"Forwarding 127.0.0.1:{port} -> {spec.ssh_host}:{remote}. In another terminal: export PSKIT_HOST=http://127.0.0.1:{port}")
    try:
        raise SystemExit(subprocess.call(argv))
    except KeyboardInterrupt:
        ok("Tunnel closed.")


def _ask_onprem_pem(project: Any) -> None:
    """Ask for the server ``.pem`` when a remote deploy has none yet.

    Asked only when a terminal is attached; an empty answer keeps the SSH agent
    or the default key. A password login (``password_file`` / ``auth: password``) asks nothing.
    """
    if project.config.provider.name != "onprem":
        return
    from ..providers.onprem_spec import assign_pem, parse_deploy, targets_remote_docker, uses_password

    cfg = project.config
    try:
        spec = parse_deploy(
            cfg.provider.options,
            service=cfg.service,
            stage=cfg.provider.stage,
            runtime=cfg.provider.runtime,
            architecture=cfg.provider.architecture,
        )
    except ValueError:
        return
    remote_docker = targets_remote_docker(spec)
    remote_ssh = spec.method == "ssh"
    if not remote_docker and not remote_ssh:
        return
    if (spec.identity_file and os.path.isfile(spec.identity_file)) or uses_password(spec) or spec.auth == "agent":
        return
    who = f"{spec.ssh_user}@{spec.ssh_host}" if spec.ssh_user else spec.ssh_host
    if not sys.stdin.isatty():
        if spec.identity_file:
            raise click.ClickException(f"SSH private key not found: {spec.identity_file}")
        return
    if remote_docker:
        echo(f"On-premises Docker deploy builds an image on this machine and copies it to {who}.")
        echo("If Docker is not installed on that server, it is installed before the image is loaded.")
    else:
        echo(f"On-premises SSH deploy copies one archive to {who} and starts the host.")
    echo("Enter a .pem path, or press Enter to use the SSH agent or the default key "
         "(password login: options.deploy.password_file or auth: password with SSHPASS).")
    entered = click.prompt("Path to the SSH private key (.pem)", default="", show_default=False)
    text = str(entered).strip()
    if not text:
        return
    candidate = Path(text).expanduser()
    if not candidate.is_file():
        raise click.ClickException(f"SSH private key not found: {text}")
    assign_pem(cfg.provider.options, str(candidate))


# ----------------------------------------------------------------------------- info / dashboard
def _print_record(record: dict) -> None:
    if record.get("endpoint"):
        echo(f"endpoint: {record['endpoint']}")
    if record.get("deployed_on"):
        echo(f"deployed on: {record['deployed_on'].get('label')}")
    echo("functions:")
    for f in record["functions"]:
        triggers = ", ".join(e["summary"] for e in f["events"]) or "direct invoke"
        echo(f"  {f['name']:<24} {f['kind']:<13} {triggers}")
        for u in f.get("urls") or []:
            echo(f"      {u['method']:<6} {u['url']}")
        if f.get("url") and not f.get("urls"):
            echo(f"      url: {f['url']}")
        if f.get("arn") or f.get("id"):
            echo(f"      id:  {f.get('arn') or f.get('id')}")
    if record.get("workflows"):
        echo("workflows:")
        for w in record["workflows"]:
            echo(f"  {w['name']:<24} {w['engine']:<9} {w.get('id') or ''}")


@cli.command("info")
@project_options
@click.option("--json", "as_json", is_flag=True, help="Machine readable output.")
@click.option("--refresh", is_flag=True, help="Re-read terraform outputs.")
def info_cmd(project, as_json: bool, refresh: bool) -> None:  # noqa: ANN001
    """Show what is deployed (functions, URLs, ids).

    
    Examples:
      pskit info
      pskit info -s prod --refresh --json
    """
    from ..core.deployer import Deployer
    from ..dashboard.generator import local_record

    deployer = Deployer(project, echo=echo)
    record = deployer.refresh_info() if refresh else deployer.load_record()
    if record is None:
        record = local_record(project)
        record["status"] = "not deployed"
    if as_json:
        click.echo(json.dumps(record, indent=2, default=str))
        return
    info(
        f"{record['service']} | {record['stage']} | {record['provider_display']} | {record.get('region')} | {record['status']}"
    )
    _print_record(record)


@cli.command()
@click.option(
    "-d", "--dir", "directory", default=".", type=click.Path(file_okay=False, exists=True), help="Project root."
)
@click.option("-o", "--output", default=None, type=click.Path(dir_okay=False), help="Write HTML here.")
@click.option("--serve", is_flag=True, help="Serve the dashboard with live logs and workflow executions.")
@click.option("--host", "bind_host", default="127.0.0.1", show_default=True, help="Bind address for --serve.")
@click.option("--port", default=8765, show_default=True)
@click.option("--open", "open_browser", is_flag=True, help="Open in the browser.")
def dashboard(
    directory: str, output: Optional[str], serve: bool, bind_host: str, port: int, open_browser: bool
) -> None:
    """Generate (and optionally serve) the deployment dashboard.

    
    Examples:
      pskit dashboard --open
      pskit dashboard --serve --open      # live logs and workflow executions
      pskit dashboard -o site/index.html
    """
    from ..dashboard.generator import collect_records, local_record, write_dashboard

    root = Path(directory).resolve()
    records = collect_records(root)
    if not records:
        try:
            from ..core.project import Project

            records = [local_record(Project.load(cwd=root))]
            echo("No deployments yet - showing the configured (local) view.")
        except ConfigError:
            pass
    path = write_dashboard(root, records, Path(output).resolve() if output else None)
    ok(f"Dashboard written to {path}")
    if serve:
        from ..dashboard.server import is_loopback, make_server

        try:
            httpd = make_server(root, bind_host, port)
        except OSError as exc:
            raise click.ClickException(f"cannot listen on {bind_host}:{port}: {exc}") from exc
        shown = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host  # noqa: S104
        if ":" in shown and not shown.startswith("["):
            shown = f"[{shown}]"
        url = f"http://{shown}:{httpd.server_address[1]}/"
        if not is_loopback(bind_host):
            fail(
                f"warning: listening on {bind_host}. Anyone who can reach this port can read logs and "
                "start or cancel workflow executions."
            )
        echo(f"Serving on {url} (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    elif open_browser:
        webbrowser.open(path.as_uri())


@cli.command()
@click.option(
    "-d", "--dir", "directory", default=".", type=click.Path(file_okay=False, exists=True), help="Project root."
)
@click.option("--host", "bind_host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=8770, show_default=True)
@click.option("--open", "open_browser", is_flag=True, help="Open in the browser.")
@click.option("--vscode-image", default=None, help="code-server image for the full VS Code view.")
def studio(directory: str, bind_host: str, port: int, open_browser: bool, vscode_image: Optional[str]) -> None:
    """Edit the project in the browser (writes pskit.yml).

    Build functions, triggers, workflows and resources with forms, edit code and deploy.

    
    Examples:
      pskit studio --open
      pskit studio -d path/to/project --port 8800
    """
    from ..studio.server import is_loopback, make_server, studio_url

    root = Path(directory).resolve()
    try:
        httpd = make_server(root, bind_host, port, vscode_image=vscode_image)
    except OSError as exc:
        raise click.ClickException(f"cannot listen on {bind_host}:{port}: {exc}") from exc
    url = studio_url(httpd)
    if not is_loopback(bind_host):
        fail(
            f"warning: listening on {bind_host}. The studio edits files and runs deploys; anyone with the "
            "link (it contains the session token) can do the same."
        )
    ok(f"pskit studio for {root}")
    echo(f"Open {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# ----------------------------------------------------------------------------- doctor
@cli.command()
@click.option("-p", "--provider", default=None, help="Provider to check (default: provider.name in pskit.yml).")
@click.option("-c", "--config", "config_path", default=None, help="Config file (default: pskit.yml).")
@click.option("--json", "as_json", is_flag=True, help="Machine readable output.")
def doctor(provider: Optional[str], config_path: Optional[str], as_json: bool) -> None:
    """Check terraform, the cloud CLI and credentials.

    Lists what is missing for the provider and how to install or configure it
    on this OS. Works without a pskit.yml when -p is given. Exits with 1 if
    anything required is missing.

    \b
    Examples:
      pskit doctor
      pskit doctor -p azure
      pskit doctor -p onprem --json
    """
    import yaml

    from ..config.loader import find_config
    from ..core.setup_check import check, os_key

    raw: dict = {}
    try:
        path = find_config(config_path)
        loaded = yaml.safe_load(path.read_text("utf-8"))
        raw = loaded if isinstance(loaded, dict) else {}
    except (ConfigError, OSError, yaml.YAMLError):
        if config_path:
            raise click.ClickException(f"cannot read {config_path}") from None
    if isinstance(raw.get("provider"), str):
        raw["provider"] = {"name": raw["provider"]}
    name = provider or str((raw.get("provider") or {}).get("name") or "")
    if not name:
        raise click.UsageError("no pskit.yml here; pass the provider, e.g. `pskit doctor -p aws`")
    if name not in PROVIDERS:
        raise click.BadParameter(f"unknown provider {name!r}; use one of {', '.join(PROVIDERS)}", param_hint="-p")
    report = check(name, raw if (raw.get("provider") or {}).get("name", name) == name else None, Path.cwd())
    if as_json:
        click.echo(json.dumps(report, indent=2))
    else:
        try:
            "✓✗".encode(getattr(sys.stdout, "encoding", None) or "ascii")
            good, bad = "✓", "✗"
        except (LookupError, UnicodeEncodeError):
            good, bad = "+", "x"
        marks = {"ok": (good, "green"), "missing": (bad, "red"), "warning": ("!", "yellow"), "info": ("i", "cyan")}
        info(f"pskit doctor: {name}")
        here = os_key()
        for item in report["checks"]:
            mark, color = marks.get(item["status"], ("?", None))
            echo(click.style(f" {mark} ", fg=color) + f"{item['title']}: {item['detail']}")
            fix = item.get("fix") or {}
            if item["status"] in ("ok",) or not (fix.get("summary") or fix.get("steps", {}).get(here)):
                continue
            if fix.get("summary"):
                echo(f"     {fix['summary']}")
            for step in (fix.get("steps") or {}).get(here) or []:
                echo(f"       {step}")
            for command in fix.get("commands") or []:
                echo(f"       $ {command}")
            for link in fix.get("links") or []:
                echo(f"       {link['label']}: {link['url']}")
        if report["ok"]:
            ok("Everything required is in place.")
        else:
            fail(f"Some required pieces are missing ({bad}).")
    if not report["ok"]:
        sys.exit(1)


# ----------------------------------------------------------------------------- local
@cli.command()
@project_options
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=3000, show_default=True)
@click.option("--env-file", default=".env", show_default=True)
def dev(project, host: str, port: int, env_file: str) -> None:  # noqa: ANN001
    """Run a local server with hot reload.

    Routes HTTP requests to your handlers and reloads them when files change.

    
    Examples:
      pskit dev
      pskit dev --port 8080 --env-file .env.local
    """
    from ..local.runner import LocalRunner
    from ..local.server import serve

    serve(LocalRunner(project, env_file=env_file), host=host, port=port, echo=echo)


@cli.command()
@project_options
@click.option("-f", "--function", "function", required=True, help="Function name from the config.")
@click.option(
    "-t",
    "--type",
    "kind",
    default=None,
    type=click.Choice(["http", "schedule", "queue", "storage", "topic", "invoke", "workflow", "smtp"]),
    help="Event type (default: the function's first event, or invoke).",
)
@click.option("-d", "--data", default=None, help="JSON payload or @file.json.")
@click.option("--method", default=None, help="HTTP method (http events).")
@click.option("--path", "path", default=None, help="HTTP path incl. query string (http events).")
@click.option("-H", "--header", "headers", multiple=True, help="HTTP header 'Name: value' (repeatable).")
@click.option("--env-file", default=".env", show_default=True)
def invoke(
    project,
    function: str,
    kind: Optional[str],
    data: Optional[str],
    method: Optional[str],  # noqa: ANN001
    path: Optional[str],
    headers: tuple,
    env_file: str,
) -> None:
    """Invoke a function locally with a synthetic event.

    
    Examples:
      pskit invoke -f hello
      pskit invoke -f hello -t http --method POST --path "/hello?x=1" -d '{"name": "Ada"}'
      pskit invoke -f worker -t queue -d @message.json
    """
    from ..local.runner import LocalRunner, build_event, parse_data, response_to_dict

    fn = project.config.functions.get(function)
    if fn is None:
        raise KeyError(f"unknown function {function!r}; known: {', '.join(sorted(project.config.functions))}")
    first = fn.events[0] if fn.events else None
    kind = kind or (first.type if first else "invoke")
    parsed_headers = {}
    for raw_header in headers:
        hname, sep, hvalue = raw_header.partition(":")
        if not sep:
            raise click.BadParameter(f"expected 'Name: value', got {raw_header!r}", param_hint="--header")
        parsed_headers[hname.strip()] = hvalue.strip()
    http_kw: dict = {"headers": parsed_headers or None}
    if kind == "http":
        route = next(iter(fn.events_of("http")), None)
        http_kw["method"] = method or (route.method if route and route.method != "ANY" else "GET")
        http_kw["path"] = path or (route.path if route else "/")
    payload = parse_data(data)
    if kind == "schedule" and payload is None and fn.events_of("schedule"):
        payload = fn.events_of("schedule")[0].input
    runner = LocalRunner(project, env_file=env_file)
    try:
        result = runner.invoke(function, build_event(kind, payload, **http_kw))
    except _errors() as exc:
        fail(f"Error: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - user code failed; show it like the cloud would
        stack = getattr(exc, "stack", "")
        if stack:
            click.echo(stack, err=True)
        else:
            traceback.print_exc()
        fail(f"{function} raised {getattr(exc, 'error', None) or type(exc).__name__}: {exc}")
        sys.exit(1)
    finally:
        runner.close()
    click.echo(json.dumps(response_to_dict(result), indent=2, default=str))


# ----------------------------------------------------------------------------- workflows
@cli.group()
def workflow() -> None:
    """Run or inspect workflows (state machines).

    
    Examples:
      pskit workflow run order -i '{"id": 1}' --history    # local, embedded engine
      pskit workflow show order --format asl
      pskit workflow start order -i @input.json --wait     # the deployed workflow
      pskit workflow executions order --status FAILED
      pskit workflow status <execution-id> --watch
      pskit workflow cancel <execution-id>
    """


@workflow.command("run")
@project_options
@click.argument("name")
@click.option("-i", "--input", "data", default=None, help="JSON input or @file.json.")
@click.option("--sleep", is_flag=True, help="Honour wait states (default: skip waiting).")
@click.option("--history", is_flag=True, help="Print the execution history.")
def workflow_run(project, name: str, data: Optional[str], sleep: bool, history: bool) -> None:  # noqa: ANN001
    """Run a workflow locally on the embedded engine.

    
    Examples:
      pskit workflow run order -i '{"id": 1}'
      pskit workflow run order -i @input.json --history --sleep
    """
    from ..local.runner import LocalRunner, parse_data
    from ..runtime.workflow import WorkflowFailed

    runner = LocalRunner(project)
    try:
        result = runner.run_workflow(name, parse_data(data), sleep=sleep)
    except WorkflowFailed as exc:
        fail(f"FAILED {exc.error}: {exc.cause}")
        if history:
            click.echo(json.dumps(exc.history, indent=2))
        sys.exit(2)
    finally:
        runner.close()
    ok("SUCCEEDED")
    click.echo(json.dumps(result["output"], indent=2, default=str))
    if history:
        click.echo(json.dumps(result["history"], indent=2))


@workflow.command("show")
@project_options
@click.argument("name")
@click.option("--format", "fmt", default="auto", type=click.Choice(["auto", "asl", "gcp", "json"]), show_default=True)
def workflow_show(project, name: str, fmt: str) -> None:  # noqa: ANN001
    """Print the compiled workflow definition.

    
    Examples:
      pskit workflow show order
      pskit workflow show order --format gcp
    """
    from ..workflows import definition_dict
    from ..workflows.asl import compile_asl
    from ..workflows.gcp import compile_gcp

    wf = project.config.workflows.get(name)
    if wf is None:
        raise KeyError(f"unknown workflow {name!r}")
    definition = definition_dict(wf)
    if fmt == "auto":
        fmt = {"aws": "asl", "gcp": "gcp"}.get(project.config.provider.name, "json")
    if fmt == "asl":
        out: Any = compile_asl(definition, lambda f: f"<arn of {f}>", wf.description, wf.timeout or 0)
    elif fmt == "gcp":
        out, warnings = compile_gcp(definition, lambda f: f"<url of {f}>", name)
        for w in warnings:
            fail(f"warning: {w}")
    else:
        out = definition
    click.echo(json.dumps(out, indent=2))


def _deployed_record(project: Any) -> dict:
    from ..core.deployer import Deployer

    record = Deployer(project, echo=echo).load_record()
    if record is None:
        raise click.ClickException(
            f"{project.config.provider.name}/{project.config.provider.stage} is not deployed yet; run `pskit deploy`"
        )
    return record


def _remote(call: Callable[[], Any]) -> Any:
    """Run a control-API / cloud-SDK call and turn its failures into CLI errors."""
    from ..core.executions import ExecutionError

    try:
        return call()
    except (ExecutionError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None


def _execution_backend(project: Any) -> Any:
    from ..core.executions import backend_for

    return backend_for(_deployed_record(project), project.root)


def _print_execution_line(item: dict) -> None:
    status = str(item.get("status") or "?")
    color = {"SUCCEEDED": "green", "RUNNING": "cyan"}.get(status, "red")
    started = str(item.get("started_at") or "")[:19].replace("T", " ")
    took = f"{item['duration_ms']:.0f} ms" if isinstance(item.get("duration_ms"), (int, float)) else ""
    current = f"@ {item['current_state']}" if status == "RUNNING" and item.get("current_state") else ""
    click.echo(
        f"{item.get('execution_id')}  {item.get('workflow', ''):<18} "
        + click.style(f"{status:<10}", fg=color)
        + f" {started:<19} {took:>10} {current}"
    )


@workflow.command("start")
@project_options
@click.argument("name")
@click.option("-i", "--input", "data", default=None, help="JSON input or @file.json.")
@click.option("--wait", is_flag=True, help="Wait for the result (on-prem; native engines return at once).")
@click.option("--json", "as_json", is_flag=True)
def workflow_start(project, name: str, data: Optional[str], wait: bool, as_json: bool) -> None:  # noqa: ANN001
    """Start a deployed workflow and print its execution id.

    
    Examples:
      pskit workflow start order -i '{"id": 1}'
      pskit workflow start order -i @input.json --wait --json
    """
    from ..local.runner import parse_data

    backend = _execution_backend(project)
    result = _remote(lambda: backend.start(name, parse_data(data), wait=wait))
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    ok(f"execution_id: {result.get('execution_id')}")
    echo(f"status:       {result.get('status')}")
    if result.get("console"):
        echo(f"console:      {result['console']}")
    if "output" in result and result.get("status") == "SUCCEEDED":
        click.echo(json.dumps(result["output"], indent=2, default=str))
    elif result.get("error"):
        fail(f"{result.get('error')}: {result.get('cause') or ''}")
    if result.get("status") == "RUNNING":
        echo(f"Follow it with: pskit workflow status {result.get('execution_id')} --watch")


@workflow.command("executions")
@project_options
@click.argument("name", required=False)
@click.option("--status", default=None, help="RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED or ABORTED.")
@click.option("-n", "--limit", default=20, show_default=True)
@click.option("--offset", default=0, show_default=True, help="Skip this many (on-prem).")
@click.option("--token", default=None, help="Next-page token printed by a previous call (AWS/GCP).")
@click.option("--json", "as_json", is_flag=True)
def workflow_executions(  # noqa: ANN001
    project, name: Optional[str], status: Optional[str], limit: int, offset: int, token: Optional[str], as_json: bool
) -> None:
    """List executions of the deployed workflows (newest first).

    
    Examples:
      pskit workflow executions
      pskit workflow executions order --status FAILED -n 50
    """
    backend = _execution_backend(project)
    result = _remote(lambda: backend.list(workflow=name, status=status, limit=limit, offset=offset, token=token))
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    if not result.get("supported", True):
        raise click.ClickException(result.get("reason") or "execution history is not available here")
    items = result.get("items") or []
    if not items:
        echo("No executions yet.")
    for item in items:
        _print_execution_line(item)
    total = result.get("total")
    if isinstance(total, int) and total > offset + len(items):
        echo(f"... {total - offset - len(items)} more (use --offset {offset + len(items)})")
    elif result.get("next_token"):
        echo(f"... more (use --token {result['next_token']})")


@workflow.command("status")
@project_options
@click.argument("execution_id")
@click.option("-w", "--workflow", "name", default=None, help="Workflow name (helps AWS/GCP resolve the id).")
@click.option("--history", is_flag=True, help="Print the state history.")
@click.option("--watch", is_flag=True, help="Poll until the execution finishes.")
@click.option("--json", "as_json", is_flag=True)
def workflow_status(  # noqa: ANN001
    project, execution_id: str, name: Optional[str], history: bool, watch: bool, as_json: bool
) -> None:
    """Show one execution: status, current state, output or error.

    
    Examples:
      pskit workflow status <execution-id>
      pskit workflow status <execution-id> -w order --history --watch
    """
    import time

    backend = _execution_backend(project)
    record = _remote(lambda: backend.get(execution_id, name))
    while watch and record.get("status") == "RUNNING":
        if not as_json:
            echo(f"RUNNING @ {record.get('current_state') or '-'}")
        time.sleep(2)
        record = _remote(lambda: backend.get(execution_id, name))
    if as_json:
        click.echo(json.dumps(record, indent=2, default=str))
        return
    _print_execution_line(record)
    if record.get("console"):
        echo(f"console: {record['console']}")
    if record.get("status") == "SUCCEEDED":
        click.echo(json.dumps(record.get("output"), indent=2, default=str))
    elif record.get("error") or record.get("cause"):
        fail(f"{record.get('error')}: {record.get('cause') or ''}")
    if history:
        for step in record.get("history") or []:
            extra = f" ({step['error']})" if step.get("error") else ""
            echo(f"  {str(step.get('ts') or '')[11:23]}  {step.get('event', ''):<6} {step.get('state')}{extra}")


@workflow.command("cancel")
@project_options
@click.argument("execution_id")
@click.option("-w", "--workflow", "name", default=None, help="Workflow name (AWS/GCP).")
def workflow_cancel(project, execution_id: str, name: Optional[str]) -> None:  # noqa: ANN001
    """Cancel a running execution (stops before its next state).

    
    Examples:
      pskit workflow cancel <execution-id>
      pskit workflow cancel <execution-id> -w order
    """
    backend = _execution_backend(project)
    record = _remote(lambda: backend.cancel(execution_id, name))
    _print_execution_line(record)


# ----------------------------------------------------------------------------- logs
@cli.command()
@project_options
@click.option("-f", "--follow", is_flag=True, help="Keep printing new entries.")
@click.option("-n", "--lines", default=100, show_default=True, help="How many recent entries to show first.")
@click.option("--function", "function", default=None, help="Only this function.")
@click.option("--level", default=None, help="Minimum level: DEBUG, INFO, WARNING, ERROR.")
@click.option(
    "--source",
    default=None,
    type=click.Choice(["function", "host", "stdout", "stderr", "invocation"]),
    help="Only one kind of entry.",
)
@click.option("-q", "--grep", "text", default=None, help="Only entries containing this text.")
@click.option("--json", "as_json", is_flag=True, help="One JSON object per line.")
@click.option("--streams", "list_streams", is_flag=True, help="List log streams (per function / container) instead.")
@click.option("--delete", "delete_ids", multiple=True, metavar="STREAM", help="Delete a log stream (repeatable).")
@click.option("--delete-all", is_flag=True, help="Delete every log stream (of --function, or of all functions).")
@click.option("-y", "--yes", is_flag=True, help="Don't ask for confirmation before deleting.")
def logs(  # noqa: ANN001
    project,
    follow: bool,
    lines: int,
    function: Optional[str],
    level: Optional[str],
    source: Optional[str],
    text: Optional[str],
    as_json: bool,
    list_streams: bool,
    delete_ids: tuple,
    delete_all: bool,
    yes: bool,
) -> None:
    """Show, list or delete logs of a deployment.

    \b
    On premises: the host's buffer (handlers, print output, invocations).
    AWS: CloudWatch Logs (needs boto3). GCP: Cloud Logging (needs
    google-cloud-logging, read only). Azure: Log Analytics when a
    `log_analytics_workspace_id` output exists. Elsewhere: use the console
    link from `pskit info`.
    """
    import time

    from ..core.cloud_logs import LogError, log_backend

    record = _deployed_record(project)
    backend = log_backend(record, project.root)
    if not backend.supported:
        raise click.ClickException(backend.reason)

    def call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except LogError as exc:
            raise click.ClickException(str(exc)) from None
        except KeyError as exc:
            raise click.ClickException(str(exc.args[0] if exc.args else exc)) from None
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from None

    if delete_ids or delete_all:
        if not backend.can_delete:
            raise click.ClickException(backend.reason or "deleting logs is not supported for this deployment")
        what = (
            f"{len(delete_ids)} log stream(s)"
            if delete_ids
            else (f"every log stream of {function}" if function else "every log stream of every function")
        )
        where = f"{project.config.provider.name}/{project.config.provider.stage}"
        if not yes and not click.confirm(f"Delete {what} of {where}? This cannot be undone", default=False):
            raise click.Abort()
        result = call(lambda: backend.delete(ids=list(delete_ids) or None, function=function, all=delete_all and not function))
        ok(f"Deleted {result.get('deleted', 0)} log stream(s)" if backend.mode == "time" else f"Deleted {result.get('deleted', 0)} entries")
        for error in result.get("errors") or []:
            fail(f"  {error}")
        return

    if list_streams:
        page = call(lambda: backend.streams(function))
        items = page.get("items") or []
        if as_json:
            for item in items:
                click.echo(json.dumps(item, default=str))
            return
        if not items:
            echo("No log streams.")
            return
        for item in items:
            last = str(item.get("last") or "")[:19].replace("T", " ")
            size = item.get("count") if item.get("count") is not None else item.get("bytes")
            echo(f"{last:<20} {str(item.get('function') or 'host'):<24} {size if size is not None else '':>10}  {item.get('id')}")
        return

    def fetch(since: int, limit: int) -> dict:
        return dict(
            call(lambda: backend.query(since=since, limit=limit, level=level, function=function, q=text, source=source))
        )

    colors = {"ERROR": "red", "CRITICAL": "red", "WARNING": "yellow", "DEBUG": "bright_black"}

    def show(entries: list) -> None:
        for entry in entries:
            if as_json:
                click.echo(json.dumps(entry, default=str))
                continue
            who = entry.get("function") or entry.get("source")
            stamp = str(entry.get("ts") or "")[:23].replace("T", " ")
            click.echo(
                click.style(f"{stamp} {entry.get('level', ''):<7}", fg=colors.get(entry.get("level", "")))
                + f" [{who}] {entry.get('message', '')}"
            )

    page = fetch(0, lines)
    show(page.get("entries") or [])
    cursor = int(page.get("next") or 0)
    while follow:
        try:
            time.sleep(1 if backend.mode == "seq" else 3)
            page = fetch(cursor, 500)
        except KeyboardInterrupt:
            break
        if page.get("gap"):
            fail("... some entries were dropped from the host's buffer ...")
        show(page.get("entries") or [])
        cursor = int(page.get("next") or cursor)


# ----------------------------------------------------------------------------- providers / terraform
@cli.command()
@click.option("--json", "as_json", is_flag=True)
def providers(as_json: bool) -> None:
    """List supported clouds and what each supports.

    
    Examples:
      pskit providers
      pskit providers --json
    """
    from ..providers.registry import available, get_provider

    rows = []
    for name in available():
        try:
            rows.append(get_provider(name).describe())
        except Exception as exc:  # noqa: BLE001 - a broken plug-in must not hide the others
            rows.append({"name": name, "tier": "unavailable", "error": str(exc)})
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    cols = ["http", "schedule", "queue", "storage", "topic", "workflow_native"]
    echo(f"{'provider':<14}{'tier':<14}" + "".join(f"{c.replace('workflow_native', 'workflows'):<11}" for c in cols))
    for r in rows:
        caps = set(r.get("capabilities", []))
        line = f"{r['name']:<14}{r.get('tier', ''):<14}"
        for c in cols:
            if c == "workflow_native":
                line += f"{'native' if c in caps else 'embedded':<11}"
            else:
                line += f"{'yes' if c in caps else '-':<11}"
        echo(line)


@cli.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@project_options
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def tf(project, args: tuple) -> None:  # noqa: ANN001
    """Run any terraform command in the generated workspace.

    
    Examples:
      pskit tf -- state list
      pskit tf -s prod -- output -json
    """
    from ..terraform.runner import TerraformRunner

    if not (project.work_dir / "main.tf.json").is_file():
        raise click.ClickException("nothing generated yet; run `pskit package` first")
    runner = TerraformRunner(project.work_dir, binary=project.config.provider.terraform.binary)
    sys.exit(runner.passthrough(list(args)))


def main(argv: Optional[list] = None) -> None:
    os.environ.setdefault("PSKIT_LOG_FORMAT", "text")
    cli.main(args=argv, prog_name="pskit")


if __name__ == "__main__":  # pragma: no cover
    main()
