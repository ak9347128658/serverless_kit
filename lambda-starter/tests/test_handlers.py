import json
import os
from pathlib import Path

from py_serverless_kit.core.project import Project
from py_serverless_kit.local import LocalRunner, build_event, http_event

ROOT = Path(__file__).resolve().parent.parent


def _config() -> str:
    """PSKIT_CONFIG if set, else pskit.yml, else aws.pskit.yml."""
    if os.environ.get("PSKIT_CONFIG"):
        return os.environ["PSKIT_CONFIG"]
    return str(ROOT / ("pskit.yml" if (ROOT / "pskit.yml").exists() else "aws.pskit.yml"))


def _runner():
    return LocalRunner(Project.load(_config()))


def test_hello():
    resp = _runner().invoke("hello", http_event("GET", "/hello?name=test"))
    assert resp.status == 200


def test_processor():
    result = _runner().invoke("processor", build_event("invoke", {"numbers": [1, 2, 3]}))
    assert result["sum"] == 6 and result["count"] == 3


def test_items_get_post_put():
    runner = _runner()
    assert runner.invoke("items", http_event("GET", "/items")).status == 200

    created = runner.invoke("items", http_event("POST", "/items", body={"name": "pen", "price": 2}))
    assert created.status == 201
    new_id = json.loads(created.body)["id"]

    updated = runner.invoke("items", http_event("PUT", f"/items/{new_id}", body={"name": "pen", "price": 3}))
    assert updated.status == 200

    assert runner.invoke("items", http_event("GET", "/items/999")).status == 404
