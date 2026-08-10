"""Reusable machinery for the `Idempotency-Key` contract (design doc: `04-api.md`
"Idempotency outcomes", `03-data-model.md` `IDEM#<key>` item shape).

Every `POST` route that moves money embeds an `IDEM#` record in the *same*
DynamoDB transaction as its domain writes — that's what makes "the record
exists" mean "the operation completed", with no `IN_PROGRESS` state to worry
about. This module supplies the pieces that are identical across every such
route: the header check, the replay/reuse decision, the `TransactItems` entry,
and what to do when a concurrent request wins the race for the same key.

What it deliberately does *not* do: decide what a route's own business
writes look like, or how a `TransactionCanceledException` maps to a
business-specific error (e.g. `InsufficientFunds`) — that stays in each
route's own repository method, which calls `resolve_conflict` only after
ruling out its own failure reasons.
"""

import dataclasses
import json
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from aws_lambda_powertools.event_handler import Response
from aws_lambda_powertools.event_handler.content_types import APPLICATION_JSON
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
from pydantic import BaseModel

from shared import dynamo
from shared.errors import IdempotencyKeyReuse, MissingIdempotencyKey
from shared.table import LEDGER_PK_NAME, LEDGER_SORT_KEY_NAME, LEDGER_TABLE_NAME
from shared.utils import _json_default, get_model_hash

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef

# design doc: 03-data-model.md, "ttl (int) — epoch seconds, 24-48h"
DEFAULT_TTL_SECONDS = 24 * 60 * 60


@dataclasses.dataclass
class IdempotencyRecord:
    idempotency_key: str
    request_hash: str
    status_code: int
    response_snapshot: str  # the original response body, returned verbatim on replay


class IdempotencyRepository:
    """Read access to `IDEM#<key>` records.

    There is no standalone `put`: a record is only ever written as a
    `TransactItem` inside the domain transaction it belongs to (`transact_item`
    below), so a record with no matching domain write would be a bug rather
    than a reachable state.
    """

    def __init__(self, table_name: str = LEDGER_TABLE_NAME) -> None:
        self._table = dynamo.get_table(table_name)

    def get(self, key: str) -> IdempotencyRecord | None:
        response = self._table.get_item(
            Key={LEDGER_PK_NAME: f"IDEM#{key}", LEDGER_SORT_KEY_NAME: "META"}
        )
        item = response.get("Item")
        if not item:
            return None

        return IdempotencyRecord(
            idempotency_key=key,
            request_hash=cast(str, item["request_hash"]),
            status_code=cast(int, item["status_code"]),
            response_snapshot=cast(str, item["response_snapshot"]),
        )


def require_key(event: APIGatewayProxyEventV2) -> str:
    """The `Idempotency-Key` header, or `MissingIdempotencyKey` (400)."""
    key = event.headers.get("idempotency-key")
    if not key:
        raise MissingIdempotencyKey("Missing idempotency key")
    return key


def check_replay(
    repository: IdempotencyRepository, key: str, request: BaseModel
) -> Response[dict[str, Any]] | None:
    """`None` if the caller should proceed with a fresh request.

    Otherwise the stored response for `key`, ready to return verbatim — status
    code included, per the design doc's "replay the snapshot verbatim" rule.
    Raises `IdempotencyKeyReuse` (422) if `key` is already bound to a request
    with a different body.
    """
    record = repository.get(key)
    if record is None:
        return None
    if record.request_hash != get_model_hash(request):
        raise IdempotencyKeyReuse("Idempotency key reuse")

    return Response(
        status_code=record.status_code,
        content_type=APPLICATION_JSON,
        body=json.loads(record.response_snapshot),
    )


def transact_item(
    table_name: str,
    key: str,
    request: BaseModel,
    response: Response[dict[str, Any]],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> "TransactWriteItemTypeDef":
    """The `Put` for the `IDEM#` record.

    Embed this as an entry in the same `transact_write_items` call as the
    domain writes `response` describes — its position in that list is whatever
    the caller passes to `resolve_conflict` as `item_index`.
    """
    return {
        "Put": {
            "TableName": table_name,
            "Item": {
                LEDGER_PK_NAME: f"IDEM#{key}",
                LEDGER_SORT_KEY_NAME: "META",
                "idempotency_key": key,
                "request_hash": get_model_hash(request),
                "status_code": response.status_code,
                "response_snapshot": json.dumps(response.body, default=_json_default),
                "ttl": int(time.time()) + ttl_seconds,
            },
            "ConditionExpression": f"attribute_not_exists({LEDGER_PK_NAME})",
        }
    }


def resolve_conflict(
    repository: IdempotencyRepository,
    key: str,
    reasons: Sequence[Mapping[str, Any]],
    item_index: int,
) -> Response[dict[str, Any]] | None:
    """Call from a domain transaction's `except TransactionCanceledException`
    block, after checking the transaction's own business-specific reasons.

    `reasons` is `TransactionCanceledException.response["CancellationReasons"]`;
    `item_index` is this key's `transact_item`'s position in `TransactItems`.
    Returns `None` if that position isn't what failed — the caller's own
    reasons still apply. Otherwise a concurrent request already committed
    under `key`; re-read and replay its response rather than failing the loser.
    """
    idempotency_item_failed = (
        len(reasons) > item_index and reasons[item_index].get("Code") == "ConditionalCheckFailed"
    )
    if not idempotency_item_failed:
        return None

    record = repository.get(key)
    # The record is written atomically with the transaction it belongs to
    # (design doc: 03-data-model.md) - if the conditional put lost the race,
    # the winner's record must already be readable.
    assert record is not None, f"idempotency Put for {key!r} lost a race with no winning record"

    return Response(
        status_code=record.status_code,
        content_type=APPLICATION_JSON,
        body=json.loads(record.response_snapshot),
    )
