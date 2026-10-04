from typing import Annotated, Literal

from pydantic import Field, JsonValue

from app.members.repository import MemberRepository
from app.models import StrictModel
from app.retrieval.repository import (
    PlanDocumentChunk,
    PlanDocumentRetriever,
    RetrievalFilters,
)
from app.tools.base import Tool, ToolDefinition


class RetrievePlanContextArguments(StrictModel):
    query: Annotated[str, Field(min_length=2, max_length=500)]
    member_id: str | None = None
    plan_id: str | None = None
    employer: str | None = None
    plan_year: int | None = None
    state: str | None = None
    doc_type: str | None = None


class RetrievePlanContextResult(StrictModel):
    status: Literal["verified", "unverified"]
    chunks: list[PlanDocumentChunk]
    message: str | None = None


class RetrievePlanContextTool(Tool[RetrievePlanContextArguments, RetrievePlanContextResult]):
    name = "retrieve_plan_context"
    description = (
        "Retrieve source-backed language from the member's plan documents for explanations "
        "of plan terms, waiting periods, network rules, or limitations. Use the known member "
        "context when available. Preserve returned source metadata and never invent citations. "
        "This tool is evidence retrieval, not a calculator."
    )
    arguments_type = RetrievePlanContextArguments
    result_type = RetrievePlanContextResult

    def __init__(self, retriever: PlanDocumentRetriever, members: MemberRepository) -> None:
        self._retriever = retriever
        self._members = members

    @property
    def definition(self) -> ToolDefinition:
        schema = self.arguments_type.model_json_schema()
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=schema,
        )

    def invoke_with_member(
        self, arguments: dict[str, JsonValue], default_member_id: str | None
    ) -> RetrievePlanContextResult:
        validated = self.arguments_type.model_validate(arguments)
        member_id = validated.member_id or default_member_id
        member = self._members.get_member(member_id) if member_id else None
        if member_id and member is None:
            return RetrievePlanContextResult(
                status="unverified",
                chunks=[],
                message="Plan context cannot be verified for an unknown demo member.",
            )
        filters = RetrievalFilters(
            plan_id=validated.plan_id or (member.plan_id if member else None),
            employer=validated.employer or (member.employer_id if member else None),
            plan_year=validated.plan_year or (member.plan_year if member else None),
            state=(validated.state or (member.state if member else None)),
            doc_type=validated.doc_type,
        )
        result = self._retriever.retrieve(validated.query, filters)
        return self.result_type.model_validate(result.model_dump())

    def execute(self, arguments: RetrievePlanContextArguments) -> RetrievePlanContextResult:
        result = self._retriever.retrieve(
            arguments.query,
            RetrievalFilters(
                plan_id=arguments.plan_id,
                employer=arguments.employer,
                plan_year=arguments.plan_year,
                state=arguments.state,
                doc_type=arguments.doc_type,
            ),
        )
        return RetrievePlanContextResult.model_validate(result.model_dump())
