"""End-to-end "how to use this resource" guide, for one resource of the current project.

One Markdown document per resource: what it is, which library to install for Python and
Node.js and how pskit packages it, the ``pskit.yml`` that declares it and its trigger,
handler code (publish / consume / connect), how to test it locally and once deployed,
provider notes (permissions, secrets, networks) and troubleshooting. The studio shows it
behind *How to use*; ``pskit resources usage KEY`` prints it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import yaml

from .resource_code import _consume_js, _consume_py, code_for

# Object stores on the other clouds speak the S3 API (see runtime/drivers S3 presets).
S3_COMPATIBLE = {
    "alibaba": ("oss", "https://oss-{region}.aliyuncs.com", False),
    "tencent": ("cos", "https://cos.{region}.myqcloud.com", False),
    "huawei": ("obs", "https://obs.{region}.myhuaweicloud.com", False),
    "oracle": ("oci", "https://{namespace}.compat.objectstorage.{region}.oraclecloud.com", True),
    "ibm": ("ibm_cos", "https://s3.{region}.cloud-object-storage.appdomain.cloud", True),
    "digitalocean": ("spaces", "https://{region}.digitaloceanspaces.com", False),
    "cloudflare": ("r2", "https://{account_id}.r2.cloudflarestorage.com", True),
}

_DEPS = {
    "onprem": "installed on the host by `pskit deploy` (vendored into the bundle)",
    "aws": "pip-installed into the Lambda zip as Linux wheels by `pskit deploy`",
    "alibaba": "pip-installed into the zip by `pskit deploy`",
    "tencent": "pip-installed into the zip by `pskit deploy`",
    "huawei": "pip-installed into the zip by `pskit deploy`",
    "ibm": "pip-installed into the zip by `pskit deploy`",
    "digitalocean": "pip-installed into the zip by `pskit deploy`",
    "gcp": "installed by Cloud Build from the bundled requirements.txt",
    "azure": "installed by the remote (Oryx) build from the bundled requirements.txt",
    "oracle": "installed by the remote build from the bundled requirements.txt",
    "cloudflare": "resolved by the Workers Python runtime from requirements.txt (pure-Python packages only)",
}
_NODE_DEPS = {
    "aws": "installed into the zip (`npm install --omit=dev`)",
    "gcp": "installed by Cloud Build from package.json",
    "azure": "installed by the remote build from package.json",
    "onprem": "installed into the bundle (`npm install --omit=dev`)",
}


def _fence(lang: str, text: str) -> str:
    return f"```{lang}\n{text.rstrip()}\n```\n"


def _guide_handler_section(cloud: str, kind: str) -> Optional[str]:
    """The "Use it in a handler" section of docs/resource-guides/<cloud>/<kind>.md, if any."""
    from ..studio.guides import guides_dir

    base = guides_dir()
    if base is None:
        return None
    path = base / cloud / f"{kind}.md"
    if not path.is_file():
        return None
    text = path.read_text("utf-8")
    match = re.search(r"^## (?:\d+\. )?Use it in a handler\s*$(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def _s3_code(key: str, endpoint: str, region: str, path_style: bool) -> Dict[str, Any]:
    addressing = ', config=Config(s3={"addressing_style": "path"})' if path_style else ""
    py = (
        "import boto3\n"
        + ("from botocore.config import Config\n" if path_style else "")
        + "\nfrom py_serverless_kit import BatchResult, resource\n\n\n"
        f"def client(ctx):\n"
        f'    r = resource("{key}")\n'
        "    # HMAC keys: names of variables set in `environment:` (never the values in pskit.yml)\n"
        "    return boto3.client(\n"
        '        "s3",\n'
        f'        endpoint_url=r.get("endpoint", "{endpoint}"),\n'
        f'        region_name=r.get("region", "{region}"),\n'
        '        aws_access_key_id=ctx.env[r.get("access_key_env", "S3_ACCESS_KEY")],\n'
        f'        aws_secret_access_key=ctx.env[r.get("secret_key_env", "S3_SECRET_KEY")]{addressing},\n'
        "    )\n\n\n"
        f"def send_to_{key.replace('-', '_')}(event, ctx):\n"
        f'    client(ctx).put_object(Bucket=resource("{key}").native_name, Key="in/hello.txt", Body=b"hello")\n'
        '    return {"written": "in/hello.txt"}\n\n\n'
        f"def list_{key.replace('-', '_')}(event, ctx):\n"
        f'    out = client(ctx).list_objects_v2(Bucket=resource("{key}").native_name, MaxKeys=20)\n'
        '    return [o["Key"] for o in out.get("Contents", [])]\n'
    )
    js = (
        'const { S3Client, PutObjectCommand, ListObjectsV2Command } = require("@aws-sdk/client-s3");\n'
        'const { resource } = require("pskit");\n\n'
        "function client() {\n"
        f'  const r = resource("{key}");\n'
        "  return new S3Client({\n"
        f'    endpoint: r.get("endpoint", "{endpoint}"),\n'
        f'    region: r.get("region", "{region}"),\n'
        f"    forcePathStyle: {'true' if path_style else 'false'},\n"
        "    credentials: {\n"
        '      accessKeyId: process.env[r.get("access_key_env", "S3_ACCESS_KEY")],\n'
        '      secretAccessKey: process.env[r.get("secret_key_env", "S3_SECRET_KEY")],\n'
        "    },\n"
        "  });\n"
        "}\n\n"
        f"exports.sendTo{key.title().replace('-', '').replace('_', '')} = async () => {{\n"
        f'  await client().send(new PutObjectCommand({{ Bucket: resource("{key}").nativeName, Key: "in/hello.txt", Body: "hello" }}));\n'
        '  return { written: "in/hello.txt" };\n'
        "};\n\n"
        f"exports.list{key.title().replace('-', '').replace('_', '')} = async () => {{\n"
        f'  const out = await client().send(new ListObjectsV2Command({{ Bucket: resource("{key}").nativeName, MaxKeys: 20 }}));\n'
        "  return (out.Contents || []).map((o) => o.Key);\n"
        "};\n"
    )
    return {
        "python": py,
        "node": js,
        "requirements": {"python": ["boto3"], "node": ["@aws-sdk/client-s3"]},
        "note": "The bucket speaks the S3 API: the AWS SDK works with the provider's endpoint and HMAC keys.",
        "flavor": "storage",
    }


def _code(provider: str, key: str, res: Any, service: Optional[str], region: str) -> Dict[str, Any]:
    if res.type == "storage" and provider in S3_COMPATIBLE:
        _preset, template, path_style = S3_COMPATIBLE[provider]
        conn = {str(k).lower(): str(v) for k, v in res.connection.items()}
        values = {"region": conn.get("region") or region or "<region>", "namespace": conn.get("namespace", "<namespace>"),
                  "account_id": conn.get("account_id", "<account_id>")}
        endpoint = conn.get("endpoint") or template.format(**values)
        return _s3_code(key, endpoint, values["region"], path_style)
    return code_for(provider, key, res, service)


def _trigger_yaml(kind: str, key: str) -> str:
    extras: Dict[str, Dict[str, Any]] = {"queue": {"batch_size": 10, "max_receive": 3}, "storage": {"events": ["created"]}, "topic": {}}
    extra = extras[kind]
    return yaml.safe_dump({"functions": {"worker": {"handler": "app.handlers.on_" + key.replace("-", "_"),
                                                    "events": [{kind: {"resource": key, **extra}}]}}}, sort_keys=False)


def _resource_yaml(key: str, raw_res: Dict[str, Any]) -> str:
    return yaml.safe_dump({"resources": {key: raw_res}}, sort_keys=False)


def usage(project: Any, record: Optional[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """``{"key", "title", "markdown", "code", "requirements"}`` for resource ``key``."""
    from .onprem_services import load_state
    from .testing import TriggerError, resource_guide

    cfg = project.config
    res = cfg.resources.get(key)
    if res is None:
        known = ", ".join(sorted(cfg.resources)) or "<none>"
        raise TriggerError(f"unknown resource {key!r}; known: {known}")
    provider = cfg.provider.name
    stage = cfg.provider.stage
    region = project.region or ""
    kind = res.type
    native = res.native_name(key)
    service = (load_state(project.root)["services"].get(key) or {}).get("service") if provider == "onprem" else None
    code = _code(provider, key, res, service, region)
    guide: Dict[str, Any] = next((g for g in resource_guide(project, record)["resources"] if g["key"] == key), {})
    driver = res.options.get("driver") if provider == "onprem" else None
    driver_name = driver.get("use") if isinstance(driver, dict) else driver
    bound = cfg.resource_functions(key)
    consumers = [fn for fn, ev in ((n, e) for n, f in cfg.functions.items() for e in f.events)
                 if ev.type == kind and getattr(ev.config, "resource", None) == key]
    common = f" -p {provider} -s {stage}"
    reqs_file = cfg.package.requirements or "requirements.txt"
    node = cfg.language == "node"
    eventful = kind in ("queue", "storage", "topic")
    req_py = list(code["requirements"]["python"])
    req_js = list(code["requirements"]["node"])
    # Queues and topics on clouds without a portable publish call: the cloud guide's SDK example.
    guide_py = None
    if kind in ("queue", "topic") and provider not in ("onprem", "aws", "gcp", "azure"):
        guide_py = _guide_handler_section(provider, kind)
    if guide_py:
        for found in re.findall(r"`requirements\.txt`:\s*`([^`]+)`", guide_py):
            if found not in req_py:
                req_py.append(found)
    md: List[str] = []

    # ------------------------------------------------------------------ summary
    md.append(f"# How to use `{key}`\n")
    rows = [
        ("Type", f"`{kind}`" + (f" ({code['flavor']})" if code.get("flavor") not in (None, kind) else "")),
        ("Provider / stage", f"`{provider}` / `{stage}`"),
        ("Native name", f"`{native}`"),
        ("Mode", "external (pskit never creates or deletes it)" if res.external else "managed (created by `pskit deploy`)"),
    ]
    if driver_name:
        rows.append(("On-prem driver", f"`{driver_name}`"))
    if service:
        rows.append(("Created with", f"one-click `{service}` service (see *On-prem services*)"))
    rows.append(("Bound to", ", ".join(f"`{f}`" for f in bound) or "no functions"))
    if eventful:
        rows.append(("Triggers", ", ".join(f"`{f}`" for f in consumers) or "none yet (step 2 adds one)"))
    md.append("| | |\n|---|---|\n" + "".join(f"| {a} | {b} |\n" for a, b in rows))
    md.append(
        "Every bound function gets the connection as environment variables and reads it with "
        f'`resource("{key}")` (Python) or `resource("{key}")` from `pskit` (Node.js):\n'
    )
    md.append(_fence("text", "\n".join(guide.get("env") or [])))

    # ------------------------------------------------------------------ install
    md.append("## 1. Install the libraries\n")
    if req_py:
        md.append(f"**Python**: add to `{reqs_file}`, then install locally for `pskit invoke` / `pskit dev`:\n")
        md.append(_fence("text", "\n".join(req_py)))
        md.append(_fence("bash", f"pip install -r {reqs_file}"))
    else:
        md.append("**Python**: nothing to install; `py_serverless_kit` is bundled with every deployment.\n")
    md.append(f"On {provider} the dependencies are {_DEPS.get(provider, 'packaged by `pskit deploy`')}.\n")
    if req_js:
        md.append("**Node.js**: add to `package.json`:\n")
        md.append(_fence("bash", "npm install " + " ".join(req_js)))
    else:
        md.append("**Node.js**: nothing to install; the `pskit` package is bundled.\n")
    md.append(f"Node.js dependencies are {_NODE_DEPS.get(provider, 'bundled from package.json')}.\n")
    if driver_name == "custom":
        md.append(
            f"The on-prem driver class (`{res.options.get('class')}`) runs inside the host; its client library must be in "
            f"`{reqs_file}` too.\n"
        )

    # ------------------------------------------------------------------ declare
    md.append("## 2. Declare it in pskit.yml\n")
    raw_res: Dict[str, Any] = {"type": kind}
    if res.external:
        raw_res["external"] = True
    if res.name:
        raw_res["name"] = res.name
    if res.connection:
        raw_res["connection"] = dict(res.connection)
    if res.options:
        raw_res["options"] = dict(res.options)
    if res.functions is not None:
        raw_res["functions"] = list(res.functions)
    md.append("This is the resource as pskit resolved it for this provider and stage:\n")
    md.append(_fence("yaml", _resource_yaml(key, raw_res)))
    if eventful:
        md.append("Run a function for every " + {"queue": "message", "storage": "new object", "topic": "message"}[kind] + ":\n")
        md.append(_fence("yaml", _trigger_yaml(kind, key)))
    secret_keys = [k for k in list(res.connection) + list(res.options) if str(k).endswith("_env")]
    if secret_keys or kind in ("database", "cache", "custom"):
        if provider == "onprem":
            env_file = cfg.provider.options.get("env_file")
            md.append(
                "**Secrets.** A `<name>_env` value is the *name* of a variable. On premises put the values in "
                f"`{env_file or '.env.onprem'}` and set `provider.options.env_file` to it: the host loads it at start "
                "(also over SSH and in Docker) and nothing reaches Terraform state.\n"
            )
        else:
            md.append(
                "**Secrets.** A `<name>_env` value is the *name* of a variable. Give the function that variable with "
                "`environment: {NAME: ${env:LOCAL_NAME}}` (resolved on the deploying machine) or your cloud's secret "
                "store, and read it with `ctx.env[...]` / `process.env[...]`.\n"
            )

    # ------------------------------------------------------------------ code
    md.append("## 3. Use it in your code\n")
    md.append(code.get("note", "") + "\n")
    md.append("### Python (`app/handlers.py`)\n")
    if guide_py:
        md.append(
            f"Publishing on {provider} uses the provider SDK (from the {provider} {kind} guide; use your key "
            f'`resource("{key}")` where the example names another):\n\n{guide_py}\n'
        )
        md.append("The trigger consumer is portable:\n")
        md.append(_fence("python", "from py_serverless_kit import BatchResult, resource\n" + _consume_py(kind, key)))
    else:
        md.append(_fence("python", code["python"]))
    md.append("### Node.js (`app/handlers.js`)\n")
    if guide_py:
        md.append(
            f"Publish with the {provider} SDK for Node.js or its HTTP API, with the same values as the Python example. "
            "The trigger consumer is portable:\n"
        )
        md.append(_fence("js", 'const { BatchResult, resource } = require("pskit");' + _consume_js(kind, key)))
    else:
        md.append(_fence("js", code["node"]))
    if not node:
        md.append("_This project uses Python; the Node.js version is for a `runtime: nodejs20` project._\n")

    # ------------------------------------------------------------------ local
    md.append("## 4. Test it locally\n")
    fn = (consumers or bound or ["worker"])[0]
    if eventful:
        sample = {"queue": '[{"order_id": 1}, {"order_id": 2}]',
                  "topic": '[{"event": "created", "id": 1}]',
                  "storage": json.dumps({"bucket": native, "key": "in/a.csv", "size": 3, "action": "created"})}[kind]
        md.append(f"Run the consumer with a fake {kind} event (no cloud needed):\n")
        md.append(_fence("bash", f"pskit invoke -f {fn} -t {kind} -d '{sample}'"))
        md.append("Or start the local server and post events to it:\n")
        md.append(_fence("bash", f"pskit dev\ncurl -X POST 'localhost:3000/__pskit/invoke/{fn}?type={kind}' -d '{sample}'"))
        md.append(
            "Publishing code runs against the real service: locally it needs the same credentials "
            + ("(put them in `.env`; `pskit invoke` / `pskit dev` load it)." if provider != "onprem" else
               "(on premises the host's publish helpers need the deployed host; test publishing after step 5).")
            + "\n"
        )
    else:
        md.append("Call the connection function directly. `pskit invoke` / `pskit dev` load `.env`, so put the secret "
                  "variables there for a local run:\n")
        md.append(_fence("bash", f"pskit invoke -f {fn}"))
        md.append("Or check only that the service answers, from this machine:\n")
        md.append(_fence("bash", f"pskit resources check {key}{common}"))

    # ------------------------------------------------------------------ deployed
    md.append("## 5. Deploy and test for real\n")
    steps = [f"pskit deploy{common}"]
    if eventful:
        steps += (guide.get("remote") or {}).get("pskit") or [f"pskit resources send {key}{common}"]
    if (guide.get("check") or {}).get("supported") or not eventful:
        steps.append(f"pskit resources check {key}{common}")
    steps.append(f"pskit logs -f --function {fn}{common}")
    md.append(_fence("bash", "\n".join(steps)))
    cli = (guide.get("remote") or {}).get("cli") or []
    if cli:
        md.append(f"With the {provider} CLI instead:\n")
        md.append(_fence("bash", "\n".join(cli)))
    console = (guide.get("remote") or {}).get("console") or [] if provider != "onprem" else []
    if console:
        md.append("In the console:\n\n" + "".join(f"{i}. {s}\n" for i, s in enumerate(console, 1)))
    md.append(
        "In the studio: **Resources > Test a resource** sends a test message / object, or runs **Test connection**; "
        "the **Logs** page shows what the function did.\n"
    )

    # ------------------------------------------------------------------ provider notes
    notes = _provider_notes(provider, kind, res, driver_name)
    if notes:
        md.append(f"## 6. {provider} notes\n\n" + "".join(f"- {n}\n" for n in notes))

    # ------------------------------------------------------------------ troubleshooting
    md.append("## 7. Troubleshooting\n")
    md.append("| Symptom | Fix |\n|---|---|\n" + "".join(f"| {a} | {b} |\n" for a, b in _troubles(provider, kind, driver_name)))
    md.append(
        f"\nStep by step for creating this kind of service: `docs/resource-guides/{provider}/{kind}.md` "
        "(studio: **Resources > Guides**).\n"
    )
    return {
        "key": key,
        "title": f"How to use {key}",
        "markdown": "\n".join(md),
        "code": code,
        "requirements": {"python": req_py, "node": req_js},
    }


def _provider_notes(provider: str, kind: str, res: Any, driver: Optional[str]) -> List[str]:
    notes: List[str] = []
    if provider == "aws":
        notes.append("Each function role gets a `pskit-resources` policy for the storage, queue and topic resources "
                     "bound to it (send/receive, put/get/list, publish). With your own `iam.role`, grant it yourself.")
        if res.external:
            notes.append("External resources are looked up by name with a Terraform data source; `connection.arn` "
                         "skips the lookup.")
    elif provider == "gcp":
        notes.append("Functions run as the service account pskit creates; grant it roles on external buckets / topics "
                     "(`roles/storage.objectAdmin`, `roles/pubsub.publisher`).")
    elif provider == "azure":
        notes.append("App settings belong to the whole Function App, so every function there sees every binding.")
        notes.append("Storage queues and blobs use the Function App's storage account (`AzureWebJobsStorage`); "
                     "Service Bus needs a connection string setting you add.")
    elif provider == "onprem":
        notes.append("The host watches the resource: queues are polled and leased, buckets listed every "
                     "`provider.options.poll_seconds`. Publish helpers go through the host, so the driver is honoured.")
        if driver and driver not in ("builtin", "directory"):
            notes.append(f"`{driver}` runs from the host's machine: the service must be reachable from there "
                         "(firewall / security group), not only from your laptop.")
        notes.append("Node.js handlers publish through the host's loopback address (`PSKIT_HOST_URL`).")
    elif provider in S3_COMPATIBLE and kind == "storage":
        notes.append("Create HMAC (S3-compatible) keys in the console and pass their variable names as "
                     "`connection.access_key_env` / `secret_key_env`.")
    else:
        notes.append(f"pskit only passes the connection values on {provider}; the service SDK does the calls.")
    if kind in ("database", "cache") and provider != "onprem":
        notes.append("Serverless functions reach private databases only through the provider's VPC / network "
                     "integration; a public endpoint must allow the function's outbound addresses.")
    return notes


def _troubles(provider: str, kind: str, driver: Optional[str]) -> List[tuple]:
    rows = [
        ("`resource 'x' is not bound to this function`", "add the function to the resource's `functions:` list, or remove the list"),
        ("`ModuleNotFoundError` / `Cannot find module`", "add the library to requirements.txt / package.json and redeploy"),
    ]
    if kind in ("queue", "storage", "topic"):
        rows.append(("the consumer never runs", f"the function needs `events: [{{{kind}: {{resource: <key>}}}}]`; check `pskit logs -f`"))
    if kind in ("database", "cache", "custom"):
        rows.append(("timeout / connection refused", "`pskit resources check <key>`; open the port to where the functions run"))
        rows.append(("`KeyError` on `ctx.env[...]`", "the `<name>_env` variable is not set where the function runs (see step 2)"))
    if provider == "aws":
        rows.append(("`AccessDenied`", "the role lacks the action: bind the resource to the function, or extend `iam.role`"))
    if provider == "onprem":
        rows.append(("`environment variable X ... is not set on the host`", "add it to `provider.options.env_file` and redeploy"))
        if driver == "sqs":
            rows.append(("`QueueDoesNotExist`", "redeploy (the sqs driver creates missing ElasticMQ queues); check `queue_url`"))
    return rows
