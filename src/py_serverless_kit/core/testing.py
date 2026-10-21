"""Test functions with real events: what to send, how, and sending it.

For every function and trigger this module knows

* a **sample payload** (also what ``pskit invoke`` takes locally),
* the **local** command (``pskit invoke``, runs the handler on this machine),
* how to fire the **deployed** trigger: ``pskit trigger`` (sent here with the
  cloud SDK or plain HTTP), the cloud's own CLI command, and the console steps.

``send`` delivers a real event to the deployed stage: an HTTP request, a queue
message, an uploaded object, a topic message or a direct invocation. It uses
the machine's normal credentials; nothing is stored.
"""

from __future__ import annotations

import json
import os
import shlex
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..runtime.adapters.base import envelope

# What the handler receives for each trigger type, as typed in the payload editor.
SAMPLES: Dict[str, Any] = {
    "http": {"hello": "world"},
    "schedule": {"report": "daily"},
    "queue": [{"order_id": 1, "action": "create"}],
    "storage": {"key": "test/hello.txt", "content": "hello from pskit"},
    "topic": {"message": {"event": "created", "id": 1}, "attributes": {"source": "pskit-test"}},
    "invoke": {"hello": "world"},
}
PAYLOAD_HELP: Dict[str, str] = {
    "http": "The JSON request body (GET requests send it as nothing; put query parameters in the path).",
    "schedule": "event.data the scheduled run receives (the schedule's `input`).",
    "queue": "One message, or a list of messages (each becomes one record).",
    "storage": '{"key": "...", "content": "..."}: the object to upload (deleted objects: {"key", "action": "deleted"}).',
    "topic": '{"message": ..., "attributes": {...}}: the published message.',
    "invoke": "event.data the function receives.",
}
_SDK = {
    "aws": "boto3 (pip install 'py-serverless-kit[aws]')",
    "gcp": "google-cloud-pubsub / google-cloud-storage",
    "azure": "azure-storage-queue / azure-storage-blob / azure-servicebus",
}


class TriggerError(Exception):
    """A test event could not be sent (unsupported, missing SDK or credentials, cloud error)."""


def _q(text: str) -> str:
    return shlex.quote(text)


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


# --------------------------------------------------------------------- model


def _triggers(project: Any, name: str) -> List[Dict[str, Any]]:
    fn = project.config.functions[name]
    out: List[Dict[str, Any]] = []
    for i, ev in enumerate(fn.events):
        c = ev.config
        item: Dict[str, Any] = {"index": i, "type": ev.type}
        if ev.type == "http":
            item.update(method=c.method if c.method != "ANY" else "POST", path=c.path, summary=f"{c.method} {c.path}")
        elif ev.type == "schedule":
            item.update(summary=f"rate({c.rate})" if c.rate else f"cron({c.cron})", input=c.input)
        elif ev.type == "queue":
            item.update(name=c.name, summary=f"queue {c.name}")
        elif ev.type == "storage":
            item.update(name=c.bucket, summary=f"bucket {c.bucket}", prefix=c.prefix or "", suffix=c.suffix or "")
        else:
            item.update(name=c.name, summary=f"topic {c.name}")
        out.append(item)
    out.append({"index": -1, "type": "invoke", "summary": "direct invoke (no trigger)"})
    return out


def _sample(trigger: Dict[str, Any]) -> Any:
    kind = trigger["type"]
    if kind == "schedule" and trigger.get("input") is not None:
        return trigger["input"]
    if kind == "storage":
        key = f"{trigger.get('prefix') or 'test/'}hello{trigger.get('suffix') or '.txt'}"
        return {"key": key, "content": "hello from pskit"}
    return SAMPLES[kind]


def _record_function(record: Optional[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for fn in (record or {}).get("functions") or []:
        if fn.get("name") == name:
            return dict(fn)
    return {}


def _http_url(fn_rec: Dict[str, Any], trigger: Dict[str, Any]) -> Optional[str]:
    for item in fn_rec.get("urls") or []:
        if item.get("method") in (trigger.get("method"), "ANY") and str(item.get("url", "")).endswith(trigger["path"]):
            return str(item["url"])
    base = fn_rec.get("url")
    return str(base) if base else None


def _onprem_endpoint(record: Optional[Dict[str, Any]], root: Path) -> Optional[str]:
    from .onprem_control import control_for

    if not record:
        return None
    control = control_for(record, root)
    return control.base_url if control is not None else None


# --------------------------------------------------------------------- how to test


def _local(name: str, trigger: Dict[str, Any], payload: Any, common: str) -> List[str]:
    kind = trigger["type"]
    cmd = f"pskit invoke -f {name}{common}"
    if trigger["index"] >= 0:
        cmd += f" -t {kind}"
    if kind == "http":
        cmd += f" --method {trigger['method']} --path {_q(trigger['path'])}"
        if trigger["method"] in ("GET", "HEAD", "DELETE"):
            return [cmd]
    if kind == "storage":
        payload = {"key": payload.get("key", "test/hello.txt"), "bucket": trigger.get("name"), "action": "created"}
    return [f"{cmd} -d {_q(_dumps(payload))}"]


def _cloud_cli(
    provider: str, region: str, name: str, fn_rec: Dict[str, Any], trigger: Dict[str, Any], payload: Any, extra: Dict[str, Any]
) -> List[str]:
    kind = trigger["type"]
    target = str(trigger.get("name") or "")
    phys = str(fn_rec.get("resource_name") or fn_rec.get("id") or name)
    if kind == "http":
        url = _http_url(fn_rec, trigger) or f"<function URL>{trigger['path']}"
        if trigger["method"] in ("GET", "HEAD", "DELETE"):
            return [f"curl -i -X {trigger['method']} {_q(url)}"]
        return [f"curl -i -X {trigger['method']} {_q(url)} -H 'content-type: application/json' -d {_q(_dumps(payload))}"]
    if kind in ("schedule", "invoke"):
        body = _dumps(envelope(kind, payload))
        if provider == "aws":
            return [
                f"aws lambda invoke --function-name {phys} --region {region} --cli-binary-format raw-in-base64-out "
                f"--payload {_q(body)} response.json && cat response.json"
            ]
        if provider == "onprem":
            return [_curl_onprem("POST", extra.get("endpoint"), f"/__pskit/invoke/{name}", body)]
        if provider == "tencent":
            return [f"tccli scf Invoke --FunctionName {phys} --Region {region} --ClientContext {_q(body)}"]
        if provider == "oracle":
            return [f"echo {_q(body)} | oci fn function invoke --function-id {fn_rec.get('id') or '<function OCID>'} --file - --body -"]
        if provider == "digitalocean":
            return [f"doctl serverless functions invoke {fn_rec.get('id') or phys} --param-file payload.json   # payload.json = {body}"]
        if fn_rec.get("url"):
            return [f"curl -i -X POST {_q(str(fn_rec['url']))} -H 'content-type: application/json' -d {_q(body)}"]
        return []
    if kind == "queue":
        body = _dumps(payload if not isinstance(payload, list) else (payload[0] if payload else {}))
        if provider == "aws":
            return [
                f"aws sqs send-message --region {region} --queue-url "
                f'"$(aws sqs get-queue-url --region {region} --queue-name {target} --query QueueUrl --output text)" '
                f"--message-body {_q(body)}"
            ]
        if provider == "gcp":
            return [f"gcloud pubsub topics publish {target} --message={_q(body)}{_gcp_project(extra)}"]
        if provider == "azure":
            return [
                f"az storage message put --queue-name {target} --account-name <storage-account> "
                f'--content "$(printf %s {_q(body)} | base64)"'
            ]
        if provider == "onprem":
            return [_curl_onprem("POST", extra.get("endpoint"), f"/__pskit/queues/{target}", body)]
        return []
    if kind == "topic":
        message = payload.get("message", payload) if isinstance(payload, dict) else payload
        body = message if isinstance(message, str) else _dumps(message)
        if provider == "aws":
            return [
                f"aws sns publish --region {region} --topic-arn "
                f'"arn:aws:sns:{region}:$(aws sts get-caller-identity --query Account --output text):{target}" '
                f"--message {_q(body)}"
            ]
        if provider == "gcp":
            return [f"gcloud pubsub topics publish {target} --message={_q(body)}{_gcp_project(extra)}"]
        if provider == "onprem":
            return [_curl_onprem("POST", extra.get("endpoint"), f"/__pskit/topics/{target}", _dumps(payload))]
        return []
    # storage
    key = str(payload.get("key", "test/hello.txt")) if isinstance(payload, dict) else "test/hello.txt"
    content = str(payload.get("content", "hello")) if isinstance(payload, dict) else "hello"
    write = f"printf %s {_q(content)} > hello.txt && "
    commands = {
        "aws": f"{write}aws s3 cp hello.txt s3://{target}/{key} --region {region}",
        "gcp": f"{write}gcloud storage cp hello.txt gs://{target}/{key}",
        "azure": f"{write}az storage blob upload --account-name <storage-account> --container-name {target} --name {key} --file hello.txt",
        "alibaba": f"{write}ossutil cp hello.txt oss://{target}/{key}",
        "tencent": f"{write}coscli cp hello.txt cos://{target}/{key}",
        "huawei": f"{write}obsutil cp hello.txt obs://{target}/{key}",
        "oracle": f"{write}oci os object put --bucket-name {target} --name {key} --file hello.txt",
        "ibm": f"{write}ibmcloud cos upload --bucket {target} --key {key} --file hello.txt",
    }
    if provider == "onprem":
        return [f"{write}" + _curl_onprem("PUT", extra.get("endpoint"), f"/__pskit/storage/{target}/{key}", None, "--data-binary @hello.txt")]
    return [commands[provider]] if provider in commands else []


def _gcp_project(extra: Dict[str, Any]) -> str:
    return f" --project {extra['project']}" if extra.get("project") else ""


def _curl_onprem(method: str, endpoint: Optional[str], path: str, body: Optional[str], data: str = "") -> str:
    base = (endpoint or "http://127.0.0.1:8080").rstrip("/")
    parts = [f"curl -i -X {method} {_q(base + path)}", '-H "x-pskit-token: $PSKIT_ADMIN_TOKEN"']
    if body is not None:
        parts += ["-H 'content-type: application/json'", f"-d {_q(body)}"]
    if data:
        parts.append(data)
    return " ".join(parts)


_CONSOLE: Dict[Tuple[str, str], List[str]] = {
    ("aws", "queue"): ["Open the Amazon SQS console in region {region}.", "Open queue {target} and choose Send and receive messages.", "Paste the payload into Message body and choose Send message.", "Check the function's logs (Studio Logs page or CloudWatch)."],
    ("aws", "topic"): ["Open the Amazon SNS console in region {region}.", "Open topic {target} and choose Publish message.", "Paste the payload into Message body and choose Publish message."],
    ("aws", "storage"): ["Open the Amazon S3 console.", "Open bucket {target}{prefix_hint} and choose Upload.", "Add a file{suffix_hint} and choose Upload."],
    ("aws", "schedule"): ["Open the Lambda console, function {function}.", "On the Test tab create an event with the JSON from the payload box (wrapped as shown in the CLI command).", "Choose Test."],
    ("aws", "invoke"): ["Open the Lambda console, function {function}.", "On the Test tab paste {{\"__pskit\": \"invoke\", \"data\": <payload>}} and choose Test."],
    ("gcp", "queue"): ["Open Pub/Sub in the Google Cloud console.", "Open topic {target}, tab Messages, choose Publish message.", "Paste the payload and choose Publish."],
    ("gcp", "topic"): ["Open Pub/Sub in the Google Cloud console.", "Open topic {target}, tab Messages, choose Publish message.", "Paste the payload and choose Publish."],
    ("gcp", "storage"): ["Open Cloud Storage in the console.", "Open bucket {target}{prefix_hint} and choose Upload files."],
    ("gcp", "schedule"): ["Open Cloud Scheduler in the console.", "Select the job of function {function} and choose Force run."],
    ("azure", "queue"): ["Open the storage account of the Function App in the Azure portal.", "Storage browser → Queues → {target} → Add message.", "Paste the payload (leave 'Encode the message body in Base64' checked) and choose OK."],
    ("azure", "storage"): ["Open the storage account of the Function App in the Azure portal.", "Storage browser → Blob containers → {target}{prefix_hint} → Upload."],
    ("azure", "topic"): ["Open the Service Bus namespace in the Azure portal.", "Topics → {target} → Service Bus Explorer → Send messages.", "Paste the payload as JSON and choose Send."],
    ("azure", "schedule"): ["Open the Function App in the Azure portal → Functions.", "Open the timer function of {function} → Code + Test → Test/Run → Run."],
    ("alibaba", "storage"): ["Open the OSS console, bucket {target}{prefix_hint}, and choose Upload Object."],
    ("alibaba", "topic"): ["Open the Simple Message Queue (MNS) console, topic {target}, and choose Publish Message."],
    ("tencent", "storage"): ["Open the COS console, bucket {target}{prefix_hint}, and choose Upload Files."],
    ("huawei", "storage"): ["Open the OBS console, bucket {target}{prefix_hint}, and choose Upload Object."],
    ("huawei", "topic"): ["Open the SMN console, topic {target}, and choose Publish Message."],
    ("oracle", "storage"): ["Open Object Storage in the OCI console, bucket {target}{prefix_hint}, and choose Upload."],
    ("ibm", "storage"): ["Open Cloud Object Storage in the IBM Cloud console, bucket {target}, and choose Upload."],
}


def _console(provider: str, name: str, fn_rec: Dict[str, Any], trigger: Dict[str, Any], region: str) -> List[str]:
    kind = trigger["type"]
    steps = _CONSOLE.get((provider, kind))
    if steps is None:
        if kind == "http":
            return ["Open the URL in a browser (GET), or use the curl command, Postman or the Studio Test page."]
        link = fn_rec.get("console")
        return [f"Open the function in the {provider} console{(': ' + str(link)) if link else ''} and use its test / invoke action."]
    hints = {
        "region": region,
        "target": trigger.get("name") or "",
        "function": fn_rec.get("resource_name") or name,
        "prefix_hint": f" (folder {trigger['prefix']})" if trigger.get("prefix") else "",
        "suffix_hint": f" ending in {trigger['suffix']}" if trigger.get("suffix") else "",
    }
    return [s.format(**hints) for s in steps]


def can_send(provider: str, kind: str, fn_rec: Dict[str, Any]) -> Tuple[bool, str]:
    """Can ``send`` deliver this kind of event on this provider?"""
    if not fn_rec:
        return False, "not deployed yet: deploy first, or test locally"
    if kind == "http":
        return (True, "") if fn_rec.get("url") or fn_rec.get("urls") else (False, "the deployment has no URL for it")
    if provider == "onprem":
        return True, ""
    if provider == "aws":
        return True, ""
    if provider == "gcp" and kind in ("queue", "topic", "storage"):
        return True, ""
    if provider == "azure" and kind in ("queue", "storage", "topic"):
        return True, ""
    if kind in ("invoke", "schedule") and fn_rec.get("url") and provider in ("gcp", "ibm", "digitalocean", "cloudflare", "oracle"):
        return True, ""
    return False, f"sending {kind} events from pskit isn't available on {provider}; use the CLI command or console steps"


def guide(project: Any, record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Everything the Test page shows: per function and trigger, payload, commands and steps."""
    cfg = project.config
    provider = cfg.provider.name
    region = project.region
    root = project.root
    extra: Dict[str, Any] = {
        "endpoint": _onprem_endpoint(record, root) if provider == "onprem" else None,
        "project": cfg.provider.options.get("project") if provider == "gcp" else None,
    }
    common = f" -p {provider} -s {cfg.provider.stage}"
    deployed = record is not None and record.get("status") not in ("local", "not deployed", "removed")
    functions = []
    for name, fn in sorted(cfg.functions.items()):
        if name.endswith("-orchestrator") and fn.handler.startswith(("py_serverless_kit.", "pskit_runtime.")):
            continue
        fn_rec = _record_function(record, name) if deployed else {}
        triggers = []
        for trig in _triggers(project, name):
            payload = _sample(trig)
            ok, reason = can_send(provider, trig["type"], fn_rec)
            sel = f" -e {trig['index']}" if trig["index"] >= 0 else " --direct"
            remote_pskit = f"pskit trigger -f {name}{sel}{common} -d {_q(_dumps(payload))}"
            triggers.append(
                {
                    **trig,
                    "sample": payload,
                    "payload_help": PAYLOAD_HELP[trig["type"]],
                    "local": _local(name, trig, payload, common),
                    "remote": {
                        "supported": ok,
                        "reason": reason,
                        "pskit": [remote_pskit] if ok else [],
                        "cli": _cloud_cli(provider, region, name, fn_rec, trig, payload, extra) if fn_rec else [],
                        "console": _console(provider, name, fn_rec, trig, region) if fn_rec else [],
                    },
                }
            )
        functions.append({"name": name, "description": fn.description, "triggers": triggers})
    return {
        "provider": provider,
        "stage": cfg.provider.stage,
        "region": region,
        "deployed": deployed,
        "functions": functions,
        "sdk": _SDK.get(provider),
    }


# --------------------------------------------------------------------- send


def _http(method: str, url: str, body: Optional[bytes], headers: Dict[str, str], timeout: float = 60) -> Dict[str, Any]:
    if not url.lower().startswith(("http://", "https://")):
        raise TriggerError(f"unexpected URL {url!r}")
    req = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - scheme checked above
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise TriggerError(f"could not reach {url}: {getattr(exc, 'reason', exc)}") from None
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        parsed = text[:4000]
    return {"status": status, "body": parsed}


def _boto3(region: str) -> Any:
    try:
        import boto3  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError:
        raise TriggerError("install boto3 to send AWS events: pip install 'py-serverless-kit[aws]'") from None
    return boto3.session.Session(region_name=region or None)


def _send_aws(region: str, fn_rec: Dict[str, Any], trig: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    session = _boto3(region)
    kind, target = trig["type"], str(trig.get("name") or "")
    if kind == "queue":
        sqs = session.client("sqs")
        url = sqs.get_queue_url(QueueName=target)["QueueUrl"]
        sent = []
        fifo = url.endswith(".fifo")
        for i, item in enumerate(payload if isinstance(payload, list) else [payload]):
            kwargs: Dict[str, Any] = {"QueueUrl": url, "MessageBody": item if isinstance(item, str) else _dumps(item)}
            if fifo:
                kwargs.update(MessageGroupId="pskit-test", MessageDeduplicationId=os.urandom(8).hex() + str(i))
            sent.append(sqs.send_message(**kwargs).get("MessageId"))
        return {"sent": len(sent), "message_ids": sent, "queue_url": url}
    if kind == "topic":
        account = session.client("sts").get_caller_identity()["Account"]
        arn = f"arn:aws:sns:{session.region_name}:{account}:{target}"
        message = payload.get("message", payload) if isinstance(payload, dict) else payload
        attrs = payload.get("attributes") if isinstance(payload, dict) else None
        kwargs = {"TopicArn": arn, "Message": message if isinstance(message, str) else _dumps(message)}
        if attrs:
            kwargs["MessageAttributes"] = {k: {"DataType": "String", "StringValue": str(v)} for k, v in attrs.items()}
        return {"message_id": session.client("sns").publish(**kwargs).get("MessageId"), "topic_arn": arn}
    if kind == "storage":
        key = str((payload or {}).get("key") or "test/hello.txt")
        if (payload or {}).get("action") == "deleted":
            session.client("s3").delete_object(Bucket=target, Key=key)
            return {"deleted": f"s3://{target}/{key}"}
        body = str((payload or {}).get("content", "hello from pskit")).encode("utf-8")
        session.client("s3").put_object(Bucket=target, Key=key, Body=body)
        return {"uploaded": f"s3://{target}/{key}", "bytes": len(body)}
    function = fn_rec.get("arn") or fn_rec.get("id") or fn_rec.get("resource_name")
    resp = session.client("lambda").invoke(FunctionName=function, Payload=_dumps(envelope(kind, payload)).encode())
    text = resp["Payload"].read().decode("utf-8", "replace")
    try:
        result: Any = json.loads(text)
    except ValueError:
        result = text
    return {"status": resp.get("StatusCode"), "function_error": resp.get("FunctionError"), "result": result}


def _send_gcp(project_id: Optional[str], trig: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    proj = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if not proj or "${" in str(proj):
        raise TriggerError("set provider.options.project (or GOOGLE_CLOUD_PROJECT) to send GCP events")
    kind, target = trig["type"], str(trig.get("name") or "")
    if kind == "storage":
        try:
            from google.cloud import storage  # type: ignore[import-not-found,import-untyped,unused-ignore]
        except ImportError:
            raise TriggerError("pip install google-cloud-storage to upload test objects") from None
        key = str((payload or {}).get("key") or "test/hello.txt")
        blob = storage.Client(project=proj).bucket(target).blob(key)
        blob.upload_from_string(str((payload or {}).get("content", "hello from pskit")))
        return {"uploaded": f"gs://{target}/{key}"}
    try:
        from google.cloud import pubsub_v1  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError:
        raise TriggerError("pip install google-cloud-pubsub to publish test messages") from None
    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(proj, target)
    items = payload if (kind == "queue" and isinstance(payload, list)) else [payload]
    ids = []
    for item in items:
        attrs: Dict[str, str] = {}
        if kind == "topic" and isinstance(item, dict) and "message" in item:
            attrs = {k: str(v) for k, v in (item.get("attributes") or {}).items()}
            item = item["message"]
        data = (item if isinstance(item, str) else _dumps(item)).encode("utf-8")
        ids.append(publisher.publish(topic, data, **attrs).result(timeout=30))
    return {"message_ids": ids, "topic": topic}


def _send_azure(trig: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    kind, target = trig["type"], str(trig.get("name") or "")
    if kind == "topic":
        conn = os.environ.get("AZURE_SERVICEBUS_CONNECTION_STRING")
        if not conn:
            raise TriggerError("set AZURE_SERVICEBUS_CONNECTION_STRING to publish to Service Bus topics")
        try:
            from azure.servicebus import ServiceBusClient, ServiceBusMessage  # type: ignore[import-not-found,import-untyped,unused-ignore]
        except ImportError:
            raise TriggerError("pip install azure-servicebus to publish test messages") from None
        message = payload.get("message", payload) if isinstance(payload, dict) else payload
        with ServiceBusClient.from_connection_string(conn) as client, client.get_topic_sender(target) as sender:
            sender.send_messages(ServiceBusMessage(message if isinstance(message, str) else _dumps(message)))
        return {"sent": 1, "topic": target}
    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise TriggerError(
            "set AZURE_STORAGE_CONNECTION_STRING (the Function App's storage account) to send queue messages or upload blobs"
        )
    if kind == "queue":
        try:
            from azure.storage.queue import QueueClient, TextBase64EncodePolicy  # type: ignore[import-not-found,import-untyped,unused-ignore]
        except ImportError:
            raise TriggerError("pip install azure-storage-queue to send test messages") from None
        client = QueueClient.from_connection_string(conn, target, message_encode_policy=TextBase64EncodePolicy())
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            client.send_message(item if isinstance(item, str) else _dumps(item))
        return {"sent": len(items), "queue": target}
    try:
        from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError:
        raise TriggerError("pip install azure-storage-blob to upload test blobs") from None
    key = str((payload or {}).get("key") or "test/hello.txt")
    blob = BlobServiceClient.from_connection_string(conn).get_blob_client(target, key)
    blob.upload_blob(str((payload or {}).get("content", "hello from pskit")).encode("utf-8"), overwrite=True)
    return {"uploaded": f"{target}/{key}"}


def _send_onprem(record: Dict[str, Any], root: Path, name: str, trig: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    from .onprem_control import control_for

    control = control_for(record, root)
    if control is None:
        raise TriggerError("the on-prem host address is unknown; run `pskit deploy`")
    kind, target = trig["type"], str(trig.get("name") or "")
    if kind == "queue":
        body = {"messages": payload} if isinstance(payload, list) else payload
        return {"result": control.request("POST", f"/__pskit/queues/{target}", body)}
    if kind == "topic":
        body = payload if isinstance(payload, dict) and "message" in payload else {"message": payload}
        return {"result": control.request("POST", f"/__pskit/topics/{target}", body)}
    if kind == "storage":
        key = str((payload or {}).get("key") or "test/hello.txt")
        method = "DELETE" if (payload or {}).get("action") == "deleted" else "PUT"
        content = None if method == "DELETE" else str((payload or {}).get("content", "hello from pskit")).encode()
        headers = {"x-pskit-token": control.token} if control.token else {}
        return _http(method, f"{control.base_url}/__pskit/storage/{target}/{key}", content, headers)
    return {"result": control.request("POST", f"/__pskit/invoke/{name}", envelope(kind, payload), timeout=900)}


def send(
    project: Any, record: Optional[Dict[str, Any]], function: str, index: int, payload: Any, path: Optional[str] = None
) -> Dict[str, Any]:
    """Fire trigger ``index`` of ``function`` (``-1`` = direct invoke) on the deployed stage."""
    if function not in project.config.functions:
        raise TriggerError(f"unknown function {function!r}")
    triggers = {t["index"]: t for t in _triggers(project, function)}
    if index not in triggers:
        raise TriggerError(f"{function} has no trigger #{index} (it has {len(triggers) - 1})")
    trig = triggers[index]
    provider = project.config.provider.name
    deployed = bool(record) and (record or {}).get("status") not in ("local", "not deployed", "removed")
    fn_rec = _record_function(record, function) if deployed else {}
    ok, reason = can_send(provider, trig["type"], fn_rec)
    if not ok:
        raise TriggerError(reason)
    if trig["type"] == "http":
        url = _http_url(fn_rec, trig) or ""
        if path:
            base = url[: -len(trig["path"])] if url.endswith(trig["path"]) else url.rstrip("/")
            url = base + (path if path.startswith("/") else "/" + path)
        has_body = trig["method"] not in ("GET", "HEAD", "DELETE") and payload is not None
        body = (payload if isinstance(payload, str) else _dumps(payload)).encode() if has_body else None
        result = _http(trig["method"], url, body, {"content-type": "application/json"} if body else {})
        return {"kind": "http", "url": url, **result}
    try:
        if provider == "onprem":
            out = _send_onprem(record or {}, project.root, function, trig, payload)
        elif provider == "aws":
            out = _send_aws(project.region, fn_rec, trig, payload)
        elif provider == "gcp" and trig["type"] in ("queue", "topic", "storage"):
            out = _send_gcp(project.config.provider.options.get("project"), trig, payload)
        elif provider == "azure":
            out = _send_azure(trig, payload)
        else:
            url = str(fn_rec.get("url"))
            out = _http("POST", url, _dumps(envelope(trig["type"], payload)).encode(), {"content-type": "application/json"})
    except TriggerError:
        raise
    except Exception as exc:  # noqa: BLE001 - credentials, permissions, missing resources: report them
        raise TriggerError(f"{type(exc).__name__}: {exc}") from None
    return {"kind": trig["type"], "summary": trig["summary"], **out}


# --------------------------------------------------------------------- resources

_RESOURCE_SAMPLES: Dict[str, Any] = {
    "queue": {"order_id": 1, "action": "create"},
    "storage": {"key": "test/hello.txt", "content": "hello from pskit"},
    "topic": {"message": {"event": "created", "id": 1}, "attributes": {"source": "pskit-test"}},
}
_RESOURCE_ACTION = {"queue": "Send a message", "storage": "Upload an object", "topic": "Publish a message"}


def can_send_resource(provider: str, kind: str, deployed: bool, external: bool) -> Tuple[bool, str]:
    if kind not in _RESOURCE_SAMPLES:
        return False, f"pskit can't send to a {kind} resource; use its own client with the connection values below"
    if not deployed and not external:
        return False, "managed resources exist after the first deploy: deploy first"
    if provider in ("aws", "onprem") or (provider in ("gcp", "azure")):
        if provider == "onprem" and not deployed:
            return False, "the on-prem host runs after deploy: deploy first"
        return True, ""
    return False, f"sending to {kind} resources from pskit isn't available on {provider}; use the CLI command or console steps"


def _env_names(key: str, res: Any) -> List[str]:
    from ..config.schema import env_key

    prefix = f"PSKIT_RESOURCE_{env_key(key)}_"
    return [prefix + n for n in ("TYPE", "NAME", "EXTERNAL")] + [prefix + env_key(k) for k in res.connection]


def resource_guide(project: Any, record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """How to test every resource: what to send (queues, buckets, topics) or how to read its connection."""
    cfg = project.config
    provider = cfg.provider.name
    region = project.region
    deployed = record is not None and record.get("status") not in ("local", "not deployed", "removed")
    extra: Dict[str, Any] = {
        "endpoint": _onprem_endpoint(record, project.root) if provider == "onprem" and deployed else None,
        "project": cfg.provider.options.get("project") if provider == "gcp" else None,
    }
    common = f" -p {provider} -s {cfg.provider.stage}"
    items = []
    for key, res in sorted(cfg.resources.items()):
        native = res.native_name(key)
        kind = res.type
        ok, reason = can_send_resource(provider, kind, deployed, res.external)
        bound = cfg.resource_functions(key)
        first = sorted(res.connection)[0] if res.connection else "url"
        entry: Dict[str, Any] = {
            "key": key,
            "type": kind,
            "name": native,
            "external": res.external,
            "functions": bound,
            "connection_keys": sorted(res.connection),
            "env": _env_names(key, res),
            "snippets": {
                "python": f'from py_serverless_kit import resource\n\nr = resource("{key}")\nprint(r.native_name, r.get("{first}"))',
                "node": f'const {{ resource }} = require("pskit");\n\nconst r = resource("{key}");\nconsole.log(r.nativeName, r.get("{first}"));',
            },
            "sendable": kind in _RESOURCE_SAMPLES,
            "action": _RESOURCE_ACTION.get(kind),
            "sample": _RESOURCE_SAMPLES.get(kind),
            "payload_help": PAYLOAD_HELP.get(kind, ""),
            "remote": {"supported": ok, "reason": reason, "pskit": [], "cli": [], "console": []},
        }
        if kind in _RESOURCE_SAMPLES:
            trig = {"index": 0, "type": kind, "name": native, "summary": f"{kind} {native}", "prefix": "", "suffix": ""}
            sample = _RESOURCE_SAMPLES[kind]
            if ok:
                entry["remote"]["pskit"] = [f"pskit resources send {key}{common} -d {_q(_dumps(sample))}"]
            if deployed or res.external:
                entry["remote"]["cli"] = _cloud_cli(provider, region, key, {}, trig, sample, extra)
                entry["remote"]["console"] = _console(provider, key, {}, trig, region)
        items.append(entry)
    return {"provider": provider, "stage": cfg.provider.stage, "deployed": deployed, "resources": items}


def send_resource(project: Any, record: Optional[Dict[str, Any]], key: str, payload: Any) -> Dict[str, Any]:
    """Send a message to a queue / topic resource or upload an object to a storage resource."""
    res = project.config.resources.get(key)
    if res is None:
        known = ", ".join(sorted(project.config.resources)) or "<none>"
        raise TriggerError(f"unknown resource {key!r}; known: {known}")
    provider = project.config.provider.name
    deployed = record is not None and record.get("status") not in ("local", "not deployed", "removed")
    ok, reason = can_send_resource(provider, res.type, deployed, res.external)
    if not ok:
        raise TriggerError(reason)
    native = res.native_name(key)
    trig = {"index": 0, "type": res.type, "name": native, "summary": f"{res.type} {native}"}
    if payload is None:
        payload = _RESOURCE_SAMPLES[res.type]
    try:
        if provider == "onprem":
            out = _send_onprem(record or {}, project.root, "", trig, payload)
        elif provider == "aws":
            out = _send_aws(project.region, {}, trig, payload)
        elif provider == "gcp":
            out = _send_gcp(project.config.provider.options.get("project"), trig, payload)
        else:
            out = _send_azure(trig, payload)
    except TriggerError:
        raise
    except Exception as exc:  # noqa: BLE001 - credentials, permissions, missing resources: report them
        raise TriggerError(f"{type(exc).__name__}: {exc}") from None
    return {"resource": key, "kind": res.type, "name": native, "functions": project.config.resource_functions(key), **out}


__all__ = [
    "SAMPLES",
    "TriggerError",
    "can_send",
    "can_send_resource",
    "guide",
    "resource_guide",
    "send",
    "send_resource",
]
