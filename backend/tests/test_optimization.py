from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.benefits.calculator import calculate_benefit
from app.benefits.models import BenefitEstimate, BenefitInput, PlanRules, UnavailableResult
from app.benefits.repository import LocalPlanRulesRepository
from app.history.repository import Authorization, Claim, MemberHistory
from app.members.repository import DemoMemberRepository, Member
from app.optimization.models import FeeQuote, HardConstraints, OptimizationRequest, Preferences
from app.optimization.optimizer import optimize_benefits
from app.providers.repository import ProcedureFee, Provider

DAY = date(2026, 11, 1)
RESET = date(2027, 1, 1)


@pytest.fixture
def member() -> Member:
    result = DemoMemberRepository().get_member("DEMO001")
    assert result
    return result.model_copy(
        update={"member_status": "active", "coverage_start_date": date(2026, 1, 1)}
    )


@pytest.fixture
def plan() -> PlanRules:
    result = LocalPlanRulesRepository().get_plan("DEMO_DENTAL_PPO", "DEMO_EMPLOYER", 2026)
    assert result
    return result


def provider(
    name: str = "NEAR", *, price: int = 1200, miles: int | None = 5, dates: list[date] | None = None
) -> Provider:
    return Provider(
        provider_id=name,
        name=name,
        specialty="general dentist",
        plan_id="DEMO_DENTAL_PPO",
        network_status="in_network",
        zip_code="10001",
        distances_miles={} if miles is None else {"10001": miles},
        fees={
            "CROWN": ProcedureFee(
                provider_charge=Decimal(price),
                allowed_amount=Decimal(price),
                source="synthetic quote",
            )
        },
        source="synthetic directory",
        verification_status="synthetic",
        verified_on="2026-10-01",
        synthetic=True,
        accepting_new_patients=True,
        available_dates=dates,
    )


def request(**updates: object) -> OptimizationRequest:
    return OptimizationRequest.model_validate(
        {"procedure": "crown", "requested_date": DAY, "origin_zip": "10001", **updates}
    )


def benefit() -> BenefitInput:
    return BenefitInput(
        procedure="crown",
        treatment_date=DAY,
        network_status="in_network",
        provider_charge=Decimal(1200),
        allowed_amount=Decimal(1200),
        fee_source="test",
        allowed_amount_source="test",
        network_source="test",
        provider_id="NEAR",
    )


def change_rule(plan: PlanRules, **updates: object) -> PlanRules:
    data = plan.model_dump()
    data["procedures"]["CROWN"].update(updates)
    return PlanRules.model_validate(data)


def safe(**updates: object) -> HardConstraints:
    return HardConstraints.model_validate(
        {
            "dentist_confirmed_multiple_dates": True,
            "dentist_safe_until": date(2027, 1, 10),
            "assume_continued_plan_terms": True,
            **updates,
        }
    )


def test_normal_default_does_not_trade_100_miles_for_50_dollars(
    member: Member, plan: PlanRules
) -> None:
    providers = [provider(), provider("FAR", price=1100, miles=100)]
    result = optimize_benefits(member, plan, providers, request())
    assert result.best_overall and result.best_overall.provider_id == "NEAR"
    assert result.cheapest_alternative and result.cheapest_alternative.provider_id == "FAR"
    assert result.best_overall.estimate.estimated_member_payment == 600
    assert result.cheapest_alternative.estimate.estimated_member_payment == 550
    assert optimize_benefits(member, plan, list(reversed(providers)), request()) == result


@pytest.mark.parametrize("priority,winner", [("cheapest", "FAR"), ("closest", "NEAR")])
def test_explicit_preferences(member: Member, plan: PlanRules, priority: str, winner: str) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider(), provider("FAR", price=1100, miles=100)],
        request(preferences=Preferences.model_validate({"priority": priority})),
    )
    assert result.best_overall and result.best_overall.provider_id == winner


def test_fastest_requires_actual_availability(member: Member, plan: PlanRules) -> None:
    later = date(2026, 11, 10)
    providers = [
        provider("FAST", price=1400, dates=[DAY]),
        provider("CHEAP", price=500, dates=[later]),
        provider("UNKNOWN", price=100),
    ]
    result = optimize_benefits(
        member,
        plan,
        providers,
        request(
            candidate_dates=[later], constraints=safe(), preferences=Preferences(priority="fastest")
        ),
    )
    assert result.best_overall and result.best_overall.provider_id == "FAST"
    assert any("availability" in item.reason for item in result.excluded)


@pytest.mark.parametrize("miles", [100, None])
def test_hard_radius_never_loses_to_cheapest(
    member: Member, plan: PlanRules, miles: int | None
) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider(), provider("FAR", price=1, miles=miles)],
        request(
            constraints=HardConstraints(max_distance_miles=20),
            preferences=Preferences(priority="cheapest"),
        ),
    )
    assert result.best_overall and result.best_overall.provider_id == "NEAR"


@pytest.mark.parametrize("updates", [{"member_status": "inactive"}, {"member_status": None}])
def test_inactive_or_unknown_member_rejected(
    member: Member, plan: PlanRules, updates: dict[str, object]
) -> None:
    result = optimize_benefits(member.model_copy(update=updates), plan, [provider()], request())
    assert result.best_overall is None
    assert isinstance(
        calculate_benefit(member.model_copy(update=updates), plan, benefit()), UnavailableResult
    )


@pytest.mark.parametrize(
    "constraints",
    [
        HardConstraints(),
        safe(urgent=True),
        safe(dentist_safe_until=date(2026, 12, 1)),
        safe(deadline=date(2026, 12, 1)),
    ],
)
def test_unsafe_unknown_or_late_delay_never_recommended(
    member: Member, plan: PlanRules, constraints: HardConstraints
) -> None:
    result = optimize_benefits(
        member.model_copy(update={"annual_maximum_remaining": 100}),
        plan,
        [provider()],
        request(
            compare_benefit_year_reset=True,
            constraints=constraints,
            preferences=Preferences(priority="cheapest"),
        ),
    )
    assert result.best_overall and result.best_overall.treatment_date == DAY
    assert result.feasible_scenarios == 1


def test_benefit_reset_simulates_both_deductible_and_annual_max(
    member: Member, plan: PlanRules
) -> None:
    original = member.model_copy(update={"annual_maximum_remaining": 100})
    result = optimize_benefits(
        original,
        plan,
        [provider()],
        request(
            compare_benefit_year_reset=True,
            constraints=safe(),
            preferences=Preferences(priority="cheapest"),
        ),
    )
    assert result.best_overall and result.best_overall.treatment_date == RESET
    assert result.feasible_scenarios == 2
    assert result.best_overall.estimate.deductible_applied == 50
    assert result.best_overall.estimate.plan_payment == 575
    assert result.best_overall.estimate.estimated_member_payment == 625
    assert result.best_overall.estimate.annual_maximum_remaining_afterward == 1425
    assert any("Hypothetical" in text for text in result.best_overall.assumptions)
    assert result.best_overall.estimate.sources == [plan.source]
    assert original.annual_maximum_remaining == 100


def test_current_year_can_beat_reset(member: Member, plan: PlanRules) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider()],
        request(
            compare_benefit_year_reset=True,
            constraints=safe(),
            preferences=Preferences(priority="cheapest"),
        ),
    )
    assert result.best_overall and result.best_overall.treatment_date == DAY
    assert result.best_overall.estimate.estimated_member_payment == 600


def test_missing_next_year_terms_require_explicit_assumption(
    member: Member, plan: PlanRules
) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider()],
        request(
            compare_benefit_year_reset=True, constraints=safe(assume_continued_plan_terms=False)
        ),
    )
    assert result.feasible_scenarios == 1
    assert "continued_plan_terms" in result.missing_fields


@pytest.mark.parametrize(
    "balance,expiry,applied",
    [(350, date(2026, 12, 31), 350), (None, None, 0), (350, date(2026, 10, 31), 0)],
)
def test_fsa_is_cash_source_not_insurance_discount(
    member: Member, plan: PlanRules, balance: int | None, expiry: date | None, applied: int
) -> None:
    result = optimize_benefits(
        member.model_copy(update={"fsa_balance": balance, "fsa_expires_on": expiry}),
        plan,
        [provider()],
        request(use_fsa=True),
    )
    best = result.best_overall
    assert best and best.estimate.estimated_member_payment == 600
    assert best.fsa_applied == applied
    assert best.estimated_cash_payment == 600 - applied


def test_unknown_fsa_carryover_not_assumed(member: Member, plan: PlanRules) -> None:
    result = optimize_benefits(
        member, plan, [provider()], request(requested_date=RESET, constraints=safe(), use_fsa=True)
    )
    assert result.best_overall and result.best_overall.fsa_applied == 0
    assert any("carryover" in text for text in result.best_overall.assumptions)


@pytest.mark.parametrize(
    "changes", [{"required_specialty": "endodontist"}, {"required_network": "out_of_network"}]
)
def test_specialty_and_network_are_hard_constraints(
    member: Member, plan: PlanRules, changes: dict[str, object]
) -> None:
    result = optimize_benefits(
        member, plan, [provider()], request(constraints=HardConstraints.model_validate(changes))
    )
    assert result.best_overall is None


def test_provider_acceptance_and_availability(member: Member, plan: PlanRules) -> None:
    closed = provider().model_copy(update={"accepting_new_patients": False})
    assert (
        optimize_benefits(
            member, plan, [closed], request(constraints=HardConstraints(new_patient=True))
        ).best_overall
        is None
    )
    assert optimize_benefits(member, plan, [provider(dates=[])], request()).best_overall is None


def test_dataset_global_network_not_assumed_plan_specific(member: Member, plan: PlanRules) -> None:
    result = optimize_benefits(
        member, plan, [provider().model_copy(update={"network_scope": "dataset_global"})], request()
    )
    assert result.best_overall is None and "plan_network_status" in result.missing_fields


def test_missing_fees_never_use_typical_price(member: Member, plan: PlanRules) -> None:
    empty = provider().model_copy(update={"fees": {}})
    result = optimize_benefits(member, plan, [empty], request())
    assert result.best_overall is None and "allowed_amount" in result.missing_fields
    quoted = optimize_benefits(
        member,
        plan,
        [empty],
        request(
            fee_quotes=[
                FeeQuote(
                    provider_id="NEAR", provider_charge=Decimal(1200), allowed_amount=Decimal(1000)
                )
            ]
        ),
    )
    assert quoted.best_overall and quoted.best_overall.estimate.estimated_member_payment == 500
    assert quoted.best_overall.estimate.fee_source == "caller_reported"


def test_missing_benefit_year_is_not_invented(member: Member, plan: PlanRules) -> None:
    result = optimize_benefits(
        member,
        plan.model_copy(update={"starts_on": None, "ends_on": None}),
        [provider()],
        request(),
    )
    assert result.best_overall is None and "benefit_year" in result.missing_fields


def test_calendar_waiting_period_and_enrollment(member: Member, plan: PlanRules) -> None:
    restricted = change_rule(plan, waiting_period_days=None, waiting_period_months=6)
    assert isinstance(calculate_benefit(member, restricted, benefit()), BenefitEstimate)
    late = member.model_copy(update={"coverage_start_date": date(2026, 6, 1)})
    assert isinstance(calculate_benefit(late, restricted, benefit()), UnavailableResult)
    unknown = member.model_copy(update={"coverage_start_date": None})
    assert isinstance(calculate_benefit(unknown, restricted, benefit()), UnavailableResult)


def test_preauthorization_requires_valid_date_provider_and_complete_history(
    member: Member, plan: PlanRules
) -> None:
    restricted = change_rule(plan, preauthorization_required=True)
    auth = Authorization(
        authorization_id="A",
        member_id=member.member_id,
        procedure_code="CROWN",
        status="approved",
        provider_id="NEAR",
        valid_from=DAY,
        valid_until=DAY,
    )
    history = MemberHistory(authorizations=[auth], authorizations_complete=True)
    assert isinstance(
        calculate_benefit(member, restricted, benefit(), history=history), BenefitEstimate
    )
    for bad in [
        history.model_copy(update={"authorizations_complete": False}),
        history.model_copy(
            update={"authorizations": [auth.model_copy(update={"valid_until": None})]}
        ),
        history.model_copy(
            update={"authorizations": [auth.model_copy(update={"provider_id": "OTHER"})]}
        ),
        history.model_copy(
            update={"authorizations": [auth.model_copy(update={"status": "pending"})]}
        ),
    ]:
        assert isinstance(
            calculate_benefit(member, restricted, benefit(), history=bad), UnavailableResult
        )


def test_frequency_limit_checks_tooth_and_complete_history(member: Member, plan: PlanRules) -> None:
    restricted = change_rule(plan, frequency_count=1, frequency_months=60, frequency_per_tooth=True)
    scenario = benefit().model_copy(update={"tooth_id": "12"})
    claim = Claim(
        claim_id="C",
        member_id=member.member_id,
        procedure_code="CROWN",
        service_date=date(2025, 1, 1),
        claim_status="paid",
        tooth_id="12",
    )
    history = MemberHistory(claims=[claim], claims_complete=True)
    assert isinstance(
        calculate_benefit(member, restricted, scenario, history=history), UnavailableResult
    )
    assert isinstance(
        calculate_benefit(member, restricted, benefit(), history=history), UnavailableResult
    )
    assert isinstance(
        calculate_benefit(member, restricted, scenario, history=MemberHistory()), UnavailableResult
    )
    other = history.model_copy(update={"claims": [claim.model_copy(update={"tooth_id": "13"})]})
    assert isinstance(
        calculate_benefit(member, restricted, scenario, history=other), BenefitEstimate
    )


def test_orthodontic_lifetime_cap_does_not_consume_annual_when_rule_excludes_it(
    member: Member, plan: PlanRules
) -> None:
    data = plan.model_dump()
    data["networks"]["in_network"]["categories"]["major"].update(
        orthodontic_lifetime_maximum_applies=True, annual_maximum_applies=False
    )
    changed = PlanRules.model_validate(data)
    result = calculate_benefit(
        member.model_copy(
            update={"orthodontic_lifetime_maximum": 1500, "orthodontic_lifetime_used": 1400}
        ),
        changed,
        benefit(),
    )
    assert isinstance(result, BenefitEstimate)
    assert result.plan_payment == 100 and result.orthodontic_lifetime_remaining_afterward == 0
    assert result.annual_maximum_consumed == 0 and result.annual_maximum_remaining_afterward == 800
    assert isinstance(calculate_benefit(member, changed, benefit()), UnavailableResult)


def test_request_accepts_tool_json_dates_and_bounds() -> None:
    parsed = OptimizationRequest.model_validate(
        {
            "procedure": "crown",
            "requested_date": "2026-11-01",
            "candidate_dates": ["2027-01-01"],
            "constraints": {"deadline": "2027-01-01"},
        }
    )
    assert parsed.requested_date == DAY and parsed.candidate_dates == [RESET]
    with pytest.raises(ValidationError):
        request(candidate_dates=[DAY] * 4)
    with pytest.raises(ValidationError):
        request(requested_date="not a date")


def test_scenario_count_is_bounded(member: Member, plan: PlanRules) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider(str(i)) for i in range(100)],
        request(
            candidate_dates=[date(2026, 11, i) for i in [2, 3, 4]],
            compare_benefit_year_reset=True,
            constraints=safe(),
        ),
    )
    assert result.scenarios_evaluated <= 40


def test_new_year_requested_directly_still_needs_safe_timing(
    member: Member, plan: PlanRules
) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider()],
        request(
            requested_date=RESET, constraints=HardConstraints(assume_continued_plan_terms=True)
        ),
    )
    assert result.best_overall is None
    assert "dentist_safe_timing" in result.missing_fields


def test_balance_date_must_match_plan_period(member: Member, plan: PlanRules) -> None:
    result = calculate_benefit(
        member.model_copy(update={"balances_as_of": date(2025, 12, 31)}), plan, benefit()
    )
    assert isinstance(result, UnavailableResult)


def test_authoritative_specialty_cannot_be_overridden_by_caller(
    member: Member, plan: PlanRules
) -> None:
    restricted = change_rule(plan, required_specialty="endodontist")
    result = optimize_benefits(
        member,
        restricted,
        [provider()],
        request(constraints=HardConstraints(required_specialty="general dentist")),
    )
    assert result.best_overall is None


def test_cash_preference_uses_fsa_with_expiry_but_preserves_insurance_total(
    member: Member, plan: PlanRules
) -> None:
    poor = member.model_copy(
        update={
            "annual_maximum_remaining": 100,
            "fsa_balance": 1000,
            "fsa_expires_on": date(2026, 12, 31),
        }
    )
    result = optimize_benefits(
        poor,
        plan,
        [provider()],
        request(
            compare_benefit_year_reset=True,
            constraints=safe(),
            preferences=Preferences(priority="cash"),
        ),
    )
    assert result.best_overall and result.best_overall.treatment_date == DAY
    assert result.best_overall.estimate.estimated_member_payment == 1100
    assert result.best_overall.fsa_applied == 1000
    assert result.best_overall.estimated_cash_payment == 100


def test_waiting_period_month_end_is_calendar_based(member: Member, plan: PlanRules) -> None:
    from app.benefits.calculator import add_months

    assert add_months(date(2024, 8, 31), 6) == date(2025, 2, 28)
    assert add_months(date(2023, 8, 31), 6) == date(2024, 2, 29)


def test_rounding_uses_calculator_not_a_second_optimizer_formula(
    member: Member, plan: PlanRules
) -> None:
    tiny = provider().model_copy(
        update={
            "fees": {
                "CROWN": ProcedureFee(
                    provider_charge=Decimal("100.01"),
                    allowed_amount=Decimal("100.01"),
                    source="synthetic quote",
                )
            }
        }
    )
    result = optimize_benefits(member, plan, [tiny], request())
    assert result.best_overall
    assert result.best_overall.estimate.plan_payment == Decimal("50.01")
    assert result.best_overall.estimated_cash_payment == Decimal("50.00")


def test_caller_quote_cannot_override_known_structured_allowance(
    member: Member, plan: PlanRules
) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider()],
        request(
            fee_quotes=[
                FeeQuote(
                    provider_id="NEAR", provider_charge=Decimal(1200), allowed_amount=Decimal(2000)
                )
            ]
        ),
    )
    assert result.best_overall is None
    assert any("conflicts" in item.reason for item in result.excluded)


def test_updated_caller_charge_can_use_matching_structured_allowance(
    member: Member, plan: PlanRules
) -> None:
    result = optimize_benefits(
        member,
        plan,
        [provider()],
        request(
            fee_quotes=[
                FeeQuote(
                    provider_id="NEAR", provider_charge=Decimal(1100), allowed_amount=Decimal(1200)
                )
            ]
        ),
    )
    assert result.best_overall and result.best_overall.estimate.estimated_member_payment == 550
    assert result.best_overall.estimate.fee_source == "caller_reported"
