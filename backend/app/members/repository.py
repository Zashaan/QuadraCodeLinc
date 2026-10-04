from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, TypeAdapter

from app.models import StrictModel

MemberId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9]+$")]
Money = Annotated[int, Field(ge=0)]


class Member(StrictModel):
    member_id: MemberId
    name: Annotated[str, Field(min_length=1, max_length=100)]
    plan_id: Annotated[str, Field(min_length=1, max_length=100)]
    employer_id: str
    plan_year: Annotated[int, Field(ge=2000, le=2100)]
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    zip_code: Annotated[str, Field(pattern=r"^[0-9]{5}$")]
    balances_as_of: date
    synthetic: Literal[True]
    currency: Literal["USD"]
    annual_maximum: Money
    annual_maximum_used: Money
    annual_maximum_remaining: Money
    deductible_total: Money
    deductible_used: Money
    deductible_remaining: Money
    fsa_balance: Money


class MemberRepository(Protocol):
    def get_member(self, member_id: str) -> Member | None: ...


class SyntheticMemberRepository:
    """Only load our synthetic fixture, never a file selected by a model or caller."""

    def __init__(self, data_path: Path | None = None) -> None:
        source = data_path or Path(__file__).resolve().parents[3] / "data/demo/members.json"
        members = TypeAdapter(list[Member]).validate_json(source.read_text(encoding="utf-8"))
        self._members = {member.member_id: member for member in members}
        if len(self._members) != len(members):
            raise ValueError("Duplicate demo member IDs")

    def get_member(self, member_id: str) -> Member | None:
        return self._members.get(member_id)


# Preserve the Milestone 1 import contract.
DemoMemberRepository = SyntheticMemberRepository


class DynamoDBMemberRepository:
    """Read-only, exact partition-key lookups. No scan and no synthetic fallback."""

    def __init__(self, table_name: str, region: str = "us-east-1", *, table: Any = None) -> None:
        if not table_name:
            raise ValueError("DYNAMODB_MEMBER_TABLE is required")
        if table is None:
            from app.aws import aws_client

            table = aws_client("dynamodb", region, resource=True).Table(table_name)
        self._table = table

    def get_member(self, member_id: str) -> Member | None:
        item = self._table.get_item(Key={"member_id": member_id}, ConsistentRead=True).get("Item")
        if item is None:
            return None
        # DynamoDB numbers deserialize as Decimal; reject fractional stored integer facts.
        item = {
            key: int(value)
            if isinstance(value, Decimal) and value == value.to_integral_value()
            else value
            for key, value in item.items()
        }
        if isinstance(item.get("balances_as_of"), str):
            item["balances_as_of"] = date.fromisoformat(item["balances_as_of"])
        member = Member.model_validate(item)
        if member.member_id != member_id:
            raise ValueError("Mismatched repository member")
        return member
