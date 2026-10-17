"""Build the deployment artifact (a deterministic zip).

Layout of the bundle root::

    <your sources>              filtered by package.include / package.exclude
    py_serverless_kit/          the stdlib-only runtime (vendored)
    pskit_manifest.json         functions, routes, embedded workflows
    <provider shim files>       e.g. pskit_entry.py, main.py, function_app.py
    <dependencies>              pip-installed when the provider runs no remote build
    requirements.txt            when the provider builds remotely (GCP, Azure)

The zip is byte-for-byte reproducible (sorted entries, fixed timestamps), so an
unchanged project never triggers a redeploy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional, Pattern

from ..providers.base import Artifact

if TYPE_CHECKING:  # pragma: no cover
    from ..core.project import Project

DEFAULT_EXCLUDES = (
    ".git/**",
    ".hg/**",
    ".pskit/**",
    ".terraform/**",
    ".venv/**",
    "venv/**",
    "env/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".idea/**",
    ".vscode/**",
    "node_modules/**",
    "tests/**",
    "dist/**",
    "build/**",
    "*.egg-info/**",
    "**/.DS_Store",
    ".env",
    ".env.*",
    "**/*.tfstate",
    "**/*.tfstate.*",
    "pskit*.yml",
    "pskit*.yaml",
    "serverless_kit*.yml",
)
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_RUNTIME_PKG = "py_serverless_kit"


class BuildError(Exception):
    pass


def glob_to_regex(pattern: str) -> Pattern[str]:
    """Translate a gitignore-like glob (``**`` = any depth) into a regex."""
    pattern = pattern.replace("\\", "/")
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if pattern.endswith("/"):
        pattern += "**"
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches(rel: str, patterns: Iterable[Pattern[str]]) -> bool:
    return any(p.match(rel) for p in patterns)


def collect_sources(source: Path, include: List[str], exclude: List[str]) -> List[Path]:
    inc = [glob_to_regex(p) for p in include]
    exc = [glob_to_regex(p) for p in list(DEFAULT_EXCLUDES) + list(exclude)]
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(source):
        rel_dir = Path(dirpath).relative_to(source).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        # prune excluded directories early (fast on big venvs / node_modules)
        dirnames[:] = sorted(d for d in dirnames if not _matches(f"{rel_dir}{d}/x", exc))
        for name in sorted(filenames):
            rel = f"{rel_dir}{name}"
            if _matches(rel, inc) and not _matches(rel, exc):
                files.append(Path(dirpath) / name)
    return files


def _copy_runtime(dest: Path) -> None:
    pkg_root = Path(__file__).resolve().parents[1]
    target = dest / _RUNTIME_PKG
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("__init__.py", "_version.py"):
        shutil.copy2(pkg_root / name, target / name)
    shutil.copytree(
        pkg_root / "runtime",
        target / "runtime",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (target / "py.typed").write_text("", "utf-8")


def read_requirements(path: Optional[Path]) -> List[str]:
    if path is None or not path.is_file():
        return []
    reqs = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # The runtime is vendored; never pull the full CLI (and its deps) into a function.
        if re.match(r"^py[-_]serverless[-_]kit\b", line, re.I):
            continue
        reqs.append(line)
    return reqs


def _pip_install(reqs_file: Path, target: Path, flags: List[str], extra: List[str]) -> None:
    cmd = (
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "--quiet",
            "--upgrade",
            "-r",
            str(reqs_file),
            "--target",
            str(target),
        ]
        + flags
        + extra
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BuildError(
            "pip install failed while vendoring dependencies:\n"
            + (proc.stderr or proc.stdout)[-4000:]
            + "\nHint: a dependency may have no Linux wheel for this runtime. "
            "Pin a version that ships manylinux wheels, or set `package.pip_args`."
        )


def _write_zip(build_dir: Path, zip_path: Path) -> int:
    count = 0
    tmp = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(p for p in build_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(build_dir).as_posix()
            if "/__pycache__/" in f"/{rel}" or rel.endswith(".pyc"):
                continue
            info = zipfile.ZipInfo(rel, date_time=_FIXED_DATE)
            mode = 0o755 if os.access(path, os.X_OK) and path.suffix in ("", ".sh") else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
            count += 1
    os.replace(tmp, zip_path)
    return count


def digest(path: Path) -> tuple[str, str]:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest(), base64.b64encode(h.digest()).decode("ascii")


def build_artifact(project: Project, *, skip_deps: bool = False, log=print) -> Artifact:  # noqa: ANN001
    cfg = project.config
    provider = project.provider
    work = project.work_dir
    build_dir = work / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    source = (project.root / cfg.package.source).resolve()
    files = collect_sources(source, cfg.package.include, cfg.package.exclude)
    for f in files:
        dest = build_dir / f.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
    log(f"  collected {len(files)} source file(s) from {source}")

    _copy_runtime(build_dir)
    (build_dir / "pskit_manifest.json").write_text(
        json.dumps(project.bundle_manifest(), indent=2, sort_keys=True), "utf-8"
    )
    for rel, content in provider.shim_files(cfg).items():
        dest = build_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content.encode("utf-8"))

    req_path = (project.root / cfg.package.requirements) if cfg.package.requirements else None
    user_reqs = read_requirements(req_path)
    vendor = provider.vendor_dependencies if cfg.package.vendor is None else cfg.package.vendor
    final_reqs = provider.bundle_requirements(user_reqs)
    if vendor:
        if final_reqs and not skip_deps:
            req_file = work / "requirements.vendor.txt"
            req_file.write_text("\n".join(final_reqs) + "\n", "utf-8")
            flags = provider.pip_platform_for(cfg)
            log(f"  vendoring {len(final_reqs)} requirement(s) for {cfg.provider.runtime}/{cfg.provider.architecture}")
            _pip_install(req_file, build_dir, flags, cfg.package.pip_args)
    else:
        (build_dir / "requirements.txt").write_text("\n".join(final_reqs) + ("\n" if final_reqs else ""), "utf-8")

    zip_path = work / "artifact.zip"
    count = _write_zip(build_dir, zip_path)
    sha_hex, sha_b64 = digest(zip_path)
    size = zip_path.stat().st_size
    log(f"  artifact {zip_path.name}: {count} files, {size / 1024:.1f} KiB, sha256 {sha_hex[:12]}")
    return Artifact(path=zip_path, sha256=sha_hex, sha256_b64=sha_b64, size=size, file_count=count, build_dir=build_dir)
