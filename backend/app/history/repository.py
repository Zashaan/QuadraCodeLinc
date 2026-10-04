"""Member-scoped claims and authorizations with explicit completeness."""

from datetime import date
from typing import Any, Literal, Protocol

from pydantic import Field

from app.aws_data import dynamodb_table, read_all
from app.benefits.models import Money, ShortText
from app.models import StrictModel


class Claim(StrictModel):
    claim_id: ShortText
    member_id: ShortText
    procedure_code: ShortText
    service_date: date
    claim_status: Literal["paid", "denied", "pending", "approved", "reversed"]
    provider_id: ShortText | None = None
    tooth_id: ShortText | None = None
    insurance_paid: Money | None = None
    allowed_amount: Money | None = None
    denial_reason: ShortText | None = None


class Authorization(StrictModel):
    authorization_id: ShortText
    member_id: ShortText
    procedure_code: ShortText
    status: Literal["approved", "denied", "pending", "expired", "cancelled"]
    provider_id: ShortText | None = None
    requested_date: date | None = None
    decision_date: date | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    reason: ShortText | None = None


class MemberHistory(StrictModel):
    claims: list[Claim] = Field(default_factory=list)
    authorizations: list[Authorization] = Field(default_factory=list)
    claims_complete: bool = False
    authorizations_complete: bool = False


class HistoryRepository(Protocol):
    def get_history(self, member_id: str) -> MemberHistory: ...


class LocalHistoryRepository:
    """Only caller-supplied synthetic histories can be considered complete."""

    def __init__(self, histories: dict[str, MemberHistory] | None = None) -> None:
        self._histories = histories or {}

    def get_history(self, member_id: str) -> MemberHistory:
        return self._histories.get(member_id, MemberHistory())


def _parse(item: dict[str, Any], model: type[Claim] | type[Authorization]) -> Any:
    if item.get("is_synthetic") is not True:
        raise ValueError("Only synthetic history records are supported")
    fields = {key: value for key, value in item.items() if key in model.model_fields}
    for key in ("service_date", "requested_date", "decision_date", "valid_from", "valid_until"):
        if isinstance(fields.get(key), str):
            fields[key] = date.fromisoformat(fields[key])
    return model.model_validate(fields)


class DynamoDBHistoryRepository:
    def __init__(
        self,
        claim_table_name: str,
        authorization_table_name: str,
        region: str = "us-east-1",
        *,
        claim_table: Any = None,
        authorization_table: Any = None,
    ) -> None:
        self._claims = dynamodb_table(claim_table_name, region, claim_table)
        self._authorizations = dynamodb_table(authorization_table_name, region, authorization_table)

    @staticmethod
    def _items(table: Any, member_id: str) -> list[dict[str, Any]]:
        # Existing tables have only claim/auth IDs as keys, and no member index.
        from boto3.dynamodb.conditions import Attr  # type: ignore[import-untyped]

        items = read_all(
            table, FilterExpression=Attr("member_id").eq(member_id), ConsistentRead=True
        )
        if any(item.get("member_id") != member_id for item in items):
            raise ValueError("Mismatched member history")
        return items

    def get_claims(self, member_id: str) -> list[Claim]:
        items = self._items(self._claims, member_id)
        claims = [_parse(item, Claim) for item in items]
        if len({claim.claim_id for claim in claims}) != len(claims):
            raise ValueError("Duplicate claim IDs")
        return sorted(claims, key=lambda claim: (claim.service_date, claim.claim_id))

    def get_authorizations(self, member_id: str) -> list[Authorization]:
        items = self._items(self._authorizations, member_id)
        auth = [_parse(item, Authorization) for item in items]
        if len({item.authorization_id for item in auth}) != len(auth):
            raise ValueError("Duplicate authorization IDs")
        return sorted(auth, key=lambda item: item.authorization_id)

    def get_history(self, member_id: str) -> MemberHistory:
        return MemberHistory(
            claims=self.get_claims(member_id),
            authorizations=self.get_authorizations(member_id),
            claims_complete=True,
            authorizations_complete=True,
        )
