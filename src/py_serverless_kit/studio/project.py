"""Read, validate and save ``pskit.yml`` for the studio.

The studio edits the YAML document *as written* (``raw``): ``${env:...}`` and
other variables stay strings and survive a round trip. Validation runs the real
loader on a temporary draft file next to ``pskit.yml``, so ``extends``,
``${file:...}`` and ``package.source`` resolve exactly as ``pskit deploy`` would.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..config.loader import DEFAULT_FILES, ConfigError, LoadedConfig, build_raw
from ..config.schema import PROVIDERS, ServiceConfig

HEADER = "# Edited with pskit studio. https://github.com/ak9347128658/serverless_kit\n"
BACKUP_DIR = Path(".pskit") / "studio" / "backups"
KEEP_BACKUPS = 20
_HANDLER_RE = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)[.:]([A-Za-z_]\w*)$")


class StudioError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def issue(level: str, message: str, where: str = "") -> Dict[str, str]:
    return {"level": level, "message": message, "where": where}


def config_path(root: Path) -> Optional[Path]:
    for name in DEFAULT_FILES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def dump_yaml(raw: Dict[str, Any]) -> str:
    text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, default_flow_style=False, width=120)
    return HEADER + text


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


def _has_comments(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith(HEADER.strip()[:24]):
            return True
        if " #" in line and not ("'" in line or '"' in line):
            return True
    return False


# --------------------------------------------------------------------- read / parse


def parse_text(text: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        return None, [issue("error", f"invalid YAML: {problem}", where)]
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, [issue("error", "the top level of pskit.yml must be a mapping", "")]
    bad = [k for k in data if not isinstance(k, str)]
    if bad:
        return None, [issue("error", f"top-level keys must be strings, got {bad!r}", "")]
    return data, []


def get_project(root: Path) -> Dict[str, Any]:
    path = config_path(root)
    if path is None:
        return {
            "exists": False,
            "path": "pskit.yml",
            "raw": None,
            "text": "",
            "mtime": None,
            "issues": [],
            "notices": ["No pskit.yml here yet: create a project with the wizard."],
        }
    text = path.read_text("utf-8")
    raw, issues = parse_text(text)
    notices: List[str] = []
    if raw is not None:
        if raw.get("extends"):
            notices.append(
                f"This file extends {raw['extends']!r}. The studio edits only {path.name}; values from the parent "
                "file still apply when validating."
            )
        if _has_comments(text):
            notices.append("Comments in this file are not kept when the studio saves it (a backup is written first).")
        issues = validate(root, raw)["issues"]
    return {
        "exists": True,
        "path": path.name,
        "raw": raw,
        "text": text,
        "mtime": _mtime(path),
        "issues": issues,
        "notices": notices,
    }


# --------------------------------------------------------------------- validate


def _where(loc: Tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if part == "config" or (isinstance(part, str) and part.startswith("function-")):
            continue
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out


def validate(
    root: Path,
    raw: Any,
    provider: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a draft the way ``pskit validate`` would; never raises for config problems."""
    from pydantic import ValidationError

    from ..core.project import Project

    result: Dict[str, Any] = {"ok": False, "issues": [], "yaml": "", "functions": [], "engines": {}}
    if not isinstance(raw, dict):
        result["issues"] = [issue("error", "the configuration must be a mapping", "")]
        return result
    result["yaml"] = dump_yaml(raw)
    existing = config_path(root)
    folder = existing.parent if existing is not None else root
    draft = folder / f".pskit-studio-draft-{uuid.uuid4().hex[:12]}.yml"
    try:
        draft.write_bytes(result["yaml"].encode("utf-8"))
        try:
            data = build_raw(draft, provider=provider or None, stage=stage or None)
        except ConfigError as exc:
            result["issues"] = [issue("error", str(exc).replace(draft.name, "pskit.yml"), "")]
            return result
        try:
            config = ServiceConfig.model_validate(data)
        except ValidationError as exc:
            result["issues"] = [
                issue("error", str(err["msg"]).replace("Value error, ", "", 1), _where(tuple(err["loc"])))
                for err in exc.errors()
            ]
            return result
        loaded = LoadedConfig(config=config, path=draft, root=draft.parent.resolve(), raw=data)
        try:
            project = Project.from_loaded(loaded)
            found = project.validate()
        except Exception as exc:  # noqa: BLE001 - a provider bug must show up as an issue, not a 500
            result["issues"] = [issue("error", f"{type(exc).__name__}: {exc}", "")]
            return result
        result["issues"] = [issue(i.level, i.message, i.where) for i in found]
        result["functions"] = [
            {
                "name": name,
                "generated": fn.handler.startswith("py_serverless_kit."),
                "kind": fn.kind,
                "handler": fn.handler,
            }
            for name, fn in sorted(project.config.functions.items())
        ]
        result["engines"] = dict(project.engines)
        result["provider"] = project.config.provider.name
        result["stage"] = project.config.provider.stage
        result["ok"] = not any(i["level"] == "error" for i in result["issues"])
        return result
    finally:
        try:
            draft.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------- save


def save_project(root: Path, raw: Any, mtime: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
    if not isinstance(raw, dict) or any(not isinstance(k, str) for k in raw):
        raise StudioError(400, "the configuration must be a mapping with string keys")
    path = config_path(root) or (root / "pskit.yml")
    if path.is_file():
        current = _mtime(path)
        if mtime is not None and not force and abs(current - float(mtime)) > 0.0005:
            raise StudioError(409, "pskit.yml changed on disk since it was loaded", code="conflict", mtime=current)
        backup_dir = root / BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        shutil.copy2(path, backup_dir / f"{path.stem}.{stamp}.yml")
        backups = sorted(backup_dir.glob(f"{path.stem}.*.yml"))
        for old in backups[:-KEEP_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(dump_yaml(raw).encode("utf-8"))
    os.replace(tmp, path)
    return get_project(root)


def init_project(root: Path, service: str, provider: str, template: str, force: bool = False) -> Dict[str, Any]:
    from ..cli.templates import render

    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", service or ""):
        raise StudioError(400, "service must be 2-32 chars of lowercase letters, digits and dashes")
    if provider not in PROVIDERS:
        raise StudioError(400, f"provider must be one of {', '.join(PROVIDERS)}")
    if template not in ("http", "events", "workflow", "full"):
        raise StudioError(400, "template must be http, events, workflow or full")
    if config_path(root) is not None and not force:
        raise StudioError(409, "a pskit.yml already exists here", code="exists")
    created = []
    for rel, content in render(template, service, provider).items():
        target = root / rel
        if target.exists() and not (force and rel == "pskit.yml"):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.replace("\r\n", "\n").encode("utf-8"))
        created.append(rel)
    out = get_project(root)
    out["created"] = created
    return out


# --------------------------------------------------------------------- handler stubs


def _source_dir(root: Path) -> Path:
    path = config_path(root)
    source = "."
    if path is not None:
        raw, _ = parse_text(path.read_text("utf-8"))
        package = (raw or {}).get("package") or {}
        if isinstance(package, dict) and isinstance(package.get("source"), str):
            source = package["source"]
    resolved = (root / source).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise StudioError(400, "package.source points outside the project") from None
    return resolved


def ensure_handler(root: Path, handler: str, events: Optional[List[str]] = None) -> Dict[str, Any]:
    from .meta import HANDLER_TEMPLATES, TEMPLATE_IMPORTS

    match = _HANDLER_RE.match(str(handler or "").strip())
    if not match:
        raise StudioError(400, f"handler {handler!r} must look like 'package.module.function'")
    module, func = match.group(1), match.group(2)
    kinds = [e for e in (events or []) if e in HANDLER_TEMPLATES]
    kind = kinds[0] if kinds else "invoke"
    source = _source_dir(root)
    parts = module.split(".")
    package_dir = source.joinpath(*parts)
    if (package_dir / "__init__.py").is_file():
        path = package_dir / "__init__.py"
    else:
        path = source.joinpath(*parts[:-1], parts[-1] + ".py")
    rel_parent = source
    for part in parts[:-1]:  # make every parent a package
        rel_parent = rel_parent / part
        rel_parent.mkdir(parents=True, exist_ok=True)
        init = rel_parent / "__init__.py"
        if not init.exists():
            init.write_bytes(b"")
    text = path.read_text("utf-8") if path.is_file() else ""
    try:
        tree = ast.parse(text) if text else ast.Module(body=[], type_ignores=[])
    except SyntaxError as exc:
        raise StudioError(
            400, f"{path.name} has a syntax error at line {exc.lineno}; fix it before adding handlers"
        ) from None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return {"path": path.relative_to(root.resolve()).as_posix(), "created": False, "line": node.lineno}
    needed = set(TEMPLATE_IMPORTS.get(kind, ("Context", "Event")))
    have: set = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "py_serverless_kit":
            have.update(a.asname or a.name for a in node.names)
    missing = sorted(needed - have)
    lines = text.splitlines()
    if not text:
        lines = ['"""Handlers created with pskit studio. This code runs unchanged on every supported cloud."""', ""]
    if missing:
        insert_at = 0
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) or (
                isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)
            ):
                insert_at = getattr(node, "end_lineno", node.lineno)
            else:
                break
        if not text:
            insert_at = 2
        lines.insert(insert_at, f"from py_serverless_kit import {', '.join(missing)}")
    body = "\n".join(lines).rstrip("\n") + "\n"
    stub = HANDLER_TEMPLATES[kind].replace("__FUNC__", func)
    body += "\n\n" + stub
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))
    line = body.splitlines().index(next(ln for ln in body.splitlines() if ln.startswith(f"def {func}("))) + 1
    return {"path": path.relative_to(root.resolve()).as_posix(), "created": True, "line": line}
