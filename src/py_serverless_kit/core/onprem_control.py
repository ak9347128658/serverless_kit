"""Talk to a deployed on-premises host's control API (``/__pskit/...``).

Used by ``pskit logs``, ``pskit workflow ...`` and the live dashboard. The admin
token is read from the generated ``pskit_host.json`` and only ever sent to the
host itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .project import WORK_DIR


class HostUnavailable(OSError):
    """The host did not answer (not running, wrong address, firewall...)."""


class HostControl:
    def __init__(self, base_url: str, token: str = "", timeout: float = 10.0) -> None:
        if not base_url.lower().startswith(("http://", "https://")):
            raise ValueError(f"on-prem control URL must be http(s), got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        url = self.base_url + path
        params = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
        if params:
            url += "?" + urlencode(params)
        data = None if body is None else json.dumps(body, default=str).encode("utf-8")
        req = Request(url, data=data, method=method)  # noqa: S310 - http(s) checked in __init__
        req.add_header("accept", "application/json")
        if data is not None:
            req.add_header("content-type", "application/json")
        if self.token:
            req.add_header("x-pskit-token", self.token)
        try:
            with urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310
                raw = resp.read()
        except HTTPError as exc:
            message = _error_text(exc)
            if exc.code == 404:
                raise KeyError(message) from None
            if exc.code in (400, 405, 413):
                raise ValueError(message) from None
            if exc.code == 401:
                raise PermissionError(f"{message} (check provider.options.admin_token)") from None
            raise HostUnavailable(f"{self.base_url} answered HTTP {exc.code}: {message}") from None
        except (URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HostUnavailable(f"on-prem host at {self.base_url} is not reachable: {reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise HostUnavailable(f"{self.base_url} returned a non-JSON response") from None

    # ------------------------------------------------------------------ helpers
    def health(self) -> Dict[str, Any]:
        return dict(self.request("GET", "/__pskit/health", timeout=3) or {})

    def stats(self) -> Dict[str, Any]:
        return dict(self.request("GET", "/__pskit/stats") or {})

    def logs(
        self,
        since: int = 0,
        limit: int = 200,
        level: Optional[str] = None,
        function: Optional[str] = None,
        q: Optional[str] = None,
        source: Optional[str] = None,
        before: Optional[int] = None,
    ) -> Dict[str, Any]:
        query = {
            "since": since,
            "limit": limit,
            "level": level,
            "function": function,
            "q": q,
            "source": source,
            "before": before,
        }
        return dict(self.request("GET", "/__pskit/logs", query=query) or {})

    def start_workflow(self, workflow: str, data: Any, wait: bool = False) -> Dict[str, Any]:
        path = "/__pskit/workflows/" + quote(workflow, safe="")
        return dict(
            self.request(
                "POST", path, body=data, query={"wait": "true" if wait else None}, timeout=None if wait else 15
            )
            or {}
        )

    def executions(
        self, workflow: Optional[str] = None, status: Optional[str] = None, limit: int = 20, offset: int = 0
    ) -> Dict[str, Any]:
        query = {"workflow": workflow, "status": status, "limit": limit, "offset": offset}
        return dict(self.request("GET", "/__pskit/executions", query=query) or {})

    def execution(self, execution_id: str) -> Dict[str, Any]:
        return dict(self.request("GET", "/__pskit/executions/" + quote(execution_id, safe="")) or {})

    def cancel(self, execution_id: str) -> Dict[str, Any]:
        return dict(self.request("POST", "/__pskit/executions/" + quote(execution_id, safe="") + "/cancel") or {})


def _error_text(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return f"HTTP {exc.code}"
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("cause") or payload)
    return str(payload)


def record_key(record: Dict[str, Any]) -> str:
    return str(record.get("key") or f"{record.get('provider')}-{record.get('stage')}")


def host_spec(record: Dict[str, Any], root: Path) -> Optional[Dict[str, Any]]:
    path = Path(root) / WORK_DIR / record_key(record) / "onprem_app" / "pskit_host.json"
    try:
        spec = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return spec if isinstance(spec, dict) else None


def control_url(spec: Dict[str, Any], record: Optional[Dict[str, Any]] = None) -> str:
    """Loopback for a host on this machine, otherwise the advertised endpoint."""
    from ..runtime.onprem_host import health_url

    method = str((spec.get("deploy") or {}).get("method") or "local")
    if method == "local":
        return health_url(spec)[: -len("/__pskit/health")]
    endpoint = spec.get("endpoint") or (record or {}).get("endpoint")
    if not endpoint:
        return health_url(spec)[: -len("/__pskit/health")]
    return str(endpoint).rstrip("/")


def control_for(record: Dict[str, Any], root: Path) -> Optional[HostControl]:
    """A client for the deployment's host, or None when it is not an on-prem deployment."""
    if record.get("provider") != "onprem":
        return None
    spec = host_spec(record, root)
    if spec is None:
        endpoint = record.get("endpoint")
        if not endpoint:
            return None
        return HostControl(str(endpoint))
    return HostControl(control_url(spec, record), token=str(spec.get("admin_token") or ""))
