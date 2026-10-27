"""Named load balancers shared by projects and clusters (core/onprem_balancers.py), over a fake SSH server."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from py_serverless_kit.core import onprem_balancers as bal
from py_serverless_kit.core import onprem_cluster as clu
from py_serverless_kit.core import onprem_servers as srv

STAT = "cpu  100 0 100 800 0 0 0 0 0 0"


class FakeServers:
    """Each host keeps the files the load balancer scripts write; the read script answers from them."""

    def __init__(self, broken: Tuple[str, ...] = ()) -> None:
        self.files: Dict[str, Dict[str, str]] = {}  # host -> {path under pskit-cluster: text}
        self.last_script: Dict[str, str] = {}
        self.broken = set(broken)
        self.calls: List[List[str]] = []

    def __call__(self, argv: List[str], stdout: Optional[Path]) -> Tuple[int, str]:
        self.calls.append(argv)
        host = next((a.split("@")[-1].split(":")[0] for a in argv if "@" in a), "")
        if argv[0] in ("scp", "sshpass") and any(a.endswith(".sh") for a in argv):
            local = next(a for a in argv if a.endswith(".sh") and Path(a).is_file())
            self.last_script[host] = Path(local).read_text("utf-8")
            return 0, ""
        command = argv[-1]
        if host in self.broken and command.startswith("sh "):
            return 255, "ssh: connect to host: Connection timed out"
        if "/proc/stat" in command:
            return 0, (f"cpu_a={STAT}\ncpu_b={STAT}\ncpus=2\nmem_total_kb=2000000\nmem_avail_kb=1000000\n"
                       "disk_total_kb=100\ndisk_used_kb=10\nload=0 0 0\nhostname=lb\n")
        match = re.match(r"^sh pskit-cluster/(lb-[a-z0-9-]+?)(-read|-remove|-logs)?\.sh$", command)
        if not match:
            return 0, ""
        folder, kind = match.group(1), match.group(2) or ""
        files = self.files.setdefault(host, {})
        script = self.last_script[host]
        if kind == "-read":
            out = ["PSKIT_SECTION=lb", files.get(f"{folder}/lb.json", ""), "PSKIT_SECTION=routes"]
            out += [json.dumps(json.loads(text)) for path, text in sorted(files.items())
                    if path.startswith(f"{folder}/routes/")]
            out += ["PSKIT_SECTION=state", "running" if files else "missing"]
            return 0, "\n".join(out) + "\n"
        if kind == "-remove":
            for path in [p for p in files if p.startswith(folder + "/")]:
                del files[path]
            return 0, ""
        if kind == "-logs":
            return 0, "GET /xyz/ 200 upstream=10.0.0.6:8080\n"
        for rel in re.findall(r'^rm -f "\$next/(\S+?)"$', script, re.M):
            files.pop(f"{folder}/{rel}", None)
        for rel, text in re.findall(r"^cat > \"\$next/(.+?)\" <<'PSKIT_LB'\n(.*?)PSKIT_LB\n", script, re.M | re.S):
            files[f"{folder}/{rel}"] = text
        oks = re.findall(r'echo "PSKIT_LB_OK=([^"]+)"', script)
        return 0, "".join(f"PSKIT_LB_OK={label}\n" for label in oks) + "PSKIT_LB_DONE=1\n"

    def routes(self, host: str = "10.0.0.9", name: str = "edge") -> Dict[str, dict]:
        return {path.rsplit("/", 1)[-1][:-5]: json.loads(text) for path, text in self.files.get(host, {}).items()
                if path.startswith(f"lb-{name}/routes/")}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for name, host in (("app1", "10.0.0.5"), ("app2", "10.0.0.6"), ("lb1", "10.0.0.9")):
        srv.save_server(tmp_path, name, {"host": host, "user": "root", "auth": "agent"})
    return tmp_path


def _backends() -> List[dict]:
    return [{"address": "10.0.0.5", "port": 8080, "server": "app1"}, {"address": "10.0.0.6", "port": 8080,
                                                                         "server": "app2"}]


def test_create_attach_projects_by_path_and_conflicts(root):
    fake = FakeServers()
    lines: List[str] = []
    item = bal.create_balancer(root, "edge", "lb1", lines.append, algorithm="least_time", run=fake)
    assert item["url"] == "http://10.0.0.9" and item["algorithm"] == "least_time" and item["kind"] == "dynamic"
    files = fake.files["10.0.0.9"]
    assert "function M.balance(route, peers)" in files["lb-edge/pskit-lua/pskit_lb.lua"]
    assert "listen 80 default_server;" in files["lb-edge/conf.d/00-main.conf"]

    xyz = bal.make_route("pskit-xyz-dev", _backends(), path="xyz", service="xyz", stage="dev")
    abc = bal.make_route("pskit-abc-dev", _backends()[:1], path="abc", algorithm="sticky", service="abc", stage="dev")
    done = bal.set_routes(root, "edge", [xyz], lines.append, run=fake)
    bal.set_routes(root, "edge", [abc], lines.append, run=fake)
    assert done[0]["url"] == "http://10.0.0.9/xyz/" and done[0]["healthy"]
    assert set(fake.routes()) == {"p-xyz", "p-abc"}
    assert "location /xyz/ {" in fake.files["10.0.0.9"]["lb-edge/conf.d/routes/p-xyz.loc"]
    assert "pskit_lb.balance" in fake.files["10.0.0.9"]["lb-edge/conf.d/r-p-xyz.conf"]  # default: least time
    assert "hash $pskit_sticky_p_abc consistent;" in fake.files["10.0.0.9"]["lb-edge/conf.d/r-p-abc.conf"]

    # Another deployment cannot take a path; the owner moving its path drops the old route.
    with pytest.raises(bal.BalancerError, match="already serves pskit-xyz-dev"):
        bal.set_routes(root, "edge", [bal.make_route("pskit-other-dev", _backends(), path="xyz")], lines.append,
                       run=fake)
    bal.set_routes(root, "edge", [bal.make_route("pskit-xyz-dev", _backends(), path="xyz2")], lines.append, run=fake)
    assert set(fake.routes()) == {"p-xyz2", "p-abc"}

    # Changing the default algorithm re-renders routes that use it, not those with their own.
    bal.update_balancer(root, "edge", lines.append, algorithm="round_robin", weights={"app1": 3}, run=fake)
    conf = fake.files["10.0.0.9"]["lb-edge/conf.d/r-p-xyz2.conf"]
    assert "pskit_lb.balance" not in conf and "least_conn" not in conf and "weight=" not in conf
    assert "hash $pskit_sticky_p_abc" in fake.files["10.0.0.9"]["lb-edge/conf.d/r-p-abc.conf"]
    bal.update_balancer(root, "edge", lines.append, algorithm="weighted_round_robin", run=fake)
    assert "server 10.0.0.5:8080 weight=3" in fake.files["10.0.0.9"]["lb-edge/conf.d/r-p-xyz2.conf"]

    current = bal.status(root, "edge", run=fake)
    assert current["running"] and {r["url"] for r in current["routes"]} == {"http://10.0.0.9/xyz2/",
                                                                            "http://10.0.0.9/abc/"}
    assert bal.remove_owner(root, "edge", "pskit-abc-dev", lines.append, run=fake) == 1
    assert set(fake.routes()) == {"p-xyz2"}
    assert "upstream=" in bal.logs(root, "edge", run=fake)


def test_second_project_adds_the_running_balancer_and_remove_needs_force(root, tmp_path_factory):
    fake = FakeServers()
    bal.create_balancer(root, "edge", "lb1", lambda _l: None, algorithm="ip_hash", port=8000, run=fake)
    bal.set_routes(root, "edge", [bal.make_route("pskit-xyz-dev", _backends(), path="xyz")], lambda _l: None, run=fake)

    other = tmp_path_factory.mktemp("other")
    srv.save_server(other, "lb1", {"host": "10.0.0.9", "user": "root", "auth": "agent"})
    lines: List[str] = []
    item = bal.create_balancer(other, "edge", "lb1", lines.append, run=fake)
    assert item["port"] == 8000 and item["algorithm"] == "ip_hash"  # taken from the server
    assert any("already runs load balancer edge" in line for line in lines)
    bal.set_routes(other, "edge", [bal.make_route("pskit-abc-dev", _backends(), path="abc")], lambda _l: None,
                   run=fake)
    assert set(fake.routes()) == {"p-xyz", "p-abc"}  # neither project overwrote the other
    shown = {r["owner"]: r["mine"] for r in bal.status(other, "edge", run=fake)["routes"]}
    assert shown == {"pskit-abc-dev": False, "pskit-xyz-dev": False}  # no deploy record in `other` yet

    with pytest.raises(bal.BalancerError, match="also serves"):
        bal.remove_balancer(other, "edge", lambda _l: None, run=fake)
    bal.forget_balancer(other, "edge")
    assert "edge" not in bal.load_balancers(other) and fake.routes()  # still running for the first project
    bal.remove_balancer(root, "edge", lambda _l: None, force=True, run=fake)
    assert not fake.files["10.0.0.9"] and "edge" not in bal.load_balancers(root)


def test_cluster_attachment_follows_deployments(root):
    fake = FakeServers()
    lines: List[str] = []
    clusters = {"prod": {"orchestrator": "swarm", "master": "app1", "advertise": "10.0.0.5",
                         "nodes": {"app1": {"role": "master", "node": "n1", "address": "10.0.0.5"},
                                   "app2": {"role": "worker", "node": "n2", "address": "10.0.0.6"}},
                         "deployments": {"pskit-shop-dev": {"service": "shop", "stage": "dev", "port": 8080},
                                         "pskit-shop-prod": {"service": "shop", "stage": "prod", "port": 8081},
                                         "pskit-blog-dev": {"service": "blog", "stage": "dev", "port": 8082}}}}
    clu._save(root, clusters)
    bal.create_balancer(root, "edge", "lb1", lines.append, run=fake)
    bal.attach_cluster(root, "edge", "prod", lines.append, run=fake)
    routes = fake.routes()
    assert {r["path"]: r["owner"] for r in routes.values()} == {
        "shop-dev": "pskit-shop-dev", "shop-prod": "pskit-shop-prod", "blog": "pskit-blog-dev"}
    assert {b["address"] for b in routes["p-blog"]["backends"]} == {"10.0.0.5", "10.0.0.6"}
    assert clu.list_clusters(root)[0]["balancers"][0]["urls"]["pskit-blog-dev"] == "http://10.0.0.9/blog/"

    # A deployment that goes away loses its route; a project's own route is left to the project.
    clusters["prod"]["deployments"].pop("pskit-shop-prod")
    clu._save(root, clusters)
    bal.set_routes(root, "edge", [bal.make_route("pskit-blog-dev", _backends(), path="journal")], lines.append,
                   run=fake)
    bal.sync_cluster(root, "prod", lines.append, run=fake)
    assert {r["path"] for r in fake.routes().values()} == {"shop", "journal"}

    bal.detach_cluster(root, "edge", "prod", lines.append, run=fake)
    assert {r["path"] for r in fake.routes().values()} == {"journal"}
    with pytest.raises(bal.BalancerError, match="does not serve cluster prod"):
        bal.detach_cluster(root, "edge", "prod", lines.append, run=fake)


def test_unreachable_balancer_only_warns_on_deploy(root):
    fake = FakeServers()
    bal.create_balancer(root, "edge", "lb1", lambda _l: None, run=fake)
    fake.broken.add("10.0.0.9")
    lines: List[str] = []
    out = bal.sync_project(root, [{"name": "edge"}], "pskit-xyz-dev", "xyz", "dev", _backends(), lines.append, run=fake)
    assert out == [] and any("did not answer" in line for line in lines)


def test_balancer_servers_are_kept_apart(root):
    fake = FakeServers()
    clu._save(root, {"prod": {"orchestrator": "swarm", "master": "app1", "advertise": "10.0.0.5",
                              "nodes": {"app1": {"role": "master", "node": "n1", "address": "10.0.0.5"}}}})
    with pytest.raises(bal.BalancerError, match="is in cluster 'prod'"):
        bal.create_balancer(root, "edge", "app1", lambda _l: None, run=fake)
    bal.create_balancer(root, "edge", "lb1", lambda _l: None, run=fake)
    with pytest.raises(clu.ClusterError, match="runs load balancer 'edge'"):
        clu.join_server(root, "prod", "lb1", lambda _l: None, run=fake)
    with pytest.raises(bal.BalancerError, match="already exists"):
        bal.create_balancer(root, "edge", "lb1", lambda _l: None, run=fake)
    with pytest.raises(bal.BalancerError, match="name"):
        bal.create_balancer(root, "Edge", "lb1", lambda _l: None, run=fake)


@pytest.mark.parametrize("raw, expected", [
    ("edge", [{"name": "edge"}]),
    ({"name": "edge", "path": "/Shop/", "algorithm": "rr"}, [{"name": "edge", "path": "shop", "algorithm": "round_robin"}]),
    ([{"name": "a"}, "b"], [{"name": "a"}, {"name": "b"}]),
    (None, []),
])
def test_project_option(raw, expected):
    assert bal.parse_option(raw) == expected


@pytest.mark.parametrize("raw, message", [
    ({"name": "edge", "port": 1}, "unknown options.balancer keys"),
    ({"path": "x"}, "needs the load balancer's name"),
    (["edge", "edge"], "more than once"),
    ({"name": "edge", "weights": {"a": 0}}, "1..100"),
])
def test_project_option_errors(raw, message):
    with pytest.raises(ValueError, match=message):
        bal.parse_option(raw)
