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
from ..config.schema import PROVIDERS

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
    @click.option("-v", "--verbose", is_flag=True, help="Show tracebacks on errors.")
    @functools.wraps(fn)
    def wrapper(
        *args: Any,
        config_path: Optional[str],
        provider: Optional[str],
        stage: Optional[str],
        region: Optional[str],
        verbose: bool,
        **kwargs: Any,
    ) -> Any:
        from ..core.project import Project

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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="py-serverless-kit")
def cli() -> None:
    """py-serverless-kit: write Python functions once, deploy them to any cloud.

    \b
    Typical flow:
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
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def init(directory: str, name: Optional[str], provider: str, template: str, force: bool) -> None:
    """Scaffold a new project."""
    import re

    from .templates import render

    target = Path(directory).resolve()
    service = name or re.sub(r"[^a-z0-9-]", "-", target.name.lower()).strip("-") or "my-service"
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", service):
        raise click.BadParameter(f"invalid service name {service!r}; use --name", param_hint="--name")
    files = render(template, service, provider)
    target.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = target / rel
        if path.exists() and not force:
            echo(f"  skip   {rel} (exists)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, "utf-8")
        echo(f"  create {rel}")
    ok(f"\nCreated service '{service}' for {provider} in {target}")
    echo("Next steps:")
    if directory != ".":
        echo(f"  cd {directory}")
    echo("  pskit dev            # local server with hot reload")
    echo("  pskit deploy         # deploy with terraform")


# ----------------------------------------------------------------------------- validate
@cli.command()
@project_options
def validate(project) -> None:  # noqa: ANN001
    """Validate the config against the selected provider."""
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
    """Build the artifact and generate Terraform (no cloud calls)."""
    from ..core.deployer import Deployer

    result = Deployer(project, echo=echo).package(skip_deps=skip_deps)
    ok(f"Packaged into {project.work_dir} ({result.resource_count} terraform resources)")


@cli.command()
@project_options
@click.option("--skip-deps", is_flag=True)
@click.option("--destroy", is_flag=True, help="Plan a destroy instead.")
def plan(project, skip_deps: bool, destroy: bool) -> None:  # noqa: ANN001
    """Show the terraform plan."""
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
    """Package and deploy with terraform apply."""
    from ..core.deployer import Deployer
    from ..dashboard.generator import write_dashboard

    record = Deployer(project, echo=echo).deploy(skip_deps=skip_deps, auto_approve=not confirm, upgrade=upgrade)
    ok(f"\nDeployed {record['service']} ({record['stage']}) to {record['provider_display']} {record['region']}")
    _print_record(record)
    if not no_dashboard:
        path = write_dashboard(project.root)
        echo(f"\nDashboard: {path.as_uri()}")


@cli.command()
@project_options
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
def remove(project, yes: bool) -> None:  # noqa: ANN001
    """Destroy every resource of this service/stage."""
    from ..core.deployer import Deployer
    from ..dashboard.generator import write_dashboard

    cfg = project.config
    if not yes:
        click.confirm(
            f"Destroy ALL resources of '{cfg.service}' stage '{cfg.provider.stage}' on {project.provider.display_name}?",
            abort=True,
        )
    Deployer(project, echo=echo).remove(auto_approve=True)
    write_dashboard(project.root)
    ok("Removed.")


# ----------------------------------------------------------------------------- info / dashboard
def _print_record(record: dict) -> None:
    if record.get("endpoint"):
        echo(f"endpoint: {record['endpoint']}")
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
    """Show what is deployed (functions, URLs, ids)."""
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
@click.option("--serve", is_flag=True, help="Serve the dashboard over HTTP.")
@click.option("--port", default=8765, show_default=True)
@click.option("--open", "open_browser", is_flag=True, help="Open in the browser.")
def dashboard(directory: str, output: Optional[str], serve: bool, port: int, open_browser: bool) -> None:
    """Generate (and optionally serve) the deployment dashboard."""
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
        import http.server
        import socketserver

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(path.parent))
        with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
            url = f"http://127.0.0.1:{port}/{path.name}"
            echo(f"Serving on {url} (Ctrl+C to stop)")
            if open_browser:
                webbrowser.open(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
    elif open_browser:
        webbrowser.open(path.as_uri())


# ----------------------------------------------------------------------------- local
@cli.command()
@project_options
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=3000, show_default=True)
@click.option("--env-file", default=".env", show_default=True)
def dev(project, host: str, port: int, env_file: str) -> None:  # noqa: ANN001
    """Run a local HTTP server that routes to your handlers (hot reload)."""
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
    type=click.Choice(["http", "schedule", "queue", "storage", "topic", "invoke", "workflow"]),
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
    """Invoke a function locally with a synthetic event."""
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
    except Exception as exc:  # noqa: BLE001 - user code failed; show it like the cloud would
        traceback.print_exc()
        fail(f"{function} raised {type(exc).__name__}: {exc}")
        sys.exit(1)
    click.echo(json.dumps(response_to_dict(result), indent=2, default=str))


# ----------------------------------------------------------------------------- workflows
@cli.group()
def workflow() -> None:
    """Run or inspect workflows (state machines)."""


@workflow.command("run")
@project_options
@click.argument("name")
@click.option("-i", "--input", "data", default=None, help="JSON input or @file.json.")
@click.option("--sleep", is_flag=True, help="Honour wait states (default: skip waiting).")
@click.option("--history", is_flag=True, help="Print the execution history.")
def workflow_run(project, name: str, data: Optional[str], sleep: bool, history: bool) -> None:  # noqa: ANN001
    """Run a workflow locally on the embedded engine."""
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
    ok("SUCCEEDED")
    click.echo(json.dumps(result["output"], indent=2, default=str))
    if history:
        click.echo(json.dumps(result["history"], indent=2))


@workflow.command("show")
@project_options
@click.argument("name")
@click.option("--format", "fmt", default="auto", type=click.Choice(["auto", "asl", "gcp", "json"]), show_default=True)
def workflow_show(project, name: str, fmt: str) -> None:  # noqa: ANN001
    """Print the compiled workflow definition."""
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


# ----------------------------------------------------------------------------- providers / terraform
@cli.command()
@click.option("--json", "as_json", is_flag=True)
def providers(as_json: bool) -> None:
    """List supported clouds and what each supports."""
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
    """Run any terraform command in the generated workspace, e.g. `pskit tf -- state list`."""
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
