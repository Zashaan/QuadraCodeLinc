from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, field_validator, model_validator

from app.models import StrictModel

NetworkStatus = Literal["in_network", "out_of_network"]
Category = Literal["preventive", "basic", "major"]


class NetworkRule(StrictModel):
    coverage_rate: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"))]
    deductible_applies: bool


class ProcedureRule(StrictModel):
    procedure_id: Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}$")]
    display_name: Annotated[str, Field(min_length=1, max_length=100)]
    aliases: Annotated[list[str], Field(min_length=1, max_length=20)]
    category: Category
    allowed_amount_required: bool
    network_rules: dict[NetworkStatus, NetworkRule]
    waiting_period_months: Annotated[int, Field(ge=0, le=120)]
    frequency_limit: Annotated[str, Field(min_length=1, max_length=300)] | None = None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, aliases: list[str]) -> list[str]:
        normalized = [alias.strip().lower() for alias in aliases]
        if any(not alias for alias in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Procedure aliases must be unique and non-empty")
        return normalized

    @model_validator(mode="after")
    def require_network_rules(self) -> "ProcedureRule":
        if set(self.network_rules) != {"in_network", "out_of_network"}:
            raise ValueError("Both network rules are required")
        return self


class PlanRules(StrictModel):
    plan_id: Annotated[str, Field(min_length=1, max_length=100)]
    employer_id: Annotated[str, Field(min_length=1, max_length=100)]
    plan_year: Annotated[int, Field(ge=2000, le=2200)]
    plan_name: Annotated[str, Field(min_length=1, max_length=100)]
    effective_date: date
    expiration_date: date
    currency: Literal["USD"]
    deductible: Annotated[Decimal, Field(ge=0)]
    annual_maximum: Annotated[Decimal, Field(ge=0)]
    procedures: Annotated[list[ProcedureRule], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_plan(self) -> "PlanRules":
        if self.effective_date > self.expiration_date:
            raise ValueError("Plan dates are reversed")
        ids = [procedure.procedure_id for procedure in self.procedures]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate procedure IDs")
        aliases = [alias for procedure in self.procedures for alias in procedure.aliases]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Procedure aliases must be unique within a plan")
        return self

    def procedure(self, value: str) -> ProcedureRule | None:
        normalized = " ".join(value.strip().lower().split())
        canonical = value.strip().upper()
        for procedure in self.procedures:
            if procedure.procedure_id == canonical or normalized in procedure.aliases:
                return procedure
        return None


class PlanRulesRepository(Protocol):
    def get_plan(self, plan_id: str, plan_year: int | None = None) -> PlanRules | None: ...


class _PlanFixture(StrictModel):
    notice: Literal["SYNTHETIC DEMO DATA — NOT REAL LINCOLN DATA"]
    plans: list[PlanRules]


class LocalPlanRulesRepository:
    def __init__(self, data_path: Path | None = None) -> None:
        source = data_path or Path(__file__).resolve().parents[3] / "data/demo/plans.json"
        fixture = TypeAdapter(_PlanFixture).validate_json(source.read_text(encoding="utf-8"))
        self._plans = {(plan.plan_id, plan.plan_year): plan for plan in fixture.plans}
        if len(self._plans) != len(fixture.plans):
            raise ValueError("Duplicate plan/year records")

    def get_plan(self, plan_id: str, plan_year: int | None = None) -> PlanRules | None:
        candidates = [
            plan
            for (candidate_id, candidate_year), plan in self._plans.items()
            if candidate_id == plan_id and (plan_year is None or candidate_year == plan_year)
        ]
        return candidates[0] if len(candidates) == 1 else None
