"""Embedded workflow (state machine) engine.

The same definition the CLI compiles to AWS Step Functions (ASL) or Google
Cloud Workflows is interpreted here for:

* ``pskit workflow run --local`` - test workflows on your laptop;
* providers without a native orchestrator - the CLI deploys an extra
  *orchestrator* function whose handler is :func:`orchestrate`, and which calls
  task handlers in-process (they live in the same bundle).

Supported state types: task, choice, wait, pass, parallel, map, succeed, fail.
"""

from __future__ import annotations

import copy
import fnmatch
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .context import Context
from .event import Event, EventType
from .response import Response

TaskRunner = Callable[[str, Any], Any]
ALL_ERRORS = ("*", "States.ALL")


class WorkflowFailed(Exception):
    def __init__(self, error: str, cause: str = "", history: Optional[List[Dict[str, Any]]] = None) -> None:
        self.error = error
        self.cause = cause
        self.history = history or []
        super().__init__(f"{error}: {cause}" if cause else error)


# --------------------------------------------------------------------- JSON path


def _tokens(path: str) -> List[Any]:
    if path in ("$", "", None):
        return []
    if not path.startswith("$"):
        raise ValueError(f"JSON path must start with '$': {path!r}")
    tokens: List[Any] = []
    for part in path[1:].replace("[", ".[").split("."):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            inner = part[1:-1].strip("'\"")
            tokens.append(int(inner) if inner.lstrip("-").isdigit() else inner)
        else:
            tokens.append(part)
    return tokens


_MISSING = object()  # "no default given" marker for get_path
_ABSENT = object()  # "path not present" value used by choice rules


def get_path(data: Any, path: str, default: Any = _MISSING) -> Any:
    current = data
    for tok in _tokens(path):
        try:
            current = current[tok]
        except (KeyError, IndexError, TypeError):
            if default is _MISSING:
                raise KeyError(path) from None
            return default
    return current


def set_path(data: Any, path: Optional[str], value: Any) -> Any:
    """Return ``data`` with ``value`` written at ``path`` (``$`` replaces; ``None`` discards)."""
    if path is None:
        return data
    tokens = _tokens(path)
    if not tokens:
        return value
    root = copy.deepcopy(data) if isinstance(data, (dict, list)) else {}
    current = root
    for tok in tokens[:-1]:
        if isinstance(current, dict):
            current = current.setdefault(tok, {})
        else:
            current = current[tok]
    current[tokens[-1]] = value
    return root


# --------------------------------------------------------------------- choice


_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    "equals": lambda a, b: a == b,
    "not_equals": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "in": lambda a, b: a in b,
    "contains": lambda a, b: a is not None and b in a,
    "matches": lambda a, b: isinstance(a, str) and fnmatch.fnmatchcase(a, b),
    "is_null": lambda a, b: (a is None) == bool(b),
    "is_present": lambda a, b: (a is not _ABSENT) == bool(b),
}
CHOICE_OPERATORS = tuple(_OPS)


def evaluate_rule(rule: Dict[str, Any], data: Any) -> bool:
    if "and" in rule:
        return all(evaluate_rule(r, data) for r in rule["and"])
    if "or" in rule:
        return any(evaluate_rule(r, data) for r in rule["or"])
    if "not" in rule:
        return not evaluate_rule(rule["not"], data)
    variable = rule.get("variable", "$")
    for op, fn in _OPS.items():
        if op in rule:
            value = get_path(data, variable, _ABSENT)
            if op == "is_present":
                return fn(value, rule[op])
            if value is _ABSENT:
                return False
            try:
                return bool(fn(value, rule[op]))
            except TypeError:
                return False
    raise ValueError(f"Choice rule has no operator (one of {', '.join(_OPS)}): {rule}")


# --------------------------------------------------------------------- engine


def _error_name(exc: BaseException) -> str:
    return getattr(exc, "error", None) or type(exc).__name__


def _matches(errors: List[str], name: str) -> bool:
    return any(e in ALL_ERRORS or e == name for e in (errors or ["*"]))


@dataclass
class WorkflowEngine:
    definition: Dict[str, Any]
    task_runner: TaskRunner
    sleep: Callable[[float], None] = time.sleep
    max_steps: int = 10_000
    history: List[Dict[str, Any]] = field(default_factory=list)

    def run(self, data: Any = None) -> Any:
        return self._run_machine(self.definition, data)

    # ------------------------------------------------------------------
    def _record(self, state: str, kind: str, **extra: Any) -> None:
        self.history.append({"ts": datetime.now(timezone.utc).isoformat(), "state": state, "type": kind, **extra})

    def _run_machine(self, machine: Dict[str, Any], data: Any) -> Any:
        states: Dict[str, Dict[str, Any]] = machine["states"]
        name: Optional[str] = machine["start_at"]
        steps = 0
        while name is not None:
            steps += 1
            if steps > self.max_steps:
                raise WorkflowFailed("States.Runtime", f"exceeded {self.max_steps} steps", self.history)
            state = states[name]
            kind = state["type"]
            self._record(name, kind, event="enter")
            if kind == "succeed":
                return data
            if kind == "fail":
                raise WorkflowFailed(state.get("error", "States.Fail"), state.get("cause", ""), self.history)
            if kind == "choice":
                name = self._choose(state, data)
                continue
            try:
                data = self._execute(state, data)
            except WorkflowFailed:
                raise
            except Exception as exc:  # noqa: BLE001 - mapped onto catch rules
                handler = next(
                    (c for c in state.get("catch") or [] if _matches(c.get("errors", ["*"]), _error_name(exc))),
                    None,
                )
                if handler is None:
                    raise WorkflowFailed(_error_name(exc), str(exc), self.history) from exc
                self._record(name, kind, event="caught", error=_error_name(exc))
                info = {"error": _error_name(exc), "cause": str(exc)}
                data = set_path(data, handler.get("result_path", "$.error"), info)
                name = handler["next"]
                continue
            if state.get("end") or not state.get("next"):
                return data
            name = state["next"]
        return data

    def _choose(self, state: Dict[str, Any], data: Any) -> str:
        for rule in state.get("choices") or []:
            if evaluate_rule(rule, data):
                return rule["next"]
        if state.get("default"):
            return state["default"]
        raise WorkflowFailed("States.NoChoiceMatched", "no choice rule matched and no default", self.history)

    def _execute(self, state: Dict[str, Any], data: Any) -> Any:
        kind = state["type"]
        input_data = get_path(data, state["input_path"]) if state.get("input_path") else data
        if kind == "task":
            result = self._with_retry(state, lambda: self.task_runner(state["function"], input_data))
        elif kind == "pass":
            result = state.get("result", input_data)
        elif kind == "wait":
            if state.get("seconds_path"):
                seconds = float(get_path(data, state["seconds_path"]))
            else:
                seconds = float(state.get("seconds", 0))
            self.sleep(seconds)
            result = input_data
        elif kind == "parallel":
            branches = state.get("branches") or []

            def run_branch(branch: Dict[str, Any]) -> Any:
                return self._run_machine(branch, copy.deepcopy(input_data))

            with ThreadPoolExecutor(max_workers=max(1, len(branches))) as pool:
                result = list(pool.map(run_branch, branches))
        elif kind == "map":
            items = get_path(input_data, state.get("items_path", "$"))
            if not isinstance(items, list):
                raise TypeError(f"map items_path did not resolve to a list: {type(items).__name__}")
            iterator = state["iterator"]
            workers = int(state.get("max_concurrency") or 0) or min(32, max(1, len(items)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                result = list(pool.map(lambda item: self._run_machine(iterator, item), items))
        else:
            raise WorkflowFailed("States.Runtime", f"unknown state type {kind!r}", self.history)
        return set_path(data, state.get("result_path", "$"), result)

    def _with_retry(self, state: Dict[str, Any], call: Callable[[], Any]) -> Any:
        attempts: Dict[int, int] = {}
        while True:
            try:
                return call()
            except Exception as exc:  # noqa: BLE001
                name = _error_name(exc)
                rules = state.get("retry") or []
                # Like ASL, the first retrier whose errors match is the one used.
                idx = next((i for i, r in enumerate(rules) if _matches(r.get("errors", ["*"]), name)), None)
                if idx is None:
                    raise
                rule = rules[idx]
                count = attempts.get(idx, 0)
                if count >= int(rule.get("max_attempts", 3)):
                    raise
                attempts[idx] = count + 1
                delay = float(rule.get("interval_seconds", 1)) * float(rule.get("backoff_rate", 2.0)) ** count
                self._record(state.get("function", "?"), "task", event="retry", attempt=count + 1, error=name)
                self.sleep(min(delay, float(rule.get("max_delay_seconds", 300))))


def in_process_runner(provider: str = "local") -> TaskRunner:
    """Task runner that invokes handlers from the current bundle via the dispatcher."""
    from .dispatch import execute

    def run(function: str, data: Any) -> Any:
        event = Event(type=EventType.WORKFLOW, provider=provider, function=function, data=data)
        result = execute(event, Context(function=function, provider=provider), function)
        return result

    return run


def orchestrate(event: Event, context: Context) -> Any:
    """Handler of the auto-generated ``<workflow>-orchestrator`` function."""
    from .manifest import load_manifest

    manifest = load_manifest()
    target = next(
        (
            (name, wf)
            for name, wf in manifest.workflows.items()
            if wf.get("orchestrator") == (event.function or context.function)
        ),
        None,
    )
    if target is None:
        raise LookupError(f"No workflow is orchestrated by function {event.function!r}")
    name, wf = target
    if event.http is not None:
        data = event.http.json(default=None) if event.http.body else dict(event.http.query)
    else:
        data = event.data
    engine = WorkflowEngine(wf["definition"], in_process_runner(event.provider))
    started = time.time()
    try:
        output = engine.run(data)
    except WorkflowFailed as exc:
        failure = {"workflow": name, "status": "FAILED", "error": exc.error, "cause": exc.cause}
        context.logger.error("workflow failed", extra=failure)
        if event.http is not None:
            return Response.json(failure, 500)
        raise
    body: Dict[str, Any] = {
        "workflow": name,
        "status": "SUCCEEDED",
        "output": output,
        "duration_ms": round((time.time() - started) * 1000, 2),
    }
    return body
