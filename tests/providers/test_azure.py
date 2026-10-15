from __future__ import annotations

import json

from py_serverless_kit.core.deployer import Deployer
from tests.conftest import full_config


def _errors(project):
    return [i for i in project.validate() if i.level == "error"]


def test_render_full_config(make_project, render):
    project = make_project(full_config("azure", options={"subscription_id": "sub"}))
    assert _errors(project) == []
    tf = render(project)
    res = tf["resource"]
    for rtype in (
        "azurerm_resource_group",
        "azurerm_storage_account",
        "azurerm_service_plan",
        "azurerm_linux_function_app",
        "azurerm_storage_queue",
        "azurerm_storage_container",
        "azurerm_servicebus_namespace",
        "azurerm_servicebus_topic",
        "azurerm_servicebus_subscription",
        "azurerm_application_insights",
    ):
        assert rtype in res, rtype
    app = res["azurerm_linux_function_app"]["app"]
    assert app["zip_deploy_file"] == "${path.module}/artifact.zip"
    assert app["site_config"]["application_stack"]["python_version"] == "3.12"
    settings = app["app_settings"]
    assert settings["SCM_DO_BUILD_DURING_DEPLOYMENT"] == "true"
    assert settings["PSKIT_SERVICE"] == "demo"
    assert "PSKIT_FUNCTION" not in settings and "PSKIT_HANDLER" not in settings
    assert "PSKIT_SERVICEBUS" in settings
    assert len(res["azurerm_storage_account"]["app"]["name"]) <= 24
    assert tf["provider"]["azurerm"][0] == {"features": {}, "subscription_id": "sub"}

    out = tf["output"]["pskit"]["value"]
    assert out["endpoint"].startswith("https://${azurerm_linux_function_app.app.default_hostname}")
    assert out["functions"]["hello"]["url"].endswith("/hello/{name}")
    assert "pipeline" in out["workflows"]  # embedded engine


def test_function_app_shim(make_project):
    project = make_project(full_config("azure"))
    Deployer(project, echo=lambda _m: None).package(skip_deps=True)
    build = project.work_dir / "build"
    src = (build / "function_app.py").read_text()
    compile(src, "function_app.py", "exec")
    assert "@app.route(route='hello/{name}', methods=['GET']" in src
    assert "@app.timer_trigger(schedule='0 */5 * * * *'" in src
    assert "_pskit.timer(timer, 'tick', {'report': 'weekly'})" in src
    assert "queue_name='jobs'" in src
    assert "path='demo-uploads-1234/{name}.csv'" in src
    assert "topic_name='events', subscription_name='worker'" in src
    assert "route='workflows/pipeline'" in src  # orchestrator of the embedded workflow
    host = json.loads((build / "host.json").read_text())
    assert host["extensions"]["http"]["routePrefix"] == ""
    assert (build / "requirements.txt").read_text().strip() == "azure-functions"


def test_greedy_route_and_key_auth(make_project):
    project = make_project("""
    service: demo
    provider: {name: azure}
    functions:
      files:
        handler: app.handlers.hello
        events: [{http: {path: "/files/{path+}", method: ANY, auth: key}}]
    """)
    src = project.provider.shim_files(project.config)["function_app.py"]
    assert "@app.route(route='files/{*path}', auth_level=func.AuthLevel.FUNCTION)" in src


def test_unsupported_combinations(make_project):
    project = make_project("""
    service: demo
    provider: {name: azure}
    functions:
      a:
        handler: app.handlers.hello
        events:
          - http: {path: /a, auth: jwt}
          - storage: {bucket: up, events: [deleted]}
          - topic: {name: t, existing: true}
    """)
    msgs = " ".join(i.message for i in _errors(project))
    assert "auth 'jwt'" in msgs
    assert "created/updated" in msgs
    assert "servicebus_namespace_id" in msgs


def test_unsupported_runtime(make_project):
    project = make_project("""
    service: demo
    provider: {name: azure, runtime: python3.13}
    functions:
      a: {handler: app.handlers.hello}
    """)
    assert any("does not support python3.13" in i.message for i in _errors(project))
