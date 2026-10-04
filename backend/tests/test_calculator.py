from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.calculator.benefits import BenefitCalculationInput, BenefitCalculator


def facts(**updates: object) -> BenefitCalculationInput:
    values: dict[str, object] = {
        "member_id": "DEMO001",
        "plan_id": "DEMO_DENTAL_PPO",
        "procedure_id": "D2740",
        "procedure_name": "ceramic crown",
        "category": "major",
        "treatment_date": date(2026, 6, 1),
        "plan_effective_date": date(2026, 1, 1),
        "plan_expiration_date": date(2026, 12, 31),
        "network_status": "in_network",
        "provider_charge": Decimal("1400.00"),
        "allowed_amount": Decimal("1200.00"),
        "allowed_amount_required": True,
        "deductible_applies": True,
        "deductible_remaining": Decimal("50.00"),
        "annual_maximum_remaining": Decimal("800.00"),
        "coverage_rate": Decimal("0.50"),
        "waiting_period_months": 0,
        "fee_source": "caller_quote",
        "plan_rule_source": "structured_plan_rules:DEMO_DENTAL_PPO:2026",
    }
    values.update(updates)
    return BenefitCalculationInput.model_validate(values)


def test_deductible_coverage_member_responsibility_and_separate_allowed_amount() -> None:
    result = BenefitCalculator().calculate(facts())
    assert result.deductible_applied == Decimal("50.00")
    assert result.amount_after_deductible == Decimal("1150.00")
    assert result.plan_payment == Decimal("575.00")
    assert result.estimated_member_payment == Decimal("825.00")
    assert result.amount_not_covered == Decimal("200.00")
    assert result.annual_maximum_remaining_after == Decimal("225.00")


def test_annual_maximum_caps_at_800_and_handles_zero_remaining() -> None:
    capped = BenefitCalculator().calculate(
        facts(
            provider_charge=Decimal("2400"),
            allowed_amount=Decimal("2200"),
            deductible_remaining=Decimal("0"),
        )
    )
    assert capped.plan_payment_before_annual_maximum == Decimal("1100.00")
    assert capped.plan_payment == Decimal("800.00")
    assert capped.annual_maximum_remaining_after == Decimal("0.00")
    zero = BenefitCalculator().calculate(facts(annual_maximum_remaining=Decimal("0")))
    assert zero.plan_payment == Decimal("0.00")
    assert zero.estimated_member_payment == Decimal("1400.00")


def test_network_rate_money_rounding_and_no_llm_dependency() -> None:
    calculator = BenefitCalculator()
    in_network = calculator.calculate(facts(coverage_rate=Decimal("0.80")))
    out_network = calculator.calculate(
        facts(network_status="out_of_network", coverage_rate=Decimal("0.60"))
    )
    assert in_network.plan_payment > out_network.plan_payment
    rounded = calculator.calculate(
        facts(
            provider_charge=Decimal("100.01"),
            allowed_amount=Decimal("100.01"),
            deductible_remaining=Decimal("0"),
            coverage_rate=Decimal("0.333"),
        )
    )
    assert rounded.plan_payment == Decimal("33.30")
    assert result_provenance_is_python(rounded.provenance)


def result_provenance_is_python(provenance: dict[str, str]) -> bool:
    return provenance["arithmetic"] == "deterministic_python_decimal"


def test_missing_allowed_amount_and_out_of_plan_date_fail_clearly() -> None:
    with pytest.raises(ValidationError, match="allowed_amount"):
        facts(allowed_amount=None)
    with pytest.raises(ValidationError, match="outside the configured plan year"):
        facts(treatment_date=date(2027, 1, 1))
