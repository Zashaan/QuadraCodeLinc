from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter

from app.models import StrictModel

MemberId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")]
Money = Annotated[int, Field(ge=0)]


class Member(StrictModel):
    member_id: MemberId
    name: Annotated[str, Field(min_length=1, max_length=100)]
    plan_id: Annotated[str, Field(min_length=1, max_length=100)]
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


class DemoMemberRepository:
    """Only load our synthetic fixture, never a file selected by a model or caller."""

    def __init__(self, data_path: Path | None = None) -> None:
        source = data_path or Path(__file__).resolve().parents[3] / "data/demo/members.json"
        members = TypeAdapter(list[Member]).validate_json(source.read_text(encoding="utf-8"))
        self._members = {member.member_id: member for member in members}
        if len(self._members) != len(members):
            raise ValueError("Duplicate demo member IDs")

    def get_member(self, member_id: str) -> Member | None:
        return self._members.get(member_id)
