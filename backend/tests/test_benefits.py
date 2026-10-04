from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.benefits.calculator import calculate_benefit
from app.benefits.models import BenefitEstimate, BenefitInput, PlanRules, UnavailableResult
from app.benefits.repository import LocalPlanRulesRepository
from app.members.repository import DemoMemberRepository, Member


@pytest.fixture
def member() -> Member:
    value = DemoMemberRepository().get_member("DEMO001")
    assert value is not None
    return value


@pytest.fixture
def plan() -> PlanRules:
    value = LocalPlanRulesRepository().get_plan("DEMO_DENTAL_PPO", "DEMO_EMPLOYER", 2026)
    assert value is not None
    return value


@pytest.fixture
def scenario() -> BenefitInput:
    return BenefitInput(
        procedure="crown",
        treatment_date=date(2026, 11, 1),
        network_status="in_network",
        provider_charge=Decimal("1400"),
        allowed_amount=Decimal("1200"),
        fee_source="synthetic quote",
        allowed_amount_source="synthetic allowed amount",
        network_source="synthetic provider record",
    )


def test_charge_allowed_writeoff_and_no_member_mutation(
    member: Member, plan: PlanRules, scenario: BenefitInput
) -> None:
    result = calculate_benefit(member, plan, scenario)
    assert isinstance(result, BenefitEstimate)
    assert result.plan_payment == Decimal("600.00")
    assert result.estimated_member_payment == Decimal("600.00")
    assert result.contractual_write_off == 200
    assert result.annual_maximum_remaining_afterward == 200
    assert member.annual_maximum_remaining == 800
    assert result.sources == [plan.source]


@pytest.mark.parametrize(
    "remaining,payment,owed", [(800, 800, 1200), (0, 0, 2000), (731, 731, 1269)]
)
def test_annual_maximum_is_stored_cap(
    member: Member, plan: PlanRules, scenario: BenefitInput, remaining: int, payment: int, owed: int
) -> None:
    result = calculate_benefit(
        member.model_copy(update={"annual_maximum_remaining": remaining}),
        plan,
        scenario.model_copy(
            update={"provider_charge": Decimal(2000), "allowed_amount": Decimal(2000)}
        ),
    )
    assert isinstance(result, BenefitEstimate)
    assert result.plan_payment == payment
    assert result.estimated_member_payment == owed
    assert result.annual_maximum_consumed == payment
    assert result.annual_maximum_remaining_afterward == remaining - payment


def test_deductible_and_out_of_network_balance_billing(
    member: Member, plan: PlanRules, scenario: BenefitInput
) -> None:
    result = calculate_benefit(
        member.model_copy(update={"deductible_remaining": 50}),
        plan,
        scenario.model_copy(update={"network_status": "out_of_network"}),
    )
    assert isinstance(result, BenefitEstimate)
    assert result.deductible_applied == 50
    assert result.amount_after_deductible == 1150
    assert result.plan_payment == 460
    assert result.estimated_member_payment == 940
    assert result.amount_not_covered == 200
    assert result.contractual_write_off == 0


def test_round_half_up_and_charge_below_allowed(
    member: Member, plan: PlanRules, scenario: BenefitInput
) -> None:
    result = calculate_benefit(
        member, plan, scenario.model_copy(update={"provider_charge": Decimal("100.01")})
    )
    assert isinstance(result, BenefitEstimate)
    assert result.plan_payment == Decimal("50.01")
    assert result.estimated_member_payment == Decimal("50.00")
    assert result.contractual_write_off == 0


def test_mapping_and_deductible_exemption_are_plan_specific(
    member: Member, plan: PlanRules, scenario: BenefitInput
) -> None:
    data = plan.model_dump()
    data["procedures"]["CROWN"]["category"] = "basic"
    data["networks"]["in_network"]["categories"]["basic"]["deductible_applies"] = False
    changed = PlanRules.model_validate(data)
    result = calculate_benefit(
        member.model_copy(update={"deductible_remaining": 50}),
        changed,
        scenario.model_copy(
            update={"provider_charge": Decimal(100), "allowed_amount": Decimal(100)}
        ),
    )
    assert isinstance(result, BenefitEstimate)
    assert result.plan_payment == 80
    assert result.deductible_applied == 0


@pytest.mark.parametrize("day", [date(2025, 12, 31), date(2027, 1, 1), date(2026, 1, 1)])
def test_unknown_year_or_historical_balance_is_unverified(
    member: Member, plan: PlanRules, scenario: BenefitInput, day: date
) -> None:
    result = calculate_benefit(member, plan, scenario.model_copy(update={"treatment_date": day}))
    assert isinstance(result, UnavailableResult)
    assert result.status == "unverified"


@pytest.mark.parametrize(
    "updates",
    [
        {"waiting_period_days": None},
        {"waiting_period_days": 180},
        {"frequency_verified_unrestricted": False},
        {"frequency_limit": "one per five years"},
    ],
)
def test_unknown_or_restricted_eligibility_is_not_guessed(
    member: Member, plan: PlanRules, scenario: BenefitInput, updates: dict[str, object]
) -> None:
    data = plan.model_dump()
    data["procedures"]["CROWN"].update(updates)
    assert isinstance(
        calculate_benefit(member, PlanRules.model_validate(data), scenario), UnavailableResult
    )


@pytest.mark.parametrize("value", [1.25, True, "NaN", "Infinity", "-1", "1.001", "1000001"])
def test_money_rejects_float_negative_nonfinite_and_excess_precision(
    scenario: BenefitInput, value: object
) -> None:
    with pytest.raises(ValidationError):
        BenefitInput.model_validate({**scenario.model_dump(), "provider_charge": value})


def test_missing_required_allowed_amount(scenario: BenefitInput) -> None:
    data = scenario.model_dump()
    del data["allowed_amount"]
    with pytest.raises(ValidationError):
        BenefitInput.model_validate(data)


def test_unknown_plan_alias_and_provenance(plan: PlanRules) -> None:
    repository = LocalPlanRulesRepository()
    assert repository.get_plan("UNKNOWN", "DEMO_EMPLOYER", 2026) is None
    assert repository.get_plan(plan.plan_id, "OTHER_EMPLOYER", 2026) is None
    assert plan.procedure_id("root canal") == "ROOT_CANAL"
    data = plan.model_dump()
    data["procedures"]["FILLING"]["aliases"].append("crown")
    with pytest.raises(ValidationError):
        PlanRules.model_validate(data)
