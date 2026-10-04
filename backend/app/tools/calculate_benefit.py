from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator

from app.calculator.benefits import (
    BenefitCalculationInput,
    BenefitCalculator,
    BenefitEstimate,
)
from app.members.repository import MemberRepository
from app.models import StrictModel
from app.plans.repository import NetworkStatus, PlanRulesRepository
from app.providers.repository import ProviderRepository
from app.tools.base import ToolDefinition


class CalculateBenefitArguments(StrictModel):
    member_id: str | None = None
    procedure: Annotated[str, Field(min_length=1, max_length=100)]
    treatment_date: date | None = None
    provider_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    network_status: NetworkStatus | None = None
    provider_charge: Annotated[Decimal, Field(ge=0)] | None = None
    allowed_amount: Annotated[Decimal, Field(ge=0)] | None = None
    fee_source: Literal["caller_quote", "verified_provider_quote"] | None = None

    @field_validator("treatment_date", mode="before")
    @classmethod
    def parse_treatment_date(cls, value: object) -> object:
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    @field_validator("provider_charge", "allowed_amount", mode="before")
    @classmethod
    def parse_money(cls, value: object) -> object:
        if isinstance(value, int | float | str) and not isinstance(value, bool):
            return Decimal(str(value))
        return value


class CalculationNeedsInput(StrictModel):
    status: Literal["missing_input", "unavailable"]
    missing: list[str]
    message: str


CalculationResult = BenefitEstimate | CalculationNeedsInput


class CalculateBenefitTool:
    name = "calculate_benefit"
    description = (
        "Calculate a deterministic dental benefit estimate using member facts, structured "
        "plan rules, and an actual synthetic-provider or caller-supplied fee. The Python tool "
        "does all arithmetic. Use the known member context; never invent a fee, allowed amount, "
        "network status, plan rule, or treatment date. If inputs are missing, ask only for the "
        "specific returned fields."
    )
    arguments_type = CalculateBenefitArguments

    def __init__(
        self,
        calculator: BenefitCalculator,
        members: MemberRepository,
        plans: PlanRulesRepository,
        providers: ProviderRepository,
    ) -> None:
        self._calculator = calculator
        self._members = members
        self._plans = plans
        self._providers = providers

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments_type.model_json_schema(),
        )

    def invoke_with_member(
        self, arguments: dict[str, JsonValue], default_member_id: str | None
    ) -> CalculationResult:
        values = self.arguments_type.model_validate(arguments)
        member_id = values.member_id or default_member_id
        if not member_id:
            return CalculationNeedsInput(
                status="missing_input",
                missing=["member_id"],
                message="A validated demo member ID is required.",
            )
        member = self._members.get_member(member_id)
        if member is None:
            return CalculationNeedsInput(
                status="unavailable",
                missing=[],
                message="The demo member could not be found.",
            )
        plan = self._plans.get_plan(member.plan_id, member.plan_year)
        if plan is None:
            return CalculationNeedsInput(
                status="unavailable",
                missing=[],
                message="Structured rules for this member's plan are unavailable.",
            )
        procedure = plan.procedure(values.procedure)
        if procedure is None:
            return CalculationNeedsInput(
                status="unavailable",
                missing=[],
                message="That procedure is not mapped in the structured demo plan.",
            )
        provider = self._providers.get_provider(values.provider_id) if values.provider_id else None
        if values.provider_id and provider is None:
            return CalculationNeedsInput(
                status="unavailable",
                missing=[],
                message="The requested provider record is unavailable.",
            )
        fee = provider.fees.get(procedure.procedure_id) if provider else None
        network_status = values.network_status or (provider.network_status if provider else None)
        provider_charge = values.provider_charge or (fee.provider_charge if fee else None)
        allowed_amount = values.allowed_amount or (fee.allowed_amount if fee else None)
        fee_source = (
            values.fee_source
            if values.provider_charge is not None
            else (provider.source if provider and fee else None)
        )
        missing: list[str] = []
        if values.treatment_date is None:
            missing.append("treatment_date")
        if network_status is None:
            missing.append("network_status")
        if provider_charge is None:
            missing.append("provider_charge")
        if procedure.allowed_amount_required and allowed_amount is None:
            missing.append("allowed_amount")
        if fee_source is None:
            missing.append("fee_source")
        if missing:
            return CalculationNeedsInput(
                status="missing_input",
                missing=missing,
                message="More exact information is required before calculating this estimate.",
            )
        assert values.treatment_date is not None
        assert network_status is not None
        assert provider_charge is not None
        assert fee_source is not None
        network_rule = procedure.network_rules[network_status]
        try:
            facts = BenefitCalculationInput(
                member_id=member.member_id,
                plan_id=member.plan_id,
                procedure_id=procedure.procedure_id,
                procedure_name=procedure.display_name,
                category=procedure.category,
                treatment_date=values.treatment_date,
                plan_effective_date=plan.effective_date,
                plan_expiration_date=plan.expiration_date,
                network_status=network_status,
                provider_charge=provider_charge,
                allowed_amount=allowed_amount,
                allowed_amount_required=procedure.allowed_amount_required,
                deductible_applies=network_rule.deductible_applies,
                deductible_remaining=Decimal(member.deductible_remaining),
                annual_maximum_remaining=Decimal(member.annual_maximum_remaining),
                coverage_rate=network_rule.coverage_rate,
                waiting_period_months=procedure.waiting_period_months,
                fee_source=fee_source,
                plan_rule_source=f"structured_plan_rules:{plan.plan_id}:{plan.plan_year}",
            )
        except ValueError as error:
            return CalculationNeedsInput(status="unavailable", missing=[], message=str(error))
        return self._calculator.calculate(facts)

    def invoke(self, arguments: dict[str, JsonValue]) -> CalculationResult:
        return self.invoke_with_member(arguments, None)
