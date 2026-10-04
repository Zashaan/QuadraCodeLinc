"""Small read-only DynamoDB helpers; bounded reads never return partial histories."""

from decimal import Decimal
from typing import Any

MAX_PAGES = 20
MAX_ITEMS = 2000


def normalize_numbers(value: Any) -> Any:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, dict):
        return {key: normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_numbers(item) for item in value]
    return value


def read_all(table: Any, *, operation: str = "scan", **kwargs: Any) -> list[dict[str, Any]]:
    """Read every page or raise; filtering cannot hide the evaluated-item budget."""
    items: list[dict[str, Any]] = []
    evaluated = 0
    last_key: dict[str, Any] | None = None
    for _ in range(MAX_PAGES):
        request = {**kwargs, "Limit": min(100, MAX_ITEMS - evaluated)}
        if last_key:
            request["ExclusiveStartKey"] = last_key
        response = getattr(table, operation)(**request)
        page = response.get("Items", [])
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise ValueError("Malformed DynamoDB page")
        evaluated += max(response.get("ScannedCount", len(page)), len(page))
        items.extend(normalize_numbers(page))
        next_key = response.get("LastEvaluatedKey")
        if len(items) > MAX_ITEMS or evaluated > MAX_ITEMS:
            raise ValueError("DynamoDB read budget exceeded; data is incomplete")
        if not next_key:
            return items
        if next_key == last_key or evaluated >= MAX_ITEMS:
            raise ValueError("DynamoDB read did not complete")
        last_key = next_key
    raise ValueError("DynamoDB page budget exceeded; data is incomplete")


def dynamodb_table(name: str, region: str, table: Any = None) -> Any:
    if not name:
        raise ValueError("DynamoDB table name is required")
    if table is not None:
        return table
    from app.aws import aws_client

    return aws_client("dynamodb", region, resource=True).Table(name)
