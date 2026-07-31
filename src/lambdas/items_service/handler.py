"""Toy items service.

A deliberately trivial CRUD pair over a placeholder table. It exists to prove the
path — API Gateway event → shared layer → boto3 → DynamoDB → response — before
any ledger semantics are built on top of it. Nothing here is the real data model.
"""

from collections.abc import Callable
from typing import Any

from shared import utils
from shared.dynamo import get_table, required_env
from shared.errors import ApiError, NotFound

TABLE_NAME_ENV = "TOY_TABLE_NAME"

logger = utils.get_logger(__name__)


def _table():
    # Resolved per invocation, not at import: reading it at import time would bake
    # the value into the cold-start snapshot and make the module unimportable when
    # the variable is missing, which is a much worse failure than a 500.
    return get_table(required_env(TABLE_NAME_ENV))


def put_item(event: dict[str, Any]) -> dict[str, Any]:
    payload = utils.parse_json_body(event)
    item_id = utils.required_str(payload, "item_id")

    # A full overwrite, no condition — "toy" means last write wins. The real
    # authorization path uses attribute_not_exists guards inside a transaction.
    _table().put_item(Item=payload)

    logger.info("stored item", extra={"item_id": item_id})
    return utils.json_response(201, payload)


def get_item(event: dict[str, Any]) -> dict[str, Any]:
    item_id = utils.path_parameter(event, "item_id")

    # Strongly consistent, matching the design doc's rule that reads on the
    # DynamoDB write path never serve a stale value.
    result = _table().get_item(Key={"item_id": item_id}, ConsistentRead=True)

    item = result.get("Item")
    if item is None:
        raise NotFound(f"no item with id '{item_id}'")
    return utils.json_response(200, item)


ROUTES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "POST /items": put_item,
    "GET /items/{item_id}": get_item,
}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    route = utils.route_key(event)
    try:
        route_handler = ROUTES.get(route)
        if route_handler is None:
            raise NotFound(f"no route for '{route}'")
        return route_handler(event)
    except ApiError as exc:
        logger.warning("request rejected: %s %s", route, exc.message)
        return utils.error_response(exc)
    except Exception:
        # Never leak an exception message to the caller; the request id in the
        # log line is what correlates this back to the CloudWatch entry.
        logger.exception("unhandled error handling %s", route)
        return utils.json_response(
            500, {"error": "InternalServerError", "message": "internal server error"}
        )
