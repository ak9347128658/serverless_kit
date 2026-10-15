import pytest
from pydantic import ValidationError

from py_serverless_kit.config.schema import EventSpec, FunctionConfig, ScheduleEvent, ServiceConfig, WorkflowConfig


@pytest.mark.parametrize(
    "raw,kind,check",
    [
        ({"http": "POST /a/{id}"}, "http", lambda c: (c.method, c.path) == ("POST", "/a/{id}")),
        ({"http": "/plain"}, "http", lambda c: (c.method, c.path) == ("GET", "/plain")),
        ({"http": {"path": "x", "method": "*"}}, "http", lambda c: (c.method, c.path) == ("ANY", "/x")),
        ({"schedule": "10 minutes"}, "schedule", lambda c: c.rate == "10 minutes"),
        ({"schedule": "0 1 * * *"}, "schedule", lambda c: c.cron == "0 1 * * *"),
        ({"queue": "jobs"}, "queue", lambda c: c.name == "jobs" and c.batch_size == 10),
        ({"storage": "bkt"}, "storage", lambda c: c.bucket == "bkt" and c.events == ["created"]),
        ({"topic": "t"}, "topic", lambda c: c.name == "t"),
    ],
)
def test_event_shorthands(raw, kind, check):
    spec = EventSpec.model_validate(raw)
    assert spec.type == kind and check(spec.config)


@pytest.mark.parametrize("path", ["/a/{bad-name}", "/a/{x}{y}", "/a/{x+}/b"])
def test_invalid_http_paths(path):
    with pytest.raises(ValidationError):
        EventSpec.model_validate({"http": {"path": path}})


def test_schedule_requires_one():
    with pytest.raises(ValidationError):
        ScheduleEvent(rate="5 minutes", cron="* * * * *")
    with pytest.raises(ValidationError):
        ScheduleEvent()
    with pytest.raises(ValidationError):
        ScheduleEvent(cron="* * * *")


def test_function_kind():
    assert FunctionConfig(handler="a.b").kind == "invoke-only"
    assert FunctionConfig(handler="a.b", events=[{"http": "/x"}]).kind == "http"
    assert FunctionConfig(handler="a.b", events=[{"schedule": "1 day"}]).kind == "scheduled"
    assert FunctionConfig(handler="a.b", events=[{"http": "/x"}, {"queue": "q"}]).kind == "http+event"
    assert FunctionConfig(handler="a.b", events=[{"queue": "q"}]).kind == "event-driven"


def test_environment_coerced_to_strings():
    fn = FunctionConfig(handler="a.b", environment={"N": 1, "B": True, "E": None})
    assert fn.environment == {"N": "1", "B": "True", "E": ""}


def test_workflow_graph_validation():
    ok = WorkflowConfig.model_validate(
        {"start_at": "A", "states": {"A": {"type": "task", "function": "f", "next": "B"}, "B": {"type": "succeed"}}}
    )
    assert ok.states["A"].end is False and ok.task_functions() == ["f"]
    with pytest.raises(ValidationError, match="unknown state"):
        WorkflowConfig.model_validate(
            {"start_at": "A", "states": {"A": {"type": "task", "function": "f", "next": "Z"}}}
        )
    with pytest.raises(ValidationError, match="start_at"):
        WorkflowConfig.model_validate({"start_at": "Q", "states": {"A": {"type": "succeed"}}})
    with pytest.raises(ValidationError, match="needs `function`"):
        WorkflowConfig.model_validate({"start_at": "A", "states": {"A": {"type": "task"}}})
    implicit_end = WorkflowConfig.model_validate({"start_at": "A", "states": {"A": {"type": "pass"}}})
    assert implicit_end.states["A"].end is True


def test_workflow_references_unknown_function():
    with pytest.raises(ValidationError, match="unknown function"):
        ServiceConfig.model_validate(
            {
                "service": "svc",
                "provider": {"name": "aws"},
                "functions": {"a": {"handler": "m.a"}},
                "workflows": {"w": {"start_at": "A", "states": {"A": {"type": "task", "function": "zzz"}}}},
            }
        )
