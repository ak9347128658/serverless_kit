"""Handlers for the lambda-starter service.

    hello      GET  /hello              HTTP
    processor  (no trigger)             invoke-only, called by its ARN
    items      GET  /items              HTTP, one Lambda for every method
               GET  /items/{id}
               POST /items
               PUT  /items/{id}
"""

from typing import Any, Dict

from py_serverless_kit import BadRequest, Context, Event, MethodNotAllowed, NotFound, Response

# In-memory store: fine for a demo, but it resets on every cold start and is
# not shared between Lambda instances. Use DynamoDB or similar for real data.
_ITEMS: Dict[str, Dict[str, Any]] = {"1": {"id": "1", "name": "first item", "price": 10.0}}


def hello(event: Event, ctx: Context):
    name = event.http.query.get("name", "world")
    ctx.logger.info("saying hello", extra={"who": name})
    return {"message": f"Hello, {name}!", "provider": event.provider, "stage": ctx.stage}


def processor(event: Event, ctx: Context):
    """Invoke-only: `event.data` is the JSON payload passed to `aws lambda invoke`."""
    data = event.data or {}
    numbers = data.get("numbers", []) if isinstance(data, dict) else []
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers):
        raise ValueError("`numbers` must be a list of numbers")
    ctx.logger.info("processing", extra={"count": len(numbers)})
    return {"count": len(numbers), "sum": sum(numbers), "request_id": ctx.request_id}


def _parse_item(event: Event) -> Dict[str, Any]:
    body = event.http.json()  # malformed JSON -> automatic 400
    if not isinstance(body, dict) or not isinstance(body.get("name"), str):
        raise BadRequest("body must be a JSON object with a string `name`")
    price = body.get("price", 0)
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        raise BadRequest("`price` must be a number")
    return {"name": body["name"], "price": price}


def items(event: Event, ctx: Context):
    method = event.http.method
    item_id = event.http.path_params.get("id")

    if method == "GET" and item_id is None:
        return {"items": list(_ITEMS.values()), "count": len(_ITEMS)}

    if method == "GET":
        if item_id not in _ITEMS:
            raise NotFound(f"item {item_id} not found")
        return _ITEMS[item_id]

    if method == "POST":
        new_id = str(max((int(k) for k in _ITEMS), default=0) + 1)
        item = {"id": new_id, **_parse_item(event)}
        _ITEMS[new_id] = item
        return Response.json(item, status=201, headers={"location": f"/items/{new_id}"})

    if method == "PUT":
        if item_id not in _ITEMS:
            raise NotFound(f"item {item_id} not found")
        _ITEMS[item_id] = {"id": item_id, **_parse_item(event)}
        return _ITEMS[item_id]

    raise MethodNotAllowed(f"{method} is not supported")
