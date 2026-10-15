"""Provider-neutral event model.

Every provider adapter converts its native payload into an :class:`Event`, so a
handler written against this module runs unchanged on any supported cloud.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from .errors import BadRequest

_MISSING = object()


class EventType:
    HTTP = "http"
    SCHEDULE = "schedule"
    QUEUE = "queue"
    STORAGE = "storage"
    TOPIC = "topic"
    INVOKE = "invoke"
    WORKFLOW = "workflow"

    ALL = (HTTP, SCHEDULE, QUEUE, STORAGE, TOPIC, INVOKE, WORKFLOW)


class Headers(Mapping[str, str]):
    """Case-insensitive, read-only header mapping.

    Multi-value headers are joined with ``", "`` (RFC 9110 section 5.3).
    """

    __slots__ = ("_data",)

    def __init__(self, raw: Union[Mapping[str, Any], List[Tuple[str, Any]], None] = None) -> None:
        self._data: Dict[str, str] = {}
        items = raw.items() if isinstance(raw, Mapping) else (raw or [])
        for key, value in items:
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            k = str(key).lower()
            self._data[k] = f"{self._data[k]}, {value}" if k in self._data else str(value)

    def __getitem__(self, key: str) -> str:
        return self._data[key.lower()]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return self._data.get(key.lower(), default)

    def to_dict(self) -> Dict[str, str]:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"Headers({self._data!r})"


def _decode_json(raw: Union[str, bytes], what: str) -> Any:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if raw == "":
        raise BadRequest(f"{what} is empty, expected JSON")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadRequest(f"{what} is not valid JSON", details=str(exc)) from exc


def parse_cookies(header: Optional[str]) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    if not header:
        return cookies
    for part in header.split(";"):
        if "=" in part:
            name, _, value = part.strip().partition("=")
            cookies[name] = value
    return cookies


@dataclass
class HttpRequest:
    method: str = "GET"
    path: str = "/"
    headers: Headers = field(default_factory=Headers)
    query: Dict[str, str] = field(default_factory=dict)
    multi_query: Dict[str, List[str]] = field(default_factory=dict)
    path_params: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    source_ip: Optional[str] = None
    route: Optional[str] = None

    def __post_init__(self) -> None:
        self.method = self.method.upper()
        if not isinstance(self.headers, Headers):
            self.headers = Headers(self.headers)
        if isinstance(self.body, str):
            self.body = self.body.encode("utf-8")
        elif self.body is None:
            self.body = b""
        if self.query and not self.multi_query:
            self.multi_query = {k: [v] for k, v in self.query.items()}

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def cookies(self) -> Dict[str, str]:
        return parse_cookies(self.headers.get("cookie"))

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding)

    def json(self, default: Any = _MISSING) -> Any:
        """Parse the body as JSON. Raises :class:`BadRequest` (HTTP 400) on bad input
        unless ``default`` is supplied."""
        try:
            return _decode_json(self.body, "Request body")
        except BadRequest:
            if default is not _MISSING:
                return default
            raise

    def form(self) -> Dict[str, str]:
        from urllib.parse import parse_qsl

        return dict(parse_qsl(self.text(), keep_blank_values=True))

    @staticmethod
    def decode_body(body: Any, is_base64: bool = False) -> bytes:
        if body is None:
            return b""
        if isinstance(body, (dict, list)):
            return json.dumps(body).encode("utf-8")
        if isinstance(body, bytes):
            return base64.b64decode(body) if is_base64 else body
        if is_base64:
            return base64.b64decode(body)
        return str(body).encode("utf-8")


@dataclass
class Record:
    """One message / object notification inside a batch event."""

    id: Optional[str] = None
    body: Any = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    # Storage-only fields.
    bucket: Optional[str] = None
    key: Optional[str] = None
    size: Optional[int] = None
    action: Optional[str] = None  # "created" | "deleted"
    raw: Any = None

    def text(self) -> str:
        if isinstance(self.body, bytes):
            return self.body.decode("utf-8")
        if isinstance(self.body, (dict, list)):
            return json.dumps(self.body)
        return "" if self.body is None else str(self.body)

    def json(self, default: Any = _MISSING) -> Any:
        if isinstance(self.body, (dict, list)):
            return self.body
        try:
            return _decode_json(self.text(), f"Record {self.id or ''} body".strip())
        except BadRequest:
            if default is not _MISSING:
                return default
            raise


@dataclass
class Event:
    type: str
    provider: str = "local"
    function: str = ""
    http: Optional[HttpRequest] = None
    records: List[Record] = field(default_factory=list)
    data: Any = None
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: Optional[str] = None
    raw: Any = None

    @property
    def is_http(self) -> bool:
        return self.type == EventType.HTTP

    @property
    def is_batch(self) -> bool:
        return self.type in (EventType.QUEUE, EventType.TOPIC, EventType.STORAGE)

    def json(self, default: Any = _MISSING) -> Any:
        """Best-effort JSON payload regardless of event type.

        * HTTP: the parsed request body
        * queue/topic/storage: the first record's parsed body
        * schedule/invoke/workflow: ``data``
        """
        if self.http is not None:
            return self.http.json(default) if default is not _MISSING else self.http.json()
        if self.records:
            return self.records[0].json(default) if default is not _MISSING else self.records[0].json()
        return self.data

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly summary, handy for logging."""
        out: Dict[str, Any] = {
            "type": self.type,
            "provider": self.provider,
            "function": self.function,
            "time": self.time.isoformat(),
            "source": self.source,
        }
        if self.http is not None:
            out["http"] = {
                "method": self.http.method,
                "path": self.http.path,
                "query": self.http.query,
                "path_params": self.http.path_params,
            }
        if self.records:
            out["records"] = len(self.records)
        if self.data is not None:
            out["data"] = self.data
        return out


def parse_time(value: Any) -> datetime:
    """Parse the many timestamp shapes providers emit; fall back to *now*."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str) and value:
        text = value.strip()
        if text.isdigit():
            return parse_time(int(text))
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
