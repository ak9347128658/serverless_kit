"""Typed model of ``pskit.yml``.

The model is intentionally provider-neutral. Anything that only makes sense
for one cloud goes under an ``options:`` mapping, which the matching provider
renderer reads and the others ignore.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Type, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROVIDERS = (
    "aws",
    "azure",
    "gcp",
    "alibaba",
    "oracle",
    "ibm",
    "digitalocean",
    "tencent",
    "huawei",
    "cloudflare",
    "onprem",
)
EVENT_TYPES = ("http", "schedule", "queue", "storage", "topic")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ANY")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_HANDLER_RE = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*[.:][A-Za-z_][\w]*$")
_RATE_RE = re.compile(r"^\s*(\d+)\s+(minute|minutes|hour|hours|day|days)\s*$", re.I)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ------------------------------------------------------------------ events


class CorsConfig(_Model):
    origins: List[str] = Field(default_factory=lambda: ["*"])
    methods: Optional[List[str]] = None
    headers: Optional[List[str]] = None
    expose_headers: List[str] = Field(default_factory=list)
    allow_credentials: bool = False
    max_age: int = 600


class HttpEvent(_Model):
    path: str = "/"
    method: str = "GET"
    cors: Union[bool, CorsConfig] = False
    auth: Literal["none", "iam", "jwt", "key"] = "none"
    authorizer: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        v = v.upper()
        if v == "*":
            v = "ANY"
        if v not in HTTP_METHODS:
            raise ValueError(f"method must be one of {', '.join(HTTP_METHODS)}")
        return v

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = "/" + v.strip().lstrip("/")
        if "{" in v and not re.fullmatch(r"(/([\w.~-]+|\{[A-Za-z_]\w*\}))*(/\{[A-Za-z_]\w*\+\})?/?", v):
            raise ValueError(f"invalid path template {v!r}; use /users/{{id}} or /files/{{path+}}")
        return v

    @property
    def cors_config(self) -> Optional[CorsConfig]:
        if self.cors is True:
            return CorsConfig()
        if isinstance(self.cors, CorsConfig):
            return self.cors
        return None


class ScheduleEvent(_Model):
    rate: Optional[str] = None
    cron: Optional[str] = None
    input: Any = None
    enabled: bool = True
    timezone: str = "UTC"
    name: Optional[str] = None

    @model_validator(mode="after")
    def _one_of(self) -> ScheduleEvent:
        if bool(self.rate) == bool(self.cron):
            raise ValueError("schedule needs exactly one of `rate` (e.g. '5 minutes') or `cron` (5 fields)")
        if self.rate and not _RATE_RE.match(self.rate):
            raise ValueError("rate must look like '<n> minutes|hours|days'")
        if self.cron and len(self.cron.split()) != 5:
            raise ValueError("cron must be a standard 5-field expression: 'min hour dom month dow'")
        return self


class QueueEvent(_Model):
    name: str
    existing: bool = False
    batch_size: int = Field(10, ge=1, le=10000)
    batch_window: int = Field(0, ge=0, le=300, description="seconds to wait to fill a batch")
    visibility_timeout: Optional[int] = None
    dead_letter: Optional[str] = Field(None, description="queue name receiving messages after max_receive")
    max_receive: int = 5
    fifo: bool = False


class StorageEvent(_Model):
    bucket: str
    existing: bool = False
    events: List[Literal["created", "deleted"]] = Field(default=["created"])
    prefix: Optional[str] = None
    suffix: Optional[str] = None


class TopicEvent(_Model):
    name: str
    existing: bool = False
    filter: Optional[Dict[str, Any]] = None


EventConfig = Union[HttpEvent, ScheduleEvent, QueueEvent, StorageEvent, TopicEvent]
_EVENT_MODELS: Dict[str, Type[BaseModel]] = {
    "http": HttpEvent,
    "schedule": ScheduleEvent,
    "queue": QueueEvent,
    "storage": StorageEvent,
    "topic": TopicEvent,
}


class EventSpec(_Model):
    """One trigger. Written in YAML as a single-key mapping, e.g. ``- http: {path: /x}``."""

    type: Literal["http", "schedule", "queue", "storage", "topic"]
    config: Any

    @model_validator(mode="before")
    @classmethod
    def _from_yaml(cls, raw: Any) -> Any:
        if isinstance(raw, dict) and set(raw) == {"type", "config"}:
            return raw
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError(
                "each event must be a single-key mapping such as `- http: {path: /hello}`; got " + repr(raw)
            )
        ((kind, body),) = raw.items()
        if kind not in _EVENT_MODELS:
            raise ValueError(f"unknown event type {kind!r}; expected one of {', '.join(EVENT_TYPES)}")
        if kind == "http" and isinstance(body, str):  # shorthand: `- http: GET /users/{id}`
            method, _, path = body.strip().partition(" ")
            body = {"method": method, "path": path or "/"} if path else {"path": method}
        if kind == "schedule" and isinstance(body, str):  # shorthand: `- schedule: 5 minutes`
            body = {"rate": body} if _RATE_RE.match(body) else {"cron": body}
        if kind in ("queue", "topic") and isinstance(body, str):
            body = {"name": body}
        if kind == "storage" and isinstance(body, str):
            body = {"bucket": body}
        return {"type": kind, "config": _EVENT_MODELS[kind].model_validate(body or {})}


# ------------------------------------------------------------------ function / provider


class IamStatement(_Model):
    effect: Literal["Allow", "Deny"] = "Allow"
    actions: List[str]
    resources: List[str] = Field(default_factory=lambda: ["*"])


class IamConfig(_Model):
    statements: List[IamStatement] = Field(default_factory=list)
    role: Optional[str] = Field(None, description="use an existing role / service account instead of creating one")


class FunctionConfig(_Model):
    handler: str
    description: str = ""
    runtime: Optional[str] = None
    memory: Optional[int] = Field(None, ge=64, le=32768)
    timeout: Optional[int] = Field(None, ge=1, le=3600)
    environment: Dict[str, str] = Field(default_factory=dict)
    tags: Dict[str, str] = Field(default_factory=dict)
    events: List[EventSpec] = Field(default_factory=list)
    concurrency: Optional[int] = Field(None, ge=0, description="reserved / max instances")
    architecture: Optional[Literal["x86_64", "arm64"]] = None
    iam: IamConfig = Field(default_factory=IamConfig)
    options: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("handler")
    @classmethod
    def _handler(cls, v: str) -> str:
        if not _HANDLER_RE.match(v):
            raise ValueError(f"handler {v!r} must be 'package.module.function' (or 'module:function')")
        return v

    @field_validator("environment", mode="before")
    @classmethod
    def _env(cls, v: Any) -> Any:
        return {str(k): "" if val is None else str(val) for k, val in (v or {}).items()}

    def events_of(self, kind: str) -> List[Any]:
        return [e.config for e in self.events if e.type == kind]

    @property
    def kind(self) -> str:
        """Human label used by the dashboard: http / scheduled / event-driven / invoke-only."""
        types = {e.type for e in self.events}
        if not types:
            return "invoke-only"
        if types == {"http"}:
            return "http"
        if types == {"schedule"}:
            return "scheduled"
        if "http" in types:
            return "http+event"
        return "event-driven"


class StateBackend(_Model):
    backend: Optional[str] = Field(None, description="terraform backend type, e.g. s3, gcs, azurerm, http")
    config: Dict[str, Any] = Field(default_factory=dict)


class TerraformSettings(_Model):
    binary: Optional[str] = Field(None, description="terraform or tofu; default autodetect")
    required_version: str = ">= 1.5.0"
    provider_version: Optional[str] = None
    extra: Dict[str, Any] = Field(
        default_factory=dict, description="raw Terraform JSON deep-merged into the generated config"
    )


class ProviderConfig(_Model):
    # Built-in names are listed in PROVIDERS; third-party providers registered through the
    # `py_serverless_kit.providers` entry point are accepted too (checked by the loader).
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    region: Optional[str] = None
    stage: str = "dev"
    runtime: str = "python3.12"
    memory: int = Field(256, ge=64, le=32768)
    timeout: int = Field(30, ge=1, le=3600)
    architecture: Literal["x86_64", "arm64"] = "x86_64"
    environment: Dict[str, str] = Field(default_factory=dict)
    tags: Dict[str, str] = Field(default_factory=dict)
    iam: IamConfig = Field(default_factory=IamConfig)
    log_retention_days: Optional[int] = 14
    options: Dict[str, Any] = Field(default_factory=dict)
    state: StateBackend = Field(default_factory=StateBackend)
    terraform: TerraformSettings = Field(default_factory=TerraformSettings)

    @field_validator("environment", mode="before")
    @classmethod
    def _env(cls, v: Any) -> Any:
        return {str(k): "" if val is None else str(val) for k, val in (v or {}).items()}

    @field_validator("runtime")
    @classmethod
    def _runtime(cls, v: str) -> str:
        if not re.fullmatch(r"python3\.(9|1[0-4])", v):
            raise ValueError("runtime must be python3.9 .. python3.14 (provider support varies)")
        return v

    @field_validator("stage")
    @classmethod
    def _stage(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,15}", v):
            raise ValueError("stage must be lowercase alphanumeric/dash, max 16 chars, e.g. dev, prod")
        return v


class PackageConfig(_Model):
    source: str = "."
    include: List[str] = Field(default_factory=lambda: ["**/*"])
    exclude: List[str] = Field(default_factory=list)
    requirements: Optional[str] = "requirements.txt"
    pip_args: List[str] = Field(default_factory=list)
    vendor: Optional[bool] = Field(None, description="force (true) or skip (false) pip install into the bundle")


# ------------------------------------------------------------------ workflows

StateType = Literal["task", "choice", "wait", "pass", "parallel", "map", "succeed", "fail"]


class RetryRule(_Model):
    errors: List[str] = Field(default_factory=lambda: ["*"])
    max_attempts: int = Field(3, ge=0, le=99)
    interval_seconds: float = Field(1, ge=0)
    backoff_rate: float = Field(2.0, ge=1)
    max_delay_seconds: Optional[float] = None


class CatchRule(_Model):
    errors: List[str] = Field(default_factory=lambda: ["*"])
    next: str
    result_path: Optional[str] = "$.error"


class WorkflowState(_Model):
    model_config = ConfigDict(extra="forbid")

    type: StateType
    function: Optional[str] = None
    next: Optional[str] = None
    end: bool = False
    comment: Optional[str] = None
    input_path: Optional[str] = None
    result_path: Optional[str] = "$"
    retry: List[RetryRule] = Field(default_factory=list)
    catch: List[CatchRule] = Field(default_factory=list)
    timeout_seconds: Optional[int] = None
    # choice
    choices: List[Dict[str, Any]] = Field(default_factory=list)
    default: Optional[str] = None
    # wait
    seconds: Optional[float] = None
    seconds_path: Optional[str] = None
    # pass
    result: Any = None
    # parallel / map
    branches: List[WorkflowDefinition] = Field(default_factory=list)
    items_path: Optional[str] = None
    iterator: Optional[WorkflowDefinition] = None
    max_concurrency: Optional[int] = None
    # fail
    error: Optional[str] = None
    cause: Optional[str] = None

    @model_validator(mode="after")
    def _shape(self) -> WorkflowState:
        t = self.type
        if t == "task" and not self.function:
            raise ValueError("task state needs `function`")
        if t == "choice" and not self.choices:
            raise ValueError("choice state needs `choices`")
        if t == "wait" and self.seconds is None and not self.seconds_path:
            raise ValueError("wait state needs `seconds` or `seconds_path`")
        if t == "parallel" and not self.branches:
            raise ValueError("parallel state needs `branches`")
        if t == "map" and self.iterator is None:
            raise ValueError("map state needs `iterator`")
        if t in ("task", "wait", "pass", "parallel", "map") and not (self.next or self.end):
            self.end = True
        return self


class WorkflowDefinition(_Model):
    start_at: str
    states: Dict[str, WorkflowState]

    @model_validator(mode="after")
    def _graph(self) -> WorkflowDefinition:
        names = set(self.states)
        if self.start_at not in names:
            raise ValueError(f"start_at {self.start_at!r} is not a defined state")
        for name, st in self.states.items():
            targets = [st.next, st.default] + [c.next for c in st.catch] + [r.get("next") for r in st.choices]
            for target in targets:
                if target and target not in names:
                    raise ValueError(f"state {name!r} points to unknown state {target!r}")
            for rule in st.choices:
                if "next" not in rule:
                    raise ValueError(f"choice rule in {name!r} has no `next`: {rule}")
        return self

    def task_functions(self) -> List[str]:
        found: List[str] = []
        for st in self.states.values():
            if st.function:
                found.append(st.function)
            for branch in st.branches:
                found.extend(branch.task_functions())
            if st.iterator is not None:
                found.extend(st.iterator.task_functions())
        return found


class WorkflowConfig(WorkflowDefinition):
    description: str = ""
    engine: Literal["auto", "native", "embedded"] = "auto"
    timeout: Optional[int] = None
    options: Dict[str, Any] = Field(default_factory=dict)


WorkflowState.model_rebuild()


# ------------------------------------------------------------------ root


class ServiceConfig(_Model):
    service: str
    provider: ProviderConfig
    package: PackageConfig = Field(default_factory=PackageConfig)
    functions: Dict[str, FunctionConfig] = Field(default_factory=dict)
    workflows: Dict[str, WorkflowConfig] = Field(default_factory=dict)
    hooks: Dict[str, List[str]] = Field(default_factory=dict)
    custom: Dict[str, Any] = Field(default_factory=dict, description="free-form values for ${self:custom.x}")

    @field_validator("service")
    @classmethod
    def _service(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", v):
            raise ValueError("service must be 2-32 chars of lowercase letters, digits and dashes")
        return v

    @field_validator("functions", "workflows")
    @classmethod
    def _names(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        for name in v:
            if not _NAME_RE.match(name):
                raise ValueError(f"name {name!r} must start with a letter and use letters, digits, - or _")
        return v

    @field_validator("hooks")
    @classmethod
    def _hooks(cls, v: Dict[str, List[str]]) -> Dict[str, List[str]]:
        allowed = {"before_package", "after_package", "before_deploy", "after_deploy", "before_remove", "after_remove"}
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(f"unknown hooks {sorted(unknown)}; allowed: {sorted(allowed)}")
        return v

    @model_validator(mode="after")
    def _cross_refs(self) -> ServiceConfig:
        for wf_name, wf in self.workflows.items():
            for fn in wf.task_functions():
                if fn not in self.functions:
                    raise ValueError(f"workflow {wf_name!r} references unknown function {fn!r}")
        seen: Dict[str, str] = {}
        for fn_name, fn_cfg in self.functions.items():
            for ev in fn_cfg.events_of("http"):
                key = f"{ev.method} {ev.path.rstrip('/') or '/'}"
                if key in seen:
                    raise ValueError(f"route {key} is used by both {seen[key]!r} and {fn_name!r}")
                seen[key] = fn_name
        return self

    # convenience -----------------------------------------------------
    def memory_of(self, fn: FunctionConfig) -> int:
        return fn.memory or self.provider.memory

    def timeout_of(self, fn: FunctionConfig) -> int:
        return fn.timeout or self.provider.timeout

    def runtime_of(self, fn: FunctionConfig) -> str:
        return fn.runtime or self.provider.runtime

    def architecture_of(self, fn: FunctionConfig) -> str:
        return fn.architecture or self.provider.architecture
