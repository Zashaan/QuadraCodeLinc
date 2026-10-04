from datetime import date
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.benefits.models import BenefitEstimate, Money, Network, ShortText
from app.models import StrictModel


def _parse_day(value: object) -> object:
    if isinstance(value, str):
        if len(value) != 10:
            raise ValueError("Use an ISO YYYY-MM-DD date")
        return date.fromisoformat(value)
    return value


class HardConstraints(StrictModel):
    dentist_confirmed_multiple_dates: bool = False
    dentist_safe_until: date | None = None
    urgent: bool = False
    max_distance_miles: Annotated[int, Field(ge=0, le=500)] | None = None
    deadline: date | None = None
    required_specialty: ShortText | None = None
    required_network: Network | None = None
    new_patient: bool = False
    assume_continued_plan_terms: bool = False

    @field_validator("dentist_safe_until", "deadline", mode="before")
    @classmethod
    def parse_dates(cls, value: object) -> object:
        return _parse_day(value)


class Preferences(StrictModel):
    priority: Literal["balanced", "cheapest", "fastest", "closest", "cash"] = "balanced"
    cost_priority: Annotated[int, Field(ge=0, le=10)] = 5
    time_priority: Annotated[int, Field(ge=0, le=10)] = 3
    distance_priority: Annotated[int, Field(ge=0, le=10)] = 5
    network_priority: Annotated[int, Field(ge=0, le=10)] = 3


class FeeQuote(StrictModel):
    provider_id: ShortText
    provider_charge: Money
    allowed_amount: Money
    # This tool input always denotes an explicitly caller-reported assumption.
    source: Literal["caller_reported"] = "caller_reported"


class OptimizationRequest(StrictModel):
    procedure: ShortText
    requested_date: date
    candidate_dates: Annotated[list[date], Field(max_length=3)] = []
    compare_benefit_year_reset: bool = False
    origin_zip: Annotated[str, Field(pattern=r"^[0-9]{5}$")] | None = None
    tooth_id: ShortText | None = None
    constraints: HardConstraints = HardConstraints()
    preferences: Preferences = Preferences()
    fee_quotes: Annotated[list[FeeQuote], Field(max_length=10)] = []
    use_fsa: bool = False

    @field_validator("requested_date", mode="before")
    @classmethod
    def parse_date(cls, value: object) -> object:
        return _parse_day(value)

    @field_validator("candidate_dates", mode="before")
    @classmethod
    def parse_candidates(cls, value: object) -> object:
        return [_parse_day(day) for day in value] if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_quotes(self) -> "OptimizationRequest":
        if len({q.provider_id for q in self.fee_quotes}) != len(self.fee_quotes):
            raise ValueError("Duplicate provider quotes")
        return self


class Scenario(StrictModel):
    scenario_id: ShortText
    provider_id: ShortText
    provider_name: ShortText
    treatment_date: date
    benefit_year: int
    timing: Literal["current_benefit_year", "after_benefit_reset"]
    network_status: Network
    distance_miles: Annotated[int, Field(ge=0)] | None
    availability_confirmed: bool
    estimate: BenefitEstimate
    fsa_applied: Money
    estimated_cash_payment: Money
    constraint_status: Literal["passed"] = "passed"
    assumptions: list[str]
    reasons: list[str]
    tradeoffs: list[str]


class ExcludedScenario(StrictModel):
    provider_id: str
    treatment_date: date
    reason: str
    missing_fields: list[str] = []


class OptimizationResult(StrictModel):
    status: Literal["optimized", "missing_information", "no_feasible_scenarios"]
    best_overall: Scenario | None = None
    cheapest_alternative: Scenario | None = None
    fastest_alternative: Scenario | None = None
    closest_alternative: Scenario | None = None
    scenarios_evaluated: int
    feasible_scenarios: int
    excluded: Annotated[list[ExcludedScenario], Field(max_length=50)]
    missing_fields: list[str]
    assumptions: list[str]
    follow_up: str | None = None
