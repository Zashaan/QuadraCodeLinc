from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.members.normalization import resolve_member_ids
from app.members.repository import MemberRepository
from app.models import StrictModel
from app.tools.base import Tool

CanonicalId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9]+$")]


class ResolveMemberIdArguments(StrictModel):
    spoken_id: Annotated[str, Field(min_length=1, max_length=256)]


class ResolveMemberIdResult(StrictModel):
    status: Literal["resolved", "repeat", "confirm"]
    member_id: CanonicalId | None = None
    candidates: list[CanonicalId] | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "ResolveMemberIdResult":
        if self.status == "resolved":
            valid = self.member_id is not None and self.candidates is None
        elif self.status == "repeat":
            valid = self.member_id is None and self.candidates is None
        else:
            valid = (
                self.member_id is None
                and self.candidates is not None
                and 2 <= len(self.candidates) <= 16
                and len(set(self.candidates)) == len(self.candidates)
            )
        if not valid:
            raise ValueError("Invalid member ID resolution")
        return self


class ResolveMemberIdTool(Tool[ResolveMemberIdArguments, ResolveMemberIdResult]):
    name = "resolve_member_id"
    description = (
        "Resolve the caller's spoken member ID before get_member. Pass their ID wording "
        "verbatim, including uncertainty or corrections; do not convert digit words yourself. "
        "Returns a repository-validated canonical ID, repeat if no match, or confirm with "
        "ambiguous candidates. This does not verify caller identity."
    )
    arguments_type = ResolveMemberIdArguments
    result_type = ResolveMemberIdResult

    def __init__(self, repository: MemberRepository) -> None:
        self._repository = repository

    def execute(self, arguments: ResolveMemberIdArguments) -> ResolveMemberIdResult:
        matches = resolve_member_ids(arguments.spoken_id, self._repository)
        if len(matches) == 1:
            return ResolveMemberIdResult(status="resolved", member_id=matches[0])
        if matches:
            return ResolveMemberIdResult(status="confirm", candidates=matches)
        return ResolveMemberIdResult(status="repeat")
