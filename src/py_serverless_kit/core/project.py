"""A loaded project: config + provider + working directory, ready to build/deploy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config.loader import LoadedConfig, load_config
from ..config.schema import EventSpec, FunctionConfig, HttpEvent, ServiceConfig
from ..providers.base import HTTP, ORCHESTRATOR_HANDLER, Issue, Provider
from ..providers.registry import get_provider
from ..workflows import definition_dict

WORK_DIR = ".pskit"


def orchestrator_name(workflow: str) -> str:
    return f"{workflow}-orchestrator"


@dataclass
class Project:
    loaded: LoadedConfig
    provider: Provider
    config: ServiceConfig
    engines: Dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(
        cls,
        config_path: Optional[str] = None,
        *,
        provider: Optional[str] = None,
        stage: Optional[str] = None,
        region: Optional[str] = None,
        cwd: Optional[Path] = None,
    ) -> Project:
        loaded = load_config(config_path, provider=provider, stage=stage, region=region, cwd=cwd)
        return cls.from_loaded(loaded)

    @classmethod
    def from_loaded(cls, loaded: LoadedConfig) -> Project:
        prov = get_provider(loaded.config.provider.name)
        config = loaded.config
        if "runtime" not in (loaded.raw.get("provider") or {}):
            # Not pinned by the user: pick what this cloud supports.
            provider_cfg = config.provider.model_copy(update={"runtime": prov.default_runtime})
            config = config.model_copy(update={"provider": provider_cfg})
        config, engines = expand(config, prov)
        return cls(loaded=loaded, provider=prov, config=config, engines=engines)

    # ---------------------------------------------------------------- paths
    @property
    def root(self) -> Path:
        return self.loaded.root

    @property
    def work_dir(self) -> Path:
        return self.root / WORK_DIR / f"{self.config.provider.name}-{self.config.provider.stage}"

    @property
    def region(self) -> str:
        return self.provider.region(self.config)

    # ---------------------------------------------------------------- checks
    def validate(self) -> List[Issue]:
        issues = self.provider.validate(self.config)
        source = (self.root / self.config.package.source).resolve()
        if not source.is_dir():
            issues.append(Issue("error", f"package.source {source} is not a directory", "package.source"))
        for name, fn in self.config.functions.items():
            if fn.handler == ORCHESTRATOR_HANDLER:
                continue
            module = fn.handler.replace(":", ".").rsplit(".", 1)[0]
            rel = Path(*module.split("."))
            if not ((source / rel).with_suffix(".py").is_file() or (source / rel / "__init__.py").is_file()):
                issues.append(
                    Issue("warning", f"handler module {module!r} not found under {source}", f"functions.{name}.handler")
                )
        return issues

    # ---------------------------------------------------------------- bundle manifest
    def bundle_manifest(self) -> Dict[str, Any]:
        functions: Dict[str, Any] = {}
        for name, fn in sorted(self.config.functions.items()):
            routes = []
            for ev in fn.events_of("http"):
                cors = ev.cors_config
                routes.append(
                    {
                        "method": ev.method,
                        "path": ev.path,
                        "cors": cors.model_dump() if cors is not None else None,
                    }
                )
            functions[name] = {
                "handler": fn.handler,
                "routes": routes,
                "timeout": self.config.timeout_of(fn),
                "memory": self.config.memory_of(fn),
                "resource_name": self.provider.physical_name(self.config.service, self.config.provider.stage, name),
            }
        workflows: Dict[str, Any] = {}
        for name, wf in sorted(self.config.workflows.items()):
            if self.engines.get(name) != "embedded":
                continue
            workflows[name] = {"orchestrator": orchestrator_name(name), "definition": definition_dict(wf)}
            if wf.timeout:
                # Enforced by the embedded engine (the on-prem host has no function timeout above it).
                workflows[name]["timeout"] = wf.timeout
        return {
            "service": self.config.service,
            "stage": self.config.provider.stage,
            "provider": self.config.provider.name,
            "region": self.region,
            "functions": functions,
            "workflows": workflows,
        }


def expand(config: ServiceConfig, provider: Provider) -> tuple[ServiceConfig, Dict[str, str]]:
    """Add generated functions (embedded workflow orchestrators) to the config."""
    engines: Dict[str, str] = {}
    functions = dict(config.functions)
    for name, wf in config.workflows.items():
        try:
            engine = provider.workflow_engine(wf)
        except Exception:  # noqa: BLE001 - reported properly by Provider.validate
            engine = "embedded"
        engines[name] = engine
        if engine != "embedded":
            continue
        orch = orchestrator_name(name)
        if orch in functions:
            continue
        events: List[EventSpec] = []
        if HTTP in provider.capabilities and wf.options.get("http", True):
            events.append(EventSpec(type="http", config=HttpEvent(method="POST", path=f"/workflows/{name}")))
        functions[orch] = FunctionConfig(
            handler=ORCHESTRATOR_HANDLER,
            description=f"Embedded orchestrator for workflow {name}",
            timeout=min(wf.timeout or provider.max_timeout, provider.max_timeout),
            events=events,
            tags={"pskit-workflow": name},
        )
    if functions == config.functions:
        return config, engines
    return config.model_copy(update={"functions": functions}), engines
