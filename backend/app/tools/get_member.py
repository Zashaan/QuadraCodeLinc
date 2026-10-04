from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.members.repository import Member, MemberId, MemberRepository
from app.models import StrictModel
from app.tools.base import Tool


class GetMemberArguments(StrictModel):
    member_id: Annotated[
        str,
        Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"),
    ]

    @field_validator("member_id")
    @classmethod
    def canonicalize_id(cls, value: str) -> str:
        return value.upper()


class GetMemberResult(StrictModel):
    status: Literal["success", "not_found"]
    member: Member | None = None
    member_id: MemberId | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "GetMemberResult":
        if self.status == "success" and (self.member is None or self.member_id is not None):
            raise ValueError("Success requires a member")
        if self.status == "not_found" and (self.member is not None or self.member_id is None):
            raise ValueError("Not found requires a member ID")
        return self


class GetMemberTool(Tool[GetMemberArguments, GetMemberResult]):
    name = "get_member"
    description = (
        "Look up a synthetic demo member by member ID. Returns stored balances in USD, "
        "including annual_maximum_remaining. Read balances directly; never calculate them. "
        "This demo does not verify identity and must not be used with real member data."
    )
    arguments_type = GetMemberArguments
    result_type = GetMemberResult

    def __init__(self, repository: MemberRepository) -> None:
        self._repository = repository

    def execute(self, arguments: GetMemberArguments) -> GetMemberResult:
        member = self._repository.get_member(arguments.member_id)
        if member is None:
            return GetMemberResult(status="not_found", member_id=arguments.member_id)
        return GetMemberResult(status="success", member=member)
