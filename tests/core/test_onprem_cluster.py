"""Scaling clusters (Swarm / Kubernetes / Nomad): scripts, manifests, the SSH flows, status, alerts and deploy."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from py_serverless_kit.core import onprem_cluster as clu
from py_serverless_kit.core import onprem_cluster_scripts as scripts
from py_serverless_kit.core import onprem_servers as srv

STAT_A = "cpu  100 0 100 800 0 0 0 0 0 0"
STAT_B = "cpu  190 0 190 820 0 0 0 0 0 0"  # 180 busy of 200 -> 90 %


class FakeSSH:
    """Records every command; answers scripts by their file name and metrics by server."""

    def __init__(self, nodes: Optional[dict] = None) -> None:
        self.calls: List[List[str]] = []
        self.scripts: List[Tuple[str, str]] = []  # (host, script text)
        self.nodes = nodes or {}
        self.status_out = ""
        self.has_image = False

    def __call__(self, argv: List[str], stdout: Optional[Path]) -> Tuple[int, str]:
        self.calls.append(argv)
        if argv[0] in ("scp", "sshpass") and any(a.endswith(".sh") for a in argv):
            local = next(a for a in argv if a.endswith(".sh") and Path(a).is_file())
            self.scripts.append((argv[-1].split("@")[-1].split(":")[0], Path(local).read_text("utf-8")))
            return 0, ""
        command = argv[-1]
        host = next((a.split("@")[-1] for a in argv if "@" in a), "")
        if command.startswith("sh pskit-cluster/master.sh") or command.startswith("sh pskit-cluster/join.sh"):
            return 0, f"installed\nPSKIT_NODE=node-{host}\n"
        if command.startswith("sh pskit-cluster/token.sh"):
            return 0, "PSKIT_TOKEN=SWMTKN-1-secret\n"
        if command.startswith("sh pskit-cluster/status.sh"):
            return 0, self.status_out
        if "/proc/stat" in command:
            if host not in self.nodes:
                return 255, "ssh: connect to host: Connection timed out"
            cpu_b, mem_avail = self.nodes[host]
            return 0, (f"cpu_a={STAT_A}\ncpu_b={cpu_b}\ncpus=4\nmem_total_kb=4000000\nmem_avail_kb={mem_avail}\n"
                       "disk_total_kb=1000000\ndisk_used_kb=500000\nload=0.5 0.4 0.3\nhostname=x\n")
        if "image inspect" in command or "images ls" in command:
            return (0 if self.has_image else 1), ""
        if command.startswith("curl -fsS http://127.0.0.1:4646/v1/nodes"):
            return 0, json.dumps([{"Name": "node-10.0.0.6", "ID": "abc-123"}])
        return 0, ""

    def script(self, name_part: str) -> str:
        return next(text for _host, text in reversed(self.scripts) if name_part in text)


@pytest.fixture
def servers(tmp_path: Path) -> Path:
    for name, host in (("edge1", "10.0.0.5"), ("edge2", "10.0.0.6"), ("edge3", "10.0.0.7")):
        srv.save_server(tmp_path, name, {"host": host, "user": "root", "auth": "agent"})
    return tmp_path


def _sh_ok(text: str) -> None:
    """The script parses as POSIX sh (when a shell is available here)."""
    shell = shutil.which("sh") or shutil.which("bash")
    if shell is None:
        return
    proc = subprocess.run([shell, "-n"], input=text.encode("utf-8"), capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()


# ------------------------------------------------------------------------------------------- scripts
@pytest.mark.parametrize("orch", ["swarm", "kubernetes", "nomad"])
def test_every_script_is_valid_sh(orch):
    manifests = {
        "swarm": scripts.swarm_stack("pskit-shop-dev", "pskit-shop-dev:abc", port=8080, replicas=2, master_node="m",
                                     cpu=0.25, memory_mb=256),
        "kubernetes": scripts.kubernetes_manifest("pskit-shop-dev", "pskit-shop-dev:abc", port=8080, replicas=2,
                                                  master_node="m", cpu=0.25, memory_mb=256),
        "nomad": scripts.nomad_job("pskit-shop-dev", "pskit-shop-dev:abc", port=8080, replicas=2, master_node="m",
                                   master_addr="10.0.0.5", cpu=0.25, memory_mb=256),
    }
    for text in (
        scripts.master_script(orch, "10.0.0.5"),
        scripts.join_token_script(orch),
        scripts.join_script(orch, "10.0.0.5", "tok'en", "10.0.0.6"),
        scripts.leave_script(orch),
        scripts.drain_script(orch, "node-b", "id-1"),
        scripts.forget_script(orch, "node-b", "id-1"),
        scripts.status_script(orch),
        scripts.image_load_script(orch, "img:1", "pskit-cluster/img.tar"),
        scripts.prune_script(orch, "pskit-shop-dev", "abc"),
        scripts.apply_script(orch, "pskit-shop-dev", manifests[orch], 8080),
        scripts.destroy_script(orch, "pskit-shop-dev"),
        scripts.scale_script(orch, "pskit-shop-dev", 4),
    ):
        assert text.startswith("#!/bin/sh\nset -eu\n")
        _sh_ok(text)


def test_scripts_install_and_join_each_orchestrator():
    swarm = scripts.master_script("swarm", "10.0.0.5")
    assert "dk swarm init --advertise-addr '10.0.0.5'" in swarm and "{{.Swarm.LocalNodeState}}" in swarm
    join = scripts.join_script("swarm", "10.0.0.5", "SWMTKN-1-x", "10.0.0.6")
    assert "dk swarm join --advertise-addr '10.0.0.6' --token 'SWMTKN-1-x' '10.0.0.5:2377'" in join
    assert "trap 'rm -f \"$0\"' EXIT" in join  # the join token does not stay on the server
    k3s = scripts.join_script("kubernetes", "10.0.0.5", "K10abc", "10.0.0.6")
    assert "K3S_URL='https://10.0.0.5:6443' K3S_TOKEN='K10abc'" in k3s and "get.k3s.io" in k3s
    assert "--tls-san 10.0.0.5" in scripts.master_script("kubernetes", "10.0.0.5")
    nomad = scripts.master_script("nomad", "10.0.0.5", "1.11.3")
    assert "releases.hashicorp.com/nomad/1.11.3/nomad_1.11.3_linux_$a.zip" in nomad
    assert "bootstrap_expect = 1" in nomad and 'servers = ["10.0.0.5:4647"]' in nomad
    worker = scripts.join_script("nomad", "10.0.0.5", "", "10.0.0.6")
    assert "server {" not in worker and 'http = "10.0.0.6"' in worker
    assert scripts.stack_name("Shop_API", "Dev") == "pskit-shop-api-dev"


def test_workloads_split_main_and_web():
    stack = scripts.swarm_stack("pskit-shop-dev", "pskit-shop-dev:abc", port=8080, replicas=3, master_node="m1",
                                cpu=0.5, memory_mb=512, python="python3")
    main, web = stack["services"]["main"], stack["services"]["web"]
    assert main["deploy"]["replicas"] == 1 and main["deploy"]["placement"]["constraints"] == ["node.hostname == m1"]
    assert main["volumes"] == ["data:/var/pskit/data"] and "ports" not in main
    assert web["deploy"]["replicas"] == 3 and web["ports"][0]["published"] == 8080
    assert web["environment"]["PSKIT_MAIN_URL"] == "http://main:8080" and web["environment"]["PSKIT_HOST_ROLE"] == "web"
    assert web["deploy"]["resources"]["reservations"] == {"cpus": "0.5", "memory": "512M"}
    assert web["healthcheck"]["test"][1] == "python3"

    k8s = scripts.kubernetes_manifest("pskit-shop-dev", "pskit-shop-dev:abc", port=8080, replicas=3, master_node="m1",
                                      cpu=0.25, memory_mb=256)
    kinds = [(i["kind"], i["metadata"]["name"]) for i in k8s["items"]]
    assert kinds == [("Namespace", "pskit"), ("Deployment", "pskit-shop-dev-main"), ("Service", "pskit-shop-dev-main"),
                     ("Deployment", "pskit-shop-dev-web"), ("Service", "pskit-shop-dev")]
    main_pod = k8s["items"][1]["spec"]["template"]["spec"]
    assert main_pod["nodeSelector"] == {"kubernetes.io/hostname": "m1"}
    assert main_pod["containers"][0]["imagePullPolicy"] == "Never"
    web_env = {e["name"]: e.get("value") for e in k8s["items"][3]["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert web_env["PSKIT_MAIN_URL"] == "http://pskit-shop-dev-main:8080"
    assert k8s["items"][4]["spec"]["type"] == "LoadBalancer"
    assert k8s["items"][3]["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"] == {
        "cpu": "250m", "memory": "256Mi"}

    job = scripts.nomad_job("pskit-shop-dev", "img:1", port=8080, replicas=2, master_node="m1", master_addr="10.0.0.5",
                            cpu=0.5, memory_mb=256)["Job"]
    main_g, web_g = job["TaskGroups"]
    assert main_g["Count"] == 1 and main_g["Constraints"][0]["Operand"] == "="
    assert web_g["Count"] == 2 and {c["Operand"] for c in web_g["Constraints"]} == {"!=", "distinct_hosts"}
    assert web_g["Tasks"][0]["Env"]["PSKIT_MAIN_URL"] == "http://10.0.0.5:8080"
    assert web_g["Tasks"][0]["Resources"] == {"CPU": 500, "MemoryMB": 256}
    apply = scripts.apply_script("nomad", "pskit-shop-dev", {"Job": job}, 8080)
    assert "<<'PSKIT_MANIFEST'" in apply and "--data @pskit-cluster/pskit-shop-dev.json" in apply


# ------------------------------------------------------------------------------------------- parsing
def test_parse_orchestrator_outputs():
    swarm = "\n".join([
        f"{scripts.MARK}nodes",
        json.dumps({"Hostname": "m1", "Status": "Ready", "Availability": "Active", "ManagerStatus": "Leader"}),
        json.dumps({"Hostname": "w1", "Status": "Down", "Availability": "Drain", "ManagerStatus": ""}),
        f"{scripts.MARK}services",
        json.dumps({"Name": "pskit-shop-dev_web", "Replicas": "2/3 (max 1 per node)"}),
        f"{scripts.MARK}pending",
        json.dumps({"Name": "pskit-shop-dev_web.3", "CurrentState": "Pending 2 minutes ago",
                    "Error": "no suitable node (insufficient resources on 2 nodes)"}),
    ])
    view = clu.parse_orchestrator("swarm", swarm)
    assert view["nodes"]["m1"]["ready"] and view["nodes"]["m1"]["manager"]
    assert view["nodes"]["w1"]["availability"] == "Drain" and not view["nodes"]["w1"]["ready"]
    assert view["workloads"] == [{"name": "pskit-shop-dev_web", "running": 2, "desired": 3}]
    assert view["pending"] == ["pskit-shop-dev_web.3: no suitable node (insufficient resources on 2 nodes)"]

    nodes = {"items": [{"metadata": {"name": "m1", "labels": {"node-role.kubernetes.io/control-plane": "true"}},
                        "spec": {}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}
    deps = {"items": [{"metadata": {"name": "pskit-shop-dev-web"}, "spec": {"replicas": 3},
                       "status": {"readyReplicas": 1}}]}
    pods = {"items": [{"metadata": {"name": "web-x"}, "status": {"conditions": [
        {"message": "0/1 nodes are available: 1 Insufficient cpu."}]}}]}
    k8s = clu.parse_orchestrator("kubernetes", "\n".join([f"{scripts.MARK}nodes", json.dumps(nodes),
                                                          f"{scripts.MARK}services", json.dumps(deps),
                                                          f"{scripts.MARK}pending", json.dumps(pods)]))
    assert k8s["nodes"]["m1"] == {"ready": True, "state": "Ready", "availability": "Active", "manager": True}
    assert k8s["workloads"][0]["running"] == 1 and "Insufficient cpu" in k8s["pending"][0]

    jobs = [{"ID": "pskit-shop-dev", "JobSummary": {"Summary": {"main": {"Running": 1}, "web": {"Running": 1,
                                                                                               "Queued": 2}}}}]
    nomad = clu.parse_orchestrator("nomad", "\n".join([
        f"{scripts.MARK}nodes", json.dumps([{"Name": "m1", "Status": "ready", "ID": "x", "SchedulingEligibility":
                                             "eligible"}]),
        f"{scripts.MARK}services", json.dumps(jobs)]))
    assert nomad["nodes"]["m1"]["ready"] and nomad["workloads"][1] == {"name": "pskit-shop-dev/web", "running": 1,
                                                                      "desired": 3}
    assert nomad["pending"] == ["pskit-shop-dev/web: 2 allocation(s) cannot be placed"]


def test_node_metrics():
    fake = FakeSSH({"10.0.0.5": (STAT_B, 1000000)})
    m = clu.node_metrics({"method": "ssh", "host": "10.0.0.5", "user": "root", "port": 22}, run=fake)
    assert m["reachable"] and m["cpu"] == 90.0 and m["memory"] == 75.0 and m["disk"] == 50.0 and m["cpus"] == 4
    down = clu.node_metrics({"method": "ssh", "host": "10.9.9.9", "user": "root", "port": 22}, run=fake)
    assert down == {"reachable": False, "error": "ssh: connect to host: Connection timed out"}


# ------------------------------------------------------------------------------------------- flows
def test_create_join_status_leave(servers):
    fake = FakeSSH({"10.0.0.5": (STAT_B, 1000000), "10.0.0.6": ("cpu  110 0 110 980 0 0 0 0 0 0", 3000000)})
    lines: List[str] = []
    created = clu.create_cluster(servers, "prod", "swarm", "edge1", lines.append, run=fake)
    assert created["nodes"] == [{"server": "edge1", "role": "master", "node": "node-10.0.0.5", "address": "10.0.0.5",
                                 "joined_at": created["nodes"][0]["joined_at"]}]
    assert "dk swarm init --advertise-addr '10.0.0.5'" in fake.script("swarm init")
    with pytest.raises(clu.ClusterError, match="already in cluster"):
        clu.create_cluster(servers, "other", "nomad", "edge1", lines.append, run=fake)

    joined = clu.join_server(servers, "prod", "edge2", lines.append, run=fake)
    assert [n["server"] for n in joined["nodes"]] == ["edge1", "edge2"]
    assert "--token 'SWMTKN-1-secret' '10.0.0.5:2377'" in fake.script("swarm join")
    assert not any("SWMTKN" in line for line in lines)  # the token is never printed

    fake.status_out = "\n".join([
        f"{scripts.MARK}nodes",
        json.dumps({"Hostname": "node-10.0.0.5", "Status": "Ready", "Availability": "Active", "ManagerStatus": "x"}),
        json.dumps({"Hostname": "node-10.0.0.6", "Status": "Ready", "Availability": "Active", "ManagerStatus": ""}),
        f"{scripts.MARK}services", f"{scripts.MARK}pending",
    ])
    current = clu.status(servers, "prod", run=fake)
    by = {n["server"]: n for n in current["nodes"]}
    assert by["edge1"]["full"] and by["edge1"]["over"] == ["cpu"]  # 90 % >= 85 %
    assert not by["edge2"]["full"] and by["edge2"]["cpu"] == 10.0
    assert current["full"] is False and current["reasons"] == []

    clu.leave_server(servers, "prod", "edge2", lines.append, run=fake)
    assert "dk node update --availability drain 'node-10.0.0.6'" in fake.script("availability drain")
    assert "dk swarm leave --force" in fake.script("swarm leave")
    assert [n["server"] for n in clu.public("prod", clu.get_cluster(servers, "prod"))["nodes"]] == ["edge1"]
    with pytest.raises(clu.ClusterError, match="master cannot leave"):
        clu.leave_server(servers, "prod", "edge1", lines.append, run=fake)


def test_nomad_leave_uses_the_node_id(servers):
    fake = FakeSSH()
    clu.create_cluster(servers, "lab", "nomad", "edge1", lambda _l: None, run=fake)
    clu.join_server(servers, "lab", "edge2", lambda _l: None, run=fake)
    clu.leave_server(servers, "lab", "edge2", lambda _l: None, run=fake)
    assert "/v1/node/'abc-123'/drain" in fake.script("/drain")
    assert "/v1/node/'abc-123'/purge" in fake.script("/purge")


def test_full_cluster_alerts_once_per_cooldown(servers):
    fake = FakeSSH({"10.0.0.5": (STAT_B, 100000)})  # 90 % cpu, 97.5 % memory
    clu.create_cluster(servers, "prod", "kubernetes", "edge1", lambda _l: None, run=fake)
    fake.status_out = ""
    sent: List[dict] = []

    def send(root, name, current, test=False):
        sent.append(clu.alert_event(name, current, test=test))
        return {"sent": ["function notify"], "errors": []}

    clu.save_settings(servers, "prod", {"alert": {"function": "notify", "cooldown_minutes": 30}})
    first = clu.check(servers, "prod", run=fake, send=send)
    assert first["full"] and first["alert"]["sent"] == ["function notify"]
    event = sent[0]
    assert event["type"] == "pskit.cluster.capacity" and event["cluster"] == "prod"
    assert "pskit cluster join prod SERVER" in event["message"] and event["nodes"][0]["over"] == ["cpu", "memory"]

    clu._update(servers, "prod", lambda c: c.__setitem__("last_alert_ts", __import__("time").time()))
    second = clu.check(servers, "prod", run=fake, send=send)
    assert second["alert"]["skipped"] == "cooldown" and len(sent) == 1


def test_send_alert_records_errors(servers, monkeypatch):
    clu.create_cluster(servers, "prod", "swarm", "edge1", lambda _l: None, run=FakeSSH())
    entry = clu.send_alert(servers, "prod", {"orchestrator": "swarm"}, test=True)
    assert entry["errors"] == ["no alert function or webhook is set (cluster settings)"]

    posted: List[dict] = []
    monkeypatch.setattr(clu, "_post_webhook", lambda url, event: posted.append({"url": url, **event}))
    clu.save_settings(servers, "prod", {"alert": {"webhook": "https://hooks.example.com/x", "function": "notify"}})
    entry = clu.send_alert(servers, "prod", {"orchestrator": "swarm"}, test=True)
    assert entry["sent"] == ["webhook"] and "no deployed on-prem stage" in entry["errors"][0]
    assert posted[0]["test"] is True and posted[0]["url"] == "https://hooks.example.com/x"
    assert [a["test"] for a in clu.get_cluster(servers, "prod")["alerts"]] == [True, True]
    with pytest.raises(clu.ClusterError, match="http"):
        clu.save_settings(servers, "prod", {"alert": {"webhook": "ftp://x"}})
    with pytest.raises(clu.ClusterError, match="10..100"):
        clu.save_settings(servers, "prod", {"capacity": {"cpu": 5}})


# ------------------------------------------------------------------------------------------- deploy
def test_deploy_target_is_one_server_or_a_cluster(servers):
    from py_serverless_kit.core import onprem_control as ctl

    clu.create_cluster(servers, "prod", "swarm", "edge1", lambda _l: None, run=FakeSSH())
    # --cluster on a project without options.cluster; replicas from pskit.yml are kept when another name is given
    options: dict = {"deploy": {"method": "local"}}
    assert srv.resolve_deploy(options, servers, "dev", cluster="prod") == "edge1"
    assert options["cluster"]["name"] == "prod" and options["deploy"]["method"] == "docker"
    options = {"cluster": {"name": "old", "replicas": 4}}
    srv.resolve_deploy(options, servers, "dev", cluster="prod")
    assert options["cluster"]["replicas"] == 4 and options["cluster"]["name"] == "prod"
    # --server wins over options.cluster: one machine
    options = {"cluster": "prod", "deploy": {"python": "bundle"}}
    assert srv.resolve_deploy(options, servers, "dev", "edge3") == "edge3"
    assert "cluster" not in options and options["deploy"]["host"] == "10.0.0.7"
    with pytest.raises(srv.ServerError, match="pick one"):
        srv.resolve_deploy({}, servers, "dev", "edge2", "prod")

    # the stage remembers the cluster of its last deploy
    work = servers / ".pskit" / "onprem-dev"
    (work / "onprem_app").mkdir(parents=True)
    (work / "onprem_app" / "pskit_host.json").write_text(json.dumps(
        {"port": 8080, "deploy": {"method": "cluster", "ssh": {"host": "10.0.0.5", "user": "root"}}}), "utf-8")
    data = ctl.remember_deploy(work, "edge1", "prod")
    assert srv.deployed_cluster(servers, "dev") == "prod" and srv.deployed_server(servers, "dev") == "edge1"
    assert ctl.deployed_on(data["spec"], "edge1", "prod")["label"] == "cluster prod (master edge1 (10.0.0.5))"
    options = {"deploy": {"method": "local"}}
    assert srv.resolve_deploy(options, servers, "dev") == "edge1" and options["cluster"]["name"] == "prod"
    options = {}
    assert srv.resolve_deploy(options, servers, "dev", srv.LOCAL) is None and "cluster" not in options
    clu.remove_cluster(servers, "prod", lambda _l: None)
    with pytest.raises(srv.ServerError, match="no longer exists"):
        srv.resolve_deploy({}, servers, "dev")


def test_studio_passes_the_cluster_to_deploy():
    from py_serverless_kit.studio.jobs import StudioError, build_argv

    assert build_argv("deploy", "onprem", "dev", {"cluster": "prod"}) == ["deploy", "--provider", "onprem",
                                                                          "--stage", "dev", "--cluster", "prod"]
    with pytest.raises(StudioError):
        build_argv("deploy", "onprem", "dev", {"cluster": "prod", "server": "edge1"})
    with pytest.raises(StudioError):
        build_argv("deploy", "onprem", "dev", {"cluster": "Bad Name"})


def test_resources_are_placed_on_the_freest_cluster_server(servers):
    from py_serverless_kit.core import onprem_services as svc

    # edge1 90 % cpu (over), edge2 25 % memory, edge3 50 % memory
    fake = FakeSSH({"10.0.0.5": (STAT_B, 3000000), "10.0.0.6": (STAT_A.replace("800", "900"), 3000000),
                    "10.0.0.7": (STAT_A.replace("800", "900"), 2000000)})
    clu.create_cluster(servers, "prod", "swarm", "edge1", lambda _l: None, run=fake)
    clu.join_server(servers, "prod", "edge2", lambda _l: None, run=fake, advertise="192.168.1.6")
    clu.join_server(servers, "prod", "edge3", lambda _l: None, run=fake)
    assert clu.place(servers, "prod", run=fake)["server"] == "edge2"
    assert clu.place(servers, "prod", "edge3")["server"] == "edge3"
    assert clu.place(servers, "prod", measure=False)["server"] == "edge1"  # the studio's quick check: no SSH
    with pytest.raises(clu.ClusterError, match="not in cluster"):
        clu.place(servers, "prod", "elsewhere")

    req = svc.check_request({"key": "cache", "service": "redis", "target": {"cluster": "prod"}}, servers, None,
                            run=fake)
    assert req["target"]["server"] == "edge2" and req["target"]["cluster"] == "prod"
    assert req["address"] == "192.168.1.6"  # the cluster address, reached by replicas on every server
    assert svc.describe_target(req["target"]) == "edge2 (root@10.0.0.6:22) in cluster prod"
    raw = {"provider": {"name": "onprem", "options": {"cluster": {"name": "prod", "replicas": 3}}}}
    assert svc.default_target(raw) == {"cluster": "prod"}
    assert svc.functions_target(raw, servers)["server"] == "edge1"

    state = svc.load_state(servers)
    state["services"]["cache"] = {"key": "cache", "service": "redis", "kind": "service", "target": req["target"],
                                  "address": req["address"], "port": 6379}
    svc.save_state(servers, state)
    fake.status_out = ""
    current = clu.status(servers, "prod", run=fake)
    assert current["resources"] == [{"key": "cache", "service": "redis", "kind": "service", "server": "edge2",
                                     "address": "192.168.1.6", "port": 6379}]
    assert {n["server"]: n["resources"] for n in current["nodes"]}["edge2"] == ["cache"]

    lines: List[str] = []
    clu.leave_server(servers, "prod", "edge2", lines.append, run=fake)
    assert "cluster" not in svc.load_state(servers)["services"]["cache"]["target"]  # keeps running on edge2
    assert any("cache (redis) on edge2" in line for line in lines)

    fake.nodes = {"10.0.0.5": (STAT_B, 3000000)}  # edge1 over the cpu limit, edge3 unreachable
    with pytest.raises(clu.ClusterError, match="over a capacity limit"):
        clu.place(servers, "prod", run=fake)


def test_options_cluster_targets_the_master(servers):
    clu.create_cluster(servers, "prod", "swarm", "edge1", lambda _l: None, run=FakeSSH())
    options = {"cluster": {"name": "prod", "replicas": 3}, "deploy": {"server": "edge2", "python": "bundle"}}
    assert srv.resolve_deploy(options, servers, "dev") == "edge1"
    assert options["cluster"] == {"name": "prod", "orchestrator": "swarm", "replicas": 3, "cpu": 0.25, "memory": 256}
    assert options["deploy"] == {"method": "docker", "host": "10.0.0.5", "user": "root", "port": 22, "server": "edge1",
                                 "python": "bundle", "auth": "agent"}
    for bad, message in (({"name": "nope"}, "no cluster 'nope'"), ({"name": "prod", "replicas": 0}, "replicas"),
                         ({"name": "prod", "size": 1}, "unknown options.cluster keys")):
        with pytest.raises(srv.ServerError, match=message):
            srv.resolve_deploy({"cluster": bad}, servers, "dev")


def test_render_writes_the_cluster_spec(make_project, tmp_path):
    for name, host in (("edge1", "10.0.0.5"), ("edge2", "10.0.0.6")):
        srv.save_server(tmp_path, name, {"host": host, "user": "root", "auth": "agent"})
    fake = FakeSSH()
    clu.create_cluster(tmp_path, "prod", "kubernetes", "edge1", lambda _l: None, run=fake)
    from py_serverless_kit.core.deployer import Deployer

    config = """
    service: shop
    provider:
      name: onprem
      runtime: python3.12
      options: {cluster: {name: prod, replicas: 4}, admin_token: tok}
    functions:
      hello: {handler: app.handlers.hello, events: [{http: {path: /hello, method: get}}]}
    """
    project = make_project(config)
    Deployer(project, echo=lambda _m: None).package(skip_deps=True)
    app = project.work_dir / "onprem_app"
    spec = json.loads((app / "pskit_host.json").read_text("utf-8"))
    assert spec["deploy"]["method"] == "cluster" and spec["cluster"]["replicas"] == 4
    assert spec["host"] == "0.0.0.0" and spec["data_dir"] == "/var/pskit/data"
    assert spec["deploy"]["ssh"]["host"] == "10.0.0.5"  # logs / tests reach the host through the master
    assert (app / "Dockerfile").is_file()
    info = json.loads((app / clu.CLUSTER_SPEC).read_text("utf-8"))
    assert info["master"] == "edge1" and info["nodes"]["edge1"]["target"]["host"] == "10.0.0.5"
    digest_before = (app / "pskit_host.json").read_bytes()

    clu.join_server(tmp_path, "prod", "edge2", lambda _l: None, run=fake)
    project2 = make_project(config)
    Deployer(project2, echo=lambda _m: None).package(skip_deps=True)
    assert (app / "pskit_host.json").read_bytes() == digest_before  # joining a server does not redeploy
    assert set(json.loads((app / clu.CLUSTER_SPEC).read_text("utf-8"))["nodes"]) == {"edge1", "edge2"}


def test_deploy_builds_ships_applies_and_records(servers, tmp_path):
    fake = FakeSSH()
    clu.create_cluster(servers, "prod", "swarm", "edge1", lambda _l: None, run=fake)
    clu.join_server(servers, "prod", "edge2", lambda _l: None, run=fake)
    app = servers / ".pskit" / "onprem-dev" / "onprem_app"
    app.mkdir(parents=True)
    settings = {"name": "prod", "orchestrator": "swarm", "replicas": 3, "cpu": 0.25, "memory": 256}
    (app / clu.CLUSTER_SPEC).write_text(json.dumps(clu.cluster_file(servers, settings)), "utf-8")
    spec = {"service": "shop", "stage": "dev", "port": 8080, "endpoint": "http://10.0.0.5:8080"}

    def run(argv, stdout):
        if argv[:3] == ["docker", "image", "inspect"]:
            return 0, "sha256:0123456789abcdef\n"
        return fake(argv, stdout)

    lines: List[str] = []
    record = clu.deploy(app, spec, lines.append, run=run)
    assert record["image"] == "pskit-shop-dev:0123456789ab" and record["replicas"] == 3
    assert ["docker", "save", "-o"] == next(c for c in fake.calls if c[:2] == ["docker", "save"])[:3]
    loads = [s for s in fake.scripts if "dk load -i" in s[1]]
    assert len(loads) == 2  # the master and the joined server
    applied = fake.script("dk stack deploy")
    assert "dk stack deploy --prune --resolve-image never -c pskit-cluster/pskit-shop-dev.json 'pskit-shop-dev'" in applied
    assert '"replicas": 3' in applied and "curl -fsS -m 3 http://127.0.0.1:8080/__pskit/health" in applied
    dep = clu.get_cluster(servers, "prod")["deployments"]["pskit-shop-dev"]
    assert dep["endpoint"] == "http://10.0.0.5:8080" and dep["image"] == record["image"]

    clu.scale(servers, "prod", "pskit-shop-dev", 6, lines.append, run=fake)
    assert "dk service scale --detach 'pskit-shop-dev_web'=6" in fake.script("service scale")
    clu.destroy(app, spec, lines.append, run=fake)
    assert "dk stack rm 'pskit-shop-dev'" in fake.script("stack rm")
    assert clu.get_cluster(servers, "prod").get("deployments") == {}


def test_nomad_scale_re_registers_the_job(servers):
    fake = FakeSSH()
    clu.create_cluster(servers, "lab", "nomad", "edge1", lambda _l: None, run=fake)
    job = scripts.nomad_job("pskit-shop-dev", "img:1", port=8080, replicas=2, master_node="m", master_addr="10.0.0.5",
                            cpu=0.25, memory_mb=256)["Job"]
    clu._update(servers, "lab", lambda c: c.__setitem__("deployments", {"pskit-shop-dev": {"replicas": 2}}))

    def run(argv, stdout):
        if argv[-1] == "curl -fsS http://127.0.0.1:4646/v1/job/pskit-shop-dev":
            return 0, json.dumps(job)
        return fake(argv, stdout)

    clu.scale(servers, "lab", "pskit-shop-dev", 6, lambda _l: None, run=run)
    text = fake.script("-scale.json")
    sent = json.loads(text.split("<<'PSKIT_MANIFEST'\n", 1)[1].split("\nPSKIT_MANIFEST", 1)[0])
    assert [(g["Name"], g["Count"]) for g in sent["Job"]["TaskGroups"]] == [("main", 1), ("web", 6)]
    assert "--data @pskit-cluster/pskit-shop-dev-scale.json http://127.0.0.1:4646/v1/jobs" in text
    _sh_ok(text)
    assert clu.get_cluster(servers, "lab")["deployments"]["pskit-shop-dev"]["replicas"] == 6
