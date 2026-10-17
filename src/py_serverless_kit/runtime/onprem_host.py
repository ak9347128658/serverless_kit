"""On-premises host: one process for HTTP, schedules, queues, storage and topics.

Deploy writes ``pskit_host.json`` plus the function bundle. This module is
stdlib-only and is copied into that bundle. Backing services default to
directories created at startup. A ``directory`` binding in the manifest uses a
path you already have instead.

* ``POST /__pskit/queues/<name>`` publishes a message (JSON body, or ``{"messages": [...]}``).
* ``POST /__pskit/topics/<name>`` publishes. ``{"message": ..., "attributes": {...}}``
  sets attributes used by topic filters.
* ``PUT`` / ``DELETE /__pskit/storage/<bucket>/<key>`` writes an object and emits
  ``created`` / ``deleted``. Dropping a file in the bucket directory does the same.
* ``POST /__pskit/invoke/<function>`` and ``POST /__pskit/workflows/<name>`` call
  functions and the embedded workflow engine.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import URLError
from urllib.parse import parse_qsl, unquote, urlsplit
from urllib.request import urlopen

from .batch import BatchResult
from .context import Context
from .cron import CronError, next_cron, rate_seconds, resolve_zone
from .dispatch import execute
from .event import Event, EventType, Headers, HttpRequest, Record
from .manifest import load_manifest, set_manifest
from .response import Response, dumps
from .routing import match_path, method_matches

_BROKER: Optional[Broker] = None
_MAX_BODY = 64 * 1024 * 1024


def install_broker(broker: Optional[Broker]) -> None:
    global _BROKER
    _BROKER = broker


def current_broker() -> Broker:
    if _BROKER is not None:
        return _BROKER
    root = os.environ.get("PSKIT_ONPREM_ROOT")
    if not root:
        raise RuntimeError("not running inside an on-premises host (PSKIT_ONPREM_ROOT is unset)")
    spec = json.loads((Path(root) / "pskit_host.json").read_text("utf-8"))
    spec["root"] = str(Path(root))
    broker = Broker(spec)
    install_broker(broker)
    return broker


def submit_queue(name: str, message: Any, attributes: Optional[Dict[str, Any]] = None) -> str:
    """Publish one message to an on-prem queue. Works from a handler in this process."""
    return current_broker().submit_queue(name, message, attributes or {})


def submit_topic(name: str, message: Any, attributes: Optional[Dict[str, Any]] = None) -> str:
    return current_broker().submit_topic(name, message, attributes or {})


def put_object(bucket: str, key: str, data: bytes) -> None:
    current_broker().put_object(bucket, key, data)


def delete_object(bucket: str, key: str) -> None:
    current_broker().delete_object(bucket, key)


def _segment(name: str, what: str) -> str:
    text = str(name).strip()
    if not text or text in (".", "..") or "/" in text or "\\" in text or "\x00" in text:
        raise ValueError(f"invalid {what} name {name!r}")
    return text


def _key_parts(key: str) -> List[str]:
    parts = [unquote(part) for part in str(key).split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"invalid object key {key!r}")
    return parts


def _body_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, bytes):
        return message.decode("utf-8", "replace")
    return json.dumps(message)


def _split_message(item: Any) -> Tuple[Any, Dict[str, Any]]:
    if isinstance(item, dict) and "message" in item and set(item) <= {"message", "attributes"}:
        raw = item.get("attributes") or {}
        return item.get("message"), dict(raw) if isinstance(raw, dict) else {}
    return item, {}


def _messages_of(payload: Any) -> List[Tuple[Any, Dict[str, Any]]]:
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return [_split_message(item) for item in payload["messages"]]
    if isinstance(payload, list):
        return [_split_message(item) for item in payload]
    return [_split_message(payload)]


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


class Broker:
    """File-backed queues, buckets and topic pending files. Safe inside one process."""

    def __init__(self, spec: Dict[str, Any]) -> None:
        self.spec = spec
        self.data_dir = Path(spec["data_dir"])
        self.services = spec.get("services") or {}
        self._lock = threading.RLock()

    def path_for(self, kind: str) -> Path:
        service = self.services.get(kind) or {}
        raw = service.get("path")
        if raw:
            return Path(raw)
        return self.data_dir / kind

    def queue_dir(self, name: str) -> Path:
        return self.path_for("queue") / _segment(name, "queue")

    def bucket_dir(self, bucket: str) -> Path:
        return self.path_for("storage") / _segment(bucket, "bucket")

    def topic_dir(self, name: str) -> Path:
        return self.path_for("topic") / _segment(name, "topic")

    def ensure(self, functions: Dict[str, Any]) -> List[str]:
        notes: List[str] = []
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for fn in functions.values():
            for ev in fn.get("events") or []:
                kind = ev.get("type")
                if kind == "queue":
                    notes.extend(self._ensure_named(self.queue_dir(ev["name"]), ev, "queue"))
                    if ev.get("dead_letter"):
                        notes.extend(self._ensure_named(self.queue_dir(ev["dead_letter"]), {"existing": False}, "queue"))
                elif kind == "storage":
                    notes.extend(self._ensure_named(self.bucket_dir(ev["bucket"]), ev, "bucket"))
                elif kind == "topic":
                    notes.extend(self._ensure_named(self.topic_dir(ev["name"]) / "pending", ev, "topic"))
        return notes

    def _ensure_named(self, path: Path, ev: Dict[str, Any], kind: str) -> List[str]:
        leaf = path if path.name == "pending" or path.name == "messages" else path / "messages"
        if kind == "bucket":
            leaf = path
        if kind == "topic":
            leaf = path
        if ev.get("existing") and not leaf.exists():
            label = ev.get("name") or ev.get("bucket") or leaf.name
            return [f"{kind} {label} is marked existing but {leaf} is not there yet; it will be picked up when it appears"]
        if kind == "bucket":
            leaf.mkdir(parents=True, exist_ok=True)
        else:
            (leaf if kind == "topic" else leaf).mkdir(parents=True, exist_ok=True)
        return []

    # ---------------------------------------------------------------- queue
    def submit_queue(self, name: str, message: Any, attributes: Optional[Dict[str, Any]] = None) -> str:
        with self._lock:
            directory = self.queue_dir(name) / "messages"
            directory.mkdir(parents=True, exist_ok=True)
            seq = self._next_seq(self.queue_dir(name))
            ident = uuid.uuid4().hex
            record = {
                "id": ident,
                "body": _body_text(message),
                "attributes": attributes or {},
                "receives": 0,
                "visible_at": 0,
                "enqueued_at": time.time(),
                "seq": seq,
            }
            _write_json(directory / f"{seq:016d}-{ident}.json", record)
            return ident

    def queue_depth(self, name: str) -> int:
        directory = self.queue_dir(name) / "messages"
        if not directory.is_dir():
            return 0
        return sum(1 for _ in directory.glob("*.json"))

    def claim_queue(self, ev: Dict[str, Any], now: float) -> List[Dict[str, Any]]:
        with self._lock:
            directory = self.queue_dir(ev["name"]) / "messages"
            if not directory.is_dir():
                return []
            batch = int(ev.get("batch_size") or 1)
            window = float(ev.get("batch_window") or 0)
            fifo = bool(ev.get("fifo"))
            chosen: List[Tuple[Path, Dict[str, Any]]] = []
            for path in sorted(directory.glob("*.json")):
                msg = _read_json(path, None)
                if not isinstance(msg, dict):
                    continue
                visible = float(msg.get("visible_at") or 0) <= now
                if not visible:
                    if fifo:
                        break
                    continue
                chosen.append((path, msg))
                if len(chosen) >= batch:
                    break
            if not chosen:
                return []
            if window and len(chosen) < batch:
                oldest = min(float(msg.get("enqueued_at") or now) for _, msg in chosen)
                if now - oldest < window:
                    return []
            visibility = float(ev.get("visibility_timeout") or 30)
            claimed = []
            for path, msg in chosen:
                msg["visible_at"] = now + visibility
                _write_json(path, msg)
                msg["path"] = str(path)
                claimed.append(msg)
            return claimed

    def settle_queue(self, ev: Dict[str, Any], claimed: List[Dict[str, Any]], failed: Iterable[str], now: float) -> None:
        failed_ids = {str(item) for item in failed}
        max_receive = int(ev.get("max_receive") or 5)
        dead = ev.get("dead_letter")
        with self._lock:
            for msg in claimed:
                path = Path(msg["path"])
                if str(msg.get("id")) not in failed_ids:
                    path.unlink(missing_ok=True)
                    continue
                current = _read_json(path, msg)
                current["receives"] = int(current.get("receives") or 0) + 1
                if dead and current["receives"] >= max_receive:
                    path.unlink(missing_ok=True)
                    self.submit_queue(
                        str(dead),
                        current.get("body"),
                        {"pskit_source_queue": ev["name"], "pskit_receives": current["receives"]},
                    )
                else:
                    current["visible_at"] = now
                    _write_json(path, current)

    def _next_seq(self, queue: Path) -> int:
        seq_path = queue / "seq"
        try:
            current = int(seq_path.read_text("ascii").strip() or "0")
        except (OSError, ValueError):
            current = 0
        current += 1
        seq_path.parent.mkdir(parents=True, exist_ok=True)
        seq_path.write_text(str(current), "ascii")
        return current

    # ---------------------------------------------------------------- topic
    def submit_topic(self, name: str, message: Any, attributes: Optional[Dict[str, Any]] = None) -> str:
        with self._lock:
            directory = self.topic_dir(name) / "pending"
            directory.mkdir(parents=True, exist_ok=True)
            ident = uuid.uuid4().hex
            record = {
                "id": ident,
                "body": _body_text(message),
                "attributes": attributes or {},
                "delivered": [],
                "enqueued_at": time.time(),
            }
            _write_json(directory / f"{ident}.json", record)
            return ident

    def claim_topic(self, name: str) -> List[Dict[str, Any]]:
        with self._lock:
            directory = self.topic_dir(name) / "pending"
            if not directory.is_dir():
                return []
            claimed = []
            for path in sorted(directory.glob("*.json")):
                msg = _read_json(path, None)
                if isinstance(msg, dict):
                    msg["path"] = str(path)
                    claimed.append(msg)
            return claimed

    def settle_topic(self, msg: Dict[str, Any], delivered_to: Iterable[str], done: bool) -> None:
        path = Path(msg["path"])
        with self._lock:
            if done or not path.is_file():
                path.unlink(missing_ok=True)
                return
            current = _read_json(path, msg)
            have = set(current.get("delivered") or [])
            have.update(delivered_to)
            current["delivered"] = sorted(have)
            _write_json(path, current)

    # ---------------------------------------------------------------- storage
    def put_object(self, bucket: str, key: str, data: bytes) -> Path:
        parts = _key_parts(key)
        path = self.bucket_dir(bucket)
        for part in parts:
            path = path / part
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def delete_object(self, bucket: str, key: str) -> bool:
        parts = _key_parts(key)
        path = self.bucket_dir(bucket)
        for part in parts:
            path = path / part
        if not path.is_file():
            return False
        path.unlink()
        return True

    def scan_bucket(self, bucket: str) -> Dict[str, Tuple[int, int]]:
        root = self.bucket_dir(bucket)
        found: Dict[str, Tuple[int, int]] = {}
        if not root.is_dir():
            return found
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            stat = path.stat()
            found[rel] = (stat.st_mtime_ns, stat.st_size)
        return found


def _filter_match(policy: Optional[Dict[str, Any]], attributes: Dict[str, Any], body: Any) -> bool:
    if not policy:
        return True
    source = dict(attributes or {})
    if isinstance(body, dict):
        for key, value in body.items():
            source.setdefault(str(key), value)
    for key, expected in policy.items():
        allowed = expected if isinstance(expected, list) else [expected]
        allowed_text = {str(item) for item in allowed}
        actual = source.get(key)
        if isinstance(actual, list):
            if not any(item in allowed or str(item) in allowed_text for item in actual):
                return False
        elif actual not in allowed and str(actual) not in allowed_text:
            return False
    return True


def _key_selected(key: str, ev: Dict[str, Any]) -> bool:
    prefix = ev.get("prefix") or ""
    suffix = ev.get("suffix") or ""
    if prefix and not key.startswith(prefix):
        return False
    if suffix and not key.endswith(suffix):
        return False
    return True


class Host:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.spec = json.loads((self.root / "pskit_host.json").read_text("utf-8"))
        self.spec["root"] = str(self.root)
        self.app_dir = self.root / "app"
        self.broker = Broker(self.spec)
        self.notes: List[str] = []
        self._stop = threading.Event()
        self._env_lock = threading.RLock()
        self._rr: Dict[str, int] = {}
        self._snapshot: Dict[str, Dict[str, Tuple[int, int]]] = {}
        self._sched = _read_json(self._sched_path(), {"origin": None, "last": {}})
        self._booted = False

    # ---------------------------------------------------------------- lifecycle
    def open(self) -> None:
        if not (self.app_dir / "pskit_manifest.json").is_file():
            raise FileNotFoundError(f"bundle manifest missing: {self.app_dir / 'pskit_manifest.json'}")
        self._prepare_imports()
        os.environ["PSKIT_MANIFEST"] = str(self.app_dir / "pskit_manifest.json")
        os.environ["PSKIT_ONPREM_ROOT"] = str(self.root)
        set_manifest(None)
        self.manifest = load_manifest(reload=True)
        install_broker(self.broker)
        self.notes.extend(self.broker.ensure(self.spec.get("functions") or {}))
        self._baseline_storage()
        if not self._sched.get("origin"):
            self._sched["origin"] = time.time()
            self._sched.setdefault("last", {})
            self._save_sched()
        self._booted = True

    def _prepare_imports(self) -> None:
        app = str(self.app_dir)
        while app in sys.path:
            sys.path.remove(app)
        sys.path.insert(0, app)
        from . import dispatch

        dispatch._HANDLERS.clear()
        for name, module in list(sys.modules.items()):
            if name != "app" and not name.startswith("app."):
                continue
            loaded = getattr(module, "__file__", None) or ""
            if not loaded.startswith(app):
                del sys.modules[name]

    def _baseline_storage(self) -> None:
        path = self.broker.data_dir / "storage-snapshot.json"
        saved = _read_json(path, None)
        if isinstance(saved, dict):
            self._snapshot = {
                bucket: {key: (int(pair[0]), int(pair[1])) for key, pair in files.items()}
                for bucket, files in saved.items()
                if isinstance(files, dict)
            }
            return
        for ev in self._events("storage"):
            bucket = ev["bucket"]
            self._snapshot.setdefault(bucket, self.broker.scan_bucket(bucket))
        self._save_snapshot()

    def _save_snapshot(self) -> None:
        _write_json(self.broker.data_dir / "storage-snapshot.json", self._snapshot)

    def _sched_path(self) -> Path:
        return self.broker.data_dir / "schedule-state.json"

    def _save_sched(self) -> None:
        self.broker.data_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self._sched_path(), self._sched)

    def rewind_schedules(self, seconds: float) -> None:
        """Test helper: pretend the last fire was ``seconds`` ago."""
        past = time.time() - seconds
        self._sched["origin"] = past
        last = self._sched.setdefault("last", {})
        for key in self._schedule_ids():
            last[key] = past
        self._save_sched()

    # ---------------------------------------------------------------- invoke
    def invoke(self, function: str, event: Event) -> Any:
        fns = self.spec.get("functions") or {}
        if function not in fns and function not in getattr(self, "manifest", load_manifest()).functions:
            known = ", ".join(sorted(fns)) or "<none>"
            raise KeyError(f"unknown function {function!r}; known: {known}")
        spec = fns.get(function) or {}
        event.function = function
        event.provider = "onprem"
        timeout = int(spec.get("timeout") or 30)
        with self._call_env(function, spec):
            ctx = Context(
                request_id=uuid.uuid4().hex,
                function=function,
                provider="onprem",
                deadline=time.time() + timeout,
            )
            return execute(event, ctx, function)

    def _call_env(self, function: str, spec: Dict[str, Any]):
        forced = {
            "PSKIT_PROVIDER": "onprem",
            "PSKIT_SERVICE": str(self.spec.get("service") or ""),
            "PSKIT_STAGE": str(self.spec.get("stage") or ""),
            "PSKIT_REGION": str(self.spec.get("region") or "local"),
            "PSKIT_FUNCTION": function,
            "PSKIT_HANDLER": str(spec.get("handler") or ""),
            "PSKIT_ONPREM_ROOT": str(self.root),
            "PSKIT_MANIFEST": str(self.app_dir / "pskit_manifest.json"),
            "PSKIT_ENDPOINT": str(self.spec.get("endpoint") or ""),
        }
        env = dict(spec.get("environment") or {})
        env.update(forced)
        return self._swap_env(env)

    def _swap_env(self, env: Dict[str, str]):
        host = self

        class _Env:
            def __enter__(self) -> None:
                host._env_lock.acquire()
                self.saved = {key: os.environ.get(key) for key in env}
                os.environ.update(env)

            def __exit__(self, *exc: Any) -> None:
                for key, old in self.saved.items():
                    if old is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = old
                host._env_lock.release()

        return _Env()

    # ---------------------------------------------------------------- pump
    def pump(self, now: Optional[float] = None) -> None:
        if not self._booted:
            self.open()
        moment = time.time() if now is None else now
        self.pump_schedules(moment)
        self.pump_queues(moment)
        self.pump_topics(moment)
        self.pump_storage(moment)

    def _events(self, kind: str) -> List[Dict[str, Any]]:
        found = []
        for fn in (self.spec.get("functions") or {}).values():
            for ev in fn.get("events") or []:
                if ev.get("type") == kind:
                    found.append(ev)
        return found

    def _schedule_ids(self) -> List[str]:
        ids = []
        for name, fn in (self.spec.get("functions") or {}).items():
            index = 0
            for ev in fn.get("events") or []:
                if ev.get("type") == "schedule":
                    ids.append(f"{name}:{ev.get('name') or index}")
                    index += 1
        return ids

    def pump_schedules(self, now: float) -> None:
        last: Dict[str, float] = self._sched.setdefault("last", {})
        origin = float(self._sched.get("origin") or now)
        changed = False
        for name, fn in (self.spec.get("functions") or {}).items():
            index = 0
            for ev in fn.get("events") or []:
                if ev.get("type") != "schedule":
                    continue
                ident = f"{name}:{ev.get('name') or index}"
                index += 1
                if not ev.get("enabled", True):
                    continue
                seen = float(last.get(ident) or origin)
                try:
                    due = self._schedule_due(ev, seen, now, ident)
                except CronError as exc:
                    self._note(f"schedule {ident}: {exc}")
                    continue
                if not due:
                    continue
                event = Event(
                    type=EventType.SCHEDULE,
                    provider="onprem",
                    function=name,
                    data=ev.get("input"),
                    source="onprem.schedule",
                )
                try:
                    self.invoke(name, event)
                except Exception as exc:  # noqa: BLE001 - one bad job must not stop the host
                    self._note(f"schedule {ident} failed: {exc}")
                last[ident] = now
                changed = True
        if changed:
            self._save_sched()

    def _schedule_due(self, ev: Dict[str, Any], last: float, now: float, ident: str) -> bool:
        if ev.get("cron"):
            name = str(ev.get("timezone") or "UTC")
            zone = resolve_zone(name)
            if zone is None:
                self._note(f"schedule {ident}: timezone {name} is unavailable; using UTC (pip install tzdata)")
                zone = timezone.utc
            nxt = next_cron(str(ev["cron"]), datetime.fromtimestamp(last, zone), zone)
            return now >= nxt.timestamp()
        interval = rate_seconds(str(ev.get("rate") or "1 minutes"))
        return now >= last + interval

    def pump_queues(self, now: float) -> None:
        groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for name, fn in (self.spec.get("functions") or {}).items():
            for ev in fn.get("events") or []:
                if ev.get("type") == "queue":
                    groups.setdefault(ev["name"], []).append((name, ev))
        for queue, consumers in groups.items():
            slot = self._rr.get(queue, 0) % len(consumers)
            function, ev = consumers[slot]
            visibility = ev.get("visibility_timeout")
            if visibility is None:
                ev = dict(ev)
                ev["visibility_timeout"] = max(30, 6 * int(self.spec["functions"][function].get("timeout") or 30))
            claimed = self.broker.claim_queue(ev, now)
            if not claimed:
                continue
            self._rr[queue] = slot + 1
            records = [
                Record(
                    id=msg.get("id"),
                    body=msg.get("body"),
                    attributes=dict(msg.get("attributes") or {}),
                    source="onprem.queue",
                )
                for msg in claimed
            ]
            failed: List[str]
            try:
                result = self.invoke(function, Event(type=EventType.QUEUE, records=records, source="onprem.queue"))
            except Exception as exc:  # noqa: BLE001
                self._note(f"queue {queue} -> {function} failed: {exc}")
                failed = [str(msg.get("id")) for msg in claimed]
            else:
                failed = list(result.failed) if isinstance(result, BatchResult) else []
            self.broker.settle_queue(ev, claimed, failed, now)

    def pump_topics(self, now: float) -> None:
        del now
        groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for name, fn in (self.spec.get("functions") or {}).items():
            for ev in fn.get("events") or []:
                if ev.get("type") == "topic":
                    groups.setdefault(ev["name"], []).append((name, ev))
        for topic, consumers in groups.items():
            for msg in self.broker.claim_topic(topic):
                already = set(msg.get("delivered") or [])
                body = msg.get("body")
                try:
                    parsed = json.loads(body) if isinstance(body, str) else body
                except ValueError:
                    parsed = body
                delivered: List[str] = []
                pending = False
                for function, ev in consumers:
                    if function in already:
                        delivered.append(function)
                        continue
                    if not _filter_match(ev.get("filter"), dict(msg.get("attributes") or {}), parsed):
                        delivered.append(function)
                        continue
                    record = Record(
                        id=msg.get("id"),
                        body=body,
                        attributes=dict(msg.get("attributes") or {}),
                        source="onprem.topic",
                    )
                    try:
                        self.invoke(function, Event(type=EventType.TOPIC, records=[record], source="onprem.topic"))
                    except Exception as exc:  # noqa: BLE001
                        self._note(f"topic {topic} -> {function} failed: {exc}")
                        pending = True
                    else:
                        delivered.append(function)
                self.broker.settle_topic(msg, delivered, done=not pending)

    def pump_storage(self, now: float) -> None:
        del now
        listeners: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for name, fn in (self.spec.get("functions") or {}).items():
            for ev in fn.get("events") or []:
                if ev.get("type") == "storage":
                    listeners.setdefault(ev["bucket"], []).append((name, ev))
        changed = False
        for bucket, consumers in listeners.items():
            current = self.broker.scan_bucket(bucket)
            previous = self._snapshot.get(bucket) or {}
            events: List[Tuple[str, str, int]] = []
            for key, (mtime, size) in current.items():
                old = previous.get(key)
                if old is None or old != (mtime, size):
                    events.append((key, "created", size))
            for key in previous:
                if key not in current:
                    events.append((key, "deleted", 0))
            if not events:
                self._snapshot[bucket] = current
                continue
            ok = True
            for function, ev in consumers:
                wanted = set(ev.get("events") or ["created"])
                records = [
                    Record(id=f"{bucket}/{key}", bucket=bucket, key=key, size=size, action=action, source="onprem.storage")
                    for key, action, size in events
                    if action in wanted and _key_selected(key, ev)
                ]
                if not records:
                    continue
                try:
                    self.invoke(function, Event(type=EventType.STORAGE, records=records, source="onprem.storage"))
                except Exception as exc:  # noqa: BLE001
                    self._note(f"storage {bucket} -> {function} failed: {exc}")
                    ok = False
            if ok:
                self._snapshot[bucket] = current
                changed = True
        if changed:
            self._save_snapshot()

    def _note(self, text: str) -> None:
        if text in self.notes:
            return
        self.notes.append(text)
        del self.notes[:-50]

    # ---------------------------------------------------------------- http
    def make_server(self, port: Optional[int] = None) -> ThreadingHTTPServer:
        bind = str(self.spec.get("host") or "127.0.0.1")
        chosen = int(self.spec.get("port") or 8080) if port is None else port
        httpd = _Server((bind, chosen), self._handler())
        httpd.host = self  # type: ignore[attr-defined]
        return httpd

    def serve_forever(self) -> None:
        if not self._booted:
            self.open()
        poller = threading.Thread(target=self._poll_loop, name="pskit-onprem", daemon=True)
        poller.start()
        server = self.make_server()
        try:
            server.serve_forever()
        finally:
            self._stop.set()
            server.server_close()

    def _poll_loop(self) -> None:
        interval = float(self.spec.get("poll_seconds") or 0.5)
        while not self._stop.wait(interval):
            try:
                self.pump()
            except Exception as exc:  # noqa: BLE001
                self._note(f"pump failed: {exc}")

    def _handler(self):
        host = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "pskit-onprem"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                return None

            def _send(self, status: int, headers: Dict[str, str], body: bytes, cookies: Optional[List[str]] = None) -> None:
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                for cookie in cookies or []:
                    self.send_header("set-cookie", cookie)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, status: int, payload: Any) -> None:
                self._send(status, {"content-type": "application/json"}, dumps(payload).encode("utf-8"))

            def _read(self) -> bytes:
                length = int(self.headers.get("content-length") or 0)
                if length > _MAX_BODY:
                    raise ValueError("request body is too large")
                return self.rfile.read(length) if length else b""

            def _admin_ok(self) -> bool:
                token = str(host.spec.get("admin_token") or "")
                if not token:
                    return True
                return self.headers.get("x-pskit-token") == token

            def do_GET(self) -> None:  # noqa: N802
                self._route()

            def do_POST(self) -> None:  # noqa: N802
                self._route()

            def do_PUT(self) -> None:  # noqa: N802
                self._route()

            def do_DELETE(self) -> None:  # noqa: N802
                self._route()

            def do_HEAD(self) -> None:  # noqa: N802
                self._route()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._route()

            def do_PATCH(self) -> None:  # noqa: N802
                self._route()

            def _route(self) -> None:
                try:
                    url = urlsplit(self.path)
                    if url.path == "/__pskit/health" or url.path == "/__pskit":
                        return self._json(200, host.health())
                    if url.path.startswith("/__pskit/"):
                        if not self._admin_ok():
                            return self._json(401, {"error": "admin token required"})
                        return self._admin(url.path)
                    self._user(url)
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001 - last-resort response
                    self._json(500, {"errorType": type(exc).__name__, "cause": str(exc)})

            def _admin(self, path: str) -> None:
                parts = [unquote(part) for part in path.split("/") if part][1:]
                raw = self._read()
                if parts and parts[0] == "queues" and len(parts) == 2:
                    return self._queue(parts[1], raw)
                if parts and parts[0] == "topics" and len(parts) == 2:
                    return self._topic(parts[1], raw)
                if parts and parts[0] == "storage" and len(parts) >= 3:
                    return self._storage(parts[1], "/".join(parts[2:]), raw)
                if parts and parts[0] == "invoke" and len(parts) == 2:
                    return self._invoke(parts[1], raw)
                if parts and parts[0] == "workflows" and len(parts) == 2:
                    return self._workflow(parts[1], raw)
                self._json(404, {"error": "unknown on-prem endpoint"})

            def _queue(self, name: str, raw: bytes) -> None:
                if self.command == "GET":
                    return self._json(200, {"queue": name, "depth": host.broker.queue_depth(name)})
                if self.command != "POST":
                    return self._json(405, {"error": "POST a JSON message"})
                payload = json.loads(raw.decode("utf-8") or "null")
                ids = [host.broker.submit_queue(name, message, attrs) for message, attrs in _messages_of(payload)]
                return self._json(202, {"queued": ids})

            def _topic(self, name: str, raw: bytes) -> None:
                if self.command != "POST":
                    return self._json(405, {"error": "POST a JSON message"})
                payload = json.loads(raw.decode("utf-8") or "null")
                ids = [host.broker.submit_topic(name, message, attrs) for message, attrs in _messages_of(payload)]
                host.pump_topics(time.time())
                return self._json(202, {"published": ids})

            def _storage(self, bucket: str, key: str, raw: bytes) -> None:
                if self.command == "PUT":
                    host.broker.put_object(bucket, key, raw)
                    host.pump_storage(time.time())
                    return self._json(201, {"bucket": bucket, "key": key, "size": len(raw)})
                if self.command == "DELETE":
                    removed = host.broker.delete_object(bucket, key)
                    host.pump_storage(time.time())
                    return self._json(200, {"deleted": removed, "bucket": bucket, "key": key})
                self._json(405, {"error": "PUT or DELETE an object"})

            def _invoke(self, function: str, raw: bytes) -> None:
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    data = raw.decode("utf-8", "replace")
                try:
                    result = host.invoke(function, Event(type=EventType.INVOKE, data=data, source="onprem.invoke"))
                except KeyError as exc:
                    return self._json(404, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    return self._json(500, {"errorType": type(exc).__name__, "cause": str(exc)})
                if isinstance(result, Response):
                    status, headers, body = result.render()
                    return self._send(status, headers, body, result.cookies)
                self._json(200, {"result": result})

            def _workflow(self, name: str, raw: bytes) -> None:
                function = f"{name}-orchestrator"
                req = HttpRequest(
                    method="POST",
                    path=f"/workflows/{name}",
                    headers=Headers({"content-type": "application/json"}),
                    body=raw,
                )
                try:
                    result = host.invoke(function, Event(type=EventType.HTTP, http=req))
                except KeyError:
                    return self._json(404, {"error": f"unknown workflow {name!r}"})
                except Exception as exc:  # noqa: BLE001
                    return self._json(500, {"errorType": type(exc).__name__, "cause": str(exc)})
                if isinstance(result, Response):
                    status, headers, body = result.render()
                    return self._send(status, headers, body, result.cookies)
                self._json(200, {"result": result})

            def _user(self, url: Any) -> None:
                routes = _routes(host)
                for method, pattern, function in routes:
                    params = match_path(pattern, url.path, tolerant=False)
                    if params is None or not (method_matches(method, self.command) or self.command == "OPTIONS"):
                        continue
                    req = HttpRequest(
                        method=self.command,
                        path=url.path,
                        headers=Headers(list(self.headers.items())),
                        query=dict(parse_qsl(url.query, keep_blank_values=True)),
                        path_params=params,
                        body=self._read(),
                        source_ip=self.client_address[0],
                        route=pattern,
                    )
                    resp = host.invoke(function, Event(type=EventType.HTTP, http=req))
                    if not isinstance(resp, Response):
                        resp = Response.json(resp)
                    status, headers, body = resp.render()
                    return self._send(status, headers, body, resp.cookies)
                self._json(404, {"error": "Not Found", "routes": [f"{m} {p}" for m, p, _ in routes]})

        return Handler

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": self.spec.get("service"),
            "stage": self.spec.get("stage"),
            "endpoint": self.spec.get("endpoint"),
            "services": {
                kind: {"use": (svc or {}).get("use"), "path": (svc or {}).get("path")}
                for kind, svc in (self.spec.get("services") or {}).items()
            },
            "notes": list(self.notes),
        }


def _routes(host: Host) -> List[Tuple[str, str, str]]:
    routes = []
    functions = host.manifest.functions if getattr(host, "manifest", None) else {}
    for name, spec in functions.items():
        for route in spec.routes:
            routes.append((route.method, route.path, name))
    routes.sort(key=lambda item: (item[1].count("{"), -len(item[1])))
    return routes


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def activate_layout(root: Path) -> None:
    """Swap ``staging`` (written by package) into ``app`` (what the host runs)."""
    root = Path(root)
    staging = root / "staging"
    app = root / "app"
    if not staging.is_dir():
        if not app.is_dir():
            raise FileNotFoundError(f"on-prem bundle is missing under {root}")
        return
    if app.exists():
        shutil.rmtree(app)
    staging.rename(app)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop(root: Path) -> int:
    root = Path(root)
    pidfile = root / "host.pid"
    if not pidfile.is_file():
        return 0
    try:
        pid = int(pidfile.read_text("ascii").strip() or "0")
    except ValueError:
        pid = 0
    if _pid_alive(pid):
        if sys.platform == "win32":
            subprocess.call(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _pid_alive(pid):
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    pidfile.unlink(missing_ok=True)
    return 0


def _wait_healthy(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as resp:  # noqa: S310 - local host URL from our own spec
                if resp.status == 200:
                    return True
        except (URLError, OSError, TimeoutError, ValueError):
            time.sleep(0.2)
    return False


def start(root: Path) -> int:
    """Stop any previous host, activate the new bundle, and daemonize."""
    root = Path(root)
    stop(root)
    activate_layout(root)
    log_path = root / "host.log"
    log = open(log_path, "ab")  # noqa: SIM115 - inherited by the child process
    cmd = [sys.executable, str(root / "onprem_ctl.py"), "foreground"]
    kwargs: Dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)  # noqa: S603 - fixed argv, this interpreter
    spec = json.loads((root / "pskit_host.json").read_text("utf-8"))
    endpoint = str(spec.get("endpoint") or "http://127.0.0.1:8080")
    if _wait_healthy(endpoint + "/__pskit/health", 30):
        return 0
    sys.stderr.write(f"on-prem host did not become healthy; see {log_path}\n")
    return 1


def foreground(root: Path) -> int:
    root = Path(root)
    activate_layout(root)
    host = Host(root)
    host.open()
    (root / "host.pid").write_text(str(os.getpid()), "ascii")
    try:
        host.serve_forever()
    finally:
        pidfile = root / "host.pid"
        if pidfile.is_file() and pidfile.read_text("ascii").strip() == str(os.getpid()):
            pidfile.unlink(missing_ok=True)
    return 0


def main(root: Path) -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "start"
    if command == "stop":
        return stop(root)
    if command == "foreground":
        return foreground(root)
    if command == "start":
        return start(root)
    sys.stderr.write("usage: onprem_ctl.py [start|stop|foreground]\n")
    return 2
