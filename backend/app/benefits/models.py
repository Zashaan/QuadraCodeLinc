"""Exact-money domain contracts; no model or cloud dependency."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, PlainSerializer, model_validator

from app.models import StrictModel


def exact_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Money must be a decimal string or integer, never a float")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError("Invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("Decimal must be finite")
    return result


Money = Annotated[
    Decimal,
    BeforeValidator(exact_decimal, json_schema_input_type=str | int),
    Field(ge=0, le=1000000, decimal_places=2),
    PlainSerializer(
        lambda value: format(abs(value) if value == 0 else value, ".2f"),
        return_type=str,
        when_used="json",
    ),
]
Rate = Annotated[Decimal, BeforeValidator(exact_decimal), Field(ge=0, le=1)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
Network = Literal["in_network", "out_of_network"]


class Source(StrictModel):
    source_id: ShortText
    document: ShortText
    section: ShortText | None = None
    page: Annotated[int, Field(ge=1)] | None = None
    plan_id: ShortText
    employer: ShortText | None = None
    plan_year: Annotated[int, Field(ge=2000, le=2100)] | None = None
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    doc_type: ShortText
    synthetic: bool
    uri: Annotated[str, Field(min_length=1, max_length=1024)] | None = None


class CoverageRule(StrictModel):
    plan_share: Rate
    deductible_applies: bool
    annual_maximum_applies: bool = True
    orthodontic_lifetime_maximum_applies: bool = False


class NetworkRule(StrictModel):
    balance_billing: bool
    categories: dict[str, CoverageRule]


class ProcedureRule(StrictModel):
    aliases: Annotated[list[ShortText], Field(min_length=1, max_length=20)]
    category: ShortText
    # None means unknown, not no restriction. Unsupported restrictions fail closed.
    waiting_period_days: Annotated[int, Field(ge=0)] | None = None
    waiting_period_months: Annotated[int, Field(ge=0, le=120)] | None = None
    preauthorization_required: bool | None = False
    preauthorization_threshold: Money | None = None
    frequency_count: Annotated[int, Field(ge=1, le=100)] | None = None
    frequency_months: Annotated[int, Field(ge=1, le=1200)] | None = None
    frequency_per_tooth: bool = False
    required_specialty: ShortText | None = None
    frequency_limit: ShortText | None = None
    frequency_verified_unrestricted: bool = False


class PlanRules(StrictModel):
    plan_id: ShortText
    employer_id: ShortText | None = None
    plan_year: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    annual_maximum: Money
    deductible: Money
    procedures: dict[str, ProcedureRule]
    networks: dict[Network, NetworkRule]
    source: Source

    @model_validator(mode="after")
    def coherent(self) -> "PlanRules":
        if (self.starts_on is None) != (self.ends_on is None):
            raise ValueError("Incomplete plan dates")
        if (
            self.starts_on is not None
            and self.ends_on is not None
            and (
                self.starts_on > self.ends_on
                or (self.plan_year is not None and self.starts_on.year != self.plan_year)
            )
        ):
            raise ValueError("Invalid plan dates")
        if (self.source.plan_id, self.source.employer, self.source.plan_year) != (
            self.plan_id,
            self.employer_id,
            self.plan_year,
        ):
            raise ValueError("Plan provenance mismatch")
        aliases: set[str] = set()
        for code, procedure in self.procedures.items():
            for alias in {code.casefold(), *(a.casefold() for a in procedure.aliases)}:
                if alias in aliases:
                    raise ValueError("Ambiguous procedure alias")
                aliases.add(alias)
            if any(procedure.category not in n.categories for n in self.networks.values()):
                raise ValueError("Missing configured coverage category")
        return self

    def procedure_id(self, wording: str) -> str | None:
        key = wording.strip().casefold()
        return next(
            (
                code
                for code, rule in self.procedures.items()
                if key == code.casefold() or key in [a.casefold() for a in rule.aliases]
            ),
            None,
        )


class BenefitInput(StrictModel):
    procedure: ShortText
    treatment_date: date
    network_status: Network
    provider_charge: Money
    allowed_amount: Money
    fee_source: ShortText
    allowed_amount_source: ShortText
    network_source: ShortText
    provider_id: ShortText | None = None
    tooth_id: ShortText | None = None


class BenefitEstimate(StrictModel):
    status: Literal["estimated"] = "estimated"
    member_id: str
    plan_id: str
    treatment_date: date
    balances_as_of: date | None
    procedure: str
    network_status: Network
    provider_charge: Money
    allowed_amount: Money
    deductible_applied: Money
    amount_after_deductible: Money
    plan_payment: Money
    estimated_member_payment: Money
    amount_not_covered: Money
    contractual_write_off: Money
    annual_maximum_consumed: Money
    annual_maximum_remaining_afterward: Money
    deductible_remaining_afterward: Money
    orthodontic_lifetime_remaining_afterward: Money | None = None
    steps: list[str]
    assumptions: list[str]
    warnings: list[str]
    sources: list[Source]
    fee_source: str
    allowed_amount_source: str
    network_source: str


class UnavailableResult(StrictModel):
    status: Literal["missing_information", "unverified", "unavailable"]
    reason: Annotated[str, Field(min_length=1, max_length=300)]
    missing_fields: list[str] = []
