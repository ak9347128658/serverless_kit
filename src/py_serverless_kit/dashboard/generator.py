"""Generate the deployment dashboard: one self-contained ``index.html``.

The page lists every deployment found under ``.pskit/*/deployment.json`` (all
providers and stages of the project) with functions, triggers, endpoints,
resource identifiers and workflows. It has no external dependencies, so it can
be opened from disk, served by ``pskit dashboard --serve``, or uploaded to any
static host / bucket.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.describe import RECORD_FILE, build_record
from ..core.project import WORK_DIR, Project

TEMPLATE = Path(__file__).with_name("template.html")


def collect_records(root: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    base = root / WORK_DIR
    if not base.is_dir():
        return records
    for path in sorted(base.glob(f"*/{RECORD_FILE}")):
        try:
            records.append(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    records.sort(key=lambda r: (r.get("service", ""), r.get("provider", ""), r.get("stage", "")))
    return records


def local_record(project: Project) -> Dict[str, Any]:
    """A record built from config only (nothing deployed yet / local dev)."""
    return build_record(project, outputs=None, status="local")


def render_html(records: List[Dict[str, Any]], title: str = "Serverless dashboard") -> str:
    payload = json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "deployments": records},
        default=str,
    )
    # Make the JSON safe inside a <script> element.
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    template = TEMPLATE.read_text("utf-8")
    return template.replace("{{TITLE}}", html.escape(title)).replace("{{DATA}}", payload)


def write_dashboard(
    root: Path,
    records: Optional[List[Dict[str, Any]]] = None,
    output: Optional[Path] = None,
    title: Optional[str] = None,
) -> Path:
    records = collect_records(root) if records is None else records
    out = output or (root / WORK_DIR / "dashboard" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    services = sorted({r.get("service", "") for r in records if r.get("service")})
    out.write_text(render_html(records, title or (" / ".join(services) or "Serverless") + " dashboard"), "utf-8")
    return out
