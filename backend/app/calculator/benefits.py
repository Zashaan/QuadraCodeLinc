from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models import StrictModel
from app.plans.repository import NetworkStatus

Money = Annotated[Decimal, Field(ge=0)]
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class BenefitCalculationInput(StrictModel):
    member_id: str
    plan_id: str
    procedure_id: str
    procedure_name: str
    category: str
    treatment_date: date
    plan_effective_date: date
    plan_expiration_date: date
    network_status: NetworkStatus
    provider_charge: Money
    allowed_amount: Money | None
    allowed_amount_required: bool
    deductible_applies: bool
    deductible_remaining: Money
    annual_maximum_remaining: Money
    coverage_rate: Annotated[Decimal, Field(ge=0, le=1)]
    waiting_period_months: Annotated[int, Field(ge=0)] = 0
    fee_source: str
    plan_rule_source: str

    @model_validator(mode="after")
    def validate_inputs(self) -> "BenefitCalculationInput":
        if self.allowed_amount_required and self.allowed_amount is None:
            raise ValueError("allowed_amount is required by this plan rule")
        if not self.plan_effective_date <= self.treatment_date <= self.plan_expiration_date:
            raise ValueError("treatment_date is outside the configured plan year")
        if self.waiting_period_months:
            raise ValueError("waiting-period eligibility cannot be verified from available facts")
        return self


class BenefitEstimate(StrictModel):
    status: Literal["estimated"] = "estimated"
    procedure_id: str
    procedure: str
    category: str
    treatment_date: date
    network_status: NetworkStatus
    provider_charge: Money
    allowed_amount: Money
    deductible_applied: Money
    amount_after_deductible: Money
    coverage_rate: Decimal
    plan_payment_before_annual_maximum: Money
    plan_payment: Money
    estimated_member_payment: Money
    amount_not_covered: Money
    annual_maximum_consumed: Money
    annual_maximum_remaining_after: Money
    calculation_steps: list[str]
    assumptions: list[str]
    warnings: list[str]
    provenance: dict[str, str]


class BenefitCalculator:
    """Pure Decimal arithmetic over already-resolved member, plan and fee facts."""

    def calculate(self, facts: BenefitCalculationInput) -> BenefitEstimate:
        allowed = facts.allowed_amount
        if allowed is None:
            allowed = facts.provider_charge
        benefit_basis = money(min(facts.provider_charge, allowed))
        deductible = (
            money(min(facts.deductible_remaining, benefit_basis))
            if facts.deductible_applies
            else Decimal("0.00")
        )
        after_deductible = money(benefit_basis - deductible)
        before_maximum = money(after_deductible * facts.coverage_rate)
        plan_payment = money(min(before_maximum, facts.annual_maximum_remaining))
        member_payment = money(facts.provider_charge - plan_payment)
        not_covered = money(max(facts.provider_charge - allowed, Decimal("0")))
        remaining = money(facts.annual_maximum_remaining - plan_payment)
        warnings: list[str] = [
            "This is a financial estimate, not a guarantee of coverage or payment.",
            "Clinical decisions and urgency must be discussed with a dentist.",
        ]
        if plan_payment < before_maximum:
            warnings.append("The remaining annual maximum reduced the estimated plan payment.")
        if not_covered:
            warnings.append("The provider charge exceeds the allowed amount.")
        return BenefitEstimate(
            procedure_id=facts.procedure_id,
            procedure=facts.procedure_name,
            category=facts.category,
            treatment_date=facts.treatment_date,
            network_status=facts.network_status,
            provider_charge=money(facts.provider_charge),
            allowed_amount=money(allowed),
            deductible_applied=deductible,
            amount_after_deductible=after_deductible,
            coverage_rate=facts.coverage_rate,
            plan_payment_before_annual_maximum=before_maximum,
            plan_payment=plan_payment,
            estimated_member_payment=member_payment,
            amount_not_covered=not_covered,
            annual_maximum_consumed=plan_payment,
            annual_maximum_remaining_after=remaining,
            calculation_steps=[
                f"Benefit basis is the lesser of charge and allowed amount: ${benefit_basis}.",
                (
                    f"Applicable deductible: ${deductible}; amount after deductible: "
                    f"${after_deductible}."
                ),
                (
                    f"Plan rate from structured rules: {facts.coverage_rate}; "
                    f"pre-cap payment: ${before_maximum}."
                ),
                f"Payment after the annual-maximum cap: ${plan_payment}.",
            ],
            assumptions=[
                "The supplied treatment date and network status are correct.",
                (
                    "Claims processing, eligibility, frequency limits, and prior services may "
                    "change the result."
                ),
            ],
            warnings=warnings,
            provenance={
                "member_facts": "member_repository",
                "plan_rules": facts.plan_rule_source,
                "fee": facts.fee_source,
                "arithmetic": "deterministic_python_decimal",
            },
        )
