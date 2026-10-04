from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator

from app.members.repository import MemberRepository
from app.models import StrictModel
from app.plans.repository import NetworkStatus, PlanRulesRepository
from app.providers.repository import ProviderMatch, ProviderRepository, ProviderSearch
from app.tools.base import Tool


class SearchProvidersArguments(StrictModel):
    member_id: str | None = None
    procedure: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$")] | None = None
    radius_miles: Annotated[Decimal, Field(gt=0, le=100)] | None = None
    network_status: NetworkStatus | None = None
    specialty: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @field_validator("radius_miles", mode="before")
    @classmethod
    def parse_radius(cls, value: object) -> object:
        if isinstance(value, int | float | str) and not isinstance(value, bool):
            return Decimal(str(value))
        return value


class SearchProvidersResult(StrictModel):
    status: Literal["success", "unavailable"]
    providers: list[ProviderMatch]
    message: str | None = None
    source: Literal["synthetic_demo_provider_repository"]


class SearchProvidersTool(Tool[SearchProvidersArguments, SearchProvidersResult]):
    name = "search_providers"
    description = (
        "Filter clearly synthetic demo providers by location, radius, network, specialty, "
        "and supported procedure. This returns factual options in fixture order; it does not "
        "choose, rank, recommend, or optimize a provider."
    )
    arguments_type = SearchProvidersArguments
    result_type = SearchProvidersResult

    def __init__(
        self,
        providers: ProviderRepository,
        plans: PlanRulesRepository,
        members: MemberRepository,
    ) -> None:
        self._providers = providers
        self._plans = plans
        self._members = members

    def invoke_with_member(
        self, arguments: dict[str, JsonValue], default_member_id: str | None
    ) -> SearchProvidersResult:
        validated = self.arguments_type.model_validate(arguments)
        member_id = validated.member_id or default_member_id
        member = self._members.get_member(member_id) if member_id else None
        procedure_id: str | None = None
        if validated.procedure:
            plan = self._plans.get_plan(member.plan_id, member.plan_year) if member else None
            procedure = plan.procedure(validated.procedure) if plan else None
            if procedure is None:
                return SearchProvidersResult(
                    status="unavailable",
                    providers=[],
                    message="The procedure is not mapped in the member's structured demo plan.",
                    source="synthetic_demo_provider_repository",
                )
            procedure_id = procedure.procedure_id
        matches = self._providers.search(
            ProviderSearch(
                zip_code=validated.zip_code or (member.zip_code if member else None),
                radius_miles=validated.radius_miles,
                network_status=validated.network_status,
                specialty=validated.specialty,
                procedure_id=procedure_id,
            )
        )
        return SearchProvidersResult(
            status="success",
            providers=matches,
            message=None if matches else "No synthetic demo providers matched those filters.",
            source="synthetic_demo_provider_repository",
        )

    def execute(self, arguments: SearchProvidersArguments) -> SearchProvidersResult:
        member_id = arguments.member_id
        member = self._members.get_member(member_id) if member_id else None
        procedure_id: str | None = None
        if arguments.procedure:
            plan = self._plans.get_plan(member.plan_id, member.plan_year) if member else None
            procedure = plan.procedure(arguments.procedure) if plan else None
            if procedure is None:
                return SearchProvidersResult(
                    status="unavailable",
                    providers=[],
                    message="The procedure is not mapped in the member's structured demo plan.",
                    source="synthetic_demo_provider_repository",
                )
            procedure_id = procedure.procedure_id
        matches = self._providers.search(
            ProviderSearch(
                zip_code=arguments.zip_code or (member.zip_code if member else None),
                radius_miles=arguments.radius_miles,
                network_status=arguments.network_status,
                specialty=arguments.specialty,
                procedure_id=procedure_id,
            )
        )
        return SearchProvidersResult(
            status="success",
            providers=matches,
            message=None if matches else "No synthetic demo providers matched those filters.",
            source="synthetic_demo_provider_repository",
        )
