from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, cast

from pydantic import Field, TypeAdapter

from app.models import StrictModel

MemberId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9]+$")]
Money = Annotated[int, Field(ge=0)]


class Member(StrictModel):
    member_id: MemberId
    name: Annotated[str, Field(min_length=1, max_length=100)]
    employer_id: Annotated[str, Field(min_length=1, max_length=100)]
    plan_id: Annotated[str, Field(min_length=1, max_length=100)]
    plan_year: Annotated[int, Field(ge=2000, le=2200)]
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$")]
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
        return self._members.get(member_id.upper())


class DynamoDbClient(Protocol):
    def get_item(self, **kwargs: Any) -> Mapping[str, Any]: ...


class DynamoDBMemberRepository:
    """DynamoDB adapter using the normal AWS credential chain via an injected/lazy client."""

    def __init__(self, table_name: str, client: DynamoDbClient | None = None) -> None:
        if not table_name.strip():
            raise ValueError("DYNAMODB_MEMBER_TABLE is required for the dynamodb repository")
        self._table_name = table_name
        self._client = client or self._default_client()

    @staticmethod
    def _default_client() -> DynamoDbClient:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError("Install boto3 to use MEMBER_REPOSITORY=dynamodb") from error
        return cast(DynamoDbClient, boto3.client("dynamodb"))

    def get_member(self, member_id: str) -> Member | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"member_id": {"S": member_id.upper()}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        return Member.model_validate(self._deserialize_item(item))

    @staticmethod
    def _deserialize_item(item: Mapping[str, Any]) -> dict[str, object]:
        def scalar(name: str, kind: str) -> str:
            value = item.get(name)
            if not isinstance(value, Mapping) or not isinstance(value.get(kind), str):
                raise ValueError("Invalid DynamoDB member record")
            return cast(str, value[kind])

        return {
            "member_id": scalar("member_id", "S").upper(),
            "name": scalar("name", "S"),
            "employer_id": scalar("employer_id", "S"),
            "plan_id": scalar("plan_id", "S"),
            "plan_year": int(scalar("plan_year", "N")),
            "state": scalar("state", "S").upper(),
            "zip_code": scalar("zip_code", "S"),
            "currency": scalar("currency", "S"),
            "annual_maximum": int(scalar("annual_maximum", "N")),
            "annual_maximum_used": int(scalar("annual_maximum_used", "N")),
            "annual_maximum_remaining": int(scalar("annual_maximum_remaining", "N")),
            "deductible_total": int(scalar("deductible_total", "N")),
            "deductible_used": int(scalar("deductible_used", "N")),
            "deductible_remaining": int(scalar("deductible_remaining", "N")),
            "fsa_balance": int(scalar("fsa_balance", "N")),
        }


# Backward-compatible Milestone 1 name used by downstream imports.
DemoMemberRepository = SyntheticMemberRepository
